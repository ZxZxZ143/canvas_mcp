# Profile and course foundation

This guide records the preserved Phase 1 behavior. Phase 2 extends it with the [academic read layer](academic-read-layer.md); the exclusions below describe the original phase, not the current full capability set. The operator subsequently reported a successful live profile/course check. `--debug` now adds fixed metadata warning codes without private values.

Supported endpoints are exactly `GET /api/v1/users/self` and `GET /api/v1/courses`. The application returns the existing `Profile`, `Course`, `Page` and `Result` models, never raw Canvas JSON. Profile output contains only ID, inert display name and timezone availability, excluding login/email/avatar fields. Course output contains ID, inert name/code and optional term. Missing optional metadata is explicitly unavailable.

## Install and run checks

Python 3.11+ is required. From PowerShell:

```powershell
Set-Location E:\canvas_mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m build --wheel
```

Normal tests use synthetic HTTP streams, no credentials and no live DNS/Canvas. The fixture forbids real DNS; connection-policy tests inject DNS answers and an in-memory socket/TLS backend. Tests use asyncio directly. Imports do not read the environment or initiate requests. The development environment created during this task reuses installed libraries through system-site-packages; a fresh environment can use the commands above.

## Configuration and secrets

Only infrastructure/composition reads the environment. `CANVAS_BASE_URL` and `CANVAS_ACCESS_TOKEN` are required. `REQUEST_TIMEOUT` defaults to 20 seconds, finite and positive up to 60; `LOG_LEVEL` accepts DEBUG/INFO/WARNING/ERROR. The origin must use HTTPS/443. Case, the default port and a trailing root slash are normalized. Paths, userinfo, query/fragment, whitespace and private IP-literal origins are rejected. Public DNS answers need no static pin. The local-personal `CANVAS_TRUSTED_PRIVATE_IPS` setting permits only exact configured RFC1918/ULA answers for the Canvas hostname; mixed DNS with any untrusted address fails closed. The deprecated `CANVAS_ALLOW_PRIVATE_ORIGIN=true` alone is rejected. Loopback and link-local remain blocked, and external redirect chains do not inherit private trust. DNS answers are vetted at connection time; the transport dials a vetted IP while TLS verifies the original hostname.

Download settings remain dormant: an empty `DOWNLOAD_DIRECTORY` is accepted because no download service exists. A supplied path must be absolute but is not created/used. `CACHE_TTL` must be zero. No real `.env` or automatic dotenv loading exists.

The credential adapter snapshots only the token for one server-created scope; it never stores the entire environment. Close/recreate the connection to change credentials. Profile verification binds the authenticated subject; later subject mismatch or 401 invalidates the connection and cursors. There is no global mutable auth state.

HTTPCore 1.0 supplies streaming and its public network-backend hook. Transport-level retries are disabled; the dedicated client owns bounded GET retries, per-request/aggregate deadlines, strict JSON, response byte limits and safe errors. There is no implicit proxy/env authentication, cookie persistence, redirects or decompression. HTTP tracing is suppressed in the sensitive request context even at DEBUG. Structured events contain fixed event/operation values, correlation IDs and attempt counts, never headers, URLs, payloads or exception strings. Reflected token text is rejected before and after the shared inert-text transformation.

## Courses and pagination

The initial student query uses `enrollment_type=student`, `enrollment_state=active`, and `include[]=term`. `active_only=False` removes the active-enrollment filter; it does not guarantee historical/inactive course completeness beyond Canvas's response. Custom student roles use the student enrollment type. Teacher/observer course queries are outside this slice.

The application returns one bounded page (default 25, maximum 100) and an opaque connection-local continuation. Continue until `next_cursor` is null. `Page.complete` describes list exhaustion; `Result.complete` may remain false when optional metadata is unavailable. The smoke command traverses pages and deduplicates IDs.

Pagination parsing is centralized. Next URLs must preserve exact origin, endpoint and original query filters/page size; only the opaque `page` value changes. External targets, redirects, malformed links and repeated targets are rejected. Header size, cursor count/bytes/TTL, pages per traversal and shared HTTP attempts are bounded. Hard limits fail explicitly rather than reporting an incomplete count as exhaustive. Unusual Link formats fail closed and require a deliberate compatibility update.

## Optional real smoke test

Run this yourself in a separate PowerShell terminal. Input is hidden and the token is not placed in command history or requested in chat. Variables are temporary and removed in `finally`. Only `--live` permits real reads; this command never runs in normal pytest.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode Profile -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

Success prints a normalized display name, course count and optional warning count. Failure prints a fixed category with exit code 1; no raw JSON, token, header or traceback. Same-OS-user process inspection remains outside the local guarantee; do not run untrusted tools in the credential-bearing terminal.

## Application entry point

Use `async with open_canvas_connection() as connection` from `canvas_mcp.composition`, then await `connection.get_profile()` or `connection.list_courses(PageRequest(...))`. The connection owns/closes its pool. Use one local runtime per process on one asyncio loop.

`ConnectionService` depends only on the narrow `LmsConnectionQueries` port, which the existing broader `LmsQueries` protocol now extends. This retains the boundary without fake implementations of out-of-scope methods. No ADR changed. MCP, assignments, modules, files, submissions, grades, calendar, announcements, OAuth, persistent storage/cache and writes remain unimplemented.

## Primary references

The implementation uses the documented [Canvas Bearer authentication](https://developerdocs.instructure.com/services/canvas), [user details](https://developerdocs.instructure.com/services/canvas/resources/users), [course listing](https://developerdocs.instructure.com/services/canvas/resources/courses), [pagination](https://developerdocs.instructure.com/services/canvas/basics/file.pagination), and [HTTPCore network backend hook](https://www.encode.io/httpcore/network-backends/). Automated tests do not depend on these sites.
