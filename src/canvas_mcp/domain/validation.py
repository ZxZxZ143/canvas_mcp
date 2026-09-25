"""Shared ingress rules and Canvas ID codec; no HTTP or environment access."""

import re
import unicodedata
from datetime import datetime, timedelta, timezone

from canvas_mcp.domain.errors import ValidationError
from canvas_mcp.domain.models import AssignmentFilter, EntityId, PageRequest


def canvas_id(value: object) -> EntityId:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[1-9][0-9]{0,18}", value)
        or int(value) > 2**63 - 1
    ):
        raise ValidationError()
    return EntityId(value)


def page_request(page: PageRequest, maximum: int = 100) -> None:
    if (
        not isinstance(page, PageRequest)
        or type(page.limit) is not int
        or not 1 <= page.limit <= maximum
        or (
            page.cursor is not None
            and (
                not isinstance(page.cursor, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", page.cursor)
            )
        )
    ):
        raise ValidationError()


def assignment_filter(query: AssignmentFilter) -> None:
    if (
        not isinstance(query, AssignmentFilter)
        or query.bucket
        not in (None, "past", "overdue", "undated", "ungraded", "unsubmitted", "upcoming", "future")
        or query.order_by not in ("position", "name", "due_at")
    ):
        raise ValidationError()
    if query.search_term is not None and (
        not isinstance(query.search_term, str)
        or not query.search_term.strip()
        or not 1 <= len(query.search_term) <= 100
        or any(unicodedata.category(c).startswith("C") for c in query.search_term)
    ):
        raise ValidationError()


def instant(value: datetime) -> datetime:
    invalid = False
    result = value
    try:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            invalid = True
        else:
            result = value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        invalid = True
    if invalid:
        raise ValidationError()
    return result


def date_range(start: datetime, end: datetime, max_days: int = 90) -> tuple[datetime, datetime]:
    start, end = instant(start), instant(end)
    if not start < end or end - start > timedelta(days=max_days):
        raise ValidationError()
    return start, end


def course_ids(values: tuple[EntityId, ...], maximum: int = 50) -> tuple[EntityId, ...]:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValidationError()
    return tuple(sorted(set(canvas_id(value) for value in values)))
