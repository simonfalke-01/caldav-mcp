# Roadmap

- [P0-01] [HIGH] Bootstrap authenticated Streamable HTTP `/mcp`, iCloud discovery, Radicale tests, container deployment, and operator documentation; nothing else is usable until this foundation works.
- [P1-01] [HIGH] Ship complete event CRUD, agenda, and text/date/calendar search with validated structured schemas; these are the core calendar-agent capabilities.
- [P2-01] [HIGH] Add timezone-safe recurrence, occurrence edits/deletes, alarms, attendees, and invitation responses; normal calendar workflows depend on these semantics.
- [P2-02] [HIGH] Add move, conflict detection, free/busy, and free-slot finding; assistants must schedule safely across calendars.
- [P2-03] [MEDIUM] Add deterministic natural-language quick-add with previewable parsing; conversational scheduling needs a safe shortcut.
- [P3-01] [HIGH] Add calendar create/delete, ICS import/export, and safe batch operations; these close major administration and portability gaps.
- [P3-02] [MEDIUM] Harden iCloud partition redirects, malformed resources, duplicate UIDs, ETags, and partial failures; production accounts contain irregular data.
- [P3-03] [MEDIUM] Add observability, rate limiting, deployment health/readiness, and operational runbooks without weakening the global auth gate.
- [P3-04] [MEDIUM] Add pagination/caps and sync-token incremental reads for large calendars; mature accounts require bounded responses.

