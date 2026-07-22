"""Strict LLM-facing request and result schemas."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import vRecur
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)


def _iso_value(value: str) -> str:
    from datetime import date, datetime

    try:
        if "T" in value or " " in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("must be an ISO 8601 date or datetime") from exc
    return value


def _iana_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("must be a valid IANA timezone such as America/New_York") from exc
    return value


def _rrule(value: str) -> str:
    normalized = value.strip().upper()
    if any(char in normalized for char in "\r\n"):
        raise ValueError("RRULE must contain only the rule value, not an ICS property")
    try:
        parsed = vRecur.from_ical(normalized)
    except Exception as exc:
        raise ValueError("must be a valid RFC 5545 recurrence rule") from exc
    if "FREQ" not in parsed:
        raise ValueError("RRULE must include FREQ")
    return normalized


IsoValue = Annotated[str, AfterValidator(_iso_value)]
TimezoneName = Annotated[str, AfterValidator(_iana_timezone)]
RRuleValue = Annotated[str, AfterValidator(_rrule)]


class StrictModel(BaseModel):
    """Reject unknown fields so agent mistakes fail visibly."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CalendarSelector(StrictModel):
    """Select a calendar by returned opaque ID or exact display name."""

    calendar: str | None = Field(
        default=None,
        description="Opaque calendar_id from list_calendars, or an exact calendar display name.",
    )


class AlarmInput(StrictModel):
    """A reminder relative to event start."""

    minutes_before: int = Field(ge=0, le=525_600, description="Minutes before DTSTART.")
    action: Literal["DISPLAY", "EMAIL", "AUDIO"] = "DISPLAY"
    description: str | None = Field(default=None, max_length=1000)


class AttendeeInput(StrictModel):
    """An RFC 5545 calendar attendee."""

    email: EmailStr
    name: str | None = Field(default=None, max_length=200)
    role: Literal["REQ-PARTICIPANT", "OPT-PARTICIPANT", "NON-PARTICIPANT", "CHAIR"] = (
        "REQ-PARTICIPANT"
    )
    partstat: Literal["NEEDS-ACTION", "ACCEPTED", "DECLINED", "TENTATIVE", "DELEGATED"] = (
        "NEEDS-ACTION"
    )
    rsvp: bool = False


class OrganizerInput(StrictModel):
    """An RFC 5545 organizer identity."""

    email: EmailStr
    name: str | None = Field(default=None, max_length=200)


class CreateEventInput(StrictModel):
    """Create a timed or all-day event with optional recurrence and collaboration metadata."""

    summary: str = Field(min_length=1, max_length=1000)
    start: IsoValue = Field(description="ISO date for all-day, otherwise ISO datetime.")
    end: IsoValue | None = Field(
        default=None,
        description="Exclusive ISO end. Defaults to next day (all-day) or one hour (timed).",
    )
    all_day: bool = False
    timezone: TimezoneName | None = Field(
        default=None,
        description="IANA TZID for naive timed values; defaults to server DEFAULT_TIMEZONE.",
    )
    calendar: str | None = Field(
        default=None, description="calendar_id or exact name; omit for the first writable calendar."
    )
    location: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=50_000)
    url: str | None = Field(default=None, max_length=4000)
    rrule: RRuleValue | None = Field(
        default=None, description="RFC 5545 rule value, e.g. FREQ=WEEKLY;BYDAY=MO;COUNT=6."
    )
    alarms: list[AlarmInput] = Field(default_factory=list, max_length=20)
    attendees: list[AttendeeInput] = Field(default_factory=list, max_length=200)
    organizer: OrganizerInput | None = None
    categories: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["TENTATIVE", "CONFIRMED", "CANCELLED"] = "CONFIRMED"
    transparency: Literal["OPAQUE", "TRANSPARENT"] = "OPAQUE"
    conflict_policy: Literal["warn", "allow", "reject"] = "warn"
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Stable caller key; retries with the same key return the original event.",
    )

    @model_validator(mode="after")
    def validate_date_shapes(self) -> CreateEventInput:
        start_is_date = "T" not in self.start and " " not in self.start
        if self.all_day != start_is_date:
            expected = "date without time" if self.all_day else "datetime including time"
            raise ValueError(f"start must be an ISO {expected} when all_day={self.all_day}")
        if self.end is not None:
            end_is_date = "T" not in self.end and " " not in self.end
            if self.all_day != end_is_date:
                raise ValueError("start and end must both be dates or both be datetimes")
        return self


class GetEventInput(CalendarSelector):
    """Find one event by UID, optionally disambiguated by calendar."""

    uid: str = Field(min_length=1, max_length=1024)


class DateRangeInput(CalendarSelector):
    """Bounded event query in an inclusive-start, exclusive-end time window."""

    start: IsoValue
    end: IsoValue
    timezone: TimezoneName | None = None
    limit: int = Field(default=500, ge=1, le=2000)


class SearchEventsInput(CalendarSelector):
    """Combine free text, date window, and calendar filtering."""

    query: str | None = Field(default=None, min_length=1, max_length=1000)
    start: IsoValue | None = None
    end: IsoValue | None = None
    timezone: TimezoneName | None = None
    limit: int = Field(default=200, ge=1, le=2000)

    @model_validator(mode="after")
    def require_filter(self) -> SearchEventsInput:
        if self.query is None and self.start is None and self.end is None and self.calendar is None:
            raise ValueError("provide query, date range, or calendar")
        if (self.start is None) != (self.end is None):
            raise ValueError("start and end must be supplied together")
        return self


class UpdateEventInput(GetEventInput):
    """Patch an event; omitted fields stay unchanged and explicit null clears nullable fields."""

    recurrence_scope: Literal["whole", "single"] = "whole"
    recurrence_id: IsoValue | None = Field(
        default=None, description="Original occurrence DTSTART; required for single scope."
    )
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    start: IsoValue | None = None
    end: IsoValue | None = None
    all_day: bool | None = None
    timezone: TimezoneName | None = None
    location: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=50_000)
    url: str | None = Field(default=None, max_length=4000)
    rrule: RRuleValue | None = None
    alarms: list[AlarmInput] | None = Field(default=None, max_length=20)
    attendees: list[AttendeeInput] | None = Field(default=None, max_length=200)
    organizer: OrganizerInput | None = None
    categories: list[str] | None = Field(default=None, max_length=100)
    status: Literal["TENTATIVE", "CONFIRMED", "CANCELLED"] | None = None
    transparency: Literal["OPAQUE", "TRANSPARENT"] | None = None
    conflict_policy: Literal["warn", "allow", "reject"] = "warn"
    expected_sequence: int | None = Field(
        default=None,
        ge=0,
        description="Reject if the stored SEQUENCE differs, preventing lost concurrent updates.",
    )

    @model_validator(mode="after")
    def validate_scope(self) -> UpdateEventInput:
        if self.recurrence_scope == "single" and self.recurrence_id is None:
            raise ValueError("recurrence_id is required for single-occurrence updates")
        return self


class DeleteEventInput(GetEventInput):
    """Delete a whole event resource or exclude one recurrence occurrence."""

    recurrence_scope: Literal["whole", "single"] = "whole"
    recurrence_id: IsoValue | None = None
    confirm_uid: str = Field(description="Must exactly equal uid to prevent accidental deletion.")

    @model_validator(mode="after")
    def validate_confirmation(self) -> DeleteEventInput:
        if self.confirm_uid != self.uid:
            raise ValueError("confirm_uid must exactly match uid")
        if self.recurrence_scope == "single" and self.recurrence_id is None:
            raise ValueError("recurrence_id is required for a single-occurrence delete")
        return self


class MoveEventInput(GetEventInput):
    """Move an entire event resource between calendars while retaining its UID."""

    destination_calendar: str = Field(min_length=1)


class RespondInviteInput(GetEventInput):
    """Set PARTSTAT for one attendee on an invitation."""

    response: Literal["ACCEPTED", "DECLINED", "TENTATIVE"]
    attendee_email: EmailStr | None = Field(
        default=None,
        description="Attendee to update; omit to use configured Apple ID without returning it.",
    )
    recurrence_scope: Literal["whole", "single"] = "whole"
    recurrence_id: IsoValue | None = None


class FindFreeSlotsInput(DateRangeInput):
    """Find open slots after combining busy intervals from selected calendars."""

    duration_minutes: int = Field(ge=1, le=10_080)
    calendar_ids: list[str] | None = Field(
        default=None, description="Calendars to combine; omit for every discovered calendar."
    )
    day_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    day_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    granularity_minutes: int = Field(default=15, ge=1, le=1440)


class QuickAddInput(StrictModel):
    """Parse and optionally create a conservative natural-language event."""

    text: str = Field(min_length=3, max_length=2000)
    timezone: TimezoneName | None = None
    calendar: str | None = None
    now: IsoValue | None = Field(
        default=None, description="Override reference time for deterministic previews/tests."
    )
    default_duration_minutes: int = Field(default=60, ge=1, le=10_080)
    preview_only: bool = False
    conflict_policy: Literal["warn", "allow", "reject"] = "warn"


class CreateCalendarInput(StrictModel):
    """Create a calendar collection."""

    name: str = Field(min_length=1, max_length=255)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


class UpdateCalendarInput(CalendarSelector):
    """Rename and/or recolor an existing calendar."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")

    @model_validator(mode="after")
    def require_change(self) -> UpdateCalendarInput:
        if self.name is None and self.color is None:
            raise ValueError("provide name and/or color")
        return self


class DeleteCalendarInput(CalendarSelector):
    """Delete a calendar and all of its resources after exact-name confirmation."""

    confirm_name: str = Field(min_length=1)


class ExportICSInput(CalendarSelector):
    """Export one UID or a bounded calendar window as standards-compliant ICS."""

    uid: str | None = None
    start: IsoValue | None = None
    end: IsoValue | None = None


class ImportICSInput(CalendarSelector):
    """Import VEVENT resources from an ICS payload with an explicit duplicate policy."""

    ics: str = Field(min_length=20, max_length=5_000_000)
    duplicate_policy: Literal["skip", "replace", "new_uid"] = "skip"
    dry_run: bool = False


class BatchDeleteInput(StrictModel):
    """Delete multiple whole event resources with explicit UID confirmation."""

    uids: list[str] = Field(min_length=1, max_length=200)
    calendar: str | None = None
    confirm_uids: list[str] = Field(min_length=1, max_length=200)
    dry_run: bool = True

    @model_validator(mode="after")
    def validate_confirmations(self) -> BatchDeleteInput:
        if sorted(set(self.uids)) != sorted(set(self.confirm_uids)):
            raise ValueError("confirm_uids must contain exactly the requested uids")
        return self


class BatchCreateInput(StrictModel):
    """Create multiple independent events with per-item results."""

    events: list[CreateEventInput] = Field(min_length=1, max_length=100)
    dry_run: bool = True


HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
