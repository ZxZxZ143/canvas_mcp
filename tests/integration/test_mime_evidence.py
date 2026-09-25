"""Accepted policy matrix is unchanged; only mismatch source diagnostics differ."""

import asyncio

import pytest

from canvas_mcp.domain.errors import (
    DownloadRejectedError,
    DownloadRejectionReason as Reason,
    MimeMismatchReason as Mime,
)
from test_files import FILE, REF, TOKEN, Reply
from test_files import file_stack as file_stack
from test_mime_diagnostic import archive


@pytest.mark.parametrize(
    "filename,canvas_mime,http_mime,body,classification",
    [
        ("PRIVATE.pdf", "application/pdf", "application/pdf", b"%PDF-data", "untrusted_document"),
        (
            "PRIVATE.pdf",
            "application/pdf",
            "application/octet-stream",
            b"%PDF-data",
            "untrusted_document",
        ),
        (
            "PRIVATE.pdf",
            "application/octet-stream",
            "application/octet-stream",
            b"%PDF-data",
            "untrusted_document",
        ),
        ("PRIVATE.txt", "text/plain", "application/octet-stream", b"inert text", "untrusted_text"),
        ("PRIVATE.json", "application/json", "text/plain", b"inert data", "untrusted_text"),
        (
            "PRIVATE.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/octet-stream",
            archive("[Content_Types].xml", "word/document.xml"),
            "office_candidate",
        ),
    ],
)
def test_agreement_or_approved_generic_mime_still_requires_inert_evidence(
    file_stack, filename, canvas_mime, http_mime, body, classification
):
    async def run():
        data = dict(
            FILE, filename=filename, display_name="PRIVATE.exe", **{"content-type": canvas_mime}
        )
        async with file_stack(
            [Reply([body], headers=[(b"content-type", http_mime.encode())])], metadata=data
        ) as s:
            item = (await s.files.download_file(s.ctx, REF)).data
            assert item.classification == classification and item.trust == "untrusted"
            await s.files.cleanup_download(s.ctx, item.artifact_id)
            assert not s.store._pending and not s.store._records
            for private in ("PRIVATE", canvas_mime, http_mime, TOKEN):
                assert private not in s.logs.getvalue()

    asyncio.run(run())


@pytest.mark.parametrize(
    "canvas_mime,http_mime,body,reason,subreason",
    [
        (
            "text/plain",
            "application/pdf",
            b"%PDF-data",
            Reason.MIME_MISMATCH,
            Mime.CANVAS_MIME_VS_EXTENSION,
        ),
        (
            "application/pdf",
            "text/plain",
            b"%PDF-data",
            Reason.MIME_MISMATCH,
            Mime.HTTP_MIME_VS_EXTENSION,
        ),
        (
            "application/pdf",
            "binary/octet-stream",
            b"%PDF-data",
            Reason.MIME_MISMATCH,
            Mime.HTTP_MIME_VS_EXTENSION,
        ),
        ("application/pdf", "", b"%PDF-data", Reason.MIME_MISMATCH, Mime.HTTP_MIME_MISSING),
        ("application/pdf", "application/octet-stream", b"wrong", Reason.PREFIX_MISMATCH, None),
        ("application/pdf", "application/pdf", b"wrong", Reason.PREFIX_MISMATCH, None),
        (
            "application/pdf",
            "application/octet-stream",
            b"<html>PRIVATE",
            Reason.UNSAFE_PREFIX,
            None,
        ),
    ],
)
def test_mime_and_prefix_contradictions_keep_precise_private_free_categories(
    file_stack, canvas_mime, http_mime, body, reason, subreason
):
    async def run():
        data = dict(FILE, filename="PRIVATE.pdf", **{"content-type": canvas_mime})
        async with file_stack(
            [Reply([body], headers=[(b"content-type", http_mime.encode())])], metadata=data
        ) as s:
            with pytest.raises(DownloadRejectedError) as caught:
                await s.files.download_file(s.ctx, REF)
            error = caught.value
            assert error.reason is reason and error.mime_reason is subreason
            assert error.diagnostic_code == reason.value + (
                "/" + subreason.value if subreason else ""
            )
            assert error.sanitized().mime_reason is subreason
            assert not s.store._pending and not s.store._records
            assert not list(s.settings.download_directory.rglob("*.part"))
            assert not list(s.settings.download_directory.rglob("*.blob"))
            output = error.diagnostic_code + repr(vars(error)) + s.logs.getvalue()
            for private in (
                "PRIVATE",
                "application/pdf",
                "application/octet-stream",
                "text/plain",
                TOKEN,
            ):
                assert private not in output

    asyncio.run(run())


@pytest.mark.parametrize(
    "body,reason",
    [
        (b"\xff", Reason.INVALID_TEXT),
        (b"\x00", Reason.INVALID_TEXT),
        (b"<svg>PRIVATE", Reason.UNSAFE_PREFIX),
    ],
)
def test_generic_mime_does_not_override_text_evidence(file_stack, body, reason):
    async def run():
        async with file_stack(
            [Reply([body], headers=[(b"content-type", b"application/octet-stream")])]
        ) as s:
            with pytest.raises(DownloadRejectedError) as caught:
                await s.files.download_file(s.ctx, REF)
            assert caught.value.reason is reason and caught.value.mime_reason is None
            assert not s.store._pending and not s.store._records

    asyncio.run(run())


@pytest.mark.parametrize("subreason", list(Mime))
def test_safe_subreason_survives_multiple_sanitization_boundaries(subreason):
    error = DownloadRejectedError(reason=Reason.MIME_MISMATCH, mime_reason=subreason)
    clean = error.sanitized().sanitized()
    assert clean.diagnostic_code == "mime_evidence_mismatch/" + subreason.value
    assert str(clean) == clean.code == "download_rejected"
    assert clean.__cause__ is clean.__context__ is None


@pytest.mark.parametrize(
    "value", ["application/pdf", "PRIVATE.pdf", "https://PRIVATE/?token=SECRET", {}, 123]
)
def test_subreason_rejects_private_arbitrary_values(value):
    with pytest.raises(TypeError) as caught:
        DownloadRejectedError(reason=Reason.MIME_MISMATCH, mime_reason=value)
    assert str(caught.value) == ""


def test_mime_subreason_cannot_be_attached_to_another_failure():
    with pytest.raises(TypeError):
        DownloadRejectedError(
            reason=Reason.CREDENTIAL_REFLECTION, mime_reason=Mime.HTTP_MIME_VS_EXTENSION
        )
