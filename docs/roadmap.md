# Canvas Student roadmap

Version 0.1.0 completes Phases 1–5: connection foundation, academic reads, secure files, local MCP integration, and personal plugin packaging. Phase 6 below records the implemented personal remote capabilities; later phases remain plans and proceed only when requested.

## Phase 6 — Remote MCP and optional mobile companion

Phase 6.1 prepares a local Streamable HTTP adapter sharing the stable stdio tools, default-deny/development authentication, scoped credential ports, HTTP limits and Docker packaging. See [architecture and boundaries](phase6-remote-mcp.md) and [actual validation](phase6-remote-mcp-validation.md).

Phase 6.2 is complete as a personal read-only beta: real Auth0 token validation, scoped Render runtime secrets, Canvas remote smoke and ChatGPT Web queries were verified. Render Free Web Service replaced Railway to meet the zero-cost constraint; no always-on guarantee is made. Phase 6.3 remote file text is complete. Phase 6.2.1 adds bounded rotating refresh tokens while retaining a 900-second access-token lifetime; current settings and live expiry evidence are in [the OAuth session guide](oauth-session-lifecycle.md). See also [Render setup](phase6-2-render-deployment.md), [remote file limits](phase6-3-remote-files.md) and [architecture and Railway history](phase6-2-remote-personal.md). Phase 6.4 may add a mobile web/PWA companion, but must not begin automatically. No native mobile support is claimed. Keep Canvas read-only throughout Phase 6.

## Phase 7 — Safe Canvas Write Capabilities

Design and add narrowly scoped submission-file upload, assignment submission, and submission comments where justified. Separate read and write capabilities. Every mutation must require explicit Codex approval, an exact preview of the intended change, fresh authorization, and an auditable result. No silent Canvas writes.

## Phase 8 — Public / Multi-user Plugin

Evaluate a transition from local stdio MCP to remote MCP, personal access tokens to OAuth, and a single-user runtime to multi-user isolation. Generalize the Narxoz-focused personal configuration for multiple Canvas institutions, then consider public plugin submission. This requires tenant isolation, encrypted token storage, institution configuration, OAuth lifecycle management, remote artifact handling, audit and observability, and a public security review.
