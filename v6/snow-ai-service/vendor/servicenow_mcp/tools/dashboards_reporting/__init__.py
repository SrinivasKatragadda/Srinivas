"""
Dashboards and Reporting tools for the ServiceNow MCP server.

This sub-package groups all dashboard and reporting capabilities:
- Reports    – sys_report CRUD and execution
- Dashboards – pa_dashboard / pa_dashboard_tab management
- PA         – Performance Analytics indicators and widgets
"""

from servicenow_mcp.tools.dashboards_reporting.dashboard_tools import (
    add_dashboard_tab,
    create_dashboard,
    delete_dashboard,
    delete_dashboard_tab,
    get_dashboard,
    list_dashboard_tabs,
    list_dashboards,
    update_dashboard,
    update_dashboard_tab,
)
from servicenow_mcp.tools.dashboards_reporting.pa_tools import (
    create_pa_indicator,
    delete_pa_indicator,
    get_pa_indicator,
    list_pa_indicators,
    list_pa_widget_instances,
    update_pa_indicator,
)
from servicenow_mcp.tools.dashboards_reporting.report_tools import (
    create_report,
    delete_report,
    get_report,
    list_reports,
    run_report,
    update_report,
)

__all__ = [
    # Reports
    "list_reports",
    "get_report",
    "create_report",
    "update_report",
    "delete_report",
    "run_report",
    # Dashboards
    "list_dashboards",
    "get_dashboard",
    "create_dashboard",
    "update_dashboard",
    "delete_dashboard",
    "list_dashboard_tabs",
    "add_dashboard_tab",
    "update_dashboard_tab",
    "delete_dashboard_tab",
    # Performance Analytics
    "list_pa_indicators",
    "get_pa_indicator",
    "create_pa_indicator",
    "update_pa_indicator",
    "delete_pa_indicator",
    "list_pa_widget_instances",
]
