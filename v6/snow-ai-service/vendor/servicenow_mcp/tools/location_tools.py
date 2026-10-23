"""
Location management tools for the ServiceNow MCP server.

This module provides CRUD-style helpers for location records and their common
relationships, such as company and user lookups. It is primarily used for
administrative inspection and maintenance of location data in ServiceNow.
"""

import logging
from typing import Any, Dict, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

LOCATION_TABLE = "cmn_location"
COMPANY_TABLE = "core_company"
USER_TABLE = "sys_user"
DISPLAY_PARAMS = {
    "sysparm_display_value": "all",
    "sysparm_exclude_reference_link": "true",
}


class ListLocationsParams(BaseModel):
    """Parameters for listing locations."""

    query: Optional[str] = Field(
        None,
        description="Search location name, full name, city, state, country, street, or phone",
    )
    city: Optional[str] = Field(None, description="Filter by city")
    state: Optional[str] = Field(None, description="Filter by state/province")
    country: Optional[str] = Field(None, description="Filter by country")
    parent: Optional[str] = Field(
        None, description="Filter by parent location sys_id/name/full name"
    )
    company: Optional[str] = Field(None, description="Filter by company sys_id or name")
    limit: int = Field(25, description="Maximum locations to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetLocationParams(BaseModel):
    """Parameters for getting a location."""

    location_id: str = Field(..., description="Location sys_id, exact name, or exact full name")
    include_raw: bool = Field(False, description="Include raw ServiceNow record")


class CreateLocationParams(BaseModel):
    """Parameters for creating a location."""

    name: str = Field(..., description="Location name")
    full_name: Optional[str] = Field(None, description="Full hierarchical location name")
    parent: Optional[str] = Field(None, description="Parent location sys_id/name/full name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    contact: Optional[str] = Field(None, description="Location contact user sys_id")
    street: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None, description="City")
    state: Optional[str] = Field(None, description="State/province")
    zip: Optional[str] = Field(None, description="Postal code")
    country: Optional[str] = Field(None, description="Country")
    phone: Optional[str] = Field(None, description="Location phone number")
    fax_phone: Optional[str] = Field(None, description="Location fax phone number")
    latitude: Optional[str] = Field(None, description="Latitude")
    longitude: Optional[str] = Field(None, description="Longitude")
    time_zone: Optional[str] = Field(None, description="Time zone")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional cmn_location fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without creating")


class UpdateLocationParams(BaseModel):
    """Parameters for updating a location."""

    location_id: str = Field(..., description="Location sys_id, exact name, or exact full name")
    name: Optional[str] = Field(None, description="Location name")
    full_name: Optional[str] = Field(None, description="Full hierarchical location name")
    parent: Optional[str] = Field(None, description="Parent location sys_id/name/full name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    contact: Optional[str] = Field(None, description="Location contact user sys_id")
    street: Optional[str] = Field(None, description="Street address")
    city: Optional[str] = Field(None, description="City")
    state: Optional[str] = Field(None, description="State/province")
    zip: Optional[str] = Field(None, description="Postal code")
    country: Optional[str] = Field(None, description="Country")
    phone: Optional[str] = Field(None, description="Location phone number")
    fax_phone: Optional[str] = Field(None, description="Location fax phone number")
    latitude: Optional[str] = Field(None, description="Latitude")
    longitude: Optional[str] = Field(None, description="Longitude")
    time_zone: Optional[str] = Field(None, description="Time zone")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional cmn_location fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class DeleteLocationParams(BaseModel):
    """Parameters for deleting a location."""

    location_id: str = Field(..., description="Location sys_id, exact name, or exact full name")
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


def _normalize_location(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    location = {
        "sys_id": _display(record.get("sys_id")),
        "name": _display(record.get("name")),
        "full_name": _display(record.get("full_name")),
        "parent": _display(record.get("parent")),
        "company": _display(record.get("company")),
        "contact": _display(record.get("contact")),
        "street": _display(record.get("street")),
        "city": _display(record.get("city")),
        "state": _display(record.get("state")),
        "zip": _display(record.get("zip")),
        "country": _display(record.get("country")),
        "phone": _display(record.get("phone")),
        "fax_phone": _display(record.get("fax_phone")),
        "latitude": _display(record.get("latitude")),
        "longitude": _display(record.get("longitude")),
        "time_zone": _display(record.get("time_zone")),
    }
    if include_raw:
        location["raw"] = record
    return location


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    sys_id = _display(record.get("sys_id"))
    return str(sys_id) if sys_id else None


def _get_location_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    location_sys_id: str,
) -> Dict[str, Any]:
    response = requests.get(
        f"{config.api_url}/table/{LOCATION_TABLE}/{location_sys_id}",
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


def _resolve_location_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    location_id: str,
) -> str:
    return _resolve_record_sys_id(
        config,
        auth_manager,
        LOCATION_TABLE,
        location_id,
        ["name", "full_name"],
        "Location",
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


def _resolve_parent_location(
    config: ServerConfig,
    auth_manager: AuthManager,
    parent: Optional[str],
) -> Optional[str]:
    if not parent:
        return None
    return _resolve_location_sys_id(config, auth_manager, parent)


def _location_payload(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: Any,
) -> Dict[str, Any]:
    body = dict(getattr(params, "fields", None) or {})
    for attr in [
        "name",
        "full_name",
        "street",
        "city",
        "state",
        "zip",
        "country",
        "phone",
        "fax_phone",
        "latitude",
        "longitude",
        "time_zone",
    ]:
        value = getattr(params, attr, None)
        if value is not None:
            body[attr] = value

    parent = _resolve_parent_location(config, auth_manager, getattr(params, "parent", None))
    if parent:
        body["parent"] = parent

    company = _resolve_company_sys_id(config, auth_manager, getattr(params, "company", None))
    if company:
        body["company"] = company

    contact = _resolve_user_sys_id(config, auth_manager, getattr(params, "contact", None))
    if contact:
        body["contact"] = contact

    return _clean(body)


def list_locations(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListLocationsParams,
) -> Dict[str, Any]:
    """List locations from ServiceNow."""

    try:
        parent = _resolve_parent_location(config, auth_manager, params.parent)
        company = _resolve_company_sys_id(config, auth_manager, params.company)
        query = _query(
            [
                (
                    f"nameLIKE{params.query}^ORfull_nameLIKE{params.query}"
                    f"^ORcityLIKE{params.query}^ORstateLIKE{params.query}"
                    f"^ORcountryLIKE{params.query}^ORstreetLIKE{params.query}"
                    f"^ORphoneLIKE{params.query}"
                    if params.query
                    else None
                ),
                f"city={params.city}" if params.city else None,
                f"state={params.state}" if params.state else None,
                f"country={params.country}" if params.country else None,
                f"parent={parent}" if parent else None,
                f"company={company}" if company else None,
            ]
        )
        response = requests.get(
            f"{config.api_url}/table/{LOCATION_TABLE}",
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
        locations = [_normalize_location(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(locations)} locations",
            "locations": locations,
            "count": len(locations),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to list locations: {exc}")
        return {"success": False, "message": f"Failed to list locations: {str(exc)}"}


def get_location(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetLocationParams,
) -> Dict[str, Any]:
    """Get a location from ServiceNow by sys_id, exact name, or exact full name."""

    try:
        location_sys_id = _resolve_location_sys_id(config, auth_manager, params.location_id)
        response = requests.get(
            f"{config.api_url}/table/{LOCATION_TABLE}/{location_sys_id}",
            params=DISPLAY_PARAMS,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": "Location found",
            "location": _normalize_location(record, params.include_raw),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to get location: {exc}")
        return {"success": False, "message": f"Failed to get location: {str(exc)}"}


def create_location(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateLocationParams,
) -> Dict[str, Any]:
    """Create a location in ServiceNow."""

    try:
        body = _location_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: location would be created",
                "table": LOCATION_TABLE,
                "payload": body,
            }
        response = requests.post(
            f"{config.api_url}/table/{LOCATION_TABLE}",
            params=DISPLAY_PARAMS,
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        sys_id = _record_sys_id(record)
        if sys_id:
            record = _get_location_record(config, auth_manager, sys_id)
        return {
            "success": True,
            "message": "Location created successfully",
            "sys_id": _record_sys_id(record),
            "location": _normalize_location(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to create location: {exc}")
        return {"success": False, "message": f"Failed to create location: {str(exc)}"}


def update_location(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateLocationParams,
) -> Dict[str, Any]:
    """Update a location in ServiceNow."""

    try:
        location_sys_id = _resolve_location_sys_id(config, auth_manager, params.location_id)
        body = _location_payload(config, auth_manager, params)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: location would be updated",
                "table": LOCATION_TABLE,
                "location_id": location_sys_id,
                "payload": body,
            }
        response = requests.patch(
            f"{config.api_url}/table/{LOCATION_TABLE}/{location_sys_id}",
            params=DISPLAY_PARAMS,
            json=body,
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        record = response.json().get("result", {})
        record = _get_location_record(config, auth_manager, location_sys_id) or record
        return {
            "success": True,
            "message": "Location updated successfully",
            "sys_id": _record_sys_id(record),
            "location": _normalize_location(record, include_raw=True),
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to update location: {exc}")
        return {"success": False, "message": f"Failed to update location: {str(exc)}"}


def delete_location(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteLocationParams,
) -> Dict[str, Any]:
    """Delete a location from ServiceNow."""

    try:
        location_sys_id = _resolve_location_sys_id(config, auth_manager, params.location_id)
        if params.dry_run:
            return {
                "success": True,
                "message": "Dry run: location would be deleted",
                "table": LOCATION_TABLE,
                "location_id": location_sys_id,
            }
        response = requests.delete(
            f"{config.api_url}/table/{LOCATION_TABLE}/{location_sys_id}",
            headers=auth_manager.get_headers(),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": "Location deleted successfully",
            "location_id": location_sys_id,
        }
    except (requests.RequestException, ValueError) as exc:
        logger.error(f"Failed to delete location: {exc}")
        return {"success": False, "message": f"Failed to delete location: {str(exc)}"}
