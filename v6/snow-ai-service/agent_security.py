"""
Optional API-key protection for the agent's /snow/* endpoints.

v5 exposed every endpoint (including ones that write to ServiceNow) to anyone who
could reach port 8001, with CORS '*'. Set AGENT_API_KEY in .env to require the header
`X-API-Key: <key>` on every /snow/* call (the UI asks for it on the connect screen).
"""

import hmac
import os
from typing import List

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str = Header(default="")):
    expected = os.getenv("AGENT_API_KEY", "")
    if not expected:
        return True
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Missing or wrong X-API-Key for the SoftProdigy agent")
    return True


def allowed_origins() -> List[str]:
    raw = os.getenv("AGENT_ALLOWED_ORIGINS", "")
    if raw.strip() == "*":
        return ["*"]
    if raw.strip():
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [f"http://{h}:{p}" for h in ("localhost", "127.0.0.1") for p in (8001, 3000, 5500, 5173, 8000, 8080)] + ["null"]
