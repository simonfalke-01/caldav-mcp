"""Container and console entry point."""

from __future__ import annotations

import sys

import uvicorn

from .config import Settings
from .errors import ConfigurationError
from .logging import configure_logging, get_logger
from .server import create_http_app
from .service import CalendarService


def main() -> None:
    """Validate configuration, log safe startup facts, and serve Streamable HTTP."""

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    configure_logging(settings.log_level)
    log = get_logger()
    service = CalendarService(settings)
    app = create_http_app(settings, service)
    log.info(
        "server_starting",
        transport="streamable-http",
        path="/mcp",
        bind="0.0.0.0",  # noqa: S104 -- required public container listener
        port=settings.port,
        mcp_auth_configured=True,
        caldav_auth_configured=True,
    )
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",  # noqa: S104 -- required public container listener
            port=settings.port,
            log_config=None,
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
