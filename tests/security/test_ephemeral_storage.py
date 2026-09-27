"""Actual Linux descriptor/containment/sandbox/cancellation checks, run in Docker build."""

import asyncio
import os
import stat
import sys

import pytest

from canvas_mcp.domain.errors import (
    FileContentTimeoutError,
    FileContentTooLargeError,
    FileParseError,
    StorageError,
)
from canvas_mcp.domain.file_content import ContentSelection
from canvas_mcp.infrastructure.files import content_runner as runner
from canvas_mcp.infrastructure.files.ephemeral import EphemeralStore
from file_content_fixtures import (
    pdf_bytes,
    package_bytes,
    docx_parts,
    pptx_parts,
    unicode_pdf_bytes,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux container descriptor boundary"
)


def make(tmp_path, body=b"inert"):
    store = EphemeralStore(_root=tmp_path)
    pending = store.begin()
    store.write(pending, body)
    return store, pending


def test_private_generated_directory_file_and_readonly_descriptor(tmp_path):
    store, pending = make(tmp_path)
    directory = tmp_path / store._name
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(os.fstat(pending.handle).st_mode) == 0o600
    reader = store.reader(pending)
    assert os.read(reader, 5) == b"inert"
    with pytest.raises(OSError):
        os.write(reader, b"overwrite")
    store.close()
    store.close()
    assert not list(tmp_path.iterdir())


def test_stream_limit_rejects_before_write_and_cleanup(tmp_path):
    store = EphemeralStore(4, _root=tmp_path)
    pending = store.begin()
    with pytest.raises(FileContentTooLargeError):
        store.write(pending, b"12345")
    assert pending.count == 0
    store.close()
    assert not list(tmp_path.iterdir())


def test_no_symlink_or_hardlink_following(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must survive")
    store, pending = make(tmp_path)
    file = tmp_path / store._name / store._file_name
    file.unlink()
    file.symlink_to(outside)
    with pytest.raises(StorageError):
        store.write(pending, b"bad")
    store.close()
    assert outside.read_bytes() == b"must survive"
    store, pending = make(tmp_path)
    file = tmp_path / store._name / store._file_name
    linked = tmp_path / "hardlink"
    os.link(file, linked)
    with pytest.raises(StorageError):
        store.reader(pending)
    store.close()
    assert linked.read_bytes() == b"inert"


def test_root_traversal_and_symlink_rejected(tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    for root in (alias, tmp_path / ".." / tmp_path.name):
        store = EphemeralStore(_root=root)
        with pytest.raises(StorageError):
            store.begin()
        store.close()
    assert list(tmp_path.iterdir()) == [alias]


def test_existing_directory_is_not_deleted_or_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "canvas_mcp.infrastructure.files.ephemeral.secrets.token_hex", lambda n: "a" * 32
    )
    existing = tmp_path / ("canvas-content-" + "a" * 32)
    existing.mkdir(mode=0o700)
    marker = existing / "owned.txt"
    marker.write_bytes(b"owned")
    store = EphemeralStore(_root=tmp_path)
    with pytest.raises(FileExistsError):
        store.begin()
    store.close()
    assert marker.read_bytes() == b"owned"


@pytest.mark.parametrize(
    "fmt,body",
    [
        ("pdf", pdf_bytes()),
        ("docx", package_bytes(docx_parts())),
        ("pptx", package_bytes(pptx_parts())),
        ("txt", b"Read only text"),
        ("md", b"# Read me"),
        ("csv", b"a,b\n1,2"),
        ("json", b'{"tool":"do not execute"}'),
    ],
    ids=["pdf", "docx", "pptx", "txt", "md", "csv", "json"],
)
def test_real_sandboxed_worker_formats_and_cleanup(tmp_path, fmt, body):
    async def run():
        store, pending = make(tmp_path, body)
        try:
            store.validate_archive(pending, fmt)
            result = await runner.parse_file(
                store.reader(pending),
                pending.count,
                pending.digest.hexdigest(),
                fmt,
                ContentSelection(),
            )
            assert result["content_available"]
            assert result["units"]
        finally:
            store.close()
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_unicode_pdf_cmap_is_decoded_inside_deny_open_sandbox(tmp_path):
    async def run():
        store, pending = make(tmp_path, unicode_pdf_bytes())
        try:
            result = await runner.parse_file(
                store.reader(pending),
                pending.count,
                pending.digest.hexdigest(),
                "pdf",
                ContentSelection(),
            )
            assert [ord(c) for c in result["units"][0]["text"]] == [1046]
        finally:
            store.close()
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["bad_pdf", "bad_hash", "timeout", "cancel", "huge_output", "raw_error"]
)
def test_worker_errors_cancellation_and_timeout_reap_before_cleanup(tmp_path, monkeypatch, failure):
    processes = []
    original = asyncio.create_subprocess_exec

    async def recording(*args, **kwargs):
        assert kwargs["close_fds"] and len(kwargs["pass_fds"]) == 1
        assert set(kwargs["env"]) == {"LANG", "TZ"}
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording)
    if failure in ("timeout", "cancel"):
        monkeypatch.setattr(runner, "_BOOTSTRAP", "import time; time.sleep(60)")
        monkeypatch.setattr(runner, "REMOTE_PARSER_TIMEOUT_SECONDS", 0.15)
    elif failure == "huge_output":
        monkeypatch.setattr(
            runner,
            "_BOOTSTRAP",
            "import sys; sys.stdout.buffer.write(b'x'*200000); sys.stdout.flush()",
        )
    elif failure == "raw_error":
        monkeypatch.setattr(
            runner,
            "_BOOTSTRAP",
            "raise RuntimeError('SUPER_SECRET_REMOTE_FILE_TOKEN /tmp/private?Signature=HIDDEN')",
        )

    async def run():
        store, pending = make(
            tmp_path, b"%PDF-1.7 malformed" if failure == "bad_pdf" else pdf_bytes()
        )
        fd = store.reader(pending)
        digest = "0" * 64 if failure == "bad_hash" else pending.digest.hexdigest()
        operation = asyncio.create_task(
            runner.parse_file(fd, pending.count, digest, "pdf", ContentSelection())
        )
        try:
            if failure == "cancel":
                while not processes:
                    await asyncio.sleep(0.001)
                operation.cancel()
            expected = (
                asyncio.CancelledError
                if failure == "cancel"
                else FileContentTimeoutError
                if failure == "timeout"
                else FileParseError
            )
            with pytest.raises(expected) as caught:
                await operation
            assert "SUPER_SECRET" not in str(caught.value)
            assert all(process.returncode is not None for process in processes)
        finally:
            store.close()
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_worker_sandbox_blocks_network_paths_processes_and_writes(tmp_path, monkeypatch):
    script = """import sys,json,os,socket,subprocess
sys.path.insert(0,sys.argv[1])
from canvas_mcp.infrastructure.files.parser_sandbox import lock_down
from canvas_mcp.infrastructure.files.ocr import OcrEngine
args=json.loads(sys.stdin.buffer.read())
engine=OcrEngine()
assert len(os.listdir('/proc/self/task'))==1
lock_down(args['fd'])
blocked=[]
for action in (lambda: open('/etc/passwd','rb'),lambda: socket.socket(),lambda: subprocess.Popen(['/bin/true']),lambda: os.write(args['fd'],b'bad')):
    try: action()
    except (OSError,PermissionError): blocked.append(True)
    else: blocked.append(False)
sys.stdout.write(json.dumps({'content':{'blocked':blocked,'secret_present':bool(os.environ.get('CANVAS_ACCESS_TOKEN'))}}))
"""
    monkeypatch.setattr(runner, "_BOOTSTRAP", script)
    monkeypatch.setenv("CANVAS_ACCESS_TOKEN", "SUPER_SECRET_REMOTE_FILE_TOKEN")

    async def run():
        store, pending = make(tmp_path)
        try:
            result = await runner.parse_file(
                store.reader(pending),
                pending.count,
                pending.digest.hexdigest(),
                "txt",
                ContentSelection(),
            )
            assert result == {"blocked": [True] * 4, "secret_present": False}
        finally:
            store.close()

    asyncio.run(run())


def test_repeated_cancellation_during_spawn_reaps_before_return(tmp_path, monkeypatch):
    processes = []
    original = asyncio.create_subprocess_exec

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.15)
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    monkeypatch.setattr(runner, "_BOOTSTRAP", "import time; time.sleep(60)")

    async def run():
        store, pending = make(tmp_path)
        task = asyncio.create_task(
            runner.parse_file(
                store.reader(pending),
                pending.count,
                pending.digest.hexdigest(),
                "txt",
                ContentSelection(),
            )
        )
        try:
            for _ in range(3):
                await asyncio.sleep(0.02)
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(processes) == 1 and processes[0].returncode is not None
        finally:
            store.close()
        assert not list(tmp_path.iterdir())

    asyncio.run(run())


def test_directory_replacement_before_open_rejected_without_deleting_replacement(
    tmp_path, monkeypatch
):
    original = os.open
    moved = tmp_path / "moved"

    def swapping(path, flags, *args, **kwargs):
        if str(path).startswith("canvas-content-"):
            directory = tmp_path / path
            directory.rename(moved)
            directory.mkdir(mode=0o700)
            (directory / "marker").write_text("must survive")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping)
    store = EphemeralStore(_root=tmp_path)
    with pytest.raises(StorageError):
        store.begin()
    with pytest.raises(StorageError):
        store.close()
    assert (tmp_path / store._name / "marker").read_text() == "must survive"
