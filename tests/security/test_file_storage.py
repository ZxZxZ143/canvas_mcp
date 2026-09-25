import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from canvas_mcp.domain.errors import ArtifactUnavailableError, ConfigurationError, StorageError
from canvas_mcp.domain.models import (
    AccessScope,
    Availability,
    ConnectionId,
    ExternalText,
    FileMetadata,
    FileReference,
    Observed,
    PrincipalId,
)
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.infrastructure.files.storage import ManagedStore
from conftest import ORIGIN

SCOPE = AccessScope(PrincipalId("local"), ConnectionId("synthetic"))


def metadata(filename="sample.txt", file_id="10"):
    known = Observed(Availability.AVAILABLE, None)
    return FileMetadata(
        file_id,
        "8",
        FileReference(file_id, "8"),
        ExternalText(filename),
        Observed(Availability.AVAILABLE, ExternalText(filename)),
        Observed(Availability.AVAILABLE, ExternalText("text/plain")),
        known,
        known,
        known,
        False,
        False,
        False,
        False,
    )


@pytest.fixture
def managed(tmp_path):
    settings = DeploymentSettings(ORIGIN, download_directory=tmp_path / "managed")
    store = ManagedStore(settings, SCOPE)
    try:
        yield store
    finally:
        store.close()


def save(store, filename="sample.txt", data=b"inert data"):
    pending = store.begin(SCOPE)
    store.write(pending, data)
    return store.finish(pending, metadata(filename), "text/plain", 0, "untrusted_text")


def test_storage_native_create_hash_atomic_finalize_and_cleanup(managed):
    pending = managed.begin(SCOPE)
    assert pending.path.suffix == ".part"
    managed.write(pending, b"inert data")
    result = managed.finish(pending, metadata(), "text/plain", 0, "untrusted_text")
    path = Path(result.local_path)
    assert path.read_bytes() == b"inert data"
    assert result.sha256 == hashlib.sha256(b"inert data").hexdigest()
    assert result.size == 10 and result.safe_filename == path.name and path.suffix == ".blob"
    assert not list(path.parent.glob("*.part"))
    assert managed.get(SCOPE, result.artifact_id) == result
    managed.cleanup(SCOPE, result.artifact_id)
    assert not path.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../file.pdf",
        "..\\file.pdf",
        "../../AGENTS.md",
        "C:\\Windows\\file",
        "\\\\server\\share",
        "NUL",
        "CON",
        "COM1",
        "foo.",
        "foo ",
        "foo.pdf::$DATA",
        "/a/b/file.pdf",
        "report.pdf",
        "REPORT.PDF",
    ],
)
def test_remote_filename_never_participates_in_storage_path(managed, name):
    result = save(managed, name)
    assert Path(result.local_path).is_relative_to(managed._settings.download_directory)
    assert result.safe_filename.startswith("f-") and result.safe_filename.endswith(".blob")
    assert name not in result.local_path


def test_same_name_and_same_file_are_fresh_immutable_downloads(managed):
    first, second = save(managed), save(managed)
    assert first.artifact_id != second.artifact_id and first.local_path != second.local_path
    assert Path(first.local_path).read_bytes() == Path(second.local_path).read_bytes()
    with pytest.raises(OSError):
        Path(first.local_path).write_bytes(b"replacement")


def test_scope_expiry_and_handle_invalidation(managed):
    result = save(managed)
    with pytest.raises(ArtifactUnavailableError):
        managed.get(replace(SCOPE, principal_id=PrincipalId("other")), result.artifact_id)
    managed._records[result.artifact_id].deadline = 0
    with pytest.raises(ArtifactUnavailableError):
        managed.get(SCOPE, result.artifact_id)
    assert not Path(result.local_path).exists()


def test_root_and_session_cannot_be_renamed_while_pinned(managed):
    result = save(managed)
    root = managed._settings.download_directory
    with pytest.raises(OSError):
        root.rename(root.with_name("moved"))
    session = Path(result.local_path).parent
    with pytest.raises(OSError):
        session.rename(session.with_name("moved"))


def test_abort_removes_partial_and_releases_reservation(managed):
    pending = managed.begin(SCOPE)
    managed.write(pending, b"partial")
    managed.abort(pending)
    assert not pending.path.exists() and not managed._pending


def test_collision_does_not_overwrite(managed, monkeypatch):
    first = save(managed)
    monkeypatch.setattr(
        "canvas_mcp.infrastructure.files.storage.secrets.token_hex", lambda _: first.artifact_id
    )
    with pytest.raises(StorageError):
        managed.begin(SCOPE)
    assert Path(first.local_path).read_bytes() == b"inert data"


def test_project_root_and_unc_rejected_without_creation(tmp_path):
    for path in (Path.cwd() / "downloads", Path("\\\\server\\share\\files"), Path("C:\\")):
        store = ManagedStore(DeploymentSettings(ORIGIN, download_directory=path), SCOPE)
        with pytest.raises(ConfigurationError):
            store.begin(SCOPE)
        store.close()


@pytest.mark.parametrize(
    "name", ["C:relative", "NUL", "CON.txt", "COM1", "trailing.", "trailing ", "file:stream"]
)
def test_windows_namespace_components_rejected(tmp_path, name):
    path = Path(name) if name.startswith("C:") else tmp_path / name
    store = ManagedStore(DeploymentSettings(ORIGIN, download_directory=path), SCOPE)
    with pytest.raises(ConfigurationError):
        store.begin(SCOPE)
    store.close()


def test_junction_in_root_or_ancestor_rejected(tmp_path):
    target, junction = tmp_path / "outside", tmp_path / "junction"
    target.mkdir()
    # Test fixture only: never execute downloaded content or production shell.
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True, capture_output=True
    )
    try:
        for root in (junction, junction / "managed"):
            store = ManagedStore(DeploymentSettings(ORIGIN, download_directory=root), SCOPE)
            with pytest.raises(StorageError):
                store.begin(SCOPE)
            store.close()
        assert list(target.iterdir()) == []
    finally:
        junction.rmdir()  # removes only the fixture junction, not its target


def test_exclusive_lease_blocks_second_store(managed):
    first = save(managed)
    second = ManagedStore(managed._settings, SCOPE)
    with pytest.raises(StorageError):
        second.begin(SCOPE)
    second.close()
    assert managed.get(SCOPE, first.artifact_id) == first


def test_crash_recovery_only_owned_known_session(managed):
    first = save(managed)
    pending = managed.begin(SCOPE)
    managed.write(pending, b"partial")
    managed._release()  # simulate OS releasing handles on process death
    managed._closed = True
    restarted = ManagedStore(managed._settings, SCOPE)
    try:
        second = save(restarted)
        assert not Path(first.local_path).exists() and not pending.path.exists()
        with pytest.raises(ArtifactUnavailableError):
            restarted.get(SCOPE, first.artifact_id)
        assert restarted.get(SCOPE, second.artifact_id) == second
    finally:
        restarted.close()


def test_unknown_recovery_object_is_not_deleted(managed):
    first = save(managed)
    unknown = Path(first.local_path).parent / "important.txt"
    unknown.write_bytes(b"do not delete")
    managed._release()
    managed._closed = True
    restarted = ManagedStore(managed._settings, SCOPE)
    with pytest.raises(StorageError):
        restarted.begin(SCOPE)
    restarted.close()
    assert unknown.read_bytes() == b"do not delete" and Path(first.local_path).exists()


def test_hard_link_is_rejected_before_recovery_deletion(managed, tmp_path):
    first = save(managed)
    managed._release()
    managed._closed = True
    alias = tmp_path / "alias"
    os.link(first.local_path, alias)
    restarted = ManagedStore(managed._settings, SCOPE)
    with pytest.raises(StorageError):
        restarted.begin(SCOPE)
    restarted.close()
    assert alias.read_bytes() == b"inert data" and Path(first.local_path).exists()


def test_root_with_inherited_permissions_fails_closed(tmp_path):
    root = tmp_path / "already-existing"
    root.mkdir()
    store = ManagedStore(DeploymentSettings(ORIGIN, download_directory=root), SCOPE)
    with pytest.raises(StorageError):
        store.begin(SCOPE)
    store.close()
    assert list(root.iterdir()) == []


def test_quotas_reserve_parallel_pending_bytes(managed):
    managed._settings = replace(
        managed._settings, max_download_bytes=5, max_artifact_bytes_total=10, max_artifact_count=2
    )
    first, second = managed.begin(SCOPE), managed.begin(SCOPE)
    with pytest.raises(StorageError):
        managed.begin(SCOPE)
    managed.abort(first)
    managed.abort(second)
    assert save(managed, data=b"hello").size == 5


def test_final_name_collision_does_not_replace(managed):
    pending = managed.begin(SCOPE)
    final = pending.path.with_suffix(".blob")
    final.write_bytes(b"existing")
    with pytest.raises(StorageError):
        managed.finish(pending, metadata(), "text/plain", 0, "untrusted_text")
    managed.abort(pending)
    assert final.read_bytes() == b"existing"
    final.unlink()  # exact test-created collision fixture only
