"""Explicit developer-only profile transport parity: --live [--details]. No files/MCP."""

import argparse
import asyncio
import io
import json
import os
import sys
from collections.abc import Mapping
from typing import cast

import httpcore

from canvas_mcp.composition import CanvasConnection, open_canvas_connection
from canvas_mcp.domain.errors import ApplicationError
from canvas_mcp.infrastructure.canvas.client import PROFILE_PATH
from canvas_mcp.infrastructure.canvas.network import PublicOriginBackend
from canvas_mcp.infrastructure.canvas.provider import CanvasProvider
from canvas_mcp.infrastructure.canvas.transport_diagnostic import (
    Mode,
    Observation,
    ObservedPool,
    ProbeBlocked,
    Report,
    baseline,
    preflight,
)
from canvas_mcp.infrastructure.logging.events import SENSITIVE_HTTP, protect_http_logging
from canvas_mcp.ports.credentials import AccessToken

MODES = (Mode.BASELINE, Mode.LIBRARY, Mode.HEADERS, Mode.TRANSPORT, Mode.CLIENT)
USER_AGENTS = {
    Mode.UA_PYTHON: f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}",
    Mode.UA_POWERSHELL: "PowerShell-compatible CanvasTransportProbe/0.0.0",
    Mode.UA_APPLICATION: "canvas-mcp/0.0.0",
}


async def baseline_worker(env: Mapping[str, str], timeout: float) -> Report:
    """Killable child bounds stdlib DNS as well as TLS/HTTP. No token in argv."""
    report = Report(Mode.BASELINE)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "canvas_mcp.transport_probe",
        "--live",
        "--_baseline-worker",
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=4096,
    )
    try:
        async with asyncio.timeout(timeout):
            # At most two fixed-schema lines: headers, then completed evidence.
            # Keep raw status if the optional body read stalls and child is killed.
            assert process.stdout is not None
            for index in range(3):
                line = await process.stdout.readline()
                if not line:
                    break
                if index == 2 or len(line) > 4096:
                    raise ProbeBlocked()
                data = json.loads(line)
                if not isinstance(data, dict) or data.get("mode") != Mode.BASELINE.value:
                    raise ProbeBlocked()
                data["mode"] = Mode.BASELINE
                candidate = Report(**data)
                candidate.safe_data()
                report = candidate
            await process.wait()
        if process.returncode != 0 or report.status is None and report.failure == "none":
            raise ProbeBlocked()
        return report
    except TimeoutError:
        report.failure = "timeout"
    except Exception:
        report.failure = "worker_failed"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return report


async def core_stage(
    connection: CanvasConnection,
    token: AccessToken,
    mode: Mode,
    timeout: float,
) -> Report:
    report = Report(
        mode, transport="vetted_ip" if mode in (Mode.TRANSPORT, Mode.CLIENT) else "system_dns"
    )
    observation = Observation(
        connection._settings.canvas_origin,
        token,
        report,
        connection._settings.trusted_private_ips,
    )
    client = cast(CanvasProvider, connection.service._provider)._client
    owned: httpcore.AsyncConnectionPool | None = None
    original: httpcore.AsyncConnectionPool | None = None
    guard = SENSITIVE_HTTP.set(True)
    try:
        async with asyncio.timeout(timeout):
            if mode not in (Mode.TRANSPORT, Mode.CLIENT):
                await preflight(
                    observation.host, timeout, report, connection._settings.trusted_private_ips
                )
            if mode is Mode.CLIENT:
                # Keep the ACTUAL production pool/backend/settings/provider/client.
                original = client._pool
                observed = ObservedPool(original, observation)
                client._pool = cast(httpcore.AsyncConnectionPool, observed)
                await connection.get_profile()
            else:
                owned = httpcore.AsyncConnectionPool(
                    network_backend=PublicOriginBackend(
                        observation.origin,
                        trusted_private_ips=connection._settings.trusted_private_ips,
                    )
                    if mode is Mode.TRANSPORT
                    else None,
                    retries=0,
                    max_connections=1,
                    max_keepalive_connections=0,
                )
                observed = ObservedPool(owned, observation)
                headers = {"Authorization": "Bearer " + token.value, "Accept": "application/json"}
                if mode is Mode.HEADERS:
                    headers["Accept-Encoding"] = "identity"
                if mode in USER_AGENTS:
                    headers["User-Agent"] = USER_AGENTS[mode]
                async with observed.stream(
                    "GET",
                    observation.origin + PROFILE_PATH,
                    headers=headers,
                    extensions={
                        "timeout": {key: timeout for key in ("connect", "read", "write", "pool")}
                    },
                ):
                    pass  # Success bodies are not read except by full production client E.
    except TimeoutError:
        report.failure = "timeout"
    except ApplicationError:
        if report.failure == "none":
            report.failure = "client_rejected"
    except Exception:
        if report.failure == "none":
            report.failure = "request_failed"
    finally:
        if original is not None:
            client._pool = original
        try:
            if owned is not None:
                async with asyncio.timeout(timeout):
                    await owned.aclose()
        except Exception:
            if report.failure == "none":
                report.failure = "request_failed"
        finally:
            SENSITIVE_HTTP.reset(guard)
    return report


async def collect(
    env: Mapping[str, str],
    *,
    user_agents: bool = False,
    worker: bool = False,
) -> list[Report]:
    # ssl.create_default_context can honor this outside isolated Python. Reject
    # before context creation; diagnostics must not generate TLS secret files.
    if env.get("SSLKEYLOGFILE") or os.environ.get("SSLKEYLOGFILE"):
        raise ProbeBlocked()
    protect_http_logging()
    guard = SENSITIVE_HTTP.set(True)
    try:
        # No profile preflight: its failure is what this command must diagnose.
        async with open_canvas_connection(env, log_stream=io.StringIO()) as connection:
            timeout = min(20.0, connection._settings.request_timeout_seconds)
            client = cast(CanvasProvider, connection.service._provider)._client
            token = await client._access_token(connection._context(), timeout)
            if worker:
                return [
                    baseline(
                        connection._settings.canvas_origin,
                        token,
                        timeout,
                        trusted_private_ips=connection._settings.trusted_private_ips,
                        headers_ready=lambda report: print(
                            json.dumps(report.safe_data(), separators=(",", ":")), flush=True
                        ),
                    )
                ]
            reports = [await baseline_worker(env, timeout)]
            for mode in (*MODES[1:], *(USER_AGENTS if user_agents else ())):
                reports.append(await core_stage(connection, token, mode, timeout))
            return reports
    finally:
        SENSITIVE_HTTP.reset(guard)


async def run(
    *, live: bool, details: bool = False, user_agents: bool = False, worker: bool = False
) -> int:
    if live is not True or any(type(flag) is not bool for flag in (details, user_agents, worker)):
        print("transport_probe explicit_opt_in_required")
        return 2
    try:
        reports = await collect(dict(os.environ), user_agents=user_agents, worker=worker)
        if worker:
            print(json.dumps(reports[0].safe_data(), separators=(",", ":")))
            return 0
        if details:
            print("request GET / canvas_origin / " + PROFILE_PATH)
        for report in reports:
            data = report.safe_data()
            if details:
                print(json.dumps(data, separators=(",", ":")))
            else:
                print(
                    f"{report.mode.value:24} {report.status if report.status is not None else 'not_received'}"
                )
        return (
            0 if all(report.status == 200 and report.failure == "none" for report in reports) else 1
        )
    except Exception:
        print("transport_probe failed")  # Never format caught exceptions/configuration.
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly permit fixed profile GETs")
    parser.add_argument(
        "--details", action="store_true", help="fixed safe wire properties and 403 categories"
    )
    parser.add_argument(
        "--user-agent-matrix",
        action="store_true",
        help="three extra benign UA trials on default transport",
    )
    parser.add_argument("--_baseline-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.live:
        parser.error("the transport diagnostic requires --live")
    if args._baseline_worker and (args.details or args.user_agent_matrix):
        parser.error("invalid diagnostic mode combination")
    try:
        return asyncio.run(
            run(
                live=True,
                details=args.details,
                user_agents=args.user_agent_matrix,
                worker=args._baseline_worker,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
