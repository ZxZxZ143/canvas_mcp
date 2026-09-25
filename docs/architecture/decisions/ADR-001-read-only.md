# ADR-001: Keep queries separate from future commands

Status: accepted after architecture review.

The personal MVP needs coursework reads and local retrieval, but Canvas tokens may permit much more. Expose only fixed application/provider query contracts and a fixed GET route allowlist; no generic request methods and no command registrations. Downloads create controlled local artifacts but never write to Canvas. MCP hints are descriptive, not the security boundary.

Future submissions/comments require a separate command port, authorization/capabilities, confirmation bound to exact action and payload, replay/expiry handling, and audit records. Do not add dormant write methods today. This avoids coupling current reads to a speculative approval framework while leaving a clear place for one.
