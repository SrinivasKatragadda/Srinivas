"""
Employee Service Center topic tools for the ServiceNow MCP server.

Provides CRUD operations for ESC topic records (topic table).
Topics group knowledge articles and service catalog items into
subject-matter areas displayed in the Employee Center navigation.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter models
# ---------------------------------------------------------------------------


class ListESCTopicsParams(BaseModel):
    """Parameters for listing Employee Center topics."""

    limit: int = Field(10, description="Maximum number of topics to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    parent: Optional[str] = Field(None, description="Filter by parent topic sys_id")
    query: Optional[str] = Field(None, description="Encoded query string (sysparm_query)")


class GetESCTopicParams(BaseModel):
    """Parameters for fetching a single Employee Center topic."""

    topic_id: str = Field(..., description="sys_id of the topic record")


class CreateESCTopicParams(BaseModel):
    """Parameters for creating an Employee Center topic."""

    title: str = Field(..., description="Display title of the topic")
    description: Optional[str] = Field(None, description="Description of the topic")
    parent: Optional[str] = Field(None, description="sys_id of the parent topic (for sub-topics)")
    icon: Optional[str] = Field(None, description="Icon name or sys_id for the topic")
    order: Optional[int] = Field(None, description="Display order relative to sibling topics")
    active: bool = Field(True, description="Whether the topic is active and visible")
    keywords: Optional[str] = Field(None, description="Comma-separated search keywords")


class UpdateESCTopicParams(BaseModel):
    """Parameters for updating an Employee Center topic."""

    topic_id: str = Field(..., description="sys_id of the topic record to update")
    title: Optional[str] = Field(None, description="Display title of the topic")
    description: Optional[str] = Field(None, description="Description of the topic")
    parent: Optional[str] = Field(None, description="sys_id of the parent topic")
    icon: Optional[str] = Field(None, description="Icon name or sys_id")
    order: Optional[int] = Field(None, description="Display order")
    active: Optional[bool] = Field(None, description="Whether the topic is active")
    keywords: Optional[str] = Field(None, description="Comma-separated search keywords")


class DeleteESCTopicParams(BaseModel):
    """Parameters for deleting an Employee Center topic."""

    topic_id: str = Field(..., description="sys_id of the topic record to delete")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def list_esc_topics(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListESCTopicsParams,
) -> Dict[str, Any]:
    """List Employee Center topics from ServiceNow."""
    api_url = f"{config.api_url}/table/topic"

    query_params: Dict[str, Any] = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "true",
    }
    filters: List[str] = []
    if params.active is not None:
        filters.append(f"active={str(params.active).lower()}")
    if params.parent:
        filters.append(f"parent={params.parent}")
    if params.query:
        filters.append(params.query)
    if filters:
        query_params["sysparm_query"] = "^".join(filters)

    try:
        response = requests.get(
            api_url,
            params=query_params,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        return {"success": True, "message": f"Found {len(result)} topic(s)", "topics": result}
    except requests.HTTPError as exc:
        logger.error("list_esc_topics HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "topics": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_esc_topics error: %s", exc)
        return {"success": False, "message": str(exc), "topics": []}


def get_esc_topic(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetESCTopicParams,
) -> Dict[str, Any]:
    """Retrieve a single Employee Center topic by sys_id."""
    api_url = f"{config.api_url}/table/topic/{params.topic_id}"
    try:
        response = requests.get(
            api_url,
            params={"sysparm_display_value": "true"},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return {
            "success": True,
            "message": "Topic retrieved successfully",
            "topic_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("get_esc_topic HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("get_esc_topic error: %s", exc)
        return {"success": False, "message": str(exc)}


def create_esc_topic(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateESCTopicParams,
) -> Dict[str, Any]:
    """Create a new Employee Center topic."""
    api_url = f"{config.api_url}/table/topic"
    data: Dict[str, Any] = {"title": params.title, "active": params.active}
    for field in ("description", "parent", "icon", "keywords"):
        value = getattr(params, field, None)
        if value is not None:
            data[field] = value
    if params.order is not None:
        data["order"] = params.order

    try:
        response = requests.post(
            api_url,
            json=data,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return {
            "success": True,
            "message": f"ESC topic '{params.title}' created successfully",
            "topic_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("create_esc_topic HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("create_esc_topic error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_esc_topic(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateESCTopicParams,
) -> Dict[str, Any]:
    """Update an existing Employee Center topic."""
    api_url = f"{config.api_url}/table/topic/{params.topic_id}"
    data: Dict[str, Any] = {}
    for field in ("title", "description", "parent", "icon", "keywords"):
        value = getattr(params, field, None)
        if value is not None:
            data[field] = value
    if params.order is not None:
        data["order"] = params.order
    if params.active is not None:
        data["active"] = params.active

    if not data:
        return {"success": False, "message": "No fields provided to update"}

    try:
        response = requests.patch(
            api_url,
            json=data,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return {
            "success": True,
            "message": "ESC topic updated successfully",
            "topic_id": params.topic_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_esc_topic HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_esc_topic error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_esc_topic(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteESCTopicParams,
) -> Dict[str, Any]:
    """Delete an Employee Center topic."""
    api_url = f"{config.api_url}/table/topic/{params.topic_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "ESC topic deleted successfully",
            "topic_id": params.topic_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_esc_topic HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_esc_topic error: %s", exc)
        return {"success": False, "message": str(exc)}
