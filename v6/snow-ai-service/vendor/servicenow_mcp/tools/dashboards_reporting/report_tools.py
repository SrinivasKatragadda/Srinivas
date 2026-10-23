"""
Report tools for the ServiceNow MCP server.

Provides CRUD and run operations for ServiceNow reports (sys_report table).
Covers report definition management and on-demand report execution via the
Reporting REST API (/api/now/v2/table/sys_report and the run endpoint).
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


class ListReportsParams(BaseModel):
    """Parameters for listing reports."""

    limit: int = Field(10, description="Maximum number of reports to return")
    offset: int = Field(0, description="Offset for pagination")
    table: Optional[str] = Field(None, description="Filter by the source table name")
    report_type: Optional[str] = Field(
        None,
        description=(
            "Filter by report type, e.g. 'list', 'bar', 'pie', 'trend', 'pivot', 'calendar'"
        ),
    )
    user: Optional[str] = Field(None, description="Filter by owner user sys_id or user_name")
    query: Optional[str] = Field(None, description="Encoded query string (sysparm_query)")


class GetReportParams(BaseModel):
    """Parameters for fetching a single report."""

    report_id: str = Field(..., description="sys_id of the sys_report record")


class CreateReportParams(BaseModel):
    """Parameters for creating a new report."""

    title: str = Field(..., description="Display title of the report")
    table: str = Field(..., description="Source table the report is based on (e.g. 'incident')")
    report_type: str = Field(
        "list",
        description=(
            "Report type: 'list' | 'bar' | 'horizontal_bar' | 'pie' | 'donut' | "
            "'trend' | 'pivot' | 'calendar' | 'map' | 'funnel'"
        ),
    )
    field: Optional[str] = Field(
        None,
        description="Primary grouping / aggregation field (required for chart types)",
    )
    filter_query: Optional[str] = Field(
        None, description="Encoded query string to filter the report data"
    )
    user: Optional[str] = Field(
        None, description="Owner user sys_id (defaults to authenticated user)"
    )
    description: Optional[str] = Field(None, description="Description / purpose of the report")
    is_public: bool = Field(False, description="Whether the report is publicly visible")
    roles: Optional[List[str]] = Field(
        None, description="List of role sys_ids or names that can view the report"
    )
    aggregation: Optional[str] = Field(
        None,
        description="Aggregation function: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN'",
    )
    aggregate_field: Optional[str] = Field(
        None, description="Field to aggregate on when using SUM/AVG/MAX/MIN"
    )


class UpdateReportParams(BaseModel):
    """Parameters for updating an existing report."""

    report_id: str = Field(..., description="sys_id of the sys_report record to update")
    title: Optional[str] = Field(None, description="Display title of the report")
    table: Optional[str] = Field(None, description="Source table")
    report_type: Optional[str] = Field(None, description="Report type")
    field: Optional[str] = Field(None, description="Primary grouping / aggregation field")
    filter_query: Optional[str] = Field(None, description="Encoded query to filter report data")
    description: Optional[str] = Field(None, description="Description of the report")
    is_public: Optional[bool] = Field(None, description="Whether the report is publicly visible")
    aggregation: Optional[str] = Field(None, description="Aggregation function")
    aggregate_field: Optional[str] = Field(None, description="Field to aggregate on")


class DeleteReportParams(BaseModel):
    """Parameters for deleting a report."""

    report_id: str = Field(..., description="sys_id of the sys_report record to delete")


class RunReportParams(BaseModel):
    """Parameters for running / exporting a report on demand."""

    report_id: str = Field(..., description="sys_id of the sys_report record to run")
    output_format: str = Field(
        "json",
        description="Output format: 'json' | 'pdf' | 'xlsx' | 'csv'",
    )
    max_results: int = Field(
        1000, description="Maximum number of data rows to return (json format only)"
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def list_reports(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListReportsParams,
) -> Dict[str, Any]:
    """List ServiceNow reports."""
    api_url = f"{config.api_url}/table/sys_report"
    query_params: Dict[str, Any] = {
        "sysparm_limit": params.limit,
        "sysparm_offset": params.offset,
        "sysparm_display_value": "true",
        "sysparm_fields": "sys_id,title,table,type,user,description,is_public,sys_updated_on",
    }
    filters: List[str] = []
    if params.table:
        filters.append(f"table={params.table}")
    if params.report_type:
        filters.append(f"type={params.report_type}")
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
        return {"success": True, "message": f"Found {len(result)} report(s)", "reports": result}
    except requests.HTTPError as exc:
        logger.error("list_reports HTTP error: %s", exc)
        return {"success": False, "message": str(exc), "reports": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("list_reports error: %s", exc)
        return {"success": False, "message": str(exc), "reports": []}


def get_report(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetReportParams,
) -> Dict[str, Any]:
    """Fetch a single ServiceNow report by sys_id."""
    api_url = f"{config.api_url}/table/sys_report/{params.report_id}"
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
            "message": "Report retrieved successfully",
            "report_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("get_report HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("get_report error: %s", exc)
        return {"success": False, "message": str(exc)}


def create_report(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateReportParams,
) -> Dict[str, Any]:
    """Create a new ServiceNow report."""
    api_url = f"{config.api_url}/table/sys_report"
    data: Dict[str, Any] = {
        "title": params.title,
        "table": params.table,
        "type": params.report_type,
        "is_public": params.is_public,
    }
    for field, api_field in (
        ("field", "field"),
        ("filter_query", "filter"),
        ("user", "user"),
        ("description", "description"),
        ("aggregation", "aggregation"),
        ("aggregate_field", "sum_field"),
    ):
        value = getattr(params, field, None)
        if value is not None:
            data[api_field] = value
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
            "message": f"Report '{params.title}' created successfully",
            "report_id": result.get("sys_id"),
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("create_report HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("create_report error: %s", exc)
        return {"success": False, "message": str(exc)}


def update_report(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateReportParams,
) -> Dict[str, Any]:
    """Update an existing ServiceNow report."""
    api_url = f"{config.api_url}/table/sys_report/{params.report_id}"
    data: Dict[str, Any] = {}
    for field, api_field in (
        ("title", "title"),
        ("table", "table"),
        ("report_type", "type"),
        ("field", "field"),
        ("filter_query", "filter"),
        ("description", "description"),
        ("aggregation", "aggregation"),
        ("aggregate_field", "sum_field"),
    ):
        value = getattr(params, field, None)
        if value is not None:
            data[api_field] = value
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
            "message": "Report updated successfully",
            "report_id": params.report_id,
            "data": result,
        }
    except requests.HTTPError as exc:
        logger.error("update_report HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("update_report error: %s", exc)
        return {"success": False, "message": str(exc)}


def delete_report(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteReportParams,
) -> Dict[str, Any]:
    """Delete a ServiceNow report."""
    api_url = f"{config.api_url}/table/sys_report/{params.report_id}"
    try:
        response = requests.delete(
            api_url,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Report deleted successfully",
            "report_id": params.report_id,
        }
    except requests.HTTPError as exc:
        logger.error("delete_report HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_report error: %s", exc)
        return {"success": False, "message": str(exc)}


def run_report(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: RunReportParams,
) -> Dict[str, Any]:
    """
    Run a ServiceNow report on demand and return its data.

    Uses the Table API to query the source table with the report's stored
    filter so the result is always fresh.  For PDF/XLSX/CSV the caller
    receives a download URL built from the standard report viewer endpoint.
    """
    # First, retrieve the report definition to get table + filter
    defn_url = f"{config.api_url}/table/sys_report/{params.report_id}"
    try:
        defn_resp = requests.get(
            defn_url,
            params={"sysparm_fields": "table,filter,type,title"},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        defn_resp.raise_for_status()
        defn = defn_resp.json().get("result", {})
    except requests.HTTPError as exc:
        return {"success": False, "message": f"Could not retrieve report definition: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"Could not retrieve report definition: {exc}"}

    source_table = defn.get("table", "")
    report_filter = defn.get("filter", "")
    report_title = defn.get("title", params.report_id)

    if params.output_format != "json":
        # Return a viewer URL the client can open in a browser
        viewer_url = (
            f"{config.instance_url}/report_viewer.do"
            f"?jvar_report_id={params.report_id}"
            f"&jvar_selected_tab=0&jvar_report_home_page=true"
        )
        return {
            "success": True,
            "message": (
                f"Report '{report_title}' viewer URL generated. "
                f"Open in a browser to export as {params.output_format.upper()}."
            ),
            "report_id": params.report_id,
            "viewer_url": viewer_url,
            "output_format": params.output_format,
        }

    # JSON: run the underlying table query directly
    if not source_table:
        return {"success": False, "message": "Report definition has no source table configured"}

    data_url = f"{config.api_url}/table/{source_table}"
    data_params: Dict[str, Any] = {
        "sysparm_limit": params.max_results,
        "sysparm_display_value": "true",
    }
    if report_filter:
        data_params["sysparm_query"] = report_filter

    try:
        data_resp = requests.get(
            data_url,
            params=data_params,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        data_resp.raise_for_status()
        rows = data_resp.json().get("result", [])
        return {
            "success": True,
            "message": f"Report '{report_title}' returned {len(rows)} row(s)",
            "report_id": params.report_id,
            "title": report_title,
            "row_count": len(rows),
            "rows": rows,
        }
    except requests.HTTPError as exc:
        logger.error("run_report data query HTTP error: %s", exc)
        return {"success": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.error("run_report error: %s", exc)
        return {"success": False, "message": str(exc)}
