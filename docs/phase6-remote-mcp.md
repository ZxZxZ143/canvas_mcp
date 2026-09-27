# Phase 6.1 — Remote MCP foundation

This is a local development transport scaffold. It does not deploy publicly,
connect ChatGPT, implement OAuth, or begin Phase 6.2. The personal Codex plugin
remains version 0.1.0, local, read-only, and stdio.

## Architecture

```text
stdio Codex → mcp_server → shared mcp/tools → application
HTTP MCP    → mcp_http_server + HTTP boundary → shared mcp/tools → application
                                                        ↓
                                             existing Canvas infrastructure
```

`python -m canvas_mcp.mcp_server` remains the stdio entry point. Its existing
`create_server(connection, download_root)` import contract and 15 production
tools remain available. Tool definitions, argument validation, safe errors and
normalized projections are shared in `mcp/tools.py` and `mcp/projection.py`.
Neither transport implements Canvas queries or file policy.

`python -m canvas_mcp.mcp_http_server` serves the official Python MCP SDK's
Streamable HTTP ASGI app at `/mcp`. It uses stateless HTTP with JSON responses,
including the SDK's initialization, notification, discovery, and tool-call flow.
There is no custom JSON-RPC implementation or deprecated HTTP+SSE adapter.

The existing supported SDK 1.x line is retained to preserve the stable plugin;
the runtime lock uses MCP 1.30.0. Its negotiated initialization protocol is
2025-11-25. The current Inspector can use its compatible `legacy` protocol era.
This is not a claim to implement the newer 2026 protocol era or upgrade stdio to
SDK 2.x. Assess that migration separately against actual client requirements.

Sources consulted before implementation:

- [OpenAI MCP server concepts](https://developers.openai.com/plugins/concepts/mcp-server)
  and [local server / Inspector quickstart](https://developers.openai.com/plugins/build/app-quickstart).
- [MCP Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
- [Official supported Python SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
  and [1.30.0 documentation](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v1.30.0/README.md).
- [Official MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector).

## Remote tool surface

HTTP exposes these 14 existing read tools with the same argument and output schemas:

| Tool | Purpose |
| --- | --- |
| `canvas_get_profile` | Connected student profile |
| `canvas_list_courses` | Course discovery |
| `canvas_list_assignments` | Compact assignment rows |
| `canvas_get_assignment` | Assignment details |
| `canvas_get_assignment_context` | Full available assignment context |
| `canvas_get_upcoming` | Upcoming workload |
| `canvas_get_overdue` | Overdue workload |
| `canvas_list_modules` | Course modules |
| `canvas_list_module_items` | Module entries |
| `canvas_list_announcements` | Announcements |
| `canvas_list_calendar_events` | Calendar events |
| `canvas_get_course_grade` | Own available grade summary |
| `canvas_list_files` | File metadata discovery |
| `canvas_get_file_metadata` | One authorized file's metadata |

`canvas_download_file` is not registered on HTTP. Calling that name returns the
same safe validation error as an unknown tool, without invoking downloads.
Remote file extraction, bytes, delivery URLs and replacement tools are deferred
to Phase 6.3. Stdio downloads retain the existing approval-gated local artifact.

The transport-aware artifact projection keeps `managed_local_path` on stdio
and allowlists only `artifact_id`, `size`, `sha256`, `content_type`, and `trust`
on HTTP. No server storage descriptor is serialized remotely. Coursework may
itself quote arbitrary paths as untrusted text; those quotations are not server
filesystem disclosures and retain their original content.

## Authentication and account scope

`McpAuthenticator` authenticates before any SDK request is processed. The
default `deny` mode rejects every `/mcp` method with HTTP 401, even if a bearer
header is supplied. `/health` alone is unauthenticated.

Development authentication requires all three explicit settings:

```text
MCP_HTTP_AUTH_MODE=development
MCP_HTTP_DEVELOPMENT=true
MCP_HTTP_DEV_TOKEN=<separate random URL-safe bearer token, 32–256 characters>
```

There is no `none` or unauthenticated production mode. Invalid settings fail
startup with a fixed structured error and no configuration echo. Development
tokens are compared in constant time; duplicate Authorization headers are
rejected. This scaffold is not production authentication. Do not publish a
development-mode server or use a public tunnel.

The immutable `McpPrincipal` contains a subject and authorized connection ID.
A request-local ContextVar carries only identity, never credentials. Tool
invocation acquires an account-scoped application connection. The app-owned
single-account registry rejects a different principal or connection, and its
credential source independently checks the same scope. It keeps the scoped
Canvas provider alive to preserve bounded continuation cursors across requests
and closes it on lifespan shutdown. No mutable module-global token or credential
switching is used. This prepares an isolation boundary without implementing
multi-tenancy.

In Phase 6.2, OAuth validation implements the authentication port and supplies
authorized principal/account mappings. The registry and credential provider
must then resolve scopes from those verified mappings, not from tool arguments.
OAuth metadata, audience/resource validation, scopes, expiry, refresh/revocation
and encrypted remote secret storage remain unimplemented.

## Credentials and startup

Stdio still uses the existing Windows Credential Manager source when selected
by the personal launcher. HTTP never selects or imports that credential store;
an inherited `CANVAS_CREDENTIAL_PROVIDER=windows` setting fails closed.

The existing `CredentialSource` port supplies scoped tokens to infrastructure.
The added `CanvasCredentialProvider` resolves such a source for an authorized
account. HTTP development uses an immutable environment source bound to its
one explicit scope. Phase 6.2 will implement remote secret storage behind this
port; Windows Credential Manager is not the remote architecture.

The default denied server can start with no Canvas settings or credentials.
In development mode, existing Canvas settings and a syntactically valid
environment token are required, but startup performs no Canvas request. Canvas
clients are composed lazily when an authenticated tool needs them. Health,
initialize, and tools/list never query Canvas. Canvas outages therefore do not
prevent startup or health checks. HTTP requires an empty `DOWNLOAD_DIRECTORY`
and never constructs Windows managed download storage.

## HTTP configuration and security

The environment contract is typed in `infrastructure/config/remote.py`.
`.env.example` is a placeholder reference, never loaded automatically.

| Setting | Default / bounds |
| --- | --- |
| `MCP_HTTP_HOST` | `127.0.0.1`; another IP requires explicit configuration |
| `MCP_HTTP_PORT` | `8000`; 1–65535 |
| `MCP_HTTP_AUTH_MODE` | `deny`; only `deny` or `development` |
| `MCP_HTTP_DEVELOPMENT` | `false`; exact `true` required for development auth |
| `MCP_HTTP_DEV_TOKEN` | Empty; required only for development auth |
| `MCP_HTTP_MAX_BODY_BYTES` | 65536; 16384–1048576 |
| `MCP_HTTP_MAX_HEADER_BYTES` | 16384; 1024–65536; also at most 100 headers |
| `MCP_HTTP_REQUEST_TIMEOUT` | 65 seconds; finite, greater than 0, at most 300 |
| `MCP_HTTP_MAX_CONCURRENCY` | 8 active requests; 1–64 |

The ASGI boundary bounds declared and streamed request bodies, header totals,
the entire request including body reads and tool execution, concurrency, and
buffered responses (262144 bytes). SDK body bounds provide another layer.
Uvicorn also sets connection concurrency, incomplete h11 header/event bounds,
and keep-alive timeout. Over-capacity requests fail immediately rather than
forming an unbounded queue. JSON buffering allows safe timeout/error responses
before headers are emitted. This does not implement long-lived subscriptions
or server-initiated streams.

The SDK validates Host and Origin against exact local authorities. Missing
Origin is supported for non-browser clients; arbitrary Origin is rejected.
No wildcard CORS surface is installed. Uvicorn runs with `proxy_headers=False`;
Forwarded and X-Forwarded-* values do not determine identity, scheme, host, or
client IP. Phase 6.2 must configure the chosen framework's explicit trusted
proxy peers and approved external Host/Origin authorities when selecting a
deployment provider. Do not set wildcard proxy trust.

`GET /health` returns only `{"status":"ok"}`. It exposes no users, credentials,
configuration, Canvas connectivity or results. It is process liveness, not
readiness for a configured or reachable Canvas account.

Structured request logs allow only a generated request ID, allowlisted MCP
operation/tool name, duration, status and fixed error code. Authorization,
Canvas tokens, signed URLs, private arguments and coursework are never logged.
SDK validation/transport traces are suppressed while handling authenticated
requests, including a host logger added after app creation. Protocol validation
errors and unexpected exceptions are replaced by fixed safe messages. Known
development secret reflections are rejected before response emission. Uvicorn
access/exception logs are disabled in the process entry point. Stdio logging
still goes to stderr and stdout remains exclusively MCP.

## Local HTTP and Inspector test

Use this synthetic fixture without Canvas credentials:

```powershell
& .\.venv\Scripts\python.exe tests/mcp/http_fake_server.py
```

In another terminal, connect the current MCP Inspector to:

```text
http://127.0.0.1:8000/mcp
Transport: Streamable HTTP
Authorization: Bearer SYNTHETIC_DEV_AUTH_TOKEN_1234567890
Protocol era: legacy (compatible initialization flow)
```

The fixture's token is public synthetic test data and must never be used for a
real account. The fixture serves only fake profile/course results.

```powershell
npm exec --yes --package=@modelcontextprotocol/inspector@2.8.0 -- mcp-inspector --cli --server-url http://127.0.0.1:8000/mcp --transport http --method tools/list --header "Authorization: Bearer SYNTHETIC_DEV_AUTH_TOKEN_1234567890" --format json
npm exec --yes --package=@modelcontextprotocol/inspector@2.8.0 -- mcp-inspector --cli --server-url http://127.0.0.1:8000/mcp --transport http --method tools/call --tool-name canvas_get_profile --header "Authorization: Bearer SYNTHETIC_DEV_AUTH_TOKEN_1234567890" --format json
```

For configured development instead, set the three HTTP development settings
and existing `CANVAS_BASE_URL`/`CANVAS_ACCESS_TOKEN` in the process environment,
leave `DOWNLOAD_DIRECTORY` empty, and use `python -m canvas_mcp.mcp_http_server`.
Only make real Canvas calls as an explicit opt-in live test.

## Docker

The image uses CPython 3.13, an exact Linux runtime dependency lock, isolated
wheel installation, and a non-root UID/GID 10001. The build context is an
allowlist: Dockerfile, packaging/lock, and runtime source. `.env*`, `.git`,
venvs, tests, downloads, diagnostics and build/runtime artifacts are excluded.
No secrets are baked into the image. The healthcheck reads only minimal health.

Build and run a local denied service (no secrets required):

```powershell
docker build --tag canvas-student:phase6.1 .
docker run --rm --name canvas-student-phase6 -p 127.0.0.1:8000:8000 -e MCP_HTTP_HOST=0.0.0.0 canvas-student:phase6.1
```

The explicit container bind supports port forwarding, while the published host
port remains loopback-only. `/health` is available at
`http://127.0.0.1:8000/health`; `/mcp` returns 401 until explicit development
authentication is configured. Supply credentials only through runtime secret
injection or the container's environment; never add them to build arguments,
Dockerfile, source files, or an image layer.

The process command is `python -m canvas_mcp.mcp_http_server`. Future deployment
requires Docker, stable HTTPS and a long-running HTTP service. No assumptions
about Vercel, Render, Railway, Fly or another provider are committed here.

## Mobile and next phases

As of the checked OpenAI product documentation, custom MCP apps are supported
on ChatGPT web, not the native ChatGPT mobile apps.
[OpenAI developer-mode FAQ](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).
Transport and application boundaries can support future mobile availability.
Phase 6.4 may add a separate mobile web/PWA companion if required.

Phase 6.2 selects hosting and adds OAuth, HTTPS/proxy policy and remote secret
storage. Phase 6.3 adds remote file handling. Neither phase is implemented here.
See [verification and independent review](phase6-remote-mcp-validation.md) for
actual results and limitations of this checkpoint.
