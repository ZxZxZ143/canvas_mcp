import asyncio
from dataclasses import replace
from datetime import timedelta
import json
from uuid import uuid4

import pytest

from canvas_mcp.application.grade_changes import GradeChangeService, state_owner
from canvas_mcp.domain.errors import (
    BudgetExceededError,
    UpstreamUnavailableError,
    StateStoreUnavailableError,
)
from canvas_mcp.domain.grade_state import NamedGrade
from canvas_mcp.domain.models import (
    Course,
    ExternalText,
    Observed,
    Availability,
    Page,
    Profile,
    AccessScope,
    PrincipalId,
)
from canvas_mcp.infrastructure.state.sql import UnavailableStateRepository
from canvas_mcp.mcp.projection import envelope, grade_changes
from tests.contract.test_state_repository import FACT, NOW


class Grades:
    def __init__(self):
        self.courses = tuple(
            Course(
                str(x),
                ExternalText(f"Course {x}"),
                ExternalText("C"),
                Observed(Availability.AVAILABLE, None),
            )
            for x in (8, 9)
        )
        self.rows = {
            "8": (
                FACT,
                replace(FACT, assignment_id="11", score=None, grade=None, visibility="ungraded"),
            ),
            "9": (replace(FACT, course_id="9"),),
        }
        self.fail = set()
        self.calls = []
        self.subject = "7"

    async def get_profile(self, ctx):
        return Profile(
            self.subject, ExternalText("Student"), Observed(Availability.AVAILABLE, "UTC")
        )

    async def list_courses(self, ctx, page, active_only):
        self.discovery_active_only = active_only
        return Page(self.courses, None, True)

    async def list_grade_facts(self, ctx, course_id, page):
        self.calls.append(course_id)
        if course_id in self.fail:
            raise UpstreamUnavailableError()
        course = next(x for x in self.courses if x.id == course_id)
        return Page(
            tuple(
                NamedGrade(fact, course.name, ExternalText(f"Assignment {fact.assignment_id}"))
                for fact in self.rows[course_id]
            ),
            None,
            True,
        )


def test_first_new_changed_retry_restart_and_other_reads(state_repo, stack):
    async def run():
        async with stack([]) as s:
            ctx = replace(
                s.ctx, as_of=NOW, scope=replace(s.scope, principal_id=PrincipalId(uuid4().hex))
            )
            source = Grades()
            service = GradeChangeService(source, state_repo, "https://canvas.example.edu")
            first = await service.check(ctx)
            assert source.discovery_active_only is False
            assert (
                first.data.baseline_created
                and not first.data.new_grades
                and not first.data.changed_grades
            )
            source.rows["8"] = (
                FACT,
                replace(FACT, assignment_id="11", score=90, grade="A", visibility="visible"),
            )
            # Unrelated current read does not touch StateRepository.
            await source.list_grade_facts(ctx, "8", None)
            new = await service.check(ctx)
            assert [x.current.fact.assignment_id for x in new.data.new_grades] == ["11"]
            assert not new.data.changed_grades
            source.rows["8"] = (replace(FACT, score=85), source.rows["8"][1])
            changed = await service.check(ctx)
            assert [
                (x.previous.score, x.current.fact.score) for x in changed.data.changed_grades
            ] == [(FACT.score, 85)]
            reopened = GradeChangeService(
                source, type(state_repo)(state_repo._settings), service.origin
            )
            same = await reopened.check(replace(ctx, request_id=uuid4().hex))
            assert (
                not same.data.baseline_created
                and not same.data.new_grades
                and not same.data.changed_grades
            )
            encoded = json.dumps(envelope(same, grade_changes))
            assert str(ctx.scope.principal_id) not in encoded and "state_hash" not in encoded

    asyncio.run(run())


def test_partial_course_baseline_and_first_course_initialization(state_repo, stack):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=PrincipalId(uuid4().hex)))
            source = Grades()
            service = GradeChangeService(source, state_repo, "origin")
            await service.check(ctx)
            source.fail = {"9"}
            source.rows["9"] = (replace(FACT, course_id="9", score=85),)
            partial = await service.check(ctx)
            assert not partial.complete and partial.data.coverage.failed == ("9",)
            assert not partial.data.new_grades and not partial.data.changed_grades
            source.fail.clear()
            later = await service.check(ctx)
            assert [x.current.fact.course_id for x in later.data.changed_grades] == ["9"]
            assert not (await service.check(ctx)).data.changed_grades
            other = replace(ctx, scope=replace(ctx.scope, principal_id=PrincipalId(uuid4().hex)))
            source.fail = {"9"}
            first = await service.check(other)
            assert first.data.baseline_created and first.data.baselined_courses == ("8",)
            source.fail.clear()
            second = await service.check(other)
            assert second.data.baselined_courses == ("9",) and not second.data.new_grades

    asyncio.run(run())


def test_response_and_transaction_failure_preserve_baseline(state_repo, stack, monkeypatch):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=PrincipalId(uuid4().hex)))
            source = Grades()
            service = GradeChangeService(source, state_repo, "origin")
            await service.check(ctx)
            source.rows["8"] = (replace(FACT, score=85),)

            def fail(result):
                raise BudgetExceededError()

            with pytest.raises(BudgetExceededError):
                await service.check(ctx, fail)
            changed = await service.check(ctx)
            assert changed.data.changed_grades[0].previous.score == FACT.score
            source.rows["8"] = (replace(FACT, score=90),)
            from canvas_mcp.infrastructure.state.sql import SqlTransaction

            real = SqlTransaction.replace_course

            async def fail_write(self, course_id, *args, **kwargs):
                await real(self, course_id, *args, **kwargs)
                if course_id == "9":
                    raise RuntimeError("synthetic DB interruption")

            monkeypatch.setattr(SqlTransaction, "replace_course", fail_write)
            with pytest.raises(RuntimeError):
                await service.check(ctx)
            monkeypatch.setattr(SqlTransaction, "replace_course", real)
            changed = await service.check(ctx)
            assert changed.data.changed_grades[0].previous.score == 85

    asyncio.run(run())


def test_concurrent_checks_emit_change_once_and_attempts(state_repo, stack):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=PrincipalId(uuid4().hex)))
            source = Grades()
            service = GradeChangeService(source, state_repo, "origin")
            await service.check(ctx)
            source.rows["8"] = (
                replace(FACT, attempt=2, score=None, grade=None, visibility="previous_attempt"),
            )
            assert not (await service.check(ctx)).data.new_grades
            source.rows["8"] = (replace(FACT, attempt=2, score=85),)
            results = await asyncio.gather(service.check(ctx), service.check(ctx))
            events = [event for output in results for event in output.data.new_grades]
            assert len(events) == 1 and events[0].reason == "new_attempt"
            source.rows["8"] = (
                replace(FACT, attempt=2, score=85, graded_at=NOW + timedelta(hours=1)),
            )
            assert not (await service.check(ctx)).data.changed_grades

    asyncio.run(run())


def test_unavailable_store_does_not_claim_no_changes(stack):
    async def run():
        async with stack([]) as s:
            with pytest.raises(StateStoreUnavailableError):
                await GradeChangeService(Grades(), UnavailableStateRepository(), "origin").check(
                    s.ctx
                )

    asyncio.run(run())


def test_identity_stable_token_rotation_and_local_sessions():
    scope = AccessScope("verified_subject", "personal_canvas")
    owner = state_owner(scope, "https://canvas.example.edu", "7")
    assert owner == state_owner(scope, "https://canvas.example.edu", "7")
    assert owner != state_owner(
        replace(scope, principal_id="another"), "https://canvas.example.edu", "7"
    )
    assert owner != state_owner(scope, "https://canvas.example.edu", "8")
    assert state_owner(AccessScope("local", "session1"), "origin", "7", local=True) == state_owner(
        AccessScope("local", "session2"), "origin", "7", local=True
    )


def test_backlog_drains_without_losing_new_or_changed_grades(state_repo, stack):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=PrincipalId(uuid4().hex)))
            source = Grades()
            source.rows["8"] = tuple(replace(FACT, assignment_id=str(x)) for x in range(100, 250))
            service = GradeChangeService(source, state_repo, "origin")
            await service.check(ctx)
            source.rows["8"] = tuple(replace(x, score=85) for x in source.rows["8"]) + tuple(
                replace(FACT, assignment_id=str(x), score=90) for x in range(250, 300)
            )
            seen = set()
            for _ in range(10):
                output = await service.check(ctx, lambda result: envelope(result, grade_changes))
                events = output.data.new_grades + output.data.changed_grades
                ids = {x.current.fact.assignment_id for x in events}
                assert not ids & seen
                seen.update(ids)
                assert len(events) <= 100
                if output.complete:
                    break
                assert any(w.code == "response_limit_reached" for w in output.warnings)
            assert seen == {str(x) for x in range(100, 300)}
            assert not (await service.check(ctx)).data.changed_grades

    asyncio.run(run())


def test_second_collector_waits_for_first_committed_snapshot(state_repo, stack):
    async def run():
        async with stack([]) as s:
            ctx = replace(s.ctx, scope=replace(s.scope, principal_id=PrincipalId(uuid4().hex)))
            source = Grades()
            service = GradeChangeService(source, state_repo, "origin")
            await service.check(ctx)
            entered, release = asyncio.Event(), asyncio.Event()
            real = source.list_grade_facts
            count = 0

            async def blocked(ctx, course_id, page):
                nonlocal count
                if course_id == "8":
                    count += 1
                    if count == 1:
                        entered.set()
                        await release.wait()
                        source.rows["8"] = (replace(FACT, score=85),)
                    else:
                        source.rows["8"] = (replace(FACT, score=90),)
                return await real(ctx, course_id, page)

            source.list_grade_facts = blocked
            first = asyncio.create_task(service.check(ctx))
            await entered.wait()
            second = asyncio.create_task(service.check(ctx))
            await asyncio.sleep(0.05)
            assert count == 1
            release.set()
            outputs = await asyncio.gather(first, second)
            assert outputs[0].data.changed_grades[0].previous.score == FACT.score
            assert outputs[1].data.changed_grades[0].previous.score == 85
            assert outputs[1].data.changed_grades[0].current.fact.score == 90

    asyncio.run(run())
