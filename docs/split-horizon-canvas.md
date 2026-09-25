# Split-horizon Canvas network policy

The logical Narxoz service is always `https://canvas.narxoz.kz`. DNS may resolve it to the known internal `192.168.4.200` on the institution network or to a globally routable address elsewhere. Configure the hostname once; never replace it with an IP literal or pin a changing public IP.

For the current personal installation:

```text
CANVAS_BASE_URL=https://canvas.narxoz.kz
CANVAS_TRUSTED_PRIVATE_IPS=192.168.4.200
```

`CANVAS_TRUSTED_PRIVATE_IPS` is an operator-controlled, comma-separated list of exact RFC1918 IPv4 or IPv6 ULA literals (maximum 16). It accepts no CIDR ranges, hostnames, public addresses, loopback, link-local, metadata, multicast, unspecified, mapped IPv6, or zone-scoped literals. Public/global DNS answers pass the ordinary address policy without a static allowlist. Private DNS answers pass only if they exactly match this configured set. **Every** answer is checked before any connection attempt: a public answer mixed with an untrusted private answer rejects the entire resolution. DNS is vetted at the actual connect boundary, then a vetted IP literal is dialed without re-resolving the hostname. TLS SNI, certificate verification, and HTTP Host retain `canvas.narxoz.kz`.

The legacy `CANVAS_ALLOW_PRIVATE_ORIGIN=true` remains accepted only if `CANVAS_TRUSTED_PRIVATE_IPS` is also nonempty; the boolean alone is a configuration error. The exact list works without the legacy flag. Migrate existing personal settings by adding the exact known private IP, then remove the legacy flag. No arbitrary RFC1918/ULA range is authorized by the flag.

The configured-origin policy applies to the Canvas API and to production capability downloads on that exact origin. An API-issued `public_url` on that exact origin may use the same trusted private destination, but the capability request is **anonymous**: no Canvas Bearer token or cookies are attached. A capability for an external approved origin uses the public-only downloader. After any chain leaves Canvas, a later return to Canvas remains public-only and anonymous. The production capability downloader is available only for a freshly verified `FileReference` and URL returned by the fixed Canvas Files API, not for arbitrary user, model, or Canvas HTML URLs. A failed capability cannot fall back to the browser route. Real Narxoz parity returned HTTP 200 binary DOCX through this anonymous capability; the installed production `FileSample` then passed download, format, hash and cleanup on 2026-09-25.

Run the local helper with an exact private IP:

```powershell
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode Profile -Live -TrustedPrivateIps '192.168.4.200'
& 'E:\canvas_mcp\scripts\Invoke-CanvasLive.ps1' -Mode PublicUrlParity -Live -TrustedPrivateIps '192.168.4.200'
```

The helper prompts locally for the HTTPS origin and token; neither belongs in source files or chat. Its older `-PrivateCanvasOrigin` switch now prompts for exact trusted private IPs instead of enabling a broad exception. The same settings should work on a public network without changing a public-IP value, but that later network state still needs a live test.

For a future hosted or multi-user plugin, trusted private destinations must be administrator-controlled institution configuration or a private network connector. This local setting is not an end-user or MCP input.
