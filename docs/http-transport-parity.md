# Fixed-profile HTTP transport parity

## Current Narxoz compatibility finding

Later live evidence confirmed that `canvas.narxoz.kz` resolves to the RFC1918
address `192.168.4.200`. The default generic SSRF policy rejected that DNS
answer before HTTP. The local personal MVP now uses the exact
`CANVAS_TRUSTED_PRIVATE_IPS=192.168.4.200` policy for the configured Canvas
origin; public DNS answers remain dynamically allowed. The pinned socket, TLS
hostname, SNI and Host rules remain in force. External redirects and
Canvas-derived URLs do not inherit private trust. Run the canonical helper with
`-TrustedPrivateIps '192.168.4.200'` for Profile, Transport and MimeTopology retests. The
earlier 403 evidence below is historical and may be a separate HTTP response;
it cannot be inferred from the DNS rejection. This agent had no Canvas token
for a live post-change request.

## Exact evidence, not an assumed transport defect

The operator reports HTTP 200 from PowerShell for:

- `GET /api/v1/users/self/profile`
- `GET /api/v1/courses?per_page=1`

The production `CanvasHttpClient` uses the module's `PROFILE_PATH` constant:
**`/api/v1/users/self`**. Its safe request identity is
`GET / canvas_origin / /api/v1/users/self` (HTTPS, effective port 443, no query).
The supplied production failure is reported as HTTP 403 / `authorization_error`.
These different resources are not an endpoint-parity experiment. Token creation
is not being investigated. No production endpoint, public error or authentication
policy is changed without same-endpoint evidence.

Source and mocked wire inspection found no Host-to-IP substitution: the configured
logical URL stays intact. `PublicOriginBackend` vets **all** DNS answers and gives
the selected public IP only to its socket backend. HTTPCore derives the request
Host and TLS hostname from the original logical origin, not that socket address.
Combined tests verify original Host + original SNI + vetted-IP dial together.
Neither a first live matrix transition nor the live 403 cause has been established
by local tests; no Narxoz matrix values are invented here.

## Developer-only matrix

```text
python -m canvas_mcp.transport_probe --live
```

Every row is a GET to the **same current production** `/api/v1/users/self`, not
the successful-but-different `/profile` route. No arbitrary URL, path, headers,
token argument or alternate account is accepted. All modes use the existing
environment configuration, scoped credential provider and composition. There is
no profile/course preflight, because the failing profile request is the subject
of the diagnostic. No course, file, MIME, registry or MCP operation runs.

| Mode | HTTP implementation / socket selection | Header difference |
| --- | --- | --- |
| `baseline_python` | Standard-library `http.client.HTTPSConnection`; normal OS DNS/TLS | Authorization + JSON Accept; stdlib automatically adds Host and `Accept-Encoding: identity` |
| `library_default` | Same installed HTTPCore as production; unchanged default backend | Authorization + JSON Accept; automatic Host |
| `production_headers` | Same default HTTPCore backend | Current production set, including `Accept-Encoding: identity` |
| `production_transport` | `PublicOriginBackend` and its vetted-IP dialing, including the explicit configured-origin private opt-in | Minimal Authorization + JSON Accept; automatic Host |
| `canvas_client` | Actual composed `CanvasConnection.get_profile`, provider, client and original pool/backend | Actual production construction; request bytes regression-compared with stage C |

The adapter around stage E's original pool adds observation and diagnostic
guards, not a replacement HTTP backend. It leaves production status handling,
normalization, scope binding and retry behavior intact. Raw HTTP status is
recorded before the client interprets it. A 200 followed by invalid profile
normalization can therefore show status 200 with `failure=client_rejected`;
this is not a successful production profile operation.

Matrix comparisons are controlled pairs, not a claim that each adjacent row
changes exactly one variable. B/C isolate the additional encoding header; B/D
compare default versus pinned transport with identical minimal headers. C/E have
the same actual request-header bytes, with production transport/provider behavior
in E. A/B can also differ in automatic encoding headers, TLS trust configuration,
resolver/connection selection and library behavior; a status change alone does
not identify which one caused it.

Normal output is five fixed labels plus safe numeric statuses. `not_received`
means no response status was observed; it is **not** a synthetic 403. `--details`
adds the fixed method/origin-class/path-template line and one fixed-schema JSON
record per row. It contains only status, equality/presence booleans, fixed
transport/protocol/header classes and fixed failure/403-category labels. It never
contains hostname/IP, URL/query, token, header value, cookie, profile data, response
body, exception text, proxy value or local user path. `null` means unobserved.
`tls_verified=true` requires completed TLS with original-host verification, not
merely configuring a verifying SSL context. Protocol is the **request** protocol
in all modes; a response status line cannot silently change its meaning.

Default output returns exit 0 only if all rows return 200 without diagnostic or
client rejection; other observations return 1 after safe output. Missing opt-in
returns 2 before config, credentials, network or filesystem operations.

## User-Agent, protocol and proxy controls

Current production and default HTTPCore have **no User-Agent**. The chosen stdlib
`http.client` baseline also has none; it is not mislabeled as a urllib default UA.
`--user-agent-matrix` adds three fixed benign trials on the default HTTPCore
transport, with the same target and minimal headers:

- `ua_python_urllib`: Python-urllib version-style UA.
- `ua_powershell_style`: `PowerShell-compatible CanvasTransportProbe/0.0.0`.
- `ua_application`: honest `canvas-mcp/0.0.0`.

The PowerShell-style value is an explicit comparison string, **not** a claim to
reproduce the operator's unknown PowerShell version/actual UA. None impersonates
a browser; no permanent UA change or WAF workaround has been adopted. The three
rows can be compared against the no-UA `library_default` row.

All current probe implementations and production offer/use HTTP/1.1; production
does not enable HTTP/2. An unexpected H2 request in the observation path rejects
rather than masquerading as HTTP/1.1. The successful PowerShell response's protocol
was not supplied; 200 alone cannot establish its version. No protocol is globally
disabled or changed by this diagnostic.

Neither `http.client` nor these directly constructed HTTPCore pools uses
environment/system application proxies, cookies, netrc or automatic redirects.
The diagnostic does not enable them. `proxy_used=false` means no application proxy
was configured; it cannot rule out transparent gateways/WAFs. PowerShell may use
different proxy policy, but its actual proxy use is unobserved. Proxy URLs and
credentials are never read into output. Tests populate a synthetic HTTPS_PROXY
and verify no proxy target is contacted.

## Security boundary and bounded evidence

**Production networking is unchanged.** Production still uses exact HTTPS origin,
public-DNS vetting, vetted-IP dialing and original-host TLS verification.

Only explicit local default-DNS diagnostic stages depart from pinning. They do a
public-DNS preflight, then verify the **actual connected peer** is public and on
443 before sending HTTP credentials. HTTPCore's observation checks peer before
TLS, original TLS hostname/context at handshake, completed TLS, and actual
generated Host/method/scheme/port/target/bearer before sending headers. The stdlib
baseline checks peer and verified TLS after its standard connection handshake,
before any HTTP header. Wrong/duplicate Host, wrong SNI, insecure TLS, private
peer, cookies, proxy auth, another target or an SNI override rejects. Rejected
opening streams are explicitly closed. Failed handshakes are not verified TLS.

This is deliberately **not a claim of production-equivalent pre-connect SSRF
protection** for default-DNS modes: a DNS race can cause an unauthenticated TCP
connection (and for stdlib, TLS handshake) to a disallowed peer before rejection.
No bearer is sent to that peer. This bounded diagnostic-only departure is needed
to compare normal OS resolver behavior; it is not a fallback in production and
must only be used with the trusted configured institution origin. Do not remove
production DNS protections to make a probe pass.

Modes are sequential with at most 20 seconds for each request stage (or a smaller
configured request timeout), plus bounded pool/connection cleanup. The blocking
stdlib stage runs in a killable isolated Python child: timeout kills/reaps it,
including a stalled system resolver. Credentials are inherited only through the
existing process environment, never argv or a file. Child stdout is validated
against a bounded fixed schema. It reports headers before optional body analysis
so an observed 403 survives a body-read timeout. Stage E retains normal retries
(at most 3); other rows attempt once. There are up to 7 HTTP attempts for the five
rows, or 10 with all UA trials. No HTTP redirect is followed, even on Canvas.

Only a 403 receives bounded inert body classification. At most 8192 bytes plus
one overflow sentinel are retained by the classifier; ordinary transport buffers
can receive more. Oversized, malformed, compressed or failed-read evidence is
unknown. Nothing is decompressed, rendered, executed, persisted or exposed. Only
these labels can leave the classifier:

| Label | Bounded evidence hint, not proof |
| --- | --- |
| `canvas_json_error` | JSON error-field shape compatible with Canvas-style errors |
| `html_gateway_error` | HTML with fixed gateway markers |
| `html_waf_error` | HTML with fixed WAF markers |
| `unknown` | No supported unambiguous local signature / unusable evidence |

Any responder can imitate these shapes/markers. Classification **cannot establish
provenance** or permissions. Raw 403 remains separate from these hints and from
production `authorization_error`; no public error mapping is changed. A–D do not
read successful response bodies. E performs its normal bounded profile parsing
without printing it. Sensitive HTTP logging suppression covers request, trace,
body analysis and cleanup; stdlib HTTP debug output is forced off.
An explicit `SSLKEYLOGFILE` is rejected before SSL context creation, so a
non-isolated invocation cannot silently enable TLS-secret logging.

## Canonical secure local command

Use a dedicated, trusted, non-transcribed PowerShell terminal and the current
installed wheel. This extends the existing helper with the same BSTR credential
handoff/cleanup. Transport mode runs offline presence checking but intentionally
does **not** run the failing live profile preflight or course/file operations.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode Transport -Live -UserAgentMatrix
```

This runs all five required stages plus the three UA trials with safe details.
Omit `-UserAgentMatrix` for just the five stages. Only origin and hidden token are
prompted; no CDN origin is needed, and no runtime directory is created. Do not
share credentials or raw responses. Share only the fixed rows from this command.
Do not resume MIME/download work unless the production `canvas_client` profile
row is 200 with `failure=none`.

## Review and verification

The independent read-only `http-transport-parity-reviewer` found no concrete
CRITICAL/HIGH production Host/SNI or credential defect. It identified three
diagnostic issues, all corrected: rejected-stream cleanup, premature TLS-success
reporting, and inconsistent request/response protocol labels. Regressions also
cover wrong Host with correct SNI, DNS rebinding/private peers, insecure/missing
TLS, production-header parity, redaction, no redirect/proxy behavior, fixed target,
403 evidence failures and child timeout/schema handling. The final read-only
re-review found **no unresolved CRITICAL/HIGH findings**. The reviewer independently
ran 76 transport tests before the last keylogging coverage update, then all 35
updated security tests; no live requests or credentials were used.

Executed verification on 2026-09-25:

- Full regression suite: **1042 passed in 247.77 seconds**, including **79 new
  tests** relative to the credential-parity baseline.
- Ruff passed; formatting: **99 files already formatted**.
- Mypy: **55 source files**, no issues.
- Wheel/source distribution build, local wheel reinstall and `pip check` passed.
- **55 isolated imports** passed without network; the complete installed Python
  file set and all source hashes matched the workspace.
- **5 installed CLI help** checks, **6 transport opt-in/combination gates**, and
  **2 installed missing-config/worker safe-failure** checks passed.

At the time of that earlier verification, a presence-only environment check found neither
`CANVAS_BASE_URL` nor `CANVAS_ACCESS_TOKEN` available to the agent. **No live Narxoz
request was made**. All matrix statuses, Host/SNI/bearer checks and rejection
tests above were synthetic verification. The subsequent RFC1918 DNS finding and
configured-origin change are summarized at the top of this page. No MCP work
was performed.
