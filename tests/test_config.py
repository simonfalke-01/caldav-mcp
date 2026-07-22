from __future__ import annotations

from pathlib import Path

import pytest

from icloud_caldav_mcp.config import Settings
from icloud_caldav_mcp.errors import ConfigurationError


def test_environment_takes_precedence_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MCP_API_KEY=file-key-that-is-long\n"
        "ICLOUD_USERNAME=file@example.com\n"
        "ICLOUD_APP_PASSWORD=file-password\n"
        "PORT=9000\n"
    )
    monkeypatch.setenv("MCP_API_KEY", "environment-key-long")
    monkeypatch.setenv("PORT", "8123")

    settings = Settings.from_env(env_file)

    assert settings.mcp_api_key.get_secret_value() == "environment-key-long"
    assert settings.icloud_username.get_secret_value() == "file@example.com"
    assert settings.port == 8123
    assert "environment-key" not in repr(settings)


def test_missing_secrets_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MCP_API_KEY", "ICLOUD_USERNAME", "ICLOUD_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigurationError, match="MCP_API_KEY"):
        Settings.from_env(tmp_path / "missing")


def test_non_loopback_http_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MCP_API_KEY=a-sufficiently-long-key\n"
        "ICLOUD_USERNAME=user@example.com\n"
        "ICLOUD_APP_PASSWORD=password\n"
        "CALDAV_URL=http://calendar.example.com\n"
    )
    with pytest.raises(ConfigurationError, match="HTTPS"):
        Settings.from_env(env_file)
