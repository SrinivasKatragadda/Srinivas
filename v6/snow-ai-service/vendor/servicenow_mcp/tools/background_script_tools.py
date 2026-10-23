"""Background Script tools for the ServiceNow MCP server.

This module provides the explicit execution surface for Scripts - Background.
It is intentionally guarded because background scripts can mutate live data,
so the helpers emphasize safety flags, auditability, and instance-supported
rollback or recording options where available.
"""

import logging
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


class ExecuteBackgroundScriptParams(BaseModel):
    """Parameters for executing a ServiceNow Scripts - Background script."""

    script: str = Field(..., description="Server-side JavaScript to execute")
    allow_execution: bool = Field(
        False,
        description=("Must be true to execute. Background scripts can change production data."),
    )
    scope: Optional[str] = Field(None, description="Application scope/sys_scope when supported")
    rollback_context: Optional[bool] = Field(
        None, description="Create rollback context when supported by the instance"
    )
    record_for_audit: Optional[bool] = Field(
        None, description="Record script execution for audit when supported"
    )
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional form fields accepted by the instance's sys.scripts.do endpoint",
    )


def execute_background_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ExecuteBackgroundScriptParams,
) -> Dict[str, Any]:
    """Execute JavaScript through ServiceNow Scripts - Background."""
    if not params.allow_execution:
        return {
            "success": False,
            "message": (
                "Background script execution was not attempted. Set allow_execution=true "
                "after reviewing the script and target instance."
            ),
        }

    form: Dict[str, Any] = dict(params.fields or {})
    form["script"] = params.script
    if params.scope is not None:
        form["sysparm_scope"] = params.scope
    if params.rollback_context is not None:
        form["rollback_context"] = str(params.rollback_context).lower()
    if params.record_for_audit is not None:
        form["record_for_audit"] = str(params.record_for_audit).lower()

    headers = auth_manager.get_headers()
    headers["Accept"] = "text/html,application/json"

    try:
        response = requests.post(
            f"{config.instance_url}/sys.scripts.do",
            data=form,
            headers=headers,
            timeout=config.timeout,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        result: Any
        if "application/json" in content_type:
            result = response.json()
        else:
            result = response.text
        return {
            "success": True,
            "message": "Background script execution request completed",
            "status_code": response.status_code,
            "result": result,
        }
    except Exception as e:
        logger.error("Error executing background script: %s", e)
        return {
            "success": False,
            "message": f"Error executing background script: {str(e)}",
        }
