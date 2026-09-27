# Canvas Student roadmap

Version 0.1.0 completes Phases 1–5: connection foundation, academic reads, secure files, local MCP integration, and personal plugin packaging. The following phases are plans, not implemented capabilities. They proceed in this order.

## Phase 6 — Remote MCP and optional mobile companion

Phase 6.1 prepares a local Streamable HTTP adapter sharing the stable stdio tools, default-deny/development authentication, scoped credential ports, HTTP limits and Docker packaging. See [architecture and boundaries](phase6-remote-mcp.md) and [actual validation](phase6-remote-mcp-validation.md).

Phase 6.2 is complete as a personal read-only beta: real Auth0 token validation, scoped Render runtime secrets, Canvas remote smoke, ChatGPT Web queries and recovery after more than 16 idle minutes were verified. Render Free Web Service replaced Railway to meet the zero-cost constraint. Recovery required manual OAuth reconnect after token expiry; no always-on guarantee is made. See [Render setup and actual evidence](phase6-2-render-deployment.md) and [architecture and Railway history](phase6-2-remote-personal.md). Phase 6.3 will implement remote files, but must not begin automatically. Phase 6.4 may add a mobile web/PWA companion. No native mobile support is claimed. Keep Canvas read-only throughout Phase 6.

## Phase 7 — Safe Canvas Write Capabilities

Design and add narrowly scoped submission-file upload, assignment submission, and submission comments where justified. Separate read and write capabilities. Every mutation must require explicit Codex approval, an exact preview of the intended change, fresh authorization, and an auditable result. No silent Canvas writes.

## Phase 8 — Public / Multi-user Plugin

Evaluate a transition from local stdio MCP to remote MCP, personal access tokens to OAuth, and a single-user runtime to multi-user isolation. Generalize the Narxoz-focused personal configuration for multiple Canvas institutions, then consider public plugin submission. This requires tenant isolation, encrypted token storage, institution configuration, OAuth lifecycle management, remote artifact handling, audit and observability, and a public security review.
