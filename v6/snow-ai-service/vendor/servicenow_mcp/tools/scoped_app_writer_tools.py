"""Scoped application create, update, and delete tools.

This module provides the write-path for scoped application artifacts,
including create, update, delete, and lifecycle operations. It relies on the
shared scoped-app helpers to resolve scope records and construct table-specific
payloads while keeping the public tool surface consistent.
"""

import logging
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field, model_validator

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.scoped_app_application_tools import (
    build_application_write_fields,
    prepare_scoped_app_create_body,
    prepare_update_set_for_scope,
    resolve_application_scope_record,
)
from servicenow_mcp.tools.scoped_app_common import (
    ArtifactType,
    api_url,
    artifact_table,
    bool_text,
    clean_body,
    display_value,
    extract_result,
    normalize_record,
    resolve_sys_id,
)
from servicenow_mcp.tools.scoped_app_menu_tools import build_menu_write_fields
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)


class CreateScopedAppArtifactParams(BaseModel):
    """Parameters for creating a scoped app lifecycle artifact."""

    artifact_type: ArtifactType = Field(..., description="Artifact type to create")
    table: Optional[str] = Field(None, description="Concrete table override")
    name: Optional[str] = Field(None, description="Name/sys_name/title for the artifact")
    title: Optional[str] = Field(None, description="Menu or module title")
    scope: Optional[str] = Field(None, description="Scope name or sys_scope sys_id")
    application: Optional[str] = Field(
        None, description="Application menu/sys_app reference where supported"
    )
    active: Optional[bool] = Field(None, description="Active flag when supported")
    update_set: Optional[str] = Field(None, description="Update set sys_id or name to use")
    make_update_set_current: bool = Field(
        True, description="Make the provided update set current before creating the artifact"
    )
    order: Optional[int] = Field(None, description="Order for menus or modules")
    roles: Optional[str] = Field(None, description="Roles required for menus or modules")
    link_type: Optional[str] = Field(None, description="Module link type")
    target_table: Optional[str] = Field(None, description="Target table for list/form modules")
    query: Optional[str] = Field(None, description="Module query/filter")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional ServiceNow fields to send directly to the target table",
    )

    @model_validator(mode="after")
    def _validate_create_fields(self) -> "CreateScopedAppArtifactParams":
        if not (self.name or self.title or self.fields):
            raise ValueError("Provide name, title, or fields for creation")
        return self


class UpdateScopedAppArtifactParams(BaseModel):
    """Parameters for updating a scoped app lifecycle artifact."""

    artifact_type: ArtifactType = Field(..., description="Artifact type to update")
    artifact_id: str = Field(
        ..., description="sys_id, sys_id:<id>, name, title, sys_name, or scope"
    )
    table: Optional[str] = Field(None, description="Concrete table override")
    identifier_field: Optional[str] = Field(None, description="Lookup field for artifact_id")
    name: Optional[str] = Field(None, description="Updated name/sys_name")
    title: Optional[str] = Field(None, description="Updated menu or module title")
    scope: Optional[str] = Field(None, description="Updated sys_scope reference when supported")
    application: Optional[str] = Field(None, description="Updated application reference")
    active: Optional[bool] = Field(None, description="Updated active flag")
    update_set: Optional[str] = Field(None, description="Update set sys_id or name to use")
    make_update_set_current: bool = Field(
        True, description="Make the provided update set current before updating the artifact"
    )
    order: Optional[int] = Field(None, description="Updated order")
    roles: Optional[str] = Field(None, description="Updated roles")
    link_type: Optional[str] = Field(None, description="Updated module link type")
    target_table: Optional[str] = Field(None, description="Updated target table")
    query: Optional[str] = Field(None, description="Updated query/filter")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional ServiceNow fields to patch directly",
    )


class DeleteScopedAppArtifactParams(BaseModel):
    """Parameters for deleting a scoped app lifecycle artifact."""

    artifact_type: ArtifactType = Field(..., description="Artifact type to delete")
    artifact_id: str = Field(
        ..., description="sys_id, sys_id:<id>, name, title, sys_name, or scope"
    )
    table: Optional[str] = Field(None, description="Concrete table override")
    identifier_field: Optional[str] = Field(None, description="Lookup field for artifact_id")


def create_scoped_app_artifact(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateScopedAppArtifactParams,
) -> Dict[str, Any]:
    """Create a scoped app lifecycle artifact."""
    try:
        table = artifact_table(params.artifact_type, params.table)
        body = _build_write_body(params)
        scope_info: Optional[Dict[str, Any]] = None
        update_set_info: Optional[Dict[str, Any]] = None
        if params.artifact_type == "scoped_app":
            scope_info = prepare_scoped_app_create_body(config, auth_manager, body)
            update_set_info = prepare_update_set_for_scope(
                config,
                auth_manager,
                params.update_set,
                scope_info,
                params.make_update_set_current,
            )
            if update_set_info:
                body.setdefault("update_set", update_set_info["sys_id"])
                body.setdefault("sys_update_set", update_set_info["sys_id"])
        response = requests.post(
            api_url(config, table),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = extract_result(response)
        record_name = display_value(
            record.get("name") or record.get("title") or record.get("sys_id")
        )
        result = {
            "success": True,
            "message": f"Created {params.artifact_type}: {record_name}",
            "artifact_type": params.artifact_type,
            "table": table,
            "record": normalize_record(record, params.artifact_type, table),
        }
        if scope_info:
            result["scope"] = scope_info
        if update_set_info:
            result["update_set"] = update_set_info
        return result
    except Exception as exc:
        logger.error("Error creating scoped app artifact: %s", exc)
        return {"success": False, "message": f"Error creating scoped app artifact: {exc}"}


def update_scoped_app_artifact(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateScopedAppArtifactParams,
) -> Dict[str, Any]:
    """Update a scoped app lifecycle artifact."""
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
        body = _build_write_body(params)
        update_set_info: Optional[Dict[str, Any]] = None
        scope_record: Optional[Dict[str, Any]] = None
        if params.artifact_type == "scoped_app" and params.scope:
            scope_record = resolve_application_scope_record(
                config,
                auth_manager,
                params.scope,
                params.scope,
                create_if_missing=False,
            )
            body["scope"] = scope_record["scope"]
            body["sys_scope"] = scope_record["sys_id"]
            body["sys_package"] = scope_record["sys_id"]
        if params.artifact_type == "scoped_app" and params.update_set:
            if not scope_record:
                existing_scope = _get_scoped_app_scope(config, auth_manager, table, sys_id)
                scope_record = resolve_application_scope_record(
                    config,
                    auth_manager,
                    existing_scope["scope"],
                    existing_scope["scope"],
                    create_if_missing=False,
                )
            update_set_info = prepare_update_set_for_scope(
                config,
                auth_manager,
                params.update_set,
                scope_record,
                params.make_update_set_current,
            )
            if update_set_info:
                body.setdefault("update_set", update_set_info["sys_id"])
                body.setdefault("sys_update_set", update_set_info["sys_id"])
        if not body:
            return {
                "success": True,
                "message": f"No changes supplied for {params.artifact_type}: {params.artifact_id}",
                "artifact_type": params.artifact_type,
                "table": table,
            }
        response = requests.patch(
            api_url(config, table, sys_id),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = extract_result(response)
        record_name = display_value(
            record.get("name") or record.get("title") or record.get("sys_id")
        )
        result = {
            "success": True,
            "message": f"Updated {params.artifact_type}: {record_name}",
            "artifact_type": params.artifact_type,
            "table": table,
            "record": normalize_record(record, params.artifact_type, table),
        }
        if update_set_info:
            result["update_set"] = update_set_info
        return result
    except Exception as exc:
        logger.error("Error updating scoped app artifact: %s", exc)
        return {"success": False, "message": f"Error updating scoped app artifact: {exc}"}


def delete_scoped_app_artifact(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteScopedAppArtifactParams,
) -> Dict[str, Any]:
    """Delete a scoped app lifecycle artifact."""
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
        response = requests.delete(
            api_url(config, table, sys_id),
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": f"Deleted {params.artifact_type}: {params.artifact_id}",
            "artifact_type": params.artifact_type,
            "table": table,
            "sys_id": sys_id,
        }
    except Exception as exc:
        logger.error("Error deleting scoped app artifact: %s", exc)
        return {"success": False, "message": f"Error deleting scoped app artifact: {exc}"}


def _build_write_body(params: Any) -> Dict[str, Any]:
    body = dict(params.fields or {})
    artifact_type = getattr(params, "artifact_type", None)
    common = {"active": bool_text(getattr(params, "active", None))}
    if getattr(params, "name", None) is not None:
        common["name"] = params.name
    if getattr(params, "title", None) is not None:
        common["title"] = params.title
    common.update(build_application_write_fields(artifact_type, getattr(params, "scope", None)))
    if getattr(params, "application", None) is not None:
        if artifact_type == "module":
            common["application"] = params.application
        else:
            common["sys_app"] = params.application
    common.update(
        build_menu_write_fields(
            artifact_type,
            getattr(params, "title", None),
            getattr(params, "order", None),
            getattr(params, "roles", None),
            getattr(params, "link_type", None),
            getattr(params, "target_table", None),
            getattr(params, "query", None),
        )
    )
    for key, value in clean_body(common).items():
        body.setdefault(key, value)
    return body


def _get_scoped_app_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    sys_id: str,
) -> Dict[str, Any]:
    response = requests.get(
        api_url(config, table, sys_id),
        headers=auth_manager.get_headers(),
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": "sys_scope,scope",
        },
        timeout=config.timeout,
    )
    record = extract_result(response)
    scope_value = display_value(record.get("scope")) or str(record.get("scope") or "").strip()
    if not scope_value and record.get("sys_scope"):
        scope_value = str(record.get("sys_scope"))
        if isinstance(record.get("sys_scope"), dict):
            scope_value = str(record["sys_scope"].get("value") or "")
    if not scope_value:
        raise ValueError("Existing scoped app does not have a resolvable scope")
    return {"scope": str(scope_value), "raw": record}
