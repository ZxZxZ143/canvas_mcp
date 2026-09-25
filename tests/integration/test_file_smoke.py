import asyncio
import sys
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from canvas_mcp import file_smoke
from canvas_mcp.application.files import FileService
from canvas_mcp.composition import CanvasConnection
from canvas_mcp.infrastructure.canvas.file_mapping import file_metadata
from canvas_mcp.domain.models import FileReference
from canvas_mcp.domain.models import PageRequest
from canvas_mcp.domain.errors import UpstreamUnavailableError
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from conftest import PROFILE, response

FILE = {
    "id": 10,
    "filename": "PRIVATE.txt",
    "display_name": "PRIVATE",
    "content-type": "text/plain",
    "size": 5,
}
COURSE = {
    "id": 8,
    "name": "PRIVATE",
    "course_code": "S",
    "enrollments": [{"type": "student", "enrollment_state": "active", "user_id": 7}],
}


def test_metadata_only_smoke_has_no_body_download_or_storage(stack, monkeypatch, capsys):
    class NoDownloads:
        def __getattr__(self, name):
            raise AssertionError("No download operation without opt in")

    async def run():
        @asynccontextmanager
        async def opened(env):
            async with stack(
                [
                    response(PROFILE),
                    response([COURSE]),
                    response(COURSE),
                    response([FILE]),
                    response(FILE),
                ]
            ) as s:
                yield CanvasConnection(
                    s.app, s.scope, s.settings, s.academic, FileService(s.provider, NoDownloads())
                )
                assert b"/assignments" not in b"".join(s.backend.writes)
                requests = [w for w in s.backend.writes if w.startswith(b"GET")]
                assert len(requests) == 5  # explicit authorization is memoized, no extra GET
                assert b"per_page=5&" in requests[3] and b"sort=size" in requests[3]

        monkeypatch.setattr(file_smoke, "open_canvas_connection", opened)
        assert await file_smoke.run() == 0

    asyncio.run(run())
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "NOT_TESTED" in output and "not_requested" in output
    assert "duration_ms=" not in output and "budget_ms=" not in output


@pytest.mark.parametrize("first_error", [UpstreamUnavailableError(), TimeoutError()])
def test_discovery_keeps_later_course_after_failed_probe(first_error, capsys, stack):
    metadata = file_metadata(FILE, FileReference("10", "8"))
    contexts = []

    class Connection:
        def _context(self):
            return self.context

        def _files(self):
            return self

        def _academic(self):
            return self

        async def get_course(self, ctx, course):
            contexts.append(ctx)

        async def list_courses(self, ctx, page):
            contexts.append(ctx)
            return SimpleNamespace(
                data=SimpleNamespace(
                    items=[SimpleNamespace(id="9"), SimpleNamespace(id="8")],
                    next_cursor=None,
                    complete=True,
                )
            )

        async def list_course_files(self, ctx, course, page, query):
            contexts.append(ctx)
            if course == "9":
                raise first_error
            return SimpleNamespace(
                data=SimpleNamespace(items=[metadata], next_cursor=None, complete=True)
            )

        async def get_file_metadata(self, ctx, ref):
            contexts.append(ctx)
            return SimpleNamespace(data=metadata)

    connection = Connection()
    connection.service = connection
    connection._settings = DeploymentSettings("https://canvas.example.edu")

    async def run():
        async with stack([]) as s:
            connection.context = s.ctx
            result = await file_smoke.discover(connection)
            assert result.candidate == metadata
            assert not result.complete and result.courses_attempted == 2
            assert result.courses_completed == 1 and result.courses_partial_or_failed == 1

    asyncio.run(run())
    assert len({id(ctx) for ctx in contexts}) == 1
    assert "PARTIAL" in capsys.readouterr().out


@pytest.mark.parametrize("empty", [False, True])
def test_opt_in_exactly_one_small_download_and_cleanup(monkeypatch, capsys, empty):
    calls = []
    metadata = file_metadata(FILE, FileReference("10", "8"))
    descriptor = SimpleNamespace(artifact_id="a" * 32, sha256="b" * 64, size=5, redirects=1)

    class Connection:
        async def get_profile(self):
            calls.append("profile")

        async def download_file(self, reference):
            assert reference == metadata.source
            calls.append("download")
            return SimpleNamespace(data=descriptor)

        async def resolve_download(self, identity):
            calls.append("verify")
            assert identity == descriptor.artifact_id
            return SimpleNamespace(data=descriptor)

        async def cleanup_download(self, identity):
            calls.append("cleanup")
            assert identity == descriptor.artifact_id

    @asynccontextmanager
    async def opened(env):
        assert int(env["MAX_DOWNLOAD_BYTES"]) <= file_smoke.SAMPLE_LIMIT
        yield Connection()

    async def discover(connection, **kwargs):
        return file_smoke.DiscoveryResult(candidate=None if empty else metadata)

    monkeypatch.setattr(file_smoke, "open_canvas_connection", opened)
    monkeypatch.setattr(file_smoke, "discover", discover)
    assert asyncio.run(file_smoke.run(download_sample=True)) == 0
    assert calls == (["profile"] if empty else ["profile", "download", "verify", "cleanup"])
    assert "PRIVATE" not in capsys.readouterr().out


def test_live_flag_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["file_smoke", "--download-sample"])
    with pytest.raises(SystemExit) as caught:
        file_smoke.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("limit", [None, 100])
def test_ordinary_production_list_contract_is_unchanged(stack, limit):
    async def run():
        async with stack([response(PROFILE), response(COURSE), response([FILE])]) as s:
            connection = CanvasConnection(
                s.app, s.scope, s.settings, s.academic, FileService(s.provider, None)
            )
            if limit is None:
                await connection.list_course_files("8")
            else:
                await connection.list_course_files("8", PageRequest(limit))
            request = [w for w in s.backend.writes if w.startswith(b"GET")][-1]
            assert f"per_page={25 if limit is None else limit}&".encode() in request
            assert b"sort=name" in request
            assert connection._context().budget.monotonic_deadline - time.monotonic() <= 60

    asyncio.run(run())
