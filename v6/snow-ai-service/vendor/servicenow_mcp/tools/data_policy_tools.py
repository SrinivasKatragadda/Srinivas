"""
Data Policy tools for the ServiceNow MCP server.

This module provides helpers for Data Policies and Data Policy Rules in
ServiceNow. Data Policies enforce mandatory and read-only behavior on the
server side, and can optionally be mirrored by UI Policy behavior on the
client, so this module focuses on the authoritative policy records.
"""

import logging
import re
from typing import Any, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.ui_policy_tools import (
    DeleteUIPolicyParams,
    GetUIPolicyParams,
    UpdateUIPolicyParams,
    delete_ui_policy,
    get_ui_policy,
    update_ui_policy,
)
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

DATA_POLICY_TABLE = "sys_data_policy2"
DATA_POLICY_RULE_TABLE = "sys_data_policy_rule"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class DataPolicyRuleInput(BaseModel):
    """Data Policy rule configuration used inside create/update policy requests."""

    field: str = Field(..., description="Field name this rule applies to")
    mandatory: Optional[bool] = Field(None, description="Require a value for the field")
    read_only: Optional[bool] = Field(None, description="Prevent edits to the field")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_data_policy_rule fields to send directly"
    )


class ListDataPoliciesParams(BaseModel):
    """Parameters for listing Data Policies."""

    limit: int = Field(10, description="Maximum number of Data Policies to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    table: Optional[str] = Field(None, description="Filter by table name")
    query: Optional[str] = Field(None, description="Search short descriptions")
    include_rules: bool = Field(True, description="Include child Data Policy Rules")


class GetDataPolicyParams(BaseModel):
    """Parameters for getting a Data Policy."""

    data_policy_id: str = Field(..., description="Data Policy sys_id or short description")
    include_rules: bool = Field(True, description="Include child Data Policy Rules")


class CreateDataPolicyParams(BaseModel):
    """Parameters for creating a Data Policy."""

    short_description: str = Field(..., description="Short description for the policy")
    table: str = Field(..., description="Table the Data Policy applies to")
    condition: Optional[str] = Field(None, description="Encoded condition for the policy")
    active: bool = Field(True, description="Whether the policy is active")
    reverse_if_false: bool = Field(True, description="Reverse rule effects when condition is false")
    use_as_ui_policy: bool = Field(
        False, description="Also enforce the Data Policy as a client-side UI Policy"
    )
    order: Optional[int] = Field(None, description="Policy evaluation order")
    description: Optional[str] = Field(None, description="Full description")
    rules: List[DataPolicyRuleInput] = Field(
        default_factory=list, description="Rules to create under this Data Policy"
    )
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_data_policy2 fields to send directly to ServiceNow"
    )


class UpdateDataPolicyParams(BaseModel):
    """Parameters for updating a Data Policy."""

    data_policy_id: str = Field(..., description="Data Policy sys_id or short description")
    short_description: Optional[str] = Field(None, description="Short description for the policy")
    table: Optional[str] = Field(None, description="Table the Data Policy applies to")
    condition: Optional[str] = Field(None, description="Encoded condition for the policy")
    active: Optional[bool] = Field(None, description="Whether the policy is active")
    reverse_if_false: Optional[bool] = Field(
        None, description="Reverse rule effects when condition is false"
    )
    use_as_ui_policy: Optional[bool] = Field(
        None, description="Also enforce the Data Policy as a client-side UI Policy"
    )
    order: Optional[int] = Field(None, description="Policy evaluation order")
    description: Optional[str] = Field(None, description="Full description")
    rules: Optional[List[DataPolicyRuleInput]] = Field(
        None, description="Rules to add or replace under this Data Policy"
    )
    replace_rules: bool = Field(
        False, description="Delete existing rules before creating the supplied rules"
    )
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_data_policy2 fields to patch directly"
    )


class DeleteDataPolicyParams(BaseModel):
    """Parameters for deleting a Data Policy."""

    data_policy_id: str = Field(..., description="Data Policy sys_id or short description")
    delete_rules: bool = Field(True, description="Delete child Data Policy Rules first")


class ListDataPolicyRulesParams(BaseModel):
    """Parameters for listing Data Policy Rules."""

    limit: int = Field(50, description="Maximum number of rules to return")
    offset: int = Field(0, description="Offset for pagination")
    data_policy_id: Optional[str] = Field(None, description="Filter by parent Data Policy sys_id")
    field: Optional[str] = Field(None, description="Filter by field name")


class GetDataPolicyRuleParams(BaseModel):
    """Parameters for getting a Data Policy Rule."""

    data_policy_rule_id: str = Field(..., description="Data Policy Rule sys_id")


class CreateDataPolicyRuleParams(BaseModel):
    """Parameters for creating a Data Policy Rule."""

    data_policy_id: str = Field(..., description="Parent Data Policy sys_id")
    field: str = Field(..., description="Field name this rule applies to")
    mandatory: Optional[bool] = Field(None, description="Require a value for the field")
    read_only: Optional[bool] = Field(None, description="Prevent edits to the field")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_data_policy_rule fields to send directly"
    )


class UpdateDataPolicyRuleParams(BaseModel):
    """Parameters for updating a Data Policy Rule."""

    data_policy_rule_id: str = Field(..., description="Data Policy Rule sys_id")
    data_policy_id: Optional[str] = Field(None, description="Parent Data Policy sys_id")
    field: Optional[str] = Field(None, description="Field name this rule applies to")
    mandatory: Optional[bool] = Field(None, description="Require a value for the field")
    read_only: Optional[bool] = Field(None, description="Prevent edits to the field")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_data_policy_rule fields to patch directly"
    )


class DeleteDataPolicyRuleParams(BaseModel):
    """Parameters for deleting a Data Policy Rule."""

    data_policy_rule_id: str = Field(..., description="Data Policy Rule sys_id")


class SetDataPolicyClientEnforcementParams(BaseModel):
    """Parameters for changing Data Policy client-side enforcement."""

    data_policy_id: str = Field(..., description="Data Policy sys_id or short description")
    use_as_ui_policy: bool = Field(
        ..., description="Whether to enforce this Data Policy as a client-side UI Policy"
    )
    reverse_if_false: Optional[bool] = Field(
        None, description="Optionally update reverse-if-false behavior at the same time"
    )


class ConvertUIPolicyToDataPolicyParams(BaseModel):
    """Parameters for converting a UI Policy to a Data Policy."""

    ui_policy_id: str = Field(..., description="UI Policy sys_id or short description")
    short_description: Optional[str] = Field(
        None, description="Optional short description for the new Data Policy"
    )
    use_as_ui_policy: bool = Field(
        True,
        description="Enable client-side enforcement on the new Data Policy after conversion",
    )
    strict: bool = Field(
        True,
        description=(
            "When true, reject UI Policies that the platform conversion button would not convert: "
            "scripted, non-global/view-specific, or actions that set visibility"
        ),
    )
    source_action: Literal["keep", "deactivate", "delete"] = Field(
        "keep",
        description="What to do with the source UI Policy after creating the Data Policy",
    )
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional sys_data_policy2 fields to set on the new Data Policy"
    )


class DataPolicyResponse(BaseModel):
    """Response from Data Policy operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    data_policy_id: Optional[str] = Field(None, description="ID of the affected Data Policy")
    data_policy_name: Optional[str] = Field(None, description="Short description of the policy")


class DataPolicyRuleResponse(BaseModel):
    """Response from Data Policy Rule operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    data_policy_rule_id: Optional[str] = Field(
        None, description="ID of the affected Data Policy Rule"
    )
    data_policy_id: Optional[str] = Field(None, description="Parent Data Policy sys_id")
    field: Optional[str] = Field(None, description="Field affected by the rule")


class ConvertUIPolicyToDataPolicyResponse(BaseModel):
    """Response from converting a UI Policy to a Data Policy."""

    success: bool = Field(..., description="Whether the conversion succeeded")
    message: str = Field(..., description="Message describing the result")
    ui_policy_id: Optional[str] = Field(None, description="Source UI Policy sys_id")
    data_policy_id: Optional[str] = Field(None, description="Created Data Policy sys_id")
    data_policy_name: Optional[str] = Field(None, description="Created Data Policy name")
    created_rules: List[Dict[str, Any]] = Field(
        default_factory=list, description="Data Policy Rules created during conversion"
    )
    warnings: List[str] = Field(default_factory=list, description="Non-fatal conversion warnings")


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


def _coerce_bool(value: Any) -> bool:
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


def _data_policy_fields() -> str:
    return (
        "sys_id,short_description,model_table,table,conditions,condition,active,"
        "reverse_if_false,enforce_ui,use_as_ui_policy,order,description,"
        "sys_created_on,sys_updated_on,sys_created_by,sys_updated_by"
    )


def _data_policy_rule_fields() -> str:
    return (
        "sys_id,sys_data_policy,data_policy,field,mandatory,read_only,readonly,"
        "sys_created_on,sys_updated_on,sys_created_by,sys_updated_by"
    )


def _normalize_data_policy_rule(item: Dict[str, Any]) -> Dict[str, Any]:
    read_only = item.get("read_only")
    if read_only in (None, ""):
        read_only = item.get("readonly")
    policy_reference = item.get("sys_data_policy") or item.get("data_policy")
    return {
        "sys_id": item.get("sys_id"),
        "data_policy": _reference_value(policy_reference),
        "data_policy_display": _display_value(policy_reference),
        "field": item.get("field"),
        "mandatory": _coerce_optional_bool(item.get("mandatory")),
        "read_only": _coerce_optional_bool(read_only),
        "created_on": item.get("sys_created_on"),
        "updated_on": item.get("sys_updated_on"),
        "created_by": _display_value(item.get("sys_created_by")),
        "updated_by": _display_value(item.get("sys_updated_by")),
    }


def _normalize_data_policy(
    item: Dict[str, Any], rules: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    table = item.get("model_table") or item.get("table")
    condition = item.get("conditions")
    if condition in (None, ""):
        condition = item.get("condition")
    use_as_ui_policy = item.get("enforce_ui")
    if use_as_ui_policy in (None, ""):
        use_as_ui_policy = item.get("use_as_ui_policy")
    return {
        "sys_id": item.get("sys_id"),
        "short_description": item.get("short_description"),
        "table": _reference_value(table),
        "model_table": _reference_value(table),
        "condition": condition,
        "conditions": condition,
        "active": _coerce_bool(item.get("active")),
        "reverse_if_false": _coerce_bool(item.get("reverse_if_false")),
        "use_as_ui_policy": _coerce_bool(use_as_ui_policy),
        "order": item.get("order"),
        "description": item.get("description"),
        "rules": rules or [],
        "created_on": item.get("sys_created_on"),
        "updated_on": item.get("sys_updated_on"),
        "created_by": _display_value(item.get("sys_created_by")),
        "updated_by": _display_value(item.get("sys_updated_by")),
    }


def _build_data_policy_body(
    *,
    short_description: Optional[str] = None,
    table: Optional[str] = None,
    condition: Optional[str] = None,
    active: Optional[bool] = None,
    reverse_if_false: Optional[bool] = None,
    use_as_ui_policy: Optional[bool] = None,
    order: Optional[int] = None,
    description: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {**(attributes or {})}
    if short_description is not None:
        body["short_description"] = short_description
    if table is not None:
        body["model_table"] = table
    if condition is not None:
        body["conditions"] = condition
    if active is not None:
        body["active"] = str(active).lower()
    if reverse_if_false is not None:
        body["reverse_if_false"] = str(reverse_if_false).lower()
    if use_as_ui_policy is not None:
        body["enforce_ui"] = str(use_as_ui_policy).lower()
    if order is not None:
        body["order"] = order
    if description is not None:
        body["description"] = description
    return body


def _build_data_policy_rule_body(
    *,
    data_policy_id: Optional[str] = None,
    field: Optional[str] = None,
    mandatory: Optional[bool] = None,
    read_only: Optional[bool] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {**(attributes or {})}
    if data_policy_id is not None:
        body["sys_data_policy"] = data_policy_id
    if field is not None:
        body["field"] = field
    if mandatory is not None:
        body["mandatory"] = str(mandatory).lower()
    if read_only is not None:
        body["read_only"] = str(read_only).lower()
    return body


def _get_data_policy_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    data_policy_id: str,
) -> Optional[Dict[str, Any]]:
    query_params = {
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
        "sysparm_fields": _data_policy_fields(),
    }
    sys_id = _extract_sys_id(data_policy_id)
    if sys_id:
        url = f"{config.instance_url}/api/now/table/{DATA_POLICY_TABLE}/{sys_id}"
    else:
        url = f"{config.instance_url}/api/now/table/{DATA_POLICY_TABLE}"
        query_params["sysparm_query"] = f"short_description={data_policy_id}"
        query_params["sysparm_limit"] = 1

    response = requests.get(
        url, params=query_params, headers=auth_manager.get_headers(), timeout=config.timeout
    )
    response.raise_for_status()
    result = response.json().get("result")
    if isinstance(result, list):
        return result[0] if result else None
    return result if isinstance(result, dict) else None


def _get_data_policy_rule_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    data_policy_rule_id: str,
) -> Optional[Dict[str, Any]]:
    sys_id = _extract_sys_id(data_policy_rule_id)
    if not sys_id:
        return None
    response = requests.get(
        f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}/{sys_id}",
        params={
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _data_policy_rule_fields(),
        },
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    result = response.json().get("result")
    return result if isinstance(result, dict) else None


def _list_rules_for_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    data_policy_id: str,
) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}",
        params={
            "sysparm_query": f"sys_data_policy={data_policy_id}",
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _data_policy_rule_fields(),
        },
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return [
        _normalize_data_policy_rule(item)
        for item in response.json().get("result", [])
        if isinstance(item, dict)
    ]


def list_data_policies(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListDataPoliciesParams,
) -> Dict[str, Any]:
    """List Data Policies from ServiceNow."""
    try:
        query_parts = []
        if params.active is not None:
            query_parts.append(f"active={str(params.active).lower()}")
        if params.table:
            query_parts.append(f"model_table={params.table}")
        if params.query:
            query_parts.append(f"short_descriptionLIKE{params.query}")

        response = requests.get(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_TABLE}",
            params={
                "sysparm_limit": params.limit,
                "sysparm_offset": params.offset,
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": _data_policy_fields(),
                "sysparm_query": "^".join(query_parts),
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()

        policies = []
        for item in response.json().get("result", []):
            if not isinstance(item, dict):
                continue
            rules = []
            if params.include_rules and item.get("sys_id"):
                rules = _list_rules_for_policy(config, auth_manager, item["sys_id"])
            policies.append(_normalize_data_policy(item, rules))

        return {
            "success": True,
            "message": f"Found {len(policies)} Data Policies",
            "data_policies": policies,
            "total": len(policies),
            "limit": params.limit,
            "offset": params.offset,
        }
    except Exception as error:
        logger.error("Error listing Data Policies: %s", error)
        return {
            "success": False,
            "message": f"Error listing Data Policies: {_extract_error_body(error)}",
            "data_policies": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_data_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetDataPolicyParams,
) -> Dict[str, Any]:
    """Get a specific Data Policy from ServiceNow."""
    try:
        policy = _get_data_policy_record(config, auth_manager, params.data_policy_id)
        if policy is None:
            return {
                "success": False,
                "message": f"Data Policy not found: {params.data_policy_id}",
            }
        rules = []
        if params.include_rules and policy.get("sys_id"):
            rules = _list_rules_for_policy(config, auth_manager, policy["sys_id"])
        return {
            "success": True,
            "message": f"Found Data Policy: {policy.get('short_description')}",
            "data_policy": _normalize_data_policy(policy, rules),
        }
    except Exception as error:
        logger.error("Error getting Data Policy: %s", error)
        return {
            "success": False,
            "message": f"Error getting Data Policy: {_extract_error_body(error)}",
        }


def create_data_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateDataPolicyParams,
) -> DataPolicyResponse:
    """Create a new Data Policy in ServiceNow."""
    try:
        response = requests.post(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_TABLE}",
            json=_build_data_policy_body(
                short_description=params.short_description,
                table=params.table,
                condition=params.condition,
                active=params.active,
                reverse_if_false=params.reverse_if_false,
                use_as_ui_policy=params.use_as_ui_policy,
                order=params.order,
                description=params.description,
                attributes=params.attributes,
            ),
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        policy_id = result.get("sys_id")
        if policy_id:
            for rule in params.rules:
                create_data_policy_rule(
                    config,
                    auth_manager,
                    CreateDataPolicyRuleParams(
                        data_policy_id=policy_id,
                        field=rule.field,
                        mandatory=rule.mandatory,
                        read_only=rule.read_only,
                        attributes=rule.attributes,
                    ),
                )
        return DataPolicyResponse(
            success=True,
            message=f"Created Data Policy: {result.get('short_description')}",
            data_policy_id=policy_id,
            data_policy_name=result.get("short_description"),
        )
    except Exception as error:
        logger.error("Error creating Data Policy: %s", error)
        return DataPolicyResponse(
            success=False,
            message=f"Error creating Data Policy: {_extract_error_body(error)}",
        )


def update_data_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateDataPolicyParams,
) -> DataPolicyResponse:
    """Update an existing Data Policy in ServiceNow."""
    try:
        existing = _get_data_policy_record(config, auth_manager, params.data_policy_id)
        if existing is None:
            return DataPolicyResponse(
                success=False, message=f"Data Policy not found: {params.data_policy_id}"
            )
        body = _build_data_policy_body(
            short_description=params.short_description,
            table=params.table,
            condition=params.condition,
            active=params.active,
            reverse_if_false=params.reverse_if_false,
            use_as_ui_policy=params.use_as_ui_policy,
            order=params.order,
            description=params.description,
            attributes=params.attributes,
        )
        if body:
            response = requests.patch(
                f"{config.instance_url}/api/now/table/{DATA_POLICY_TABLE}/{existing['sys_id']}",
                json=body,
                headers=auth_manager.get_headers(),
                timeout=config.timeout,
            )
            response.raise_for_status()
            existing = response.json().get("result", existing)

        if params.rules is not None:
            if params.replace_rules:
                _delete_rules_for_policy(config, auth_manager, existing["sys_id"])
            for rule in params.rules:
                create_data_policy_rule(
                    config,
                    auth_manager,
                    CreateDataPolicyRuleParams(
                        data_policy_id=existing["sys_id"],
                        field=rule.field,
                        mandatory=rule.mandatory,
                        read_only=rule.read_only,
                        attributes=rule.attributes,
                    ),
                )

        return DataPolicyResponse(
            success=True,
            message=f"Updated Data Policy: {existing.get('short_description')}",
            data_policy_id=existing.get("sys_id"),
            data_policy_name=existing.get("short_description"),
        )
    except Exception as error:
        logger.error("Error updating Data Policy: %s", error)
        return DataPolicyResponse(
            success=False,
            message=f"Error updating Data Policy: {_extract_error_body(error)}",
        )


def delete_data_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteDataPolicyParams,
) -> DataPolicyResponse:
    """Delete a Data Policy from ServiceNow."""
    try:
        existing = _get_data_policy_record(config, auth_manager, params.data_policy_id)
        if existing is None:
            return DataPolicyResponse(
                success=False, message=f"Data Policy not found: {params.data_policy_id}"
            )
        if params.delete_rules:
            _delete_rules_for_policy(config, auth_manager, existing["sys_id"])
        response = requests.delete(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_TABLE}/{existing['sys_id']}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return DataPolicyResponse(
            success=True,
            message=f"Deleted Data Policy: {existing.get('short_description')}",
            data_policy_id=existing.get("sys_id"),
            data_policy_name=existing.get("short_description"),
        )
    except Exception as error:
        logger.error("Error deleting Data Policy: %s", error)
        return DataPolicyResponse(
            success=False,
            message=f"Error deleting Data Policy: {_extract_error_body(error)}",
        )


def list_data_policy_rules(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListDataPolicyRulesParams,
) -> Dict[str, Any]:
    """List Data Policy Rules from ServiceNow."""
    try:
        query_parts = []
        if params.data_policy_id:
            query_parts.append(f"sys_data_policy={params.data_policy_id}")
        if params.field:
            query_parts.append(f"field={params.field}")
        response = requests.get(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}",
            params={
                "sysparm_limit": params.limit,
                "sysparm_offset": params.offset,
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": _data_policy_rule_fields(),
                "sysparm_query": "^".join(query_parts),
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        rules = [
            _normalize_data_policy_rule(item)
            for item in response.json().get("result", [])
            if isinstance(item, dict)
        ]
        return {
            "success": True,
            "message": f"Found {len(rules)} Data Policy Rules",
            "data_policy_rules": rules,
            "total": len(rules),
            "limit": params.limit,
            "offset": params.offset,
        }
    except Exception as error:
        logger.error("Error listing Data Policy Rules: %s", error)
        return {
            "success": False,
            "message": f"Error listing Data Policy Rules: {_extract_error_body(error)}",
            "data_policy_rules": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_data_policy_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetDataPolicyRuleParams,
) -> Dict[str, Any]:
    """Get a specific Data Policy Rule from ServiceNow."""
    try:
        rule = _get_data_policy_rule_record(config, auth_manager, params.data_policy_rule_id)
        if rule is None:
            return {
                "success": False,
                "message": f"Data Policy Rule not found: {params.data_policy_rule_id}",
            }
        return {
            "success": True,
            "message": f"Found Data Policy Rule for field: {rule.get('field')}",
            "data_policy_rule": _normalize_data_policy_rule(rule),
        }
    except Exception as error:
        logger.error("Error getting Data Policy Rule: %s", error)
        return {
            "success": False,
            "message": f"Error getting Data Policy Rule: {_extract_error_body(error)}",
        }


def create_data_policy_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateDataPolicyRuleParams,
) -> DataPolicyRuleResponse:
    """Create a Data Policy Rule in ServiceNow."""
    try:
        response = requests.post(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}",
            json=_build_data_policy_rule_body(
                data_policy_id=params.data_policy_id,
                field=params.field,
                mandatory=params.mandatory,
                read_only=params.read_only,
                attributes=params.attributes,
            ),
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return DataPolicyRuleResponse(
            success=True,
            message=f"Created Data Policy Rule for field: {result.get('field')}",
            data_policy_rule_id=result.get("sys_id"),
            data_policy_id=_reference_value(
                result.get("sys_data_policy") or result.get("data_policy")
            ),
            field=result.get("field"),
        )
    except Exception as error:
        logger.error("Error creating Data Policy Rule: %s", error)
        return DataPolicyRuleResponse(
            success=False,
            message=f"Error creating Data Policy Rule: {_extract_error_body(error)}",
        )


def update_data_policy_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateDataPolicyRuleParams,
) -> DataPolicyRuleResponse:
    """Update a Data Policy Rule in ServiceNow."""
    try:
        existing = _get_data_policy_rule_record(config, auth_manager, params.data_policy_rule_id)
        if existing is None:
            return DataPolicyRuleResponse(
                success=False,
                message=f"Data Policy Rule not found: {params.data_policy_rule_id}",
            )
        body = _build_data_policy_rule_body(
            data_policy_id=params.data_policy_id,
            field=params.field,
            mandatory=params.mandatory,
            read_only=params.read_only,
            attributes=params.attributes,
        )
        if not body:
            return DataPolicyRuleResponse(
                success=True,
                message=f"No changes to update for Data Policy Rule: {existing.get('sys_id')}",
                data_policy_rule_id=existing.get("sys_id"),
                data_policy_id=_reference_value(
                    existing.get("sys_data_policy") or existing.get("data_policy")
                ),
                field=existing.get("field"),
            )
        response = requests.patch(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}/{existing['sys_id']}",
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        return DataPolicyRuleResponse(
            success=True,
            message=f"Updated Data Policy Rule for field: {result.get('field')}",
            data_policy_rule_id=result.get("sys_id"),
            data_policy_id=_reference_value(
                result.get("sys_data_policy") or result.get("data_policy")
            ),
            field=result.get("field"),
        )
    except Exception as error:
        logger.error("Error updating Data Policy Rule: %s", error)
        return DataPolicyRuleResponse(
            success=False,
            message=f"Error updating Data Policy Rule: {_extract_error_body(error)}",
        )


def delete_data_policy_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteDataPolicyRuleParams,
) -> DataPolicyRuleResponse:
    """Delete a Data Policy Rule from ServiceNow."""
    try:
        existing = _get_data_policy_rule_record(config, auth_manager, params.data_policy_rule_id)
        if existing is None:
            return DataPolicyRuleResponse(
                success=False,
                message=f"Data Policy Rule not found: {params.data_policy_rule_id}",
            )
        response = requests.delete(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}/{existing['sys_id']}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return DataPolicyRuleResponse(
            success=True,
            message=f"Deleted Data Policy Rule for field: {existing.get('field')}",
            data_policy_rule_id=existing.get("sys_id"),
            data_policy_id=_reference_value(
                existing.get("sys_data_policy") or existing.get("data_policy")
            ),
            field=existing.get("field"),
        )
    except Exception as error:
        logger.error("Error deleting Data Policy Rule: %s", error)
        return DataPolicyRuleResponse(
            success=False,
            message=f"Error deleting Data Policy Rule: {_extract_error_body(error)}",
        )


def set_data_policy_client_enforcement(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: SetDataPolicyClientEnforcementParams,
) -> DataPolicyResponse:
    """Enable or disable using a Data Policy as a client-side UI Policy."""
    return update_data_policy(
        config,
        auth_manager,
        UpdateDataPolicyParams(
            data_policy_id=params.data_policy_id,
            use_as_ui_policy=params.use_as_ui_policy,
            reverse_if_false=params.reverse_if_false,
        ),
    )


def convert_ui_policy_to_data_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ConvertUIPolicyToDataPolicyParams,
) -> ConvertUIPolicyToDataPolicyResponse:
    """Convert an eligible UI Policy and its actions into a Data Policy and rules."""
    try:
        ui_policy_result = get_ui_policy(
            config, auth_manager, GetUIPolicyParams(ui_policy_id=params.ui_policy_id)
        )
        if not ui_policy_result.get("success"):
            return ConvertUIPolicyToDataPolicyResponse(
                success=False,
                message=ui_policy_result.get("message", "UI Policy not found"),
            )

        ui_policy = ui_policy_result["ui_policy"]
        warnings = _validate_ui_policy_conversion(ui_policy, strict=params.strict)
        blocking_warnings = [
            warning for warning in warnings if warning.startswith("Cannot convert")
        ]
        if blocking_warnings:
            return ConvertUIPolicyToDataPolicyResponse(
                success=False,
                message="UI Policy is not eligible for Data Policy conversion",
                ui_policy_id=ui_policy.get("sys_id"),
                warnings=warnings,
            )

        rule_inputs: List[DataPolicyRuleInput] = []
        for action in ui_policy.get("actions", []):
            mandatory = action.get("mandatory")
            read_only = action.get("readonly")
            if mandatory is None and read_only is None:
                warnings.append(
                    f"Skipped action for field {action.get('field')}: no mandatory/read-only state"
                )
                continue
            rule_inputs.append(
                DataPolicyRuleInput(
                    field=action["field"],
                    mandatory=mandatory,
                    read_only=read_only,
                )
            )

        if not rule_inputs:
            return ConvertUIPolicyToDataPolicyResponse(
                success=False,
                message="UI Policy has no convertible mandatory or read-only actions",
                ui_policy_id=ui_policy.get("sys_id"),
                warnings=warnings,
            )

        create_response = create_data_policy(
            config,
            auth_manager,
            CreateDataPolicyParams(
                short_description=(
                    params.short_description
                    or ui_policy.get("short_description")
                    or ui_policy.get("name")
                    or f"Converted from UI Policy {ui_policy.get('sys_id')}"
                ),
                table=ui_policy["table"],
                condition=ui_policy.get("conditions"),
                active=bool(ui_policy.get("active")),
                reverse_if_false=bool(ui_policy.get("reverse_if_false")),
                use_as_ui_policy=params.use_as_ui_policy,
                order=_coerce_int(ui_policy.get("order")),
                description=ui_policy.get("description"),
                rules=rule_inputs,
                attributes=params.attributes,
            ),
        )
        if not create_response.success or not create_response.data_policy_id:
            return ConvertUIPolicyToDataPolicyResponse(
                success=False,
                message=create_response.message,
                ui_policy_id=ui_policy.get("sys_id"),
                warnings=warnings,
            )

        source_warning = _apply_source_action(
            config, auth_manager, params.source_action, str(ui_policy.get("sys_id"))
        )
        if source_warning:
            warnings.append(source_warning)

        created_rules = _list_rules_for_policy(config, auth_manager, create_response.data_policy_id)
        return ConvertUIPolicyToDataPolicyResponse(
            success=True,
            message=(
                f"Converted UI Policy {ui_policy.get('name')} to Data Policy "
                f"{create_response.data_policy_name}"
            ),
            ui_policy_id=ui_policy.get("sys_id"),
            data_policy_id=create_response.data_policy_id,
            data_policy_name=create_response.data_policy_name,
            created_rules=created_rules,
            warnings=warnings,
        )
    except Exception as error:
        logger.error("Error converting UI Policy to Data Policy: %s", error)
        return ConvertUIPolicyToDataPolicyResponse(
            success=False,
            message=f"Error converting UI Policy to Data Policy: {_extract_error_body(error)}",
        )


def _validate_ui_policy_conversion(ui_policy: Dict[str, Any], *, strict: bool) -> List[str]:
    warnings: List[str] = []
    if ui_policy.get("run_scripts"):
        message = "Cannot convert scripted UI Policies; Data Policies do not support scripts"
        if strict:
            warnings.append(message)
        else:
            warnings.append("Scripts will be ignored during non-strict conversion")

    if not ui_policy.get("global"):
        message = "Cannot convert non-global or view-specific UI Policies"
        if strict:
            warnings.append(message)
        else:
            warnings.append("View/global settings will be ignored during non-strict conversion")

    for action in ui_policy.get("actions", []):
        if action.get("visible") is not None:
            message = (
                f"Cannot convert visibility action for field {action.get('field')}; "
                "Data Policies only support mandatory and read-only rules"
            )
            if strict:
                warnings.append(message)
            else:
                warnings.append(
                    f"Visibility behavior for field {action.get('field')} will be ignored"
                )
    return warnings


def _apply_source_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    source_action: Literal["keep", "deactivate", "delete"],
    ui_policy_id: str,
) -> Optional[str]:
    if source_action == "keep":
        return None
    if source_action == "deactivate":
        result = update_ui_policy(
            config,
            auth_manager,
            UpdateUIPolicyParams(ui_policy_id=ui_policy_id, active=False),
        )
        if not result.success:
            return f"Converted policy, but failed to deactivate source UI Policy: {result.message}"
        return None
    if source_action == "delete":
        result = delete_ui_policy(
            config, auth_manager, DeleteUIPolicyParams(ui_policy_id=ui_policy_id)
        )
        if not result.success:
            return f"Converted policy, but failed to delete source UI Policy: {result.message}"
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _delete_rules_for_policy(
    config: ServerConfig,
    auth_manager: AuthManager,
    data_policy_id: str,
) -> None:
    response = requests.get(
        f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}",
        params={
            "sysparm_query": f"sys_data_policy={data_policy_id}",
            "sysparm_fields": "sys_id",
        },
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    for item in response.json().get("result", []):
        if not isinstance(item, dict) or not item.get("sys_id"):
            continue
        delete_response = requests.delete(
            f"{config.instance_url}/api/now/table/{DATA_POLICY_RULE_TABLE}/{item['sys_id']}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        delete_response.raise_for_status()
