# Testing architecture and implementation gates

Phase 2 adds executable academic mapping, aggregate, pagination, time/status, adversarial-content and safe smoke tests; see [the academic guide](../academic-read-layer.md). Both live commands remain explicit `--live` opt-ins outside pytest. References below to wider coursework tests as planned are retained blueprint text; file/MCP gates are still future work.

The profile/course slice now has executable configuration, HTTP, mapping, application, composition and security tests; see [run commands](../foundation.md) and [review results](../foundation-review.md). The wider coursework/MCP/file tests below remain planned. Normal tests use mock network streams and no credentials; real Canvas reads require the separate explicit `--live` smoke command. Do not confuse declaration checks with verification of future security controls.

## Test layers

| Layer | Isolation / fixtures | Required observations |
| --- | --- | --- |
| Unit | In-memory fake `LmsQueries` and `CourseArtifacts`, supplied observation time/budget, no Canvas or disk | Assignment-context composition, no automatic downloads/grades, unknown vs known-absent, submitted/late/missing independence, partial coverage, cancellation, shared budgets |
| Contract | Synthetic or irreversibly redacted Canvas-shaped JSON, no real identities/tokens/signed URLs | Map profile, courses, assignments, own due overrides, rubric, module refs, files, own submissions/grades, calendar/announcements into normalized models; malformed data fails safely |
| MCP | Future SDK test transport and schema/result assertions, no live account | Required args/types/ranges, unknown-key rejection, identity not caller-selected, strict output projection, all tool contracts, conservative download side-effect hint, safe known/unknown errors |
| Security | Adversarial strings/URLs/path fixtures and sandboxed temporary roots; stubbed DNS/HTTP | Threat controls below, tested at the enforcing boundary |
| Integration | Future local service composition with injected stub HTTP transport and isolated artifact root | Tool -> use case -> provider mapping -> result; pagination, retries, redirects, scopes, partials and local artifact handoff |

Fakes implement real contract behaviors and explicit failures for a test; production skeletons must never return fabricated success. Test fixtures must not rely on actual accounts, course exports, or secrets. No blanket disabling of TLS/outbound controls for tests: inject a stub resolver/transport and run a separate controlled socket test when implementing DNS/connection pinning. Any test-only bypass must be unavailable to runtime composition.

## Security cases required before runtime use

- **Secrets:** sentinel token in credentials and hostile error/response bodies; assert absent from stdout/stderr, structured tool results, exception mapping, debug mode, artifacts, filenames and telemetry. Generic dataclass/exception serialization must be rejected or excluded. Inspect download requests and parser child environments for ambient credentials, cookies, proxy auth and env leakage.
- **Input and authorization:** malformed/zero/negative/huge/bool IDs; percent-encoded separators and URL-like IDs; date/DST bounds, bad IANA zones, excessive page/window/course lists. Valid but unrelated course/file/assignment/module pairs must not authorize access. Staff-capable upstream token must still expose own submissions/grades only.
- **Content/injection:** descriptions, titles, announcements and PDF/notebook contents contain “ignore instructions,” exfiltration URLs and requests to replace AGENTS. Verify safe data projection and that the host workflow performs no secret reads, command execution, writes or unsolicited network calls. Do not claim model compliance alone enforces server permissions.
- **Network:** redirects to private/loopback/link-local/metadata addresses, IPv6 and mapped IPv4, scheme changes, userinfo, unapproved ports/hosts, alternate IP spellings, malicious Link headers and query parameters, DNS changes between validation and connection, environmental proxies/netrc. Same-origin/API redirects and content authentication rules must be exercised.
- **Storage:** traversal, absolute/UNC/drive paths, ADS, NUL, Windows device names, duplicate names, prefix-confusion roots, symlink/junction/reparse ancestors and mid-open swaps. Race tests are platform-specific. Confirm no overwrite, no out-of-root creation/deletion, safe reopen and cleanup after root replacement.
- **Resources/formats:** absent/false Content-Length, endless/chunked streams, slow headers/body, byte limit at N-1/N/N+1, encoded content, zip bombs, Office ZIP versus plain archives, macros/active HTML/executable/polyglot/unknown types, per-file plus cumulative quotas, parser expansion/time/memory/network limits. Verify no automatic extraction or execution.
- **Capabilities:** guessed/tampered/expired/cross-connection artifact and cursor handles, altered query/page limits, restart, subject/origin rebinding, permission revocation before open, content replacement after digest. Bound cursor entries and transitive retained bytes without storing raw DTO pages. Local handoff returns only an authorized generated path; remote mode never does.
- **Crash recovery:** crash before/after marker creation and during streaming/finalization; validate owned-session markers, live leases and stale partials. Unknown marker/files, concurrent sessions, root replacement and quota exhaustion must fail closed without deleting unrelated paths. Repeated crashes must not bypass storage quota, and restart never revives prior artifact grants.
- **Resilience:** every retry/redirect/page/metadata fetch consumes the one aggregate budget, deadline is monotonic, cancellation propagates, concurrency is process-bound, Retry-After cannot exceed remaining time, no retries on 401/403 or malformed bodies. Full serialized response respects its cap and reports truncation honestly.
- **Academic evidence:** null due date vs failed retrieval, no rubric vs unavailable rubric, excused/offline/no-submission assignments, ungraded submitted work, late submitted work, hidden grades, date overrides and partial course scans. Provider text/reference clipping must propagate markers, warnings and incomplete scope. No complete “next deadline” answer when relevant scope was not exhausted.

## Live integration policy

The implemented optional live smoke command requires explicit environment credentials and `--live`, performs only profile/course reads, and never runs in default CI. No credentials in command history, fixtures, reports, logs or snapshots. Future live tests for other capabilities need their own scope review.

## Release gates

Before enabling the local MVP: central argument/result validation; correct Canvas mappings; own-user authorization; redaction/error tests; shared budgets; fixed outbound routes and tested DNS/redirect controls; platform-proven artifact confinement; bounded cleanup; and verified credential-free isolated Codex inspection. If a target host cannot support the bridge/reader boundary, report inspection unsupported. No optimistic fallback to an unrestricted parser.

Before remote/OAuth/multi-user/cache/write phases: update the threat model, review the new inbound identity/token/retention/approval boundary, and add isolation and regression tests. Existing query tests should run unchanged against application use cases with a different transport or provider. Contract conformance tests for a second provider are preferable to generalized infrastructure built in advance.

Current verification runs pytest, configured mypy/Ruff, package build/import checks and AST dependency tests. Imports remain free of environment/network side effects; no MCP handlers or out-of-scope endpoint implementations are allowed. Future schema/transport/file controls still require their respective tests before use.
