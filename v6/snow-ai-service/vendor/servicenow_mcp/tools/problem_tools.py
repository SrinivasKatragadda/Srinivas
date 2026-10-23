"""Problem Management tools for ServiceNow MCP.

This module exposes the problem-management workflows used by the MCP server:
listing problems, retrieving problem details, creating problem records, and
managing linked tasks and incidents. It acts as the administrative layer for
problem records rather than the end-user fulfillment flow.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

PROBLEM_TABLE = "problem"
PROBLEM_TASK_TABLE = "problem_task"
INCIDENT_TABLE = "incident"
DEFAULT_KNOWN_ERROR_TABLE = "kb_knowledge"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class ListProblemsParams(BaseModel):
    """Parameters for List Problems tool calls."""
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    state: Optional[str] = Field(None, description="Problem state")
    known_error: Optional[bool] = Field(None, description="Filter by known error flag")
    assigned_to: Optional[str] = Field(None, description="Assigned-to user sys_id")
    assignment_group: Optional[str] = Field(None, description="Assignment group sys_id")
    category: Optional[str] = None
    priority: Optional[str] = None
    active: Optional[bool] = Field(None, description="Active filter")
    query: Optional[str] = Field(None, description="Search number/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetProblemParams(BaseModel):
    """Parameters for Get Problem tool calls."""
    problem_id: str = Field(..., description="Problem sys_id, sys_id:<id>, or PRB number")
    include_tasks: bool = Field(True, description="Include problem tasks")
    include_incidents: bool = Field(True, description="Include linked incidents")
    include_raw: bool = Field(False, description="Include raw records")


class CreateProblemParams(BaseModel):
    """Parameters for Create Problem tool calls."""
    short_description: str = Field(..., description="Short description")
    description: Optional[str] = None
    state: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    business_service: Optional[str] = Field(None, description="Business service sys_id")
    cmdb_ci: Optional[str] = Field(None, description="Configuration item sys_id")
    impact: Optional[str] = None
    urgency: Optional[str] = None
    priority: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    known_error: Optional[bool] = Field(None, description="Mark as known error")
    workaround: Optional[str] = None
    cause_notes: Optional[str] = None
    fix_notes: Optional[str] = None
    related_incident_id: Optional[str] = Field(
        None, description="Incident sys_id or INC number to link"
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem fields")


class UpdateProblemParams(BaseModel):
    """Parameters for Update Problem tool calls."""
    problem_id: str = Field(..., description="Problem sys_id, sys_id:<id>, or PRB number")
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    business_service: Optional[str] = None
    cmdb_ci: Optional[str] = None
    impact: Optional[str] = None
    urgency: Optional[str] = None
    priority: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    known_error: Optional[bool] = None
    workaround: Optional[str] = None
    cause_notes: Optional[str] = None
    fix_notes: Optional[str] = None
    close_code: Optional[str] = None
    close_notes: Optional[str] = None
    comments: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem fields")


class ResolveProblemParams(BaseModel):
    """Parameters for Resolve Problem tool calls."""
    problem_id: str = Field(..., description="Problem sys_id, sys_id:<id>, or PRB number")
    resolution_notes: str = Field(..., description="Resolution or fix notes")
    resolution_code: Optional[str] = Field(None, description="Resolution/close code")
    state: str = Field(
        "5", description="Resolved state value for the instance; common default is 5"
    )
    fix_notes: Optional[str] = None
    close_notes: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem fields")


class ListProblemTasksParams(BaseModel):
    """Parameters for List Problem Tasks tool calls."""
    problem_id: Optional[str] = Field(None, description="Problem sys_id or PRB number")
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    state: Optional[str] = None
    assigned_to: Optional[str] = None
    assignment_group: Optional[str] = None
    active: Optional[bool] = Field(None, description="Active filter")
    query: Optional[str] = Field(None, description="Search number/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetProblemTaskParams(BaseModel):
    """Parameters for Get Problem Task tool calls."""
    task_id: str = Field(..., description="Problem task sys_id, sys_id:<id>, or PTASK number")
    include_raw: bool = Field(False, description="Include raw record")


class CreateProblemTaskParams(BaseModel):
    """Parameters for Create Problem Task tool calls."""
    problem_id: str = Field(..., description="Problem sys_id, sys_id:<id>, or PRB number")
    short_description: str = Field(..., description="Short description")
    description: Optional[str] = None
    state: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem_task fields")


class UpdateProblemTaskParams(BaseModel):
    """Parameters for Update Problem Task tool calls."""
    task_id: str = Field(..., description="Problem task sys_id, sys_id:<id>, or PTASK number")
    problem_id: Optional[str] = Field(None, description="New problem sys_id or PRB number")
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    comments: Optional[str] = None
    work_notes: Optional[str] = None
    close_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem_task fields")


class ResolveProblemTaskParams(BaseModel):
    """Parameters for Resolve Problem Task tool calls."""
    task_id: str = Field(..., description="Problem task sys_id, sys_id:<id>, or PTASK number")
    resolution_notes: str = Field(..., description="Resolution notes")
    state: str = Field(
        "3", description="Closed/complete state value for the instance; common default is 3"
    )
    close_notes: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem_task fields")


class LinkProblemToIncidentParams(BaseModel):
    """Parameters for Link Problem To Incident tool calls."""
    problem_id: str = Field(..., description="Problem sys_id, sys_id:<id>, or PRB number")
    incident_id: str = Field(..., description="Incident sys_id, sys_id:<id>, or INC number")
    incident_problem_field: str = Field(
        "problem_id", description="Incident field that references problem"
    )
    add_work_notes: bool = Field(True, description="Add work notes on the incident and problem")
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional incident fields")


class LinkProblemToKnownErrorParams(BaseModel):
    """Parameters for Link Problem To Known Error tool calls."""
    problem_id: str = Field(..., description="Problem sys_id, sys_id:<id>, or PRB number")
    known_error_id: Optional[str] = Field(None, description="Known error record sys_id or number")
    known_error_table: str = Field(DEFAULT_KNOWN_ERROR_TABLE, description="Known error table")
    known_error_field: Optional[str] = Field(
        None, description="Problem reference field for the known error record, if customized"
    )
    mark_known_error: bool = Field(True, description="Set problem.known_error to true")
    workaround: Optional[str] = Field(None, description="Known workaround text")
    cause_notes: Optional[str] = None
    work_notes: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional problem fields")


def _table_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    """Build ServiceNow Table API URL for the given table and optional sys_id.
    
    Args:
        config: Server configuration containing instance URL.
        table: Table name (e.g., 'problem', 'incident').
        sys_id: Optional record sys_id for single-record endpoints.
        
    Returns:
        Full URL for Table API endpoint.
    """
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _headers(auth_manager: AuthManager) -> Dict[str, str]:
    """Build HTTP headers for ServiceNow API requests with authentication.
    
    Args:
        auth_manager: Authentication manager providing authorization headers.
        
    Returns:
        Dictionary of headers with auth, Accept, and Content-Type for JSON.
    """
    headers = auth_manager.get_headers()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    return headers


def _display(value: Any) -> Any:
    """Extract display value from ServiceNow reference object or return value as-is.
    
    ServiceNow returns reference fields as {"value": sys_id, "display_value": label}.
    This extracts the human-readable display value with fallback to value field.
    
    Args:
        value: ServiceNow field value (dict reference or scalar).
        
    Returns:
        Display value for references, or original value for scalars.
    """
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _clean_identifier(identifier: str) -> str:
    """Validate and clean a record identifier (problem number, sys_id, etc.).
    
    Args:
        identifier: User-provided record identifier.
        
    Returns:
        Cleaned identifier (whitespace trimmed).
        
    Raises:
        ValueError: If identifier is empty or whitespace-only.
    """
    cleaned = str(identifier).strip()
    if not cleaned:
        raise ValueError("Record identifier cannot be empty")
    return cleaned


def _identifier_variants(identifier: str) -> List[str]:
    """Generate case variants of an identifier for lenient matching.
    
    Args:
        identifier: Original identifier (case-sensitive).
        
    Returns:
        List of variants: [original, UPPERCASE, lowercase] with deduplication.
    """
    variants: List[str] = []
    for candidate in (identifier, identifier.upper(), identifier.lower()):
        if candidate not in variants:
            variants.append(candidate)
    return variants


def _matches_identifier(value: Any, identifier: str) -> bool:
    """Check if a field value matches an identifier (case-insensitive).
    
    Args:
        value: ServiceNow field value (reference or scalar).
        identifier: User-provided identifier to match against.
        
    Returns:
        True if display value matches identifier (case-insensitive).
    """
    display_value = _display(value)
    if display_value is None:
        return False
    return str(display_value).strip().casefold() == identifier.casefold()


def _extract_value(value: Any) -> Any:
    """Extract value (sys_id) from ServiceNow reference object or return as-is.
    
    Args:
        value: ServiceNow field value (reference or scalar).
        
    Returns:
        Underlying value (sys_id) for references, or original value for scalars.
    """
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    """Extract sys_id from ServiceNow record with normalization.
    
    Args:
        record: ServiceNow record dictionary.
        
    Returns:
        sys_id value (trimmed) or None if missing/empty.
    """
    sys_id = _extract_value(record.get("sys_id"))
    if sys_id is None:
        return None
    return str(sys_id).strip() or None


def _ref(value: Any) -> Dict[str, Any]:
    """Normalize value into ServiceNow reference object format.
    
    Args:
        value: Value (reference dict or scalar sys_id).
        
    Returns:
        Dictionary with 'value' (sys_id) and 'display_value' fields.
    """
    if isinstance(value, dict):
        return {
            "value": value.get("value"),
            "display_value": value.get("display_value") or value.get("value"),
        }
    return {"value": value, "display_value": value}


def _read_bool(value: Any) -> Optional[bool]:
    """Parse boolean from ServiceNow field value (bool, string, or reference).
    
    Accepts: true/false (bool), '1'/'0', 'yes'/'no', 'true'/'false' (string, case-insensitive).
    
    Args:
        value: ServiceNow field value to parse.
        
    Returns:
        Boolean interpretation or None if unparseable.
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
    """Convert Python boolean to ServiceNow string representation.
    
    Args:
        value: Python boolean value.
        
    Returns:
        'true' or 'false' string, or None if input is None.
    """
    if value is None:
        return None
    return "true" if value else "false"


def _extract_sys_id(identifier: str) -> Optional[str]:
    """Extract sys_id from identifier if explicitly marked or pattern-matched.
    
    Supports: direct 32-char hex sys_id or 'sys_id:HEXVALUE' prefix.
    
    Args:
        identifier: Identifier that may contain sys_id.
        
    Returns:
        Valid sys_id string or None if not a sys_id format.
    """
    cleaned = _clean_identifier(identifier)
    if cleaned.startswith("sys_id:"):
        cleaned = cleaned.replace("sys_id:", "", 1).strip()
    return cleaned if SYS_ID_PATTERN.fullmatch(cleaned) else None


def _query(parts: Sequence[Optional[str]]) -> str:
    """Build ServiceNow query string from multiple optional parts joined by OR.
    
    Args:
        parts: List of query clauses (e.g., ['number=PRB0001', 'number=prb0001']).
        
    Returns:
        Combined query with '^' (OR) joining non-empty parts.
    """
    return "^".join(part for part in parts if part)


def _get(
    config: ServerConfig, auth_manager: AuthManager, table: str, sys_id: str
) -> Dict[str, Any]:
    """Fetch single record from ServiceNow table by sys_id.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        sys_id: Record sys_id.
        
    Returns:
        Record dictionary (empty dict if not found).
    """
    response = requests.get(
        _table_url(config, table, sys_id),
        headers=_headers(auth_manager),
        params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _list(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Fetch multiple records from ServiceNow table with optional query.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        query: Optional ServiceNow query string (e.g., 'number=PRB0001').
        limit: Max records to return (default 10).
        offset: Starting record index (default 0).
        
    Returns:
        List of record dictionaries (empty list if none found).
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
    return response.json().get("result", [])


def _post(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Create new record in ServiceNow table via HTTP POST.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table name.
        body: Fields to set on new record.
        
    Returns:
        Created record dictionary (with sys_id).
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
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    sys_id: str,
    body: Dict[str, Any],
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
    """Resolve record identifier to full ServiceNow record via multi-strategy lookup.
    
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


def _problem(config: ServerConfig, auth_manager: AuthManager, identifier: str) -> Dict[str, Any]:
    """Resolve problem record by identifier (PRB number or sys_id).
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        identifier: Problem identifier (e.g., 'PRB0001234', 'sys_id:...').
        
    Returns:
        Problem record dictionary.
    """
    return _resolve(config, auth_manager, PROBLEM_TABLE, identifier)


def _problem_task(
    config: ServerConfig, auth_manager: AuthManager, identifier: str
) -> Dict[str, Any]:
    """Resolve problem task record by identifier.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        identifier: Problem task identifier (number or sys_id).
        
    Returns:
        Problem task record dictionary.
    """
    return _resolve(config, auth_manager, PROBLEM_TASK_TABLE, identifier)


def _incident(config: ServerConfig, auth_manager: AuthManager, identifier: str) -> Dict[str, Any]:
    """Resolve incident record by identifier (INC number or sys_id).
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        identifier: Incident identifier (e.g., 'INC0001234', 'sys_id:...').
        
    Returns:
        Incident record dictionary.
    """
    return _resolve(config, auth_manager, INCIDENT_TABLE, identifier)


def _known_error(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
) -> Dict[str, Any]:
    """Resolve known error record by identifier from specified table.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Known error table name.
        identifier: Known error identifier (number or sys_id).
        
    Returns:
        Known error record dictionary.
    """
    return _resolve(config, auth_manager, table, identifier)


def _body(params: Any, field_map: Dict[str, str]) -> Dict[str, Any]:
    """Build ServiceNow API request body from params with field mapping.
    
    Maps Pydantic model fields to ServiceNow table fields, coercing booleans to strings.
    Merges any additional custom attributes from params.
    
    Args:
        params: Pydantic model with fields to send.
        field_map: Dict mapping param field names to ServiceNow field names.
        
    Returns:
        Request body dict ready for HTTP POST/PATCH (skips None values).
    """
    body: Dict[str, Any] = {}
    for model_field, table_field in field_map.items():
        value = getattr(params, model_field, None)
        if value is None:
            continue
        if isinstance(value, bool):
            body[table_field] = _coerce_bool(value)
        else:
            body[table_field] = value
    attributes = getattr(params, "attributes", None)
    if attributes:
        body.update(attributes)
    return body


def _problem_body(params: Any) -> Dict[str, Any]:
    """Build request body for problem record creation/update.
    
    Args:
        params: CreateProblemParams or UpdateProblemParams.
        
    Returns:
        Mapped request body for Table API POST/PATCH.
    """
    return _body(
        params,
        {
            "short_description": "short_description",
            "description": "description",
            "state": "state",
            "category": "category",
            "subcategory": "subcategory",
            "business_service": "business_service",
            "cmdb_ci": "cmdb_ci",
            "impact": "impact",
            "urgency": "urgency",
            "priority": "priority",
            "assignment_group": "assignment_group",
            "assigned_to": "assigned_to",
            "known_error": "known_error",
            "workaround": "workaround",
            "cause_notes": "cause_notes",
            "fix_notes": "fix_notes",
            "close_code": "close_code",
            "close_notes": "close_notes",
            "comments": "comments",
            "work_notes": "work_notes",
        },
    )


def _problem_task_body(params: Any) -> Dict[str, Any]:
    """Build request body for problem task record creation/update.
    
    Args:
        params: CreateProblemTaskParams or UpdateProblemTaskParams.
        
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
            "close_notes": "close_notes",
        },
    )


def _norm_problem(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize problem record from ServiceNow to API response format.
    
    Extracts key fields and normalizes references/booleans for API consumption.
    
    Args:
        record: Raw ServiceNow problem record.
        include_raw: If True, include full raw record in output.
        
    Returns:
        Normalized problem dict with sys_id, number, state, priority, assignments, etc.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "short_description": record.get("short_description"),
        "description": record.get("description"),
        "state": _display(record.get("state")),
        "category": _display(record.get("category")),
        "subcategory": _display(record.get("subcategory")),
        "business_service": _ref(record.get("business_service")),
        "cmdb_ci": _ref(record.get("cmdb_ci")),
        "impact": _display(record.get("impact")),
        "urgency": _display(record.get("urgency")),
        "priority": _display(record.get("priority")),
        "assignment_group": _ref(record.get("assignment_group")),
        "assigned_to": _ref(record.get("assigned_to")),
        "known_error": _read_bool(record.get("known_error")),
        "workaround": record.get("workaround"),
        "cause_notes": record.get("cause_notes"),
        "fix_notes": record.get("fix_notes"),
        "active": _read_bool(record.get("active")),
        "opened_at": record.get("opened_at"),
        "closed_at": record.get("closed_at"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_problem_task(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize problem task record from ServiceNow to API response format.
    
    Args:
        record: Raw ServiceNow problem task record.
        include_raw: If True, include full raw record in output.
        
    Returns:
        Normalized task dict with sys_id, number, problem reference, state, assignments.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "problem": _ref(record.get("problem")),
        "short_description": record.get("short_description"),
        "description": record.get("description"),
        "state": _display(record.get("state")),
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


def _norm_incident(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize incident record from ServiceNow to API response format.
    
    Args:
        record: Raw ServiceNow incident record.
        include_raw: If True, include full raw record in output.
        
    Returns:
        Normalized incident dict with sys_id, number, state, problem reference, caller.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "short_description": record.get("short_description"),
        "state": _display(record.get("state")),
        "problem": _ref(record.get("problem_id")),
        "caller": _ref(record.get("caller_id")),
        "assignment_group": _ref(record.get("assignment_group")),
        "assigned_to": _ref(record.get("assigned_to")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_known_error(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize known error record from ServiceNow to API response format.
    
    Args:
        record: Raw ServiceNow known error record.
        include_raw: If True, include full raw record in output.
        
    Returns:
        Normalized known error dict with sys_id, number, title, state.
    """
    data = {
        "sys_id": _record_sys_id(record),
        "number": record.get("number"),
        "title": record.get("short_description") or record.get("title"),
        "state": _display(record.get("state") or record.get("workflow_state")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _tasks_for_problem(
    config: ServerConfig,
    auth_manager: AuthManager,
    problem_sys_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch all problem tasks linked to a problem.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        problem_sys_id: Problem record sys_id.
        limit: Max tasks to retrieve (default 100).
        
    Returns:
        List of problem task records.
    """
    return _list(config, auth_manager, PROBLEM_TASK_TABLE, f"problem={problem_sys_id}", limit=limit)


def _incidents_for_problem(
    config: ServerConfig,
    auth_manager: AuthManager,
    problem_sys_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch all incidents linked to a problem via problem_id reference.
    
    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        problem_sys_id: Problem record sys_id.
        limit: Max incidents to retrieve (default 100).
        
    Returns:
        List of incident records.
    """
    return _list(config, auth_manager, INCIDENT_TABLE, f"problem_id={problem_sys_id}", limit=limit)


def list_problems(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListProblemsParams,
) -> Dict[str, Any]:
    """List problems for ServiceNow MCP workflows.

    Composes a problem query from caller filters and returns normalized problem
    records with optional raw payloads.
    """
    query = _query(
        [
            f"state={params.state}" if params.state else None,
            (
                f"known_error={_coerce_bool(params.known_error)}"
                if params.known_error is not None
                else None
            ),
            f"assigned_to={params.assigned_to}" if params.assigned_to else None,
            f"assignment_group={params.assignment_group}" if params.assignment_group else None,
            f"category={params.category}" if params.category else None,
            f"priority={params.priority}" if params.priority else None,
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
        records = _list(config, auth_manager, PROBLEM_TABLE, query, params.limit, params.offset)
        problems = [_norm_problem(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(problems)} problems",
            "problems": problems,
            "count": len(problems),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error listing problems: %s", e)
        return {"success": False, "message": f"Error listing problems: {str(e)}"}


def get_problem(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetProblemParams,
) -> Dict[str, Any]:
    """Get problem for ServiceNow MCP workflows.

    Retrieves the problem record and, when requested, expands linked tasks and
    incidents into the response payload.
    """
    try:
        problem_record = _problem(config, auth_manager, params.problem_id)
        problem_sys_id = _record_sys_id(problem_record)
        if not problem_sys_id:
            raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
        tasks = (
            _tasks_for_problem(config, auth_manager, problem_sys_id) if params.include_tasks else []
        )
        incidents = (
            _incidents_for_problem(config, auth_manager, problem_sys_id)
            if params.include_incidents
            else []
        )
        return {
            "success": True,
            "problem": _norm_problem(problem_record, params.include_raw),
            "problem_tasks": [_norm_problem_task(record, params.include_raw) for record in tasks],
            "linked_incidents": [
                _norm_incident(record, params.include_raw) for record in incidents
            ],
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting problem: %s", e)
        return {"success": False, "message": f"Error getting problem: {str(e)}"}


def create_problem(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateProblemParams,
) -> Dict[str, Any]:
    """Create problem for ServiceNow MCP workflows.

    Creates the problem record and optionally links it back to an incident by
    writing the incident's `problem_id` reference.
    """
    body = _problem_body(params)
    try:
        problem_record = _post(config, auth_manager, PROBLEM_TABLE, body)
        incident_record: Optional[Dict[str, Any]] = None
        if params.related_incident_id:
            incident_record = _incident(config, auth_manager, params.related_incident_id)
            incident_sys_id = _record_sys_id(incident_record)
            problem_sys_id = _record_sys_id(problem_record)
            if not incident_sys_id:
                raise ValueError(f"Incident does not have a sys_id: {params.related_incident_id}")
            if not problem_sys_id:
                raise ValueError("Created problem does not have a sys_id")
            _patch(
                config,
                auth_manager,
                INCIDENT_TABLE,
                incident_sys_id,
                {"problem_id": problem_sys_id},
            )
        return {
            "success": True,
            "message": (
                f"Created problem {problem_record.get('number') or problem_record.get('sys_id')}"
            ),
            "problem": _norm_problem(problem_record, True),
            "linked_incident": _norm_incident(incident_record, True) if incident_record else None,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error creating problem: %s", e)
        return {"success": False, "message": f"Error creating problem: {str(e)}"}


def update_problem(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateProblemParams,
) -> Dict[str, Any]:
    """Update problem for ServiceNow MCP workflows.

    Applies a partial update to the problem record and returns the normalized
    result for downstream workflow steps.
    """
    body = _problem_body(params)
    if not body:
        return {"success": False, "message": "No problem fields supplied to update"}
    try:
        problem_record = _problem(config, auth_manager, params.problem_id)
        problem_sys_id = _record_sys_id(problem_record)
        if not problem_sys_id:
            raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
        updated = _patch(config, auth_manager, PROBLEM_TABLE, problem_sys_id, body)
        return {
            "success": True,
            "message": f"Updated problem {updated.get('number') or updated.get('sys_id')}",
            "problem": _norm_problem(updated, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating problem: %s", e)
        return {"success": False, "message": f"Error updating problem: {str(e)}"}


def resolve_problem(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ResolveProblemParams,
) -> Dict[str, Any]:
    """Resolve problem for ServiceNow MCP workflows.

    Updates close and work notes together so callers can resolve the record in a
    single workflow action.
    """
    body = {
        "state": params.state,
        "fix_notes": params.fix_notes or params.resolution_notes,
        "close_notes": params.close_notes or params.resolution_notes,
    }
    if params.resolution_code:
        body["close_code"] = params.resolution_code
    if params.work_notes:
        body["work_notes"] = params.work_notes
    if params.attributes:
        body.update(params.attributes)
    try:
        problem_record = _problem(config, auth_manager, params.problem_id)
        problem_sys_id = _record_sys_id(problem_record)
        if not problem_sys_id:
            raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
        updated = _patch(config, auth_manager, PROBLEM_TABLE, problem_sys_id, body)
        return {
            "success": True,
            "message": f"Resolved problem {updated.get('number') or updated.get('sys_id')}",
            "problem": _norm_problem(updated, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error resolving problem: %s", e)
        return {"success": False, "message": f"Error resolving problem: {str(e)}"}


def list_problem_tasks(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListProblemTasksParams,
) -> Dict[str, Any]:
    """List problem tasks for ServiceNow MCP workflows.

    Supports optional scoping to a parent problem and returns normalized task
    records for review or follow-up automation.
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
        if params.problem_id:
            problem_record = _problem(config, auth_manager, params.problem_id)
            problem_sys_id = _record_sys_id(problem_record)
            if not problem_sys_id:
                raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
            query_parts.insert(0, f"problem={problem_sys_id}")
        records = _list(
            config,
            auth_manager,
            PROBLEM_TASK_TABLE,
            _query(query_parts),
            params.limit,
            params.offset,
        )
        tasks = [_norm_problem_task(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(tasks)} problem tasks",
            "problem_tasks": tasks,
            "count": len(tasks),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error listing problem tasks: %s", e)
        return {"success": False, "message": f"Error listing problem tasks: {str(e)}"}


def get_problem_task(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetProblemTaskParams,
) -> Dict[str, Any]:
    """Get problem task for ServiceNow MCP workflows.

    Fetches a single problem task and preserves the caller's raw payload
    preference in the normalized response.
    """
    try:
        task_record = _problem_task(config, auth_manager, params.task_id)
        return {
            "success": True,
            "problem_task": _norm_problem_task(task_record, params.include_raw),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting problem task: %s", e)
        return {"success": False, "message": f"Error getting problem task: {str(e)}"}


def create_problem_task(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateProblemTaskParams,
) -> Dict[str, Any]:
    """Create a problem task under a parent problem record."""
    try:
        problem_record = _problem(config, auth_manager, params.problem_id)
        body = _problem_task_body(params)
        problem_sys_id = _record_sys_id(problem_record)
        if not problem_sys_id:
            raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
        body["problem"] = problem_sys_id
        task_record = _post(config, auth_manager, PROBLEM_TASK_TABLE, body)
        return {
            "success": True,
            "message": (
                f"Created problem task {task_record.get('number') or task_record.get('sys_id')}"
            ),
            "problem_task": _norm_problem_task(task_record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error creating problem task: %s", e)
        return {"success": False, "message": f"Error creating problem task: {str(e)}"}


def update_problem_task(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateProblemTaskParams,
) -> Dict[str, Any]:
    """Update a problem task and optionally re-associate its parent problem."""
    body = _problem_task_body(params)
    try:
        if params.problem_id:
            problem_record = _problem(config, auth_manager, params.problem_id)
            problem_sys_id = _record_sys_id(problem_record)
            if not problem_sys_id:
                raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
            body["problem"] = problem_sys_id
        if not body:
            return {"success": False, "message": "No problem task fields supplied to update"}
        task_record = _problem_task(config, auth_manager, params.task_id)
        task_sys_id = _record_sys_id(task_record)
        if not task_sys_id:
            raise ValueError(f"Problem task does not have a sys_id: {params.task_id}")
        updated = _patch(
            config,
            auth_manager,
            PROBLEM_TASK_TABLE,
            task_sys_id,
            body,
        )
        return {
            "success": True,
            "message": f"Updated problem task {updated.get('number') or updated.get('sys_id')}",
            "problem_task": _norm_problem_task(updated, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating problem task: %s", e)
        return {"success": False, "message": f"Error updating problem task: {str(e)}"}


def resolve_problem_task(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ResolveProblemTaskParams,
) -> Dict[str, Any]:
    """Resolve a problem task by applying close-state and notes payloads."""
    body: Dict[str, Any] = {
        "state": params.state,
        "close_notes": params.close_notes or params.resolution_notes,
    }
    if params.work_notes:
        body["work_notes"] = params.work_notes
    if params.attributes:
        body.update(params.attributes)
    try:
        task_record = _problem_task(config, auth_manager, params.task_id)
        task_sys_id = _record_sys_id(task_record)
        if not task_sys_id:
            raise ValueError(f"Problem task does not have a sys_id: {params.task_id}")
        updated = _patch(
            config,
            auth_manager,
            PROBLEM_TASK_TABLE,
            task_sys_id,
            body,
        )
        return {
            "success": True,
            "message": f"Resolved problem task {updated.get('number') or updated.get('sys_id')}",
            "problem_task": _norm_problem_task(updated, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error resolving problem task: %s", e)
        return {"success": False, "message": f"Error resolving problem task: {str(e)}"}


def link_problem_to_incident(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: LinkProblemToIncidentParams,
) -> Dict[str, Any]:
    """Link a problem to an incident and optionally stamp cross-work-notes."""
    try:
        problem_record = _problem(config, auth_manager, params.problem_id)
        incident_record = _incident(config, auth_manager, params.incident_id)
        problem_sys_id = _record_sys_id(problem_record)
        incident_sys_id = _record_sys_id(incident_record)
        if not problem_sys_id:
            raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
        if not incident_sys_id:
            raise ValueError(f"Incident does not have a sys_id: {params.incident_id}")
        incident_body: Dict[str, Any] = {
            params.incident_problem_field: problem_sys_id,
        }
        problem_body: Dict[str, Any] = {}
        if params.add_work_notes:
            incident_body["work_notes"] = (
                f"Linked to problem {problem_record.get('number') or problem_record['sys_id']}."
            )
            problem_body["work_notes"] = (
                f"Linked to incident {incident_record.get('number') or incident_record['sys_id']}."
            )
        if params.attributes:
            incident_body.update(params.attributes)
        updated_incident = _patch(
            config,
            auth_manager,
            INCIDENT_TABLE,
            incident_sys_id,
            incident_body,
        )
        updated_problem = problem_record
        if problem_body:
            updated_problem = _patch(
                config,
                auth_manager,
                PROBLEM_TABLE,
                problem_sys_id,
                problem_body,
            )
        return {
            "success": True,
            "message": (
                f"Linked problem {problem_record.get('number') or problem_record['sys_id']} "
                f"to incident {incident_record.get('number') or incident_record['sys_id']}"
            ),
            "problem": _norm_problem(updated_problem, True),
            "incident": _norm_incident(updated_incident, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error linking problem to incident: %s", e)
        return {"success": False, "message": f"Error linking problem to incident: {str(e)}"}


def link_problem_to_known_error(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: LinkProblemToKnownErrorParams,
) -> Dict[str, Any]:
    """Mark or link a problem as a known error with optional detail fields."""
    try:
        problem_record = _problem(config, auth_manager, params.problem_id)
        body: Dict[str, Any] = {}
        known_error_record: Optional[Dict[str, Any]] = None

        if params.mark_known_error:
            body["known_error"] = "true"
        if params.workaround is not None:
            body["workaround"] = params.workaround
        if params.cause_notes is not None:
            body["cause_notes"] = params.cause_notes
        if params.work_notes is not None:
            body["work_notes"] = params.work_notes
        if params.known_error_id:
            known_error_record = _known_error(
                config,
                auth_manager,
                params.known_error_table,
                params.known_error_id,
            )
            if params.known_error_field:
                body[params.known_error_field] = known_error_record["sys_id"]
        if params.attributes:
            body.update(params.attributes)
        if not body:
            return {
                "success": False,
                "message": "No known-error fields supplied to update",
            }

        problem_sys_id = _record_sys_id(problem_record)
        if not problem_sys_id:
            raise ValueError(f"Problem does not have a sys_id: {params.problem_id}")
        updated = _patch(config, auth_manager, PROBLEM_TABLE, problem_sys_id, body)
        return {
            "success": True,
            "message": (
                f"Linked problem {updated.get('number') or updated.get('sys_id')} to known error "
                f"context"
            ),
            "problem": _norm_problem(updated, True),
            "known_error": (
                _norm_known_error(known_error_record, True) if known_error_record else None
            ),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error linking problem to known error: %s", e)
        return {"success": False, "message": f"Error linking problem to known error: {str(e)}"}
