from __future__ import annotations

from datetime import UTC, datetime

import pytest

from icloud_caldav_mcp.errors import ValidationError
from icloud_caldav_mcp.ical import build_event, event_component_to_dict, first_event
from icloud_caldav_mcp.models import AlarmInput, AttendeeInput, CreateEventInput, OrganizerInput


def test_build_and_parse_full_recurring_event_redacts_configured_identity() -> None:
    request = CreateEventInput(
        summary="Weekly planning",
        start="2026-08-03T09:00:00",
        end="2026-08-03T10:00:00",
        timezone="Asia/Singapore",
        rrule="FREQ=WEEKLY;BYDAY=MO;COUNT=4",
        location="Room 1",
        categories=["work", "planning"],
        alarms=[AlarmInput(minutes_before=15)],
        attendees=[AttendeeInput(email="configured@example.com", rsvp=True)],
        organizer=OrganizerInput(email="owner@example.com", name="Owner"),
    )
    calendar, uid = build_event(
        request, "UTC", uid="stable-uid", now=datetime(2026, 7, 22, tzinfo=UTC)
    )
    serialized = calendar.to_ical().decode()
    parsed = event_component_to_dict(
        first_event(calendar),
        calendar_id="calendar-id",
        calendar_name="Primary",
        configured_username="configured@example.com",
    )

    assert uid == "stable-uid"
    assert "RRULE:FREQ=WEEKLY;COUNT=4;BYDAY=MO" in serialized
    assert "TZID=Asia/Singapore" in serialized
    assert parsed["attendees"][0]["email"] == "[configured account]"
    assert parsed["alarms"][0]["minutes_before"] == 15
    assert parsed["rrule"] == "FREQ=WEEKLY;COUNT=4;BYDAY=MO"


def test_all_day_uses_date_values_and_exclusive_end() -> None:
    calendar, _ = build_event(
        CreateEventInput(summary="Holiday", start="2026-08-10", all_day=True), "UTC"
    )
    component = first_event(calendar)
    assert component.decoded("DTSTART").isoformat() == "2026-08-10"
    assert component.decoded("DTEND").isoformat() == "2026-08-11"


def test_end_before_start_is_rejected() -> None:
    with pytest.raises(ValidationError, match="later"):
        build_event(
            CreateEventInput(
                summary="Bad",
                start="2026-08-03T10:00:00",
                end="2026-08-03T09:00:00",
            ),
            "UTC",
        )


def test_unknown_model_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        CreateEventInput.model_validate(
            {
                "summary": "Nope",
                "start": "2026-08-03T10:00:00",
                "unexpected": "field",
            }
        )
