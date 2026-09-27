"""Infrastructure-only secret source; never inject it into application or MCP."""

from dataclasses import dataclass, field
from typing import Protocol

from canvas_mcp.domain.models import AccessScope


@dataclass(frozen=True)
class AccessToken:
    """repr suppression is convenience, NOT safe serialization or secure memory."""

    value: str = field(repr=False)


class CredentialSource(Protocol):
    async def get_access_token(self, scope: AccessScope) -> AccessToken: ...


class CanvasCredentialProvider(Protocol):
    """Resolve a credential source for a principal's authorized account scope.

    The local Windows source already implements CredentialSource. Remote secret
    storage in Phase 6.2 implements this provider; application services see neither.
    """

    def for_scope(self, scope: AccessScope) -> CredentialSource: ...
