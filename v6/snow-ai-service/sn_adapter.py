"""
sn_adapter.py — bridges SoftProdigy session auth with vendor/servicenow_mcp tool modules.

Every vendor tool function signature is:
    fn(config: ServerConfig, auth_manager: AuthManager, params: SomeParams) -> SomeResult

Provides two ways to get a (ServerConfig, AuthManager) pair:
  1. get_sn_context(session_id)  — from a registered session (X-Session-ID header flow)
  2. get_sn_context_from_creds(instance_url, username, password)  — direct creds (body flow)

The session store is owned here as a module-level singleton.
Call register_session() after a successful /snow/login to cache creds for the header flow.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))

from servicenow_mcp.utils.config import ServerConfig, AuthConfig, AuthType, BasicAuthConfig
from servicenow_mcp.auth.auth_manager import AuthManager

# ---------------------------------------------------------------------------
# Module-level session store
# dict: session_id -> {"instance_url": str, "username": str, "password": str}
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}

# Sensitive field names — values whose keys match any of these markers will be
# redacted in logs produced by redact_sensitive().
SENSITIVE_KEY_MARKERS = {"password", "secret", "token", "key", "credential", "api_key", "auth"}


def register_session(session_id: str, data: dict) -> None:
    """Store credentials for a session.

    Args:
        session_id: Unique session identifier (e.g. from the auth cookie / JWT sub).
        data: Mapping that must contain at least:
              - "instance_url" or "snow_url": full ServiceNow URL
              - "username": ServiceNow username
              - "password": ServiceNow password
    """
    _sessions[session_id] = data


def unregister_session(session_id: str) -> None:
    """Remove a session from the store.  No-op if the session does not exist."""
    _sessions.pop(session_id, None)


def get_sn_context(session_id: str) -> tuple[ServerConfig, AuthManager]:
    """Build a (ServerConfig, AuthManager) pair from a stored session.

    Args:
        session_id: Identifier previously passed to register_session().

    Returns:
        (ServerConfig, AuthManager) ready to pass into any vendor tool function.

    Raises:
        KeyError: If session_id is not found in the store.
        ValueError: If the session is missing instance_url, username, or password.
    """
    session = _sessions.get(session_id)
    if not session:
        raise KeyError(f"Session '{session_id}' not found. Please log in first.")

    instance_url = session.get("instance_url") or session.get("snow_url", "")
    username = session.get("username", "")
    password = session.get("password", "")

    if not instance_url or not username or not password:
        raise ValueError("Session is missing instance_url, username, or password.")

    # Normalise URL — strip trailing slash, ensure scheme prefix.
    instance_url = instance_url.strip().rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = "https://" + instance_url

    auth_config = AuthConfig(
        type=AuthType.BASIC,
        basic=BasicAuthConfig(username=username, password=password),
    )
    config = ServerConfig(instance_url=instance_url, auth=auth_config)
    auth_manager = AuthManager(config=auth_config, instance_url=instance_url)
    return config, auth_manager


def get_sn_context_from_creds(
    instance_url: str, username: str, password: str
) -> tuple[ServerConfig, AuthManager]:
    """Build a (ServerConfig, AuthManager) pair directly from credentials.

    This is the 'body flow' equivalent of get_sn_context() — no session store involved.
    Use it when the caller passes credentials in the request body instead of relying
    on a stored session (e.g. from sn_client-style endpoints).

    Args:
        instance_url: Full or bare ServiceNow URL.
        username:     ServiceNow username.
        password:     ServiceNow password.

    Returns:
        (ServerConfig, AuthManager) ready to pass into any vendor tool function.
    """
    instance_url = instance_url.strip().rstrip("/")
    if not instance_url.startswith("http"):
        instance_url = "https://" + instance_url

    auth_config = AuthConfig(
        type=AuthType.BASIC,
        basic=BasicAuthConfig(username=username, password=password),
    )
    config = ServerConfig(instance_url=instance_url, auth=auth_config)
    auth_manager = AuthManager(config=auth_config, instance_url=instance_url)
    return config, auth_manager


def redact_sensitive(data: dict) -> dict:
    """Return a copy of *data* with sensitive field values replaced by '***'.

    Sensitivity is determined by whether any marker from SENSITIVE_KEY_MARKERS
    appears as a substring of the key (case-insensitive).  Nested dicts are
    recursed into.
    """
    out: dict = {}
    for k, v in data.items():
        if any(marker in k.lower() for marker in SENSITIVE_KEY_MARKERS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = redact_sensitive(v)
        else:
            out[k] = v
    return out
