"""Opt-in local MIME diagnostic or body-free redirect topology. No MCP tool."""

import argparse
import asyncio
import os
from pathlib import Path

from canvas_mcp.composition import open_canvas_connection
from canvas_mcp.domain.errors import ApplicationError, ConfigurationError
from canvas_mcp.domain.mime import DiagnosticStatus
from canvas_mcp.file_smoke import MAX_SAMPLE_CANDIDATES, discover, step, validate_candidates
from canvas_mcp.infrastructure.files.format_probe import MAX_PROBE_BYTES
from canvas_mcp.infrastructure.files.manager import FileDownloadManager
from canvas_mcp.infrastructure.files.mime_diagnostic import MimeDiagnostic
from canvas_mcp.infrastructure.files.mime_registry import JsonMimeCompatibilityRegistry
from canvas_mcp.infrastructure.files.mime_runtime import MimeRuntime


def print_redirect_topology(record: dict[str, object]) -> None:
    """Only the diagnostic's fixed hop projection reaches the local console."""
    hops = record.get("redirect_hops", [])
    assert isinstance(hops, list)
    for hop in hops:
        print(
            f"hop {hop['hop_index']}: {hop['origin_class'].value} / {hop['status']} / "
            f"auth={'yes' if hop['authorization_attached'] else 'no'} / "
            f"{hop['response_content_type_class'].value}"
        )


async def run(
    *, allow_mismatch: bool, remember: bool = False, redirect_topology: bool = False
) -> int:
    # Gate before configuration, directories, credentials, or network access.
    if (
        allow_mismatch is not True
        or type(remember) is not bool
        or type(redirect_topology) is not bool
        or (redirect_topology and remember)
    ):
        step("mime_diagnostic", "FAIL", "explicit_opt_in_required")
        return 1
    records: list[dict[str, object]] = []
    try:
        env = dict(os.environ)
        env["MAX_DOWNLOAD_BYTES"] = str(
            min(int(env.get("MAX_DOWNLOAD_BYTES", str(MAX_PROBE_BYTES))), MAX_PROBE_BYTES)
        )
        root = Path(env.get("CANVAS_MIME_RUNTIME_DIRECTORY", r"C:\canvas_mcp_runtime\diagnostics"))
        profile = env.get("CANVAS_APPLICATION_PROFILE", "personal")
        async with open_canvas_connection(env) as connection:
            await connection.get_profile()
            manager = connection._files()._downloads
            if not isinstance(manager, FileDownloadManager):
                raise ConfigurationError()
            token = await manager._client._api._access_token(
                connection._context(), connection._settings.request_timeout_seconds
            )
            if token.value.casefold() in str(root).casefold():
                raise ConfigurationError()
            del token
            with MimeRuntime(root) as runtime:
                registry = None if redirect_topology else JsonMimeCompatibilityRegistry(runtime)
                diagnostic = MimeDiagnostic(manager, registry, application_profile=profile)
                discovery = await discover(connection, collect_samples=True)
                candidate_limit = 1 if redirect_topology else MAX_SAMPLE_CANDIDATES
                validation = await validate_candidates(connection, discovery.identities)
                if not validation.candidates:
                    step("mime_diagnostic", "NOT_TESTED", "no_metadata_valid_candidate")
                    return 1
                failed = False
                try:
                    for selected in validation.candidates[:candidate_limit]:
                        try:
                            record = await diagnostic.probe(
                                connection._context(download=True),
                                selected.source,
                                allow_mismatch=True,
                                remember=remember,
                                headers_only=redirect_topology,
                            )
                        except ApplicationError:
                            if diagnostic.report is not None:
                                records.append(diagnostic.report)
                            raise  # Non-MIME failure: stop, never try another candidate.
                        records.append(record)
                        step("mime_candidate", str(record["result"]))
                        if record["result"] != (
                            "HEADERS_ONLY" if redirect_topology else DiagnosticStatus.EXPECTED.value
                        ):
                            failed = True
                            break
                finally:
                    if records:
                        runtime.write_report(records)
                        step("mime_report", "WRITTEN")
                        if redirect_topology:
                            print_redirect_topology(records[0])
                return 1 if failed else 0
    except ApplicationError as error:
        step("mime_diagnostic", "FAIL", error.diagnostic_code)
        return 1
    except Exception:
        step("mime_diagnostic", "FAIL", "internal_error")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-mismatch-diagnostic", action="store_true")
    parser.add_argument(
        "--redirect-topology",
        action="store_true",
        help="one candidate; response headers only, no body persistence or MIME learning",
    )
    parser.add_argument(
        "--remember-if-validated",
        action="store_true",
        help="remember only a verified supported-format MIME alias for this account",
    )
    args = parser.parse_args()
    if not args.live or not args.allow_mismatch_diagnostic:
        parser.error("requires both --live and --allow-mismatch-diagnostic")
    if args.redirect_topology and args.remember_if_validated:
        parser.error("redirect topology cannot be combined with remembering compatibility")
    try:
        return asyncio.run(
            run(
                allow_mismatch=True,
                remember=args.remember_if_validated,
                redirect_topology=args.redirect_topology,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
