"""Credential Manager boundary tests with a disposable, synthetic target."""

import asyncio
import sys
from uuid import uuid4

import pytest

from canvas_mcp.domain.errors import AuthorizationError, ConfigurationError
from canvas_mcp.domain.models import AccessScope, ConnectionId, PrincipalId
from canvas_mcp.infrastructure.config import windows_credentials as win


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager only")
def test_synthetic_windows_credential_round_trip(monkeypatch):
    monkeypatch.setattr(win, "TARGET_NAME", f"canvas-mcp:test:{uuid4()}")
    scope = AccessScope(PrincipalId("local"), ConnectionId("test"))
    other = AccessScope(PrincipalId("other"), ConnectionId("test"))
    try:
        win.save_token("SYNTHETIC_TOKEN_123456")
        assert asyncio.run(win.WindowsCredentialSource(scope).get_access_token(scope)).value == (
            "SYNTHETIC_TOKEN_123456"
        )
        with pytest.raises(AuthorizationError):
            asyncio.run(win.WindowsCredentialSource(scope).get_access_token(other))
        assert win.remove_token() is True
        assert win.remove_token() is False
        with pytest.raises(ConfigurationError):
            win.read_token()
    finally:
        win.remove_token()


def test_invalid_token_is_rejected_before_storage(monkeypatch):
    monkeypatch.setattr(
        win, "_win32cred", lambda: pytest.fail("should not access Credential Manager")
    )
    with pytest.raises(ConfigurationError):
        win.save_token("line\nbreak")
