"""
Company management tools for the ServiceNow MCP server.

This module provides CRUD-style helpers for company records and the related
display/reference fields used by other organizational data modules. It focuses
on the master company data that cost centers, departments, and locations often
link back to.
"""

import logging
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

COMPANY_TABLE = "core_company"
DISPLAY_PARAMS = {
    "sysparm_display_value": "all",
    "sysparm_exclude_reference_link": "true",
}


class ListCompaniesParams(BaseModel):
    """Parameters for listing companies."""

    query: Optional[str] = Field(
        None,
        description="Search company name, city, state, country, phone, or website",
    )
    city: Optional[str] = Field(None, description="Filter by city")
    state: Optional[str] = Field(None, description="Filter by state/province")
    country: Optional[str] = Field(None, description="Filter by country")
    parent: Optional[str] = Field(None, description="Filter by parent company sys_id or name")
    customer: Optional[bool] = Field(None, description="Filter customer companies")
    vendor: Optional[bool] = Field(None, description="Filter vendor companies")
    manufacturer: Optional[bool] = Field(None, description="Filter manufacturer companies")
    limit: int = Field(25, description="Maximum companies to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetCompanyParams(BaseModel):
    """Parameters for getting a company."""

    company_id: str = Field(..., description="Company sys_id or exact company name")
    include_raw: bool = Field(False, description="Include raw ServiceNow record")


class CreateCompanyParams(BaseModel):
    """Parameters for creating a company."""

    name: str = Field(..., description="Company name")
    parent: Optional[str] = Field(None, description="Parent company sys_id or name")
    street: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None, description="City")
    state: Optional[str] = Field(None, description="State/province")
    zip: Optional[str] = Field(None, description="Postal code")
    country: Optional[str] = Field(None, description="Country")
    phone: Optional[str] = Field(None, description="Company phone number")
    fax_phone: Optional[str] = Field(None, description="Company fax phone number")
    website: Optional[str] = Field(None, description="Company website")
    email: Optional[str] = Field(None, description="Company email address")
    customer: Optional[bool] = Field(None, description="Whether the company is a customer")
    vendor: Optional[bool] = Field(None, description="Whether the company is a vendor")
    manufacturer: Optional[bool] = Field(None, description="Whether the company is a manufacturer")
    notes: Optional[str] = Field(None, description="Company notes")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional core_company fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without creating")


class UpdateCompanyParams(BaseModel):
    """Parameters for updating a company."""

    company_id: str = Field(..., description="Company sys_id or exact company name")
    name: Optional[str] = Field(None, description="Company name")
    parent: Optional[str] = Field(None, description="Parent company sys_id or name")
    street: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None, description="City")
    state: Optional[str] = Field(None, description="State/province")
    zip: Optional[str] = Field(None, description="Postal code")
    country: Optional[str] = Field(None, description="Country")
    phone: Optional[str] = Field(None, description="Company phone number")
    fax_phone: Optional[str] = Field(None, description="Company fax phone number")
    website: Optional[str] = Field(None, description="Company website")
    email: Optional[str] = Field(None, description="Company email address")
    customer: Optional[bool] = Field(None, description="Whether the company is a customer")
    vendor: Optional[bool] = Field(None, description="Whether the company is a vendor")
    manufacturer: Optional[bool] = Field(None, description="Whether the company is a manufacturer")
    notes: Optional[str] = Field(None, description="Company notes")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional core_company fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class DeleteCompanyParams(BaseModel):
    """Parameters for deleting a company."""

    company_id: str = Field(..., description="Company sys_id or exact company name")
    dry_run: bool = Field(False, description="Validate and show target without deleting")


def _bool_text(value: bool) -> str:
    return str(value).lower()


def _clean(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _normalize_company(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    company = {
        "sys_id": _display(record.get("sys_id")),
        "name": _display(record.get("name")),
        "parent": _display(record.get("parent")),
        "street": _display(record.get("street")),
        "city": _display(record.get("city")),
        "state": _display(record.get("state")),
        "zip": _display(record.get("zip")),
        "country": _display(record.get("country")),
        "phone": _display(record.get("phone")),
        "fax_phone": _display(record.get("fax_phone")),
        "website": _display(record.get("website")),
        "email": _display(record.get("email")),
        "customer": _display(record.get("customer")),
        "vendor": _display(record.get("vendor")),
        "manufacturer": _display(record.get("manufacturer")),
        "notes": _display(record.get("notes")),
    }
    if include_raw:
        company["raw"] = record
    return company


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    sys_id = _display(record.get("sys_id"))
    return str(sys_id) if sys_id else None


def _get_company_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    company_sys_id: str,
) -> Dict[str, Any]:
    response = requests.get(
        f"{config.api_url}/table/{COMPANY_TABLE}/{company_sys_id}",
        params=DISPLAY_PARAMS,
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json().get("result", {})


def _query(parts: list[Optional[str]]) -> str:
    return "^".join(part for part in parts if part)


def _resolve_company_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    company_id: str,
) -> str:
    company_id = company_id.strip()
    if len(company_id) == 32 and all(char in "0123456789abcdefABCDEF" for char in company_id):
        return company_id

    query_params = {
        "sysparm_query": f"name={company_id}",
        "sysparm_limit": "2",
        **DISPLAY_PARAMS,
    }
    response = requests.get(
        f"{config.api_url}/table/{COMPANY_TABLE}",
        params=query_params,
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    records = response.json().get("result", [])
    if not records:
        raise ValueError(f"Company not found: {company_id}")
    if len(records) > 1:
        raise ValueError(f"Multiple companies matched: {company_id}")
    sys_id = _display(records[0].get("sys_id"))
    if not sys_id:
        raise ValueError(f"Resolved company has no sys_id: {company_id}")
    return str(sys_id)


def _resolve_parent(
    config: ServerConfig,
    auth_manager: AuthManager,
    parent: Optional[str],
) -> Optional[str]:
    if not parent:
        return None
    return _resolve_company_sys_id(config, auth_manager, parent)


def _company_payload(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: Any,
) -> Dict[str, Any]:
    body = dict(getattr(params, "fields", None) or {})
    for attr in [
        "name",
        "street",
        "city",
        "state",
        "zip",
        "country",
        "phone",
        "fax_phone",
        "website",
        "email",
        "customer",
        "vendor",
        "manufacturer",
        "notes",
    ]:
        value = getattr(params, attr, None)
        if value is not None:
            body[attr] = _bool_text(value) if isinstance(value, bool) else value

    parent = _resolve_parent(config, auth_manager, getattr(params, "parent", None))
    if parent:
        body["parent"] = parent

    return _clean(body)


def list_companies(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListCompaniesParams,
) -> Dict[str, Any]:
    """List companies from ServiceNow."""

    try:
        parent = _resolve_parent(config, auth_manager, params.parent)
        query = _query(
            [
                (
                    f"nameLIKE{params.query}^ORcityLIKE{params.query}^ORstateLIKE{params.query}"
                    f"^ORcountryLIKE{params.query}^ORphoneLIKE{params.query}"
                    f"^ORwebsiteLIKE{params.query}"
                    if params.query
                    else None
                ),
                f"city={params.city}" if params.city else None,
                f"state={params.state}" if params.state else None,
                f"country={params.country}" if params.country else None,
                f"parent={parent}" if parent else None,
                f"customer={_bool_text(params.customer)}" if params.customer is not None else None,
                f"vendor={_bool_text(params.vendor)}" if params.vendor is not None else None,
                (
                    f"manufacturer={_bool_text(params.manufacturer)}"
                    if params.manufacturer is not None
                    else None
                ),
            ]
        )
        response = requests.get(
            f"{config.api_url}/table/{COMPANY_TABLE}",
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
        companies = [_normalize_company(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(companies)} companies",
            "companies": companies,
            "count": len(companies),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to list companies: {exc}")
        return {"success": False, "message": f"Failed to list companies: {str(exc)}"}


def get_company(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetCompanyParams,
) -> Dict[str, Any]:
    """Get a company from ServiceNow by sys_id or exact name."""

    try:
        company_sys_id = _resolve_company_sys_id(config, auth_manager, params.company_id)
        response = requests.get(
            f"{config.api_url}/table/{COMPANY_TABLE}/{company_sys_id}",
            params=DISPLAY_PARAMS,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": "Company found",
            "company": _normalize_company(record, params.include_raw),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to get company: {exc}")
        return {"success": False, "message": f"Failed to get company: {str(exc)}"}


def create_company(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateCompanyParams,
) -> Dict[str, Any]:
    """Create a company in ServiceNow."""

    try:
        body = _company_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: company would be created",
                "table": COMPANY_TABLE,
                "payload": body,
            }
        response = requests.post(
            f"{config.api_url}/table/{COMPANY_TABLE}",
            params=DISPLAY_PARAMS,
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        sys_id = _record_sys_id(record)
        if sys_id:
            record = _get_company_record(config, auth_manager, sys_id)
        return {
            "success": True,
            "message": "Company created successfully",
            "sys_id": _record_sys_id(record),
            "company": _normalize_company(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to create company: {exc}")
        return {"success": False, "message": f"Failed to create company: {str(exc)}"}


def update_company(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateCompanyParams,
) -> Dict[str, Any]:
    """Update a company in ServiceNow."""

    try:
        company_sys_id = _resolve_company_sys_id(config, auth_manager, params.company_id)
        body = _company_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: company would be updated",
                "table": COMPANY_TABLE,
                "company_id": company_sys_id,
                "payload": body,
            }
        response = requests.patch(
            f"{config.api_url}/table/{COMPANY_TABLE}/{company_sys_id}",
            params=DISPLAY_PARAMS,
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        record = _get_company_record(config, auth_manager, company_sys_id) or record
        return {
            "success": True,
            "message": "Company updated successfully",
            "sys_id": _record_sys_id(record),
            "company": _normalize_company(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to update company: {exc}")
        return {"success": False, "message": f"Failed to update company: {str(exc)}"}


def delete_company(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteCompanyParams,
) -> Dict[str, Any]:
    """Delete a company from ServiceNow."""

    try:
        company_sys_id = _resolve_company_sys_id(config, auth_manager, params.company_id)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: company would be deleted",
                "table": COMPANY_TABLE,
                "company_id": company_sys_id,
            }
        response = requests.delete(
            f"{config.api_url}/table/{COMPANY_TABLE}/{company_sys_id}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Company deleted successfully",
            "company_id": company_sys_id,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to delete company: {exc}")
        return {"success": False, "message": f"Failed to delete company: {str(exc)}"}
