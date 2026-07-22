# Journal

Append-only record of completed delivery cycles.

## Cycle 1 — 2026-07-22T23:31:48+08:00   Phase: 0→3
- Item:        Full authenticated CalDAV MCP delivery (Tier: HIGH)
- Why it mattered: The empty repository became a deployable calendar agent with 21 safe management tools and two independent authentication layers.
- Evidence:    Roadmap P0-01, P1-01, P2-01, P2-02, P2-03, and P3-01; Radicale full lifecycle; Inspector tool discovery; container auth probe.
- Change:      Built Streamable HTTP `/mcp`, global bearer auth, iCloud CalDAV service, CRUD/search/recurrence/availability/invites/quick-add/ICS/batch/calendar tools, CI, Docker, and operator runbook.
- Verification: typecheck=p lint=p unit=p caldav-integration=p mcp-inspector=p auth-gate=p
- Commit:      b5fe55f12f52e10cd06a9e5af32389ed40181206 "feat: ship full iCloud CalDAV MCP server"   Pushed: origin/main @ b5fe55f12f52e10cd06a9e5af32389ed40181206
----------------------------------------------------------------
## Cycle 2 — 2026-07-22T23:34:10+08:00   Phase: 3
- Item:        Recurrence REPORT compatibility fallback (Tier: HIGH)
- Why it mattered: Agenda and availability queries now remain correct when an iCloud-compatible server rejects server-side recurrence expansion.
- Evidence:    Regression test forces expanded REPORT failure and proves three locally expanded weekly occurrences are returned.
- Change:      Retried rejected expanded REPORTs without expansion, expanded RFC 5545 recurrences locally, and isolated malformed resources.
- Verification: typecheck=p lint=p unit=p caldav-integration=p mcp-inspector=p auth-gate=p
- Commit:      004ad54ce5f7ce2bf04c3ba2c43c2758d72b9da4 "fix: fall back to local recurrence expansion"   Pushed: origin/main @ 004ad54ce5f7ce2bf04c3ba2c43c2758d72b9da4
----------------------------------------------------------------
