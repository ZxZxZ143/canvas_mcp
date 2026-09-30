"""State misconfiguration disables only state operations, never ordinary Canvas reads."""

from collections.abc import Mapping
from canvas_mcp.domain.errors import StateStoreUnavailableError
from canvas_mcp.infrastructure.state.settings import StateSettings
from canvas_mcp.infrastructure.state.sql import (
    SQLiteStateRepository,
    PostgreSQLStateRepository,
    UnavailableStateRepository,
)
from canvas_mcp.ports.state import StateRepository


def repository(environ: Mapping[str, str], *, remote: bool = False) -> StateRepository:
    try:
        settings = StateSettings.load(environ, remote=remote)
    except StateStoreUnavailableError:
        return UnavailableStateRepository()
    return (
        SQLiteStateRepository(settings)
        if settings.backend == "sqlite"
        else PostgreSQLStateRepository(settings)
    )
