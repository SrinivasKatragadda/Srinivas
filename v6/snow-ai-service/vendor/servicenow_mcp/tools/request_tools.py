"""Request Management tools for ServiceNow MCP.

This module implements request and fulfillment helpers for ServiceNow's
service catalog. It covers request records, requested items, catalog tasks,
approvals, and the variable/value relationships that tie request data together.

The module is intentionally focused on request lifecycle management so that
catalog administration and fulfillment execution can evolve independently.
"""

import ast
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

REQUEST_TABLE = "sc_request"
REQUESTED_ITEM_TABLE = "sc_req_item"
CATALOG_TASK_TABLE = "sc_task"
APPROVAL_TABLE = "sysapproval_approver"
VARIABLE_DEFINITION_TABLE = "item_option_new"
VARIABLE_CHOICE_TABLE = "question_choice"
VARIABLE_VALUE_TABLE = "sc_item_option"
VARIABLE_LINK_TABLE = "sc_item_option_mtom"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class ListRequestsParams(BaseModel):
    """Parameters for List Requests tool calls."""
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    state: Optional[str] = Field(None, description="Request state")
    stage: Optional[str] = Field(None, description="Request stage")
    approval: Optional[str] = Field(None, description="Approval state")
    requested_for: Optional[str] = Field(None, description="Requested-for user sys_id")
    opened_by: Optional[str] = Field(None, description="Opened-by user sys_id")
    active: Optional[bool] = Field(None, description="Active filter")
    query: Optional[str] = Field(None, description="Search number/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetRequestParams(BaseModel):
    """Parameters for Get Request tool calls."""
    request_id: str = Field(..., description="Request sys_id, sys_id:<id>, or REQ number")
    include_items: bool = Field(True, description="Include requested items")
    include_tasks: bool = Field(True, description="Include catalog tasks")
    include_approvals: bool = Field(True, description="Include approvals")
    include_raw: bool = Field(False, description="Include raw records")


class CatalogTaskOrderInput(BaseModel):
    """Model or helper class for Catalog Task Order Input."""
    short_description: str = Field(..., description="Catalog task short description")
    description: Optional[str] = None
    state: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    comments: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional sc_task fields")


class RequestedItemOrderInput(BaseModel):
    """Model or helper class for Requested Item Order Input."""
    catalog_item: Optional[str] = Field(None, description="Catalog item sys_id")
    variables: Optional[Dict[str, Any]] = Field(
        None,
        description="Portal-style catalog variable values keyed by variable name, label, or sys_id",
    )
    requested_for: Optional[str] = None
    opened_by: Optional[str] = None
    quantity: Optional[int] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    stage: Optional[str] = None
    approval: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    comments: Optional[str] = None
    work_notes: Optional[str] = None
    catalog_tasks: Optional[List[CatalogTaskOrderInput]] = Field(
        None,
        description="Catalog tasks to create under the requested item in the same order flow",
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional sc_req_item fields")


class CreateRequestParams(BaseModel):
    """Parameters for Create Request tool calls."""
    requested_for: Optional[str] = None
    opened_by: Optional[str] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    stage: Optional[str] = None
    approval: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    special_instructions: Optional[str] = None
    requested_items: Optional[List[RequestedItemOrderInput]] = Field(
        None,
        description=(
            "Requested items to create under the request in the same call, similar to "
            "submitting a service portal order."
        ),
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional sc_request fields")


class UpdateRequestParams(CreateRequestParams):
    """Parameters for Update Request tool calls."""
    request_id: str = Field(..., description="Request sys_id, sys_id:<id>, or REQ number")
    comments: Optional[str] = None
    work_notes: Optional[str] = None


class ListRequestedItemsParams(BaseModel):
    """Parameters for List Requested Items tool calls."""
    request_id: Optional[str] = Field(None, description="Parent request sys_id or REQ number")
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    state: Optional[str] = None
    stage: Optional[str] = None
    approval: Optional[str] = None
    assigned_to: Optional[str] = None
    assignment_group: Optional[str] = None
    query: Optional[str] = Field(None, description="Search number/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetRequestedItemParams(BaseModel):
    """Parameters for Get Requested Item tool calls."""
    requested_item_id: str = Field(..., description="Requested item sys_id or RITM number")
    include_tasks: bool = Field(True, description="Include catalog tasks")
    include_approvals: bool = Field(True, description="Include approvals")
    include_raw: bool = Field(False, description="Include raw records")


class GetCatalogItemRequestOptionsParams(BaseModel):
    """Parameters for Get Catalog Item Request Options tool calls."""
    catalog_item_id: str = Field(..., description="Catalog item sys_id")
    include_choices: bool = Field(True, description="Include allowed choice values for variables")
    include_raw: bool = Field(
        False, description="Include raw variable definition and choice records"
    )


class CreateRequestedItemParams(BaseModel):
    """Parameters for Create Requested Item tool calls."""
    request_id: str = Field(..., description="Parent request sys_id or REQ number")
    catalog_item: Optional[str] = Field(None, description="Catalog item sys_id")
    variables: Optional[Dict[str, Any]] = Field(
        None,
        description="Portal-style catalog variable values keyed by variable name, label, or sys_id",
    )
    requested_for: Optional[str] = None
    opened_by: Optional[str] = None
    quantity: Optional[int] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    stage: Optional[str] = None
    approval: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    catalog_tasks: Optional[List[CatalogTaskOrderInput]] = Field(
        None,
        description="Catalog tasks to create under the requested item in the same call",
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional sc_req_item fields")


class UpdateRequestedItemParams(CreateRequestedItemParams):
    """Parameters for Update Requested Item tool calls."""
    requested_item_id: str = Field(..., description="Requested item sys_id or RITM number")
    request_id: Optional[str] = Field(None, description="New parent request sys_id or REQ number")
    comments: Optional[str] = None
    work_notes: Optional[str] = None


class ListCatalogTasksParams(BaseModel):
    """Parameters for List Catalog Tasks tool calls."""
    request_id: Optional[str] = Field(None, description="Parent request sys_id or REQ number")
    requested_item_id: Optional[str] = Field(
        None, description="Requested item sys_id or RITM number"
    )
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    state: Optional[str] = None
    assigned_to: Optional[str] = None
    assignment_group: Optional[str] = None
    active: Optional[bool] = Field(None, description="Active filter")
    query: Optional[str] = Field(None, description="Search number/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class CreateCatalogTaskParams(BaseModel):
    """Parameters for Create Catalog Task tool calls."""
    requested_item_id: str = Field(..., description="Requested item sys_id or RITM number")
    short_description: str = Field(..., description="Short description")
    description: Optional[str] = None
    state: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional sc_task fields")


class UpdateCatalogTaskParams(CreateCatalogTaskParams):
    """Parameters for Update Catalog Task tool calls."""
    task_id: str = Field(..., description="Catalog task sys_id or SCTASK number")
    requested_item_id: Optional[str] = Field(
        None, description="New requested item sys_id or RITM number"
    )
    short_description: Optional[str] = None
    comments: Optional[str] = None


class ListRequestApprovalsParams(BaseModel):
    """Parameters for List Request Approvals tool calls."""
    target_id: Optional[str] = Field(None, description="REQ/RITM/SCTASK number or sys_id")
    target_table: Optional[str] = Field(None, description="sc_request, sc_req_item, or sc_task")
    include_requested_items: bool = Field(True, description="Include RITM approvals for REQ")
    include_catalog_tasks: bool = Field(
        True,
        description="Include catalog task approvals when targeting a request or requested item",
    )
    state: Optional[str] = None
    approver: Optional[str] = None
    limit: int = Field(50, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class UpdateRequestApprovalParams(BaseModel):
    """Parameters for Update Request Approval tool calls."""
    approval_id: str = Field(..., description="Approval sys_id or sys_id:<id>")
    state: str = Field(..., description="New approval state")
    comments: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional approval fields")


class GetRequestFulfillmentStatusParams(BaseModel):
    """Parameters for Get Request Fulfillment Status tool calls."""
    request_id: str = Field(..., description="Request sys_id, sys_id:<id>, or REQ number")
    include_raw: bool = Field(False, description="Include raw records")


def _table_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    """Build ServiceNow table API URL with optional sys_id suffix.
    
    Args:
        config: Server configuration.
        table: Table name (e.g., 'sc_request', 'sc_req_item').
        sys_id: Optional record sys_id for direct record access.
        
    Returns:
        Full API URL (base URL if no sys_id, record URL if sys_id provided).
    """
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _headers(auth_manager: AuthManager) -> Dict[str, str]:
    """Build HTTP headers for ServiceNow API requests.
    
    Args:
        auth_manager: Authentication manager providing base headers.
        
    Returns:
        Dict with Authorization, Accept, and Content-Type headers.
    """
    headers = auth_manager.get_headers()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    return headers


def _coerce_value_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """Extract value/display_value mapping from reference or JSON string.
    
    Handles reference dicts, JSON-encoded strings, and literal Python dicts.
    Used for normalizing ServiceNow field values that may be references or display values.
    
    Args:
        value: Raw field value (dict, JSON string, or scalar).
        
    Returns:
        Dict with 'value' and/or 'display_value' keys, or None if not a mapping.
    """
    if isinstance(value, dict):
        return value if ("value" in value or "display_value" in value) else None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            for parse in (json.loads, ast.literal_eval):
                try:
                    parsed = parse(text)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(parsed, dict) and ("value" in parsed or "display_value" in parsed):
                    return parsed
    return None


def _display(value: Any) -> Any:
    """Extract display_value from reference mapping, or return raw value.
    
    Args:
        value: Field value (reference dict, JSON string, or scalar).
        
    Returns:
        display_value if available, else value field, else original value.
    """
    mapping = _coerce_value_mapping(value)
    if mapping is None:
        return value
    return mapping.get("display_value") or mapping.get("value")


def _extract_value(value: Any) -> Any:
    """Extract value field from reference mapping, or return raw value.
    
    Args:
        value: Field value (reference dict, JSON string, or scalar).
        
    Returns:
        value field if available, else display_value, else original value.
    """
    mapping = _coerce_value_mapping(value)
    if mapping is None:
        return value
    return mapping.get("value") or mapping.get("display_value")


def _clean_identifier(identifier: str) -> str:
    """Validate and clean record identifier.
    
    Args:
        identifier: Raw user input identifier string.
        
    Returns:
        Trimmed identifier.
        
    Raises:
        ValueError: If identifier is empty or blank after trimming.
    """
    cleaned = str(identifier).strip() if identifier is not None else ""
    if not cleaned:
        raise ValueError("Record identifier cannot be empty")
    return cleaned


def _identifier_variants(identifier: str) -> List[str]:
    """Generate case variants of identifier for fuzzy matching.
    
    Args:
        identifier: Original identifier string.
        
    Returns:
        List of case variants: original, uppercase, lowercase (deduped).
    """
    variants: List[str] = []
    for candidate in (identifier, identifier.upper(), identifier.lower()):
        if candidate not in variants:
            variants.append(candidate)
    return variants


def _matches_identifier(value: Any, identifier: str) -> bool:
    """Check if field value matches identifier (case-insensitive).
    
    Args:
        value: Field value (ref dict, string, etc).
        identifier: Search identifier.
        
    Returns:
        True if display_value matches identifier case-insensitive.
    """
    display_value = _display(value)
    if display_value is None:
        return False
    return str(display_value).strip().casefold() == identifier.casefold()


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    sys_id = _extract_value(record.get("sys_id"))
    if sys_id is None:
        return None
    return str(sys_id).strip() or None


def _read_bool(value: Any) -> Optional[bool]:
    """Parse ServiceNow boolean field value to Python bool.
    
    Handles ServiceNow string representations: 'true', '1', 'yes' → True,
    'false', '0', 'no' → False. Returns None for empty/invalid values.
    
    Args:
        value: Raw field value (may be bool, string, or other).
        
    Returns:
        Boolean value, or None if value is None or falsy/invalid.
    """
    value = _display(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _coerce_bool(value: Optional[bool]) -> Optional[str]:
    """Convert boolean to ServiceNow string format ('true' or 'false').
    
    Args:
        value: Python boolean value.
        
    Returns:
        'true' or 'false' string for API, or None if input is None.
    """
    if value is None:
        return None
    return "true" if value else "false"


def _extract_sys_id(identifier: str) -> Optional[str]:
    """Extract sys_id from identifier string (validates format).
    
    Handles 'sys_id:' prefix and validates against SYS_ID_PATTERN regex.
    
    Args:
        identifier: Identifier string (cleaned input).
        
    Returns:
        Valid sys_id string, or None if not in sys_id format.
    """
    cleaned = _clean_identifier(identifier)
    if cleaned.startswith("sys_id:"):
        cleaned = cleaned.replace("sys_id:", "", 1).strip()
    return cleaned if SYS_ID_PATTERN.fullmatch(cleaned) else None


def _query(parts: Sequence[Optional[str]]) -> str:
    """Build ServiceNow query string from parts.
    
    Joins non-empty parts with '^' operator (AND logic).
    
    Args:
        parts: List of query conditions (may include None/empty).
        
    Returns:
        Query string ready for sysparm_query API parameter.
    """
    return "^".join(str(part) for part in parts if part)


def _ref(value: Any) -> Dict[str, Any]:
    """Format value as reference dict with both value and display_value.
    
    Args:
        value: Raw field value (may be ref dict or string).
        
    Returns:
        Dict with extracted 'value' and 'display_value' fields.
    """
    return {"value": _extract_value(value), "display_value": _display(value)}


def _get(
    config: ServerConfig, auth_manager: AuthManager, table: str, sys_id: str
) -> Dict[str, Any]:
    """Fetch single record from ServiceNow table via HTTP GET.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        sys_id: Record sys_id.
        
    Returns:
        Record dictionary with all fields (display and raw values).
    """
    response = requests.get(
        _table_url(config, table, sys_id),
        headers=_headers(auth_manager),
        params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        timeout=config.timeout,
    )
    if response.status_code == 404:
        return {}
    response.raise_for_status()
    return response.json().get("result", {}) or {}


def _list(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Fetch records from ServiceNow table with optional query and pagination.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        query: Query string (sysparm_query format, optional).
        limit: Max records to return (default 10).
        offset: Pagination offset (default 0).
        
    Returns:
        List of record dictionaries.
    """
    params: Dict[str, Any] = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": limit,
        "sysparm_offset": offset,
    }
    if query:
        params["sysparm_query"] = query
    response = requests.get(
        _table_url(config, table),
        headers=_headers(auth_manager),
        params=params,
        timeout=config.timeout,
    )
    response.raise_for_status()
    result = response.json().get("result", [])
    return result if isinstance(result, list) else [result]


def _post(
    config: ServerConfig, auth_manager: AuthManager, table: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Create new record in ServiceNow table via HTTP POST.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        body: Fields to create (request body).
        
    Returns:
        Created record dictionary.
    """
    response = requests.post(
        _table_url(config, table),
        headers=_headers(auth_manager),
        params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        json=body,
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _patch(
    config: ServerConfig, auth_manager: AuthManager, table: str, sys_id: str, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Update existing record in ServiceNow table via HTTP PATCH.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        sys_id: Record sys_id to update.
        body: Fields to update.
        
    Returns:
        Updated record dictionary.
    """
    response = requests.patch(
        _table_url(config, table, sys_id),
        headers=_headers(auth_manager),
        params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        json=body,
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _resolve(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    number_field: Optional[str] = "number",
) -> Dict[str, Any]:
    """Resolve request/item identifier to full ServiceNow record via multi-strategy lookup.
    
    Lookup strategy (in order):
    1. If identifier is sys_id format: direct sys_id query
    2. Exact match on number_field with case-insensitive comparison
    3. Fuzzy/LIKE match if exact fails
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        identifier: User-provided identifier (number, sys_id, or pattern).
        number_field: Field containing record number (default 'number').
        
    Returns:
        Resolved record dictionary.
        
    Raises:
        ValueError: If identifier not found or multiple matches ambiguous.
    """
    cleaned_identifier = _clean_identifier(identifier)
    sys_id = _extract_sys_id(cleaned_identifier)
    if sys_id:
        record = _get(config, auth_manager, table, sys_id)
        if record:
            return record
        raise ValueError(f"{table} record not found: {cleaned_identifier}")
    if not number_field:
        raise ValueError(f"{table} records must be referenced by sys_id")
    exact_query = _query(
        [f"{number_field}={value}" for value in _identifier_variants(cleaned_identifier)]
    )
    records = _list(config, auth_manager, table, exact_query, limit=10)
    exact_matches = [
        record
        for record in records
        if _matches_identifier(record.get(number_field), cleaned_identifier)
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ValueError(f"Multiple {table} records matched {cleaned_identifier}; use sys_id")
    if len(records) == 1:
        return records[0]
    if len(records) > 1:
        raise ValueError(f"Multiple {table} records matched {cleaned_identifier}; use sys_id")

    fuzzy_records = _list(
        config, auth_manager, table, f"{number_field}LIKE{cleaned_identifier}", limit=10
    )
    fuzzy_matches = [
        record
        for record in fuzzy_records
        if _matches_identifier(record.get(number_field), cleaned_identifier)
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]
    if len(fuzzy_matches) > 1:
        raise ValueError(f"Multiple {table} records matched {cleaned_identifier}; use sys_id")
    raise ValueError(f"{table} record not found: {cleaned_identifier}")


def _body(params: Any, field_map: Dict[str, str]) -> Dict[str, Any]:
    """Build request body from params with field mapping.
    
    Maps Pydantic model fields to ServiceNow table fields. Skips None values.
    Merges any additional custom attributes from params.attributes.
    
    Args:
        params: Pydantic model with fields to send.
        field_map: Dict mapping param field names to ServiceNow field names.
        
    Returns:
        Request body dict ready for HTTP POST/PATCH.
    """
    body = {
        table_field: getattr(params, model_field)
        for model_field, table_field in field_map.items()
        if getattr(params, model_field, None) is not None
    }
    attributes = getattr(params, "attributes", None)
    if attributes:
        body.update(attributes)
    return body


def _set_if_missing(body: Dict[str, Any], field: str, *values: Any) -> None:
    """Set field in body if not already set, using first non-None value from values.
    
    Args:
        body: Request body dict to modify.
        field: Field name to set.
        *values: Candidate values in priority order.
    """
    if body.get(field) not in (None, ""):
        return
    for value in values:
        if value not in (None, ""):
            body[field] = value
            return


def _ordered_item_label(item: RequestedItemOrderInput) -> str:
    """Generate display label for service catalog item order.
    
    Args:
        item: Requested item details.
        
    Returns:
        Human-readable label (short description, catalog item ID, or generic).
    """
    if item.short_description:
        return item.short_description
    if item.catalog_item:
        return f"Catalog item {item.catalog_item}"
    return "Requested item"


def _derived_request_summary(items: Sequence[RequestedItemOrderInput]) -> Dict[str, str]:
    """Generate request summary from list of ordered items.
    
    Builds short_description and description for multi-item requests.
    
    Args:
        items: List of requested items with quantities.
        
    Returns:
        Dict with 'short_description' and 'description' keys for request body.
    """
    labels: List[str] = []
    for item in items:
        label = _ordered_item_label(item)
        quantity = item.quantity if item.quantity not in (None, 1) else None
        labels.append(f"{quantity} x {label}" if quantity else label)
    if not labels:
        return {}
    if len(labels) == 1:
        short_description = f"Service catalog request: {labels[0]}"
    else:
        short_description = f"Service catalog request: {labels[0]} + {len(labels) - 1} more"
    description = "Ordered through MCP request fulfillment flow:\n" + "\n".join(
        f"- {label}" for label in labels
    )
    return {"short_description": short_description, "description": description}


def _request_body(params: Any) -> Dict[str, Any]:
    """Build request body for service request creation/update.
    
    Args:
        params: CreateRequestParams or UpdateRequestParams.
        
    Returns:
        Mapped request body for Table API POST/PATCH.
    """
    return _body(
        params,
        {
            "requested_for": "requested_for",
            "opened_by": "opened_by",
            "short_description": "short_description",
            "description": "description",
            "state": "state",
            "stage": "stage",
            "approval": "approval",
            "priority": "priority",
            "due_date": "due_date",
            "special_instructions": "special_instructions",
            "comments": "comments",
            "work_notes": "work_notes",
        },
    )


def _ritm_body(params: Any) -> Dict[str, Any]:
    """Build request body for requested item (RITM) creation/update.
    
    Args:
        params: CreateRequestedItemParams or UpdateRequestedItemParams.
        
    Returns:
        Mapped request body for Table API POST/PATCH.
    """
    return _body(
        params,
        {
            "catalog_item": "cat_item",
            "requested_for": "requested_for",
            "opened_by": "opened_by",
            "quantity": "quantity",
            "short_description": "short_description",
            "description": "description",
            "state": "state",
            "stage": "stage",
            "approval": "approval",
            "assignment_group": "assignment_group",
            "assigned_to": "assigned_to",
            "due_date": "due_date",
            "comments": "comments",
            "work_notes": "work_notes",
        },
    )


def _task_body(params: Any) -> Dict[str, Any]:
    """Build request body for request task creation/update.
    
    Args:
        params: CreateRequestTaskParams or UpdateRequestTaskParams.
        
    Returns:
        Mapped request body for Table API POST/PATCH.
    """
    return _body(
        params,
        {
            "short_description": "short_description",
            "description": "description",
            "state": "state",
            "assignment_group": "assignment_group",
            "assigned_to": "assigned_to",
            "due_date": "due_date",
            "comments": "comments",
            "work_notes": "work_notes",
        },
    )


def _build_request_body(params: CreateRequestParams) -> Dict[str, Any]:
    """Build the top-level request payload, deriving a summary from ordered items.

    Args:
        params: Request creation parameters.

    Returns:
        Request body ready for Table API submission.
    """
    body = _request_body(params)
    if params.requested_items:
        derived = _derived_request_summary(params.requested_items)
        _set_if_missing(body, "short_description", derived.get("short_description"))
        _set_if_missing(body, "description", derived.get("description"))
    return body


def _prepare_requested_item_body(params: Any, request_record: Dict[str, Any]) -> Dict[str, Any]:
    """Build a requested-item payload and inherit defaults from the parent request.

    Args:
        params: Requested item creation or update parameters.
        request_record: Parent request record returned by ServiceNow.

    Returns:
        Requested item payload with request linkage and inherited fields.

    Raises:
        ValueError: If the parent request does not expose a sys_id.
    """
    body = _ritm_body(params)
    request_sys_id = _record_sys_id(request_record)
    if not request_sys_id:
        raise ValueError("Request does not have a sys_id")
    body["request"] = request_sys_id
    _set_if_missing(body, "requested_for", _extract_value(request_record.get("requested_for")))
    _set_if_missing(body, "opened_by", _extract_value(request_record.get("opened_by")))
    _set_if_missing(body, "due_date", request_record.get("due_date"))
    return body


def _prepare_catalog_task_body(params: Any, item_record: Dict[str, Any]) -> Dict[str, Any]:
    """Build a catalog task payload and inherit assignment defaults from the item.

    Args:
        params: Catalog task creation or update parameters.
        item_record: Parent requested item record.

    Returns:
        Catalog task payload with request-item linkage and inherited assignments.

    Raises:
        ValueError: If the requested item does not expose a sys_id.
    """
    body = _task_body(params)
    item_sys_id = _record_sys_id(item_record)
    if not item_sys_id:
        raise ValueError("Requested item does not have a sys_id")
    body["request_item"] = item_sys_id
    _set_if_missing(body, "assignment_group", _extract_value(item_record.get("assignment_group")))
    _set_if_missing(body, "assigned_to", _extract_value(item_record.get("assigned_to")))
    _set_if_missing(body, "due_date", item_record.get("due_date"))
    return body


def _catalog_item_variable_definitions(
    config: ServerConfig,
    auth_manager: AuthManager,
    catalog_item_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Fetch variable definitions for a catalog item in display order.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        catalog_item_id: Catalog item sys_id.
        limit: Maximum definitions to fetch.

    Returns:
        Ordered list of catalog variable definition records.
    """
    return _list(
        config,
        auth_manager,
        VARIABLE_DEFINITION_TABLE,
        f"cat_item={catalog_item_id}^ORDERBYorder",
        limit=limit,
    )


def _catalog_variable_choices(
    config: ServerConfig,
    auth_manager: AuthManager,
    variable_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Fetch active choice records for a catalog variable.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        variable_id: Variable definition sys_id.
        limit: Maximum choices to fetch.

    Returns:
        Ordered list of active choice records.
    """
    return _list(
        config,
        auth_manager,
        VARIABLE_CHOICE_TABLE,
        f"question={variable_id}^inactive!=true^ORDERBYorder",
        limit=limit,
    )


def _normalize_match_key(value: Any) -> Optional[str]:
    """Normalize a value for case-insensitive variable matching.

    Args:
        value: Raw value, display mapping, or scalar.

    Returns:
        Trimmed case-folded text, or None when no comparable value exists.
    """
    display_value = _display(value)
    if display_value is None:
        return None
    normalized = str(display_value).strip().casefold()
    return normalized or None


def _resolve_variable_definition(
    definitions: Sequence[Dict[str, Any]],
    identifier: str,
) -> Dict[str, Any]:
    """Resolve a catalog variable definition by sys_id, name, or question text.

    Args:
        definitions: Catalog variable definitions available for the item.
        identifier: User-supplied variable identifier.

    Returns:
        Matching variable definition record.

    Raises:
        ValueError: If no matching variable definition is found.
    """
    cleaned_identifier = _clean_identifier(identifier)
    sys_id = _extract_sys_id(cleaned_identifier)
    if sys_id:
        for definition in definitions:
            if _record_sys_id(definition) == sys_id:
                return definition

    normalized_identifier = cleaned_identifier.casefold()
    for field in ("name", "question_text"):
        for definition in definitions:
            normalized_value = _normalize_match_key(definition.get(field))
            if normalized_value == normalized_identifier:
                return definition

    raise ValueError(f"Catalog variable not found for identifier: {cleaned_identifier}")


def _serialize_variable_value(value: Any) -> str:
    """Serialize a catalog variable input into a single scalar string.

    Args:
        value: Raw variable input, possibly wrapped in mappings.

    Returns:
        String value suitable for ServiceNow variable answer records.

    Raises:
        ValueError: If the input resolves to a non-scalar dict or list.
    """
    flattened_value = _flatten_variable_scalar(value)
    if flattened_value is not value:
        return _serialize_variable_value(flattened_value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        raise ValueError("Catalog variable values must resolve to a single existing scalar value")
    return str(value)


def _is_blank_variable_value(value: str) -> bool:
    """Return whether a serialized catalog variable value is blank."""
    return value == ""


def _value_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """Return a normalized value/display mapping when one can be derived."""
    return _coerce_value_mapping(value)


def _flatten_variable_scalar(value: Any) -> Any:
    """Peel nested value mappings until a stored scalar value is reached.

    Args:
        value: Raw variable value that may contain nested mappings.

    Returns:
        The innermost scalar or the original value when no mapping exists.
    """
    current = value
    for _ in range(5):
        mapping = _value_mapping(current)
        if not mapping:
            return current
        next_value = mapping.get("value")
        if next_value in (None, ""):
            next_value = mapping.get("display_value")
        if next_value is current:
            return current
        current = next_value
    return current


def _flatten_variable_display(value: Any) -> Any:
    """Peel nested value mappings until a display scalar value is reached.

    Args:
        value: Raw variable value that may contain nested mappings.

    Returns:
        The innermost display scalar or the original value when no mapping exists.
    """
    current = value
    for _ in range(5):
        mapping = _value_mapping(current)
        if not mapping:
            return current
        next_value = mapping.get("display_value")
        if next_value in (None, ""):
            next_value = mapping.get("value")
        if next_value is current:
            return current
        current = next_value
    return current


def _choice_match_token(value: Any) -> Optional[str]:
    """Normalize a choice candidate into a comparable token."""
    return _normalize_match_key(value)


def _variable_choice_candidates(raw_value: Any, serialized_value: str) -> List[str]:
    """Generate candidate strings for matching a variable value to choice options.

    Args:
        raw_value: Original variable input.
        serialized_value: Serialized scalar form of the input.

    Returns:
        Ordered unique candidate strings for choice matching.
    """
    candidates: List[str] = []
    for candidate in (
        _extract_value(raw_value),
        _display(raw_value),
        _flatten_variable_scalar(raw_value),
        _flatten_variable_display(raw_value),
        serialized_value,
    ):
        if candidate in (None, ""):
            continue
        candidate_text = str(candidate).strip()
        if candidate_text and candidate_text not in candidates:
            candidates.append(candidate_text)
    return candidates


def _definition_is_mandatory(definition: Dict[str, Any]) -> bool:
    """Return whether a catalog variable definition is marked mandatory."""
    return _read_bool(definition.get("mandatory")) is True


def _definition_default_value(definition: Dict[str, Any]) -> Optional[str]:
    """Extract the default value configured on a variable definition.

    Args:
        definition: Catalog variable definition record.

    Returns:
        Default value as text, or None when no default is set.
    """
    default_value = _extract_value(definition.get("default_value"))
    if default_value in (None, ""):
        return None
    return str(default_value)


def _definition_label(definition: Dict[str, Any], fallback: Optional[str] = None) -> Optional[str]:
    """Choose the best human-readable label for a variable definition.

    Args:
        definition: Catalog variable definition record.
        fallback: Fallback label to use when neither question text nor name exists.

    Returns:
        Best available label text, or the fallback.
    """
    label = _display(definition.get("question_text"))
    if label not in (None, ""):
        return str(label)
    name = _display(definition.get("name"))
    if name not in (None, ""):
        return str(name)
    return fallback


def _variable_choice_value(
    config: ServerConfig,
    auth_manager: AuthManager,
    definition: Dict[str, Any],
    raw_value: Any,
    serialized_value: str,
    choice_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> str:
    if _is_blank_variable_value(serialized_value):
        return serialized_value

    variable_id = _record_sys_id(definition)
    if not variable_id:
        return serialized_value

    choices = None
    if choice_cache is not None:
        choices = choice_cache.get(variable_id)
    if choices is None:
        choices = _catalog_variable_choices(config, auth_manager, variable_id)
        if choice_cache is not None:
            choice_cache[variable_id] = choices
    if not choices:
        return serialized_value

    candidate_tokens = [
        token
        for token in (
            _choice_match_token(candidate)
            for candidate in _variable_choice_candidates(raw_value, serialized_value)
        )
        if token
    ]
    for choice in choices:
        choice_value = _extract_value(choice.get("value"))
        choice_text = _display(choice.get("text"))
        choice_tokens = {
            _choice_match_token(choice_value),
            _choice_match_token(choice_text),
        }
        if any(token in choice_tokens for token in candidate_tokens):
            return str(choice_value if choice_value not in (None, "") else choice_text)

    variable_label = _definition_label(definition, variable_id) or variable_id
    raise ValueError(f"Invalid choice for catalog variable {variable_label}: {serialized_value}")


def _variable_choice_display(
    config: ServerConfig,
    auth_manager: AuthManager,
    definition: Dict[str, Any],
    raw_value: Any,
    serialized_value: str,
    choice_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> str:
    if _is_blank_variable_value(serialized_value):
        return serialized_value

    variable_id = _record_sys_id(definition)
    if not variable_id:
        return serialized_value

    choices = None
    if choice_cache is not None:
        choices = choice_cache.get(variable_id)
    if choices is None:
        choices = _catalog_variable_choices(config, auth_manager, variable_id)
        if choice_cache is not None:
            choice_cache[variable_id] = choices
    if not choices:
        return serialized_value

    candidate_tokens = [
        token
        for token in (
            _choice_match_token(candidate)
            for candidate in _variable_choice_candidates(raw_value, serialized_value)
        )
        if token
    ]
    for choice in choices:
        choice_value = _extract_value(choice.get("value"))
        choice_text = _display(choice.get("text"))
        choice_tokens = {
            _choice_match_token(choice_value),
            _choice_match_token(choice_text),
        }
        if any(token in choice_tokens for token in candidate_tokens):
            return str(choice_text if choice_text not in (None, "") else choice_value)

    variable_label = _definition_label(definition, variable_id) or variable_id
    raise ValueError(f"Invalid choice for catalog variable {variable_label}: {serialized_value}")


def _resolve_requested_item_variables(
    config: ServerConfig,
    auth_manager: AuthManager,
    catalog_item_id: Optional[str],
    variables: Optional[Dict[str, Any]],
    definition_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    choice_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    require_mandatory: bool = False,
) -> List[Dict[str, Any]]:
    """Resolve requested-item variable inputs against catalog definitions and choices.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        catalog_item_id: Catalog item sys_id tied to the requested item.
        variables: User-provided variable inputs keyed by identifier.
        definition_cache: Optional cache of variable definitions by catalog item.
        choice_cache: Optional cache of choice records by variable definition.
        require_mandatory: Whether to enforce mandatory/default variable coverage.

    Returns:
        Resolved variable payloads containing ids, labels, types, and stored/display values.

    Raises:
        ValueError: If catalog context is missing, a variable cannot be resolved, or a mandatory
            variable is missing.
    """
    if not variables and not require_mandatory:
        return []
    if not catalog_item_id:
        if not variables:
            return []
        raise ValueError("Requested item variables require a catalog_item")

    definitions = None
    if definition_cache is not None:
        definitions = definition_cache.get(catalog_item_id)
    if definitions is None:
        definitions = _catalog_item_variable_definitions(config, auth_manager, catalog_item_id)
        if definition_cache is not None:
            definition_cache[catalog_item_id] = definitions

    resolved_variables_by_id: Dict[str, Dict[str, Any]] = {}
    for identifier, raw_value in (variables or {}).items():
        definition = _resolve_variable_definition(definitions, identifier)
        variable_id = _record_sys_id(definition)
        if not variable_id:
            raise ValueError(f"Catalog variable is missing a sys_id: {identifier}")
        serialized_value = _serialize_variable_value(raw_value)
        stored_value = _variable_choice_value(
            config,
            auth_manager,
            definition,
            raw_value,
            serialized_value,
            choice_cache,
        )
        display_value = _variable_choice_display(
            config,
            auth_manager,
            definition,
            raw_value,
            serialized_value,
            choice_cache,
        )
        resolved_variables_by_id[variable_id] = {
            "variable_id": variable_id,
            "name": definition.get("name"),
            "label": _definition_label(definition, variable_id),
            "type": _display(definition.get("type")),
            "stored_value": stored_value,
            "value": display_value,
            "display_value": display_value,
        }

    if require_mandatory:
        for definition in definitions:
            variable_id = _record_sys_id(definition)
            if not variable_id:
                continue
            existing = resolved_variables_by_id.get(variable_id)
            if existing and not _is_blank_variable_value(existing["value"]):
                continue

            default_value = _definition_default_value(definition)
            if default_value is not None:
                stored_default = _variable_choice_value(
                    config,
                    auth_manager,
                    definition,
                    default_value,
                    default_value,
                    choice_cache,
                )
                display_default = _variable_choice_display(
                    config,
                    auth_manager,
                    definition,
                    default_value,
                    default_value,
                    choice_cache,
                )
                resolved_variables_by_id[variable_id] = {
                    "variable_id": variable_id,
                    "name": definition.get("name"),
                    "label": _definition_label(definition, variable_id),
                    "type": _display(definition.get("type")),
                    "stored_value": stored_default,
                    "value": display_default,
                    "display_value": display_default,
                }
                continue

            if _definition_is_mandatory(definition):
                variable_label = _definition_label(definition, variable_id) or variable_id
                raise ValueError(f"Missing mandatory catalog variable: {variable_label}")

    return list(resolved_variables_by_id.values())


def _create_requested_item_variable_answers(
    config: ServerConfig,
    auth_manager: AuthManager,
    item_record: Dict[str, Any],
    resolved_variables: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Create ServiceNow variable answer/link records for a requested item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        item_record: Requested item record that will own the variable answers.
        resolved_variables: Resolved variable payloads returned by
            `_resolve_requested_item_variables`.

    Returns:
        Created variable answer metadata, including answer and link sys_ids.

    Raises:
        ValueError: If the requested item or created variable answer lacks a sys_id.
    """
    item_sys_id = _record_sys_id(item_record)
    if not item_sys_id:
        raise ValueError("Requested item does not have a sys_id")

    created_variables: List[Dict[str, Any]] = []
    for variable in resolved_variables:
        stored_value = variable.get("stored_value", variable["value"])
        value_record = _post(
            config,
            auth_manager,
            VARIABLE_VALUE_TABLE,
            {
                "item_option_new": variable["variable_id"],
                "value": stored_value,
            },
        )
        value_sys_id = _record_sys_id(value_record)
        if not value_sys_id:
            raise ValueError(
                f"Variable answer missing sys_id for "
                f"{variable.get('name') or variable.get('label')}"
            )
        link_record = _post(
            config,
            auth_manager,
            VARIABLE_LINK_TABLE,
            {
                "request_item": item_sys_id,
                "sc_item_option": value_sys_id,
            },
        )
        created_variables.append(
            {
                "variable_id": variable["variable_id"],
                "name": variable.get("name"),
                "label": variable.get("label"),
                "type": variable.get("type"),
                "value": variable.get("value"),
                "display_value": variable.get("display_value", variable.get("value")),
                "stored_value": variable.get("stored_value", variable.get("value")),
                "answer_sys_id": value_sys_id,
                "link_sys_id": _record_sys_id(link_record),
            }
        )
    return created_variables


def _requested_item_variables_by_item(
    config: ServerConfig,
    auth_manager: AuthManager,
    item_records: Sequence[Dict[str, Any]],
    limit: int = 500,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load existing variable answers for a collection of requested items.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        item_records: Requested item records to expand.
        limit: Base fetch limit used for link and value lookups.

    Returns:
        Mapping of requested item sys_id to normalized variable answer payloads.
    """
    item_ids = [_record_sys_id(record) for record in item_records if _record_sys_id(record)]
    if not item_ids:
        return {}

    link_records = _list(
        config,
        auth_manager,
        VARIABLE_LINK_TABLE,
        f"request_itemIN{','.join(item_ids)}",
        limit=max(limit, len(item_ids) * 10),
    )
    option_ids = [
        str(_extract_value(record.get("sc_item_option"))).strip()
        for record in link_records
        if _extract_value(record.get("sc_item_option"))
    ]
    option_records = (
        _list(
            config,
            auth_manager,
            VARIABLE_VALUE_TABLE,
            f"sys_idIN{','.join(option_ids)}",
            limit=max(limit, len(option_ids) or 1),
        )
        if option_ids
        else []
    )
    option_by_id = {
        _record_sys_id(record): record for record in option_records if _record_sys_id(record)
    }

    definition_by_id: Dict[str, Dict[str, Any]] = {}
    choice_cache: Dict[str, List[Dict[str, Any]]] = {}
    for item_record in item_records:
        catalog_item_id = _extract_value(item_record.get("cat_item"))
        if not catalog_item_id:
            continue
        for definition in _catalog_item_variable_definitions(
            config, auth_manager, str(catalog_item_id)
        ):
            definition_sys_id = _record_sys_id(definition)
            if definition_sys_id and definition_sys_id not in definition_by_id:
                definition_by_id[definition_sys_id] = definition

    variables_by_item: Dict[str, List[Dict[str, Any]]] = {item_id: [] for item_id in item_ids}
    for link_record in link_records:
        item_id = _extract_value(link_record.get("request_item"))
        option_id = _extract_value(link_record.get("sc_item_option"))
        if not item_id or not option_id:
            continue
        option_record = option_by_id.get(str(option_id).strip())
        definition = None
        if option_record:
            definition_id = _extract_value(option_record.get("item_option_new"))
            if definition_id is not None:
                definition = definition_by_id.get(str(definition_id).strip())
        variable_id = _record_sys_id(definition)
        if variable_id is None and option_record:
            option_definition_id = _extract_value(option_record.get("item_option_new"))
            variable_id = str(option_definition_id).strip() if option_definition_id else None
        label = _definition_label(definition) if definition else None
        if label is None and option_record:
            label = _display(option_record.get("item_option_new"))
        raw_value = option_record.get("value") if option_record else None
        stored_value = _flatten_variable_scalar(raw_value)
        display_value = _flatten_variable_display(raw_value)
        normalized_value = stored_value
        if definition is not None and stored_value not in (None, ""):
            normalized_value = _variable_choice_display(
                config,
                auth_manager,
                definition,
                stored_value,
                str(stored_value),
                choice_cache,
            )
            display_value = normalized_value
        variables_by_item.setdefault(str(item_id).strip(), []).append(
            {
                "variable_id": variable_id,
                "name": definition.get("name") if definition else None,
                "label": label,
                "type": _display(definition.get("type")) if definition else None,
                "value": normalized_value,
                "display_value": display_value,
                "stored_value": stored_value,
                "answer_sys_id": _record_sys_id(option_record) if option_record else None,
                "link_sys_id": _record_sys_id(link_record),
            }
        )
    return variables_by_item


def _can_load_requested_item_variables(item_records: Sequence[Dict[str, Any]]) -> bool:
    """Return whether any requested item is associated with a catalog item."""
    return any(_extract_value(record.get("cat_item")) for record in item_records)


def _validate_requested_item_inputs(items: Sequence[RequestedItemOrderInput]) -> None:
    """Validate that each requested item supplies enough data to be created.

    Args:
        items: Requested item order inputs from the caller.

    Raises:
        ValueError: If an item lacks both identifying content and required catalog context.
    """
    for index, item in enumerate(items, start=1):
        if any(
            getattr(item, field) for field in ("catalog_item", "short_description", "description")
        ):
            if item.variables and not item.catalog_item:
                raise ValueError(f"Requested item {index} variables require a catalog_item")
            continue
        raise ValueError(
            f"Requested item {index} needs at least a catalog_item, short_description, or "
            f"description"
        )


def _prevalidate_requested_item_variables(
    config: ServerConfig,
    auth_manager: AuthManager,
    items: Sequence[RequestedItemOrderInput],
) -> None:
    """Validate requested-item variables before any request records are created.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        items: Requested items to validate.

    Raises:
        ValueError: If any item contains invalid or incomplete variable values.
    """
    definition_cache: Dict[str, List[Dict[str, Any]]] = {}
    choice_cache: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        _resolve_requested_item_variables(
            config,
            auth_manager,
            item.catalog_item,
            item.variables,
            definition_cache,
            choice_cache,
            require_mandatory=True,
        )


def _normalize_catalog_choice(choice: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize a catalog variable choice record for API responses.

    Args:
        choice: Raw choice record.
        include_raw: Whether to include the raw ServiceNow record.

    Returns:
        Normalized choice payload.
    """
    data = {
        "value": _extract_value(choice.get("value")),
        "label": _display(choice.get("text")),
        "order": choice.get("order"),
    }
    if include_raw:
        data["raw"] = choice
    return data


def _catalog_submission_policy() -> Dict[str, Any]:
    """Describe how request submission interacts with existing catalog metadata."""
    return {
        "uses_existing_catalog_definitions": True,
        "creates_catalog_variable_definitions": False,
        "creates_catalog_variable_choices": False,
        "creates_request_variable_answers_only": True,
    }


def _catalog_item_request_options(
    config: ServerConfig,
    auth_manager: AuthManager,
    catalog_item_id: str,
    include_choices: bool = True,
    include_raw: bool = False,
    definition_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    choice_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Describe the request-time variable options available for a catalog item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        catalog_item_id: Catalog item sys_id.
        include_choices: Whether to expand each variable's allowed choices.
        include_raw: Whether to include raw definition/choice records.
        definition_cache: Optional cache of definitions by catalog item.
        choice_cache: Optional cache of choices by variable definition.

    Returns:
        Catalog item option metadata including variable definitions and submission policy.
    """
    definitions = None
    if definition_cache is not None:
        definitions = definition_cache.get(catalog_item_id)
    if definitions is None:
        definitions = _catalog_item_variable_definitions(config, auth_manager, catalog_item_id)
        if definition_cache is not None:
            definition_cache[catalog_item_id] = definitions

    variables: List[Dict[str, Any]] = []
    mandatory_count = 0
    for definition in definitions:
        variable_id = _record_sys_id(definition)
        is_mandatory = _definition_is_mandatory(definition)
        if is_mandatory:
            mandatory_count += 1
        choices: List[Dict[str, Any]] = []
        if include_choices and variable_id:
            cached_choices = None
            if choice_cache is not None:
                cached_choices = choice_cache.get(variable_id)
            if cached_choices is None:
                cached_choices = _catalog_variable_choices(config, auth_manager, variable_id)
                if choice_cache is not None:
                    choice_cache[variable_id] = cached_choices
            choices = [_normalize_catalog_choice(choice, include_raw) for choice in cached_choices]
        variable_data = {
            "variable_id": variable_id,
            "name": definition.get("name"),
            "label": _display(definition.get("question_text")),
            "type": _display(definition.get("type")),
            "mandatory": is_mandatory,
            "default_value": _extract_value(definition.get("default_value")),
            "help_text": _display(definition.get("help_text")),
            "description": _display(definition.get("description")),
            "order": definition.get("order"),
            "choices": choices,
        }
        if include_raw:
            variable_data["raw"] = definition
        variables.append(variable_data)

    return {
        "catalog_item": {"value": catalog_item_id, "display_value": catalog_item_id},
        "variable_count": len(variables),
        "mandatory_variable_count": mandatory_count,
        "variables": variables,
        "submission_policy": _catalog_submission_policy(),
    }


def _catalog_item_request_options_by_item(
    config: ServerConfig,
    auth_manager: AuthManager,
    item_records: Sequence[Dict[str, Any]],
    include_choices: bool = True,
    include_raw: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Load catalog option metadata for a set of requested items.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        item_records: Requested items to inspect.
        include_choices: Whether to include allowed choices for variables.
        include_raw: Whether to include raw definition/choice records.

    Returns:
        Mapping of requested item sys_id to catalog option metadata.
    """
    definition_cache: Dict[str, List[Dict[str, Any]]] = {}
    choice_cache: Dict[str, List[Dict[str, Any]]] = {}
    result: Dict[str, Dict[str, Any]] = {}
    for item_record in item_records:
        item_sys_id = _record_sys_id(item_record)
        catalog_item_id = _extract_value(item_record.get("cat_item"))
        if not item_sys_id or not catalog_item_id:
            continue
        result[item_sys_id] = _catalog_item_request_options(
            config,
            auth_manager,
            str(catalog_item_id),
            include_choices=include_choices,
            include_raw=include_raw,
            definition_cache=definition_cache,
            choice_cache=choice_cache,
        )
    return result


def _variable_values_map(variables: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten normalized variable payloads into a label-to-value lookup.

    Args:
        variables: Normalized variable payloads.

    Returns:
        Mapping keyed by label, name, or variable id.
    """
    values: Dict[str, Any] = {}
    for variable in variables:
        key = variable.get("label") or variable.get("name") or variable.get("variable_id")
        if key:
            values[str(key)] = variable.get("display_value", variable.get("value"))
    return values


def _norm_request(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize a request record for API responses.

    Args:
        record: Raw ServiceNow request record.
        include_raw: Whether to attach the raw record.

    Returns:
        Normalized request payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "requested_for": _ref(record.get("requested_for")),
        "opened_by": _ref(record.get("opened_by")),
        "state": _display(record.get("state") or record.get("request_state")),
        "stage": _display(record.get("stage")),
        "approval": _display(record.get("approval")),
        "short_description": record.get("short_description"),
        "description": record.get("description"),
        "priority": _display(record.get("priority")),
        "active": _read_bool(record.get("active")),
        "due_date": record.get("due_date"),
        "opened_at": record.get("opened_at"),
        "closed_at": record.get("closed_at"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_ritm(
    record: Dict[str, Any],
    include_raw: bool = False,
    variables: Optional[Sequence[Dict[str, Any]]] = None,
    catalog_item_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalize a requested item record for API responses.

    Args:
        record: Raw ServiceNow requested item record.
        include_raw: Whether to attach the raw record.
        variables: Optional normalized variable answers for the item.
        catalog_item_options: Optional catalog metadata for the item.

    Returns:
        Normalized requested item payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "request": _ref(record.get("request")),
        "catalog_item": _ref(record.get("cat_item")),
        "requested_for": _ref(record.get("requested_for")),
        "opened_by": _ref(record.get("opened_by")),
        "state": _display(record.get("state")),
        "stage": _display(record.get("stage")),
        "approval": _display(record.get("approval")),
        "quantity": record.get("quantity"),
        "price": _display(record.get("price")),
        "short_description": record.get("short_description"),
        "description": record.get("description"),
        "assignment_group": _ref(record.get("assignment_group")),
        "assigned_to": _ref(record.get("assigned_to")),
        "active": _read_bool(record.get("active")),
        "due_date": record.get("due_date"),
        "opened_at": record.get("opened_at"),
        "closed_at": record.get("closed_at"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if variables is not None:
        data["variables"] = list(variables)
        data["variable_values"] = _variable_values_map(variables)
    if catalog_item_options is not None:
        data["catalog_item_options"] = catalog_item_options
    if include_raw:
        data["raw"] = record
    return data


def _norm_task(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize a request task record for API responses.

    Args:
        record: Raw ServiceNow request task record.
        include_raw: Whether to attach the raw record.

    Returns:
        Normalized request task payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "request_item": _ref(record.get("request_item")),
        "state": _display(record.get("state")),
        "short_description": record.get("short_description"),
        "description": record.get("description"),
        "assignment_group": _ref(record.get("assignment_group")),
        "assigned_to": _ref(record.get("assigned_to")),
        "active": _read_bool(record.get("active")),
        "due_date": record.get("due_date"),
        "opened_at": record.get("opened_at"),
        "closed_at": record.get("closed_at"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_approval(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize an approval record for API responses.

    Args:
        record: Raw ServiceNow approval record.
        include_raw: Whether to attach the raw record.

    Returns:
        Normalized approval payload.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "state": _display(record.get("state")),
        "approver": _ref(record.get("approver")),
        "target": _ref(record.get("sysapproval") or record.get("document_id")),
        "source_table": _display(record.get("source_table")),
        "comments": record.get("comments"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _approval_target_sys_id(record: Dict[str, Any]) -> Optional[str]:
    """Extract the target record sys_id from an approval record."""
    target = _extract_value(record.get("sysapproval")) or _extract_value(record.get("document_id"))
    if target is None:
        return None
    return str(target).strip() or None


def _task_request_item_sys_id(record: Dict[str, Any]) -> Optional[str]:
    """Extract the parent requested-item sys_id from a catalog task record."""
    request_item = _extract_value(record.get("request_item"))
    if request_item is None:
        return None
    return str(request_item).strip() or None


def _request(config: ServerConfig, auth_manager: AuthManager, identifier: str) -> Dict[str, Any]:
    """Resolve a request record by number or sys_id."""
    return _resolve(config, auth_manager, REQUEST_TABLE, identifier)


def _ritm(config: ServerConfig, auth_manager: AuthManager, identifier: str) -> Dict[str, Any]:
    """Resolve a requested item record by number or sys_id."""
    return _resolve(config, auth_manager, REQUESTED_ITEM_TABLE, identifier)


def _task(config: ServerConfig, auth_manager: AuthManager, identifier: str) -> Dict[str, Any]:
    """Resolve a catalog task record by number or sys_id."""
    return _resolve(config, auth_manager, CATALOG_TASK_TABLE, identifier)


def _target(
    config: ServerConfig,
    auth_manager: AuthManager,
    identifier: str,
    target_table: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a request-related target record across request, item, and task tables.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        identifier: Request, RITM, SCTASK, or sys_id identifier.
        target_table: Optional explicit table to search first.

    Returns:
        Resolved ServiceNow record.

    Raises:
        ValueError: If the target cannot be resolved in any supported table.
    """
    if target_table:
        return _resolve(config, auth_manager, target_table, identifier)
    upper = identifier.upper()
    if upper.startswith("REQ"):
        return _request(config, auth_manager, identifier)
    if upper.startswith("RITM"):
        return _ritm(config, auth_manager, identifier)
    if upper.startswith("SCTASK"):
        return _task(config, auth_manager, identifier)
    for table in (REQUEST_TABLE, REQUESTED_ITEM_TABLE, CATALOG_TASK_TABLE):
        try:
            return _resolve(config, auth_manager, table, identifier)
        except (ValueError, requests.exceptions.RequestException):
            continue
    raise ValueError(f"Could not resolve target record: {identifier}")


def _ritms_for_request(
    config: ServerConfig, auth_manager: AuthManager, request_sys_id: str, limit: int = 100
) -> List[Dict[str, Any]]:
    """Fetch requested items that belong to a request."""
    return _list(
        config, auth_manager, REQUESTED_ITEM_TABLE, f"request={request_sys_id}", limit=limit
    )


def _tasks_for_ritms(
    config: ServerConfig, auth_manager: AuthManager, ritm_sys_ids: Sequence[str], limit: int = 100
) -> List[Dict[str, Any]]:
    """Fetch catalog tasks associated with one or more requested items."""
    sys_ids = [sys_id for sys_id in ritm_sys_ids if sys_id]
    if not sys_ids:
        return []
    return _list(
        config, auth_manager, CATALOG_TASK_TABLE, f"request_itemIN{','.join(sys_ids)}", limit=limit
    )


def _dedupe(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate records using sys_id when available."""
    seen = set()
    result: List[Dict[str, Any]] = []
    for record in records:
        sys_id = _record_sys_id(record)
        if sys_id and sys_id in seen:
            continue
        if sys_id:
            seen.add(sys_id)
        result.append(record)
    return result


def _approvals_for_targets(
    config: ServerConfig,
    auth_manager: AuthManager,
    target_sys_ids: Sequence[str],
    state: Optional[str] = None,
    approver: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch approval records linked to request, item, or task targets.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        target_sys_ids: Target request-related sys_ids.
        state: Optional approval state filter.
        approver: Optional approver sys_id filter.
        limit: Maximum records to fetch per approval field.

    Returns:
        Deduplicated approval records for the supplied targets.
    """
    sys_ids = [sys_id for sys_id in target_sys_ids if sys_id]
    if not sys_ids:
        return []
    records: List[Dict[str, Any]] = []
    for field in ("sysapproval", "document_id"):
        records.extend(
            _list(
                config,
                auth_manager,
                APPROVAL_TABLE,
                _query(
                    [
                        f"{field}IN{','.join(sys_ids)}",
                        f"state={state}" if state else None,
                        f"approver={approver}" if approver else None,
                    ]
                ),
                limit=limit,
            )
        )
    return _dedupe(records)


def _counts(records: Sequence[Dict[str, Any]], field: str = "state") -> Dict[str, int]:
    """Count records by display-normalized field value."""
    counts: Dict[str, int] = {}
    for record in records:
        value = str(_display(record.get(field)) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _is_open(record: Dict[str, Any]) -> bool:
    """Return whether a request-related record should be treated as still open."""
    if _read_bool(record.get("active")) is False:
        return False
    state = str(_display(record.get("state")) or "").strip().lower()
    return state not in {
        "closed",
        "closed complete",
        "closed incomplete",
        "closed skipped",
        "complete",
        "completed",
        "cancelled",
        "canceled",
        "3",
        "4",
        "7",
    }


def _overall(
    request_record: Dict[str, Any],
    ritms: Sequence[Dict[str, Any]],
    tasks: Sequence[Dict[str, Any]],
    approvals: Sequence[Dict[str, Any]],
) -> str:
    """Derive an overall fulfillment state for a request workflow."""
    approval_states = {
        str(_display(record.get("state")) or "").strip().lower() for record in approvals
    }
    if "rejected" in approval_states:
        return "approval_rejected"
    if approval_states.intersection({"requested", "not requested", "pending"}):
        return "awaiting_approval"
    if any(_is_open(task) for task in tasks):
        return "in_fulfillment"
    if any(_is_open(item) for item in ritms):
        return "items_open"
    if not _is_open(request_record):
        return "complete"
    return "request_open"


def _pending_approval(record: Dict[str, Any]) -> bool:
    """Return whether an approval record is still awaiting action."""
    state = str(_display(record.get("state")) or "").strip().lower()
    return state in {"requested", "not requested", "pending"}


def _rejected_approval(record: Dict[str, Any]) -> bool:
    """Return whether an approval record has been rejected."""
    state = str(_display(record.get("state")) or "").strip().lower()
    return state == "rejected"


def _current_step(
    request_record: Dict[str, Any],
    ritms: Sequence[Dict[str, Any]],
    tasks: Sequence[Dict[str, Any]],
    approvals: Sequence[Dict[str, Any]],
) -> str:
    """Summarize the current user-facing fulfillment step for a request."""
    if any(_rejected_approval(record) for record in approvals):
        return "Approval rejected"
    if any(_pending_approval(record) for record in approvals):
        return "Awaiting approval"
    if any(_is_open(record) for record in tasks):
        return "Catalog task fulfillment in progress"
    if any(_is_open(record) for record in ritms):
        return "Requested item fulfillment in progress"
    if _is_open(request_record):
        return "Request submitted"
    return "Complete"


def _build_blockers(
    tasks: Sequence[Dict[str, Any]], approvals: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Build blocker summaries from rejected approvals and unassigned open tasks."""
    blockers: List[Dict[str, Any]] = []
    for approval in approvals:
        if _rejected_approval(approval):
            blockers.append(
                {
                    "type": "approval_rejected",
                    "approval": _record_sys_id(approval),
                    "approver": _display(approval.get("approver")),
                    "target": _approval_target_sys_id(approval),
                    "state": _display(approval.get("state")),
                }
            )
    for task in tasks:
        if (
            _is_open(task)
            and not _extract_value(task.get("assigned_to"))
            and not _extract_value(task.get("assignment_group"))
        ):
            blockers.append(
                {
                    "type": "unassigned_catalog_task",
                    "task": _record_sys_id(task),
                    "number": task.get("number"),
                    "short_description": task.get("short_description"),
                }
            )
    return blockers


def _item_fulfillment_status(
    item_record: Dict[str, Any],
    tasks: Sequence[Dict[str, Any]],
    approvals: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute fulfillment status for a single requested item."""
    item_approvals = [
        record
        for record in approvals
        if _approval_target_sys_id(record) == _record_sys_id(item_record)
    ]
    return {
        "sys_id": _record_sys_id(item_record),
        "number": item_record.get("number"),
        "catalog_item": _ref(item_record.get("cat_item")),
        "state": _display(item_record.get("state")),
        "stage": _display(item_record.get("stage")),
        "approval": _display(item_record.get("approval")),
        "task_count": len(tasks),
        "open_task_count": sum(1 for task in tasks if _is_open(task)),
        "approval_count": len(item_approvals),
        "pending_approval_count": sum(
            1 for approval in item_approvals if _pending_approval(approval)
        ),
        "overall_state": _overall(item_record, [], tasks, item_approvals),
    }


def _fulfillment_status(
    request_record: Dict[str, Any],
    ritms: Sequence[Dict[str, Any]],
    tasks: Sequence[Dict[str, Any]],
    approvals: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute aggregate fulfillment status for a request and its child records."""
    tasks_by_item: Dict[str, List[Dict[str, Any]]] = {}
    for task in tasks:
        request_item_id = _task_request_item_sys_id(task)
        if not request_item_id:
            continue
        tasks_by_item.setdefault(request_item_id, []).append(task)

    item_summaries = [
        _item_fulfillment_status(
            item_record,
            tasks_by_item.get(_record_sys_id(item_record) or "", []),
            approvals,
        )
        for item_record in ritms
    ]
    return {
        "overall_state": _overall(request_record, ritms, tasks, approvals),
        "current_step": _current_step(request_record, ritms, tasks, approvals),
        "request_state": _display(
            request_record.get("state") or request_record.get("request_state")
        ),
        "request_stage": _display(request_record.get("stage")),
        "request_approval": _display(request_record.get("approval")),
        "requested_item_count": len(ritms),
        "open_requested_item_count": sum(1 for item in ritms if _is_open(item)),
        "catalog_task_count": len(tasks),
        "open_catalog_task_count": sum(1 for task in tasks if _is_open(task)),
        "approval_count": len(approvals),
        "pending_approval_count": sum(1 for approval in approvals if _pending_approval(approval)),
        "rejected_approval_count": sum(1 for approval in approvals if _rejected_approval(approval)),
        "unassigned_task_count": sum(
            1
            for task in tasks
            if _is_open(task)
            and not _extract_value(task.get("assigned_to"))
            and not _extract_value(task.get("assignment_group"))
        ),
        "requested_items_by_state": _counts(ritms),
        "requested_items_by_stage": _counts(ritms, "stage"),
        "catalog_tasks_by_state": _counts(tasks),
        "approvals_by_state": _counts(approvals),
        "blockers": _build_blockers(tasks, approvals),
        "requested_item_progress": item_summaries,
    }


def _create_requested_item_bundle(
    config: ServerConfig,
    auth_manager: AuthManager,
    request_record: Dict[str, Any],
    params: Any,
    require_mandatory: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Create a requested item plus any variable answers and child catalog tasks.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        request_record: Parent request record.
        params: Requested item parameters.
        require_mandatory: Whether catalog mandatory variables must be satisfied.

    Returns:
        Tuple of created item record, created task records, and created variable records.

    Raises:
        ValueError: If the requested item lacks minimum identifying content.
    """
    item_body = _prepare_requested_item_body(params, request_record)
    if not any(item_body.get(field) for field in ("cat_item", "short_description", "description")):
        raise ValueError(
            "Requested items need at least a catalog_item, short_description, or description"
        )
    resolved_variables = _resolve_requested_item_variables(
        config,
        auth_manager,
        item_body.get("cat_item"),
        getattr(params, "variables", None),
        require_mandatory=require_mandatory,
    )
    item_record = _post(config, auth_manager, REQUESTED_ITEM_TABLE, item_body)
    created_variables = _create_requested_item_variable_answers(
        config,
        auth_manager,
        item_record,
        resolved_variables,
    )
    created_tasks: List[Dict[str, Any]] = []
    for task_params in getattr(params, "catalog_tasks", None) or []:
        task_body = _prepare_catalog_task_body(task_params, item_record)
        created_tasks.append(_post(config, auth_manager, CATALOG_TASK_TABLE, task_body))
    return item_record, created_tasks, created_variables


def _approval_target_ids_for_request(
    request_record: Dict[str, Any],
    ritms: Sequence[Dict[str, Any]],
    tasks: Sequence[Dict[str, Any]],
) -> List[str]:
    """Collect all request-related sys_ids that can own approval records."""
    target_ids: List[str] = []
    request_sys_id = _record_sys_id(request_record)
    if request_sys_id:
        target_ids.append(request_sys_id)
    target_ids.extend(_record_sys_id(record) for record in ritms if _record_sys_id(record))
    target_ids.extend(_record_sys_id(record) for record in tasks if _record_sys_id(record))
    return target_ids


def list_requests(
    config: ServerConfig, auth_manager: AuthManager, params: ListRequestsParams
) -> Dict[str, Any]:
    """List requests for ServiceNow MCP workflows.

    Builds a request query from caller filters and returns normalized request
    records with counts and paging metadata.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Request list filters and paging options.

    Returns:
        Result payload containing normalized requests and count metadata.
    """
    query = _query(
        [
            f"state={params.state}" if params.state else None,
            f"stage={params.stage}" if params.stage else None,
            f"approval={params.approval}" if params.approval else None,
            f"requested_for={params.requested_for}" if params.requested_for else None,
            f"opened_by={params.opened_by}" if params.opened_by else None,
            f"active={_coerce_bool(params.active)}" if params.active is not None else None,
            (
                f"numberLIKE{params.query}^ORshort_descriptionLIKE{params.query}^ORdescriptionLIKE{params.query}"
                if params.query
                else None
            ),
            params.encoded_query,
        ]
    )
    try:
        records = _list(config, auth_manager, REQUEST_TABLE, query, params.limit, params.offset)
        requests_list = [_norm_request(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(requests_list)} requests",
            "requests": requests_list,
            "count": len(requests_list),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error listing requests: %s", e)
        return {"success": False, "message": f"Error listing requests: {str(e)}"}


def get_catalog_item_request_options(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetCatalogItemRequestOptionsParams,
) -> Dict[str, Any]:
    """Get catalog item request options for ServiceNow MCP workflows.

    Expands catalog item variable and choice metadata so request builders can
    render the correct form inputs.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog item option lookup parameters.

    Returns:
        Result payload containing normalized catalog item option metadata.
    """
    try:
        options = _catalog_item_request_options(
            config,
            auth_manager,
            params.catalog_item_id,
            include_choices=params.include_choices,
            include_raw=params.include_raw,
        )
        return {
            "success": True,
            "message": "Retrieved catalog item request options",
            "catalog_item_options": options,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting catalog item request options: %s", e)
        return {
            "success": False,
            "message": f"Error getting catalog item request options: {str(e)}",
        }


def get_request(
    config: ServerConfig, auth_manager: AuthManager, params: GetRequestParams
) -> Dict[str, Any]:
    """Get request for ServiceNow MCP workflows.

    Returns the request, its requested items, catalog tasks, approvals, and the
    derived fulfillment status in a single response payload.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Request retrieval options controlling child expansion.

    Returns:
        Result payload containing the normalized request, optional child
        records, fulfillment status, and submission policy.
    """
    try:
        request_record = _request(config, auth_manager, params.request_id)
        request_sys_id = _record_sys_id(request_record)
        if not request_sys_id:
            raise ValueError(f"Request does not have a sys_id: {params.request_id}")
        ritms = _ritms_for_request(config, auth_manager, request_sys_id)
        ritm_ids = [_record_sys_id(record) for record in ritms if _record_sys_id(record)]
        tasks = _tasks_for_ritms(config, auth_manager, ritm_ids)
        approval_target_ids = _approval_target_ids_for_request(request_record, ritms, tasks)
        approvals = _approvals_for_targets(config, auth_manager, approval_target_ids)
        variables_by_item = (
            _requested_item_variables_by_item(config, auth_manager, ritms)
            if params.include_items and _can_load_requested_item_variables(ritms)
            else {}
        )
        options_by_item = (
            _catalog_item_request_options_by_item(
                config, auth_manager, ritms, include_choices=True, include_raw=params.include_raw
            )
            if params.include_items and _can_load_requested_item_variables(ritms)
            else {}
        )
        return {
            "success": True,
            "request": _norm_request(request_record, params.include_raw),
            "requested_items": (
                [
                    _norm_ritm(
                        record,
                        params.include_raw,
                        variables_by_item.get(_record_sys_id(record) or "", []),
                        options_by_item.get(_record_sys_id(record) or ""),
                    )
                    for record in ritms
                ]
                if params.include_items
                else []
            ),
            "catalog_tasks": (
                [_norm_task(record, params.include_raw) for record in tasks]
                if params.include_tasks
                else []
            ),
            "approvals": (
                [_norm_approval(record, params.include_raw) for record in approvals]
                if params.include_approvals
                else []
            ),
            "fulfillment_status": _fulfillment_status(request_record, ritms, tasks, approvals),
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting request: %s", e)
        return {"success": False, "message": f"Error getting request: {str(e)}"}


def create_request(
    config: ServerConfig, auth_manager: AuthManager, params: CreateRequestParams
) -> Dict[str, Any]:
    """Create request for ServiceNow MCP workflows.

    Creates the parent request record first, then provisions requested items,
    catalog tasks, and approvals needed to represent the fulfillment flow.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Request creation parameters, including optional requested items.

    Returns:
        Result payload containing the created request, child records,
        fulfillment status, and submission policy.
    """
    body = _build_request_body(params)
    if not body and not params.requested_items:
        return {
            "success": False,
            "message": "At least one request field or attributes entry is required",
        }
    try:
        _validate_requested_item_inputs(params.requested_items or [])
        _prevalidate_requested_item_variables(config, auth_manager, params.requested_items or [])
        record = _post(config, auth_manager, REQUEST_TABLE, body)
        created_ritms: List[Dict[str, Any]] = []
        created_tasks: List[Dict[str, Any]] = []
        created_variables_by_item: Dict[str, List[Dict[str, Any]]] = {}
        for item_params in params.requested_items or []:
            item_record, task_records, variable_records = _create_requested_item_bundle(
                config,
                auth_manager,
                record,
                item_params,
                require_mandatory=True,
            )
            created_ritms.append(item_record)
            created_tasks.extend(task_records)
            item_sys_id = _record_sys_id(item_record)
            if item_sys_id:
                created_variables_by_item[item_sys_id] = variable_records
        request_sys_id = _record_sys_id(record)
        current_ritms = (
            _ritms_for_request(config, auth_manager, request_sys_id)
            if request_sys_id
            else created_ritms
        )
        current_tasks = (
            _tasks_for_ritms(
                config,
                auth_manager,
                [_record_sys_id(item) for item in current_ritms if _record_sys_id(item)],
            )
            if current_ritms
            else created_tasks
        )
        approvals = (
            _approvals_for_targets(
                config,
                auth_manager,
                _approval_target_ids_for_request(record, current_ritms, current_tasks),
            )
            if request_sys_id
            else []
        )
        variables_by_item = (
            _requested_item_variables_by_item(config, auth_manager, current_ritms)
            if current_ritms and _can_load_requested_item_variables(current_ritms)
            else created_variables_by_item
        )
        options_by_item = (
            _catalog_item_request_options_by_item(
                config, auth_manager, current_ritms, include_choices=True, include_raw=True
            )
            if current_ritms and _can_load_requested_item_variables(current_ritms)
            else {}
        )
        return {
            "success": True,
            "message": f"Created request {record.get('number') or record.get('sys_id')}",
            "request": _norm_request(record, True),
            "requested_items": [
                _norm_ritm(
                    item,
                    True,
                    variables_by_item.get(_record_sys_id(item) or "", []),
                    options_by_item.get(_record_sys_id(item) or ""),
                )
                for item in current_ritms
            ],
            "catalog_tasks": [_norm_task(task, True) for task in current_tasks],
            "approvals": [_norm_approval(record, True) for record in approvals],
            "fulfillment_status": _fulfillment_status(
                record, current_ritms, current_tasks, approvals
            ),
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error creating request: %s", e)
        return {"success": False, "message": f"Error creating request: {str(e)}"}


def update_request(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateRequestParams
) -> Dict[str, Any]:
    """Update request for ServiceNow MCP workflows.

    Applies request-level field updates and can append additional requested
    items while keeping the response fully normalized.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Request update parameters.

    Returns:
        Result payload containing the updated request, child records,
        fulfillment status, and submission policy.
    """
    body = _request_body(params)
    if not body and not params.requested_items:
        return {"success": False, "message": "No request fields supplied to update"}
    try:
        _validate_requested_item_inputs(params.requested_items or [])
        _prevalidate_requested_item_variables(config, auth_manager, params.requested_items or [])
        request_record = _request(config, auth_manager, params.request_id)
        request_sys_id = _record_sys_id(request_record)
        if not request_sys_id:
            raise ValueError(f"Request does not have a sys_id: {params.request_id}")
        record = (
            request_record
            if not body
            else _patch(config, auth_manager, REQUEST_TABLE, request_sys_id, body)
        )
        for item_params in params.requested_items or []:
            _create_requested_item_bundle(
                config,
                auth_manager,
                record,
                item_params,
                require_mandatory=True,
            )
        existing_ritms = _ritms_for_request(config, auth_manager, request_sys_id)
        ritm_ids = [_record_sys_id(item) for item in existing_ritms if _record_sys_id(item)]
        existing_tasks = _tasks_for_ritms(config, auth_manager, ritm_ids)
        approvals = _approvals_for_targets(
            config,
            auth_manager,
            _approval_target_ids_for_request(record, existing_ritms, existing_tasks),
        )
        variables_by_item = (
            _requested_item_variables_by_item(config, auth_manager, existing_ritms)
            if _can_load_requested_item_variables(existing_ritms)
            else {}
        )
        options_by_item = (
            _catalog_item_request_options_by_item(
                config, auth_manager, existing_ritms, include_choices=True, include_raw=True
            )
            if _can_load_requested_item_variables(existing_ritms)
            else {}
        )
        return {
            "success": True,
            "message": f"Updated request {record.get('number') or record.get('sys_id')}",
            "request": _norm_request(record, True),
            "requested_items": [
                _norm_ritm(
                    item,
                    True,
                    variables_by_item.get(_record_sys_id(item) or "", []),
                    options_by_item.get(_record_sys_id(item) or ""),
                )
                for item in existing_ritms
            ],
            "catalog_tasks": [_norm_task(task, True) for task in existing_tasks],
            "approvals": [_norm_approval(record, True) for record in approvals],
            "fulfillment_status": _fulfillment_status(
                record, existing_ritms, existing_tasks, approvals
            ),
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating request: %s", e)
        return {"success": False, "message": f"Error updating request: {str(e)}"}


def list_requested_items(
    config: ServerConfig, auth_manager: AuthManager, params: ListRequestedItemsParams
) -> Dict[str, Any]:
    """List requested items for ServiceNow MCP workflows.

    Supports filtering by parent request or item state and returns normalized
    RITM records with paging metadata.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Requested-item list filters and paging options.

    Returns:
        Result payload containing normalized requested items and count metadata.
    """
    query_parts: List[Optional[str]] = [
        f"state={params.state}" if params.state else None,
        f"stage={params.stage}" if params.stage else None,
        f"approval={params.approval}" if params.approval else None,
        f"assigned_to={params.assigned_to}" if params.assigned_to else None,
        f"assignment_group={params.assignment_group}" if params.assignment_group else None,
        (
            f"numberLIKE{params.query}^ORshort_descriptionLIKE{params.query}^ORdescriptionLIKE{params.query}"
            if params.query
            else None
        ),
        params.encoded_query,
    ]
    try:
        if params.request_id:
            request_record = _request(config, auth_manager, params.request_id)
            request_sys_id = _record_sys_id(request_record)
            if not request_sys_id:
                raise ValueError(f"Request does not have a sys_id: {params.request_id}")
            query_parts.insert(0, f"request={request_sys_id}")
        records = _list(
            config,
            auth_manager,
            REQUESTED_ITEM_TABLE,
            _query(query_parts),
            params.limit,
            params.offset,
        )
        items = [_norm_ritm(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(items)} requested items",
            "requested_items": items,
            "count": len(items),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error listing requested items: %s", e)
        return {"success": False, "message": f"Error listing requested items: {str(e)}"}


def get_requested_item(
    config: ServerConfig, auth_manager: AuthManager, params: GetRequestedItemParams
) -> Dict[str, Any]:
    """Get requested item for ServiceNow MCP workflows.

    Returns the requested item together with optional tasks, approvals, and the
    computed fulfillment status.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Requested-item retrieval options controlling child expansion.

    Returns:
        Result payload containing the normalized requested item, optional child
        records, fulfillment status, and submission policy.
    """
    try:
        item_record = _ritm(config, auth_manager, params.requested_item_id)
        item_sys_id = _record_sys_id(item_record)
        if not item_sys_id:
            raise ValueError(f"Requested item does not have a sys_id: {params.requested_item_id}")
        tasks = _tasks_for_ritms(config, auth_manager, [item_sys_id])
        approval_target_ids = [item_sys_id] + [
            _record_sys_id(task) for task in tasks if _record_sys_id(task)
        ]
        approvals = _approvals_for_targets(config, auth_manager, approval_target_ids)
        variables = (
            _requested_item_variables_by_item(config, auth_manager, [item_record]).get(
                item_sys_id, []
            )
            if _can_load_requested_item_variables([item_record])
            else []
        )
        options = (
            _catalog_item_request_options_by_item(
                config,
                auth_manager,
                [item_record],
                include_choices=True,
                include_raw=params.include_raw,
            ).get(item_sys_id)
            if _can_load_requested_item_variables([item_record])
            else None
        )
        return {
            "success": True,
            "requested_item": _norm_ritm(item_record, params.include_raw, variables, options),
            "catalog_tasks": (
                [_norm_task(record, params.include_raw) for record in tasks]
                if params.include_tasks
                else []
            ),
            "approvals": (
                [_norm_approval(record, params.include_raw) for record in approvals]
                if params.include_approvals
                else []
            ),
            "fulfillment_status": _item_fulfillment_status(item_record, tasks, approvals),
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting requested item: %s", e)
        return {"success": False, "message": f"Error getting requested item: {str(e)}"}


def create_requested_item(
    config: ServerConfig, auth_manager: AuthManager, params: CreateRequestedItemParams
) -> Dict[str, Any]:
    """Create requested item for ServiceNow MCP workflows.

    Creates a RITM bundle from the selected catalog item and returns the item,
    tasks, approvals, and fulfillment summary.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Requested-item creation parameters.

    Returns:
        Result payload containing the created requested item, child tasks,
        approvals, fulfillment status, and submission policy.
    """
    try:
        request_record = _request(config, auth_manager, params.request_id)
        record, task_records, created_variables = _create_requested_item_bundle(
            config,
            auth_manager,
            request_record,
            params,
            require_mandatory=True,
        )
        item_sys_id = _record_sys_id(record)
        current_tasks = (
            _tasks_for_ritms(config, auth_manager, [item_sys_id]) if item_sys_id else task_records
        )
        approvals = (
            _approvals_for_targets(
                config,
                auth_manager,
                [item_sys_id]
                + [_record_sys_id(task) for task in current_tasks if _record_sys_id(task)],
            )
            if item_sys_id
            else []
        )
        variables = (
            _requested_item_variables_by_item(config, auth_manager, [record]).get(item_sys_id, [])
            if item_sys_id and _can_load_requested_item_variables([record])
            else created_variables
        )
        options = (
            _catalog_item_request_options_by_item(
                config, auth_manager, [record], include_choices=True, include_raw=True
            ).get(item_sys_id)
            if item_sys_id and _can_load_requested_item_variables([record])
            else None
        )
        return {
            "success": True,
            "message": f"Created requested item {record.get('number') or record.get('sys_id')}",
            "requested_item": _norm_ritm(record, True, variables, options),
            "catalog_tasks": [_norm_task(task, True) for task in current_tasks],
            "approvals": [_norm_approval(record, True) for record in approvals],
            "fulfillment_status": _item_fulfillment_status(record, current_tasks, approvals),
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error creating requested item: %s", e)
        return {"success": False, "message": f"Error creating requested item: {str(e)}"}


def update_requested_item(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateRequestedItemParams
) -> Dict[str, Any]:
    """Update requested item for ServiceNow MCP workflows.

    Applies requested item changes, updates variables when supplied, and keeps
    the catalog-task view aligned with the request state.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Requested-item update parameters.

    Returns:
        Result payload containing the updated requested item, child tasks,
        approvals, fulfillment status, and submission policy.
    """
    body = _ritm_body(params)
    try:
        if params.request_id:
            request_record = _request(config, auth_manager, params.request_id)
            request_sys_id = _record_sys_id(request_record)
            if not request_sys_id:
                raise ValueError(f"Request does not have a sys_id: {params.request_id}")
            body["request"] = request_sys_id
        if not body and not params.catalog_tasks and not params.variables:
            return {"success": False, "message": "No requested item fields supplied to update"}
        item_record = _ritm(config, auth_manager, params.requested_item_id)
        item_sys_id = _record_sys_id(item_record)
        if not item_sys_id:
            raise ValueError(f"Requested item does not have a sys_id: {params.requested_item_id}")
        resolved_variables = _resolve_requested_item_variables(
            config,
            auth_manager,
            params.catalog_item or _extract_value(item_record.get("cat_item")),
            params.variables,
        )
        record = (
            item_record
            if not body
            else _patch(config, auth_manager, REQUESTED_ITEM_TABLE, item_sys_id, body)
        )
        if resolved_variables:
            _create_requested_item_variable_answers(
                config, auth_manager, record, resolved_variables
            )
        created_tasks: List[Dict[str, Any]] = []
        for task_params in params.catalog_tasks or []:
            task_body = _prepare_catalog_task_body(task_params, record)
            created_tasks.append(_post(config, auth_manager, CATALOG_TASK_TABLE, task_body))
        all_tasks = _tasks_for_ritms(config, auth_manager, [item_sys_id])
        approval_target_ids = [item_sys_id] + [
            _record_sys_id(task) for task in all_tasks if _record_sys_id(task)
        ]
        approvals = _approvals_for_targets(config, auth_manager, approval_target_ids)
        variables = (
            _requested_item_variables_by_item(config, auth_manager, [record]).get(item_sys_id, [])
            if _can_load_requested_item_variables([record])
            else []
        )
        options = (
            _catalog_item_request_options_by_item(
                config, auth_manager, [record], include_choices=True, include_raw=True
            ).get(item_sys_id)
            if _can_load_requested_item_variables([record])
            else None
        )
        return {
            "success": True,
            "message": f"Updated requested item {record.get('number') or record.get('sys_id')}",
            "requested_item": _norm_ritm(record, True, variables, options),
            "catalog_tasks": [_norm_task(task, True) for task in all_tasks],
            "approvals": [_norm_approval(record, True) for record in approvals],
            "fulfillment_status": _item_fulfillment_status(record, all_tasks, approvals),
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating requested item: %s", e)
        return {"success": False, "message": f"Error updating requested item: {str(e)}"}


def list_catalog_tasks(
    config: ServerConfig, auth_manager: AuthManager, params: ListCatalogTasksParams
) -> Dict[str, Any]:
    """List catalog tasks for request-fulfillment workflows.

    Supports filtering by parent request, requested item, assignment, and task
    state while returning normalized task records with paging metadata.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog task list filters and paging options.

    Returns:
        Result payload containing normalized catalog tasks and count metadata.
    """
    query_parts: List[Optional[str]] = [
        f"state={params.state}" if params.state else None,
        f"assigned_to={params.assigned_to}" if params.assigned_to else None,
        f"assignment_group={params.assignment_group}" if params.assignment_group else None,
        f"active={_coerce_bool(params.active)}" if params.active is not None else None,
        (
            f"numberLIKE{params.query}^ORshort_descriptionLIKE{params.query}^ORdescriptionLIKE{params.query}"
            if params.query
            else None
        ),
        params.encoded_query,
    ]
    try:
        if params.requested_item_id:
            item_record = _ritm(config, auth_manager, params.requested_item_id)
            item_sys_id = _record_sys_id(item_record)
            if not item_sys_id:
                raise ValueError(
                    f"Requested item does not have a sys_id: {params.requested_item_id}"
                )
            query_parts.insert(0, f"request_item={item_sys_id}")
        elif params.request_id:
            request_record = _request(config, auth_manager, params.request_id)
            request_sys_id = _record_sys_id(request_record)
            if not request_sys_id:
                raise ValueError(f"Request does not have a sys_id: {params.request_id}")
            ritms = _ritms_for_request(config, auth_manager, request_sys_id)
            ritm_ids = [_record_sys_id(record) for record in ritms if _record_sys_id(record)]
            if not ritm_ids:
                return {
                    "success": True,
                    "message": "No catalog tasks found because the request has no requested items",
                    "catalog_tasks": [],
                    "count": 0,
                    "limit": params.limit,
                    "offset": params.offset,
                }
            query_parts.insert(0, f"request_itemIN{','.join(ritm_ids)}")
        records = _list(
            config,
            auth_manager,
            CATALOG_TASK_TABLE,
            _query(query_parts),
            params.limit,
            params.offset,
        )
        tasks = [_norm_task(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(tasks)} catalog tasks",
            "catalog_tasks": tasks,
            "count": len(tasks),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error listing catalog tasks: %s", e)
        return {"success": False, "message": f"Error listing catalog tasks: {str(e)}"}


def create_catalog_task(
    config: ServerConfig, auth_manager: AuthManager, params: CreateCatalogTaskParams
) -> Dict[str, Any]:
    """Create a catalog task under an existing requested item.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog task creation parameters.

    Returns:
        Result payload containing the created normalized catalog task.
    """
    try:
        item_record = _ritm(config, auth_manager, params.requested_item_id)
        body = _prepare_catalog_task_body(params, item_record)
        record = _post(config, auth_manager, CATALOG_TASK_TABLE, body)
        return {
            "success": True,
            "message": f"Created catalog task {record.get('number') or record.get('sys_id')}",
            "catalog_task": _norm_task(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error creating catalog task: %s", e)
        return {"success": False, "message": f"Error creating catalog task: {str(e)}"}


def update_catalog_task(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateCatalogTaskParams
) -> Dict[str, Any]:
    """Update catalog task fields with optional requested-item reassociation.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Catalog task update parameters.

    Returns:
        Result payload containing the updated normalized catalog task.
    """
    body = _task_body(params)
    try:
        if params.requested_item_id:
            item_record = _ritm(config, auth_manager, params.requested_item_id)
            item_sys_id = _record_sys_id(item_record)
            if not item_sys_id:
                raise ValueError(
                    f"Requested item does not have a sys_id: {params.requested_item_id}"
                )
            body["request_item"] = item_sys_id
        if not body:
            return {"success": False, "message": "No catalog task fields supplied to update"}
        task_record = _task(config, auth_manager, params.task_id)
        task_sys_id = _record_sys_id(task_record)
        if not task_sys_id:
            raise ValueError(f"Catalog task does not have a sys_id: {params.task_id}")
        record = _patch(config, auth_manager, CATALOG_TASK_TABLE, task_sys_id, body)
        return {
            "success": True,
            "message": f"Updated catalog task {record.get('number') or record.get('sys_id')}",
            "catalog_task": _norm_task(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating catalog task: %s", e)
        return {"success": False, "message": f"Error updating catalog task: {str(e)}"}


def list_request_approvals(
    config: ServerConfig, auth_manager: AuthManager, params: ListRequestApprovalsParams
) -> Dict[str, Any]:
    """List approvals for requests and related fulfillment records.

    Can scope approvals to a specific request, requested item, or catalog task,
    and optionally expand to child requested items and catalog tasks.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Approval list filters, target scope, and paging options.

    Returns:
        Result payload containing normalized approval records and count metadata.
    """
    try:
        if params.target_id:
            target = _target(config, auth_manager, params.target_id, params.target_table)
            target_sys_id = _record_sys_id(target)
            if not target_sys_id:
                raise ValueError(f"Target record does not have a sys_id: {params.target_id}")
            target_ids = [target_sys_id]
            number = str(target.get("number") or "").upper()
            target_table = params.target_table or (
                REQUEST_TABLE
                if number.startswith("REQ")
                else (
                    REQUESTED_ITEM_TABLE
                    if number.startswith("RITM")
                    else CATALOG_TASK_TABLE if number.startswith("SCTASK") else None
                )
            )
            if target_table == REQUEST_TABLE:
                ritms = _ritms_for_request(config, auth_manager, target_sys_id)
                ritm_ids = [_record_sys_id(record) for record in ritms if _record_sys_id(record)]
                if params.include_requested_items:
                    target_ids.extend(ritm_ids)
                if params.include_catalog_tasks:
                    target_ids.extend(
                        _record_sys_id(record)
                        for record in _tasks_for_ritms(config, auth_manager, ritm_ids)
                        if _record_sys_id(record)
                    )
            elif target_table == REQUESTED_ITEM_TABLE and params.include_catalog_tasks:
                target_ids.extend(
                    _record_sys_id(record)
                    for record in _tasks_for_ritms(config, auth_manager, [target_sys_id])
                    if _record_sys_id(record)
                )
            records = _approvals_for_targets(
                config, auth_manager, target_ids, params.state, params.approver, params.limit
            )
        else:
            records = _list(
                config,
                auth_manager,
                APPROVAL_TABLE,
                _query(
                    [
                        f"state={params.state}" if params.state else None,
                        f"approver={params.approver}" if params.approver else None,
                        params.encoded_query,
                    ]
                ),
                params.limit,
                params.offset,
            )
        approvals = [_norm_approval(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(approvals)} approvals",
            "approvals": approvals,
            "count": len(approvals),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error listing request approvals: %s", e)
        return {"success": False, "message": f"Error listing request approvals: {str(e)}"}


def update_request_approval(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateRequestApprovalParams
) -> Dict[str, Any]:
    """Apply approve or reject state updates to a specific approval record.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Approval update parameters.

    Returns:
        Result payload containing the updated normalized approval record.
    """
    try:
        approval_sys_id = _extract_sys_id(params.approval_id)
        if not approval_sys_id:
            raise ValueError("Approval records must be referenced by sys_id")
        body: Dict[str, Any] = {"state": params.state}
        if params.comments is not None:
            body["comments"] = params.comments
        if params.attributes:
            body.update(params.attributes)
        record = _patch(config, auth_manager, APPROVAL_TABLE, approval_sys_id, body)
        return {
            "success": True,
            "message": f"Updated approval {record.get('sys_id')}",
            "approval": _norm_approval(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating request approval: %s", e)
        return {"success": False, "message": f"Error updating request approval: {str(e)}"}


def get_request_fulfillment_status(
    config: ServerConfig, auth_manager: AuthManager, params: GetRequestFulfillmentStatusParams
) -> Dict[str, Any]:
    """Return a synthesized fulfillment-status snapshot for a request.

    Loads the parent request, requested items, catalog tasks, and approvals,
    then derives a consolidated workflow-status summary for downstream agents.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Request fulfillment status parameters.

    Returns:
        Result payload containing the normalized request, child records, and
        derived fulfillment status summary.
    """
    try:
        request_record = _request(config, auth_manager, params.request_id)
        request_sys_id = _record_sys_id(request_record)
        if not request_sys_id:
            raise ValueError(f"Request does not have a sys_id: {params.request_id}")
        ritms = _ritms_for_request(config, auth_manager, request_sys_id)
        ritm_ids = [_record_sys_id(record) for record in ritms if _record_sys_id(record)]
        tasks = _tasks_for_ritms(config, auth_manager, ritm_ids)
        approvals = _approvals_for_targets(
            config, auth_manager, _approval_target_ids_for_request(request_record, ritms, tasks)
        )
        status = _fulfillment_status(request_record, ritms, tasks, approvals)
        variables_by_item = (
            _requested_item_variables_by_item(config, auth_manager, ritms)
            if _can_load_requested_item_variables(ritms)
            else {}
        )
        options_by_item = (
            _catalog_item_request_options_by_item(
                config, auth_manager, ritms, include_choices=True, include_raw=params.include_raw
            )
            if _can_load_requested_item_variables(ritms)
            else {}
        )
        return {
            "success": True,
            "message": "Retrieved request fulfillment status",
            "request": _norm_request(request_record, params.include_raw),
            "status": status,
            "requested_items": [
                _norm_ritm(
                    record,
                    params.include_raw,
                    variables_by_item.get(_record_sys_id(record) or "", []),
                    options_by_item.get(_record_sys_id(record) or ""),
                )
                for record in ritms
            ],
            "catalog_tasks": [_norm_task(record, params.include_raw) for record in tasks],
            "approvals": [_norm_approval(record, params.include_raw) for record in approvals],
            "submission_policy": _catalog_submission_policy(),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting request fulfillment status: %s", e)
        return {"success": False, "message": f"Error getting request fulfillment status: {str(e)}"}
