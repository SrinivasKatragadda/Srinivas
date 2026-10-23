"""Scoped application menu and module detail helpers.

This module handles menu and module artifacts that belong to scoped
applications. It builds query and write-field fragments for application menus,
modules, and related navigation records so those structures can be managed
consistently across scoped-app workflows.
"""

from typing import Any, Dict, List, Optional

from servicenow_mcp.tools.scoped_app_common import SYS_ID_PATTERN

MENU_ARTIFACT_TYPES = {"app_menu", "module"}


def is_menu_artifact(artifact_type: str) -> bool:
    """Return whether the artifact type belongs to the menu/module family."""
    return artifact_type in MENU_ARTIFACT_TYPES


def build_menu_query_parts(
    artifact_type: str,
    scope: Optional[str],
    application: Optional[str],
) -> List[str]:
    """Build query fragments for menu/module records based on scope context.

    The function decides whether scope should be matched through sys_scope or
    sys_scope.scope and whether application context should be mapped to sys_app
    or application depending on the artifact family.
    """
    query_parts: List[str] = []
    if scope:
        if SYS_ID_PATTERN.fullmatch(scope):
            query_parts.append(f"sys_scope={scope}")
        else:
            query_parts.append(f"sys_scope.scope={scope}")
    if application:
        if artifact_type == "module":
            query_parts.append(f"application={application}")
        else:
            query_parts.append(f"sys_app={application}")
    return query_parts


def build_menu_write_fields(
    artifact_type: str,
    title: Optional[str],
    order: Optional[int],
    roles: Optional[str],
    link_type: Optional[str],
    target_table: Optional[str],
    query: Optional[str],
) -> Dict[str, Any]:
    """Build write-field payloads for menu and module artifacts.

    Only fields valid for the specific artifact family are included in the
    resulting payload.
    """
    body: Dict[str, Any] = {}
    if is_menu_artifact(artifact_type):
        body.update({"order": order, "roles": roles})
    if artifact_type == "module":
        body.update({"link_type": link_type, "table": target_table, "query": query})
    if is_menu_artifact(artifact_type) and title:
        body.setdefault("name", title)
    return body
