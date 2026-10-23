"""
UI Action tools for the ServiceNow MCP server.

This module provides helpers for managing UI Actions in ServiceNow. UI Actions
are the form and list actions that drive buttons, links, and record-level
interactions, so the tools here focus on keeping those entry points easy to
inspect and maintain.

The module is intended to support both administrative discovery and targeted
record updates without exposing unrelated catalog or workflow concerns.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


class ListUIActionsParams(BaseModel):
    """Parameters for listing UI Actions."""

    limit: int = Field(10, description="Maximum number of UI Actions to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    table: Optional[str] = Field(None, description="Filter by table name")
    form_button: Optional[bool] = Field(None, description="Filter by form button actions")
    list_button: Optional[bool] = Field(None, description="Filter by list button actions")
    query: Optional[str] = Field(None, description="Search query for UI Action names")


class GetUIActionParams(BaseModel):
    """Parameters for getting a UI Action."""

    ui_action_id: str = Field(..., description="UI Action sys_id or name")


class CreateUIActionParams(BaseModel):
    """Parameters for creating a UI Action."""

    name: str = Field(..., description="Name of the UI Action")
    table: str = Field(..., description="Table the UI Action applies to")
    action_name: str = Field(..., description="Action name (used for client scripts)")
    script: Optional[str] = Field(None, description="Client/server script content")
    condition: Optional[str] = Field(None, description="Condition for showing the action")
    form_button: bool = Field(True, description="Whether this is a form button")
    list_button: bool = Field(False, description="Whether this is a list button")
    form_style: Optional[str] = Field(
        None, description="Form button style (primary, secondary, etc.)"
    )
    form_order: Optional[int] = Field(None, description="Order on the form")
    list_order: Optional[int] = Field(None, description="Order in the list")
    active: bool = Field(True, description="Whether the UI Action is active")
    show_insert: bool = Field(True, description="Show on insert")
    show_update: bool = Field(True, description="Show on update")
    show_query: bool = Field(False, description="Show on query")
    client: bool = Field(True, description="Whether this is a client-side action")
    on_click: Optional[str] = Field(None, description="On-click function name")
    comments: Optional[str] = Field(None, description="Comments about the UI Action")


class UpdateUIActionParams(BaseModel):
    """Parameters for updating a UI Action."""

    ui_action_id: str = Field(..., description="UI Action sys_id or name")
    name: Optional[str] = Field(None, description="Name of the UI Action")
    table: Optional[str] = Field(None, description="Table the UI Action applies to")
    action_name: Optional[str] = Field(None, description="Action name (used for client scripts)")
    script: Optional[str] = Field(None, description="Client/server script content")
    condition: Optional[str] = Field(None, description="Condition for showing the action")
    form_button: Optional[bool] = Field(None, description="Whether this is a form button")
    list_button: Optional[bool] = Field(None, description="Whether this is a list button")
    form_style: Optional[str] = Field(
        None, description="Form button style (primary, secondary, etc.)"
    )
    form_order: Optional[int] = Field(None, description="Order on the form")
    list_order: Optional[int] = Field(None, description="Order in the list")
    active: Optional[bool] = Field(None, description="Whether the UI Action is active")
    show_insert: Optional[bool] = Field(None, description="Show on insert")
    show_update: Optional[bool] = Field(None, description="Show on update")
    show_query: Optional[bool] = Field(None, description="Show on query")
    client: Optional[bool] = Field(None, description="Whether this is a client-side action")
    on_click: Optional[str] = Field(None, description="On-click function name")
    comments: Optional[str] = Field(None, description="Comments about the UI Action")


class DeleteUIActionParams(BaseModel):
    """Parameters for deleting a UI Action."""

    ui_action_id: str = Field(..., description="UI Action sys_id or name")


class UIActionResponse(BaseModel):
    """Response from UI Action operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    ui_action_id: Optional[str] = Field(None, description="ID of the affected UI Action")
    ui_action_name: Optional[str] = Field(None, description="Name of the affected UI Action")


def _build_ui_action_fields() -> str:
    return (
        "sys_id,name,table,action_name,active,form_button,list_button,form_style,"
        "form_order,list_order,condition,script,show_insert,show_update,show_query,"
        "client,on_click,comments,sys_created_on,sys_updated_on,"
        "sys_created_by,sys_updated_by"
    )


def _display_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _result_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = data.get("result", [])
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        items: List[Dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                items.append(item)
            else:
                logger.debug("Skipping non-dict UI Action record: %r", item)
        return items
    if result not in (None, ""):
        logger.debug("Unexpected UI Action result payload: %r", result)
    return []


def _normalize_ui_action(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sys_id": _display_value(item.get("sys_id")),
        "name": _display_value(item.get("name")),
        "table": _display_value(item.get("table")),
        "action_name": _display_value(item.get("action_name")),
        "active": _bool_value(item.get("active")),
        "form_button": _bool_value(item.get("form_button")),
        "list_button": _bool_value(item.get("list_button")),
        "form_style": _display_value(item.get("form_style")),
        "form_order": _display_value(item.get("form_order")),
        "list_order": _display_value(item.get("list_order")),
        "condition": _display_value(item.get("condition")),
        "script": _display_value(item.get("script")),
        "show_insert": _bool_value(item.get("show_insert")),
        "show_update": _bool_value(item.get("show_update")),
        "show_query": _bool_value(item.get("show_query")),
        "client": _bool_value(item.get("client")),
        "on_click": _display_value(item.get("on_click")),
        "comments": _display_value(item.get("comments")),
        "created_on": _display_value(item.get("sys_created_on")),
        "updated_on": _display_value(item.get("sys_updated_on")),
        "created_by": _display_value(item.get("sys_created_by")),
        "updated_by": _display_value(item.get("sys_updated_by")),
    }


def list_ui_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListUIActionsParams,
) -> Dict[str, Any]:
    """List UI Actions from ServiceNow."""
    try:
        url = f"{config.instance_url}/api/now/table/sys_ui_action"
        query_params = {
            "sysparm_limit": params.limit,
            "sysparm_offset": params.offset,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_action_fields(),
        }

        query_parts = []
        if params.active is not None:
            query_parts.append(f"active={str(params.active).lower()}")
        if params.table:
            query_parts.append(f"table={params.table}")
        if params.form_button is not None:
            query_parts.append(f"form_button={str(params.form_button).lower()}")
        if params.list_button is not None:
            query_parts.append(f"list_button={str(params.list_button).lower()}")
        if params.query:
            query_parts.append(f"nameLIKE{params.query}")

        query_params["sysparm_query"] = "^".join(query_parts)

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()
        items = _result_items(data)
        ui_actions = [_normalize_ui_action(item) for item in items]

        return {
            "success": True,
            "message": f"Found {len(ui_actions)} UI Actions",
            "ui_actions": ui_actions,
            "total": len(ui_actions),
            "limit": params.limit,
            "offset": params.offset,
        }

    except Exception as e:
        logger.error(f"Error listing UI Actions: {e}")
        return {
            "success": False,
            "message": f"Error listing UI Actions: {str(e)}",
            "ui_actions": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_ui_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetUIActionParams,
) -> Dict[str, Any]:
    """Get a specific UI Action from ServiceNow."""
    try:
        query_params = {
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_action_fields(),
        }

        # Determine if we're querying by sys_id or name
        if params.ui_action_id.startswith("sys_id:"):
            sys_id = params.ui_action_id.replace("sys_id:", "")
            url = f"{config.instance_url}/api/now/table/sys_ui_action/{sys_id}"
        else:
            # Query by name
            url = f"{config.instance_url}/api/now/table/sys_ui_action"
            query_params["sysparm_query"] = f"name={params.ui_action_id}"

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()

        if "result" not in data:
            return {
                "success": False,
                "message": f"UI Action not found: {params.ui_action_id}",
            }

        items = _result_items(data)
        if not items:
            return {
                "success": False,
                "message": f"UI Action not found: {params.ui_action_id}",
            }

        item = items[0]
        ui_action = _normalize_ui_action(item)

        return {
            "success": True,
            "message": f"Found UI Action: {_display_value(item.get('name'))}",
            "ui_action": ui_action,
        }

    except Exception as e:
        logger.error(f"Error getting UI Action: {e}")
        return {
            "success": False,
            "message": f"Error getting UI Action: {str(e)}",
        }


def create_ui_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateUIActionParams,
) -> UIActionResponse:
    """Create a new UI Action in ServiceNow."""
    url = f"{config.instance_url}/api/now/table/sys_ui_action"

    # Build the request body
    body = {
        "name": params.name,
        "table": params.table,
        "action_name": params.action_name,
        "active": str(params.active).lower(),
        "form_button": str(params.form_button).lower(),
        "list_button": str(params.list_button).lower(),
        "show_insert": str(params.show_insert).lower(),
        "show_update": str(params.show_update).lower(),
        "show_query": str(params.show_query).lower(),
        "client": str(params.client).lower(),
    }

    if params.script:
        body["script"] = params.script
    if params.condition:
        body["condition"] = params.condition
    if params.form_style:
        body["form_style"] = params.form_style
    if params.form_order is not None:
        body["form_order"] = params.form_order
    if params.list_order is not None:
        body["list_order"] = params.list_order
    if params.on_click:
        body["on_click"] = params.on_click
    if params.comments:
        body["comments"] = params.comments

    headers = auth_manager.get_headers()

    try:
        response = requests.post(url, json=body, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()

        if "result" not in data:
            return UIActionResponse(
                success=False,
                message="Failed to create UI Action",
            )

        result = data["result"]

        return UIActionResponse(
            success=True,
            message=f"Created UI Action: {result.get('name')}",
            ui_action_id=result.get("sys_id"),
            ui_action_name=result.get("name"),
        )

    except Exception as e:
        logger.error(f"Error creating UI Action: {e}")
        return UIActionResponse(
            success=False,
            message=f"Error creating UI Action: {str(e)}",
        )


def update_ui_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateUIActionParams,
) -> UIActionResponse:
    """Update an existing UI Action in ServiceNow."""
    # First, get the UI Action to update
    get_params = GetUIActionParams(ui_action_id=params.ui_action_id)
    get_result = get_ui_action(config, auth_manager, get_params)

    if not get_result["success"]:
        return UIActionResponse(
            success=False,
            message=get_result["message"],
        )

    ui_action = get_result["ui_action"]
    sys_id = ui_action["sys_id"]

    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_ui_action/{sys_id}"

    # Build the request body
    body = {}

    if params.name is not None:
        body["name"] = params.name
    if params.table is not None:
        body["table"] = params.table
    if params.action_name is not None:
        body["action_name"] = params.action_name
    if params.script is not None:
        body["script"] = params.script
    if params.condition is not None:
        body["condition"] = params.condition
    if params.form_button is not None:
        body["form_button"] = str(params.form_button).lower()
    if params.list_button is not None:
        body["list_button"] = str(params.list_button).lower()
    if params.form_style is not None:
        body["form_style"] = params.form_style
    if params.form_order is not None:
        body["form_order"] = params.form_order
    if params.list_order is not None:
        body["list_order"] = params.list_order
    if params.active is not None:
        body["active"] = str(params.active).lower()
    if params.show_insert is not None:
        body["show_insert"] = str(params.show_insert).lower()
    if params.show_update is not None:
        body["show_update"] = str(params.show_update).lower()
    if params.show_query is not None:
        body["show_query"] = str(params.show_query).lower()
    if params.client is not None:
        body["client"] = str(params.client).lower()
    if params.on_click is not None:
        body["on_click"] = params.on_click
    if params.comments is not None:
        body["comments"] = params.comments

    # If no fields to update, return success
    if not body:
        return UIActionResponse(
            success=True,
            message=f"No changes to update for UI Action: {ui_action['name']}",
            ui_action_id=sys_id,
            ui_action_name=ui_action["name"],
        )

    headers = auth_manager.get_headers()

    try:
        response = requests.patch(url, json=body, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()

        if "result" not in data:
            return UIActionResponse(
                success=False,
                message=f"Failed to update UI Action: {ui_action['name']}",
            )

        result = data["result"]

        return UIActionResponse(
            success=True,
            message=f"Updated UI Action: {result.get('name')}",
            ui_action_id=result.get("sys_id"),
            ui_action_name=result.get("name"),
        )

    except Exception as e:
        logger.error(f"Error updating UI Action: {e}")
        return UIActionResponse(
            success=False,
            message=f"Error updating UI Action: {str(e)}",
        )


def delete_ui_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteUIActionParams,
) -> UIActionResponse:
    """Delete a UI Action from ServiceNow."""
    # First, get the UI Action to delete
    get_params = GetUIActionParams(ui_action_id=params.ui_action_id)
    get_result = get_ui_action(config, auth_manager, get_params)

    if not get_result["success"]:
        return UIActionResponse(
            success=False,
            message=get_result["message"],
        )

    ui_action = get_result["ui_action"]
    sys_id = ui_action["sys_id"]
    name = ui_action["name"]

    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_ui_action/{sys_id}"

    headers = auth_manager.get_headers()

    try:
        response = requests.delete(url, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        return UIActionResponse(
            success=True,
            message=f"Deleted UI Action: {name}",
            ui_action_id=sys_id,
            ui_action_name=name,
        )

    except Exception as e:
        logger.error(f"Error deleting UI Action: {e}")
        return UIActionResponse(
            success=False,
            message=f"Error deleting UI Action: {str(e)}",
        )
