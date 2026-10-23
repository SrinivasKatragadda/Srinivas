"""Shared helpers for scoped application ServiceNow tools.

This module centralizes the low-level utilities used by scoped-application
tooling: table lookup metadata, artifact profiles, record normalization,
reference resolution, and query helpers. It is the shared interpretation layer
for the scoped app reader and writer modules.
"""

import re
from typing import Any, Dict, List, Literal, Optional

import requests

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

ArtifactType = Literal[
    "scoped_app",
    "scope",
    "app_menu",
    "module",
    "application_file",
    "app_metadata",
]

ARTIFACT_PROFILES: Dict[str, Dict[str, Any]] = {
    "scoped_app": {
        "table": "sys_app",
        "label": "Scoped application / custom application",
        "lookup_fields": ["sys_id", "name", "scope"],
        "key_fields": [
            "sys_id",
            "name",
            "scope",
            "version",
            "short_description",
            "active",
            "sys_scope",
            "sys_package",
            "sys_updated_on",
        ],
    },
    "scope": {
        "table": "sys_scope",
        "label": "Application scope",
        "lookup_fields": ["sys_id", "name", "scope"],
        "key_fields": ["sys_id", "name", "scope", "private", "runtime_access_tracking"],
    },
    "app_menu": {
        "table": "sys_app_application",
        "label": "Application menu",
        "lookup_fields": ["sys_id", "title", "name"],
        "key_fields": [
            "sys_id",
            "title",
            "name",
            "active",
            "order",
            "roles",
            "sys_scope",
            "sys_package",
            "sys_app",
        ],
    },
    "module": {
        "table": "sys_app_module",
        "label": "Application module",
        "lookup_fields": ["sys_id", "title", "name"],
        "key_fields": [
            "sys_id",
            "title",
            "name",
            "active",
            "order",
            "application",
            "link_type",
            "table",
            "query",
            "roles",
            "sys_scope",
            "sys_package",
            "sys_app",
        ],
    },
    "application_file": {
        "table": "sys_metadata",
        "label": "Application file metadata",
        "lookup_fields": ["sys_id", "name", "sys_name"],
        "key_fields": [
            "sys_id",
            "name",
            "sys_name",
            "sys_class_name",
            "sys_scope",
            "sys_package",
            "sys_update_name",
            "sys_updated_on",
        ],
        "table_override_note": (
            "Create and update application files through their concrete metadata table "
            "when possible, for example sys_script_include, sys_script, sys_ui_action, "
            "sys_ui_policy, sys_db_object, or sys_dictionary."
        ),
    },
    "app_metadata": {
        "table": "sys_metadata",
        "label": "Generic scoped app metadata",
        "lookup_fields": ["sys_id", "name", "sys_name"],
        "key_fields": [
            "sys_id",
            "name",
            "sys_name",
            "sys_class_name",
            "sys_scope",
            "sys_package",
            "sys_update_name",
            "sys_updated_on",
        ],
        "table_override_note": "Use table when working with a concrete metadata record type.",
    },
}


def artifact_table(artifact_type: str, table: Optional[str]) -> str:
    """Resolve the backing table for an artifact type.

    Honors an explicit table override and otherwise falls back to the artifact
    profile mapping.
    """
    if table:
        return table
    profile = ARTIFACT_PROFILES.get(artifact_type)
    if not profile:
        raise ValueError(f"Unsupported artifact_type: {artifact_type}")
    return str(profile["table"])


def api_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    """Build a Table API URL for the given table and optional record id."""
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def extract_result(response: requests.Response) -> Any:
    """Raise for HTTP errors and normalize ServiceNow result envelopes."""
    response.raise_for_status()
    payload = response.json() if response.content else {}
    return payload.get("result", payload)


def display_value(value: Any) -> Any:
    """Extract display value from ServiceNow reference envelopes when present."""
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def raw_value(value: Any) -> Any:
    """Extract raw reference value from ServiceNow envelopes when present."""
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def bool_text(value: Optional[bool]) -> Optional[str]:
    """Convert optional bool to ServiceNow-style lowercase text representation."""
    if value is None:
        return None
    return "true" if value else "false"


def clean_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """Remove keys with None values before sending request bodies to Table API."""
    return {key: value for key, value in body.items() if value is not None}


def normalize_record(record: Dict[str, Any], artifact_type: str, table: str) -> Dict[str, Any]:
    """Normalize raw Table API record into consistent scoped-app artifact output.

    Preserves key profile fields and sys_* metadata while exposing both display
    and raw scope/package identifiers for downstream tooling.
    """
    profile = ARTIFACT_PROFILES.get(artifact_type, {})
    fields = {
        key: display_value(value)
        for key, value in record.items()
        if key in profile.get("key_fields", []) or key.startswith("sys_")
    }
    return {
        "sys_id": raw_value(record.get("sys_id")),
        "name": display_value(record.get("name") or record.get("sys_name") or record.get("title")),
        "artifact_type": artifact_type,
        "table": table,
        "sys_scope": display_value(record.get("sys_scope")),
        "sys_scope_sys_id": raw_value(record.get("sys_scope")),
        "sys_package": display_value(record.get("sys_package")),
        "sys_package_sys_id": raw_value(record.get("sys_package")),
        "fields": fields,
        "raw": record,
    }


def query_records(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query: Optional[str],
    limit: int = 10,
    offset: int = 0,
    fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Query records from a table with display values enabled."""
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
        api_url(config, table),
        headers=auth_manager.get_headers(),
        params=params,
        timeout=config.timeout,
    )
    result = extract_result(response)
    return result if isinstance(result, list) else []


def resolve_sys_id(
    config: ServerConfig,
    auth_manager: AuthManager,
    artifact_type: str,
    table: str,
    identifier: str,
    identifier_field: Optional[str] = None,
) -> str:
    """Resolve a record identifier to a concrete sys_id.

    Accepts direct sys_id values or performs lookup-field matching based on the
    artifact profile.
    """
    cleaned = identifier.strip()
    if cleaned.startswith("sys_id:"):
        cleaned = cleaned.replace("sys_id:", "", 1).strip()
    if SYS_ID_PATTERN.fullmatch(cleaned):
        return cleaned

    lookup_fields = [identifier_field] if identifier_field else []
    lookup_fields.extend(ARTIFACT_PROFILES.get(artifact_type, {}).get("lookup_fields", []))
    lookup_fields = [field for field in dict.fromkeys(lookup_fields) if field != "sys_id"]
    query = "^OR".join(f"{field}={cleaned}" for field in lookup_fields)
    records = query_records(config, auth_manager, table, query, limit=2)
    if not records:
        raise ValueError(f"{artifact_type} record not found in {table}: {identifier}")
    if len(records) > 1:
        for record in records:
            if any(
                str(display_value(record.get(field))).strip() == cleaned for field in lookup_fields
            ):
                return str(raw_value(record.get("sys_id")))
    return str(raw_value(records[0].get("sys_id")))
