"""Opt-in file smoke; try <=3 candidates, verify/clean ONE <=1 MiB accepted sample."""

import argparse
import asyncio
import math
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from canvas_mcp.composition import CanvasConnection, open_canvas_connection
from canvas_mcp.domain.errors import (
    ApplicationError,
    AuthorizationError,
    DownloadRejectedError,
    DownloadRejectionReason,
    NotFoundError,
    RateLimitError,
    RequestBudgetExceededError,
    UpstreamUnavailableError,
    ValidationError,
)
from canvas_mcp.domain.models import (
    FileFilter,
    FileMetadata,
    FileReference,
    PageRequest,
    RequestContext,
)
from canvas_mcp.infrastructure.files.policy import metadata_policy

SAMPLE_LIMIT = 1_048_576
COURSE_DISCOVERY_TIMEOUT = 20.0
AUTHORIZATION_TIMEOUT = 20.0
METADATA_TIMEOUT = 20.0
CANDIDATE_VALIDATION_TIMEOUT = 60.0
MAX_COURSES = 10
MAX_FILES_PER_COURSE = 5
MAX_METADATA_PROBES = 10
MAX_SAMPLE_CANDIDATES = 3
# Only known per-file evidence failures permit another saved candidate. Unknown
# reasons, subclasses (including unsafe_redirect), credentials, framing and
# storage errors fail closed. This is NOT a production retry policy.
SAMPLE_FALLBACK_REASONS = frozenset(
    (
        DownloadRejectionReason.UNSUPPORTED_EXTENSION,
        DownloadRejectionReason.MIME_MISMATCH,
        DownloadRejectionReason.INVALID_TEXT,
        DownloadRejectionReason.UNSAFE_PREFIX,
        DownloadRejectionReason.PREFIX_MISMATCH,
        DownloadRejectionReason.UNSUPPORTED_ENCODING,
    )
)


@dataclass(frozen=True)
class SmokeTiming:
    """Local diagnostic orchestration only; never changes DeploymentSettings."""

    probe_timeout: float = 35.0
    discovery_timeout: float = 120.0

    def __post_init__(self) -> None:
        for value, maximum in ((self.probe_timeout, 60), (self.discovery_timeout, 180)):
            if (
                type(value) not in (int, float)
                or not 0 < value <= maximum
                or not math.isfinite(value)
            ):
                raise ValidationError()


def _milliseconds(seconds: float) -> int:
    # Fixed finite integer projection; no absolute clock, source identity or path.
    return min(180_000, max(0, int(seconds * 1000)))


def step(
    name: str,
    status: str,
    reason: str | None = None,
    *,
    duration_ms: int | None = None,
    budget_ms: int | None = None,
) -> None:
    suffix = f" duration_ms={duration_ms}" if duration_ms is not None else ""
    suffix += f" budget_ms={budget_ms}" if budget_ms is not None else ""
    print(f"{name:24} {status}" + (f" ({reason})" if reason else "") + suffix)


@dataclass
class DiscoveryResult:
    """Smoke-only observation, not a production query/authorization contract.

    completed counts returned file listings, not course authorization GETs or
    exhaustive scans. partial_or_failed counts affected course probes once; it
    can overlap completed when listing succeeded but metadata/coverage did not.
    eligible_candidates counts list entries passing pre-resolution policy/size.
    Only freshly resolved, policy-checked metadata becomes candidate.
    """

    candidate: FileMetadata | None = field(default=None, repr=False)
    alternates: tuple[FileMetadata, ...] = field(default=(), repr=False)
    identities: tuple[FileReference, ...] = field(default=(), repr=False)
    courses_attempted: int = 0
    courses_completed: int = 0
    courses_partial_or_failed: int = 0
    files_seen: int = 0
    eligible_candidates: int = 0
    metadata_probes: int = 0
    incomplete_reasons: set[str] = field(default_factory=set)

    @property
    def candidates(self) -> tuple[FileMetadata, ...]:
        if self.candidate is None:
            return ()
        return (self.candidate, *self.alternates)[:MAX_SAMPLE_CANDIDATES]

    @property
    def complete(self) -> bool:
        return not self.incomplete_reasons

    @property
    def reason(self) -> str | None:
        if "request_budget_exceeded" in self.incomplete_reasons:
            return "request_budget_exceeded"
        if "authorization_unavailable" in self.incomplete_reasons:
            return "authorization_unavailable"
        if self.candidate is not None or self.identities:
            return "discovery_incomplete" if not self.complete else None
        if self.courses_attempted and not self.courses_completed:
            return "all_course_probes_failed"
        if not self.complete:
            return "discovery_incomplete"
        return "no_files" if self.files_seen == 0 else "no_policy_eligible_file"


def _check_budget(ctx: RequestContext) -> None:
    budget = ctx.budget
    if (
        time.monotonic() >= budget.monotonic_deadline
        or budget.http_attempts_used >= budget.limits.max_http_attempts
        or budget.pages_used >= budget.limits.max_pages
    ):
        raise RequestBudgetExceededError()


@asynccontextmanager
async def _stage(
    name: Literal[
        "course_discovery", "course_authorization", "course_file_probe", "file_metadata_probe"
    ],
    ctx: RequestContext,
    timeout: float,
    debug: bool,
) -> AsyncIterator[None]:
    """Pair smoke STARTED with terminal diagnostics, without changing HTTP logs.

    timeout_at cancels/awaits this same sequential coroutine; no detached tasks.
    The shared deadline is never reset after discovery starts. Authorization,
    listing and metadata each get their own bounded stage allowance. HTTP still
    has its unchanged per-attempt timeout; the stage caps total retry time too.
    """
    _check_budget(ctx)
    started = time.monotonic()
    deadline = min(ctx.budget.monotonic_deadline, started + timeout)
    step(name, "STARTED", budget_ms=_milliseconds(deadline - started) if debug else None)

    def terminal(status: str, reason: str | None = None) -> None:
        step(
            name,
            status,
            reason,
            duration_ms=_milliseconds(time.monotonic() - started) if debug else None,
        )

    try:
        async with asyncio.timeout_at(deadline):
            yield
    except TimeoutError:
        reason = (
            "request_budget_exceeded"
            if time.monotonic() >= ctx.budget.monotonic_deadline
            else "probe_timeout"
        )
        terminal("TIMEOUT", reason)
        raise
    except asyncio.CancelledError:
        reason = (
            "request_budget_exceeded"
            if time.monotonic() >= ctx.budget.monotonic_deadline
            else "cancelled"
        )
        terminal("CANCELLED", reason)
        raise
    except ApplicationError as error:
        terminal("FAIL", error.diagnostic_code)
        raise
    except Exception:
        terminal("FAIL", "internal_error")
        raise
    else:
        terminal("PASS")


def _eligible(metadata: FileMetadata, limit: int) -> bool:
    try:
        metadata_policy(metadata, limit)
    except DownloadRejectedError:
        return False
    return metadata.size.value is not None


async def discover(
    connection: CanvasConnection,
    *,
    timing: SmokeTiming = SmokeTiming(),
    debug: bool = False,
    collect_samples: bool = False,
) -> DiscoveryResult:
    """Sample mode saves <=3 listing identities; validation is a separate phase."""
    ctx = connection._context()
    # This is a NEW smoke-owned context with no work performed yet. Assign its
    # validated smoke deadline once, instead of inheriting the production 60s
    # aggregate default. Counts/limits and all downstream HTTP policy stay intact.
    ctx.budget.monotonic_deadline = time.monotonic() + timing.discovery_timeout
    outcome = DiscoveryResult()
    limit = min(SAMPLE_LIMIT, connection._settings.max_download_bytes)
    try:
        async with asyncio.timeout_at(ctx.budget.monotonic_deadline):
            async with _stage("course_discovery", ctx, COURSE_DISCOVERY_TIMEOUT, debug):
                courses = await connection.service.list_courses(ctx, PageRequest(MAX_COURSES))
            if courses.data.next_cursor is not None or not courses.data.complete:
                outcome.incomplete_reasons.add("coverage_limit")
            step("courses", "PASS" if outcome.complete else "PARTIAL")
            for index, course in enumerate(courses.data.items[:MAX_COURSES]):
                _check_budget(ctx)
                outcome.courses_attempted += 1
                course_partial = False
                try:
                    async with _stage("course_authorization", ctx, AUTHORIZATION_TIMEOUT, debug):
                        await connection._academic().get_course(ctx, course.id)
                    # Existing provider authorization memo on this SAME context
                    # avoids a duplicate course GET inside FileService. Do not
                    # invent/seed authorization from the course-list response.
                    async with _stage("course_file_probe", ctx, timing.probe_timeout, debug):
                        response = await connection._files().list_course_files(
                            ctx,
                            course.id,
                            PageRequest(MAX_FILES_PER_COURSE),
                            FileFilter(sort="size"),
                        )
                    # Commit observations before any subsequent await/cancellation.
                    outcome.courses_completed += 1
                    outcome.files_seen += len(response.data.items)
                    if response.data.next_cursor is not None or not response.data.complete:
                        course_partial = True
                        outcome.incomplete_reasons.add("coverage_limit")
                    filter_started = time.monotonic()
                    eligible = tuple(item for item in response.data.items if _eligible(item, limit))
                    outcome.eligible_candidates += len(eligible)
                    if debug:
                        step(
                            "candidate_filter",
                            "PASS",
                            duration_ms=_milliseconds(time.monotonic() - filter_started),
                        )
                    seen: set[tuple[str, str]] = set()
                    for candidate in eligible:
                        identity = (candidate.source.course_id, candidate.source.file_id)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        if collect_samples:
                            # Commit listing identities, not download permission.
                            # No metadata await can spend the discovery remainder.
                            outcome.identities += (candidate.source,)
                            if len(outcome.identities) >= MAX_SAMPLE_CANDIDATES:
                                break
                            continue
                        _check_budget(ctx)
                        if outcome.metadata_probes >= MAX_METADATA_PROBES:
                            raise RequestBudgetExceededError()
                        outcome.metadata_probes += 1
                        async with _stage("file_metadata_probe", ctx, METADATA_TIMEOUT, debug):
                            resolved = await connection._files().get_file_metadata(
                                ctx, candidate.source
                            )
                        if _eligible(resolved.data, limit):
                            outcome.candidate = resolved.data
                            if index + 1 < len(courses.data.items):
                                outcome.incomplete_reasons.add("candidate_short_circuit")
                            break
                    if outcome.identities and index + 1 < len(courses.data.items):
                        outcome.incomplete_reasons.add("candidate_short_circuit")
                except TimeoutError:
                    course_partial = True
                    outcome.incomplete_reasons.add("probe_timeout")
                    _check_budget(ctx)  # distinguish global exhaustion; no next probe
                except AuthorizationError:
                    course_partial = True
                    outcome.incomplete_reasons.add("authorization_unavailable")
                except (NotFoundError, RateLimitError, UpstreamUnavailableError):
                    course_partial = True
                    outcome.incomplete_reasons.add("probe_failed")
                except RequestBudgetExceededError:
                    course_partial = True
                    raise
                except asyncio.CancelledError:
                    course_partial = True
                    raise
                finally:
                    if course_partial:
                        outcome.courses_partial_or_failed += 1
                if outcome.candidate is not None or outcome.identities:
                    break
    except RequestBudgetExceededError:
        outcome.incomplete_reasons.add("request_budget_exceeded")
    except TimeoutError:
        outcome.incomplete_reasons.add(
            "request_budget_exceeded"
            if time.monotonic() >= ctx.budget.monotonic_deadline
            else "probe_timeout"
        )
    except AuthorizationError:
        outcome.incomplete_reasons.add("authorization_unavailable")
    except asyncio.CancelledError:
        step("file_discovery", "CANCELLED", "cancelled")
        raise
    step(
        "files",
        "PASS" if outcome.complete else "PARTIAL",
        None if outcome.complete else outcome.reason,
    )
    if debug:
        step(
            "file_discovery",
            "PASS" if outcome.complete else "PARTIAL",
            None if outcome.complete else outcome.reason,
        )
    print(
        f"Course probes: attempted={outcome.courses_attempted}; "
        f"listings_completed={outcome.courses_completed}; "
        f"partial_or_failed={outcome.courses_partial_or_failed}"
    )
    print(
        f"Files seen: {outcome.files_seen}; eligible candidates: {outcome.eligible_candidates}; "
        f"metadata probes: {outcome.metadata_probes}"
    )
    if not collect_samples or not outcome.identities:
        step(
            "metadata",
            "PASS" if outcome.candidate is not None else "NOT_TESTED",
            None if outcome.candidate is not None else outcome.reason,
        )
    return outcome


@dataclass
class CandidateValidationResult:
    candidates: tuple[FileMetadata, ...] = field(default=(), repr=False)
    probes: int = 0
    warnings: set[str] = field(default_factory=set)

    @property
    def reason(self) -> str | None:
        if "request_budget_exceeded" in self.warnings:
            return "candidate_validation_budget_exceeded"
        if self.warnings == {"probe_timeout"} and not self.candidates:
            return "candidate_validation_timeout"
        if self.warnings:
            return "candidate_validation_incomplete"
        return None if self.candidates else "no_metadata_valid_candidate"


async def validate_candidates(
    connection: CanvasConnection,
    identities: tuple[FileReference, ...],
    *,
    debug: bool = False,
) -> CandidateValidationResult:
    """Fresh smoke phase/context; never replenish a deadline inside its loop."""
    ctx = connection._context()
    started = time.monotonic()
    ctx.budget.monotonic_deadline = started + CANDIDATE_VALIDATION_TIMEOUT
    outcome = CandidateValidationResult()
    limit = min(SAMPLE_LIMIT, connection._settings.max_download_bytes)
    step(
        "candidate_validation",
        "STARTED",
        budget_ms=_milliseconds(CANDIDATE_VALIDATION_TIMEOUT) if debug else None,
    )
    try:
        async with asyncio.timeout_at(ctx.budget.monotonic_deadline):
            for reference in identities[:MAX_SAMPLE_CANDIDATES]:
                _check_budget(ctx)
                outcome.probes += 1
                try:
                    async with _stage("file_metadata_probe", ctx, METADATA_TIMEOUT, debug):
                        resolved = (
                            await connection._files().get_file_metadata(ctx, reference)
                        ).data
                        metadata_policy(resolved, limit)
                    if resolved.size.value is None:
                        outcome.warnings.add("unknown_file_size")
                        continue
                    outcome.candidates += (resolved,)
                except TimeoutError:
                    outcome.warnings.add("probe_timeout")
                    _check_budget(ctx)
                except (NotFoundError, RateLimitError, UpstreamUnavailableError):
                    outcome.warnings.add("probe_failed")
                except DownloadRejectedError as error:
                    # Same reviewed whitelist as download fallback; permissions,
                    # size/security subclasses and unknown failures remain fatal.
                    if (
                        type(error) is not DownloadRejectedError
                        or error.reason not in SAMPLE_FALLBACK_REASONS
                    ):
                        raise
                    outcome.warnings.add("metadata_policy_rejected")
    except (RequestBudgetExceededError, TimeoutError):
        outcome.warnings.add("request_budget_exceeded")
    except asyncio.CancelledError:
        step("candidate_validation", "CANCELLED", "cancelled")
        raise
    except ApplicationError as error:
        step("candidate_validation", "FAIL", error.diagnostic_code if debug else error.code)
        raise
    except Exception:
        step("candidate_validation", "FAIL", "internal_error")
        raise
    step(
        "candidate_validation",
        "PARTIAL" if outcome.warnings else "PASS",
        outcome.reason if outcome.warnings else None,
        duration_ms=_milliseconds(time.monotonic() - started) if debug else None,
    )
    step("validated_candidates", str(len(outcome.candidates)))
    print(f"Candidate metadata probes: {outcome.probes}")
    step(
        "metadata",
        "PASS" if outcome.candidates else "NOT_TESTED",
        None if outcome.candidates else outcome.reason,
    )
    return outcome


async def run(
    *,
    download_sample: bool = False,
    timing: SmokeTiming = SmokeTiming(),
    debug: bool = False,
) -> int:
    current = "connection"
    try:
        env = dict(os.environ)
        # Never download more than the advertised small sample, even if fresh
        # metadata changes after discovery or lies about the byte count.
        if download_sample:
            env["MAX_DOWNLOAD_BYTES"] = str(
                min(int(env.get("MAX_DOWNLOAD_BYTES", str(SAMPLE_LIMIT))), SAMPLE_LIMIT)
            )
        async with open_canvas_connection(env) as connection:
            current = "profile"
            await connection.get_profile()
            step(current, "PASS")
            current = "file_discovery"
            discovery = await discover(
                connection, timing=timing, debug=debug, collect_samples=download_sample
            )
            candidates = discovery.candidates
            reason = discovery.reason
            if download_sample and discovery.identities:
                current = "candidate_validation"
                validation = await validate_candidates(
                    connection, discovery.identities, debug=debug
                )
                candidates, reason = validation.candidates, validation.reason
            if not download_sample or not candidates:
                step(
                    "download",
                    "NOT_TESTED",
                    "not_requested" if not download_sample else reason,
                )
                return 0
            for selected in candidates[:MAX_SAMPLE_CANDIDATES]:
                current = "download"
                step("download_candidate", "STARTED")
                try:
                    # The production manager freshly authorizes/resolves each
                    # reference. Rejection cleans pending bytes before returning.
                    downloaded = (await connection.download_file(selected.source)).data
                except DownloadRejectedError as error:
                    if (
                        type(error) is not DownloadRejectedError
                        or error.reason not in SAMPLE_FALLBACK_REASONS
                    ):
                        raise
                    step(
                        "download_candidate",
                        "REJECTED",
                        error.diagnostic_code if debug else error.code,
                    )
                    continue
                step("download_candidate", "PASS")
                step("public_url", "PASS")
                step(current, "PASS")
                step("format_validation", "PASS")
                # Deliberately outside the fallback catch: verification/cleanup
                # failures must abort, even if their error is a policy subclass.
                try:
                    current = "verify_managed_hash"
                    verified = (await connection.resolve_download(downloaded.artifact_id)).data
                    if verified.sha256 != downloaded.sha256 or verified.size != downloaded.size:
                        raise ApplicationError()
                    step("local_containment", "PASS")
                    step("sha256", "PASS")
                    step(current, "PASS")
                    print(
                        f"Downloaded bytes: {verified.size}; redirects: {verified.redirects}; SHA-256 verified: yes"
                    )
                finally:
                    try:
                        await connection.cleanup_download(downloaded.artifact_id)
                    except Exception:
                        current = "cleanup"
                        raise
                    step("cleanup", "PASS")
                break
            else:
                step("download", "FAIL", "no_policy_downloadable_candidate")
                return 1
            current = "connection_close"
        return 0
    except ApplicationError as error:
        step(current, "FAIL", error.diagnostic_code if debug else error.code)
        return 1
    except Exception:
        step(current, "FAIL", "internal_error")
        return 1


def _seconds(value: str, maximum: float) -> float:
    invalid = False
    try:
        parsed = float(value)
        invalid = not math.isfinite(parsed) or not 0 < parsed <= maximum
    except ValueError:
        invalid = True
    if invalid:
        # Never echo arbitrary CLI text in diagnostics, including malformed URLs.
        raise argparse.ArgumentTypeError(f"must be finite seconds > 0 and <= {maximum:g}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--download-sample", action="store_true")
    parser.add_argument(
        "--probe-timeout",
        type=lambda value: _seconds(value, 60),
        default=35.0,
        help="file-list stage seconds, default 35, >0 and <=60; smoke only",
    )
    parser.add_argument(
        "--discovery-timeout",
        type=lambda value: _seconds(value, 180),
        default=120.0,
        help="overall discovery seconds, default 120, >0 and <=180; smoke only",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="fixed stage timings and effective budgets; never private data",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("the optional smoke test requires --live")
    try:
        return asyncio.run(
            run(
                download_sample=args.download_sample,
                timing=SmokeTiming(args.probe_timeout, args.discovery_timeout),
                debug=args.debug,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
