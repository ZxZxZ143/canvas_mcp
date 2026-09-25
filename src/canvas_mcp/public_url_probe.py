"""Local, one-candidate Canvas public_url parity diagnostic. No MCP surface."""

import argparse
import asyncio
import os

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.domain.errors import (
    ApplicationError,
    ConfigurationError,
    DownloadRejectedError,
    PublicUrlOriginUnapprovedError,
)
from canvas_mcp.domain.mime import DiagnosticStatus, ResponseContentClass
from canvas_mcp.file_smoke import discover, validate_candidates
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from canvas_mcp.infrastructure.files.manager import FileDownloadManager
from canvas_mcp.infrastructure.files.mime_diagnostic import MimeDiagnostic
from canvas_mcp.route_probe import _NoRuleRegistry


def _last(report: dict[str, object] | None) -> tuple[str, str]:
    hops = report.get("redirect_hops") if report is not None else None
    if not isinstance(hops, list) or not hops or not isinstance(hops[-1], dict):
        return "none", "other"
    last = hops[-1]
    status = last.get("status")
    content = last.get("response_content_type_class")
    return (
        str(status) if type(status) is int and 100 <= status <= 599 else "none",
        content.value if isinstance(content, ResponseContentClass) else "other",
    )


def _format(report: dict[str, object] | None, content_class: str) -> str:
    detected = report.get("detected_format") if report is not None else None
    allowed = {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "opaque_zip",
        "utf8_text",
        "json",
        "ipynb",
        "unrecognized",
        "unsafe",
    }
    return detected if content_class != "html" and detected in allowed else "not_inspected"


async def run(*, public_url_parity: bool) -> int:
    if public_url_parity is not True:
        return 1
    try:
        env = dict(os.environ)
        env["MAX_DOWNLOAD_BYTES"] = str(
            min(int(env.get("MAX_DOWNLOAD_BYTES", str(MAX_PROBE_BYTES))), MAX_PROBE_BYTES)
        )
        async with open_canvas_connection(env) as connection:
            await connection.get_profile()
            manager = connection._files()._downloads
            if not isinstance(manager, FileDownloadManager):
                raise ConfigurationError()
            discovery = await discover(connection, collect_samples=True)
            validation = await validate_candidates(connection, discovery.identities)
            if not validation.candidates:
                return 1
            reference = validation.candidates[0].source
            diagnostic = MimeDiagnostic(manager, _NoRuleRegistry())
            browser: dict[str, object] | None
            try:
                browser = await diagnostic.probe(
                    connection._context(download=True), reference, allow_mismatch=True
                )
            except ApplicationError as error:
                browser = diagnostic.report
                if not isinstance(error, DownloadRejectedError):
                    return 1
            browser_status, browser_class = _last(browser)
            print(
                f"variant=browser_route api_authorized={'true' if browser_status != 'none' else 'false'} "
                f"status={browser_status} final_class={browser_class}"
            )
            if browser is None or browser.get("state") != "erased":
                return 1
            probe_error: ApplicationError | None = None
            public: dict[str, object] | None
            try:
                public = await diagnostic.probe(
                    connection._context(download=True),
                    reference,
                    allow_mismatch=True,
                    public_url=True,
                )
            except ApplicationError as exc:
                probe_error = exc
                public = diagnostic.report
            received = diagnostic.capability_received or isinstance(
                probe_error, PublicUrlOriginUnapprovedError
            )
            status, content_class = _last(public)
            mismatch = public is not None and public.get("mime_mismatch") is True
            reason = (
                probe_error.diagnostic_code
                if probe_error is not None
                else "mime_evidence_mismatch"
                if mismatch
                else "none"
            )
            print(
                f"variant=public_url api_authorized={'true' if received else 'false'} "
                f"public_url_received={'true' if received else 'false'} "
                f"download_status={status} final_class={content_class} "
                f"detected_format={_format(public, content_class)} reason={reason}"
            )
            return int(
                probe_error is not None
                or public is None
                or public.get("state") != "erased"
                or public.get("result") != DiagnosticStatus.EXPECTED.value
                or mismatch
            )
    except ApplicationError:
        return 1
    except Exception:
        return 1


async def run_origin(*, public_url_origin: bool) -> int:
    """Show only the exact origin to the local operator for manual approval."""
    if public_url_origin is not True:
        return 1
    try:
        async with open_canvas_connection(dict(os.environ)) as connection:
            await connection.get_profile()
            manager = connection._files()._downloads
            if not isinstance(manager, FileDownloadManager):
                raise ConfigurationError()
            discovery = await discover(connection, collect_samples=True)
            validation = await validate_candidates(connection, discovery.identities)
            if not validation.candidates:
                print("public_url_origin=unavailable reason=no_candidate")
                return 1
            reference = validation.candidates[0].source
            origin = await manager._provider.inspect_file_capability_origin(
                connection._context(download=True), reference
            )
            print(f"public_url_origin={origin}")
            return 0
    except ApplicationError as error:
        print(f"public_url_origin=unavailable reason={error.diagnostic_code}")
        return 1
    except Exception:
        print("public_url_origin=unavailable reason=internal_error")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--public-url-parity", action="store_true")
    parser.add_argument("--public-url-origin", action="store_true")
    args = parser.parse_args()
    if not args.live or args.public_url_parity == args.public_url_origin:
        parser.error("requires --live and exactly one public URL mode")
    try:
        if args.public_url_origin:
            return asyncio.run(run_origin(public_url_origin=True))
        return asyncio.run(run(public_url_parity=True))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
