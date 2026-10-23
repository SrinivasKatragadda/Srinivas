"""
Server-side script tools for the ServiceNow MCP server.

This module provides generic CRUD-style helpers for ServiceNow server-side
script artifacts, including scheduled script executions, scheduled jobs, fix
scripts, script includes, script actions, and scripted REST assets.

The module acts as the shared backing layer for several higher-level tool
modules that need consistent server-side script record handling.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

SERVER_SIDE_SCRIPT_TABLES = {
    "business_rule": "sys_script",
    "script_include": "sys_script_include",
    "scheduled_script_execution": "sysauto_script",
    "scheduled_job": "sysauto",
    "script_action": "sysevent_script_action",
    "fix_script": "sys_script_fix",
    "scripted_rest_api": "sys_ws_definition",
    "scripted_rest_resource": "sys_ws_operation",
    "processor": "sys_processor",
    "workflow_script": "wf_activity",
    "ui_action": "sys_ui_action",
}

SERVER_SIDE_SCRIPT_PROFILES = {
    "business_rule": {
        "table": "sys_script",
        "artifact": "Business Rule",
        "trigger": "Record insert, update, delete, display, or query",
        "main_use": "Record lifecycle logic",
        "primary_tools": [
            "list_business_rules",
            "get_business_rule",
            "create_business_rule",
            "update_business_rule",
            "delete_business_rule",
        ],
        "key_fields": [
            "name",
            "collection",
            "active",
            "advanced",
            "when",
            "order",
            "condition",
            "filter_condition",
            "script",
            "action_insert",
            "action_update",
            "action_delete",
            "action_display",
            "action_query",
            "abort_action",
            "role_conditions",
        ],
    },
    "script_include": {
        "table": "sys_script_include",
        "artifact": "Script Include",
        "trigger": "Called by other scripts, APIs, GlideAjax, or scoped code",
        "main_use": "Reusable server-side functions/classes",
        "primary_tools": [
            "list_script_includes",
            "get_script_include",
            "create_script_include",
            "update_script_include",
            "detailed_update_script_include",
        ],
        "key_fields": [
            "name",
            "api_name",
            "script",
            "active",
            "client_callable",
            "access",
            "caller_access",
            "caller_restriction",
            "sys_scope",
            "description",
        ],
    },
    "scheduled_script_execution": {
        "table": "sysauto_script",
        "artifact": "Scheduled Script Execution",
        "trigger": "Time-based schedule",
        "main_use": "Batch jobs, cleanup, sync, reminders",
        "primary_tools": [
            "list_scheduled_script_executions",
            "get_scheduled_script_execution",
            "create_scheduled_script_execution",
            "update_scheduled_script_execution",
        ],
        "key_fields": [
            "name",
            "script",
            "active",
            "run_type",
            "run_start",
            "entered_time",
            "run_period",
            "run_dayofweek",
            "run_dayofmonth",
            "run_month",
            "time_zone",
            "conditional",
            "condition",
        ],
    },
    "scheduled_job": {
        "table": "sysauto",
        "artifact": "Scheduled Job",
        "trigger": "Time-based schedule or system scheduler",
        "main_use": "Scheduled platform job definitions and scheduler records",
        "primary_tools": [
            "list_scheduled_jobs",
            "get_scheduled_job",
            "create_scheduled_job",
            "update_scheduled_job",
            "delete_scheduled_job",
        ],
        "key_fields": [
            "name",
            "active",
            "run_type",
            "run_start",
            "run_time",
            "run_period",
            "run_dayofweek",
            "run_dayofmonth",
            "run_month",
            "time_zone",
            "conditional",
            "condition",
        ],
    },
    "fix_script": {
        "table": "sys_script_fix",
        "artifact": "Fix Script",
        "trigger": "App install, app upgrade, or manual run",
        "main_use": "One-time data/config repair",
        "primary_tools": [
            "list_fix_scripts",
            "get_fix_script",
            "create_fix_script",
            "update_fix_script",
            "delete_fix_script",
        ],
        "key_fields": [
            "name",
            "script",
            "active",
            "description",
            "before",
            "run_once",
            "unloadable",
            "flush_cache",
        ],
    },
    "background_script": {
        "table": None,
        "artifact": "Scripts - Background",
        "trigger": "Manual admin execution",
        "main_use": "Investigation, emergency repair, testing",
        "primary_tools": ["execute_background_script"],
        "key_fields": ["script", "scope", "rollback_context", "record_for_audit"],
        "warning": "This is executable admin code, not a normal ServiceNow table artifact.",
    },
    "background_job": {
        "table": "sys_trigger",
        "artifact": "Background Job / Scheduler Worker Item",
        "trigger": "Scheduler queue execution",
        "main_use": "Inspect or maintain queued/background scheduler work",
        "primary_tools": [
            "list_background_jobs",
            "get_background_job",
            "create_background_job",
            "update_background_job",
            "delete_background_job",
        ],
        "key_fields": [
            "name",
            "state",
            "trigger_type",
            "next_action",
            "job_context",
            "document",
            "system_id",
            "claimed_by",
        ],
        "warning": "sys_trigger is scheduler state; changing it can affect running jobs.",
    },
    "script_action": {
        "table": "sysevent_script_action",
        "artifact": "Script Action",
        "trigger": "Event fired",
        "main_use": "Event-driven server automation",
        "primary_tools": [
            "list_script_actions",
            "get_script_action",
            "create_script_action",
            "update_script_action",
            "delete_script_action",
        ],
        "key_fields": [
            "name",
            "event_name",
            "active",
            "script",
            "condition_script",
            "order",
            "event_parm_1",
            "event_parm_2",
            "description",
        ],
    },
}

SCRIPT_TYPE_DESCRIPTIONS = (
    "Known script_type values: business_rule, script_include, "
    "scheduled_script_execution, scheduled_job, script_action, fix_script, "
    "scripted_rest_api, scripted_rest_resource, processor, workflow_script, ui_action. "
    "Use table for custom or instance-specific script tables."
)


def _extract_sys_id(identifier: str) -> Optional[str]:
    cleaned = identifier.strip()
    if cleaned.startswith("sys_id:"):
        candidate = cleaned.replace("sys_id:", "", 1).strip()
        return candidate or None
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


def _resolve_table(script_type: Optional[str], table: Optional[str]) -> str:
    """The script record table. `table` may only name a server-side script table (never the table a script runs on,
    e.g. 'incident' — that would create an incident record instead of a script) and must agree with script_type."""
    allowed = set(SERVER_SIDE_SCRIPT_TABLES.values())
    if script_type and script_type not in SERVER_SIDE_SCRIPT_TABLES:
        raise ValueError(
            f"Unknown script_type '{script_type}'. Use one of: {', '.join(sorted(SERVER_SIDE_SCRIPT_TABLES))}"
        )
    mapped = SERVER_SIDE_SCRIPT_TABLES.get(script_type) if script_type else None
    if table:
        if table not in allowed:
            raise ValueError(
                f"'{table}' is not a server-side script table. 'table' selects the script record table "
                f"({', '.join(sorted(allowed))}); for the table a business rule runs on, set fields.collection."
            )
        if mapped and table != mapped:
            raise ValueError(f"script_type '{script_type}' is stored in {mapped}, not {table}")
        return table
    if mapped:
        return mapped
    raise ValueError("Either script_type or table must be provided")


def _coerce_record_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value.keys()).issubset({"display_value", "value", "link"}):
        return value.get("display_value") or value.get("value")
    return value


def _normalize_record(
    item: Dict[str, Any], table: str, script_type: Optional[str]
) -> Dict[str, Any]:
    return {
        "sys_id": item.get("sys_id"),
        "name": item.get("name") or item.get("sys_name"),
        "table": table,
        "script_type": script_type,
        "fields": {key: _coerce_record_value(value) for key, value in item.items()},
    }


def _build_query(parts: List[str]) -> Optional[str]:
    filtered = [part for part in parts if part]
    if not filtered:
        return None
    return "^".join(filtered)


def _request_table_records(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query_params: Dict[str, Any],
) -> Any:
    response = requests.get(
        f"{config.instance_url}/api/now/table/{table}",
        params=query_params,
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result")


class ListServerSideScriptsParams(BaseModel):
    """Parameters for listing server-side scripts or scheduled jobs."""

    script_type: Optional[str] = Field(None, description=SCRIPT_TYPE_DESCRIPTIONS)
    table: Optional[str] = Field(None, description="ServiceNow table name override")
    limit: int = Field(10, description="Maximum number of records to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status when supported")
    query: Optional[str] = Field(None, description="Search text for name/sys_name")
    encoded_query: Optional[str] = Field(
        None, description="Additional ServiceNow encoded query, for example active=true^nameLIKEfoo"
    )
    fields: Optional[List[str]] = Field(
        None,
        description=(
            "Fields to return. Leave empty to let ServiceNow return all readable fields, "
            "which gives the broadest available field coverage."
        ),
    )


class GetServerSideScriptParams(BaseModel):
    """Parameters for getting a server-side script or scheduled job."""

    script_id: str = Field(..., description="Record sys_id, sys_id:<id>, name, or sys_name")
    script_type: Optional[str] = Field(None, description=SCRIPT_TYPE_DESCRIPTIONS)
    table: Optional[str] = Field(None, description="ServiceNow table name override")
    fields: Optional[List[str]] = Field(
        None, description="Fields to return. Leave empty to return all readable fields."
    )


class CreateServerSideScriptParams(BaseModel):
    """Parameters for creating a server-side script or scheduled job."""

    script_type: Optional[str] = Field(None, description=SCRIPT_TYPE_DESCRIPTIONS)
    table: Optional[str] = Field(
        None,
        description=(
            "Script record table (only a server-side script table such as sys_script). NOT the table a business "
            "rule runs on — put that in fields.collection."
        ),
    )
    name: Optional[str] = Field(None, description="Record name when the table supports name")
    script: Optional[str] = Field(None, description="Server-side JavaScript body")
    active: Optional[bool] = Field(None, description="Active flag when the table supports active")
    run_type: Optional[str] = Field(
        None,
        description="Scheduled job run type when supported, for example daily, weekly, monthly",
    )
    condition: Optional[str] = Field(None, description="Condition field when supported")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "All additional ServiceNow fields to set. Use this for any available field "
            "on the target table, including schedule, time, application, package, order, "
            "collection, state, description, roles, or instance-specific custom fields."
        ),
    )


class UpdateServerSideScriptParams(BaseModel):
    """Parameters for updating a server-side script or scheduled job."""

    script_id: str = Field(..., description="Record sys_id, sys_id:<id>, name, or sys_name")
    script_type: Optional[str] = Field(None, description=SCRIPT_TYPE_DESCRIPTIONS)
    table: Optional[str] = Field(None, description="ServiceNow table name override")
    name: Optional[str] = Field(None, description="Record name when the table supports name")
    script: Optional[str] = Field(None, description="Server-side JavaScript body")
    active: Optional[bool] = Field(None, description="Active flag when the table supports active")
    run_type: Optional[str] = Field(None, description="Scheduled job run type when supported")
    condition: Optional[str] = Field(None, description="Condition field when supported")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional fields to update on the target ServiceNow table.",
    )


class DeleteServerSideScriptParams(BaseModel):
    """Parameters for deleting a server-side script or scheduled job."""

    script_id: str = Field(..., description="Record sys_id, sys_id:<id>, name, or sys_name")
    script_type: Optional[str] = Field(None, description=SCRIPT_TYPE_DESCRIPTIONS)
    table: Optional[str] = Field(None, description="ServiceNow table name override")


class ServerSideScriptResponse(BaseModel):
    """Response from server-side script operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    script_id: Optional[str] = Field(None, description="ID of the affected record")
    script_name: Optional[str] = Field(None, description="Name of the affected record")
    table: Optional[str] = Field(None, description="ServiceNow table used")


class GetServerSideScriptTypeMetadataParams(BaseModel):
    """Parameters for getting server-side script type metadata."""

    script_type: Optional[str] = Field(
        None,
        description="Specific script type to describe. Leave empty to return all profiles.",
    )


def get_server_side_script_type_metadata(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetServerSideScriptTypeMetadataParams,
) -> Dict[str, Any]:
    """Return field, trigger, table, and tool guidance for server-side script types."""
    del config, auth_manager
    if params.script_type:
        profile = SERVER_SIDE_SCRIPT_PROFILES.get(params.script_type)
        if not profile:
            return {
                "success": False,
                "message": f"Unknown script type: {params.script_type}",
                "available_types": sorted(SERVER_SIDE_SCRIPT_PROFILES),
            }
        return {"success": True, "script_type": params.script_type, "profile": profile}
    return {
        "success": True,
        "profiles": SERVER_SIDE_SCRIPT_PROFILES,
        "available_types": sorted(SERVER_SIDE_SCRIPT_PROFILES),
    }


def list_server_side_scripts(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListServerSideScriptsParams,
) -> Dict[str, Any]:
    """List ServiceNow server-side scripts or scheduled jobs."""
    try:
        table = _resolve_table(params.script_type, params.table)
        query_params: Dict[str, Any] = {
            "sysparm_limit": params.limit,
            "sysparm_offset": params.offset,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
        }
        if params.fields:
            query_params["sysparm_fields"] = ",".join(params.fields)

        query_parts = []
        if params.active is not None:
            query_parts.append(f"active={str(params.active).lower()}")
        if params.query:
            query_parts.append(f"nameLIKE{params.query}")
        if params.encoded_query:
            query_parts.append(params.encoded_query)
        encoded_query = _build_query(query_parts)
        if encoded_query:
            query_params["sysparm_query"] = encoded_query

        result = _request_table_records(config, auth_manager, table, query_params)
        if params.query and isinstance(result, list) and not result:
            fallback_query_parts = []
            if params.active is not None:
                fallback_query_parts.append(f"active={str(params.active).lower()}")
            fallback_query_parts.append(f"sys_nameLIKE{params.query}")
            if params.encoded_query:
                fallback_query_parts.append(params.encoded_query)
            fallback_query_params = dict(query_params)
            fallback_query_params["sysparm_query"] = _build_query(fallback_query_parts)
            try:
                fallback_result = _request_table_records(
                    config, auth_manager, table, fallback_query_params
                )
                if isinstance(fallback_result, list):
                    result = fallback_result
            except Exception:
                logger.debug(
                    "sys_name fallback search failed for table %s; using name search result",
                    table,
                    exc_info=True,
                )
        records = [
            _normalize_record(item, table, params.script_type)
            for item in (result or [])
            if isinstance(item, dict)
        ]
        return {
            "success": True,
            "message": f"Found {len(records)} records in {table}",
            "records": records,
            "total": len(records),
            "limit": params.limit,
            "offset": params.offset,
            "table": table,
        }
    except Exception as e:
        logger.error("Error listing server-side scripts: %s", e)
        return {
            "success": False,
            "message": f"Error listing server-side scripts: {str(e)}",
            "records": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_server_side_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetServerSideScriptParams,
) -> Dict[str, Any]:
    """Get a specific ServiceNow server-side script or scheduled job."""
    try:
        table = _resolve_table(params.script_type, params.table)
        query_params: Dict[str, Any] = {
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
        }
        if params.fields:
            query_params["sysparm_fields"] = ",".join(params.fields)

        sys_id = _extract_sys_id(params.script_id)
        if sys_id:
            url = f"{config.instance_url}/api/now/table/{table}/{sys_id}"
        else:
            url = f"{config.instance_url}/api/now/table/{table}"
            query_params["sysparm_query"] = f"name={params.script_id}"
            query_params["sysparm_limit"] = 10

        if sys_id:
            response = requests.get(
                url,
                params=query_params,
                headers=auth_manager.get_headers(),
                timeout=config.timeout,
            )
            response.raise_for_status()
            result = response.json().get("result")
        else:
            result = _request_table_records(config, auth_manager, table, query_params)
            if isinstance(result, list) and not result:
                fallback_query_params = dict(query_params)
                fallback_query_params["sysparm_query"] = f"sys_name={params.script_id}"
                try:
                    fallback_result = _request_table_records(
                        config, auth_manager, table, fallback_query_params
                    )
                    if isinstance(fallback_result, list):
                        result = fallback_result
                except Exception:
                    logger.debug(
                        "sys_name fallback lookup failed for table %s; using name lookup result",
                        table,
                        exc_info=True,
                    )
        item = result if isinstance(result, dict) else None
        if isinstance(result, list):
            normalized_id = params.script_id.strip().lower()
            item = next(
                (
                    candidate
                    for candidate in result
                    if isinstance(candidate, dict)
                    and str(candidate.get("name") or candidate.get("sys_name") or "")
                    .strip()
                    .lower()
                    == normalized_id
                ),
                result[0] if result else None,
            )

        if not isinstance(item, dict):
            return {
                "success": False,
                "message": f"Record not found in {table}: {params.script_id}",
                "table": table,
            }

        return {
            "success": True,
            "message": f"Found record in {table}: {item.get('name') or item.get('sys_name')}",
            "record": _normalize_record(item, table, params.script_type),
            "table": table,
        }
    except Exception as e:
        logger.error("Error getting server-side script: %s", e)
        return {
            "success": False,
            "message": f"Error getting server-side script: {str(e)}",
        }


def _build_write_body(params: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = dict(params.fields or {})
    if params.name is not None:
        body["name"] = params.name
    if params.script is not None:
        body["script"] = params.script
    if params.active is not None:
        body["active"] = str(params.active).lower()
    if params.run_type is not None:
        body["run_type"] = params.run_type
    if params.condition is not None:
        body["condition"] = params.condition
    return body


def _build_write_query_params(body: Dict[str, Any]) -> Dict[str, Any]:
    if "entered_time" in body:
        return {"sysparm_input_display_value": "true"}
    return {}


def create_server_side_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateServerSideScriptParams,
) -> ServerSideScriptResponse:
    """Create a ServiceNow server-side script or scheduled job."""
    try:
        table = _resolve_table(params.script_type, params.table)
        body = _build_write_body(params)
        if not body:
            return ServerSideScriptResponse(
                success=False,
                message="No fields supplied for server-side script creation",
                table=table,
            )
        if table == "sys_script":
            if not body.get("collection"):
                return ServerSideScriptResponse(
                    success=False,
                    message="A business rule needs fields.collection (the table it runs on); nothing was created",
                    table=table,
                )
            # Without advanced=true ServiceNow ignores the script field and the rule does nothing
            if body.get("script") and "advanced" not in body:
                body["advanced"] = "true"

        url = f"{config.instance_url}/api/now/table/{table}"
        response = requests.post(
            url,
            params=_build_write_query_params(body),
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result")
        if not isinstance(result, dict):
            return ServerSideScriptResponse(
                success=False,
                message=f"Unexpected create response from ServiceNow: {data}",
                table=table,
            )
        return ServerSideScriptResponse(
            success=True,
            message=f"Created record in {table}: {result.get('name') or result.get('sys_name')}",
            script_id=result.get("sys_id"),
            script_name=result.get("name") or result.get("sys_name"),
            table=table,
        )
    except Exception as e:
        logger.error("Error creating server-side script: %s", e)
        return ServerSideScriptResponse(
            success=False,
            message=f"Error creating server-side script: {str(e)}",
        )


def update_server_side_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateServerSideScriptParams,
) -> ServerSideScriptResponse:
    """Update a ServiceNow server-side script or scheduled job."""
    try:
        table = _resolve_table(params.script_type, params.table)
        get_result = get_server_side_script(
            config,
            auth_manager,
            GetServerSideScriptParams(
                script_id=params.script_id,
                script_type=params.script_type,
                table=table,
                fields=["sys_id", "name", "sys_name"],
            ),
        )
        if not get_result.get("success"):
            missing_message = get_result.get("message", "Failed to find record")
            return ServerSideScriptResponse(
                success=False,
                message=(
                    f"{missing_message}. The target component is not available; "
                    "ask the user to recreate it, then retry the update."
                ),
                table=table,
            )

        record = get_result["record"]
        sys_id = record["sys_id"]
        body = _build_write_body(params)
        if not body:
            return ServerSideScriptResponse(
                success=True,
                message=f"No changes to update for record in {table}",
                script_id=sys_id,
                script_name=record.get("name"),
                table=table,
            )

        url = f"{config.instance_url}/api/now/table/{table}/{sys_id}"
        response = requests.patch(
            url,
            params=_build_write_query_params(body),
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result")
        if not isinstance(result, dict):
            return ServerSideScriptResponse(
                success=False,
                message=f"Unexpected update response from ServiceNow: {data}",
                table=table,
            )
        return ServerSideScriptResponse(
            success=True,
            message=f"Updated record in {table}: {result.get('name') or result.get('sys_name')}",
            script_id=result.get("sys_id", sys_id),
            script_name=result.get("name") or result.get("sys_name"),
            table=table,
        )
    except Exception as e:
        logger.error("Error updating server-side script: %s", e)
        return ServerSideScriptResponse(
            success=False,
            message=f"Error updating server-side script: {str(e)}",
        )


def delete_server_side_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteServerSideScriptParams,
) -> ServerSideScriptResponse:
    """Delete a ServiceNow server-side script or scheduled job."""
    try:
        table = _resolve_table(params.script_type, params.table)
        get_result = get_server_side_script(
            config,
            auth_manager,
            GetServerSideScriptParams(
                script_id=params.script_id,
                script_type=params.script_type,
                table=table,
                fields=["sys_id", "name", "sys_name"],
            ),
        )
        if not get_result.get("success"):
            return ServerSideScriptResponse(
                success=False,
                message=get_result.get("message", "Failed to find record"),
                table=table,
            )

        record = get_result["record"]
        sys_id = record["sys_id"]
        name = record.get("name")
        response = requests.delete(
            f"{config.instance_url}/api/now/table/{table}/{sys_id}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return ServerSideScriptResponse(
            success=True,
            message=f"Deleted record in {table}: {name or sys_id}",
            script_id=sys_id,
            script_name=name,
            table=table,
        )
    except Exception as e:
        logger.error("Error deleting server-side script: %s", e)
        return ServerSideScriptResponse(
            success=False,
            message=f"Error deleting server-side script: {str(e)}",
        )
