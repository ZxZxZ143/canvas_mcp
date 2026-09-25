"""Local diagnostic evidence, not document trust or a download override API."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class ExpectedFormat(str, Enum):
    PDF = "pdf"
    TXT = "txt"
    MD = "md"
    CSV = "csv"
    PY = "py"
    JSON = "json"
    IPYNB = "ipynb"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    ZIP = "zip"


class DiagnosticStatus(str, Enum):
    EXPECTED = "VALIDATED_EXPECTED_FORMAT"
    OTHER = "VALIDATED_OTHER_SAFE_FORMAT"
    UNRECOGNIZED = "UNRECOGNIZED"
    UNSAFE = "UNSAFE"
    INVALID = "INVALID"


class RedirectOriginClass(str, Enum):
    CANVAS = "canvas_origin"
    EXTERNAL = "approved_external_origin"


class ResponseContentClass(str, Enum):
    HTML = "html"
    JSON = "json"
    BINARY = "binary"
    OTHER = "other"


@dataclass(frozen=True)
class RedirectHop:
    """Fixed diagnostic projection, never a URL/header or content capability."""

    hop_index: int
    origin_class: RedirectOriginClass
    status: int
    authorization_attached: bool
    response_content_type_class: ResponseContentClass


@dataclass(frozen=True)
class FormatEvidence:
    expected_format: ExpectedFormat
    detected_format: str
    validation_method: str
    result: DiagnosticStatus

    @property
    def learnable(self) -> bool:
        return self.result is DiagnosticStatus.EXPECTED


@dataclass(frozen=True, repr=False)
class MimeScope:
    canvas_origin: str
    application_profile: str
    canvas_subject: str


@dataclass(frozen=True, repr=False)
class QuarantinedFile:
    """Internal transient state; never registered as a normal artifact."""

    artifact_id: str
    size: int
    sha256: str
    expected_format: ExpectedFormat
    canvas_mime: str | None
    http_mime: str
    redirect_count: int
    trust: Literal["untrusted"] = field(default="untrusted", init=False)
    state: Literal["quarantine"] = field(default="quarantine", init=False)


@dataclass(frozen=True, repr=False)
class MimeRule:
    expected_format: ExpectedFormat
    http_mime: str
    detected_format: str
    validation_method: str
    evidence_count: int
    first_seen: str
    last_seen: str
    state: Literal["active", "disabled"]


@dataclass(frozen=True, repr=False)
class RevalidationTicket:
    identity: str
