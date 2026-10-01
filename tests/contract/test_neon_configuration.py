import asyncio
import io
import json
from dataclasses import replace

import psycopg
import pytest

from canvas_mcp.domain.errors import StateStoreUnavailableError
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.infrastructure.state.sql import PostgreSQLStateRepository
from canvas_mcp.infrastructure.state.sql import UnavailableStateRepository
from canvas_mcp.infrastructure.logging.events import EventLogger
from canvas_mcp.application.grade_changes import GradeChangeService
from tests.unit.test_grade_changes import Grades
from tests.contract.test_state_repository import FACT


@pytest.mark.parametrize("mode", ["require", "verify-full"])
def test_neon_tls_is_upgraded_to_certificate_verification(mode, monkeypatch):
    settings = StateSettings.load(
        {
            "STATE_DATABASE_URL": f"postgresql://user:SECRET@example.invalid/db?sslmode={mode}&channel_binding=require"
        },
        remote=True,
    )
    calls = []
    sentinel = object()

    def connect(url, **kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(psycopg, "connect", connect)
    assert PostgreSQLStateRepository(settings)._connect() is sentinel
    assert calls == [
        {
            "connect_timeout": 5,
            "autocommit": False,
            "sslmode": "verify-full",
            "sslrootcert": "system",
        }
    ]
    assert "SECRET" not in repr(settings)


@pytest.mark.parametrize(
    "suffix",
    [
        "sslmode=disable",
        "sslmode=prefer",
        "sslmode=allow",
        "sslmode=require&channel_binding=disable",
        "sslmode=require&sslmode=disable",
    ],
)
def test_neon_configuration_fails_closed(suffix):
    with pytest.raises(StateStoreUnavailableError):
        StateSettings.load(
            {"STATE_DATABASE_URL": "postgresql://user:SECRET@example.invalid/db?" + suffix},
            remote=True,
        )


def test_connect_retry_is_bounded_and_never_retries_transaction(monkeypatch):
    settings = StateSettings.load(
        {"STATE_DATABASE_URL": "postgresql://user:SECRET@example.invalid/db?sslmode=require"},
        remote=True,
    )
    calls, sleeps = [], []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        raise psycopg.OperationalError("SECRET_FAILURE")

    monkeypatch.setattr(psycopg, "connect", fail)
    monkeypatch.setattr("canvas_mcp.infrastructure.state.sql.time.sleep", sleeps.append)
    with pytest.raises(psycopg.OperationalError):
        PostgreSQLStateRepository(settings)._connect()
    assert len(calls) == 3 and sleeps == [0.25, 0.5]


def test_schema_inspection_has_only_fields_and_counts(state_repo):
    report = asyncio.run(state_repo.inspect())
    assert report["schema_version"] == 1
    fields = report["tables"]["grade_state"]["fields"]
    assert "score" in fields and "state_hash" in fields
    assert not {"course_name", "assignment_name", "description", "token", "owner_email"} & set(
        fields
    )
    assert all(set(item) == {"fields", "row_count"} for item in report["tables"].values())


def test_timing_is_private_and_separates_collection_from_state(state_repo, stack):
    from uuid import uuid4

    async def run():
        async with stack([]) as s:
            stream = io.StringIO()
            logger = EventLogger("INFO", stream)
            source = Grades()
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=uuid4().hex))
            await GradeChangeService(
                source, state_repo, "https://synthetic.invalid", timing=logger.state_timing
            ).check(ctx)
            report = json.loads(stream.getvalue())
            assert set(report) == {
                "event",
                "canvas_collection_ms",
                "state_processing_ms",
                "total_ms",
            }
            assert report["event"] == "grade_check_timing"
            assert (
                abs(
                    report["total_ms"]
                    - report["canvas_collection_ms"]
                    - report["state_processing_ms"]
                )
                < 0.03
            )

    asyncio.run(run())


@pytest.mark.parametrize("callback", ["timing", "audit", "both"])
def test_observability_failure_cannot_consume_an_unreturned_grade(state_repo, stack, callback):
    from uuid import uuid4

    def fail(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=uuid4().hex))
            source = Grades()
            service = GradeChangeService(
                source,
                state_repo,
                "https://synthetic.invalid",
                timing=fail if callback in ("timing", "both") else None,
                audit=fail if callback in ("audit", "both") else None,
            )
            assert (await service.check(ctx)).data.baseline_created
            source.rows["8"] = (replace(FACT, score=85),)
            report = await service.check(ctx)
            assert report.data.changed_grades[0].previous.score == FACT.score
            assert report.data.changed_grades[0].current.fact.score == 85
            assert not (await service.check(ctx)).data.changed_grades

    asyncio.run(run())


def test_failed_audit_cannot_mask_safe_store_failure(stack):
    def fail(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    async def run():
        async with stack([]) as s:
            service = GradeChangeService(
                Grades(), UnavailableStateRepository(), "https://synthetic.invalid", audit=fail
            )
            with pytest.raises(StateStoreUnavailableError) as error:
                await service.check(s.ctx)
            assert error.value.diagnostic_code == "state_store_unavailable"
            assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in str(error.value)

    asyncio.run(run())
