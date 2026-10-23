"""Errors raised by the ITAM service. Messages are safe to show to users (no credentials, no payload dumps)."""

from typing import Optional


class ITAMError(Exception):
    """A refused or failed ITAM operation."""

    def __init__(self, message: str, code: str = "ITAM_ERROR", detail: str = "", status: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail
        self.status = status

    def to_dict(self) -> dict:
        out = {"error": self.message, "code": self.code}
        if self.detail:
            out["detail"] = self.detail
        if self.status:
            out["status_code"] = self.status
        return out

    def __str__(self) -> str:
        return f"{self.message}" + (f" — {self.detail}" if self.detail else "")
