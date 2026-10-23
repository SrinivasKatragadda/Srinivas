"""Scoped application and scope detail helpers.

This module supports discovery and manipulation of scoped applications,
application scopes, and related lifecycle metadata. It provides the shared
helpers that other scoped-app modules use to reason about scope records,
update-set references, and application-level artifact relationships.

The helpers here are intentionally low-level and table-oriented so they can be
reused by both read and write workflows across the scoped application tools.
"""

import re
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.scoped_app_common import (
    SYS_ID_PATTERN,
    ArtifactType,
    api_url,
    artifact_table,
    display_value,
    extract_result,
    normalize_record,
    query_records,
    raw_value,
    resolve_sys_id,
)
from servicenow_mcp.utils.config import ServerConfig

APPLICATION_ARTIFACT_TYPES = {"scoped_app", "scope"}
SCOPE_SLUG_PATTERN = re.compile(r"[^a-z0-9_]+")
UPDATE_SET_FIELDS = [
    "sys_id",
    "name",
    "description",
    "state",
    "current",
    "application",
    "sys_created_on",
    "sys_updated_on",
]
APPLICATION_SCOPE_PREFERENCE_NAMES = [
    "glide.ui.current_application",
    "sys_scope",
    "application",
]


class EnsureApplicationScopeParams(BaseModel):
    """Parameters for resolving or creating an application scope."""

    scope: str = Field(..., description="Scope name such as x_acme_app, or an existing sys_id")
    display_name: str = Field(
        ..., description="Display name to use if the scope record must be created"
    )
    create_if_missing: bool = Field(
        True, description="Create sys_scope when the requested scope name does not exist"
    )


class ChangeArtifactApplicationScopeParams(BaseModel):
    """Parameters for moving an artifact to a different application scope."""

    artifact_type: ArtifactType = Field(
        ..., description="Artifact type to move, for example scoped_app, app_menu, or module"
    )
    artifact_id: str = Field(
        ..., description="sys_id, sys_id:<id>, name, title, sys_name, or scope"
    )
    target_scope: str = Field(..., description="Target scope name such as x_acme_app or sys_id")
    table: Optional[str] = Field(None, description="Concrete table override")
    identifier_field: Optional[str] = Field(None, description="Lookup field for artifact_id")
    create_scope_if_missing: bool = Field(
        False, description="Create the target sys_scope if it does not already exist"
    )
    target_scope_display_name: Optional[str] = Field(
        None, description="Display name to use when creating the target scope"
    )
    target_application: Optional[str] = Field(
        None,
        description=(
            "Optional target scoped app/menu reference. For app_menu this resolves to sys_app; "
            "for module this resolves to sys_app_module.application."
        ),
    )
    update_set: Optional[str] = Field(
        None, description="Update set sys_id or name to bind to the target scope"
    )
    make_update_set_current: bool = Field(
        True, description="Make the provided update set current after binding it to the scope"
    )
    verify_change: bool = Field(
        True, description="Read the artifact after patching to verify scope"
    )


class SetApplicationUpdateSetParams(BaseModel):
    """Parameters for linking an update set to an application scope."""

    update_set_id: str = Field(..., description="Update set sys_id or name")
    application_scope: str = Field(..., description="Application scope name or sys_id")
    make_current: bool = Field(True, description="Make this update set current after linking")


class SwitchApplicationScopeParams(BaseModel):
    """Parameters for switching the current working application scope."""

    application_scope: str = Field(..., description="Application scope name or sys_id")
    update_user_preferences: bool = Field(
        False,
        description=(
            "Also update legacy sys_user_preference rows. This is not used as proof that "
            "the UI picker switched."
        ),
    )
    preference_name: Optional[str] = Field(
        None,
        description=(
            "Optional sys_user_preference name override. Defaults to known ServiceNow "
            "application picker preference names."
        ),
    )


def is_application_artifact(artifact_type: str) -> bool:
    """Return whether an artifact type stores scope in application fields."""
    return artifact_type in APPLICATION_ARTIFACT_TYPES


def build_application_query_parts(
    artifact_type: str,
    scope: Optional[str],
    application: Optional[str],
) -> List[str]:
    """Build query fragments used to locate application-scoped artifacts."""
    query_parts: List[str] = []
    if scope:
        if SYS_ID_PATTERN.fullmatch(scope):
            query_parts.append(f"sys_id={scope}^ORsys_scope={scope}")
        else:
            query_parts.append(f"scope={scope}^ORname={scope}")
    if application:
        query_parts.append(f"sys_app={application}")
    return query_parts


def build_application_write_fields(
    artifact_type: str,
    scope: Optional[str],
) -> Dict[str, Any]:
    """Build scope field assignments for create or update operations."""
    if scope is None:
        return {}
    if is_application_artifact(artifact_type):
        return {"scope": scope}
    return {"sys_scope": scope}


def prepare_scoped_app_create_body(
    config: ServerConfig,
    auth_manager: AuthManager,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Ensure a custom application create request has an explicit sys_scope reference."""
    if body.get("sys_scope"):
        body.setdefault("sys_package", body["sys_scope"])
        return {
            "sys_scope": body["sys_scope"],
            "sys_package": body["sys_package"],
            "scope": body.get("scope"),
            "scope_created": False,
        }

    app_name = str(body.get("name") or body.get("title") or body.get("sys_name") or "").strip()
    scope_name = str(body.get("scope") or "").strip()
    if not scope_name:
        if not app_name:
            raise ValueError("Provide scope or name when creating a scoped app")
        scope_name = _derive_scope_name(app_name)
        body["scope"] = scope_name

    scope_sys_id, scope_created = resolve_or_create_application_scope(
        config,
        auth_manager,
        scope_name,
        app_name or scope_name,
    )
    body["sys_scope"] = scope_sys_id
    body.setdefault("sys_package", scope_sys_id)
    return {
        "sys_scope": scope_sys_id,
        "sys_package": scope_sys_id,
        "scope": scope_name,
        "scope_created": scope_created,
    }


def resolve_or_create_application_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    scope: str,
    display_name: str,
) -> tuple[str, bool]:
    """Resolve an existing application scope or create it when absent."""
    records = _find_application_scope(config, auth_manager, scope)
    if records:
        return str(raw_value(records[0].get("sys_id"))), False
    if SYS_ID_PATTERN.fullmatch(scope):
        raise ValueError(f"Application scope sys_id not found: {scope}")

    response = requests.post(
        api_url(config, "sys_scope"),
        headers=auth_manager.get_headers(),
        params={"sysparm_input_display_value": "true"},
        json={"name": display_name, "scope": scope},
        timeout=config.timeout,
    )
    record = extract_result(response)
    return str(raw_value(record.get("sys_id"))), True


def ensure_application_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: EnsureApplicationScopeParams,
) -> Dict[str, Any]:
    """Resolve an application scope, optionally creating it when missing."""
    try:
        scope_record = resolve_application_scope_record(
            config,
            auth_manager,
            params.scope,
            params.display_name,
            params.create_if_missing,
        )
        return {
            "success": True,
            "message": (
                "Created application scope"
                if scope_record["created"]
                else "Found application scope"
            ),
            "scope": scope_record,
        }
    except Exception as exc:
        return {"success": False, "message": f"Error ensuring application scope: {exc}"}


def change_artifact_application_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ChangeArtifactApplicationScopeParams,
) -> Dict[str, Any]:
    """Move a scoped app artifact into a different application scope."""
    try:
        if params.artifact_type == "scope":
            raise ValueError("Use ensure_application_scope to create or verify sys_scope records")

        table = artifact_table(params.artifact_type, params.table)
        artifact_sys_id = resolve_sys_id(
            config,
            auth_manager,
            params.artifact_type,
            table,
            params.artifact_id,
            params.identifier_field,
        )
        scope_record = resolve_application_scope_record(
            config,
            auth_manager,
            params.target_scope,
            params.target_scope_display_name or params.target_scope,
            params.create_scope_if_missing,
        )
        body = build_scope_change_body(
            config,
            auth_manager,
            params.artifact_type,
            scope_record,
            params.target_application,
        )
        update_set = prepare_update_set_for_scope(
            config,
            auth_manager,
            params.update_set,
            scope_record,
            params.make_update_set_current,
        )
        if update_set:
            body["update_set"] = update_set["sys_id"]
            body["sys_update_set"] = update_set["sys_id"]

        response = requests.patch(
            api_url(config, table, artifact_sys_id),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = extract_result(response)
        verification = None
        if params.verify_change:
            verification = verify_artifact_scope(
                config,
                auth_manager,
                table,
                artifact_sys_id,
                scope_record,
            )
            if not verification["success"]:
                return {
                    "success": False,
                    "message": verification["message"],
                    "artifact_type": params.artifact_type,
                    "table": table,
                    "record": normalize_record(record, params.artifact_type, table),
                    "scope": scope_record,
                    "update_set": update_set,
                    "request_body": body,
                    "verification": verification,
                }
        return {
            "success": True,
            "message": f"Changed {params.artifact_type} scope to {scope_record['scope']}",
            "artifact_type": params.artifact_type,
            "table": table,
            "record": normalize_record(record, params.artifact_type, table),
            "scope": scope_record,
            "update_set": update_set,
            "request_body": body,
            "verification": verification,
        }
    except Exception as exc:
        return {"success": False, "message": f"Error changing application scope: {exc}"}


def set_application_update_set(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: SetApplicationUpdateSetParams,
) -> Dict[str, Any]:
    """Attach an update set to an application scope and optionally make it current."""
    try:
        scope_record = resolve_application_scope_record(
            config,
            auth_manager,
            params.application_scope,
            params.application_scope,
            create_if_missing=False,
        )
        update_set = resolve_update_set_record(config, auth_manager, params.update_set_id)
        updated_update_set = link_update_set_to_scope(
            config,
            auth_manager,
            update_set["sys_id"],
            scope_record["sys_id"],
            params.make_current,
        )
        return {
            "success": True,
            "message": (
                f"Linked update set {updated_update_set['name']} to scope {scope_record['scope']}"
            ),
            "scope": scope_record,
            "update_set": updated_update_set,
        }
    except Exception as exc:
        return {"success": False, "message": f"Error setting application update set: {exc}"}


def switch_application_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: SwitchApplicationScopeParams,
) -> Dict[str, Any]:
    """Switch the current user's working application scope through the UI picker API."""
    try:
        scope_record = resolve_application_scope_record(
            config,
            auth_manager,
            params.application_scope,
            params.application_scope,
            create_if_missing=False,
        )
        picker_result = set_concourse_application_picker(
            config,
            auth_manager,
            scope_record["sys_id"],
        )
        requested_picker_app = extract_picker_current_sys_id(picker_result)
        if requested_picker_app and requested_picker_app != str(scope_record["sys_id"]):
            return build_application_switch_failure(
                scope_record,
                picker_result,
                None,
                (
                    "ServiceNow returned a different app_id from the switch request. "
                    f"Expected {scope_record['sys_id']}, got {requested_picker_app}."
                ),
            )

        verification = get_concourse_application_picker(config, auth_manager)
        current = extract_picker_current_sys_id(verification)
        if not current:
            return build_application_switch_failure(
                scope_record,
                picker_result,
                verification,
                (
                    "ServiceNow accepted the application switch request, but the picker "
                    "verification response did not include a current application id."
                ),
            )
        if current != str(scope_record["sys_id"]):
            return build_application_switch_failure(
                scope_record,
                picker_result,
                verification,
                (
                    "Application scope was not switched for this authenticated ServiceNow "
                    f"session. Expected {scope_record['scope']} "
                    f"({scope_record['sys_id']}), but the picker still reports {current}."
                ),
            )

        updated_preferences = []
        if params.update_user_preferences:
            if params.preference_name:
                preference_names = [params.preference_name]
            else:
                preference_names = APPLICATION_SCOPE_PREFERENCE_NAMES
            for preference_name in preference_names:
                updated_preferences.extend(
                    upsert_scope_user_preference(
                        config,
                        auth_manager,
                        preference_name,
                        scope_record["sys_id"],
                    )
                )
        return {
            "success": True,
            "message": (
                f"Application picker reports current scope {scope_record['scope']} "
                f"({scope_record['sys_id']})."
            ),
            "scope": scope_record,
            "picker_result": picker_result,
            "verification": verification,
            "updated_preferences": updated_preferences,
        }
    except Exception as exc:
        return {"success": False, "message": f"Error switching application scope: {exc}"}


def build_application_switch_failure(
    scope_record: Dict[str, Any],
    picker_result: Optional[Dict[str, Any]],
    verification: Optional[Dict[str, Any]],
    reason: str,
) -> Dict[str, Any]:
    """Build a detailed failure payload when scope switching does not stick."""
    return {
        "success": False,
        "status": "not_switched",
        "message": (
            f"{reason} The browser UI will not show {scope_record['scope']} until the "
            "same UI session's application picker reports this sys_id as current. If MCP "
            "uses basic/OAuth credentials, it may be a different ServiceNow session than "
            "your browser tab."
        ),
        "scope": scope_record,
        "picker_result": picker_result,
        "verification": verification,
    }


def resolve_application_scope_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    scope: str,
    display_name: str,
    create_if_missing: bool,
) -> Dict[str, Any]:
    """Resolve an application scope record and normalize output metadata."""
    records = _find_application_scope(config, auth_manager, scope)
    if records:
        record = records[0]
        return {
            "sys_id": str(raw_value(record.get("sys_id"))),
            "name": display_value(record.get("name")),
            "scope": display_value(record.get("scope")),
            "created": False,
            "raw": record,
        }
    if not create_if_missing:
        raise ValueError(f"Application scope not found: {scope}")
    sys_id, _created = resolve_or_create_application_scope(
        config,
        auth_manager,
        scope,
        display_name,
    )
    records = _find_application_scope(config, auth_manager, sys_id)
    record = records[0] if records else {"sys_id": sys_id, "name": display_name, "scope": scope}
    return {
        "sys_id": sys_id,
        "name": display_value(record.get("name")),
        "scope": display_value(record.get("scope")),
        "created": True,
        "raw": record,
    }


def build_scope_change_body(
    config: ServerConfig,
    auth_manager: AuthManager,
    artifact_type: str,
    scope_record: Dict[str, Any],
    target_application: Optional[str],
) -> Dict[str, Any]:
    """Build the field payload required to move an artifact into a scope."""
    body: Dict[str, Any] = {
        "sys_scope": scope_record["sys_id"],
        "sys_package": scope_record["sys_id"],
    }
    if artifact_type == "scoped_app":
        body["scope"] = scope_record["scope"]
    if artifact_type == "app_menu":
        scoped_app = resolve_scoped_app_for_scope(
            config,
            auth_manager,
            target_application or str(scope_record["scope"]),
            scope_record,
            required=False,
        )
        if scoped_app:
            body["sys_app"] = scoped_app["sys_id"]
    if artifact_type == "module" and target_application:
        body["application"] = resolve_sys_id(
            config,
            auth_manager,
            "module",
            "sys_app_application",
            target_application,
        )
    return body


def resolve_scoped_app_for_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    identifier: str,
    scope_record: Dict[str, Any],
    required: bool,
) -> Optional[Dict[str, Any]]:
    """Resolve a scoped application that belongs to the target scope."""
    query = (
        f"sys_id={identifier}^ORscope={identifier}^ORname={identifier}"
        f"^ORsys_scope={scope_record['sys_id']}^ORsys_scope.scope={scope_record['scope']}"
    )
    records = query_records(
        config,
        auth_manager,
        "sys_app",
        query,
        limit=5,
        fields=["sys_id", "name", "scope", "sys_scope"],
    )
    for record in records:
        if (
            str(raw_value(record.get("sys_id"))) == identifier
            or str(display_value(record.get("scope"))) == identifier
            or str(display_value(record.get("name"))) == identifier
            or str(raw_value(record.get("sys_scope"))) == str(scope_record["sys_id"])
            or str(display_value(record.get("sys_scope"))) == str(scope_record["scope"])
        ):
            return {
                "sys_id": str(raw_value(record.get("sys_id"))),
                "name": display_value(record.get("name")),
                "scope": display_value(record.get("scope")),
                "raw": record,
            }
    if required:
        raise ValueError(f"Scoped app not found for scope: {scope_record['scope']}")
    return None


def verify_artifact_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    artifact_sys_id: str,
    scope_record: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify that an artifact now references the expected scope fields."""
    response = requests.get(
        api_url(config, table, artifact_sys_id),
        headers=auth_manager.get_headers(),
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": "sys_id,name,scope,sys_scope,sys_package,sys_app,application",
        },
        timeout=config.timeout,
    )
    record = extract_result(response)
    actual_scope = str(raw_value(record.get("sys_scope")) or "")
    actual_package = str(raw_value(record.get("sys_package")) or "")
    expected_scope = str(scope_record["sys_id"])
    scope_ok = actual_scope == expected_scope
    package_ok = actual_package in {"", expected_scope}
    if scope_ok and package_ok:
        return {"success": True, "record": record}
    return {
        "success": False,
        "message": (
            "ServiceNow did not apply the requested application scope. "
            f"Expected sys_scope/sys_package {expected_scope}, got "
            f"sys_scope={actual_scope or '<empty>'}, "
            f"sys_package={actual_package or '<empty>'}."
        ),
        "record": record,
    }


def upsert_scope_user_preference(
    config: ServerConfig,
    auth_manager: AuthManager,
    preference_name: str,
    scope_sys_id: str,
) -> List[Dict[str, Any]]:
    """Upsert UI user preferences that track the current application scope."""
    records = query_records(
        config,
        auth_manager,
        "sys_user_preference",
        f"name={preference_name}",
        limit=20,
        fields=["sys_id", "name", "value", "system", "user"],
    )
    updated = []
    for record in records:
        sys_id = raw_value(record.get("sys_id"))
        if not sys_id:
            continue
        response = requests.patch(
            api_url(config, "sys_user_preference", str(sys_id)),
            headers=auth_manager.get_headers(),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            json={"value": scope_sys_id},
            timeout=config.timeout,
        )
        updated.append(extract_result(response))
    if updated:
        return updated

    response = requests.post(
        api_url(config, "sys_user_preference"),
        headers=auth_manager.get_headers(),
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        },
        json={"name": preference_name, "value": scope_sys_id, "system": "false"},
        timeout=config.timeout,
    )
    return [extract_result(response)]


def extract_picker_current_sys_id(picker_payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract the current application sys_id from picker API payloads."""
    if not picker_payload:
        return None
    for key in ("app_id", "current", "currentId"):
        value = picker_payload.get(key)
        if isinstance(value, dict):
            candidate = value.get("sysId") or value.get("sys_id") or value.get("value")
            if candidate:
                return str(candidate)
        if value:
            return str(value)
    result = picker_payload.get("result")
    if isinstance(result, dict):
        return extract_picker_current_sys_id(result)
    return None


def set_concourse_application_picker(
    config: ServerConfig,
    auth_manager: AuthManager,
    scope_sys_id: str,
) -> Dict[str, Any]:
    """Set the Concourse application picker to a target scope sys_id."""
    response = requests.put(
        f"{config.instance_url}/api/now/ui/concoursepicker/application",
        headers=auth_manager.get_headers(),
        json={"app_id": scope_sys_id},
        timeout=config.timeout,
    )
    result = extract_result(response)
    return result if isinstance(result, dict) else {"result": result}


def get_concourse_application_picker(
    config: ServerConfig,
    auth_manager: AuthManager,
) -> Dict[str, Any]:
    """Read the current Concourse application picker state."""
    response = requests.get(
        f"{config.instance_url}/api/now/ui/concoursepicker/application",
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    result = extract_result(response)
    return result if isinstance(result, dict) else {"result": result}


def prepare_update_set_for_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    update_set_id: Optional[str],
    scope_record: Dict[str, Any],
    make_current: bool,
) -> Optional[Dict[str, Any]]:
    """Resolve and optionally activate an update set for the target scope."""
    if not update_set_id:
        return None
    update_set = resolve_update_set_record(config, auth_manager, update_set_id)
    return link_update_set_to_scope(
        config,
        auth_manager,
        update_set["sys_id"],
        scope_record["sys_id"],
        make_current,
    )


def resolve_update_set_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    update_set_id: str,
) -> Dict[str, Any]:
    """Resolve a sys_update_set by sys_id or by name."""
    if SYS_ID_PATTERN.fullmatch(update_set_id):
        query = f"sys_id={update_set_id}"
    else:
        query = f"name={update_set_id}"
    records = query_records(
        config,
        auth_manager,
        "sys_update_set",
        query,
        limit=2,
        fields=UPDATE_SET_FIELDS,
    )
    if not records:
        raise ValueError(f"Update set not found: {update_set_id}")

    normalized = update_set_id.casefold()
    record = records[0]
    for candidate in records:
        if (
            str(display_value(candidate.get("sys_id")) or "").casefold() == normalized
            or str(display_value(candidate.get("name")) or "").casefold() == normalized
        ):
            record = candidate
            break
    return normalize_update_set(record)


def link_update_set_to_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    update_set_sys_id: str,
    scope_sys_id: str,
    make_current: bool,
) -> Dict[str, Any]:
    """Link an update set to an application scope and optionally make it current."""
    body: Dict[str, Any] = {"application": scope_sys_id}
    if make_current:
        body["current"] = "true"
    response = requests.patch(
        api_url(config, "sys_update_set", update_set_sys_id),
        headers=auth_manager.get_headers(),
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": ",".join(UPDATE_SET_FIELDS),
        },
        json=body,
        timeout=config.timeout,
    )
    record = extract_result(response)
    return normalize_update_set(record)


def normalize_update_set(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a sys_update_set record into the scoped-app response shape."""
    application = record.get("application")
    return {
        "sys_id": str(raw_value(record.get("sys_id"))),
        "name": display_value(record.get("name")),
        "description": display_value(record.get("description")),
        "state": display_value(record.get("state")),
        "current": display_value(record.get("current")),
        "application": (
            {
                "value": raw_value(application),
                "display_value": display_value(application),
            }
            if application not in (None, "")
            else None
        ),
        "raw": record,
    }


def get_application_children(
    config: ServerConfig,
    auth_manager: AuthManager,
    scope_id: str,
    scope_name: str,
) -> Dict[str, Any]:
    """Collect menus, modules, and metadata records under an application scope."""
    scope_query = f"sys_scope={scope_id}"
    if scope_name:
        scope_query = f"{scope_query}^ORsys_scope.scope={scope_name}"
    menu_records = query_records(config, auth_manager, "sys_app_application", scope_query, 100)
    module_records = query_records(config, auth_manager, "sys_app_module", scope_query, 200)
    metadata_records = query_records(config, auth_manager, "sys_metadata", scope_query, 200)
    return {
        "menus": [
            normalize_record(record, "app_menu", "sys_app_application") for record in menu_records
        ],
        "modules": [
            normalize_record(record, "module", "sys_app_module") for record in module_records
        ],
        "metadata_files": [
            normalize_record(record, "application_file", "sys_metadata")
            for record in metadata_records
        ],
        "menu_count": len(menu_records),
        "module_count": len(module_records),
        "metadata_file_count": len(metadata_records),
    }


def _find_application_scope(
    config: ServerConfig,
    auth_manager: AuthManager,
    scope: str,
) -> List[Dict[str, Any]]:
    if SYS_ID_PATTERN.fullmatch(scope):
        query = f"sys_id={scope}"
    else:
        query = f"scope={scope}^ORname={scope}"
    records = query_records(
        config,
        auth_manager,
        "sys_scope",
        query,
        limit=2,
        fields=["sys_id", "name", "scope"],
    )
    return [
        record
        for record in records
        if SYS_ID_PATTERN.fullmatch(scope)
        or str(display_value(record.get("scope"))).strip() == scope
        or str(display_value(record.get("name"))).strip() == scope
    ]


def _derive_scope_name(app_name: str) -> str:
    slug = SCOPE_SLUG_PATTERN.sub("_", app_name.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        raise ValueError("Unable to derive scope from scoped app name")
    if slug.startswith("x_"):
        return slug
    return f"x_mcp_{slug}"
