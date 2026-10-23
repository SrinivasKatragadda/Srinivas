"""
Department management tools for the ServiceNow MCP server.

This module provides CRUD-style helpers for department records and related
lookups such as company and manager references. It focuses on administrative
management of organizational structure data that other modules, such as user
and location tools, can reuse.
"""

import logging
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

DEPARTMENT_TABLE = "cmn_department"
COMPANY_TABLE = "core_company"
USER_TABLE = "sys_user"
DISPLAY_PARAMS = {
    "sysparm_display_value": "all",
    "sysparm_exclude_reference_link": "true",
}


class ListDepartmentsParams(BaseModel):
    """Parameters for listing departments."""

    query: Optional[str] = Field(
        None,
        description="Search department name, id, description, company, parent, or department head",
    )
    company: Optional[str] = Field(None, description="Filter by company sys_id or name")
    parent: Optional[str] = Field(
        None, description="Filter by parent department sys_id, id, or name"
    )
    department_head: Optional[str] = Field(
        None, description="Filter by department head sys_id, user name, email, or name"
    )
    limit: int = Field(25, description="Maximum departments to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetDepartmentParams(BaseModel):
    """Parameters for getting a department."""

    department_id: str = Field(..., description="Department sys_id, exact id, or exact name")
    include_raw: bool = Field(False, description="Include raw ServiceNow record")


class CreateDepartmentParams(BaseModel):
    """Parameters for creating a department."""

    name: str = Field(..., description="Department name")
    id: Optional[str] = Field(None, description="Department code/id")
    description: Optional[str] = Field(None, description="Department description")
    parent: Optional[str] = Field(None, description="Parent department sys_id, id, or name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    department_head: Optional[str] = Field(
        None, description="Department head sys_id, user name, email, or name"
    )
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional cmn_department fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without creating")


class UpdateDepartmentParams(BaseModel):
    """Parameters for updating a department."""

    department_id: str = Field(..., description="Department sys_id, exact id, or exact name")
    name: Optional[str] = Field(None, description="Department name")
    id: Optional[str] = Field(None, description="Department code/id")
    description: Optional[str] = Field(None, description="Department description")
    parent: Optional[str] = Field(None, description="Parent department sys_id, id, or name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    department_head: Optional[str] = Field(
        None, description="Department head sys_id, user name, email, or name"
    )
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional cmn_department fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class DeleteDepartmentParams(BaseModel):
    """Parameters for deleting a department."""

    department_id: str = Field(..., description="Department sys_id, exact id, or exact name")
    dry_run: bool = Field(False, description="Validate and show target without deleting")


def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _is_sys_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdefABCDEF" for char in value)


def _query(parts: list[Optional[str]]) -> str:
    return "^".join(part for part in parts if part)


def _normalize_department(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    department = {
        "sys_id": _display(record.get("sys_id")),
        "name": _display(record.get("name")),
        "id": _display(record.get("id")),
        "description": _display(record.get("description")),
        "parent": _display(record.get("parent")),
        "company": _display(record.get("company")),
        "department_head": _display(record.get("dept_head")),
    }
    if include_raw:
        department["raw"] = record
    return department


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    sys_id = _display(record.get("sys_id"))
    return str(sys_id) if sys_id else None


def _get_department_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    department_sys_id: str,
) -> Dict[str, Any]:
    response = requests.get(
        f"{config.api_url}/table/{DEPARTMENT_TABLE}/{department_sys_id}",
        params=DISPLAY_PARAMS,
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _resolve_record_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    lookup_fields: list[str],
    label: str,
) -> str:
    identifier = identifier.strip()
    if _is_sys_id(identifier):
        return identifier

    query = "^OR".join(f"{field}={identifier}" for field in lookup_fields)
    response = requests.get(
        f"{config.api_url}/table/{table}",
        params={
            "sysparm_query": query,
            "sysparm_limit": "2",
            **DISPLAY_PARAMS,
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


def _resolve_department_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    department_id: str,
) -> str:
    return _resolve_record_sys_id(
        config,
        auth_manager,
        DEPARTMENT_TABLE,
        department_id,
        ["id", "name"],
        "Department",
    )


def _resolve_company_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    company: Optional[str],
) -> Optional[str]:
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


def _resolve_user_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    user: Optional[str],
) -> Optional[str]:
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


def _resolve_parent_department(
    config: ServerConfig,
    auth_manager: AuthManager,
    parent: Optional[str],
) -> Optional[str]:
    if not parent:
        return None
    return _resolve_department_sys_id(config, auth_manager, parent)


def _department_payload(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: Any,
) -> Dict[str, Any]:
    body = dict(getattr(params, "fields", None) or {})
    for attr in ["name", "id", "description"]:
        value = getattr(params, attr, None)
        if value is not None:
            body[attr] = value

    parent = _resolve_parent_department(config, auth_manager, getattr(params, "parent", None))
    if parent:
        body["parent"] = parent

    company = _resolve_company_sys_id(config, auth_manager, getattr(params, "company", None))
    if company:
        body["company"] = company

    department_head = _resolve_user_sys_id(
        config, auth_manager, getattr(params, "department_head", None)
    )
    if department_head:
        body["dept_head"] = department_head

    return _clean(body)


def list_departments(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListDepartmentsParams,
) -> Dict[str, Any]:
    """List departments from ServiceNow."""

    try:
        company = _resolve_company_sys_id(config, auth_manager, params.company)
        parent = _resolve_parent_department(config, auth_manager, params.parent)
        department_head = _resolve_user_sys_id(config, auth_manager, params.department_head)
        query = _query(
            [
                (
                    f"nameLIKE{params.query}^ORidLIKE{params.query}"
                    f"^ORdescriptionLIKE{params.query}^ORcompany.nameLIKE{params.query}"
                    f"^ORparent.nameLIKE{params.query}^ORdept_head.nameLIKE{params.query}"
                    if params.query
                    else None
                ),
                f"company={company}" if company else None,
                f"parent={parent}" if parent else None,
                f"dept_head={department_head}" if department_head else None,
            ]
        )
        response = requests.get(
            f"{config.api_url}/table/{DEPARTMENT_TABLE}",
            params={
                "sysparm_query": query,
                "sysparm_limit": str(params.limit),
                "sysparm_offset": str(params.offset),
                **DISPLAY_PARAMS,
            },
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        records = response.json().get("result", [])
        departments = [_normalize_department(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(departments)} departments",
            "departments": departments,
            "count": len(departments),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to list departments: {exc}")
        return {"success": False, "message": f"Failed to list departments: {str(exc)}"}


def get_department(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetDepartmentParams,
) -> Dict[str, Any]:
    """Get a department from ServiceNow by sys_id, exact id, or exact name."""

    try:
        department_sys_id = _resolve_department_sys_id(config, auth_manager, params.department_id)
        response = requests.get(
            f"{config.api_url}/table/{DEPARTMENT_TABLE}/{department_sys_id}",
            params=DISPLAY_PARAMS,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": "Department found",
            "department": _normalize_department(record, params.include_raw),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to get department: {exc}")
        return {"success": False, "message": f"Failed to get department: {str(exc)}"}


def create_department(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateDepartmentParams,
) -> Dict[str, Any]:
    """Create a department in ServiceNow."""

    try:
        body = _department_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: department would be created",
                "table": DEPARTMENT_TABLE,
                "payload": body,
            }
        response = requests.post(
            f"{config.api_url}/table/{DEPARTMENT_TABLE}",
            params=DISPLAY_PARAMS,
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        sys_id = _record_sys_id(record)
        if sys_id:
            record = _get_department_record(config, auth_manager, sys_id)
        return {
            "success": True,
            "message": "Department created successfully",
            "sys_id": _record_sys_id(record),
            "department": _normalize_department(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to create department: {exc}")
        return {"success": False, "message": f"Failed to create department: {str(exc)}"}


def update_department(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateDepartmentParams,
) -> Dict[str, Any]:
    """Update a department in ServiceNow."""

    try:
        department_sys_id = _resolve_department_sys_id(config, auth_manager, params.department_id)
        body = _department_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: department would be updated",
                "table": DEPARTMENT_TABLE,
                "department_id": department_sys_id,
                "payload": body,
            }
        response = requests.patch(
            f"{config.api_url}/table/{DEPARTMENT_TABLE}/{department_sys_id}",
            params=DISPLAY_PARAMS,
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        record = _get_department_record(config, auth_manager, department_sys_id) or record
        return {
            "success": True,
            "message": "Department updated successfully",
            "sys_id": _record_sys_id(record),
            "department": _normalize_department(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to update department: {exc}")
        return {"success": False, "message": f"Failed to update department: {str(exc)}"}


def delete_department(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteDepartmentParams,
) -> Dict[str, Any]:
    """Delete a department from ServiceNow."""

    try:
        department_sys_id = _resolve_department_sys_id(config, auth_manager, params.department_id)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: department would be deleted",
                "table": DEPARTMENT_TABLE,
                "department_id": department_sys_id,
            }
        response = requests.delete(
            f"{config.api_url}/table/{DEPARTMENT_TABLE}/{department_sys_id}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Department deleted successfully",
            "department_id": department_sys_id,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to delete department: {exc}")
        return {"success": False, "message": f"Failed to delete department: {str(exc)}"}
