"""Nonsecret deployment contract. Construction performs no validation or I/O.

Reject invalid settings at composition time before exposing tools. See the
configuration contract for units, validation, and unsupported cache settings.
CANVAS_ACCESS_TOKEN is intentionally absent; it belongs to CredentialSource.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class DeploymentSettings:
    canvas_origin: str
    allow_private_origin: bool = False
    trusted_private_ips: tuple[str, ...] = ()
    download_directory: Path | None = None
    download_origins: tuple[str, ...] = ()
    max_download_bytes: int = 26_214_400
    download_timeout_seconds: float = 120.0
    request_timeout_seconds: float = 20.0
    cache_ttl_seconds: int = 0
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    aggregate_timeout_seconds: float = 60.0
    max_get_attempts: int = 3
    max_redirects: int = 3
    max_api_response_bytes: int = 2_097_152
    max_tool_response_bytes: int = 131_072
    max_text_characters: int = 16_000
    max_page_size: int = 100
    max_pages: int = 20
    max_aggregate_requests: int = 100
    max_concurrency: int = 4
    max_courses: int = 50
    max_window_days: int = 90
    artifact_ttl_seconds: int = 86_400
    max_artifact_bytes_total: int = 262_144_000
    max_artifact_count: int = 100
    cursor_ttl_seconds: int = 600
    max_session_cursors: int = 64
    max_cursor_state_bytes: int = 4_096
    max_session_cursor_bytes: int = 262_144
