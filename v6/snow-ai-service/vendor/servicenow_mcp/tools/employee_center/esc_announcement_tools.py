"""
Employee Service Center announcement tools for the ServiceNow MCP server.

Provides full CRUD operations for ESC announcements (esc_announcement table).
Announcements surface banners, news items, and alerts to employees directly
inside the Employee Center portal homepage and topic pages.
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


class ListESCAnnouncementsParams(BaseModel):
    """Parameters for listing Employee Center announcements."""

    limit: int = Field(10, description="Maximum number of announcements to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    announcement_type: Optional[str] = Field(
        None, description="Filter by type: 'banner', 'news', or 'alert'"
    )
    query: Optional[str] = Field(None, description="Encoded query string (sysparm_query)")


class GetESCAnnouncementParams(BaseModel):
    """Parameters for fetching a single Employee Center announcement."""

    announcement_id: str = Field(..., description="sys_id of the esc_announcement record")


class CreateESCAnnouncementParams(BaseModel):
    """Parameters for creating an Employee Center announcement."""

    title: str = Field(..., description="Title of the announcement")
    text: str = Field(..., description="Body text / HTML content of the announcement")
    announcement_type: str = Field(
        "banner",
        description="Announcement type: 'banner' | 'news' | 'alert'",
    )
    start_date: Optional[str] = Field(
        None, description="Date/time the announcement becomes visible (ISO-8601 or YYYY-MM-DD)"
    )
    end_date: Optional[str] = Field(
        None, description="Date/time the announcement expires (ISO-8601 or YYYY-MM-DD)"
    )
    active: bool = Field(True, description="Whether the announcement is active")
    priority: Optional[int] = Field(
        None, description="Display priority – lower numbers appear first"
    )
    target_audience: Optional[str] = Field(
        None, description="sys_id or name of the target audience group"
    )
    link: Optional[str] = Field(None, description="Optional call-to-action URL")
    link_text: Optional[str] = Field(None, description="Display text for the call-to-action link")


class UpdateESCAnnouncementParams(BaseModel):
    """Parameters for updating an Employee Center announcement."""

    announcement_id: str = Field(..., description="sys_id of the esc_announcement record to update")
    title: Optional[str] = Field(None, description="Title of the announcement")
    text: Optional[str] = Field(None, description="Body text / HTML content")
    announcement_type: Optional[str] = Field(None, description="Announcement type")
    start_date: Optional[str] = Field(None, description="Visibility start date")
    end_date: Optional[str] = Field(None, description="Expiry date")
    active: Optional[bool] = Field(None, description="Whether the announcement is active")
    priority: Optional[int] = Field(None, description="Display priority")
    target_audience: Optional[str] = Field(None, description="Target audience sys_id or name")
    link: Optional[str] = Field(None, description="Call-to-action URL")
    link_text: Optional[str] = Field(None, description="Call-to-action link text")


class DeleteESCAnnouncementParams(BaseModel):
    """Parameters for deleting an Employee Center announcement."""

    announcement_id: str = Field(..., description="sys_id of the esc_announcement record to delete")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def list_esc_announcements(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListESCAnnouncementsParams,
) -> Dict[str, Any]:
    """List Employee Center announcements from ServiceNow."""
    api_url = f"{config.api_url}/table/esc_announcement"

    query_params: Dict[str, Any] = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "true",
    }
    filters: List[str] = []
    if params.active is not None:
        filters.append(f"active={str(params.active).lower()}")
    if params.announcement_type:
        filters.append(f"type={params.announcement_type}")
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
        return {
            "success": True,
            "message": f"Found {len(result)} announcement(s)",
            "announcements": result,
        }
    except requests.HTTPError as exc:
        logger.error("list_esc_announcements HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "announcements": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_esc_announcements error: %s", exc)
        return {"success": False, "message": str(exc), "announcements": []}


def get_esc_announcement(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetESCAnnouncementParams,
) -> Dict[str, Any]:
    """Retrieve a single Employee Center announcement by sys_id."""
    api_url = f"{config.api_url}/table/esc_announcement/{params.announcement_id}"
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
            "message": "Announcement retrieved successfully",
            "announcement_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("get_esc_announcement HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("get_esc_announcement error: %s", exc)
        return {"success": False, "message": str(exc)}


def create_esc_announcement(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateESCAnnouncementParams,
) -> Dict[str, Any]:
    """Create a new Employee Center announcement."""
    api_url = f"{config.api_url}/table/esc_announcement"
    data: Dict[str, Any] = {
        "title": params.title,
        "text": params.text,
        "type": params.announcement_type,
        "active": params.active,
    }
    for field, api_field in (
        ("start_date", "start_date"),
        ("end_date", "end_date"),
        ("target_audience", "target_audience"),
        ("link", "link"),
        ("link_text", "link_text"),
    ):
        value = getattr(params, field, None)
        if value is not None:
            data[api_field] = value
    if params.priority is not None:
        data["priority"] = params.priority

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
            "message": f"ESC announcement '{params.title}' created successfully",
            "announcement_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("create_esc_announcement HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("create_esc_announcement error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_esc_announcement(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateESCAnnouncementParams,
) -> Dict[str, Any]:
    """Update an existing Employee Center announcement."""
    api_url = f"{config.api_url}/table/esc_announcement/{params.announcement_id}"
    data: Dict[str, Any] = {}
    for field, api_field in (
        ("title", "title"),
        ("text", "text"),
        ("announcement_type", "type"),
        ("start_date", "start_date"),
        ("end_date", "end_date"),
        ("target_audience", "target_audience"),
        ("link", "link"),
        ("link_text", "link_text"),
    ):
        value = getattr(params, field, None)
        if value is not None:
            data[api_field] = value
    if params.active is not None:
        data["active"] = params.active
    if params.priority is not None:
        data["priority"] = params.priority

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
            "message": "ESC announcement updated successfully",
            "announcement_id": params.announcement_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_esc_announcement HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_esc_announcement error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_esc_announcement(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteESCAnnouncementParams,
) -> Dict[str, Any]:
    """Delete an Employee Center announcement."""
    api_url = f"{config.api_url}/table/esc_announcement/{params.announcement_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "ESC announcement deleted successfully",
            "announcement_id": params.announcement_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_esc_announcement HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_esc_announcement error: %s", exc)
        return {"success": False, "message": str(exc)}
