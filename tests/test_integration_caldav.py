from __future__ import annotations

import pytest

from icloud_caldav_mcp.errors import ConflictError
from icloud_caldav_mcp.models import (
    AlarmInput,
    AttendeeInput,
    BatchCreateInput,
    BatchDeleteInput,
    CreateCalendarInput,
    CreateEventInput,
    DateRangeInput,
    DeleteCalendarInput,
    DeleteEventInput,
    ExportICSInput,
    FindFreeSlotsInput,
    GetEventInput,
    ImportICSInput,
    MoveEventInput,
    OrganizerInput,
    SearchEventsInput,
    UpdateCalendarInput,
    UpdateEventInput,
)
from icloud_caldav_mcp.service import CalendarService


@pytest.mark.integration
def test_complete_caldav_round_trip(caldav_service: CalendarService) -> None:
    calendars = caldav_service.list_calendars()
    assert calendars["count"] == 1
    primary_id = calendars["calendars"][0]["calendar_id"]

    created = caldav_service.create_event(
        CreateEventInput(
            summary="Planning meeting",
            start="2026-08-03T09:00:00",
            end="2026-08-03T10:00:00",
            timezone="Asia/Singapore",
            calendar=primary_id,
            description="Roadmap and launch plan",
            location="Room 1",
            rrule="FREQ=WEEKLY;COUNT=3",
            alarms=[AlarmInput(minutes_before=10)],
            attendees=[AttendeeInput(email="configured@example.com")],
            organizer=OrganizerInput(email="owner@example.com"),
            categories=["work", "planning"],
        )
    )
    uid = created["uid"]
    assert created["created"] is True
    assert created["conflicts"] == []

    fetched = caldav_service.get_event(GetEventInput(uid=uid, calendar=primary_id))
    assert fetched["summary"] == "Planning meeting"
    assert fetched["rrule"] == "FREQ=WEEKLY;COUNT=3"
    assert fetched["alarms"][0]["minutes_before"] == 10
    assert fetched["attendees"][0]["email"] == "[configured account]"

    range_request = DateRangeInput(
        start="2026-08-01T00:00:00+08:00",
        end="2026-08-31T00:00:00+08:00",
        calendar=primary_id,
    )
    agenda = caldav_service.list_events(range_request)
    assert len(agenda["events"]) == 3
    assert {item["uid"] for item in agenda["events"]} == {uid}

    search = caldav_service.search_events(
        SearchEventsInput(query="launch plan", calendar=primary_id)
    )
    assert search["count"] == 1
    assert search["events"][0]["uid"] == uid

    updated = caldav_service.update_event(
        UpdateEventInput(uid=uid, calendar=primary_id, summary="Updated planning meeting")
    )
    assert updated["sequence"] == 1
    assert updated["changed_fields"] == ["summary"]

    with pytest.raises(ConflictError, match="changed since"):
        caldav_service.update_event(
            UpdateEventInput(
                uid=uid,
                calendar=primary_id,
                summary="Stale edit",
                expected_sequence=0,
            )
        )

    exception = caldav_service.update_event(
        UpdateEventInput(
            uid=uid,
            calendar=primary_id,
            recurrence_scope="single",
            recurrence_id="2026-08-10T09:00:00+08:00",
            start="2026-08-10T11:00:00+08:00",
            end="2026-08-10T12:00:00+08:00",
            summary="Moved occurrence",
        )
    )
    assert exception["created_exception"] is True
    assert exception["sequence"] == 2

    removed_occurrence = caldav_service.delete_event(
        DeleteEventInput(
            uid=uid,
            calendar=primary_id,
            recurrence_scope="single",
            recurrence_id="2026-08-17T09:00:00+08:00",
            confirm_uid=uid,
        )
    )
    assert removed_occurrence["scope"] == "single"
    assert removed_occurrence["sequence"] == 3

    busy = caldav_service.free_busy(range_request)
    assert busy["count"] >= 1
    slots = caldav_service.find_free_slots(
        FindFreeSlotsInput(
            start="2026-08-03T08:00:00+08:00",
            end="2026-08-03T12:00:00+08:00",
            duration_minutes=60,
            calendar_ids=[primary_id],
            day_start="08:00",
            day_end="12:00",
            granularity_minutes=60,
        )
    )
    assert all(slot["start"] != "2026-08-03T09:00:00+08:00" for slot in slots["slots"])

    second = caldav_service.create_calendar(CreateCalendarInput(name="Archive", color="#112233"))
    second_id = second["calendar_id"]
    calendar_update = caldav_service.update_calendar(
        UpdateCalendarInput(calendar=second_id, name="Archive 2", color="#334455")
    )
    assert calendar_update["changed_fields"] == ["name", "color"]
    moved = caldav_service.move_event(
        MoveEventInput(uid=uid, calendar=primary_id, destination_calendar=second_id)
    )
    assert moved["destination_calendar_id"] == second_id

    exported = caldav_service.export_ics(ExportICSInput(uid=uid, calendar=second_id))
    assert "BEGIN:VEVENT" in exported["ics"]
    assert "configured@example.com" not in exported["ics"].casefold()
    caldav_service.delete_event(DeleteEventInput(uid=uid, calendar=second_id, confirm_uid=uid))
    imported = caldav_service.import_ics(
        ImportICSInput(calendar=second_id, ics=exported["ics"], duplicate_policy="skip")
    )
    assert imported["created"] == 1

    idempotent_request = CreateEventInput(
        summary="Safe retry",
        start="2026-09-01T09:00:00+08:00",
        calendar=second_id,
        idempotency_key="integration-safe-retry",
    )
    first_retry = caldav_service.create_event(idempotent_request)
    second_retry = caldav_service.create_event(idempotent_request)
    assert first_retry["uid"] == second_retry["uid"]
    assert second_retry["idempotent_replay"] is True

    batch_request = BatchCreateInput(
        events=[
            CreateEventInput(
                summary="Batch event",
                start="2026-09-02T09:00:00+08:00",
                calendar=second_id,
                idempotency_key="batch-event-one",
            )
        ]
    )
    batch_preview = caldav_service.batch_create(batch_request)
    assert batch_preview["dry_run"] is True
    batch_created = caldav_service.batch_create(
        BatchCreateInput(events=batch_request.events, dry_run=False)
    )
    assert batch_created["count"] == 1

    preview = caldav_service.batch_delete(
        BatchDeleteInput(uids=[uid], confirm_uids=[uid], calendar=second_id)
    )
    assert preview["would_delete"] == [uid]
    deleted = caldav_service.batch_delete(
        BatchDeleteInput(uids=[uid], confirm_uids=[uid], calendar=second_id, dry_run=False)
    )
    assert deleted["deleted"][0]["uid"] == uid

    leftover_uids = [first_retry["uid"], batch_created["created"][0]["uid"]]
    caldav_service.batch_delete(
        BatchDeleteInput(
            uids=leftover_uids,
            confirm_uids=leftover_uids,
            calendar=second_id,
            dry_run=False,
        )
    )
    deleted_calendar = caldav_service.delete_calendar(
        DeleteCalendarInput(calendar=second_id, confirm_name="Archive 2")
    )
    assert deleted_calendar["deleted"] is True
