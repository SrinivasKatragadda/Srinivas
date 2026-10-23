"""Notification, email template, event, and script action tools for ServiceNow MCP.

This module handles the event-driven notification surface in ServiceNow,
including notifications, email templates, event definitions, event queue
records, and script actions. It is the administrative and inspection layer for
message delivery behavior rather than the runtime mail transport itself.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

NOTIFICATION_TABLE = "sysevent_email_action"
EMAIL_TEMPLATE_TABLE = "sysevent_email_template"
EVENT_DEFINITION_TABLE = "sysevent_register"
EVENT_QUEUE_TABLE = "sysevent"
SCRIPT_ACTION_TABLE = "sysevent_script_action"
SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class ListNotificationsParams(BaseModel):
    """Parameters for List Notifications tool calls."""
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    active: Optional[bool] = Field(None, description="Active filter")
    table: Optional[str] = Field(None, description="Target table, such as incident")
    event_name: Optional[str] = Field(None, description="Triggering event name")
    query: Optional[str] = Field(None, description="Search notification name/subject")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetNotificationParams(BaseModel):
    """Parameters for Get Notification tool calls."""
    notification_id: str = Field(..., description="Notification sys_id, sys_id:<id>, or exact name")
    include_raw: bool = Field(False, description="Include raw record")


class CreateNotificationParams(BaseModel):
    """Parameters for Create Notification tool calls."""
    name: str = Field(..., description="Notification name")
    table: Optional[str] = Field(None, description="Target table stored in collection")
    active: bool = Field(True, description="Whether the notification is active")
    event_name: Optional[str] = Field(None, description="Event name trigger")
    subject: Optional[str] = Field(None, description="Email subject")
    message_html: Optional[str] = Field(
        None,
        description=(
            "HTML body for the notification. This is written to message_html and also "
            "mirrored to the legacy message field for instances that still render from it."
        ),
    )
    message_text: Optional[str] = Field(None, description="Plain text body written to message_text")
    template: Optional[str] = Field(None, description="Email template sys_id or display value")
    recipient_fields: Optional[str] = Field(None, description="Comma-separated recipient fields")
    recipient_users: Optional[str] = Field(None, description="Comma-separated user sys_ids")
    recipient_groups: Optional[str] = Field(None, description="Comma-separated group sys_ids")
    condition: Optional[str] = Field(None, description="Encoded condition")
    advanced_condition: Optional[str] = Field(None, description="Advanced condition script")
    send_when: Optional[str] = Field(None, description="Send when value accepted by instance")
    weight: Optional[str] = Field(None, description="Notification weight/order")
    content_type: Optional[str] = Field(
        None,
        description=(
            "Content type. Defaults to text/html when only message_html is supplied, "
            "multipart/alternative when both message_html and message_text are supplied, "
            "and text/plain when only message_text is supplied."
        ),
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional notification fields")


class UpdateNotificationParams(BaseModel):
    """Parameters for Update Notification tool calls."""
    notification_id: str = Field(..., description="Notification sys_id, sys_id:<id>, or exact name")
    name: Optional[str] = None
    table: Optional[str] = Field(None, description="Target table stored in collection")
    active: Optional[bool] = None
    event_name: Optional[str] = None
    subject: Optional[str] = None
    message_html: Optional[str] = Field(
        None,
        description=(
            "HTML body for the notification. This is written to message_html and also "
            "mirrored to the legacy message field for instances that still render from it."
        ),
    )
    message_text: Optional[str] = Field(None, description="Plain text body written to message_text")
    template: Optional[str] = None
    recipient_fields: Optional[str] = None
    recipient_users: Optional[str] = None
    recipient_groups: Optional[str] = None
    condition: Optional[str] = None
    advanced_condition: Optional[str] = None
    send_when: Optional[str] = None
    weight: Optional[str] = None
    content_type: Optional[str] = Field(
        None,
        description=(
            "Content type. Defaults to text/html when only message_html is supplied, "
            "multipart/alternative when both message_html and message_text are supplied, "
            "and text/plain when only message_text is supplied."
        ),
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional notification fields")


class ListEmailTemplatesParams(BaseModel):
    """Parameters for List Email Templates tool calls."""
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    active: Optional[bool] = Field(None, description="Active filter")
    table: Optional[str] = Field(None, description="Target table stored in collection")
    query: Optional[str] = Field(None, description="Search template name/subject")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetEmailTemplateParams(BaseModel):
    """Parameters for Get Email Template tool calls."""
    template_id: str = Field(..., description="Template sys_id, sys_id:<id>, or exact name")
    include_raw: bool = Field(False, description="Include raw record")


class CreateEmailTemplateParams(BaseModel):
    """Parameters for Create Email Template tool calls."""
    name: str = Field(..., description="Template name")
    table: Optional[str] = Field(None, description="Target table stored in collection")
    active: Optional[bool] = Field(None, description="Whether the template is active")
    subject: Optional[str] = None
    message_html: Optional[str] = Field(
        None,
        description=(
            "HTML body for the template. This is written to message_html and also "
            "mirrored to the legacy message field for instances that still render from it."
        ),
    )
    message_text: Optional[str] = Field(None, description="Plain text body written to message_text")
    email_layout: Optional[str] = Field(None, description="Email layout sys_id or display value")
    content_type: Optional[str] = Field(
        None,
        description=(
            "Content type. Defaults to text/html when only message_html is supplied, "
            "multipart/alternative when both message_html and message_text are supplied, "
            "and text/plain when only message_text is supplied."
        ),
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional template fields")


class UpdateEmailTemplateParams(BaseModel):
    """Parameters for Update Email Template tool calls."""
    template_id: str = Field(..., description="Template sys_id, sys_id:<id>, or exact name")
    name: Optional[str] = None
    table: Optional[str] = Field(None, description="Target table stored in collection")
    active: Optional[bool] = None
    subject: Optional[str] = None
    message_html: Optional[str] = Field(
        None,
        description=(
            "HTML body for the template. This is written to message_html and also "
            "mirrored to the legacy message field for instances that still render from it."
        ),
    )
    message_text: Optional[str] = Field(None, description="Plain text body written to message_text")
    email_layout: Optional[str] = None
    content_type: Optional[str] = Field(
        None,
        description=(
            "Content type. Defaults to text/html when only message_html is supplied, "
            "multipart/alternative when both message_html and message_text are supplied, "
            "and text/plain when only message_text is supplied."
        ),
    )
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional template fields")


class ListEventDefinitionsParams(BaseModel):
    """Parameters for List Event Definitions tool calls."""
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    table: Optional[str] = Field(None, description="Event table")
    query: Optional[str] = Field(None, description="Search event name/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetEventDefinitionParams(BaseModel):
    """Parameters for Get Event Definition tool calls."""
    event_id: str = Field(..., description="Event definition sys_id, sys_id:<id>, or event name")
    include_raw: bool = Field(False, description="Include raw record")


class CreateEventDefinitionParams(BaseModel):
    """Parameters for Create Event Definition tool calls."""
    event_name: str = Field(..., description="Event name, such as incident.created")
    table: Optional[str] = Field(None, description="Table associated with the event")
    description: Optional[str] = None
    fired_by: Optional[str] = Field(None, description="Description of what fires the event")
    queue: Optional[str] = Field(None, description="Event queue")
    suffix: Optional[str] = Field(None, description="Event suffix")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional event registry fields"
    )


class UpdateEventDefinitionParams(BaseModel):
    """Parameters for Update Event Definition tool calls."""
    event_id: str = Field(..., description="Event definition sys_id, sys_id:<id>, or event name")
    event_name: Optional[str] = None
    table: Optional[str] = None
    description: Optional[str] = None
    fired_by: Optional[str] = None
    queue: Optional[str] = None
    suffix: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional event registry fields"
    )


class QueueEventParams(BaseModel):
    """Parameters for Queue Event tool calls."""
    event_name: str = Field(..., description="Event name to queue")
    table: Optional[str] = Field(None, description="Record table for event instance")
    record_id: Optional[str] = Field(None, description="Record sys_id, sys_id:<id>, or number")
    record_number_field: str = Field(
        "number", description="Field used when record_id is not a sys_id"
    )
    parm1: Optional[str] = Field(None, description="Event parm1")
    parm2: Optional[str] = Field(None, description="Event parm2")
    queue: Optional[str] = Field(None, description="Event queue")
    attributes: Optional[Dict[str, Any]] = Field(None, description="Additional sysevent fields")


class ListScriptActionsParams(BaseModel):
    """Parameters for List Script Actions tool calls."""
    limit: int = Field(10, description="Maximum records")
    offset: int = Field(0, description="Pagination offset")
    active: Optional[bool] = Field(None, description="Active filter")
    event_name: Optional[str] = Field(None, description="Event name")
    query: Optional[str] = Field(None, description="Search script action name/description")
    encoded_query: Optional[str] = Field(None, description="Extra encoded query")
    include_raw: bool = Field(False, description="Include raw records")


class GetScriptActionParams(BaseModel):
    """Parameters for Get Script Action tool calls."""
    script_action_id: str = Field(
        ..., description="Script action sys_id, sys_id:<id>, or exact name"
    )
    include_raw: bool = Field(False, description="Include raw record")


class CreateScriptActionParams(BaseModel):
    """Parameters for Create Script Action tool calls."""
    name: str = Field(..., description="Script action name")
    event_name: str = Field(..., description="Event name that triggers the script action")
    script: str = Field(..., description="Script action body")
    active: bool = Field(True, description="Whether the script action is active")
    condition_script: Optional[str] = Field(None, description="Condition script")
    description: Optional[str] = None
    order: Optional[str] = Field(None, description="Execution order")
    event_parm_1: Optional[str] = Field(None, description="Expected event parm1 usage/label")
    event_parm_2: Optional[str] = Field(None, description="Expected event parm2 usage/label")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional script action fields"
    )


class UpdateScriptActionParams(BaseModel):
    """Parameters for Update Script Action tool calls."""
    script_action_id: str = Field(
        ..., description="Script action sys_id, sys_id:<id>, or exact name"
    )
    name: Optional[str] = None
    event_name: Optional[str] = None
    script: Optional[str] = None
    active: Optional[bool] = None
    condition_script: Optional[str] = None
    description: Optional[str] = None
    order: Optional[str] = None
    event_parm_1: Optional[str] = Field(None, description="Expected event parm1 usage/label")
    event_parm_2: Optional[str] = Field(None, description="Expected event parm2 usage/label")
    attributes: Optional[Dict[str, Any]] = Field(
        None, description="Additional script action fields"
    )


class DeleteScriptActionParams(BaseModel):
    """Parameters for Delete Script Action tool calls."""
    script_action_id: str = Field(
        ..., description="Script action sys_id, sys_id:<id>, or exact name"
    )


def _table_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _headers(auth_manager: AuthManager) -> Dict[str, str]:
    headers = auth_manager.get_headers()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    return headers


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _ref(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {
            "value": value.get("value"),
            "display_value": value.get("display_value") or value.get("value"),
        }
    return {"value": value, "display_value": value}


def _read_bool(value: Any) -> Optional[bool]:
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
    display_value = _display(value)
    if display_value is None:
        return False
    return str(display_value).strip().casefold() == identifier.casefold()


def _extract_value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value")
    return value


def _record_sys_id(record: Dict[str, Any]) -> Optional[str]:
    sys_id = _extract_value(record.get("sys_id"))
    if sys_id is None:
        return None
    return str(sys_id).strip() or None


def _extract_sys_id(identifier: str) -> Optional[str]:
    cleaned = _clean_identifier(identifier)
    if cleaned.startswith("sys_id:"):
        cleaned = cleaned.replace("sys_id:", "", 1).strip()
    return cleaned if SYS_ID_PATTERN.fullmatch(cleaned) else None


def _query(parts: Sequence[Optional[str]]) -> str:
    return "^".join(part for part in parts if part)


def _get(
    config: ServerConfig, auth_manager: AuthManager, table: str, sys_id: str
) -> Dict[str, Any]:
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
    match_field: Optional[str] = "name",
) -> Dict[str, Any]:
    cleaned_identifier = _clean_identifier(identifier)
    sys_id = _extract_sys_id(cleaned_identifier)
    if sys_id:
        record = _get(config, auth_manager, table, sys_id)
        if record:
            return record
        raise ValueError(f"{table} record not found: {cleaned_identifier}")
    if not match_field:
        raise ValueError(f"{table} records must be referenced by sys_id")
    exact_query = _query(
        [f"{match_field}={value}" for value in _identifier_variants(cleaned_identifier)]
    )
    records = _list(config, auth_manager, table, exact_query, limit=10)
    exact_matches = [
        record
        for record in records
        if _matches_identifier(record.get(match_field), cleaned_identifier)
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
        config, auth_manager, table, f"{match_field}LIKE{cleaned_identifier}", limit=10
    )
    fuzzy_matches = [
        record
        for record in fuzzy_records
        if _matches_identifier(record.get(match_field), cleaned_identifier)
    ]
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0]
    if len(fuzzy_matches) > 1:
        raise ValueError(f"Multiple {table} records matched {cleaned_identifier}; use sys_id")
    raise ValueError(f"{table} record not found: {cleaned_identifier}")


def _body(params: Any, field_map: Dict[str, str]) -> Dict[str, Any]:
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


def _body_without_attributes(params: Any, field_map: Dict[str, str]) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    for model_field, table_field in field_map.items():
        value = getattr(params, model_field, None)
        if value is None:
            continue
        if isinstance(value, bool):
            body[table_field] = _coerce_bool(value)
        else:
            body[table_field] = value
    return body


def _apply_message_fields(body: Dict[str, Any], params: Any) -> Dict[str, Any]:
    message_html = getattr(params, "message_html", None)
    message_text = getattr(params, "message_text", None)
    content_type = getattr(params, "content_type", None)

    if message_html is not None:
        body["message_html"] = message_html
        # Some older/customized instances still render from `message`.
        body.setdefault("message", message_html)
    if message_text is not None:
        body["message_text"] = message_text
        if message_html is None:
            body.setdefault("message", message_text)

    if content_type is not None:
        body["content_type"] = content_type
    elif message_html is not None and message_text is not None:
        body["content_type"] = "multipart/alternative"
    elif message_html is not None:
        body["content_type"] = "text/html"
    elif message_text is not None:
        body["content_type"] = "text/plain"

    attributes = getattr(params, "attributes", None)
    if attributes:
        body.update(attributes)
    return body


def _notification_body(params: Any) -> Dict[str, Any]:
    body = _body_without_attributes(
        params,
        {
            "name": "name",
            "table": "collection",
            "active": "active",
            "event_name": "event_name",
            "subject": "subject",
            "template": "template",
            "recipient_fields": "recipient_fields",
            "recipient_users": "recipient_users",
            "recipient_groups": "recipient_groups",
            "condition": "condition",
            "advanced_condition": "advanced_condition",
            "send_when": "send_when",
            "weight": "weight",
        },
    )
    return _apply_message_fields(body, params)


def _template_body(params: Any) -> Dict[str, Any]:
    body = _body_without_attributes(
        params,
        {
            "name": "name",
            "table": "collection",
            "active": "active",
            "subject": "subject",
            "email_layout": "email_layout",
        },
    )
    return _apply_message_fields(body, params)


def _event_definition_body(params: Any) -> Dict[str, Any]:
    return _body(
        params,
        {
            "event_name": "event_name",
            "table": "table",
            "description": "description",
            "fired_by": "fired_by",
            "queue": "queue",
            "suffix": "suffix",
        },
    )


def _script_action_body(params: Any) -> Dict[str, Any]:
    return _body(
        params,
        {
            "name": "name",
            "event_name": "event_name",
            "script": "script",
            "active": "active",
            "condition_script": "condition_script",
            "description": "description",
            "order": "order",
            "event_parm_1": "event_parm_1",
            "event_parm_2": "event_parm_2",
        },
    )


def _norm_notification(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _record_sys_id(record),
        "name": record.get("name"),
        "table": _display(record.get("collection")),
        "active": _read_bool(record.get("active")),
        "event_name": _display(record.get("event_name")),
        "subject": record.get("subject"),
        "message_html": record.get("message_html") or record.get("message"),
        "message_text": record.get("message_text"),
        "message": record.get("message"),
        "template": _ref(record.get("template")),
        "recipient_fields": record.get("recipient_fields"),
        "recipient_users": _display(record.get("recipient_users")),
        "recipient_groups": _display(record.get("recipient_groups")),
        "condition": record.get("condition"),
        "send_when": _display(record.get("send_when")),
        "weight": record.get("weight"),
        "content_type": _display(record.get("content_type")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_template(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _record_sys_id(record),
        "name": record.get("name"),
        "table": _display(record.get("collection")),
        "active": _read_bool(record.get("active")),
        "subject": record.get("subject"),
        "message_html": record.get("message_html") or record.get("message"),
        "message_text": record.get("message_text"),
        "message": record.get("message"),
        "email_layout": _ref(record.get("email_layout")),
        "content_type": _display(record.get("content_type")),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_event_definition(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _record_sys_id(record),
        "event_name": record.get("event_name"),
        "table": _display(record.get("table")),
        "description": record.get("description"),
        "fired_by": record.get("fired_by"),
        "queue": _display(record.get("queue")),
        "suffix": record.get("suffix"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_queued_event(record: Dict[str, Any], include_raw: bool = True) -> Dict[str, Any]:
    data = {
        "sys_id": _record_sys_id(record),
        "name": record.get("name"),
        "table": _display(record.get("table")),
        "instance": _ref(record.get("instance")),
        "parm1": record.get("parm1"),
        "parm2": record.get("parm2"),
        "queue": _display(record.get("queue")),
        "state": _display(record.get("state")),
        "created_on": record.get("sys_created_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def _norm_script_action(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    data = {
        "sys_id": _record_sys_id(record),
        "name": record.get("name"),
        "event_name": _display(record.get("event_name")),
        "active": _read_bool(record.get("active")),
        "script": record.get("script"),
        "condition_script": record.get("condition_script"),
        "description": record.get("description"),
        "order": record.get("order"),
        "event_parm_1": record.get("event_parm_1"),
        "event_parm_2": record.get("event_parm_2"),
        "created_on": record.get("sys_created_on"),
        "updated_on": record.get("sys_updated_on"),
    }
    if include_raw:
        data["raw"] = record
    return data


def list_notifications(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListNotificationsParams,
) -> Dict[str, Any]:
    """List notifications for ServiceNow MCP workflows.

    Builds a filter query from the caller's criteria and returns normalized
    notification records with optional raw payloads for debugging.
    """
    query = _query(
        [
            f"active={_coerce_bool(params.active)}" if params.active is not None else None,
            f"collection={params.table}" if params.table else None,
            f"event_name={params.event_name}" if params.event_name else None,
            f"nameLIKE{params.query}^ORsubjectLIKE{params.query}" if params.query else None,
            params.encoded_query,
        ]
    )
    try:
        records = _list(
            config, auth_manager, NOTIFICATION_TABLE, query, params.limit, params.offset
        )
        notifications = [_norm_notification(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(notifications)} notifications",
            "notifications": notifications,
            "count": len(notifications),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error listing notifications: %s", e)
        return {"success": False, "message": f"Error listing notifications: {str(e)}"}


def get_notification(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetNotificationParams,
) -> Dict[str, Any]:
    """Get notification for ServiceNow MCP workflows.

    Resolves a single notification by identifier and preserves the caller's
    raw-record preference in the normalized response.
    """
    try:
        record = _resolve(config, auth_manager, NOTIFICATION_TABLE, params.notification_id)
        return {"success": True, "notification": _norm_notification(record, params.include_raw)}
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting notification: %s", e)
        return {"success": False, "message": f"Error getting notification: {str(e)}"}


def create_notification(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateNotificationParams,
) -> Dict[str, Any]:
    """Create notification for ServiceNow MCP workflows.

    Posts a new notification definition and returns the created record in the
    normalized notification shape used across the MCP tools.
    """
    try:
        record = _post(config, auth_manager, NOTIFICATION_TABLE, _notification_body(params))
        return {
            "success": True,
            "message": f"Created notification {record.get('name') or record.get('sys_id')}",
            "notification": _norm_notification(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating notification: %s", e)
        return {"success": False, "message": f"Error creating notification: {str(e)}"}


def update_notification(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateNotificationParams,
) -> Dict[str, Any]:
    """Update notification for ServiceNow MCP workflows.

    Applies a partial patch only when at least one notification field is
    present, avoiding empty writes to the instance.
    """
    body = _notification_body(params)
    if not body:
        return {"success": False, "message": "No notification fields supplied to update"}
    try:
        existing = _resolve(config, auth_manager, NOTIFICATION_TABLE, params.notification_id)
        sys_id = _record_sys_id(existing)
        if not sys_id:
            raise ValueError(f"Notification does not have a sys_id: {params.notification_id}")
        record = _patch(config, auth_manager, NOTIFICATION_TABLE, sys_id, body)
        return {
            "success": True,
            "message": f"Updated notification {record.get('name') or record.get('sys_id')}",
            "notification": _norm_notification(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating notification: %s", e)
        return {"success": False, "message": f"Error updating notification: {str(e)}"}


def list_email_templates(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListEmailTemplatesParams,
) -> Dict[str, Any]:
    """List email templates for ServiceNow MCP workflows.

    Supports active, table, and text filters and returns normalized template
    records with paging metadata.
    """
    query = _query(
        [
            f"active={_coerce_bool(params.active)}" if params.active is not None else None,
            f"collection={params.table}" if params.table else None,
            f"nameLIKE{params.query}^ORsubjectLIKE{params.query}" if params.query else None,
            params.encoded_query,
        ]
    )
    try:
        records = _list(
            config, auth_manager, EMAIL_TEMPLATE_TABLE, query, params.limit, params.offset
        )
        templates = [_norm_template(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(templates)} email templates",
            "email_templates": templates,
            "count": len(templates),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error listing email templates: %s", e)
        return {"success": False, "message": f"Error listing email templates: {str(e)}"}


def get_email_template(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetEmailTemplateParams,
) -> Dict[str, Any]:
    """Get one email template record by identifier."""
    try:
        record = _resolve(config, auth_manager, EMAIL_TEMPLATE_TABLE, params.template_id)
        return {"success": True, "email_template": _norm_template(record, params.include_raw)}
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting email template: %s", e)
        return {"success": False, "message": f"Error getting email template: {str(e)}"}


def create_email_template(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateEmailTemplateParams,
) -> Dict[str, Any]:
    """Create an email template and return the normalized created record."""
    try:
        record = _post(config, auth_manager, EMAIL_TEMPLATE_TABLE, _template_body(params))
        return {
            "success": True,
            "message": f"Created email template {record.get('name') or record.get('sys_id')}",
            "email_template": _norm_template(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating email template: %s", e)
        return {"success": False, "message": f"Error creating email template: {str(e)}"}


def update_email_template(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateEmailTemplateParams,
) -> Dict[str, Any]:
    """Update an email template with partial-field patch semantics."""
    body = _template_body(params)
    if not body:
        return {"success": False, "message": "No email template fields supplied to update"}
    try:
        existing = _resolve(config, auth_manager, EMAIL_TEMPLATE_TABLE, params.template_id)
        sys_id = _record_sys_id(existing)
        if not sys_id:
            raise ValueError(f"Email template does not have a sys_id: {params.template_id}")
        record = _patch(config, auth_manager, EMAIL_TEMPLATE_TABLE, sys_id, body)
        return {
            "success": True,
            "message": f"Updated email template {record.get('name') or record.get('sys_id')}",
            "email_template": _norm_template(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating email template: %s", e)
        return {"success": False, "message": f"Error updating email template: {str(e)}"}


def list_event_definitions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListEventDefinitionsParams,
) -> Dict[str, Any]:
    """List event definitions used by notification routing workflows."""
    query = _query(
        [
            f"table={params.table}" if params.table else None,
            (
                f"event_nameLIKE{params.query}^ORdescriptionLIKE{params.query}"
                if params.query
                else None
            ),
            params.encoded_query,
        ]
    )
    try:
        records = _list(
            config, auth_manager, EVENT_DEFINITION_TABLE, query, params.limit, params.offset
        )
        events = [_norm_event_definition(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(events)} event definitions",
            "event_definitions": events,
            "count": len(events),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error listing event definitions: %s", e)
        return {"success": False, "message": f"Error listing event definitions: {str(e)}"}


def get_event_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetEventDefinitionParams,
) -> Dict[str, Any]:
    """Get one event definition record by identifier."""
    try:
        record = _resolve(
            config,
            auth_manager,
            EVENT_DEFINITION_TABLE,
            params.event_id,
            match_field="event_name",
        )
        return {
            "success": True,
            "event_definition": _norm_event_definition(record, params.include_raw),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting event definition: %s", e)
        return {"success": False, "message": f"Error getting event definition: {str(e)}"}


def create_event_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateEventDefinitionParams,
) -> Dict[str, Any]:
    """Create a new sysevent registration record."""
    try:
        record = _post(config, auth_manager, EVENT_DEFINITION_TABLE, _event_definition_body(params))
        return {
            "success": True,
            "message": (
                f"Created event definition {record.get('event_name') or record.get('sys_id')}"
            ),
            "event_definition": _norm_event_definition(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating event definition: %s", e)
        return {"success": False, "message": f"Error creating event definition: {str(e)}"}


def update_event_definition(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateEventDefinitionParams,
) -> Dict[str, Any]:
    """Update an existing event definition using partial fields."""
    body = _event_definition_body(params)
    if not body:
        return {"success": False, "message": "No event definition fields supplied to update"}
    try:
        existing = _resolve(
            config,
            auth_manager,
            EVENT_DEFINITION_TABLE,
            params.event_id,
            match_field="event_name",
        )
        sys_id = _record_sys_id(existing)
        if not sys_id:
            raise ValueError(f"Event definition does not have a sys_id: {params.event_id}")
        record = _patch(config, auth_manager, EVENT_DEFINITION_TABLE, sys_id, body)
        return {
            "success": True,
            "message": (
                f"Updated event definition {record.get('event_name') or record.get('sys_id')}"
            ),
            "event_definition": _norm_event_definition(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating event definition: %s", e)
        return {"success": False, "message": f"Error updating event definition: {str(e)}"}


def queue_event(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: QueueEventParams,
) -> Dict[str, Any]:
    """Queue a sysevent record for downstream notification processing."""
    body: Dict[str, Any] = {"name": params.event_name}
    if params.parm1 is not None:
        body["parm1"] = params.parm1
    if params.parm2 is not None:
        body["parm2"] = params.parm2
    if params.queue is not None:
        body["queue"] = params.queue
    if params.table is not None:
        body["table"] = params.table
    if params.record_id:
        if not params.table:
            return {
                "success": False,
                "message": "table is required when record_id is supplied",
            }
        sys_id = _extract_sys_id(params.record_id)
        if not sys_id:
            record = _resolve(
                config,
                auth_manager,
                params.table,
                params.record_id,
                match_field=params.record_number_field,
            )
            sys_id = _record_sys_id(record)
            if not sys_id:
                raise ValueError(
                    f"{params.table} record does not have a sys_id: {params.record_id}"
                )
        body["instance"] = sys_id
    if params.attributes:
        body.update(params.attributes)
    try:
        record = _post(config, auth_manager, EVENT_QUEUE_TABLE, body)
        return {
            "success": True,
            "message": f"Queued event {params.event_name}",
            "event": _norm_queued_event(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error queueing event: %s", e)
        return {"success": False, "message": f"Error queueing event: {str(e)}"}


def list_script_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListScriptActionsParams,
) -> Dict[str, Any]:
    """List script actions tied to event and notification logic."""
    query = _query(
        [
            f"active={_coerce_bool(params.active)}" if params.active is not None else None,
            f"event_name={params.event_name}" if params.event_name else None,
            f"nameLIKE{params.query}^ORdescriptionLIKE{params.query}" if params.query else None,
            params.encoded_query,
        ]
    )
    try:
        records = _list(
            config, auth_manager, SCRIPT_ACTION_TABLE, query, params.limit, params.offset
        )
        actions = [_norm_script_action(record, params.include_raw) for record in records]
        return {
            "success": True,
            "message": f"Retrieved {len(actions)} script actions",
            "script_actions": actions,
            "count": len(actions),
            "limit": params.limit,
            "offset": params.offset,
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error listing script actions: %s", e)
        return {"success": False, "message": f"Error listing script actions: {str(e)}"}


def get_script_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GetScriptActionParams,
) -> Dict[str, Any]:
    """Get one script action by identifier."""
    try:
        record = _resolve(config, auth_manager, SCRIPT_ACTION_TABLE, params.script_action_id)
        return {"success": True, "script_action": _norm_script_action(record, params.include_raw)}
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error getting script action: %s", e)
        return {"success": False, "message": f"Error getting script action: {str(e)}"}


def create_script_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateScriptActionParams,
) -> Dict[str, Any]:
    """Create a script action record for event-driven automations."""
    try:
        record = _post(config, auth_manager, SCRIPT_ACTION_TABLE, _script_action_body(params))
        return {
            "success": True,
            "message": f"Created script action {record.get('name') or record.get('sys_id')}",
            "script_action": _norm_script_action(record, True),
        }
    except requests.exceptions.RequestException as e:
        logger.error("Error creating script action: %s", e)
        return {"success": False, "message": f"Error creating script action: {str(e)}"}


def update_script_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: UpdateScriptActionParams,
) -> Dict[str, Any]:
    """Update a script action with explicit partial-field semantics."""
    body = _script_action_body(params)
    if not body:
        return {"success": False, "message": "No script action fields supplied to update"}
    try:
        existing = _resolve(config, auth_manager, SCRIPT_ACTION_TABLE, params.script_action_id)
        sys_id = _record_sys_id(existing)
        if not sys_id:
            raise ValueError(f"Script action does not have a sys_id: {params.script_action_id}")
        record = _patch(config, auth_manager, SCRIPT_ACTION_TABLE, sys_id, body)
        return {
            "success": True,
            "message": f"Updated script action {record.get('name') or record.get('sys_id')}",
            "script_action": _norm_script_action(record, True),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error updating script action: %s", e)
        return {"success": False, "message": f"Error updating script action: {str(e)}"}


def delete_script_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeleteScriptActionParams,
) -> Dict[str, Any]:
    """Delete a script action after resolving its concrete sys_id."""
    try:
        existing = _resolve(config, auth_manager, SCRIPT_ACTION_TABLE, params.script_action_id)
        sys_id = _record_sys_id(existing)
        if not sys_id:
            raise ValueError(f"Script action does not have a sys_id: {params.script_action_id}")
        response = requests.delete(
            _table_url(config, SCRIPT_ACTION_TABLE, sys_id),
            headers=_headers(auth_manager),
            timeout=config.timeout,
        )
        response.raise_for_status()
        return {
            "success": True,
            "message": f"Deleted script action {existing.get('name') or sys_id}",
            "script_action_id": sys_id,
            "script_action_name": existing.get("name"),
        }
    except (ValueError, requests.exceptions.RequestException) as e:
        logger.error("Error deleting script action: %s", e)
        return {"success": False, "message": f"Error deleting script action: {str(e)}"}
