"""
SLA definition tools for the ServiceNow MCP server.

These tools manage SLA Definition records through the contract_sla table. Some
ServiceNow instances expose legacy SLA definitions through the sla table, so
the read and update helpers can auto-detect the active backing table before
writing or querying records.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

SLA_TABLE = "contract_sla"
SLA_LEGACY_TABLE = "sla"
SLA_TABLE_CANDIDATES = (SLA_TABLE, SLA_LEGACY_TABLE)
SCHEDULE_TABLE = "cmn_schedule"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
SERVICE_NOW_DURATION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
COLON_DURATION_PATTERN = re.compile(
    r"^(?:(?P<days>\d+)\s+)?(?P<hours>\d+):(?P<minutes>\d{1,2}):(?P<seconds>\d{1,2})$"
)
UNIT_DURATION_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b",
    re.IGNORECASE,
)


class ListSLASchedulesParams(BaseModel):
    """Parameters for listing schedules that can be selected on SLA definitions."""

    limit: int = Field(20, description="Maximum number of schedules to return")
    offset: int = Field(0, description="Offset for pagination")
    active: Optional[bool] = Field(True, description="Filter by active status")
    query: Optional[str] = Field(None, description="Search schedule name or description")
    encoded_query: Optional[str] = Field(
        None, description="Additional ServiceNow encoded query for instance-specific filters"
    )
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class ListSLAsParams(BaseModel):
    """Parameters for listing SLA definitions."""

    limit: int = Field(10, description="Maximum number of SLA definitions to return")
    offset: int = Field(0, description="Offset for pagination")
    sla_table: Optional[str] = Field(
        None,
        description=(
            "SLA definition backing table to query, such as contract_sla or sla. "
            "Omit to query both."
        ),
    )
    active: Optional[bool] = Field(None, description="Filter by active status")
    table: Optional[str] = Field(None, description="Filter by target table, such as incident")
    query: Optional[str] = Field(None, description="Name contains filter")
    encoded_query: Optional[str] = Field(
        None, description="Additional ServiceNow encoded query for instance-specific filters"
    )
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetSLAParams(BaseModel):
    """Parameters for getting an SLA definition."""

    sla_id: str = Field(..., description="SLA Definition sys_id, sys_id:<sys_id>, or exact name")
    sla_table: Optional[str] = Field(
        None,
        description=(
            "SLA definition backing table, such as contract_sla or sla. Omit to auto-detect."
        ),
    )
    include_raw: bool = Field(False, description="Include the raw ServiceNow record")


class CreateSLAParams(BaseModel):
    """Parameters for creating an SLA definition."""

    name: str = Field(..., description="Name of the SLA definition")
    table: str = Field(..., description="Target table, such as incident, task, or change_request")
    sla_table: Optional[str] = Field(
        None,
        description=(
            "SLA definition backing table to create in. Defaults to contract_sla; "
            "use sla for instances that store SLA definitions there."
        ),
    )
    active: bool = Field(True, description="Whether the SLA definition is active")
    duration: Optional[str] = Field(
        None,
        description=(
            "SLA duration. Accepts ServiceNow value 'YYYY-MM-DD HH:MM:SS', "
            "HH:MM:SS, 'D HH:MM:SS', seconds as a number string, or text like "
            "'2 hours 30 minutes'. Duration component fields take precedence."
        ),
    )
    duration_days: Optional[int] = Field(None, description="Duration days")
    duration_hours: Optional[int] = Field(None, description="Duration hours")
    duration_minutes: Optional[int] = Field(None, description="Duration minutes")
    duration_seconds: Optional[int] = Field(None, description="Duration seconds")
    start_condition: Optional[str] = Field(
        None, description="Encoded condition that starts the SLA"
    )
    stop_condition: Optional[str] = Field(None, description="Encoded condition that stops the SLA")
    pause_condition: Optional[str] = Field(
        None, description="Encoded condition that pauses the SLA"
    )
    reset_condition: Optional[str] = Field(
        None, description="Encoded condition that resets the SLA"
    )
    schedule: Optional[str] = Field(
        None,
        description=(
            "Schedule sys_id, sys_id:<sys_id>, or exact schedule name from cmn_schedule. "
            "Use list_sla_schedules to select one."
        ),
    )
    timezone: Optional[str] = Field(None, description="Timezone name or sys_id")
    workflow: Optional[str] = Field(None, description="Workflow sys_id or display value")
    type: Optional[str] = Field(None, description="SLA type, such as SLA, OLA, or Underpinning")
    description: Optional[str] = Field(None, description="Description of the SLA definition")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional SLA definition fields to send to ServiceNow"
    )


class UpdateSLAParams(BaseModel):
    """Parameters for updating an SLA definition."""

    sla_id: str = Field(..., description="SLA Definition sys_id, sys_id:<sys_id>, or exact name")
    sla_table: Optional[str] = Field(
        None,
        description=(
            "SLA definition backing table, such as contract_sla or sla. "
            "Omit to auto-detect and update the table where the record is found."
        ),
    )
    name: Optional[str] = Field(None, description="Updated SLA definition name")
    table: Optional[str] = Field(None, description="Updated target table")
    active: Optional[bool] = Field(None, description="Updated active status")
    duration: Optional[str] = Field(
        None,
        description=(
            "Updated SLA duration. Accepts ServiceNow value 'YYYY-MM-DD HH:MM:SS', "
            "HH:MM:SS, 'D HH:MM:SS', seconds as a number string, or text like "
            "'2 hours 30 minutes'. Duration component fields take precedence."
        ),
    )
    duration_days: Optional[int] = Field(None, description="Updated duration days")
    duration_hours: Optional[int] = Field(None, description="Updated duration hours")
    duration_minutes: Optional[int] = Field(None, description="Updated duration minutes")
    duration_seconds: Optional[int] = Field(None, description="Updated duration seconds")
    start_condition: Optional[str] = Field(None, description="Updated start condition")
    stop_condition: Optional[str] = Field(None, description="Updated stop condition")
    pause_condition: Optional[str] = Field(None, description="Updated pause condition")
    reset_condition: Optional[str] = Field(None, description="Updated reset condition")
    schedule: Optional[str] = Field(
        None,
        description=(
            "Updated schedule sys_id, sys_id:<sys_id>, or exact schedule name from cmn_schedule. "
            "Use list_sla_schedules to select one."
        ),
    )
    timezone: Optional[str] = Field(None, description="Updated timezone name or sys_id")
    workflow: Optional[str] = Field(None, description="Updated workflow sys_id or display value")
    type: Optional[str] = Field(None, description="Updated SLA type")
    description: Optional[str] = Field(None, description="Updated description")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional SLA definition fields to patch on the record"
    )


def _clean_sla_table(sla_table: Optional[str]) -> Optional[str]:
    if sla_table is None:
        return None
    cleaned = str(sla_table).strip().lower()
    if not cleaned:
        return None
    if not TABLE_NAME_PATTERN.fullmatch(cleaned):
        raise ValueError(f"Invalid SLA table name: {sla_table}")
    return cleaned


def _sla_tables(sla_table: Optional[str] = None) -> List[str]:
    cleaned = _clean_sla_table(sla_table)
    if cleaned:
        return [cleaned]
    return list(SLA_TABLE_CANDIDATES)


def _target_table_field(sla_table: str) -> str:
    return "table" if sla_table == SLA_LEGACY_TABLE else "collection"


def _table_url(
    config: ServerConfig, sys_id: Optional[str] = None, sla_table: str = SLA_TABLE
) -> str:
    table = _clean_sla_table(sla_table) or SLA_TABLE
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _generic_table_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _response_error_detail(response: requests.Response) -> str:
    status = f"HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if body:
        body_text = str(body)
        if len(body_text) > 1000:
            body_text = f"{body_text[:1000]}..."
        return f"{status}: {body_text}"
    return status


def _extract_display_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _extract_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _read_bool(value: Any) -> Optional[bool]:
    value = _extract_display_value(value)
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
    if value is None:
        return None
    return "true" if value else "false"


def _clean_identifier(identifier: str) -> str:
    cleaned = str(identifier).strip()
    if not cleaned:
        raise ValueError("Record identifier cannot be empty")
    return cleaned


def _identifier_variants(identifier: str) -> List[str]:
    variants: List[str] = []
    for candidate in (identifier, identifier.upper(), identifier.lower()):
        if candidate not in variants:
            variants.append(candidate)
    return variants


def _matches_identifier(value: Any, identifier: str) -> bool:
    display_value = _extract_display_value(value)
    if display_value is None:
        return False
    return str(display_value).strip().casefold() == identifier.casefold()


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    sys_id = _extract_value(record.get("sys_id"))
    if sys_id is None:
        return None
    return str(sys_id).strip() or None


def _extract_sys_id(identifier: str) -> Optional[str]:
    cleaned = _clean_identifier(identifier)
    if cleaned.startswith("sys_id:"):
        cleaned = cleaned.replace("sys_id:", "", 1).strip()
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


def _build_query(parts: List[Optional[str]]) -> str:
    return "^".join(part for part in parts if part)


def _format_duration_seconds(total_seconds: int) -> str:
    if total_seconds < 0:
        raise ValueError("SLA duration cannot be negative")
    return (datetime(1970, 1, 1) + timedelta(seconds=total_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _duration_components_to_seconds(params: Any) -> Optional[int]:
    components = [
        getattr(params, "duration_days", None),
        getattr(params, "duration_hours", None),
        getattr(params, "duration_minutes", None),
        getattr(params, "duration_seconds", None),
    ]
    if all(value is None for value in components):
        return None

    days = getattr(params, "duration_days", None) or 0
    hours = getattr(params, "duration_hours", None) or 0
    minutes = getattr(params, "duration_minutes", None) or 0
    seconds = getattr(params, "duration_seconds", None) or 0
    if min(days, hours, minutes, seconds) < 0:
        raise ValueError("SLA duration components cannot be negative")
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _normalize_duration(params: Any) -> Optional[str]:
    component_seconds = _duration_components_to_seconds(params)
    if component_seconds is not None:
        return _format_duration_seconds(component_seconds)

    duration = getattr(params, "duration", None)
    if duration is None:
        return None
    cleaned = str(duration).strip()
    if not cleaned:
        return None
    if SERVICE_NOW_DURATION_PATTERN.fullmatch(cleaned):
        return cleaned
    if cleaned.isdigit():
        return _format_duration_seconds(int(cleaned))

    colon_match = COLON_DURATION_PATTERN.fullmatch(cleaned)
    if colon_match:
        days = int(colon_match.group("days") or 0)
        hours = int(colon_match.group("hours"))
        minutes = int(colon_match.group("minutes"))
        seconds = int(colon_match.group("seconds"))
        if minutes > 59 or seconds > 59:
            raise ValueError("SLA duration minutes and seconds must be less than 60")
        return _format_duration_seconds(days * 86400 + hours * 3600 + minutes * 60 + seconds)

    total_seconds = 0
    matched = False
    for match in UNIT_DURATION_PATTERN.finditer(cleaned):
        matched = True
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        if unit in {"day", "days", "d"}:
            total_seconds += int(value * 86400)
        elif unit in {"hour", "hours", "hr", "hrs", "h"}:
            total_seconds += int(value * 3600)
        elif unit in {"minute", "minutes", "min", "mins", "m"}:
            total_seconds += int(value * 60)
        elif unit in {"second", "seconds", "sec", "secs", "s"}:
            total_seconds += int(value)
    if matched:
        return _format_duration_seconds(total_seconds)

    # Leave uncommon instance-specific duration strings untouched.
    return cleaned


def _normalize_schedule(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sys_id": _record_sys_id(record),
        "name": record.get("name"),
        "description": record.get("description"),
        "active": _read_bool(record.get("active")),
        "timezone": _extract_display_value(record.get("time_zone") or record.get("timezone")),
        "type": _extract_display_value(record.get("type")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }


def _resolve_schedule_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    schedule: str,
) -> str:
    schedule_sys_id = _extract_sys_id(schedule)
    if schedule_sys_id:
        return schedule_sys_id

    headers = auth_manager.get_headers()
    params = {
        "sysparm_query": f"name={schedule}",
        "sysparm_limit": 2,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    response = requests.get(
        _generic_table_url(config, SCHEDULE_TABLE),
        headers=headers,
        params=params,
        timeout=config.timeout,
    )
    response.raise_for_status()
    results = response.json().get("result", [])
    if not results:
        raise ValueError(f"Schedule not found: {schedule}")
    if len(results) > 1:
        raise ValueError(f"Multiple schedules matched {schedule}; use a schedule sys_id")
    sys_id = _record_sys_id(results[0])
    if not sys_id:
        raise ValueError(f"Schedule does not have a sys_id: {schedule}")
    return sys_id


def _normalize_sla(record: Dict[str, Any], sla_table: Optional[str] = None) -> Dict[str, Any]:
    return {
        "sla_table": sla_table,
        "sys_id": _record_sys_id(record),
        "name": record.get("name"),
        "table": _extract_display_value(record.get("collection") or record.get("table")),
        "active": _read_bool(record.get("active")),
        "duration": _extract_display_value(record.get("duration")),
        "duration_value": _extract_value(record.get("duration")),
        "start_condition": record.get("start_condition"),
        "stop_condition": record.get("stop_condition"),
        "pause_condition": record.get("pause_condition"),
        "reset_condition": record.get("reset_condition"),
        "schedule": _extract_display_value(record.get("schedule")),
        "schedule_sys_id": _extract_value(record.get("schedule")),
        "timezone": _extract_display_value(record.get("timezone")),
        "workflow": _extract_display_value(record.get("workflow")),
        "type": _extract_display_value(record.get("type")),
        "description": record.get("description"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }


def _build_sla_body(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: Any,
    include_none: bool = False,
    sla_table: str = SLA_TABLE,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    target_sla_table = _clean_sla_table(sla_table) or SLA_TABLE
    field_map = {
        "name": "name",
        "table": _target_table_field(target_sla_table),
        "start_condition": "start_condition",
        "stop_condition": "stop_condition",
        "pause_condition": "pause_condition",
        "reset_condition": "reset_condition",
        "timezone": "timezone",
        "workflow": "workflow",
        "type": "type",
        "description": "description",
    }

    for model_field, table_field in field_map.items():
        value = getattr(params, model_field, None)
        if value is not None or include_none:
            body[table_field] = value

    duration = _normalize_duration(params)
    if duration is not None:
        body["duration"] = duration

    schedule = getattr(params, "schedule", None)
    if schedule is not None:
        body["schedule"] = _resolve_schedule_sys_id(config, auth_manager, schedule)

    active_value = _coerce_bool(getattr(params, "active", None))
    if active_value is not None:
        body["active"] = active_value

    attributes = getattr(params, "attributes", None)
    if attributes:
        body.update(attributes)

    return body


def _get_sla_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    identifier: str,
    sla_table: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    headers = auth_manager.get_headers()
    base_params = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    cleaned_identifier = _clean_identifier(identifier)
    sys_id = _extract_sys_id(cleaned_identifier)
    attempted_tables = _sla_tables(sla_table)
    errors: List[str] = []

    for target_sla_table in attempted_tables:
        params = dict(base_params)
        try:
            if sys_id:
                response = requests.get(
                    _table_url(config, sys_id, sla_table=target_sla_table),
                    headers=headers,
                    params=params,
                    timeout=config.timeout,
                )
                if response.status_code == 404:
                    errors.append(f"{target_sla_table}: not found")
                    continue
                response.raise_for_status()
                result = response.json().get("result", {})
                if result:
                    return target_sla_table, result
                errors.append(f"{target_sla_table}: empty response")
                continue

            params["sysparm_query"] = _build_query(
                [f"name={value}" for value in _identifier_variants(cleaned_identifier)]
            )
            params["sysparm_limit"] = 10
            response = requests.get(
                _table_url(config, sla_table=target_sla_table),
                headers=headers,
                params=params,
                timeout=config.timeout,
            )
            if response.status_code == 404:
                errors.append(f"{target_sla_table}: table or record not found")
                continue
            response.raise_for_status()
            results = response.json().get("result", [])
            exact_matches = [
                record
                for record in results
                if _matches_identifier(record.get("name"), cleaned_identifier)
            ]
            if len(exact_matches) == 1:
                return target_sla_table, exact_matches[0]
            if len(exact_matches) > 1:
                errors.append(
                    f"{target_sla_table}: multiple exact matches for {cleaned_identifier}"
                )
                continue
            if len(results) == 1:
                return target_sla_table, results[0]
            if len(results) > 1:
                errors.append(f"{target_sla_table}: multiple matches for {cleaned_identifier}")
                continue

            like_params = dict(base_params)
            like_params["sysparm_query"] = f"nameLIKE{cleaned_identifier}"
            like_params["sysparm_limit"] = 10
            like_response = requests.get(
                _table_url(config, sla_table=target_sla_table),
                headers=headers,
                params=like_params,
                timeout=config.timeout,
            )
            if like_response.status_code == 404:
                errors.append(f"{target_sla_table}: table or record not found")
                continue
            like_response.raise_for_status()
            like_results = like_response.json().get("result", [])
            like_matches = [
                record
                for record in like_results
                if _matches_identifier(record.get("name"), cleaned_identifier)
            ]
            if len(like_matches) == 1:
                return target_sla_table, like_matches[0]
            if len(like_matches) > 1:
                errors.append(
                    f"{target_sla_table}: multiple exact matches for {cleaned_identifier}"
                )
        except requests.exceptions.RequestException as exc:
            errors.append(f"{target_sla_table}: {exc}")

    tried = ", ".join(attempted_tables)
    details = f" Tried: {'; '.join(errors)}" if errors else ""
    raise ValueError(f"SLA not found in {tried}: {cleaned_identifier}.{details}")


def list_sla_schedules(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListSLASchedulesParams,
) -> Dict[str, Any]:
    """List schedules that can be selected on SLA definitions."""
    try:
        query = _build_query(
            [
                f"active={_coerce_bool(params.active)}" if params.active is not None else None,
                f"nameLIKE{params.query}^ORdescriptionLIKE{params.query}" if params.query else None,
                params.encoded_query,
            ]
        )
        request_params: Dict[str, Any] = {
            "sysparm_limit": params.limit,
            "sysparm_offset": params.offset,
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        }
        if query:
            request_params["sysparm_query"] = query

        headers = auth_manager.get_headers()
        response = requests.get(
            _generic_table_url(config, SCHEDULE_TABLE),
            headers=headers,
            params=request_params,
            timeout=config.timeout,
        )
        response.raise_for_status()
        records = response.json().get("result", [])
        schedules = [_normalize_schedule(record) for record in records]
        result: Dict[str, Any] = {
            "success": True,
            "message": f"Found {len(schedules)} SLA schedules",
            "schedules": schedules,
            "total": len(schedules),
            "limit": params.limit,
            "offset": params.offset,
        }
        if params.include_raw:
            result["raw_records"] = records
        return result
    except Exception as exc:
        logger.error("Error listing SLA schedules: %s", exc)
        return {
            "success": False,
            "message": f"Error listing SLA schedules: {exc}",
            "schedules": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def list_slas(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListSLAsParams,
) -> Dict[str, Any]:
    """List SLA definitions from ServiceNow."""
    try:
        headers = auth_manager.get_headers()
        slas: List[Dict[str, Any]] = []
        raw_records: List[Dict[str, Any]] = []
        warnings: List[str] = []

        for target_sla_table in _sla_tables(params.sla_table):
            query = _build_query(
                [
                    f"active={_coerce_bool(params.active)}" if params.active is not None else None,
                    (
                        f"{_target_table_field(target_sla_table)}={params.table}"
                        if params.table
                        else None
                    ),
                    f"nameLIKE{params.query}" if params.query else None,
                    params.encoded_query,
                ]
            )
            request_params: Dict[str, Any] = {
                "sysparm_limit": params.limit,
                "sysparm_offset": params.offset,
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            }
            if query:
                request_params["sysparm_query"] = query

            try:
                response = requests.get(
                    _table_url(config, sla_table=target_sla_table),
                    headers=headers,
                    params=request_params,
                    timeout=config.timeout,
                )
                if response.status_code == 404 and not params.sla_table:
                    warnings.append(f"{target_sla_table}: table not found")
                    continue
                response.raise_for_status()
                records = response.json().get("result", [])
                slas.extend(_normalize_sla(record, target_sla_table) for record in records)
                if params.include_raw:
                    raw_records.extend(
                        {"sla_table": target_sla_table, "record": record} for record in records
                    )
            except requests.exceptions.RequestException as exc:
                if params.sla_table:
                    raise
                warnings.append(f"{target_sla_table}: {exc}")

        result: Dict[str, Any] = {
            "success": True,
            "message": f"Found {len(slas)} SLA definitions",
            "slas": slas,
            "total": len(slas),
            "limit": params.limit,
            "offset": params.offset,
        }
        if warnings:
            result["warnings"] = warnings
        if params.include_raw:
            result["raw_records"] = raw_records
        return result
    except Exception as exc:
        logger.error("Error listing SLA definitions: %s", exc)
        return {
            "success": False,
            "message": f"Error listing SLA definitions: {exc}",
            "slas": [],
            "total": 0,
            "limit": params.limit,
            "offset": params.offset,
        }


def get_sla(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetSLAParams,
) -> Dict[str, Any]:
    """Get a specific SLA definition from ServiceNow."""
    try:
        target_sla_table, record = _get_sla_record(
            config, auth_manager, params.sla_id, params.sla_table
        )
        result: Dict[str, Any] = {
            "success": True,
            "message": f"Retrieved SLA definition: {record.get('name')}",
            "sla": _normalize_sla(record, target_sla_table),
        }
        if params.include_raw:
            result["record"] = record
        return result
    except Exception as exc:
        logger.error("Error getting SLA definition: %s", exc)
        return {
            "success": False,
            "message": f"Error getting SLA definition: {exc}",
        }


def create_sla(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateSLAParams,
) -> Dict[str, Any]:
    """Create an SLA definition in ServiceNow."""
    try:
        target_sla_table = _clean_sla_table(params.sla_table) or SLA_TABLE
        body = _build_sla_body(config, auth_manager, params, sla_table=target_sla_table)
        headers = auth_manager.get_headers()
        response = requests.post(
            _table_url(config, sla_table=target_sla_table),
            headers=headers,
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            json=body,
            timeout=config.timeout,
        )
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise RuntimeError(
                "ServiceNow rejected SLA creation on "
                f"{target_sla_table}. {_response_error_detail(response)}"
            ) from exc
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": f"Created SLA definition: {record.get('name')}",
            "sla": _normalize_sla(record, target_sla_table),
            "record": record,
        }
    except Exception as exc:
        logger.error("Error creating SLA definition: %s", exc)
        return {
            "success": False,
            "message": f"Error creating SLA definition: {exc}",
        }


def update_sla(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateSLAParams,
) -> Dict[str, Any]:
    """Update an SLA definition in ServiceNow."""
    try:
        target_sla_table, current = _get_sla_record(
            config, auth_manager, params.sla_id, params.sla_table
        )
        sys_id = _record_sys_id(current)
        if not sys_id:
            raise ValueError(f"SLA does not have a sys_id: {params.sla_id}")

        body = _build_sla_body(config, auth_manager, params, sla_table=target_sla_table)
        if not body:
            return {
                "success": True,
                "message": "No changes requested",
                "sla": _normalize_sla(current, target_sla_table),
                "record": current,
            }

        headers = auth_manager.get_headers()
        response = requests.patch(
            _table_url(config, sys_id, sla_table=target_sla_table),
            headers=headers,
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            json=body,
            timeout=config.timeout,
        )
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            payload_fields = ", ".join(sorted(body.keys()))
            raise RuntimeError(
                "ServiceNow rejected SLA update on "
                f"{target_sla_table}/{sys_id}. Payload fields: {payload_fields}. "
                f"{_response_error_detail(response)}"
            ) from exc
        record = response.json().get("result", {})
        return {
            "success": True,
            "message": f"Updated SLA definition: {record.get('name')}",
            "sla": _normalize_sla(record, target_sla_table),
            "record": record,
        }
    except Exception as exc:
        logger.error("Error updating SLA definition: %s", exc)
        return {
            "success": False,
            "message": f"Error updating SLA definition: {exc}",
        }
