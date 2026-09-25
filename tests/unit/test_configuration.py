import asyncio
from dataclasses import asdict

import pytest

from canvas_mcp.domain.errors import AuthorizationError, ConfigurationError
from canvas_mcp.domain.models import AccessScope, ConnectionId, PrincipalId
from canvas_mcp.infrastructure.config.environment import EnvironmentCredentialSource, load_settings
from canvas_mcp.infrastructure.config.origin import normalize_origin
from conftest import ORIGIN, TOKEN

SCOPE = AccessScope(PrincipalId("local"), ConnectionId("test"))


def test_valid_configuration_is_nonsecret_and_normalized():
    settings = load_settings(
        {
            "CANVAS_BASE_URL": "HTTPS://Canvas.Example.edu:443/",
            "CANVAS_ACCESS_TOKEN": TOKEN,
            "REQUEST_TIMEOUT": "3.5",
        }
    )
    assert settings.canvas_origin == ORIGIN
    assert settings.request_timeout_seconds == 3.5
    assert settings.download_directory is None
    assert TOKEN not in repr(settings) + str(asdict(settings))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://canvas.example.edu",
        "https://u:p@canvas.example.edu",
        "https://canvas.example.edu/api",
        "https://canvas.example.edu?x=1",
        "https://canvas.example.edu#x",
        "https://canvas.example.edu:80",
        "https://canvas.example.edu:",
        "https://canvas.example.edu\\evil",
        "https://canvas.example.edu\n",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://[::1]",
        "https://2130706433",
        "https://127.1",
        "https://localhost",
        "https://canvas.local",
        "https://canvas.example.edu.",
        "https://%65vil.example",
        "https://evil..example",
    ],
)
def test_reject_invalid_origins(value):
    with pytest.raises(ConfigurationError):
        normalize_origin(value)


def test_missing_base():
    with pytest.raises(ConfigurationError):
        load_settings({})


@pytest.mark.parametrize(
    "value", ["", "a b", "secret\n", "\rBearer", "secret\x00", "é", "a" * 4097]
)
def test_invalid_or_missing_token(value):
    with pytest.raises(ConfigurationError) as error:
        EnvironmentCredentialSource.from_environment(SCOPE, {"CANVAS_ACCESS_TOKEN": value})
    assert str(error.value) == "configuration_error"


@pytest.mark.parametrize(
    "name,value",
    [
        ("REQUEST_TIMEOUT", "nan"),
        ("REQUEST_TIMEOUT", "inf"),
        ("REQUEST_TIMEOUT", "0"),
        ("REQUEST_TIMEOUT", "61"),
        ("REQUEST_TIMEOUT", TOKEN),
        ("LOG_LEVEL", TOKEN),
        ("CACHE_TTL", "1"),
        ("MAX_DOWNLOAD_BYTES", "-1"),
        ("DOWNLOAD_DIRECTORY", "relative"),
    ],
)
def test_invalid_nonsecret_settings(name, value):
    with pytest.raises(ConfigurationError) as error:
        load_settings({"CANVAS_BASE_URL": ORIGIN, name: value})
    assert TOKEN not in repr(error.value) + str(error.value)
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_credentials_are_scoped_snapshot_with_safe_repr():
    async def run():
        env = {"CANVAS_ACCESS_TOKEN": TOKEN}
        source = EnvironmentCredentialSource.from_environment(SCOPE, env)
        env["CANVAS_ACCESS_TOKEN"] = "replacement"
        token = await source.get_access_token(SCOPE)
        assert token.value == TOKEN
        assert TOKEN not in repr(token) + str(token) + repr(source)
        with pytest.raises(AuthorizationError):
            await source.get_access_token(AccessScope(PrincipalId("other"), SCOPE.connection_id))

    asyncio.run(run())
