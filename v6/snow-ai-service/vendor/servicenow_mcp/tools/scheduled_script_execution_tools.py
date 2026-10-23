"""Scheduled Script Execution tools for the ServiceNow MCP server.

This module provides helpers for scheduled script execution records and reuses
the generic server-side script record operations for the actual create/update
flows. It is intended for the scheduled execution variant of script records.
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

SCRIPT_TYPE = "scheduled_script_execution"


class ListScheduledScriptExecutionsParams(BaseModel):
    """Parameters for listing Scheduled Script Executions."""

    limit: int = Field(10, description="Maximum number of records to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    query: Optional[str] = Field(None, description="Search text for name/sys_name")
    encoded_query: Optional[str] = Field(None, description="Additional ServiceNow encoded query")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class GetScheduledScriptExecutionParams(BaseModel):
    """Parameters for getting a Scheduled Script Execution."""

    scheduled_script_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class CreateScheduledScriptExecutionParams(BaseModel):
    """Parameters for creating a Scheduled Script Execution."""

    name: str = Field(..., description="Name of the Scheduled Script Execution")
    script: str = Field(..., description="Server-side JavaScript body")
    active: Optional[bool] = Field(True, description="Whether the job is active")
    run_type: Optional[str] = Field(None, description="Run type, for example daily or weekly")
    run_start: Optional[str] = Field(None, description="First run date/time")
    run_time: Optional[str] = Field(None, description="Time of day for time-based runs")
    run_period: Optional[str] = Field(None, description="Repeat interval/period")
    run_dayofweek: Optional[str] = Field(None, description="Day of week for weekly runs")
    run_dayofmonth: Optional[str] = Field(None, description="Day of month for monthly runs")
    run_month: Optional[str] = Field(None, description="Month for yearly runs")
    time_zone: Optional[str] = Field(None, description="Time zone for the schedule")
    condition: Optional[str] = Field(None, description="Condition field when supported")
    conditional: Optional[bool] = Field(None, description="Whether to evaluate condition/script")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sysauto_script field, including schedule and run fields",
    )


class UpdateScheduledScriptExecutionParams(BaseModel):
    """Parameters for updating a Scheduled Script Execution."""

    scheduled_script_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    name: Optional[str] = Field(None, description="Name of the Scheduled Script Execution")
    script: Optional[str] = Field(None, description="Server-side JavaScript body")
    active: Optional[bool] = Field(None, description="Whether the job is active")
    run_type: Optional[str] = Field(None, description="Run type, for example daily or weekly")
    run_start: Optional[str] = Field(None, description="First run date/time")
    run_time: Optional[str] = Field(None, description="Time of day for time-based runs")
    run_period: Optional[str] = Field(None, description="Repeat interval/period")
    run_dayofweek: Optional[str] = Field(None, description="Day of week for weekly runs")
    run_dayofmonth: Optional[str] = Field(None, description="Day of month for monthly runs")
    run_month: Optional[str] = Field(None, description="Month for yearly runs")
    time_zone: Optional[str] = Field(None, description="Time zone for the schedule")
    condition: Optional[str] = Field(None, description="Condition field when supported")
    conditional: Optional[bool] = Field(None, description="Whether to evaluate condition/script")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Any additional sysauto_script field to update"
    )


class DeleteScheduledScriptExecutionParams(BaseModel):
    """Parameters for deleting a Scheduled Script Execution."""

    scheduled_script_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")


def list_scheduled_script_executions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListScheduledScriptExecutionsParams,
) -> Dict[str, Any]:
    """List Scheduled Script Executions from sysauto_script."""
    return list_server_side_scripts(
        config,
        auth_manager,
        ListServerSideScriptsParams(script_type=SCRIPT_TYPE, **params.model_dump()),
    )


def get_scheduled_script_execution(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetScheduledScriptExecutionParams,
) -> Dict[str, Any]:
    """Get a Scheduled Script Execution from sysauto_script."""
    return get_server_side_script(
        config,
        auth_manager,
        GetServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=params.scheduled_script_id,
            fields=params.fields,
        ),
    )


def create_scheduled_script_execution(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateScheduledScriptExecutionParams,
) -> ServerSideScriptResponse:
    """Create a Scheduled Script Execution in sysauto_script."""
    values = _scheduled_values(params)
    return create_server_side_script(
        config,
        auth_manager,
        CreateServerSideScriptParams(script_type=SCRIPT_TYPE, **values),
    )


def update_scheduled_script_execution(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateScheduledScriptExecutionParams,
) -> ServerSideScriptResponse:
    """Update a Scheduled Script Execution in sysauto_script."""
    values = _scheduled_values(params)
    script_id = values.pop("scheduled_script_id")
    return update_server_side_script(
        config,
        auth_manager,
        UpdateServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=script_id,
            **values,
        ),
    )


def delete_scheduled_script_execution(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteScheduledScriptExecutionParams,
) -> ServerSideScriptResponse:
    """Delete a Scheduled Script Execution from sysauto_script."""
    return delete_server_side_script(
        config,
        auth_manager,
        DeleteServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=params.scheduled_script_id,
        ),
    )


def _scheduled_values(params: Any) -> Dict[str, Any]:
    values = params.model_dump()
    fields = dict(values.pop("fields", {}) or {})
    for field_name in [
        "run_start",
        "run_time",
        "run_period",
        "run_dayofweek",
        "run_dayofmonth",
        "run_month",
        "time_zone",
        "conditional",
    ]:
        value = values.pop(field_name, None)
        if value is not None:
            fields[field_name] = str(value).lower() if isinstance(value, bool) else value
    values["fields"] = fields
    return values
