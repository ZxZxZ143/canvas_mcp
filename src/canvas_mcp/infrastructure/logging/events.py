"""Structured allowlisted events; no arbitrary message, exception or payload API."""

import json
import logging
from contextvars import ContextVar
from enum import Enum
from typing import TextIO
from uuid import UUID

SENSITIVE_HTTP = ContextVar("canvas_sensitive_http", default=False)
SENSITIVE_STATE = ContextVar("canvas_sensitive_state", default=False)

OPERATIONS = frozenset(
    (
        "profile",
        "courses",
        "courses.get",
        "assignments.list",
        "assignments.get",
        "modules.list",
        "modules.items",
        "submission.get",
        "calendar.list",
        "announcements.list",
        "grades.get",
        "module_sequence.get",
        "modules.item",
        "files.list",
        "files.get",
        "files.public_url",
        "files.download",
        "files.cleanup",
        "files.mime_diagnostic",
    )
)
FAILURE_CODES = frozenset(
    (
        "internal_error",
        "authentication_error",
        "authorization_error",
        "network_policy_error",
        "not_found",
        "validation_error",
        "rate_limit",
        "upstream_unavailable",
        "upstream_timeout",
        "upstream_connection_error",
        "configuration_error",
        "malformed_upstream",
        "unsupported_capability",
        "budget_exceeded",
        "request_budget_exceeded",
        "download_rejected",
        "unsafe_redirect",
        "download_permission_unavailable",
        "public_url_origin_unapproved",
        "file_too_large",
        "storage_error",
        "download_timeout",
        "artifact_unavailable",
        *(f"upstream_http_{status}" for status in range(500, 600)),
    )
)


class Event(str, Enum):
    PRIVATE_ORIGIN = "configured_private_origin_enabled"
    STARTED = "canvas_request_started"
    COMPLETED = "canvas_request_completed"
    FAILED = "canvas_request_failed"
    PAGE = "pagination_page_retrieved"
    REDIRECT = "download_redirect_validated"
    MIME_MISMATCH = "mime_mismatch_detected"
    MIME_VALIDATED = "mime_diagnostic_validated"
    MIME_LEARNED = "mime_compatibility_learned"
    MIME_RECONFIRMED = "mime_compatibility_reconfirmed"
    MIME_DISABLED = "mime_compatibility_disabled"


class _NoHttpTrace(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not SENSITIVE_HTTP.get()


class _NoStateTrace(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not SENSITIVE_STATE.get()


def protect_state_logging() -> None:
    logger = logging.getLogger("psycopg")
    if not any(isinstance(item, _NoStateTrace) for item in logger.filters):
        logger.addFilter(_NoStateTrace())


def protect_http_logging() -> None:
    # HTTPCore 1.0's DEBUG tracing includes response headers and exception objects.
    for name in (
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _NoHttpTrace) for item in logger.filters):
            logger.addFilter(_NoHttpTrace())


class EventLogger:
    def __init__(self, level: str = "INFO", stream: TextIO | None = None) -> None:
        # Private logger: no root propagation, no shared credentials/configuration.
        self._logger = logging.Logger("canvas_events", getattr(logging, level))  # noqa: LOG001
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._logger.propagate = False

    def configured_private_origin_enabled(self) -> None:
        self._logger.log(
            max(self._logger.level, logging.INFO),
            json.dumps({"event": Event.PRIVATE_ORIGIN.value}),
        )

    def state_check(self, event: str, scanned: int, failed: int, new: int, changed: int) -> None:
        if event not in ("state_sync_failed", "baseline_created", "grade_diff_completed"):
            return
        counts = (scanned, failed, new, changed)
        if any(type(x) is not int or not 0 <= x <= 5000 for x in counts):
            return
        self._logger.log(
            logging.WARNING if event == "state_sync_failed" else logging.INFO,
            json.dumps(
                {
                    "event": event,
                    "scanned_count": scanned,
                    "failed_count": failed,
                    "new_count": new,
                    "changed_count": changed,
                }
            ),
        )

    def emit(
        self,
        event: Event,
        request_id: str,
        operation: str,
        *,
        attempt: int = 0,
        reason: str | None = None,
        retry_exhausted: bool = False,
        retry_stop: str = "not_retryable",
        bytes_written: int = 0,
        redirect_count: int = 0,
    ) -> None:
        if operation not in OPERATIONS or not isinstance(event, Event):
            return
        if type(attempt) is not int or not 0 <= attempt <= 3:
            return
        try:
            safe_id = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            safe_id = "invalid"
        self._logger.log(
            logging.WARNING if event is Event.FAILED else logging.INFO,
            json.dumps(
                {
                    "event": event.value,
                    "request_id": safe_id,
                    "operation": operation,
                    "attempt": attempt,
                    **(
                        {
                            "bytes_written": bytes_written
                            if type(bytes_written) is int and 0 <= bytes_written <= 262_144_000
                            else 0,
                            "redirect_count": redirect_count
                            if type(redirect_count) is int and 0 <= redirect_count <= 3
                            else 0,
                        }
                        if operation == "files.download"
                        else {}
                    ),
                    **(
                        {
                            "reason": reason if reason in FAILURE_CODES else "internal_error",
                            "retry_exhausted": retry_exhausted is True,
                            "retry_stop": retry_stop
                            if retry_stop
                            in (
                                "not_retryable",
                                "attempt_limit",
                                "request_budget",
                                "retry_scheduled",
                            )
                            else "not_retryable",
                        }
                        if event is Event.FAILED
                        else {}
                    ),
                }
            ),
        )
