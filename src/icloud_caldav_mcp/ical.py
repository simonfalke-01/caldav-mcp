"""RFC 5545 construction, parsing, and temporal normalization."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event, vCalAddress, vRecur

from .errors import ValidationError
from .models import AlarmInput, AttendeeInput, CreateEventInput, OrganizerInput

PRODID = "-//caldav-mcp//iCloud Calendar MCP//EN"


def parse_iso(value: str) -> date | datetime:
    """Parse the validated model representation into a temporal object."""

    if "T" in value or " " in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return date.fromisoformat(value)


def parse_datetime(value: str, timezone_name: str) -> datetime:
    """Parse a date/datetime for a query boundary, applying a timezone when naive."""

    parsed = parse_iso(value)
    zone = ZoneInfo(timezone_name)
    if isinstance(parsed, datetime):
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed
    return datetime.combine(parsed, time.min, zone)


def normalize_event_times(
    start_value: str,
    end_value: str | None,
    all_day: bool,
    timezone_name: str,
) -> tuple[date | datetime, date | datetime]:
    """Normalize event bounds and enforce exclusive, strictly increasing ends."""

    start = parse_iso(start_value)
    if all_day:
        if isinstance(start, datetime):
            raise ValidationError("all-day start must be an ISO date without a time")
        end = parse_iso(end_value) if end_value else start + timedelta(days=1)
        if isinstance(end, datetime):
            raise ValidationError("all-day end must be an ISO date without a time")
    else:
        if not isinstance(start, datetime):
            raise ValidationError("timed start must include a time")
        zone = ZoneInfo(timezone_name)
        start = start.replace(tzinfo=zone) if start.tzinfo is None else start.astimezone(zone)
        if end_value:
            end = parse_iso(end_value)
            if not isinstance(end, datetime):
                raise ValidationError("timed end must include a time")
            end = end.replace(tzinfo=zone) if end.tzinfo is None else end.astimezone(zone)
        else:
            end = start + timedelta(hours=1)
    if end <= start:
        raise ValidationError("event end must be later than start")
    return start, end


def new_calendar() -> Calendar:
    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    return calendar


def _add_attendee(event: Event, attendee: AttendeeInput) -> None:
    address = vCalAddress(f"mailto:{attendee.email}")
    if attendee.name:
        address.params["CN"] = attendee.name
    address.params["ROLE"] = attendee.role
    address.params["PARTSTAT"] = attendee.partstat
    address.params["RSVP"] = "TRUE" if attendee.rsvp else "FALSE"
    event.add("attendee", address, encode=0)


def _add_organizer(event: Event, organizer: OrganizerInput) -> None:
    address = vCalAddress(f"mailto:{organizer.email}")
    if organizer.name:
        address.params["CN"] = organizer.name
    event.add("organizer", address, encode=0)


def _add_alarm(event: Event, alarm_input: AlarmInput, summary: str) -> None:
    alarm = Alarm()
    alarm.add("action", alarm_input.action)
    alarm.add("trigger", timedelta(minutes=-alarm_input.minutes_before))
    if alarm_input.action in {"DISPLAY", "EMAIL"}:
        alarm.add("description", alarm_input.description or summary)
    if alarm_input.action == "EMAIL":
        alarm.add("summary", summary)
    event.add_component(alarm)


def build_event(
    request: CreateEventInput,
    default_timezone: str,
    *,
    uid: str | None = None,
    now: datetime | None = None,
) -> tuple[Calendar, str]:
    """Build a complete VCALENDAR with one validated VEVENT."""

    timezone_name = request.timezone or default_timezone
    start, end = normalize_event_times(request.start, request.end, request.all_day, timezone_name)
    event_uid = uid or str(uuid4())
    current = now or datetime.now(UTC)

    event = Event()
    event.add("uid", event_uid)
    event.add("summary", request.summary)
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("dtstamp", current)
    event.add("created", current)
    event.add("last-modified", current)
    event.add("sequence", 0)
    event.add("status", request.status)
    event.add("transp", request.transparency)
    if request.location:
        event.add("location", request.location)
    if request.description:
        event.add("description", request.description)
    if request.url:
        event.add("url", request.url)
    if request.rrule:
        event.add("rrule", vRecur.from_ical(request.rrule))
    if request.categories:
        event.add("categories", request.categories)
    if request.organizer:
        _add_organizer(event, request.organizer)
    for attendee in request.attendees:
        _add_attendee(event, attendee)
    for alarm in request.alarms:
        _add_alarm(event, alarm, request.summary)

    calendar = new_calendar()
    calendar.add_component(event)
    return calendar, event_uid


def _decoded(component: Event, name: str) -> Any | None:
    try:
        return component.decoded(name)
    except (KeyError, ValueError, TypeError):
        return None


def _serialize_temporal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _timezone_name(component: Event) -> str | None:
    prop = component.get("DTSTART")
    if prop is None:
        return None
    tzid = prop.params.get("TZID")
    if tzid:
        return str(tzid)
    dt = getattr(prop, "dt", None)
    tzinfo = getattr(dt, "tzinfo", None)
    return getattr(tzinfo, "key", None) or (str(tzinfo) if tzinfo else None)


def _rrule_text(component: Event) -> str | None:
    value = component.get("RRULE")
    if value is None:
        return None
    encoded = value.to_ical() if hasattr(value, "to_ical") else str(value).encode()
    return encoded.decode()


def _property_values(component: Event, name: str) -> list[Any]:
    value = component.get(name)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _calendar_addresses(
    component: Event, configured_username: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    def address_dict(value: Any) -> dict[str, Any]:
        raw = str(value)
        email = raw.removeprefix("mailto:").removeprefix("MAILTO:")
        if configured_username and email.casefold() == configured_username.casefold():
            email = "[configured account]"
        params = getattr(value, "params", {})
        return {
            "email": email,
            "name": str(params.get("CN")) if params.get("CN") else None,
            "role": str(params.get("ROLE", "REQ-PARTICIPANT")),
            "partstat": str(params.get("PARTSTAT", "NEEDS-ACTION")),
            "rsvp": str(params.get("RSVP", "FALSE")).upper() == "TRUE",
        }

    attendees = [address_dict(value) for value in _property_values(component, "ATTENDEE")]
    organizer_value = component.get("ORGANIZER")
    organizer = address_dict(organizer_value) if organizer_value else None
    return attendees, organizer


def _alarms(component: Event) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for alarm in component.subcomponents:
        if alarm.name != "VALARM":
            continue
        trigger = _decoded(alarm, "TRIGGER")  # type: ignore[arg-type]
        minutes_before: int | None = None
        absolute: str | None = None
        if isinstance(trigger, timedelta):
            minutes_before = int(-trigger.total_seconds() // 60)
        elif isinstance(trigger, datetime):
            absolute = trigger.isoformat()
        results.append(
            {
                "action": str(alarm.get("ACTION", "DISPLAY")),
                "minutes_before": minutes_before,
                "absolute_trigger": absolute,
                "description": str(alarm.get("DESCRIPTION"))
                if alarm.get("DESCRIPTION") is not None
                else None,
            }
        )
    return results


def event_component_to_dict(
    component: Event,
    *,
    calendar_id: str,
    calendar_name: str,
    configured_username: str | None = None,
) -> dict[str, Any]:
    """Convert a VEVENT into a stable, JSON-serializable tool result."""

    start = _decoded(component, "DTSTART")
    end = _decoded(component, "DTEND")
    if end is None and start is not None:
        duration = _decoded(component, "DURATION")
        if isinstance(duration, timedelta):
            end = start + duration
    all_day = isinstance(start, date) and not isinstance(start, datetime)
    attendees, organizer = _calendar_addresses(component, configured_username)
    categories: list[str] = []
    for value in _property_values(component, "CATEGORIES"):
        cats = getattr(value, "cats", None)
        categories.extend(str(cat) for cat in cats) if cats else categories.append(str(value))
    sequence = _decoded(component, "SEQUENCE")
    return {
        "uid": str(component.get("UID", "")),
        "summary": str(component.get("SUMMARY", "")),
        "start": _serialize_temporal(start),
        "end": _serialize_temporal(end),
        "all_day": all_day,
        "timezone": None if all_day else _timezone_name(component),
        "location": str(component.get("LOCATION")) if component.get("LOCATION") else None,
        "description": str(component.get("DESCRIPTION")) if component.get("DESCRIPTION") else None,
        "url": str(component.get("URL")) if component.get("URL") else None,
        "rrule": _rrule_text(component),
        "recurrence_id": _serialize_temporal(_decoded(component, "RECURRENCE-ID")),
        "sequence": int(sequence or 0),
        "status": str(component.get("STATUS", "CONFIRMED")),
        "transparency": str(component.get("TRANSP", "OPAQUE")),
        "categories": sorted(set(categories)),
        "alarms": _alarms(component),
        "attendees": attendees,
        "organizer": organizer,
        "calendar_id": calendar_id,
        "calendar_name": calendar_name,
        "last_modified": _serialize_temporal(_decoded(component, "LAST-MODIFIED")),
    }


def first_event(calendar: Calendar) -> Event:
    """Return the first VEVENT, preferring the recurrence master."""

    events = [component for component in calendar.walk("VEVENT") if isinstance(component, Event)]
    if not events:
        raise ValidationError("ICS resource does not contain a VEVENT")
    return next((event for event in events if event.get("RECURRENCE-ID") is None), events[0])


def recurrence_key(value: date | datetime) -> str:
    """Normalize recurrence values for component matching."""

    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC).isoformat()
    return value.isoformat()


def component_interval(component: Event, fallback_zone: str) -> tuple[datetime, datetime]:
    """Return an aware interval for conflict and availability calculations."""

    start = _decoded(component, "DTSTART")
    end = _decoded(component, "DTEND")
    if start is None:
        raise ValidationError("event has no DTSTART")
    if end is None:
        duration = _decoded(component, "DURATION") or (
            timedelta(days=1)
            if isinstance(start, date) and not isinstance(start, datetime)
            else timedelta(hours=1)
        )
        end = start + duration
    zone = ZoneInfo(fallback_zone)

    def aware(value: date | datetime) -> datetime:
        if isinstance(value, datetime):
            return value.replace(tzinfo=zone) if value.tzinfo is None else value
        return datetime.combine(value, time.min, zone)

    return aware(start), aware(end)
