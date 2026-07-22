# Charter

Build and continuously harden a production-quality iCloud Calendar MCP server for an AI
calendar operator. The server uses Python 3.11+, FastMCP, Streamable HTTP at `/mcp`, and the
`caldav` and `icalendar` libraries. It binds to `0.0.0.0` on `PORT` (default `8000`).

Every HTTP request is gated by `Authorization: Bearer <MCP_API_KEY>`. iCloud access uses the
separate `ICLOUD_USERNAME` and `ICLOUD_APP_PASSWORD` secrets against
`https://caldav.icloud.com`. Secrets are loaded from environment variables before `.env`, are
never committed or logged, and must never appear in tool results. Configuration fails fast.

The product vision covers calendar discovery and management; event CRUD and search; rigorous
all-day/timezone semantics; recurrence and occurrence exceptions; alarms, attendees, and invite
responses; moving, free/busy, conflict detection, and natural-language quick-add; batch
operations, ICS import/export, categories, calendar colors, and resilience to iCloud discovery,
redirect, and response quirks.

Each substantive change is tested locally, including CalDAV round-trips against Radicale and an
MCP authentication/protocol check. Work is committed atomically and pushed without secrets or
history rewriting. The repository must be green at rest and deployable behind HTTPS.

