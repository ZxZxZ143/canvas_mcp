# Canvas Student roadmap

Version 0.1.0 completes Phases 1–5: connection foundation, academic reads, secure files, local MCP integration, and personal plugin packaging. The following phases are plans, not implemented capabilities. They proceed in this order.

## Phase 6 — Personal Plugin Hardening & UX

Keep the plugin personal and Canvas read-only. Improve daily use through faster course and assignment resolution, better caching and follow-up context, fewer redundant Canvas calls, stronger file and material search, clearer credential and configuration UX, observability, performance profiling, and other reliability polish. Any caching must preserve authorization and data freshness boundaries.

## Phase 7 — Safe Canvas Write Capabilities

Design and add narrowly scoped submission-file upload, assignment submission, and submission comments where justified. Separate read and write capabilities. Every mutation must require explicit Codex approval, an exact preview of the intended change, fresh authorization, and an auditable result. No silent Canvas writes.

## Phase 8 — Public / Multi-user Plugin

Evaluate a transition from local stdio MCP to remote MCP, personal access tokens to OAuth, and a single-user runtime to multi-user isolation. Generalize the Narxoz-focused personal configuration for multiple Canvas institutions, then consider public plugin submission. This requires tenant isolation, encrypted token storage, institution configuration, OAuth lifecycle management, remote artifact handling, audit and observability, and a public security review.
