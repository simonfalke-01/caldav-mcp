"""FastMCP tool registry and authenticated Streamable HTTP application."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar, cast

from fastmcp import FastMCP
from starlette.types import ASGIApp

from .auth import BearerAuthMiddleware
from .config import Settings
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
    QuickAddInput,
    RespondInviteInput,
    SearchEventsInput,
    UpdateCalendarInput,
    UpdateEventInput,
)
from .quickadd import parse_quick_add
from .service import CalendarService

P = ParamSpec("P")
T = TypeVar("T")


async def _thread(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    return await asyncio.to_thread(function, *args, **kwargs)


def build_mcp(service: CalendarService) -> FastMCP:
    """Build the full tool registry around an injected CalDAV service."""

    mcp = FastMCP(
        "iCloud Calendar",
        instructions=(
            "Manage the operator's iCloud calendars. Always call list_calendars before choosing a "
            "calendar. Use ISO 8601 values, preserve returned UIDs, preview destructive batch "
            "work, "
            "and surface conflict warnings to the operator. All-day end dates are exclusive."
        ),
        version="0.1.0",
        mask_error_details=True,
        strict_input_validation=True,
    )

    @mcp.tool
    async def list_calendars() -> dict[str, Any]:
        """List every discovered calendar with its opaque calendar_id, display name, and color.

        Call this before any calendar-specific operation. Discovery follows the CalDAV principal
        and calendar-home-set chain, including iCloud partition-host redirects. Secret credentials
        and internal calendar URLs are never returned.
        """

        return await _thread(service.list_calendars)

    @mcp.tool
    async def create_event(request: CreateEventInput) -> dict[str, Any]:
        """Create a validated timed or all-day event and return its stable UID.

        Timed values need ISO date-times; naive values use `timezone`. All-day values need ISO dates
        and use an exclusive end date. Supports RRULE recurrence, VALARM reminders, attendees,
        organizer, categories, URL, status, transparency, and advisory/rejectable conflict checks.
        """

        return await _thread(service.create_event, request)

    @mcp.tool
    async def get_event(request: GetEventInput) -> dict[str, Any]:
        """Return full structured details for one event UID.

        Include calendar_id when the same UID could exist in multiple calendars. The result includes
        recurrence, alarms, attendees, organizer, categories, sequence, timezone, and all-day state.
        """

        return await _thread(service.get_event, request)

    @mcp.tool
    async def list_events(request: DateRangeInput) -> dict[str, Any]:
        """List events intersecting an inclusive-start, exclusive-end range.

        Recurrences are expanded by the CalDAV server. Results are ordered, bounded by `limit`, and
        may be scoped to one calendar. Use the `truncated` result before assuming completeness.
        """

        return await _thread(service.list_events, request)

    @mcp.tool(name="agenda")
    async def agenda(request: DateRangeInput) -> dict[str, Any]:
        """Alias of list_events for building an agenda over a precise ISO 8601 window."""

        return await _thread(service.list_events, request)

    @mcp.tool
    async def search_events(request: SearchEventsInput) -> dict[str, Any]:
        """Search text fields with optional date-range and calendar filters.

        Filters combine with AND semantics. Text matching is case-insensitive. Supply a bounded date
        range for large accounts and inspect `truncated` before acting on the result set.
        """

        return await _thread(service.search_events, request)

    @mcp.tool
    async def update_event(request: UpdateEventInput) -> dict[str, Any]:
        """Patch any event field while preserving omitted values and incrementing SEQUENCE.

        Set `recurrence_scope=single` plus the original occurrence DTSTART in `recurrence_id` to
        create/update a RECURRENCE-ID exception; `whole` edits the master. Explicit null clears a
        nullable property. Conflict policy can warn, allow, or reject before saving.
        """

        return await _thread(service.update_event, request)

    @mcp.tool(name="edit_event")
    async def edit_event(request: UpdateEventInput) -> dict[str, Any]:
        """Alias of update_event for patching whole events or one recurring occurrence safely."""

        return await _thread(service.update_event, request)

    @mcp.tool
    async def delete_event(request: DeleteEventInput) -> dict[str, Any]:
        """Delete an entire UID or exclude one recurring occurrence.

        `confirm_uid` must exactly repeat `uid`. Single scope adds EXDATE and removes any matching
        exception while retaining the series; whole scope deletes the CalDAV resource. The result
        reports exactly which scope changed.
        """

        return await _thread(service.delete_event, request)

    @mcp.tool
    async def move_event(request: MoveEventInput) -> dict[str, Any]:
        """Move one complete event resource to another calendar without changing its UID.

        The destination is created before the source is removed. If source deletion fails, the
        destination copy is rolled back so a failed move does not intentionally leave duplicates.
        """

        return await _thread(service.move_event, request)

    @mcp.tool
    async def respond_to_invite(request: RespondInviteInput) -> dict[str, Any]:
        """Set an attendee's PARTSTAT to ACCEPTED, DECLINED, or TENTATIVE.

        Omit attendee_email to match the configured Apple ID internally (it is never returned).
        iCloud's delivery of scheduling replies depends on calendar sharing and server support.
        """

        return await _thread(service.respond_to_invite, request)

    @mcp.tool
    async def free_busy(request: DateRangeInput) -> dict[str, Any]:
        """Return merged busy intervals for opaque, non-cancelled events in a bounded window."""

        return await _thread(service.free_busy, request)

    @mcp.tool
    async def find_free_slots(request: FindFreeSlotsInput) -> dict[str, Any]:
        """Find candidate open slots across selected calendars.

        Combines overlapping busy periods, honors event transparency, supports local working-hour
        bounds, and advances by `granularity_minutes`. A result is capped at 500 candidates.
        """

        return await _thread(service.find_free_slots, request)

    @mcp.tool
    async def quick_add(request: QuickAddInput) -> dict[str, Any]:
        """Parse and optionally create a conservative natural-language event.

        Examples: `lunch with Sam Friday 1pm for 90 minutes` or `offsite tomorrow all day`.
        Ambiguous phrases without explicit temporal evidence are rejected. Set `preview_only=true`
        to inspect the exact structured event before any mutation.
        """

        structured, parsed = parse_quick_add(request, service.settings.default_timezone)
        if request.preview_only:
            return cast(
                dict[str, Any],
                service.sanitize_result({"preview": True, "parsed": parsed, "created": False}),
            )
        result = await _thread(service.create_event, structured)
        return cast(
            dict[str, Any],
            service.sanitize_result({"preview": False, "parsed": parsed, **result}),
        )

    @mcp.tool
    async def create_calendar(request: CreateCalendarInput) -> dict[str, Any]:
        """Create a named CalDAV calendar, optionally with an Apple-style hex color."""

        return await _thread(service.create_calendar, request)

    @mcp.tool
    async def update_calendar(request: UpdateCalendarInput) -> dict[str, Any]:
        """Rename and/or recolor a calendar selected by its opaque calendar_id.

        Colors use Apple's `#RRGGBB` or `#RRGGBBAA` format. The server reports the exact fields
        changed and never constructs calendar URLs from names.
        """

        return await _thread(service.update_calendar, request)

    @mcp.tool
    async def delete_calendar(request: DeleteCalendarInput) -> dict[str, Any]:
        """Delete a calendar and all its events after exact display-name confirmation.

        This is destructive. Pass both calendar_id and an exact `confirm_name` copied from
        list_calendars; no event recovery is attempted.
        """

        return await _thread(service.delete_calendar, request)

    @mcp.tool
    async def export_ics(request: ExportICSInput) -> dict[str, Any]:
        """Export a single UID or a calendar/range as a complete VCALENDAR text payload."""

        return await _thread(service.export_ics, request)

    @mcp.tool
    async def import_ics(request: ImportICSInput) -> dict[str, Any]:
        """Import VEVENT resources from ICS with skip, replace, or new_uid duplicate handling.

        Use `dry_run=true` first to see the exact per-UID actions. Payloads are capped at 5 MB.
        Replacing is intentionally explicit and creates each grouped recurrence series atomically.
        """

        return await _thread(service.import_ics, request)

    @mcp.tool
    async def batch_create_events(request: BatchCreateInput) -> dict[str, Any]:
        """Preview or create up to 100 independently validated events.

        Default `dry_run=true` returns the planned order without mutation. Actual execution returns
        per-index successes and failures; supply idempotency_key on each event to make retries safe.
        """

        return await _thread(service.batch_create, request)

    @mcp.tool
    async def batch_delete_events(request: BatchDeleteInput) -> dict[str, Any]:
        """Preview or delete up to 200 whole event UIDs with exact set confirmation.

        `confirm_uids` must contain the same unique values as `uids`; default `dry_run=true` makes
        preview the safe first call. Per-item failures do not hide successful deletions.
        """

        return await _thread(service.batch_delete, request)

    return mcp


def create_http_app(settings: Settings, service: CalendarService | None = None) -> ASGIApp:
    """Create the ASGI app with authentication outside all FastMCP routes."""

    calendar_service = service or CalendarService(settings)
    mcp = build_mcp(calendar_service)
    inner = mcp.http_app(path="/mcp", transport="streamable-http")
    return BearerAuthMiddleware(inner, settings.mcp_api_key.get_secret_value())
