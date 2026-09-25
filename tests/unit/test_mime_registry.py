import copy
import json
from dataclasses import replace

import pytest

from canvas_mcp.domain.errors import StorageError
from canvas_mcp.domain.mime import ExpectedFormat as Format, MimeScope, RevalidationTicket
from canvas_mcp.infrastructure.files.format_probe import identify
from canvas_mcp.infrastructure.files.mime_registry import (
    JsonMimeCompatibilityRegistry,
    normalized_mime,
)
from canvas_mcp.infrastructure.files.mime_observation import diagnostic_mime

SCOPE = MimeScope("https://canvas.example.edu", "personal", "7")
MIME = "application/x-unusual-pdf"
EVIDENCE = identify(b"%PDF-1.7", Format.PDF)


class MemoryRuntime:
    def __init__(self):
        self.value = None
        self.fail = False

    def read_registry(self):
        return copy.deepcopy(self.value)

    def write_registry(self, value):
        if self.fail:
            raise StorageError()
        self.value = json.loads(json.dumps(value))


def learned():
    runtime = MemoryRuntime()
    registry = JsonMimeCompatibilityRegistry(runtime)
    assert registry.record_validated(SCOPE, MIME, EVIDENCE, remember=True, ticket=None) == "learned"
    return runtime, registry


def test_explicit_remember_required_and_expected_evidence():
    runtime = MemoryRuntime()
    registry = JsonMimeCompatibilityRegistry(runtime)
    assert (
        registry.record_validated(SCOPE, MIME, EVIDENCE, remember=False, ticket=None)
        == "not_recorded"
    )
    assert (
        registry.record_validated(
            SCOPE, MIME, identify(b"<html>", Format.PDF), remember=True, ticket=None
        )
        == "not_learnable"
    )
    assert runtime.value is None


def test_reconfirmation_preemptive_disable_persists_and_count_increments():
    runtime, registry = learned()
    ticket = registry.begin_revalidation(SCOPE, Format.PDF, MIME)
    assert ticket is not None
    assert (
        JsonMimeCompatibilityRegistry(runtime).lookup(SCOPE, Format.PDF, MIME).state == "disabled"
    )
    assert (
        registry.record_validated(SCOPE, MIME, EVIDENCE, remember=False, ticket=ticket)
        == "reconfirmed"
    )
    rule = JsonMimeCompatibilityRegistry(runtime).lookup(SCOPE, Format.PDF, MIME)
    assert rule.evidence_count == 2 and rule.state == "active" and rule.last_seen >= rule.first_seen
    assert (
        registry.record_validated(SCOPE, MIME, EVIDENCE, remember=True, ticket=ticket)
        == "not_recorded"
    )


def test_contradiction_or_crash_disabled_sticky_no_forged_ticket():
    runtime, registry = learned()
    ticket = registry.begin_revalidation(SCOPE, Format.PDF, MIME)
    assert (
        registry.record_validated(
            SCOPE, MIME, identify(b"<html>", Format.PDF), remember=True, ticket=ticket
        )
        == "not_learnable"
    )
    registry = JsonMimeCompatibilityRegistry(runtime)
    assert registry.begin_revalidation(SCOPE, Format.PDF, MIME) is None
    assert (
        registry.record_validated(
            SCOPE, MIME, EVIDENCE, remember=True, ticket=RevalidationTicket("forged")
        )
        == "disabled"
    )
    assert registry.lookup(SCOPE, Format.PDF, MIME).state == "disabled"


@pytest.mark.parametrize(
    "other",
    [
        replace(SCOPE, canvas_origin="https://other.example.edu"),
        replace(SCOPE, canvas_subject="8"),
        replace(SCOPE, application_profile="other"),
    ],
)
def test_no_cross_university_user_profile_rules(other):
    _, registry = learned()
    assert registry.lookup(other, Format.PDF, MIME) is None
    assert registry.begin_revalidation(other, Format.PDF, MIME) is None
    assert registry.lookup(SCOPE, Format.PDF, MIME).state == "active"


def test_failed_atomic_persistence_poisoned_instance():
    runtime, registry = learned()
    runtime.fail = True
    with pytest.raises(StorageError):
        registry.begin_revalidation(SCOPE, Format.PDF, MIME)
    with pytest.raises(StorageError):
        registry.lookup(SCOPE, Format.PDF, MIME)


@pytest.mark.parametrize(
    "mime",
    [
        "https://private.example/file?sig=private",
        "text/private?secret=yes",
        "text/plain\r\nprivate",
        "x/" + "a" * 64,
        "",
        "private",
        "text/with space",
    ],
)
def test_report_mime_rejects_url_or_arbitrary_text(mime):
    with pytest.raises(StorageError):
        normalized_mime(mime)


def test_report_strips_parameters_and_secret_even_case_encoded():
    assert normalized_mime('Text/Plain; filename="private-course-name"') == "text/plain"
    from canvas_mcp.domain.errors import DownloadRejectedError

    for mime in (
        "application/SUPER_SECRET",
        "application/super_secret",
        "application/%53UPER_SECRET",
        "text/plain; private=SUPER_SECRET",
    ):
        with pytest.raises(DownloadRejectedError):
            diagnostic_mime(mime, "SUPER_SECRET")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update(version=True),
        lambda raw: raw.update(extra="private"),
        lambda raw: next(iter(raw["rules"].values())).update(expected_format="exe"),
        lambda raw: next(iter(raw["rules"].values())).update(evidence_count=True),
        lambda raw: next(iter(raw["rules"].values())).update(http_mime="https://private.example/"),
        lambda raw: next(iter(raw["rules"].values())).update(validation_method="model_approved"),
    ],
)
def test_corrupt_registry_fails_closed(mutate):
    runtime, _ = learned()
    # Round-trip StrEnums as real JSON before corruption.
    import json

    runtime.value = json.loads(json.dumps(runtime.value))
    mutate(runtime.value)
    with pytest.raises(StorageError):
        JsonMimeCompatibilityRegistry(runtime)
