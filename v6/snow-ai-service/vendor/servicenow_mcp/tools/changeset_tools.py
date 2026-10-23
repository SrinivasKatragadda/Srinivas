"""
Changeset tools for the ServiceNow MCP server.

This module provides helpers for update sets, remote update sets, update XML
records, and related preview artifacts. It is the change-package management
layer used to inspect and track ServiceNow configuration changes before they
are promoted or applied.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

# Type variable for Pydantic models
T = TypeVar("T", bound=BaseModel)
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
UPDATE_SET_TABLE = "sys_update_set"
REMOTE_UPDATE_SET_TABLE = "sys_remote_update_set"
UPDATE_XML_TABLE = "sys_update_xml"
PREVIEW_PROBLEM_TABLE = "sys_update_preview_problem"
USER_PREFERENCE_TABLE = "sys_user_preference"
SYS_SCOPE_TABLE = "sys_scope"
SYS_SCOPE_FIELDS = "sys_id,name,scope"
UPDATE_SET_FIELDS = (
    "sys_id,name,description,state,current,application,sys_created_on,"
    "sys_updated_on,sys_created_by,sys_updated_by,parent,base_update_set"
)
REMOTE_UPDATE_SET_FIELDS = (
    "sys_id,name,description,state,update_set,application,sys_created_on,"
    "sys_updated_on,sys_created_by,sys_updated_by"
)


class ListChangesetsParams(BaseModel):
    """Parameters for listing changesets."""

    limit: Optional[int] = Field(10, description="Maximum number of records to return")
    offset: Optional[int] = Field(0, description="Offset to start from")
    state: Optional[str] = Field(None, description="Filter by state")
    application: Optional[str] = Field(None, description="Filter by application")
    developer: Optional[str] = Field(None, description="Filter by developer")
    timeframe: Optional[str] = Field(
        None, description="Filter by timeframe (recent, last_week, last_month)"
    )
    query: Optional[str] = Field(None, description="Additional query string")


class GetChangesetDetailsParams(BaseModel):
    """Parameters for getting changeset details."""

    changeset_id: str = Field(..., description="Changeset ID or sys_id")


class CreateChangesetParams(BaseModel):
    """Parameters for creating a changeset."""

    name: str = Field(..., description="Name of the changeset")
    description: Optional[str] = Field(None, description="Description of the changeset")
    application: str = Field(..., description="Application the changeset belongs to")
    developer: Optional[str] = Field(None, description="Developer responsible for the changeset")


class UpdateChangesetParams(BaseModel):
    """Parameters for updating a changeset."""

    changeset_id: str = Field(..., description="Changeset ID or sys_id")
    name: Optional[str] = Field(None, description="Name of the changeset")
    description: Optional[str] = Field(None, description="Description of the changeset")
    state: Optional[str] = Field(None, description="State of the changeset")
    developer: Optional[str] = Field(None, description="Developer responsible for the changeset")


class CommitChangesetParams(BaseModel):
    """Parameters for committing a changeset."""

    changeset_id: str = Field(..., description="Changeset ID or sys_id")
    commit_message: Optional[str] = Field(None, description="Commit message")


class PublishChangesetParams(BaseModel):
    """Parameters for publishing a changeset."""

    changeset_id: str = Field(..., description="Changeset ID or sys_id")
    publish_notes: Optional[str] = Field(None, description="Notes for publishing")


class AddFileToChangesetParams(BaseModel):
    """Parameters for adding a file to a changeset."""

    changeset_id: str = Field(..., description="Changeset ID or sys_id")
    file_path: str = Field(..., description="Path of the file to add")
    file_content: str = Field(..., description="Content of the file")


class ListUpdateSetsParams(ListChangesetsParams):
    """Parameters for listing update sets."""


class GetUpdateSetDetailsParams(BaseModel):
    """Parameters for getting update set details."""

    update_set_id: str = Field(..., description="Update set sys_id or name")


class CreateUpdateSetParams(BaseModel):
    """Parameters for creating an update set."""

    name: str = Field(..., description="Update set name")
    description: Optional[str] = Field(None, description="Update set description")
    application: Optional[str] = Field(None, description="Application scope sys_id or name")
    developer: Optional[str] = Field(None, description="Developer responsible for the update set")
    state: Optional[str] = Field(None, description="Initial update set state")
    make_current: bool = Field(True, description="Whether to make this the current update set")
    parent_update_set_id: Optional[str] = Field(
        None, description="Optional parent/base update set sys_id"
    )


class SwitchCurrentUpdateSetParams(BaseModel):
    """Parameters for switching the current update set."""

    update_set_id: str = Field(..., description="Update set sys_id or name")
    return_previous_current: bool = Field(
        True, description="Include the previously current update sets in the response"
    )


class CommitUpdateSetParams(BaseModel):
    """Parameters for committing an update set."""

    update_set_id: str = Field(..., description="Update set sys_id or name")
    commit_message: Optional[str] = Field(None, description="Optional commit note")


class PreviewUpdateSetParams(BaseModel):
    """Parameters for previewing an update set promotion state."""

    update_set_id: str = Field(..., description="Update set or remote update set sys_id or name")
    prefer_remote: bool = Field(
        False, description="Prefer resolving against remote update sets first"
    )
    include_changes: bool = Field(True, description="Include captured update XML records")
    include_problems: bool = Field(True, description="Include preview problems if present")
    limit: int = Field(100, description="Maximum related records to return per related list")


class ValidateUpdateSetParams(BaseModel):
    """Parameters for validating an update set for promotion."""

    update_set_id: str = Field(..., description="Update set or remote update set sys_id or name")
    prefer_remote: bool = Field(
        False, description="Prefer resolving against remote update sets first"
    )
    mode: str = Field(
        "promotion",
        description="Validation mode: source, target, or promotion",
    )
    require_complete: bool = Field(
        True, description="Require the source update set to be complete before promotion"
    )
    require_changes: bool = Field(True, description="Require at least one captured change")
    require_clean_preview: bool = Field(
        True, description="Require zero preview problems for remote update sets"
    )
    require_not_current: bool = Field(
        False, description="Require the update set to no longer be marked current"
    )
    limit: int = Field(100, description="Maximum related records to inspect")


class PromoteUpdateSetParams(BaseModel):
    """Parameters for orchestrating an update set promotion workflow."""

    update_set_id: str = Field(..., description="Update set or remote update set sys_id or name")
    target_environment: Optional[str] = Field(
        None, description="Friendly name of the target environment"
    )
    auto_commit: bool = Field(
        False, description="Commit a source update set automatically before validation"
    )
    prefer_remote: bool = Field(
        False, description="Prefer resolving against remote update sets first"
    )
    include_preview: bool = Field(
        True, description="Include preview details in the promotion workflow result"
    )


def _unwrap_and_validate_params(
    params: Union[Dict[str, Any], BaseModel],
    model_class: Type[T],
    required_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Unwrap and validate parameters.

    Args:
        params: The parameters to unwrap and validate. Can be a dictionary or a Pydantic model.
        model_class: The Pydantic model class to validate against.
        required_fields: List of fields that must be present.

    Returns:
        A dictionary with success status and validated parameters or error message.
    """
    try:
        # Handle case where params is already a Pydantic model
        if isinstance(params, BaseModel):
            # If it's already the correct model class, use it directly
            if isinstance(params, model_class):
                model_instance = params
            # Otherwise, convert to dict and create new instance
            else:
                model_instance = model_class(**params.dict())
        # Handle dictionary case
        else:
            # Create model instance
            model_instance = model_class(**params)

        # Check required fields
        if required_fields:
            missing_fields = []
            for field in required_fields:
                if getattr(model_instance, field, None) is None:
                    missing_fields.append(field)

            if missing_fields:
                return {
                    "success": False,
                    "message": f"Missing required fields: {', '.join(missing_fields)}",
                }

        return {
            "success": True,
            "params": model_instance,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Invalid parameters: {str(e)}",
        }


def _get_instance_url(auth_manager: AuthManager, server_config: ServerConfig) -> Optional[str]:
    """
    Get the instance URL from either auth_manager or server_config.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.

    Returns:
        The instance URL or None if not found.
    """
    # Try to get instance_url from server_config
    if hasattr(server_config, "instance_url"):
        return server_config.instance_url

    # Try to get instance_url from auth_manager
    if hasattr(auth_manager, "instance_url"):
        return auth_manager.instance_url

    # If neither has instance_url, check if auth_manager is actually a ServerConfig
    # and server_config is actually an AuthManager (parameters swapped)
    if hasattr(server_config, "get_headers") and not hasattr(auth_manager, "get_headers"):
        if hasattr(auth_manager, "instance_url"):
            return auth_manager.instance_url

    logger.error("Cannot find instance_url in either auth_manager or server_config")
    return None


def _get_headers(
    auth_manager: AuthManager, server_config: ServerConfig
) -> Optional[Dict[str, str]]:
    """
    Get the headers from either auth_manager or server_config.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.

    Returns:
        The headers or None if not found.
    """
    # Try to get headers from auth_manager
    if hasattr(auth_manager, "get_headers"):
        return auth_manager.get_headers()

    # Try to get headers from server_config
    if hasattr(server_config, "get_headers"):
        return server_config.get_headers()

    # If neither has get_headers, check if auth_manager is actually a ServerConfig
    # and server_config is actually an AuthManager (parameters swapped)
    if hasattr(server_config, "get_headers") and not hasattr(auth_manager, "get_headers"):
        return server_config.get_headers()

    logger.error("Cannot find get_headers method in either auth_manager or server_config")
    return None


def _request_timeout(server_config: ServerConfig) -> int:
    return getattr(server_config, "timeout", 60)


def _display_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _raw_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _table_url(instance_url: str, table: str, sys_id: Optional[str] = None) -> str:
    base_url = f"{instance_url}/api/now/table/{table}"
    return f"{base_url}/{sys_id}" if sys_id else base_url


def _request_json(
    method: str,
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    table: str,
    sys_id: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    request_headers = dict(headers)
    request_headers.setdefault("Accept", "application/json")
    if body is not None:
        request_headers["Content-Type"] = "application/json"

    response = requests.request(
        method=method,
        url=_table_url(instance_url, table, sys_id),
        headers=request_headers,
        params=params,
        json=body,
        timeout=_request_timeout(server_config),
    )
    response.raise_for_status()
    return response.json()


def _safe_table_query(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    table: str,
    query: str,
    limit: int = 100,
    fields: Optional[str] = None,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "sysparm_query": query,
        "sysparm_limit": limit,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    if fields:
        params["sysparm_fields"] = fields
    response_json = _request_json("GET", instance_url, headers, server_config, table, params=params)
    result = response_json.get("result", [])
    return result if isinstance(result, list) else []


def _get_table_record(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    table: str,
    sys_id: str,
    fields: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }
    if fields:
        params["sysparm_fields"] = fields
    try:
        response_json = _request_json(
            "GET",
            instance_url,
            headers,
            server_config,
            table,
            sys_id=sys_id,
            params=params,
        )
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    result = response_json.get("result", {})
    return result if isinstance(result, dict) and result else None


def _find_table_record(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    table: str,
    identifier: str,
    name_fields: List[str],
    fields: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    cleaned_identifier = str(identifier).strip()
    if not cleaned_identifier:
        return None

    if SYS_ID_PATTERN.fullmatch(cleaned_identifier):
        return _get_table_record(
            instance_url,
            headers,
            server_config,
            table,
            cleaned_identifier,
            fields=fields,
        )

    query = "^OR".join(f"{field}={cleaned_identifier}" for field in name_fields)
    records = _safe_table_query(
        instance_url,
        headers,
        server_config,
        table,
        query,
        limit=20,
        fields=fields,
    )
    if not records:
        return None

    normalized_identifier = cleaned_identifier.casefold()
    for record in records:
        for field in name_fields:
            if (
                str(_display_value(record.get(field)) or "").strip().casefold()
                == normalized_identifier
            ):
                return record
    return records[0]


def _resolve_update_set_record(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    identifier: str,
    prefer_remote: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    search_order: List[Tuple[str, str, str]] = [
        (REMOTE_UPDATE_SET_TABLE, REMOTE_UPDATE_SET_FIELDS, "remote"),
        (UPDATE_SET_TABLE, UPDATE_SET_FIELDS, "local"),
    ]
    if not prefer_remote:
        search_order.reverse()

    for table, fields, scope in search_order:
        record = _find_table_record(
            instance_url,
            headers,
            server_config,
            table,
            identifier,
            ["name"],
            fields=fields,
        )
        if record:
            return scope, record

    raise ValueError(f"Update set not found: {identifier}")


def _resolve_application_scope_sys_id(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    identifier: str,
) -> str:
    cleaned_identifier = str(identifier).strip()
    if not cleaned_identifier:
        raise ValueError("Application scope cannot be empty")

    record = _find_table_record(
        instance_url,
        headers,
        server_config,
        SYS_SCOPE_TABLE,
        cleaned_identifier,
        ["scope", "name"],
        fields=SYS_SCOPE_FIELDS,
    )
    if not record:
        raise ValueError(
            f"Application scope not found: {identifier}. "
            "Pass the sys_scope sys_id or exact scope value, for example x_acme_app."
        )

    sys_id = _raw_value(record.get("sys_id"))
    if not sys_id:
        raise ValueError(f"Resolved application scope has no sys_id: {identifier}")
    return str(sys_id)


def _normalize_update_set(scope: str, record: Dict[str, Any]) -> Dict[str, Any]:
    application = record.get("application")
    parent = record.get("parent") or record.get("base_update_set")
    linked_update_set = record.get("update_set")
    return {
        "scope": scope,
        "sys_id": _raw_value(record.get("sys_id")),
        "name": _display_value(record.get("name")),
        "description": _display_value(record.get("description")),
        "state": _display_value(record.get("state")),
        "current": _bool_value(record.get("current")),
        "application": (
            {
                "value": _raw_value(application),
                "display_value": _display_value(application),
            }
            if application not in (None, "")
            else None
        ),
        "parent": (
            {"value": _raw_value(parent), "display_value": _display_value(parent)}
            if parent not in (None, "")
            else None
        ),
        "linked_update_set": (
            {
                "value": _raw_value(linked_update_set),
                "display_value": _display_value(linked_update_set),
            }
            if linked_update_set not in (None, "")
            else None
        ),
        "created_on": _display_value(record.get("sys_created_on")),
        "updated_on": _display_value(record.get("sys_updated_on")),
        "created_by": _display_value(record.get("sys_created_by")),
        "updated_by": _display_value(record.get("sys_updated_by")),
    }


def _normalize_change_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sys_id": _raw_value(record.get("sys_id")),
        "name": _display_value(record.get("name")),
        "action": _display_value(record.get("action")),
        "type": _display_value(record.get("type")),
        "target_name": _display_value(record.get("target_name")),
        "update_name": _display_value(record.get("update_name")),
        "created_on": _display_value(record.get("sys_created_on")),
    }


def _normalize_preview_problem(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sys_id": _raw_value(record.get("sys_id")),
        "name": _display_value(record.get("name")),
        "type": _display_value(record.get("type")),
        "status": _display_value(record.get("status") or record.get("state")),
        "description": _display_value(
            record.get("description") or record.get("problem_description") or record.get("message")
        ),
        "created_on": _display_value(record.get("sys_created_on")),
    }


def _list_update_xml_records(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    update_set_sys_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    return _safe_table_query(
        instance_url,
        headers,
        server_config,
        UPDATE_XML_TABLE,
        f"update_set={update_set_sys_id}",
        limit=limit,
    )


def _list_preview_problem_records(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    update_set_sys_id: str,
    limit: int,
) -> List[Dict[str, Any]]:
    return _safe_table_query(
        instance_url,
        headers,
        server_config,
        PREVIEW_PROBLEM_TABLE,
        f"remote_update_set={update_set_sys_id}^ORupdate_set={update_set_sys_id}",
        limit=limit,
    )


def _sync_update_set_user_preferences(
    instance_url: str,
    headers: Dict[str, str],
    server_config: ServerConfig,
    new_update_set_sys_id: str,
    previous_update_set_sys_ids: List[str],
) -> Dict[str, Any]:
    previous_ids = [sys_id for sys_id in previous_update_set_sys_ids if sys_id]
    if not previous_ids:
        return {
            "updated_preferences": [],
            "verified_preferences": [],
            "message": (
                "No previous current update set ids were available for user preference sync."
            ),
        }

    preference_records = _safe_table_query(
        instance_url,
        headers,
        server_config,
        USER_PREFERENCE_TABLE,
        f"valueIN{','.join(previous_ids)}",
        limit=50,
    )

    candidate_preferences = [
        record for record in preference_records if not _bool_value(record.get("system"))
    ]

    updated_preferences: List[Dict[str, Any]] = []
    for preference in candidate_preferences:
        preference_sys_id = _raw_value(preference.get("sys_id"))
        if not preference_sys_id:
            continue
        response_json = _request_json(
            "PATCH",
            instance_url,
            headers,
            server_config,
            USER_PREFERENCE_TABLE,
            sys_id=str(preference_sys_id),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            body={"value": new_update_set_sys_id},
        )
        result = response_json.get("result", {})
        if isinstance(result, dict):
            updated_preferences.append(result)

    verified_preferences = [
        record
        for record in updated_preferences
        if str(_raw_value(record.get("value")) or "") == new_update_set_sys_id
    ]

    message = (
        f"Updated {len(verified_preferences)} user preference record(s) to the new update set."
        if verified_preferences
        else "No matching user preference records were updated."
    )

    return {
        "updated_preferences": updated_preferences,
        "verified_preferences": verified_preferences,
        "message": message,
    }


def list_changesets(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], ListChangesetsParams],
) -> Dict[str, Any]:
    """
    List changesets from ServiceNow.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for listing changesets. Can be a dictionary or a ListChangesetsParams
            object.

    Returns:
        A list of changesets.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(params, ListChangesetsParams)

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Build query parameters
    query_params = {
        "sysparm_limit": validated_params.limit,
        "sysparm_offset": validated_params.offset,
    }

    # Build sysparm_query
    query_parts = []

    if validated_params.state:
        query_parts.append(f"state={validated_params.state}")

    if validated_params.application:
        try:
            application_sys_id = _resolve_application_scope_sys_id(
                instance_url,
                headers,
                server_config,
                validated_params.application,
            )
            query_parts.append(f"application={application_sys_id}")
        except ValueError:
            query_parts.append(f"application={validated_params.application}")

    if validated_params.developer:
        query_parts.append(f"developer={validated_params.developer}")

    if validated_params.timeframe:
        if validated_params.timeframe == "recent":
            query_parts.append(
                "sys_created_onONLast 7 days@javascript:gs.beginningOfLast7Days()@javascript:gs."
                "endOfToday()"
            )
        elif validated_params.timeframe == "last_week":
            query_parts.append(
                "sys_created_onONLast week@javascript:gs.beginningOfLastWeek()@javascript:gs."
                "endOfLastWeek()"
            )
        elif validated_params.timeframe == "last_month":
            query_parts.append(
                "sys_created_onONLast month@javascript:gs.beginningOfLastMonth()@javascript:gs."
                "endOfLastMonth()"
            )

    if validated_params.query:
        query_parts.append(validated_params.query)

    if query_parts:
        query_params["sysparm_query"] = "^".join(query_parts)

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_set"

    try:
        response = requests.get(url, params=query_params, headers=headers)
        response.raise_for_status()

        result = response.json()

        return {
            "success": True,
            "changesets": result.get("result", []),
            "count": len(result.get("result", [])),
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error listing changesets: {e}")
        return {
            "success": False,
            "message": f"Error listing changesets: {str(e)}",
        }


def get_changeset_details(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], GetChangesetDetailsParams],
) -> Dict[str, Any]:
    """
    Get detailed information about a specific changeset.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for getting changeset details. Can be a dictionary or a
            GetChangesetDetailsParams object.

    Returns:
        Detailed information about the changeset.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(
        params, GetChangesetDetailsParams, required_fields=["changeset_id"]
    )

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_set/{validated_params.changeset_id}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        result = response.json()

        # Get the changeset details
        changeset = result.get("result", {})

        # Get the changes in this changeset
        changes_url = f"{instance_url}/api/now/table/sys_update_xml"
        changes_params = {
            "sysparm_query": f"update_set={validated_params.changeset_id}",
        }

        changes_response = requests.get(changes_url, params=changes_params, headers=headers)
        changes_response.raise_for_status()

        changes_result = changes_response.json()
        changes = changes_result.get("result", [])

        return {
            "success": True,
            "changeset": changeset,
            "changes": changes,
            "change_count": len(changes),
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting changeset details: {e}")
        return {
            "success": False,
            "message": f"Error getting changeset details: {str(e)}",
        }


def create_changeset(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], CreateChangesetParams],
) -> Dict[str, Any]:
    """
    Create a new changeset in ServiceNow.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for creating a changeset. Can be a dictionary or a
            CreateChangesetParams object.

    Returns:
        The created changeset.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(
        params, CreateChangesetParams, required_fields=["name", "application"]
    )

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Prepare the request data
    data = {
        "name": validated_params.name,
    }

    # Add optional fields if provided
    if validated_params.description:
        data["description"] = validated_params.description
    if validated_params.developer:
        data["developer"] = validated_params.developer

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Add Content-Type header
    headers["Content-Type"] = "application/json"
    data["application"] = _resolve_application_scope_sys_id(
        instance_url,
        headers,
        server_config,
        validated_params.application,
    )

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_set"

    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()

        result = response.json()

        return {
            "success": True,
            "message": "Changeset created successfully",
            "changeset": result["result"],
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error creating changeset: {e}")
        return {
            "success": False,
            "message": f"Error creating changeset: {str(e)}",
        }


def update_changeset(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], UpdateChangesetParams],
) -> Dict[str, Any]:
    """
    Update an existing changeset in ServiceNow.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for updating a changeset. Can be a dictionary or a
            UpdateChangesetParams object.

    Returns:
        The updated changeset.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(
        params, UpdateChangesetParams, required_fields=["changeset_id"]
    )

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Prepare the request data
    data = {}

    # Add optional fields if provided
    if validated_params.name:
        data["name"] = validated_params.name
    if validated_params.description:
        data["description"] = validated_params.description
    if validated_params.state:
        data["state"] = validated_params.state
    if validated_params.developer:
        data["developer"] = validated_params.developer

    # If no fields to update, return error
    if not data:
        return {
            "success": False,
            "message": "No fields to update",
        }

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Add Content-Type header
    headers["Content-Type"] = "application/json"

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_set/{validated_params.changeset_id}"

    try:
        response = requests.patch(url, json=data, headers=headers)
        response.raise_for_status()

        result = response.json()

        return {
            "success": True,
            "message": "Changeset updated successfully",
            "changeset": result["result"],
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error updating changeset: {e}")
        return {
            "success": False,
            "message": f"Error updating changeset: {str(e)}",
        }


def commit_changeset(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], CommitChangesetParams],
) -> Dict[str, Any]:
    """
    Commit a changeset in ServiceNow.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for committing a changeset. Can be a dictionary or a
            CommitChangesetParams object.

    Returns:
        The committed changeset.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(
        params, CommitChangesetParams, required_fields=["changeset_id"]
    )

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Prepare the request data
    data = {
        "state": "complete",
    }

    # Add commit message if provided
    if validated_params.commit_message:
        data["description"] = validated_params.commit_message

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Add Content-Type header
    headers["Content-Type"] = "application/json"

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_set/{validated_params.changeset_id}"

    try:
        response = requests.patch(url, json=data, headers=headers)
        response.raise_for_status()

        result = response.json()

        return {
            "success": True,
            "message": "Changeset committed successfully",
            "changeset": result["result"],
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error committing changeset: {e}")
        return {
            "success": False,
            "message": f"Error committing changeset: {str(e)}",
        }


def publish_changeset(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], PublishChangesetParams],
) -> Dict[str, Any]:
    """
    Publish a changeset in ServiceNow.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for publishing a changeset. Can be a dictionary or a
            PublishChangesetParams object.

    Returns:
        The published changeset.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(
        params, PublishChangesetParams, required_fields=["changeset_id"]
    )

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Add Content-Type header
    headers["Content-Type"] = "application/json"

    # Prepare the request data for the publish action
    data = {
        "state": "published",
    }

    # Add publish notes if provided
    if validated_params.publish_notes:
        data["description"] = validated_params.publish_notes

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_set/{validated_params.changeset_id}"

    try:
        response = requests.patch(url, json=data, headers=headers)
        response.raise_for_status()

        result = response.json()

        return {
            "success": True,
            "message": "Changeset published successfully",
            "changeset": result["result"],
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error publishing changeset: {e}")
        return {
            "success": False,
            "message": f"Error publishing changeset: {str(e)}",
        }


def add_file_to_changeset(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], AddFileToChangesetParams],
) -> Dict[str, Any]:
    """
    Add a file to a changeset in ServiceNow.

    Args:
        auth_manager: The authentication manager.
        server_config: The server configuration.
        params: The parameters for adding a file to a changeset. Can be a dictionary or a
            AddFileToChangesetParams object.

    Returns:
        The result of the add file operation.
    """
    # Unwrap and validate parameters
    result = _unwrap_and_validate_params(
        params,
        AddFileToChangesetParams,
        required_fields=["changeset_id", "file_path", "file_content"],
    )

    if not result["success"]:
        return result

    validated_params = result["params"]

    # Get the instance URL
    instance_url = _get_instance_url(auth_manager, server_config)
    if not instance_url:
        return {
            "success": False,
            "message": "Cannot find instance_url in either server_config or auth_manager",
        }

    # Get the headers
    headers = _get_headers(auth_manager, server_config)
    if not headers:
        return {
            "success": False,
            "message": "Cannot find get_headers method in either auth_manager or server_config",
        }

    # Add Content-Type header
    headers["Content-Type"] = "application/json"

    # Prepare the request data for adding a file
    data = {
        "update_set": validated_params.changeset_id,
        "name": validated_params.file_path,
        "payload": validated_params.file_content,
        "type": "file",
    }

    # Make the API request
    url = f"{instance_url}/api/now/table/sys_update_xml"

    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()

        result = response.json()

        return {
            "success": True,
            "message": "File added to changeset successfully",
            "file": result["result"],
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error adding file to changeset: {e}")
        return {
            "success": False,
            "message": f"Error adding file to changeset: {str(e)}",
        }


def list_update_sets(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], ListUpdateSetsParams],
) -> Dict[str, Any]:
    """List update sets using update-set friendly naming."""
    result = list_changesets(auth_manager, server_config, params)
    if not result.get("success"):
        return result
    return {
        "success": True,
        "message": f"Retrieved {result.get('count', 0)} update sets",
        "update_sets": result.get("changesets", []),
        "count": result.get("count", 0),
    }


def get_update_set_details(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], GetUpdateSetDetailsParams],
) -> Dict[str, Any]:
    """Get update set details using update-set friendly naming."""
    result = _unwrap_and_validate_params(
        params, GetUpdateSetDetailsParams, required_fields=["update_set_id"]
    )
    if not result["success"]:
        return result
    validated_params = result["params"]
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = _get_headers(auth_manager, server_config)
    if not instance_url or not headers:
        return {
            "success": False,
            "message": "Cannot resolve ServiceNow connection details",
        }

    try:
        scope, record = _resolve_update_set_record(
            instance_url,
            headers,
            server_config,
            validated_params.update_set_id,
        )
        normalized_update_set = _normalize_update_set(scope, record)
        change_count = 0
        changes: List[Dict[str, Any]] = []
        if scope == "local":
            raw_changes = _list_update_xml_records(
                instance_url,
                headers,
                server_config,
                str(normalized_update_set["sys_id"]),
                100,
            )
            changes = [_normalize_change_record(item) for item in raw_changes]
            change_count = len(changes)

        return {
            "success": True,
            "message": "Retrieved update set details",
            "update_set": normalized_update_set,
            "changes": changes,
            "change_count": change_count,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error getting update set details: %s", exc)
        return {
            "success": False,
            "message": f"Error getting update set details: {str(exc)}",
        }


def create_update_set(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], CreateUpdateSetParams],
) -> Dict[str, Any]:
    """Create a new update set."""
    result = _unwrap_and_validate_params(params, CreateUpdateSetParams, required_fields=["name"])
    if not result["success"]:
        return result

    validated_params = result["params"]
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = _get_headers(auth_manager, server_config)
    if not instance_url or not headers:
        return {
            "success": False,
            "message": "Cannot resolve ServiceNow connection details",
        }

    body: Dict[str, Any] = {
        "name": validated_params.name,
    }
    if validated_params.description:
        body["description"] = validated_params.description
    if validated_params.application:
        body["application"] = _resolve_application_scope_sys_id(
            instance_url,
            headers,
            server_config,
            validated_params.application,
        )
    if validated_params.developer:
        body["developer"] = validated_params.developer
    if validated_params.state:
        body["state"] = validated_params.state
    if validated_params.parent_update_set_id:
        body["parent"] = validated_params.parent_update_set_id

    try:
        response_json = _request_json(
            "POST",
            instance_url,
            headers,
            server_config,
            UPDATE_SET_TABLE,
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": UPDATE_SET_FIELDS,
            },
            body=body,
        )
        record = response_json.get("result", {})
        normalized_update_set = _normalize_update_set("local", record)

        if validated_params.make_current:
            created_update_set_id = normalized_update_set.get("sys_id")
            if not created_update_set_id:
                raise ValueError("Created update set does not have a sys_id")

            switch_result = switch_current_update_set(
                auth_manager,
                server_config,
                SwitchCurrentUpdateSetParams(update_set_id=str(created_update_set_id)),
            )
            if not switch_result.get("success"):
                return {
                    "success": False,
                    "message": (
                        f"Update set was created as {record.get('name') or validated_params.name}, "
                        f"but could not be made current: {switch_result.get('message')}"
                    ),
                    "update_set": normalized_update_set,
                }

            return {
                "success": True,
                "message": (
                    f"Created update set {record.get('name') or validated_params.name} "
                    "and made it the current update set"
                ),
                "update_set": switch_result.get("update_set", normalized_update_set),
                "previous_current_update_sets": switch_result.get(
                    "previous_current_update_sets",
                    [],
                ),
            }

        return {
            "success": True,
            "message": f"Created update set {record.get('name') or validated_params.name}",
            "update_set": normalized_update_set,
        }
    except requests.exceptions.RequestException as exc:
        logger.error("Error creating update set: %s", exc)
        return {"success": False, "message": f"Error creating update set: {str(exc)}"}


def switch_current_update_set(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], SwitchCurrentUpdateSetParams],
) -> Dict[str, Any]:
    """Switch the current update set for the calling user."""
    result = _unwrap_and_validate_params(
        params, SwitchCurrentUpdateSetParams, required_fields=["update_set_id"]
    )
    if not result["success"]:
        return result

    validated_params = result["params"]
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = _get_headers(auth_manager, server_config)
    if not instance_url or not headers:
        return {
            "success": False,
            "message": "Cannot resolve ServiceNow connection details",
        }

    try:
        previous_current_sets: List[Dict[str, Any]] = []
        previous_current_ids: List[str] = []
        if validated_params.return_previous_current:
            previous_current_sets = _safe_table_query(
                instance_url,
                headers,
                server_config,
                UPDATE_SET_TABLE,
                "current=true",
                limit=10,
                fields=UPDATE_SET_FIELDS,
            )
            previous_current_ids = [
                str(_raw_value(current_record.get("sys_id")))
                for current_record in previous_current_sets
                if _raw_value(current_record.get("sys_id"))
            ]

        _scope, record = _resolve_update_set_record(
            instance_url,
            headers,
            server_config,
            validated_params.update_set_id,
            prefer_remote=False,
        )
        update_set_sys_id = _raw_value(record.get("sys_id"))
        if not update_set_sys_id:
            raise ValueError("Resolved update set does not have a sys_id")

        response_json = _request_json(
            "PATCH",
            instance_url,
            headers,
            server_config,
            UPDATE_SET_TABLE,
            sys_id=str(update_set_sys_id),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": UPDATE_SET_FIELDS,
            },
            body={"current": "true"},
        )
        updated_record = response_json.get("result", {})
        preference_sync = _sync_update_set_user_preferences(
            instance_url,
            headers,
            server_config,
            str(update_set_sys_id),
            previous_current_ids,
        )
        message = (
            f"Switched current update set to {updated_record.get('name') or update_set_sys_id}"
        )
        if preference_sync.get("verified_preferences"):
            message = f"{message} and synced the user preference used by the UI picker"
        return {
            "success": True,
            "message": message,
            "update_set": _normalize_update_set("local", updated_record),
            "previous_current_update_sets": [
                _normalize_update_set("local", current_record)
                for current_record in previous_current_sets
                if _raw_value(current_record.get("sys_id")) != str(update_set_sys_id)
            ],
            "user_preference_sync": preference_sync,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error switching current update set: %s", exc)
        return {
            "success": False,
            "message": f"Error switching current update set: {str(exc)}",
        }


def commit_update_set(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], CommitUpdateSetParams],
) -> Dict[str, Any]:
    """Commit an update set using update-set friendly naming."""
    result = _unwrap_and_validate_params(
        params, CommitUpdateSetParams, required_fields=["update_set_id"]
    )
    if not result["success"]:
        return result
    validated_params = result["params"]
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = _get_headers(auth_manager, server_config)
    if not instance_url or not headers:
        return {
            "success": False,
            "message": "Cannot resolve ServiceNow connection details",
        }

    try:
        _scope, record = _resolve_update_set_record(
            instance_url,
            headers,
            server_config,
            validated_params.update_set_id,
            prefer_remote=False,
        )
        update_set_sys_id = _raw_value(record.get("sys_id"))
        if not update_set_sys_id:
            raise ValueError("Resolved update set does not have a sys_id")

        body: Dict[str, Any] = {"state": "complete"}
        if validated_params.commit_message:
            body["description"] = validated_params.commit_message

        response_json = _request_json(
            "PATCH",
            instance_url,
            headers,
            server_config,
            UPDATE_SET_TABLE,
            sys_id=str(update_set_sys_id),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
                "sysparm_fields": UPDATE_SET_FIELDS,
            },
            body=body,
        )
        updated_record = response_json.get("result", {})
        return {
            "success": True,
            "message": "Update set committed successfully",
            "update_set": _normalize_update_set("local", updated_record),
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error committing update set: %s", exc)
        return {"success": False, "message": f"Error committing update set: {str(exc)}"}


def preview_update_set(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], PreviewUpdateSetParams],
) -> Dict[str, Any]:
    """Preview update set promotion details and related records."""
    result = _unwrap_and_validate_params(
        params, PreviewUpdateSetParams, required_fields=["update_set_id"]
    )
    if not result["success"]:
        return result

    validated_params = result["params"]
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = _get_headers(auth_manager, server_config)
    if not instance_url or not headers:
        return {
            "success": False,
            "message": "Cannot resolve ServiceNow connection details",
        }

    try:
        scope, record = _resolve_update_set_record(
            instance_url,
            headers,
            server_config,
            validated_params.update_set_id,
            prefer_remote=validated_params.prefer_remote,
        )
        normalized_update_set = _normalize_update_set(scope, record)
        update_set_sys_id = normalized_update_set["sys_id"]
        linked_update_set = normalized_update_set.get("linked_update_set") or {}
        linked_update_set_id = linked_update_set.get("value")

        warnings: List[str] = []
        change_records: List[Dict[str, Any]] = []
        preview_problem_records: List[Dict[str, Any]] = []

        if validated_params.include_changes:
            if scope == "local":
                raw_changes = _list_update_xml_records(
                    instance_url,
                    headers,
                    server_config,
                    str(update_set_sys_id),
                    validated_params.limit,
                )
                change_records = [_normalize_change_record(item) for item in raw_changes]
                warnings.append(
                    "This is a source update set. Platform preview happens after the update set "
                    "is retrieved into a target instance."
                )
            elif linked_update_set_id:
                raw_changes = _list_update_xml_records(
                    instance_url,
                    headers,
                    server_config,
                    str(linked_update_set_id),
                    validated_params.limit,
                )
                change_records = [_normalize_change_record(item) for item in raw_changes]
            else:
                warnings.append(
                    "Remote update set is not linked to a local update set record, so captured "
                    "change details could not be expanded."
                )

        if validated_params.include_problems:
            try:
                raw_problems = _list_preview_problem_records(
                    instance_url,
                    headers,
                    server_config,
                    str(update_set_sys_id),
                    validated_params.limit,
                )
                preview_problem_records = [
                    _normalize_preview_problem(item) for item in raw_problems
                ]
            except requests.exceptions.RequestException as exc:
                warnings.append(f"Unable to load preview problems: {str(exc)}")

        preview_status = "source_only" if scope == "local" else "remote"
        if scope == "remote" and not preview_problem_records:
            preview_status = "clean"
        elif scope == "remote" and preview_problem_records:
            preview_status = "problems_found"

        return {
            "success": True,
            "message": "Retrieved update set preview details",
            "preview_status": preview_status,
            "update_set": normalized_update_set,
            "changes": change_records,
            "change_count": len(change_records),
            "preview_problems": preview_problem_records,
            "preview_problem_count": len(preview_problem_records),
            "warnings": warnings,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error previewing update set: %s", exc)
        return {"success": False, "message": f"Error previewing update set: {str(exc)}"}


def validate_update_set(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], ValidateUpdateSetParams],
) -> Dict[str, Any]:
    """Validate whether an update set is ready for promotion."""
    result = _unwrap_and_validate_params(
        params, ValidateUpdateSetParams, required_fields=["update_set_id"]
    )
    if not result["success"]:
        return result

    validated_params = result["params"]
    instance_url = _get_instance_url(auth_manager, server_config)
    headers = _get_headers(auth_manager, server_config)
    if not instance_url or not headers:
        return {
            "success": False,
            "message": "Cannot resolve ServiceNow connection details",
        }

    try:
        scope, record = _resolve_update_set_record(
            instance_url,
            headers,
            server_config,
            validated_params.update_set_id,
            prefer_remote=validated_params.prefer_remote,
        )
        normalized_update_set = _normalize_update_set(scope, record)
        update_set_sys_id = str(normalized_update_set["sys_id"])

        errors: List[str] = []
        warnings: List[str] = []
        checks: List[Dict[str, Any]] = []

        change_records = _list_update_xml_records(
            instance_url,
            headers,
            server_config,
            update_set_sys_id,
            validated_params.limit,
        )
        change_count = len(change_records)

        state = str(normalized_update_set.get("state") or "").strip().lower()
        current = bool(normalized_update_set.get("current"))

        if scope == "local":
            if validated_params.require_complete and state not in {"complete", "committed"}:
                errors.append(
                    f"Source update set state is '{normalized_update_set.get('state')}', expected "
                    f"complete or committed."
                )
            if validated_params.require_changes and change_count == 0:
                errors.append("Source update set has no captured customer updates.")
            if validated_params.require_not_current and current:
                errors.append("Update set is still marked current.")
            elif current:
                warnings.append("Update set is still marked current.")
        else:
            preview_problem_records = _list_preview_problem_records(
                instance_url,
                headers,
                server_config,
                update_set_sys_id,
                validated_params.limit,
            )
            preview_problem_count = len(preview_problem_records)
            if validated_params.require_clean_preview and preview_problem_count > 0:
                errors.append(
                    f"Remote update set has {preview_problem_count} preview problem(s) to resolve."
                )
            if state not in {"previewed", "committed", "complete"}:
                warnings.append(
                    f"Remote update set state is '{normalized_update_set.get('state')}'. Preview/"
                    f"commit readiness may still need review."
                )
            checks.append(
                {
                    "name": "preview_problems",
                    "status": "pass" if preview_problem_count == 0 else "fail",
                    "details": f"{preview_problem_count} preview problems found",
                }
            )

        checks.extend(
            [
                {
                    "name": "state",
                    "status": (
                        "pass"
                        if scope == "remote" or state in {"complete", "committed"}
                        else "fail"
                    ),
                    "details": normalized_update_set.get("state"),
                },
                {
                    "name": "captured_changes",
                    "status": "pass" if change_count > 0 else "fail",
                    "details": f"{change_count} update XML records",
                },
                {
                    "name": "current_flag",
                    "status": "warn" if current else "pass",
                    "details": "current=true" if current else "current=false",
                },
            ]
        )

        ready = len(errors) == 0
        return {
            "success": True,
            "message": "Update set validation complete",
            "ready": ready,
            "mode": validated_params.mode,
            "update_set": normalized_update_set,
            "change_count": change_count,
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
        }
    except (ValueError, requests.exceptions.RequestException) as exc:
        logger.error("Error validating update set: %s", exc)
        return {"success": False, "message": f"Error validating update set: {str(exc)}"}


def promote_update_set(
    auth_manager: AuthManager,
    server_config: ServerConfig,
    params: Union[Dict[str, Any], PromoteUpdateSetParams],
) -> Dict[str, Any]:
    """Prepare and validate an update set promotion workflow."""
    result = _unwrap_and_validate_params(
        params, PromoteUpdateSetParams, required_fields=["update_set_id"]
    )
    if not result["success"]:
        return result

    validated_params = result["params"]
    auto_commit_result: Optional[Dict[str, Any]] = None

    if validated_params.auto_commit:
        auto_commit_result = commit_update_set(
            auth_manager,
            server_config,
            CommitUpdateSetParams(update_set_id=validated_params.update_set_id),
        )
        if not auto_commit_result.get("success"):
            return {
                "success": False,
                "message": "Promotion workflow failed during automatic commit",
                "auto_commit_result": auto_commit_result,
            }

    validation_result = validate_update_set(
        auth_manager,
        server_config,
        ValidateUpdateSetParams(
            update_set_id=validated_params.update_set_id,
            prefer_remote=validated_params.prefer_remote,
            mode="promotion",
        ),
    )
    if not validation_result.get("success"):
        return validation_result

    preview_result: Optional[Dict[str, Any]] = None
    if validated_params.include_preview:
        preview_result = preview_update_set(
            auth_manager,
            server_config,
            PreviewUpdateSetParams(
                update_set_id=validated_params.update_set_id,
                prefer_remote=validated_params.prefer_remote,
            ),
        )

    update_set = validation_result.get("update_set", {})
    scope = update_set.get("scope")
    ready = bool(validation_result.get("ready"))

    if ready and scope == "local":
        promotion_status = "ready_for_retrieval"
        next_actions = [
            "Retrieve the committed update set in the target instance.",
            "Run preview_update_set against the retrieved remote update set.",
            "Resolve preview problems in the target instance before commit.",
        ]
    elif ready and scope == "remote":
        promotion_status = "ready_for_commit"
        next_actions = [
            "Commit the remote update set in the target instance.",
            "Run post-deployment validation after commit.",
        ]
    else:
        promotion_status = "blocked"
        next_actions = [
            "Resolve the validation errors reported in this result.",
            "Rerun validate_update_set or promote_update_set after fixes.",
        ]

    return {
        "success": ready,
        "message": "Update set promotion workflow prepared",
        "promotion_status": promotion_status,
        "target_environment": validated_params.target_environment,
        "update_set": update_set,
        "auto_commit_result": auto_commit_result,
        "validation": validation_result,
        "preview": preview_result,
        "next_actions": next_actions,
    }
