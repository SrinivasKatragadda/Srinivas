"""
ServiceNow MCP Errors and Diagnostic Helpers.
Adapted with actionable troubleshooting advice for authentication, ACLs, and business rules.
"""

from typing import Optional


class ServiceNowError(Exception):
    """Domain exception for ServiceNow API and MCP operations."""

    def __init__(self, message: str, code: str = "SERVICENOW_ERROR", detail: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail
        self.status_code = status_code

    def to_dict(self) -> dict:
        result = {
            "error": self.message,
            "code": self.code,
        }
        if self.detail:
            result["detail"] = self.detail
        if self.status_code:
            result["status_code"] = self.status_code
        return result


def basic_auth_diagnostic() -> str:
    """Actionable checklist for 401 Basic Auth failures."""
    return (
        "Basic auth was rejected by the ServiceNow instance (HTTP 401). Common causes:\n"
        "  1. Username must be the login user_name, not the email or display name.\n"
        "  2. The account needs a valid LOCAL password. If SSO / Federated ID is configured, "
        "browser login works but Basic REST auth fails. Use a dedicated local account or OAuth.\n"
        "  3. ServiceNow 'Basic Auth Restriction' policy may be active (default deny). "
        "The user needs snc_basic_auth_api_access role or Web Service Access Only.\n"
        "  4. Confirm the account is active, not locked out, and not requiring password reset.\n"
        "  5. Instance might be hibernating/sleeping (PDI). Check developer.servicenow.com to wake it."
    )


def forbidden_diagnostic() -> str:
    """Actionable checklist for 403 Forbidden failures."""
    return (
        "Authenticated, but not authorized (HTTP 403). Causes:\n"
        "  1. A data policy or field-level ACL rejected a field in the request.\n"
        "  2. Account is missing required roles (e.g., 'itil' for ITSM, 'admin' for sys_* tables).\n"
        "  3. If WRITE_ENABLED is false, write operations are rejected by server safety guardrails."
    )


def business_rule_abort_diagnostic(detail: str = "") -> str:
    """Diagnostic for business rule aborts."""
    return (
        f"Operation was aborted by server-side logic (business rule or data policy). {detail}\n"
        "Roles do not bypass business rules. Check required fields or state transition rules."
    )
