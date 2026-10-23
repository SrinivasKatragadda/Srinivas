"""
Configuration management for ServiceNow MCP.
Supports reading from environment variables, .env files, and runtime overrides.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Search for .env in current directory, parent directory, and snow-ai-service
current_dir = Path.cwd()
possible_env_paths = [
    current_dir / ".env",
    current_dir.parent / ".env",
    current_dir.parent / "snow-ai-service" / ".env",
    Path(__file__).resolve().parent.parent.parent / ".env",
    Path(__file__).resolve().parent.parent.parent.parent / "snow-ai-service" / ".env",
]

for p in possible_env_paths:
    if p.is_file():
        load_dotenv(p)


def get_instance_url(override: Optional[str] = None) -> str:
    url = override or os.getenv("SERVICENOW_INSTANCE_URL") or os.getenv("SNOW_INSTANCE_URL") or ""
    url = url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def get_username(override: Optional[str] = None) -> str:
    return (override or os.getenv("SERVICENOW_USERNAME") or os.getenv("SNOW_USER") or os.getenv("SERVICENOW_USER") or "").strip()


def get_password(override: Optional[str] = None) -> str:
    return (override or os.getenv("SERVICENOW_PASSWORD") or os.getenv("SNOW_PASS") or os.getenv("SERVICENOW_PASS") or "").strip()


def get_oauth_client_id() -> str:
    return (os.getenv("SERVICENOW_CLIENT_ID") or os.getenv("SNOW_CLIENT_ID") or "").strip()


def get_oauth_client_secret() -> str:
    return (os.getenv("SERVICENOW_CLIENT_SECRET") or os.getenv("SNOW_CLIENT_SECRET") or "").strip()


def get_oauth_grant() -> str:
    """'password' (default; uses SERVICENOW_USERNAME / PASSWORD) or 'client_credentials'."""
    return (os.getenv("SERVICENOW_OAUTH_GRANT") or "password").strip().lower()


def get_verify_tls() -> bool:
    val = os.getenv("SERVICENOW_VERIFY_TLS") or os.getenv("SNOW_VERIFY_TLS", "true")
    return val.strip().lower() not in ("0", "false", "no", "off")


def is_write_enabled() -> bool:
    val = os.getenv("SERVICENOW_WRITE_ENABLED") or os.getenv("WRITE_ENABLED", "true")
    return val.strip().lower() in ("1", "true", "yes", "on")


def is_scripting_allowed() -> bool:
    val = os.getenv("SERVICENOW_ALLOW_SCRIPTING") or os.getenv("ALLOW_SCRIPTING", "true")
    return val.strip().lower() in ("1", "true", "yes", "on")


def get_mcp_transport() -> str:
    return os.getenv("MCP_TRANSPORT", "stdio").strip().lower()


def get_mcp_host() -> str:
    return os.getenv("MCP_HOST", "0.0.0.0").strip()


def get_mcp_port() -> int:
    return int(os.getenv("MCP_PORT", "8002"))
