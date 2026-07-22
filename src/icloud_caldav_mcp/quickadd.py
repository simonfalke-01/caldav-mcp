"""Conservative natural-language event parsing for quick_add."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from .errors import ValidationError
from .models import CreateEventInput, QuickAddInput

_DATE_SIGNAL = re.compile(
    r"\b(today|tomorrow|tonight|next\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|\d{4}-\d{2}-\d{2}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2})\b",
    re.IGNORECASE,
)
_TIME_SIGNAL = re.compile(
    r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)?\b",
    re.IGNORECASE,
)
_DURATION = re.compile(r"\bfor\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_ALL_DAY = re.compile(r"\ball[- ]day\b", re.IGNORECASE)


def parse_quick_add(
    request: QuickAddInput, default_timezone: str
) -> tuple[CreateEventInput, dict[str, Any]]:
    """Parse an explicit date/time phrase; reject guesses without temporal evidence."""

    zone_name = request.timezone or default_timezone
    zone = ZoneInfo(zone_name)
    if request.now:
        parsed_now = datetime.fromisoformat(request.now.replace("Z", "+00:00"))
        now = (
            parsed_now.replace(tzinfo=zone)
            if parsed_now.tzinfo is None
            else parsed_now.astimezone(zone)
        )
    else:
        now = datetime.now(zone)

    text = request.text.strip()
    all_day = bool(_ALL_DAY.search(text))
    has_date = bool(_DATE_SIGNAL.search(text))
    has_time = bool(_TIME_SIGNAL.search(text))
    if not has_date and not has_time:
        raise ValidationError(
            "quick_add needs an explicit date or time; use create_event for fully structured input."
        )
    if not all_day and not has_time:
        raise ValidationError("timed quick_add needs an explicit time, or include 'all day'.")

    parse_text = text
    tomorrow = bool(re.search(r"\btomorrow\b", parse_text, re.IGNORECASE))
    today = bool(re.search(r"\btoday\b", parse_text, re.IGNORECASE))
    parse_text = re.sub(r"\b(?:today|tomorrow)\b", "", parse_text, flags=re.IGNORECASE)
    duration_match = _DURATION.search(parse_text)
    duration_minutes = request.default_duration_minutes
    if duration_match:
        amount = float(duration_match.group(1))
        unit = duration_match.group(2).casefold()
        duration_minutes = round(amount * 60 if unit.startswith(("hour", "hr")) else amount)
        parse_text = _DURATION.sub("", parse_text)

    default = now.replace(hour=9 if all_day else now.hour, minute=0, second=0, microsecond=0)
    try:
        parsed, ignored = date_parser.parse(
            parse_text,
            fuzzy_with_tokens=True,
            default=default,
        )
    except (ValueError, OverflowError):
        raise ValidationError(
            "quick_add could not identify one unambiguous date and time; use create_event."
        ) from None
    parsed = parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
    if tomorrow:
        parsed += timedelta(days=1)
    elif today:
        parsed = parsed.replace(year=now.year, month=now.month, day=now.day)
    elif not has_date and parsed <= now:
        parsed += timedelta(days=1)
    elif has_date and parsed < now and not re.search(r"\d{4}-", text):
        parsed = parsed.replace(year=parsed.year + 1)

    ignored_text = " ".join(part.strip(" ,;-@").strip() for part in ignored if part.strip(" ,;-@"))
    ignored_text = _ALL_DAY.sub("", ignored_text)
    ignored_text = re.sub(r"\s+", " ", ignored_text).strip(" ,;-")
    summary = ignored_text or re.sub(r"\s+", " ", parse_text).strip(" ,;-")
    if not summary:
        summary = "Event"

    if all_day:
        start = parsed.date().isoformat()
        end = (parsed.date() + timedelta(days=1)).isoformat()
    else:
        start = parsed.isoformat()
        end = (parsed + timedelta(minutes=duration_minutes)).isoformat()
    create = CreateEventInput(
        summary=summary,
        start=start,
        end=end,
        all_day=all_day,
        timezone=zone_name,
        calendar=request.calendar,
        conflict_policy=request.conflict_policy,
    )
    confidence = "high" if has_date and (all_day or has_time) else "medium"
    return create, {
        "summary": summary,
        "start": start,
        "end": end,
        "all_day": all_day,
        "timezone": zone_name,
        "duration_minutes": 1440 if all_day else duration_minutes,
        "confidence": confidence,
    }
