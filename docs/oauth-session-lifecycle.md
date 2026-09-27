# Phase 6.2.1 — personal OAuth session lifecycle

Status: **COMPLETE**, verified on 2026-09-27. Both short and production access-token
expiry tests succeeded without manual Reconnect, including a new Render container
after idle. Phase 6.4 is not started.

## Cause and ownership

The previous grant had a 900-second access token, no Refresh Token grant and
no offline access. After expiry, ChatGPT required manual Reconnect; browser
Auth0 SSO made that reconnect quick but did not renew the grant automatically.

ChatGPT owns the OAuth client lifecycle and exchanges its refresh token directly
with Auth0. The Render MCP is only a resource server: it validates each incoming
RS256 access token's signature, issuer, exact MCP audience, expiry, `canvas:read`
and the private allowed subject. It stores no ChatGPT access/refresh tokens,
performs no refresh exchange and exposes no refresh or token endpoint. Its only
Auth0 network requests are fixed discovery/JWKS GETs. A fresh server can validate
a new access token without knowing the original authorization exchange.
Canvas PAT authentication and Phase 6.3 file handling are unchanged.

## Final provider configuration

Existing tenant: `dev-5k1vm44z72yha1vm`.
Issuer: `https://dev-5k1vm44z72yha1vm.us.auth0.com/`.
Existing API: **Canvas Student Remote**.
Resource/audience: `https://canvas-student-mcp-z70n.onrender.com/mcp`.
Existing client: **Canvas Student ChatGPT**, Native/public, strict third-party.

| Setting | Saved value |
| --- | --- |
| API signing algorithm | RS256 |
| API application permission | `canvas:read` only |
| Maximum Access Token Lifetime | 900 seconds |
| Implicit / Hybrid Flow Access Token Lifetime | 900 seconds; implicit grant remains unused |
| Allow Offline Access | On |
| Client grants | Authorization Code and Refresh Token |
| Token Endpoint Authentication Method | `none`; no client secret |
| Authorization Code PKCE | S256, required by strict third-party policy |
| Refresh Token Rotation | Enabled |
| Rotation Overlap Period | 5 seconds |
| Refresh Token Idle Lifetime | 604800 seconds (7 days) |
| Refresh Token Maximum Lifetime | 2592000 seconds (30 days) |
| Infinite lifetimes | Disabled |
| Reuse detection | Preserved; no fallback around invalid/reused tokens |
| Callback | `https://chatgpt.com/connector_platform_oauth_redirect` |

These values were saved and checked after dashboard reload. Rotation is available
in the current tenant; no paid upgrade, payment method, Enterprise refresh-token
management endpoint or additional service was introduced. The tenant currently
shows its initial trial. This verifies present availability, not a guarantee
against future provider pricing or entitlement changes.

The absolute limit belongs to the original rotating-token family and is not
extended by rotation. Successful renewals reset inactivity within that absolute
limit. The 5-second overlap tolerates use of the immediately previous token during
a legitimate refresh race. Previous-token reuse outside those 5 seconds, or any
older-token reuse even inside that interval, invalidates the family and requires
new authorization. See Auth0's
[expiration rules](https://auth0.com/docs/secure/tokens/refresh-tokens/configure-refresh-token-expiration),
[rotation overlap](https://auth0.com/docs/secure/tokens/refresh-tokens/configure-refresh-token-rotation)
and [strict third-party controls](https://auth0.com/docs/get-started/applications/third-party-applications/security-controls).

## Actual ChatGPT scope configuration

The installed personal connection is **Canvas Student Remote Persistent**.
The existing custom public client is reused; its secret field is empty and
token endpoint authentication is `none`. In the actual creation UI:

- The default business scope checkbox is `canvas:read`.
- **Base scopes / Базовые области доступа** contains `offline_access`.
- Optional OIDC scopes are unchecked; `openid`, `profile` and `email` were not added.

Auth0 consent displayed both Canvas read access and offline access. A successful
refresh exchange proves a refresh token was issued, without inspecting a token
response. The original installed connection was removed through ChatGPT's
recoverable uninstall flow. Its app definition retains its name, and its OAuth
scope configuration is not editable in the current management UI, so the new
connection uses the Persistent suffix. Only the new connection remains installed.
The old grant cannot gain a refresh token retroactively.

`offline_access` controls renewal between ChatGPT and Auth0; it does not grant a
Canvas capability. Protected-resource metadata, HTTP challenges, tool security
schemes and the server-required scope remain `canvas:read` only. Access tokens
may contain the legitimate extra lifecycle scope, but still need `canvas:read`.
This follows the [MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).
Current [OpenAI plugin OAuth guidance](https://developers.openai.com/plugins/build/auth)
supports Authorization Code with PKCE and expiring/rotating credentials.
Auth0 requires both the requested lifecycle scope and API offline access for
[refresh-token issuance](https://auth0.com/docs/secure/tokens/refresh-tokens/get-refresh-tokens).

## Safe live evidence

All timestamps below are UTC, 2026-09-27. Only event classes, non-secret
configuration, HTTP status and timing were inspected. No access/refresh token,
authorization code, verifier, private subject, PAT or coursework content is
recorded here. Logs cannot prove token contents; Auth0 events establish exchange
success and dashboard configuration establishes rotation policy.
The existing MCP event classes are `oauth_validation_success`,
`oauth_validation_failure` and `oauth_authorization_denied`. An expired access
token is rejected as HTTP 401 `invalid_token`, without printing its claims.
No refresh event is invented in MCP logs: the actual refresh event belongs to Auth0.

### Short 120-second test

Both API lifespan fields were temporarily set to 120 seconds. A new consent/code
exchange succeeded at 12:52:44.488; refresh success at 12:52:47.391 establishes
`refresh_token_issued=yes` with rotation enabled.

The first real `canvas_list_courses` succeeded after refresh at 12:56:05.851.
A second fresh query succeeded after refresh at 13:00:40.862, 275.011 seconds
later. ChatGPT returned five active courses each time without a Reconnect prompt
or manual reconnect between calls. Render recorded the second courses call as
HTTP 200 at 13:00:43, duration 1636 ms, instance `s6cst`.

Immediately after that result, both API lifespan fields were restored to 900
seconds (Auth0 resource-server update at 13:02:11.707). A dashboard reload
confirmed both 900 values and offline access still enabled. One deliberate
Reconnect then established the production-TTL code exchange at 13:04:21.729;
that setup reconnect is outside the expiry test.

### Production expiry, cold recovery and file regression

After the production-TTL reconnect, instance `s6cst` recorded these real tool
calls as HTTP 200:

| Tool | Completion UTC | Server duration |
| --- | --- | --- |
| `canvas_list_courses` | 13:05:23 | 1021 ms |
| `canvas_get_upcoming` | 13:05:56 | 32343 ms |
| `canvas_get_assignment_context` | 13:06:05 | 7960 ms |
| `canvas_get_file_content` | 13:06:16 | 9525 ms |

Courses returned five active entries. Upcoming/context retained
`optional_metadata_unavailable` and incomplete coverage. The same PDF returned
519944 bytes, three processed pages, available text and `truncated=false`.
Its SHA-256 remained
`d617512c7c5a3a1b84a0efb8be5b00b3896c9e979a28ed7d451b8514364da193`.
No manual upload was used. Identical bytes and unchanged extraction code preserve
the previous text result; this does not add OCR or recover image-only diagrams.

Agent requests stopped after this completed flow. A conservative idle baseline
was recorded at 13:08:22.183. No agent health, metadata or tool requests were made
during the interval; dashboard log reads did not contact the MCP endpoint.
The next fresh ChatGPT query was submitted starting at 13:24:40.176, after
16 minutes 17.993 seconds from that conservative baseline, and more than 18
minutes after the last completed tool call.

The first post-idle request succeeded without a Reconnect prompt, manual reconnect
or interactive login. Auth0 recorded **Successful Refresh Token exchange** at
13:24:48.085, 20 minutes 26.356 seconds after the production code exchange;
there was no intervening refresh/code/login event for this client in the inspected
log sequence. The original 900-second access token had therefore expired.
`canvas_list_courses` completed HTTP 200 at 13:25:13 in instance `7fw49`, duration
2470 ms, and ChatGPT returned five active courses.

The runtime instance changed from `s6cst` to `7fw49` without a new deployment or
runtime configuration change, demonstrating recovery in a fresh container after
idle. This is consistent with [Render Free spin-down](https://render.com/docs/free),
though no separate platform sleep/start timestamp was isolated. The total
prompt-to-successful-server-call interval was approximately 33 seconds, including
ChatGPT processing, OAuth renewal, proxy/container recovery and the Canvas call.
The OAuth event timestamp is not an exchange-duration measurement, and 2470 ms
is the application HTTP request duration, not pure Canvas latency. Proxy wake
duration was not separately measurable. The setup `initialize` RPC had returned
200 at 13:04:23 (3 ms); no fresh initialize RPC was observed on the post-idle
request. The stateless HTTP client resumed with a tool call in the new container.

A further real file-content call after refresh/cold recovery again returned the
same SHA-256, size, three processed pages, `content_available=true` and
`truncated=false`, without manual upload. Render recorded this file call as
HTTP 200 at 13:26:44 in `7fw49`, application duration 9842 ms.

### Optional browser SSO logout

At 13:08:21.111, the tenant's `/v2/logout` page and Auth0 Failed Logout event reported
`invalid_request: This endpoint is not available for third-party clients`.
The logout was not confirmed, so browser-session independence is **not verified**.
No client ownership, security policy, logout redirects or tenant settings were
changed to bypass this limitation. Successful refresh exchange remains separately
observable without inferring browser cookie state.

## Reconnect and revocation

Reconnect is required after 7 days without refresh, the original family's 30-day
maximum, grant revocation, genuine reuse detection or another provider/client
authorization failure. A transient Render wake delay is a separate availability
condition and does not itself imply OAuth expiry or justify wider permissions.

To revoke renewal through Auth0 Dashboard: **User Management → Users → select
the enrolled user → Authorized Applications → Canvas Student ChatGPT → Revoke**.
This is Auth0's documented grant-revocation procedure; it does not require reading
the refresh token or Enterprise token-management APIs. The live grant was not
revoked during this verification. See [Auth0 revocation](https://auth0.com/docs/secure/tokens/refresh-tokens/revoke-refresh-tokens).

An already issued self-contained JWT can still pass this stateless MCP's checks
until its expiry, at most the configured 900 seconds after issuance. Grant
revocation is not an instantaneous JWT denylist. For immediate service denial,
set `MCP_HTTP_AUTH_MODE=deny` and deploy that configuration, or suspend the Render
service. Changing/removing the allowed subject also fails closed after the
configuration takes effect. These are operator actions, not custom MCP endpoints.
Revoking ChatGPT access does not require rotating the independent Canvas PAT.

## Verification and review

Added synthetic signed-access-token tests cover initial/renewed acceptance,
expired initial rejection, required scope, subject, exact resource, issuer,
opaque refresh-token-shaped Bearer rejection, restart without session state,
no refresh endpoints and nearly simultaneous initial/renewed requests one second
before the initial expiry. The concurrency test releases two threads through a
barrier; it simulates resulting access tokens, not ChatGPT's refresh exchange.
Only fixed Auth0 discovery/JWKS GETs are possible in these fixtures.

- Full pytest: 1403 passed, 35 platform-specific tests skipped on Windows,
  two existing dependency/fixture deprecation warnings.
- Final focused OAuth tests after the concurrency fixture improvement: 38 passed.
- Mypy: 81 source files passed. Ruff check and format check passed (204 files).
- Source distribution and wheel built and wheel reinstalled. All 81 installed
  source files match, 80 isolated imports and 11 CLI help paths passed;
  local/HTTP surfaces each contain 15 tools. Both dependency checks passed.
- Real installed local stdio: initialize, 15-tool listing, profile and courses passed.
- Independent read-only `oauth-refresh-security-reviewer`: no Critical/High
  source/test/documentation findings. Its suggestion to make near-expiry concurrency deterministic
  was addressed with a frozen clock and barrier and independently re-reviewed
  (38 passed). Final production live-evidence review cleared; latency,
  initialization and optional SSO-logout limitations were retained explicitly.

Runtime source is unchanged from the reviewed Phase 6.3 deployment
`d996d8c050ddb37ece480c2adace0643c615bea8`; this phase requires no code redeployment.
The running Render deployment remains `dep-dasg0rbncjis73b84iug`.
