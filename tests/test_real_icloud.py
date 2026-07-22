from __future__ import annotations

import os

import pytest

from icloud_caldav_mcp.config import Settings
from icloud_caldav_mcp.service import CalendarService


@pytest.mark.icloud
def test_read_only_icloud_discovery() -> None:
    if os.environ.get("RUN_ICLOUD_SMOKE") != "1":
        pytest.skip("set RUN_ICLOUD_SMOKE=1 to authorize a read-only iCloud discovery smoke")
    settings = Settings.from_env()
    if settings.caldav_url != "https://caldav.icloud.com":
        pytest.skip("CALDAV_URL is not the iCloud origin")
    service = CalendarService(settings)
    try:
        result = service.list_calendars()
    finally:
        service.close()
    assert result["count"] >= 1
