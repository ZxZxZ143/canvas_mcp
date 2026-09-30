"""Identical contract suite runs against real SQLite and real PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

import pytest

from canvas_mcp.domain.errors import StateStoreUnavailableError
from canvas_mcp.domain.grade_state import GradeFact
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.infrastructure.state.sql import SQLiteStateRepository

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
FACT = GradeFact("8", "10", 1, 80.125123456, "B", 100, NOW, NOW, "graded", "visible")


def test_baseline_latest_precision_restart_and_isolation(state_repo):
    owner = uuid4().hex

    async def run():
        async with state_repo.transaction(owner) as tx:
            assert not await tx.initialized()
            assert await tx.baseline("8") is None
            await tx.replace_course("8", (FACT,), NOW)
            await tx.mark_checked()
        reopened = type(state_repo)(state_repo._settings)
        async with reopened.transaction(owner) as tx:
            assert await tx.initialized()
            assert await tx.baseline("8") == (FACT,)
        async with reopened.transaction(uuid4().hex) as tx:
            assert not await tx.initialized()
            assert await tx.baseline("8") is None
        await reopened.reset(owner)
        async with reopened.transaction(owner) as tx:
            assert not await tx.initialized()
            assert await tx.baseline("8") is None

    asyncio.run(run())


def test_atomic_rollback_and_repeated_upsert(state_repo):
    owner = uuid4().hex

    async def run():
        async with state_repo.transaction(owner) as tx:
            await tx.replace_course("8", (FACT,), NOW)
            await tx.mark_checked()
        with pytest.raises(RuntimeError):
            async with state_repo.transaction(owner) as tx:
                await tx.replace_course("8", (replace(FACT, score=85),), NOW)
                await tx.replace_course("9", (replace(FACT, course_id="9"),), NOW)
                raise RuntimeError("synthetic interruption")
        for _ in range(3):
            async with state_repo.transaction(owner) as tx:
                assert await tx.baseline("8") == (FACT,)
                assert await tx.baseline("9") is None
                await tx.replace_course("8", (FACT,), NOW)
        db = state_repo._connect()
        try:
            mark = "%s" if state_repo._postgres else "?"
            cursor = db.cursor()
            cursor.execute(
                "SELECT count(*),min(state_version),max(state_version) FROM grade_state WHERE owner="
                + mark,
                (owner,),
            )
            assert cursor.fetchone() == (2, 1, 1)
        finally:
            db.close()

    asyncio.run(run())


def test_concurrent_lock_and_cancel_rollback(state_repo):
    owner = uuid4().hex

    async def run():
        held = asyncio.Event()
        release = asyncio.Event()
        observed = []

        async def first():
            async with state_repo.transaction(owner) as tx:
                await tx.replace_course("8", (FACT,), NOW)
                held.set()
                await release.wait()
                await tx.mark_checked()

        async def second():
            async with state_repo.transaction(owner) as tx:
                observed.append(await tx.baseline("8"))

        task = asyncio.create_task(first())
        await held.wait()
        other = asyncio.create_task(second())
        await asyncio.sleep(0.05)
        assert not observed
        release.set()
        await asyncio.gather(task, other)
        assert observed == [(FACT,)]
        held.clear()
        release.clear()
        task = asyncio.create_task(first())
        await held.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with state_repo.transaction(owner) as tx:
            assert await tx.baseline("8") == (FACT,)

    asyncio.run(run())


def test_latest_previous_attempt_is_separate_from_baseline(state_repo):
    owner = uuid4().hex

    async def run():
        async with state_repo.transaction(owner) as tx:
            await tx.replace_course("8", (FACT,), NOW)
        pending = replace(FACT, attempt=2, score=None, grade=None, visibility="previous_attempt")
        async with state_repo.transaction(owner) as tx:
            await tx.replace_course("8", (pending,), NOW, baseline=(FACT,))
        async with state_repo.transaction(owner) as tx:
            assert await tx.baseline("8") == (FACT,)

    asyncio.run(run())


def test_unmigrated_incompatible_schema_and_safe_settings(tmp_path):
    path = tmp_path / "unmigrated.db"
    repo = SQLiteStateRepository(StateSettings("sqlite", "sqlite", path))

    async def run():
        with pytest.raises(StateStoreUnavailableError):
            async with repo.transaction("owner"):
                pass
        await repo.migrate()
        db = sqlite3.connect(path)
        db.execute("UPDATE state_schema SET version=2")
        db.commit()
        db.close()
        with pytest.raises(StateStoreUnavailableError):
            async with repo.transaction("owner"):
                pass
        with pytest.raises(StateStoreUnavailableError):
            await repo.migrate()

    asyncio.run(run())


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sqlite:///relative.db",
        "postgresql://user:SECRET@host/db",
        "postgresql://user:SECRET@host/db?sslmode=disable",
        "postgresql://user:SECRET@host/db?sslmode=verify-full&options=evil",
    ],
)
def test_invalid_settings_never_echo_secret(value):
    with pytest.raises(StateStoreUnavailableError) as error:
        StateSettings.load({"STATE_DATABASE_URL": value}, remote=True)
    assert "SECRET" not in str(error.value)


def test_remote_sqlite_rejected_and_secret_repr(tmp_path):
    settings = StateSettings.load(
        {"STATE_DATABASE_URL": "postgresql://user:SECRET@host/db?sslmode=verify-full"}, remote=True
    )
    assert "SECRET" not in repr(settings)
    with pytest.raises(StateStoreUnavailableError):
        StateSettings.load(
            {"STATE_DATABASE_URL": f"sqlite:///{tmp_path / 'local.db'}"}, remote=True
        )
