"""Transport authentication contract, independent of OAuth and Canvas secrets."""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from canvas_mcp.domain.models import AccessScope, ConnectionId, PrincipalId


@dataclass(frozen=True)
class McpPrincipal:
    subject: PrincipalId
    connection_id: ConnectionId

    @property
    def scope(self) -> AccessScope:
        return AccessScope(self.subject, self.connection_id)


class McpAuthenticator(Protocol):
    async def authenticate(self, authorization: str | None) -> McpPrincipal | None: ...


class OAuthRejection(Exception):
    """Only fixed safe codes escape token validation."""

    def __init__(self, status: int, code: str, challenge: str | None = None) -> None:
        super().__init__(code)
        self.status, self.code, self.challenge = status, code, challenge


# ContextVar isolates concurrent requests and contains identity only, never a token.
current_principal: ContextVar[McpPrincipal | None] = ContextVar("mcp_principal", default=None)
# A bounded per-request allowlisted error ledger, shared with SDK child tasks.
current_failures: ContextVar[list[str] | None] = ContextVar("mcp_failures", default=None)
