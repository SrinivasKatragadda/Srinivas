"""Table and field schema tools for the ServiceNow MCP server.

This module exposes table-definition and dictionary-field helpers used to
inspect and manage ServiceNow schema information. It is the schema discovery
layer for callers that need to understand tables, columns, and dictionary
metadata before performing record-level operations.
"""

import re
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

TABLE_DEFINITION_TABLE = "sys_db_object"
FIELD_DEFINITION_TABLE = "sys_dictionary"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


def _api_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _extract_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _extract_display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _extract_result(response: requests.Response) -> Any:
    response.raise_for_status()
    payload = response.json() if response.content else {}
    return payload.get("result", payload)


def _bool_text(value: Optional[bool]) -> Optional[str]:
    if value is None:
        return None
    return "true" if value else "false"


def _clean_body(body: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in body.items() if value is not None}


def _merge_extra_fields(body: Dict[str, Any], fields: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    clean = _clean_body(body)
    clean.update(fields or {})
    return clean


def _query_records(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query: Optional[str],
    limit: int = 10,
    offset: int = 0,
    fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": limit,
        "sysparm_offset": offset,
    }
    if query:
        params["sysparm_query"] = query
    if fields:
        params["sysparm_fields"] = ",".join(fields)

    response = requests.get(
        _api_url(config, table),
        headers=auth_manager.get_headers(),
        params=params,
        timeout=config.timeout,
    )
    result = _extract_result(response)
    return result if isinstance(result, list) else []


def _resolve_table_sys_id(config: ServerConfig, auth_manager: AuthManager, table_id: str) -> str:
    cleaned = table_id.strip()
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    records = _query_records(
        config,
        auth_manager,
        TABLE_DEFINITION_TABLE,
        f"name={cleaned}^ORlabel={cleaned}",
        limit=2,
    )
    if not records:
        raise ValueError(f"Table definition not found: {table_id}")
    if len(records) > 1:
        exact = [record for record in records if _extract_display(record.get("name")) == cleaned]
        if len(exact) == 1:
            return str(_extract_value(exact[0].get("sys_id")))
    return str(_extract_value(records[0].get("sys_id")))


def _resolve_field_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    field_id: str,
    table: Optional[str] = None,
) -> str:
    cleaned = field_id.strip()
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned

    target_table = table
    element = cleaned
    if "." in cleaned and not table:
        target_table, element = cleaned.split(".", 1)
    if not target_table:
        raise ValueError("Provide table when field_id is not sys_id or table.element")

    records = _query_records(
        config,
        auth_manager,
        FIELD_DEFINITION_TABLE,
        f"name={target_table}^element={element}",
        limit=2,
    )
    if not records:
        raise ValueError(f"Field definition not found: {target_table}.{element}")
    return str(_extract_value(records[0].get("sys_id")))


def _normalize_table_definition(
    record: Dict[str, Any], include_raw: bool = False
) -> Dict[str, Any]:
    normalized = {
        "sys_id": _extract_value(record.get("sys_id")),
        "name": _extract_display(record.get("name")),
        "label": _extract_display(record.get("label")),
        "super_class": _extract_display(record.get("super_class")),
        "super_class_sys_id": _extract_value(record.get("super_class")),
        "scope": _extract_display(record.get("sys_scope")),
        "scope_sys_id": _extract_value(record.get("sys_scope")),
        "is_extendable": _extract_display(record.get("is_extendable")),
        "access": _extract_display(record.get("access")),
        "caller_access": _extract_display(record.get("caller_access")),
        "create_access_controls": _extract_display(record.get("create_access_controls")),
        "number_ref": _extract_display(record.get("number_ref")),
        "attributes": _extract_display(record.get("attributes")),
        "created_on": _extract_display(record.get("sys_created_on")),
        "updated_on": _extract_display(record.get("sys_updated_on")),
    }
    normalized = {key: value for key, value in normalized.items() if value not in (None, "")}
    if include_raw:
        normalized["raw"] = record
    return normalized


def _normalize_field_definition(
    record: Dict[str, Any], include_raw: bool = False
) -> Dict[str, Any]:
    normalized = {
        "sys_id": _extract_value(record.get("sys_id")),
        "table": _extract_display(record.get("name")),
        "element": _extract_display(record.get("element")),
        "label": _extract_display(record.get("column_label")),
        "type": _extract_display(record.get("internal_type")),
        "type_sys_id": _extract_value(record.get("internal_type")),
        "max_length": _extract_display(record.get("max_length")),
        "mandatory": _extract_display(record.get("mandatory")),
        "read_only": _extract_display(record.get("read_only")),
        "active": _extract_display(record.get("active")),
        "reference": _extract_display(record.get("reference")),
        "reference_sys_id": _extract_value(record.get("reference")),
        "choice": _extract_display(record.get("choice")),
        "default_value": _extract_display(record.get("default_value")),
        "attributes": _extract_display(record.get("attributes")),
        "comments": _extract_display(record.get("comments")),
        "dependent": _extract_display(record.get("dependent")),
        "dependent_on_field": _extract_display(record.get("dependent_on_field")),
        "display": _extract_display(record.get("display")),
        "unique": _extract_display(record.get("unique")),
        "calculated": _extract_display(record.get("calculated")),
        "calculation": _extract_display(record.get("calculation")),
        "audit": _extract_display(record.get("audit")),
        "created_on": _extract_display(record.get("sys_created_on")),
        "updated_on": _extract_display(record.get("sys_updated_on")),
    }
    normalized = {key: value for key, value in normalized.items() if value not in (None, "")}
    if include_raw:
        normalized["raw"] = record
    return normalized


class ListTableDefinitionsParams(BaseModel):
    """Parameters for listing ServiceNow table definitions from sys_db_object."""

    limit: int = Field(25, description="Maximum number of tables to return")
    offset: int = Field(0, description="Offset for pagination")
    query: Optional[str] = Field(None, description="Search text for table name or label")
    name: Optional[str] = Field(None, description="Exact table name filter, such as x_app_table")
    encoded_query: Optional[str] = Field(None, description="Additional encoded query")
    include_raw: bool = Field(False, description="Include raw sys_db_object records")


class GetTableDefinitionParams(BaseModel):
    """Parameters for reading one table definition."""

    table_id: str = Field(..., description="sys_id, table name, or table label")
    include_fields: bool = Field(True, description="Include field dictionary entries")
    include_raw: bool = Field(True, description="Include raw sys_db_object/sys_dictionary records")


class CreateTableDefinitionParams(BaseModel):
    """Parameters for creating a ServiceNow table definition."""

    name: str = Field(..., description="Table name, usually x_scope_table_name")
    label: str = Field(..., description="Human-readable table label")
    super_class: Optional[str] = Field(None, description="Parent table name or sys_id")
    is_extendable: Optional[bool] = Field(None, description="Whether this table can be extended")
    access: Optional[str] = Field(None, description="Table access setting when supported")
    caller_access: Optional[str] = Field(None, description="Caller access setting when supported")
    attributes: Optional[str] = Field(None, description="Comma-separated table attributes")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_db_object fields to set",
    )


class UpdateTableDefinitionParams(BaseModel):
    """Parameters for updating a ServiceNow table definition."""

    table_id: str = Field(..., description="sys_id, table name, or table label")
    label: Optional[str] = Field(None, description="Updated table label")
    super_class: Optional[str] = Field(None, description="Updated parent table name or sys_id")
    is_extendable: Optional[bool] = Field(None, description="Whether this table can be extended")
    access: Optional[str] = Field(None, description="Table access setting when supported")
    caller_access: Optional[str] = Field(None, description="Caller access setting when supported")
    attributes: Optional[str] = Field(None, description="Comma-separated table attributes")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_db_object fields to update",
    )


class ListFieldDefinitionsParams(BaseModel):
    """Parameters for listing fields from sys_dictionary."""

    table: Optional[str] = Field(None, description="Table name filter")
    query: Optional[str] = Field(None, description="Search text for element or label")
    field_type: Optional[str] = Field(None, description="Field type filter, such as string")
    encoded_query: Optional[str] = Field(None, description="Additional encoded query")
    limit: int = Field(50, description="Maximum number of fields to return")
    offset: int = Field(0, description="Offset for pagination")
    include_raw: bool = Field(False, description="Include raw sys_dictionary records")


class GetFieldDefinitionParams(BaseModel):
    """Parameters for reading one field definition."""

    field_id: str = Field(..., description="sys_id, field name with table param, or table.field")
    table: Optional[str] = Field(None, description="Table name when field_id is just the element")
    include_raw: bool = Field(True, description="Include raw sys_dictionary record")


class CreateFieldDefinitionParams(BaseModel):
    """Parameters for creating a field definition in sys_dictionary."""

    table: str = Field(..., description="Table name to add the field to")
    element: str = Field(..., description="Column/element name")
    label: str = Field(..., description="Column label shown to users")
    field_type: str = Field(..., description="Field type, such as string, integer, reference")
    max_length: Optional[int] = Field(None, description="Maximum length when supported")
    mandatory: Optional[bool] = Field(None, description="Whether the field is mandatory")
    read_only: Optional[bool] = Field(None, description="Whether the field is read-only")
    active: Optional[bool] = Field(None, description="Whether the field is active")
    reference: Optional[str] = Field(None, description="Reference table for reference fields")
    choice: Optional[str] = Field(None, description="Choice behavior, such as 0, 1, 2, 3")
    default_value: Optional[str] = Field(None, description="Default value")
    attributes: Optional[str] = Field(None, description="Comma-separated field attributes")
    comments: Optional[str] = Field(None, description="Field comments or help for admins")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_dictionary fields to set",
    )


class UpdateFieldDefinitionParams(BaseModel):
    """Parameters for updating a field definition in sys_dictionary."""

    field_id: str = Field(..., description="sys_id, field name with table param, or table.field")
    table: Optional[str] = Field(None, description="Table name when field_id is just the element")
    label: Optional[str] = Field(None, description="Updated column label")
    field_type: Optional[str] = Field(None, description="Updated field type when supported")
    max_length: Optional[int] = Field(None, description="Updated maximum length")
    mandatory: Optional[bool] = Field(None, description="Whether the field is mandatory")
    read_only: Optional[bool] = Field(None, description="Whether the field is read-only")
    active: Optional[bool] = Field(None, description="Whether the field is active")
    reference: Optional[str] = Field(None, description="Reference table for reference fields")
    choice: Optional[str] = Field(None, description="Choice behavior, such as 0, 1, 2, 3")
    default_value: Optional[str] = Field(None, description="Default value")
    attributes: Optional[str] = Field(None, description="Comma-separated field attributes")
    comments: Optional[str] = Field(None, description="Field comments or help for admins")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Any additional sys_dictionary fields to update",
    )


def list_table_definitions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListTableDefinitionsParams,
) -> Dict[str, Any]:
    """List ServiceNow table definitions."""
    try:
        query_parts = []
        if params.name:
            query_parts.append(f"name={params.name}")
        if params.query:
            query_parts.append(f"nameLIKE{params.query}^ORlabelLIKE{params.query}")
        if params.encoded_query:
            query_parts.append(params.encoded_query)
        records = _query_records(
            config,
            auth_manager,
            TABLE_DEFINITION_TABLE,
            "^".join(query_parts),
            params.limit,
            params.offset,
        )
        return {
            "success": True,
            "table": TABLE_DEFINITION_TABLE,
            "count": len(records),
            "records": [
                _normalize_table_definition(record, params.include_raw) for record in records
            ],
        }
    except Exception as exc:
        return {"success": False, "message": f"Error listing table definitions: {exc}"}


def get_table_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetTableDefinitionParams,
) -> Dict[str, Any]:
    """Get a ServiceNow table definition and optionally its fields."""
    try:
        sys_id = _resolve_table_sys_id(config, auth_manager, params.table_id)
        response = requests.get(
            _api_url(config, TABLE_DEFINITION_TABLE, sys_id),
            headers=auth_manager.get_headers(),
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
            timeout=config.timeout,
        )
        record = _extract_result(response)
        table_info = _normalize_table_definition(record, params.include_raw)
        result: Dict[str, Any] = {
            "success": True,
            "table": TABLE_DEFINITION_TABLE,
            "record": table_info,
        }
        if params.include_fields:
            table_name = table_info.get("name") or _extract_display(record.get("name"))
            field_records = _query_records(
                config,
                auth_manager,
                FIELD_DEFINITION_TABLE,
                f"name={table_name}",
                limit=500,
            )
            result["fields"] = [
                _normalize_field_definition(field, params.include_raw) for field in field_records
            ]
            result["field_count"] = len(field_records)
        return result
    except Exception as exc:
        return {"success": False, "message": f"Error getting table definition: {exc}"}


def create_table_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateTableDefinitionParams,
) -> Dict[str, Any]:
    """Create a ServiceNow table definition."""
    try:
        body = _merge_extra_fields(
            {
                "name": params.name,
                "label": params.label,
                "super_class": params.super_class,
                "is_extendable": _bool_text(params.is_extendable),
                "access": params.access,
                "caller_access": params.caller_access,
                "attributes": params.attributes,
            },
            params.fields,
        )
        response = requests.post(
            _api_url(config, TABLE_DEFINITION_TABLE),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = _extract_result(response)
        return {
            "success": True,
            "message": f"Created table definition: {record.get('name')}",
            "record": _normalize_table_definition(record, True),
        }
    except Exception as exc:
        return {"success": False, "message": f"Error creating table definition: {exc}"}


def update_table_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateTableDefinitionParams,
) -> Dict[str, Any]:
    """Update a ServiceNow table definition."""
    try:
        sys_id = _resolve_table_sys_id(config, auth_manager, params.table_id)
        body = _merge_extra_fields(
            {
                "label": params.label,
                "super_class": params.super_class,
                "is_extendable": _bool_text(params.is_extendable),
                "access": params.access,
                "caller_access": params.caller_access,
                "attributes": params.attributes,
            },
            params.fields,
        )
        if not body:
            return {"success": True, "message": "No table definition changes supplied"}
        response = requests.patch(
            _api_url(config, TABLE_DEFINITION_TABLE, sys_id),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = _extract_result(response)
        return {
            "success": True,
            "message": f"Updated table definition: {record.get('name')}",
            "record": _normalize_table_definition(record, True),
        }
    except Exception as exc:
        return {"success": False, "message": f"Error updating table definition: {exc}"}


def list_field_definitions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListFieldDefinitionsParams,
) -> Dict[str, Any]:
    """List ServiceNow field definitions."""
    try:
        query_parts = []
        if params.table:
            query_parts.append(f"name={params.table}")
        if params.query:
            query_parts.append(f"elementLIKE{params.query}^ORcolumn_labelLIKE{params.query}")
        if params.field_type:
            query_parts.append(f"internal_type={params.field_type}")
        if params.encoded_query:
            query_parts.append(params.encoded_query)
        records = _query_records(
            config,
            auth_manager,
            FIELD_DEFINITION_TABLE,
            "^".join(query_parts),
            params.limit,
            params.offset,
        )
        return {
            "success": True,
            "table": FIELD_DEFINITION_TABLE,
            "count": len(records),
            "records": [
                _normalize_field_definition(record, params.include_raw) for record in records
            ],
        }
    except Exception as exc:
        return {"success": False, "message": f"Error listing field definitions: {exc}"}


def get_field_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetFieldDefinitionParams,
) -> Dict[str, Any]:
    """Get a ServiceNow field definition."""
    try:
        sys_id = _resolve_field_sys_id(config, auth_manager, params.field_id, params.table)
        response = requests.get(
            _api_url(config, FIELD_DEFINITION_TABLE, sys_id),
            headers=auth_manager.get_headers(),
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
            timeout=config.timeout,
        )
        record = _extract_result(response)
        return {
            "success": True,
            "table": FIELD_DEFINITION_TABLE,
            "record": _normalize_field_definition(record, params.include_raw),
        }
    except Exception as exc:
        return {"success": False, "message": f"Error getting field definition: {exc}"}


def create_field_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateFieldDefinitionParams,
) -> Dict[str, Any]:
    """Create a ServiceNow field definition."""
    try:
        body = _field_body(params)
        response = requests.post(
            _api_url(config, FIELD_DEFINITION_TABLE),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = _extract_result(response)
        return {
            "success": True,
            "message": f"Created field definition: {record.get('name')}.{record.get('element')}",
            "record": _normalize_field_definition(record, True),
        }
    except Exception as exc:
        return {"success": False, "message": f"Error creating field definition: {exc}"}


def update_field_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateFieldDefinitionParams,
) -> Dict[str, Any]:
    """Update a ServiceNow field definition."""
    try:
        sys_id = _resolve_field_sys_id(config, auth_manager, params.field_id, params.table)
        body = _field_body(params)
        body.pop("name", None)
        body.pop("element", None)
        if not body:
            return {"success": True, "message": "No field definition changes supplied"}
        response = requests.patch(
            _api_url(config, FIELD_DEFINITION_TABLE, sys_id),
            headers=auth_manager.get_headers(),
            params={"sysparm_input_display_value": "true"},
            json=body,
            timeout=config.timeout,
        )
        record = _extract_result(response)
        return {
            "success": True,
            "message": f"Updated field definition: {record.get('name')}.{record.get('element')}",
            "record": _normalize_field_definition(record, True),
        }
    except Exception as exc:
        return {"success": False, "message": f"Error updating field definition: {exc}"}


def _field_body(params: Any) -> Dict[str, Any]:
    return _merge_extra_fields(
        {
            "name": getattr(params, "table", None),
            "element": getattr(params, "element", None),
            "column_label": getattr(params, "label", None),
            "internal_type": getattr(params, "field_type", None),
            "max_length": getattr(params, "max_length", None),
            "mandatory": _bool_text(getattr(params, "mandatory", None)),
            "read_only": _bool_text(getattr(params, "read_only", None)),
            "active": _bool_text(getattr(params, "active", None)),
            "reference": getattr(params, "reference", None),
            "choice": getattr(params, "choice", None),
            "default_value": getattr(params, "default_value", None),
            "attributes": getattr(params, "attributes", None),
            "comments": getattr(params, "comments", None),
        },
        getattr(params, "fields", None),
    )
