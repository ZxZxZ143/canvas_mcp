# Configuration contract

> This document began as a Phase 3 configuration contract. The current personal
> plugin adds a [Windows Credential Manager provider](../personal-plugin.md) and
> local stdio MCP launch; historical statements about missing credential storage
> below apply only to the earlier environment-token setup.

Phase 3 implementation detail: [secure file layer](../secure-file-layer.md). Download storage is lazy (blank directory permitted for metadata-only calls), Windows-only and private, outside the project. File deadlines/quotas/recovery are now enforced, not dormant. Capability TTL is 24h with lazy/explicit/session physical cleanup; no background timer. Concurrency is shared API/download per one supported local runtime. Original future-policy wording below is historical where qualified here.

`infrastructure/config/schema.py` defines nonsecret typed settings. `environment.py` validates configuration before composition; its separate scoped credential source snapshots the token. The application receives policy/context, not an environment reader. No `.env` or automatic dotenv loading exists. See the foundation and secure-file guides for setup and smoke commands.

| Environment input | Typed field / boundary | Rule |
| --- | --- | --- |
| `CANVAS_BASE_URL` | `canvas_origin` | Required normalized exact HTTPS origin on port 443 (DNS hostname or public IP literal); reject userinfo/path/query/fragment and private IP literals. Public DNS is the default. |
| `CANVAS_TRUSTED_PRIVATE_IPS` | `trusted_private_ips` | Local operator-controlled comma-separated exact RFC1918/ULA IP literals, maximum 16. Only these private DNS answers may serve the configured Canvas hostname; every other DNS answer must be public/global. Mixed answers containing any untrusted address fail closed. |
| `CANVAS_ALLOW_PRIVATE_ORIGIN` | Deprecated compatibility flag | Exactly `true` or `false`. `true` requires nonempty `CANVAS_TRUSTED_PRIVATE_IPS`; it never grants broad private access. The exact IP list works without this flag. |
| `CANVAS_ACCESS_TOKEN` | CredentialSource only | Required nonempty secret at runtime; never a DeploymentSettings field, CLI argument, log or DTO |
| `DOWNLOAD_DIRECTORY` | `download_directory: Path or None` | Optional for metadata; required for download, dedicated local-drive private root outside repository with native confinement checks |
| `MAX_DOWNLOAD_BYTES` | `max_download_bytes` | Default 26,214,400 (25 MiB), positive and <= operator disk quota |
| `DOWNLOAD_TIMEOUT` | `download_timeout_seconds` | Default 120s, finite positive <=300; metadata + queue + full stream |
| `MAX_DOWNLOAD_REDIRECTS` | `max_redirects` | Default 3, integer 0..3; never automatic unrestricted following |
| `REQUEST_TIMEOUT` | `request_timeout_seconds` | Default 20 seconds, finite positive <= aggregate timeout; includes connect/read/total |
| `CACHE_TTL` | `cache_ttl_seconds` | Exactly 0 in MVP; positive values rejected as unsupported, not silently ignored |
| `LOG_LEVEL` | `log_level` | DEBUG/INFO/WARNING/ERROR; default INFO; debug never enables payload logging |
| `CANVAS_DOWNLOAD_ORIGINS` | `download_origins` | Empty or comma-separated exact approved HTTPS origins; no wildcard, path or credentials |

The `.env.example` uses an invalid example domain and empty token; it cannot establish a connection. Empty download path permits metadata-only calls but rejects downloads. Never paste real credentials into tracked files or chat. Ignore rules are not a credential scanner or secret store.

Remaining defaults in `DeploymentSettings` are operator policy, not arbitrary MCP arguments. Connection/file adapters enforce their request/page/cursor/artifact limits. Later loaders may expose validated overrides, never permissive fallbacks on validation failure.

| Policy | Default and accounting |
| --- | --- |
| Aggregate deadline | 60 seconds measured by monotonic time; all child calls share it |
| GET attempts | 3 total per operation including first; all consume aggregate attempts |
| Content redirects | 3 maximum; each request/hop consumes aggregate attempts; API redirects prohibited |
| HTTP API body | 2 MiB per response, including a decoded limit if decompression is ever allowed |
| Serialized tool result | 128 KiB total including data, wrappers, warnings and local artifact metadata |
| External text | 16,000 characters per long field, additionally limited by total output |
| Page size / page count | 25 default, max 100 items/page; max 20 total pages across one aggregate |
| Aggregate network requests | 100 HTTP attempts total, retries and redirects included |
| Concurrency | Max 4 active outgoing requests per server process, shared across tools |
| Courses / date range | Max 50 requested/discovered courses; max 90 days for a date window |
| Artifacts | Max 100 artifacts and 250 MiB reserved/actual bytes per session, including partials |
| Artifact retention | 24-hour capability TTL; physical cleanup on expired access, explicit cleanup, session end or verified nonlive-session recovery at next storage use |
| Cursor state | 10-minute TTL, max 64 entries, 4 KiB/entry and 256 KiB/session including referenced state; minimal traversal data only, invalidated on subject change/restart |

Maxima must be finite, positive and coherent (per-file <= quota, cursor size <= session total). `BudgetLimits` carries policy; adapters maintain accounting outside domain dataclasses. The supported MVP is one connection runtime per process, with an exclusive storage-root lease. Before admitting downloads, bounded recovery removes only verified owned nonlive artifacts or fails closed. Future remote service requires process-wide quotas and per-principal admission before concurrent users are exposed.

No OS keychain, encrypted store, token refresh or OAuth flow is implemented. The credential interface isolates that later change. Credential replacement must verify the same subject before preserving a connection; a different subject/origin invalidates its namespace as described in the overview. Changing the environment while a process is running is not treated as a supported hot-reload mechanism.
