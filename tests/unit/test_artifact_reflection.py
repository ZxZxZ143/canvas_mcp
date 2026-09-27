"""Reject current capability echoes without censoring ordinary coursework links."""

from html import escape
from urllib.parse import quote

import pytest
from canvas_mcp.infrastructure.files.policy import reflects_capability

TARGET = "https://canvas.example/file?Signature=private&verifier=sensitive"


@pytest.mark.parametrize(
    "text",
    [
        TARGET,
        escape(TARGET),
        quote(TARGET, safe=""),
        quote(quote(TARGET, safe=""), safe=""),
        TARGET.replace("/", "\\/"),
        TARGET.partition("?")[2],
    ],
)
def test_common_encoded_reflections_reject(text):
    assert reflects_capability(text.encode(), TARGET)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_unicode_binary_reflection_rejects(encoding):
    assert reflects_capability(TARGET.encode(encoding), TARGET)


def test_ordinary_url_is_not_censored():
    assert not reflects_capability(b"https://canvas.example/lecture?view=student", TARGET)
