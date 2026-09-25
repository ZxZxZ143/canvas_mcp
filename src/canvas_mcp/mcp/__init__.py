"""Future MCP transport adapter. No SDK import, handlers, registration or startup.

Owns request/session identity binding, ingress schema adapter, response projection,
untrusted-content labels, safe errors, and stdio framing. Uses CourseworkQueries,
never the Canvas HTTP client, credentials, raw URLs, or arbitrary filesystem paths.
The future local artifact bridge resolves scoped handles separately from queries.
"""
