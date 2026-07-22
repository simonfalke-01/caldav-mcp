from __future__ import annotations

import pytest

from icloud_caldav_mcp.errors import ValidationError
from icloud_caldav_mcp.models import QuickAddInput
from icloud_caldav_mcp.quickadd import parse_quick_add


def test_quick_add_parses_relative_phrase_and_duration() -> None:
    event, parsed = parse_quick_add(
        QuickAddInput(
            text="lunch with Sam tomorrow 1pm for 90 minutes",
            timezone="Asia/Singapore",
            now="2026-07-22T10:00:00+08:00",
        ),
        "UTC",
    )
    assert event.summary == "lunch with Sam"
    assert event.start == "2026-07-23T13:00:00+08:00"
    assert event.end == "2026-07-23T14:30:00+08:00"
    assert parsed["confidence"] == "high"


def test_quick_add_rejects_phrase_without_time_evidence() -> None:
    with pytest.raises(ValidationError, match="explicit date or time"):
        parse_quick_add(QuickAddInput(text="have lunch with Sam"), "UTC")
