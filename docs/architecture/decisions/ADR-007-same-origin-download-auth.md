# ADR-007: Canvas download authorization across exact-origin redirects

Status: implemented for the local personal MVP. This supersedes ADR-006's
hop-zero-only authorization rule; its ID-only ingress, storage and file-validation
rules remain in force.

Live Narxoz topology showed three responses on the exact configured Canvas origin:
302 with auth, 302 without auth, then 200 HTML without auth. The final HTML was
correctly rejected as a DOCX mismatch. The observed chain supports preserving
Canvas bearer authorization while the redirect chain stays on that exact origin;
it does not prove the post-change body is a valid DOCX.

The download client begins in `TRUSTED_CANVAS_CHAIN`. It constructs each GET and
its headers afresh. On an exact normalized configured Canvas origin, the bearer
token is included and the trusted state persists. A redirect to an approved
external origin changes state permanently to `EXTERNAL_CHAIN`; that request and
every later request are anonymous, including a return to Canvas. No cookie,
Referer, response header or ambient credential is forwarded.

Exact origin means HTTPS scheme, canonical hostname and effective port 443.
Subdomains and IP literals that differ from the configured Canvas origin,
non-443 ports, downgrade, userinfo and encoded/Unicode host tricks do not gain
Canvas authorization. Redirect targets still pass the
existing allowlist, loop, count, token-reflection and SSRF checks.

When exact `CANVAS_TRUSTED_PRIVATE_IPS` are configured, requests while in the
trusted Canvas chain use a configured-origin pool that permits only those
private IPs and global addresses. Anonymous API-issued same-origin capabilities
use the same destination policy without Authorization. Once either chain becomes
external, it uses the separate public-only pool permanently.
Original-host TLS SNI, certificate verification and HTTP Host remain mandatory.
The exact private-IP policy is described in the split-horizon guide.

The final body still passes the existing extension, HTTP MIME, unsafe-prefix,
size and bounded format checks. HTML remains rejected. This decision adds no
MCP surface, general URL fetch, MIME exception or Canvas write operation.
