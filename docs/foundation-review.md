# Independent foundation implementation review

An actual read-only subagent, `canvas_foundation_reviewer`, reviewed source, tests, dependency direction and the established security architecture. It used synthetic local tests, not live Canvas or credentials. No CRITICAL finding or origin/route escape was found.

| Severity | Finding | Disposition and verification |
| --- | --- | --- |
| HIGH | Token checking before HTML/control normalization allowed text to reconstruct a reflected token in the normalized profile | ACCEPTED. One shared infrastructure inert-text helper now serves screening and mapping. The HTTP credential boundary rejects raw and transformed reflection; entity/tag/control-split regression cases cover it. Credentials never enter application DTOs. |
| MEDIUM | Waiting for the profile authentication lock could exceed the aggregate deadline | ACCEPTED. Remaining monotonic time bounds the lock wait and operation. A queued-request test proves it fails before the held lock is released. |
| LOW | `raise ... from None` suppressed display but retained private exception text in configuration and timezone mapping chains | ACCEPTED. Raise fixed safe errors after leaving those exception handlers. Tests assert absent context/cause. |

The reviewer independently rechecked the fixes and reported all three resolved, with no remaining blocking findings for the profile/course GETs. Its targeted verification passed 118 tests. The final main full-suite verification passed 131 tests, plus type checking and configured lint. The wheel built successfully, was installed into the project virtual environment, and all 30 installed package modules imported successfully in isolated mode. The installed smoke-command help and dependency consistency check also passed. No real Canvas smoke test was run.

Implementation choices remain within the ADRs: a two-method connection subprotocol refines the provider interface for this slice; downloads remain disabled, so their directory setting is optional for now. HTTPCore's public backend hook provides the architecture's DNS-to-connection boundary without a transport framework or extra provider abstraction.

Residual limits: the upstream service and API documentation can change; unusual Link forms or non-public/private Canvas deployments fail closed. Security tests exercise the actual client with mock network streams and verified socket/TLS arguments, not a live university account. Same-OS-user compromise and future MCP/remote/OAuth/download requirements remain separate security boundaries.
