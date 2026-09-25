import asyncio
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from canvas_mcp import mime_probe
from canvas_mcp.composition import CanvasConnection
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from conftest import response
from test_files import COURSE, REF, Reply, file_stack as file_stack
from test_mime_diagnostic import MIME, metadata
from test_redirect_topology import NoBodyReply, redirect, forbid_quarantine


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--live"],
        ["--allow-mismatch-diagnostic"],
        ["--live", "--remember-if-validated"],
        ["--redirect-topology"],
        ["--live", "--redirect-topology"],
    ],
)
def test_cli_requires_both_flags_before_io(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["mime_probe", *arguments])
    monkeypatch.setattr(mime_probe, "open_canvas_connection", lambda *a: pytest.fail("network"))
    with pytest.raises(SystemExit) as error:
        mime_probe.main()
    assert error.value.code == 2


def test_run_rejects_nonliteral_optin_before_io(monkeypatch):
    monkeypatch.setattr(mime_probe, "open_canvas_connection", lambda *a: pytest.fail("network"))
    assert asyncio.run(mime_probe.run(allow_mismatch=False)) == 1
    assert asyncio.run(mime_probe.run(allow_mismatch=1)) == 1
    assert asyncio.run(mime_probe.run(allow_mismatch=True, redirect_topology=1)) == 1
    assert (
        asyncio.run(mime_probe.run(allow_mismatch=True, redirect_topology=True, remember=True)) == 1
    )


def test_cli_refuses_topology_with_remember_before_io(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mime_probe",
            "--live",
            "--allow-mismatch-diagnostic",
            "--redirect-topology",
            "--remember-if-validated",
        ],
    )
    monkeypatch.setattr(mime_probe, "open_canvas_connection", lambda *a: pytest.fail("network"))
    with pytest.raises(SystemExit) as error:
        mime_probe.main()
    assert error.value.code == 2


@pytest.mark.parametrize("failure", [False, True])
def test_cli_up_to_three_or_stop_at_first_nonmime_failure(
    file_stack, tmp_path, monkeypatch, capsys, failure
):
    body = b"%PDF-fixture"
    meta = metadata("pdf", body)
    selected = file_metadata(meta, REF)
    saved = {}

    @asynccontextmanager
    async def opened(env):
        replies = [
            Reply(
                (b"<html>" if failure and i == 0 else body,),
                headers=[(b"content-type", MIME.encode())],
            )
            for i in range(3)
        ]
        async with file_stack(
            replies,
            meta,
            api_extra=[response(COURSE), response(meta), response(COURSE), response(meta)],
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            saved["stack"] = s
            yield CanvasConnection(s.app, s.scope, s.settings, s.academic, s.files)

    async def discovery(*a, **k):
        return SimpleNamespace(identities=(REF, REF, REF))

    async def validation(*a, **k):
        return SimpleNamespace(candidates=(selected,) * 4)

    monkeypatch.setattr(mime_probe, "open_canvas_connection", opened)
    monkeypatch.setattr(mime_probe, "discover", discovery)
    monkeypatch.setattr(mime_probe, "validate_candidates", validation)
    monkeypatch.setenv("CANVAS_MIME_RUNTIME_DIRECTORY", str(tmp_path / "diagnostics"))
    assert asyncio.run(mime_probe.run(allow_mismatch=True, remember=True)) == (1 if failure else 0)
    assert len(saved["stack"].pool.calls) == (1 if failure else 3)
    assert len(list((tmp_path / "diagnostics").glob("mime-report-*.json"))) == 1
    if failure:
        assert not (tmp_path / "diagnostics" / "mime-compatibility.json").exists()
    output = capsys.readouterr().out
    assert "private-course-file" not in output and MIME not in output
    assert "mime_report" in output


@pytest.mark.parametrize("html", [False, True])
def test_topology_cli_traces_only_one_candidate_and_prints_only_classes(
    file_stack, tmp_path, monkeypatch, capsys, html
):
    import json
    from conftest import ORIGIN
    from canvas_mcp.infrastructure.files.policy import MEDIA

    meta = metadata("docx", b"inert")
    selected = file_metadata(meta, REF)
    saved = {}

    @asynccontextmanager
    async def opened(env):
        final_type = b"text/html" if html else MEDIA["docx"].encode()
        replies = [
            redirect(ORIGIN + "/authorized?signature=PRIVATE"),
            NoBodyReply(headers=[(b"content-type", final_type)]),
        ]
        async with file_stack(replies, meta, max_download_bytes=MAX_PROBE_BYTES) as s:
            saved["stack"] = s
            forbid_quarantine(monkeypatch, s)
            yield CanvasConnection(s.app, s.scope, s.settings, s.academic, s.files)

    async def discovery(*a, **k):
        return SimpleNamespace(identities=(REF,) * 3)

    async def validation(connection, identities):
        assert len(identities) == 3  # Bounded metadata selection, only one transfer below.
        return SimpleNamespace(candidates=(selected,) * 3)

    monkeypatch.setattr(mime_probe, "open_canvas_connection", opened)
    monkeypatch.setattr(mime_probe, "discover", discovery)
    monkeypatch.setattr(mime_probe, "validate_candidates", validation)
    monkeypatch.setenv("CANVAS_MIME_RUNTIME_DIRECTORY", str(tmp_path / "diagnostics"))
    monkeypatch.setattr(
        mime_probe,
        "JsonMimeCompatibilityRegistry",
        lambda *a: pytest.fail("topology must not load registry rules"),
    )
    assert asyncio.run(mime_probe.run(allow_mismatch=True, redirect_topology=True)) == int(html)
    assert len(saved["stack"].pool.calls) == 2
    report = json.loads(next((tmp_path / "diagnostics").glob("mime-report-*.json")).read_text())
    assert len(report["candidates"]) == 1 and len(report["candidates"][0]["redirect_hops"]) == 2
    assert not (tmp_path / "diagnostics" / "mime-compatibility.json").exists()
    output = capsys.readouterr().out
    assert "hop 0: canvas_origin / 302 / auth=yes / html" in output
    assert "hop 1: canvas_origin / 200 / auth=yes / " + ("html" if html else "binary") in output
    assert "PRIVATE" not in output and ORIGIN not in output and "private-course-file" not in output
    assert "PRIVATE" not in json.dumps(report) and "application/" not in json.dumps(report)
