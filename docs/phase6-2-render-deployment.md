# Phase 6.2 — Render Free personal deployment

Status: Phase 6.2 complete as a personal read-only beta. Render Free, real Auth0
login/consent/code exchange and ChatGPT Web queries passed against real Canvas.
Post-idle recovery also passed; it required reconnecting an expired OAuth session.
The service is not always-on, remote PDF content remains unavailable and Phase
6.3 has not started.
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

For the verified free path, use a Native public strict third-party Auth0 client,
Authorization Code only, with one user-delegated API grant for `canvas:read` and
token-endpoint authentication `none`. ChatGPT's successful discovery may default
to DCR because Auth0 advertises a registration endpoint even when tenant DCR is
disabled. Select **Custom OAuth client**, enter the public client ID, leave client
secret empty, select `none` and keep only `canvas:read`. Disable optional OIDC
profile/offline scopes; strict third-party API authorization is the chosen flow.
Recheck the actual callback after discovery: the live form can replace its initial
bootstrap callback. Save that exact callback in Auth0 before connecting.

The verified API access-token lifetime is 900 seconds. Offline access and the
client's refresh-token grant are disabled. A later ChatGPT invocation may require
reconnecting through the existing Auth0 session after token expiry; this is
separate from Render's idle spin-down and does not justify adding broader scopes.

In ChatGPT Web's current custom app/MCP management, enter the actual `/mcp` URL
and complete Auth0 login/consent. Verify resource/audience, scope and PKCE through
the actual flow without printing authorization codes, JWTs or private subject.
Test courses, coursework due this week and explanation of the next assignment
against real Canvas. No mobile compatibility or Phase 6.3 capability is claimed.

## Explicit Canvas smoke on Free

Free has no SSH/dashboard shell or one-off jobs. Do not upgrade to obtain them.
For an explicitly operator-selected verification deployment, temporarily set
Docker Command to:

```text
/bin/sh -c python -m canvas_mcp.remote_smoke --verify-canvas && exec python -m canvas_mcp.mcp_http_server
```

This performs the requested smoke once in that deployment. Inspect only its
safe outputs: `canvas_destination_class=public_global`, `canvas_connection=ok`,
`courses_accessible=true`. Failure prevents that verification deployment from
starting. Remove the override and redeploy immediately after verification.
This is a value for Render's Docker Command field, not a command to run in a
local shell. On the verified service, quotes around the script were preserved
as command text and caused exit 127; the unquoted field above ran successfully.
The operator may prepend `python --version && id &&` after `/bin/sh -c ` in the
same explicit verification deployment to confirm Python and non-root runtime UID.
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

Verified on 2026-09-27:

- Render service `srv-dasdor60tbcc73evk93g`, Docker Free, Oregon, one instance;
  workspace Hobby with no card on file and projected charges of $0. Auto deploy
  is off; health path is `/health`.
- Actual assigned origin: `https://canvas-student-mcp-z70n.onrender.com`.
  Exact API resource/audience: `https://canvas-student-mcp-z70n.onrender.com/mcp`.
- Reviewed source commit `d382685ad40becf3d713668e3387053ef479424e` on
  `codex/phase6-2-render` built and deployed successfully in Render deployment
  `dep-dasdor60tbcc73evka5g` (48.5 seconds). Local Docker commands timed out;
  the actual Render image build succeeded. Bootstrap `/health` returned 200
  with exactly `{"status":"ok"}`; unauthenticated MCP returned 401 in deny mode.
- Auth0 API is RS256, only `canvas:read`, access lifetime 900 seconds, explicit
  consent, no offline access, no M2M access. Client `Canvas Student ChatGPT`
  is Native, strict third-party, public Authorization Code only. Its exact
  callback was copied from the actual ChatGPT custom-client form. DCR and CIMD
  remain disabled.
- After successful discovery, ChatGPT's custom-client form supplied the final
  exact callback `https://chatgpt.com/connector_platform_oauth_redirect`; this
  replaced the initial bootstrap form callback. Registration uses the predefined
  public client with token-endpoint authentication `none`, only `canvas:read`,
  and optional OIDC profile/offline scopes disabled.
- User separately approved promoting `Username-Password-Authentication` to
  domain level, including present and future third-party clients in this tenant.
  Public signups are disabled; both flags were verified after reload. The
  resource server still permits only the private enrolled subject.
- Full pytest: 1,334 passed (one upstream Starlette/httpx deprecation warning).
  Mypy: 71 source files passed. Ruff check and format passed. Source distribution
  and wheel built; installed wheel imports, dependency check, 11 CLI help paths
  and real local stdio initialize/list of 15 tools passed. Installed local plugin
  files remained byte-identical.
- Two independent read-only deployment/OAuth reviews found no Critical/High
  issues. Bounded JWKS outage-cooldown behavior and strict PKCE metadata parsing
  findings were fixed and re-reviewed before the deployed commit.

- Explicit verification deployment `dep-daseannpn0mc7381o5a0` succeeded in
  33.9 seconds. Actual runtime: Python 3.13.15 and UID/GID 10001:10001.
  Smoke outputs: `canvas_destination_class=public_global`,
  `canvas_connection=ok`, `courses_accessible=true`.
- Verification override was removed. Normal OAuth deployment
  `dep-dasec2o473hc73fu8dk0` is Live using the unchanged default Docker CMD.
  Health is 200 with the exact minimal body. Both protected-resource metadata
  routes are 200 with the exact final resource, issuer and `canvas:read` scope.
  No-auth `/mcp` is 401 with the path-aware metadata challenge; `/debug` is 404.
- Live boundary checks: untrusted Origin 421, invalid synthetic bearer 401 with
  `invalid_token`, unknown diagnostics route 404. Forged Forwarded/X-Forwarded
  headers did not change the configured resource in public metadata.
- ChatGPT Web personal plugin connected; real Canvas profile was displayed as
  its account label. Its tool UI showed exactly 14 Read tools and no downloader.
  A fresh chat called `canvas_list_courses` and returned five active courses.
  Runtime logs independently showed OAuth validation success, initialize 200,
  profile 200 and courses 200. Observed structured runtime logs contained no
  private subject, JWT-shaped token, tool arguments or coursework payload.
- ChatGPT's seven-day upcoming query returned a real pending assignment and
  scanned five of five active courses. Runtime confirmed
  `canvas_get_upcoming` 200 in 31,307 ms. Canvas reported
  `optional_metadata_unavailable` and `complete: false`; ChatGPT preserved that
  warning rather than claiming complete coverage.
- A follow-up called `canvas_get_assignment_context` and explained the real
  due date, upload submission type, current status and related module items.
  The assignment description was empty and a related module PDF's content was
  unavailable through the 14-tool remote interface. ChatGPT explicitly limited
  its explanation instead of inventing instructions or submitting anything.
- Strict third-party Auth0 policy requires PKCE for Authorization Code flows;
  Auth0 supports only S256. Real public-client exchange succeeded under that
  policy; no authorization code, verifier or access token was printed or saved.
  See [strict third-party controls](https://auth0.com/docs/get-started/applications/third-party-applications/security-controls)
  and [PKCE authorization](https://auth0.com/docs/api/authentication/authorization-code-flow-with-pkce/authorize-with-pkce).

### Actual idle/cold recovery

Agent requests to the service stopped at 2026-09-27 10:02:34 UTC. No health,
metadata, Canvas or MCP pings were sent during the idle interval; the intermediate
log-view tab was closed. The next fresh ChatGPT request was submitted at
10:19:18.863 UTC, after 16 minutes 44.863 seconds of agent inactivity.

- First attempt: ChatGPT reported that the connection had expired and presented
  **Reconnect**. It did not silently return the old course result. This is the
  expected possibility with the 900-second access-token lifetime and no refresh
  token; automatic first-attempt recovery is not claimed.
- Manual Reconnect was selected at 10:19:56.349 UTC. The connection dialog became
  available by the observation 43 seconds later. Continuing reused the existing
  Auth0 login session without another password entry or expanded grant.
- Render runtime instance changed from `gfgzh` to `m4hgw`, confirming a new
  container after idle. The default CMD remained active; no smoke command ran at
  startup or wake. Fresh initialize returned 200 at 10:21:23 UTC (410 ms), fresh
  profile returned 200 at 10:21:27 UTC (2,967 ms), and a new
  `canvas_list_courses` returned 200 at 10:21:33 UTC (890 ms). ChatGPT again
  reported five active courses.
- The complete first-request-to-successful-Canvas-call cycle was approximately
  134 seconds, including manual UI steps and OAuth reconnect. Render's proxy-only
  wake duration was not isolated from discovery and browser interaction; the
  result must not be presented as a precise 134-second container startup.

A live wrong-subject token is unavailable because only one safe enrolled identity
exists; synthetic valid wrong-subject tests verify 403 before Canvas composition.
All required real Render/OAuth/ChatGPT checks are recorded. No JWT, PAT, private
subject or coursework payload is recorded in this evidence.

Official documentation checked 2026-09-27:
[Free services](https://render.com/docs/free),
[Docker](https://render.com/docs/docker),
[Web services and PORT](https://render.com/docs/web-services),
[Health checks](https://render.com/docs/health-checks).
