"""Synchronous CalDAV application service with safe structured results."""

from __future__ import annotations

import copy
import hashlib
import re
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, TypeVar, cast
from urllib.parse import unquote, urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

import caldav
import recurring_ical_events
from caldav import DAVClient
from caldav import error as caldav_error  # type: ignore[attr-defined]
from caldav.elements import dav as caldav_dav
from caldav.elements import ical as caldav_ical
from icalendar import Alarm, vCalAddress, vRecur
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

from .config import Settings
from .errors import BackendError, ConflictError, NotFoundError, ValidationError
from .ical import (
    build_event,
    component_interval,
    event_component_to_dict,
    first_event,
    new_calendar,
    normalize_event_times,
    parse_datetime,
    parse_iso,
    recurrence_key,
)
from .logging import get_logger
from .models import (
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
    RespondInviteInput,
    SearchEventsInput,
    UpdateCalendarInput,
    UpdateEventInput,
)

T = TypeVar("T")


class CalendarService:
    """Owns discovery and all mutations for one configured CalDAV account."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = DAVClient(
            url=settings.caldav_url,
            username=settings.icloud_username.get_secret_value(),
            password=settings.icloud_app_password.get_secret_value(),
            timeout=30,
        )
        self._lock = threading.RLock()
        self._log = get_logger()

    def close(self) -> None:
        with self._lock:
            self._client.close()

    @staticmethod
    def _calendar_id(calendar: caldav.Calendar) -> str:
        return hashlib.sha256(str(calendar.url).encode()).hexdigest()[:20]

    @staticmethod
    def _safe_calendar_name(calendar: caldav.Calendar) -> str:
        try:
            name = calendar.get_display_name()
        except Exception:
            name = None
        if name:
            return str(name)
        path_name = unquote(urlparse(str(calendar.url)).path.rstrip("/").split("/")[-1])
        return path_name or "Unnamed calendar"

    @staticmethod
    def _calendar_color(calendar: caldav.Calendar) -> str | None:
        try:
            value = calendar.get_property(caldav_ical.CalendarColor(), use_cached=False)
            return str(value) if value else None
        except Exception:
            return None

    def _principal(self) -> caldav.Principal:
        return cast(caldav.Principal, self._client.principal())

    def _calendars(self) -> list[caldav.Calendar]:
        return list(self._principal().calendars())

    def _calendar_summary(self, calendar: caldav.Calendar) -> dict[str, Any]:
        return {
            "calendar_id": self._calendar_id(calendar),
            "name": self._safe_calendar_name(calendar),
            "color": self._calendar_color(calendar),
        }

    def _resolve_calendar(
        self, selector: str | None, *, calendars: list[caldav.Calendar] | None = None
    ) -> caldav.Calendar:
        available = calendars if calendars is not None else self._calendars()
        if not available:
            raise NotFoundError("No calendars are available for the configured account.")
        if selector is None:
            return available[0]
        matches = [
            calendar
            for calendar in available
            if self._calendar_id(calendar) == selector
            or self._safe_calendar_name(calendar) == selector
        ]
        if not matches:
            raise NotFoundError(
                "Calendar not found. Call list_calendars and pass a returned calendar_id."
            )
        if len(matches) > 1:
            raise ConflictError("Calendar name is ambiguous; use the returned calendar_id.")
        return matches[0]

    def sanitize_result(self, value: Any) -> Any:
        """Recursively remove configured credentials from every tool-bound value."""

        if isinstance(value, dict):
            return {key: self.sanitize_result(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.sanitize_result(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.sanitize_result(item) for item in value)
        if not isinstance(value, str):
            return value
        replacements = (
            (self.settings.mcp_api_key.get_secret_value(), "[REDACTED]"),
            (self.settings.icloud_app_password.get_secret_value(), "[REDACTED]"),
            (
                self.settings.icloud_username.get_secret_value(),
                "redacted-account@example.invalid",
            ),
        )
        result = value
        for secret, replacement in replacements:
            if secret:
                result = re.sub(re.escape(secret), replacement, result, flags=re.IGNORECASE)
        return result

    def _safe_call(self, operation: str, callback: Callable[[], T]) -> T:
        try:
            return cast(T, self.sanitize_result(callback()))
        except (NotFoundError, ConflictError, ValidationError):
            raise
        except caldav_error.AuthorizationError:
            raise BackendError(
                "CalDAV authentication failed. Verify the Apple ID and app-specific password."
            ) from None
        except caldav_error.NotFoundError:
            raise NotFoundError("The requested CalDAV resource was not found.") from None
        except (caldav_error.DAVError, OSError, TimeoutError):
            self._log.warning("caldav_operation_failed", operation=operation)
            raise BackendError(
                f"CalDAV could not complete {operation}. Retry; if it persists, "
                "verify account access."
            ) from None
        except Exception as exc:
            self._log.error(
                "unexpected_caldav_failure", operation=operation, error_type=type(exc).__name__
            )
            raise BackendError(f"Calendar backend failed while attempting {operation}.") from None

    def list_calendars(self) -> dict[str, Any]:
        """Discover principal → calendar-home-set → calendars, including Apple redirects."""

        with self._lock:
            calendars = self._safe_call("calendar discovery", self._calendars)
            results = [self._calendar_summary(calendar) for calendar in calendars]
            return {"calendars": results, "count": len(results)}

    def _find_event(
        self, uid: str, calendar_selector: str | None
    ) -> tuple[caldav.Event, caldav.Calendar]:
        calendars = self._calendars()
        candidates = (
            [self._resolve_calendar(calendar_selector, calendars=calendars)]
            if calendar_selector
            else calendars
        )
        matches: list[tuple[caldav.Event, caldav.Calendar]] = []
        for calendar in candidates:
            try:
                resource = cast(caldav.Event, calendar.event_by_uid(uid))
                resource.load()
                matches.append((resource, calendar))
            except caldav_error.NotFoundError:
                continue
        if not matches:
            raise NotFoundError("Event UID was not found in the selected calendar scope.")
        if len(matches) > 1:
            raise ConflictError("Event UID exists in multiple calendars; provide calendar_id.")
        return matches[0]

    def get_event(self, request: GetEventInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                resource, calendar = self._find_event(request.uid, request.calendar)
                component = first_event(resource.icalendar_instance)
                return event_component_to_dict(
                    component,
                    calendar_id=self._calendar_id(calendar),
                    calendar_name=self._safe_calendar_name(calendar),
                    configured_username=self.settings.icloud_username.get_secret_value(),
                )

            return self._safe_call("event lookup", work)

    def _query_resources(
        self,
        calendar: caldav.Calendar,
        start: datetime | None,
        end: datetime | None,
    ) -> list[caldav.Event]:
        if start is not None and end is not None:
            try:
                return list(calendar.search(start=start, end=end, event=True, expand=True))
            except caldav_error.AuthorizationError:
                raise
            except (caldav_error.ReportError, caldav_error.ResponseError):
                self._log.info(
                    "server_recurrence_expansion_unsupported",
                    calendar_id=self._calendar_id(calendar),
                )
                return list(calendar.search(start=start, end=end, event=True, expand=False))
        return list(calendar.events())

    def _range_components(
        self,
        instance: ICalendar,
        start: datetime | None,
        end: datetime | None,
        calendar_id: str,
    ) -> list[IEvent]:
        raw = [component for component in instance.walk("VEVENT") if isinstance(component, IEvent)]
        if start is None or end is None:
            return raw
        try:
            return [
                component
                for component in recurring_ical_events.of(instance).between(start, end)
                if isinstance(component, IEvent)
            ]
        except Exception:
            self._log.warning("local_recurrence_expansion_failed", calendar_id=calendar_id)
            overlapping: list[IEvent] = []
            for component in raw:
                try:
                    item_start, item_end = component_interval(
                        component, self.settings.default_timezone
                    )
                except ValidationError:
                    continue
                if item_start < end and item_end > start:
                    overlapping.append(component)
            return overlapping

    def _events(
        self,
        *,
        calendar_selector: str | None,
        start: datetime | None,
        end: datetime | None,
        query: str | None,
        limit: int,
    ) -> dict[str, Any]:
        calendars = self._calendars()
        selected = (
            [self._resolve_calendar(calendar_selector, calendars=calendars)]
            if calendar_selector
            else calendars
        )
        needle = query.casefold() if query else None
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None, str | None, str]] = set()
        total_matching = 0
        for calendar in selected:
            calendar_id = self._calendar_id(calendar)
            calendar_name = self._safe_calendar_name(calendar)
            for resource in self._query_resources(calendar, start, end):
                try:
                    instance = resource.icalendar_instance
                except Exception:
                    self._log.warning(
                        "skipping_malformed_calendar_resource", calendar_id=calendar_id
                    )
                    continue
                for raw_component in self._range_components(instance, start, end, calendar_id):
                    item = event_component_to_dict(
                        raw_component,
                        calendar_id=calendar_id,
                        calendar_name=calendar_name,
                        configured_username=self.settings.icloud_username.get_secret_value(),
                    )
                    if needle:
                        haystack = "\n".join(
                            str(item.get(field) or "")
                            for field in ("summary", "description", "location", "categories")
                        ).casefold()
                        if needle not in haystack:
                            continue
                    key = (
                        item["uid"],
                        item["recurrence_id"],
                        item["start"],
                        calendar_id,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    total_matching += 1
                    if len(results) < limit:
                        results.append(item)
        results.sort(key=lambda item: (item.get("start") or "", item.get("summary") or ""))
        return {
            "events": results,
            "count": len(results),
            "truncated": total_matching > len(results),
            "limit": limit,
        }

    def list_events(self, request: DateRangeInput) -> dict[str, Any]:
        with self._lock:
            zone = request.timezone or self.settings.default_timezone
            start = parse_datetime(request.start, zone)
            end = parse_datetime(request.end, zone)
            if end <= start:
                raise ValidationError("range end must be later than start")
            return self._safe_call(
                "event range query",
                lambda: self._events(
                    calendar_selector=request.calendar,
                    start=start,
                    end=end,
                    query=None,
                    limit=request.limit,
                ),
            )

    def search_events(self, request: SearchEventsInput) -> dict[str, Any]:
        with self._lock:
            zone = request.timezone or self.settings.default_timezone
            start = parse_datetime(request.start, zone) if request.start else None
            end = parse_datetime(request.end, zone) if request.end else None
            if start is not None and end is not None and end <= start:
                raise ValidationError("range end must be later than start")
            return self._safe_call(
                "event search",
                lambda: self._events(
                    calendar_selector=request.calendar,
                    start=start,
                    end=end,
                    query=request.query,
                    limit=request.limit,
                ),
            )

    @staticmethod
    def _dict_interval(item: dict[str, Any], zone_name: str) -> tuple[datetime, datetime]:
        zone = ZoneInfo(zone_name)

        def convert(value: str) -> datetime:
            parsed = parse_iso(value)
            if isinstance(parsed, datetime):
                return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed
            return datetime.combine(parsed, time.min, zone)

        return convert(item["start"]), convert(item["end"])

    def _find_conflicts(
        self,
        calendar: caldav.Calendar,
        start: date | datetime,
        end: date | datetime,
        *,
        exclude_uid: str | None = None,
    ) -> list[dict[str, Any]]:
        zone_name = self.settings.default_timezone
        zone = ZoneInfo(zone_name)

        def aware(value: date | datetime) -> datetime:
            if isinstance(value, datetime):
                return value.replace(tzinfo=zone) if value.tzinfo is None else value
            return datetime.combine(value, time.min, zone)

        range_start, range_end = aware(start), aware(end)
        result = self._events(
            calendar_selector=self._calendar_id(calendar),
            start=range_start,
            end=range_end,
            query=None,
            limit=2000,
        )
        conflicts = []
        for item in result["events"]:
            if exclude_uid and item["uid"] == exclude_uid:
                continue
            item_start, item_end = self._dict_interval(item, zone_name)
            if (
                item_start < range_end
                and item_end > range_start
                and item["transparency"] != "TRANSPARENT"
            ):
                conflicts.append(
                    {
                        "uid": item["uid"],
                        "summary": item["summary"],
                        "start": item["start"],
                        "end": item["end"],
                    }
                )
        return conflicts

    @staticmethod
    def _event_uid(request: CreateEventInput) -> str:
        return (
            str(uuid5(NAMESPACE_URL, f"caldav-mcp:{request.idempotency_key}"))
            if request.idempotency_key
            else str(uuid4())
        )

    @staticmethod
    def _save_new_event(calendar: caldav.Calendar, data: bytes | str) -> caldav.Event:
        """Create while preserving the supplied RFC 5545 SEQUENCE.

        caldav 2.2.6 increments SEQUENCE even when `increase_seqno=False`; pre-decrementing the
        in-memory value compensates without persisting an invalid revision.
        """

        instance = ICalendar.from_ical(data)
        master = first_event(instance)
        if master.get("SEQUENCE") is not None:
            sequence = int(master.decoded("SEQUENCE", 0))
            master.pop("SEQUENCE", None)
            master.add("sequence", sequence - 1)
        resource = caldav.Event(client=calendar.client, data=instance, parent=calendar)
        return resource.save(no_overwrite=True, increase_seqno=False)

    def create_event(self, request: CreateEventInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                calendar = self._resolve_calendar(request.calendar)
                uid = self._event_uid(request)
                if request.idempotency_key:
                    try:
                        existing = cast(caldav.Event, calendar.event_by_uid(uid))
                        existing.load()
                        return {
                            "created": False,
                            "idempotent_replay": True,
                            "uid": uid,
                            "calendar_id": self._calendar_id(calendar),
                            "calendar_name": self._safe_calendar_name(calendar),
                            "conflicts": [],
                        }
                    except caldav_error.NotFoundError:
                        pass
                instance, uid = build_event(request, self.settings.default_timezone, uid=uid)
                component = first_event(instance)
                start, end = component_interval(component, self.settings.default_timezone)
                conflicts = self._find_conflicts(calendar, start, end)
                if conflicts and request.conflict_policy == "reject":
                    raise ConflictError(
                        f"Event overlaps {len(conflicts)} existing event(s); use "
                        "conflict_policy='warn' "
                        "or 'allow' to create it intentionally."
                    )
                self._save_new_event(calendar, instance.to_ical())
                return {
                    "created": True,
                    "idempotent_replay": False,
                    "uid": uid,
                    "calendar_id": self._calendar_id(calendar),
                    "calendar_name": self._safe_calendar_name(calendar),
                    "conflicts": conflicts if request.conflict_policy == "warn" else [],
                }

            return self._safe_call("event creation", work)

    @staticmethod
    def _replace_property(component: IEvent, name: str, value: Any | None) -> None:
        component.pop(name, None)
        if value is not None and value != []:
            component.add(name.lower(), value)

    @staticmethod
    def _replace_alarms(component: IEvent, alarms: list[Any], summary: str) -> None:
        component.subcomponents = [
            part for part in component.subcomponents if part.name != "VALARM"
        ]
        for alarm_input in alarms:
            alarm = Alarm()
            alarm.add("action", alarm_input.action)
            alarm.add("trigger", timedelta(minutes=-alarm_input.minutes_before))
            if alarm_input.action in {"DISPLAY", "EMAIL"}:
                alarm.add("description", alarm_input.description or summary)
            if alarm_input.action == "EMAIL":
                alarm.add("summary", summary)
            component.add_component(alarm)

    @staticmethod
    def _replace_attendees(component: IEvent, attendees: list[Any]) -> None:
        component.pop("ATTENDEE", None)
        for attendee in attendees:
            address = vCalAddress(f"mailto:{attendee.email}")
            if attendee.name:
                address.params["CN"] = attendee.name
            address.params["ROLE"] = attendee.role
            address.params["PARTSTAT"] = attendee.partstat
            address.params["RSVP"] = "TRUE" if attendee.rsvp else "FALSE"
            component.add("attendee", address, encode=0)

    @staticmethod
    def _replace_organizer(component: IEvent, organizer: Any | None) -> None:
        component.pop("ORGANIZER", None)
        if organizer:
            address = vCalAddress(f"mailto:{organizer.email}")
            if organizer.name:
                address.params["CN"] = organizer.name
            component.add("organizer", address, encode=0)

    @staticmethod
    def _component_by_recurrence(
        instance: ICalendar, recurrence_id: str
    ) -> tuple[IEvent, IEvent, bool]:
        master = first_event(instance)
        raw_recurrence = parse_iso(recurrence_id)
        target_key = recurrence_key(raw_recurrence)
        for component in instance.walk("VEVENT"):
            if not isinstance(component, IEvent):
                continue
            current = component.decoded("RECURRENCE-ID", None)
            if current is not None and recurrence_key(current) == target_key:
                return component, master, False

        component = copy.deepcopy(master)
        for property_name in ("RRULE", "RDATE", "EXDATE"):
            component.pop(property_name, None)
        original_start = master.decoded("DTSTART")
        original_end = master.decoded("DTEND", None)
        duration = (
            original_end - original_start
            if original_end is not None
            else master.decoded("DURATION", timedelta(hours=1))
        )
        recurrence_value: date | datetime = raw_recurrence
        if (
            isinstance(original_start, datetime)
            and isinstance(raw_recurrence, datetime)
            and raw_recurrence.tzinfo is None
            and original_start.tzinfo is not None
        ):
            recurrence_value = raw_recurrence.replace(tzinfo=original_start.tzinfo)
        component.pop("DTSTART", None)
        component.pop("DTEND", None)
        component.pop("DURATION", None)
        component.add("recurrence-id", recurrence_value)
        component.add("dtstart", recurrence_value)
        component.add("dtend", recurrence_value + duration)
        instance.add_component(component)
        return component, master, True

    def _apply_patch(self, component: IEvent, request: UpdateEventInput) -> list[str]:
        supplied = request.model_fields_set
        changed: list[str] = []
        scalar_properties = {
            "summary": "SUMMARY",
            "location": "LOCATION",
            "description": "DESCRIPTION",
            "url": "URL",
            "status": "STATUS",
            "transparency": "TRANSP",
        }
        for field_name, property_name in scalar_properties.items():
            if field_name in supplied:
                self._replace_property(component, property_name, getattr(request, field_name))
                changed.append(field_name)

        temporal_fields = {"start", "end", "all_day", "timezone"}
        if supplied & temporal_fields:
            existing_start = component.decoded("DTSTART")
            existing_end = component.decoded("DTEND", None)
            existing_all_day = isinstance(existing_start, date) and not isinstance(
                existing_start, datetime
            )
            all_day = (
                request.all_day
                if "all_day" in supplied and request.all_day is not None
                else existing_all_day
            )
            start_value = (
                request.start
                if "start" in supplied and request.start is not None
                else existing_start.isoformat()
            )
            end_value = (
                request.end
                if "end" in supplied
                else existing_end.isoformat()
                if existing_end is not None
                else None
            )
            timezone_name = request.timezone or self.settings.default_timezone
            start, end = normalize_event_times(start_value, end_value, all_day, timezone_name)
            for name in ("DTSTART", "DTEND", "DURATION"):
                component.pop(name, None)
            component.add("dtstart", start)
            component.add("dtend", end)
            changed.extend(sorted(supplied & temporal_fields))

        if "rrule" in supplied:
            self._replace_property(
                component,
                "RRULE",
                vRecur.from_ical(request.rrule) if request.rrule is not None else None,
            )
            changed.append("rrule")
        if "categories" in supplied:
            self._replace_property(component, "CATEGORIES", request.categories)
            changed.append("categories")
        if "alarms" in supplied:
            self._replace_alarms(
                component, request.alarms or [], str(component.get("SUMMARY", "Event"))
            )
            changed.append("alarms")
        if "attendees" in supplied:
            self._replace_attendees(component, request.attendees or [])
            changed.append("attendees")
        if "organizer" in supplied:
            self._replace_organizer(component, request.organizer)
            changed.append("organizer")
        if not changed:
            raise ValidationError("No editable fields were supplied.")
        component.pop("LAST-MODIFIED", None)
        component.add("last-modified", datetime.now(UTC))
        return sorted(set(changed))

    def update_event(self, request: UpdateEventInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                resource, calendar = self._find_event(request.uid, request.calendar)
                instance = resource.icalendar_instance
                created_exception = False
                if request.recurrence_scope == "single":
                    component, _master, created_exception = self._component_by_recurrence(
                        instance, request.recurrence_id or ""
                    )
                else:
                    component = first_event(instance)
                stored_sequence = int(first_event(instance).decoded("SEQUENCE", 0))
                if (
                    request.expected_sequence is not None
                    and request.expected_sequence != stored_sequence
                ):
                    raise ConflictError(
                        "Event changed since it was read; fetch it again before updating."
                    )
                changed = self._apply_patch(component, request)
                start, end = component_interval(component, self.settings.default_timezone)
                conflicts = self._find_conflicts(calendar, start, end, exclude_uid=request.uid)
                if conflicts and request.conflict_policy == "reject":
                    raise ConflictError(
                        f"Updated time overlaps {len(conflicts)} existing event(s); "
                        "no change was saved."
                    )
                resource.icalendar_instance = instance
                resource.save(increase_seqno=True)
                sequence = int(first_event(instance).decoded("SEQUENCE", 0))
                return {
                    "updated": True,
                    "uid": request.uid,
                    "scope": request.recurrence_scope,
                    "recurrence_id": request.recurrence_id,
                    "created_exception": created_exception,
                    "changed_fields": changed,
                    "sequence": sequence,
                    "calendar_id": self._calendar_id(calendar),
                    "conflicts": conflicts if request.conflict_policy == "warn" else [],
                }

            return self._safe_call("event update", work)

    def delete_event(self, request: DeleteEventInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                resource, calendar = self._find_event(request.uid, request.calendar)
                if request.recurrence_scope == "whole":
                    resource.delete()
                    return {
                        "deleted": True,
                        "uid": request.uid,
                        "scope": "whole",
                        "calendar_id": self._calendar_id(calendar),
                    }
                instance = resource.icalendar_instance
                master = first_event(instance)
                raw_id = parse_iso(request.recurrence_id or "")
                master_start = master.decoded("DTSTART")
                if (
                    isinstance(raw_id, datetime)
                    and isinstance(master_start, datetime)
                    and master_start.tzinfo is not None
                ):
                    raw_id = (
                        raw_id.replace(tzinfo=master_start.tzinfo)
                        if raw_id.tzinfo is None
                        else raw_id.astimezone(master_start.tzinfo)
                    )
                target_key = recurrence_key(raw_id)
                instance.subcomponents = [
                    component
                    for component in instance.subcomponents
                    if not (
                        isinstance(component, IEvent)
                        and component.get("RECURRENCE-ID") is not None
                        and recurrence_key(component.decoded("RECURRENCE-ID")) == target_key
                    )
                ]
                master.add("exdate", raw_id)
                master.pop("LAST-MODIFIED", None)
                master.add("last-modified", datetime.now(UTC))
                resource.icalendar_instance = instance
                resource.save(increase_seqno=True)
                return {
                    "deleted": True,
                    "uid": request.uid,
                    "scope": "single",
                    "recurrence_id": request.recurrence_id,
                    "calendar_id": self._calendar_id(calendar),
                    "sequence": int(master.decoded("SEQUENCE", 0)),
                }

            return self._safe_call("event deletion", work)

    def move_event(self, request: MoveEventInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                resource, source = self._find_event(request.uid, request.calendar)
                destination = self._resolve_calendar(request.destination_calendar)
                if self._calendar_id(source) == self._calendar_id(destination):
                    raise ValidationError("Source and destination calendars are the same.")
                created = self._save_new_event(destination, resource.data)
                try:
                    resource.delete()
                except Exception:
                    try:
                        created.delete()
                    except Exception:
                        self._log.error("move_rollback_failed")
                    raise BackendError(
                        "Move could not remove the source; the destination copy was rolled back."
                    ) from None
                return {
                    "moved": True,
                    "uid": request.uid,
                    "source_calendar_id": self._calendar_id(source),
                    "destination_calendar_id": self._calendar_id(destination),
                }

            return self._safe_call("event move", work)

    def respond_to_invite(self, request: RespondInviteInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                resource, calendar = self._find_event(request.uid, request.calendar)
                instance = resource.icalendar_instance
                if request.recurrence_scope == "single":
                    if request.recurrence_id is None:
                        raise ValidationError("recurrence_id is required for single scope")
                    component, _master, _created = self._component_by_recurrence(
                        instance, request.recurrence_id
                    )
                else:
                    component = first_event(instance)
                target = str(
                    request.attendee_email or self.settings.icloud_username.get_secret_value()
                ).casefold()
                attendees = component.get("ATTENDEE")
                values = (
                    attendees if isinstance(attendees, list) else [attendees] if attendees else []
                )
                matched = False
                for attendee in values:
                    email = str(attendee).removeprefix("mailto:").removeprefix("MAILTO:")
                    if email.casefold() == target:
                        attendee.params["PARTSTAT"] = request.response
                        attendee.params["RSVP"] = "FALSE"
                        matched = True
                if not matched:
                    raise NotFoundError("The selected attendee is not present on this event.")
                resource.icalendar_instance = instance
                resource.save(increase_seqno=True)
                return {
                    "updated": True,
                    "uid": request.uid,
                    "response": request.response,
                    "scope": request.recurrence_scope,
                    "calendar_id": self._calendar_id(calendar),
                }

            return self._safe_call("invitation response", work)

    def free_busy(self, request: DateRangeInput) -> dict[str, Any]:
        listing = self.list_events(request)
        zone = request.timezone or self.settings.default_timezone
        intervals: list[tuple[datetime, datetime, list[str]]] = []
        for item in listing["events"]:
            if item["transparency"] == "TRANSPARENT" or item["status"] == "CANCELLED":
                continue
            start, end = self._dict_interval(item, zone)
            intervals.append((start, end, [item["uid"]]))
        intervals.sort(key=lambda value: value[0])
        merged: list[tuple[datetime, datetime, list[str]]] = []
        for start, end, uids in intervals:
            if not merged or start > merged[-1][1]:
                merged.append((start, end, uids))
            else:
                previous = merged[-1]
                merged[-1] = (previous[0], max(previous[1], end), previous[2] + uids)
        return {
            "busy": [
                {"start": start.isoformat(), "end": end.isoformat(), "event_uids": uids}
                for start, end, uids in merged
            ],
            "count": len(merged),
            "truncated_source": listing["truncated"],
        }

    def find_free_slots(self, request: FindFreeSlotsInput) -> dict[str, Any]:
        with self._lock:
            zone_name = request.timezone or self.settings.default_timezone
            zone = ZoneInfo(zone_name)
            window_start = parse_datetime(request.start, zone_name)
            window_end = parse_datetime(request.end, zone_name)
            if window_end <= window_start:
                raise ValidationError("range end must be later than start")
            calendars = self._calendars()
            if request.calendar_ids:
                selected = [
                    self._resolve_calendar(value, calendars=calendars)
                    for value in request.calendar_ids
                ]
            elif request.calendar:
                selected = [self._resolve_calendar(request.calendar, calendars=calendars)]
            else:
                selected = calendars
            busy: list[tuple[datetime, datetime]] = []
            for calendar in selected:
                listing = self._events(
                    calendar_selector=self._calendar_id(calendar),
                    start=window_start,
                    end=window_end,
                    query=None,
                    limit=2000,
                )
                if listing["truncated"]:
                    raise BackendError(
                        "Availability source exceeded 2000 events; narrow the window."
                    )
                for item in listing["events"]:
                    if item["transparency"] == "TRANSPARENT" or item["status"] == "CANCELLED":
                        continue
                    busy.append(self._dict_interval(item, zone_name))
            busy.sort()
            merged: list[tuple[datetime, datetime]] = []
            for start, end in busy:
                start, end = max(start, window_start), min(end, window_end)
                if end <= start:
                    continue
                if not merged or start > merged[-1][1]:
                    merged.append((start, end))
                else:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))

            candidates: list[dict[str, str]] = []
            duration = timedelta(minutes=request.duration_minutes)
            granularity = timedelta(minutes=request.granularity_minutes)
            cursor = window_start
            for blocked_start, blocked_end in [*merged, (window_end, window_end)]:
                while cursor + duration <= blocked_start:
                    local = cursor.astimezone(zone)
                    local_end = (cursor + duration).astimezone(zone)
                    within_hours = True
                    if request.day_start:
                        lower = time.fromisoformat(request.day_start)
                        within_hours = local.timetz().replace(tzinfo=None) >= lower
                    if request.day_end:
                        upper = time.fromisoformat(request.day_end)
                        within_hours = (
                            within_hours and local_end.timetz().replace(tzinfo=None) <= upper
                        )
                    if within_hours and local.date() == local_end.date():
                        candidates.append(
                            {"start": cursor.isoformat(), "end": (cursor + duration).isoformat()}
                        )
                    cursor += granularity
                cursor = max(cursor, blocked_end)
            return {
                "slots": candidates[:500],
                "count": min(len(candidates), 500),
                "truncated": len(candidates) > 500,
                "timezone": zone_name,
                "calendars_checked": [self._calendar_id(calendar) for calendar in selected],
            }

    def create_calendar(self, request: CreateCalendarInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                if any(self._safe_calendar_name(cal) == request.name for cal in self._calendars()):
                    raise ConflictError("A calendar with that exact name already exists.")
                calendar = self._principal().make_calendar(name=request.name)
                if request.color:
                    calendar.set_properties([caldav_ical.CalendarColor(request.color)])
                return {"created": True, **self._calendar_summary(calendar)}

            return self._safe_call("calendar creation", work)

    def update_calendar(self, request: UpdateCalendarInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                if request.calendar is None:
                    raise ValidationError("calendar is required")
                calendar = self._resolve_calendar(request.calendar)
                properties: list[Any] = []
                changed: list[str] = []
                if request.name is not None:
                    properties.append(caldav_dav.DisplayName(request.name))
                    changed.append("name")
                if request.color is not None:
                    properties.append(caldav_ical.CalendarColor(request.color))
                    changed.append("color")
                calendar.set_properties(properties)
                return {
                    "updated": True,
                    "calendar_id": self._calendar_id(calendar),
                    "changed_fields": changed,
                    "name": request.name or self._safe_calendar_name(calendar),
                    "color": request.color or self._calendar_color(calendar),
                }

            return self._safe_call("calendar update", work)

    def delete_calendar(self, request: DeleteCalendarInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                if request.calendar is None:
                    raise ValidationError("calendar is required for destructive calendar deletion")
                calendar = self._resolve_calendar(request.calendar)
                name = self._safe_calendar_name(calendar)
                if request.confirm_name != name:
                    raise ValidationError(
                        "confirm_name must exactly match the discovered calendar name"
                    )
                calendar_id = self._calendar_id(calendar)
                calendar.delete()
                return {"deleted": True, "calendar_id": calendar_id, "name": name}

            return self._safe_call("calendar deletion", work)

    def export_ics(self, request: ExportICSInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                if request.uid:
                    resource, calendar = self._find_event(request.uid, request.calendar)
                    return {
                        "ics": resource.data,
                        "event_count": len(resource.icalendar_instance.walk("VEVENT")),
                        "calendar_id": self._calendar_id(calendar),
                    }
                if (request.start is None) != (request.end is None):
                    raise ValidationError("start and end must be supplied together")
                calendar = self._resolve_calendar(request.calendar)
                start = (
                    parse_datetime(request.start, self.settings.default_timezone)
                    if request.start
                    else None
                )
                end = (
                    parse_datetime(request.end, self.settings.default_timezone)
                    if request.end
                    else None
                )
                resources = self._query_resources(calendar, start, end)
                output = new_calendar()
                count = 0
                for resource in resources[:2000]:
                    for component in resource.icalendar_instance.walk("VEVENT"):
                        if isinstance(component, IEvent):
                            output.add_component(copy.deepcopy(component))
                            count += 1
                return {
                    "ics": output.to_ical().decode(),
                    "event_count": count,
                    "calendar_id": self._calendar_id(calendar),
                    "truncated": len(resources) > 2000,
                }

            return self._safe_call("ICS export", work)

    def import_ics(self, request: ImportICSInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                try:
                    incoming = ICalendar.from_ical(request.ics)
                except Exception:
                    raise ValidationError("ICS payload could not be parsed.") from None
                calendar = self._resolve_calendar(request.calendar)
                timezones = [
                    copy.deepcopy(component)
                    for component in incoming.subcomponents
                    if component.name == "VTIMEZONE"
                ]
                grouped: dict[str, list[IEvent]] = defaultdict(list)
                for component in incoming.walk("VEVENT"):
                    if isinstance(component, IEvent):
                        uid = str(component.get("UID") or uuid4())
                        component["UID"] = uid
                        grouped[uid].append(component)
                if not grouped:
                    raise ValidationError("ICS payload contains no VEVENT components.")
                planned: list[dict[str, Any]] = []
                for original_uid, components in grouped.items():
                    exists = False
                    try:
                        calendar.event_by_uid(original_uid).load()
                        exists = True
                    except caldav_error.NotFoundError:
                        pass
                    action = "create"
                    final_uid = original_uid
                    if exists and request.duplicate_policy == "skip":
                        action = "skip"
                    elif exists and request.duplicate_policy == "replace":
                        action = "replace"
                    elif exists and request.duplicate_policy == "new_uid":
                        action = "create"
                        final_uid = str(uuid4())
                    planned.append(
                        {"original_uid": original_uid, "uid": final_uid, "action": action}
                    )
                    if request.dry_run or action == "skip":
                        continue
                    if action == "replace":
                        calendar.event_by_uid(original_uid).delete()
                    container = new_calendar()
                    for tz_component in timezones:
                        container.add_component(copy.deepcopy(tz_component))
                    for component in components:
                        copied = copy.deepcopy(component)
                        copied["UID"] = final_uid
                        container.add_component(copied)
                    self._save_new_event(calendar, container.to_ical())
                return {
                    "dry_run": request.dry_run,
                    "calendar_id": self._calendar_id(calendar),
                    "items": planned,
                    "created": sum(item["action"] == "create" for item in planned),
                    "replaced": sum(item["action"] == "replace" for item in planned),
                    "skipped": sum(item["action"] == "skip" for item in planned),
                }

            return self._safe_call("ICS import", work)

    def batch_delete(self, request: BatchDeleteInput) -> dict[str, Any]:
        with self._lock:

            def work() -> dict[str, Any]:
                targets: list[tuple[str, caldav.Event, caldav.Calendar]] = []
                failures: list[dict[str, str]] = []
                for uid in dict.fromkeys(request.uids):
                    try:
                        resource, calendar = self._find_event(uid, request.calendar)
                        targets.append((uid, resource, calendar))
                    except (NotFoundError, ConflictError) as exc:
                        failures.append({"uid": uid, "error": str(exc)})
                if request.dry_run:
                    return {
                        "dry_run": True,
                        "would_delete": [uid for uid, _resource, _calendar in targets],
                        "failures": failures,
                    }
                deleted: list[dict[str, str]] = []
                for uid, resource, calendar in targets:
                    try:
                        resource.delete()
                        deleted.append({"uid": uid, "calendar_id": self._calendar_id(calendar)})
                    except Exception:
                        failures.append({"uid": uid, "error": "CalDAV deletion failed."})
                return {"dry_run": False, "deleted": deleted, "failures": failures}

            return self._safe_call("batch deletion", work)

    def batch_create(self, request: BatchCreateInput) -> dict[str, Any]:
        """Create validated independent events and preserve per-item failure visibility."""

        if request.dry_run:
            planned: list[dict[str, Any]] = []
            for index, event_request in enumerate(request.events):
                uid = self._event_uid(event_request)
                instance, uid = build_event(event_request, self.settings.default_timezone, uid=uid)
                component = first_event(instance)
                planned.append(
                    {
                        "index": index,
                        "uid": uid if event_request.idempotency_key else None,
                        "summary": str(component.get("SUMMARY", "")),
                        "start": component.decoded("DTSTART").isoformat(),
                        "calendar": event_request.calendar,
                    }
                )
            return cast(
                dict[str, Any],
                self.sanitize_result({"dry_run": True, "planned": planned, "count": len(planned)}),
            )
        created: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for index, event_request in enumerate(request.events):
            try:
                created.append({"index": index, **self.create_event(event_request)})
            except (BackendError, ConflictError, NotFoundError, ValidationError) as exc:
                failures.append({"index": index, "error": str(exc)})
        return cast(
            dict[str, Any],
            self.sanitize_result(
                {
                    "dry_run": False,
                    "created": created,
                    "failures": failures,
                    "count": len(created),
                }
            ),
        )
