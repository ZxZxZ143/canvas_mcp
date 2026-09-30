"""App-owned scoped connections; remote composition never selects Windows credentials."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import TextIO

from canvas_mcp.composition import CanvasConnection, open_scoped_connection
from canvas_mcp.domain.errors import AuthorizationError, ConfigurationError
from canvas_mcp.domain.models import AccessScope
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource, load_settings
from canvas_mcp.infrastructure.config.schema import DeploymentSettings
from canvas_mcp.mcp.identity import current_principal
from canvas_mcp.ports.credentials import CanvasCredentialProvider, CredentialSource
from canvas_mcp.ports.state import StateRepository
from canvas_mcp.infrastructure.state.factory import repository as state_repository


@dataclass(frozen=True, repr=False)
class RemoteSecretProvider:
    """Runtime-only PAT for the one authorized personal connection."""

    scope: AccessScope
    source: CredentialSource = field(repr=False)

    def for_scope(self, scope: AccessScope) -> CredentialSource:
        if scope != self.scope or str(scope.connection_id) != "personal_canvas":
            raise AuthorizationError()
        return self.source


@dataclass(frozen=True, repr=False)
class DevelopmentCredentialProvider:
    """Immutable dev credential bound to one account; not a remote secret store."""

    scope: AccessScope
    source: CredentialSource = field(repr=False)

    def for_scope(self, scope: AccessScope) -> CredentialSource:
        if scope != self.scope:
            raise AuthorizationError()
        return self.source


class ScopedConnections:
    """Single-account 6.1 registry with request identity checked on every lease.

    Keeping the scoped provider alive preserves its bounded pagination cursors.
    No module-global credentials, cross-account fallback, or client-supplied scope.
    """

    def __init__(
        self,
        settings: DeploymentSettings,
        credentials: CanvasCredentialProvider,
        scope: AccessScope,
        log_stream: TextIO | None = None,
        state: StateRepository | None = None,
    ) -> None:
        self.settings, self.credentials, self.scope = settings, credentials, scope
        self.log_stream = log_stream
        self.state = state
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()
        self._connection: CanvasConnection | None = None

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[CanvasConnection]:
        principal = current_principal.get()
        if principal is None or principal.scope != self.scope:
            raise AuthorizationError()
        async with self._lock:
            if self._connection is None:
                self._connection = await self._stack.enter_async_context(
                    open_scoped_connection(
                        self.settings,
                        principal.scope,
                        self.credentials.for_scope(principal.scope),
                        log_stream=self.log_stream,
                        local_downloads=False,
                        state=self.state,
                    )
                )
            connection = self._connection
        yield connection

    async def aclose(self) -> None:
        await self._stack.aclose()
        self._connection = None


def development_connections(
    environ: Mapping[str, str], scope: AccessScope, log_stream: TextIO | None = None
) -> ScopedConnections:
    # Never honor an inherited local plugin's Windows provider on HTTP.
    if environ.get("CANVAS_CREDENTIAL_PROVIDER", "environment") != "environment":
        raise ConfigurationError()
    settings = load_settings(environ)
    if settings.download_directory is not None:
        raise ConfigurationError()
    source = EnvironmentCredentialSource.from_environment(scope, environ)
    return ScopedConnections(
        settings,
        DevelopmentCredentialProvider(scope, source),
        scope,
        log_stream,
        state=state_repository(environ, remote=True),
    )


def personal_connections(
    environ: Mapping[str, str], scope: AccessScope, log_stream: TextIO | None = None
) -> ScopedConnections:
    if (
        environ.get("CANVAS_CREDENTIAL_PROVIDER", "environment") != "environment"
        or environ.get("CANVAS_TRUSTED_PRIVATE_IPS", "")
        or environ.get("CANVAS_ALLOW_PRIVATE_ORIGIN", "false") != "false"
        or environ.get("DOWNLOAD_DIRECTORY", "")
        or environ.get("CANVAS_DOWNLOAD_ORIGINS", "")
    ):
        raise ConfigurationError()
    settings = load_settings(environ)
    source = EnvironmentCredentialSource.from_environment(scope, environ)
    return ScopedConnections(
        settings,
        RemoteSecretProvider(scope, source),
        scope,
        log_stream,
        state=state_repository(environ, remote=True),
    )
