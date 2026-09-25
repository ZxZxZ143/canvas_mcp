"""Dedicated Windows Credential Manager source for the personal Canvas plugin."""

from dataclasses import dataclass
from typing import Any

from canvas_mcp.domain.errors import AuthorizationError, ConfigurationError
from canvas_mcp.domain.models import AccessScope
from canvas_mcp.ports.credentials import AccessToken

TARGET_NAME = "canvas-mcp:personal:narxoz-student:v1"


def _win32cred() -> Any:
    try:
        import win32cred  # type: ignore[import-untyped]

        return win32cred
    except ImportError:
        raise ConfigurationError() from None


def _validated_token(value: str) -> str:
    # Match the existing environment credential contract without retaining bad input.
    import re

    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]{1,4096}=*", value):
        raise ConfigurationError()
    if len(value) > 4096 or len(value.encode("utf-16-le")) > 2560:
        raise ConfigurationError()
    return value


def save_token(value: str) -> None:
    """Create or replace only this plugin's generic credential."""
    token = _validated_token(value)
    api = _win32cred()
    try:
        api.CredWrite(
            {
                "Type": api.CRED_TYPE_GENERIC,
                "TargetName": TARGET_NAME,
                "CredentialBlob": token,
                "Persist": api.CRED_PERSIST_LOCAL_MACHINE,
                "UserName": "canvas-student",
            },
            0,
        )
    except Exception:
        raise ConfigurationError() from None


def read_token() -> AccessToken:
    api = _win32cred()
    try:
        credential = api.CredRead(TARGET_NAME, api.CRED_TYPE_GENERIC, 0)
        blob = credential["CredentialBlob"]
        value = blob.decode("utf-16-le") if isinstance(blob, bytes) else blob
        return AccessToken(_validated_token(value))
    except Exception:
        raise ConfigurationError() from None


def remove_token() -> bool:
    """Delete only this plugin's exact target; report whether it existed."""
    api = _win32cred()
    try:
        api.CredDelete(TARGET_NAME, api.CRED_TYPE_GENERIC, 0)
        return True
    except Exception as error:
        if getattr(error, "winerror", None) == 1168 or (
            getattr(error, "args", ()) and error.args[0] == 1168
        ):
            return False
        raise ConfigurationError() from None


@dataclass(frozen=True, repr=False)
class WindowsCredentialSource:
    _scope: AccessScope

    async def get_access_token(self, scope: AccessScope) -> AccessToken:
        if scope != self._scope:
            raise AuthorizationError()
        return read_token()
