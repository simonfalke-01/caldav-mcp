# Decisions

## 2026-07-22 — FastMCP transport and authentication boundary

FastMCP 3.2.4 live documentation confirms `transport="http"` (Streamable HTTP) and
`http_app(path="/mcp")`. A fixed opaque operator key is not an OAuth/JWT identity token, so a
constant-time ASGI middleware gates HTTP requests before they reach FastMCP. This makes the
authentication boundary independent of tool dispatch and guarantees a clean `401` for missing or
incorrect bearer credentials.

## 2026-07-22 — CalDAV endpoint and redirects

Use the origin endpoint `https://caldav.icloud.com` and let the CalDAV/HTTP client follow Apple's
principal, calendar-home-set, and partition-host redirects. Store canonical resource URLs returned
by discovery rather than constructing partition paths.

## 2026-07-22 — FastMCP version verification

Context7's current indexed FastMCP documentation (3.2.4) confirmed the Streamable HTTP and ASGI
APIs. The resolved production lock uses FastMCP 3.4.4; its installed signatures were inspected and
the `/mcp` behavior was exercised with MCP Inspector 1.0.0 before delivery.

## 2026-07-22 — `caldav` SEQUENCE create workaround

`caldav` 2.2.6 increments an existing `SEQUENCE` inside `Event.save()` even when
`increase_seqno=False`. New and copied resources are therefore pre-decremented in memory before
the library save, yielding the intended stored value (0 for a new event, unchanged for a move).
Updates use the library's single increment. A Radicale regression test proves 0 → 1 → 2 → 3 over
create, master edit, occurrence edit, and occurrence delete.
