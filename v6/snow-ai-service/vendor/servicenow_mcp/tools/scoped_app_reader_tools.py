"""Scoped application read and search tools.

This module provides read-only discovery and search helpers for scoped
application artifacts. It combines application, menu, and scope-aware queries
so callers can inspect the full scoped app surface without needing to know the
underlying table layout in advance.
"""

import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.scoped_app_application_tools import (
    build_application_query_parts,
    get_application_children,
    is_application_artifact,
)
from servicenow_mcp.tools.scoped_app_common import (
    ArtifactType,
    api_url,
    artifact_table,
    bool_text,
    display_value,
    extract_result,
    normalize_record,
    query_records,
    raw_value,
    resolve_sys_id,
)
from servicenow_mcp.tools.scoped_app_menu_tools import build_menu_query_parts
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


class ListScopedAppArtifactsParams(BaseModel):
    """Parameters for listing scoped app lifecycle artifacts."""

    artifact_type: ArtifactType = Field(
        ...,
        description=(
            "Artifact type: scoped_app, scope, app_menu, module, application_file, app_metadata"
        ),
    )
    table: Optional[str] = Field(
        None, description="Concrete ServiceNow table override for application files or metadata"
    )
    scope: Optional[str] = Field(None, description="Filter by scope name or sys_scope sys_id")
    application: Optional[str] = Field(
        None, description="Filter by sys_app/application reference when supported"
    )
    query: Optional[str] = Field(None, description="Search text for name, sys_name, or title")
    encoded_query: Optional[str] = Field(None, description="Additional ServiceNow encoded query")
    active: Optional[bool] = Field(None, description="Filter by active flag when supported")
    limit: int = Field(25, description="Maximum records to return")
    offset: int = Field(0, description="Offset for pagination")
    fields: Optional[List[str]] = Field(None, description="Fields to return")


class GetScopedAppArtifactParams(BaseModel):
    """Parameters for getting a scoped app lifecycle artifact."""

    artifact_type: ArtifactType = Field(..., description="Artifact type to retrieve")
    artifact_id: str = Field(
        ..., description="sys_id, sys_id:<id>, name, title, sys_name, or scope"
    )
    table: Optional[str] = Field(None, description="Concrete table override")
    identifier_field: Optional[str] = Field(
        None, description="Field to use when artifact_id is not a sys_id"
    )
    include_children: bool = Field(
        False,
        description="For scoped_app/scope, include application menus, modules, and metadata files",
    )
    fields: Optional[List[str]] = Field(None, description="Fields to return for the main record")


def list_scoped_app_artifacts(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListScopedAppArtifactsParams,
) -> Dict[str, Any]:
    """List scoped app lifecycle artifacts."""
    try:
        table = artifact_table(params.artifact_type, params.table)
        query_parts: List[str] = []
        if is_application_artifact(params.artifact_type):
            query_parts.extend(
                build_application_query_parts(
                    params.artifact_type,
                    params.scope,
                    params.application,
                )
            )
        else:
            query_parts.extend(
                build_menu_query_parts(
                    params.artifact_type,
                    params.scope,
                    params.application,
                )
            )
        if params.active is not None:
            query_parts.append(f"active={bool_text(params.active)}")
        if params.query:
            query_parts.append(
                f"nameLIKE{params.query}^ORsys_nameLIKE{params.query}^ORtitleLIKE{params.query}"
            )
        if params.encoded_query:
            query_parts.append(params.encoded_query)

        records = query_records(
            config,
            auth_manager,
            table,
            "^".join(query_parts),
            params.limit,
            params.offset,
            params.fields,
        )
        return {
            "success": True,
            "message": f"Found {len(records)} {params.artifact_type} records",
            "artifact_type": params.artifact_type,
            "table": table,
            "count": len(records),
            "records": [
                normalize_record(record, params.artifact_type, table) for record in records
            ],
            "limit": params.limit,
            "offset": params.offset,
        }
    except Exception as exc:
        logger.error("Error listing scoped app artifacts: %s", exc)
        return {
            "success": False,
            "message": f"Error listing scoped app artifacts: {exc}",
            "records": [],
            "count": 0,
        }


def get_scoped_app_artifact(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetScopedAppArtifactParams,
) -> Dict[str, Any]:
    """Get one scoped app lifecycle artifact."""
    try:
        table = artifact_table(params.artifact_type, params.table)
        sys_id = resolve_sys_id(
            config,
            auth_manager,
            params.artifact_type,
            table,
            params.artifact_id,
            params.identifier_field,
        )
        query_params: Dict[str, Any] = {
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        }
        if params.fields:
            query_params["sysparm_fields"] = ",".join(params.fields)
        response = requests.get(
            api_url(config, table, sys_id),
            headers=auth_manager.get_headers(),
            params=query_params,
            timeout=config.timeout,
        )
        record = extract_result(response)
        result: Dict[str, Any] = {
            "success": True,
            "message": f"Found {params.artifact_type}: {params.artifact_id}",
            "artifact_type": params.artifact_type,
            "table": table,
            "record": normalize_record(record, params.artifact_type, table),
        }
        if params.include_children and is_application_artifact(params.artifact_type):
            scope_id = str(raw_value(record.get("sys_scope") or record.get("sys_id")))
            scope_name = str(display_value(record.get("scope") or record.get("name") or ""))
            result["children"] = get_application_children(
                config, auth_manager, scope_id, scope_name
            )
        return result
    except Exception as exc:
        logger.error("Error getting scoped app artifact: %s", exc)
        return {"success": False, "message": f"Error getting scoped app artifact: {exc}"}
