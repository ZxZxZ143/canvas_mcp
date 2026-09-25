# Architecture overview

> Historical architecture blueprint from the earlier implementation phases. The current
> 0.1.0 release includes the [local 15-tool MCP server](../local-mcp.md) and
> [personal Codex plugin](../personal-plugin.md); references below to MCP as future
> work describe the earlier design state.

Current implementation: Phase 3 adds the [secure file layer](../secure-file-layer.md) to Phase 1/2. Application file discovery/resolution/download and Windows managed storage are implemented. A separate [local MIME diagnostic](../mime-diagnostic-and-learning.md) adds bounded inert identification and private scoped aliases; normal downloads remain strict and registry-blind. MCP and general document inspection remain unimplemented. [ADR-006](decisions/ADR-006-secure-file-layer.md) permits authenticated initial Canvas downloads and an internal generated-path descriptor. Request-local authorization reuse is ephemeral, not a persistent cache.

See the [foundation guide](../foundation.md), [academic guide](../academic-read-layer.md) and file guide for the three implemented application phases and optional smoke commands. Their reviews record checks and limitations. MCP, remote hosting and content inspection remain future work; the architecture below governs those later boundaries.

## Boundaries and dependency direction

```text
Codex + trusted AGENTS/skills
           |
      MCP adapter                 input validation / identity / safe projection
           |
    application queries           assignment context / workload orchestration
           |
    ports + domain records        provider-neutral contracts
           ^
  infrastructure adapters         Canvas / credentials / controlled files / logs

composition wires the profile/course slice; MCP wiring is future work
```

| Area | Owns | Must not own |
| --- | --- | --- |
| `domain/` | IDs, normalized records, scope, budgets, errors; internal generated-path descriptor | HTTP, env, MCP, filesystem APIs, Canvas JSON |
| `application/` | Query contracts, central ingress schema rules, aggregates, coverage | Tokens, HTTP routes, env, filesystem APIs |
| `ports/lms.py` | Bounded queries for a scoped LMS connection | Raw response bodies or URLs, write operations |
| `ports/credentials.py` | Infrastructure-only credential source | Application access or model-visible serialization |
| `ports/artifacts.py` | Resolve authorized course/file IDs into artifact metadata | Caller-supplied URL, filename, destination path |
| `infrastructure/canvas/` | Canvas mapping, fixed routes, self-user authorization, source resolution, HTTP resilience | MCP handlers, coursework orchestration |
| `infrastructure/config/` | Deployment settings, future env loader and token source | Business logic or global mutable auth |
| `infrastructure/files/` | Outbound policy, managed storage; separate local bounded MIME identification/registry | General document parsing/rendering, executing, extracting, copying into projects |
| `infrastructure/logging/` | Allowlisted structured stderr events | Content/payload logging |
| `mcp/` | Transport, session scope, schema exposure, safe errors/results, local artifact path bridge | Canvas HTTP/credentials or general filesystem access |
| `composition.py` | Explicit profile/course connection wiring and validation | MCP startup or out-of-scope coursework |

Domain depends on the standard library only. Application imports domain and the LMS/artifact ports. Ports import domain, never application. Infrastructure imports domain/ports; outer composition alone wires concrete adapters into application and MCP. MCP uses the application interface; its local artifact path bridge receives only a scoped artifact resolver, not credentials or Canvas HTTP. That bridge and adapter-private HTTP/source collaborators do not need extra public abstract factories.

One future application service implements `CourseworkQueries`; it is not a factory per tool. The LMS port contains only query capabilities needed for the stated workflows. A new provider implements those normalized queries and may return `unsupported_capability` for optional features. The `canvas_*` names are the initial compatibility facade; a later provider can add a neutral naming facade over the same application workflows. Renaming the internal package is not required for correctness. Provider-specific ID/HTTP validation stays at the adapter ingress and does not constrain all providers to Canvas numeric IDs.

## Functional responsibility

The [tool contracts](mcp-tools.md) map all requested capabilities to typed declarations. Profile identifies the authenticated subject. Course and assignment reads support natural-language resolution. Assignment context combines course, assignment, rubric, own submission, attachment metadata/references and module context; it does not download files or fetch grades. Modules/items describe structure. Files have distinct listing, metadata and controlled download operations. Grades are a separate explicit query. Workload combines assignment dates/status with optional course calendar events and announcements.

`Observed` separates known absence (`available` + null), unknown (`unavailable` + null), unsupported and not-requested values. A successfully empty collection is `available` + an empty collection, not an error. `Page.complete` describes exhaustion of that collection; `Result.complete` describes whether the requested query scope was fully evaluated. A page can be successful but not exhaustive. Every unavailable/truncated component has a fixed-code warning; there is no silent fallback to “not submitted” or “no due date.”

Provider mapping communicates clipping explicitly: `ExternalText.truncated` records text loss and `Observed.truncated` records a clipped optional component such as extracted references or rubric rows. Collection exhaustion remains `Page.complete`. The application carries those markers forward, sets incomplete scope and emits the corresponding fixed warning; it must not infer completeness from a successfully returned object alone.

`LmsQueries.get_rubric` returns the same observed component shape, so a provider can report rubric-row clipping before application aggregation. Bare tuples such as `submission_types` and criterion `ratings` must not be shortened silently: enforce their mapping bounds and raise a safe budget/malformed-data error when an unmarked collection cannot fit. Individual retained text can carry its own truncation marker. Optional-component failure then becomes unavailable with a warning, not a fabricated complete tuple.

Submission presence, graded status, late/missing flags and excused/required state are separate dimensions. A late submission is still submitted; graded alone does not establish submission; “missing” requires provider evidence rather than the existence of a deadline. Effective due dates are for the authenticated learner, including overrides where supported. Unable-to-resolve overrides means an unknown deadline, not a confident course default. Dates are timezone-aware UTC instants, displayed with the requested IANA timezone. The host resolves “this week/tomorrow” into explicit date bounds; the server does not interpret natural language.

## Resilience and work accounting

One trusted request context contains scope, request ID, observation time, and a shared request-owned budget ledger. Policy maxima come from deployment configuration; caller page/date limits can only narrow them. All aggregate branches share the same ledger and monotonic deadline. Each HTTP attempt, including retry, metadata authorization lookup, content request and redirect hop, consumes the total attempt budget. Every fetched page consumes the total page budget. Per-request response limits and the final serialized MCP envelope limit also apply; text limits cannot substitute for a result byte limit.

Default budgets are [specified centrally](configuration.md). The HTTP adapter owns at most three GET attempts total per operation, within the aggregate budget/deadline, with jitter and bounded Retry-After. The application never wraps this in another retry loop. No retry for credentials, authorization, invalid input, malformed payloads, disallowed downloads, or file size rejection. Failures after partial downloads delete partials; no unsafe append/resume. API redirects are rejected; content redirects follow the separate restrictive policy. Cancellation propagates into siblings and streams, releasing handles and quotas.

Process-level concurrency is capped independently of each aggregate; a future remote deployment additionally needs per-principal admission limits. Timeouts cover connect, headers, reads and total elapsed time. Pagination validates same origin, fixed expected route, permitted parameters and cycles; raw Link headers never become model-visible continuations. Retries and pagination must not reset deadlines. Clock time describes coursework; monotonic time enforces elapsed budgets.

Failure of the primary object or authentication fails the tool. Optional rubric/module/calendar failures can return an explicitly incomplete result. Partial authorization denial is only reported after the parent course is authorized; unrelated or invisible objects receive a generic not-found response to avoid disclosing existence. [Error policy](mcp-tools.md#error-contract) controls all tool errors.

## Evolution without new infrastructure now

| Phase | Components that change | Boundaries retained |
| --- | --- | --- |
| 1: local, personal token, stdio, queries | Implement env credentials, Canvas and files adapters, application, local MCP | One package/process; ephemeral data |
| 2: optional remote transport/OAuth/cache | Authenticated transport, token source/refresh coordinator, deliberate cache adapter | Query contracts, scoped context, normalized DTOs |
| 3: multi-user service | Caller auth, connection ownership registry, encrypted token storage, per-user quotas, audits | Server-bound principal/connection on every operation |
| 4: public plugin/multiple institutions | Packaging/discovery, reviewed onboarding/origin policy and consent | No model-selected credential or arbitrary institution URL |
| Later provider | New adapter and optional tool naming facade | Core use cases and normalized records |
| Later commands | Separate command port, registrations and immutable approval/audit contracts | Query methods remain queries |

Remote deployment is not a switch on the local listener: it requires inbound authentication, Origin checks where applicable, transport audience validation, per-user connection authorization, resource-access isolation, and an OAuth/security review. Canvas OAuth authorizes upstream Canvas access; it does not authenticate callers to the MCP service. OAuth redirects, CSRF/state/PKCE, refresh-token concurrency, token encryption and issuer/audience boundaries remain future design work. No local port listens now.

MVP identity is a trusted OS-user session, not a tenant database. A connection binds provider origin and verified authenticated user/subject. Token changes require subject verification; same-subject rotation can preserve binding, but subject/origin changes invalidate the session and all artifacts/cursors and create a new connection. Never retain a namespace merely because an environment variable has the same name. Future cache keys include principal, connection, provider, subject/binding version and query; authorization is rechecked before serving artifacts or cached data.

Four narrow external ports cover LMS reads, credentials, artifacts and the optional local MIME registry. No cache, clock abstraction, database, event bus, DI framework, worker, tenant schema or Canvas write interface is created. Time is supplied in request context; caches are disabled. See [the architecture debate](review-summary.md) and [decisions](decisions/) for the original reasoning and the [MIME diagnostic contract](../mime-diagnostic-and-learning.md) for the explicit local-state addition.

## Architecture-phase repository layout

This preserved blueprint predates the connection implementation; its added modules and checks are listed in the foundation guide and task report.

```text
canvas_mcp/
├── AGENTS.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── skills/
│   ├── canvas-navigation/SKILL.md
│   ├── assignment-workflow/SKILL.md
│   ├── course-materials/SKILL.md
│   └── study-overview/SKILL.md
├── docs/architecture/
│   ├── overview.md
│   ├── security.md
│   ├── threat-model.md
│   ├── mcp-tools.md
│   ├── data-flow.md
│   ├── configuration.md
│   ├── testing.md
│   ├── review-summary.md
│   └── decisions/
│       ├── ADR-001-read-only.md
│       ├── ADR-002-provider-boundary.md
│       ├── ADR-003-untrusted-content.md
│       ├── ADR-004-controlled-artifacts.md
│       └── ADR-005-credentials-and-scope.md
├── src/canvas_mcp/
│   ├── __init__.py
│   ├── composition.py
│   ├── domain/{__init__.py,models.py,errors.py}
│   ├── application/{__init__.py,contracts.py,queries.py}
│   ├── ports/{__init__.py,lms.py,credentials.py,artifacts.py}
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   ├── canvas/__init__.py
│   │   ├── config/{__init__.py,schema.py}
│   │   ├── files/__init__.py
│   │   └── logging/__init__.py
│   └── mcp/__init__.py
└── tests/
    ├── README.md
    ├── unit/README.md
    ├── contract/README.md
    ├── mcp/README.md
    ├── security/README.md
    └── integration/README.md
```
