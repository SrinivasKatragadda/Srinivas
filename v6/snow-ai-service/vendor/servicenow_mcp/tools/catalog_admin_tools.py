"""
Administrative Service Catalog tools for the ServiceNow MCP server.

This module provides catalog-level CRUD helpers for catalogs, catalog items,
and catalog client scripts. It supports the administrative workflows needed to
maintain catalog structure, manage active catalog entries, and keep the
underlying catalog metadata in sync with business requirements.

The helpers in this file are deliberately narrow: they focus on catalog
administration rather than request fulfillment or item variable management,
which are handled in separate modules.
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel, ConfigDict, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

CATALOG_TABLE = "sc_catalog"
CATALOG_ITEM_TABLE = "sc_cat_item"
CATALOG_CLIENT_SCRIPT_TABLE = "catalog_script_client"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class ListCatalogsParams(BaseModel):
    """Parameters for List Catalogs tool calls."""
    limit: int = Field(10, description="Maximum catalogs to return")
    offset: int = Field(0, description="Pagination offset")
    query: Optional[str] = Field(None, description="Search query for title or description")
    active: Optional[bool] = Field(True, description="Filter by active catalogs")
    include_raw: bool = Field(False, description="Include raw records")


class GetCatalogParams(BaseModel):
    """Parameters for Get Catalog tool calls."""
    catalog_id: str = Field(..., description="Catalog sys_id or title")
    include_raw: bool = Field(False, description="Include raw record")


class CreateCatalogParams(BaseModel):
    """Parameters for Create Catalog tool calls."""
    title: str = Field(..., description="Catalog title")
    description: Optional[str] = Field(None, description="Catalog description")
    active: Optional[bool] = Field(True, description="Whether the catalog is active")
    manager: Optional[str] = Field(None, description="Manager sys_id")
    desktop_image: Optional[str] = Field(None, description="Desktop image sys_id or value")
    mobile_subtitle: Optional[str] = Field(None, description="Mobile subtitle")
    homepage_renderer: Optional[str] = Field(None, description="Homepage renderer value")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to set on sc_catalog"
    )


class UpdateCatalogParams(BaseModel):
    """Parameters for Update Catalog tool calls."""
    catalog_id: str = Field(..., description="Catalog sys_id or title")
    title: Optional[str] = Field(None, description="Catalog title")
    description: Optional[str] = Field(None, description="Catalog description")
    active: Optional[bool] = Field(None, description="Whether the catalog is active")
    manager: Optional[str] = Field(None, description="Manager sys_id")
    desktop_image: Optional[str] = Field(None, description="Desktop image sys_id or value")
    mobile_subtitle: Optional[str] = Field(None, description="Mobile subtitle")
    homepage_renderer: Optional[str] = Field(None, description="Homepage renderer value")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to update on sc_catalog"
    )


class CreateCatalogItemParams(BaseModel):
    """Parameters for Create Catalog Item tool calls."""
    name: str = Field(..., description="Catalog item name")
    short_description: Optional[str] = Field(None, description="Short description")
    description: Optional[str] = Field(None, description="Description")
    category: Optional[str] = Field(None, description="Category sys_id")
    workflow: Optional[str] = Field(
        None, description="Legacy fulfillment workflow sys_id for the catalog item"
    )
    flow: Optional[str] = Field(None, description="Flow Designer flow sys_id for the catalog item")
    price: Optional[str] = Field(None, description="Price")
    active: Optional[bool] = Field(True, description="Whether the catalog item is active")
    order: Optional[int] = Field(None, description="Display order")
    picture: Optional[str] = Field(None, description="Picture attachment or image value")
    icon: Optional[str] = Field(None, description="Icon")
    delivery_time: Optional[str] = Field(None, description="Delivery time")
    availability: Optional[str] = Field(None, description="Availability")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to set on sc_cat_item"
    )


class PublishCatalogItemParams(BaseModel):
    """Parameters for Publish Catalog Item tool calls."""
    item_id: str = Field(..., description="Catalog item sys_id or name")
    active: bool = Field(True, description="Whether to make the catalog item active")
    attributes: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Additional sc_cat_item fields to set while publishing, such as catalogs or portal "
            "flags"
        ),
    )


class ListCatalogClientScriptsParams(BaseModel):
    """Parameters for List Catalog Client Scripts tool calls."""
    limit: int = Field(20, description="Maximum catalog client scripts to return")
    offset: int = Field(0, description="Pagination offset")
    catalog_item_id: Optional[str] = Field(None, description="Filter by catalog item sys_id")
    query: Optional[str] = Field(None, description="Search query for name, condition, or script")
    active: Optional[bool] = Field(None, description="Filter by active state")
    include_raw: bool = Field(False, description="Include raw records")


class GetCatalogClientScriptParams(BaseModel):
    """Parameters for Get Catalog Client Script tool calls."""
    client_script_id: str = Field(..., description="Catalog client script sys_id or name")
    include_raw: bool = Field(False, description="Include raw record")


class CreateCatalogClientScriptParams(BaseModel):
    """Parameters for Create Catalog Client Script tool calls."""
    model_config = ConfigDict(populate_by_name=True)

    catalog_item_id: str = Field(..., description="Catalog item sys_id")
    name: str = Field(..., description="Catalog client script name")
    script: str = Field(..., description="Client script body")
    active: Optional[bool] = Field(True, description="Whether the script is active")
    condition: Optional[str] = Field(None, description="Condition expression")
    applies_to: Optional[str] = Field(None, description="Applies-to value if supported")
    script_type: Optional[str] = Field(
        None,
        alias="type",
        description="Client script type such as onLoad",
    )
    ui_type: Optional[str] = Field(None, description="UI type")
    order: Optional[int] = Field(None, description="Execution order")
    variable_id: Optional[str] = Field(None, description="Target variable sys_id")
    variable_set_id: Optional[str] = Field(None, description="Target variable set sys_id")
    isolate_script: Optional[bool] = Field(None, description="Use isolated script")
    global_script: Optional[bool] = Field(None, description="Global script flag")
    description: Optional[str] = Field(None, description="Description")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to set on catalog_script_client"
    )


class UpdateCatalogClientScriptParams(BaseModel):
    """Parameters for Update Catalog Client Script tool calls."""
    model_config = ConfigDict(populate_by_name=True)

    client_script_id: str = Field(..., description="Catalog client script sys_id or name")
    catalog_item_id: Optional[str] = Field(None, description="Catalog item sys_id")
    name: Optional[str] = Field(None, description="Catalog client script name")
    script: Optional[str] = Field(None, description="Client script body")
    active: Optional[bool] = Field(None, description="Whether the script is active")
    condition: Optional[str] = Field(None, description="Condition expression")
    applies_to: Optional[str] = Field(None, description="Applies-to value if supported")
    script_type: Optional[str] = Field(
        None,
        alias="type",
        description="Client script type such as onLoad",
    )
    ui_type: Optional[str] = Field(None, description="UI type")
    order: Optional[int] = Field(None, description="Execution order")
    variable_id: Optional[str] = Field(None, description="Target variable sys_id")
    variable_set_id: Optional[str] = Field(None, description="Target variable set sys_id")
    isolate_script: Optional[bool] = Field(None, description="Use isolated script")
    global_script: Optional[bool] = Field(None, description="Global script flag")
    description: Optional[str] = Field(None, description="Description")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional table fields to update on catalog_script_client"
    )


def _headers(auth_manager: AuthManager) -> Dict[str, str]:
    """Build JSON request headers for ServiceNow Table API calls.

    Args:
        auth_manager: Authentication manager providing base headers.

    Returns:
        Header mapping with authorization and JSON content negotiation.
    """
    headers = auth_manager.get_headers()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    return headers


def _coerce_value_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """Return a ServiceNow value/display mapping when the input already matches that shape.

    Args:
        value: Raw field value.

    Returns:
        Mapping containing `value` and/or `display_value`, or None.
    """
    if isinstance(value, dict) and ("value" in value or "display_value" in value):
        return value
    return None


def _display(value: Any) -> Any:
    """Extract a human-readable display value from a ServiceNow field.

    Args:
        value: Raw ServiceNow field value.

    Returns:
        `display_value` when present, otherwise `value`, otherwise the original input.
    """
    mapping = _coerce_value_mapping(value)
    if mapping is not None:
        return mapping.get("display_value") or mapping.get("value")
    return value


def _extract_value(value: Any) -> Any:
    """Extract the stored internal value from a ServiceNow field.

    Args:
        value: Raw ServiceNow field value.

    Returns:
        `value` when present, otherwise `display_value`, otherwise the original input.
    """
    mapping = _coerce_value_mapping(value)
    if mapping is not None:
        return mapping.get("value") or mapping.get("display_value")
    return value


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    """Extract and normalize a record sys_id from a Table API record.

    Args:
        record: ServiceNow record dictionary.

    Returns:
        Trimmed sys_id string, or None when no sys_id is available.
    """
    sys_id = _extract_value(record.get("sys_id"))
    if sys_id in (None, ""):
        return None
    return str(sys_id).strip() or None


def _ref(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize a reference field into value/display pairs.

    Args:
        value: Raw reference field value.

    Returns:
        Normalized reference payload, or None when no reference content exists.
    """
    normalized_value = _extract_value(value)
    display_value = _display(value)
    if normalized_value in (None, "") and display_value in (None, ""):
        return None
    return {
        "value": None if normalized_value in (None, "") else str(normalized_value),
        "display_value": None if display_value in (None, "") else str(display_value),
    }


def _table_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    """Build the Table API endpoint URL for a table or specific record.

    Args:
        config: Server configuration.
        table: ServiceNow table name.
        sys_id: Optional record sys_id.

    Returns:
        Full Table API URL.
    """
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _query(parts: List[Optional[str]]) -> Optional[str]:
    """Join non-empty query segments into a ServiceNow encoded query.

    Args:
        parts: Candidate encoded-query segments.

    Returns:
        Caret-delimited query string, or None when no parts remain.
    """
    segments = [segment for segment in parts if segment]
    return "^".join(segments) if segments else None


def _bool_string(value: Optional[bool]) -> Optional[str]:
    """Convert a Python boolean into ServiceNow's string representation.

    Args:
        value: Boolean value to normalize.

    Returns:
        `"true"`, `"false"`, or None.
    """
    if value is None:
        return None
    return "true" if value else "false"


def _request_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    search_fields: List[str],
) -> Dict[str, Any]:
    """Resolve a record by sys_id or exact match across one or more fields.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: ServiceNow table name.
        identifier: User-supplied sys_id or human-readable identifier.
        search_fields: Fields to search when the identifier is not a sys_id.

    Returns:
        Resolved ServiceNow record.

    Raises:
        ValueError: If the identifier is empty, not found, or resolves ambiguously.
    """
    cleaned_identifier = str(identifier).strip()
    if not cleaned_identifier:
        raise ValueError("Record identifier cannot be empty")

    params = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    if SYS_ID_PATTERN.fullmatch(cleaned_identifier):
        response = requests.get(
            _table_url(config, table, cleaned_identifier),
            headers=_headers(auth_manager),
            params=params,
            timeout=config.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        if not result:
            raise ValueError(f"{table} record not found: {cleaned_identifier}")
        return result

    query = "^OR".join(f"{field}={cleaned_identifier}" for field in search_fields)
    response = requests.get(
        _table_url(config, table),
        headers=_headers(auth_manager),
        params={
            **params,
            "sysparm_query": query,
            "sysparm_limit": 20,
        },
        timeout=config.timeout,
    )
    response.raise_for_status()
    records = response.json().get("result", [])
    if not records:
        raise ValueError(f"{table} record not found for identifier: {cleaned_identifier}")

    normalized_identifier = cleaned_identifier.casefold()
    exact_matches: List[Dict[str, Any]] = []
    for field in search_fields:
        for record in records:
            field_value = _display(record.get(field))
            if (
                field_value is not None
                and str(field_value).strip().casefold() == normalized_identifier
            ):
                if record not in exact_matches:
                    exact_matches.append(record)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ValueError(f"Multiple {table} records matched identifier: {cleaned_identifier}")
    if len(records) == 1:
        return records[0]
    raise ValueError(f"Multiple {table} records matched identifier: {cleaned_identifier}")


def _list_records(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query: Optional[str],
    limit: int,
    offset: int,
) -> List[Dict[str, Any]]:
    """List records from a Table API endpoint with paging and display values enabled.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: ServiceNow table name.
        query: Optional encoded query.
        limit: Maximum records to return.
        offset: Pagination offset.

    Returns:
        List of ServiceNow records.
    """
    response = requests.get(
        _table_url(config, table),
        headers=_headers(auth_manager),
        params={
            "sysparm_query": query,
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        },
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", [])


def _post_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a record through the ServiceNow Table API.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: ServiceNow table name.
        body: Record payload to create.

    Returns:
        Created ServiceNow record.
    """
    response = requests.post(
        _table_url(config, table),
        headers=_headers(auth_manager),
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        },
        json=body,
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _patch_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    sys_id: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Update an existing record through the ServiceNow Table API.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: ServiceNow table name.
        sys_id: Record sys_id.
        body: Partial update payload.

    Returns:
        Updated ServiceNow record.
    """
    response = requests.patch(
        _table_url(config, table, sys_id),
        headers=_headers(auth_manager),
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        },
        json=body,
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _build_catalog_payload(params: Any) -> Dict[str, Any]:
    """Build a catalog create or update payload from model parameters.

    Args:
        params: Catalog create or update parameter model.

    Returns:
        Table API payload for `sc_catalog`.
    """
    body: Dict[str, Any] = {}
    field_map = {
        "title": "title",
        "description": "description",
        "manager": "manager",
        "desktop_image": "desktop_image",
        "mobile_subtitle": "mobile_subtitle",
        "homepage_renderer": "homepage_renderer",
    }
    for attr_name, field_name in field_map.items():
        value = getattr(params, attr_name, None)
        if value is not None:
            body[field_name] = value
    if getattr(params, "active", None) is not None:
        body["active"] = _bool_string(params.active)
    extra = getattr(params, "attributes", None) or {}
    body.update(extra)
    return body


def _build_catalog_item_payload(params: CreateCatalogItemParams) -> Dict[str, Any]:
    """Build a catalog item payload from creation parameters.

    Args:
        params: Catalog item creation parameters.

    Returns:
        Table API payload for `sc_cat_item`.
    """
    body: Dict[str, Any] = {"name": params.name}
    optional_field_map = {
        "short_description": "short_description",
        "description": "description",
        "category": "category",
        "workflow": "workflow",
        "flow": "flow_designer_flow",
        "price": "price",
        "picture": "picture",
        "icon": "icon",
        "delivery_time": "delivery_time",
        "availability": "availability",
    }
    for attr_name, field_name in optional_field_map.items():
        value = getattr(params, attr_name, None)
        if value is not None:
            body[field_name] = value
    if params.active is not None:
        body["active"] = _bool_string(params.active)
    if params.order is not None:
        body["order"] = str(params.order)
    body.update(params.attributes or {})
    return body


def _build_catalog_client_script_payload(params: Any) -> Dict[str, Any]:
    """Build a catalog client script payload from create or update parameters.

    Args:
        params: Catalog client script parameter model.

    Returns:
        Table API payload for `catalog_script_client`.
    """
    body: Dict[str, Any] = {}
    field_map = {
        "catalog_item_id": "cat_item",
        "name": "name",
        "script": "script",
        "condition": "condition",
        "applies_to": "applies_to",
        "script_type": "type",
        "ui_type": "ui_type",
        "variable_id": "cat_variable",
        "variable_set_id": "variable_set",
        "description": "description",
    }
    for attr_name, field_name in field_map.items():
        value = getattr(params, attr_name, None)
        if value is not None:
            body[field_name] = value
    if getattr(params, "active", None) is not None:
        body["active"] = _bool_string(params.active)
    if getattr(params, "isolate_script", None) is not None:
        body["isolate_script"] = _bool_string(params.isolate_script)
    if getattr(params, "global_script", None) is not None:
        body["global"] = _bool_string(params.global_script)
    if getattr(params, "order", None) is not None:
        body["order"] = str(params.order)
    body.update(getattr(params, "attributes", None) or {})
    return body


def _normalize_catalog(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize a catalog record for tool responses.

    Args:
        record: Raw ServiceNow catalog record.
        include_raw: Whether to include the raw record.

    Returns:
        Normalized catalog payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "title": _display(record.get("title")),
        "description": _display(record.get("description")),
        "active": _display(record.get("active")),
        "manager": _ref(record.get("manager")),
        "desktop_image": _ref(record.get("desktop_image")),
        "mobile_subtitle": _display(record.get("mobile_subtitle")),
        "homepage_renderer": _display(record.get("homepage_renderer")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _normalize_catalog_item(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize a catalog item record for tool responses.

    Args:
        record: Raw ServiceNow catalog item record.
        include_raw: Whether to include the raw record.

    Returns:
        Normalized catalog item payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "name": _display(record.get("name")),
        "short_description": _display(record.get("short_description")),
        "description": _display(record.get("description")),
        "category": _ref(record.get("category")),
        "workflow": _ref(record.get("workflow")),
        "flow": _ref(record.get("flow_designer_flow")),
        "price": _display(record.get("price")),
        "picture": _ref(record.get("picture")),
        "icon": _display(record.get("icon")),
        "active": _display(record.get("active")),
        "order": _display(record.get("order")),
        "delivery_time": _display(record.get("delivery_time")),
        "availability": _display(record.get("availability")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _normalize_catalog_client_script(
    record: Dict[str, Any], include_raw: bool = False
) -> Dict[str, Any]:
    """Normalize a catalog client script record for tool responses.

    Args:
        record: Raw ServiceNow catalog client script record.
        include_raw: Whether to include the raw record.

    Returns:
        Normalized catalog client script payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "name": _display(record.get("name")),
        "catalog_item": _ref(record.get("cat_item")),
        "variable": _ref(record.get("cat_variable")),
        "variable_set": _ref(record.get("variable_set")),
        "active": _display(record.get("active")),
        "applies_to": _display(record.get("applies_to")),
        "type": _display(record.get("type")),
        "ui_type": _display(record.get("ui_type")),
        "condition": _display(record.get("condition")),
        "script": _display(record.get("script")),
        "description": _display(record.get("description")),
        "global": _display(record.get("global")),
        "isolate_script": _display(record.get("isolate_script")),
        "order": _display(record.get("order")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def list_catalogs(
    config: ServerConfig, auth_manager: AuthManager, params: ListCatalogsParams
) -> Dict[str, Any]:
    """List catalogs for ServiceNow MCP workflows.

    Applies optional active and text filters, then returns normalized catalog
    records with paging metadata.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog list filters and paging options.

    Returns:
        Result payload containing normalized catalogs and count metadata.
    """
    query = _query(
        [
            f"active={_bool_string(params.active)}" if params.active is not None else None,
            f"titleLIKE{params.query}^ORdescriptionLIKE{params.query}" if params.query else None,
        ]
    )
    try:
        records = _list_records(
            config, auth_manager, CATALOG_TABLE, query, params.limit, params.offset
        )
        catalogs = [_normalize_catalog(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(catalogs)} catalogs",
            "catalogs": catalogs,
            "count": len(catalogs),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error listing catalogs: %s", e)
        return {
            "success": False,
            "message": f"Error listing catalogs: {str(e)}",
            "catalogs": [],
            "count": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_catalog(
    config: ServerConfig, auth_manager: AuthManager, params: GetCatalogParams
) -> Dict[str, Any]:
    """Get catalog for ServiceNow MCP workflows.

    Resolves one catalog record by identifier and returns the normalized catalog
    payload expected by MCP callers.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog lookup parameters.

    Returns:
        Result payload containing the normalized catalog record.
    """
    try:
        record = _request_record(
            config,
            auth_manager,
            CATALOG_TABLE,
            params.catalog_id,
            ["title"],
        )
        return {
            "success": True,
            "message": "Retrieved catalog",
            "catalog": _normalize_catalog(record, params.include_raw),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting catalog: %s", e)
        return {"success": False, "message": f"Error getting catalog: {str(e)}"}


def create_catalog(
    config: ServerConfig, auth_manager: AuthManager, params: CreateCatalogParams
) -> Dict[str, Any]:
    """Create catalog for ServiceNow MCP workflows.

    Creates a catalog record from validated request fields and returns a
    normalized representation of the created catalog.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog creation parameters.

    Returns:
        Result payload containing the created normalized catalog.
    """
    try:
        record = _post_record(config, auth_manager, CATALOG_TABLE, _build_catalog_payload(params))
        return {
            "success": True,
            "message": f"Created catalog {record.get('title') or record.get('sys_id')}",
            "catalog": _normalize_catalog(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating catalog: %s", e)
        return {"success": False, "message": f"Error creating catalog: {str(e)}"}


def update_catalog(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateCatalogParams
) -> Dict[str, Any]:
    """Update catalog for ServiceNow MCP workflows.

    Applies a partial patch to a catalog record and blocks empty update payloads
    before issuing a write operation.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog update parameters.

    Returns:
        Result payload containing the updated normalized catalog.
    """
    body = _build_catalog_payload(params)
    if not body:
        return {"success": False, "message": "No catalog fields supplied to update"}
    try:
        record = _request_record(
            config,
            auth_manager,
            CATALOG_TABLE,
            params.catalog_id,
            ["title"],
        )
        sys_id = _record_sys_id(record)
        if not sys_id:
            raise ValueError(f"Catalog does not have a sys_id: {params.catalog_id}")
        updated = _patch_record(config, auth_manager, CATALOG_TABLE, sys_id, body)
        return {
            "success": True,
            "message": f"Updated catalog {updated.get('title') or sys_id}",
            "catalog": _normalize_catalog(updated, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating catalog: %s", e)
        return {"success": False, "message": f"Error updating catalog: {str(e)}"}


def create_catalog_item(
    config: ServerConfig, auth_manager: AuthManager, params: CreateCatalogItemParams
) -> Dict[str, Any]:
    """Create catalog item for ServiceNow MCP workflows.

    Creates a new catalog item in the target catalog and returns the normalized
    created item payload for follow-up automation.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog item creation parameters.

    Returns:
        Result payload containing the created normalized catalog item.
    """
    try:
        record = _post_record(
            config, auth_manager, CATALOG_ITEM_TABLE, _build_catalog_item_payload(params)
        )
        return {
            "success": True,
            "message": f"Created catalog item {record.get('name') or record.get('sys_id')}",
            "catalog_item": _normalize_catalog_item(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating catalog item: %s", e)
        return {"success": False, "message": f"Error creating catalog item: {str(e)}"}


def publish_catalog_item(
    config: ServerConfig, auth_manager: AuthManager, params: PublishCatalogItemParams
) -> Dict[str, Any]:
    """Publish catalog item for ServiceNow MCP workflows.

    Toggles the catalog item's active state and returns the updated item record
    so callers can confirm publish or unpublish status.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog item publish or unpublish parameters.

    Returns:
        Result payload containing the updated normalized catalog item and
        publish-state flag.
    """
    try:
        record = _request_record(
            config,
            auth_manager,
            CATALOG_ITEM_TABLE,
            params.item_id,
            ["sys_id", "name", "short_description"],
        )
        sys_id = _record_sys_id(record)
        if not sys_id:
            raise ValueError(f"Unable to resolve catalog item sys_id for {params.item_id}")

        body: Dict[str, Any] = {"active": _bool_string(params.active)}
        body.update(params.attributes or {})

        updated = _patch_record(config, auth_manager, CATALOG_ITEM_TABLE, sys_id, body)
        return {
            "success": True,
            "message": (
                f"Published catalog item {updated.get('name') or updated.get('sys_id')}"
                if params.active
                else f"Unpublished catalog item {updated.get('name') or updated.get('sys_id')}"
            ),
            "catalog_item": _normalize_catalog_item(updated, True),
            "published": bool(params.active),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error publishing catalog item: %s", e)
        return {"success": False, "message": f"Error publishing catalog item: {str(e)}"}


def list_catalog_client_scripts(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListCatalogClientScriptsParams,
) -> Dict[str, Any]:
    """List catalog client scripts for ServiceNow MCP workflows.

    Filters catalog client scripts by catalog item, active flag, and text query,
    then returns normalized script records with paging metadata.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog client script list filters and paging options.

    Returns:
        Result payload containing normalized client scripts and count metadata.
    """
    query = _query(
        [
            f"cat_item={params.catalog_item_id}" if params.catalog_item_id else None,
            f"active={_bool_string(params.active)}" if params.active is not None else None,
            (
                f"nameLIKE{params.query}^ORconditionLIKE{params.query}^ORscriptLIKE{params.query}"
                if params.query
                else None
            ),
        ]
    )
    try:
        records = _list_records(
            config,
            auth_manager,
            CATALOG_CLIENT_SCRIPT_TABLE,
            query,
            params.limit,
            params.offset,
        )
        client_scripts = [
            _normalize_catalog_client_script(record, params.include_raw) for record in records
        ]
        return {
            "success": True,
            "message": f"Retrieved {len(client_scripts)} catalog client scripts",
            "catalog_client_scripts": client_scripts,
            "count": len(client_scripts),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error listing catalog client scripts: %s", e)
        return {
            "success": False,
            "message": f"Error listing catalog client scripts: {str(e)}",
            "catalog_client_scripts": [],
            "count": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_catalog_client_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetCatalogClientScriptParams,
) -> Dict[str, Any]:
    """Get one catalog client script by sys_id or name.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog client script lookup parameters.

    Returns:
        Result payload containing the normalized catalog client script.
    """
    try:
        record = _request_record(
            config,
            auth_manager,
            CATALOG_CLIENT_SCRIPT_TABLE,
            params.client_script_id,
            ["name"],
        )
        return {
            "success": True,
            "message": "Retrieved catalog client script",
            "catalog_client_script": _normalize_catalog_client_script(record, params.include_raw),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting catalog client script: %s", e)
        return {
            "success": False,
            "message": f"Error getting catalog client script: {str(e)}",
        }


def create_catalog_client_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCatalogClientScriptParams,
) -> Dict[str, Any]:
    """Create a catalog client script for a catalog item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog client script creation parameters.

    Returns:
        Result payload containing the created normalized catalog client script.
    """
    try:
        record = _post_record(
            config,
            auth_manager,
            CATALOG_CLIENT_SCRIPT_TABLE,
            _build_catalog_client_script_payload(params),
        )
        return {
            "success": True,
            "message": (
                f"Created catalog client script {record.get('name') or record.get('sys_id')}"
            ),
            "catalog_client_script": _normalize_catalog_client_script(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating catalog client script: %s", e)
        return {
            "success": False,
            "message": f"Error creating catalog client script: {str(e)}",
        }


def update_catalog_client_script(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateCatalogClientScriptParams,
) -> Dict[str, Any]:
    """Update an existing catalog client script.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog client script update parameters.

    Returns:
        Result payload containing the updated normalized catalog client script.
    """
    body = _build_catalog_client_script_payload(params)
    if not body:
        return {"success": False, "message": "No catalog client script fields supplied to update"}
    try:
        record = _request_record(
            config,
            auth_manager,
            CATALOG_CLIENT_SCRIPT_TABLE,
            params.client_script_id,
            ["name"],
        )
        sys_id = _record_sys_id(record)
        if not sys_id:
            raise ValueError(
                f"Catalog client script does not have a sys_id: {params.client_script_id}"
            )
        updated = _patch_record(config, auth_manager, CATALOG_CLIENT_SCRIPT_TABLE, sys_id, body)
        return {
            "success": True,
            "message": f"Updated catalog client script {updated.get('name') or sys_id}",
            "catalog_client_script": _normalize_catalog_client_script(updated, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating catalog client script: %s", e)
        return {
            "success": False,
            "message": f"Error updating catalog client script: {str(e)}",
        }
