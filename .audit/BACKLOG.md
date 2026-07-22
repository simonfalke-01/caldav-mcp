# Backlog

## SUBSTANTIVE

- [P1-01] Core event CRUD, agenda, and search.
- [P2-01] Recurrence, occurrence exceptions, alarms, attendees, invite responses.
- [P2-02] Move, conflicts, free/busy, free slots.
- [P2-03] Natural-language quick-add.
- [P3-01] Calendar administration, ICS portability, batch operations.
- [P3-02] iCloud and malformed-data robustness.
- [P3-03] Production operations.
- [P3-04] Large-calendar synchronization and pagination.

## BUGS

- None known.

## JANITORIAL

- None.

## IN PROGRESS

- None.

## DONE

- Repository initialized on `main` with the operator-provided `origin`.
- [P0-01] Authenticated Streamable HTTP, Radicale, Inspector, CI, container, and documentation.
- [P1-01] Core event CRUD, agenda, search, strict validation, conflicts, and optimistic sequence checks.
- [P2-01] Recurrence exceptions, EXDATE deletion, alarms, attendees, organizer, and invite response.
- [P2-02] Move, free/busy, conflict detection, and free-slot finding.
- [P2-03] Conservative natural-language quick-add with mutation preview.
- [P3-01] Calendar administration/color, ICS portability, idempotency, and batch operations.
- [P3-02a] Rejected recurrence-expansion REPORT fallback with local RFC 5545 expansion.

## OPEN QUESTIONS

- Real-iCloud smoke verification requires operator-provided credentials and is skipped safely when absent.
