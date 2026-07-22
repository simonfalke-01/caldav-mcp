"""Stable, non-leaking errors returned at the MCP boundary."""


class CalendarMCPError(Exception):
    """Base exception whose message is safe to show to an MCP caller."""


class ConfigurationError(CalendarMCPError):
    """Required configuration is absent or invalid."""


class ValidationError(CalendarMCPError):
    """A calendar-specific semantic validation failed."""


class NotFoundError(CalendarMCPError):
    """A requested calendar resource was not found."""


class ConflictError(CalendarMCPError):
    """A resource lookup or mutation was ambiguous or unsafe."""


class BackendError(CalendarMCPError):
    """The CalDAV service rejected or could not complete an operation."""
