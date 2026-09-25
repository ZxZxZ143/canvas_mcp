# ADR-002: Put Canvas behind a narrow query provider

Status: accepted after architecture review.

MCP endpoint wrappers would couple transport, Canvas schemas and coursework workflows. Use one `LmsQueries` port returning normalized records; keep Canvas routes, mappings, authentication attachment and resilience in infrastructure. Application aggregates own assignment context and cross-course workload. Core IDs are opaque; each provider supplies its own canonical ingress codec.

Preserve the requested `canvas_*` facade for v1. A future provider implements the port and reports unsupported optional capabilities; a naming facade can change without rewriting orchestration. Do not build multiple provider implementations, plugin registries, repositories, generic REST tools or a service per resource now. The price is maintaining deliberate normalized mappings and provider contract tests.
