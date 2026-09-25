# ARCHITECTURE REVIEW SUMMARY

Status: historical architecture review, retained for comparison. The original proposal below predates the implemented runtime, local private-origin opt-in and [ADR-007](decisions/ADR-007-same-origin-download-auth.md). Current network and redirect policy is in [security.md](security.md) and the [secure file guide](../secure-file-layer.md).

## Original architecture proposal (review input, retained for comparison)

Use a single Python 3.11+ package and process with a future stdio MCP adapter. Keep domain records and application orchestration independent of MCP, HTTP, environment variables, and filesystem APIs. Source directories: `domain`, `application`, `ports`, `infrastructure/{canvas,config,files,logging}`, and `mcp`. A future composition root wires concrete adapters. There is no runtime entry point, SDK dependency, HTTP client, server, cache, database, or background worker in this phase.

Dependencies point inward: MCP -> application -> ports + domain; infrastructure -> ports + domain. Configuration and composition are outer concerns. Domain records are immutable dataclasses; ports and application query contracts are Protocol declarations only. No factory hierarchy, dependency injection framework, generic repository, event bus, or service-per-entity abstraction.

Three external ports protect real boundaries: an LMS query provider, a credential source used only by infrastructure, and a controlled artifact store/downloader. The application owns assignment-context and upcoming-work aggregation; the provider exposes bounded provider-neutral reads. The Canvas adapter owns route construction, mappings, pagination, GET retries, and its private HTTP client. Canvas-prefixed public tool names remain a compatibility facade; core IDs are opaque provider identifiers and unsupported provider capabilities are explicit errors. No second provider or speculative write interface is implemented.

The local transport creates a trusted request context with an internal principal ID, connection ID, correlation ID, snapshot time, and deadline. Neither the tool arguments nor retrieved content can select a token or principal. The connection binds the configured origin, credential, and Canvas account; all lookups are scoped to that connection and the authenticated user's own data. A future remote transport must authenticate the caller and bind an authorized connection server-side. No global mutable authentication state. Storage, continuations, caches, and logs must be namespaced by principal plus connection; a numeric Canvas ID is never globally unique.

Input schemas live at the application ingress: validated query records bound IDs, searches, date ranges, pagination, and aggregate work. MCP schemas derive from that contract. Validation does not grant object authorization. The provider verifies course/object membership and uses only the current user's submission and grades. Validation of hostile HTTP responses and filesystem/network invariants remains at those independent trust boundaries. Typed skeletons in this phase do not enforce those checks.

Tools return normalized records, small allowlisted fields, observation time, completeness, safe error categories, and opaque continuations. Free-text fields carry an explicit untrusted-content wrapper. No raw HTTP response, exception, token, signed URL, or arbitrary local path becomes an MCP result. Filenames, titles, descriptions, HTML, announcements, and files are always external data. HTML is converted to bounded inert text before model exposure; metadata labels and sanitization do not solve prompt injection on their own. Agent instructions must prohibit treating retrieved content as authority to run commands, read secrets, or change policy. Downloaded documents are not parsed or executed inside the credential-bearing server.

Use a fixed read operation allowlist and only GET for Canvas business requests, not merely an MCP advisory readOnlyHint. Some reads may use POST in other providers later, so the generic port expresses query semantics while the initial Canvas adapter permits GET only. Server token privileges may exceed the application allowlist; least-privilege Canvas credentials remain desirable. Future commands require separate ports/handlers, scopes, authorization, confirmation bound to an exact immutable action, and audit; no command registrations exist now.

The configured Canvas origin is an administrator-controlled HTTPS origin, not a model argument. Paths are constructed from validated IDs. Pagination links must stay on the same origin and expected API route; bounded traversals have cycle detection. API redirects are rejected. GET retries are centralized at the HTTP adapter (maximum three total attempts within one deadline, jitter and bounded Retry-After), never nested in application aggregates. Authentication/authorization failures and malformed data are not retried. Response size, pages, items, text length, request concurrency, total requests, and aggregate deadlines all have limits. Partial aggregates report exactly which components or course scope are incomplete; missing status is unknown, not not-submitted. Primary authentication failure fails the request, not an empty success.

Downloads accept course/file IDs only; no URL, target path, or destination filename tool arguments. The provider reauthorizes the file and resolves a short-lived private source from Canvas metadata. Only the download infrastructure sees the signed source URL. A separate credential-free HTTP client fetches approved Canvas/CDN HTTPS origins without cookies or Canvas Authorization. API metadata requests use the authenticated API client; files requiring authentication on the content request are initially rejected rather than forwarding a token to an arbitrary endpoint. No wildcard CDN allowlist or implicit trust from a URL appearing in metadata. Every redirect hop is revalidated (maximum three); DNS and connection targets must exclude loopback, private, link-local, multicast, unspecified, and cloud metadata addresses, including IPv6, and protect against rebinding. Private institutional hosts require a future explicit deployment policy; the personal public-Canvas default rejects them.

File infrastructure creates an owner-only session directory under a configured download root outside the repository. It generates random artifact names, uses exclusive no-follow creation, validates resolved containment, and rejects symlink/junction/reparse ancestors. It streams with time and byte limits independent of Content-Length, does not auto-decompress HTTP content, and removes partials on errors. It never uses a remote filename as a path and never overwrites an existing file. Metadata and observed type must agree with permitted formats; executable/active HTML content is rejected, archives are opaque and never expanded, macro formats are rejected, notebooks/code are text data and never run. Aggregate disk/file quotas and expiry protect local resources. The store returns an artifact handle; a trusted local Codex bridge may resolve a handle to an approved read-only local artifact, while remote transport would require authenticated streaming. Tool inputs cannot request arbitrary filesystem reads. Content inspection is a separate least-privilege, credential-free operation with parser resource limits.

Nonsecret deployment settings describe origin, download root, timeout, byte and aggregate budgets, cache TTL (zero/off), and log level. Only outer configuration code may read environment variables. The credential source initially reads a personal token from the environment, but can later be replaced with an OS keychain or OAuth token store. A token wrapper suppresses repr but is never considered a security boundary: serialization, logs, errors, and MCP fields must use allowlists. OAuth refresh and stores are deferred. A safe `.env.example` has an empty token and an invalid example origin; no real `.env`.

Logs go to stderr for stdio MCP, containing request IDs, operation names, counts, duration, and safe codes. They exclude credentials, URLs, headers, raw exceptions, content, user search strings, and personal coursework. Debug mode cannot disable redaction. Raw-payload debugging and network telemetry are disabled for the MVP. Errors have stable application categories and are mapped to fixed safe messages without exception chaining in tool output. All runtime data is ephemeral; downloads have bounded retention and cleanup, cache/persistence are absent, and credentials never enter artifact storage. Future persistence and auditing require deliberate retention and isolation policy.

Future tool contracts cover profile, courses, assignments, assignment context, modules/items, file metadata and downloads, own submission status, own grades, upcoming calendar/workload, and announcements. Assignment context excludes grades by default and never auto-downloads attachments. Workload uses per-user effective deadlines and distinguishes submission state, graded/late/missing flags, date state, and unknowns. Upcoming queries are time bounded with explicit course coverage; undated or overdue/unsubmitted queries need an explicit mode rather than assuming a date filter can answer everything.

Tests are planned at unit (fake ports), provider mapping, MCP contract/error, mocked HTTP integration, and security boundaries. Security cases include malicious content, path traversal/device names/junctions, SSRF/redirects/DNS, overlarge/decompressed bodies, malformed schemas, credential leakage, pagination budgets, and connection isolation. Real Canvas tests remain opt-in and read-only with explicit credentials. Static scaffold checks ensure declarations import and there are no runtime implementations.

## Independent review and architect dispositions

An actual independent subagent, `architecture_critic` (role: architecture-critic), reviewed the original proposal and existing instruction files without editing project files. It reported zero CRITICAL, three HIGH, six MEDIUM and one LOW finding. The architect assessed all ten rather than delegating the design decision to the critic.

### 1. HIGH: Agent instructions lack the proposed trust boundary

Challenged decision: rely on trusted agent instructions while AGENTS calls Canvas authoritative without limiting that authority. Attack: a description or notebook asks for environment secrets, execution or external uploads; the agent treats coursework text as policy. The wrapper cannot repair missing instructions.

Critic recommendation: limit authority to coursework facts and add explicit agent rules, referenced from each skill.

Disposition: **ACCEPT**. Added two short global-policy paragraphs to AGENTS and one pointer in each skill; these are necessary architectural corrections, not replacements of the workflows. Requirements remain factual evidence and do not authorize execution, secret reads, arbitrary networking or communications. Corrected the course-materials future metadata tool name to match the requested contract. See ADR-003 and security trust boundaries.

### 2. HIGH: Credential-free inspection lacks a process boundary

Challenged decision: no-auth download client and “separate inspection” imply a credential-free environment. Attack: a parser inherits the server environment or shares an ambient token with Codex; a malicious document leaks it. Removing headers is not process isolation.

Critic recommendation: define launch/environment ownership, inspector capabilities, OS isolation and residual same-user risk.

Disposition: **ACCEPT**. Token belongs only in the server process environment. A separate inspector receives only approved bytes/grant and limits, uses an explicit credential-free environment, no network or ambient project/home access, and requires sandboxing. Neither environment scrubbing nor an owner-only directory is claimed to defeat a compromised same-account process. Host isolation is a release gate, not an implemented feature. See security and authentication data flow.

### 3. HIGH: Opaque artifact handle has no usable, authorized host handoff

Challenged decision: an unspecified bridge “may” resolve an artifact. Failure: the current host cannot inspect a handle, leading to unsafe arbitrary-path tools or missing coursework functionality.

Critic recommendation: define scope, expiry, unguessability, content identity, allowed operation, cleanup and the exact point at which approved bytes/path cross the boundary.

Disposition: **PARTIALLY ACCEPT**. The gap is valid and now has a concrete local-only path/digest/expiry handoff via `LocalArtifactResolver`, with private registry lookup, permission and scope checks, safe reopen and identity validation. The core remains path-free. I chose the minimal local path bridge rather than adding a resource-streaming subsystem in the local MVP; remote authenticated streaming is an explicit future replacement. A path alone does not constrain an external reader; host isolation/compatibility must be demonstrated before enabling inspection. No generic filesystem read tool was added. See ADR-004 and security's local handoff contract.

### 4. MEDIUM: Opaque continuations do not imply integrity

Challenged decision: opaque namespaced cursors without specified binding. Attack: change query, course, connection or encoded upstream URL to resume an unauthorized/unbounded scan.

Critic recommendation: protect integrity and bind principal/connection, canonical operation/query, version, expiry and authorization; explain live views.

Disposition: **ACCEPT**. MVP uses unguessable server-resolved references in bounded session memory, not raw/Base64 URLs. All relevant scope/query/version/expiry bindings and authorization are rechecked; 10-minute TTL/64-entry cap and invalidation are explicit. A future stateless design needs authenticated integrity. Each continuation is a bounded new request over a live view. Aggregate component cursors are not reused across public tools.

### 5. MEDIUM: Null/empty results conflate absence and failure

Challenged decision: top-level completeness cannot describe every missing component. Failure: failed due/status/rubric fetch becomes no due date/not submitted/no rubric.

Critic recommendation: distinguish present, absent, unavailable, unsupported and unrequested without wrapping every scalar.

Disposition: **ACCEPT**. `Observed<T>` distinguishes available value, known absence, unavailable, not supported and not requested; empty successful collections remain distinct. Required fields remain ordinary normalized fields. Submission flags are independent and nullable when unknown; aggregates carry component warnings and course coverage. This is sufficient without a generic result framework around every field.

### 6. MEDIUM: Independent limits multiply across aggregates

Challenged decision: per-call retries/pages/limits without one accounting owner. Failure: each branch gets a fresh allowance and expands the total workload; wall clock shifts defeat elapsed deadlines.

Critic recommendation: one immutable policy and shared aggregate accounting/deadline, including all attempts, serialized result bytes and process/session caps.

Disposition: **ACCEPT**. Added `BudgetLimits` and a request-owned ledger skeleton to `RequestContext`. All retries, pages, redirects and metadata reads count against that one budget and a monotonic deadline; concurrency is process-wide, artifacts/cursors are session-bound. The records do not implement atomic reservation or timing yet. This is a small necessary contract, not a rate-limit service or scheduling framework.

### 7. MEDIUM: Opaque IDs need provider-specific route rules

Challenged decision: opaque IDs become “validated” path segments without a codec. Attack: separator/percent/query syntax or double decoding changes the route.

Critic recommendation: define Canvas syntax and exactly-once encoding while retaining opaque domain IDs.

Disposition: **ACCEPT**. Shared Canvas ingress codec accepts canonical bounded positive ASCII decimal strings, rejecting bool/numeric coercion, leading zeros, separators and URL syntax; the adapter reuses that codec and fixed routes. Generic domain IDs remain opaque for another provider. This confines Canvas assumptions instead of pushing its syntax into all domain models.

### 8. MEDIUM: Credential replacement can cross identity namespaces

Challenged decision: vague connection-to-“Canvas account” binding. Attack: switch to another user's token and serve old artifacts/cursors in the same namespace.

Critic recommendation: bind provider origin and verified authenticated subject; explicitly invalidate on subject/origin changes.

Disposition: **ACCEPT**. The verified user/subject is distinguished from an institution account. Subject/origin replacement creates a new binding and invalidates artifacts/cursors; same-subject rotation revalidates permissions. No hot-reload assumptions or global auth state. Future remote connection selection is server-authorized ownership, never a schema-only check.

### 9. MEDIUM: HTML conversion can erase needed references

Challenged decision: inert text is the only retained representation. Failure: relevant file links disappear or every surviving link is treated as downloadable.

Critic recommendation: separately extract classified references and resolve authorized first-party IDs without arbitrary fetches.

Disposition: **ACCEPT**. `Assignment.references` contains bounded `MaterialReference` values in addition to inert text. Authorized supported file IDs become attachment metadata; external/inaccessible/unresolved/unsupported references stay unfetched and affect completeness when relevant. No raw URL needs to cross the core boundary. This preserves useful requirements without granting network authority to embedded links.

### 10. LOW: File-format and platform guarantees need feasibility rules

Challenged decision: broad type agreement and no-follow promises obscure Office ZIP signatures, unknown MIME and Windows races. Failure: legitimate files are misclassified or a pre-open check is bypassed.

Critic recommendation: format matrix and platform-specific race-resistant acceptance criteria.

Disposition: **PARTIALLY ACCEPT**. Added a conservative format matrix, generic MIME rule, inert OOXML-container handling and explicit fail-closed platform release gates. Exact system calls, parser selection and archive library APIs are deliberately deferred until implementation/platform tests; encoding them in domain interfaces would couple the core to operating-system details. No macro safety claim is made from a ZIP signature alone.

## Changes made and decisions defended

The design now has explicit agent/process/artifact boundaries, availability states, route ID rules, shared budgets, lifecycle-bound credentials/cursors, and classified material references. Existing instruction files received only small trust-policy/metadata-name corrections. Typed source contains records, exception categories, Protocol methods and reserved module docstrings, not simulated success or runtime controls.

Preserved one package/process, one application query surface, provider query abstraction and three external capability boundaries (LMS, credentials, artifacts). The artifact boundary has separate download and narrow resolver views because MCP needs only the latter for handoff. No cache/clock factory, tenant database, event bus, second provider, server startup, secret loader, network client, OAuth, parser or downloader was built. Remote streaming and per-user distributed controls remain future gates. These choices keep the MVP implementable while preventing transport/credential/provider details from owning coursework orchestration.

## Final critic review

The same independent critic reviewed the revised documents and declarations and returned **PASS for the architecture/declaration phase**, with **no unresolved CRITICAL or HIGH findings**. It explicitly confirmed closure of the agent-instruction, process-isolation and artifact-handoff HIGH findings. This verdict does not authorize enabling credentials, a runtime or document inspection.

It also identified three MEDIUM contract refinements and one LOW terminology issue. The architect addressed all four:

1. **MEDIUM — Lost truncation evidence. ACCEPT.** The provider's plain text/reference records could conceal clipping while the application promised warnings. Added `ExternalText.truncated` and component-level `Observed.truncated`, propagated to fixed `content_truncated` warnings and incomplete results. This avoids a general provider response framework.
2. **MEDIUM — Crash recovery versus ephemeral registry. ACCEPT.** Restart loses access registry entries, so “registered artifacts only” could not justify stale cleanup. Defined a minimal owner-only session lifecycle marker, maintenance/live-session checks, verified generated names and handle-based deletion; unknown entries stay untouched, leftovers count against storage admission, and old access grants never revive. This is temporary artifact housekeeping metadata, not a database or persisted coursework cache. No cleanup code was implemented.
3. **MEDIUM — Cursor memory bounded only by count. ACCEPT.** Count/TTL alone could retain large DTO buffers. Require minimal traversal state only, no raw/full DTO buffers, maximum 4 KiB per cursor and 256 KiB per session including transitive state. Oversize state yields explicit incomplete coverage/narrower-query guidance. Configuration skeleton and tests reflect these ceilings.
4. **LOW — Resource/path terminology. ACCEPT.** Renamed overview references to “local artifact path bridge,” matching the chosen design rather than implying an MCP resource subsystem.

The targeted revisions were returned to the critic for closure confirmation. It confirmed crash recovery, cursor byte limits and bridge terminology closed, and identified one narrow MEDIUM follow-up: rubric rows still crossed the provider port as a bare tuple. **ACCEPT**: changed `LmsQueries.get_rubric` to return `Observed[tuple[RubricCriterion, ...]]` and prohibited silent clipping of unmarked ratings/submission-type tuples; over-budget unmarked collections must fail safely. The critic then explicitly confirmed this correction closed and all review findings addressed: **PASS, with no unresolved CRITICAL/HIGH findings or remaining contract corrections from this review**. Implementation security gates remain open by design, not unresolved architectural findings.

## Scaffold verification

Verified locally with Python 3.13 without network access or bytecode output: all 19 source modules parse, compile in memory and import; annotations resolve for 57 declared classes and their methods; all methods contain declarations only. Inward import checks passed. TOML metadata has no runtime dependency or entry point. All 24 Markdown files, local links and skill frontmatter were checked. No real `.env`, runtime implementation, generated cache, credentials or functional integration tests were created.

The package wheel was not built because setuptools is not installed in the available Python environment. No dependency installation or network lookup was performed; packaging metadata is syntax-checked, not claimed build-tested. Runtime behavior/security tests remain the planned implementation gates in testing.md.

## Remaining MEDIUM/LOW risks and limitations

- **MEDIUM:** An unrestricted host/model can misuse content despite text labels. Isolated host inspection and action authorization require end-to-end validation; the scaffold cannot certify a Codex host sandbox.
- **MEDIUM:** Same-OS-user process compromise, host logs/backups and copies of already-read artifacts are outside server isolation/expiry guarantees. The deployment must not imply otherwise.
- **MEDIUM:** DNS-to-socket enforcement, Windows/POSIX file confinement and document-parser sandboxing require concrete implementation and adversarial tests; fail closed where unsupported.
- **MEDIUM:** Future remote/OAuth, caching, persistence and write capabilities need another security review and user-isolation/approval controls before activation. The local architecture is not a deployable remote security design.
- **LOW:** Strict no-auth/exact-origin content policy can reject legitimate institutional/CDN downloads; operator-reviewed origins and an explicit future credential rule may be needed. No automatic allowlist expansion.
- **LOW:** API visibility and bounded live scans can leave incomplete coursework answers or unsupported module-page/legacy-document content. Coverage/availability must remain explicit; never claim exhaustive results on partial evidence.

These are declared implementation/deployment limits, not unresolved authority to bypass the blueprint's required controls.
