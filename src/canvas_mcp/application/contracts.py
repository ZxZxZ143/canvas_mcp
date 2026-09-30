"""Ingress/result schema sketches, not validated constructors or serializers.

Implementation must add the central schema validation specified in mcp-tools.md
before exposing these contracts through any transport. Direct dataclass creation
is not proof that values are valid or authorized.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar

from canvas_mcp.domain.models import (
    Announcement,
    Assignment,
    CalendarEvent,
    Course,
    EntityId,
    ExternalText,
    AttachmentMetadata,
    ModuleSequence,
    Observed,
    Page,
    PageRequest,
    RubricCriterion,
    Submission,
)

T = TypeVar("T")


@dataclass(frozen=True)
class CourseQuery:
    course_id: EntityId


@dataclass(frozen=True)
class AssignmentQuery:
    course_id: EntityId
    assignment_id: EntityId


@dataclass(frozen=True)
class ModuleQuery:
    course_id: EntityId
    module_id: EntityId


@dataclass(frozen=True)
class FileQuery:
    course_id: EntityId
    file_id: EntityId


@dataclass(frozen=True)
class DateWindow:
    start: datetime
    end: datetime
    timezone: str


@dataclass(frozen=True)
class UpcomingQuery:
    window: DateWindow | None
    course_ids: tuple[EntityId, ...]
    mode: Literal["upcoming", "overdue", "not_submitted", "undated"]
    page: PageRequest
    include_submitted: bool = False
    include_calendar: bool = False
    include_announcements: bool = False


@dataclass(frozen=True)
class Warning:
    """Fixed component/code, never an upstream exception or arbitrary message."""

    component: str
    code: str
    course_id: EntityId | None = None


@dataclass(frozen=True)
class Result(Generic[T]):
    data: T
    request_id: str
    observed_at: datetime
    complete: bool
    warnings: tuple[Warning, ...]


@dataclass(frozen=True)
class AssignmentContext:
    course: Course
    assignment: Assignment
    rubric: Observed[tuple[RubricCriterion, ...]]
    submission: Observed[Submission]
    attachments: Observed[tuple[AttachmentMetadata, ...]]
    module_context: Observed[tuple[ModuleSequence, ...]]


@dataclass(frozen=True)
class WorkItem:
    course: Course
    assignment: Assignment
    submission: Observed[Submission]
    due_state: Literal["upcoming", "overdue", "no_due_date", "unknown"]
    features: WorkloadFeatures | None = None


@dataclass(frozen=True)
class WorkloadFeatures:
    """Evidence derived from assignment-list metadata, never an effort estimate."""

    description_excerpt: Observed[ExternalText]
    description_available: bool | None
    description_length_band: str
    rubric_available: bool | None
    rubric_criteria_count: int | None
    direct_attachment_count: int | None
    linked_file_count: int | None
    task_type_signals: tuple[str, ...]
    availability: str
    warning_flags: tuple[str, ...]


@dataclass(frozen=True)
class StudyPlanContext:
    workload: Workload
    timezone: str
    start_at: datetime
    end_at: datetime
    days: int


@dataclass(frozen=True)
class CourseCoverage:
    """Normalized IDs for the caller, never log fields.

    scanned means all assignment pages succeeded (including empty courses).
    failed includes courses not started/completed because the shared budget ended.
    """

    requested: tuple[EntityId, ...]
    scanned: tuple[EntityId, ...]
    failed: tuple[EntityId, ...]
    discovery_complete: bool


@dataclass(frozen=True)
class Workload:
    items: Page[WorkItem]
    calendar: Observed[Page[CalendarEvent]]
    announcements: Observed[Page[Announcement]]
    coverage: CourseCoverage
