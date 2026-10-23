"""Configuration for the local generic ServiceNow flow deployment server.

This module resolves the base URL used by the local flow deployment server.
It accepts environment overrides and normalizes the resulting URL so the Flow
Designer tooling can consistently reach the server across environments.
"""

from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_FLOW_SERVER_URL = os.getenv("DEFAULT_FLOW_SERVER_URL", "http://127.0.0.1:3000")
FLOW_SERVER_URL = os.getenv("FLOW_SERVER_URL") or os.getenv("SERVICENOW_FLOW_SERVER_URL")


def get_flow_server_url(override: Optional[str] = None) -> str:
    """Return the normalized generic flow server base URL."""
    configured = (
        override
        or os.getenv("SERVICENOW_FLOW_SERVER_URL")
        or os.getenv("FLOW_SERVER_URL")
        or FLOW_SERVER_URL
        or DEFAULT_FLOW_SERVER_URL
    )
    configured = str(configured or DEFAULT_FLOW_SERVER_URL).strip() or DEFAULT_FLOW_SERVER_URL
    if not re.match(r"^https?://", configured, flags=re.IGNORECASE):
        configured = f"http://{configured}"
    return configured.rstrip("/")
