"""Scoped opt-in aliases. Strict production downloads do not consult this store."""

import hashlib
import json
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone

from canvas_mcp.domain.errors import StorageError
from canvas_mcp.domain.mime import (
    ExpectedFormat,
    FormatEvidence,
    MimeRule,
    MimeScope,
    RevalidationTicket,
)
from canvas_mcp.infrastructure.config.origin import normalize_origin
from canvas_mcp.infrastructure.files.mime_runtime import MimeRuntime
from canvas_mcp.infrastructure.files.windows import check

MAX_RULES = 64
METHODS = {
    "pdf": ("pdf", "pdf_signature"),
    **{ext: ("utf8_text", "utf8_no_nul") for ext in ("txt", "md", "csv", "py")},
    "json": ("json", "json_syntax"),
    "ipynb": ("ipynb", "notebook_structure"),
    **{ext: (ext, "zip_directory_structure") for ext in ("docx", "pptx", "xlsx")},
    "zip": ("opaque_zip", "zip_directory_structure"),
}


def normalized_mime(value: str) -> str:
    media = value.split(";", 1)[0].strip().lower()
    if len(value) > 1024 or not re.fullmatch(
        r"[a-z0-9][a-z0-9!#$&^_.+\-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+\-]{0,62}", media
    ):
        raise StorageError()
    return media


def _scope_key(scope: MimeScope) -> str:
    check(
        type(scope) is MimeScope and bool(scope.application_profile) and bool(scope.canvas_subject)
    )
    check(len(scope.application_profile) <= 128 and len(scope.canvas_subject) <= 128)
    return hashlib.sha256(
        json.dumps(
            [
                normalize_origin(scope.canvas_origin),
                scope.application_profile,
                scope.canvas_subject,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class JsonMimeCompatibilityRegistry:
    def __init__(self, runtime: MimeRuntime) -> None:
        self._runtime = runtime
        self._tickets: dict[str, str] = {}
        self._rules: dict[str, dict[str, object]] = {}
        self._poisoned = False
        raw = runtime.read_registry()
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise StorageError()
        check(
            set(raw) == {"version", "rules"} and type(raw["version"]) is int and raw["version"] == 1
        )
        rules = raw["rules"]
        check(type(rules) is dict and len(rules) <= MAX_RULES)
        for key, row in rules.items():
            check(type(key) is str and re.fullmatch(r"[a-f0-9]{64}", key) and type(row) is dict)
            check(
                set(row)
                == {
                    "scope",
                    "expected_format",
                    "http_mime",
                    "detected_format",
                    "validation_method",
                    "evidence_count",
                    "first_seen",
                    "last_seen",
                    "state",
                }
            )
            check(type(row["scope"]) is str and re.fullmatch(r"[a-f0-9]{64}", row["scope"]))
            check(type(row["expected_format"]) is str and row["expected_format"] in METHODS)
            check(
                type(row["http_mime"]) is str
                and normalized_mime(row["http_mime"]) == row["http_mime"]
            )
            check(
                (row["detected_format"], row["validation_method"])
                == METHODS[row["expected_format"]]
            )
            check(type(row["evidence_count"]) is int and 1 <= row["evidence_count"] <= 1_000_000)
            check(row["state"] in ("active", "disabled"))
            check(
                all(
                    type(row[k]) is str
                    and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", row[k])
                    for k in ("first_seen", "last_seen")
                )
            )
            check(
                key
                == self._key(row["scope"], ExpectedFormat(row["expected_format"]), row["http_mime"])
            )
            self._rules[key] = row

    @staticmethod
    def _key(scope_key: str, expected: ExpectedFormat, mime: str) -> str:
        check(type(expected) is ExpectedFormat and normalized_mime(mime) == mime)
        return hashlib.sha256(
            (scope_key + "\0" + expected.value + "\0" + mime).encode()
        ).hexdigest()

    def _save(self) -> None:
        check(not self._poisoned)
        try:
            self._runtime.write_registry({"version": 1, "rules": self._rules})
        except BaseException:
            self._poisoned = True
            raise

    def lookup(self, scope: MimeScope, expected: ExpectedFormat, http_mime: str) -> MimeRule | None:
        check(not self._poisoned)
        row = self._rules.get(self._key(_scope_key(scope), expected, http_mime))
        if row is None:
            return None
        return MimeRule(
            expected,
            http_mime,
            str(row["detected_format"]),
            str(row["validation_method"]),
            int(str(row["evidence_count"])),
            str(row["first_seen"]),
            str(row["last_seen"]),
            "active" if row["state"] == "active" else "disabled",
        )

    def begin_revalidation(
        self, scope: MimeScope, expected: ExpectedFormat, http_mime: str
    ) -> RevalidationTicket | None:
        check(not self._poisoned)
        key = self._key(_scope_key(scope), expected, http_mime)
        row = self._rules.get(key)
        if row is None or row["state"] != "active":
            return None
        # Persist disabled BEFORE reading any candidate bytes. A crash/failure is
        # fail-closed; only the same-process successful validation can restore it.
        row["state"] = "disabled"
        self._save()
        identity = secrets.token_hex(16)
        self._tickets[identity] = key
        return RevalidationTicket(identity)

    def record_validated(
        self,
        scope: MimeScope,
        http_mime: str,
        evidence: FormatEvidence,
        *,
        remember: bool,
        ticket: RevalidationTicket | None,
    ) -> str:
        check(not self._poisoned)
        check(type(remember) is bool and type(evidence) is FormatEvidence)
        expected = evidence.expected_format
        scope_key = _scope_key(scope)
        key = self._key(scope_key, expected, http_mime)
        valid = (
            evidence.learnable
            and (evidence.detected_format, evidence.validation_method) == METHODS[expected.value]
        )
        row = self._rules.get(key)
        authorized_ticket = ticket is not None and self._tickets.pop(ticket.identity, None) == key
        if not valid:
            return "not_learnable"
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if row is not None:
            if not authorized_ticket:
                return "disabled" if row["state"] == "disabled" else "not_recorded"
            row.update(
                state="active",
                evidence_count=min(int(str(row["evidence_count"])) + 1, 1_000_000),
                last_seen=now,
            )
            self._save()
            return "reconfirmed"
        if not remember:
            return "not_recorded"
        check(len(self._rules) < MAX_RULES)
        rule = MimeRule(
            expected,
            http_mime,
            evidence.detected_format,
            evidence.validation_method,
            1,
            now,
            now,
            "active",
        )
        self._rules[key] = {"scope": scope_key, **asdict(rule)}
        self._save()
        return "learned"
