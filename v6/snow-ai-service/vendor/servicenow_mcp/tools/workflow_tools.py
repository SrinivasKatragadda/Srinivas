"""
Workflow management tools for the ServiceNow MCP server.

This module provides helpers for viewing and managing workflow records in
ServiceNow. It focuses on the workflow-definition surface, including discovery,
listing, and record-level administration for active and historical workflows.
"""

import logging
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

# Type variable for Pydantic models
T = TypeVar("T", bound=BaseModel)

MAX_WORKFLOW_READ_LIMIT = 100
WORKFLOW_FIELDS = (
    "sys_id",
    "name",
    "description",
    "table",
    "active",
    "sys_created_on",
    "sys_updated_on",
)
WORKFLOW_VERSION_FIELDS = (
    "sys_id",
    "workflow",
    "name",
    "description",
    "version",
    "published",
    "state",
    "sys_created_on",
    "sys_updated_on",
)
WORKFLOW_ACTIVITY_FIELDS = (
    "sys_id",
    "workflow_version",
    "name",
    "description",
    "order",
    "activity_definition",
    "active",
    "sys_created_on",
    "sys_updated_on",
)


class ListWorkflowsParams(BaseModel):
    """Parameters for listing workflows."""

    limit: Optional[int] = Field(
        10,
        ge=1,
        le=MAX_WORKFLOW_READ_LIMIT,
        description="Maximum number of records to return",
    )
    offset: Optional[int] = Field(0, ge=0, description="Offset to start from")
    active: Optional[bool] = Field(None, description="Filter by active status")
    name: Optional[str] = Field(None, description="Filter by name (contains)")
    query: Optional[str] = Field(None, description="Additional query string")


class GetWorkflowDetailsParams(BaseModel):
    """Parameters for getting workflow details."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")
    include_versions: Optional[bool] = Field(False, description="Include workflow versions")


class ListWorkflowVersionsParams(BaseModel):
    """Parameters for listing workflow versions."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")
    limit: Optional[int] = Field(
        10,
        ge=1,
        le=MAX_WORKFLOW_READ_LIMIT,
        description="Maximum number of records to return",
    )
    offset: Optional[int] = Field(0, ge=0, description="Offset to start from")


class GetWorkflowActivitiesParams(BaseModel):
    """Parameters for getting workflow activities."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")
    version: Optional[str] = Field(None, description="Specific version to get activities for")
    limit: Optional[int] = Field(
        25,
        ge=1,
        le=MAX_WORKFLOW_READ_LIMIT,
        description="Maximum number of activity summaries to return",
    )
    offset: Optional[int] = Field(0, ge=0, description="Activity offset to start from")


class CreateWorkflowParams(BaseModel):
    """Parameters for creating a new workflow."""

    name: str = Field(..., description="Name of the workflow")
    description: Optional[str] = Field(None, description="Description of the workflow")
    table: Optional[str] = Field(None, description="Table the workflow applies to")
    active: Optional[bool] = Field(True, description="Whether the workflow is active")
    publish: Optional[bool] = Field(
        True,
        description="Whether to publish the initial workflow version after creation",
    )
    create_studio_flow: Optional[bool] = Field(
        True,
        description=(
            "Whether to also create a Flow Designer record (sys_hub_flow) "
            "for Workflow Studio visibility"
        ),
    )
    studio_attributes: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional attributes for the Flow Designer sys_hub_flow record",
    )
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional attributes for the workflow"
    )


class UpdateWorkflowParams(BaseModel):
    """Parameters for updating a workflow."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")
    name: Optional[str] = Field(None, description="Name of the workflow")
    description: Optional[str] = Field(None, description="Description of the workflow")
    table: Optional[str] = Field(None, description="Table the workflow applies to")
    active: Optional[bool] = Field(None, description="Whether the workflow is active")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional attributes for the workflow"
    )


class ActivateWorkflowParams(BaseModel):
    """Parameters for activating a workflow."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")


class DeactivateWorkflowParams(BaseModel):
    """Parameters for deactivating a workflow."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")


class PublishWorkflowParams(BaseModel):
    """Parameters for publishing a workflow."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")
    version: Optional[str] = Field(
        None, description="Specific version to publish (defaults to latest)"
    )


class AddWorkflowActivityParams(BaseModel):
    """Parameters for adding an activity to a workflow."""

    workflow_id: Optional[str] = Field(None, description="Workflow ID or sys_id (preferred)")
    workflow_version_id: Optional[str] = Field(
        None, description="Workflow version ID (optional if workflow_id is provided)"
    )
    name: str = Field(..., description="Name of the activity")
    description: Optional[str] = Field(None, description="Description of the activity")
    activity_type: str = Field(
        ..., description="Type of activity (e.g., 'approval', 'task', 'notification')"
    )
    position: Optional[int] = Field(
        None, description="Optional explicit order value for the activity"
    )
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional attributes for the activity"
    )


class UpdateWorkflowActivityParams(BaseModel):
    """Parameters for updating a workflow activity."""

    activity_id: str = Field(..., description="Activity ID or sys_id")
    name: Optional[str] = Field(None, description="Name of the activity")
    description: Optional[str] = Field(None, description="Description of the activity")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional attributes for the activity"
    )


class DeleteWorkflowActivityParams(BaseModel):
    """Parameters for deleting a workflow activity."""

    activity_id: str = Field(..., description="Activity ID or sys_id")


class ReorderWorkflowActivitiesParams(BaseModel):
    """Parameters for reordering workflow activities."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")
    activity_ids: List[str] = Field(..., description="List of activity IDs in the desired order")


class DeleteWorkflowParams(BaseModel):
    """Parameters for deleting a workflow."""

    workflow_id: str = Field(..., description="Workflow ID or sys_id")


def _unwrap_params(params: Any, param_class: Type[T]) -> Dict[str, Any]:
    """
    Unwrap parameters if they're wrapped in a Pydantic model.
    This helps handle cases where the parameters are passed as a model instead of a dict.
    """
    if isinstance(params, dict):
        return params
    if isinstance(params, param_class):
        return params.dict(exclude_none=True)
    return params


def _get_auth_and_config(
    auth_manager_or_config: Union[AuthManager, ServerConfig],
    server_config_or_auth: Union[ServerConfig, AuthManager],
) -> tuple[AuthManager, ServerConfig]:
    """
    Get the correct auth_manager and server_config objects.

    This function handles the case where the parameters might be swapped.

    Args:
        auth_manager_or_config: Either an AuthManager or a ServerConfig.
        server_config_or_auth: Either a ServerConfig or an AuthManager.

    Returns:
        tuple[AuthManager, ServerConfig]: The correct auth_manager and server_config.

    Raises:
        ValueError: If the parameters are not of the expected types.
    """
    # Check if the parameters are in the correct order
    if isinstance(auth_manager_or_config, AuthManager) and isinstance(
        server_config_or_auth, ServerConfig
    ):
        return auth_manager_or_config, server_config_or_auth

    # Check if the parameters are swapped
    if isinstance(auth_manager_or_config, ServerConfig) and isinstance(
        server_config_or_auth, AuthManager
    ):
        return server_config_or_auth, auth_manager_or_config

    # If we get here, at least one of the parameters is not of the expected type
    if hasattr(auth_manager_or_config, "get_headers"):
        auth_manager = auth_manager_or_config
    elif hasattr(server_config_or_auth, "get_headers"):
        auth_manager = server_config_or_auth
    else:
        raise ValueError("Cannot find get_headers method in either auth_manager or server_config")

    if hasattr(auth_manager_or_config, "instance_url"):
        server_config = auth_manager_or_config
    elif hasattr(server_config_or_auth, "instance_url"):
        server_config = server_config_or_auth
    else:
        raise ValueError(
            "Cannot find instance_url attribute in either auth_manager or server_config"
        )

    return auth_manager, server_config


def _fields_param(fields: tuple[str, ...]) -> str:
    return ",".join(fields)


def _bounded_page_value(
    value: Any,
    default: int,
    minimum: int,
    maximum: Optional[int] = None,
) -> int:
    try:
        bounded = int(value)
    except (TypeError, ValueError):
        bounded = default
    bounded = max(minimum, bounded)
    if maximum is not None:
        bounded = min(maximum, bounded)
    return bounded


def _display_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _reference_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value")
    return value


def _truncate_text(value: Any, limit: int = 300) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated {len(value) - limit} chars]"


def _without_empty_values(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in record.items() if value not in (None, "")}


def _workflow_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return _without_empty_values(
        {
            "sys_id": _reference_value(record.get("sys_id")),
            "name": _display_value(record.get("name")),
            "description": _truncate_text(_display_value(record.get("description"))),
            "table": _display_value(record.get("table")),
            "active": _display_value(record.get("active")),
            "sys_created_on": _display_value(record.get("sys_created_on")),
            "sys_updated_on": _display_value(record.get("sys_updated_on")),
        }
    )


def _workflow_version_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return _without_empty_values(
        {
            "sys_id": _reference_value(record.get("sys_id")),
            "workflow_id": _reference_value(record.get("workflow")),
            "workflow": _display_value(record.get("workflow")),
            "name": _display_value(record.get("name")),
            "description": _truncate_text(_display_value(record.get("description"))),
            "version": _display_value(record.get("version")),
            "published": _display_value(record.get("published")),
            "state": _display_value(record.get("state")),
            "sys_created_on": _display_value(record.get("sys_created_on")),
            "sys_updated_on": _display_value(record.get("sys_updated_on")),
        }
    )


def _workflow_activity_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return _without_empty_values(
        {
            "sys_id": _reference_value(record.get("sys_id")),
            "workflow_version_id": _reference_value(record.get("workflow_version")),
            "workflow_version": _display_value(record.get("workflow_version")),
            "name": _display_value(record.get("name")),
            "description": _truncate_text(_display_value(record.get("description"))),
            "order": _display_value(record.get("order")),
            "activity_definition": _display_value(record.get("activity_definition")),
            "active": _display_value(record.get("active")),
            "sys_created_on": _display_value(record.get("sys_created_on")),
            "sys_updated_on": _display_value(record.get("sys_updated_on")),
        }
    )


def _studio_flow_summary(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return _without_empty_values(
        {
            "sys_id": _reference_value(record.get("sys_id")),
            "name": _display_value(record.get("name")),
            "description": _truncate_text(_display_value(record.get("description"))),
            "active": _display_value(record.get("active")),
            "sys_created_on": _display_value(record.get("sys_created_on")),
            "sys_updated_on": _display_value(record.get("sys_updated_on")),
        }
    )


def _pagination(response: requests.Response, count: int, limit: int, offset: int) -> Dict[str, Any]:
    total_header = response.headers.get("X-Total-Count")
    try:
        total = int(total_header) if total_header is not None else None
    except (TypeError, ValueError):
        total = None
    has_more = offset + count < total if total is not None else count == limit
    return {
        "count": count,
        "limit": limit,
        "offset": offset,
        "total": total,
        "has_more": has_more,
    }


def _resolve_workflow_version(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    workflow_id: str,
    version: Optional[str] = None,
    prefer_draft: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Resolve a workflow version record by sys_id or version number.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        workflow_id: Parent workflow sys_id
        version: Optional version sys_id or version number
        prefer_draft: When True and version is not provided, prefer unpublished versions

    Returns:
        The resolved wf_workflow_version record or None if not found.
    """
    headers = auth_manager.get_headers()
    url = f"{server_config.instance_url}/api/now/table/wf_workflow_version"

    # If caller provided a sys_id directly, fetch that version.
    if version and len(version) == 32:
        direct_url = f"{url}/{version}"
        response = requests.get(
            direct_url,
            headers=headers,
            params={"sysparm_fields": _fields_param(WORKFLOW_VERSION_FIELDS)},
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        if record and str(record.get("workflow")) == workflow_id:
            return record
        return None

    query_parts = [f"workflow={workflow_id}"]
    if version:
        query_parts.append(f"version={version}")
    elif prefer_draft:
        query_parts.append("published=false")

    query_parts.append("ORDERBYDESCversion")
    query_params = {
        "sysparm_query": "^".join(query_parts),
        "sysparm_limit": 1,
        "sysparm_fields": _fields_param(WORKFLOW_VERSION_FIELDS),
    }

    response = requests.get(url, headers=headers, params=query_params)
    response.raise_for_status()
    versions = response.json().get("result", [])
    if versions:
        return versions[0]

    # Fallback: if no draft found, get latest available.
    if prefer_draft and not version:
        fallback_params = {
            "sysparm_query": f"workflow={workflow_id}^ORDERBYDESCversion",
            "sysparm_limit": 1,
            "sysparm_fields": _fields_param(WORKFLOW_VERSION_FIELDS),
        }
        fallback_response = requests.get(url, headers=headers, params=fallback_params)
        fallback_response.raise_for_status()
        fallback_versions = fallback_response.json().get("result", [])
        if fallback_versions:
            return fallback_versions[0]

    return None


def list_workflows(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    List workflows from ServiceNow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for listing workflows

    Returns:
        Dictionary containing the list of workflows
    """
    params = _unwrap_params(params, ListWorkflowsParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    # Convert parameters to ServiceNow query format
    limit = _bounded_page_value(params.get("limit"), 10, 1, MAX_WORKFLOW_READ_LIMIT)
    offset = _bounded_page_value(params.get("offset"), 0, 0)
    query_params = {
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_fields": _fields_param(WORKFLOW_FIELDS),
    }

    # Build query string
    query_parts = []

    if params.get("active") is not None:
        query_parts.append(f"active={str(params['active']).lower()}")

    if params.get("name"):
        query_parts.append(f"nameLIKE{params['name']}")

    if params.get("query"):
        query_parts.append(params["query"])

    if query_parts:
        query_params["sysparm_query"] = "^".join(query_parts)

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow"

        response = requests.get(url, headers=headers, params=query_params)
        response.raise_for_status()

        workflows = response.json().get("result", [])
        return {
            "workflows": [_workflow_summary(record) for record in workflows],
            **_pagination(response, len(workflows), limit, offset),
        }
    except requests.RequestException as e:
        logger.error(f"Error listing workflows: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error listing workflows: {e}")
        return {"error": str(e)}


def get_workflow_details(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Get detailed information about a specific workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for getting workflow details

    Returns:
        Dictionary containing the workflow details
    """
    params = _unwrap_params(params, GetWorkflowDetailsParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow/{workflow_id}"

        response = requests.get(
            url,
            headers=headers,
            params={"sysparm_fields": _fields_param(WORKFLOW_FIELDS)},
        )
        response.raise_for_status()

        result = response.json()
        return {
            "workflow": _workflow_summary(result.get("result", {})),
        }
    except requests.RequestException as e:
        logger.error(f"Error getting workflow details: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error getting workflow details: {e}")
        return {"error": str(e)}


def list_workflow_versions(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    List versions of a specific workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for listing workflow versions

    Returns:
        Dict[str, Any]: List of workflow versions
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, ListWorkflowVersionsParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Convert parameters to ServiceNow query format
    limit = _bounded_page_value(params.get("limit"), 10, 1, MAX_WORKFLOW_READ_LIMIT)
    offset = _bounded_page_value(params.get("offset"), 0, 0)
    query_params = {
        "sysparm_query": f"workflow={workflow_id}",
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_fields": _fields_param(WORKFLOW_VERSION_FIELDS),
    }

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow_version"

        response = requests.get(url, headers=headers, params=query_params)
        response.raise_for_status()

        versions = response.json().get("result", [])
        return {
            "versions": [_workflow_version_summary(record) for record in versions],
            **_pagination(response, len(versions), limit, offset),
            "workflow_id": workflow_id,
        }
    except requests.RequestException as e:
        logger.error(f"Error listing workflow versions: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error listing workflow versions: {e}")
        return {"error": str(e)}


def get_workflow_activities(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Get activities for a specific workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for getting workflow activities

    Returns:
        Dict[str, Any]: List of workflow activities
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, GetWorkflowActivitiesParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    version_id = params.get("version")
    limit = _bounded_page_value(params.get("limit"), 25, 1, MAX_WORKFLOW_READ_LIMIT)
    offset = _bounded_page_value(params.get("offset"), 0, 0)

    # If no version specified, get the latest published version
    if not version_id:
        try:
            headers = auth_manager.get_headers()
            version_url = f"{server_config.instance_url}/api/now/table/wf_workflow_version"
            version_params = {
                "sysparm_query": f"workflow={workflow_id}^published=true^ORDERBYDESCversion",
                "sysparm_limit": 1,
                "sysparm_fields": "sys_id",
            }

            version_response = requests.get(version_url, headers=headers, params=version_params)
            version_response.raise_for_status()

            version_result = version_response.json()
            versions = version_result.get("result", [])

            if not versions:
                return {
                    "error": f"No published versions found for workflow {workflow_id}",
                    "workflow_id": workflow_id,
                }

            version_id = versions[0]["sys_id"]
        except requests.RequestException as e:
            logger.error(f"Error getting workflow version: {e}")
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error getting workflow version: {e}")
            return {"error": str(e)}

    # Get activities for the version
    try:
        headers = auth_manager.get_headers()
        activities_url = f"{server_config.instance_url}/api/now/table/wf_activity"
        activities_params = {
            "sysparm_query": f"workflow_version={version_id}",
            "sysparm_orderby": "order",
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_fields": _fields_param(WORKFLOW_ACTIVITY_FIELDS),
        }

        activities_response = requests.get(
            activities_url, headers=headers, params=activities_params
        )
        activities_response.raise_for_status()

        activities = activities_response.json().get("result", [])
        return {
            "activities": [_workflow_activity_summary(record) for record in activities],
            **_pagination(activities_response, len(activities), limit, offset),
            "workflow_id": workflow_id,
            "version_id": version_id,
        }
    except requests.RequestException as e:
        logger.error(f"Error getting workflow activities: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error getting workflow activities: {e}")
        return {"error": str(e)}


def create_workflow(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create a new workflow in ServiceNow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for creating a workflow

    Returns:
        Dict[str, Any]: Created workflow details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, CreateWorkflowParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    # Validate required parameters
    if not params.get("name"):
        return {"error": "Workflow name is required"}

    # Prepare data for the API request
    data = {
        "name": params["name"],
    }

    if params.get("description"):
        data["description"] = params["description"]

    if params.get("table"):
        data["table"] = params["table"]

    if params.get("active") is not None:
        data["active"] = str(params["active"]).lower()

    if params.get("attributes"):
        # Add any additional attributes
        data.update(params["attributes"])

    publish_initial_version = params.get("publish", True)
    create_studio_flow = params.get("create_studio_flow", True)

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow"

        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        workflow = result.get("result", {})
        workflow_id = workflow.get("sys_id")
        if not workflow_id:
            return {"error": "Workflow was created but no sys_id was returned"}

        # ServiceNow workflows require at least one wf_workflow_version to be editable/visible.
        version_data = {
            "workflow": workflow_id,
            "name": params["name"],
            "published": "false",
        }
        if params.get("description"):
            version_data["description"] = params["description"]

        versions_url = f"{server_config.instance_url}/api/now/table/wf_workflow_version"
        version_response = requests.post(versions_url, headers=headers, json=version_data)
        version_response.raise_for_status()
        workflow_version = version_response.json().get("result", {})

        if publish_initial_version and workflow_version.get("sys_id"):
            publish_url = (
                f"{server_config.instance_url}/api/now/table/wf_workflow_version/"
                f"{workflow_version['sys_id']}"
            )
            publish_response = requests.patch(
                publish_url,
                headers=headers,
                json={"published": "true"},
            )
            publish_response.raise_for_status()
            workflow_version = publish_response.json().get("result", workflow_version)

        studio_flow = None
        warnings: List[str] = []

        if create_studio_flow:
            # Flow Designer (Workflow Studio) list is backed by sys_hub_flow.
            studio_data = {
                "name": params["name"],
                "active": str(params.get("active", True)).lower(),
            }
            if params.get("description"):
                studio_data["description"] = params["description"]
            if params.get("studio_attributes"):
                studio_data.update(params["studio_attributes"])

            try:
                studio_url = f"{server_config.instance_url}/api/now/table/sys_hub_flow"
                studio_response = requests.post(studio_url, headers=headers, json=studio_data)
                studio_response.raise_for_status()
                studio_flow = studio_response.json().get("result", {})
            except requests.RequestException as studio_error:
                logger.warning(
                    "Legacy workflow created but Flow Designer flow creation failed: %s",
                    studio_error,
                )
                warnings.append(
                    "Legacy workflow was created, but creating the Flow Designer "
                    "sys_hub_flow record failed."
                )

        return {
            "workflow": _workflow_summary(workflow),
            "workflow_version": _workflow_version_summary(workflow_version),
            "published": publish_initial_version,
            "studio_flow": _studio_flow_summary(studio_flow),
            "studio_visible": studio_flow is not None,
            "warnings": warnings,
            "message": "Workflow created successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error creating workflow: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error creating workflow: {e}")
        return {"error": str(e)}


def update_workflow(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Update an existing workflow in ServiceNow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for updating a workflow

    Returns:
        Dict[str, Any]: Updated workflow details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, UpdateWorkflowParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Prepare data for the API request
    data = {}

    if params.get("name"):
        data["name"] = params["name"]

    if params.get("description") is not None:
        data["description"] = params["description"]

    if params.get("table"):
        data["table"] = params["table"]

    if params.get("active") is not None:
        data["active"] = str(params["active"]).lower()

    if params.get("attributes"):
        # Add any additional attributes
        data.update(params["attributes"])

    if not data:
        return {"error": "No update parameters provided"}

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow/{workflow_id}"

        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return {
            "workflow": _workflow_summary(result.get("result", {})),
            "message": "Workflow updated successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error updating workflow: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error updating workflow: {e}")
        return {"error": str(e)}


def activate_workflow(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Activate a workflow in ServiceNow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for activating a workflow

    Returns:
        Dict[str, Any]: Activated workflow details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, ActivateWorkflowParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Prepare data for the API request
    data = {
        "active": "true",
    }

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow/{workflow_id}"

        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return {
            "workflow": _workflow_summary(result.get("result", {})),
            "message": "Workflow activated successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error activating workflow: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error activating workflow: {e}")
        return {"error": str(e)}


def deactivate_workflow(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deactivate a workflow in ServiceNow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for deactivating a workflow

    Returns:
        Dict[str, Any]: Deactivated workflow details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, DeactivateWorkflowParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Prepare data for the API request
    data = {
        "active": "false",
    }

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow/{workflow_id}"

        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return {
            "workflow": _workflow_summary(result.get("result", {})),
            "message": "Workflow deactivated successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error deactivating workflow: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error deactivating workflow: {e}")
        return {"error": str(e)}


def publish_workflow(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Publish a workflow version in ServiceNow to make it visible in Workflow Studio.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for publishing a workflow

    Returns:
        Dict[str, Any]: Published workflow version details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, PublishWorkflowParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Get the workflow version to publish
    version = params.get("version")
    version_record = None
    try:
        version_record = _resolve_workflow_version(
            auth_manager=auth_manager,
            server_config=server_config,
            workflow_id=workflow_id,
            version=version,
            prefer_draft=False,
        )
    except requests.RequestException as e:
        logger.error(f"Error getting workflow versions: {e}")
        return {"error": str(e)}

    if not version_record:
        return {"error": "No workflow versions found for this workflow"}

    version_id = version_record.get("sys_id")
    if not version_id:
        return {"error": "Resolved workflow version did not include sys_id"}

    # Prepare data for the API request to publish the version
    data = {
        "published": "true",
    }

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow_version/{version_id}"

        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return {
            "workflow_version": _workflow_version_summary(result.get("result", {})),
            "workflow_id": workflow_id,
            "version_id": version_id,
            "message": "Workflow published successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error publishing workflow: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error publishing workflow: {e}")
        return {"error": str(e)}


def add_workflow_activity(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Add a new activity to a workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for adding a workflow activity

    Returns:
        Dict[str, Any]: Added workflow activity details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, AddWorkflowActivityParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    workflow_version_id = params.get("workflow_version_id")
    if not workflow_version_id and not workflow_id:
        return {"error": "Either workflow_id or workflow_version_id is required"}

    activity_name = params.get("name")
    if not activity_name:
        return {"error": "Activity name is required"}

    activity_type = params.get("activity_type")
    if not activity_type:
        return {"error": "Activity type is required"}

    try:
        headers = auth_manager.get_headers()

        # Resolve the editable version to target.
        version_record = None
        if workflow_version_id:
            version_url = (
                f"{server_config.instance_url}/api/now/table/wf_workflow_version/"
                f"{workflow_version_id}"
            )
            version_response = requests.get(
                version_url,
                headers=headers,
                params={"sysparm_fields": "sys_id,workflow"},
            )
            version_response.raise_for_status()
            version_record = version_response.json().get("result", {})
            if not version_record:
                return {"error": "Workflow version not found"}
            if not workflow_id:
                workflow_id = str(version_record.get("workflow", ""))
        else:
            version_record = _resolve_workflow_version(
                auth_manager=auth_manager,
                server_config=server_config,
                workflow_id=workflow_id,
                prefer_draft=True,
            )
            if not version_record:
                return {
                    "error": f"No workflow versions found for workflow {workflow_id}",
                    "workflow_id": workflow_id,
                }
            workflow_version_id = version_record.get("sys_id")

        if not workflow_version_id:
            return {"error": "Unable to resolve workflow version ID"}

        # Determine activity order if not explicitly provided.
        order_value = params.get("position")
        if order_value is None:
            order_url = f"{server_config.instance_url}/api/now/table/wf_activity"
            order_params = {
                "sysparm_query": f"workflow_version={workflow_version_id}^ORDERBYDESCorder",
                "sysparm_limit": 1,
                "sysparm_fields": "order",
            }
            order_response = requests.get(order_url, headers=headers, params=order_params)
            order_response.raise_for_status()
            order_result = order_response.json().get("result", [])

            if order_result:
                try:
                    last_order = int(order_result[0].get("order", 0))
                except (TypeError, ValueError):
                    last_order = 0
                order_value = last_order + 100
            else:
                order_value = 100
        else:
            try:
                order_value = int(order_value)
            except (TypeError, ValueError):
                return {"error": "position must be an integer"}

        # Prepare data for the API request
        data = {
            "workflow_version": workflow_version_id,
            "name": activity_name,
            "order": order_value,
            "activity_definition": activity_type,
        }

        if params.get("description"):
            data["description"] = params["description"]

        if params.get("attributes"):
            # Add any additional attributes
            data.update(params["attributes"])

        url = f"{server_config.instance_url}/api/now/table/wf_activity"

        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return {
            "activity": _workflow_activity_summary(result.get("result", {})),
            "workflow_id": workflow_id,
            "version_id": workflow_version_id,
            "message": "Activity added successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error adding workflow activity: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error adding workflow activity: {e}")
        return {"error": str(e)}


def update_workflow_activity(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Update an existing activity in a workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for updating a workflow activity

    Returns:
        Dict[str, Any]: Updated workflow activity details
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, UpdateWorkflowActivityParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    activity_id = params.get("activity_id")
    if not activity_id:
        return {"error": "Activity ID is required"}

    # Prepare data for the API request
    data = {}

    if params.get("name"):
        data["name"] = params["name"]

    if params.get("description") is not None:
        data["description"] = params["description"]

    if params.get("attributes"):
        # Add any additional attributes
        data.update(params["attributes"])

    if not data:
        return {"error": "No update parameters provided"}

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_activity/{activity_id}"

        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return {
            "activity": _workflow_activity_summary(result.get("result", {})),
            "message": "Activity updated successfully",
        }
    except requests.RequestException as e:
        logger.error(f"Error updating workflow activity: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error updating workflow activity: {e}")
        return {"error": str(e)}


def delete_workflow_activity(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Delete an activity from a workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for deleting a workflow activity

    Returns:
        Dict[str, Any]: Result of the deletion operation
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, DeleteWorkflowActivityParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    activity_id = params.get("activity_id")
    if not activity_id:
        return {"error": "Activity ID is required"}

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_activity/{activity_id}"

        response = requests.delete(url, headers=headers)
        response.raise_for_status()

        return {
            "message": "Activity deleted successfully",
            "activity_id": activity_id,
        }
    except requests.RequestException as e:
        logger.error(f"Error deleting workflow activity: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error deleting workflow activity: {e}")
        return {"error": str(e)}


def reorder_workflow_activities(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Reorder activities in a workflow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for reordering workflow activities

    Returns:
        Dict[str, Any]: Result of the reordering operation
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, ReorderWorkflowActivitiesParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    activity_ids = params.get("activity_ids")
    if not activity_ids:
        return {"error": "Activity IDs are required"}

    # Make the API requests to update the order of each activity
    try:
        headers = auth_manager.get_headers()
        results = []

        for i, activity_id in enumerate(activity_ids):
            # Calculate the new order value (100, 200, 300, etc.)
            new_order = (i + 1) * 100

            url = f"{server_config.instance_url}/api/now/table/wf_activity/{activity_id}"
            data = {"order": new_order}

            try:
                response = requests.patch(url, headers=headers, json=data)
                response.raise_for_status()

                results.append(
                    {
                        "activity_id": activity_id,
                        "new_order": new_order,
                        "success": True,
                    }
                )
            except requests.RequestException as e:
                logger.error(f"Error updating activity order: {e}")
                results.append(
                    {
                        "activity_id": activity_id,
                        "error": str(e),
                        "success": False,
                    }
                )

        return {
            "message": "Activities reordered",
            "workflow_id": workflow_id,
            "results": results,
        }
    except Exception as e:
        logger.error(f"Unexpected error reordering workflow activities: {e}")
        return {"error": str(e)}


def delete_workflow(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Delete a workflow from ServiceNow.

    Args:
        auth_manager: Authentication manager
        server_config: Server configuration
        params: Parameters for deleting a workflow

    Returns:
        Dict[str, Any]: Result of the deletion operation
    """
    # Unwrap parameters if needed
    params = _unwrap_params(params, DeleteWorkflowParams)

    # Get the correct auth_manager and server_config
    try:
        auth_manager, server_config = _get_auth_and_config(auth_manager, server_config)
    except ValueError as e:
        logger.error(f"Error getting auth and config: {e}")
        return {"error": str(e)}

    workflow_id = params.get("workflow_id")
    if not workflow_id:
        return {"error": "Workflow ID is required"}

    # Make the API request
    try:
        headers = auth_manager.get_headers()
        url = f"{server_config.instance_url}/api/now/table/wf_workflow/{workflow_id}"

        response = requests.delete(url, headers=headers)
        response.raise_for_status()

        return {
            "message": f"Workflow {workflow_id} deleted successfully",
            "workflow_id": workflow_id,
        }
    except requests.RequestException as e:
        logger.error(f"Error deleting workflow: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error deleting workflow: {e}")
        return {"error": str(e)}
