import asyncio
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from canvas_mcp import route_probe
from canvas_mcp.composition import CanvasConnection
from canvas_mcp.domain.errors import ConfigurationError, StorageError, ValidationError
from canvas_mcp.domain.models import FileReference
from canvas_mcp.domain.mime import RedirectOriginClass, ResponseContentClass
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata
from canvas_mcp.infrastructure.files.download import (
    DownloadRouteVariant,
    controlled_download_target,
)
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from canvas_mcp.infrastructure.files.policy import MEDIA
from conftest import ORIGIN, response
from test_files import CDN, COURSE, REF, TOKEN, Reply, file_stack as file_stack, redirect
from test_mime_diagnostic import archive, metadata


def test_fixed_route_shapes_and_no_caller_query_injection():
    meta = file_metadata(metadata("docx", b"inert"), REF)
    assert controlled_download_target(ORIGIN, meta, DownloadRouteVariant.CURRENT) == (
        ORIGIN + "/courses/8/files/10/download"
    )
    assert controlled_download_target(ORIGIN, meta, DownloadRouteVariant.COURSE_FORCED) == (
        ORIGIN + "/courses/8/files/10/download?download_frd=1"
    )
    assert controlled_download_target(ORIGIN, meta, DownloadRouteVariant.GLOBAL_FORCED) == (
        ORIGIN + "/files/10/download?download_frd=1"
    )
    submission = replace(meta, source=FileReference("10", "8", "submission_attachment", "9"))
    assert controlled_download_target(ORIGIN, submission, DownloadRouteVariant.CURRENT) == (
        ORIGIN + "/files/10/download"
    )
    assert controlled_download_target(ORIGIN, submission, DownloadRouteVariant.GLOBAL_FORCED) == (
        ORIGIN + "/files/10/download?download_frd=1"
    )
    with pytest.raises(ConfigurationError):
        controlled_download_target(ORIGIN, submission, DownloadRouteVariant.COURSE_FORCED)
    with pytest.raises(ConfigurationError):
        controlled_download_target(ORIGIN, meta, "B")
    for bad in (
        FileReference("10?download_frd=0", "8"),
        FileReference("10", "8?preview=1"),
        FileReference("10", "8", "course_file", "1"),
    ):
        with pytest.raises(ValidationError):
            controlled_download_target(
                ORIGIN, replace(meta, source=bad), DownloadRouteVariant.COURSE_FORCED
            )


@pytest.mark.parametrize("args", [[], ["--live"], ["--route-parity"]])
def test_route_cli_requires_opt_in_before_io(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["route_probe", *args])
    monkeypatch.setattr(route_probe, "open_canvas_connection", lambda *a: pytest.fail("network"))
    with pytest.raises(SystemExit) as error:
        route_probe.main()
    assert error.value.code == 2


def test_parity_reports_only_safe_fields_and_validated_body(file_stack, monkeypatch, capsys):
    body = archive("[Content_Types].xml", "word/document.xml")
    meta = metadata("docx", body)
    selected = file_metadata(meta, REF)
    saved = {}

    @asynccontextmanager
    async def opened(env):
        replies = [
            Reply((b"<html>wrapped</html>",), headers=[(b"content-type", b"text/html")]),
            redirect(ORIGIN + "/same?signature=PRIVATE"),
            Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())]),
            redirect(CDN + "/asset?signature=PRIVATE"),
            Reply((body,), headers=[(b"content-type", MEDIA["docx"].encode())]),
        ]
        async with file_stack(
            replies,
            meta,
            api_extra=[response(COURSE), response(meta), response(COURSE), response(meta)],
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            saved["stack"] = s
            yield CanvasConnection(s.app, s.scope, s.settings, s.academic, s.files)

    async def discovery(*args, **kwargs):
        return SimpleNamespace(identities=(REF,))

    async def validation(*args, **kwargs):
        return SimpleNamespace(candidates=(selected,))

    monkeypatch.setattr(route_probe, "open_canvas_connection", opened)
    monkeypatch.setattr(route_probe, "discover", discovery)
    monkeypatch.setattr(route_probe, "validate_candidates", validation)
    assert asyncio.run(route_probe.run(route_parity=True)) == 0
    calls = saved["stack"].pool.calls
    assert len(calls) == 5
    assert ["Authorization" in call[2] for call in calls] == [True, True, True, True, False]
    assert calls[0][1] == ORIGIN + "/courses/8/files/10/download"
    assert calls[1][1] == ORIGIN + "/courses/8/files/10/download?download_frd=1"
    assert calls[3][1] == ORIGIN + "/files/10/download?download_frd=1"
    assert all("NEVER_FETCH" not in call[1] and "DO_NOT_RETURN" not in call[1] for call in calls)
    assert TOKEN not in repr(calls[4])
    assert not saved["stack"].store._pending and not saved["stack"].store._records
    output = capsys.readouterr().out
    assert (
        "variant=A status=200 redirect_count=0 final_content_class=html auth_preserved=true detected_format=not_inspected"
        in output
    )
    assert (
        "variant=B status=200 redirect_count=1 final_content_class=binary auth_preserved=true detected_format=docx"
        in output
    )
    assert (
        "variant=C status=200 redirect_count=1 final_content_class=binary auth_preserved=true detected_format=docx"
        in output
    )
    for private in ("PRIVATE", TOKEN, ORIGIN, CDN, "10", "8", "private-course-file", "NEVER_FETCH"):
        assert private not in output


def test_pdf_bytes_remain_accepted_by_production_route(file_stack):
    async def run():
        body = b"%PDF-1.7\nfixture"
        async with file_stack(
            [Reply((body,), headers=[(b"content-type", MEDIA["pdf"].encode())])],
            metadata("pdf", body),
        ) as s:
            result = (await s.files.download_file(s.ctx, REF)).data
            assert s.pool.calls[0][1] == ORIGIN + "/courses/8/files/10/download"
            await s.files.cleanup_download(s.ctx, result.artifact_id)

    asyncio.run(run())


def test_cleanup_unconfirmed_stops_parity(file_stack, monkeypatch, capsys):
    selected = file_metadata(metadata("docx", b"inert"), REF)
    calls = []

    @asynccontextmanager
    async def opened(env):
        async with file_stack(max_download_bytes=MAX_PROBE_BYTES) as s:
            yield CanvasConnection(s.app, s.scope, s.settings, s.academic, s.files)

    async def discovery(*args, **kwargs):
        return SimpleNamespace(identities=(REF,))

    async def validation(*args, **kwargs):
        return SimpleNamespace(candidates=(selected,))

    class FailedDiagnostic:
        def __init__(self, *args, **kwargs):
            self.report = None

        async def probe(self, *args, **kwargs):
            calls.append(kwargs["route_variant"])
            self.report = {
                "state": "cleanup_unconfirmed",
                "redirect_hops": [
                    {
                        "status": 200,
                        "origin_class": RedirectOriginClass.CANVAS,
                        "authorization_attached": True,
                        "response_content_type_class": ResponseContentClass.BINARY,
                    }
                ],
            }
            raise StorageError()

    monkeypatch.setattr(route_probe, "open_canvas_connection", opened)
    monkeypatch.setattr(route_probe, "discover", discovery)
    monkeypatch.setattr(route_probe, "validate_candidates", validation)
    monkeypatch.setattr(route_probe, "MimeDiagnostic", FailedDiagnostic)
    assert asyncio.run(route_probe.run(route_parity=True)) == 1
    assert calls == [DownloadRouteVariant.CURRENT]
    assert "variant=A status=200" in capsys.readouterr().out
