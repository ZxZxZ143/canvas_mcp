"""Safe CLI behavior without real Canvas or credential writes."""

from canvas_mcp import credentials_cli
from canvas_mcp.domain.errors import AuthenticationError


def test_configure_validates_before_replacing(monkeypatch, capsys):
    saved = []

    async def reject(_token):
        raise AuthenticationError()

    monkeypatch.setattr(credentials_cli.getpass, "getpass", lambda _prompt: "SYNTHETIC")
    monkeypatch.setattr(credentials_cli, "_validate", reject)
    monkeypatch.setattr(credentials_cli, "save_token", saved.append)
    assert credentials_cli.main(["configure"]) == 1
    assert saved == []
    assert "SYNTHETIC" not in capsys.readouterr().err


def test_logout_deletes_only_dedicated_credential(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(credentials_cli, "remove_token", lambda: calls.append(True) or True)
    assert credentials_cli.main(["logout"]) == 0
    assert calls == [True]
    assert "removed" in capsys.readouterr().out
