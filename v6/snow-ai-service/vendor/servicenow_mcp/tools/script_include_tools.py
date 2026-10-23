"""
Script Include tools for the ServiceNow MCP server.

This module provides helpers for managing Script Includes in ServiceNow. It
focuses on the class-like server-side script records that power reusable logic
in scoped and global applications, allowing them to be inspected and updated
through the MCP layer.
"""

import logging
import re
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


def _extract_display_value(value: Any) -> Optional[str]:
    """Return a stable display value from ServiceNow reference fields."""
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    if value is None:
        return None
    return str(value)


def _coerce_servicenow_bool(value: Any) -> bool:
    """Normalize ServiceNow booleans that may arrive as strings or bools."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _normalize_script_include(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw ServiceNow script include record."""
    return {
        "sys_id": item.get("sys_id"),
        "name": item.get("name"),
        "script": item.get("script"),
        "description": item.get("description"),
        "api_name": item.get("api_name"),
        "client_callable": _coerce_servicenow_bool(item.get("client_callable")),
        "active": _coerce_servicenow_bool(item.get("active")),
        "access": item.get("access"),
        "application": _extract_display_value(item.get("sys_scope") or item.get("application")),
        "caller_access": item.get("caller_access"),
        "caller_restriction": item.get("caller_restriction"),
        "created_on": item.get("sys_created_on"),
        "updated_on": item.get("sys_updated_on"),
        "created_by": _extract_display_value(item.get("sys_created_by")),
        "updated_by": _extract_display_value(item.get("sys_updated_by")),
        "fields": item,
    }


def _select_script_include_match(results: Any, script_include_id: str) -> Optional[Dict[str, Any]]:
    """Select the best matching script include from a ServiceNow response."""
    if isinstance(results, dict):
        return results

    if not isinstance(results, list):
        return None

    normalized_identifier = script_include_id.strip().lower()
    fallback_match: Optional[Dict[str, Any]] = None

    for item in results:
        if not isinstance(item, dict):
            continue

        if fallback_match is None:
            fallback_match = item

        name = str(item.get("name") or "").strip().lower()
        api_name = str(item.get("api_name") or "").strip().lower()

        if name == normalized_identifier or api_name == normalized_identifier:
            return item

        if api_name.endswith(f".{normalized_identifier}"):
            return item

    return fallback_match


def _extract_sys_id(identifier: str) -> Optional[str]:
    """Return a ServiceNow sys_id from either raw or prefixed input."""
    cleaned = identifier.strip()
    if cleaned.startswith("sys_id:"):
        candidate = cleaned.replace("sys_id:", "", 1).strip()
        return candidate or None
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


class ListScriptIncludesParams(BaseModel):
    """Parameters for listing script includes."""

    limit: int = Field(10, description="Maximum number of script includes to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    client_callable: Optional[bool] = Field(None, description="Filter by client callable status")
    query: Optional[str] = Field(None, description="Search query for script includes")


class GetScriptIncludeParams(BaseModel):
    """Parameters for getting a script include."""

    script_include_id: str = Field(..., description="Script include ID or name")


class CreateScriptIncludeParams(BaseModel):
    """Parameters for creating a script include."""

    name: str = Field(..., description="Name of the script include")
    script: str = Field(..., description="Script content")
    description: Optional[str] = Field(None, description="Description of the script include")
    api_name: Optional[str] = Field(None, description="API name of the script include")
    client_callable: bool = Field(
        False, description="Whether the script include is client callable"
    )
    active: bool = Field(True, description="Whether the script include is active")
    access: str = Field("package_private", description="Access level of the script include")
    application: Optional[str] = Field(None, description="Scoped application/sys_scope sys_id")
    caller_access: Optional[str] = Field(None, description="Caller access restriction")
    caller_restriction: Optional[str] = Field(None, description="Caller restriction setting")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_script_include field to set, including custom fields",
    )


class UpdateScriptIncludeParams(BaseModel):
    """Parameters for updating a script include."""

    script_include_id: str = Field(..., description="Script include ID or name")
    name: Optional[str] = Field(None, description="Name of the script include")
    script: Optional[str] = Field(None, description="Script content")
    description: Optional[str] = Field(None, description="Description of the script include")
    api_name: Optional[str] = Field(None, description="API name of the script include")
    client_callable: Optional[bool] = Field(
        None, description="Whether the script include is client callable"
    )
    active: Optional[bool] = Field(None, description="Whether the script include is active")
    access: Optional[str] = Field(None, description="Access level of the script include")
    application: Optional[str] = Field(None, description="Scoped application/sys_scope sys_id")
    caller_access: Optional[str] = Field(None, description="Caller access restriction")
    caller_restriction: Optional[str] = Field(None, description="Caller restriction setting")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_script_include field to update, including custom fields",
    )


class DetailedUpdateScriptIncludeParams(BaseModel):
    """Detailed parameters for updating a script include with additional fields."""

    script_include_id: str = Field(..., description="Script include ID or name")
    name: Optional[str] = Field(None, description="Name of the script include")
    script: Optional[str] = Field(None, description="Script content")
    description: Optional[str] = Field(None, description="Description of the script include")
    api_name: Optional[str] = Field(None, description="API name of the script include")
    client_callable: Optional[bool] = Field(
        None, description="Whether the script include is client callable"
    )
    active: Optional[bool] = Field(None, description="Whether the script include is active")
    access: Optional[str] = Field(None, description="Access level of the script include")
    application: Optional[str] = Field(None, description="Scoped application ID")
    caller_access: Optional[str] = Field(None, description="Caller access restriction")
    caller_restriction: Optional[str] = Field(None, description="Caller restriction setting")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_script_include field to update, including custom fields",
    )


class GetEditableFieldsParams(BaseModel):
    """Parameters for getting editable fields information."""

    script_include_id: Optional[str] = Field(
        None, description="Script include ID to get current values (optional)"
    )


class EditableFieldsResponse(BaseModel):
    """Response containing information about editable fields."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    editable_fields: Dict[str, Any] = Field(..., description="Information about editable fields")
    current_values: Optional[Dict[str, Any]] = Field(
        None, description="Current values if script_include_id provided"
    )


class DeleteScriptIncludeParams(BaseModel):
    """Parameters for deleting a script include."""

    script_include_id: str = Field(..., description="Script include ID or name")


class ScriptIncludeResponse(BaseModel):
    """Response from script include operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    script_include_id: Optional[str] = Field(None, description="ID of the affected script include")
    script_include_name: Optional[str] = Field(
        None, description="Name of the affected script include"
    )


def list_script_includes(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListScriptIncludesParams,
) -> Dict[str, Any]:
    """List script includes from ServiceNow.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The parameters for the request.

    Returns:
        A dictionary containing the list of script includes.
    """
    try:
        # Build the URL
        url = f"{config.instance_url}/api/now/table/sys_script_include"

        # Build query parameters
        query_params = {
            "sysparm_limit": params.limit,
            "sysparm_offset": params.offset,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": (
                "sys_id,name,script,description,api_name,client_callable,active,access,"
                "sys_scope,application,caller_access,caller_restriction,"
                "sys_created_on,sys_updated_on,sys_created_by,sys_updated_by"
            ),
        }

        # Add filters if provided
        query_parts = []

        if params.active is not None:
            query_parts.append(f"active={str(params.active).lower()}")

        if params.client_callable is not None:
            query_parts.append(f"client_callable={str(params.client_callable).lower()}")

        if params.query:
            query_parts.append(f"nameLIKE{params.query}")

        if query_parts:
            query_params["sysparm_query"] = "^".join(query_parts)

        # Make the request
        headers = auth_manager.get_headers()

        response = requests.get(
            url,
            params=query_params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        # Parse the response
        data = response.json()
        script_includes = []

        for item in data.get("result", []):
            if not isinstance(item, dict):
                logger.warning("Skipping unexpected script include list item: %r", item)
                continue
            script_includes.append(_normalize_script_include(item))

        return {
            "success": True,
            "message": f"Found {len(script_includes)} script includes",
            "script_includes": script_includes,
            "total": len(script_includes),
            "limit": params.limit,
            "offset": params.offset,
        }

    except Exception as e:
        logger.error(f"Error listing script includes: {e}")
        return {
            "success": False,
            "message": f"Error listing script includes: {str(e)}",
            "script_includes": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_script_include(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetScriptIncludeParams,
) -> Dict[str, Any]:
    """Get a specific script include from ServiceNow.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The parameters for the request.

    Returns:
        A dictionary containing the script include data.
    """
    try:
        # Build query parameters
        query_params = {
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": (
                "sys_id,name,script,description,api_name,client_callable,active,access,"
                "sys_scope,application,caller_access,caller_restriction,"
                "sys_created_on,sys_updated_on,sys_created_by,sys_updated_by"
            ),
        }

        # Determine if we're querying by sys_id or name
        sys_id = _extract_sys_id(params.script_include_id)
        if sys_id:
            url = f"{config.instance_url}/api/now/table/sys_script_include/{sys_id}"
        else:
            # Query by exact name/api_name first
            url = f"{config.instance_url}/api/now/table/sys_script_include"
            query_params["sysparm_query"] = (
                f"name={params.script_include_id}^ORapi_name={params.script_include_id}"
            )

        # Make the request
        headers = auth_manager.get_headers()

        response = requests.get(
            url,
            params=query_params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        # Parse the response
        data = response.json()

        if "result" not in data:
            return {
                "success": False,
                "message": f"Script include not found: {params.script_include_id}",
            }

        # Handle both single result and list of results
        result = data["result"]
        item = _select_script_include_match(result, params.script_include_id)

        # Fallback to a broader search when exact lookup misses.
        if item is None and not sys_id:
            fallback_query_params = dict(query_params)
            fallback_query_params["sysparm_query"] = (
                f"nameLIKE{params.script_include_id}^ORapi_nameLIKE{params.script_include_id}"
            )
            fallback_query_params["sysparm_limit"] = 10

            response = requests.get(
                url,
                params=fallback_query_params,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            fallback_data = response.json()
            item = _select_script_include_match(
                fallback_data.get("result"), params.script_include_id
            )

        if item is None:
            return {
                "success": False,
                "message": f"Script include not found: {params.script_include_id}",
            }

        if not isinstance(item, dict):
            logger.error(
                "Unexpected script include response format for %s: %r",
                params.script_include_id,
                item,
            )
            return {
                "success": False,
                "message": f"Unexpected script include response format: {item}",
            }

        script_include = _normalize_script_include(item)

        return {
            "success": True,
            "message": f"Found script include: {item.get('name')}",
            "script_include": script_include,
        }

    except Exception as e:
        logger.error(f"Error getting script include: {e}")
        return {
            "success": False,
            "message": f"Error getting script include: {str(e)}",
        }


def create_script_include(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateScriptIncludeParams,
) -> ScriptIncludeResponse:
    """Create a new script include in ServiceNow.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The parameters for the request.

    Returns:
        A response indicating the result of the operation.
    """
    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_script_include"

    # Build the request body
    body = dict(params.fields or {})
    body.update(
        {
            "name": params.name,
            "script": params.script,
            "active": str(params.active).lower(),
            "client_callable": str(params.client_callable).lower(),
            "access": params.access,
        }
    )

    if params.description:
        body["description"] = params.description

    if params.api_name:
        body["api_name"] = params.api_name
    if params.application:
        body["sys_scope"] = params.application
    if params.caller_access:
        body["caller_access"] = params.caller_access
    if params.caller_restriction:
        body["caller_restriction"] = params.caller_restriction

    # Make the request
    headers = auth_manager.get_headers()

    try:
        response = requests.post(
            url,
            json=body,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        # Parse the response
        data = response.json()

        if "result" not in data:
            return ScriptIncludeResponse(
                success=False,
                message="Failed to create script include",
            )

        result = data["result"]

        return ScriptIncludeResponse(
            success=True,
            message=f"Created script include: {result.get('name')}",
            script_include_id=result.get("sys_id"),
            script_include_name=result.get("name"),
        )

    except Exception as e:
        logger.error(f"Error creating script include: {e}")
        return ScriptIncludeResponse(
            success=False,
            message=f"Error creating script include: {str(e)}",
        )


def update_script_include(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateScriptIncludeParams,
) -> ScriptIncludeResponse:
    """Update an existing script include in ServiceNow.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The parameters for the request.

    Returns:
        A response indicating the result of the operation.
    """
    # First, get the script include to update
    get_params = GetScriptIncludeParams(script_include_id=params.script_include_id)
    get_result = get_script_include(config, auth_manager, get_params)

    if not get_result["success"]:
        return ScriptIncludeResponse(
            success=False,
            message=get_result["message"],
        )

    script_include = get_result["script_include"]
    sys_id = script_include["sys_id"]

    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_script_include/{sys_id}"

    # Build the request body
    body = dict(params.fields or {})

    if params.name is not None:
        body["name"] = params.name

    if params.script is not None:
        body["script"] = params.script

    if params.description is not None:
        body["description"] = params.description

    if params.api_name is not None:
        body["api_name"] = params.api_name

    if params.client_callable is not None:
        body["client_callable"] = str(params.client_callable).lower()

    if params.active is not None:
        body["active"] = str(params.active).lower()

    if params.access is not None:
        body["access"] = params.access
    if params.application is not None:
        body["sys_scope"] = params.application
    if params.caller_access is not None:
        body["caller_access"] = params.caller_access
    if params.caller_restriction is not None:
        body["caller_restriction"] = params.caller_restriction

    # If no fields to update, return success
    if not body:
        return ScriptIncludeResponse(
            success=True,
            message=f"No changes to update for script include: {script_include['name']}",
            script_include_id=sys_id,
            script_include_name=script_include["name"],
        )

    # Make the request
    headers = auth_manager.get_headers()

    try:
        response = requests.patch(
            url,
            json=body,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        # Parse the response
        data = response.json()

        if "result" not in data or not isinstance(data["result"], dict):
            logger.error(f"Unexpected update response format for script include {sys_id}: {data}")
            return ScriptIncludeResponse(
                success=False,
                message=f"Unexpected update response format from ServiceNow: {data}",
            )

        result = data["result"]
        script_name = result.get("name", script_include.get("name"))

        return ScriptIncludeResponse(
            success=True,
            message=f"Updated script include: {script_name}",
            script_include_id=result.get("sys_id", sys_id),
            script_include_name=script_name,
        )

    except Exception as e:
        logger.error(f"Error updating script include: {e}")
        return ScriptIncludeResponse(
            success=False,
            message=f"Error updating script include: {str(e)}",
        )


def delete_script_include(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteScriptIncludeParams,
) -> ScriptIncludeResponse:
    """Delete a script include from ServiceNow.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The parameters for the request.

    Returns:
        A response indicating the result of the operation.
    """
    # First, get the script include to delete
    get_params = GetScriptIncludeParams(script_include_id=params.script_include_id)
    get_result = get_script_include(config, auth_manager, get_params)

    if not get_result["success"]:
        return ScriptIncludeResponse(
            success=False,
            message=get_result["message"],
        )

    script_include = get_result["script_include"]
    sys_id = script_include["sys_id"]
    name = script_include["name"]

    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_script_include/{sys_id}"

    # Make the request
    headers = auth_manager.get_headers()

    try:
        response = requests.delete(
            url,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        return ScriptIncludeResponse(
            success=True,
            message=f"Deleted script include: {name}",
            script_include_id=sys_id,
            script_include_name=name,
        )

    except Exception as e:
        logger.error(f"Error deleting script include: {e}")
        return ScriptIncludeResponse(
            success=False,
            message=f"Error deleting script include: {str(e)}",
        )


def get_editable_fields(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetEditableFieldsParams,
) -> EditableFieldsResponse:
    """Get information about editable fields for script includes.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The parameters for the request.

    Returns:
        Information about editable fields and optionally current values.
    """
    try:
        # Define all editable fields with their descriptions and validation rules
        editable_fields = {
            "name": {
                "description": "Name of the script include",
                "type": "string",
                "required": False,
                "validation": "Must be unique and not empty",
                "example": "MyCustomScriptInclude",
            },
            "script": {
                "description": "JavaScript code content of the script include",
                "type": "string",
                "required": False,
                "validation": "Valid JavaScript code",
                "example": "function myFunction() { return 'Hello World'; }",
            },
            "description": {
                "description": "Description of what the script include does",
                "type": "string",
                "required": False,
                "validation": "Optional descriptive text",
                "example": "This script include provides utility functions for user management",
            },
            "api_name": {
                "description": "API name for external access",
                "type": "string",
                "required": False,
                "validation": "Must follow API naming conventions",
                "example": "x_custom_app_my_script",
            },
            "client_callable": {
                "description": "Whether the script can be called from client-side scripts",
                "type": "boolean",
                "required": False,
                "validation": "true or false",
                "example": True,
            },
            "active": {
                "description": "Whether the script include is active and available for use",
                "type": "boolean",
                "required": False,
                "validation": "true or false",
                "example": True,
            },
            "access": {
                "description": "Access level for the script include",
                "type": "string",
                "required": False,
                "validation": "package_private, public, or internal",
                "allowed_values": ["package_private", "public", "internal"],
                "example": "package_private",
            },
            "application": {
                "description": "Scoped application this script include belongs to",
                "type": "string",
                "required": False,
                "validation": "Valid application sys_id",
                "example": "sys_id_of_application",
            },
            "caller_access": {
                "description": "Caller access restriction",
                "type": "string",
                "required": False,
                "validation": "none, caller_restriction, or caller_tracking",
                "allowed_values": ["none", "caller_restriction", "caller_tracking"],
                "example": "none",
            },
            "caller_restriction": {
                "description": "Specific caller restriction setting",
                "type": "string",
                "required": False,
                "validation": "Depends on caller_access setting",
                "example": "",
            },
        }

        current_values = None
        if params.script_include_id:
            # Get current values for the specified script include
            get_params = GetScriptIncludeParams(script_include_id=params.script_include_id)
            get_result = get_script_include(config, auth_manager, get_params)

            if get_result["success"]:
                script_include = get_result["script_include"]
                current_values = {
                    "name": script_include.get("name"),
                    "script": script_include.get("script"),
                    "description": script_include.get("description"),
                    "api_name": script_include.get("api_name"),
                    "client_callable": script_include.get("client_callable"),
                    "active": script_include.get("active"),
                    "access": script_include.get("access"),
                    # Additional fields that might not be in current get function
                    "application": script_include.get("application"),
                    "caller_access": script_include.get("caller_access"),
                    "caller_restriction": script_include.get("caller_restriction"),
                }
            else:
                return EditableFieldsResponse(
                    success=False,
                    message=f"Failed to get current values: {get_result['message']}",
                    editable_fields=editable_fields,
                )

        return EditableFieldsResponse(
            success=True,
            message="Retrieved editable fields information",
            editable_fields=editable_fields,
            current_values=current_values,
        )

    except Exception as e:
        logger.error(f"Error getting editable fields: {e}")
        return EditableFieldsResponse(
            success=False,
            message=f"Error getting editable fields: {str(e)}",
            editable_fields={},
        )


def detailed_update_script_include(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DetailedUpdateScriptIncludeParams,
) -> ScriptIncludeResponse:
    """Update a script include with detailed validation and additional fields.

    This function provides enhanced validation and supports additional fields
    beyond the basic update function.

    Args:
        config: The server configuration.
        auth_manager: The authentication manager.
        params: The detailed parameters for the update.

    Returns:
        A response indicating the result of the operation.
    """
    try:
        # First, get the script include to update
        get_params = GetScriptIncludeParams(script_include_id=params.script_include_id)
        get_result = get_script_include(config, auth_manager, get_params)

        if not get_result["success"]:
            return ScriptIncludeResponse(
                success=False,
                message=get_result["message"],
            )

        script_include = get_result["script_include"]
        sys_id = script_include["sys_id"]

        # Validate input parameters
        validation_errors = []

        if params.name is not None and not params.name.strip():
            validation_errors.append("Name cannot be empty")

        if params.script is not None:
            # Basic JavaScript validation - check for balanced braces/brackets
            if params.script.count("{") != params.script.count("}"):
                validation_errors.append("Script has unbalanced curly braces")
            if params.script.count("(") != params.script.count(")"):
                validation_errors.append("Script has unbalanced parentheses")
            if params.script.count("[") != params.script.count("]"):
                validation_errors.append("Script has unbalanced square brackets")

        if params.access is not None and params.access not in [
            "package_private",
            "public",
            "internal",
        ]:
            validation_errors.append("Access must be one of: package_private, public, internal")

        if params.caller_access is not None and params.caller_access not in [
            "none",
            "caller_restriction",
            "caller_tracking",
        ]:
            validation_errors.append(
                "Caller access must be one of: none, caller_restriction, caller_tracking"
            )

        if validation_errors:
            return ScriptIncludeResponse(
                success=False,
                message=f"Validation errors: {'; '.join(validation_errors)}",
            )

        # Build the URL
        url = f"{config.instance_url}/api/now/table/sys_script_include/{sys_id}"

        # Build the request body with all possible fields
        body = dict(params.fields or {})

        # Basic fields
        if params.name is not None:
            body["name"] = params.name.strip()
        if params.script is not None:
            body["script"] = params.script
        if params.description is not None:
            body["description"] = params.description
        if params.api_name is not None:
            body["api_name"] = params.api_name
        if params.client_callable is not None:
            body["client_callable"] = str(params.client_callable).lower()
        if params.active is not None:
            body["active"] = str(params.active).lower()
        if params.access is not None:
            body["access"] = params.access

        # Additional fields
        if params.application is not None:
            body["application"] = params.application
        if params.caller_access is not None:
            body["caller_access"] = params.caller_access
        if params.caller_restriction is not None:
            body["caller_restriction"] = params.caller_restriction

        # If no fields to update, return success
        if not body:
            return ScriptIncludeResponse(
                success=True,
                message=f"No changes to update for script include: {script_include['name']}",
                script_include_id=sys_id,
                script_include_name=script_include["name"],
            )

        # Make the request
        headers = auth_manager.get_headers()

        response = requests.patch(
            url,
            json=body,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        # Parse the response
        data = response.json()

        if "result" not in data or not isinstance(data["result"], dict):
            logger.error(
                f"Unexpected detailed update response format for script include {sys_id}: {data}"
            )
            return ScriptIncludeResponse(
                success=False,
                message=f"Unexpected update response format from ServiceNow: {data}",
            )

        result = data["result"]
        script_name = result.get("name", script_include.get("name"))

        # Log what was updated
        updated_fields = list(body.keys())
        logger.info(f"Updated script include {script_name} fields: {', '.join(updated_fields)}")

        return ScriptIncludeResponse(
            success=True,
            message=f"Updated script include: {script_name} (fields: {', '.join(updated_fields)})",
            script_include_id=result.get("sys_id", sys_id),
            script_include_name=script_name,
        )

    except Exception as e:
        logger.error(f"Error in detailed update of script include: {e}")
        return ScriptIncludeResponse(
            success=False,
            message=f"Error updating script include: {str(e)}",
        )
