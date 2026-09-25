# Redirect/auth diagnosis after the Narxoz HTML result

This page records the earlier diagnostic design and its then-current policy.
Later live topology showed a Canvas → Canvas → Canvas chain with bearer auth
only on hop zero and final HTML. The exact-origin credential state described in
[ADR-007](architecture/decisions/ADR-007-same-origin-download-auth.md) is now
implemented; the historical statements below about anonymous same-origin hops
are superseded. A post-change live body validation has not yet been observed.

## Evidence and decision boundary

The operator's real report states: expected DOCX; Canvas MIME
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`; final
HTTP MIME `text/html`; unsafe HTML-like prefix; result `UNSAFE`. Quarantine was
erased and no mapping was learned. This is evidence of wrong content, not a benign
MIME alias. Do not whitelist HTML, learn this mapping or persist/review the page.

That report does **not** provide redirect origin topology. It cannot establish
why HTML was returned, whether it was a login/error page, or whether missing
authorization caused it. No origins, paths or HTML contents are inferred here.
The new trace is designed to obtain the missing evidence without another body
download. No authorization, origin, MIME acceptance or credential-forwarding
policy is changed in this update. No MCP is implemented.

## Production behavior when this diagnostic was introduced (superseded)

`CanvasDownloadClient._stream()` constructs the initial URL from the canonical
configured Canvas origin and freshly authorized file/source identity. It creates
fresh `Accept`/`Accept-Encoding: identity` headers per request. Its authorization
condition is still exactly **`redirects == 0`**. There is no cookie jar, automatic
redirect, proxy/netrc authentication, cookie forwarding or Referer forwarding.

For 301/302/303/307/308, `redirect_target()` resolves relative references, validates
the exact normalized HTTPS origin against Canvas plus operator-approved external
origins, rejects unsafe locator syntax/reflected credentials, and enforces loops
and the configured cap (at most three redirects/four requests). The next iteration
is always anonymous, even for a same-origin redirect or a later return to Canvas.
Only hop-zero 401 can invalidate the Canvas credential; later 401 is an anonymous
download rejection. This distinction must be revisited together with any future
same-origin preservation change.

Origin comparison uses normalized scheme/hostname/effective port, not string
prefixes, domain suffixes, DNS identity or matching IP addresses. The current
deployment accepts HTTPS port 443 only. Case/default `:443` normalize; userinfo,
raw Unicode, percent-encoded authorities, trailing dots, non-443 ports and invalid
origins reject. An ASCII punycode spelling is a distinct exact hostname, not a
lookalike alias. Actual connections vet all DNS answers, dial a vetted public IP
and retain the original TLS hostname/certificate verification. A CNAME/shared IP
does not turn an external origin into Canvas.

## New explicit local trace

```text
python -m canvas_mcp.mime_probe --live --allow-mismatch-diagnostic --redirect-topology
```

The additional flag selects **one metadata-valid candidate**, after the existing
bounded discovery and at-most-three metadata probes. It never proceeds to a second
download candidate, even if the first is rejected or unavailable. This is a fresh
selection, not a claim to identify the exact unnamed file in a previous report.

The shared GET/redirect path is exercised unchanged. Each response appends only:

| Field | Allowed diagnostic value |
| --- | --- |
| `hop_index` | Integer 0–3 |
| `origin_class` | `canvas_origin` or `approved_external_origin` |
| `status` | Integer HTTP status 100–599 |
| `authorization_attached` | Boolean taken from the actual outgoing header dictionary |
| `response_content_type_class` | `html`, `json`, `binary`, `other` |

Content class is an inert, fixed classification of the Content-Type **claim**, not
byte validation. Missing/duplicate/malformed/unrecognized claims classify as other;
the strict downstream header/policy checks remain independent. HTML includes
`text/html`/XHTML, JSON recognizes the known JSON/notebook types, and binary includes
known PDF/ZIP/Office/generic binary claims. Classification never changes acceptance.

The next requested origin is the next record's origin class. Records describe only
responses actually received. A blocked Location, loop, timeout or rejected DNS
target is not falsely listed as contacted; a fixed failure reason accompanies the
available partial trace. Collection occurs before redirect/status/MIME rejection,
so a final 200 HTML, 401, 403, 404 or 500 response can still be diagnosed.

No hostname, URL, path, query, signed locator, Location, cookie, authorization value,
token or body is projected. Trace-only reports also omit raw MIME values, filenames,
course/file/user identity, size and hash. They contain only mode, bounded hops,
fixed result/reason and `state=not_downloaded`. Existing full MIME diagnostics now
also include these safe hop records, without changing their acceptance policy.

Trace mode does **not iterate, inspect, render or persist any response body**,
including redirect bodies. The transport may receive some body bytes in its header
read buffer; this is not application-level body iteration or persistence. No `.part`
file, `QuarantinedFile` or `DownloadedFile` is created. A compatible final header
returns `HEADERS_ONLY`, never validated/downloaded. A conflicting HTML MIME remains
a strict rejection and the command exits 1 **after writing/printing the trace**.
No optional login/error-page content classification is attempted.

The mode does not instantiate/load the MIME-rule registry or call rule lookup,
revalidation or learning. The private runtime boundary still checks existing file
ownership/type/size and exclusively leases the directory for report writing.
`--remember-if-validated` is incompatible with trace mode and rejects before I/O.
Both original live/diagnostic opt-ins remain mandatory. Request budgets/timeouts,
authorization, metadata checks, allowed origins, DNS/TLS/redirect checks, status,
encoding and declared-length policy still apply. Actual-byte/hash/length equality
checks cannot be claimed in a headers-only operation; no bytes are accepted.

The report is generated under `CANVAS_MIME_RUNTIME_DIRECTORY` (default
`C:\canvas_mcp_runtime\diagnostics`) as `mime-report-<random>.json`, using the same
private ACLs, pinned ancestors, exclusive lease, bounded report quota and atomic
non-overwriting report publication. Console output contains only the hop projection
plus existing fixed stages/reasons/counts. Never copy raw headers or HTML into chat.

## Local hidden-token command

Run after installing the current wheel. Create only the parent, not inherited-ACL
managed leaves. Use the already-reviewed CDN origins, not signed URLs or newly
guessed allowlist entries. Do not run in a transcribed/debug-logging shell.

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode MimeTopology -Live
```

Uses the [canonical authentication helper](authentication-parity.md#canonical-live-helper),
including the same hidden-token handoff and a required profile/course preflight.
A failed preflight stops before the selected diagnostic.

Do not add `-RememberIfValidated`. Report quota/unknown files/private-ACL failure
still fail closed; do not delete unfamiliar runtime files or loosen ACLs to make a
trace run. Process environments cannot promise secret-memory zeroization.

## Historical candidate — adopted after the live topology in ADR-007

Origin confinement does not logically require hop-zero-only authorization. A
possible narrower correction, **only after evaluating the observed topology**, is:

```text
canvas_trusted_chain:
  initial exact Canvas request -> auth yes
  exact Canvas redirect       -> auth yes, remain trusted
  approved external redirect  -> auth no, enter anonymous_chain
anonymous_chain:
  every subsequent request   -> auth no, including a return to Canvas
```

| Scenario | Policy at diagnostic introduction | Later adopted in ADR-007 |
| --- | --- | --- |
| Canvas → Canvas | yes, no | yes, yes |
| Canvas → Canvas → Canvas | yes, no, no | yes, yes, yes |
| Canvas → approved external | yes, no | yes, no |
| Canvas → external → Canvas | yes, no, no | yes, no, no |
| Canvas → unapproved origin | initial only; target blocked | same |

At the time, executable tests asserted the first column; the second column was
a candidate. ADR-007 later adopted the second column after the live topology
was observed. The current regression suite now tests that adopted state machine.

An all-Canvas anonymous-follow-up chain ending in HTML would make stripped bearer
authorization a plausible cause, but topology alone still does not prove that the
redirected route accepts bearer auth rather than a session cookie. Before adoption,
review which additional same-origin handlers receive credentials, related 401
invalidation semantics, and a separately authorized controlled verification. An
external hop ending in HTML does not justify sending Canvas credentials to a CDN.

Restoration after any external hop is prohibited in both policies. An external
server's redirect back to Canvas is not fresh authorization. The permanent
anonymous state prevents a third party from redirecting the token to a new Canvas
route. Same-origin preservation would keep exact-origin confinement but expand
credential exposure from the initial vetted route to additional same-origin
handlers/services. Exact-origin equality is necessary, not a blanket argument that
such an expansion is required or risk-free. No HTML whitelist, cookie automation,
arbitrary URL/path tool, new origin or weakened file validation is justified.

## Independent review

The read-only `redirect-auth-reviewer` challenged cross-origin credential flow,
DNS/normalization/IDN/userinfo/port tricks, restoration, same-origin risk expansion
and fail-closed behavior. No justified CRITICAL/HIGH finding was identified in the
reviewed implementation/tests. The reviewer explicitly supports keeping candidate
policy changes gated on actual topology and preserving permanent anonymity after
an external hop. Follow-up refinements: metadata may validate the bounded set before
choosing one trace candidate; response records do not imply a blocked target was
contacted; headers-only does not claim byte validation; transport header buffering
is distinguished from application body access.

## Validation results — 2026-09-24

- Full automated suite: **886 passed in 101.00 seconds**.
- Ruff checks passed; formatting check: **91 files already formatted**.
- Mypy: **53 source files**, no issues.
- Wheel and source distribution build passed; wheel installation and `pip check`
  passed.
- Isolated installed-package verification: **53 modules** imported without network
  access, with installed source hashes matching the workspace; **4 CLI help**
  checks and **5 opt-in/incompatible-mode rejection** checks passed.
- The independent reviewer separately ran the observation unit tests:
  **20 passed**. Final re-review found no unresolved CRITICAL/HIGH issues.

At the time of this diagnostic's automated checks, a presence-only environment check found neither
`CANVAS_BASE_URL` nor `CANVAS_ACCESS_TOKEN` available to the agent process. No live
Narxoz request was made in this update. Use the hidden-token command above for one
headers-only candidate trace. The actual redirect topology and cause of the HTML
response were then unobserved. The later live topology and adopted policy are
summarized at the top; the historical test counts above do not cover ADR-007.
