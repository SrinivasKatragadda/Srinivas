"""
Dashboard tools for the ServiceNow MCP server.

Provides CRUD operations for Performance Analytics dashboards
(pa_dashboard) and dashboard tabs (pa_dashboard_tab).
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter models – dashboards (pa_dashboard)
# ---------------------------------------------------------------------------


class ListDashboardsParams(BaseModel):
    """Parameters for listing PA dashboards."""

    limit: int = Field(10, description="Maximum number of dashboards to return")
    offset: int = Field(0, description="Offset for pagination")
    user: Optional[str] = Field(None, description="Filter by owner user sys_id or user_name")
    query: Optional[str] = Field(None, description="Encoded query string (sysparm_query)")


class GetDashboardParams(BaseModel):
    """Parameters for fetching a single dashboard."""

    dashboard_id: str = Field(..., description="sys_id of the pa_dashboard record")


class CreateDashboardParams(BaseModel):
    """Parameters for creating a new PA dashboard."""

    name: str = Field(..., description="Display name of the dashboard")
    description: Optional[str] = Field(None, description="Description of the dashboard")
    user: Optional[str] = Field(
        None, description="Owner user sys_id (defaults to authenticated user)"
    )
    roles: Optional[List[str]] = Field(
        None, description="Role sys_ids or names that can view the dashboard"
    )
    is_public: bool = Field(False, description="Whether the dashboard is publicly visible")


class UpdateDashboardParams(BaseModel):
    """Parameters for updating an existing PA dashboard."""

    dashboard_id: str = Field(..., description="sys_id of the pa_dashboard record to update")
    name: Optional[str] = Field(None, description="Display name of the dashboard")
    description: Optional[str] = Field(None, description="Description of the dashboard")
    is_public: Optional[bool] = Field(None, description="Whether the dashboard is publicly visible")


class DeleteDashboardParams(BaseModel):
    """Parameters for deleting a PA dashboard."""

    dashboard_id: str = Field(..., description="sys_id of the pa_dashboard record to delete")


# ---------------------------------------------------------------------------
# Parameter models – dashboard tabs (pa_dashboard_tab)
# ---------------------------------------------------------------------------


class ListDashboardTabsParams(BaseModel):
    """Parameters for listing tabs within a PA dashboard."""

    dashboard_id: str = Field(..., description="sys_id of the parent pa_dashboard record")


class AddDashboardTabParams(BaseModel):
    """Parameters for adding a tab to an existing PA dashboard."""

    dashboard_id: str = Field(..., description="sys_id of the parent pa_dashboard record")
    name: str = Field(..., description="Display name of the tab")
    order: Optional[int] = Field(None, description="Display order of the tab (lower = left)")


class UpdateDashboardTabParams(BaseModel):
    """Parameters for updating a PA dashboard tab."""

    tab_id: str = Field(..., description="sys_id of the pa_dashboard_tab record to update")
    name: Optional[str] = Field(None, description="Display name of the tab")
    order: Optional[int] = Field(None, description="Display order of the tab")


class DeleteDashboardTabParams(BaseModel):
    """Parameters for deleting a PA dashboard tab."""

    tab_id: str = Field(..., description="sys_id of the pa_dashboard_tab record to delete")


# ---------------------------------------------------------------------------
# Dashboard tool implementations
# ---------------------------------------------------------------------------


def list_dashboards(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListDashboardsParams,
) -> Dict[str, Any]:
    """List Performance Analytics dashboards from ServiceNow."""
    api_url = f"{config.api_url}/table/pa_dashboards"
    query_params: Dict[str, Any] = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "true",
        "sysparm_fields": "sys_id,name,description,user,is_public,sys_updated_on",
    }
    filters: List[str] = []
    if params.user:
        filters.append(f"user={params.user}")
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
            "message": f"Found {len(result)} dashboard(s)",
            "dashboards": result,
        }
    except requests.HTTPError as exc:
        logger.error("list_dashboards HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "dashboards": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_dashboards error: %s", exc)
        return {"success": False, "message": str(exc), "dashboards": []}


def get_dashboard(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetDashboardParams,
) -> Dict[str, Any]:
    """Fetch a single PA dashboard by sys_id, including its tabs."""
    api_url = f"{config.api_url}/table/pa_dashboards/{params.dashboard_id}"
    try:
        response = requests.get(
            api_url,
            params={"sysparm_display_value": "true"},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        dashboard = response.json().get("result", {})

        # Enrich with tabs
        tabs_url = f"{config.api_url}/table/pa_dashboard_tab"
        tabs_resp = requests.get(
            tabs_url,
            params={
                "sysparm_query": f"dashboard={params.dashboard_id}^ORDERBYorder",
                "sysparm_display_value": "true",
                "sysparm_fields": "sys_id,name,order",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        tabs = tabs_resp.json().get("result", []) if tabs_resp.ok else []

        return {
            "success": True,
            "message": "Dashboard retrieved successfully",
            "dashboard_id": dashboard.get("sys_id"),
            "data": dashboard,
            "tabs": tabs,
        }
    except requests.HTTPError as exc:
        logger.error("get_dashboard HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("get_dashboard error: %s", exc)
        return {"success": False, "message": str(exc)}


def create_dashboard(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateDashboardParams,
) -> Dict[str, Any]:
    """Create a new PA dashboard."""
    api_url = f"{config.api_url}/table/pa_dashboards"
    data: Dict[str, Any] = {"name": params.name, "is_public": params.is_public}
    if params.description:
        data["description"] = params.description
    if params.user:
        data["user"] = params.user
    if params.roles:
        data["roles"] = ",".join(params.roles)

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
            "message": f"Dashboard '{params.name}' created successfully",
            "dashboard_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("create_dashboard HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("create_dashboard error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_dashboard(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateDashboardParams,
) -> Dict[str, Any]:
    """Update an existing PA dashboard."""
    api_url = f"{config.api_url}/table/pa_dashboards/{params.dashboard_id}"
    data: Dict[str, Any] = {}
    for field in ("name", "description"):
        value = getattr(params, field, None)
        if value is not None:
            data[field] = value
    if params.is_public is not None:
        data["is_public"] = params.is_public

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
            "message": "Dashboard updated successfully",
            "dashboard_id": params.dashboard_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_dashboard HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_dashboard error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_dashboard(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteDashboardParams,
) -> Dict[str, Any]:
    """Delete a PA dashboard."""
    api_url = f"{config.api_url}/table/pa_dashboards/{params.dashboard_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Dashboard deleted successfully",
            "dashboard_id": params.dashboard_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_dashboard HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_dashboard error: %s", exc)
        return {"success": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# Dashboard tab tool implementations
# ---------------------------------------------------------------------------


def list_dashboard_tabs(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListDashboardTabsParams,
) -> Dict[str, Any]:
    """List all tabs for a given PA dashboard."""
    api_url = f"{config.api_url}/table/pa_dashboard_tab"
    try:
        response = requests.get(
            api_url,
            params={
                "sysparm_query": f"dashboard={params.dashboard_id}^ORDERBYorder",
                "sysparm_display_value": "true",
                "sysparm_fields": "sys_id,name,order,dashboard",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        return {"success": True, "message": f"Found {len(result)} tab(s)", "tabs": result}
    except requests.HTTPError as exc:
        logger.error("list_dashboard_tabs HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "tabs": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_dashboard_tabs error: %s", exc)
        return {"success": False, "message": str(exc), "tabs": []}


def add_dashboard_tab(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: AddDashboardTabParams,
) -> Dict[str, Any]:
    """Add a new tab to an existing PA dashboard."""
    api_url = f"{config.api_url}/table/pa_dashboard_tab"
    data: Dict[str, Any] = {"dashboard": params.dashboard_id, "name": params.name}
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
            "message": f"Dashboard tab '{params.name}' added successfully",
            "tab_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("add_dashboard_tab HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("add_dashboard_tab error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_dashboard_tab(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateDashboardTabParams,
) -> Dict[str, Any]:
    """Update a PA dashboard tab."""
    api_url = f"{config.api_url}/table/pa_dashboard_tab/{params.tab_id}"
    data: Dict[str, Any] = {}
    if params.name is not None:
        data["name"] = params.name
    if params.order is not None:
        data["order"] = params.order

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
            "message": "Dashboard tab updated successfully",
            "tab_id": params.tab_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_dashboard_tab HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_dashboard_tab error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_dashboard_tab(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteDashboardTabParams,
) -> Dict[str, Any]:
    """Delete a PA dashboard tab."""
    api_url = f"{config.api_url}/table/pa_dashboard_tab/{params.tab_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Dashboard tab deleted successfully",
            "tab_id": params.tab_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_dashboard_tab HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_dashboard_tab error: %s", exc)
        return {"success": False, "message": str(exc)}
