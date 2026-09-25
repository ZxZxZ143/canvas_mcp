"""Allowlisted Canvas mappings. Every display string remains untrusted data."""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from canvas_mcp.domain.errors import MalformedUpstreamError
from canvas_mcp.domain.models import Availability, Course, EntityId, ExternalText, Observed, Profile
from canvas_mcp.infrastructure.canvas.text import inert_text


def text(value: object, max_chars: int = 512) -> ExternalText:
    if not isinstance(value, str) or not value.strip():
        raise MalformedUpstreamError()
    clean = inert_text(value).replace("\n", " ").replace("\t", " ")
    return ExternalText(clean[:max_chars], truncated=len(clean) > max_chars)


def entity_id(value: object) -> EntityId:
    if type(value) is int:
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value
    else:
        raise MalformedUpstreamError()
    if not re.fullmatch(r"[1-9][0-9]{0,18}", candidate) or int(candidate) > 2**63 - 1:
        raise MalformedUpstreamError()
    return EntityId(candidate)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise MalformedUpstreamError()
    return value


def profile(value: object) -> Profile:
    raw = _object(value)
    timezone: Observed[str] = Observed(Availability.UNAVAILABLE, None)
    if "time_zone" in raw and raw["time_zone"] is None:
        timezone = Observed(Availability.AVAILABLE, None)
    elif "time_zone" in raw:
        zone = raw["time_zone"]
        if not isinstance(zone, str) or len(zone) > 128:
            raise MalformedUpstreamError()
        invalid = False
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            invalid = True
        if invalid:
            raise MalformedUpstreamError()
        timezone = Observed(Availability.AVAILABLE, zone)
    return Profile(entity_id(raw.get("id")), text(raw.get("name")), timezone)


def course(value: object) -> Course:
    raw = _object(value)
    hidden = raw.get("hide_final_grades")
    if hidden is not None and type(hidden) is not bool:
        raise MalformedUpstreamError()
    term: Observed[ExternalText] = Observed(Availability.UNAVAILABLE, None)
    if "term" in raw and raw["term"] is None:
        term = Observed(Availability.AVAILABLE, None)
    elif "term" in raw:
        term_raw = _object(raw["term"])
        term = Observed(Availability.AVAILABLE, text(term_raw.get("name")))
    return Course(
        entity_id(raw.get("id")), text(raw.get("name")), text(raw.get("course_code")), term, hidden
    )
