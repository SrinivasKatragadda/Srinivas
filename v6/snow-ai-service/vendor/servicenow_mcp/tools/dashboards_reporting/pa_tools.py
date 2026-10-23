"""
Performance Analytics (PA) indicator and widget tools for the ServiceNow MCP server.

Covers the pa_indicator and pa_widget_instance tables, enabling agents to
define KPI indicators and inspect dashboard widget placements.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter models – PA indicators (pa_indicator)
# ---------------------------------------------------------------------------


class ListPAIndicatorsParams(BaseModel):
    """Parameters for listing Performance Analytics indicators."""

    limit: int = Field(10, description="Maximum number of indicators to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    indicator_type: Optional[str] = Field(
        None,
        description="Filter by indicator type: 'manual' | 'automated' | 'breakdown'",
    )
    query: Optional[str] = Field(None, description="Encoded query string (sysparm_query)")


class GetPAIndicatorParams(BaseModel):
    """Parameters for fetching a single PA indicator."""

    indicator_id: str = Field(..., description="sys_id of the pa_indicator record")


class CreatePAIndicatorParams(BaseModel):
    """Parameters for creating a Performance Analytics indicator."""

    name: str = Field(..., description="Display name of the indicator")
    description: Optional[str] = Field(
        None, description="Description of what the indicator measures"
    )
    indicator_type: str = Field(
        "automated",
        description="Indicator type: 'manual' | 'automated'",
    )
    table: Optional[str] = Field(
        None, description="Source table for automated indicators (e.g. 'incident')"
    )
    filter_query: Optional[str] = Field(
        None, description="Encoded query to filter the source table"
    )
    aggregation: Optional[str] = Field(
        None,
        description="Aggregation type: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN'",
    )
    aggregate_field: Optional[str] = Field(
        None, description="Field to aggregate when using SUM/AVG/MAX/MIN"
    )
    unit: Optional[str] = Field(None, description="Unit of measurement (e.g. '%', 'hours', '$')")
    direction: Optional[str] = Field(
        None,
        description="Positive trend direction: 'maximize' | 'minimize'",
    )
    active: bool = Field(True, description="Whether the indicator is active")
    category: Optional[str] = Field(
        None, description="sys_id of the pa_indicator_category the indicator belongs to"
    )


class UpdatePAIndicatorParams(BaseModel):
    """Parameters for updating a Performance Analytics indicator."""

    indicator_id: str = Field(..., description="sys_id of the pa_indicator record to update")
    name: Optional[str] = Field(None, description="Display name")
    description: Optional[str] = Field(None, description="Description")
    table: Optional[str] = Field(None, description="Source table")
    filter_query: Optional[str] = Field(None, description="Encoded filter query")
    aggregation: Optional[str] = Field(None, description="Aggregation type")
    aggregate_field: Optional[str] = Field(None, description="Aggregation field")
    unit: Optional[str] = Field(None, description="Unit of measurement")
    direction: Optional[str] = Field(None, description="Positive trend direction")
    active: Optional[bool] = Field(None, description="Whether the indicator is active")
    category: Optional[str] = Field(None, description="sys_id of the indicator category")


class DeletePAIndicatorParams(BaseModel):
    """Parameters for deleting a PA indicator."""

    indicator_id: str = Field(..., description="sys_id of the pa_indicator record to delete")


# ---------------------------------------------------------------------------
# Parameter models – PA widget instances (pa_widget_instance)
# ---------------------------------------------------------------------------


class ListPAWidgetInstancesParams(BaseModel):
    """Parameters for listing PA widget instances on a dashboard tab."""

    tab_id: str = Field(..., description="sys_id of the pa_dashboard_tab to inspect")
    limit: int = Field(20, description="Maximum number of widget instances to return")


# ---------------------------------------------------------------------------
# PA Indicator tool implementations
# ---------------------------------------------------------------------------


def list_pa_indicators(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListPAIndicatorsParams,
) -> Dict[str, Any]:
    """List Performance Analytics indicators from ServiceNow."""
    api_url = f"{config.api_url}/table/pa_indicators"
    query_params: Dict[str, Any] = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "true",
        "sysparm_fields": (
            "sys_id,name,description,indicator_type,table,aggregation,unit,direction,active,category"
        ),
    }
    filters: List[str] = []
    if params.active is not None:
        filters.append(f"active={str(params.active).lower()}")
    if params.indicator_type:
        filters.append(f"indicator_type={params.indicator_type}")
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
            "message": f"Found {len(result)} indicator(s)",
            "indicators": result,
        }
    except requests.HTTPError as exc:
        logger.error("list_pa_indicators HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "indicators": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_pa_indicators error: %s", exc)
        return {"success": False, "message": str(exc), "indicators": []}


def get_pa_indicator(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetPAIndicatorParams,
) -> Dict[str, Any]:
    """Fetch a single PA indicator by sys_id."""
    api_url = f"{config.api_url}/table/pa_indicators/{params.indicator_id}"
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
            "message": "PA indicator retrieved successfully",
            "indicator_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("get_pa_indicator HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("get_pa_indicator error: %s", exc)
        return {"success": False, "message": str(exc)}


def create_pa_indicator(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreatePAIndicatorParams,
) -> Dict[str, Any]:
    """Create a new Performance Analytics indicator."""
    api_url = f"{config.api_url}/table/pa_indicators"
    data: Dict[str, Any] = {
        "name": params.name,
        "indicator_type": params.indicator_type,
        "active": params.active,
    }
    for field, api_field in (
        ("description", "description"),
        ("table", "table"),
        ("filter_query", "filter"),
        ("aggregation", "aggregation"),
        ("aggregate_field", "aggregate_field"),
        ("unit", "unit"),
        ("direction", "direction"),
        ("category", "category"),
    ):
        value = getattr(params, field, None)
        if value is not None:
            data[api_field] = value

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
            "message": f"PA indicator '{params.name}' created successfully",
            "indicator_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("create_pa_indicator HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("create_pa_indicator error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_pa_indicator(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdatePAIndicatorParams,
) -> Dict[str, Any]:
    """Update an existing PA indicator."""
    api_url = f"{config.api_url}/table/pa_indicators/{params.indicator_id}"
    data: Dict[str, Any] = {}
    for field, api_field in (
        ("name", "name"),
        ("description", "description"),
        ("table", "table"),
        ("filter_query", "filter"),
        ("aggregation", "aggregation"),
        ("aggregate_field", "aggregate_field"),
        ("unit", "unit"),
        ("direction", "direction"),
        ("category", "category"),
    ):
        value = getattr(params, field, None)
        if value is not None:
            data[api_field] = value
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
            "message": "PA indicator updated successfully",
            "indicator_id": params.indicator_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_pa_indicator HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_pa_indicator error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_pa_indicator(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeletePAIndicatorParams,
) -> Dict[str, Any]:
    """Delete a PA indicator."""
    api_url = f"{config.api_url}/table/pa_indicators/{params.indicator_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "PA indicator deleted successfully",
            "indicator_id": params.indicator_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_pa_indicator HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_pa_indicator error: %s", exc)
        return {"success": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# PA widget instance tool implementations
# ---------------------------------------------------------------------------


def list_pa_widget_instances(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListPAWidgetInstancesParams,
) -> Dict[str, Any]:
    """
    List PA widget instances placed on a given dashboard tab.

    Widget instances link a PA indicator (or other data source) to a
    specific position within a dashboard tab.
    """
    api_url = f"{config.api_url}/table/pa_widget_instance"
    try:
        response = requests.get(
            api_url,
            params={
                "sysparm_query": f"tab={params.tab_id}^ORDERBYrow^ORDERBYcol",
                "sysparm_limit": params.limit,
                "sysparm_display_value": "true",
                "sysparm_fields": "sys_id,tab,indicator,row,col,colspan,rowspan,widget_type",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        return {
            "success": True,
            "message": f"Found {len(result)} widget instance(s)",
            "widgets": result,
        }
    except requests.HTTPError as exc:
        logger.error("list_pa_widget_instances HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "widgets": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_pa_widget_instances error: %s", exc)
        return {"success": False, "message": str(exc), "widgets": []}
