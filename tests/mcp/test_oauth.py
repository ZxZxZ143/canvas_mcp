"""Cryptographically signed synthetic tokens and real SDK HTTP security tests."""

import asyncio
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from threading import Barrier

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from canvas_mcp.domain.errors import ConfigurationError
from canvas_mcp.domain.errors import AuthenticationError
from canvas_mcp.infrastructure.auth0 import Auth0Authenticator
from canvas_mcp.infrastructure.config.oauth import OAuthSettings, load_oauth_settings
from canvas_mcp.infrastructure.config.remote import RemoteSettings, load_remote_settings
from canvas_mcp.mcp.identity import OAuthRejection, current_principal
from canvas_mcp.mcp_http_server import create_app
from test_http import PathFreeConnection, rpc, CANVAS_TOKEN

ISSUER = "https://synthetic.auth0.com/"
BASE = "https://synthetic.onrender.com"
SUBJECT = "auth0|SYNTHETIC_PRIVATE_SUBJECT"
OAUTH = OAuthSettings(ISSUER, BASE + "/mcp", BASE, SUBJECT)
ENV = {
    "AUTH0_DOMAIN": "synthetic.auth0.com",
    "AUTH0_AUDIENCE": BASE + "/mcp",
    "AUTH0_ALLOWED_SUBJECT": SUBJECT,
    "MCP_PUBLIC_BASE_URL": BASE,
    "CANVAS_ACCESS_TOKEN": CANVAS_TOKEN,
}
SETTINGS = RemoteSettings(auth_mode="oauth")


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, **changes):
    claims = {
        "iss": ISSUER,
        "aud": BASE + "/mcp",
        "sub": SUBJECT,
        "iat": int(time.time()) - 1,
        "exp": int(time.time()) + 600,
        "scope": "canvas:read",
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "current"})


class Pool:
    def __init__(self, key):
        self.calls = []
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        self.documents = {
            ISSUER + ".well-known/openid-configuration": {
                "issuer": ISSUER,
                "jwks_uri": ISSUER + ".well-known/jwks.json",
                "authorization_endpoint": ISSUER + "authorize",
                "token_endpoint": ISSUER + "oauth/token",
                "code_challenge_methods_supported": ["S256"],
            },
            ISSUER + ".well-known/jwks.json": {
                "keys": [{**jwk, "kid": "current", "alg": "RS256", "use": "sig"}]
            },
        }
        self.status = 200

    @asynccontextmanager
    async def stream(self, method, url, **kwargs):
        assert method == "GET" and kwargs["headers"] == {"Accept": "application/json"}
        self.calls.append(url)
        pool = self

        class Response:
            status = pool.status

            async def aiter_stream(self):
                yield json.dumps(pool.documents[url]).encode()

        yield Response()

    async def aclose(self):
        pass


def client(key, *, logs=None, pool=None, failure=None):
    opened = []

    @asynccontextmanager
    async def factory():
        assert current_principal.get().scope.connection_id == "personal_canvas"
        assert current_principal.get().subject == SUBJECT
        opened.append(True)

        class Connection(PathFreeConnection):
            async def get_profile(self):
                if failure:
                    raise failure
                return await super().__getattr__("get_profile")()

        yield Connection()

    verifier = Auth0Authenticator(OAUTH, pool=pool or Pool(key))
    return TestClient(
        create_app(
            SETTINGS,
            environ=ENV,
            connection_factory=factory,
            log_stream=logs or io.StringIO(),
            authenticator=verifier,
        ),
        base_url=BASE,
    ), opened


def headers(value):
    return {
        "Authorization": "Bearer " + value,
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25",
    }


def test_metadata_challenge_routes_and_health_do_not_open_canvas(key):
    connection, opened = client(key)
    with connection:
        assert connection.get("/health").json() == {"status": "ok"}
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            doc = connection.get(path)
            assert doc.status_code == 200
            assert doc.json()["resource"] == BASE + "/mcp"
            assert doc.json()["authorization_servers"] == [ISSUER]
            assert doc.json()["scopes_supported"] == ["canvas:read"]
        missing = connection.post("/mcp", json={})
        assert missing.status_code == 401
        assert missing.headers["WWW-Authenticate"] == OAUTH.challenge()
        assert connection.get("/debug").status_code == 404
        assert (
            connection.get("/health", headers={"Host": "internal-healthcheck"}).status_code == 200
        )
    assert not opened


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"iss": "https://other.auth0.com/"}, 401),
        ({"aud": "https://other/mcp"}, 401),
        ({"aud": [BASE + "/mcp", "another"]}, 401),
        ({"exp": 1}, 401),
        ({"nbf": 9999999999}, 401),
        ({"iat": 9999999999}, 401),
        ({"sub": "auth0|other"}, 403),
        ({"scope": "canvas:write"}, 403),
        ({"scope": ["canvas:read"]}, 403),
    ],
)
def test_invalid_wrong_user_and_scope_never_open_canvas(key, changes, status):
    connection, opened = client(key)
    with connection:
        response = rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_profile", "arguments": {}},
            headers(token(key, **changes)),
        )
        assert response.status_code == status
    assert not opened


def test_forged_unsigned_and_malformed_tokens(key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    connection, opened = client(key)
    with connection:
        for value in (
            token(other),
            "bad.jwt.token",
            jwt.encode({"sub": SUBJECT}, algorithm="none", key=""),
        ):
            assert rpc(connection, "tools/list", headers=headers(value)).status_code == 401
    assert not opened


def test_wrong_subject_file_content_never_composes_canvas_storage_or_parser(key, monkeypatch):
    from canvas_mcp.infrastructure.files.ephemeral import EphemeralStore

    def forbidden(*args, **kwargs):
        raise AssertionError("Wrong subject reached ephemeral file creation")

    monkeypatch.setattr(EphemeralStore, "begin", forbidden)
    connection, opened = client(key)
    with connection:
        response = rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_file_content", "arguments": {"course_id": 8, "file_id": 50}},
            headers(token(key, sub="auth0|other")),
        )
        assert response.status_code == 403
    assert not opened


def test_expired_initial_access_is_replaced_by_independent_refreshed_access(key, monkeypatch):
    """Synthetic provider outputs only: this server never performs a refresh exchange."""
    issued = int(time.time())
    clock = [issued]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock[0], tz=tz)

    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)
    initial = token(key, iat=issued, exp=issued + 120, jti="synthetic-initial")
    renewed = token(
        key,
        iat=issued + 121,
        exp=issued + 241,
        jti="synthetic-renewed",
        scope="canvas:read offline_access",
    )
    logs = io.StringIO()
    pool = Pool(key)
    connection, opened = client(key, logs=logs, pool=pool)
    with connection:
        request = {"name": "canvas_list_courses", "arguments": {}}
        first = rpc(connection, "tools/call", request, headers(initial))
        assert first.status_code == 200 and not first.json()["result"].get("isError")
        clock[0] = issued + 121
        expired = rpc(connection, "tools/call", request, headers(initial))
        assert expired.status_code == 401
        assert 'error="invalid_token"' in expired.headers["WWW-Authenticate"]
        assert len(opened) == 1
        second = rpc(connection, "tools/call", request, headers(renewed))
        assert second.status_code == 200 and not second.json()["result"].get("isError")
    assert len(opened) == 2
    assert pool.calls == [
        ISSUER + ".well-known/openid-configuration",
        ISSUER + ".well-known/jwks.json",
    ]
    for private in (initial, renewed, CANVAS_TOKEN, SUBJECT):
        assert private not in logs.getvalue()
        assert private not in first.text + expired.text + second.text


@pytest.mark.parametrize(
    "changes,status,code",
    [
        ({"scope": "offline_access"}, 403, "insufficient_scope"),
        ({"scope": "canvas:write offline_access"}, 403, "insufficient_scope"),
        ({"sub": "auth0|wrong-refreshed-user"}, 403, "authorization_denied"),
        ({"aud": "https://another.example/mcp"}, 401, "invalid_token"),
        ({"aud": [BASE + "/mcp", "https://another.example/api"]}, 401, "invalid_token"),
        ({"iss": "https://another.auth0.com/"}, 401, "invalid_token"),
        ({"exp": 1}, 401, "invalid_token"),
    ],
)
def test_refreshed_access_rechecks_identity_resource_expiry_and_business_scope(
    key, changes, status, code
):
    logs = io.StringIO()
    connection, opened = client(key, logs=logs)
    renewed = token(
        key, jti="synthetic-renewed", **{"scope": "canvas:read offline_access", **changes}
    )
    with connection:
        response = rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_profile", "arguments": {}},
            headers(renewed),
        )
        assert response.status_code == status
        assert code in response.text
    assert not opened
    assert renewed not in response.text + logs.getvalue()


def test_opaque_refresh_token_cannot_be_mcp_bearer_or_reach_auth0_or_canvas(key):
    synthetic_refresh = "synthetic_refresh_" + "NOT_A_RESOURCE_ACCESS_TOKEN_" * 3
    pool = Pool(key)
    logs = io.StringIO()
    connection, opened = client(key, logs=logs, pool=pool)
    with connection:
        response = rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_profile", "arguments": {}},
            headers(synthetic_refresh),
        )
        assert response.status_code == 401
        assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]
        assert "invalid_token" in response.text
    assert not opened and not pool.calls
    assert synthetic_refresh not in response.text + logs.getvalue()


def test_new_resource_server_accepts_refreshed_access_without_original_exchange(key):
    initial = token(key, jti="synthetic-original-access")
    renewed = token(
        key,
        iat=int(time.time()),
        exp=int(time.time()) + 900,
        jti="synthetic-new-access",
        scope="canvas:read offline_access",
    )
    request = {"name": "canvas_get_profile", "arguments": {}}
    old_server, old_opened = client(key)
    with old_server:
        original = rpc(old_server, "tools/call", request, headers(initial))
        assert original.status_code == 200
    fresh_pool = Pool(key)
    new_server, new_opened = client(key, pool=fresh_pool)
    with new_server:
        fresh = rpc(new_server, "tools/call", request, headers(renewed))
        assert fresh.status_code == 200
        assert (
            original.json()["result"]["structuredContent"]["id"]
            == fresh.json()["result"]["structuredContent"]["id"]
        )
        for path in ("/refresh", "/oauth/token", "/oauth/revoke"):
            assert new_server.post(path, json={"grant_type": "refresh_token"}).status_code == 404
        resource = new_server.get("/.well-known/oauth-protected-resource").json()
        assert resource["scopes_supported"] == ["canvas:read"]
        listed = rpc(new_server, "tools/list", headers=headers(renewed)).json()["result"]["tools"]
        assert len(listed) == 18
        assert all(
            t["securitySchemes"] == [{"type": "oauth2", "scopes": ["canvas:read"]}] for t in listed
        )
    assert len(old_opened) == len(new_opened) == 1
    assert fresh_pool.calls == [
        ISSUER + ".well-known/openid-configuration",
        ISSUER + ".well-known/jwks.json",
    ]


def test_concurrent_access_tokens_do_not_share_oauth_session_or_refresh_state(key, monkeypatch):
    issued = int(time.time())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(issued + 119, tz=tz)

    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)
    values = [
        token(key, iat=issued, exp=issued + 120, jti="synthetic-concurrent-initial"),
        token(
            key,
            iat=issued + 119,
            exp=issued + 1019,
            jti="synthetic-concurrent-renewed",
            scope="canvas:read offline_access",
        ),
    ]
    barrier = Barrier(2)
    pool = Pool(key)
    logs = io.StringIO()
    connection, opened = client(key, logs=logs, pool=pool)

    def invoke(value):
        barrier.wait(timeout=5)
        return rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_profile", "arguments": {}},
            headers(value),
        )

    with connection, ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(invoke, values))
        assert all(
            r.status_code == 200 and not r.json()["result"].get("isError") for r in responses
        )
        assert len({r.json()["result"]["structuredContent"]["id"] for r in responses}) == 1
        assert all("Mcp-Session-Id" not in r.headers for r in responses)
    assert len(opened) == 2 and len(pool.calls) == 2
    assert all(value not in logs.getvalue() for value in values)


def test_auth0_oidc_userinfo_audience_is_allowed_only_with_our_resource(key):
    connection, opened = client(key)
    with connection:
        for aud in ([OAUTH.audience], [OAUTH.audience, ISSUER + "userinfo"]):
            assert (
                rpc(connection, "tools/list", headers=headers(token(key, aud=aud))).status_code
                == 200
            )
        for aud in ([ISSUER + "userinfo"], [OAUTH.audience, OAUTH.audience]):
            assert (
                rpc(connection, "tools/list", headers=headers(token(key, aud=aud))).status_code
                == 401
            )
    assert not opened


def test_real_sdk_initialize_tools_profile_courses_upcoming_and_private_logs(key):
    logs = io.StringIO()
    connection, opened = client(key, logs=logs)
    bearer = token(key)
    with connection:
        h = headers(bearer)
        assert (
            rpc(
                connection,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "synthetic", "version": "1"},
                },
                h,
            ).status_code
            == 200
        )
        tools = rpc(connection, "tools/list", headers=h).json()["result"]["tools"]
        assert len(tools) == 18
        for tool in tools:
            assert tool["securitySchemes"] == [{"type": "oauth2", "scopes": ["canvas:read"]}]
            assert tool["_meta"]["securitySchemes"] == tool["securitySchemes"]
        profile = next(t for t in tools if t["name"] == "canvas_get_profile")
        assert profile["_meta"]["openai/profile"] is True
        assert profile["outputSchema"]["additionalProperties"] is False
        for name, args in (
            ("canvas_get_profile", {}),
            ("canvas_list_courses", {}),
            ("canvas_get_upcoming", {}),
        ):
            response = rpc(connection, "tools/call", {"name": name, "arguments": args}, h)
            assert response.status_code == 200 and not response.json()["result"].get("isError")
            if name == "canvas_get_profile":
                assert set(response.json()["result"]["structuredContent"]) == {"id", "name"}
        extra = rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_profile", "arguments": {"user": "other"}},
            h,
        )
        assert extra.json()["result"]["isError"] is True
    assert opened
    events = {json.loads(line)["event"] for line in logs.getvalue().splitlines()}
    assert {
        "oauth_validation_success",
        "mcp_initialize",
        "tool_call_started",
        "tool_call_completed",
    } <= events
    for private in (SUBJECT, CANVAS_TOKEN, bearer, "Synthetic coursework"):
        assert private not in logs.getvalue()


def test_exact_host_origin_and_proxy_headers(key):
    connection, opened = client(key)
    with connection:
        for extra in (
            {"Host": "evil.onrender.com"},
            {"Origin": "https://evil.example"},
            {"Host": "evil.example", "X-Forwarded-Host": "synthetic.onrender.com"},
            {"Host": "evil.onrender.com", "Forwarded": "host=synthetic.onrender.com;proto=https"},
        ):
            assert (
                rpc(connection, "tools/list", headers={**headers(token(key)), **extra}).status_code
                == 421
            )
        assert (
            rpc(
                connection,
                "tools/list",
                headers={**headers(token(key)), "X-Forwarded-Host": "evil.example"},
            ).status_code
            == 200
        )
    assert not opened


def test_key_cache_and_rotation(key):
    async def run():
        pool = Pool(key)
        auth = Auth0Authenticator(OAUTH, pool=pool)
        for _ in range(3):
            assert (
                await auth.authenticate("Bearer " + token(key))
            ).connection_id == "personal_canvas"
        assert len(pool.calls) == 2
        newer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(newer.public_key()))
        pool.documents[ISSUER + ".well-known/jwks.json"]["keys"].append({**jwk, "kid": "rotated"})
        rotated = jwt.encode(
            jwt.decode(
                token(newer), newer.public_key(), algorithms=["RS256"], audience=OAUTH.audience
            ),
            newer,
            algorithm="RS256",
            headers={"kid": "rotated"},
        )
        with pytest.raises(OAuthRejection):
            await auth.authenticate("Bearer " + rotated)
        auth._last_fetch -= 31
        assert await auth.authenticate("Bearer " + rotated)
        assert len(pool.calls) == 4
        await auth.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "https://other.auth0.com/"),
        ("jwks_uri", "https://evil.example/keys"),
        ("code_challenge_methods_supported", ["plain"]),
        ("code_challenge_methods_supported", "plainS256"),
        ("code_challenge_methods_supported", ["S256", 1]),
    ],
)
def test_untrusted_discovery_never_fetches_other_urls(key, field, value):
    pool = Pool(key)
    pool.documents[ISSUER + ".well-known/openid-configuration"][field] = value
    connection, opened = client(key, pool=pool)
    with connection:
        assert rpc(connection, "tools/list", headers=headers(token(key))).status_code == 503
    assert len(pool.calls) == 1 and not opened


def test_provider_outage_cooldown_remains_retryable_and_recovers(key):
    pool = Pool(key)
    pool.status = 503
    connection, opened = client(key, pool=pool)
    with connection:
        for _ in range(2):
            response = rpc(connection, "tools/list", headers=headers(token(key)))
            assert response.status_code == 503 and "WWW-Authenticate" not in response.headers
        assert len(pool.calls) == 1 and not opened
        pool.status = 200
        verifier = connection.app.authenticator
        verifier._last_fetch -= 31
        assert rpc(connection, "tools/list", headers=headers(token(key))).status_code == 200
        assert len(pool.calls) == 3 and not opened


def test_rate_limit_is_bounded_and_has_no_identity_map(key):
    connection, opened = client(key)
    with connection:
        responses = [
            rpc(
                connection,
                "tools/call",
                {"name": "canvas_get_profile", "arguments": {}},
                headers(token(key)),
            )
            for _ in range(20)
        ]
        assert any(response.status_code == 429 for response in responses)
        assert len(opened) < 20


def test_canvas_pat_error_does_not_initiate_oauth_and_invalid_args_are_not_upstream(key):
    logs = io.StringIO()
    connection, opened = client(key, logs=logs, failure=AuthenticationError())
    with connection:
        failed = rpc(
            connection,
            "tools/call",
            {"name": "canvas_get_profile", "arguments": {}},
            headers(token(key)),
        )
        result = failed.json()["result"]
        assert result["isError"] and "operator maintenance" in result["content"][0]["text"]
        assert "mcp/www_authenticate" not in json.dumps(result)
        assert "WWW-Authenticate" not in failed.headers
        assert "canvas_upstream_error" in logs.getvalue()
        logs.seek(0)
        logs.truncate()
        rpc(
            connection, "tools/call", {"name": "unknown_tool", "arguments": {}}, headers(token(key))
        )
        assert "canvas_upstream_error" not in logs.getvalue()
    assert len(opened) == 1


def test_synthetic_private_inputs_never_reach_metadata_health_errors_or_logs(key):
    logs = io.StringIO()
    connection, opened = client(key, logs=logs)
    bearer = token(key)
    with connection:
        responses = [
            connection.get("/health"),
            connection.get("/.well-known/oauth-protected-resource"),
            rpc(
                connection,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": CANVAS_TOKEN, "version": SUBJECT},
                },
                headers(bearer),
            ),
            rpc(
                connection,
                "tools/call",
                {
                    "name": "canvas_get_profile",
                    "arguments": {"private": CANVAS_TOKEN, "subject": SUBJECT, "token": bearer},
                },
                headers(bearer),
            ),
            rpc(connection, "tools/list", headers=headers(token(key, exp=1))),
        ]
        for private in (CANVAS_TOKEN, SUBJECT, bearer):
            assert all(private not in response.text for response in responses)
            assert private not in logs.getvalue()
    assert not opened


def test_provider_failure_and_key_count_are_bounded(key):
    for bad_keys in ([], [{}] * 17, [{"kty": "RSA", "kid": "bad", "n": "bad", "e": "AQAB"}]):
        pool = Pool(key)
        pool.documents[ISSUER + ".well-known/jwks.json"] = {"keys": bad_keys}
        connection, opened = client(key, pool=pool)
        with connection:
            assert rpc(connection, "tools/list", headers=headers(token(key))).status_code == 503
        assert not opened


def test_production_settings_fail_closed_and_provider_injected_port():
    assert load_remote_settings({"PORT": "9876", "MCP_HTTP_PORT": "8000"}).port == 9876
    assert load_remote_settings({}).host == "127.0.0.1"
    for env in (
        {"MCP_HTTP_AUTH_MODE": "development", "MCP_HTTP_DEVELOPMENT": "true"},
        {"MCP_HTTP_DEV_TOKEN": "synthetic"},
        {"MCP_HTTP_DEVELOPMENT": "true"},
    ):
        with pytest.raises(ConfigurationError):
            load_remote_settings({"MCP_HTTP_DEPLOYMENT": "production", **env})
    for provider in ({"RENDER": "true"}, {"RAILWAY_ENVIRONMENT_ID": "synthetic"}, {}):
        settings = load_remote_settings(
            {
                **provider,
                "MCP_HTTP_DEPLOYMENT": "production",
                "MCP_HTTP_AUTH_MODE": "oauth",
                "MCP_HTTP_HOST": "0.0.0.0",
                "PORT": "10001",
                "MCP_HTTP_PORT": "8000",
            }
        )
        assert settings.port == 10001 and settings.host == "0.0.0.0"
        assert settings.deployment == "production" and settings.auth_mode == "oauth"
    with pytest.raises(ConfigurationError):
        load_remote_settings({"MCP_HTTP_DEPLOYMENT": "render"})
    assert load_oauth_settings(ENV) == OAUTH
    for field, value in (
        ("AUTH0_AUDIENCE", BASE),
        ("AUTH0_ALLOWED_SUBJECT", ""),
        ("AUTH0_ISSUER", "https://other.auth0.com/"),
        ("MCP_PUBLIC_BASE_URL", "http://localhost"),
        ("AUTH0_REQUIRED_SCOPES", "canvas:write"),
    ):
        with pytest.raises(ConfigurationError):
            load_oauth_settings({**ENV, field: value})
