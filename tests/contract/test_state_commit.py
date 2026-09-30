import asyncio
from dataclasses import replace
import sqlite3
from uuid import uuid4

import pytest

from canvas_mcp.domain.errors import StateStoreUnavailableError
from tests.contract.test_state_repository import FACT, NOW
from tests.contract.test_grade_mapping import raw, COURSE
from canvas_mcp.infrastructure.canvas.grade_mapping import grade


def test_commit_failure_returns_safe_error_and_retains_old_baseline(state_repo, monkeypatch):
    owner = uuid4().hex

    async def run():
        async with state_repo.transaction(owner) as tx:
            await tx.replace_course("8", (FACT,), NOW)
        connect = state_repo._connect

        class BrokenCommit:
            def __init__(self):
                self.connection = connect()

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                raise sqlite3.OperationalError("SECRET_DATABASE_FAILURE")

        monkeypatch.setattr(state_repo, "_connect", BrokenCommit)
        with pytest.raises(StateStoreUnavailableError) as error:
            async with state_repo.transaction(owner) as tx:
                await tx.replace_course("8", (replace(FACT, score=85),), NOW)
        assert "SECRET" not in str(error.value)
        monkeypatch.setattr(state_repo, "_connect", connect)
        async with state_repo.transaction(owner) as tx:
            assert await tx.baseline("8") == (FACT,)

    asyncio.run(run())


def test_unknown_grade_attempt_never_claims_a_repeat_attempt():
    fact = grade(raw(grade_matches_current_submission=None, attempt=2), COURSE, "7").fact
    assert fact.exposed and fact.attempt is None
