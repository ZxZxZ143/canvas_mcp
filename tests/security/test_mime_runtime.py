import os
from pathlib import Path

import pytest

from canvas_mcp.domain.errors import ConfigurationError, StorageError
from canvas_mcp.infrastructure.files.mime_runtime import MimeRuntime, REGISTRY_NAME


def test_private_runtime_lease_and_pinned_root(tmp_path):
    root = tmp_path / "diagnostics"
    with MimeRuntime(root) as runtime:
        runtime._fs.private(runtime._pins[-1])
        with pytest.raises(StorageError):
            with MimeRuntime(root):
                pytest.fail("second lease")
        with pytest.raises(OSError):
            root.rename(root.with_name("renamed"))
        runtime.write_registry({"version": 1, "rules": {}})
    with MimeRuntime(root) as runtime:
        assert runtime.read_registry() == {"version": 1, "rules": {}}


def test_state_atomic_replacement_failure_preserves_old_and_erases_temp(tmp_path, monkeypatch):
    root = tmp_path / "diagnostics"
    with MimeRuntime(root) as runtime:
        runtime.write_registry({"version": 1, "rules": {}})

        def fail(*a, **k):
            raise StorageError()

        monkeypatch.setattr(runtime._fs, "rename", fail)
        with pytest.raises(StorageError):
            runtime.write_registry({"version": 1, "rules": {"uncommitted": 1}})
        assert runtime.read_registry() == {"version": 1, "rules": {}}
        assert not list(root.glob("*.tmp"))


def test_runtime_rejects_project_cloud_unc_and_inherited_acl_root(tmp_path):
    paths = [
        Path.cwd() / "diagnostics",
        Path("C:/"),
        Path("//server/share/diagnostics"),
        tmp_path / "OneDrive" / "diagnostics",
        tmp_path / "Desktop" / "diagnostics",
        tmp_path / "CON",
    ]
    for path in paths:
        with pytest.raises(ConfigurationError):
            with MimeRuntime(path):
                pytest.fail("unsafe root")
    root = tmp_path / "inherited"
    root.mkdir()
    with pytest.raises(StorageError):
        with MimeRuntime(root):
            pytest.fail("nonprivate root")


def test_unknown_or_crash_state_blocks_without_deleting(tmp_path):
    root = tmp_path / "diagnostics"
    with MimeRuntime(root) as runtime:
        handle = runtime._fs.open_file(root / "pending-crash.tmp", create=True, write=True)
        runtime._fs.write(handle, b"inert partial state")
        runtime._fs.close_handle(handle)
    with pytest.raises(StorageError):
        with MimeRuntime(root):
            pytest.fail("unknown crash state accepted")
    assert (root / "pending-crash.tmp").exists()


def test_registry_hardlink_is_rejected(tmp_path):
    root = tmp_path / "diagnostics"
    with MimeRuntime(root) as runtime:
        runtime.write_registry({"version": 1, "rules": {}})
    os.link(root / REGISTRY_NAME, tmp_path / "link.json")
    with pytest.raises(StorageError):
        with MimeRuntime(root):
            pytest.fail("hardlinked registry")


def test_registry_duplicate_keys_and_oversize_fail_closed(tmp_path):
    root = tmp_path / "diagnostics"
    with MimeRuntime(root) as runtime:
        handle = runtime._fs.open_file(root / REGISTRY_NAME, create=True, write=True)
        runtime._fs.write(handle, b'{"version":1,"version":1,"rules":{}}')
        runtime._fs.close_handle(handle)
        with pytest.raises(StorageError):
            runtime.read_registry()
        with pytest.raises(StorageError):
            runtime.write_registry({"large": "a" * 65536})


def test_report_nonoverwrite_and_quota(tmp_path, monkeypatch):
    with MimeRuntime(tmp_path / "diagnostics") as runtime:
        monkeypatch.setattr(
            "canvas_mcp.infrastructure.files.mime_runtime.secrets.token_hex", lambda _: "a" * 32
        )
        runtime.write_report([{"result": "synthetic"}])
        with pytest.raises(StorageError):
            runtime.write_report([{"result": "different"}])
        assert "synthetic" in next(runtime.root.glob("mime-report-*.json")).read_text()
