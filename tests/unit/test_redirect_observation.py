from dataclasses import asdict
import json

import pytest

from canvas_mcp.domain.errors import DownloadRejectedError
from canvas_mcp.domain.mime import ExpectedFormat, MimeScope, RedirectOriginClass
from canvas_mcp.infrastructure.files.mime_observation import MimeObservation, response_content_class


@pytest.mark.parametrize(
    "mime,classification",
    [
        (b"text/html; private=https://private.example/path?secret=PRIVATE", "html"),
        (b"APPLICATION/XHTML+XML", "html"),
        (b"application/json", "json"),
        (b"application/x-ipynb+json", "json"),
        (b"application/octet-stream", "binary"),
        (b"binary/octet-stream", "binary"),
        (b"application/pdf", "binary"),
        (b"application/zip", "binary"),
        (b"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "binary"),
        (b"text/plain", "other"),
        (b"https://private.example/path?token=PRIVATE", "other"),
        (b"text/html/PRIVATE", "other"),
        (b"text/html" + b"x" * 1024, "other"),
        (b"\xffprivate", "other"),
    ],
    ids=[
        "html_parameters",
        "xhtml",
        "json",
        "notebook",
        "generic",
        "binary_alias",
        "pdf",
        "zip",
        "docx",
        "text",
        "url",
        "invalid",
        "oversized",
        "nonascii",
    ],
)
def test_header_classification_only_fixed_enum_never_payload(mime, classification):
    result = response_content_class([(b"Content-Type", mime)])
    assert result.value == classification
    assert "PRIVATE" not in json.dumps(result)


def test_missing_or_duplicate_mime_is_other():
    assert response_content_class([]).value == "other"
    assert (
        response_content_class(
            [(b"Content-Type", b"text/html"), (b"content-type", b"application/pdf")]
        ).value
        == "other"
    )


def test_observation_bounds_and_fixed_projection():
    observation = MimeObservation(
        MimeScope("https://canvas.example.edu", "personal", "7"),
        ExpectedFormat.DOCX,
        None,
        allow_mismatch=True,
        headers_only=True,
    )
    for index in range(4):
        observation.record_hop(index, RedirectOriginClass.CANVAS, 302, index == 0, [])
    with pytest.raises(DownloadRejectedError):
        observation.record_hop(4, RedirectOriginClass.CANVAS, 200, False, [])
    assert len(observation.hops) == 4
    assert "canvas.example.edu" not in json.dumps([asdict(hop) for hop in observation.hops])


@pytest.mark.parametrize("status", ["PRIVATE", True, 99, 600])
def test_invalid_status_never_projected(status):
    observation = MimeObservation(
        MimeScope("https://canvas.example.edu", "personal", "7"),
        ExpectedFormat.DOCX,
        None,
        allow_mismatch=True,
        headers_only=True,
    )
    with pytest.raises(DownloadRejectedError):
        observation.record_hop(0, RedirectOriginClass.CANVAS, status, True, [])
    assert not observation.hops
