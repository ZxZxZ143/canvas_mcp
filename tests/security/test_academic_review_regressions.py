import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from canvas_mcp.domain.errors import MalformedUpstreamError, ValidationError
from canvas_mcp.domain.models import Availability, Observed, Page, PageRequest, SubmissionState
from canvas_mcp.infrastructure.canvas.academic_mapping import content, timestamp
from conftest import PROFILE, TOKEN, next_link, response


@pytest.mark.parametrize(
    "url",
    [
        "//cdn.example.test/file?verifier=SIGNED_SENTINEL",
        "/courses/8/files/50/download?verifier=SIGNED_SENTINEL",
        "files/50?access_token=SIGNED_SENTINEL",
        "https://cdn.example.test/file?signature=SIGNED_SENTINEL",
    ],
)
def test_signed_relative_urls_do_not_survive_content_projection(url):
    value = content("Read this " + url + " and continue.")
    assert "SIGNED_SENTINEL" not in value.text and "[reference omitted]" in value.text
    assert "and continue." in value.text


def test_url_omission_does_not_destroy_arithmetic():
    assert content("Compute 1 /2 + 1 /4 and x/y.").text == "Compute 1 /2 + 1 /4 and x/y."
    code = "quotient = x // 2\n// explain this code\ndata: notes"
    assert content("<pre>" + code + "</pre>").text == code


def test_display_names_and_titles_remain_single_line():
    from canvas_mcp.infrastructure.canvas.mapping import profile
    from canvas_mcp.infrastructure.canvas.academic_mapping import title

    raw = "Student\nGrades API: available\tFake"
    assert "\n" not in profile({"id": 7, "name": raw}).display_name.text
    assert "\t" not in profile({"id": 7, "name": raw}).display_name.text
    assert "\n" not in title("<p>Title</p><p>Fake status</p>").text


@pytest.mark.parametrize(
    "html,fragments",
    [
        ("<p>https://example.test</p><p>Do not submit.</p>", ["Do not submit."]),
        (
            "<pre>if ready:\n    work()\nelse:\n    wait()</pre>",
            ["if ready:\n    work()", "else:\n    wait()"],
        ),
        ("<ul><li>First</li><li>Second</li></ul>", ["First\n", "Second"]),
        ("first<br>second", ["first\nsecond"]),
    ],
)
def test_html_structure_is_not_collapsed_into_changed_instructions(html, fragments):
    value = content(html)
    for fragment in fragments:
        assert fragment in value.text


@pytest.mark.parametrize(
    "stamp", ["2026-01-01T00:00:00+00:60", "2026-01-01T00:00:00-05:99", "2026-01-01T00:00:00+24:00"]
)
def test_invalid_rfc3339_offsets_fail_without_normalization(stamp):
    with pytest.raises(MalformedUpstreamError) as error:
        timestamp(stamp)
    assert error.value.__context__ is None


def test_block_boundaries_cannot_hide_reflected_token(stack, academic_payloads):
    async def run():
        d = academic_payloads
        d["assignment"]["description"] = "<p>" + TOKEN[:10] + "</p><p>" + TOKEN[10:] + "</p>"
        async with stack(
            [response(PROFILE), response(d["course"]), response(d["assignment"])]
        ) as s:
            with pytest.raises(MalformedUpstreamError):
                await s.academic.get_assignment(s.ctx, "8", "10")
            assert TOKEN not in s.logs.getvalue()

    asyncio.run(run())


def test_graded_unknown_submission_stays_in_upcoming_with_warning(stack, academic_payloads):
    from canvas_mcp.application.academic import AcademicService
    from canvas_mcp.infrastructure.canvas import academic_mapping as mapping
    from conftest import ORIGIN

    async def run():
        d = academic_payloads
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        assignment = mapping.assignment(d["assignment"], "8", "7", ORIGIN)
        sub = replace(assignment.submission.value, state=SubmissionState.UNKNOWN, graded=True)
        assignment = replace(
            assignment,
            due_at=Observed(Availability.AVAILABLE, now + timedelta(days=1)),
            submission=Observed(Availability.AVAILABLE, sub),
        )

        class Fake:
            async def list_courses(self, ctx, page, active_only):
                return Page((mapping.student_course(d["course"], "8", "7"),), None, True)

            async def list_assignments(self, ctx, course_id, page):
                return Page((assignment,), None, True)

        async with stack([]) as s:
            result = await AcademicService(Fake()).get_upcoming(replace(s.ctx, as_of=now))
            assert len(result.data.items.items) == 1 and not result.complete
            assert any(w.code == "submission_unknown" for w in result.warnings)

    asyncio.run(run())


def test_concurrent_cursor_replay_only_one_request_wins(stack, monkeypatch):
    async def run():
        async with stack(
            [response(PROFILE), response([], headers=[(b"Link", next_link())]), response([])]
        ) as s:
            first = await s.provider.list_courses(s.ctx, PageRequest(), True)
            original = s.client.get_courses_page
            started, release = asyncio.Event(), asyncio.Event()

            async def blocked(*args, **kwargs):
                started.set()
                await release.wait()
                return await original(*args, **kwargs)

            monkeypatch.setattr(s.client, "get_courses_page", blocked)
            page = PageRequest(cursor=first.next_cursor)
            task = asyncio.create_task(s.provider.list_courses(s.ctx, page, True))
            await started.wait()
            try:
                with pytest.raises(ValidationError):
                    await s.provider.list_courses(s.ctx, page, True)
            finally:
                release.set()
            assert (await task).complete
            assert len([w for w in s.backend.writes if w.startswith(b"GET")]) == 3

    asyncio.run(run())


def test_hidden_course_totals_are_unavailable_without_enrollment_read(stack, academic_payloads):
    async def run():
        d = academic_payloads
        async with stack(
            [response(PROFILE), response({**d["course"], "hide_final_grades": True})]
        ) as s:
            result = await s.academic.get_course_grade(s.ctx, "8")
            assert result.data.current_score.value is None and result.data.final_score.value is None
            assert result.data.current_score.state is Availability.UNAVAILABLE
            assert len([w for w in s.backend.writes if w.startswith(b"GET")]) == 2

    asyncio.run(run())
