# Phase 6.2 — Render Free personal deployment

Status: Render adaptation implemented; live deployment/OAuth/ChatGPT evidence
pending. No final hostname, real token exchange or cold-start result is claimed.
Render replaces Railway because the available Railway workspace had an expired
trial, no credits and an invoice that blocked free deployment. That history is
preserved in [the resource-server architecture record](phase6-2-remote-personal.md).

## Account and Git deployment

Sign in to the existing Render account/workspace. Do not add a card, upgrade,
create paid resources or resume unrelated services. In New → Web Service choose
the existing `ZxZxZ143/canvas_mcp` repository through its existing Git connection
(or Public Git Repository). Deploy a reviewed, committed `codex/` branch.
Git builds receive committed source only; uncommitted local files are unavailable.

Set name `canvas-student-mcp` (subject to availability), Language **Docker**,
root directory blank, Dockerfile path `./Dockerfile`, build context repository
root, and compute **Free ($0/month)**. Use exactly one instance in one region.
Set Advanced → Health Check Path to `/health`; leave Docker Command blank for
normal operation. Disable auto deploy during staged OAuth configuration.

Bootstrap with these non-secret variables before the hostname is assigned:

| Variable | Bootstrap value |
| --- | --- |
| `MCP_HTTP_AUTH_MODE` | `deny` |
| `MCP_HTTP_DEPLOYMENT` | `production` |
| `MCP_HTTP_HOST` | `0.0.0.0` |

Leave `PORT` to Render; the injected value takes precedence over `MCP_HTTP_PORT`.
Local defaults remain loopback, port 8000, deny authentication. Provider markers
such as `RENDER` and `RAILWAY_ENVIRONMENT_ID` confer no security authority.
Production and OAuth configurations reject development flags and bearer tokens.
Bootstrap has minimal liveness and denies all MCP requests, with no Canvas runtime.

The Dockerfile uses Python 3.13, UID/GID 10001:10001 and a pinned Linux dependency
set. Windows credential storage is not a remote dependency and is never selected.
The allowlisted `.dockerignore` excludes tests, diagnostics, local plugins,
downloads, generated files and all environment files. `.gitignore` excludes
runtime secrets and generated data. Never pass PATs or OAuth tokens via Docker
ARGs, command lines, Git, image tags or uploaded source archives. Render exposes
environment variables as build arguments; the Dockerfile references no secret ARG.

## Domain and final runtime configuration

After creation, record the **actual** public `https://<service>.onrender.com`
hostname shown by Render. Do not infer it from the requested name. Managed TLS
terminates at Render; the application binds plain HTTP internally. Security
identity comes exclusively from configured `MCP_PUBLIC_BASE_URL`. Host must
match its exact authority (optional explicit :443); no `*.onrender.com` wildcard.
Forwarded/X-Forwarded headers are not trusted. `/health` allows provider probes
and returns only `{"status":"ok"}` without Canvas/Auth0 calls or configuration.

Configure Render Environment after obtaining the real hostname:

| Variable | Final value/purpose |
| --- | --- |
| `MCP_HTTP_AUTH_MODE` | `oauth` |
| `MCP_HTTP_DEPLOYMENT` | `production` |
| `MCP_HTTP_HOST` | `0.0.0.0` |
| `MCP_PUBLIC_BASE_URL` | Actual exact HTTPS origin, no trailing slash |
| `AUTH0_DOMAIN` | Selected tenant's exact public domain |
| `AUTH0_ISSUER` | `https://<same-auth0-domain>/` |
| `AUTH0_AUDIENCE` | Actual exact public origin plus `/mcp` |
| `AUTH0_REQUIRED_SCOPES` | `canvas:read` |
| `AUTH0_ALLOWED_SUBJECT` | Private immutable enrolled user subject; secret |
| `CANVAS_BASE_URL` | `https://canvas.narxoz.kz` |
| `CANVAS_ACCESS_TOKEN` | Private server-only PAT; secret |
| `CANVAS_CREDENTIAL_PROVIDER` | `environment` or unset |

Do not set private-IP overrides, development auth, download directory/origins,
Windows credential settings or diagnostic runtime directories remotely. Public
DNS must resolve Canvas exclusively to public global addresses. Unexpected private
resolution fails closed; never add Narxoz's internal IP to cloud configuration.

## Auth0 and ChatGPT Web

Only after the domain exists, create the Auth0 API with identifier equal to the
exact final `/mcp` URL, RS256 and only `canvas:read`. Preserve issuer identification,
PKCE S256, resource-bound audience verification, bounded JWKS caching and the
server's single-subject allowlist. Dashboard account identity is not an OAuth
application user's subject. No client secret or management token belongs in MCP.

Use the currently verified ChatGPT public client/CIMD registration path supported
by Auth0's free configuration. Take callback and client metadata URLs from the
actual ChatGPT flow; never invent them. Require explicit consent and a narrow
client/API grant. If a required feature is paid, stop that path and assess a
documented free supported alternative without weakening authentication.

In ChatGPT Web's current custom app/MCP management, enter the actual `/mcp` URL
and complete Auth0 login/consent. Verify resource/audience, scope and PKCE through
the actual flow without printing authorization codes, JWTs or private subject.
Test courses, coursework due this week and explanation of the next assignment
against real Canvas. No mobile compatibility or Phase 6.3 capability is claimed.

## Explicit Canvas smoke on Free

Free has no SSH/dashboard shell or one-off jobs. Do not upgrade to obtain them.
For an explicitly operator-selected verification deployment, temporarily set
Docker Command to:

```sh
sh -c 'python -m canvas_mcp.remote_smoke --verify-canvas && exec python -m canvas_mcp.mcp_http_server'
```

This performs the requested smoke once in that deployment. Inspect only its
safe outputs: `canvas_destination_class=public_global`, `canvas_connection=ok`,
`courses_accessible=true`. Failure prevents that verification deployment from
starting. Remove the override and redeploy immediately after verification.
Normal CMD and health/startup never call Canvas or Auth0. No diagnostic HTTP
route, background job or second service is introduced.

## Sleep, ephemeral state and limits

This is a personal preview/beta service. Render Free spins down after 15 minutes
without inbound HTTP/WebSocket traffic; waking can take about a minute. Render
receives the initial request before the container starts, so extending application
timeouts does not solve proxy cold starts. Keep the existing bounded request
timeout. Startup only constructs lazy resources and performs no upstream calls.

Filesystem changes disappear on sleep/restart/deployment. The service stores no
PAT, OAuth tokens, identity database or required pagination state on disk.
In-memory cursors and rate limits reset. A cursor from a previous instance is
rejected with a sanitized validation error; restart the listing without a cursor.
HTTP MCP instructions tell clients to do so. No database or horizontal scaling.

Free workspaces receive 750 instance-hours/month shared across free web services;
only active hours count. Exhaustion suspends free services until the next month.
Bandwidth and build minutes also have included quotas: with no payment method,
over-limit free services/builds are suspended or disabled rather than automatically
charged. Review workspace usage and confirm no payment method before activation.
High outbound volume may cause suspension. No scheduled Canvas synchronization,
scraping, third-party uptime pings or artificial keepalive requests are permitted.

After warm ChatGPT success, leave the service untouched for more than 15 minutes.
Do not poll its health or metadata during that interval. Invoke from ChatGPT,
record first attempt (wait/reconnect/temporary failure/success), retry once
manually if needed, and record elapsed wake duration. Do not promise always-on
or seamless cold starts without observed evidence.

## Evidence and rollback

Record Render build success, Python/UID runtime evidence, Free/one instance,
actual HTTPS origin, health 200, safe build/runtime logs and startup duration.
Verify root and path-aware protected-resource metadata, no-auth 401 OAuth challenge,
wrong valid Auth0 subject 403 before Canvas composition, initialize, 14 tools,
profile, courses and upcoming work. Synthetic wrong-user evidence does not replace
the live test when a safe second enrolled identity is available.

Review only expected public routes: `/health`, `/mcp` and the two OAuth metadata
paths. Logs must contain no PAT/JWT/subject, course names, assignment text,
arguments, local paths or signed URLs. Do not publish raw private logs.

To revoke remote access set auth mode `deny` and redeploy, or suspend this service.
Revoke the dedicated Auth0 client/API grant and rotate the Canvas PAT only when
explicitly authorized. Use the existing local personal stdio plugin 0.1.0 as
fallback; it retains its 15 tools and Windows Credential Manager independently.

## Current evidence

Live Render build/domain/health, real OAuth, Canvas remote smoke, ChatGPT Web
and cold-start checks are pending. Phase 6.2 is incomplete until all three live
systems work together. Automated test and review results are recorded after runs.

Official documentation checked 2026-09-27:
[Free services](https://render.com/docs/free),
[Docker](https://render.com/docs/docker),
[Web services and PORT](https://render.com/docs/web-services),
[Health checks](https://render.com/docs/health-checks).
