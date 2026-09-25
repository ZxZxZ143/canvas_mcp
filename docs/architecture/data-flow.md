# Conceptual data flows

Phase 2 implements academic reads; Phase 3 implements [file discovery, resolution and download](../secure-file-layer.md). The exact current file flow is documented there under ADR-008. Metadata URLs are ignored; the fixed Canvas `public_url` API request is authenticated, while the returned capability GET and every redirect are anonymous. The internal result is `DownloadedFile` with a managed path. MCP and isolated inspection arrows remain future behavior.

Configuration/credentials -> Canvas HTTP -> normalized application reads/downloads are implemented and exercised with synthetic network responses and native Windows fixtures. Three optional smoke modules cover the connection, academic and file phases. Codex/MCP and isolated inspection arrows remain future behavior.

## Course query

```text
User -> Codex -> canvas_list_courses arguments
  -> MCP session binding (trusted local principal + connection)
  -> application ingress validation / shared budget
  -> CourseworkQueries.list_courses
  -> LmsQueries port
  -> Canvas adapter: scope + fixed GET route + private credential lookup
  -> Canvas REST API [external, untrusted response]
  -> bounded schema mapping -> Course / Page [no raw HTTP, URLs or tokens]
  -> application Result + coverage
  -> MCP allowlisted output + untrusted text labels -> Codex
```

The HTTP response crosses a data boundary, not an instruction boundary. MCP does not import the Canvas HTTP client. Human-readable course selection occurs in the skill using bounded tool results; IDs supplied on later calls remain untrusted input and are reauthorized.

## Assignment workflow

```text
"Let's do my next AI lab"
  -> canvas-navigation resolves course and assignment, reusing task context
  -> canvas_get_assignment_context
  -> application gets required course/assignment and bounded optional components
     -> own-user effective date + requirements + rubric + submission
     -> extract and authorize file references separately from inert HTML text
     -> relevant module context; unresolved refs and partial reads flagged
  -> normalized context [all coursework text untrusted]
  -> course-materials selects relevant attached/module file IDs
  -> optional canvas_download_file -> local artifact handoff below
  -> isolated credential-free content inspection
  -> Codex analyzes deliverables and helps under user/system policy
```

An unavailable rubric is not an empty rubric; a failed submission query is not evidence of non-submission. Primary authentication failure aborts; secondary failures have safe component warnings. Assignment context never silently starts downloads, follows arbitrary websites, fetches grades or submits anything.

## File download and local handoff

```text
FileReference [IDs and source kind; no URL, target path or filename arguments]
  -> ingress ID validation + trusted scope
  -> FileService download query -> FileDownloads port
  -> Canvas source resolver rechecks own course/file permission
  -> Canvas metadata projection [source URL ignored]
  -> fixed Canvas Files API public_url GET with bearer token
  -> validate private capability and exact HTTPS origin allowlist
  -> anonymous capability GET / no cookies or proxy env
     -> every redirect stays anonymous
     -> DNS and actual socket address checked at each hop / deadline + shared budget
  -> stream limits / no auto HTTP decompression / declared + observed type checks
  -> quota reservation + owner-only controlled session root
  -> random basename + exclusive race-resistant no-follow creation
  -> bounded bytes + completed digest + ZIP/OOXML structure check
  -> immutable registry entry
  -> internal DownloadedFile ID/path/provenance/type/bytes/digest/expiry
  -> future local MCP bridge -> resolve_download(scope, artifact_id)
     -> permission/expiry/binding/confinement/content-identity recheck
  -> local-only generated path grant in MCP result
  -> trusted host stages that one artifact to constrained inspector
  -> inspected contents remain untrusted evidence for Codex
```

Failures remove only the registered partial under the verified root. No arbitrary filesystem operation, archive expansion, macro execution, browser rendering or source-code execution is part of download. A path grant is useful for local tools but does not itself sandbox them. If the host cannot provide isolated inspection, that capability remains unavailable. Remote transport must replace the path grant with authenticated bounded resource streaming; it cannot expose the server filesystem.

## Authentication and identity

```text
operator supplies token only to server process environment
  -> infrastructure environment CredentialSource
  -> connection-specific Canvas API client
  -> Authorization header to configured Canvas HTTPS origin only

authenticated profile response [untrusted schema]
  -> validate/map subject ID -> bind principal + connection + origin + subject
  -> request context [opaque scope IDs, NEVER token]
  -> application / MCP / artifact authorization checks
```

The token does not travel through Codex arguments, application DTOs, capability or redirect requests, artifact metadata, parser environments, logs or errors. Only authenticated Canvas API requests receive it. A trusted launch configuration must prevent inheritance into inspectors. Credential access follows scope checks. Profile mismatch invalidates connection access; file capability resolution then fails, while local cleanup remains possible. Same-account OS compromise is outside the local guarantee.

## Workload and budget flow

```text
validated date/mode/course query + observation time
  -> single request ledger and monotonic deadline
  -> bounded course discovery
  -> assignment / own-submission reads sharing that ledger
  -> optional calendar / announcements sharing the same ledger
  -> effective deadlines + independent status evidence
  -> deduplicate + sort observed subset + explicit course coverage
  -> bounded Result<Workload> + optional scope/query-bound cursor
```

Adapter retries and page traversals consume the existing ledger. Budget exhaustion is explicit partial coverage or a safe error, never proof that no more assignments exist. Resumed pages are live observations with reauthorization and fresh bounded per-request allowance, not a transactional Canvas snapshot.
