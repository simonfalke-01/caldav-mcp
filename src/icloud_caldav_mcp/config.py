"""Environment-first configuration with fail-fast secret validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .errors import ConfigurationError


class Settings(BaseModel):
    """Validated runtime configuration; secret values remain wrapped at rest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mcp_api_key: SecretStr = Field(min_length=16)
    icloud_username: SecretStr = Field(min_length=3)
    icloud_app_password: SecretStr = Field(min_length=4)
    caldav_url: str = "https://caldav.icloud.com"
    port: int = Field(default=8000, ge=1, le=65535)
    default_timezone: str = "UTC"
    log_level: str = "INFO"

    @field_validator("caldav_url")
    @classmethod
    def validate_caldav_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise ValueError("CALDAV_URL must use HTTPS (loopback HTTP is allowed for tests)")
        return value

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("DEFAULT_TIMEZONE must be a valid IANA timezone") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @classmethod
    def from_env(cls, env_file: Path | str = ".env") -> Self:
        """Load process environment over `.env`, then apply safe non-secret defaults."""

        file_values = dotenv_values(env_file)

        def read(name: str, default: str | None = None) -> str | None:
            process_value = os.environ.get(name)
            if process_value is not None:
                return process_value
            file_value = file_values.get(name)
            return str(file_value) if file_value is not None else default

        missing = [
            name
            for name in ("MCP_API_KEY", "ICLOUD_USERNAME", "ICLOUD_APP_PASSWORD")
            if not read(name)
        ]
        if missing:
            names = ", ".join(missing)
            raise ConfigurationError(
                f"Missing required environment variable(s): {names}. "
                "Copy .env.example to .env and supply non-placeholder values."
            )
        try:
            return cls(
                mcp_api_key=SecretStr(read("MCP_API_KEY") or ""),
                icloud_username=SecretStr(read("ICLOUD_USERNAME") or ""),
                icloud_app_password=SecretStr(read("ICLOUD_APP_PASSWORD") or ""),
                caldav_url=read("CALDAV_URL", "https://caldav.icloud.com") or "",
                port=int(read("PORT", "8000") or "8000"),
                default_timezone=read("DEFAULT_TIMEZONE", "UTC") or "UTC",
                log_level=read("LOG_LEVEL", "INFO") or "INFO",
            )
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(f"Invalid server configuration: {exc}") from None
