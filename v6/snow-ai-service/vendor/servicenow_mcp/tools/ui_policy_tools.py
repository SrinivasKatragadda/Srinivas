"""
UI Policy tools for the ServiceNow MCP server.

This module provides tools for managing UI Policies in ServiceNow. UI Policies
control field behavior on forms, including show/hide state, mandatory rules,
and read-only behavior, so this module focuses on the records and action rows
that define those behaviors.

The helpers are intended to support both discovery and administration of UI
Policy logic without mixing in unrelated client script or catalog workflows.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
UI_POLICY_ACTION_PARENT_FIELD_FALLBACKS = ("ui_policy", "sys_ui_policy")


class ListUIPoliciesParams(BaseModel):
    """Parameters for listing UI Policies."""

    limit: int = Field(10, description="Maximum number of UI Policies to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    table: Optional[str] = Field(None, description="Filter by table name")
    query: Optional[str] = Field(None, description="Search query for UI Policy names")


class GetUIPolicyParams(BaseModel):
    """Parameters for getting a UI Policy."""

    ui_policy_id: str = Field(..., description="UI Policy sys_id or name")


class UIPolicyAction(BaseModel):
    """UI Policy action configuration."""

    field: str = Field(..., description="Field name this action applies to")
    mandatory: bool = Field(False, description="Make field mandatory")
    visible: bool = Field(True, description="Make field visible")
    readonly: bool = Field(False, description="Make field readonly")


class CreateUIPolicyParams(BaseModel):
    """Parameters for creating a UI Policy."""

    name: str = Field(..., description="Name of the UI Policy")
    table: str = Field(..., description="Table the UI Policy applies to")
    conditions: Optional[str] = Field(
        None,
        description=(
            "Encoded conditions for the UI Policy, for example 'state=4^ORstate=5^ORstate=6'"
        ),
    )
    condition: Optional[str] = Field(
        None,
        description="Alias for conditions; encoded before sending to ServiceNow",
    )
    active: bool = Field(True, description="Whether the UI Policy is active")
    short_description: Optional[str] = Field(None, description="Short description")
    description: Optional[str] = Field(None, description="Full description")
    actions: List[UIPolicyAction] = Field(
        default_factory=list, description="List of actions for this policy"
    )
    order: Optional[int] = Field(None, description="Order of execution")
    on_load: bool = Field(False, description="Execute on form load")
    reverse_if_false: bool = Field(True, description="Reverse actions when condition is false")
    run_scripts: bool = Field(False, description="Run client-side scripts when the policy matches")
    script_true: Optional[str] = Field(None, description="Client script executed when true")
    script_false: Optional[str] = Field(None, description="Client script executed when false")
    global_policy: Optional[bool] = Field(
        None, description="Apply the UI Policy globally across views"
    )
    view: Optional[str] = Field(None, description="Specific view where the UI Policy applies")
    comments: Optional[str] = Field(None, description="Comments about the UI Policy")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_ui_policy fields to send directly to ServiceNow"
    )


class UpdateUIPolicyParams(BaseModel):
    """Parameters for updating a UI Policy."""

    ui_policy_id: str = Field(..., description="UI Policy sys_id or name")
    name: Optional[str] = Field(None, description="Name of the UI Policy")
    table: Optional[str] = Field(None, description="Table the UI Policy applies to")
    conditions: Optional[str] = Field(
        None,
        description=(
            "Encoded conditions for the UI Policy, for example 'state=4^ORstate=5^ORstate=6'"
        ),
    )
    condition: Optional[str] = Field(
        None,
        description="Alias for conditions; encoded before sending to ServiceNow",
    )
    active: Optional[bool] = Field(None, description="Whether the UI Policy is active")
    short_description: Optional[str] = Field(None, description="Short description")
    description: Optional[str] = Field(None, description="Full description")
    actions: Optional[List[UIPolicyAction]] = Field(
        None, description="List of actions for this policy"
    )
    order: Optional[int] = Field(None, description="Order of execution")
    on_load: Optional[bool] = Field(None, description="Execute on form load")
    reverse_if_false: Optional[bool] = Field(
        None, description="Reverse actions when condition is false"
    )
    run_scripts: Optional[bool] = Field(
        None, description="Run client-side scripts when the policy matches"
    )
    script_true: Optional[str] = Field(None, description="Client script executed when true")
    script_false: Optional[str] = Field(None, description="Client script executed when false")
    global_policy: Optional[bool] = Field(
        None, description="Apply the UI Policy globally across views"
    )
    view: Optional[str] = Field(None, description="Specific view where the UI Policy applies")
    comments: Optional[str] = Field(None, description="Comments about the UI Policy")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_ui_policy fields to patch directly in ServiceNow"
    )


class DeleteUIPolicyParams(BaseModel):
    """Parameters for deleting a UI Policy."""

    ui_policy_id: str = Field(..., description="UI Policy sys_id or name")


class ListUIPolicyActionsParams(BaseModel):
    """Parameters for listing UI Policy actions."""

    limit: int = Field(50, description="Maximum number of UI Policy actions to return")
    offset: int = Field(0, description="Offset for pagination")
    ui_policy_id: Optional[str] = Field(
        None, description="Filter by parent UI Policy sys_id or name"
    )
    field: Optional[str] = Field(None, description="Filter by field name")


class GetUIPolicyActionParams(BaseModel):
    """Parameters for getting a UI Policy action."""

    ui_policy_action_id: str = Field(..., description="UI Policy action sys_id")


class CreateUIPolicyActionParams(BaseModel):
    """Parameters for creating a UI Policy action."""

    ui_policy_id: str = Field(..., description="Parent UI Policy sys_id or name")
    field: str = Field(..., description="Field name this action applies to")
    mandatory: Optional[bool] = Field(None, description="Set mandatory state; omit to leave alone")
    visible: Optional[bool] = Field(None, description="Set visible state; omit to leave alone")
    readonly: Optional[bool] = Field(None, description="Set read-only state; omit to leave alone")
    order: Optional[int] = Field(None, description="Action order")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_ui_policy_action fields to send directly to ServiceNow"
    )


class UpdateUIPolicyActionParams(BaseModel):
    """Parameters for updating a UI Policy action."""

    ui_policy_action_id: str = Field(..., description="UI Policy action sys_id")
    ui_policy_id: Optional[str] = Field(None, description="Parent UI Policy sys_id or name")
    field: Optional[str] = Field(None, description="Field name this action applies to")
    mandatory: Optional[bool] = Field(
        None, description="Set mandatory state; omit to leave unchanged"
    )
    visible: Optional[bool] = Field(None, description="Set visible state; omit to leave unchanged")
    readonly: Optional[bool] = Field(
        None, description="Set read-only state; omit to leave unchanged"
    )
    order: Optional[int] = Field(None, description="Action order")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_ui_policy_action fields to patch directly"
    )


class DeleteUIPolicyActionParams(BaseModel):
    """Parameters for deleting a UI Policy action."""

    ui_policy_action_id: str = Field(..., description="UI Policy action sys_id")


class UIPolicyResponse(BaseModel):
    """Response from UI Policy operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    ui_policy_id: Optional[str] = Field(None, description="ID of the affected UI Policy")
    ui_policy_name: Optional[str] = Field(None, description="Name of the affected UI Policy")


class UIPolicyActionResponse(BaseModel):
    """Response from UI Policy action operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    ui_policy_action_id: Optional[str] = Field(
        None, description="ID of the affected UI Policy action"
    )
    ui_policy_id: Optional[str] = Field(None, description="Parent UI Policy sys_id")
    field: Optional[str] = Field(None, description="Field affected by the action")


def _extract_sys_id(identifier: str) -> Optional[str]:
    cleaned = identifier.strip()
    if cleaned.startswith("sys_id:"):
        candidate = cleaned.replace("sys_id:", "", 1).strip()
        return candidate or None
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


def _reference_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _display_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _coerce_servicenow_bool(value: Any) -> bool:
    normalized = _display_value(value)
    if isinstance(normalized, bool):
        return normalized
    if isinstance(normalized, str):
        return normalized.strip().lower() == "true"
    return bool(normalized)


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    normalized = _display_value(value)
    if normalized in (None, ""):
        return None
    if isinstance(normalized, bool):
        return normalized
    if isinstance(normalized, str):
        lowered = normalized.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _extract_error_body(error: Exception) -> str:
    response = getattr(error, "response", None)
    if response is None:
        return str(error)
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or payload
    except ValueError:
        detail = response.text.strip() or str(error)
    return f"HTTP {response.status_code}: {detail}"


def _normalize_ui_policy_conditions(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized or "^" in normalized:
        return normalized

    normalized = re.sub(r"\s+\bOR\b\s+", "^OR", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+\bAND\b\s+", "^", normalized, flags=re.IGNORECASE)
    return normalized


def _requested_conditions(params: Any) -> Optional[str]:
    conditions = getattr(params, "conditions", None)
    if conditions is None:
        conditions = getattr(params, "condition", None)
    return _normalize_ui_policy_conditions(conditions)


def _build_ui_policy_fields() -> str:
    return (
        "sys_id,short_description,table,active,conditions,description,"
        "order,on_load,reverse_if_false,run_scripts,script_true,script_false,"
        "global,view,comments,sys_created_on,sys_updated_on,"
        "sys_created_by,sys_updated_by"
    )


def _build_ui_policy_action_fields() -> str:
    parent_fields = ",".join(UI_POLICY_ACTION_PARENT_FIELD_FALLBACKS)
    return (
        f"sys_id,{parent_fields},field,mandatory,visible,disabled,order,"
        "sys_created_on,sys_updated_on,sys_created_by,sys_updated_by"
    )


def _normalize_ui_policy_action(item: Dict[str, Any]) -> Dict[str, Any]:
    policy_reference = _ui_policy_action_parent_value(item)
    return {
        "sys_id": item.get("sys_id"),
        "ui_policy": policy_reference,
        "ui_policy_display": _ui_policy_action_parent_display(item),
        "field": item.get("field"),
        "mandatory": _coerce_optional_bool(item.get("mandatory")),
        "visible": _coerce_optional_bool(item.get("visible")),
        "readonly": _coerce_optional_bool(item.get("disabled", item.get("readonly"))),
        "order": item.get("order"),
        "created_on": item.get("sys_created_on"),
        "updated_on": item.get("sys_updated_on"),
        "created_by": _display_value(item.get("sys_created_by")),
        "updated_by": _display_value(item.get("sys_updated_by")),
    }


def _normalize_ui_policy(item: Dict[str, Any], actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    conditions = item.get("conditions", item.get("condition"))
    return {
        "sys_id": item.get("sys_id"),
        "name": item.get("short_description") or item.get("name"),
        "table": _reference_value(item.get("table")),
        "active": _coerce_servicenow_bool(item.get("active")),
        "conditions": conditions,
        "condition": conditions,
        "short_description": item.get("short_description"),
        "description": item.get("description"),
        "order": item.get("order"),
        "on_load": _coerce_servicenow_bool(item.get("on_load")),
        "reverse_if_false": _coerce_servicenow_bool(item.get("reverse_if_false")),
        "run_scripts": _coerce_servicenow_bool(item.get("run_scripts")),
        "script_true": item.get("script_true"),
        "script_false": item.get("script_false"),
        "global": _coerce_servicenow_bool(item.get("global")),
        "view": _display_value(item.get("view")),
        "comments": item.get("comments"),
        "actions": actions,
        "created_on": item.get("sys_created_on"),
        "updated_on": item.get("sys_updated_on"),
        "created_by": _display_value(item.get("sys_created_by")),
        "updated_by": _display_value(item.get("sys_updated_by")),
    }


def _resolve_ui_policy_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    ui_policy_id: Optional[str],
) -> Optional[str]:
    if ui_policy_id is None:
        return None

    sys_id = _extract_sys_id(ui_policy_id)
    if sys_id:
        response = requests.get(
            f"{config.instance_url}/api/now/table/sys_ui_policy/{sys_id}",
            params={
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": "sys_id",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result")
        if not isinstance(result, dict) or result.get("sys_id") != sys_id:
            raise ValueError(f"UI Policy not found or not readable: {sys_id}")
        return sys_id

    result = get_ui_policy(config, auth_manager, GetUIPolicyParams(ui_policy_id=ui_policy_id))
    if not result.get("success"):
        raise ValueError(result.get("message") or f"UI Policy not found: {ui_policy_id}")

    resolved_sys_id = result.get("ui_policy", {}).get("sys_id")
    if not resolved_sys_id:
        raise ValueError(f"UI Policy did not include a sys_id: {ui_policy_id}")
    return resolved_sys_id


def _get_ui_policy_display_name(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
) -> Optional[str]:
    try:
        response = requests.get(
            f"{config.instance_url}/api/now/table/sys_ui_policy/{policy_sys_id}",
            params={
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": "sys_id,short_description,name",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result")
        if not isinstance(result, dict):
            return None
        return result.get("short_description") or result.get("name")
    except Exception as error:
        logger.debug("Could not get UI Policy display name: %s", error)
        return None


def _ui_policy_action_parent_field_candidates(
    config: ServerConfig,
    auth_manager: AuthManager,
) -> List[str]:
    candidates: List[str] = []
    try:
        response = requests.get(
            f"{config.instance_url}/api/now/table/sys_dictionary",
            params={
                "sysparm_query": (
                    "name=sys_ui_policy_action^reference=sys_ui_policy^elementISNOTEMPTY"
                ),
                "sysparm_fields": "element",
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        for item in response.json().get("result", []):
            if isinstance(item, dict) and item.get("element"):
                candidates.append(str(item["element"]))
    except Exception as error:
        logger.debug("Could not discover UI Policy Action parent field: %s", error)

    if candidates:
        return list(dict.fromkeys(candidates))
    return list(UI_POLICY_ACTION_PARENT_FIELD_FALLBACKS)


def _ui_policy_action_parent_body(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
) -> Dict[str, str]:
    return {
        parent_field: policy_sys_id
        for parent_field in _ui_policy_action_parent_field_candidates(config, auth_manager)
    }


def _ui_policy_action_parent_display_body(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
) -> Optional[Dict[str, str]]:
    display_name = _get_ui_policy_display_name(config, auth_manager, policy_sys_id)
    if not display_name:
        return None
    return {
        parent_field: display_name
        for parent_field in _ui_policy_action_parent_field_candidates(config, auth_manager)
    }


def _ui_policy_action_parent_query(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
    action_sys_id: Optional[str] = None,
) -> str:
    parent_queries = [
        f"{parent_field}={policy_sys_id}"
        for parent_field in _ui_policy_action_parent_field_candidates(config, auth_manager)
    ]
    query = "^NQ".join(parent_queries)
    if action_sys_id:
        query = "^NQ".join(
            f"{parent_query}^sys_id={action_sys_id}" for parent_query in parent_queries
        )
    return query


def _ui_policy_action_parent_value(action: Optional[Dict[str, Any]]) -> Optional[str]:
    if not action:
        return None
    for parent_field in UI_POLICY_ACTION_PARENT_FIELD_FALLBACKS:
        value = _reference_value(action.get(parent_field))
        if value not in (None, ""):
            return str(value)
    for key, value in action.items():
        if str(key).startswith("__"):
            continue
        if "policy" in str(key).lower():
            resolved = _reference_value(value)
            if resolved not in (None, ""):
                return str(resolved)
    return None


def _ui_policy_action_parent_display(action: Optional[Dict[str, Any]]) -> Optional[str]:
    if not action:
        return None
    for parent_field in UI_POLICY_ACTION_PARENT_FIELD_FALLBACKS:
        value = _display_value(action.get(parent_field))
        if value not in (None, ""):
            return str(value)
    return None


def _build_ui_policy_action_body(
    *,
    config: Optional[ServerConfig] = None,
    auth_manager: Optional[AuthManager] = None,
    ui_policy_id: Optional[str] = None,
    field: Optional[str] = None,
    mandatory: Optional[bool] = None,
    visible: Optional[bool] = None,
    readonly: Optional[bool] = None,
    order: Optional[int] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {**(attributes or {})}
    if ui_policy_id is not None:
        if config is not None and auth_manager is not None:
            body.update(_ui_policy_action_parent_body(config, auth_manager, ui_policy_id))
        else:
            body["ui_policy"] = ui_policy_id
    if field is not None:
        body["field"] = field
    if mandatory is not None:
        body["mandatory"] = str(mandatory).lower()
    if visible is not None:
        body["visible"] = str(visible).lower()
    if readonly is not None:
        body["disabled"] = str(readonly).lower()
    if order is not None:
        body["order"] = order
    return body


def _delete_ui_policy_action_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    action_sys_id: str,
) -> None:
    try:
        response = requests.delete(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action/{action_sys_id}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
    except Exception as error:
        logger.warning("Could not delete orphan UI Policy action %s: %s", action_sys_id, error)


def _create_linked_ui_policy_action_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
    action_body: Dict[str, Any],
) -> Dict[str, Any]:
    attempts: List[str] = []
    response = requests.post(
        f"{config.instance_url}/api/now/table/sys_ui_policy_action",
        params={"sysparm_input_display_value": "false"},
        json={**action_body, **_ui_policy_action_parent_body(config, auth_manager, policy_sys_id)},
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    result = response.json().get("result", {})
    action_sys_id = result.get("sys_id")
    if not action_sys_id:
        raise ValueError("ServiceNow did not return a sys_id for the UI Policy action")

    try:
        return _ensure_ui_policy_action_linked(
            config,
            auth_manager,
            action_sys_id,
            policy_sys_id,
        )
    except ValueError as link_error:
        attempts.append(f"sys_id body create failed: {link_error}")
        _delete_ui_policy_action_record(config, auth_manager, action_sys_id)

        response = requests.post(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action",
            params={
                "sysparm_input_display_value": "false",
                "sysparm_query": _ui_policy_action_parent_query(
                    config,
                    auth_manager,
                    policy_sys_id,
                ),
            },
            json=action_body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        action_sys_id = result.get("sys_id")
        if action_sys_id:
            try:
                return _ensure_ui_policy_action_linked(
                    config,
                    auth_manager,
                    action_sys_id,
                    policy_sys_id,
                )
            except ValueError as query_link_error:
                attempts.append(f"related-list query create failed: {query_link_error}")
                _delete_ui_policy_action_record(config, auth_manager, action_sys_id)
        else:
            attempts.append("related-list query create did not return a sys_id")

        display_body = _ui_policy_action_parent_display_body(
            config,
            auth_manager,
            policy_sys_id,
        )
        if not display_body:
            raise ValueError("; ".join(attempts)) from link_error

        response = requests.post(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action",
            params={"sysparm_input_display_value": "true"},
            json={**action_body, **display_body},
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        action_sys_id = result.get("sys_id")
        if not action_sys_id:
            raise ValueError(
                "ServiceNow did not return a sys_id for the UI Policy action"
            ) from link_error
        try:
            return _ensure_ui_policy_action_linked(
                config,
                auth_manager,
                action_sys_id,
                policy_sys_id,
            )
        except ValueError as display_link_error:
            attempts.append(f"display-value create failed: {display_link_error}")
            action_record = _get_ui_policy_action_record(config, auth_manager, action_sys_id)
            if not action_record:
                action_record = {"sys_id": action_sys_id, **action_body}
            action_record["__link_warning"] = (
                "ServiceNow created the UI Policy Action but stripped the parent "
                "ui_policy reference. The action is currently not linked to the UI Policy. "
                "This is usually caused by a write ACL, Business Rule, Data Policy, "
                "or scope restriction on sys_ui_policy_action.ui_policy."
            )
            action_record["__link_attempts"] = attempts
            action_record["__expected_ui_policy"] = policy_sys_id
            logger.debug("%s Attempts: %s", action_record["__link_warning"], "; ".join(attempts))
            return action_record


def _get_ui_policy_action_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    ui_policy_action_id: str,
) -> Optional[Dict[str, Any]]:
    sys_id = _extract_sys_id(ui_policy_action_id)
    if not sys_id:
        return None

    response = requests.get(
        f"{config.instance_url}/api/now/table/sys_ui_policy_action/{sys_id}",
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_policy_action_fields(),
        },
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    result = response.json().get("result")
    return result if isinstance(result, dict) else None


def _get_ui_policy_related_action_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
    action_sys_id: str,
) -> Optional[Dict[str, Any]]:
    response = requests.get(
        f"{config.instance_url}/api/now/table/sys_ui_policy_action",
        params={
            "sysparm_query": _ui_policy_action_parent_query(
                config,
                auth_manager,
                policy_sys_id,
                action_sys_id,
            ),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_policy_action_fields(),
            "sysparm_limit": 1,
        },
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    result = response.json().get("result", [])
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return result[0]
    return None


def _action_parent_value(action: Optional[Dict[str, Any]]) -> Optional[str]:
    return _ui_policy_action_parent_value(action)


def _action_is_linked_to_policy(
    action: Optional[Dict[str, Any]],
    policy_sys_id: str,
) -> bool:
    return _action_parent_value(action) == policy_sys_id


def _ensure_ui_policy_action_linked(
    config: ServerConfig,
    auth_manager: AuthManager,
    action_sys_id: str,
    policy_sys_id: str,
) -> Dict[str, Any]:
    for _attempt in range(2):
        related_action = _get_ui_policy_related_action_record(
            config,
            auth_manager,
            policy_sys_id,
            action_sys_id,
        )
        if related_action is not None:
            return related_action

        direct_action = _get_ui_policy_action_record(config, auth_manager, action_sys_id)
        if _action_is_linked_to_policy(direct_action, policy_sys_id):
            return direct_action

        time.sleep(0.25)

    response = requests.patch(
        f"{config.instance_url}/api/now/table/sys_ui_policy_action/{action_sys_id}",
        params={"sysparm_input_display_value": "false"},
        json=_ui_policy_action_parent_body(config, auth_manager, policy_sys_id),
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()

    for _attempt in range(3):
        related_action = _get_ui_policy_related_action_record(
            config,
            auth_manager,
            policy_sys_id,
            action_sys_id,
        )
        if related_action is not None:
            return related_action

        direct_action = _get_ui_policy_action_record(config, auth_manager, action_sys_id)
        if _action_is_linked_to_policy(direct_action, policy_sys_id):
            return direct_action

        if _attempt < 2:
            time.sleep(0.25)

    display_body = _ui_policy_action_parent_display_body(config, auth_manager, policy_sys_id)
    if display_body:
        response = requests.patch(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action/{action_sys_id}",
            params={"sysparm_input_display_value": "true"},
            json=display_body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()

        for _attempt in range(3):
            related_action = _get_ui_policy_related_action_record(
                config,
                auth_manager,
                policy_sys_id,
                action_sys_id,
            )
            if related_action is not None:
                return related_action

            direct_action = _get_ui_policy_action_record(config, auth_manager, action_sys_id)
            if _action_is_linked_to_policy(direct_action, policy_sys_id):
                return direct_action

            if _attempt < 2:
                time.sleep(0.25)

    direct_action = _get_ui_policy_action_record(config, auth_manager, action_sys_id)
    observed_parent = _action_parent_value(direct_action) or "<empty>"
    observed_fields = ", ".join(sorted(str(key) for key in (direct_action or {}).keys()))
    candidates = ", ".join(_ui_policy_action_parent_field_candidates(config, auth_manager))
    raise ValueError(
        f"UI Policy action {action_sys_id} parent is {observed_parent}; "
        f"expected UI Policy {policy_sys_id}. Tried parent fields: {candidates}. "
        f"Returned fields: {observed_fields or '<none>'}"
    )


def list_ui_policies(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListUIPoliciesParams,
) -> Dict[str, Any]:
    """List UI Policies from ServiceNow."""
    try:
        url = f"{config.instance_url}/api/now/table/sys_ui_policy"
        query_params = {
            "sysparm_limit": params.limit,
            "sysparm_offset": params.offset,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_policy_fields(),
        }

        query_parts = []
        if params.active is not None:
            query_parts.append(f"active={str(params.active).lower()}")
        if params.table:
            query_parts.append(f"table={params.table}")
        if params.query:
            query_parts.append(f"short_descriptionLIKE{params.query}")

        query_params["sysparm_query"] = "^".join(query_parts)

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()
        ui_policies = []
        for item in data.get("result", []):
            # Get actions for this policy
            actions = _get_ui_policy_actions(config, auth_manager, item.get("sys_id"))
            ui_policies.append(_normalize_ui_policy(item, actions))

        return {
            "success": True,
            "message": f"Found {len(ui_policies)} UI Policies",
            "ui_policies": ui_policies,
            "total": len(ui_policies),
            "limit": params.limit,
            "offset": params.offset,
        }

    except Exception as e:
        logger.error(f"Error listing UI Policies: {e}")
        return {
            "success": False,
            "message": f"Error listing UI Policies: {str(e)}",
            "ui_policies": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def _get_ui_policy_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
) -> List[Dict[str, Any]]:
    """Get actions for a UI Policy."""
    try:
        url = f"{config.instance_url}/api/now/table/sys_ui_policy_action"
        query_params = {
            "sysparm_query": _ui_policy_action_parent_query(
                config,
                auth_manager,
                policy_sys_id,
            ),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_policy_action_fields(),
        }

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()
        actions = []
        for item in data.get("result", []):
            actions.append(_normalize_ui_policy_action(item))

        return actions

    except Exception as e:
        logger.error(f"Error getting UI Policy actions: {e}")
        return []


def get_ui_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetUIPolicyParams,
) -> Dict[str, Any]:
    """Get a specific UI Policy from ServiceNow."""
    try:
        query_params = {
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_ui_policy_fields(),
        }

        # Determine if we're querying by sys_id or short description
        sys_id = _extract_sys_id(params.ui_policy_id)
        if sys_id:
            url = f"{config.instance_url}/api/now/table/sys_ui_policy/{sys_id}"
        else:
            url = f"{config.instance_url}/api/now/table/sys_ui_policy"
            query_params["sysparm_query"] = f"short_description={params.ui_policy_id}"

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()

        if "result" not in data:
            return {
                "success": False,
                "message": f"UI Policy not found: {params.ui_policy_id}",
            }

        # Handle both single result and list of results
        result = data["result"]
        if isinstance(result, list):
            if not result:
                return {
                    "success": False,
                    "message": f"UI Policy not found: {params.ui_policy_id}",
                }
            item = result[0]
        else:
            item = result

        # Get actions for this policy
        actions = _get_ui_policy_actions(config, auth_manager, item.get("sys_id"))
        ui_policy = _normalize_ui_policy(item, actions)

        return {
            "success": True,
            "message": f"Found UI Policy: {item.get('short_description')}",
            "ui_policy": ui_policy,
        }

    except Exception as e:
        logger.error(f"Error getting UI Policy: {e}")
        return {
            "success": False,
            "message": f"Error getting UI Policy: {str(e)}",
        }


def create_ui_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateUIPolicyParams,
) -> UIPolicyResponse:
    """Create a new UI Policy in ServiceNow."""
    url = f"{config.instance_url}/api/now/table/sys_ui_policy"

    # Build the request body
    body = {**(params.attributes or {})}
    body.update(
        {
            "short_description": params.short_description or params.name,
            "table": params.table,
            "active": str(params.active).lower(),
            "on_load": str(params.on_load).lower(),
            "reverse_if_false": str(params.reverse_if_false).lower(),
            "run_scripts": str(params.run_scripts).lower(),
        }
    )

    conditions = _requested_conditions(params)
    if conditions:
        body["conditions"] = conditions
    if params.description:
        body["description"] = params.description
    if params.order is not None:
        body["order"] = params.order
    if params.script_true is not None:
        body["script_true"] = params.script_true
    if params.script_false is not None:
        body["script_false"] = params.script_false
    if params.global_policy is not None:
        body["global"] = str(params.global_policy).lower()
    if params.view is not None:
        body["view"] = params.view
    if params.comments:
        body["comments"] = params.comments

    headers = auth_manager.get_headers()

    try:
        response = requests.post(url, json=body, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()

        if "result" not in data:
            return UIPolicyResponse(
                success=False,
                message="Failed to create UI Policy",
            )

        result = data["result"]
        policy_sys_id = result.get("sys_id")

        # Create actions for this policy
        if params.actions:
            _create_ui_policy_actions(config, auth_manager, policy_sys_id, params.actions)

        return UIPolicyResponse(
            success=True,
            message=f"Created UI Policy: {result.get('short_description')}",
            ui_policy_id=policy_sys_id,
            ui_policy_name=result.get("short_description"),
        )

    except Exception as e:
        logger.error(f"Error creating UI Policy: {e}")
        return UIPolicyResponse(
            success=False,
            message=f"Error creating UI Policy: {str(e)}",
        )


def _create_ui_policy_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
    actions: List[UIPolicyAction],
) -> None:
    """Create actions for a UI Policy."""
    for action in actions:
        try:
            action_body = _build_ui_policy_action_body(
                field=action.field,
                mandatory=action.mandatory,
                visible=action.visible,
                readonly=action.readonly,
            )
            _create_linked_ui_policy_action_record(
                config,
                auth_manager,
                policy_sys_id,
                action_body,
            )
        except Exception as e:
            logger.error(f"Error creating UI Policy action for field {action.field}: {e}")


def update_ui_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateUIPolicyParams,
) -> UIPolicyResponse:
    """Update an existing UI Policy in ServiceNow."""
    # First, get the UI Policy to update
    get_params = GetUIPolicyParams(ui_policy_id=params.ui_policy_id)
    get_result = get_ui_policy(config, auth_manager, get_params)

    if not get_result["success"]:
        return UIPolicyResponse(
            success=False,
            message=get_result["message"],
        )

    ui_policy = get_result["ui_policy"]
    sys_id = ui_policy["sys_id"]

    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_ui_policy/{sys_id}"

    # Build the request body
    body = {**(params.attributes or {})}

    if params.name is not None:
        body["short_description"] = params.name
    if params.table is not None:
        body["table"] = params.table
    conditions = _requested_conditions(params)
    if conditions is not None:
        body["conditions"] = conditions
    if params.active is not None:
        body["active"] = str(params.active).lower()
    if params.short_description is not None:
        body["short_description"] = params.short_description
    if params.description is not None:
        body["description"] = params.description
    if params.order is not None:
        body["order"] = params.order
    if params.on_load is not None:
        body["on_load"] = str(params.on_load).lower()
    if params.reverse_if_false is not None:
        body["reverse_if_false"] = str(params.reverse_if_false).lower()
    if params.run_scripts is not None:
        body["run_scripts"] = str(params.run_scripts).lower()
    if params.script_true is not None:
        body["script_true"] = params.script_true
    if params.script_false is not None:
        body["script_false"] = params.script_false
    if params.global_policy is not None:
        body["global"] = str(params.global_policy).lower()
    if params.view is not None:
        body["view"] = params.view
    if params.comments is not None:
        body["comments"] = params.comments

    headers = auth_manager.get_headers()

    try:
        # Update the policy
        if body:
            response = requests.patch(url, json=body, headers=headers, timeout=config.timeout)
            response.raise_for_status()

        # Update actions if provided
        if params.actions is not None:
            _update_ui_policy_actions(config, auth_manager, sys_id, params.actions)

        # Get updated policy for response
        updated_result = get_ui_policy(config, auth_manager, get_params)
        if updated_result["success"]:
            updated_policy = updated_result["ui_policy"]
            return UIPolicyResponse(
                success=True,
                message=f"Updated UI Policy: {updated_policy.get('name')}",
                ui_policy_id=sys_id,
                ui_policy_name=updated_policy.get("name"),
            )
        else:
            return UIPolicyResponse(
                success=True,
                message=f"Updated UI Policy: {ui_policy['name']}",
                ui_policy_id=sys_id,
                ui_policy_name=ui_policy["name"],
            )

    except Exception as e:
        logger.error(f"Error updating UI Policy: {e}")
        return UIPolicyResponse(
            success=False,
            message=f"Error updating UI Policy: {str(e)}",
        )


def _update_ui_policy_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
    actions: List[UIPolicyAction],
) -> None:
    """Update actions for a UI Policy."""
    # First, delete existing actions
    _delete_ui_policy_actions(config, auth_manager, policy_sys_id)

    # Then create new actions
    _create_ui_policy_actions(config, auth_manager, policy_sys_id, actions)


def _delete_ui_policy_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    policy_sys_id: str,
) -> None:
    """Delete all actions for a UI Policy."""
    try:
        # Get all actions for this policy
        url = f"{config.instance_url}/api/now/table/sys_ui_policy_action"
        query_params = {
            "sysparm_query": _ui_policy_action_parent_query(
                config,
                auth_manager,
                policy_sys_id,
            ),
            "sysparm_fields": "sys_id",
        }

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()
        for item in data.get("result", []):
            action_sys_id = item.get("sys_id")
            delete_url = f"{config.instance_url}/api/now/table/sys_ui_policy_action/{action_sys_id}"
            requests.delete(delete_url, headers=headers, timeout=config.timeout)

    except Exception as e:
        logger.error(f"Error deleting UI Policy actions: {e}")


def list_ui_policy_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListUIPolicyActionsParams,
) -> Dict[str, Any]:
    """List UI Policy actions from ServiceNow."""
    try:
        query_parts = []
        if params.ui_policy_id:
            policy_sys_id = _resolve_ui_policy_sys_id(config, auth_manager, params.ui_policy_id)
            query_parts.append(_ui_policy_action_parent_query(config, auth_manager, policy_sys_id))
        if params.field:
            query_parts.append(f"field={params.field}")

        response = requests.get(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action",
            params={
                "sysparm_limit": params.limit,
                "sysparm_offset": params.offset,
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": _build_ui_policy_action_fields(),
                "sysparm_query": "^".join(query_parts),
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        actions = [
            _normalize_ui_policy_action(item)
            for item in response.json().get("result", [])
            if isinstance(item, dict)
        ]
        return {
            "success": True,
            "message": f"Found {len(actions)} UI Policy actions",
            "ui_policy_actions": actions,
            "total": len(actions),
            "limit": params.limit,
            "offset": params.offset,
        }
    except Exception as error:
        logger.error("Error listing UI Policy actions: %s", error)
        return {
            "success": False,
            "message": f"Error listing UI Policy actions: {_extract_error_body(error)}",
            "ui_policy_actions": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_ui_policy_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetUIPolicyActionParams,
) -> Dict[str, Any]:
    """Get a specific UI Policy action from ServiceNow."""
    try:
        action = _get_ui_policy_action_record(config, auth_manager, params.ui_policy_action_id)
        if action is None:
            return {
                "success": False,
                "message": f"UI Policy action not found: {params.ui_policy_action_id}",
            }
        normalized = _normalize_ui_policy_action(action)
        return {
            "success": True,
            "message": f"Found UI Policy action for field: {normalized.get('field')}",
            "ui_policy_action": normalized,
        }
    except Exception as error:
        logger.error("Error getting UI Policy action: %s", error)
        return {
            "success": False,
            "message": f"Error getting UI Policy action: {_extract_error_body(error)}",
        }


def create_ui_policy_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateUIPolicyActionParams,
) -> UIPolicyActionResponse:
    """Create a UI Policy action in ServiceNow."""
    try:
        policy_sys_id = _resolve_ui_policy_sys_id(config, auth_manager, params.ui_policy_id)
        action_body = _build_ui_policy_action_body(
            field=params.field,
            mandatory=params.mandatory,
            visible=params.visible,
            readonly=params.readonly,
            order=params.order,
            attributes=params.attributes,
        )
        linked_action = _create_linked_ui_policy_action_record(
            config,
            auth_manager,
            policy_sys_id,
            action_body,
        )
        normalized = _normalize_ui_policy_action(linked_action)
        link_warning = linked_action.get("__link_warning")
        if link_warning:
            return UIPolicyActionResponse(
                success=True,
                message=(
                    f"Created UI Policy action for field {normalized.get('field')}, "
                    f"but it is not linked: {link_warning}"
                ),
                ui_policy_action_id=normalized.get("sys_id"),
                ui_policy_id=normalized.get("ui_policy"),
                field=normalized.get("field"),
            )
        return UIPolicyActionResponse(
            success=True,
            message=f"Created and linked UI Policy action for field: {normalized.get('field')}",
            ui_policy_action_id=normalized.get("sys_id"),
            ui_policy_id=normalized.get("ui_policy") or policy_sys_id,
            field=normalized.get("field"),
        )
    except Exception as error:
        logger.error("Error creating UI Policy action: %s", error)
        return UIPolicyActionResponse(
            success=False,
            message=f"Error creating UI Policy action: {_extract_error_body(error)}",
        )


def update_ui_policy_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateUIPolicyActionParams,
) -> UIPolicyActionResponse:
    """Update a UI Policy action in ServiceNow."""
    try:
        existing = _get_ui_policy_action_record(config, auth_manager, params.ui_policy_action_id)
        if existing is None:
            return UIPolicyActionResponse(
                success=False,
                message=f"UI Policy action not found: {params.ui_policy_action_id}",
            )

        policy_sys_id = _resolve_ui_policy_sys_id(config, auth_manager, params.ui_policy_id)
        body = _build_ui_policy_action_body(
            config=config,
            auth_manager=auth_manager,
            ui_policy_id=policy_sys_id,
            field=params.field,
            mandatory=params.mandatory,
            visible=params.visible,
            readonly=params.readonly,
            order=params.order,
            attributes=params.attributes,
        )
        if not body:
            return UIPolicyActionResponse(
                success=True,
                message=f"No changes to update for UI Policy action: {existing.get('sys_id')}",
                ui_policy_action_id=existing.get("sys_id"),
                ui_policy_id=_reference_value(existing.get("ui_policy")),
                field=existing.get("field"),
            )

        response = requests.patch(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action/{existing['sys_id']}",
            params={"sysparm_input_display_value": "false"},
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        if policy_sys_id:
            result = _ensure_ui_policy_action_linked(
                config,
                auth_manager,
                existing["sys_id"],
                policy_sys_id,
            )
        normalized = _normalize_ui_policy_action(result)
        return UIPolicyActionResponse(
            success=True,
            message=f"Updated UI Policy action for field: {normalized.get('field')}",
            ui_policy_action_id=normalized.get("sys_id"),
            ui_policy_id=normalized.get("ui_policy") or policy_sys_id,
            field=normalized.get("field"),
        )
    except Exception as error:
        logger.error("Error updating UI Policy action: %s", error)
        return UIPolicyActionResponse(
            success=False,
            message=f"Error updating UI Policy action: {_extract_error_body(error)}",
        )


def delete_ui_policy_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteUIPolicyActionParams,
) -> UIPolicyActionResponse:
    """Delete a UI Policy action from ServiceNow."""
    try:
        existing = _get_ui_policy_action_record(config, auth_manager, params.ui_policy_action_id)
        if existing is None:
            return UIPolicyActionResponse(
                success=False,
                message=f"UI Policy action not found: {params.ui_policy_action_id}",
            )

        response = requests.delete(
            f"{config.instance_url}/api/now/table/sys_ui_policy_action/{existing['sys_id']}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return UIPolicyActionResponse(
            success=True,
            message=f"Deleted UI Policy action for field: {existing.get('field')}",
            ui_policy_action_id=existing.get("sys_id"),
            ui_policy_id=_reference_value(existing.get("ui_policy")),
            field=existing.get("field"),
        )
    except Exception as error:
        logger.error("Error deleting UI Policy action: %s", error)
        return UIPolicyActionResponse(
            success=False,
            message=f"Error deleting UI Policy action: {_extract_error_body(error)}",
        )


def delete_ui_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteUIPolicyParams,
) -> UIPolicyResponse:
    """Delete a UI Policy from ServiceNow."""
    # First, get the UI Policy to delete
    get_params = GetUIPolicyParams(ui_policy_id=params.ui_policy_id)
    get_result = get_ui_policy(config, auth_manager, get_params)

    if not get_result["success"]:
        return UIPolicyResponse(
            success=False,
            message=get_result["message"],
        )

    ui_policy = get_result["ui_policy"]
    sys_id = ui_policy["sys_id"]
    name = ui_policy["name"]

    # Delete actions first
    _delete_ui_policy_actions(config, auth_manager, sys_id)

    # Build the URL
    url = f"{config.instance_url}/api/now/table/sys_ui_policy/{sys_id}"

    headers = auth_manager.get_headers()

    try:
        response = requests.delete(url, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        return UIPolicyResponse(
            success=True,
            message=f"Deleted UI Policy: {name}",
            ui_policy_id=sys_id,
            ui_policy_name=name,
        )

    except Exception as e:
        logger.error(f"Error deleting UI Policy: {e}")
        return UIPolicyResponse(
            success=False,
            message=f"Error deleting UI Policy: {str(e)}",
        )
