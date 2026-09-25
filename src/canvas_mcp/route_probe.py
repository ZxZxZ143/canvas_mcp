"""Opt-in local comparison of fixed Canvas download routes. No MCP surface."""

import argparse
import asyncio
import os

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.domain.errors import ApplicationError, ConfigurationError, DownloadRejectedError
from canvas_mcp.domain.mime import (
    ExpectedFormat,
    FormatEvidence,
    MimeRule,
    MimeScope,
    RedirectOriginClass,
    ResponseContentClass,
    RevalidationTicket,
)
from canvas_mcp.file_smoke import discover, validate_candidates
from canvas_mcp.infrastructure.files.download import DownloadRouteVariant
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from canvas_mcp.infrastructure.files.manager import FileDownloadManager
from canvas_mcp.infrastructure.files.mime_diagnostic import MimeDiagnostic


class _NoRuleRegistry:
    """Permit bounded format evidence without reading or changing MIME aliases."""

    def lookup(self, scope: MimeScope, expected: ExpectedFormat, http_mime: str) -> MimeRule | None:
        return None

    def begin_revalidation(
        self, scope: MimeScope, expected: ExpectedFormat, http_mime: str
    ) -> RevalidationTicket | None:
        return None

    def record_validated(
        self,
        scope: MimeScope,
        http_mime: str,
        evidence: FormatEvidence,
        *,
        remember: bool,
        ticket: RevalidationTicket | None,
    ) -> str:
        return "not_recorded"


def _print_variant(variant: DownloadRouteVariant, report: dict[str, object] | None) -> None:
    hops = report.get("redirect_hops") if report is not None else None
    hops = hops if isinstance(hops, list) else []
    last = hops[-1] if hops else None
    status = last.get("status") if isinstance(last, dict) else None
    status = status if type(status) is int and 100 <= status <= 599 else "none"
    content_class = last.get("response_content_type_class") if isinstance(last, dict) else None
    content_class = (
        content_class.value if isinstance(content_class, ResponseContentClass) else "other"
    )
    # Auth is expected on the exact Canvas origin until the first external hop,
    # then absent for the rest of the chain, including any return to Canvas.
    trusted = True
    auth_preserved = bool(hops)
    for hop in hops:
        if not isinstance(hop, dict):
            auth_preserved = False
            break
        if hop.get("origin_class") is RedirectOriginClass.EXTERNAL:
            trusted = False
        if hop.get("authorization_attached") is not trusted:
            auth_preserved = False
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
    detected = detected if content_class != "html" and detected in allowed else "not_inspected"
    print(
        f"variant={variant.value} status={status} redirect_count={max(0, len(hops) - 1)} "
        f"final_content_class={content_class} "
        f"auth_preserved={'true' if auth_preserved else 'false'} detected_format={detected}"
    )


async def run(*, route_parity: bool) -> int:
    if route_parity is not True:
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
            variants = [DownloadRouteVariant.CURRENT]
            if reference.source_kind != "submission_attachment":
                variants.append(DownloadRouteVariant.COURSE_FORCED)
            variants.append(DownloadRouteVariant.GLOBAL_FORCED)
            diagnostic = MimeDiagnostic(manager, _NoRuleRegistry())
            for variant in variants:
                report: dict[str, object] | None
                fatal = False
                try:
                    report = await diagnostic.probe(
                        connection._context(download=True),
                        reference,
                        allow_mismatch=True,
                        route_variant=variant,
                    )
                except ApplicationError as error:
                    report = diagnostic.report
                    fatal = not isinstance(error, DownloadRejectedError)
                _print_variant(variant, report)
                if (
                    fatal
                    or report is None
                    or report.get("state") != "erased"
                    or not report.get("redirect_hops")
                ):
                    return 1
            return 0
    except ApplicationError:
        return 1
    except Exception:
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--route-parity", action="store_true")
    args = parser.parse_args()
    if not args.live or not args.route_parity:
        parser.error("requires both --live and --route-parity")
    try:
        return asyncio.run(run(route_parity=True))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
