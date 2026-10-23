"""
Business Rule tools for the ServiceNow MCP server.

This module provides CRUD-style helpers for ServiceNow business rules and the
supporting normalization logic used to render rule records consistently. The
tools are aimed at administrators who need to inspect, create, update, or
remove business rules while preserving a stable API surface for the MCP layer.

The module also includes small coercion helpers for ServiceNow-specific data
shapes such as reference fields and boolean values encoded as strings.
"""

import logging
import re
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
BUSINESS_RULE_WHEN_VALUES = {"before", "after", "async", "display"}


def _extract_display_value(value: Any) -> Optional[str]:
    """Return a stable display value from ServiceNow reference fields."""
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    if value is None:
        return None
    return str(value)


def _coerce_servicenow_bool(value: Any) -> bool:
    """Normalize ServiceNow booleans that may arrive as strings or bools."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _normalize_business_rule(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw ServiceNow business rule record."""
    table_value = item.get("collection")
    if table_value in (None, ""):
        table_value = item.get("table")

    return {
        "sys_id": item.get("sys_id"),
        "name": item.get("name"),
        "table": table_value,
        "active": _coerce_servicenow_bool(item.get("active")),
        "when": item.get("when"),
        "order": item.get("order"),
        "condition": item.get("condition"),
        "filter_condition": item.get("filter_condition"),
        "role_conditions": item.get("role_conditions"),
        "script": item.get("script"),
        "description": item.get("description"),
        "advanced": _coerce_servicenow_bool(item.get("advanced")),
        "insert": _coerce_servicenow_bool(item.get("action_insert", item.get("insert"))),
        "update": _coerce_servicenow_bool(item.get("action_update", item.get("update"))),
        "delete": _coerce_servicenow_bool(item.get("action_delete", item.get("delete"))),
        "display": _coerce_servicenow_bool(item.get("action_display", item.get("display"))),
        "query": _coerce_servicenow_bool(item.get("action_query", item.get("query"))),
        "abort_action": _coerce_servicenow_bool(item.get("abort_action")),
        "use_transaction": _coerce_servicenow_bool(item.get("use_transaction")),
        "created_on": item.get("sys_created_on"),
        "updated_on": item.get("sys_updated_on"),
        "created_by": _extract_display_value(item.get("sys_created_by")),
        "updated_by": _extract_display_value(item.get("sys_updated_by")),
        "fields": item,
    }


def _extract_sys_id(identifier: str) -> Optional[str]:
    """Return a ServiceNow sys_id from either raw or prefixed input."""
    cleaned = identifier.strip()
    if cleaned.startswith("sys_id:"):
        candidate = cleaned.replace("sys_id:", "", 1).strip()
        return candidate or None
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


def _select_business_rule_match(results: Any, business_rule_id: str) -> Optional[Dict[str, Any]]:
    """Select the best matching business rule from a ServiceNow response."""
    if isinstance(results, dict):
        return results
    if not isinstance(results, list):
        return None

    normalized_identifier = business_rule_id.strip().lower()
    fallback_match: Optional[Dict[str, Any]] = None

    for item in results:
        if not isinstance(item, dict):
            continue
        if fallback_match is None:
            fallback_match = item
        name = str(item.get("name") or "").strip().lower()
        if name == normalized_identifier:
            return item

    return fallback_match


class ListBusinessRulesParams(BaseModel):
    """Parameters for listing business rules."""

    limit: int = Field(10, description="Maximum number of business rules to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    table: Optional[str] = Field(None, description="Filter by table name")
    query: Optional[str] = Field(None, description="Search query for business rule names")


class GetBusinessRuleParams(BaseModel):
    """Parameters for getting a business rule."""

    business_rule_id: str = Field(..., description="Business rule sys_id or name")


class CreateBusinessRuleParams(BaseModel):
    """Parameters for creating a business rule."""

    name: str = Field(..., description="Name of the business rule")
    table: str = Field(..., description="Table the business rule applies to")
    script: Optional[str] = Field(None, description="Script content for the business rule")
    condition: Optional[str] = Field(None, description="Condition for the business rule")
    filter_condition: Optional[str] = Field(
        None, description="Condition builder encoded/filter condition"
    )
    role_conditions: Optional[str] = Field(None, description="Roles required to run the rule")
    when: Optional[str] = Field(
        None, description="When the business rule runs (before, after, async, display)"
    )
    order: Optional[int] = Field(None, description="Order of the business rule")
    active: bool = Field(True, description="Whether the business rule is active")
    advanced: Optional[bool] = Field(
        None, description="Advanced mode (needed for the script to run); defaults to true when a script is given"
    )
    insert: bool = Field(False, description="Whether the business rule runs on insert")
    update: bool = Field(False, description="Whether the business rule runs on update")
    delete: bool = Field(False, description="Whether the business rule runs on delete")
    display: bool = Field(False, description="Whether the business rule runs on display")
    query: bool = Field(False, description="Whether the business rule runs on query")
    abort_action: Optional[bool] = Field(None, description="Abort the database action")
    use_transaction: bool = Field(False, description="Whether the business rule uses a transaction")
    description: Optional[str] = Field(None, description="Description of the business rule")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_script field to set, including scoped/custom fields",
    )


class UpdateBusinessRuleParams(BaseModel):
    """Parameters for updating a business rule."""

    business_rule_id: str = Field(..., description="Business rule sys_id or name")
    name: Optional[str] = Field(None, description="Name of the business rule")
    table: Optional[str] = Field(None, description="Table the business rule applies to")
    script: Optional[str] = Field(None, description="Script content for the business rule")
    condition: Optional[str] = Field(None, description="Condition for the business rule")
    filter_condition: Optional[str] = Field(
        None, description="Condition builder encoded/filter condition"
    )
    role_conditions: Optional[str] = Field(None, description="Roles required to run the rule")
    when: Optional[str] = Field(
        None, description="When the business rule runs (before, after, async, display)"
    )
    order: Optional[int] = Field(None, description="Order of the business rule")
    active: Optional[bool] = Field(None, description="Whether the business rule is active")
    advanced: Optional[bool] = Field(
        None, description="Whether the business rule uses advanced mode"
    )
    insert: Optional[bool] = Field(None, description="Whether the business rule runs on insert")
    update: Optional[bool] = Field(None, description="Whether the business rule runs on update")
    delete: Optional[bool] = Field(None, description="Whether the business rule runs on delete")
    display: Optional[bool] = Field(None, description="Whether the business rule runs on display")
    query: Optional[bool] = Field(None, description="Whether the business rule runs on query")
    abort_action: Optional[bool] = Field(None, description="Abort the database action")
    use_transaction: Optional[bool] = Field(
        None, description="Whether the business rule uses a transaction"
    )
    description: Optional[str] = Field(None, description="Description of the business rule")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_script field to update, including scoped/custom fields",
    )


class DeleteBusinessRuleParams(BaseModel):
    """Parameters for deleting a business rule."""

    business_rule_id: str = Field(..., description="Business rule sys_id or name")


class BusinessRuleResponse(BaseModel):
    """Response from business rule operations."""

    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result")
    business_rule_id: Optional[str] = Field(None, description="ID of the affected business rule")
    business_rule_name: Optional[str] = Field(
        None, description="Name of the affected business rule"
    )


def _build_business_rule_fields() -> str:
    return (
        "sys_id,name,collection,table,active,when,order,condition,script,description,advanced,"
        "action_insert,action_update,action_delete,action_display,action_query,"
        "insert,update,delete,display,query,filter_condition,role_conditions,abort_action,"
        "use_transaction,sys_created_on,sys_updated_on,sys_scope,sys_package,"
        "sys_created_by,sys_updated_by"
    )


def list_business_rules(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListBusinessRulesParams,
) -> Dict[str, Any]:
    """List business rules from ServiceNow."""
    try:
        url = f"{config.instance_url}/api/now/table/sys_script"
        query_params = {
            "sysparm_limit": params.limit,
            "sysparm_offset": params.offset,
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_business_rule_fields(),
        }

        query_parts = []
        if params.active is not None:
            query_parts.append(f"active={str(params.active).lower()}")
        if params.table:
            query_parts.append(f"collection={params.table}")
        if params.query:
            query_parts.append(f"nameLIKE{params.query}")

        query_params["sysparm_query"] = "^".join(query_parts)

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()
        business_rules = []
        for item in data.get("result", []):
            if not isinstance(item, dict):
                logger.warning("Skipping unexpected business rule list item: %r", item)
                continue
            business_rules.append(_normalize_business_rule(item))

        return {
            "success": True,
            "message": f"Found {len(business_rules)} business rules",
            "business_rules": business_rules,
            "total": len(business_rules),
            "limit": params.limit,
            "offset": params.offset,
        }

    except Exception as e:
        logger.error(f"Error listing business rules: {e}")
        return {
            "success": False,
            "message": f"Error listing business rules: {str(e)}",
            "business_rules": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_business_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetBusinessRuleParams,
) -> Dict[str, Any]:
    """Get a specific business rule from ServiceNow."""
    try:
        query_params = {
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _build_business_rule_fields(),
        }

        sys_id = _extract_sys_id(params.business_rule_id)
        if sys_id:
            url = f"{config.instance_url}/api/now/table/sys_script/{sys_id}"
            query_params.pop("sysparm_fields", None)
        else:
            url = f"{config.instance_url}/api/now/table/sys_script"
            query_params["sysparm_query"] = f"name={params.business_rule_id}"

        headers = auth_manager.get_headers()
        response = requests.get(url, params=query_params, headers=headers, timeout=config.timeout)
        response.raise_for_status()

        data = response.json()
        if "result" not in data:
            return {
                "success": False,
                "message": f"Business rule not found: {params.business_rule_id}",
            }

        result = data["result"]
        item = _select_business_rule_match(result, params.business_rule_id)

        if item is None and not sys_id:
            fallback_query_params = dict(query_params)
            fallback_query_params["sysparm_query"] = f"nameLIKE{params.business_rule_id}"
            fallback_query_params["sysparm_limit"] = 10
            response = requests.get(
                url, params=fallback_query_params, headers=headers, timeout=config.timeout
            )
            response.raise_for_status()
            fallback_data = response.json()
            item = _select_business_rule_match(fallback_data.get("result"), params.business_rule_id)

        if item is None or not isinstance(item, dict):
            return {
                "success": False,
                "message": f"Business rule not found: {params.business_rule_id}",
            }

        business_rule = _normalize_business_rule(item)

        return {
            "success": True,
            "message": f"Found business rule: {item.get('name')}",
            "business_rule": business_rule,
        }

    except Exception as e:
        logger.error(f"Error getting business rule: {e}")
        return {
            "success": False,
            "message": f"Error getting business rule: {str(e)}",
        }


def create_business_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateBusinessRuleParams,
) -> BusinessRuleResponse:
    """Create a new business rule in ServiceNow."""
    url = f"{config.instance_url}/api/now/table/sys_script"
    when = params.when or ("display" if params.display else None)
    if when is not None and when not in BUSINESS_RULE_WHEN_VALUES:
        return BusinessRuleResponse(
            success=False,
            message=f"Invalid 'when' value '{when}'. Use one of: {sorted(BUSINESS_RULE_WHEN_VALUES)}",
        )
    if when != "display" and not (params.insert or params.update or params.delete or params.query):
        return BusinessRuleResponse(
            success=False,
            message="Set at least one of insert, update, delete or query (or when='display'), "
                    "otherwise the rule never runs. Nothing was created.",
        )
    # Without advanced=true ServiceNow ignores the script field
    advanced = params.advanced if params.advanced is not None else bool(params.script)
    body: Dict[str, Any] = dict(params.fields or {})
    body.update(
        {
            "name": params.name,
            "collection": params.table,
            "active": str(params.active).lower(),
            "advanced": str(advanced).lower(),
            "action_insert": str(params.insert).lower(),
            "action_update": str(params.update).lower(),
            "action_delete": str(params.delete).lower(),
            "action_query": str(params.query).lower(),
        }
    )
    if params.use_transaction:
        body["use_transaction"] = "true"
    if when is not None:
        body["when"] = when

    if params.script is not None:
        body["script"] = params.script
    if params.condition is not None:
        body["condition"] = params.condition
    if params.filter_condition is not None:
        body["filter_condition"] = params.filter_condition
    if params.role_conditions is not None:
        body["role_conditions"] = params.role_conditions
    if params.when is not None:
        body["when"] = params.when
    if params.order is not None:
        body["order"] = params.order
    if params.description is not None:
        body["description"] = params.description
    if params.abort_action is not None:
        body["abort_action"] = str(params.abort_action).lower()

    headers = auth_manager.get_headers()
    try:
        response = requests.post(url, json=body, headers=headers, timeout=config.timeout)
        response.raise_for_status()
        data = response.json()
        if "result" not in data:
            return BusinessRuleResponse(
                success=False,
                message="Failed to create business rule",
            )

        result = data["result"]
        return BusinessRuleResponse(
            success=True,
            message=f"Created business rule: {result.get('name')}",
            business_rule_id=result.get("sys_id"),
            business_rule_name=result.get("name"),
        )
    except Exception as e:
        logger.error(f"Error creating business rule: {e}")
        return BusinessRuleResponse(
            success=False,
            message=f"Error creating business rule: {str(e)}",
        )


def update_business_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateBusinessRuleParams,
) -> BusinessRuleResponse:
    """Update an existing business rule in ServiceNow."""
    get_params = GetBusinessRuleParams(business_rule_id=params.business_rule_id)
    get_result = get_business_rule(config, auth_manager, get_params)

    if not get_result.get("success"):
        return BusinessRuleResponse(
            success=False, message=get_result.get("message", "Failed to find business rule")
        )

    business_rule = get_result["business_rule"]
    sys_id = business_rule["sys_id"]
    url = f"{config.instance_url}/api/now/table/sys_script/{sys_id}"
    body: Dict[str, Any] = dict(params.fields or {})

    if params.name is not None:
        body["name"] = params.name
    if params.table is not None:
        body["collection"] = params.table
    if params.script is not None:
        body["script"] = params.script
    if params.condition is not None:
        body["condition"] = params.condition
    if params.filter_condition is not None:
        body["filter_condition"] = params.filter_condition
    if params.role_conditions is not None:
        body["role_conditions"] = params.role_conditions
    if params.when is not None:
        if params.when not in BUSINESS_RULE_WHEN_VALUES:
            return BusinessRuleResponse(
                success=False,
                message=f"Invalid 'when' value '{params.when}'. Use one of: {sorted(BUSINESS_RULE_WHEN_VALUES)}",
            )
        body["when"] = params.when
    elif params.display:
        body["when"] = "display"   # a display rule is when=display (sys_script has no action_display column)
    if params.order is not None:
        body["order"] = params.order
    if params.active is not None:
        body["active"] = str(params.active).lower()
    if params.advanced is not None:
        body["advanced"] = str(params.advanced).lower()
    elif params.script is not None:
        body["advanced"] = "true"  # a new script only runs in advanced mode
    if params.insert is not None:
        body["action_insert"] = str(params.insert).lower()
    if params.update is not None:
        body["action_update"] = str(params.update).lower()
    if params.delete is not None:
        body["action_delete"] = str(params.delete).lower()
    if params.query is not None:
        body["action_query"] = str(params.query).lower()
    if params.abort_action is not None:
        body["abort_action"] = str(params.abort_action).lower()
    if params.use_transaction is not None:
        body["use_transaction"] = str(params.use_transaction).lower()
    if params.description is not None:
        body["description"] = params.description

    if not body:
        return BusinessRuleResponse(
            success=True,
            message=f"No changes to update for business rule: {business_rule['name']}",
            business_rule_id=sys_id,
            business_rule_name=business_rule["name"],
        )

    headers = auth_manager.get_headers()
    try:
        response = requests.patch(url, json=body, headers=headers, timeout=config.timeout)
        response.raise_for_status()
        data = response.json()
        if "result" not in data:
            return BusinessRuleResponse(
                success=False,
                message=f"Failed to update business rule: {business_rule['name']}",
            )

        result = data["result"]
        return BusinessRuleResponse(
            success=True,
            message=f"Updated business rule: {result.get('name')}",
            business_rule_id=result.get("sys_id"),
            business_rule_name=result.get("name"),
        )
    except Exception as e:
        logger.error(f"Error updating business rule: {e}")
        return BusinessRuleResponse(
            success=False,
            message=f"Error updating business rule: {str(e)}",
        )


def delete_business_rule(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteBusinessRuleParams,
) -> BusinessRuleResponse:
    """Delete a business rule from ServiceNow."""
    get_params = GetBusinessRuleParams(business_rule_id=params.business_rule_id)
    get_result = get_business_rule(config, auth_manager, get_params)

    if not get_result.get("success"):
        return BusinessRuleResponse(
            success=False, message=get_result.get("message", "Failed to find business rule")
        )

    business_rule = get_result["business_rule"]
    sys_id = business_rule["sys_id"]
    url = f"{config.instance_url}/api/now/table/sys_script/{sys_id}"
    headers = auth_manager.get_headers()

    try:
        response = requests.delete(url, headers=headers, timeout=config.timeout)
        response.raise_for_status()
        return BusinessRuleResponse(
            success=True,
            message=f"Deleted business rule: {business_rule['name']}",
            business_rule_id=sys_id,
            business_rule_name=business_rule["name"],
        )
    except Exception as e:
        logger.error(f"Error deleting business rule: {e}")
        return BusinessRuleResponse(
            success=False,
            message=f"Error deleting business rule: {str(e)}",
        )
