"""
Cost center management tools for the ServiceNow MCP server.

This module provides CRUD-style helpers for cost center records and their
common relationships to companies, departments, and managers. It is designed
for administrative maintenance of financial reference data used by other
ServiceNow modules.
"""

import logging
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

COST_CENTER_TABLE = "cmn_cost_center"
COMPANY_TABLE = "core_company"
DEPARTMENT_TABLE = "cmn_department"
USER_TABLE = "sys_user"


class ListCostCentersParams(BaseModel):
    """Parameters for listing cost centers."""

    query: Optional[str] = Field(
        None,
        description="Search cost center name, code, description, company, department, or manager",
    )
    company: Optional[str] = Field(None, description="Filter by company sys_id or name")
    department: Optional[str] = Field(None, description="Filter by department sys_id, id, or name")
    manager: Optional[str] = Field(
        None, description="Filter by manager sys_id, user name, email, or name"
    )
    limit: int = Field(25, description="Maximum cost centers to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetCostCenterParams(BaseModel):
    """Parameters for getting a cost center."""

    cost_center_id: str = Field(..., description="Cost center sys_id, exact code, or exact name")
    include_raw: bool = Field(False, description="Include raw ServiceNow record")


class CreateCostCenterParams(BaseModel):
    """Parameters for creating a cost center."""

    name: str = Field(..., description="Cost center name")
    code: Optional[str] = Field(None, description="Cost center code")
    description: Optional[str] = Field(None, description="Cost center description")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id, id, or name")
    manager: Optional[str] = Field(None, description="Manager sys_id, user name, email, or name")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional cmn_cost_center fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without creating")


class UpdateCostCenterParams(BaseModel):
    """Parameters for updating a cost center."""

    cost_center_id: str = Field(..., description="Cost center sys_id, exact code, or exact name")
    name: Optional[str] = Field(None, description="Cost center name")
    code: Optional[str] = Field(None, description="Cost center code")
    description: Optional[str] = Field(None, description="Cost center description")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id, id, or name")
    manager: Optional[str] = Field(None, description="Manager sys_id, user name, email, or name")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional cmn_cost_center fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class DeleteCostCenterParams(BaseModel):
    """Parameters for deleting a cost center."""

    cost_center_id: str = Field(..., description="Cost center sys_id, exact code, or exact name")
    dry_run: bool = Field(False, description="Validate and show target without deleting")


def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys whose values are None from a payload dictionary."""
    return {key: value for key, value in data.items() if value is not None}


def _display(value: Any) -> Any:
    """Extract the display value from a ServiceNow field mapping when available."""
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _is_sys_id(value: str) -> bool:
    """Return whether a string matches the expected 32-character sys_id format."""
    return len(value) == 32 and all(char in "0123456789abcdefABCDEF" for char in value)


def _query(parts: list[Optional[str]]) -> str:
    """Join non-empty encoded-query fragments using ServiceNow's caret separator."""
    return "^".join(part for part in parts if part)


def _normalize_cost_center(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    """Normalize a cost center record for tool responses.

    Args:
        record: Raw ServiceNow cost center record.
        include_raw: Whether to include the raw record.

    Returns:
        Normalized cost center payload.
    """
    cost_center = {
        "sys_id": _display(record.get("sys_id")),
        "name": _display(record.get("name")),
        "code": _display(record.get("code")),
        "description": _display(record.get("description")),
        "company": _display(record.get("company")),
        "department": _display(record.get("department")),
        "manager": _display(record.get("manager")),
    }
    if include_raw:
        cost_center["raw"] = record
    return cost_center


def _resolve_record_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    lookup_fields: list[str],
    label: str,
) -> str:
    """Resolve a related record to a sys_id via direct sys_id or exact field lookup.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        table: Table to query.
        identifier: Sys_id or human-readable identifier.
        lookup_fields: Fields to search for exact matches.
        label: Human-readable label used in error messages.

    Returns:
        Resolved record sys_id.

    Raises:
        ValueError: If the identifier is empty, not found, or resolves ambiguously.
    """
    identifier = identifier.strip()
    if _is_sys_id(identifier):
        return identifier

    query = "^OR".join(f"{field}={identifier}" for field in lookup_fields)
    response = requests.get(
        f"{config.api_url}/table/{table}",
        params={
            "sysparm_query": query,
            "sysparm_limit": "2",
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        },
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    records = response.json().get("result", [])
    if not records:
        raise ValueError(f"{label} not found: {identifier}")
    if len(records) > 1:
        raise ValueError(f"Multiple {label.lower()} records matched: {identifier}")
    sys_id = _display(records[0].get("sys_id"))
    if not sys_id:
        raise ValueError(f"Resolved {label.lower()} has no sys_id: {identifier}")
    return str(sys_id)


def _resolve_cost_center_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    cost_center_id: str,
) -> str:
    """Resolve a cost center by code, name, or sys_id."""
    return _resolve_record_sys_id(
        config,
        auth_manager,
        COST_CENTER_TABLE,
        cost_center_id,
        ["code", "name"],
        "Cost center",
    )


def _resolve_company_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    company: Optional[str],
) -> Optional[str]:
    """Resolve an optional company reference to a sys_id."""
    if not company:
        return None
    return _resolve_record_sys_id(
        config,
        auth_manager,
        COMPANY_TABLE,
        company,
        ["name"],
        "Company",
    )


def _resolve_department_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    department: Optional[str],
) -> Optional[str]:
    """Resolve an optional department reference to a sys_id."""
    if not department:
        return None
    return _resolve_record_sys_id(
        config,
        auth_manager,
        DEPARTMENT_TABLE,
        department,
        ["id", "name"],
        "Department",
    )


def _resolve_user_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    user: Optional[str],
) -> Optional[str]:
    """Resolve an optional user or manager reference to a sys_id."""
    if not user:
        return None
    return _resolve_record_sys_id(
        config,
        auth_manager,
        USER_TABLE,
        user,
        ["user_name", "email", "name"],
        "User",
    )


def _cost_center_payload(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: Any,
) -> Dict[str, Any]:
    """Build a cost center payload and resolve related references.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Create or update parameter model.

    Returns:
        Cleaned payload ready for the ServiceNow Table API.
    """
    body = dict(getattr(params, "fields", None) or {})
    for attr in ["name", "code", "description"]:
        value = getattr(params, attr, None)
        if value is not None:
            body[attr] = value

    company = _resolve_company_sys_id(config, auth_manager, getattr(params, "company", None))
    if company:
        body["company"] = company

    department = _resolve_department_sys_id(
        config, auth_manager, getattr(params, "department", None)
    )
    if department:
        body["department"] = department

    manager = _resolve_user_sys_id(config, auth_manager, getattr(params, "manager", None))
    if manager:
        body["manager"] = manager

    return _clean(body)


def list_cost_centers(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListCostCentersParams,
) -> Dict[str, Any]:
    """List cost centers from ServiceNow.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Cost center list filters and paging options.

    Returns:
        Result payload containing normalized cost centers and count metadata.
    """

    try:
        company = _resolve_company_sys_id(config, auth_manager, params.company)
        department = _resolve_department_sys_id(config, auth_manager, params.department)
        manager = _resolve_user_sys_id(config, auth_manager, params.manager)
        query = _query(
            [
                (
                    f"nameLIKE{params.query}^ORcodeLIKE{params.query}"
                    f"^ORdescriptionLIKE{params.query}^ORcompany.nameLIKE{params.query}"
                    f"^ORdepartment.nameLIKE{params.query}^ORmanager.nameLIKE{params.query}"
                    if params.query
                    else None
                ),
                f"company={company}" if company else None,
                f"department={department}" if department else None,
                f"manager={manager}" if manager else None,
            ]
        )
        response = requests.get(
            f"{config.api_url}/table/{COST_CENTER_TABLE}",
            params={
                "sysparm_query": query,
                "sysparm_limit": str(params.limit),
                "sysparm_offset": str(params.offset),
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        records = response.json().get("result", [])
        cost_centers = [_normalize_cost_center(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(cost_centers)} cost centers",
            "cost_centers": cost_centers,
            "count": len(cost_centers),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to list cost centers: {exc}")
        return {"success": False, "message": f"Failed to list cost centers: {str(exc)}"}


def get_cost_center(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetCostCenterParams,
) -> Dict[str, Any]:
    """Get a cost center from ServiceNow by sys_id, exact code, or exact name.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Cost center lookup parameters.

    Returns:
        Result payload containing the normalized cost center.
    """

    try:
        cost_center_sys_id = _resolve_cost_center_sys_id(
            config, auth_manager, params.cost_center_id
        )
        response = requests.get(
            f"{config.api_url}/table/{COST_CENTER_TABLE}/{cost_center_sys_id}",
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": "Cost center found",
            "cost_center": _normalize_cost_center(record, params.include_raw),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to get cost center: {exc}")
        return {"success": False, "message": f"Failed to get cost center: {str(exc)}"}


def create_cost_center(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCostCenterParams,
) -> Dict[str, Any]:
    """Create a cost center in ServiceNow.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Cost center creation parameters.

    Returns:
        Result payload containing either the dry-run payload or the created
        normalized cost center.
    """

    try:
        body = _cost_center_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: cost center would be created",
                "table": COST_CENTER_TABLE,
                "payload": body,
            }
        response = requests.post(
            f"{config.api_url}/table/{COST_CENTER_TABLE}",
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": "Cost center created successfully",
            "cost_center": _normalize_cost_center(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to create cost center: {exc}")
        return {"success": False, "message": f"Failed to create cost center: {str(exc)}"}


def update_cost_center(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateCostCenterParams,
) -> Dict[str, Any]:
    """Update a cost center in ServiceNow.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Cost center update parameters.

    Returns:
        Result payload containing either the dry-run payload or the updated
        normalized cost center.
    """

    try:
        cost_center_sys_id = _resolve_cost_center_sys_id(
            config, auth_manager, params.cost_center_id
        )
        body = _cost_center_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: cost center would be updated",
                "table": COST_CENTER_TABLE,
                "cost_center_id": cost_center_sys_id,
                "payload": body,
            }
        response = requests.patch(
            f"{config.api_url}/table/{COST_CENTER_TABLE}/{cost_center_sys_id}",
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": "Cost center updated successfully",
            "cost_center": _normalize_cost_center(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to update cost center: {exc}")
        return {"success": False, "message": f"Failed to update cost center: {str(exc)}"}


def delete_cost_center(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteCostCenterParams,
) -> Dict[str, Any]:
    """Delete a cost center from ServiceNow.

    Args:
        config: Server configuration.
        auth_manager: Authentication manager.
        params: Cost center deletion parameters.

    Returns:
        Result payload containing either the dry-run target or the deleted
        cost center sys_id.
    """

    try:
        cost_center_sys_id = _resolve_cost_center_sys_id(
            config, auth_manager, params.cost_center_id
        )
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: cost center would be deleted",
                "table": COST_CENTER_TABLE,
                "cost_center_id": cost_center_sys_id,
            }
        response = requests.delete(
            f"{config.api_url}/table/{COST_CENTER_TABLE}/{cost_center_sys_id}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Cost center deleted successfully",
            "cost_center_id": cost_center_sys_id,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to delete cost center: {exc}")
        return {"success": False, "message": f"Failed to delete cost center: {str(exc)}"}
