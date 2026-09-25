# ADR-005: Isolate credentials and bind access to a verified subject

Status: accepted after architecture review.

Separate nonsecret typed deployment configuration from `CredentialSource`. The future env implementation supplies a token only to the server's connection-specific Canvas API client. Application and MCP records carry server-created scope, never a token. Secret repr suppression is not a serializer or memory-security control; outputs/logs/errors use allowlists.

A connection binds principal, provider origin and verified authenticated subject. Subject/origin replacement invalidates handles/cursors; same-subject rotation can preserve the binding after revalidation. Request-owned budgets prevent multiplying resource limits across aggregates. Cursor state is bounded runtime memory, not a general cache or database.

Future keychain/OAuth/encrypted stores and inbound user authentication replace outer adapters. They do not add global mutable auth to the core. No tenant database or OAuth framework is built now. Local same-OS-user compromise remains outside the isolation guarantee; parser environments and sandbox access must be separate from server credentials.
