"""Flow Designer deployment tools backed by the local generic flow server.

This module converts Flow Designer specifications into records and API calls
that can be deployed through the local flow server. It also performs payload
sanitization and response compaction so generated flow definitions stay safe to
render in logs and tool responses.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from typing import Annotated, Any, Dict, Literal, Optional, Union

import requests
from pydantic import BaseModel, ConfigDict, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.flow_server_config import get_flow_server_url
from servicenow_mcp.utils.config import AuthType, ServerConfig

logger = logging.getLogger(__name__)

SENSITIVE_KEY_MARKERS = ("password", "pwd", "secret", "token", "authorization")
LARGE_RESPONSE_KEYS = (
    "log",
    "logs",
    "stdout",
    "stderr",
    "source",
    "generatedSource",
    "stack",
    "trace",
)
MAX_SERVER_RETURN_STRING_CHARS = 700
MAX_SERVER_RETURN_LIST_ITEMS = 8
MAX_SERVER_RETURN_DICT_KEYS = 24
MAX_SERVER_RETURN_DEPTH = 3
SUPPORTED_TRIGGER_TYPES = ("record.created", "record.updated", "record.created_or_updated")
SUPPORTED_ACTION_TYPES = ("core.log", "core.sendEmail", "core.updateRecord", "core.lookUpRecord")
SUPPORTED_RUN_AS = ("system", "user")
FLOW_SPEC_REQUIRED_FIELD_NAMES = ("id", "name", "runAs", "trigger", "actions")
DEFAULT_SCOPE_ID = "global"
DEFAULT_SCOPE_NAME = "Global"
DEFAULT_ADMIN_EMAIL = "admin@example.com"
PLACEHOLDER_INSTANCE_URLS = (
    "https://your-instance.service-now.com",
    "http://your-instance.service-now.com",
    "https://example.invalid",
)
PLACEHOLDER_SECRETS = ("***REDACTED***", "<password>")
SUMMARY_SCALAR_KEYS = (
    "success",
    "ok",
    "status",
    "msg",
    "reqId",
    "message",
    "flowName",
    "flowSysId",
    "sys_id",
    "sysId",
    "verified",
)
DATA_PILL_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
SUPPORTED_DATA_PILL_ROOTS = ("trigger", "action")
FLOW_DEPLOYMENT_FIELD_NAMES = (
    "instanceUrl",
    "username",
    "password",
    "scopeId",
    "scopeName",
    "reinstall",
    "flowSpec",
)
FLOW_TOOL_CONTROL_FIELD_NAMES = (
    "deployment_request",
    "flow_server_url",
    "timeout_seconds",
    "timeout",
    "include_server_logs",
    "json_text",
    "fill_runtime_credentials",
)


class FlowTrigger(BaseModel):
    """Trigger configuration accepted by the generic Flow Server."""

    model_config = ConfigDict(extra="allow")

    type: Literal["record.created", "record.updated", "record.created_or_updated"] = Field(
        description="Record trigger type supported by the Flow Server.",
    )
    table: str = Field(description="ServiceNow table watched by the record trigger.")
    condition: Optional[str] = Field(
        None,
        description=(
            "Optional ServiceNow encoded query string. Use an empty string for no condition."
        ),
    )


class FlowActionInputs(BaseModel):
    """Extensible inputs for Flow Server actions without a documented fixed shape."""

    model_config = ConfigDict(extra="allow")


class CoreLogInputs(FlowActionInputs):
    """Inputs required by the core.log action."""

    log_level: str = Field(description="Log severity such as info, warning, or error.")
    log_message: Any = Field(description="Log message string or Flow Server template object.")


class CoreSendEmailInputs(FlowActionInputs):
    """Inputs required by the core.sendEmail action."""

    table_name: str = Field(description="Table containing the email record.")
    record: str = Field(description="Record sys_id or data pill.")
    watermark_email: bool = Field(description="Whether to add an email watermark.")
    ah_to: str = Field(description="Recipient email address or data pill.")
    ah_subject: Any = Field(description="Email subject string or template object.")
    ah_body: Any = Field(description="Static email body string or template object.")


class CoreLogAction(BaseModel):
    """A core.log Flow Server action."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable action id, unique within the flow.")
    type: Literal["core.log"] = Field(description="Flow Server log action.")
    inputs: CoreLogInputs = Field(description="Required core.log inputs.")


class CoreSendEmailAction(BaseModel):
    """A core.sendEmail Flow Server action."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable action id, unique within the flow.")
    type: Literal["core.sendEmail"] = Field(description="Flow Server email action.")
    inputs: CoreSendEmailInputs = Field(description="Required core.sendEmail inputs.")


class CoreUpdateRecordAction(BaseModel):
    """A core.updateRecord Flow Server action."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable action id, unique within the flow.")
    type: Literal["core.updateRecord"] = Field(description="Flow Server record update action.")
    inputs: FlowActionInputs = Field(description="Flow Server record update inputs.")


class CoreLookUpRecordAction(BaseModel):
    """A core.lookUpRecord Flow Server action."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable action id, unique within the flow.")
    type: Literal["core.lookUpRecord"] = Field(description="Flow Server record lookup action.")
    inputs: FlowActionInputs = Field(description="Flow Server record lookup inputs.")


FlowAction = Annotated[
    Union[
        CoreLogAction,
        CoreSendEmailAction,
        CoreUpdateRecordAction,
        CoreLookUpRecordAction,
    ],
    Field(discriminator="type"),
]


class FlowSpec(BaseModel):
    """Generic flow specification passed to the Flow Server."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable lowercase flow id.")
    name: str = Field(description="Flow Designer flow name.")
    description: Optional[str] = Field(None, description="Optional flow description.")
    runAs: Literal["system", "user"] = Field(description="Execution identity for the flow.")
    trigger: FlowTrigger = Field(description="Record trigger configuration.")
    actions: list[FlowAction] = Field(description="Ordered Flow Server actions.")


class CreateFlowParams(BaseModel):
    """Parameters for deploying a Flow Designer flow through the TS flow server."""

    instanceUrl: Optional[str] = Field(
        None,
        description=(
            "ServiceNow instance URL to include in the /deploy-flow request. If omitted, "
            "the current MCP ServiceNow instance URL is used when available."
        ),
    )
    username: Optional[str] = Field(
        None,
        description=(
            "ServiceNow username to include in the /deploy-flow request. If omitted, "
            "the current MCP basic-auth username is used when available."
        ),
    )
    password: Optional[str] = Field(
        None,
        description=(
            "ServiceNow password to include in the /deploy-flow request. If omitted, "
            "the current MCP basic-auth password is used when available."
        ),
    )
    scopeId: Optional[str] = Field(
        None,
        description=("ServiceNow application scope technical name, sys_scope sys_id, or global."),
    )
    scopeName: Optional[str] = Field(
        None,
        description="ServiceNow application scope display name.",
    )
    reinstall: Optional[bool] = Field(
        None,
        description=(
            "Whether the Flow Server should run the SDK install with reinstall=true. "
            "Use false for normal updates."
        ),
    )
    flowSpec: Optional[FlowSpec] = Field(
        None,
        description=(
            "Generic flow specification with id, name, runAs, trigger, and actions. "
            "This is passed directly to the Flow Server."
        ),
    )
    deployment_request: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Backward-compatible wrapper for the full /deploy-flow JSON body. Prefer "
            "sending instanceUrl, username, password, scopeId, scopeName, reinstall, "
            "and flowSpec as top-level tool arguments so the client JSON matches the "
            "Flow Server request files."
        ),
    )
    json_text: Optional[str] = Field(
        None,
        description=(
            "Optional raw Flow Server JSON text. If strict JSON parsing fails, the tool "
            "tries a local repair pass for common JSON-like formatting mistakes."
        ),
    )
    flow_server_url: Optional[str] = Field(
        None,
        description=(
            "Base URL for the local generic flow server. Defaults to "
            "SERVICENOW_FLOW_SERVER_URL or the exported FLOW_SERVER_URL value "
            "in servicenow_mcp.tools.flow_server_config."
        ),
    )
    timeout_seconds: int = Field(
        600,
        gt=0,
        description=(
            "Timeout for the /deploy-flow call. Flow generation, SDK build, install, "
            "and verification can take several minutes."
        ),
    )
    timeout: Optional[int] = Field(
        None,
        gt=0,
        description=(
            "Compatibility timeout accepted from MCP clients that send timeout in the "
            "tool arguments. Values >=1000 are treated as milliseconds; smaller values "
            "are treated as seconds. Prefer timeout_seconds for direct tool calls."
        ),
    )
    include_server_logs: bool = Field(
        False,
        description=(
            "When true, include a very small bounded log preview from the Flow Server "
            "response. Defaults to false to keep MCP context compact."
        ),
    )


class ValidateFlowJsonParams(BaseModel):
    """Parameters for validating a Flow Server deployment JSON body."""

    instanceUrl: Optional[str] = Field(None, description="ServiceNow instance URL.")
    username: Optional[str] = Field(None, description="ServiceNow username.")
    password: Optional[str] = Field(None, description="ServiceNow password.")
    scopeId: Optional[str] = Field(None, description="Application scope id/name/sys_id/global.")
    scopeName: Optional[str] = Field(None, description="Application scope display name.")
    reinstall: Optional[bool] = Field(None, description="Whether SDK reinstall should be used.")
    flowSpec: Optional[FlowSpec] = Field(None, description="Generic flow specification.")
    deployment_request: Optional[Dict[str, Any]] = Field(
        None,
        description="Backward-compatible wrapper for the full Flow Server request JSON body.",
    )
    json_text: Optional[str] = Field(
        None,
        description="Optional raw JSON text to parse and validate.",
    )
    fill_runtime_credentials: bool = Field(
        True,
        description=(
            "When true, missing instanceUrl, username, and password are filled from "
            "the current MCP basic-auth runtime config before validation."
        ),
    )


class HintFlowJsonParams(BaseModel):
    """Parameters for generating a ready-to-fill Flow Server JSON body."""

    flow_name: str = Field(
        "Incident Created - Notify Admin",
        description="Flow Designer flow name.",
    )
    flow_id: Optional[str] = Field(
        None,
        description="Stable flow id. If omitted, one is generated from flow_name.",
    )
    description: str = Field(
        "Notify admin when incident is created",
        description="Flow description.",
    )
    table: str = Field("incident", description="Trigger table.")
    trigger_type: str = Field(
        "record.created",
        description="Trigger type: record.created, record.updated, or record.created_or_updated.",
    )
    scopeId: str = Field(DEFAULT_SCOPE_ID, description="Application scope id/name/sys_id/global.")
    scopeName: str = Field(DEFAULT_SCOPE_NAME, description="Application scope display name.")
    runAs: str = Field("system", description="Run as system or user.")
    reinstall: bool = Field(False, description="Whether SDK reinstall should be used.")
    include_email_action: bool = Field(
        True,
        description="Include a core.sendEmail action after the core.log action.",
    )
    recipient_email: str = Field(
        DEFAULT_ADMIN_EMAIL,
        description="Recipient used when include_email_action is true.",
    )


class CreateIncidentAdminNotificationFlowParams(BaseModel):
    """Parameters for a one-call incident-created admin notification flow."""

    flow_name: str = Field(
        "Incident Created - Notify Admin",
        description="Flow Designer flow name.",
    )
    flow_id: Optional[str] = Field(
        None,
        description="Stable flow id. If omitted, one is generated from flow_name.",
    )
    description: str = Field(
        "Notify admin when incident is created",
        description="Flow description.",
    )
    recipient_email: str = Field(
        DEFAULT_ADMIN_EMAIL,
        description="Admin email address for the notification.",
    )
    scopeId: str = Field(
        DEFAULT_SCOPE_ID,
        description="Application scope id/name/sys_id/global. Defaults to global.",
    )
    scopeName: str = Field(
        DEFAULT_SCOPE_NAME,
        description="Application scope display name. Defaults to Global.",
    )
    reinstall: bool = Field(False, description="Whether SDK reinstall should be used.")
    flow_server_url: Optional[str] = Field(
        None,
        description="Optional Flow Server base URL override.",
    )
    timeout_seconds: int = Field(
        600,
        gt=0,
        description="Timeout for the /deploy-flow call.",
    )
    timeout: Optional[int] = Field(
        None,
        gt=0,
        description="Compatibility timeout. Values >=1000 are treated as milliseconds.",
    )
    include_server_logs: bool = Field(
        False,
        description="Include bounded Flow Server log previews. Defaults to false.",
    )


def _is_sensitive_key(key: Any) -> bool:
    """Return True when a key name indicates sensitive credential/token content."""
    normalized = str(key).lower()
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def _redact_string(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Redact explicit secret substrings from a text value."""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def _redact_sensitive_values(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact sensitive keys and secret values in nested payloads."""
    if isinstance(value, dict):
        return {
            key: (
                "***REDACTED***"
                if _is_sensitive_key(key)
                else _redact_sensitive_values(val, secrets=secrets)
            )
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        return _redact_string(value, secrets=secrets)
    return value


def _redact_text(text: str, password: Optional[str] = None) -> str:
    """Redact plaintext password/secret patterns in error and log text."""
    redacted = text
    if password:
        redacted = redacted.replace(password, "***REDACTED***")
    return re.sub(
        r'(?i)("?(?:password|pwd|secret|token|authorization)"?\s*[:=]\s*")([^"]*)(")',
        r"\1***REDACTED***\3",
        redacted,
    )


def _parse_response(response: requests.Response, password: Optional[str]) -> Any:
    """Parse Flow Server response JSON when possible, with safe text fallbacks.

    Parsing tries strict JSON first, then line-by-line JSON extraction from mixed
    responses, and finally returns redacted raw text.
    """
    try:
        return _redact_sensitive_values(response.json(), secrets=(password or "",))
    except ValueError as exc:
        logger.debug(
            "MCP_FLOW_RESPONSE_JSON_DECODE_FAILED status_code=%s error=%s",
            response.status_code,
            exc,
        )
        text = response.text

    stripped_text = text.strip()
    if stripped_text.startswith(("{", "[")):
        try:
            return _redact_sensitive_values(
                json.loads(stripped_text),
                secrets=(password or "",),
            )
        except ValueError as exc:
            logger.debug(
                "MCP_FLOW_RESPONSE_TEXT_JSON_DECODE_FAILED status_code=%s error=%s",
                response.status_code,
                exc,
            )
            pass

    for line in reversed(stripped_text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith(("{", "[")):
            continue
        try:
            return _redact_sensitive_values(
                json.loads(candidate),
                secrets=(password or "",),
            )
        except ValueError:
            continue

    return _redact_text(response.text, password=password)


def _safe_scalar(value: Any) -> bool:
    """Return True when value is a scalar safe for inline summary output."""
    return isinstance(value, (str, int, float, bool)) or value is None


def _truncate_text(value: Any, limit: int = 500) -> str:
    """Convert a value to text and cap it with truncation metadata."""
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _is_large_response_key(key: Any) -> bool:
    """Identify keys that usually carry oversized logs or payload fragments."""
    normalized = str(key).lower()
    return any(marker.lower() in normalized for marker in LARGE_RESPONSE_KEYS)


def _bounded_log_preview(value: Any, limit: int = 5) -> Dict[str, Any]:
    """Build a compact tail preview for list/string logs and similar outputs."""
    if isinstance(value, list):
        preview_items = value[-limit:]
        return {
            "count": len(value),
            "tail": [_truncate_text(item, 300) for item in preview_items],
        }
    if isinstance(value, str):
        lines = value.splitlines()
        return {
            "line_count": len(lines),
            "tail": [_truncate_text(line, 300) for line in lines[-limit:]],
        }
    return {"type": type(value).__name__, "preview": _truncate_text(value, 300)}


def _compact_server_result(result: Any, include_logs: bool = False) -> Dict[str, Any]:
    """Compact Flow Server responses to safe, high-signal diagnostics.

    Keeps key status fields and bounded previews while omitting noisy payload
    sections unless log previews are explicitly requested.
    """
    if not isinstance(result, dict):
        return {
            "type": type(result).__name__,
            "preview": _truncate_text(result),
        }

    compact: Dict[str, Any] = {}
    omitted_keys = []

    for key, value in result.items():
        if _is_large_response_key(key):
            omitted_keys.append(str(key))
            if include_logs:
                compact[f"{key}_preview"] = _bounded_log_preview(value)
            continue

        if key in SUMMARY_SCALAR_KEYS and _safe_scalar(value):
            compact[key] = _truncate_text(value) if isinstance(value, str) else value
            continue

        if key in {"verification", "flow", "record"} and isinstance(value, dict):
            compact[key] = {
                nested_key: (
                    _truncate_text(nested_value) if isinstance(nested_value, str) else nested_value
                )
                for nested_key, nested_value in value.items()
                if _safe_scalar(nested_value)
            }
            continue

        if key == "err" and isinstance(value, dict):
            compact[key] = {
                nested_key: (
                    _truncate_text(nested_value) if isinstance(nested_value, str) else nested_value
                )
                for nested_key, nested_value in value.items()
                if nested_key in {"type", "message", "code", "name"} and _safe_scalar(nested_value)
            }
            if "stack" in value:
                compact[key]["stack_preview"] = _bounded_log_preview(value["stack"])
            continue

        if key in {"warnings", "errors"} and isinstance(value, list):
            compact[key] = [_truncate_text(item, 300) for item in value[:10]]
            if len(value) > 10:
                compact[f"{key}_omitted_count"] = len(value) - 10
            continue

        if key == "issues":
            compact[key] = _bounded_server_return_data(value)
            continue

        omitted_keys.append(str(key))

    if omitted_keys:
        compact["omitted_keys"] = sorted(set(omitted_keys))
    return compact


def _extract_flow_server_error(result: Any) -> Optional[Dict[str, Any]]:
    """Extract normalized error metadata from varied Flow Server response shapes."""
    if isinstance(result, dict):
        err = result.get("err")
        message = None
        error_type = None
        stack_preview = None
        if isinstance(err, dict):
            message = err.get("message")
            error_type = err.get("type") or err.get("name")
            if err.get("stack"):
                stack_preview = _bounded_log_preview(err["stack"])

        message = message or result.get("msg") or result.get("message")
        if message:
            extracted: Dict[str, Any] = {
                "message": _truncate_text(message, 1000),
            }
            if error_type:
                extracted["type"] = error_type
            if result.get("reqId"):
                extracted["reqId"] = result["reqId"]
            if stack_preview:
                extracted["stack_preview"] = stack_preview
            return extracted

    if isinstance(result, str) and result:
        return {"message": _truncate_text(result, 1000)}
    return None


def _bounded_server_return_data(
    value: Any,
    include_logs: bool = False,
    depth: int = 0,
) -> Any:
    """Recursively bound server return data for safe inline transport.

    Applies depth, list-length, key-count, and string-size limits while preserving
    structural signals needed for troubleshooting.
    """
    if depth > MAX_SERVER_RETURN_DEPTH:
        return {"truncated": True, "reason": "maximum nesting depth reached"}

    if isinstance(value, dict):
        bounded: Dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:MAX_SERVER_RETURN_DICT_KEYS]:
            if _is_sensitive_key(key):
                bounded[key] = "***REDACTED***"
            elif _is_large_response_key(key):
                bounded[key] = _bounded_log_preview(
                    item,
                    limit=10 if include_logs else 5,
                )
            else:
                bounded[key] = _bounded_server_return_data(
                    item,
                    include_logs=include_logs,
                    depth=depth + 1,
                )
        if len(items) > MAX_SERVER_RETURN_DICT_KEYS:
            sample_items = items[MAX_SERVER_RETURN_DICT_KEYS : MAX_SERVER_RETURN_DICT_KEYS + 5]
            bounded["_truncated_keys"] = {
                "omitted_count": len(items) - MAX_SERVER_RETURN_DICT_KEYS,
                "sample": [str(key) for key, _item in sample_items],
            }
        return bounded

    if isinstance(value, list):
        bounded_items = [
            _bounded_server_return_data(
                item,
                include_logs=include_logs,
                depth=depth + 1,
            )
            for item in value[:MAX_SERVER_RETURN_LIST_ITEMS]
        ]
        if len(value) > MAX_SERVER_RETURN_LIST_ITEMS:
            bounded_items.append(
                {
                    "truncated": True,
                    "omitted_count": len(value) - MAX_SERVER_RETURN_LIST_ITEMS,
                }
            )
        return bounded_items

    if isinstance(value, str):
        return _truncate_text(value, MAX_SERVER_RETURN_STRING_CHARS)

    return value


def _rethink_json_payload(
    reason: str,
    body: Optional[Dict[str, Any]] = None,
    validation: Optional[Dict[str, Any]] = None,
    server_return_data: Any = None,
    server_error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build guidance payload explaining JSON repair/remediation steps.

    Aggregates diagnostics context (body, validation, server response) into
    a checklist-driven remediation guide for the user.
    """
    payload: Dict[str, Any] = {
        "needed": True,
        "reason": reason,
        "message": (
            "Rethink the Flow Server JSON, run validate_flow_json, and then call "
            "create_flow again with the corrected top-level JSON body."
        ),
        "recommended_tools": ["validate_flow_json", "hint_flow_json", "create_flow"],
        "checklist": [
            (
                "Keep instanceUrl, username, password, scopeId, scopeName, reinstall, "
                "and flowSpec at the top level."
            ),
            (
                "Confirm flowSpec.id, flowSpec.name, flowSpec.runAs, flowSpec.trigger, "
                "and flowSpec.actions are present."
            ),
            f"Use one of these trigger types: {', '.join(SUPPORTED_TRIGGER_TYPES)}.",
            f"Use supported action types only: {', '.join(SUPPORTED_ACTION_TYPES)}.",
            (
                "Data pills must start with {{trigger.*}} or {{action.*}}. For current "
                "record fields, use {{trigger.current.<field>}}."
            ),
            "For core.sendEmail, keep ah_body static and use data pills in ah_subject if needed.",
            (
                "Do not switch to activate_workflow or publish_workflow after a "
                "create_flow failure; fix the JSON or Flow Server URL and retry create_flow."
            ),
        ],
    }
    if body is not None:
        payload["requestSummary"] = _flow_request_log_summary(body)
    if validation is not None:
        payload["validation"] = validation
    if server_return_data is not None:
        payload["flowServerReturned"] = "See deploymentResult.serverReturnData."
    if server_error is not None:
        payload["flowServerError"] = server_error
    return payload


def _flow_response_request_body(body: Dict[str, Any], password: str) -> Dict[str, Any]:
    flow_spec = body.get("flowSpec") if isinstance(body.get("flowSpec"), dict) else {}
    trigger = flow_spec.get("trigger") if isinstance(flow_spec.get("trigger"), dict) else {}
    actions = flow_spec.get("actions") if isinstance(flow_spec.get("actions"), list) else []
    action_summaries = []

    for action in actions[:10]:
        if not isinstance(action, dict):
            continue
        inputs = action.get("inputs") if isinstance(action.get("inputs"), dict) else {}
        action_summaries.append(
            {
                "id": action.get("id"),
                "type": action.get("type"),
                "input_keys": sorted(str(key) for key in inputs.keys()),
            }
        )

    if len(actions) > 10:
        action_summaries.append(
            {
                "truncated": True,
                "omitted_count": len(actions) - 10,
            }
        )

    response_body = {
        "instanceUrl": body.get("instanceUrl"),
        "username": body.get("username"),
        "password": body.get("password"),
        "scopeId": body.get("scopeId"),
        "scopeName": body.get("scopeName"),
        "reinstall": body.get("reinstall"),
        "flowSpec": {
            "id": flow_spec.get("id"),
            "name": flow_spec.get("name"),
            "description": _truncate_text(flow_spec.get("description", ""), 180),
            "runAs": flow_spec.get("runAs"),
            "trigger": {
                "type": trigger.get("type"),
                "table": trigger.get("table"),
                "has_condition": bool(trigger.get("condition")),
            },
            "action_count": len(actions),
            "actions": action_summaries,
        },
    }
    return _redact_sensitive_values(response_body, secrets=(password,))


def _flow_client_guidance() -> list[str]:
    """Return deployment best-practice guidance for MCP clients."""
    return [
        (
            "Do not call activate_workflow or publish_workflow for this result. "
            "Those workflow tools are not the Flow Server deployment path."
        ),
        (
            "The create_flow tool calls Flow Server /deploy-flow, which generates, "
            "builds, installs, and verifies the Flow Designer flow."
        ),
        (
            "If deployment fails, fix the JSON or Flow Server URL and retry "
            "create_flow; use validate_flow_json before retrying."
        ),
        (
            "Do not create test incidents or inspect flow execution unless the user "
            "explicitly asks for post-deployment testing."
        ),
    ]


def _flow_response_body(
    body: Dict[str, Any],
    deployment_result: Dict[str, Any],
    password: str,
) -> Dict[str, Any]:
    """Assemble complete flow deployment response with request summary + result."""
    response_body = _flow_response_request_body(body, password=password)
    response_body["deploymentResult"] = deployment_result
    response_body["clientGuidance"] = _flow_client_guidance()
    return response_body


def _flow_request_log_summary(body: Dict[str, Any]) -> Dict[str, Any]:
    """Build telemetry-friendly summary of flow deployment request.

    Extracts high-level flow structure (scope, trigger, action types) for
    structured logging without exposing sensitive details.
    """
    flow_spec = body.get("flowSpec") if isinstance(body.get("flowSpec"), dict) else {}
    trigger = flow_spec.get("trigger") if isinstance(flow_spec.get("trigger"), dict) else {}
    actions = flow_spec.get("actions") if isinstance(flow_spec.get("actions"), list) else []
    return {
        "instanceUrl": body.get("instanceUrl"),
        "scopeId": body.get("scopeId"),
        "scopeName": body.get("scopeName"),
        "reinstall": body.get("reinstall"),
        "flow_id": flow_spec.get("id"),
        "flow_name": flow_spec.get("name"),
        "runAs": flow_spec.get("runAs"),
        "trigger_type": trigger.get("type"),
        "trigger_table": trigger.get("table"),
        "has_trigger_condition": bool(trigger.get("condition")),
        "action_count": len(actions),
        "action_types": [action.get("type") for action in actions[:10] if isinstance(action, dict)],
    }


def _current_basic_credentials(config: ServerConfig) -> tuple[Optional[str], Optional[str]]:
    """Extract basic auth username/password from current server configuration."""
    if config.auth.type != AuthType.BASIC or not config.auth.basic:
        return None, None
    return config.auth.basic.username, config.auth.basic.password


def _is_placeholder_instance_url(value: Any) -> bool:
    """Return True when instance URL is a template placeholder needing replacement."""
    if not isinstance(value, str):
        return False
    normalized = value.strip().rstrip("/").lower()
    return normalized in {item.lower() for item in PLACEHOLDER_INSTANCE_URLS}


def _is_placeholder_secret(value: Any) -> bool:
    """Return True when password/token is a template placeholder needing replacement."""
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {item.lower() for item in PLACEHOLDER_SECRETS}


def _merge_runtime_credentials(
    config: ServerConfig,
    deployment_request: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge request fields with runtime config defaults using request-first precedence.

    Caller-supplied values are preserved; missing values are filled from the current
    ServerConfig basic auth, defaulting instanceUrl/scope as needed.
    """
    merged = dict(deployment_request)
    username, password = _current_basic_credentials(config)

    if (
        not merged.get("instanceUrl") or _is_placeholder_instance_url(merged.get("instanceUrl"))
    ) and config.instance_url != "https://example.invalid":
        merged["instanceUrl"] = config.instance_url
    if not merged.get("username") and username:
        merged["username"] = username
    if (not merged.get("password") or _is_placeholder_secret(merged.get("password"))) and password:
        merged["password"] = password
    if not merged.get("scopeId"):
        merged["scopeId"] = DEFAULT_SCOPE_ID
    if not merged.get("scopeName"):
        merged["scopeName"] = DEFAULT_SCOPE_NAME
    if "reinstall" not in merged:
        merged["reinstall"] = False

    return merged


def _strip_json_markdown_fence(text: str) -> str:
    """Remove markdown triple-backtick fences from JSON block text."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_object_text(text: str) -> str:
    """Extract the first { ... } bracketed region from text for JSON parsing."""
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return stripped
    return stripped[start : end + 1]


def _normalize_json_like_text(text: str) -> str:
    """Repair common JSON-like formatting issues before strict parsing.

    Normalizes markdown fences, smart quotes, dangling commas, Python literals,
    and unquoted object keys that commonly appear in model-generated payloads.
    """
    normalized = _strip_json_markdown_fence(text)
    normalized = _extract_json_object_text(normalized)
    normalized = (
        normalized.replace("\ufeff", "")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
    normalized = re.sub(r"\bTrue\b", "true", normalized)
    normalized = re.sub(r"\bFalse\b", "false", normalized)
    normalized = re.sub(r"\bNone\b", "null", normalized)
    normalized = re.sub(
        r"(^|[{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)",
        r'\1"\2"\3',
        normalized,
        flags=re.MULTILINE,
    )
    return normalized.strip()


def _literal_eval_json_like_text(text: str) -> Optional[Dict[str, Any]]:
    """Attempt permissive dict parsing via ast.literal_eval as a final fallback."""
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        logger.debug("MCP_FLOW_JSON_LITERAL_EVAL_FAILED error=%s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_json_text(json_text: Optional[str]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse optional JSON text with staged repair and clear validation errors."""
    if not json_text:
        return None, None
    logger.info("MCP_FLOW_JSON_PARSE_START raw_length=%s", len(json_text))
    try:
        parsed = json.loads(json_text)
        logger.info("MCP_FLOW_JSON_PARSE_END repaired=false parsed_type=%s", type(parsed).__name__)
    except json.JSONDecodeError as exc:
        logger.warning(
            "[FlowJSON] Initial JSON parsing failed; attempting local JSON repair: %s",
            exc,
        )
        fixed_text = _normalize_json_like_text(json_text)
        try:
            parsed = json.loads(fixed_text)
        except json.JSONDecodeError as repair_exc:
            logger.debug(
                "MCP_FLOW_JSON_NORMALIZED_PARSE_FAILED error=%s",
                repair_exc,
            )
            parsed = _literal_eval_json_like_text(
                _strip_json_markdown_fence(_extract_json_object_text(json_text))
            )
        if parsed is None:
            logger.warning(
                "MCP_FLOW_JSON_PARSE_END repaired=false success=false error=%s",
                exc,
            )
            return (
                None,
                f"json_text is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
            )
        logger.info("[FlowJSON] JSON repair succeeded after initial parse failure.")
        logger.info("MCP_FLOW_JSON_PARSE_END repaired=true parsed_type=%s", type(parsed).__name__)
    if not isinstance(parsed, dict):
        return (
            None,
            "json_text must parse to a JSON object",
        )
    return parsed, None


def _build_deployment_request(params: BaseModel) -> tuple[Dict[str, Any], Optional[str]]:
    """Build the deployment request body from JSON text, fallback body, and top-level fields."""
    values = params.model_dump(exclude_none=True)
    parsed_json, parse_error = _parse_json_text(values.get("json_text"))
    if parse_error:
        return {}, parse_error

    body = dict(parsed_json or {})
    if values.get("deployment_request"):
        body.update(values["deployment_request"])
    for field_name in FLOW_DEPLOYMENT_FIELD_NAMES:
        if field_name in values:
            body[field_name] = values[field_name]

    logger.debug(
        "MCP_FLOW_REQUEST_BUILT body_keys=%s control_keys=%s",
        sorted(body.keys()),
        sorted(key for key in values.keys() if key in FLOW_TOOL_CONTROL_FIELD_NAMES),
    )
    return body, None


def _flow_spec_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized[:80] or "generated_flow"


def _incident_admin_notification_body(
    config: ServerConfig,
    params: CreateIncidentAdminNotificationFlowParams,
) -> Dict[str, Any]:
    """Build a ready-to-deploy default flow body for incident admin notifications."""
    username, password = _current_basic_credentials(config)
    instance_url = config.instance_url if config.instance_url != "https://example.invalid" else None
    flow_id = params.flow_id or _flow_spec_id(params.flow_name)
    body = {
        "scopeId": params.scopeId or DEFAULT_SCOPE_ID,
        "scopeName": params.scopeName or DEFAULT_SCOPE_NAME,
        "reinstall": params.reinstall,
        "flowSpec": {
            "id": flow_id,
            "name": params.flow_name,
            "description": params.description,
            "runAs": "system",
            "trigger": {
                "type": "record.created",
                "table": "incident",
                "condition": "",
            },
            "actions": [
                {
                    "id": "log_created_incident",
                    "type": "core.log",
                    "inputs": {
                        "log_level": "info",
                        "log_message": {"template": "Incident created: {{trigger.current.number}}"},
                    },
                },
                {
                    "id": "send_email_admin",
                    "type": "core.sendEmail",
                    "inputs": {
                        "table_name": "incident",
                        "record": "{{trigger.current.sys_id}}",
                        "watermark_email": True,
                        "ah_to": params.recipient_email or DEFAULT_ADMIN_EMAIL,
                        "ah_subject": {
                            "template": ("New Incident Created: {{trigger.current.number}}")
                        },
                        "ah_body": {
                            "template": (
                                "A new incident was created. Open the record in "
                                "ServiceNow for details."
                            )
                        },
                    },
                },
            ],
        },
    }
    if instance_url:
        body["instanceUrl"] = instance_url
    if username:
        body["username"] = username
    if password:
        body["password"] = password
    return body


def _request_shape_hint() -> Dict[str, Any]:
    return {
        "instanceUrl": "https://your-instance.service-now.com",
        "username": "admin",
        "password": "***REDACTED***",
        "scopeId": DEFAULT_SCOPE_ID,
        "scopeName": DEFAULT_SCOPE_NAME,
        "reinstall": False,
        "flowSpec": {
            "id": "incident_created_notify_admin",
            "name": "Incident Created - Notify Admin",
            "description": "Notify admin when incident is created",
            "runAs": "system",
            "trigger": {
                "type": "record.created",
                "table": "incident",
                "condition": "",
            },
            "actions": [
                {
                    "id": "log_created_incident",
                    "type": "core.log",
                    "inputs": {
                        "log_level": "info",
                        "log_message": {"template": "Incident created: {{trigger.current.number}}"},
                    },
                }
            ],
        },
    }


def _validation_error(path: str, message: str, hint: str) -> Dict[str, str]:
    return {"path": path, "message": message, "hint": hint}


def _validation_warning(path: str, message: str, hint: str) -> Dict[str, str]:
    return {"path": path, "message": message, "hint": hint}


def _iter_data_pill_strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from _iter_data_pill_strings(item, next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_data_pill_strings(item, f"{path}[{index}]")


def _validate_data_pills(value: Any, path: str) -> list[Dict[str, str]]:
    """Validate data pill roots in nested action input values."""
    errors: list[Dict[str, str]] = []
    for string_path, text in _iter_data_pill_strings(value, path):
        for match in DATA_PILL_PATTERN.finditer(text):
            expression = match.group(1).strip()
            root = expression.split(".", 1)[0].strip()
            if root not in SUPPORTED_DATA_PILL_ROOTS:
                suggested = f"trigger.{expression}" if root == "current" else None
                hint = (
                    f"Use {{{{{suggested}}}}} instead."
                    if suggested
                    else "Data pills must start with trigger.* or action.*."
                )
                errors.append(
                    _validation_error(
                        string_path,
                        (
                            f"Unsupported data pill root in {expression!r}. "
                            "Use trigger.* or action.*"
                        ),
                        hint,
                    )
                )
    return errors


def _validate_deployment_request_details(body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate full Flow Server deployment payload and return structured findings.

    Performs staged checks for top-level credentials/scope fields, flowSpec core
    structure, trigger definition, action definitions, and data pill usage.

    Returns:
        Dict[str, Any]: Validation result with keys ``valid``, ``errors``, and
        ``warnings`` where each finding includes path/message/hint.
    """
    errors: list[Dict[str, str]] = []
    warnings: list[Dict[str, str]] = []

    for field_name in ("instanceUrl", "username", "password", "scopeId", "scopeName"):
        if not body.get(field_name):
            errors.append(
                _validation_error(
                    field_name,
                    f"{field_name} is required",
                    f"Set top-level {field_name}, not a nested wrapper field.",
                )
            )

    if _is_placeholder_instance_url(body.get("instanceUrl")):
        errors.append(
            _validation_error(
                "instanceUrl",
                "instanceUrl is still a template placeholder",
                "Use the real ServiceNow instance URL or omit it so runtime config can fill it.",
            )
        )

    if _is_placeholder_secret(body.get("password")):
        errors.append(
            _validation_error(
                "password",
                "password is still a template placeholder",
                "Use the real password or omit it so runtime basic auth can fill it.",
            )
        )

    if "reinstall" in body and not isinstance(body["reinstall"], bool):
        errors.append(
            _validation_error(
                "reinstall",
                "reinstall must be a boolean",
                "Use true or false, not a string.",
            )
        )

    flow_spec = body.get("flowSpec")
    if not isinstance(flow_spec, dict):
        errors.append(
            _validation_error(
                "flowSpec",
                "flowSpec is required and must be an object",
                "Set flowSpec at the top level with id, name, runAs, trigger, and actions.",
            )
        )
        return {"valid": False, "errors": errors, "warnings": warnings}

    for field_name in FLOW_SPEC_REQUIRED_FIELD_NAMES:
        if not flow_spec.get(field_name):
            errors.append(
                _validation_error(
                    f"flowSpec.{field_name}",
                    f"flowSpec.{field_name} is required",
                    f"Add flowSpec.{field_name} to the JSON body.",
                )
            )

    flow_id = flow_spec.get("id")
    if isinstance(flow_id, str):
        normalized_flow_id = _flow_spec_id(flow_id)
        if flow_id != normalized_flow_id:
            warnings.append(
                _validation_warning(
                    "flowSpec.id",
                    "flowSpec.id will be normalized by the Flow Server",
                    f"Use {normalized_flow_id!r} to avoid surprises.",
                )
            )
        if len(flow_id) > 80:
            warnings.append(
                _validation_warning(
                    "flowSpec.id",
                    "flowSpec.id is longer than 80 characters",
                    "Keep ids short and stable. The Flow Server truncates ids to 80 characters.",
                )
            )
    elif flow_id is not None:
        errors.append(
            _validation_error(
                "flowSpec.id",
                "flowSpec.id must be a string",
                "Use a stable lowercase identifier such as incident_created_notify_admin.",
            )
        )

    if flow_spec.get("runAs") and flow_spec["runAs"] not in SUPPORTED_RUN_AS:
        errors.append(
            _validation_error(
                "flowSpec.runAs",
                "flowSpec.runAs must be system or user",
                'Use runAs: "system" unless the flow should run as the triggering user.',
            )
        )

    trigger = flow_spec.get("trigger")
    if not isinstance(trigger, dict):
        errors.append(
            _validation_error(
                "flowSpec.trigger",
                "flowSpec.trigger must be an object",
                "Use trigger with type, table, and condition like the sample JSON.",
            )
        )
    else:
        trigger_type = trigger.get("type")
        if trigger_type not in SUPPORTED_TRIGGER_TYPES:
            errors.append(
                _validation_error(
                    "flowSpec.trigger.type",
                    "Unsupported trigger type",
                    f"Use one of: {', '.join(SUPPORTED_TRIGGER_TYPES)}.",
                )
            )
        if not trigger.get("table") or not isinstance(trigger.get("table"), str):
            errors.append(
                _validation_error(
                    "flowSpec.trigger.table",
                    "flowSpec.trigger.table must be a table name string",
                    "Use a ServiceNow table name such as incident, problem, or change_request.",
                )
            )
        if "condition" in trigger and not isinstance(trigger["condition"], str):
            errors.append(
                _validation_error(
                    "flowSpec.trigger.condition",
                    "flowSpec.trigger.condition must be a string",
                    "Use an encoded query string, or an empty string for no condition.",
                )
            )

    actions = flow_spec.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append(
            _validation_error(
                "flowSpec.actions",
                "flowSpec.actions must be a non-empty list",
                "Add at least one action such as core.log.",
            )
        )
    else:
        action_ids = set()
        for index, action in enumerate(actions):
            action_path = f"flowSpec.actions[{index}]"
            if not isinstance(action, dict):
                errors.append(
                    _validation_error(
                        action_path,
                        "Each action must be an object",
                        'Use {"id": "log_created", "type": "core.log", "inputs": {...}}.',
                    )
                )
                continue

            action_id = action.get("id")
            if not action_id or not isinstance(action_id, str):
                errors.append(
                    _validation_error(
                        f"{action_path}.id",
                        "Action id is required and must be a string",
                        "Use a stable action id such as log_created_incident.",
                    )
                )
            elif action_id in action_ids:
                errors.append(
                    _validation_error(
                        f"{action_path}.id",
                        "Action id must be unique within the flow",
                        "Give each action a different stable id.",
                    )
                )
            else:
                action_ids.add(action_id)

            action_type = action.get("type")
            if action_type not in SUPPORTED_ACTION_TYPES:
                errors.append(
                    _validation_error(
                        f"{action_path}.type",
                        "Unsupported action type",
                        f"Use one of: {', '.join(SUPPORTED_ACTION_TYPES)}.",
                    )
                )

            inputs = action.get("inputs")
            if not isinstance(inputs, dict):
                errors.append(
                    _validation_error(
                        f"{action_path}.inputs",
                        "Action inputs must be an object",
                        "Set inputs to the action input object expected by the Flow Server.",
                    )
                )
                continue

            errors.extend(_validate_data_pills(inputs, f"{action_path}.inputs"))

            if action_type == "core.log":
                for input_name in ("log_level", "log_message"):
                    if input_name not in inputs:
                        errors.append(
                            _validation_error(
                                f"{action_path}.inputs.{input_name}",
                                f"core.log input {input_name} is required",
                                "Use log_level and log_message like the sample request JSON.",
                            )
                        )
            elif action_type == "core.sendEmail":
                if "ah_body" in inputs and any(
                    DATA_PILL_PATTERN.search(text)
                    for _, text in _iter_data_pill_strings(
                        inputs["ah_body"],
                        f"{action_path}.inputs.ah_body",
                    )
                ):
                    warnings.append(
                        _validation_warning(
                            f"{action_path}.inputs.ah_body",
                            "SDK 4.7 rejects data pills in email body",
                            "Keep ah_body static; use data pills in ah_subject instead.",
                        )
                    )
                for input_name in (
                    "table_name",
                    "record",
                    "watermark_email",
                    "ah_to",
                    "ah_subject",
                    "ah_body",
                ):
                    if input_name not in inputs:
                        errors.append(
                            _validation_error(
                                f"{action_path}.inputs.{input_name}",
                                f"core.sendEmail input {input_name} is required",
                                "Add the required input like the sample email action JSON.",
                            )
                        )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _validate_deployment_request(body: Dict[str, Any]) -> Optional[str]:
    details = _validate_deployment_request_details(body)
    if details["valid"]:
        return None
    first_error = details["errors"][0]
    return f"{first_error['path']}: {first_error['message']}"


def _resolve_timeout_seconds(params: CreateFlowParams) -> float:
    """Resolve effective timeout in seconds from compatibility and canonical fields.

    Legacy ``timeout`` values >=1000 are treated as milliseconds.
    """
    if params.timeout is None or "timeout_seconds" in params.model_fields_set:
        return float(params.timeout_seconds)

    if params.timeout >= 1000:
        return params.timeout / 1000.0
    return float(params.timeout)


def _validation_hints() -> list[str]:
    return [
        "Send the Flow Server request fields at the top level.",
        "Do not wrap the body in deployment_request unless you need backward compatibility.",
        "Do not include MCP-only fields in the Flow Server body.",
        f"Supported trigger types: {', '.join(SUPPORTED_TRIGGER_TYPES)}.",
        f"Supported action types: {', '.join(SUPPORTED_ACTION_TYPES)}.",
        "Data pills must start with {{trigger.*}} or {{action.*}}.",
        "For deployment retry, call create_flow again; do not use workflow activation tools.",
    ]


def validate_flow_json(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateFlowJsonParams,
) -> Dict[str, Any]:
    """Validate Flow deployment payload shape and semantics without calling Flow Server.

    Workflow:
    1. Build payload from ``json_text``/``deployment_request`` and direct fields.
    2. Optionally merge runtime credentials from current server config.
    3. Run deep structural validation and data-pill checks.
    4. Return structured errors/warnings with remediation hints.
    """
    del auth_manager

    logger.info(
        "MCP_FLOW_VALIDATE_START input_fields=%s fill_runtime_credentials=%s",
        sorted(params.model_fields_set),
        params.fill_runtime_credentials,
    )
    body, build_error = _build_deployment_request(params)
    if not build_error and params.fill_runtime_credentials:
        body = _merge_runtime_credentials(config, body)

    password = str(body.get("password") or "")
    compact_request = _flow_response_request_body(body, password=password)

    if build_error:
        logger.warning("MCP_FLOW_VALIDATE_END valid=false build_error=%s", build_error)
        return {
            "valid": False,
            "errors": [
                _validation_error(
                    "json_text",
                    build_error,
                    (
                        "Provide a JSON object with the same top-level shape "
                        "as the sample request files."
                    ),
                )
            ],
            "warnings": [],
            "hints": _validation_hints(),
            "expected_request_shape": _request_shape_hint(),
        }

    validation = _validate_deployment_request_details(body)
    logger.info(
        "MCP_FLOW_VALIDATE_END valid=%s error_count=%s warning_count=%s summary=%s",
        validation["valid"],
        len(validation["errors"]),
        len(validation["warnings"]),
        _flow_request_log_summary(body),
    )
    return {
        "valid": validation["valid"],
        "errors": validation["errors"],
        "warnings": validation["warnings"],
        "hints": _validation_hints(),
        "request": compact_request,
        "clientGuidance": _flow_client_guidance(),
        "expected_request_shape": None if validation["valid"] else _request_shape_hint(),
    }


def hint_flow_json(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: HintFlowJsonParams,
) -> Dict[str, Any]:
    """Generate a validated starter Flow Server body for rapid authoring.

    Produces a deterministic flow skeleton with trigger, logging action, and
    optional email action so callers can edit and re-validate before deployment.
    """
    del auth_manager

    logger.info(
        "MCP_FLOW_HINT_START flow_name=%s table=%s trigger_type=%s include_email_action=%s",
        params.flow_name,
        params.table,
        params.trigger_type,
        params.include_email_action,
    )
    username, _password = _current_basic_credentials(config)
    instance_url = (
        config.instance_url
        if config.instance_url != "https://example.invalid"
        else "https://your-instance.service-now.com"
    )
    flow_id = params.flow_id or _flow_spec_id(params.flow_name)
    actions: list[Dict[str, Any]] = [
        {
            "id": "log_flow_event",
            "type": "core.log",
            "inputs": {
                "log_level": "info",
                "log_message": {"template": f"{params.flow_name}: {{{{trigger.current.number}}}}"},
            },
        }
    ]

    if params.include_email_action:
        actions.append(
            {
                "id": "send_email_notification",
                "type": "core.sendEmail",
                "inputs": {
                    "table_name": params.table,
                    "record": "{{trigger.current.sys_id}}",
                    "watermark_email": True,
                    "ah_to": params.recipient_email,
                    "ah_subject": {
                        "template": f"{params.flow_name}: {{{{trigger.current.number}}}}"
                    },
                    "ah_body": {"template": "Open the ServiceNow record for details."},
                },
            }
        )

    hinted_body = {
        "instanceUrl": instance_url,
        "username": username or "admin",
        "password": "***REDACTED***",
        "scopeId": params.scopeId,
        "scopeName": params.scopeName,
        "reinstall": params.reinstall,
        "flowSpec": {
            "id": flow_id,
            "name": params.flow_name,
            "description": params.description,
            "runAs": params.runAs,
            "trigger": {
                "type": params.trigger_type,
                "table": params.table,
                "condition": "",
            },
            "actions": actions,
        },
    }
    logger.info("MCP_FLOW_HINT_END summary=%s", _flow_request_log_summary(hinted_body))
    return hinted_body


def create_incident_admin_notification_flow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateIncidentAdminNotificationFlowParams,
) -> Dict[str, Any]:
    """Deploy a one-call default incident-created admin notification flow.

    This convenience tool builds a known-good flow spec and forwards it through
    ``create_flow`` so the same deployment, validation, and diagnostics path is
    reused.
    """
    logger.info(
        "MCP_FLOW_CREATE_INCIDENT_ADMIN_START flow_name=%s scopeId=%s scopeName=%s",
        params.flow_name,
        params.scopeId,
        params.scopeName,
    )
    body = _incident_admin_notification_body(config, params)
    create_params = CreateFlowParams(
        deployment_request=body,
        flow_server_url=params.flow_server_url,
        timeout_seconds=params.timeout_seconds,
        timeout=params.timeout,
        include_server_logs=params.include_server_logs,
    )
    result = create_flow(config, auth_manager, create_params)
    if isinstance(result, dict):
        result["autonomousDefaults"] = {
            "scopeId": params.scopeId or DEFAULT_SCOPE_ID,
            "scopeName": params.scopeName or DEFAULT_SCOPE_NAME,
            "recipient_email": params.recipient_email or DEFAULT_ADMIN_EMAIL,
            "trigger_table": "incident",
            "trigger_type": "record.created",
            "post_deployment_testing": "not attempted",
        }
        guidance = result.setdefault("clientGuidance", [])
        if isinstance(guidance, list):
            guidance.append(
                (
                    "This one-call tool already chose defaults. Do not ask the user "
                    "for scope or email unless they explicitly want different values."
                )
            )
    return result


def create_flow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateFlowParams,
) -> Dict[str, Any]:
    """Deploy a Flow Designer flow through the local Flow Server endpoint.

    Pipeline:
    1. Build and normalize request payload from caller inputs.
    2. Merge runtime credentials/scope defaults when missing.
    3. Validate payload locally before external call.
    4. Call ``/deploy-flow`` and normalize response diagnostics.
    5. Return redacted request summary + bounded server return data.
    """
    del auth_manager  # Runtime credentials are read from the ServerConfig auth model.

    started_at = time.monotonic()
    logger.info(
        "MCP_FLOW_CREATE_START input_fields=%s include_server_logs=%s",
        sorted(params.model_fields_set),
        params.include_server_logs,
    )
    body, build_error = _build_deployment_request(params)
    if build_error:
        logger.warning("MCP_FLOW_CREATE_ABORT reason=json_parse_error error=%s", build_error)
        return {
            "success": False,
            "message": (
                "Flow deployment was not attempted because the supplied JSON text "
                "could not be parsed."
            ),
            "error": build_error,
            "hints": _validation_hints(),
            "rethinkJson": _rethink_json_payload("json_text could not be parsed or repaired"),
            "expected_request_shape": _request_shape_hint(),
        }

    body = _merge_runtime_credentials(config, body)
    validation_details = _validate_deployment_request_details(body)
    password = str(body.get("password") or "")
    compact_request = _flow_response_request_body(body, password=password)
    request_summary = _flow_request_log_summary(body)
    logger.info("MCP_FLOW_CREATE_REQUEST_READY summary=%s", request_summary)

    if not validation_details["valid"]:
        first_error = validation_details["errors"][0]
        logger.warning(
            "MCP_FLOW_CREATE_ABORT reason=validation_failed error_count=%s errors=%s summary=%s",
            len(validation_details["errors"]),
            [f"{error['path']}: {error['message']}" for error in validation_details["errors"]],
            request_summary,
        )
        return {
            "success": False,
            "message": (
                "Flow deployment was not attempted. Ask the client to provide a "
                "top-level JSON body like the example Flow Server request files."
            ),
            "error": f"{first_error['path']}: {first_error['message']}",
            "validation": validation_details,
            "hints": _validation_hints(),
            "rethinkJson": _rethink_json_payload(
                "local JSON validation failed before calling the Flow Server",
                body=body,
                validation=validation_details,
            ),
            "expected_request_shape": _request_shape_hint(),
            "request": compact_request,
            "clientGuidance": _flow_client_guidance(),
        }

    flow_server_url = get_flow_server_url(params.flow_server_url)
    endpoint = f"{flow_server_url}/deploy-flow"
    timeout_seconds = _resolve_timeout_seconds(params)
    logger.info(
        "MCP_FLOW_DEPLOY_START endpoint=%s timeout_seconds=%s summary=%s",
        endpoint,
        timeout_seconds,
        request_summary,
    )

    try:
        response = requests.post(
            endpoint,
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        result = _parse_response(response, password=password)
        success = 200 <= response.status_code < 300
        server_response = _compact_server_result(
            result,
            include_logs=params.include_server_logs,
        )
        server_return_data = (
            _bounded_server_return_data(
                result,
                include_logs=params.include_server_logs,
            )
            if params.include_server_logs
            else server_response
        )
        server_error = _extract_flow_server_error(result)
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "MCP_FLOW_DEPLOY_END success=%s status_code=%s elapsed_ms=%s "
            "server_response_keys=%s omitted_keys=%s",
            success,
            response.status_code,
            elapsed_ms,
            sorted(server_response.keys()),
            server_response.get("omitted_keys", []),
        )
        deployment_result = {
            "success": success,
            "message": (
                "Flow deployment request completed" if success else "Flow deployment request failed"
            ),
            "status_code": response.status_code,
            "flow_server_endpoint": endpoint,
            "server_response": "See serverReturnData.",
            "serverReturnData": server_return_data,
            "serverReturnDataMode": (
                "bounded_with_log_previews" if params.include_server_logs else "compact"
            ),
        }
        if server_error:
            deployment_result["serverError"] = server_error
        if not success:
            deployment_result["rethinkJson"] = _rethink_json_payload(
                "Flow Server returned a non-success HTTP status",
                body=body,
                validation=validation_details,
                server_return_data=server_return_data,
                server_error=server_error,
            )
        return _flow_response_body(body, deployment_result, password=password)
    except requests.RequestException as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.exception(
            "MCP_FLOW_DEPLOY_ERROR endpoint=%s elapsed_ms=%s error=%s summary=%s",
            endpoint,
            elapsed_ms,
            exc,
            request_summary,
        )
        deployment_result = {
            "success": False,
            "message": "Error calling the generic flow server /deploy-flow endpoint",
            "flow_server_endpoint": endpoint,
            "error": _redact_text(str(exc), password=password),
            "serverReturnData": None,
            "rethinkJson": _rethink_json_payload(
                (
                    "The Flow Server could not be reached or the request failed "
                    "before a response was returned"
                ),
                body=body,
                validation=validation_details,
            ),
        }
        return _flow_response_body(body, deployment_result, password=password)
