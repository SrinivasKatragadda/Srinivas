"""Scheduled Job tools for the ServiceNow MCP server.

This module wraps the server-side script helpers needed to manage scheduled
jobs in ServiceNow. It treats scheduled jobs as script-driven records and
reuses the generic server-side script operations for create, read, update,
delete, and list flows.

Time parsing helpers in this module normalize date/time input so scheduled
jobs can be created consistently from natural ServiceNow-style schedule data.
"""

import re
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

SCRIPT_TYPE = "scheduled_job"
TIME_WITH_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[ T](\d{1,2}:\d{2}(?::\d{2})?)")
TIME_PATTERN = re.compile(r"^(\d{1,2})(?::(\d{1,2}))?(?::(\d{1,2}))?$")


class ListScheduledJobsParams(BaseModel):
    """Parameters for listing Scheduled Jobs."""

    limit: int = Field(10, description="Maximum number of records to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    query: Optional[str] = Field(None, description="Search text for name/sys_name")
    encoded_query: Optional[str] = Field(None, description="Additional ServiceNow encoded query")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class GetScheduledJobParams(BaseModel):
    """Parameters for getting a Scheduled Job."""

    scheduled_job_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class CreateScheduledJobParams(BaseModel):
    """Parameters for creating a Scheduled Job."""

    name: str = Field(..., description="Name of the Scheduled Job")
    active: Optional[bool] = Field(True, description="Whether the job is active")
    run_type: Optional[str] = Field(None, description="Run type, for example daily or weekly")
    run_start: Optional[str] = Field(None, description="First run date/time")
    entered_time: Optional[str] = Field(
        None,
        description=(
            "Hour/time value for the sysauto.entered_time glide_utc_time field. "
            "Accepts values like 8, 08:00, 08:00:00, or 1970-01-01 08:00:00."
        ),
    )
    run_time: Optional[str] = Field(
        None,
        description="Alias for entered_time; kept for callers that still use run_time",
    )
    run_period: Optional[str] = Field(None, description="Repeat interval/period")
    run_dayofweek: Optional[str] = Field(None, description="Day of week for weekly runs")
    run_dayofmonth: Optional[str] = Field(None, description="Day of month for monthly runs")
    run_month: Optional[str] = Field(None, description="Month for yearly runs")
    time_zone: Optional[str] = Field(None, description="Time zone for the schedule")
    condition: Optional[str] = Field(None, description="Condition field when supported")
    conditional: Optional[bool] = Field(None, description="Whether to evaluate condition/script")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sysauto field, including schedule and job fields",
    )


class UpdateScheduledJobParams(BaseModel):
    """Parameters for updating a Scheduled Job."""

    scheduled_job_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")
    name: Optional[str] = Field(None, description="Name of the Scheduled Job")
    active: Optional[bool] = Field(None, description="Whether the job is active")
    run_type: Optional[str] = Field(None, description="Run type, for example daily or weekly")
    run_start: Optional[str] = Field(None, description="First run date/time")
    entered_time: Optional[str] = Field(
        None,
        description=(
            "Hour/time value for the sysauto.entered_time glide_utc_time field. "
            "Accepts values like 8, 08:00, 08:00:00, or 1970-01-01 08:00:00."
        ),
    )
    run_time: Optional[str] = Field(
        None,
        description="Alias for entered_time; kept for callers that still use run_time",
    )
    run_period: Optional[str] = Field(None, description="Repeat interval/period")
    run_dayofweek: Optional[str] = Field(None, description="Day of week for weekly runs")
    run_dayofmonth: Optional[str] = Field(None, description="Day of month for monthly runs")
    run_month: Optional[str] = Field(None, description="Month for yearly runs")
    time_zone: Optional[str] = Field(None, description="Time zone for the schedule")
    condition: Optional[str] = Field(None, description="Condition field when supported")
    conditional: Optional[bool] = Field(None, description="Whether to evaluate condition/script")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Any additional sysauto field to update"
    )


class DeleteScheduledJobParams(BaseModel):
    """Parameters for deleting a Scheduled Job."""

    scheduled_job_id: str = Field(..., description="Record sys_id, sys_id:<id>, or name")


def list_scheduled_jobs(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListScheduledJobsParams,
) -> Dict[str, Any]:
    """List Scheduled Jobs from sysauto."""
    return list_server_side_scripts(
        config,
        auth_manager,
        ListServerSideScriptsParams(script_type=SCRIPT_TYPE, **params.model_dump()),
    )


def get_scheduled_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetScheduledJobParams,
) -> Dict[str, Any]:
    """Get a Scheduled Job from sysauto."""
    return get_server_side_script(
        config,
        auth_manager,
        GetServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=params.scheduled_job_id,
            fields=params.fields,
        ),
    )


def create_scheduled_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateScheduledJobParams,
) -> ServerSideScriptResponse:
    """Create a Scheduled Job in sysauto."""
    values = _scheduled_job_values(params)
    return create_server_side_script(
        config,
        auth_manager,
        CreateServerSideScriptParams(script_type=SCRIPT_TYPE, **values),
    )


def update_scheduled_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateScheduledJobParams,
) -> ServerSideScriptResponse:
    """Update a Scheduled Job in sysauto."""
    values = _scheduled_job_values(params)
    script_id = values.pop("scheduled_job_id")
    return update_server_side_script(
        config,
        auth_manager,
        UpdateServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=script_id,
            **values,
        ),
    )


def delete_scheduled_job(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteScheduledJobParams,
) -> ServerSideScriptResponse:
    """Delete a Scheduled Job from sysauto."""
    return delete_server_side_script(
        config,
        auth_manager,
        DeleteServerSideScriptParams(
            script_type=SCRIPT_TYPE,
            script_id=params.scheduled_job_id,
        ),
    )


def _scheduled_job_values(params: Any) -> Dict[str, Any]:
    values = params.model_dump()
    fields = dict(values.pop("fields", {}) or {})
    run_time = values.pop("run_time", None)
    entered_time = values.pop("entered_time", None)
    if entered_time is not None:
        fields["entered_time"] = _normalize_entered_time(entered_time)
    elif run_time is not None:
        fields["entered_time"] = _normalize_entered_time(run_time)
    for field_name in [
        "run_start",
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


def _normalize_entered_time(value: Any) -> str:
    """Normalize sysauto.entered_time values for ServiceNow glide_utc_time fields."""
    text = str(value).strip()
    date_match = TIME_WITH_DATE_PATTERN.match(text)
    if date_match:
        text = date_match.group(1)

    match = TIME_PATTERN.match(text)
    if not match:
        return text

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    second = int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return text

    return f"{hour:02d}:{minute:02d}:{second:02d}"
