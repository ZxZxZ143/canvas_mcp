"""Minimal grade facts and pure comparison; no database or transport dependencies."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Literal

from canvas_mcp.domain.models import EntityId, ExternalText


@dataclass(frozen=True)
class GradeFact:
    course_id: EntityId
    assignment_id: EntityId
    attempt: int | None
    score: float | None
    grade: str | None
    points_possible: float | None
    graded_at: datetime | None
    posted_at: datetime | None
    workflow: Literal["graded", "submitted", "pending_review", "unsubmitted", "unknown"]
    visibility: Literal["visible", "ungraded", "hidden", "previous_attempt"]

    @property
    def exposed(self) -> bool:
        return self.visibility == "visible" and (self.score is not None or self.grade is not None)

    @property
    def state_hash(self) -> str:
        # Timestamps and workflow alone are not a grade change.
        return hashlib.sha256(
            json.dumps(
                [
                    self.visibility,
                    None if self.score is None else float(self.score),
                    self.grade,
                    None if self.points_possible is None else float(self.points_possible),
                ],
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True)
class NamedGrade:
    fact: GradeFact
    course_name: ExternalText
    assignment_name: ExternalText


@dataclass(frozen=True)
class GradeChange:
    current: NamedGrade
    previous: GradeFact | None
    reason: Literal["new_grade", "new_attempt", "grade_changed"]


def compare(previous: GradeFact | None, current: NamedGrade) -> GradeChange | None:
    now = current.fact
    if not now.exposed:
        return None
    if previous is None or not previous.exposed:
        return GradeChange(current, None, "new_grade")
    if previous.attempt is not None and now.attempt is not None and previous.attempt != now.attempt:
        return GradeChange(current, None, "new_attempt")
    if previous.state_hash != now.state_hash:
        return GradeChange(current, previous, "grade_changed")
    return None
