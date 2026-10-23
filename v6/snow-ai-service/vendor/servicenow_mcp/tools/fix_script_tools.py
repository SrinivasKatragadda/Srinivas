"""Fix Script tools for the ServiceNow MCP server.

This module provides helper functions for ServiceNow Fix Scripts, reusing the
same server-side script record patterns as background jobs and other script-
backed operational tooling. It is primarily aimed at discovery and lifecycle
management of fix script records.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.server_side_script_tools import (
    CreateServerSideScriptParams,
    DeleteServerSideScriptParams,
    GetServerSideScriptParams,
    ListServerSideScriptsParams,
    ServerSideScriptResponse,
    UpdateServerSideScriptParams,
    create_server_side_script,
    delete_server_side_script,
    get_server_side_script,
    list_server_side_scripts,
    update_server_side_script,
)
from servicenow_mcp.utils.config import ServerConfig

SCRIPT_TYPE = "fix_script"


class ListFixScriptsParams(BaseModel):
    """Parameters for listing Fix Scripts."""

    limit: int = Field(10, description="Maximum number of records to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    query: Optional[str] = Field(None, description="Search text for name/sys_name")
    encoded_query: Optional[str] = Field(None, description="Additional ServiceNow encoded query")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class GetFixScriptParams(BaseModel):
    """Parameters for getting a Fix Script."""

    fix_script_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class CreateFixScriptParams(BaseModel):
    """Parameters for creating a Fix Script."""

    name: str = Field(..., description="Name of the Fix Script")
    script: str = Field(..., description="Server-side JavaScript body")
    active: Optional[bool] = Field(True, description="Whether the script is active")
    description: Optional[str] = Field(None, description="Description of the fix script")
    before: Optional[bool] = Field(None, description="Run before app install/upgrade")
    run_once: Optional[bool] = Field(None, description="Run once only")
    unloadable: Optional[bool] = Field(None, description="Track changes in update sets")
    flush_cache: Optional[bool] = Field(None, description="Flush cache after running")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_script_fix field, including run-once or unloadable flags",
    )


class UpdateFixScriptParams(BaseModel):
    """Parameters for updating a Fix Script."""

    fix_script_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    name: Optional[str] = Field(None, description="Name of the Fix Script")
    script: Optional[str] = Field(None, description="Server-side JavaScript body")
    active: Optional[bool] = Field(None, description="Whether the script is active")
    description: Optional[str] = Field(None, description="Description of the fix script")
    before: Optional[bool] = Field(None, description="Run before app install/upgrade")
    run_once: Optional[bool] = Field(None, description="Run once only")
    unloadable: Optional[bool] = Field(None, description="Track changes in update sets")
    flush_cache: Optional[bool] = Field(None, description="Flush cache after running")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Any additional sys_script_fix field to update"
    )


class DeleteFixScriptParams(BaseModel):
    """Parameters for deleting a Fix Script."""

    fix_script_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")


def list_fix_scripts(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListFixScriptsParams,
) -> Dict[str, Any]:
    """List Fix Scripts from sys_script_fix."""
    return list_server_side_scripts(
        config,
        auth_manager,
        ListServerSideScriptsParams(script_type=SCRIPT_TYPE, **params.model_dump()),
    )


def get_fix_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetFixScriptParams,
) -> Dict[str, Any]:
    """Get a Fix Script from sys_script_fix."""
    return get_server_side_script(
        config,
        auth_manager,
        GetServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=params.fix_script_id,
            fields=params.fields,
        ),
    )


def create_fix_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateFixScriptParams,
) -> ServerSideScriptResponse:
    """Create a Fix Script in sys_script_fix."""
    values = _fix_script_values(params)
    return create_server_side_script(
        config,
        auth_manager,
        CreateServerSideScriptParams(script_type=SCRIPT_TYPE, **values),
    )


def update_fix_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateFixScriptParams,
) -> ServerSideScriptResponse:
    """Update a Fix Script in sys_script_fix."""
    values = _fix_script_values(params)
    script_id = values.pop("fix_script_id")
    return update_server_side_script(
        config,
        auth_manager,
        UpdateServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=script_id,
            **values,
        ),
    )


def delete_fix_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteFixScriptParams,
) -> ServerSideScriptResponse:
    """Delete a Fix Script from sys_script_fix."""
    return delete_server_side_script(
        config,
        auth_manager,
        DeleteServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=params.fix_script_id,
        ),
    )


def _fix_script_values(params: Any) -> Dict[str, Any]:
    values = params.model_dump()
    fields = dict(values.pop("fields", {}) or {})
    for field_name in ["description", "before", "run_once", "unloadable", "flush_cache"]:
        value = values.pop(field_name, None)
        if value is not None:
            fields[field_name] = str(value).lower() if isinstance(value, bool) else value
    values["fields"] = fields
    return values
