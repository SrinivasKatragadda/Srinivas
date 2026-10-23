"""Errors raised by the ITOM service. Messages are safe to show to users (no credentials, no payload dumps)."""

from snow_itam.errors import ITAMError


class ITOMError(ITAMError):
    """A refused or failed ITOM operation.

    It extends the shared backend error type (snow_itam's backends raise ITAMError for every ServiceNow failure),
    so hosts handle ITAM and ITOM refusals the same way."""

    def __init__(self, message: str, code: str = "ITOM_ERROR", detail: str = "", status=None):
        super().__init__(message, code, detail, status)
