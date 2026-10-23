"""
Configuration module for the ServiceNow MCP server.

This module defines the authentication and runtime configuration models used
throughout the ServiceNow MCP server. It centralizes auth strategy selection,
instance connection details, and general runtime knobs so the rest of the code
can stay focused on domain logic.
"""

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class AuthType(str, Enum):
    """Authentication types supported by the ServiceNow MCP server."""

    BASIC = "basic"
    OAUTH = "oauth"
    API_KEY = "api_key"


class BasicAuthConfig(BaseModel):
    """Configuration for basic authentication."""

    username: str
    password: str


class OAuthConfig(BaseModel):
    """Configuration for OAuth authentication."""

    client_id: str
    client_secret: str
    username: str
    password: str
    token_url: Optional[str] = None


class ApiKeyConfig(BaseModel):
    """Configuration for API key authentication."""

    api_key: str
    header_name: str = "X-ServiceNow-API-Key"


class AuthConfig(BaseModel):
    """Authentication configuration."""

    type: AuthType
    basic: Optional[BasicAuthConfig] = None
    oauth: Optional[OAuthConfig] = None
    api_key: Optional[ApiKeyConfig] = None


class ServerConfig(BaseModel):
    """Server configuration."""

    instance_url: str
    auth: AuthConfig
    debug: bool = False
    timeout: int = 30

    @property
    def api_url(self) -> str:
        """Get the API URL for the ServiceNow instance."""
        return f"{self.instance_url}/api/now"


def normalize_servicenow_instance_url(instance_url: str) -> str:
    """Normalize a ServiceNow instance URL for Flow Server request payloads."""
    normalized = str(instance_url or "").strip().rstrip("/")
    if normalized and not re.match(r"^https?://", normalized, flags=re.IGNORECASE):
        normalized = f"https://{normalized}"
    return normalized
