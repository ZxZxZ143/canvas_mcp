# Phase 6.1 local verification and independent boundary review

Verified on 2026-09-27. No public deployment, OAuth implementation, ChatGPT
connection, or real Canvas tool invocation was performed for this subphase.
Tests use synthetic tokens and fake Canvas/application data.

## Actual results

| Check | Result |
| --- | --- |
| Full `pytest -q --tb=short` | 1306 passed, one Starlette TestClient deprecation warning, 233.55 seconds |
| Final `pytest tests/mcp tests/plugin -q --tb=short` | 109 passed, same warning, 14.23 seconds; includes the additional official Python SDK client regression |
| `mypy` | Passed, 68 source files |
| `ruff check` | Passed |
| `ruff format --check` | Passed, 181 Python files |
| `git diff --check` | Passed |
| Isolated sdist/wheel build | Passed with setuptools 84.0.0 |
| Final wheel forced installation | Passed, package remains version 0.1.0 |
| `pip check` | No broken requirements |
| Isolated package imports | 67 submodules imported successfully |
| Installed/source comparison | All 68 source Python files byte-identical to installed wheel |
| Installed personal plugin comparison | All seven plugin files byte-identical; no plugin package changes |
| Installed personal plugin launcher | Actual cached v0.1.0 launcher initialized successfully and listed all 15 tools, without Canvas calls |
| CLI `--help` | Passed for stdio/HTTP MCP, credentials, connection/academic/file smoke, MIME, route, transport and public-URL probes |
| Isolated HTTP import | Windows Credential Manager module absent from imported modules |
| Official MCP Inspector 2.8.0 | Local HTTP initialization, 14 tools via tools/list, synthetic profile tool invocation passed |
| Official Python MCP client | Local HTTP initialize, 14 tools, synthetic profile invocation passed; protocol 2025-11-25 |
| Linux runtime dependency lock | All 29 exact pins downloaded as compatible Linux CPython 3.13 wheels; complete dependency graph checked from wheel metadata |
| Docker build | Not verified: local Docker Linux engine did not respond; final bounded build timed out after 45 seconds, no image produced |
| Container runtime/healthcheck | Not run because no image was built |

The first full-suite attempt put pytest's temporary files inside the repository.
The existing secure download/runtime layer correctly rejected those paths,
causing 200 failures and 1103 passes. Rerunning with pytest's default temporary
directory outside the repository passed all 1306 tests. No storage policy was
weakened to accommodate the test directory. The subsequent additional SDK
client test passed in the final 109-test MCP/plugin run.

Windows sandbox temporary-file permissions required running build/install and
tests with the requested access outside the restricted sandbox. Docker was
installed but its Linux engine was initially stopped. Starting Desktop and
checking readiness did not yield a responsive engine. Only this task's hung
`canvas-student:phase6.1` build helpers were stopped; no Docker image or public
service was created. The Linux wheel check is a dependency validation and must
not be reported as a successful Docker build. Once the local engine is healthy,
run the build and local-only container command in the architecture guide and
check `/health` and default `/mcp` denial.

## HTTP boundaries covered by tests

- SDK initialization, initialized notification, discovery and tool invocation.
- All 14 read-tool logical results match stdio projections and schemas.
- Stdio retains all 15 tools and managed-path download behavior.
- Startup, health and discovery perform no Canvas calls.
- Default deny refuses all MCP methods; explicit development flags and a
  separate bearer token are required; malformed modes fail closed.
- Unknown/withheld tools, unexpected arguments and malformed protocol input
  cannot echo synthetic tokens, exception paths or signed URLs.
- Remote artifact projection allowlists metadata and omits local paths.
- Windows credentials and local download roots are rejected by HTTP composition.
- Host/Origin validation rejects rebinding; arbitrary forwarded headers cannot
  bypass it; no wildcard CORS is emitted.
- Body/header bounds, tool timeout, concurrency rejection and request identity
  cleanup are checked.
- Scoped credential/connection mismatch is rejected. Repeated authorized
  leases preserve the same scoped provider and its continuation state, with
  one lifespan close.
- Logging remains safe even when the host installs a root handler after app
  creation. Configured development secret reflections are rejected.

## Independent remote-mcp-boundary-reviewer

The requested independent reviewer inspected all ten boundaries: adapter
business logic, production auth denial, remote paths, secrets/errors/logs,
Docker context, stdio behavior, credential isolation, health, proxy trust and
explicit download withholding. No justified CRITICAL/HIGH issue was found.

One MEDIUM issue was reproduced: SDK private input/header warnings could reach
a host log handler added after `create_app()`. The SDK uses both direct root
logging and named transport-security loggers. The fix filters the emitting
root/MCP loggers as well as existing handlers while a request principal is
active. A late-handler regression was added. Independent re-review passed all
eight original malformed-input/header probes, including Content-Type, with
synthetic secrets absent from HTTP responses, service logs and captured host
logs. The finding is resolved.

The reviewer also independently confirmed unchanged signatures, defaults,
descriptions and annotations for all 15 stdio tools, matching schemas for the
14 HTTP tools, and rejection of three unauthorized principal/account cases.
No reviewer edited source or accessed actual credentials.

## Status

HTTP MCP works locally and stable stdio/plugin regression checks pass. The
Phase 6.1 implementation and local transport validation are complete, but the
overall verification checkpoint remains pending the Docker build/runtime
check. Do not call the Docker image validated or deploy publicly. Phase 6.2
has not started.
