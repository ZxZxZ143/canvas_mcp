# Phase 6.2 — resource-server architecture and historical Railway evaluation

Render Free Web Service is now the active hosting target. Follow
[the Render deployment guide](phase6-2-render-deployment.md) for current deployment
steps and evidence. The Railway instructions and blocker below are retained as
history and optional provider support; no Railway payment or upgrade is authorized.

Status: implementation and synthetic validation only. The selected Railway
workspace currently blocks deployment and plan changes because its trial is
expired, credits are zero, and an outstanding invoice must be resolved. No
payment, paid upgrade, public service, Auth0 application/grant, or ChatGPT link
has been created. No final resource/domain exists yet. Do not mark Phase 6.2
complete until the live evidence checklist below is satisfied.

After the user authorized another free workspace, the account's workspace
selector was inspected: only its existing workspace is available. New Workspace
offers paid Pro/Hobby and is also invoice-blocked. No alternate free workspace
could be selected or created within this account.

## Architecture and boundaries

ChatGPT Web performs authorization-code OAuth with Auth0 and PKCE S256. The
HTTP server is an OAuth protected resource, not an authorization server. Each
MCP request verifies a signed Auth0 access token and authorizes exactly one
configured immutable subject before application composition or Canvas access.
The verified `McpPrincipal` contains only subject and `personal_canvas`
connection identity. A scoped registry retains one Canvas provider and its
bounded continuation state; every lease checks the current principal.

`RemoteSecretProvider` binds a runtime Canvas PAT to that one connection. The
OAuth credential and Canvas credential have separate destinations and uses.
Neither is exchanged for the other. The backend has no client secret,
management token, refresh-token store, or stored ChatGPT access tokens. The PAT
is captured from runtime configuration and never compiled into the image.

There are 14 read-only remote tools. Downloads/extraction and write operations
are unavailable. The local 0.1.0 stdio plugin keeps all 15 existing tools and
its existing Windows credential/launch behavior.

## Railway configuration

Use the existing Dockerfile for a long-running service; `railway.toml` selects
Docker, `/health`, the existing HTTP module, one replica, and three retries.
Use one region and one instance; horizontal scaling would break in-memory
pagination continuations and independent rate-limit accounting. Generate a
Railway HTTPS domain after the service exists and record its exact authority.
The audience is the exact URL ending in `/mcp`, without a trailing slash.

Keep the service on a genuinely free plan. Railway currently advertises a $0
Free plan with $1 monthly usage credit; this does not guarantee a continuously
running Python service fits that credit. Exhaustion can interrupt service.
The current workspace does not expose a usable free-plan switch while its
invoice is outstanding. Do not enter payment details or subscribe to Hobby/Pro
to bypass the user's zero-cost constraint.

Runtime settings (placeholders only):

| Variable | Value/purpose |
| --- | --- |
| `MCP_HTTP_AUTH_MODE` | `oauth` |
| `MCP_HTTP_DEPLOYMENT` | `production` (explicit, provider-independent) |
| `MCP_HTTP_HOST` | `0.0.0.0` |
| `PORT` | Railway injection; typed integer, takes precedence |
| `MCP_HTTP_PORT` | Optional local fallback, default `8000` |
| `MCP_PUBLIC_BASE_URL` | `https://<service>.up.railway.app` |
| `AUTH0_DOMAIN` | `<tenant>.<region>.auth0.com` |
| `AUTH0_ISSUER` | `https://<same-auth0-domain>/`, exact trailing slash |
| `AUTH0_AUDIENCE` | `https://<service>.up.railway.app/mcp` |
| `AUTH0_REQUIRED_SCOPES` | `canvas:read` |
| `AUTH0_ALLOWED_SUBJECT` | Private immutable subject, never committed/logged |
| `CANVAS_BASE_URL` | `https://canvas.narxoz.kz` |
| `CANVAS_ACCESS_TOKEN` | Private server-only PAT, runtime secret |
| `CANVAS_CREDENTIAL_PROVIDER` | `environment` or unset |

Do not set `MCP_HTTP_DEV_TOKEN`, `MCP_HTTP_DEVELOPMENT`, private-IP overrides,
download directory, or download origins remotely. Development authentication
is rejected whenever explicit `MCP_HTTP_DEPLOYMENT=production` is set. Provider
markers confer no security authority. OAuth also
rejects development flags/tokens. `deny` remains a fail-closed bootstrap mode
with liveness only and no configured Canvas provider. Local defaults remain
loopback/8000/deny. Directly creating `RemoteSettings` is a Python test API;
production always uses the validated environment loader.

Inject private values using service variables after provisioning. Do not use
Docker ARGs, image tags, command lines, source archives, or build logs to carry
secrets. Railway can expose variables during builds, but this Dockerfile does
not reference them. Keep one region/replica in service settings as well.

## Auth0 setup and free client registration

Create an API named Canvas Student Remote whose identifier is the **final
exact `/mcp` URL**. Select RS256; add only the `canvas:read` permission. Configure
a short access-token lifetime such as 900 seconds. Require explicit user
consent and limit the API grant to the intended ChatGPT client/read scope.
Disable database self-signup for the connection used by this personal app and
use a closed personal enrollment/policy. Independently, set the server's exact
allowed subject after the intended user signs in. Dashboard membership is not
an OAuth application-user identity. Do not infer a subject from an email.

Under Tenant Settings > Advanced, verify Resource Parameter Compatibility
Profile is enabled. The inspected tenant already has it enabled: Auth0 checks
`audience` first and, when absent, uses `resource`. Client authorization and
token requests must carry the exact MCP resource. No audience translation
proxy is implemented. Enable Include Issuer in Authorization Responses and
verify published metadata and successful/error callbacks contain the exact
issuer before using a stable OpenAI callback.

Prefer CIMD only when the tenant can import the real OpenAI document with a
supported **free** token authentication method. Current Auth0 manual CIMD
requires an administrator to preview/import a URL, creates strict third-party
clients, and requires an explicit API client grant. It is not unrestricted
automatic DCR. The inspected tenant currently advertises CIMD as disabled.
OpenAI's current metadata publishes `none` and `private_key_jwt`; its legacy
singular preference is `private_key_jwt`. Auth0 documents Private Key JWT as
Enterprise-only. Do not purchase Enterprise or assume the importer negotiates
the plural field. Preview the actual document and verify `none` is accepted.
If it is not, use OpenAI's supported predefined public OAuth client mode:
register a native/public Auth0 app, token authentication `none`,
authorization-code/refresh grants, and required S256 PKCE; enter its public
client ID in the ChatGPT management UI. No client secret belongs in this
server. Copy the exact callback shown in the current OpenAI UI, with no
wildcards. Do not register invented callback IDs or a replacement CIMD file.

For CIMD, use the exact document shown by the connection UI. Issuer-enabled
eligible connections use `https://chatgpt.com/oauth/client.json` and
`https://chatgpt.com/connector_platform_oauth_redirect`; others use
callback-specific URLs. Strict Auth0 third-party apps currently do not support
OIDC scopes/ID tokens, whereas ChatGPT requests advertised OIDC scopes. Verify
this combination with the actual tenant/client rather than claiming success.
The predefined first-party public client path can support advertised OIDC
scopes. Reject any setup requiring a paid feature under the free-only request.

The tenant's Include Issuer in Authorization Responses setting was enabled
and the dashboard confirmed it checked. A real public discovery/JWKS read also
verified issuer identification, S256, fixed endpoints, and public keys. CIMD
is still advertised false. Resource compatibility was already enabled.
No provider applications or grants have been created: the final Railway
resource does not exist and live registration remains pending. Auth0's current
trial is not evidence of permanent free-tier availability.

## Metadata, validation, and errors

The official MCP SDK creates RFC 9728 metadata at
`/.well-known/oauth-protected-resource/mcp`. A root alias serves the same
document at `/.well-known/oauth-protected-resource`. The exact resource,
Auth0 issuer and `canvas:read` are configuration-derived, never inferred from
Host/Forwarded headers. The metadata contains no identity or credentials.

Every tool declares top-level `securitySchemes` and its compatibility `_meta`
mirror. OAuth-only `canvas_get_profile` has `_meta["openai/profile"]=true` and
an object schema with only string `id` and `name`, additional properties
forbidden. Its id is the actual immutable Canvas provider id serialized as a
string, stable for this single institution/account. No caller account/user
argument is accepted. Local stdio and development HTTP retain their original
normalized profile envelope.

Missing/invalid tokens return 401 and `WWW-Authenticate` linking the path-aware
metadata and read scope. Bad signatures, issuer, resource, expiry, future nbf
or iat, malformed/unsigned tokens, and unrelated client/ID tokens fail closed.
A valid wrong subject returns 403; a valid allowed user missing scope returns
403 with an `insufficient_scope` challenge. None can open Canvas. Tokens can
have the exact resource audience alone, or Auth0's documented resource plus
the **exact issuer** `/userinfo` audience when OIDC scopes are used. Arbitrary
extra or duplicate audiences are rejected.

Authentication is enforced before MCP dispatch, so OAuth failures are HTTP
challenges. There is no tool-level OAuth fallback requiring a fabricated
CallToolResult `_meta["mcp/www_authenticate"]`. That field is required by
OpenAI when a tool itself reports an OAuth linking failure. Here Canvas PAT
failures are separate safe MCP `authentication_error` results, instructing
operator PAT maintenance, and must never initiate another Auth0 login.

Discovery/JWKS fetches use two fixed issuer-origin HTTPS URLs and the existing
global-IP DNS/connect policy. No redirects, arbitrary metadata URLs, proxy
environment, or client-provided key URLs are followed. Responses are bounded
to 64 KiB, fetches to 5 seconds, connections to 2, and JWKS to 16 RSA keys of
at least 2048 bits. Cache lifetime is 300 seconds. Unknown key ids can refresh
after a shared 30-second cooldown, allowing rotation without an attacker
causing a fetch per request. Validation always rechecks JWT claims/signature;
only public signing keys/discovery are cached. Provider failure denies access.

Logout/disconnect and grant/refresh-token revocation are managed by
ChatGPT/Auth0. Offline JWT verification cannot immediately observe revocation
of an already issued access token: it remains usable until expiry. Keep the
short TTL; for an urgent shutdown remove the allowed binding, switch runtime
to `deny` and redeploy, or stop the Railway service. No custom refresh store.

## HTTP, network, logs, and bounds

Only `/health`, `/mcp` and the two protected-resource metadata paths are public.
There are no HTTP debug/smoke/file/Windows endpoints. Health returns only
`{"status":"ok"}` and contacts neither Auth0 nor Canvas. Its Host can be
Railway's healthcheck host. Every other OAuth route requires the exact
configured public authority (optionally explicit :443). An absent Origin is
accepted; a supplied Origin must equal the public base URL. No wildcard
Railway domain or arbitrary browser origin is accepted. Uvicorn ignores
Forwarded/X-Forwarded headers (`proxy_headers=False`); canonical HTTPS identity
comes from configuration even though the container sees proxy HTTP.

Defaults: 64 KiB request body, 16 KiB/100 headers, 8 concurrent calls, 65-second
whole-request timeout, 256 KiB buffered response. The single-instance global
request bucket refills at 120/minute with a burst of 30; the one authorized
principal's tool bucket refills at 60/minute with a burst of 15. 429 returns
Retry-After: 1. Metadata shares the request bucket; health is exempt. There is
no unbounded per-IP or per-subject dictionary. This protects model loops and
limits signing-key refresh abuse, but is not a distributed DDoS service.

Allowlisted logs emit OAuth validation success/failure, authorization denied,
MCP initialization, tool started/completed, and classified Canvas upstream
failures with random request ids and known tool names only. They do not emit
JWTs, PATs, subjects, arguments, coursework, signed URLs, resolved addresses,
or exception strings. SDK private input echoes are suppressed. Synthetic
secret tests exercise responses, metadata, health and late-added log handlers.

Normal egress is Auth0 discovery/JWKS and the configured Canvas API only. Cloud
Canvas DNS must resolve entirely to public/global addresses; never inherit the
local `192.168.4.200` pin. TLS validates the original hostname and the network
backend dials vetted IPs. A provider-side public-DNS change is not permission
to weaken that policy.

## Live evidence still required

1. Resolve the Railway workspace's free-plan eligibility without payments;
   provision the one-instance service/domain and runtime variables. Build the
   final source snapshot; record its deployment id/image digest privately.
2. Inspect the exact running image: UID/GID 10001, no secret build args/files,
   no test/download/generated diagnostic directories, no Windows dependency
   needed by remote imports, `/health` 200 on injected PORT. Build context is
   allowlisted source and pinned packaging; helper CLI modules are not routes.
3. Explicitly run `python -m canvas_mcp.remote_smoke --verify-canvas` **inside
   Railway**. Successful output contains only destination class public_global,
   canvas_connection ok, and courses_accessible true. It does not run at startup
   or on health. Never print course names or PATs in deployment logs.
4. Use the current MCP Inspector Auth flow against final HTTPS `/mcp`: metadata,
   Auth0 discovery, S256 challenge, login, consent, resource-bound code exchange,
   initialization, 14 tools, profile. Keep tokens out of command arguments/logs.
5. Internet unauthenticated `/mcp` must return only a 401 OAuth challenge. Verify
   authenticated initialize/list/profile/courses/upcoming with real Canvas.
   Use a separate safely enrolled Auth0 test user: login may succeed, but server
   must return 403 and its Canvas connection must never be opened. Synthetic
   RSA fixtures cover this locally but do not establish the real-account test.
6. In the current ChatGPT Web developer/custom MCP management UI, create the
   private Canvas Student Remote connection with final `/mcp` URL and complete
   OAuth. Confirm the real scenarios: courses; work due this week; next dated
   assignment explanation using assignment context. Do not publish publicly or
   claim native ChatGPT mobile support.

## Validation and rollback

Final verification actually executed:

| Check | Actual result |
| --- | --- |
| `.venv/Scripts/python -m pytest -q` | 1330 passed, 248.47 seconds; one existing Starlette TestClient/httpx deprecation warning |
| `.venv/Scripts/python -m mypy` | Passed, 71 source files |
| `.venv/Scripts/python -m ruff check src tests` | Passed |
| `.venv/Scripts/python -m ruff format --check src tests` | Passed, 127 files |
| `git diff --check` | Passed; Git CRLF notices only |
| `.venv/Scripts/python -m build` | sdist and wheel built; repeated after final source fixes |
| `pip install --force-reinstall --no-deps dist/canvas_mcp-0.1.0-py3-none-any.whl` | Passed |
| `.venv/Scripts/python -m pip check` | No broken requirements |
| Installed wheel with `python -I` | All modules imported; 71 source/installed Python files byte-identical |
| Installed CLI `--help` checks | All 11 entries passed, including remote smoke |
| Actual installed personal plugin launcher | Initialize passed, tools/list contains 15; seven plugin files byte-identical to unchanged source |
| Linux CPython 3.13 pinned wheel metadata | 29 wheels, full dependency graph including PyJWT crypto extra consistent |
| Public Auth0 discovery/JWKS (no token) | Success; fixed endpoints/issuer, S256 true, issuer identification true, CIMD false |
| `docker version` | Timed out at 20 seconds; engine unresponsive |
| `docker build --tag canvas-student:phase6.2 .` | Attempted, timed out at 45 seconds; only that build process tree terminated; no built image |
| Railway live deployment/domain/image checks | Pending: free-plan/billing blocker |
| Inspector real Auth0 login/consent/token exchange | Pending: no deployed final resource/client grant |
| Real cross-account denial/Canvas calls/ChatGPT scenarios | Pending; synthetic isolation tests passed, no live claim |

An initial full-suite failure caught URL parsing in the MCP adapter, contrary
to the existing dependency-direction rule; parsing moved into typed config and
the final full suite passed. A verification harness initially passed StringIO
as a Windows subprocess stderr descriptor; using a real descriptor fixed the
harness, and the actual installed launcher then passed.

Two independent read-only reviews and final re-reviews completed:

- `remote-oauth-security-reviewer`: no Critical/High findings. Medium Auth0
  dual-audience compatibility finding fixed with the narrow documented allowlist;
  independently re-tested, final 23 OAuth tests passed.
- `deployment-boundary-reviewer`: no Critical/High findings. PAT-maintenance
  wording and incorrect upstream-error counting fixed; final independent
  HTTP/OAuth run passed 65 tests. Added bounded per-request error-code ledger
  never contains private exception text or arguments.

Docker/live/Auth0 login/ChatGPT results remain pending. Local fixtures and
public metadata reads are not proof of a successful deployed OAuth flow.

To disable the remote service, switch to deny and redeploy or stop Railway.
Remove/revoke the Auth0 app grant and remote Canvas PAT as needed. Delete the
Railway service only when explicitly requested. Local Windows credentials,
plugin 0.1.0 files, and stdio configuration are independent and remain usable.
Do not begin Phase 6.3 from this implementation.

## Sources checked on 2026-09-27

- [OpenAI MCP OAuth, profile and registration](https://developers.openai.com/plugins/build/auth)
- [Current MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [Auth0 manual CIMD and plan restriction](https://auth0.com/docs/get-started/auth0-overview/create-applications/register-applications-with-cimd)
- [Auth0 third-party security controls](https://auth0.com/docs/get-started/applications/third-party-applications/security-controls)
- [Auth0 resource support GA](https://auth0.com/blog/auth0-auth-for-mcp-servers-generally-available/)
- [Auth0 API/UserInfo audiences](https://auth0.com/docs/secure/tokens/access-tokens/get-access-tokens)
- [Railway config as code](https://docs.railway.com/config-as-code/reference)
- [Railway health checks](https://docs.railway.com/deployments/healthchecks)
- [Railway public networking](https://docs.railway.com/networking/public-networking)
- [Railway free pricing](https://docs.railway.com/pricing/plans)

The existing MCP SDK 1.30 transport negotiates 2025-11-25. Its supported RFC
9728 helpers are reused; the latest authorization requirements were checked
without replacing the stable stdio/shared tool implementation with SDK 2.
