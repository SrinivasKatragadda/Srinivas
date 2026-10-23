"""
Employee Center tools for the ServiceNow MCP server.

This sub-package groups all Employee Service Center (ESC) capabilities:
- Pages         – ESC portal page CRUD (esp_page)
- Topics        – topic / topic category management
- Announcements – ESC announcement lifecycle (esc_announcement)
"""

from servicenow_mcp.tools.employee_center.esc_announcement_tools import (
    create_esc_announcement,
    delete_esc_announcement,
    get_esc_announcement,
    list_esc_announcements,
    update_esc_announcement,
)
from servicenow_mcp.tools.employee_center.esc_page_tools import (
    create_esc_page,
    delete_esc_page,
    get_esc_page,
    list_esc_pages,
    update_esc_page,
)
from servicenow_mcp.tools.employee_center.esc_topic_tools import (
    create_esc_topic,
    delete_esc_topic,
    get_esc_topic,
    list_esc_topics,
    update_esc_topic,
)

__all__ = [
    # Pages
    "list_esc_pages",
    "get_esc_page",
    "create_esc_page",
    "update_esc_page",
    "delete_esc_page",
    # Topics
    "list_esc_topics",
    "get_esc_topic",
    "create_esc_topic",
    "update_esc_topic",
    "delete_esc_topic",
    # Announcements
    "list_esc_announcements",
    "get_esc_announcement",
    "create_esc_announcement",
    "update_esc_announcement",
    "delete_esc_announcement",
]
