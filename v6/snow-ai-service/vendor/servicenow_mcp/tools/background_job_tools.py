"""Background Job tools for the ServiceNow MCP server.

This module wraps the ServiceNow background job record surface, using the same
server-side script helpers that power other script-backed workflow tools. It is
aimed at inspection and administrative management of scheduled/background job
records rather than execution orchestration.
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

TABLE = "sys_trigger"


class ListBackgroundJobsParams(BaseModel):
    """Parameters for listing Background Jobs."""

    limit: int = Field(10, description="Maximum number of records to return")
    offset: int = Field(0, description="Offset for pagination")
    query: Optional[str] = Field(None, description="Search text for name/sys_name")
    encoded_query: Optional[str] = Field(None, description="Additional ServiceNow encoded query")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class GetBackgroundJobParams(BaseModel):
    """Parameters for getting a Background Job."""

    background_job_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class CreateBackgroundJobParams(BaseModel):
    """Parameters for creating a Background Job."""

    name: Optional[str] = Field(None, description="Background Job name")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Any sys_trigger field, such as state, trigger_type, next_action, or job_context"
        ),
    )


class UpdateBackgroundJobParams(BaseModel):
    """Parameters for updating a Background Job."""

    background_job_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    name: Optional[str] = Field(None, description="Background Job name")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Any sys_trigger field to update"
    )


class DeleteBackgroundJobParams(BaseModel):
    """Parameters for deleting a Background Job."""

    background_job_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")


def list_background_jobs(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListBackgroundJobsParams,
) -> Dict[str, Any]:
    """List Background Jobs from sys_trigger."""
    values = params.model_dump()
    return list_server_side_scripts(
        config,
        auth_manager,
        ListServerSideScriptsParams(table=TABLE, **values),
    )


def get_background_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetBackgroundJobParams,
) -> Dict[str, Any]:
    """Get a Background Job from sys_trigger."""
    return get_server_side_script(
        config,
        auth_manager,
        GetServerSideScriptParams(
            table=TABLE,
            script_id=params.background_job_id,
            fields=params.fields,
        ),
    )


def create_background_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateBackgroundJobParams,
) -> ServerSideScriptResponse:
    """Create a Background Job in sys_trigger."""
    return create_server_side_script(
        config,
        auth_manager,
        CreateServerSideScriptParams(table=TABLE, **params.model_dump()),
    )


def update_background_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateBackgroundJobParams,
) -> ServerSideScriptResponse:
    """Update a Background Job in sys_trigger."""
    values = params.model_dump()
    script_id = values.pop("background_job_id")
    return update_server_side_script(
        config,
        auth_manager,
        UpdateServerSideScriptParams(table=TABLE, script_id=script_id, **values),
    )


def delete_background_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteBackgroundJobParams,
) -> ServerSideScriptResponse:
    """Delete a Background Job from sys_trigger."""
    return delete_server_side_script(
        config,
        auth_manager,
        DeleteServerSideScriptParams(table=TABLE, script_id=params.background_job_id),
    )
