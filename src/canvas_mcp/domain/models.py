"""Normalized contract skeletons, not validators or Canvas schema mappings.

IDs are opaque in the core. The Canvas ingress profile requires positive decimal
IDs encoded as strings. All datetimes must be aware; validation is future work.
No raw URLs, tokens, HTTP responses, or filesystem paths belong in these records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Generic, Literal, NewType, TypeVar

EntityId = NewType("EntityId", str)
PrincipalId = NewType("PrincipalId", str)
ConnectionId = NewType("ConnectionId", str)
ArtifactId = NewType("ArtifactId", str)
T = TypeVar("T")


@dataclass(frozen=True)
class AccessScope:
    """Server-created identity/connection binding, never accepted from tool input."""

    principal_id: PrincipalId
    connection_id: ConnectionId


@dataclass(frozen=True)
class BudgetLimits:
    max_http_attempts: int
    max_pages: int
    max_response_bytes: int


@dataclass
class RequestBudget:
    """Request-owned ledger shared by calls in one query on one asyncio loop.

    The HTTP adapter reserves counters without yielding, enforces the monotonic
    deadline and propagates cancellation. Not a cross-thread/global counter.
    """

    limits: BudgetLimits
    monotonic_deadline: float
    http_attempts_used: int = 0
    pages_used: int = 0


@dataclass(frozen=True)
class RequestContext:
    scope: AccessScope
    request_id: str
    as_of: datetime
    budget: RequestBudget
    # Request-local normalized course authorization, discarded with the request.
    courses: dict[EntityId, Course] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class ExternalText:
    """Bounded inert text; its words remain untrusted even after sanitization."""

    text: str
    truncated: bool = False
    format: Literal["plain"] = field(default="plain", init=False)
    trust: Literal["untrusted"] = field(default="untrusted", init=False)


class Availability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_SUPPORTED = "not_supported"
    NOT_REQUESTED = "not_requested"


@dataclass(frozen=True)
class Observed(Generic[T]):
    """Available + None means a known absence; unavailable + None means unknown."""

    state: Availability
    value: T | None
    truncated: bool = False


@dataclass(frozen=True)
class PageRequest:
    """Bounded page request. A cursor is an opaque reference, never a raw URL."""

    limit: int = 25
    cursor: str | None = None


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True)
class Profile:
    id: EntityId
    display_name: ExternalText
    timezone: Observed[str]


@dataclass(frozen=True)
class Course:
    id: EntityId
    name: ExternalText
    code: ExternalText
    term: Observed[ExternalText]
    grades_hidden: bool | None = None


@dataclass(frozen=True)
class Assignment:
    id: EntityId
    course_id: EntityId
    title: ExternalText
    description: Observed[ExternalText]
    due_at: Observed[datetime]
    points: Observed[float]
    submission_types: tuple[ExternalText, ...]
    references: Observed[tuple[MaterialReference, ...]]
    unlock_at: Observed[datetime] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    lock_at: Observed[datetime] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    allowed_attempts: Observed[int] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    rubric: Observed[tuple[RubricCriterion, ...]] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    attachments: Observed[tuple[AttachmentMetadata, ...]] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    submission: Observed[Submission] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    can_submit: bool | None = None
    published: bool | None = None
    required: bool | None = None

    description_redacted: bool = False
    description_nontext_content: bool = False

    @property
    def description_verbatim(self) -> Observed[ExternalText]:
        """One stored source body; the compatibility view must not double domain budgets."""
        return self.description


@dataclass(frozen=True)
class RubricRating:
    id: ExternalText
    description: ExternalText
    points: Observed[float]
    long_description: Observed[ExternalText]


@dataclass(frozen=True)
class RubricCriterion:
    id: ExternalText
    description: ExternalText
    points: Observed[float]
    ratings: tuple[RubricRating, ...]
    long_description: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )


class SubmissionState(str, Enum):
    UNKNOWN = "unknown"
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"


@dataclass(frozen=True)
class Submission:
    """Own-user evidence, not a direct copy of a Canvas workflow_state string."""

    course_id: EntityId
    assignment_id: EntityId
    state: SubmissionState
    submitted_at: Observed[datetime]
    graded: bool | None
    late: bool | None
    missing: bool | None
    excused: bool | None
    required: bool | None
    user_id: EntityId | None = None
    workflow_state: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    submission_type: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    attempt: Observed[int] = field(default_factory=lambda: Observed(Availability.UNAVAILABLE, None))
    graded_at: Observed[datetime] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    attachments: Observed[tuple[AttachmentMetadata, ...]] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )


@dataclass(frozen=True)
class Module:
    id: EntityId
    course_id: EntityId
    title: ExternalText
    position: int | None
    unlock_at: Observed[datetime] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    state: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    completed_at: Observed[datetime] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    items_count: int | None = None
    require_sequential_progress: bool | None = None


@dataclass(frozen=True)
class ModuleItem:
    id: EntityId
    module_id: EntityId
    course_id: EntityId
    title: ExternalText
    kind: Literal["assignment", "file", "page", "external", "other"]
    target_id: EntityId | None
    position: int | None
    item_type: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    indent: int | None = None
    completion_requirement: Observed[CompletionRequirement] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )


@dataclass(frozen=True)
class CompletionRequirement:
    kind: ExternalText
    completed: bool | None
    min_score: Observed[float]
    min_percentage: Observed[float]


@dataclass(frozen=True)
class ModuleSequence:
    module: Module
    current_item: ModuleItem
    previous_item: ModuleItem | None
    next_item: ModuleItem | None


@dataclass(frozen=True)
class AttachmentMetadata:
    """Display metadata only, not a file capability, URL or local artifact."""

    id: EntityId
    display_name: ExternalText
    filename: Observed[ExternalText]
    content_type: Observed[ExternalText]
    size: Observed[int]
    created_at: Observed[datetime]
    updated_at: Observed[datetime]


@dataclass(frozen=True)
class AssignmentFilter:
    search_term: str | None = None
    bucket: (
        Literal["past", "overdue", "undated", "ungraded", "unsubmitted", "upcoming", "future"]
        | None
    ) = None
    order_by: Literal["position", "name", "due_at"] = "position"


@dataclass(frozen=True)
class FileReference:
    """Identity/provenance, never a URL/path or authority grant.

    A course is required in this MVP. source_id is an assignment or module-item
    ID; module_id is required only for module_file. Resolution rechecks evidence.
    """

    file_id: EntityId
    course_id: EntityId
    source_kind: Literal[
        "course_file", "assignment_attachment", "module_file", "submission_attachment"
    ] = "course_file"
    source_id: EntityId | None = None
    module_id: EntityId | None = None


@dataclass(frozen=True)
class FileFilter:
    search_term: str | None = None
    content_type: str | None = None
    sort: Literal["name", "size", "created_at", "updated_at", "content_type"] = "name"
    order: Literal["asc", "desc"] = "asc"


@dataclass(frozen=True)
class FileMetadata:
    id: EntityId
    course_id: EntityId
    source: FileReference
    display_name: ExternalText
    filename: Observed[ExternalText]
    content_type: Observed[ExternalText]
    size: Observed[int]
    created_at: Observed[datetime]
    updated_at: Observed[datetime]
    locked: bool | None
    hidden: bool | None
    locked_for_user: bool | None
    hidden_for_user: bool | None


@dataclass(frozen=True)
class DownloadedFile:
    """Local-only inspection candidate, NOT permission to parse or execute.

    local_path is generated/verified by storage, never input. A future remote
    or MCP serializer must deliberately handle or omit this local capability.
    """

    artifact_id: ArtifactId
    source: FileReference
    local_path: str
    original_display_name: ExternalText
    original_filename: Observed[ExternalText]
    safe_filename: str
    content_type: str
    declared_content_type: Observed[ExternalText]
    size: int
    sha256: str
    redirects: int
    expires_at: datetime
    classification: Literal[
        "untrusted_document",
        "untrusted_text",
        "opaque_archive",
        "office_candidate",
        "untrusted_image",
    ]
    trust: Literal["untrusted"] = field(default="untrusted", init=False)


@dataclass(frozen=True)
class MaterialReference:
    """Extracted before HTML-to-text conversion; no dereferenceable URL exposed."""

    label: ExternalText
    kind: Literal["file", "assignment", "page", "external", "unsupported"]
    target_id: EntityId | None
    state: Literal["resolved", "inaccessible", "unresolved", "not_fetched"]


@dataclass(frozen=True)
class Artifact:
    """Opaque handle only. Resolution is a transport/storage access decision."""

    id: ArtifactId
    media_type: str
    size_bytes: int
    sha256: str
    expires_at: datetime
    trust: Literal["untrusted"] = field(default="untrusted", init=False)


@dataclass(frozen=True)
class Grade:
    course_id: EntityId
    assignment_id: EntityId | None
    score: Observed[float]
    maximum: Observed[float]
    display_grade: Observed[ExternalText]


@dataclass(frozen=True)
class CalendarEvent:
    id: EntityId
    course_id: EntityId
    title: ExternalText
    starts_at: Observed[datetime]
    ends_at: Observed[datetime]
    all_day: bool | None
    assignment_id: EntityId | None
    description: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    location_name: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    location_address: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    event_type: Literal["event", "assignment"] = "event"


@dataclass(frozen=True)
class Announcement:
    id: EntityId
    course_id: EntityId
    title: ExternalText
    body: ExternalText
    published_at: Observed[datetime]
    delayed_post_at: Observed[datetime] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )
    read_state: Observed[ExternalText] = field(
        default_factory=lambda: Observed(Availability.UNAVAILABLE, None)
    )


@dataclass(frozen=True)
class CourseGrade:
    course_id: EntityId
    current_score: Observed[float]
    current_grade: Observed[ExternalText]
    final_score: Observed[float]
    final_grade: Observed[ExternalText]
    current_points: Observed[float]
