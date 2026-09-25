"""Bounded redirect topology and explicit same-origin credential state."""

import asyncio
import json

import pytest

from canvas_mcp.domain.errors import ApplicationError, ConfigurationError, DownloadRejectedError
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from canvas_mcp.infrastructure.files.mime_diagnostic import MimeDiagnostic
from canvas_mcp.infrastructure.files.mime_runtime import MimeRuntime
from canvas_mcp.infrastructure.files.policy import MEDIA
from conftest import ORIGIN
from test_files import CDN, CDN2, REF, TOKEN, Pool, Reply, file_stack as file_stack
from test_mime_diagnostic import metadata


class NoBodyReply(Reply):
    async def aiter_stream(self):
        pytest.fail("topology must never iterate a response body")
        yield b"unreachable"


class NoRegistry:
    def __getattr__(self, name):
        pytest.fail("topology must never use or mutate MIME rules")


def redirect(target):
    return NoBodyReply(
        status=302,
        headers=[
            (b"location", target.encode("utf-8")),
            (b"content-type", b'text/html; filename="PRIVATE"'),
            (b"set-cookie", b"session=PRIVATE"),
        ],
    )


def forbid_quarantine(monkeypatch, s):
    def forbid(*a, **k):
        pytest.fail("topology must never create/read/write/publish quarantine")

    for name in ("begin", "write", "inspect_pending", "finish"):
        monkeypatch.setattr(s.store, name, forbid)
    monkeypatch.setattr("canvas_mcp.infrastructure.files.mime_diagnostic.identify", forbid)


@pytest.mark.parametrize(
    "targets,origins,expected_auth",
    [
        (
            [ORIGIN + "/authorized-download?signature=PRIVATE", ORIGIN + "/final?secret=PRIVATE"],
            ["canvas_origin"] * 3,
            [True, True, True],
        ),
        (
            [CDN + "/private?signature=PRIVATE"],
            ["canvas_origin", "approved_external_origin"],
            [True, False],
        ),
        (
            [CDN + "/private", ORIGIN + "/return"],
            ["canvas_origin", "approved_external_origin", "canvas_origin"],
            [True, False, False],
        ),
        (
            [ORIGIN + "/same", CDN + "/external", CDN2 + "/final"],
            [
                "canvas_origin",
                "canvas_origin",
                "approved_external_origin",
                "approved_external_origin",
            ],
            [True, True, False, False],
        ),
        (
            ["/relative?signature=PRIVATE", "HTTPS://CANVAS.EXAMPLE.EDU:443/final"],
            ["canvas_origin"] * 3,
            [True, True, True],
        ),
    ],
    ids=["same_origin", "external", "external_then_canvas", "mixed", "canonical_same_origin"],
)
@pytest.mark.parametrize("html", [False, True])
def test_headers_only_topology_auth_and_no_body_or_learning(
    file_stack, tmp_path, monkeypatch, targets, origins, expected_auth, html
):
    async def run():
        final_mime = b"text/html" if html else MEDIA["docx"].encode()
        replies = [
            *(redirect(target) for target in targets),
            NoBodyReply(headers=[(b"content-type", final_mime)]),
        ]
        async with file_stack(
            replies, metadata("docx", b"inert"), max_download_bytes=MAX_PROBE_BYTES
        ) as s:
            forbid_quarantine(monkeypatch, s)
            diagnostic = MimeDiagnostic(s.manager, NoRegistry())
            if html:
                with pytest.raises(DownloadRejectedError):
                    await diagnostic.probe(s.ctx, REF, allow_mismatch=True, headers_only=True)
                record = diagnostic.report
                assert record["result"] == "REJECTED"
                assert record["reason"] == "mime_evidence_mismatch/http_mime_vs_extension"
            else:
                record = await diagnostic.probe(s.ctx, REF, allow_mismatch=True, headers_only=True)
                assert record["result"] == "HEADERS_ONLY"
            assert record["state"] == "not_downloaded"
            hops = record["redirect_hops"]
            assert [h["origin_class"] for h in hops] == origins
            assert [h["status"] for h in hops] == [302] * len(targets) + [200]
            assert [h["authorization_attached"] for h in hops] == expected_auth
            assert hops[-1]["response_content_type_class"] == ("html" if html else "binary")
            assert [h["hop_index"] for h in hops] == list(range(len(hops)))
            assert all(
                set(h)
                == {
                    "hop_index",
                    "origin_class",
                    "status",
                    "authorization_attached",
                    "response_content_type_class",
                }
                for h in hops
            )
            for index, call in enumerate(s.pool.calls):
                assert hops[index]["authorization_attached"] == ("Authorization" in call[2])
                assert "Cookie" not in call[2] and "Referer" not in call[2]
                if not expected_auth[index]:
                    assert TOKEN not in repr(call)
            assert len(s.pool.calls) == len(hops)
            assert not s.store._pending and not s.store._records
            assert not (tmp_path / "owned").exists()
            with MimeRuntime(tmp_path / "diagnostics") as runtime:
                runtime.write_report([record])
            exposed = json.dumps(record) + s.logs.getvalue()
            exposed += next((tmp_path / "diagnostics").glob("mime-report-*.json")).read_text()
            for private in (
                "PRIVATE",
                "signature",
                TOKEN,
                ORIGIN,
                CDN,
                "private-course-file",
                "location",
                "cookie",
                "sha256",
                "size",
            ):
                assert private not in exposed

    asyncio.run(run())


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/f?signature=PRIVATE",
        "http://canvas.example.edu/f",
        "https://canvas.example.edu:444/f",
        "https://canvas.example.edu.evil.example/f",
        "https://canvas.example.edu@evil.example/f",
        "https://user@canvas.example.edu/f",
        "https://canvas.example.edu./f",
        "https://%63anvas.example.edu/f",
        "https://cаnvas.example.edu/f",  # Cyrillic a is not an origin alias.
        "https://xn--canvs-yqa.example.edu/f",
        "https://127.0.0.1/f",
        CDN + "/f?token=" + TOKEN,
    ],
)
def test_rejected_target_never_appears_as_contacted_or_receives_auth(
    file_stack, tmp_path, monkeypatch, target
):
    async def run():
        async with file_stack(
            [redirect(target)], metadata("docx", b"inert"), max_download_bytes=MAX_PROBE_BYTES
        ) as s:
            forbid_quarantine(monkeypatch, s)
            diagnostic = MimeDiagnostic(s.manager, NoRegistry())
            with pytest.raises(ApplicationError):
                await diagnostic.probe(s.ctx, REF, allow_mismatch=True, headers_only=True)
            assert len(s.pool.calls) == 1
            assert len(diagnostic.report["redirect_hops"]) == 1
            assert diagnostic.report["redirect_hops"][0]["authorization_attached"] is True
            assert diagnostic.report["reason"] == "unsafe_redirect"
            assert target not in json.dumps(diagnostic.report)

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 403, 404, 500])
@pytest.mark.parametrize("redirected", [False, True])
def test_error_status_is_recorded_before_rejection_without_body(
    file_stack, monkeypatch, status, redirected
):
    async def run():
        replies = [redirect(ORIGIN + "/final")] if redirected else []
        replies.append(NoBodyReply(status=status, headers=[(b"content-type", b"application/json")]))
        async with file_stack(
            replies, metadata("docx", b"inert"), max_download_bytes=MAX_PROBE_BYTES
        ) as s:
            forbid_quarantine(monkeypatch, s)
            diagnostic = MimeDiagnostic(s.manager, NoRegistry())
            with pytest.raises(ApplicationError):
                await diagnostic.probe(s.ctx, REF, allow_mismatch=True, headers_only=True)
            last = diagnostic.report["redirect_hops"][-1]
            assert last["status"] == status and last["response_content_type_class"] == "json"
            assert last["authorization_attached"] is True
            assert s.provider._invalidated == (status == 401)

    asyncio.run(run())


def test_max_hops_stays_bounded(file_stack, monkeypatch):
    async def run():
        async with file_stack(
            [redirect(ORIGIN + "/step" + str(i)) for i in range(4)],
            metadata("docx", b"inert"),
            max_download_bytes=MAX_PROBE_BYTES,
        ) as s:
            forbid_quarantine(monkeypatch, s)
            diagnostic = MimeDiagnostic(s.manager, NoRegistry())
            with pytest.raises(ApplicationError):
                await diagnostic.probe(s.ctx, REF, allow_mismatch=True, headers_only=True)
            assert len(s.pool.calls) == len(diagnostic.report["redirect_hops"]) == 4
            assert diagnostic.report["reason"] == "unsafe_redirect"

    asyncio.run(run())


def test_trace_remember_is_rejected_before_authorization_or_network(file_stack):
    async def run():
        async with file_stack(max_download_bytes=MAX_PROBE_BYTES) as s:
            with pytest.raises(ConfigurationError):
                await MimeDiagnostic(s.manager, NoRegistry()).probe(
                    s.ctx, REF, allow_mismatch=True, headers_only=True, remember=True
                )
            assert not s.pool.calls and not s.backend.writes

    asyncio.run(run())


@pytest.mark.parametrize("external", [False, True])
def test_private_canvas_pool_only_while_chain_trusted(file_stack, monkeypatch, external):
    async def run():
        final = NoBodyReply(headers=[(b"content-type", MEDIA["docx"].encode())])
        async with file_stack(
            [],
            metadata("docx", b"inert"),
            max_download_bytes=MAX_PROBE_BYTES,
            allow_private_origin=True,
            trusted_private_ips=("192.168.4.200",),
        ) as s:
            forbid_quarantine(monkeypatch, s)
            assert s.downloader._initial_pool is not None
            await s.downloader._initial_pool.aclose()
            private = Pool(
                [redirect(CDN + "/external")]
                if external
                else [redirect(ORIGIN + "/a"), redirect(ORIGIN + "/b"), final]
            )
            s.downloader._initial_pool = private
            s.pool.replies = [redirect(ORIGIN + "/return"), final] if external else []
            report = await MimeDiagnostic(s.manager, NoRegistry()).probe(
                s.ctx, REF, allow_mismatch=True, headers_only=True
            )
            assert [h["authorization_attached"] for h in report["redirect_hops"]] == (
                [True, False, False] if external else [True, True, True]
            )
            assert len(private.calls) == (1 if external else 3)
            assert len(s.pool.calls) == (2 if external else 0)
            assert all("Authorization" in call[2] for call in private.calls)
            assert all("Authorization" not in call[2] for call in s.pool.calls)
            assert TOKEN not in json.dumps(report) + s.logs.getvalue()

    asyncio.run(run())
