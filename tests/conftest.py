from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from caldav import DAVClient
from pydantic import SecretStr

from icloud_caldav_mcp.config import Settings
from icloud_caldav_mcp.service import CalendarService


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mcp_api_key=SecretStr("test-mcp-key-at-least-16"),
        icloud_username=SecretStr("configured@example.com"),
        icloud_app_password=SecretStr("test-app-password"),
        caldav_url="http://127.0.0.1:9999",
        default_timezone="Asia/Singapore",
    )


@pytest.fixture
def radicale_url(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    storage = tmp_path / "radicale"
    process = subprocess.Popen(  # noqa: S603 - fixed local executable and arguments
        [
            sys.executable,
            "-m",
            "radicale",
            "-C",
            "/dev/null",
            "--server-hosts",
            f"127.0.0.1:{port}",
            "--auth-type",
            "none",
            "--storage-filesystem-folder",
            str(storage),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("Radicale exited before accepting connections") from None
            time.sleep(0.05)
    else:
        process.terminate()
        raise RuntimeError("Radicale did not start within 10 seconds")
    try:
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def caldav_service(radicale_url: str) -> Iterator[CalendarService]:
    bootstrap = DAVClient(radicale_url, username="configured@example.com", password="test-password")
    bootstrap.principal().make_calendar(name="Primary", cal_id="primary")
    bootstrap.close()
    settings = Settings(
        mcp_api_key=SecretStr("integration-key-123456"),
        icloud_username=SecretStr("configured@example.com"),
        icloud_app_password=SecretStr("test-password"),
        caldav_url=radicale_url,
        default_timezone="Asia/Singapore",
    )
    service = CalendarService(settings)
    try:
        yield service
    finally:
        service.close()
