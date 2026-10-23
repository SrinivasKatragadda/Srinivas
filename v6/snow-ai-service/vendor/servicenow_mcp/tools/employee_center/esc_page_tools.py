"""
Employee Service Center page tools for the ServiceNow MCP server.

Provides full CRUD operations for ESC portal pages (esp_page table).
Pages are the primary content containers in the Employee Center portal
and map directly to the portal's navigation structure.
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


class ListESCPagesParams(BaseModel):
    """Parameters for listing Employee Center pages."""

    limit: int = Field(10, description="Maximum number of pages to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    query: Optional[str] = Field(None, description="Encoded query string (sysparm_query)")


class GetESCPageParams(BaseModel):
    """Parameters for fetching a single Employee Center page."""

    page_id: str = Field(..., description="sys_id or unique identifier of the esp_page record")


class CreateESCPageParams(BaseModel):
    """Parameters for creating a new Employee Center page."""

    title: str = Field(..., description="Display title of the page")
    url: str = Field(..., description="URL suffix used in the Employee Center portal")
    description: Optional[str] = Field(None, description="Short description / purpose of the page")
    template: Optional[str] = Field(
        None, description="Page layout template (e.g. 'two_columns_wide_left')"
    )
    active: bool = Field(True, description="Whether the page is published and visible")
    short_description: Optional[str] = Field(None, description="Short description shown in nav")
    icon: Optional[str] = Field(None, description="Icon name or sys_id for the page")


class UpdateESCPageParams(BaseModel):
    """Parameters for updating an existing Employee Center page."""

    page_id: str = Field(..., description="sys_id of the esp_page record to update")
    title: Optional[str] = Field(None, description="Display title of the page")
    url: Optional[str] = Field(None, description="URL suffix of the page")
    description: Optional[str] = Field(None, description="Description of the page")
    template: Optional[str] = Field(None, description="Page layout template")
    active: Optional[bool] = Field(None, description="Whether the page is active")
    short_description: Optional[str] = Field(None, description="Short description")
    icon: Optional[str] = Field(None, description="Icon name or sys_id")


class DeleteESCPageParams(BaseModel):
    """Parameters for deleting an Employee Center page."""

    page_id: str = Field(..., description="sys_id of the esp_page record to delete")


class ESCPageResponse(BaseModel):
    """Standard response for Employee Center page operations."""

    success: bool
    message: str
    page_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def list_esc_pages(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListESCPagesParams,
) -> Dict[str, Any]:
    """List Employee Center portal pages from ServiceNow."""
    api_url = f"{config.api_url}/table/esp_page"

    query_params: Dict[str, Any] = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "true",
    }
    filters: List[str] = []
    if params.active is not None:
        filters.append(f"active={str(params.active).lower()}")
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
        return {"success": True, "message": f"Found {len(result)} page(s)", "pages": result}
    except requests.HTTPError as exc:
        logger.error("list_esc_pages HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "pages": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_esc_pages error: %s", exc)
        return {"success": False, "message": str(exc), "pages": []}


def get_esc_page(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetESCPageParams,
) -> Dict[str, Any]:
    """Retrieve a single Employee Center page by sys_id."""
    api_url = f"{config.api_url}/table/esp_page/{params.page_id}"
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
            "message": "Page retrieved successfully",
            "page_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("get_esc_page HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("get_esc_page error: %s", exc)
        return {"success": False, "message": str(exc)}


def create_esc_page(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateESCPageParams,
) -> Dict[str, Any]:
    """Create a new Employee Center portal page."""
    api_url = f"{config.api_url}/table/esp_page"
    data: Dict[str, Any] = {
        "title": params.title,
        "url": params.url,
        "active": params.active,
    }
    if params.description:
        data["description"] = params.description
    if params.template:
        data["template"] = params.template
    if params.short_description:
        data["short_description"] = params.short_description
    if params.icon:
        data["icon"] = params.icon

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
            "message": f"ESC page '{params.title}' created successfully",
            "page_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("create_esc_page HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("create_esc_page error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_esc_page(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateESCPageParams,
) -> Dict[str, Any]:
    """Update an existing Employee Center portal page."""
    api_url = f"{config.api_url}/table/esp_page/{params.page_id}"
    data: Dict[str, Any] = {}
    for field in ("title", "url", "description", "template", "short_description", "icon"):
        value = getattr(params, field, None)
        if value is not None:
            data[field] = value
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
            "message": "ESC page updated successfully",
            "page_id": params.page_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_esc_page HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_esc_page error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_esc_page(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteESCPageParams,
) -> Dict[str, Any]:
    """Delete an Employee Center portal page."""
    api_url = f"{config.api_url}/table/esp_page/{params.page_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "ESC page deleted successfully",
            "page_id": params.page_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_esc_page HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_esc_page error: %s", exc)
        return {"success": False, "message": str(exc)}
