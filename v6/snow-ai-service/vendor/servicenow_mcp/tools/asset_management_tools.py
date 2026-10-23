"""Asset management tools for ServiceNow hardware and software assets.

This module groups the asset lifecycle helpers used by the ServiceNow MCP
server to inspect, create, update, and transition asset records. The tools are
centered on common asset workflows such as assignment, stock changes,
maintenance, retirement, and disposal.

The implementation is intentionally table-driven so the same normalization and
lookup patterns can be reused across hardware, software, consumable, license,
and facility asset classes.
"""

import logging
import re
from typing import Any, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, Field, model_validator

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

AssetClass = Literal[
    "asset",
    "hardware",
    "software",
    "consumable",
    "license",
    "facility",
]
AssetLifecycleAction = Literal[
    "assign",
    "return",
    "reserve",
    "unreserve",
    "in_stock",
    "in_use",
    "on_order",
    "in_maintenance",
    "retire",
    "dispose",
    "lost",
    "stolen",
    "transfer",
    "receive",
    "consume",
    "cancel",
]

ASSET_TABLES: Dict[str, Dict[str, Any]] = {
    "asset": {
        "table": "alm_asset",
        "label": "Base asset",
        "lookup_fields": ["sys_id", "asset_tag", "serial_number", "display_name"],
    },
    "hardware": {
        "table": "alm_hardware",
        "label": "Hardware asset",
        "lookup_fields": ["sys_id", "asset_tag", "serial_number", "display_name"],
    },
    "software": {
        "table": "alm_license",
        "label": "Software license asset",
        "lookup_fields": ["sys_id", "asset_tag", "serial_number", "display_name"],
    },
    "license": {
        "table": "alm_license",
        "label": "Software license asset",
        "lookup_fields": ["sys_id", "asset_tag", "serial_number", "display_name"],
    },
    "consumable": {
        "table": "alm_consumable",
        "label": "Consumable asset",
        "lookup_fields": ["sys_id", "asset_tag", "serial_number", "display_name"],
    },
    "facility": {
        "table": "alm_facility",
        "label": "Facility asset",
        "lookup_fields": ["sys_id", "asset_tag", "display_name"],
    },
}

# install_status values of the out-of-box Asset Management plugin (verified against sys_choice on a live
# instance). "Reserved" is a substate of In stock / In transit, not a state: "reserved" maps to In stock.
STATE_VALUES: Dict[str, str] = {
    "on_order": "2",
    "in_stock": "6",
    "in_use": "1",
    "in_maintenance": "3",
    "retired": "7",
    "missing": "8",
    "in_transit": "9",
    "consumed": "10",
    "build": "11",
    "reserved": "6",
}

# substatus values that exist out of the box (there is no "assigned", "in_transit" or "consumed" substate)
SUBSTATE_VALUES: Dict[str, str] = {
    "available": "available",
    "reserved": "reserved",
    "pending_transfer": "pending_transfer",
    "in_repair": "in_repair",
    "pending_repair": "pending_repair",
    "disposed": "disposed",
    "lost": "lost",
    "stolen": "stolen",
    "sold": "sold",
}

OPTIONAL_ASSET_TABLES = {
    "alm_consumable": "Hardware Asset Management consumable assets",
    "alm_facility": "Hardware Asset Management facility assets",
    "alm_license": "Software Asset Management license assets",
    "alm_transfer_order": "Asset transfer order records",
    "alm_transfer_order_line": "Asset transfer order line records",
    "ast_contract": "Contract Management asset contract records",
    "contract": "Legacy or instance-specific contract records",
    "clm_m2m_contract_asset": "Contract-to-asset linkage records",
    "sn_hamp_hw_model_lifecycle": "Hardware Asset Management lifecycle data",
    "samp_sw_product": "Software Asset Management product data",
    "sn_itam_asset_attestation": "ITAM asset attestation data",
}


class AssetManagementMetadataParams(BaseModel):
    """Parameters for Asset Management Metadata tool calls."""
    asset_class: Optional[AssetClass] = Field(
        None, description="Specific asset class metadata to return"
    )


class ListAssetsParams(BaseModel):
    """Parameters for List Assets tool calls."""
    asset_class: AssetClass = Field("asset", description="Asset table/class to query")
    query: Optional[str] = Field(None, description="Search asset tag, serial, display name, model")
    encoded_query: Optional[str] = Field(None, description="Additional ServiceNow encoded query")
    stockroom: Optional[str] = Field(None, description="Stockroom sys_id/name filter")
    assigned_to: Optional[str] = Field(None, description="Assigned user sys_id/user name filter")
    company: Optional[str] = Field(None, description="Company sys_id/name filter")
    location: Optional[str] = Field(None, description="Location sys_id/name filter")
    department: Optional[str] = Field(None, description="Department sys_id/name filter")
    cost_center: Optional[str] = Field(None, description="Cost center sys_id/name/code filter")
    install_status: Optional[str] = Field(None, description="Install status value")
    substatus: Optional[str] = Field(None, description="Substatus value")
    limit: int = Field(25, description="Maximum assets to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetAssetParams(BaseModel):
    """Parameters for Get Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class to query")
    include_raw: bool = Field(False, description="Include raw ServiceNow record")


class CreateHardwareAssetParams(BaseModel):
    """Parameters for Create Hardware Asset tool calls."""
    asset_tag: Optional[str] = Field(None, description="Asset tag")
    serial_number: Optional[str] = Field(None, description="Serial number")
    display_name: Optional[str] = Field(None, description="Display name")
    model: Optional[str] = Field(None, description="Model sys_id or name")
    stockroom: Optional[str] = Field(None, description="Stockroom sys_id or name")
    assigned_to: Optional[str] = Field(None, description="Assigned user sys_id or user name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id or name")
    cost_center: Optional[str] = Field(None, description="Cost center sys_id, name, or code")
    install_status: Optional[str] = Field("6", description="Install status, default In Stock")
    substatus: Optional[str] = Field("available", description="Asset substatus")
    cost: Optional[str] = Field(None, description="Asset cost")
    purchase_date: Optional[str] = Field(None, description="Purchase date YYYY-MM-DD")
    warranty_expiration: Optional[str] = Field(None, description="Warranty expiration date")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional alm_hardware fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without creating")

    @model_validator(mode="after")
    def _validate_identity(self) -> "CreateHardwareAssetParams":
        if not (self.asset_tag or self.serial_number or self.display_name):
            raise ValueError("Provide asset_tag, serial_number, or display_name")
        return self


class CreateSoftwareAssetParams(BaseModel):
    """Parameters for Create Software Asset tool calls."""
    asset_tag: Optional[str] = Field(None, description="Asset/license tag")
    display_name: Optional[str] = Field(None, description="Display name")
    model: Optional[str] = Field(None, description="Software model sys_id or name")
    assigned_to: Optional[str] = Field(None, description="Assigned user sys_id or user name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id or name")
    cost_center: Optional[str] = Field(None, description="Cost center sys_id, name, or code")
    install_status: Optional[str] = Field("6", description="Install status, default In Stock")
    substatus: Optional[str] = Field("available", description="Asset substatus")
    license_count: Optional[int] = Field(None, description="License entitlement/right count")
    cost: Optional[str] = Field(None, description="License cost")
    purchase_date: Optional[str] = Field(None, description="Purchase date YYYY-MM-DD")
    expiration_date: Optional[str] = Field(None, description="License expiration date")
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional alm_license fields"
    )
    dry_run: bool = Field(False, description="Validate and show changes without creating")

    @model_validator(mode="after")
    def _validate_identity(self) -> "CreateSoftwareAssetParams":
        if not (self.asset_tag or self.display_name or self.model):
            raise ValueError("Provide asset_tag, display_name, or model")
        return self


class UpdateAssetParams(BaseModel):
    """Parameters for Update Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class to update")
    asset_tag: Optional[str] = Field(None, description="Asset tag")
    serial_number: Optional[str] = Field(None, description="Serial number")
    display_name: Optional[str] = Field(None, description="Display name")
    model: Optional[str] = Field(None, description="Model sys_id or name")
    stockroom: Optional[str] = Field(None, description="Stockroom sys_id or name")
    assigned_to: Optional[str] = Field(None, description="Assigned user sys_id or user name")
    company: Optional[str] = Field(None, description="Company sys_id or name")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id or name")
    cost_center: Optional[str] = Field(None, description="Cost center sys_id, name, or code")
    install_status: Optional[str] = Field(None, description="Install status value")
    substatus: Optional[str] = Field(None, description="Substatus value")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class DeleteAssetParams(BaseModel):
    """Parameters for Delete Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class to delete")
    dry_run: bool = Field(False, description="Validate and show target without deleting")
    confirm: bool = Field(False, description="Must be true to delete (deleting an asset cannot be undone)")


class AssetLifecycleActionParams(BaseModel):
    """Parameters for Asset Lifecycle Action tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class to update")
    action: AssetLifecycleAction = Field(..., description="Lifecycle action to perform")
    assigned_to: Optional[str] = Field(None, description="User sys_id or user name for assignment")
    reserved_for: Optional[str] = Field(
        None, description="User sys_id or user name for reservation"
    )
    stockroom: Optional[str] = Field(None, description="Stockroom sys_id or name")
    destination_stockroom: Optional[str] = Field(None, description="Destination stockroom")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id or name")
    cost_center: Optional[str] = Field(None, description="Cost center sys_id, name, or code")
    quantity: Optional[int] = Field(None, description="Quantity for consumable actions")
    comments: Optional[str] = Field(None, description="Work notes/comments")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class AssignAssetParams(BaseModel):
    """Parameters for Assign Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class")
    assigned_to: str = Field(..., description="User sys_id, user name, or email")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    department: Optional[str] = Field(None, description="Department sys_id or name")
    cost_center: Optional[str] = Field(None, description="Cost center sys_id, name, or code")
    comments: Optional[str] = Field(None, description="Work notes/comments")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without assigning")


class ReturnAssetParams(BaseModel):
    """Parameters for Return Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class")
    stockroom: Optional[str] = Field(None, description="Return stockroom sys_id or name")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    comments: Optional[str] = Field(None, description="Work notes/comments")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without returning")


class ReserveAssetParams(BaseModel):
    """Parameters for Reserve Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, asset tag, serial number, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class")
    reserved_for: str = Field(..., description="User sys_id, user name, or email")
    comments: Optional[str] = Field(None, description="Work notes/comments")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without reserving")


class ListStockroomsParams(BaseModel):
    """Parameters for List Stockrooms tool calls."""
    query: Optional[str] = Field(None, description="Search name, code, manager, or location")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    manager: Optional[str] = Field(None, description="Manager sys_id or user name")
    active: Optional[bool] = Field(None, description="Filter active stockrooms")
    limit: int = Field(25, description="Maximum stockrooms to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw records")


class GetStockroomParams(BaseModel):
    """Parameters for Get Stockroom tool calls."""
    stockroom_id: str = Field(..., description="Stockroom sys_id, name, or code")
    include_raw: bool = Field(False, description="Include raw record")


class CreateStockroomParams(BaseModel):
    """Parameters for Create Stockroom tool calls."""
    name: str = Field(..., description="Stockroom name")
    code: Optional[str] = Field(None, description="Stockroom code")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    manager: Optional[str] = Field(None, description="Manager sys_id or user name")
    active: Optional[bool] = Field(True, description="Whether stockroom is active")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without creating")


class UpdateStockroomParams(BaseModel):
    """Parameters for Update Stockroom tool calls."""
    stockroom_id: str = Field(..., description="Stockroom sys_id, name, or code")
    name: Optional[str] = Field(None, description="Stockroom name")
    code: Optional[str] = Field(None, description="Stockroom code")
    location: Optional[str] = Field(None, description="Location sys_id or name")
    manager: Optional[str] = Field(None, description="Manager sys_id or user name")
    active: Optional[bool] = Field(None, description="Whether stockroom is active")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without updating")


class DeleteStockroomParams(BaseModel):
    """Parameters for Delete Stockroom tool calls."""
    stockroom_id: str = Field(..., description="Stockroom sys_id, name, or code")
    dry_run: bool = Field(False, description="Validate and show target without deleting")


class TransferAssetParams(BaseModel):
    """Parameters for Transfer Asset tool calls."""
    asset_id: str = Field(..., description="Asset sys_id, tag, serial, or name")
    asset_class: AssetClass = Field("asset", description="Asset table/class")
    from_stockroom: Optional[str] = Field(None, description="Source stockroom sys_id/name")
    to_stockroom: str = Field(..., description="Destination stockroom sys_id/name")
    quantity: Optional[int] = Field(None, description="Quantity for consumable transfers")
    transfer_order: Optional[str] = Field(None, description="Existing transfer order sys_id/number")
    create_transfer_order: bool = Field(
        True, description="Create a transfer order when one is not supplied"
    )
    ship: bool = Field(False, description="Mark transferred asset as in transit")
    receive: bool = Field(False, description="Immediately receive into destination stockroom")
    comments: Optional[str] = Field(None, description="Comments/work notes")
    dry_run: bool = Field(False, description="Validate and show transfer without updating")


class ListAssetModelsParams(BaseModel):
    """Parameters for List Asset Models tool calls."""
    query: Optional[str] = Field(None, description="Search model name, number, or manufacturer")
    model_category: Optional[str] = Field(None, description="Model category sys_id/name")
    manufacturer: Optional[str] = Field(None, description="Manufacturer sys_id/name")
    asset_tracking_strategy: Optional[str] = Field(None, description="Tracking strategy value")
    limit: int = Field(25, description="Maximum models to return")
    offset: int = Field(0, description="Pagination offset")
    include_raw: bool = Field(False, description="Include raw records")


class CreateAssetModelParams(BaseModel):
    """Parameters for Create Asset Model tool calls."""
    name: str = Field(..., description="Model name")
    model_number: Optional[str] = Field(None, description="Model number")
    manufacturer: Optional[str] = Field(None, description="Manufacturer sys_id/name")
    model_category: Optional[str] = Field(None, description="Model category sys_id/name")
    cost: Optional[str] = Field(None, description="Model cost")
    asset_tracking_strategy: Optional[str] = Field(None, description="Tracking strategy")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional cmdb_model fields")
    dry_run: bool = Field(False, description="Validate and show changes without creating")


class UpdateAssetModelParams(BaseModel):
    """Parameters for Update Asset Model tool calls."""
    model_id: str = Field(..., description="Model sys_id, name, or model number")
    name: Optional[str] = Field(None, description="Model name")
    model_number: Optional[str] = Field(None, description="Model number")
    manufacturer: Optional[str] = Field(None, description="Manufacturer sys_id/name")
    model_category: Optional[str] = Field(None, description="Model category sys_id/name")
    cost: Optional[str] = Field(None, description="Model cost")
    asset_tracking_strategy: Optional[str] = Field(None, description="Tracking strategy")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Additional fields")
    dry_run: bool = Field(False, description="Validate and show changes without updating")


def _headers(auth_manager: AuthManager) -> Dict[str, str]:
    headers = auth_manager.get_headers()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    return headers


def _table_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _ref(value: Any) -> Optional[Dict[str, Any]]:
    raw = _value(value)
    display = _display(value)
    if raw in (None, "") and display in (None, ""):
        return None
    return {
        "value": None if raw in (None, "") else str(raw),
        "display_value": None if display in (None, "") else str(display),
    }


def _bool_text(value: Optional[bool]) -> Optional[str]:
    if value is None:
        return None
    return "true" if value else "false"


def _clean(body: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in body.items() if value is not None}


def _table_for_asset(asset_class: str) -> str:
    profile = ASSET_TABLES.get(asset_class)
    if not profile:
        raise ValueError(f"Unsupported asset_class: {asset_class}")
    return str(profile["table"])


def _normalize_state_value(field: str, value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    if field == "install_status":
        return STATE_VALUES.get(text, text)
    if field == "substatus":
        return SUBSTATE_VALUES.get(text, text)
    return value


def _normalize_state_fields(body: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(body)
    for field in ("install_status", "substatus"):
        if field in normalized:
            normalized[field] = _normalize_state_value(field, normalized[field])
    return normalized


def _dry_run_result(
    operation: str,
    table: str,
    body: Optional[Dict[str, Any]] = None,
    sys_id: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    result = {
        "success": True,
        "dry_run": True,
        "message": f"Dry run: would {operation}",
        "operation": operation,
        "table": table,
    }
    if sys_id:
        result["sys_id"] = sys_id
    if body is not None:
        result["request_body"] = body
    result.update(extra)
    return result


def _request(
    config: ServerConfig,
    auth_manager: AuthManager,
    method: str,
    table: str,
    sys_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Any:
    response = requests.request(
        method,
        _table_url(config, table, sys_id),
        headers=_headers(auth_manager),
        params=params,
        json=body,
        timeout=config.timeout,
    )
    response.raise_for_status()
    payload = response.json() if response.content else {}
    return payload.get("result", payload)


def _list_records(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query: Optional[str],
    limit: int,
    offset: int,
) -> List[Dict[str, Any]]:
    result = _request(
        config,
        auth_manager,
        "GET",
        table,
        params={
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            **({"sysparm_query": query} if query else {}),
        },
    )
    return result if isinstance(result, list) else []


def _check_optional_table(
    config: ServerConfig, auth_manager: AuthManager, table: str
) -> Dict[str, Any]:
    status = {
        "table": table,
        "label": OPTIONAL_ASSET_TABLES.get(table),
        "available": None,
        "checked": False,
    }
    if table not in OPTIONAL_ASSET_TABLES:
        return status
    try:
        records = _list_records(config, auth_manager, "sys_db_object", f"name={table}", 1, 0)
        status["checked"] = True
        status["available"] = bool(records)
        return status
    except requests.exceptions.RequestException as exc:
        status["message"] = f"Could not verify table availability: {exc}"
        return status


def _ensure_optional_table(
    config: ServerConfig, auth_manager: AuthManager, table: str
) -> Dict[str, Any]:
    status = _check_optional_table(config, auth_manager, table)
    if status.get("checked") and status.get("available") is False:
        raise ValueError(
            f"Optional ServiceNow table {table} is not available. "
            f"Install or activate {OPTIONAL_ASSET_TABLES.get(table, 'the required plugin')}."
        )
    return status


def _ensure_asset_table_available(
    config: ServerConfig, auth_manager: AuthManager, asset_class: str
) -> Optional[Dict[str, Any]]:
    table = _table_for_asset(asset_class)
    if table in OPTIONAL_ASSET_TABLES:
        return _ensure_optional_table(config, auth_manager, table)
    return None


def _resolve_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    lookup_fields: List[str],
) -> Dict[str, Any]:
    cleaned = str(identifier).strip()
    if not cleaned:
        raise ValueError("Identifier cannot be empty")
    params = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    if SYS_ID_PATTERN.fullmatch(cleaned):
        result = _request(config, auth_manager, "GET", table, cleaned, params=params)
        if isinstance(result, dict) and result:
            return result
        raise ValueError(f"{table} record not found: {identifier}")

    query = "^OR".join(f"{field}={cleaned}" for field in lookup_fields)
    records = _list_records(config, auth_manager, table, query, 20, 0)
    if not records:
        raise ValueError(f"{table} record not found: {identifier}")

    normalized = cleaned.casefold()
    exact = []
    for record in records:
        for field in lookup_fields:
            if str(_display(record.get(field)) or "").strip().casefold() == normalized:
                exact.append(record)
                break
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"Multiple {table} records matched: {identifier}")
    if len(records) == 1:
        return records[0]
    raise ValueError(f"Multiple {table} records matched: {identifier}")


def _resolve_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: Optional[str],
    lookup_fields: List[str],
) -> Optional[str]:
    if identifier is None:
        return None
    if SYS_ID_PATTERN.fullmatch(str(identifier).strip()):
        return str(identifier).strip()
    record = _resolve_record(config, auth_manager, table, identifier, lookup_fields)
    sys_id = _value(record.get("sys_id"))
    if not sys_id:
        raise ValueError(f"Resolved {table} record has no sys_id: {identifier}")
    return str(sys_id)


def _asset_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    asset_class: str,
    asset_id: str,
) -> Dict[str, Any]:
    profile = ASSET_TABLES[asset_class]
    return _resolve_record(
        config,
        auth_manager,
        str(profile["table"]),
        asset_id,
        list(profile["lookup_fields"]),
    )


def _asset_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    asset_class: str,
    asset_id: str,
) -> str:
    record = _asset_record(config, auth_manager, asset_class, asset_id)
    sys_id = _value(record.get("sys_id"))
    if not sys_id:
        raise ValueError(f"Asset has no sys_id: {asset_id}")
    return str(sys_id)


def _duplicate_asset_candidates(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    asset_tag: Optional[str],
    serial_number: Optional[str],
) -> List[Dict[str, Any]]:
    checks = []
    if asset_tag:
        checks.append(f"asset_tag={asset_tag}")
    if serial_number:
        checks.append(f"serial_number={serial_number}")
    if not checks:
        return []

    records = _list_records(config, auth_manager, table, "^OR".join(checks), 10, 0)
    asset_tag_key = asset_tag.strip().casefold() if asset_tag else None
    serial_key = serial_number.strip().casefold() if serial_number else None
    duplicates = []
    for record in records:
        record_tag = str(_display(record.get("asset_tag")) or "").strip().casefold()
        record_serial = str(_display(record.get("serial_number")) or "").strip().casefold()
        if (asset_tag_key and record_tag == asset_tag_key) or (
            serial_key and record_serial == serial_key
        ):
            duplicates.append(_normalize_asset(record, False))
    return duplicates


def _query(parts: List[Optional[str]]) -> Optional[str]:
    return "^".join(part for part in parts if part)


def _normalize_asset(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _value(record.get("sys_id")),
        "asset_tag": _display(record.get("asset_tag")),
        "serial_number": _display(record.get("serial_number")),
        "display_name": _display(record.get("display_name") or record.get("name")),
        "model": _ref(record.get("model")),
        "model_category": _ref(record.get("model_category")),
        "stockroom": _ref(record.get("stockroom")),
        "assigned_to": _ref(record.get("assigned_to")),
        "reserved_for": _ref(record.get("reserved_for")),
        "company": _ref(record.get("company")),
        "location": _ref(record.get("location")),
        "department": _ref(record.get("department")),
        "cost_center": _ref(record.get("cost_center")),
        "install_status": _display(record.get("install_status")),
        "substatus": _display(record.get("substatus")),
        "quantity": _display(record.get("quantity")),
        "cost": _display(record.get("cost")),
        "purchase_date": _display(record.get("purchase_date")),
        "warranty_expiration": _display(record.get("warranty_expiration")),
        "retirement_date": _display(record.get("retirement_date")),
        "created_on": _display(record.get("sys_created_on")),
        "updated_on": _display(record.get("sys_updated_on")),
    }
    if include_raw:
        data["raw"] = record
    return data


def _normalize_stockroom(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _value(record.get("sys_id")),
        "name": _display(record.get("name")),
        "code": _display(record.get("code")),
        "location": _ref(record.get("location")),
        "manager": _ref(record.get("manager")),
        "active": _display(record.get("active")),
    }
    if include_raw:
        data["raw"] = record
    return data


def _normalize_model(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _value(record.get("sys_id")),
        "name": _display(record.get("name")),
        "model_number": _display(record.get("model_number")),
        "manufacturer": _ref(record.get("manufacturer")),
        "model_category": _ref(record.get("cmdb_model_category")),
        "cost": _display(record.get("cost")),
        "asset_tracking_strategy": _display(record.get("asset_tracking_strategy")),
    }
    if include_raw:
        data["raw"] = record
    return data


def _resolve_common_refs(
    config: ServerConfig,
    auth_manager: AuthManager,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    ref_specs = {
        "model": ("cmdb_model", ["name", "model_number"]),
        "stockroom": ("alm_stockroom", ["name", "code"]),
        "assigned_to": ("sys_user", ["user_name", "email", "name"]),
        "reserved_for": ("sys_user", ["user_name", "email", "name"]),
        "company": ("core_company", ["name"]),
        "location": ("cmn_location", ["name", "full_name"]),
        "department": ("cmn_department", ["name"]),
        "cost_center": ("cmn_cost_center", ["name", "code"]),
        "manager": ("sys_user", ["user_name", "email", "name"]),
        "manufacturer": ("core_company", ["name"]),
        "model_category": ("cmdb_model_category", ["name"]),
        "cmdb_model_category": ("cmdb_model_category", ["name"]),
    }
    resolved = dict(body)
    for field, (table, lookups) in ref_specs.items():
        value = resolved.get(field)
        if value not in (None, ""):
            try:
                resolved[field] = _resolve_sys_id(config, auth_manager, table, str(value), lookups)
            except ValueError as exc:
                if "not found" in str(exc).lower():
                    created_sys_id = _auto_create_reference(config, auth_manager, table, str(value))
                    if created_sys_id:
                        resolved[field] = created_sys_id
                        continue
                    raise ComponentResolutionError(
                        table, {"field": field, "identifier": str(value)}, str(exc)
                    ) from exc
                raise
    return resolved


def _asset_payload(params: Any) -> Dict[str, Any]:
    body = dict(getattr(params, "fields", None) or {})
    for attr in [
        "asset_tag",
        "serial_number",
        "display_name",
        "model",
        "stockroom",
        "assigned_to",
        "company",
        "location",
        "department",
        "cost_center",
        "install_status",
        "substatus",
        "cost",
        "purchase_date",
        "warranty_expiration",
        "expiration_date",
        "license_count",
    ]:
        value = getattr(params, attr, None)
        if value is not None:
            field = "rights" if attr == "license_count" else attr
            body[field] = value
    return _normalize_state_fields(_clean(body))


def get_asset_management_metadata(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: AssetManagementMetadataParams,
) -> Dict[str, Any]:
    """Return static metadata used by asset-management tools.

    Provides class profiles, lifecycle action names, and optional plugin table
    hints used for capability-aware workflows.
    """
    del config, auth_manager
    if params.asset_class:
        return {
            "success": True,
            "asset_class": params.asset_class,
            "profile": ASSET_TABLES[params.asset_class],
            "lifecycle_actions": list(AssetLifecycleAction.__args__),
            "optional_tables": OPTIONAL_ASSET_TABLES,
        }
    return {
        "success": True,
        "asset_classes": ASSET_TABLES,
        "lifecycle_actions": list(AssetLifecycleAction.__args__),
        "state_values": STATE_VALUES,
        "substatus_values": SUBSTATE_VALUES,
        "optional_tables": OPTIONAL_ASSET_TABLES,
    }


def list_assets(
    config: ServerConfig, auth_manager: AuthManager, params: ListAssetsParams
) -> Dict[str, Any]:
    """List assets for ServiceNow MCP workflows.

    Applies class-aware filters, resolves reference identifiers, and returns
    normalized asset records with paging metadata.
    """
    try:
        table = _table_for_asset(params.asset_class)
        _ensure_asset_table_available(config, auth_manager, params.asset_class)
        refs = _resolve_common_refs(
            config,
            auth_manager,
            _clean(
                {
                    "stockroom": params.stockroom,
                    "assigned_to": params.assigned_to,
                    "company": params.company,
                    "location": params.location,
                    "department": params.department,
                    "cost_center": params.cost_center,
                }
            ),
        )
        install_status = _normalize_state_value("install_status", params.install_status)
        substatus = _normalize_state_value("substatus", params.substatus)
        query = _query(
            [
                (
                    f"asset_tagLIKE{params.query}^ORserial_numberLIKE{params.query}"
                    f"^ORdisplay_nameLIKE{params.query}^ORmodel.nameLIKE{params.query}"
                    if params.query
                    else None
                ),
                f"stockroom={refs['stockroom']}" if refs.get("stockroom") else None,
                f"assigned_to={refs['assigned_to']}" if refs.get("assigned_to") else None,
                f"company={refs['company']}" if refs.get("company") else None,
                f"location={refs['location']}" if refs.get("location") else None,
                f"department={refs['department']}" if refs.get("department") else None,
                f"cost_center={refs['cost_center']}" if refs.get("cost_center") else None,
                f"install_status={install_status}" if params.install_status else None,
                f"substatus={substatus}" if params.substatus else None,
                params.encoded_query,
            ]
        )
        records = _list_records(config, auth_manager, table, query, params.limit, params.offset)
        assets = [_normalize_asset(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(assets)} assets",
            "assets": assets,
            "count": len(assets),
            "limit": params.limit,
            "offset": params.offset,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error listing assets: %s", exc)
        return {"success": False, "message": f"Error listing assets: {exc}", "assets": []}


def get_asset(
    config: ServerConfig, auth_manager: AuthManager, params: GetAssetParams
) -> Dict[str, Any]:
    """Get one asset by id or lookup key for the requested asset class."""
    try:
        _ensure_asset_table_available(config, auth_manager, params.asset_class)
        record = _asset_record(config, auth_manager, params.asset_class, params.asset_id)
        return {
            "success": True,
            "message": "Retrieved asset",
            "asset": _normalize_asset(record, params.include_raw),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error getting asset: %s", exc)
        return {"success": False, "message": f"Error getting asset: {exc}"}


def create_hardware_asset(
    config: ServerConfig, auth_manager: AuthManager, params: CreateHardwareAssetParams
) -> Dict[str, Any]:
    """Create a hardware asset with duplicate checks and dry-run support."""
    try:
        table_status = _ensure_asset_table_available(config, auth_manager, "hardware")
        body = _resolve_common_refs(config, auth_manager, _asset_payload(params))
        duplicates = _duplicate_asset_candidates(
            config, auth_manager, "alm_hardware", params.asset_tag, params.serial_number
        )
        if duplicates:
            return {
                "success": False,
                "message": "Duplicate hardware asset found for asset_tag or serial_number",
                "duplicates": duplicates,
            }
        if params.dry_run:
            return _dry_run_result(
                "create hardware asset",
                "alm_hardware",
                body,
                table_status=table_status,
                duplicates=[],
            )
        record = _request(config, auth_manager, "POST", "alm_hardware", body=body)
        return {
            "success": True,
            "message": f"Created hardware asset {record.get('asset_tag') or record.get('sys_id')}",
            "asset": _normalize_asset(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error creating hardware asset: %s", exc)
        return {"success": False, "message": f"Error creating hardware asset: {exc}"}


def create_software_asset(
    config: ServerConfig, auth_manager: AuthManager, params: CreateSoftwareAssetParams
) -> Dict[str, Any]:
    """Create a software-license asset with duplicate detection safeguards."""
    try:
        table_status = _ensure_asset_table_available(config, auth_manager, "software")
        body = _resolve_common_refs(config, auth_manager, _asset_payload(params))
        duplicates = _duplicate_asset_candidates(
            config, auth_manager, "alm_license", params.asset_tag, None
        )
        if duplicates:
            return {
                "success": False,
                "message": "Duplicate software asset found for asset_tag",
                "duplicates": duplicates,
            }
        if params.dry_run:
            return _dry_run_result(
                "create software asset",
                "alm_license",
                body,
                table_status=table_status,
                duplicates=[],
            )
        record = _request(config, auth_manager, "POST", "alm_license", body=body)
        return {
            "success": True,
            "message": f"Created software asset {record.get('asset_tag') or record.get('sys_id')}",
            "asset": _normalize_asset(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error creating software asset: %s", exc)
        return {"success": False, "message": f"Error creating software asset: {exc}"}


def update_asset(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateAssetParams
) -> Dict[str, Any]:
    """Update an asset record with class-aware validation and dedupe checks."""
    try:
        table = _table_for_asset(params.asset_class)
        table_status = _ensure_asset_table_available(config, auth_manager, params.asset_class)
        sys_id = _asset_sys_id(config, auth_manager, params.asset_class, params.asset_id)
        body = _resolve_common_refs(config, auth_manager, _asset_payload(params))
        if not body:
            return {"success": False, "message": "No asset fields supplied to update"}
        duplicates = _duplicate_asset_candidates(
            config, auth_manager, table, params.asset_tag, params.serial_number
        )
        duplicates = [item for item in duplicates if item.get("sys_id") != sys_id]
        if duplicates:
            return {
                "success": False,
                "message": "Duplicate asset found for asset_tag or serial_number",
                "duplicates": duplicates,
            }
        if params.dry_run:
            return _dry_run_result(
                "update asset",
                table,
                body,
                sys_id=sys_id,
                table_status=table_status,
                duplicates=[],
            )
        record = _request(config, auth_manager, "PATCH", table, sys_id, body=body)
        return {
            "success": True,
            "message": f"Updated asset {record.get('asset_tag') or sys_id}",
            "asset": _normalize_asset(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error updating asset: %s", exc)
        return {"success": False, "message": f"Error updating asset: {exc}"}


def delete_asset(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteAssetParams
) -> Dict[str, Any]:
    """Delete an asset record, optionally as a dry-run preview."""
    try:
        table = _table_for_asset(params.asset_class)
        table_status = _ensure_asset_table_available(config, auth_manager, params.asset_class)
        sys_id = _asset_sys_id(config, auth_manager, params.asset_class, params.asset_id)
        if params.dry_run:
            return _dry_run_result("delete asset", table, sys_id=sys_id, table_status=table_status)
        if not params.confirm:
            return {"success": False, "requires_confirmation": True, "sys_id": sys_id,
                    "message": "Deleting an asset cannot be undone (prefer retiring it). Call again with confirm=true."}
        _request(config, auth_manager, "DELETE", table, sys_id)
        try:
            _request(config, auth_manager, "GET", table, sys_id)
            still_there = True
        except requests.exceptions.RequestException:
            still_there = False
        if still_there:
            return {"success": False, "sys_id": sys_id,
                    "message": "The delete was accepted but the asset still exists (a business rule or ACL kept it)"}
        return {"success": True, "verified": True, "message": f"Deleted asset {params.asset_id}", "sys_id": sys_id}
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error deleting asset: %s", exc)
        return {"success": False, "message": f"Error deleting asset: {exc}"}


def perform_asset_lifecycle_action(
    config: ServerConfig, auth_manager: AuthManager, params: AssetLifecycleActionParams
) -> Dict[str, Any]:
    """Apply a lifecycle action (assign, reserve, transfer, retire, and more)."""
    try:
        table = _table_for_asset(params.asset_class)
        table_status = _ensure_asset_table_available(config, auth_manager, params.asset_class)
        sys_id = _asset_sys_id(config, auth_manager, params.asset_class, params.asset_id)
        body = build_lifecycle_body(config, auth_manager, params)
        if params.dry_run:
            return _dry_run_result(
                f"perform {params.action} on asset",
                table,
                body,
                sys_id=sys_id,
                table_status=table_status,
            )
        record = _request(config, auth_manager, "PATCH", table, sys_id, body=body)
        return {
            "success": True,
            "message": f"Performed {params.action} on asset {params.asset_id}",
            "asset": _normalize_asset(record, True),
            "request_body": body,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error performing asset lifecycle action: %s", exc)
        return {"success": False, "message": f"Error performing asset lifecycle action: {exc}"}


def build_lifecycle_body(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: AssetLifecycleActionParams,
) -> Dict[str, Any]:
    """Build a state-safe field payload for a lifecycle transition request."""
    body = dict(params.fields or {})
    action = params.action
    if action == "assign":
        if not params.assigned_to:
            raise ValueError("assigned_to is required for assign")
        body.update(
            {
                "assigned_to": params.assigned_to,
                "install_status": STATE_VALUES["in_use"],
                "substatus": "",
                "stockroom": "",
            }
        )
    elif action == "return":
        body.update(
            {
                "assigned_to": "",
                "reserved_for": "",
                "install_status": STATE_VALUES["in_stock"],
                "substatus": SUBSTATE_VALUES["available"],
            }
        )
        if params.stockroom:
            body["stockroom"] = params.stockroom
    elif action == "reserve":
        if not params.reserved_for:
            raise ValueError("reserved_for is required for reserve")
        body.update(
            {
                "reserved_for": params.reserved_for,
                "install_status": STATE_VALUES["reserved"],
                "substatus": SUBSTATE_VALUES["reserved"],
            }
        )
    elif action == "unreserve":
        body.update({"reserved_for": "", "install_status": STATE_VALUES["in_stock"]})
        body["substatus"] = SUBSTATE_VALUES["available"]
    elif action == "in_stock":
        body.update({"install_status": STATE_VALUES["in_stock"]})
        body.setdefault("substatus", SUBSTATE_VALUES["available"])
    elif action == "in_use":
        body.update({"install_status": STATE_VALUES["in_use"]})
        if params.assigned_to:
            body["assigned_to"] = params.assigned_to
        body.setdefault("substatus", "")
    elif action == "on_order":
        body.update({"install_status": STATE_VALUES["on_order"]})
    elif action == "in_maintenance":
        body.update({"install_status": STATE_VALUES["in_maintenance"]})
    elif action == "retire":
        body.update({"install_status": STATE_VALUES["retired"]})
        if not body.get("substatus"):
            raise ValueError("A retired substate is required (e.g. disposed, sold, donated, obsolete)")
    elif action == "dispose":
        body.update({"install_status": STATE_VALUES["retired"], "substatus": "disposed"})
    elif action in {"lost", "stolen"}:
        body.update({"install_status": STATE_VALUES["missing"], "substatus": action})
    elif action == "transfer":
        if not params.destination_stockroom:
            raise ValueError("destination_stockroom is required for transfer")
        body.update(
            {
                "stockroom": params.destination_stockroom,
                "install_status": STATE_VALUES["in_stock"],
                "substatus": SUBSTATE_VALUES["pending_transfer"],
            }
        )
    elif action == "receive":
        if params.stockroom:
            body["stockroom"] = params.stockroom
        body.update({"install_status": STATE_VALUES["in_stock"]})
        body.setdefault("substatus", SUBSTATE_VALUES["available"])
    elif action == "consume":
        body.update({"install_status": STATE_VALUES["consumed"], "substatus": ""})
        if params.quantity is not None:
            body["quantity"] = str(params.quantity)
    elif action == "cancel":
        pass  # no "cancelled" substate exists; cancel only applies the supplied fields

    if params.location:
        body["location"] = params.location
    if params.department:
        body["department"] = params.department
    if params.cost_center:
        body["cost_center"] = params.cost_center
    if params.comments:
        body["comments"] = params.comments
        body["work_notes"] = params.comments
    return _resolve_common_refs(config, auth_manager, _normalize_state_fields(_clean(body)))


def assign_asset(
    config: ServerConfig, auth_manager: AuthManager, params: AssignAssetParams
) -> Dict[str, Any]:
    """Assign an asset to a user through the lifecycle-action pathway."""
    lifecycle_params = AssetLifecycleActionParams(
        asset_id=params.asset_id,
        asset_class=params.asset_class,
        action="assign",
        assigned_to=params.assigned_to,
        location=params.location,
        department=params.department,
        cost_center=params.cost_center,
        comments=params.comments,
        fields=params.fields,
        dry_run=params.dry_run,
    )
    return perform_asset_lifecycle_action(config, auth_manager, lifecycle_params)


def return_asset(
    config: ServerConfig, auth_manager: AuthManager, params: ReturnAssetParams
) -> Dict[str, Any]:
    """Return an assigned asset to stock using lifecycle transition logic."""
    lifecycle_params = AssetLifecycleActionParams(
        asset_id=params.asset_id,
        asset_class=params.asset_class,
        action="return",
        stockroom=params.stockroom,
        location=params.location,
        comments=params.comments,
        fields=params.fields,
        dry_run=params.dry_run,
    )
    return perform_asset_lifecycle_action(config, auth_manager, lifecycle_params)


def reserve_asset(
    config: ServerConfig, auth_manager: AuthManager, params: ReserveAssetParams
) -> Dict[str, Any]:
    """Reserve an asset for a user via the shared lifecycle action engine."""
    lifecycle_params = AssetLifecycleActionParams(
        asset_id=params.asset_id,
        asset_class=params.asset_class,
        action="reserve",
        reserved_for=params.reserved_for,
        comments=params.comments,
        fields=params.fields,
        dry_run=params.dry_run,
    )
    return perform_asset_lifecycle_action(config, auth_manager, lifecycle_params)


def list_stockrooms(
    config: ServerConfig, auth_manager: AuthManager, params: ListStockroomsParams
) -> Dict[str, Any]:
    """List stockrooms with optional location and manager filtering."""
    try:
        refs = _resolve_common_refs(
            config,
            auth_manager,
            _clean({"location": params.location, "manager": params.manager}),
        )
        query = _query(
            [
                (
                    f"nameLIKE{params.query}^ORcodeLIKE{params.query}"
                    f"^ORlocation.nameLIKE{params.query}^ORmanager.nameLIKE{params.query}"
                    if params.query
                    else None
                ),
                f"location={refs['location']}" if refs.get("location") else None,
                f"manager={refs['manager']}" if refs.get("manager") else None,
                f"active={_bool_text(params.active)}" if params.active is not None else None,
            ]
        )
        records = _list_records(
            config, auth_manager, "alm_stockroom", query, params.limit, params.offset
        )
        stockrooms = [_normalize_stockroom(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(stockrooms)} stockrooms",
            "stockrooms": stockrooms,
            "count": len(stockrooms),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error listing stockrooms: %s", exc)
        return {"success": False, "message": f"Error listing stockrooms: {exc}"}


def get_stockroom(
    config: ServerConfig, auth_manager: AuthManager, params: GetStockroomParams
) -> Dict[str, Any]:
    """Get one stockroom record by identifier."""
    try:
        record = _resolve_record(
            config, auth_manager, "alm_stockroom", params.stockroom_id, ["name", "code"]
        )
        return {
            "success": True,
            "message": "Retrieved stockroom",
            "stockroom": _normalize_stockroom(record, params.include_raw),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error getting stockroom: %s", exc)
        return {"success": False, "message": f"Error getting stockroom: {exc}"}


def create_stockroom(
    config: ServerConfig, auth_manager: AuthManager, params: CreateStockroomParams
) -> Dict[str, Any]:
    """Create a stockroom record with resolved reference fields."""
    try:
        body = _resolve_common_refs(
            config,
            auth_manager,
            _clean(
                {
                    **params.fields,
                    "name": params.name,
                    "code": params.code,
                    "location": params.location,
                    "manager": params.manager,
                    "active": _bool_text(params.active),
                }
            ),
        )
        if params.dry_run:
            return _dry_run_result("create stockroom", "alm_stockroom", body)
        record = _request(config, auth_manager, "POST", "alm_stockroom", body=body)
        return {
            "success": True,
            "message": f"Created stockroom {record.get('name') or record.get('sys_id')}",
            "stockroom": _normalize_stockroom(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error creating stockroom: %s", exc)
        return {"success": False, "message": f"Error creating stockroom: {exc}"}


def update_stockroom(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateStockroomParams
) -> Dict[str, Any]:
    """Update stockroom fields and block no-op update requests."""
    try:
        existing = _resolve_record(
            config, auth_manager, "alm_stockroom", params.stockroom_id, ["name", "code"]
        )
        sys_id = str(_value(existing.get("sys_id")))
        body = _resolve_common_refs(
            config,
            auth_manager,
            _clean(
                {
                    **params.fields,
                    "name": params.name,
                    "code": params.code,
                    "location": params.location,
                    "manager": params.manager,
                    "active": _bool_text(params.active),
                }
            ),
        )
        if not body:
            return {"success": False, "message": "No stockroom fields supplied to update"}
        if params.dry_run:
            return _dry_run_result("update stockroom", "alm_stockroom", body, sys_id=sys_id)
        record = _request(config, auth_manager, "PATCH", "alm_stockroom", sys_id, body=body)
        return {
            "success": True,
            "message": f"Updated stockroom {record.get('name') or sys_id}",
            "stockroom": _normalize_stockroom(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error updating stockroom: %s", exc)
        return {"success": False, "message": f"Error updating stockroom: {exc}"}


def delete_stockroom(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteStockroomParams
) -> Dict[str, Any]:
    """Delete a stockroom record, supporting dry-run previews."""
    try:
        existing = _resolve_record(
            config, auth_manager, "alm_stockroom", params.stockroom_id, ["name", "code"]
        )
        sys_id = str(_value(existing.get("sys_id")))
        if params.dry_run:
            return _dry_run_result("delete stockroom", "alm_stockroom", sys_id=sys_id)
        _request(config, auth_manager, "DELETE", "alm_stockroom", sys_id)
        return {
            "success": True,
            "message": f"Deleted stockroom {params.stockroom_id}",
            "sys_id": sys_id,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error deleting stockroom: %s", exc)
        return {"success": False, "message": f"Error deleting stockroom: {exc}"}


def transfer_asset(
    config: ServerConfig, auth_manager: AuthManager, params: TransferAssetParams
) -> Dict[str, Any]:
    """Transfer an asset between stockrooms with optional transfer-order records."""
    try:
        table_statuses = [
            status
            for status in [
                _ensure_asset_table_available(config, auth_manager, params.asset_class),
                _ensure_optional_table(config, auth_manager, "alm_transfer_order"),
                _ensure_optional_table(config, auth_manager, "alm_transfer_order_line"),
            ]
            if status
        ]
        destination = _resolve_sys_id(
            config, auth_manager, "alm_stockroom", params.to_stockroom, ["name", "code"]
        )
        source = _resolve_sys_id(
            config, auth_manager, "alm_stockroom", params.from_stockroom, ["name", "code"]
        )
        asset_sys_id = _asset_sys_id(config, auth_manager, params.asset_class, params.asset_id)
        transfer_order = params.transfer_order
        transfer_order_record = None
        transfer_order_body = _clean(
            {
                "from_stockroom": source,
                "to_stockroom": destination,
                "comments": params.comments,
            }
        )
        if not transfer_order and params.create_transfer_order:
            if params.dry_run:
                transfer_order = "<new transfer order sys_id>"
            else:
                transfer_order_record = _request(
                    config,
                    auth_manager,
                    "POST",
                    "alm_transfer_order",
                    body=transfer_order_body,
                )
                transfer_order = str(_value(transfer_order_record.get("sys_id")))
        line_record = None
        line_body = None
        if transfer_order:
            line_body = _clean(
                {
                    "transfer_order": transfer_order,
                    "asset": asset_sys_id,
                    "quantity_requested": params.quantity,
                    "from_stockroom": source,
                    "to_stockroom": destination,
                }
            )
        if params.dry_run:
            action = "receive" if params.receive else "transfer"
            lifecycle_body = build_lifecycle_body(
                config,
                auth_manager,
                AssetLifecycleActionParams(
                    asset_id=asset_sys_id,
                    asset_class=params.asset_class,
                    action=action,
                    stockroom=params.to_stockroom,
                    destination_stockroom=params.to_stockroom,
                    quantity=params.quantity,
                    comments=params.comments,
                    dry_run=True,
                ),
            )
            return _dry_run_result(
                "transfer asset",
                table=_table_for_asset(params.asset_class),
                body=lifecycle_body,
                sys_id=asset_sys_id,
                table_statuses=table_statuses,
                transfer_order_body=transfer_order_body if params.create_transfer_order else None,
                transfer_order_line_body=line_body,
            )
        if not transfer_order and params.create_transfer_order:
            transfer_order_record = _request(
                config,
                auth_manager,
                "POST",
                "alm_transfer_order",
                body=transfer_order_body,
            )
            transfer_order = str(_value(transfer_order_record.get("sys_id")))
        if transfer_order:
            line_record = _request(
                config,
                auth_manager,
                "POST",
                "alm_transfer_order_line",
                body=line_body,
            )

        action = "receive" if params.receive else "transfer"
        lifecycle = perform_asset_lifecycle_action(
            config,
            auth_manager,
            AssetLifecycleActionParams(
                asset_id=asset_sys_id,
                asset_class=params.asset_class,
                action=action,
                stockroom=params.to_stockroom,
                destination_stockroom=params.to_stockroom,
                quantity=params.quantity,
                comments=params.comments,
            ),
        )
        return {
            "success": lifecycle.get("success", False),
            "message": (
                f"Transferred asset {params.asset_id} to {params.to_stockroom}"
                if lifecycle.get("success")
                else lifecycle.get("message")
            ),
            "transfer_order": transfer_order_record,
            "transfer_order_line": line_record,
            "asset_result": lifecycle,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error transferring asset: %s", exc)
        return {"success": False, "message": f"Error transferring asset: {exc}"}


def list_asset_models(
    config: ServerConfig, auth_manager: AuthManager, params: ListAssetModelsParams
) -> Dict[str, Any]:
    """List CMDB asset models with category and manufacturer filtering."""
    try:
        refs = _resolve_common_refs(
            config,
            auth_manager,
            _clean(
                {
                    "cmdb_model_category": params.model_category,
                    "manufacturer": params.manufacturer,
                }
            ),
        )
        query = _query(
            [
                (
                    f"nameLIKE{params.query}^ORmodel_numberLIKE{params.query}"
                    f"^ORmanufacturer.nameLIKE{params.query}"
                    if params.query
                    else None
                ),
                (
                    f"cmdb_model_category={refs['cmdb_model_category']}"
                    if refs.get("cmdb_model_category")
                    else None
                ),
                f"manufacturer={refs['manufacturer']}" if refs.get("manufacturer") else None,
                (
                    f"asset_tracking_strategy={params.asset_tracking_strategy}"
                    if params.asset_tracking_strategy
                    else None
                ),
            ]
        )
        records = _list_records(
            config, auth_manager, "cmdb_model", query, params.limit, params.offset
        )
        models = [_normalize_model(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(models)} asset models",
            "models": models,
            "count": len(models),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error listing asset models: %s", exc)
        return {"success": False, "message": f"Error listing asset models: {exc}"}


def _model_payload(params: Any) -> Dict[str, Any]:
    body = dict(getattr(params, "fields", None) or {})
    for attr, field in {
        "name": "name",
        "model_number": "model_number",
        "manufacturer": "manufacturer",
        "model_category": "cmdb_model_category",
        "cost": "cost",
        "asset_tracking_strategy": "asset_tracking_strategy",
    }.items():
        value = getattr(params, attr, None)
        if value is not None:
            body[field] = value
    return _clean(body)


def create_asset_model(
    config: ServerConfig, auth_manager: AuthManager, params: CreateAssetModelParams
) -> Dict[str, Any]:
    """Create a new CMDB asset model record."""
    try:
        body = _resolve_common_refs(config, auth_manager, _model_payload(params))
        if params.dry_run:
            return _dry_run_result("create asset model", "cmdb_model", body)
        record = _request(config, auth_manager, "POST", "cmdb_model", body=body)
        return {
            "success": True,
            "message": f"Created asset model {record.get('name') or record.get('sys_id')}",
            "model": _normalize_model(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error creating asset model: %s", exc)
        return _safe_error("create_asset_model", exc, "cmdb_model")


def update_asset_model(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateAssetModelParams
) -> Dict[str, Any]:
    """Update an existing CMDB asset model record."""
    try:
        existing = _resolve_record(
            config, auth_manager, "cmdb_model", params.model_id, ["name", "model_number"]
        )
        sys_id = str(_value(existing.get("sys_id")))
        body = _resolve_common_refs(config, auth_manager, _model_payload(params))
        if not body:
            return {"success": False, "message": "No model fields supplied to update"}
        if params.dry_run:
            return _dry_run_result("update asset model", "cmdb_model", body, sys_id=sys_id)
        record = _request(config, auth_manager, "PATCH", "cmdb_model", sys_id, body=body)
        return {
            "success": True,
            "message": f"Updated asset model {record.get('name') or sys_id}",
            "model": _normalize_model(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error updating asset model: %s", exc)
        return {"success": False, "message": f"Error updating asset model: {exc}"}


# ---------------------------------------------------------------------------
# Business-safe Asset Management tool family
# ---------------------------------------------------------------------------

PrefixedAssetClass = Literal["hardware", "consumable", "software_license", "generic"]

ALLOWED_ASSET_TABLES = {
    "alm_asset",
    "alm_hardware",
    "alm_consumable",
    "alm_license",
    "alm_entitlement",
    "alm_entitlement_user",
    "alm_entitlement_asset",
    "alm_stockroom",
    "alm_stockroom_type",
    "alm_stock_rule",
    "alm_transfer_order",
    "alm_transfer_order_line",
    "alm_m2m_stockroom_model",
    "cmdb_model",
    "cmdb_model_category",
    "core_company",
    "cmdb_ci",
    "ast_contract",
    "contract",
    "clm_m2m_contract_asset",
    "asset_task",
    "sys_user",
    "cmn_location",
    "cmn_department",
    "cmn_cost_center",
}

PREFIXED_ASSET_TABLES = {
    "hardware": "alm_hardware",
    "consumable": "alm_consumable",
    "software_license": "alm_license",
    "generic": "alm_asset",
}

STATE_LABEL_TO_VALUE = {
    "On order": STATE_VALUES["on_order"],
    "In stock": STATE_VALUES["in_stock"],
    "In transit": STATE_VALUES["in_transit"],
    "Consumed": STATE_VALUES["consumed"],
    "In use": STATE_VALUES["in_use"],
    "In maintenance": STATE_VALUES["in_maintenance"],
    "Retired": STATE_VALUES["retired"],
    "Missing": STATE_VALUES["missing"],
    "Build": STATE_VALUES["build"],
}

STATE_VALUE_TO_LABEL = {value: label for label, value in STATE_LABEL_TO_VALUE.items()}

ALLOWED_STATE_SUBSTATES = {
    "On order": [],
    "In stock": [
        "Available",
        "Reserved",
        "Defective",
        "In repair",
        "Pending repair",
        "Pending install",
        "Pending disposal",
        "Pending donation",
        "Pending transfer",
        "Pre-allocated",
        "On hold",
        "Legal hold",
        "Quarantine",
        "Pending return",
        "Pending certificate",
        "Test",
        "Pending resale",
        "Pending evaluation",
    ],
    "In transit": [
        "Available",
        "Reserved",
        "Defective",
        "Pending install",
        "Pending disposal",
        "Pending donation",
        "Pre-allocated",
        "Pending resale",
    ],
    "In use": ["Pending fulfillment", "End of support", "Pending retirement", "In repair", "Pending repair",
               "Is shutdown"],
    "In maintenance": [],
    "Retired": [
        "Disposed",
        "Pending disposal",
        "Sold",
        "Donated",
        "Vendor credit",
        "Lease return",
        "Obsolete",
        "RMA",
        "Buy out",
        "Pending resale",
        "Recalled",
    ],
    "Missing": ["Lost", "Stolen"],
    "Consumed": [],
    "Build": [],
}

SUBSTATE_LABEL_TO_VALUE = {
    label: label.lower().replace(" ", "_").replace("-", "_")
    for labels in ALLOWED_STATE_SUBSTATES.values()
    for label in labels
}


class ReferenceLookup(BaseModel):
    """Model or helper class for Reference Lookup."""
    sys_id: Optional[str] = Field(None, description="ServiceNow sys_id")
    name: Optional[str] = Field(None, description="Human-readable name or number")
    display_value: Optional[str] = Field(None, description="Display value")
    query: Optional[Dict[str, Any]] = Field(
        None, description="Exact field/value lookup for allowlisted reference tables"
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_string_lookup(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return {}
            if cleaned.startswith("sys_id:"):
                cleaned = cleaned.replace("sys_id:", "", 1).strip()
            if SYS_ID_PATTERN.fullmatch(cleaned):
                return {"sys_id": cleaned}
            return {"name": cleaned}
        return value


class SafeMutationFields(BaseModel):
    """Model or helper class for Safe Mutation Fields."""
    dry_run: bool = Field(False, description="Validate and return planned changes without writing")
    correlation_id: Optional[str] = Field(None, description="Caller correlation id for audit")
    idempotency_key: Optional[str] = Field(None, description="Caller idempotency key for retries")
    return_before_after: bool = Field(
        True, description="Return before/after records for mutating operations where applicable"
    )


class AssetCreateParams(SafeMutationFields):
    """Parameters for Asset Create tool calls."""
    asset_class: PrefixedAssetClass = Field(..., description="Target asset class")
    asset_tag: Optional[str] = Field(None, description="Asset tag, used for duplicate checks")
    serial_number: Optional[str] = Field(
        None, description="Serial number, used for duplicate checks"
    )
    display_name: Optional[str] = Field(None, description="Asset display name")
    name: Optional[str] = Field(None, description="Asset name")
    model: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmdb_model by sys_id, name, model_number, or query"
    )
    model_category: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmdb_model_category by sys_id, name, or query"
    )
    stockroom: Optional[ReferenceLookup] = Field(
        None, description="Reference to alm_stockroom by sys_id, name, code, or query"
    )
    location: Optional[ReferenceLookup] = Field(
        None,
        description=(
            "Reference to cmn_location by sys_id, name, full_name, or query. "
            "If a named location is missing, it is created before the asset is created."
        ),
    )
    department: Optional[ReferenceLookup] = Field(
        None,
        description=(
            "Reference to cmn_department by sys_id, name, or query. "
            "If a named department is missing, it is created before the asset is created."
        ),
    )
    cost_center: Optional[ReferenceLookup] = Field(
        None,
        description=(
            "Reference to cmn_cost_center by sys_id, name, code, or query. "
            "If a named cost center is missing, it is created before the asset is created."
        ),
    )
    assigned_to: Optional[ReferenceLookup] = Field(
        None, description="Reference to sys_user by sys_id, user_name, email, name, or query"
    )
    reserved_for: Optional[ReferenceLookup] = Field(
        None, description="Reference to sys_user by sys_id, user_name, email, name, or query"
    )
    state: Optional[str] = Field("In stock", description="Asset state label")
    substate: Optional[str] = Field("Available", description="Asset substate label")
    quantity: Optional[int] = Field(None, description="Quantity for consumable assets")
    cost: Optional[float] = Field(None, description="Asset cost")
    acquisition_date: Optional[str] = Field(None, description="Purchase/acquisition date")
    warranty_expiration: Optional[str] = Field(None, description="Warranty expiration date")
    vendor: Optional[ReferenceLookup] = Field(
        None,
        description=(
            "Reference to core_company by sys_id, name, or query. "
            "If a named company is missing, it is created before the asset is created."
        ),
    )
    contract: Optional[ReferenceLookup] = Field(
        None, description="Reference to ast_contract/contract by sys_id, number, name, or query"
    )
    ci: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmdb_ci by sys_id, name, asset_tag, serial_number, or query"
    )
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional ServiceNow asset table fields"
    )


class AssetUpdateParams(SafeMutationFields):
    """Parameters for Asset Update tool calls."""
    asset: ReferenceLookup = Field(
        ...,
        description="Target asset reference by sys_id, asset_tag, serial_number, name, or query",
    )
    asset_class: PrefixedAssetClass = Field("generic", description="Asset class/table")
    asset_tag: Optional[str] = Field(None, description="New asset tag")
    serial_number: Optional[str] = Field(None, description="New serial number")
    display_name: Optional[str] = Field(None, description="New display name")
    model: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmdb_model by sys_id, name, model_number, or query"
    )
    stockroom: Optional[ReferenceLookup] = Field(
        None, description="Reference to alm_stockroom by sys_id, name, code, or query"
    )
    location: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmn_location by sys_id, name, full_name, or query"
    )
    department: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmn_department by sys_id, name, or query"
    )
    cost_center: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmn_cost_center by sys_id, name, code, or query"
    )
    assigned_to: Optional[ReferenceLookup] = Field(
        None, description="Reference to sys_user by sys_id, user_name, email, name, or query"
    )
    reserved_for: Optional[ReferenceLookup] = Field(
        None, description="Reference to sys_user by sys_id, user_name, email, name, or query"
    )
    vendor: Optional[ReferenceLookup] = Field(
        None, description="Reference to core_company by sys_id, name, or query"
    )
    ci: Optional[ReferenceLookup] = Field(
        None, description="Reference to cmdb_ci by sys_id, name, asset_tag, serial_number, or query"
    )
    contract: Optional[ReferenceLookup] = Field(
        None, description="Reference to ast_contract/contract by sys_id, number, name, or query"
    )
    fields: Dict[str, Any] = Field(
        default_factory=dict, description="Additional ServiceNow asset table fields"
    )


class AssetSearchParams(BaseModel):
    """Parameters for Asset Search tool calls."""
    asset_class: PrefixedAssetClass = "generic"
    query: Optional[str] = None
    state: Optional[str] = None
    substate: Optional[str] = None
    model: Optional[ReferenceLookup] = None
    model_category: Optional[ReferenceLookup] = None
    stockroom: Optional[ReferenceLookup] = None
    assigned_to: Optional[ReferenceLookup] = None
    location: Optional[ReferenceLookup] = None
    department: Optional[ReferenceLookup] = None
    cost_center: Optional[ReferenceLookup] = None
    vendor: Optional[ReferenceLookup] = None
    created_after: Optional[str] = None
    updated_after: Optional[str] = None
    warranty_expiring_before: Optional[str] = None
    lease_expiring_before: Optional[str] = None
    include_records: bool = True
    limit: int = 25
    offset: int = 0


class AssetStateParams(SafeMutationFields):
    """Parameters for Asset State tool calls."""
    asset: ReferenceLookup
    asset_class: PrefixedAssetClass = "generic"
    state: Optional[str] = None
    substate: Optional[str] = None
    force: bool = False
    reason: Optional[str] = None


class AssetSubstateParams(BaseModel):
    """Parameters for Asset Substate tool calls."""
    state: str = Field(..., description="Asset state label")


class AssetAssignmentParams(SafeMutationFields):
    """Parameters for Asset Assignment tool calls."""
    asset: ReferenceLookup
    asset_class: PrefixedAssetClass = "generic"
    user: ReferenceLookup
    assignment_date: Optional[str] = None
    location: Optional[ReferenceLookup] = None
    department: Optional[ReferenceLookup] = None
    cost_center: Optional[ReferenceLookup] = None
    reason: Optional[str] = None


class AssetReturnParams(SafeMutationFields):
    """Parameters for Asset Return tool calls."""
    asset: ReferenceLookup
    asset_class: PrefixedAssetClass = "generic"
    stockroom: Optional[ReferenceLookup] = None
    state: str = "In stock"
    substate: str = "Available"
    reason: Optional[str] = None


class ConsumableQuantityParams(SafeMutationFields):
    """Parameters for Consumable Quantity tool calls."""
    asset: ReferenceLookup
    adjustment: int
    reason: str


class AssetTransferOrderParams(SafeMutationFields):
    """Parameters for Asset Transfer Order tool calls."""
    from_stockroom: ReferenceLookup
    to_stockroom: ReferenceLookup
    requested_by: Optional[ReferenceLookup] = None
    reason: Optional[str] = None


class AssetTransferLineParams(SafeMutationFields):
    """Parameters for Asset Transfer Line tool calls."""
    transfer_order: ReferenceLookup
    asset: Optional[ReferenceLookup] = None
    asset_class: PrefixedAssetClass = "hardware"
    model: Optional[ReferenceLookup] = None
    quantity: int = 1


class AssetRetireParams(SafeMutationFields):
    """Parameters for Asset Retire tool calls."""
    asset: ReferenceLookup
    asset_class: PrefixedAssetClass = "generic"
    substate: str = "Obsolete"
    reason: str
    data_wipe_confirmed: Optional[bool] = None
    update_ci: bool = False


class AssetReportParams(AssetSearchParams):
    """Parameters for Asset Report tool calls."""
    group_by: Optional[str] = None


class ComponentResolutionError(ValueError):
    """Raised when a referenced ServiceNow component is required by the request but missing."""

    def __init__(self, table: str, lookup: Any, message: Optional[str] = None):
        self.table = table
        self.lookup = lookup
        super().__init__(
            message
            or (
                f"{table} component was not found for lookup {lookup}. "
                "Ask the user to create or recreate this component, then retry the update."
            )
        )


REFERENCE_RECOVERY_TOOLS = {
    "alm_stockroom": "create_stockroom or asset_stockroom_create",
    "cmdb_ci": "servicenow_internal_cmdb.upsert_ci",
    "cmdb_model": "create_asset_model",
    "cmdb_model_category": "create_model_category",
    "cmn_cost_center": "create_cost_center",
    "cmn_department": "create_department",
    "cmn_location": "create_location",
    "core_company": "create_company",
    "sys_user": "create_user",
    "sys_user_group": "create_group",
}


AUTO_CREATE_REFERENCE_TABLES = {
    "cmn_cost_center": {"label": "cost center", "name_field": "name"},
    "cmn_department": {"label": "department", "name_field": "name"},
    "cmn_location": {"label": "location", "name_field": "name"},
    "core_company": {"label": "company", "name_field": "name"},
}


def _auto_create_reference(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
) -> Optional[str]:
    spec = AUTO_CREATE_REFERENCE_TABLES.get(table)
    if not spec or not identifier.strip():
        return None

    name = identifier.strip()
    body = {spec["name_field"]: name}
    try:
        record = _request(
            config,
            auth_manager,
            "POST",
            table,
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            body=body,
        )
    except requests.exceptions.RequestException as exc:
        raise ComponentResolutionError(
            table,
            {"identifier": identifier, "auto_create": body},
            f"{table} record not found and auto-create failed: {exc}",
        ) from exc

    if not isinstance(record, dict):
        raise ComponentResolutionError(
            table,
            {"identifier": identifier, "auto_create": body},
            f"{table} record not found and auto-create returned an unexpected response",
        )

    sys_id = _value(record.get("sys_id"))
    if not sys_id:
        raise ComponentResolutionError(
            table,
            {"identifier": identifier, "auto_create": body},
            f"{table} record not found and auto-create did not return a sys_id",
        )

    logger.info("Created missing %s reference '%s' (%s)", spec["label"], name, sys_id)
    return str(sys_id)


def _component_error_payload(exc: ComponentResolutionError) -> Dict[str, Any]:
    create_tool = REFERENCE_RECOVERY_TOOLS.get(exc.table)
    payload = {
        "table": exc.table,
        "lookup": exc.lookup,
        "action_required": "create_or_recreate_component",
    }
    if create_tool:
        payload["recommended_tool"] = create_tool
    return payload


def _state_validation_payload(message: str) -> Dict[str, Any]:
    match = re.search(
        r"Invalid substate '([^']+)' for state '([^']+)'\. Allowed: (.+)",
        message,
    )
    if not match:
        return {}
    invalid_substate, state, allowed_text = match.groups()
    allowed = [item.strip() for item in allowed_text.split(",") if item.strip()]
    return {
        "validation_error": {
            "type": "invalid_asset_substate",
            "state": state,
            "invalid_substate": invalid_substate,
            "allowed_substates": allowed,
            "action_required": "choose_allowed_substate_or_change_state",
        },
        "user_prompt": (
            f"State '{state}' cannot use substate '{invalid_substate}'. "
            f"Use one of: {', '.join(allowed)}."
        ),
    }


def _missing_component_prompt(missing_component: Dict[str, Any]) -> str:
    prompt = f"The referenced {missing_component['table']} component is not available. "
    if missing_component.get("recommended_tool"):
        return (
            prompt
            + f"Use {missing_component['recommended_tool']} first, then retry this operation."
        )
    return prompt + "Please create or recreate it, then retry this operation."


def _tool_result(
    success: bool,
    operation: str,
    table: Optional[str] = None,
    sys_id: Optional[str] = None,
    display_value: Optional[str] = None,
    changed: bool = False,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    records: Optional[List[Dict[str, Any]]] = None,
    warnings: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
    correlation_id: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    result = {
        "success": success,
        "operation": operation,
        "table": table,
        "sys_id": sys_id,
        "display_value": display_value,
        "changed": changed,
        "before": before,
        "after": after,
        "records": records,
        "warnings": warnings or [],
        "errors": errors or [],
        "correlation_id": correlation_id,
    }
    result.update(extra)
    return result


def _safe_error(operation: str, exc: Exception, table: Optional[str] = None) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    if isinstance(exc, ComponentResolutionError):
        missing_component = _component_error_payload(exc)
        extra.update(
            {
                "missing_component": missing_component,
                "user_prompt": _missing_component_prompt(missing_component),
            }
        )
    else:
        extra.update(_state_validation_payload(str(exc)))
    return _tool_result(
        False,
        operation,
        table=table,
        errors=[str(exc)],
        warnings=[],
        **extra,
    )


def _prefixed_table(asset_class: str) -> str:
    table = PREFIXED_ASSET_TABLES.get(asset_class)
    if not table:
        raise ValueError(f"Unsupported asset_class: {asset_class}")
    return table


def _sanitize_value(value: Any) -> str:
    text = str(value).strip()
    if any(token in text for token in ["^", "javascript:", "NQ", "@javascript"]):
        raise ValueError(f"Unsafe query value: {text}")
    return text


def _encoded_equals(field: str, value: Any) -> str:
    return f"{field}={_sanitize_value(value)}"


def _lookup_to_identifier(lookup: Optional[ReferenceLookup]) -> Optional[str]:
    if not lookup:
        return None
    return lookup.sys_id or lookup.name or lookup.display_value


def table_exists(config: ServerConfig, auth_manager: AuthManager, table_name: str) -> bool:
    """Return whether a ServiceNow table exists, restricted to Asset Management allowlist."""
    if table_name not in ALLOWED_ASSET_TABLES and table_name not in OPTIONAL_ASSET_TABLES:
        raise ValueError(f"Table is not allowlisted for asset tools: {table_name}")
    records = _list_records(config, auth_manager, "sys_db_object", f"name={table_name}", 1, 0)
    return bool(records)


def _check_table_available(
    config: ServerConfig, auth_manager: AuthManager, table: str, required: bool = True
) -> List[str]:
    if table not in OPTIONAL_ASSET_TABLES and table in {
        "alm_asset",
        "alm_hardware",
        "alm_stockroom",
        "cmdb_model",
        "cmdb_model_category",
        "core_company",
        "cmdb_ci",
    }:
        return []
    try:
        if table_exists(config, auth_manager, table):
            return []
    except requests.exceptions.RequestException as exc:
        if required:
            raise ValueError(f"Unable to verify table {table}: {exc}") from exc
        return [f"Could not verify optional table {table}: {exc}"]
    if required:
        raise ValueError(f"ServiceNow table {table} is unavailable")
    return [f"Optional table {table} is unavailable; skipped optional linkage"]


def _resolve_reference(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    lookup: Optional[ReferenceLookup],
    fields: List[str],
    required: bool = False,
) -> Optional[str]:
    if lookup is None:
        if required:
            raise ValueError(f"Reference for {table} is required")
        return None
    if lookup.sys_id:
        try:
            record = _request(
                config,
                auth_manager,
                "GET",
                table,
                lookup.sys_id,
                params={
                    "sysparm_display_value": "all",
                    "sysparm_exclude_reference_link": "true",
                },
            )
        except requests.exceptions.RequestException as exc:
            raise ComponentResolutionError(table, {"sys_id": lookup.sys_id}) from exc
        if not isinstance(record, dict) or not record:
            raise ComponentResolutionError(table, {"sys_id": lookup.sys_id})
        return lookup.sys_id
    if lookup.query:
        parts = [_encoded_equals(field, value) for field, value in lookup.query.items()]
        records = _list_records(config, auth_manager, table, "^".join(parts), 3, 0)
        if len(records) == 1:
            return str(_value(records[0].get("sys_id")))
        if len(records) > 1:
            candidates = [
                str(_display(item.get("name") or item.get("number") or item.get("sys_id")))
                for item in records
            ]
            raise ValueError(f"Multiple {table} records matched {lookup.query}: {candidates}")
        raise ComponentResolutionError(table, {"query": lookup.query})
    identifier = _lookup_to_identifier(lookup)
    if identifier:
        try:
            return _resolve_sys_id(config, auth_manager, table, identifier, fields)
        except ValueError as exc:
            message = str(exc)
            if "not found" in message.lower():
                created_sys_id = _auto_create_reference(config, auth_manager, table, identifier)
                if created_sys_id:
                    return created_sys_id
                raise ComponentResolutionError(table, {"identifier": identifier}, message) from exc
            raise
    if required:
        raise ValueError(
            f"Reference lookup for {table} did not include sys_id, name, display_value, or query"
        )
    return None


def _state_value(state: str) -> str:
    return STATE_LABEL_TO_VALUE.get(state, STATE_VALUES.get(state, state))


def _state_label(state: str) -> str:
    if state in ALLOWED_STATE_SUBSTATES:
        return state
    return STATE_VALUE_TO_LABEL.get(_state_value(state), state)


def _substate_value(substate: Optional[str]) -> Optional[str]:
    if substate in (None, ""):
        return None
    return SUBSTATE_LABEL_TO_VALUE.get(
        str(substate), SUBSTATE_VALUES.get(str(substate), str(substate))
    )


def _effective_create_substate(state: str, substate: Optional[str]) -> Optional[str]:
    label = _state_label(state)
    if substate == "Available" and label != "In stock":
        return None
    return substate


def validate_asset_state(state: str, substate: Optional[str]) -> None:
    """Validate that state and substate combinations are allowed."""
    if state not in ALLOWED_STATE_SUBSTATES and state not in STATE_VALUES:
        raise ValueError(f"Invalid asset state: {state}")
    label = _state_label(state)
    allowed = ALLOWED_STATE_SUBSTATES.get(label, [])
    if substate and allowed and substate not in allowed:
        raise ValueError(
            f"Invalid substate '{substate}' for state '{label}'. Allowed: {', '.join(allowed)}"
        )
    if substate and not allowed and label in ALLOWED_STATE_SUBSTATES:
        raise ValueError(f"State '{label}' does not define common substates")


def get_allowed_asset_states(
    config: ServerConfig, auth_manager: AuthManager, params: AssetManagementMetadataParams
) -> Dict[str, Any]:
    """Return the configured set of allowed asset states."""
    del config, auth_manager, params
    return _tool_result(
        True, "get_allowed_asset_states", records=[{"state": s} for s in ALLOWED_STATE_SUBSTATES]
    )


def get_allowed_asset_substates(
    config: ServerConfig, auth_manager: AuthManager, params: AssetSubstateParams
) -> Dict[str, Any]:
    """Return allowed substates for a specific asset state."""
    del config, auth_manager
    state = params.state
    if state not in ALLOWED_STATE_SUBSTATES:
        return _tool_result(
            False, "get_allowed_asset_substates", errors=[f"Invalid asset state: {state}"]
        )
    return _tool_result(
        True,
        "get_allowed_asset_substates",
        records=[{"substate": value} for value in ALLOWED_STATE_SUBSTATES[state]],
    )


def _asset_identity_query(params: AssetCreateParams, payload: Dict[str, Any]) -> Optional[str]:
    checks = []
    for field in ("asset_tag", "serial_number", "ci"):
        value = payload.get(field)
        if value:
            checks.append(_encoded_equals(field, value))
    if payload.get("model") and payload.get("assigned_to"):
        checks.append(
            f"model={_sanitize_value(payload['model'])}^assigned_to={_sanitize_value(payload['assigned_to'])}"
        )
    return "^OR".join(checks) if checks else None


def _asset_create_payload(
    config: ServerConfig, auth_manager: AuthManager, params: AssetCreateParams
) -> Dict[str, Any]:
    state = params.state or "In stock"
    substate = _effective_create_substate(state, params.substate)
    validate_asset_state(state, substate)
    if params.asset_class == "consumable" and (params.quantity is None or params.quantity < 1):
        raise ValueError("quantity must be >= 1 for consumable assets")
    payload = dict(params.fields or {})
    payload.update(
        _clean(
            {
                "asset_tag": params.asset_tag,
                "serial_number": params.serial_number,
                "display_name": params.display_name or params.name,
                "install_status": _state_value(state),
                "substatus": _substate_value(substate),
                "quantity": params.quantity,
                "cost": params.cost,
                "purchase_date": params.acquisition_date,
                "warranty_expiration": params.warranty_expiration,
                "model": _resolve_reference(
                    config, auth_manager, "cmdb_model", params.model, ["name", "model_number"]
                ),
                "model_category": _resolve_reference(
                    config, auth_manager, "cmdb_model_category", params.model_category, ["name"]
                ),
                "stockroom": _resolve_reference(
                    config, auth_manager, "alm_stockroom", params.stockroom, ["name", "code"]
                ),
                "location": _resolve_reference(
                    config, auth_manager, "cmn_location", params.location, ["name", "full_name"]
                ),
                "department": _resolve_reference(
                    config, auth_manager, "cmn_department", params.department, ["name"]
                ),
                "cost_center": _resolve_reference(
                    config, auth_manager, "cmn_cost_center", params.cost_center, ["name", "code"]
                ),
                "assigned_to": _resolve_reference(
                    config,
                    auth_manager,
                    "sys_user",
                    params.assigned_to,
                    ["user_name", "email", "name"],
                ),
                "reserved_for": _resolve_reference(
                    config,
                    auth_manager,
                    "sys_user",
                    params.reserved_for,
                    ["user_name", "email", "name"],
                ),
                "vendor": _resolve_reference(
                    config, auth_manager, "core_company", params.vendor, ["name"]
                ),
                "ci": _resolve_reference(
                    config,
                    auth_manager,
                    "cmdb_ci",
                    params.ci,
                    ["name", "serial_number", "asset_tag"],
                ),
            }
        )
    )
    return _clean(payload)


def _resolve_contract_reference(
    config: ServerConfig,
    auth_manager: AuthManager,
    contract: Optional[ReferenceLookup],
) -> tuple[Optional[str], List[str]]:
    if not contract:
        return None, []
    warnings: List[str] = []
    for table in ("ast_contract", "contract"):
        table_warnings = _check_table_available(config, auth_manager, table, required=False)
        if table_warnings:
            warnings.extend(table_warnings)
            continue
        return (
            _resolve_reference(config, auth_manager, table, contract, ["number", "name"], True),
            warnings,
        )
    raise ValueError("No supported contract table is available for contract lookup")


def asset_create(
    config: ServerConfig, auth_manager: AuthManager, params: AssetCreateParams
) -> Dict[str, Any]:
    """Handle asset create.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    operation = f"asset_create_{params.asset_class}"
    table = _prefixed_table(params.asset_class)
    try:
        warnings = _check_table_available(config, auth_manager, table, required=True)
        payload = _asset_create_payload(config, auth_manager, params)
        contract_id, contract_warnings = _resolve_contract_reference(
            config, auth_manager, params.contract
        )
        warnings.extend(contract_warnings)
        duplicate_query = _asset_identity_query(params, payload)
        duplicates = (
            _list_records(config, auth_manager, table, duplicate_query, 10, 0)
            if duplicate_query
            else []
        )
        if duplicates:
            return _tool_result(
                False,
                operation,
                table=table,
                records=[_normalize_asset(item, False) for item in duplicates],
                warnings=warnings,
                errors=[
                    "Duplicate asset found using asset tag, serial number, CI, or model/user "
                    "identity"
                ],
                correlation_id=params.correlation_id,
            )
        if params.dry_run:
            return _tool_result(
                True,
                operation,
                table=table,
                changed=False,
                warnings=warnings,
                correlation_id=params.correlation_id,
                dry_run=True,
                planned_operation="insert",
                payload=payload,
                contract_sys_id=contract_id,
            )
        record = _request(config, auth_manager, "POST", table, body=payload)
        sys_id = str(_value(record.get("sys_id")))
        if contract_id:
            warnings.extend(
                _link_contract_if_available(
                    config, auth_manager, sys_id, contract_id, params.dry_run
                )
            )
        return _tool_result(
            True,
            operation,
            table=table,
            sys_id=sys_id,
            display_value=str(
                _display(record.get("display_name") or record.get("asset_tag") or sys_id)
            ),
            changed=True,
            after=_normalize_asset(record, True) if params.return_before_after else None,
            warnings=warnings,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.warning("%s failed: %s", operation, exc)
        return _safe_error(operation, exc, table)


def _link_contract_if_available(
    config: ServerConfig,
    auth_manager: AuthManager,
    asset_sys_id: str,
    contract_sys_id: str,
    dry_run: bool,
) -> List[str]:
    warnings = _check_table_available(
        config, auth_manager, "clm_m2m_contract_asset", required=False
    )
    if warnings:
        return warnings
    if not dry_run:
        _request(
            config,
            auth_manager,
            "POST",
            "clm_m2m_contract_asset",
            body={"asset": asset_sys_id, "contract": contract_sys_id},
        )
    return []


def asset_create_hardware(
    config: ServerConfig, auth_manager: AuthManager, params: AssetCreateParams
) -> Dict[str, Any]:
    """Handle asset create hardware.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.asset_class = "hardware"
    return asset_create(config, auth_manager, params)


def asset_create_consumable(
    config: ServerConfig, auth_manager: AuthManager, params: AssetCreateParams
) -> Dict[str, Any]:
    """Handle asset create consumable.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.asset_class = "consumable"
    return asset_create(config, auth_manager, params)


def asset_create_software_license(
    config: ServerConfig, auth_manager: AuthManager, params: AssetCreateParams
) -> Dict[str, Any]:
    """Handle asset create software license.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.asset_class = "software_license"
    return asset_create(config, auth_manager, params)


def asset_search(
    config: ServerConfig, auth_manager: AuthManager, params: AssetSearchParams
) -> Dict[str, Any]:
    """Handle asset search.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = _prefixed_table(params.asset_class)
    try:
        refs = {
            "model": _resolve_reference(
                config, auth_manager, "cmdb_model", params.model, ["name", "model_number"]
            ),
            "model_category": _resolve_reference(
                config, auth_manager, "cmdb_model_category", params.model_category, ["name"]
            ),
            "stockroom": _resolve_reference(
                config, auth_manager, "alm_stockroom", params.stockroom, ["name", "code"]
            ),
            "assigned_to": _resolve_reference(
                config, auth_manager, "sys_user", params.assigned_to, ["user_name", "email", "name"]
            ),
            "location": _resolve_reference(
                config, auth_manager, "cmn_location", params.location, ["name", "full_name"]
            ),
            "department": _resolve_reference(
                config, auth_manager, "cmn_department", params.department, ["name"]
            ),
            "cost_center": _resolve_reference(
                config, auth_manager, "cmn_cost_center", params.cost_center, ["name", "code"]
            ),
            "vendor": _resolve_reference(
                config, auth_manager, "core_company", params.vendor, ["name"]
            ),
        }
        query = _query(
            [
                (
                    f"asset_tagLIKE{_sanitize_value(params.query)}^ORserial_numberLIKE{_sanitize_value(params.query)}^ORdisplay_nameLIKE{_sanitize_value(params.query)}"
                    if params.query
                    else None
                ),
                f"install_status={_state_value(params.state)}" if params.state else None,
                f"substatus={_substate_value(params.substate)}" if params.substate else None,
                *[f"{field}={value}" for field, value in refs.items() if value],
                (
                    f"sys_created_on>={_sanitize_value(params.created_after)}"
                    if params.created_after
                    else None
                ),
                (
                    f"sys_updated_on>={_sanitize_value(params.updated_after)}"
                    if params.updated_after
                    else None
                ),
                (
                    f"warranty_expiration<={_sanitize_value(params.warranty_expiring_before)}"
                    if params.warranty_expiring_before
                    else None
                ),
                (
                    f"lease_expiration<={_sanitize_value(params.lease_expiring_before)}"
                    if params.lease_expiring_before
                    else None
                ),
            ]
        )
        records = _list_records(config, auth_manager, table, query, params.limit, params.offset)
        normalized = [_normalize_asset(record, params.include_records) for record in records]
        return _tool_result(
            True,
            "asset_search",
            table=table,
            records=normalized,
            count=len(normalized),
            query=query,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_search", exc, table)


def asset_get(
    config: ServerConfig, auth_manager: AuthManager, params: AssetUpdateParams
) -> Dict[str, Any]:
    """Handle asset get.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = _prefixed_table(params.asset_class)
    try:
        sys_id = _resolve_reference(
            config,
            auth_manager,
            table,
            params.asset,
            ["asset_tag", "serial_number", "display_name"],
            True,
        )
        record = _request(
            config,
            auth_manager,
            "GET",
            table,
            sys_id,
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        )
        return _tool_result(
            True, "asset_get", table=table, sys_id=sys_id, after=_normalize_asset(record, True)
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_get", exc, table)


def asset_update(
    config: ServerConfig, auth_manager: AuthManager, params: AssetUpdateParams
) -> Dict[str, Any]:
    """Handle asset update.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = _prefixed_table(params.asset_class)
    try:
        sys_id = _resolve_reference(
            config,
            auth_manager,
            table,
            params.asset,
            ["asset_tag", "serial_number", "display_name"],
            True,
        )
        before = _request(
            config,
            auth_manager,
            "GET",
            table,
            sys_id,
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        )
        payload = dict(params.fields or {})
        payload.update(
            _clean(
                {
                    "asset_tag": params.asset_tag,
                    "serial_number": params.serial_number,
                    "display_name": params.display_name,
                    "model": _resolve_reference(
                        config, auth_manager, "cmdb_model", params.model, ["name", "model_number"]
                    ),
                    "stockroom": _resolve_reference(
                        config, auth_manager, "alm_stockroom", params.stockroom, ["name", "code"]
                    ),
                    "location": _resolve_reference(
                        config, auth_manager, "cmn_location", params.location, ["name", "full_name"]
                    ),
                    "department": _resolve_reference(
                        config, auth_manager, "cmn_department", params.department, ["name"]
                    ),
                    "cost_center": _resolve_reference(
                        config,
                        auth_manager,
                        "cmn_cost_center",
                        params.cost_center,
                        ["name", "code"],
                    ),
                    "assigned_to": _resolve_reference(
                        config,
                        auth_manager,
                        "sys_user",
                        params.assigned_to,
                        ["user_name", "email", "name"],
                    ),
                    "reserved_for": _resolve_reference(
                        config,
                        auth_manager,
                        "sys_user",
                        params.reserved_for,
                        ["user_name", "email", "name"],
                    ),
                    "vendor": _resolve_reference(
                        config, auth_manager, "core_company", params.vendor, ["name"]
                    ),
                    "ci": _resolve_reference(
                        config,
                        auth_manager,
                        "cmdb_ci",
                        params.ci,
                        ["name", "serial_number", "asset_tag"],
                    ),
                }
            )
        )
        if not payload:
            raise ValueError("No asset fields supplied to update")
        if params.dry_run:
            return _tool_result(
                True,
                "asset_update",
                table=table,
                sys_id=sys_id,
                before=_normalize_asset(before, True),
                changed=False,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        after = _request(config, auth_manager, "PATCH", table, sys_id, body=payload)
        return _tool_result(
            True,
            "asset_update",
            table=table,
            sys_id=sys_id,
            changed=True,
            before=_normalize_asset(before, True) if params.return_before_after else None,
            after=_normalize_asset(after, True) if params.return_before_after else None,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_update", exc, table)


def asset_update_state(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle asset update state.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = _prefixed_table(params.asset_class)
    try:
        if not params.state:
            raise ValueError("state is required")
        if params.force and not params.reason:
            raise ValueError("reason is required when force=true")
        validate_asset_state(params.state, params.substate)
        sys_id = _resolve_reference(
            config,
            auth_manager,
            table,
            params.asset,
            ["asset_tag", "serial_number", "display_name"],
            True,
        )
        before = _request(
            config,
            auth_manager,
            "GET",
            table,
            sys_id,
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        )
        payload = _clean(
            {
                "install_status": _state_value(params.state),
                "substatus": _substate_value(params.substate),
                "comments": params.reason,
                "work_notes": params.reason,
            }
        )
        if params.dry_run:
            return _tool_result(
                True,
                "asset_update_state",
                table=table,
                sys_id=sys_id,
                before=_normalize_asset(before, True),
                changed=False,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        after = _request(config, auth_manager, "PATCH", table, sys_id, body=payload)
        return _tool_result(
            True,
            "asset_update_state",
            table=table,
            sys_id=sys_id,
            changed=True,
            before=_normalize_asset(before, True) if params.return_before_after else None,
            after=_normalize_asset(after, True) if params.return_before_after else None,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_update_state", exc, table)


def validate_asset_state_transition(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Validate asset state transition.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    del config, auth_manager
    try:
        if not params.state:
            raise ValueError("state is required")
        validate_asset_state(params.state, params.substate)
        return _tool_result(True, "validate_asset_state_transition")
    except ValueError as exc:
        return _safe_error("validate_asset_state_transition", exc)


def asset_assign_to_user(
    config: ServerConfig, auth_manager: AuthManager, params: AssetAssignmentParams
) -> Dict[str, Any]:
    """Handle asset assign to user.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = _prefixed_table(params.asset_class)
    try:
        user_id = _resolve_reference(
            config, auth_manager, "sys_user", params.user, ["user_name", "email", "name"], True
        )
        sys_id = _resolve_reference(
            config,
            auth_manager,
            table,
            params.asset,
            ["asset_tag", "serial_number", "display_name"],
            True,
        )
        before = _request(
            config,
            auth_manager,
            "GET",
            table,
            sys_id,
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        )
        payload = _clean(
            {
                "assigned_to": user_id,
                "install_status": STATE_LABEL_TO_VALUE["In use"],
                "substatus": "",
                "stockroom": "",
                "assigned": params.assignment_date,
                "location": _resolve_reference(
                    config, auth_manager, "cmn_location", params.location, ["name", "full_name"]
                ),
                "department": _resolve_reference(
                    config, auth_manager, "cmn_department", params.department, ["name"]
                ),
                "cost_center": _resolve_reference(
                    config, auth_manager, "cmn_cost_center", params.cost_center, ["name", "code"]
                ),
                "comments": params.reason,
                "work_notes": params.reason,
            }
        )
        if params.dry_run:
            return _tool_result(
                True,
                "asset_assign_to_user",
                table=table,
                sys_id=sys_id,
                before=_normalize_asset(before, True),
                changed=False,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        after = _request(config, auth_manager, "PATCH", table, sys_id, body=payload)
        return _tool_result(
            True,
            "asset_assign_to_user",
            table=table,
            sys_id=sys_id,
            changed=True,
            before=_normalize_asset(before, True) if params.return_before_after else None,
            after=_normalize_asset(after, True) if params.return_before_after else None,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_assign_to_user", exc, table)


def asset_return_from_user(
    config: ServerConfig, auth_manager: AuthManager, params: AssetReturnParams
) -> Dict[str, Any]:
    """Handle asset return from user.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = _prefixed_table(params.asset_class)
    try:
        validate_asset_state(params.state, params.substate)
        sys_id = _resolve_reference(
            config,
            auth_manager,
            table,
            params.asset,
            ["asset_tag", "serial_number", "display_name"],
            True,
        )
        before = _request(
            config,
            auth_manager,
            "GET",
            table,
            sys_id,
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        )
        payload = _clean(
            {
                "assigned_to": "",
                "reserved_for": "",
                "stockroom": _resolve_reference(
                    config, auth_manager, "alm_stockroom", params.stockroom, ["name", "code"]
                ),
                "install_status": _state_value(params.state),
                "substatus": _substate_value(params.substate),
                "comments": params.reason,
                "work_notes": params.reason,
            }
        )
        if params.dry_run:
            return _tool_result(
                True,
                "asset_return_from_user",
                table=table,
                sys_id=sys_id,
                before=_normalize_asset(before, True),
                changed=False,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        after = _request(config, auth_manager, "PATCH", table, sys_id, body=payload)
        return _tool_result(
            True,
            "asset_return_from_user",
            table=table,
            sys_id=sys_id,
            changed=True,
            before=_normalize_asset(before, True) if params.return_before_after else None,
            after=_normalize_asset(after, True) if params.return_before_after else None,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_return_from_user", exc, table)


def asset_adjust_consumable_quantity(
    config: ServerConfig, auth_manager: AuthManager, params: ConsumableQuantityParams
) -> Dict[str, Any]:
    """Handle asset adjust consumable quantity.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = "alm_consumable"
    try:
        _check_table_available(config, auth_manager, table, True)
        sys_id = _resolve_reference(
            config,
            auth_manager,
            table,
            params.asset,
            ["asset_tag", "serial_number", "display_name"],
            True,
        )
        before = _request(
            config,
            auth_manager,
            "GET",
            table,
            sys_id,
            params={"sysparm_display_value": "all", "sysparm_exclude_reference_link": "true"},
        )
        current = int(_display(before.get("quantity")) or 0)
        new_quantity = current + params.adjustment
        if new_quantity < 0:
            raise ValueError("Consumable quantity cannot become negative")
        payload = {"quantity": new_quantity, "comments": params.reason, "work_notes": params.reason}
        if params.dry_run:
            return _tool_result(
                True,
                "asset_adjust_consumable_quantity",
                table=table,
                sys_id=sys_id,
                before=_normalize_asset(before, True),
                changed=False,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        after = _request(config, auth_manager, "PATCH", table, sys_id, body=payload)
        return _tool_result(
            True,
            "asset_adjust_consumable_quantity",
            table=table,
            sys_id=sys_id,
            changed=True,
            before=_normalize_asset(before, True),
            after=_normalize_asset(after, True),
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_adjust_consumable_quantity", exc, table)


def asset_transfer_create(
    config: ServerConfig, auth_manager: AuthManager, params: AssetTransferOrderParams
) -> Dict[str, Any]:
    """Handle asset transfer create.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = "alm_transfer_order"
    try:
        warnings = _check_table_available(config, auth_manager, table, True)
        payload = _clean(
            {
                "from_stockroom": _resolve_reference(
                    config,
                    auth_manager,
                    "alm_stockroom",
                    params.from_stockroom,
                    ["name", "code"],
                    True,
                ),
                "to_stockroom": _resolve_reference(
                    config,
                    auth_manager,
                    "alm_stockroom",
                    params.to_stockroom,
                    ["name", "code"],
                    True,
                ),
                "requested_by": _resolve_reference(
                    config,
                    auth_manager,
                    "sys_user",
                    params.requested_by,
                    ["user_name", "email", "name"],
                ),
                "comments": params.reason,
            }
        )
        if params.dry_run:
            return _tool_result(
                True,
                "asset_transfer_create",
                table=table,
                changed=False,
                warnings=warnings,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        record = _request(config, auth_manager, "POST", table, body=payload)
        return _tool_result(
            True,
            "asset_transfer_create",
            table=table,
            sys_id=str(_value(record.get("sys_id"))),
            display_value=str(_display(record.get("number") or record.get("sys_id"))),
            changed=True,
            after=record if params.return_before_after else None,
            warnings=warnings,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_transfer_create", exc, table)


def asset_transfer_line_create(
    config: ServerConfig, auth_manager: AuthManager, params: AssetTransferLineParams
) -> Dict[str, Any]:
    """Handle asset transfer line create.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    table = "alm_transfer_order_line"
    try:
        warnings = _check_table_available(config, auth_manager, table, True)
        if params.asset_class != "consumable" and params.quantity != 1:
            raise ValueError("Non-consumable transfer lines must use quantity=1")
        asset_table = _prefixed_table(params.asset_class)
        payload = _clean(
            {
                "transfer_order": _resolve_reference(
                    config,
                    auth_manager,
                    "alm_transfer_order",
                    params.transfer_order,
                    ["number"],
                    True,
                ),
                "asset": (
                    _resolve_reference(
                        config,
                        auth_manager,
                        asset_table,
                        params.asset,
                        ["asset_tag", "serial_number", "display_name"],
                    )
                    if params.asset
                    else None
                ),
                "model": (
                    _resolve_reference(
                        config, auth_manager, "cmdb_model", params.model, ["name", "model_number"]
                    )
                    if params.model
                    else None
                ),
                "quantity_requested": params.quantity,
            }
        )
        if not payload.get("asset") and not payload.get("model"):
            raise ValueError("transfer line requires asset or model")
        if params.dry_run:
            return _tool_result(
                True,
                "asset_transfer_line_create",
                table=table,
                changed=False,
                warnings=warnings,
                dry_run=True,
                payload=payload,
                correlation_id=params.correlation_id,
            )
        record = _request(config, auth_manager, "POST", table, body=payload)
        return _tool_result(
            True,
            "asset_transfer_line_create",
            table=table,
            sys_id=str(_value(record.get("sys_id"))),
            changed=True,
            after=record if params.return_before_after else None,
            warnings=warnings,
            correlation_id=params.correlation_id,
        )
    except (ValueError, requests.exceptions.RequestException) as exc:
        return _safe_error("asset_transfer_line_create", exc, table)


def asset_retire(
    config: ServerConfig, auth_manager: AuthManager, params: AssetRetireParams
) -> Dict[str, Any]:
    """Handle asset retire.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    if params.substate == "Disposed" and params.data_wipe_confirmed is not True:
        return _tool_result(
            False,
            "asset_retire",
            errors=["data_wipe_confirmed=true is required before final disposal"],
        )
    return asset_update_state(
        config,
        auth_manager,
        AssetStateParams(
            asset=params.asset,
            asset_class=params.asset_class,
            state="Retired",
            substate=params.substate,
            reason=params.reason,
            dry_run=params.dry_run,
            correlation_id=params.correlation_id,
            idempotency_key=params.idempotency_key,
            return_before_after=params.return_before_after,
        ),
    )


def asset_stockroom_create(
    config: ServerConfig, auth_manager: AuthManager, params: CreateStockroomParams
) -> Dict[str, Any]:
    """Handle asset stockroom create.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    return create_stockroom(config, auth_manager, params)


def asset_stockroom_inventory(
    config: ServerConfig, auth_manager: AuthManager, params: AssetSearchParams
) -> Dict[str, Any]:
    """Handle asset stockroom inventory.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.asset_class = params.asset_class or "generic"
    return asset_search(config, auth_manager, params)


def asset_report_inventory(
    config: ServerConfig, auth_manager: AuthManager, params: AssetReportParams
) -> Dict[str, Any]:
    """Handle asset report inventory.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    result = asset_search(config, auth_manager, params)
    if not result.get("success"):
        return result
    records = result.get("records") or []
    summary: Dict[str, int] = {}
    group_by = params.group_by or "install_status"
    for record in records:
        key = str(record.get(group_by) or "Unknown")
        summary[key] = summary.get(key, 0) + 1
    result["summary"] = summary
    result["operation"] = "asset_report_inventory"
    if not params.include_records:
        result["records"] = None
    return result


def unsupported_plugin_tool(tool_name: str, table: str):
    """Handle unsupported plugin tool.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    def _tool(
        config: ServerConfig, auth_manager: AuthManager, params: SafeMutationFields
    ) -> Dict[str, Any]:
        try:
            warnings = _check_table_available(config, auth_manager, table, required=False)
            if warnings:
                return _tool_result(
                    False,
                    tool_name,
                    table=table,
                    warnings=warnings,
                    errors=[f"{tool_name} requires unavailable table {table}"],
                    correlation_id=getattr(params, "correlation_id", None),
                )
            return _tool_result(
                False,
                tool_name,
                table=table,
                errors=[
                    f"{tool_name} is plugin-dependent and not implemented beyond availability "
                    f"detection"
                ],
            )
        except (ValueError, requests.exceptions.RequestException) as exc:
            return _safe_error(tool_name, exc, table)

    return _tool


# Small named wrappers for lifecycle-state tools requested by the MCP surface.
def mark_asset_available(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset available.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Available"
    return asset_update_state(config, auth_manager, params)


def mark_asset_reserved(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset reserved.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Reserved"
    return asset_update_state(config, auth_manager, params)


def mark_asset_on_order(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset on order.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "On order", None
    return asset_update_state(config, auth_manager, params)


def mark_asset_in_use(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset in use.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state = "In use"
    params.substate = params.substate or None
    return asset_update_state(config, auth_manager, params)


def mark_asset_in_transit(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset in transit.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state = "In transit"
    params.substate = params.substate or "Available"
    return asset_update_state(config, auth_manager, params)


def mark_asset_in_maintenance(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset in maintenance.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In maintenance", None
    return asset_update_state(config, auth_manager, params)


def mark_asset_retired(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset retired.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state = "Retired"
    params.substate = params.substate or "Obsolete"
    return asset_update_state(config, auth_manager, params)


def mark_asset_missing(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset missing.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state = "Missing"
    params.substate = params.substate or "Lost"
    return asset_update_state(config, auth_manager, params)


def mark_asset_lost(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset lost.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "Missing", "Lost"
    return asset_update_state(config, auth_manager, params)


def mark_asset_stolen(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset stolen.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "Missing", "Stolen"
    return asset_update_state(config, auth_manager, params)


def mark_asset_quarantined(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset quarantined.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Quarantine"
    return asset_update_state(config, auth_manager, params)


def mark_asset_legal_hold(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset legal hold.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Legal hold"
    return asset_update_state(config, auth_manager, params)


def mark_asset_pending_repair(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset pending repair.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Pending repair"
    return asset_update_state(config, auth_manager, params)


def mark_asset_pending_disposal(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset pending disposal.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Pending disposal"
    return asset_update_state(config, auth_manager, params)


def mark_asset_pending_transfer(
    config: ServerConfig, auth_manager: AuthManager, params: AssetStateParams
) -> Dict[str, Any]:
    """Handle mark asset pending transfer.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    params.state, params.substate = "In stock", "Pending transfer"
    return asset_update_state(config, auth_manager, params)


asset_repair_create = unsupported_plugin_tool("asset_repair_create", "asset_task")
asset_repair_close = unsupported_plugin_tool("asset_repair_close", "asset_task")
asset_sam_create_entitlement = unsupported_plugin_tool(
    "asset_sam_create_entitlement", "alm_entitlement"
)
