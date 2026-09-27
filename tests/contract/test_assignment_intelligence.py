"""Source fidelity, partial evidence and deterministic relationship regressions."""

import asyncio
from dataclasses import replace

import pytest

from canvas_mcp.application.academic import AcademicService
from canvas_mcp.application.contracts import AssignmentContext, Result
from canvas_mcp.domain.models import Availability, Observed
from canvas_mcp.infrastructure.canvas.assignment_text import assignment_visible_text
from canvas_mcp.infrastructure.canvas import academic_mapping as mapping
from canvas_mcp.mcp.projection import assignment_context, assignment_detail, envelope
from conftest import ORIGIN
from tests.unit.test_academic_application import FakeAcademic


@pytest.mark.parametrize(
    "html,expected",
    [
        (
            "<p>Keep bad grammer,, &amp; symbols.</p><p>Second.</p>",
            "Keep bad grammer,, & symbols.\n\nSecond.",
        ),
        ("<ol start='3'><li>First</li><li>Second</li></ol>", "3. First\n4. Second"),
        ("<ol reversed start='3'><li>First</li><li>Second</li></ol>", "3. First\n2. Second"),
        ("<ol reversed type='A'><li>First</li><li>Second</li></ol>", "B. First\nA. Second"),
        ("<p>x<sup>2</sup> + H<sub>2</sub>O.</p>", "x^(2) + H_(2)O."),
        ("<ul><li>A<ul><li>B</li></ul></li><li>C</li></ul>", "• A\n  • B\n• C"),
        (
            "<pre>if x:\n    print(x)\n\n\n# keep blanks</pre>",
            "if x:\n    print(x)\n\n\n# keep blanks",
        ),
        (
            "Visible<script>bad()</script><style>bad</style><span hidden>hidden</span><template>hidden</template>",
            "Visible",
        ),
        (
            'Read <a href="https://docs.python.org/3/">Python docs</a>.',
            "Read Python docs (https://docs.python.org/3/).",
        ),
        ("<p>Compute x // 2 and x / 2.</p>", "Compute x // 2 and x / 2."),
        ("<pre>x //2</pre>", "x //2"),
        ("<dl><dt>Term</dt><dd>Definition</dd></dl>", "Term\n\nDefinition"),
        ("Visible<math hidden><mi>x</mi></math>", "Visible"),
    ],
)
def test_visible_wording_structure_and_inert_links(html, expected):
    text, redacted, nontext = assignment_visible_text(html)
    assert text.text == expected
    assert not text.truncated and not redacted and not nontext


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.test/f?verifier=PRIVATE",
        "javascript:alert(PRIVATE)",
        "file:///PRIVATE",
        "//cdn.example.test/f?token=PRIVATE",
        "/courses/8/files/50?token=PRIVATE",
        "https://user:PRIVATE@example.test/f",
    ],
)
def test_capabilities_are_redacted_without_losing_label(url):
    text, redacted, _ = assignment_visible_text(f'<p>Read <a href="{url}">Instructions</a>.</p>')
    assert "Instructions" in text.text and "PRIVATE" not in text.text
    assert redacted


def test_truncation_is_explicit():
    text, _, _ = assignment_visible_text("a" * 16001)
    assert len(text.text) == 16000 and text.truncated


@pytest.mark.parametrize(
    "source",
    [
        "<math><msup><mi>x</mi><mn>2</mn></msup></math>",
        "<img class='equation_image' alt='x^{2}=4' src='/equation_images/x' />",
        "<svg><text>x2</text></svg>",
    ],
)
def test_nontext_math_is_not_silently_claimed_verbatim(source, academic_payloads):
    raw = {**academic_payloads["assignment"], "description": "Solve " + source}
    projected = assignment_detail(mapping.assignment(raw, "8", "7", ORIGIN))
    assert projected["description_nontext_content"] is True
    body = projected["description_verbatim_text"]["value"]["text"]
    assert "Solve x2" not in body
    assert "[Non-text source omitted" in body or "[Image alt text:" in body


@pytest.mark.parametrize("character", ["a", "я", "漢", "\\", '"'])
def test_maximum_source_survives_domain_and_wire_budgets(stack, academic_payloads, character):
    async def run():
        async with stack([]) as state:
            fake = FakeAcademic(academic_payloads)
            fake.assignment = mapping.assignment(
                {
                    **academic_payloads["assignment"],
                    "description": "<pre>" + character * 16000 + "</pre>",
                },
                "8",
                "7",
                ORIGIN,
            )
            result = await AcademicService(fake).get_assignment_context(state.ctx, "8", "10")
            output = envelope(result, assignment_context)
            source = output["data"]["assignment"]["description_verbatim_text"]["value"]
            assert source["text"] == character * 16000 and not source["truncated"]
            legacy = output["data"]["assignment"]["description"]["value"]
            assert legacy["truncated"] is (character != "a")

    asyncio.run(run())


@pytest.mark.parametrize(
    "description,available", [("", False), (None, False), ("<p>Work.</p>", True)]
)
def test_empty_description_is_distinct_from_unavailable(academic_payloads, description, available):
    raw = {**academic_payloads["assignment"], "description": description}
    result = assignment_detail(mapping.assignment(raw, "8", "7", ORIGIN))
    assert result["description_available"] is available
    del raw["description"]
    assert (
        assignment_detail(mapping.assignment(raw, "8", "7", ORIGIN))["description_available"]
        is None
    )


@pytest.mark.parametrize("count", [15, 20, 25])
def test_aggregate_wire_cost_preserves_source_and_attachments(stack, academic_payloads, count):
    async def run():
        async with stack([]) as state:
            raw = {
                **academic_payloads["assignment"],
                "description": "я" * 16000,
                "attachments": [
                    {**academic_payloads["attachment"], "id": i + 50} for i in range(count)
                ],
            }
            fake = FakeAcademic(academic_payloads)
            fake.assignment = mapping.assignment(raw, "8", "7", ORIGIN)
            # Exercise exact MCP aggregate adaptation independently of the
            # unchanged domain budget, which can reject very large attachment
            # metadata before projection (the same limit existed previously).
            result = Result(
                AssignmentContext(
                    fake.course,
                    fake.assignment,
                    fake.assignment.rubric,
                    fake.assignment.submission,
                    fake.assignment.attachments,
                    fake.sequence,
                ),
                state.ctx.request_id,
                state.ctx.as_of,
                True,
                (),
            )
            output = envelope(result, assignment_context)
            body = output["data"]["assignment"]["description_verbatim_text"]["value"]
            assert body["text"] == "я" * 16000 and not body["truncated"]
            assert len(output["data"]["attachments"]["value"]) == count

    asyncio.run(run())


def test_aggregate_candidates_preserve_authorization_and_do_not_fetch_files(
    stack, academic_payloads
):
    async def run():
        async with stack([]) as state:
            fake = FakeAcademic(academic_payloads)
            result = await AcademicService(fake).get_assignment_context(state.ctx, "8", "10")
            output = assignment_context(result.data)
            assert fake.calls == ["course", "assignment", "sequence"]
            assert len(output["material_candidates"]) == 1
            candidate = output["material_candidates"][0]
            assert candidate["relationship"] == "direct_attachment"
            assert candidate["file_reference"]["source_kind"] == "assignment_attachment"
            assert candidate["file_reference"]["source_id"] == 10
            # A body-only link does not gain assignment_attachment authority.
            output = assignment_context(
                replace(result.data, attachments=Observed(Availability.AVAILABLE, ()))
            )
            candidate = output["material_candidates"][0]
            assert candidate["relationship"] == "assignment_link"
            assert candidate["file_reference"]["source_kind"] == "course_file"
            assert candidate["file_reference"]["source_id"] is None
            assert not state.backend.writes

    asyncio.run(run())
