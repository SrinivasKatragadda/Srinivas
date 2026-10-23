"""
Security administration tools for the ServiceNow MCP server.

This module manages ACLs, roles, role mappings, and best-effort ACL
validation/testing using documented ServiceNow security behavior plus the
standard security tables exposed through the Table API.

The tools here emphasize traceable, table-backed security administration. They
support security configuration inspection, role mapping relationships, and
lightweight validation helpers that make it easier to reason about access
control state from the MCP interface.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Literal, Optional, Set

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

ACL_TABLE = "sys_security_acl"
ACL_ROLE_TABLE = "sys_security_acl_role"
ROLE_TABLE = "sys_user_role"
USER_ROLE_TABLE = "sys_user_has_role"
GROUP_ROLE_TABLE = "sys_group_has_role"
ROLE_CONTAINS_TABLE = "sys_user_role_contains"
USER_TABLE = "sys_user"
GROUP_TABLE = "sys_user_group"
SECURITY_ATTRIBUTE_TABLE = "sys_security_attribute"

RoleMappingType = Literal["user_role", "group_role", "role_contains", "acl_role"]
IdentityType = Literal["user", "group", "role"]

ROLE_MAPPING_CONFIG: Dict[str, Dict[str, str]] = {
    "user_role": {
        "table": USER_ROLE_TABLE,
        "source_field": "user",
        "target_field": "role",
        "source_table": USER_TABLE,
        "source_query_field": "user_name",
    },
    "group_role": {
        "table": GROUP_ROLE_TABLE,
        "source_field": "group",
        "target_field": "role",
        "source_table": GROUP_TABLE,
        "source_query_field": "name",
    },
    "role_contains": {
        "table": ROLE_CONTAINS_TABLE,
        "source_field": "role",
        "target_field": "contains",
        "source_table": ROLE_TABLE,
        "source_query_field": "name",
    },
    "acl_role": {
        "table": ACL_ROLE_TABLE,
        "source_field": "sys_security_acl",
        "target_field": "sys_user_role",
        "source_table": ACL_TABLE,
        "source_query_field": "name",
    },
}

ACL_FIELDS = (
    "sys_id,name,type,operation,description,active,advanced,admin_overrides,"
    "decision_type,applies_to,condition,script,sys_created_on,sys_updated_on"
)
ROLE_FIELDS = "sys_id,name,description,sys_created_on,sys_updated_on"


class ListACLsParams(BaseModel):
    """Parameters for listing ACLs."""

    limit: int = Field(10, description="Maximum number of ACLs to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(None, description="Filter by active status")
    acl_type: Optional[str] = Field(None, description="ACL type, such as record or REST_Endpoint")
    operation: Optional[str] = Field(None, description="ACL operation such as read, write, create")
    table: Optional[str] = Field(None, description="Filter by secured table name")
    field: Optional[str] = Field(None, description="Filter by secured field name")
    query: Optional[str] = Field(
        None,
        description="Case-insensitive search term that matches ACL name or description",
    )


class GetACLParams(BaseModel):
    """Parameters for getting a specific ACL."""

    acl_id: str = Field(..., description="ACL sys_id or ACL name")
    operation: Optional[str] = Field(
        None, description="Optional operation to disambiguate ACL names"
    )
    acl_type: Optional[str] = Field(None, description="Optional ACL type to disambiguate ACL names")


class CreateACLParams(BaseModel):
    """Parameters for creating an ACL."""

    name: str = Field(
        ...,
        description=(
            "Secured object name, for example incident, incident.short_description, or *.number"
        ),
    )
    operation: str = Field(..., description="ACL operation such as read, write, create, or delete")
    acl_type: str = Field("record", description="ACL type such as record or REST_Endpoint")
    description: Optional[str] = Field(None, description="Description of the ACL")
    decision_type: Optional[str] = Field(
        None,
        description="Decision type, typically allow_if or deny_unless",
    )
    admin_overrides: bool = Field(
        True, description="Whether users with admin automatically pass the ACL"
    )
    active: bool = Field(True, description="Whether the ACL is active")
    advanced: bool = Field(
        False, description="Whether to enable advanced ACL fields such as script logic"
    )
    applies_to: Optional[str] = Field(
        None,
        description="Encoded query filter that restricts which records the ACL applies to",
    )
    data_condition: Optional[str] = Field(
        None,
        description="Encoded query condition that must evaluate true for access",
    )
    script: Optional[str] = Field(None, description="ACL script that returns true or false")
    requires_roles: Optional[List[str]] = Field(
        None,
        description=(
            "Roles required by the ACL. Uses role names or sys_ids and replaces the ACL role list."
        ),
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Additional sys_security_acl fields to send directly to ServiceNow, such as "
            "scope-specific security constraint fields."
        ),
    )


class UpdateACLParams(BaseModel):
    """Parameters for updating an ACL."""

    acl_id: str = Field(..., description="ACL sys_id or ACL name")
    operation: Optional[str] = Field(
        None, description="Optional operation to disambiguate ACL names"
    )
    acl_type: Optional[str] = Field(None, description="Optional ACL type to disambiguate ACL names")
    name: Optional[str] = Field(None, description="Updated secured object name")
    new_operation: Optional[str] = Field(None, description="Updated ACL operation")
    new_acl_type: Optional[str] = Field(None, description="Updated ACL type")
    description: Optional[str] = Field(None, description="Updated ACL description")
    decision_type: Optional[str] = Field(
        None,
        description="Updated decision type, typically allow_if or deny_unless",
    )
    admin_overrides: Optional[bool] = Field(None, description="Updated admin override flag")
    active: Optional[bool] = Field(None, description="Updated active flag")
    advanced: Optional[bool] = Field(None, description="Updated advanced flag")
    applies_to: Optional[str] = Field(
        None,
        description="Updated encoded query filter limiting which records the ACL applies to",
    )
    data_condition: Optional[str] = Field(
        None,
        description="Updated encoded query condition that must evaluate true for access",
    )
    script: Optional[str] = Field(None, description="Updated ACL script")
    requires_roles: Optional[List[str]] = Field(
        None,
        description="Replacement ACL role list. Uses role names or sys_ids.",
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional sys_security_acl fields to patch directly in ServiceNow",
    )


class TestACLAccessParams(BaseModel):
    """Parameters for testing a single ACL against a user, group, or role."""

    acl_id: str = Field(..., description="ACL sys_id or ACL name")
    analyze_as: IdentityType = Field(
        ...,
        description="Analyze the ACL as a user, group, or role",
    )
    identity: str = Field(
        ...,
        description="User name/sys_id, group name/sys_id, or role name/sys_id",
    )
    operation: Optional[str] = Field(
        None, description="Optional operation to disambiguate ACL names"
    )
    acl_type: Optional[str] = Field(None, description="Optional ACL type to disambiguate ACL names")
    record_id: Optional[str] = Field(
        None,
        description=(
            "Record sys_id used to evaluate applies_to or data_condition filters when possible"
        ),
    )
    record_table: Optional[str] = Field(
        None,
        description="Override table name for wildcard ACLs when record_id is supplied",
    )


class ValidateACLParams(BaseModel):
    """Parameters for validating an ACL."""

    acl_id: str = Field(..., description="ACL sys_id or ACL name")
    operation: Optional[str] = Field(
        None, description="Optional operation to disambiguate ACL names"
    )
    acl_type: Optional[str] = Field(None, description="Optional ACL type to disambiguate ACL names")


class ListRolesParams(BaseModel):
    """Parameters for listing roles."""

    limit: int = Field(10, description="Maximum number of roles to return")
    offset: int = Field(0, description="Offset for pagination")
    query: Optional[str] = Field(
        None,
        description="Case-insensitive search term that matches role name or description",
    )


class GetRoleParams(BaseModel):
    """Parameters for getting a specific role."""

    role_id: str = Field(..., description="Role sys_id or role name")


class CreateRoleParams(BaseModel):
    """Parameters for creating a role."""

    name: str = Field(..., description="Role name")
    description: Optional[str] = Field(None, description="Role description")
    contains_roles: Optional[List[str]] = Field(
        None,
        description=(
            "Roles this role should contain. Uses role names or sys_ids and replaces containment "
            "mappings."
        ),
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional sys_user_role fields to send directly to ServiceNow",
    )


class UpdateRoleParams(BaseModel):
    """Parameters for updating a role."""

    role_id: str = Field(..., description="Role sys_id or role name")
    name: Optional[str] = Field(None, description="Updated role name")
    description: Optional[str] = Field(None, description="Updated role description")
    contains_roles: Optional[List[str]] = Field(
        None,
        description="Replacement contained roles for the role. Uses role names or sys_ids.",
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional sys_user_role fields to patch directly in ServiceNow",
    )


class ValidateRoleParams(BaseModel):
    """Parameters for validating a role."""

    role_id: str = Field(..., description="Role sys_id or role name")


class ListRoleMappingsParams(BaseModel):
    """Parameters for listing role mappings."""

    mapping_type: RoleMappingType = Field(
        ...,
        description="Mapping type: user_role, group_role, role_contains, or acl_role",
    )
    limit: int = Field(20, description="Maximum number of mappings to return")
    offset: int = Field(0, description="Offset for pagination")
    source: Optional[str] = Field(
        None,
        description=(
            "Optional source identifier. Use a user, group, role, or ACL name/sys_id depending on "
            "mapping type."
        ),
    )
    target_role: Optional[str] = Field(
        None, description="Optional target role identifier to filter on"
    )
    acl_operation: Optional[str] = Field(
        None,
        description="Optional ACL operation used when source is an ACL name for acl_role mappings",
    )
    acl_type: Optional[str] = Field(
        None,
        description="Optional ACL type used when source is an ACL name for acl_role mappings",
    )


class CreateRoleMappingParams(BaseModel):
    """Parameters for creating a role mapping."""

    mapping_type: RoleMappingType = Field(
        ...,
        description="Mapping type: user_role, group_role, role_contains, or acl_role",
    )
    source: str = Field(
        ...,
        description=(
            "Source identifier. Use a user, group, role, or ACL name/sys_id depending on mapping "
            "type."
        ),
    )
    target_role: str = Field(..., description="Target role name or sys_id")
    acl_operation: Optional[str] = Field(
        None,
        description="Optional ACL operation used when source is an ACL name for acl_role mappings",
    )
    acl_type: Optional[str] = Field(
        None,
        description="Optional ACL type used when source is an ACL name for acl_role mappings",
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional mapping-table fields to send directly to ServiceNow",
    )


class UpdateRoleMappingParams(BaseModel):
    """Parameters for updating a role mapping."""

    mapping_type: RoleMappingType = Field(
        ...,
        description="Mapping type: user_role, group_role, role_contains, or acl_role",
    )
    mapping_id: str = Field(..., description="Role mapping sys_id")
    source: Optional[str] = Field(
        None,
        description=(
            "Updated source identifier. Use a user, group, role, or ACL name/sys_id depending on "
            "mapping type."
        ),
    )
    target_role: Optional[str] = Field(None, description="Updated target role name or sys_id")
    acl_operation: Optional[str] = Field(
        None,
        description="Optional ACL operation used when source is an ACL name for acl_role mappings",
    )
    acl_type: Optional[str] = Field(
        None,
        description="Optional ACL type used when source is an ACL name for acl_role mappings",
    )
    extra_fields: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional mapping-table fields to patch directly in ServiceNow",
    )


class ValidateRoleMappingParams(BaseModel):
    """Parameters for validating a role mapping."""

    mapping_type: RoleMappingType = Field(
        ...,
        description="Mapping type: user_role, group_role, role_contains, or acl_role",
    )
    mapping_id: Optional[str] = Field(None, description="Existing role mapping sys_id")
    source: Optional[str] = Field(
        None,
        description="Source identifier when validating a not-yet-created mapping",
    )
    target_role: Optional[str] = Field(
        None,
        description="Target role identifier when validating a not-yet-created mapping",
    )
    acl_operation: Optional[str] = Field(
        None,
        description="Optional ACL operation used when source is an ACL name for acl_role mappings",
    )
    acl_type: Optional[str] = Field(
        None,
        description="Optional ACL type used when source is an ACL name for acl_role mappings",
    )


class ValidateSecurityConstraintsParams(BaseModel):
    """Parameters for validating the ACLs that secure an object."""

    table: str = Field(..., description="Secured table name")
    operation: str = Field(..., description="ACL operation to inspect")
    field: Optional[str] = Field(None, description="Optional field name on the table")
    acl_type: str = Field("record", description="ACL type such as record")


def _extract_sys_id(identifier: str) -> Optional[str]:
    cleaned = identifier.strip()
    if cleaned.startswith("sys_id:"):
        candidate = cleaned.replace("sys_id:", "", 1).strip()
        return candidate or None
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


def _coerce_servicenow_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _extract_display_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    if value in (None, ""):
        return None
    return str(value)


def _table_url(config: ServerConfig, table: str, record_id: Optional[str] = None) -> str:
    base = f"{config.api_url}/table/{table}"
    if record_id:
        return f"{base}/{record_id}"
    return base


def _request_json(
    method: str,
    url: str,
    auth_manager: AuthManager,
    timeout: int,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    response = requests.request(
        method,
        url,
        params=params,
        json=json_body,
        headers=auth_manager.get_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("result")


def _format_permission_error(exc: requests.RequestException, action: str) -> str:
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != 403:
        return f"Failed to {action}: {exc}"

    return (
        f"Failed to {action}: ServiceNow returned 403 Forbidden. "
        "The MCP request is authenticated separately from your browser UI session, so an elevated "
        "security_admin role in the browser does not automatically apply here. Verify the API "
        "credentials being used by MCP, and if OAuth is configured make sure the request is "
        "running "
        "as the intended user rather than only as an OAuth client."
    )


def _delete_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    record_id: str,
) -> None:
    response = requests.delete(
        _table_url(config, table, record_id),
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()


def _query_table(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    *,
    query: Optional[str] = None,
    fields: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    display_value: str = "false",
    exclude_reference_link: bool = True,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "sysparm_display_value": display_value,
        "sysparm_exclude_reference_link": str(exclude_reference_link).lower(),
    }
    if query:
        params["sysparm_query"] = query
    if fields:
        params["sysparm_fields"] = fields
    if limit is not None:
        params["sysparm_limit"] = str(limit)
    if offset is not None:
        params["sysparm_offset"] = str(offset)

    result = _request_json(
        "GET",
        _table_url(config, table),
        auth_manager,
        config.timeout,
        params=params,
    )
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _get_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    record_id: str,
    *,
    fields: Optional[str] = None,
    display_value: str = "false",
) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "sysparm_display_value": display_value,
        "sysparm_exclude_reference_link": "true",
    }
    if fields:
        params["sysparm_fields"] = fields

    try:
        result = _request_json(
            "GET",
            _table_url(config, table, record_id),
            auth_manager,
            config.timeout,
            params=params,
        )
    except requests.RequestException:
        return None

    if isinstance(result, dict):
        return result
    return None


def _resolve_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    *,
    query_field: str,
    fields: Optional[str] = None,
    extra_query_parts: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    sys_id = _extract_sys_id(identifier)
    if sys_id:
        return _get_record(
            config,
            auth_manager,
            table,
            sys_id,
            fields=fields,
            display_value="false",
        )

    query_parts = list(extra_query_parts or [])
    query_parts.append(f"{query_field}={identifier}")
    results = _query_table(
        config,
        auth_manager,
        table,
        query="^".join(query_parts),
        fields=fields,
        limit=5,
        display_value="false",
    )
    if results:
        return results[0]

    query_parts = list(extra_query_parts or [])
    query_parts.append(f"{query_field}LIKE{identifier}")
    results = _query_table(
        config,
        auth_manager,
        table,
        query="^".join(query_parts),
        fields=fields,
        limit=5,
        display_value="false",
    )
    if results:
        return results[0]

    return None


def _resolve_role(
    config: ServerConfig,
    auth_manager: AuthManager,
    role_id: str,
) -> Optional[Dict[str, Any]]:
    return _resolve_record(
        config,
        auth_manager,
        ROLE_TABLE,
        role_id,
        query_field="name",
        fields=ROLE_FIELDS,
    )


def _resolve_user(
    config: ServerConfig,
    auth_manager: AuthManager,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    return _resolve_record(
        config,
        auth_manager,
        USER_TABLE,
        user_id,
        query_field="user_name",
        fields="sys_id,user_name,name",
    )


def _resolve_group(
    config: ServerConfig,
    auth_manager: AuthManager,
    group_id: str,
) -> Optional[Dict[str, Any]]:
    return _resolve_record(
        config,
        auth_manager,
        GROUP_TABLE,
        group_id,
        query_field="name",
        fields="sys_id,name",
    )


def _resolve_acl(
    config: ServerConfig,
    auth_manager: AuthManager,
    acl_id: str,
    *,
    operation: Optional[str] = None,
    acl_type: Optional[str] = None,
    fields: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    extra_query_parts: List[str] = []
    if operation:
        extra_query_parts.append(f"operation={operation}")
    if acl_type:
        extra_query_parts.append(f"type={acl_type}")

    return _resolve_record(
        config,
        auth_manager,
        ACL_TABLE,
        acl_id,
        query_field="name",
        fields=fields,
        extra_query_parts=extra_query_parts,
    )


def _resolve_source_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    mapping_type: RoleMappingType,
    source: str,
    *,
    acl_operation: Optional[str] = None,
    acl_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if mapping_type == "user_role":
        return _resolve_user(config, auth_manager, source)
    if mapping_type == "group_role":
        return _resolve_group(config, auth_manager, source)
    if mapping_type == "role_contains":
        return _resolve_role(config, auth_manager, source)
    return _resolve_acl(
        config,
        auth_manager,
        source,
        operation=acl_operation,
        acl_type=acl_type,
        fields=ACL_FIELDS,
    )


def _normalize_decision_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    mapping = {
        "allow": "allow_if",
        "allow_if": "allow_if",
        "allow if": "allow_if",
        "deny": "deny_unless",
        "deny_unless": "deny_unless",
        "deny unless": "deny_unless",
    }
    return mapping.get(normalized, value)


def _parse_acl_name(name: Optional[str]) -> Dict[str, Optional[str]]:
    object_name = (name or "").strip()
    if not object_name:
        return {"table": None, "field": None}
    if "." not in object_name:
        return {"table": object_name, "field": None}
    table_name, field_name = object_name.split(".", 1)
    return {"table": table_name or None, "field": field_name or None}


def _get_role_names_by_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    role_ids: List[str],
) -> Dict[str, str]:
    unique_ids = [role_id for role_id in dict.fromkeys(role_ids) if role_id]
    if not unique_ids:
        return {}

    results = _query_table(
        config,
        auth_manager,
        ROLE_TABLE,
        query=f"sys_idIN{','.join(unique_ids)}",
        fields="sys_id,name",
        limit=len(unique_ids),
        display_value="false",
    )
    return {
        str(item.get("sys_id")): str(item.get("name")) for item in results if item.get("sys_id")
    }


def _get_acl_role_mappings(
    config: ServerConfig,
    auth_manager: AuthManager,
    acl_sys_id: str,
) -> List[Dict[str, Any]]:
    rows = _query_table(
        config,
        auth_manager,
        ACL_ROLE_TABLE,
        query=f"sys_security_acl={acl_sys_id}",
        fields="sys_id,sys_security_acl,sys_user_role",
        display_value="false",
    )
    role_names = _get_role_names_by_id(
        config,
        auth_manager,
        [str(row.get("sys_user_role")) for row in rows if row.get("sys_user_role")],
    )
    mappings: List[Dict[str, Any]] = []
    for row in rows:
        role_id = _extract_display_value(row.get("sys_user_role"))
        mappings.append(
            {
                "mapping_id": row.get("sys_id"),
                "role_id": role_id,
                "role_name": role_names.get(role_id or "", role_id or ""),
            }
        )
    return mappings


def _normalize_acl(
    item: Dict[str, Any],
    role_mappings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    parsed_name = _parse_acl_name(_extract_display_value(item.get("name")))
    roles = role_mappings or []
    return {
        "sys_id": item.get("sys_id"),
        "name": _extract_display_value(item.get("name")),
        "table": parsed_name["table"],
        "field": parsed_name["field"],
        "type": _extract_display_value(item.get("type")),
        "operation": _extract_display_value(item.get("operation")),
        "description": _extract_display_value(item.get("description")),
        "active": _coerce_servicenow_bool(item.get("active")),
        "advanced": _coerce_servicenow_bool(item.get("advanced")),
        "admin_overrides": _coerce_servicenow_bool(item.get("admin_overrides")),
        "decision_type": _extract_display_value(item.get("decision_type")),
        "applies_to": _extract_display_value(item.get("applies_to")),
        "data_condition": _extract_display_value(item.get("condition")),
        "script": _extract_display_value(item.get("script")),
        "requires_roles": [role["role_name"] for role in roles if role.get("role_name")],
        "requires_role_ids": [role["role_id"] for role in roles if role.get("role_id")],
        "sys_created_on": item.get("sys_created_on"),
        "sys_updated_on": item.get("sys_updated_on"),
    }


def _normalize_role(
    item: Dict[str, Any],
    *,
    contained_roles: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    contained = contained_roles or []
    return {
        "sys_id": item.get("sys_id"),
        "name": _extract_display_value(item.get("name")),
        "description": _extract_display_value(item.get("description")),
        "contains_roles": [role["role_name"] for role in contained if role.get("role_name")],
        "contains_role_ids": [role["role_id"] for role in contained if role.get("role_id")],
        "sys_created_on": item.get("sys_created_on"),
        "sys_updated_on": item.get("sys_updated_on"),
    }


def _resolve_role_ids(
    config: ServerConfig,
    auth_manager: AuthManager,
    identifiers: Optional[List[str]],
) -> Dict[str, Any]:
    desired_identifiers = identifiers or []
    resolved_ids: List[str] = []
    missing: List[str] = []
    resolved_records: List[Dict[str, Any]] = []

    for identifier in desired_identifiers:
        role_record = _resolve_role(config, auth_manager, identifier)
        if not role_record or not role_record.get("sys_id"):
            missing.append(identifier)
            continue
        resolved_ids.append(str(role_record["sys_id"]))
        resolved_records.append(role_record)

    return {
        "role_ids": list(dict.fromkeys(resolved_ids)),
        "missing": missing,
        "roles": resolved_records,
    }


def _sync_mapping_targets(
    config: ServerConfig,
    auth_manager: AuthManager,
    mapping_type: RoleMappingType,
    source_id: str,
    target_ids: List[str],
) -> Dict[str, Any]:
    mapping_config = ROLE_MAPPING_CONFIG[mapping_type]
    rows = _query_table(
        config,
        auth_manager,
        mapping_config["table"],
        query=f"{mapping_config['source_field']}={source_id}",
        fields=f"sys_id,{mapping_config['source_field']},{mapping_config['target_field']}",
        display_value="false",
    )

    existing_by_target = {
        str(row.get(mapping_config["target_field"])): str(row.get("sys_id"))
        for row in rows
        if row.get(mapping_config["target_field"]) and row.get("sys_id")
    }
    desired_ids = list(dict.fromkeys(target_ids))

    added: List[str] = []
    removed: List[str] = []

    for target_id in desired_ids:
        if target_id in existing_by_target:
            continue
        payload = {
            mapping_config["source_field"]: source_id,
            mapping_config["target_field"]: target_id,
        }
        result = _request_json(
            "POST",
            _table_url(config, mapping_config["table"]),
            auth_manager,
            config.timeout,
            json_body=payload,
        )
        if isinstance(result, dict) and result.get("sys_id"):
            added.append(str(result["sys_id"]))

    for existing_target_id, mapping_id in existing_by_target.items():
        if existing_target_id in desired_ids:
            continue
        _delete_record(config, auth_manager, mapping_config["table"], mapping_id)
        removed.append(mapping_id)

    return {
        "added_mapping_ids": added,
        "removed_mapping_ids": removed,
        "target_role_ids": desired_ids,
    }


def _get_contained_role_mappings(
    config: ServerConfig,
    auth_manager: AuthManager,
    role_sys_id: str,
) -> List[Dict[str, str]]:
    rows = _query_table(
        config,
        auth_manager,
        ROLE_CONTAINS_TABLE,
        query=f"role={role_sys_id}",
        fields="sys_id,role,contains",
        display_value="false",
    )
    role_names = _get_role_names_by_id(
        config,
        auth_manager,
        [str(row.get("contains")) for row in rows if row.get("contains")],
    )
    mappings: List[Dict[str, str]] = []
    for row in rows:
        contained_id = _extract_display_value(row.get("contains"))
        if not contained_id:
            continue
        mappings.append(
            {
                "mapping_id": str(row.get("sys_id")),
                "role_id": contained_id,
                "role_name": role_names.get(contained_id, contained_id),
            }
        )
    return mappings


def _expand_role_closure(
    config: ServerConfig,
    auth_manager: AuthManager,
    seed_role_ids: List[str],
) -> Set[str]:
    visited: Set[str] = set()
    stack: List[str] = [role_id for role_id in seed_role_ids if role_id]

    while stack:
        current_role_id = stack.pop()
        if current_role_id in visited:
            continue
        visited.add(current_role_id)
        rows = _query_table(
            config,
            auth_manager,
            ROLE_CONTAINS_TABLE,
            query=f"role={current_role_id}",
            fields="contains",
            display_value="false",
        )
        for row in rows:
            contained_id = _extract_display_value(row.get("contains"))
            if contained_id and contained_id not in visited:
                stack.append(contained_id)

    return visited


def _would_create_role_cycle(
    config: ServerConfig,
    auth_manager: AuthManager,
    parent_role_id: str,
    contained_role_id: str,
) -> bool:
    if parent_role_id == contained_role_id:
        return True
    reachable = _expand_role_closure(config, auth_manager, [contained_role_id])
    return parent_role_id in reachable


def _collect_identity_role_ids(
    config: ServerConfig,
    auth_manager: AuthManager,
    analyze_as: IdentityType,
    identity: str,
) -> Dict[str, Any]:
    if analyze_as == "role":
        role_record = _resolve_role(config, auth_manager, identity)
        if not role_record or not role_record.get("sys_id"):
            return {"success": False, "message": f"Role not found: {identity}", "role_ids": []}

        role_ids = _expand_role_closure(config, auth_manager, [str(role_record["sys_id"])])
        role_names = _get_role_names_by_id(config, auth_manager, list(role_ids))
        return {
            "success": True,
            "message": f"Resolved role closure for {role_record.get('name')}",
            "subject": {
                "type": "role",
                "sys_id": role_record.get("sys_id"),
                "name": role_record.get("name"),
            },
            "role_ids": sorted(role_ids),
            "role_names": [role_names.get(role_id, role_id) for role_id in sorted(role_ids)],
        }

    if analyze_as == "group":
        group_record = _resolve_group(config, auth_manager, identity)
        if not group_record or not group_record.get("sys_id"):
            return {"success": False, "message": f"Group not found: {identity}", "role_ids": []}

        rows = _query_table(
            config,
            auth_manager,
            GROUP_ROLE_TABLE,
            query=f"group={group_record['sys_id']}",
            fields="role",
            display_value="false",
        )
        direct_role_ids = [
            role_id
            for role_id in (_extract_display_value(row.get("role")) for row in rows)
            if role_id
        ]
        role_ids = _expand_role_closure(config, auth_manager, direct_role_ids)
        role_names = _get_role_names_by_id(config, auth_manager, list(role_ids))
        return {
            "success": True,
            "message": f"Resolved group roles for {group_record.get('name')}",
            "subject": {
                "type": "group",
                "sys_id": group_record.get("sys_id"),
                "name": group_record.get("name"),
            },
            "role_ids": sorted(role_ids),
            "role_names": [role_names.get(role_id, role_id) for role_id in sorted(role_ids)],
        }

    user_record = _resolve_user(config, auth_manager, identity)
    if not user_record or not user_record.get("sys_id"):
        return {"success": False, "message": f"User not found: {identity}", "role_ids": []}

    rows = _query_table(
        config,
        auth_manager,
        USER_ROLE_TABLE,
        query=f"user={user_record['sys_id']}",
        fields="role",
        display_value="false",
    )
    direct_role_ids = sorted(
        {
            role_id
            for role_id in (_extract_display_value(row.get("role")) for row in rows)
            if role_id
        }
    )
    role_ids = sorted(_expand_role_closure(config, auth_manager, direct_role_ids))
    role_names = _get_role_names_by_id(config, auth_manager, role_ids)
    return {
        "success": True,
        "message": f"Resolved user roles for {user_record.get('user_name')}",
        "subject": {
            "type": "user",
            "sys_id": user_record.get("sys_id"),
            "name": user_record.get("user_name"),
        },
        "role_ids": role_ids,
        "role_names": [role_names.get(role_id, role_id) for role_id in role_ids],
    }


def _normalize_script_for_validation(script: Optional[str]) -> str:
    return re.sub(r"\s+", "", (script or "")).lower()


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    return list(dict.fromkeys(item for item in items if item))


def _role_names_include(role_names: List[str], expected_name: str) -> bool:
    expected = expected_name.strip().lower()
    return any((role_name or "").strip().lower() == expected for role_name in role_names)


def _has_security_attribute_constraints(item: Dict[str, Any]) -> bool:
    for key, value in item.items():
        if "security_attribute" not in key:
            continue
        if _extract_display_value(value):
            return True
    return False


def _resolve_acl_table_name(
    acl_record: Dict[str, Any],
    record_table_override: Optional[str] = None,
) -> Optional[str]:
    parsed_name = _parse_acl_name(_extract_display_value(acl_record.get("name")))
    table_name = parsed_name["table"]
    if table_name and table_name != "*":
        return table_name
    if record_table_override:
        return record_table_override
    return None


def _evaluate_encoded_query(
    config: ServerConfig,
    auth_manager: AuthManager,
    table_name: str,
    record_id: str,
    encoded_query: str,
) -> str:
    query = encoded_query.strip()
    if not query:
        return "passed"

    combined_query = f"sys_id={record_id}^{query.lstrip('^')}"
    results = _query_table(
        config,
        auth_manager,
        table_name,
        query=combined_query,
        fields="sys_id",
        limit=1,
        display_value="false",
    )
    return "passed" if results else "failed"


def _build_acl_body(
    *,
    name: Optional[str] = None,
    operation: Optional[str] = None,
    acl_type: Optional[str] = None,
    description: Optional[str] = None,
    decision_type: Optional[str] = None,
    admin_overrides: Optional[bool] = None,
    active: Optional[bool] = None,
    advanced: Optional[bool] = None,
    applies_to: Optional[str] = None,
    data_condition: Optional[str] = None,
    script: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if operation is not None:
        body["operation"] = operation
    if acl_type is not None:
        body["type"] = acl_type
    if description is not None:
        body["description"] = description
    if decision_type is not None:
        body["decision_type"] = _normalize_decision_type(decision_type)
    if admin_overrides is not None:
        body["admin_overrides"] = str(admin_overrides).lower()
    if active is not None:
        body["active"] = str(active).lower()
    if advanced is not None:
        body["advanced"] = str(advanced).lower()
    if applies_to is not None:
        body["applies_to"] = applies_to
    if data_condition is not None:
        body["condition"] = data_condition
    if script is not None:
        body["script"] = script
    if extra_fields:
        body.update(extra_fields)
    return body


def list_acls(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListACLsParams,
) -> Dict[str, Any]:
    """List ACLs from ServiceNow."""
    query_parts: List[str] = []
    if params.active is not None:
        query_parts.append(f"active={str(params.active).lower()}")
    if params.acl_type:
        query_parts.append(f"type={params.acl_type}")
    if params.operation:
        query_parts.append(f"operation={params.operation}")
    if params.query:
        query_parts.append(f"nameLIKE{params.query}^ORdescriptionLIKE{params.query}")
    elif params.table:
        query_parts.append(f"nameLIKE{params.table}")

    try:
        records = _query_table(
            config,
            auth_manager,
            ACL_TABLE,
            query="^".join(query_parts) if query_parts else None,
            fields=ACL_FIELDS,
            limit=params.limit,
            offset=params.offset,
            display_value="false",
        )
        normalized: List[Dict[str, Any]] = []
        for record in records:
            acl_roles = _get_acl_role_mappings(config, auth_manager, str(record.get("sys_id")))
            acl = _normalize_acl(record, acl_roles)
            if params.table and acl.get("table") not in {params.table, "*"}:
                continue
            if params.field and acl.get("field") not in {params.field, "*"}:
                continue
            normalized.append(acl)

        return {
            "success": True,
            "message": f"Found {len(normalized)} ACLs",
            "acls": normalized,
            "count": len(normalized),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.RequestException as exc:
        logger.error("Failed to list ACLs: %s", exc)
        return {"success": False, "message": f"Failed to list ACLs: {exc}", "acls": []}


def get_acl(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetACLParams,
) -> Dict[str, Any]:
    """Get a specific ACL from ServiceNow."""
    try:
        record = _resolve_acl(
            config,
            auth_manager,
            params.acl_id,
            operation=params.operation,
            acl_type=params.acl_type,
            fields=ACL_FIELDS,
        )
        if not record or not record.get("sys_id"):
            return {"success": False, "message": f"ACL not found: {params.acl_id}"}

        acl_roles = _get_acl_role_mappings(config, auth_manager, str(record.get("sys_id")))
        return {
            "success": True,
            "message": f"Found ACL: {record.get('name')}",
            "acl": _normalize_acl(record, acl_roles),
        }
    except requests.RequestException as exc:
        logger.error("Failed to get ACL %s: %s", params.acl_id, exc)
        return {"success": False, "message": f"Failed to get ACL: {exc}"}


def create_acl(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateACLParams,
) -> Dict[str, Any]:
    """Create an ACL in ServiceNow."""
    try:
        role_resolution = _resolve_role_ids(config, auth_manager, params.requires_roles)
        if role_resolution["missing"]:
            missing = ", ".join(role_resolution["missing"])
            return {
                "success": False,
                "message": f"Cannot create ACL because these roles were not found: {missing}",
            }

        body = _build_acl_body(
            name=params.name,
            operation=params.operation,
            acl_type=params.acl_type,
            description=params.description,
            decision_type=params.decision_type,
            admin_overrides=params.admin_overrides,
            active=params.active,
            advanced=params.advanced,
            applies_to=params.applies_to,
            data_condition=params.data_condition,
            script=params.script,
            extra_fields=params.extra_fields,
        )
        result = _request_json(
            "POST",
            _table_url(config, ACL_TABLE),
            auth_manager,
            config.timeout,
            json_body=body,
        )
        if not isinstance(result, dict) or not result.get("sys_id"):
            return {"success": False, "message": "Failed to create ACL"}

        sync_result = _sync_mapping_targets(
            config,
            auth_manager,
            "acl_role",
            str(result["sys_id"]),
            role_resolution["role_ids"],
        )
        acl_roles = _get_acl_role_mappings(config, auth_manager, str(result.get("sys_id")))
        acl = _normalize_acl(result, acl_roles)
        return {
            "success": True,
            "message": f"Created ACL: {result.get('name')}",
            "acl_id": result.get("sys_id"),
            "acl_name": result.get("name"),
            "acl": acl,
            "role_mapping_sync": sync_result,
        }
    except requests.RequestException as exc:
        logger.error("Failed to create ACL %s: %s", params.name, exc)
        return {"success": False, "message": _format_permission_error(exc, "create ACL")}


def update_acl(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateACLParams,
) -> Dict[str, Any]:
    """Update an ACL in ServiceNow."""
    try:
        existing = _resolve_acl(
            config,
            auth_manager,
            params.acl_id,
            operation=params.operation,
            acl_type=params.acl_type,
            fields=ACL_FIELDS,
        )
        if not existing or not existing.get("sys_id"):
            return {"success": False, "message": f"ACL not found: {params.acl_id}"}

        body = _build_acl_body(
            name=params.name,
            operation=params.new_operation,
            acl_type=params.new_acl_type,
            description=params.description,
            decision_type=params.decision_type,
            admin_overrides=params.admin_overrides,
            active=params.active,
            advanced=params.advanced,
            applies_to=params.applies_to,
            data_condition=params.data_condition,
            script=params.script,
            extra_fields=params.extra_fields,
        )

        if body:
            result = _request_json(
                "PATCH",
                _table_url(config, ACL_TABLE, str(existing["sys_id"])),
                auth_manager,
                config.timeout,
                json_body=body,
            )
        else:
            result = existing

        role_sync_result = None
        if params.requires_roles is not None:
            role_resolution = _resolve_role_ids(config, auth_manager, params.requires_roles)
            if role_resolution["missing"]:
                missing = ", ".join(role_resolution["missing"])
                return {
                    "success": False,
                    "message": f"Cannot update ACL because these roles were not found: {missing}",
                }
            role_sync_result = _sync_mapping_targets(
                config,
                auth_manager,
                "acl_role",
                str(existing["sys_id"]),
                role_resolution["role_ids"],
            )

        refreshed = _get_record(
            config,
            auth_manager,
            ACL_TABLE,
            str(existing["sys_id"]),
            fields=ACL_FIELDS,
            display_value="false",
        )
        refreshed = refreshed or result or existing
        acl_roles = _get_acl_role_mappings(config, auth_manager, str(existing["sys_id"]))
        return {
            "success": True,
            "message": f"Updated ACL: {refreshed.get('name')}",
            "acl_id": refreshed.get("sys_id"),
            "acl_name": refreshed.get("name"),
            "acl": _normalize_acl(refreshed, acl_roles),
            "role_mapping_sync": role_sync_result,
        }
    except requests.RequestException as exc:
        logger.error("Failed to update ACL %s: %s", params.acl_id, exc)
        return {"success": False, "message": _format_permission_error(exc, "update ACL")}


def validate_acl(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateACLParams,
) -> Dict[str, Any]:
    """Validate an ACL using documented ServiceNow ACL rules plus reference checks."""
    try:
        record = _resolve_acl(
            config,
            auth_manager,
            params.acl_id,
            operation=params.operation,
            acl_type=params.acl_type,
            fields=None,
        )
        if not record or not record.get("sys_id"):
            return {"success": False, "message": f"ACL not found: {params.acl_id}"}

        acl_roles = _get_acl_role_mappings(config, auth_manager, str(record["sys_id"]))
        acl = _normalize_acl(record, acl_roles)

        issues: List[str] = []
        warnings: List[str] = []
        checks: List[Dict[str, Any]] = []

        has_roles = bool(acl_roles)
        has_security_attributes = _has_security_attribute_constraints(record)
        has_data_condition = bool(_extract_display_value(record.get("condition")))
        has_script = bool(_extract_display_value(record.get("script")))

        if not any([has_roles, has_security_attributes, has_data_condition, has_script]):
            issues.append(
                "ACL is effectively empty. ServiceNow denies access by default when an ACL has "
                "no roles, security attributes, data condition, or script."
            )

        if acl["name"] and acl["name"].startswith("*") and acl["applies_to"]:
            issues.append("Wildcard ACLs cannot use the applies_to filter.")

        script_text = _normalize_script_for_validation(acl["script"])
        if script_text in {"true", "returntrue"} or "answer=true" in script_text:
            issues.append(
                "ACL script is overly permissive and matches ServiceNow invalid ACL guidance."
            )

        missing_roles: List[str] = []
        for mapping in acl_roles:
            role_id = mapping.get("role_id")
            if not role_id:
                missing_roles.append("<empty role reference>")
                continue
            if not _resolve_role(config, auth_manager, role_id):
                missing_roles.append(mapping.get("role_name") or role_id)
        if missing_roles:
            issues.append(f"ACL references missing roles: {', '.join(missing_roles)}")

        security_attribute_validation = "not_applicable"
        if has_security_attributes:
            security_attribute_validation = "best_effort_only"
            warnings.append(
                "Security Attribute constraints were detected. The validator preserves them, but "
                "runtime evaluation still needs in-instance Access Analyzer or form testing."
            )
            security_attribute_value = _extract_display_value(record.get("security_attribute"))
            if security_attribute_value:
                security_attribute_validation = "not_found"
                attribute_record = _resolve_record(
                    config,
                    auth_manager,
                    SECURITY_ATTRIBUTE_TABLE,
                    security_attribute_value,
                    query_field="name",
                    fields="sys_id,name",
                )
                if attribute_record:
                    security_attribute_validation = "found"
                else:
                    warnings.append(
                        "Security attribute reference could not be resolved through the Table API. "
                        "This may indicate a missing attribute or an instance-specific storage "
                        "pattern."
                    )

        if acl["script"] and not acl["advanced"]:
            warnings.append("ACL has a script but advanced mode is not enabled.")

        checks.append({"name": "has_required_security_logic", "passed": not issues})
        checks.append(
            {
                "name": "security_attribute_reference_check",
                "status": security_attribute_validation,
            }
        )
        checks.append({"name": "role_reference_check", "passed": not missing_roles})

        status = "valid" if not issues else "invalid"
        return {
            "success": True,
            "message": f"ACL validation completed with status: {status}",
            "status": status,
            "acl": acl,
            "issues": issues,
            "warnings": warnings,
            "checks": checks,
        }
    except requests.RequestException as exc:
        logger.error("Failed to validate ACL %s: %s", params.acl_id, exc)
        return {"success": False, "message": f"Failed to validate ACL: {exc}"}


def test_acl_access(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: TestACLAccessParams,
) -> Dict[str, Any]:
    """Best-effort test of one ACL against a user, role, or group."""
    try:
        acl_record = _resolve_acl(
            config,
            auth_manager,
            params.acl_id,
            operation=params.operation,
            acl_type=params.acl_type,
            fields=None,
        )
        if not acl_record or not acl_record.get("sys_id"):
            return {"success": False, "message": f"ACL not found: {params.acl_id}"}

        acl_roles = _get_acl_role_mappings(config, auth_manager, str(acl_record["sys_id"]))
        acl = _normalize_acl(acl_record, acl_roles)
        subject_roles = _collect_identity_role_ids(
            config,
            auth_manager,
            params.analyze_as,
            params.identity,
        )
        if not subject_roles.get("success"):
            return subject_roles

        subject_role_ids = set(subject_roles.get("role_ids", []))
        subject_role_names = subject_roles.get("role_names", [])
        required_role_ids = {role["role_id"] for role in acl_roles if role.get("role_id")}
        subject_has_admin = _role_names_include(subject_role_names, "admin")
        admin_override_applied = bool(acl.get("admin_overrides") and subject_has_admin)
        role_check_status = "passed"
        if required_role_ids and subject_role_ids.isdisjoint(required_role_ids):
            role_check_status = "failed"
        if admin_override_applied:
            role_check_status = "bypassed"

        table_name = _resolve_acl_table_name(acl_record, params.record_table)
        applies_to_status = "not_applicable"
        if acl.get("applies_to"):
            if admin_override_applied:
                applies_to_status = "bypassed"
            elif params.record_id and table_name:
                applies_to_status = _evaluate_encoded_query(
                    config,
                    auth_manager,
                    table_name,
                    params.record_id,
                    acl["applies_to"],
                )
            else:
                applies_to_status = "unknown"

        data_condition_status = "not_applicable"
        if acl.get("data_condition"):
            if admin_override_applied:
                data_condition_status = "bypassed"
            elif params.record_id and table_name:
                data_condition_status = _evaluate_encoded_query(
                    config,
                    auth_manager,
                    table_name,
                    params.record_id,
                    acl["data_condition"],
                )
            else:
                data_condition_status = "unknown"

        script_status = "not_applicable"
        if acl.get("script"):
            script_status = "bypassed" if admin_override_applied else "unknown"

        security_attribute_status = "not_applicable"
        if _has_security_attribute_constraints(acl_record):
            security_attribute_status = "bypassed" if admin_override_applied else "unknown"

        checks = [
            {
                "name": "admin_override",
                "status": "applied" if admin_override_applied else "not_applicable",
                "enabled": acl.get("admin_overrides", False),
                "subject_has_admin": subject_has_admin,
            },
            {
                "name": "role_check",
                "status": role_check_status,
                "required_roles": acl["requires_roles"],
                "subject_roles": subject_role_names,
            },
            {"name": "applies_to", "status": applies_to_status, "query": acl.get("applies_to")},
            {
                "name": "data_condition",
                "status": data_condition_status,
                "query": acl.get("data_condition"),
            },
            {"name": "script", "status": script_status},
            {"name": "security_attributes", "status": security_attribute_status},
        ]

        statuses = {check["status"] for check in checks}
        if "failed" in statuses:
            outcome = "blocked"
        elif "unknown" in statuses:
            outcome = "indeterminate"
        else:
            outcome = "allowed"

        explanation = {
            "allowed": (
                "The ACL passed every check that can be evaluated through the Table API."
                if not admin_override_applied
                else "The subject has the admin role and this ACL allows admin override."
            ),
            "blocked": "At least one ACL check failed.",
            "indeterminate": (
                "No check failed, but at least one script or security constraint still requires "
                "runtime evaluation inside ServiceNow."
            ),
        }[outcome]

        return {
            "success": True,
            "message": f"ACL test completed with outcome: {outcome}",
            "outcome": outcome,
            "explanation": explanation,
            "acl": acl,
            "subject": subject_roles.get("subject"),
            "checks": checks,
        }
    except requests.RequestException as exc:
        logger.error("Failed to test ACL %s: %s", params.acl_id, exc)
        return {"success": False, "message": f"Failed to test ACL: {exc}"}


def list_roles(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListRolesParams,
) -> Dict[str, Any]:
    """List roles from ServiceNow."""
    query = None
    if params.query:
        query = f"nameLIKE{params.query}^ORdescriptionLIKE{params.query}"

    try:
        records = _query_table(
            config,
            auth_manager,
            ROLE_TABLE,
            query=query,
            fields=ROLE_FIELDS,
            limit=params.limit,
            offset=params.offset,
            display_value="false",
        )
        roles = []
        for record in records:
            contained = _get_contained_role_mappings(
                config, auth_manager, str(record.get("sys_id"))
            )
            roles.append(_normalize_role(record, contained_roles=contained))

        return {
            "success": True,
            "message": f"Found {len(roles)} roles",
            "roles": roles,
            "count": len(roles),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.RequestException as exc:
        logger.error("Failed to list roles: %s", exc)
        return {"success": False, "message": f"Failed to list roles: {exc}", "roles": []}


def get_role(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetRoleParams,
) -> Dict[str, Any]:
    """Get a specific role from ServiceNow."""
    try:
        role_record = _resolve_role(config, auth_manager, params.role_id)
        if not role_record or not role_record.get("sys_id"):
            return {"success": False, "message": f"Role not found: {params.role_id}"}

        contained = _get_contained_role_mappings(
            config,
            auth_manager,
            str(role_record.get("sys_id")),
        )
        return {
            "success": True,
            "message": f"Found role: {role_record.get('name')}",
            "role": _normalize_role(role_record, contained_roles=contained),
        }
    except requests.RequestException as exc:
        logger.error("Failed to get role %s: %s", params.role_id, exc)
        return {"success": False, "message": f"Failed to get role: {exc}"}


def create_role(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateRoleParams,
) -> Dict[str, Any]:
    """Create a role in ServiceNow."""
    try:
        contains_resolution = _resolve_role_ids(config, auth_manager, params.contains_roles)
        if contains_resolution["missing"]:
            missing = ", ".join(contains_resolution["missing"])
            return {
                "success": False,
                "message": (
                    f"Cannot create role because these contained roles were not found: {missing}"
                ),
            }

        body: Dict[str, Any] = {"name": params.name}
        if params.description is not None:
            body["description"] = params.description
        if params.extra_fields:
            body.update(params.extra_fields)

        result = _request_json(
            "POST",
            _table_url(config, ROLE_TABLE),
            auth_manager,
            config.timeout,
            json_body=body,
        )
        if not isinstance(result, dict) or not result.get("sys_id"):
            return {"success": False, "message": "Failed to create role"}

        sync_result = _sync_mapping_targets(
            config,
            auth_manager,
            "role_contains",
            str(result["sys_id"]),
            contains_resolution["role_ids"],
        )
        contained = _get_contained_role_mappings(config, auth_manager, str(result["sys_id"]))
        return {
            "success": True,
            "message": f"Created role: {result.get('name')}",
            "role_id": result.get("sys_id"),
            "role_name": result.get("name"),
            "role": _normalize_role(result, contained_roles=contained),
            "contained_role_sync": sync_result,
        }
    except requests.RequestException as exc:
        logger.error("Failed to create role %s: %s", params.name, exc)
        return {"success": False, "message": f"Failed to create role: {exc}"}


def update_role(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateRoleParams,
) -> Dict[str, Any]:
    """Update a role in ServiceNow."""
    try:
        role_record = _resolve_role(config, auth_manager, params.role_id)
        if not role_record or not role_record.get("sys_id"):
            return {"success": False, "message": f"Role not found: {params.role_id}"}

        body: Dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.description is not None:
            body["description"] = params.description
        if params.extra_fields:
            body.update(params.extra_fields)

        if body:
            result = _request_json(
                "PATCH",
                _table_url(config, ROLE_TABLE, str(role_record["sys_id"])),
                auth_manager,
                config.timeout,
                json_body=body,
            )
        else:
            result = role_record

        contains_sync_result = None
        if params.contains_roles is not None:
            contains_resolution = _resolve_role_ids(config, auth_manager, params.contains_roles)
            if contains_resolution["missing"]:
                missing = ", ".join(contains_resolution["missing"])
                return {
                    "success": False,
                    "message": (
                        f"Cannot update role because these contained roles were not found: "
                        f"{missing}"
                    ),
                }
            contains_sync_result = _sync_mapping_targets(
                config,
                auth_manager,
                "role_contains",
                str(role_record["sys_id"]),
                contains_resolution["role_ids"],
            )

        refreshed = _get_record(
            config,
            auth_manager,
            ROLE_TABLE,
            str(role_record["sys_id"]),
            fields=ROLE_FIELDS,
            display_value="false",
        )
        refreshed = refreshed or result or role_record
        contained = _get_contained_role_mappings(config, auth_manager, str(role_record["sys_id"]))
        return {
            "success": True,
            "message": f"Updated role: {refreshed.get('name')}",
            "role_id": refreshed.get("sys_id"),
            "role_name": refreshed.get("name"),
            "role": _normalize_role(refreshed, contained_roles=contained),
            "contained_role_sync": contains_sync_result,
        }
    except requests.RequestException as exc:
        logger.error("Failed to update role %s: %s", params.role_id, exc)
        return {"success": False, "message": f"Failed to update role: {exc}"}


def validate_role(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateRoleParams,
) -> Dict[str, Any]:
    """Validate a role, including containment checks."""
    try:
        role_record = _resolve_role(config, auth_manager, params.role_id)
        if not role_record or not role_record.get("sys_id"):
            return {"success": False, "message": f"Role not found: {params.role_id}"}

        contained = _get_contained_role_mappings(config, auth_manager, str(role_record["sys_id"]))
        normalized_role = _normalize_role(role_record, contained_roles=contained)
        issues: List[str] = []
        warnings: List[str] = []

        duplicates = _query_table(
            config,
            auth_manager,
            ROLE_TABLE,
            query=f"name={role_record.get('name')}",
            fields="sys_id,name",
            limit=10,
            display_value="false",
        )
        duplicate_ids = {
            str(item.get("sys_id"))
            for item in duplicates
            if item.get("sys_id") and str(item.get("sys_id")) != str(role_record.get("sys_id"))
        }
        if duplicate_ids:
            issues.append(
                f"Role name '{role_record.get('name')}' is duplicated on {len(duplicate_ids)} "
                f"other record(s)."
            )

        for contained_role in contained:
            if _would_create_role_cycle(
                config,
                auth_manager,
                str(role_record["sys_id"]),
                contained_role["role_id"],
            ):
                issues.append(
                    f"Contained role '{contained_role['role_name']}' introduces a circular role "
                    f"dependency."
                )

        if not contained:
            warnings.append("Role does not contain any other roles.")

        status = "valid" if not issues else "invalid"
        return {
            "success": True,
            "message": f"Role validation completed with status: {status}",
            "status": status,
            "role": normalized_role,
            "issues": issues,
            "warnings": warnings,
        }
    except requests.RequestException as exc:
        logger.error("Failed to validate role %s: %s", params.role_id, exc)
        return {"success": False, "message": f"Failed to validate role: {exc}"}


def list_role_mappings(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListRoleMappingsParams,
) -> Dict[str, Any]:
    """List role mappings for a specific mapping type."""
    mapping_config = ROLE_MAPPING_CONFIG[params.mapping_type]
    query_parts: List[str] = []

    try:
        if params.source:
            source_record = _resolve_source_record(
                config,
                auth_manager,
                params.mapping_type,
                params.source,
                acl_operation=params.acl_operation,
                acl_type=params.acl_type,
            )
            if not source_record or not source_record.get("sys_id"):
                return {
                    "success": False,
                    "message": (
                        f"Source not found for mapping type {params.mapping_type}: {params.source}"
                    ),
                }
            query_parts.append(f"{mapping_config['source_field']}={source_record['sys_id']}")

        if params.target_role:
            role_record = _resolve_role(config, auth_manager, params.target_role)
            if not role_record or not role_record.get("sys_id"):
                return {
                    "success": False,
                    "message": f"Target role not found: {params.target_role}",
                }
            query_parts.append(f"{mapping_config['target_field']}={role_record['sys_id']}")

        fields = (
            f"sys_id,{mapping_config['source_field']},{mapping_config['target_field']},"
            "sys_created_on,sys_updated_on"
        )
        rows = _query_table(
            config,
            auth_manager,
            mapping_config["table"],
            query="^".join(query_parts) if query_parts else None,
            fields=fields,
            limit=params.limit,
            offset=params.offset,
            display_value="true",
        )

        mappings = []
        for row in rows:
            mappings.append(
                {
                    "sys_id": row.get("sys_id"),
                    "mapping_type": params.mapping_type,
                    "source": _extract_display_value(row.get(mapping_config["source_field"])),
                    "target_role": _extract_display_value(row.get(mapping_config["target_field"])),
                    "sys_created_on": row.get("sys_created_on"),
                    "sys_updated_on": row.get("sys_updated_on"),
                }
            )

        return {
            "success": True,
            "message": f"Found {len(mappings)} {params.mapping_type} mapping(s)",
            "mappings": mappings,
            "count": len(mappings),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.RequestException as exc:
        logger.error("Failed to list %s mappings: %s", params.mapping_type, exc)
        return {
            "success": False,
            "message": f"Failed to list {params.mapping_type} mappings: {exc}",
            "mappings": [],
        }


def create_role_mapping(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateRoleMappingParams,
) -> Dict[str, Any]:
    """Create a role mapping in ServiceNow."""
    mapping_config = ROLE_MAPPING_CONFIG[params.mapping_type]
    try:
        source_record = _resolve_source_record(
            config,
            auth_manager,
            params.mapping_type,
            params.source,
            acl_operation=params.acl_operation,
            acl_type=params.acl_type,
        )
        if not source_record or not source_record.get("sys_id"):
            return {
                "success": False,
                "message": (
                    f"Source not found for mapping type {params.mapping_type}: {params.source}"
                ),
            }

        target_role = _resolve_role(config, auth_manager, params.target_role)
        if not target_role or not target_role.get("sys_id"):
            return {"success": False, "message": f"Target role not found: {params.target_role}"}

        if params.mapping_type == "role_contains" and _would_create_role_cycle(
            config,
            auth_manager,
            str(source_record["sys_id"]),
            str(target_role["sys_id"]),
        ):
            return {
                "success": False,
                "message": "This containment mapping would introduce a circular role dependency.",
            }

        existing = _query_table(
            config,
            auth_manager,
            mapping_config["table"],
            query=(
                f"{mapping_config['source_field']}={source_record['sys_id']}^"
                f"{mapping_config['target_field']}={target_role['sys_id']}"
            ),
            fields=f"sys_id,{mapping_config['source_field']},{mapping_config['target_field']}",
            limit=1,
            display_value="false",
        )
        if existing:
            return {
                "success": True,
                "message": "Role mapping already exists",
                "mapping_id": existing[0].get("sys_id"),
            }

        body = {
            mapping_config["source_field"]: source_record["sys_id"],
            mapping_config["target_field"]: target_role["sys_id"],
        }
        if params.extra_fields:
            body.update(params.extra_fields)

        result = _request_json(
            "POST",
            _table_url(config, mapping_config["table"]),
            auth_manager,
            config.timeout,
            json_body=body,
        )
        if not isinstance(result, dict) or not result.get("sys_id"):
            return {"success": False, "message": "Failed to create role mapping"}

        return {
            "success": True,
            "message": f"Created {params.mapping_type} mapping",
            "mapping_id": result.get("sys_id"),
            "mapping_type": params.mapping_type,
            "source_id": source_record.get("sys_id"),
            "target_role_id": target_role.get("sys_id"),
        }
    except requests.RequestException as exc:
        logger.error("Failed to create %s mapping: %s", params.mapping_type, exc)
        return {"success": False, "message": f"Failed to create role mapping: {exc}"}


def update_role_mapping(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateRoleMappingParams,
) -> Dict[str, Any]:
    """Update an existing role mapping."""
    mapping_config = ROLE_MAPPING_CONFIG[params.mapping_type]
    try:
        existing = _get_record(
            config,
            auth_manager,
            mapping_config["table"],
            params.mapping_id,
            fields=f"sys_id,{mapping_config['source_field']},{mapping_config['target_field']}",
            display_value="false",
        )
        if not existing or not existing.get("sys_id"):
            return {
                "success": False,
                "message": f"Role mapping not found: {params.mapping_id}",
            }

        body: Dict[str, Any] = {}
        if params.source is not None:
            source_record = _resolve_source_record(
                config,
                auth_manager,
                params.mapping_type,
                params.source,
                acl_operation=params.acl_operation,
                acl_type=params.acl_type,
            )
            if not source_record or not source_record.get("sys_id"):
                return {
                    "success": False,
                    "message": (
                        f"Source not found for mapping type {params.mapping_type}: {params.source}"
                    ),
                }
            body[mapping_config["source_field"]] = source_record["sys_id"]

        if params.target_role is not None:
            target_role = _resolve_role(config, auth_manager, params.target_role)
            if not target_role or not target_role.get("sys_id"):
                return {
                    "success": False,
                    "message": f"Target role not found: {params.target_role}",
                }
            future_source_id = str(
                body.get(mapping_config["source_field"])
                or existing.get(mapping_config["source_field"])
            )
            future_target_id = str(target_role["sys_id"])
            if params.mapping_type == "role_contains" and _would_create_role_cycle(
                config,
                auth_manager,
                future_source_id,
                future_target_id,
            ):
                return {
                    "success": False,
                    "message": (
                        "This containment mapping would introduce a circular role dependency."
                    ),
                }
            body[mapping_config["target_field"]] = future_target_id

        if params.extra_fields:
            body.update(params.extra_fields)

        if not body:
            return {
                "success": True,
                "message": "No changes requested for role mapping",
                "mapping_id": params.mapping_id,
            }

        result = _request_json(
            "PATCH",
            _table_url(config, mapping_config["table"], params.mapping_id),
            auth_manager,
            config.timeout,
            json_body=body,
        )
        if not isinstance(result, dict):
            return {"success": False, "message": "Failed to update role mapping"}

        return {
            "success": True,
            "message": f"Updated {params.mapping_type} mapping",
            "mapping_id": result.get("sys_id") or params.mapping_id,
            "mapping_type": params.mapping_type,
        }
    except requests.RequestException as exc:
        logger.error(
            "Failed to update %s mapping %s: %s", params.mapping_type, params.mapping_id, exc
        )
        return {"success": False, "message": f"Failed to update role mapping: {exc}"}


def validate_role_mapping(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateRoleMappingParams,
) -> Dict[str, Any]:
    """Validate a role mapping or a proposed role mapping."""
    mapping_config = ROLE_MAPPING_CONFIG[params.mapping_type]
    try:
        issues: List[str] = []
        warnings: List[str] = []

        source_record: Optional[Dict[str, Any]] = None
        target_role: Optional[Dict[str, Any]] = None
        mapping_record: Optional[Dict[str, Any]] = None

        if params.mapping_id:
            mapping_record = _get_record(
                config,
                auth_manager,
                mapping_config["table"],
                params.mapping_id,
                fields=f"sys_id,{mapping_config['source_field']},{mapping_config['target_field']}",
                display_value="false",
            )
            if not mapping_record:
                return {
                    "success": False,
                    "message": f"Role mapping not found: {params.mapping_id}",
                }

        if params.source:
            source_record = _resolve_source_record(
                config,
                auth_manager,
                params.mapping_type,
                params.source,
                acl_operation=params.acl_operation,
                acl_type=params.acl_type,
            )
        elif mapping_record:
            source_record = _get_record(
                config,
                auth_manager,
                mapping_config["source_table"],
                str(mapping_record.get(mapping_config["source_field"])),
                display_value="false",
            )
        if not source_record:
            issues.append("Source record could not be resolved.")

        if params.target_role:
            target_role = _resolve_role(config, auth_manager, params.target_role)
        elif mapping_record:
            target_field_value = _extract_display_value(
                mapping_record.get(mapping_config["target_field"])
            )
            if target_field_value:
                target_role = _resolve_role(config, auth_manager, target_field_value)
        if not target_role:
            issues.append("Target role could not be resolved.")

        if source_record and target_role:
            duplicates = _query_table(
                config,
                auth_manager,
                mapping_config["table"],
                query=(
                    f"{mapping_config['source_field']}={source_record['sys_id']}^"
                    f"{mapping_config['target_field']}={target_role['sys_id']}"
                ),
                fields="sys_id",
                limit=10,
                display_value="false",
            )
            duplicate_ids = [str(item.get("sys_id")) for item in duplicates if item.get("sys_id")]
            if params.mapping_id:
                duplicate_ids = [
                    mapping_id for mapping_id in duplicate_ids if mapping_id != params.mapping_id
                ]
            if duplicate_ids:
                issues.append(
                    f"Duplicate mapping exists on {len(duplicate_ids)} other record(s): "
                    f"{', '.join(duplicate_ids)}"
                )

        if params.mapping_type == "role_contains" and source_record and target_role:
            if _would_create_role_cycle(
                config,
                auth_manager,
                str(source_record["sys_id"]),
                str(target_role["sys_id"]),
            ):
                issues.append("Role containment would introduce a circular dependency.")

        if params.mapping_type == "acl_role" and source_record:
            acl_validation = validate_acl(
                config,
                auth_manager,
                ValidateACLParams(acl_id=str(source_record["sys_id"])),
            )
            if acl_validation.get("status") == "invalid":
                warnings.append("Target ACL currently fails validation checks.")

        status = "valid" if not issues else "invalid"
        return {
            "success": True,
            "message": f"Role mapping validation completed with status: {status}",
            "status": status,
            "mapping_type": params.mapping_type,
            "mapping_id": params.mapping_id,
            "source": source_record.get("sys_id") if source_record else None,
            "target_role": target_role.get("sys_id") if target_role else None,
            "issues": issues,
            "warnings": warnings,
        }
    except requests.RequestException as exc:
        logger.error("Failed to validate %s mapping: %s", params.mapping_type, exc)
        return {"success": False, "message": f"Failed to validate role mapping: {exc}"}


def validate_security_constraints(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateSecurityConstraintsParams,
) -> Dict[str, Any]:
    """Validate the ACL set that secures a table or table field."""
    object_names = [params.table, "*"]
    if params.field:
        object_names = [
            f"{params.table}.{params.field}",
            f"{params.table}.*",
            f"*.{params.field}",
            "*.*",
        ]

    try:
        records = _query_table(
            config,
            auth_manager,
            ACL_TABLE,
            query=f"type={params.acl_type}^operation={params.operation}^nameIN{','.join(object_names)}",
            fields=ACL_FIELDS,
            limit=20,
            display_value="false",
        )

        validations = []
        aggregate_issues: List[str] = []
        aggregate_warnings: List[str] = []
        for record in records:
            validation = validate_acl(
                config,
                auth_manager,
                ValidateACLParams(acl_id=str(record.get("sys_id"))),
            )
            validations.append(validation)

        if not validations:
            aggregate_warnings.append(
                "No matching ACLs were found for this object. Access may be controlled by broader "
                "wildcards "
                "or denied by default."
            )
        active_validations = [
            validation
            for validation in validations
            if validation.get("acl", {}).get("active", False)
        ]
        inactive_validation_count = len(validations) - len(active_validations)
        for validation in active_validations:
            aggregate_issues.extend(validation.get("issues", []))
            aggregate_warnings.extend(validation.get("warnings", []))
        if inactive_validation_count:
            aggregate_warnings.append(
                "Inactive ACLs were excluded from the aggregate status because they do not "
                "participate "
                "in runtime access checks."
            )

        aggregate_issues = _dedupe_preserve_order(aggregate_issues)
        aggregate_warnings = _dedupe_preserve_order(aggregate_warnings)
        valid_active_count = sum(
            1 for validation in active_validations if validation.get("status") == "valid"
        )
        invalid_active_count = sum(
            1 for validation in active_validations if validation.get("status") == "invalid"
        )

        if not validations or not active_validations:
            status = "review_needed"
            status_reason = (
                "No active matching ACLs were available for configuration validation. Effective "
                "access still "
                "depends on broader wildcards or ServiceNow's default deny behavior."
            )
        elif valid_active_count and invalid_active_count:
            status = "mixed"
            status_reason = (
                "Active matching ACLs are mixed: some passed configuration validation and some "
                "failed. "
                "This alone does not prove that access is blocked."
            )
        elif invalid_active_count:
            status = "invalid"
            status_reason = "Every active matching ACL failed configuration validation."
        else:
            status = "valid"
            status_reason = (
                "All active matching ACLs passed configuration validation. Effective access still "
                "requires "
                "identity-specific ACL testing or in-instance runtime evaluation."
            )

        return {
            "success": True,
            "message": f"Security constraint validation completed with status: {status}",
            "status": status,
            "status_reason": status_reason,
            "object": {
                "table": params.table,
                "field": params.field,
                "operation": params.operation,
                "type": params.acl_type,
            },
            "summary": {
                "matching_acl_count": len(validations),
                "active_acl_count": len(active_validations),
                "valid_active_acl_count": valid_active_count,
                "invalid_active_acl_count": invalid_active_count,
                "inactive_acl_count": inactive_validation_count,
            },
            "effective_access": {
                "status": "not_tested",
                "message": (
                    "This tool reviews ACL configuration only. Use identity-specific ACL testing "
                    "or "
                    "ServiceNow's in-instance security analysis to determine effective access."
                ),
            },
            "validations": validations,
            "issues": aggregate_issues,
            "warnings": aggregate_warnings,
        }
    except requests.RequestException as exc:
        logger.error(
            "Failed to validate security constraints for %s.%s/%s: %s",
            params.table,
            params.field,
            params.operation,
            exc,
        )
        return {"success": False, "message": f"Failed to validate security constraints: {exc}"}
