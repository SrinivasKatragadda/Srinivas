"""Flow Designer deployment tools backed by the local generic flow server.

This module translates Flow Designer JSON definitions into deployment actions
against the local generic flow server. It includes validation, hinting, source
generation, deployment, and verification helpers for flows and subflows.

The module is intentionally security-conscious: it sanitizes sensitive values,
compacts large responses, and normalizes instance URLs before invoking the
flow server.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from copy import deepcopy
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, ConfigDict, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.tools.flow_designer.config import get_flow_server_url
from servicenow_mcp.utils.config import (
    AuthType,
    ServerConfig,
    normalize_servicenow_instance_url,
)

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
MAX_FLOW_ACTION_SUMMARIES = 6
MAX_FLOW_VALIDATION_ITEMS = 8
AVAILABLE_ACTIONS_TIMEOUT_SECONDS = 2
SUPPORTED_TRIGGER_TYPES = ("record.created", "record.updated", "record.created_or_updated")
FALLBACK_ACTION_TYPES = (
    "core.log",
    "core.sendEmail",
    "core.updateRecord",
    "core.lookUpRecord",
    "core.waitForCondition",
)
SUPPORTED_FLOW_LOGIC_TYPES = (
    "flowLogic.if",
    "flowLogic.forEach",
    "flowLogic.doUntil",
    "flowLogic.parallel",
    "flowLogic.wait",
    "flowLogic.decision",
    "flowLogic.setFlowVariables",
    "flowLogic.endFlow",
    "flowLogic.try",
    "flowLogic.exitLoop",
    "flowLogic.skipIteration",
    "flowLogic.goBack",
    "flowLogic.callWorkflow",
    "flowLogic.assignSubflowOutputs",
    "flowLogic.dynamicFlow",
    "flowLogic.getFlowOutputs",
)
FLOW_LOGIC_LABELS = {
    "flowLogic.if": "If",
    "flowLogic.forEach": "For Each",
    "flowLogic.doUntil": "Do the following until",
    "flowLogic.parallel": "Do the following in parallel",
    "flowLogic.wait": "Wait for a duration of time",
    "flowLogic.decision": "Make a decision",
    "flowLogic.setFlowVariables": "Set Flow Variables",
    "flowLogic.endFlow": "End Flow",
    "flowLogic.try": "Try",
    "flowLogic.exitLoop": "Exit Loop",
    "flowLogic.skipIteration": "Skip Iteration",
    "flowLogic.goBack": "Go back",
    "flowLogic.callWorkflow": "Call a Workflow",
    "flowLogic.assignSubflowOutputs": "Assign Subflow Outputs",
    "flowLogic.dynamicFlow": "Dynamic Flow",
    "flowLogic.getFlowOutputs": "Get Flow Outputs",
}
FLOW_LOGIC_DESCRIPTIONS = {
    "flowLogic.if": "Run nested actions only when conditions match; supports branches.",
    "flowLogic.forEach": "Loop through a list of records/items and run nested actions.",
    "flowLogic.doUntil": "Repeat nested actions until an end condition is met.",
    "flowLogic.parallel": "Run multiple nested branches in parallel.",
    "flowLogic.wait": "Pause for a fixed duration, relative time, or date/time.",
    "flowLogic.decision": "Route execution through a decision table.",
    "flowLogic.setFlowVariables": "Set flow variables for later data-pill reuse.",
    "flowLogic.endFlow": "Stop the current flow.",
    "flowLogic.try": "Run protected actions with optional catch/finally handling.",
    "flowLogic.exitLoop": "Break out of the nearest loop.",
    "flowLogic.skipIteration": "Skip the current loop item and continue with the next one.",
    "flowLogic.goBack": "Jump back to a previous step.",
    "flowLogic.callWorkflow": "Run a legacy published Workflow.",
    "flowLogic.assignSubflowOutputs": "Assign output values returned by a subflow.",
    "flowLogic.dynamicFlow": "Choose and run a flow or subflow at runtime.",
    "flowLogic.getFlowOutputs": "Read output values from dynamic flow execution.",
}
FLOW_LOGIC_TYPE_ALIASES = {
    "if": "flowLogic.if",
    "elseif": "flowLogic.if",
    "else": "flowLogic.if",
    "flowlogicif": "flowLogic.if",
    "foreach": "flowLogic.forEach",
    "for each": "flowLogic.forEach",
    "for_each": "flowLogic.forEach",
    "flowlogicforeach": "flowLogic.forEach",
    "dothefollowinguntil": "flowLogic.doUntil",
    "dountil": "flowLogic.doUntil",
    "until": "flowLogic.doUntil",
    "flowlogicdountil": "flowLogic.doUntil",
    "dothefollowinginparallel": "flowLogic.parallel",
    "parallel": "flowLogic.parallel",
    "flowlogicparallel": "flowLogic.parallel",
    "waitforadurationoftime": "flowLogic.wait",
    "wait": "flowLogic.wait",
    "waitduration": "flowLogic.wait",
    "flowlogicwait": "flowLogic.wait",
    "makeadecision": "flowLogic.decision",
    "decision": "flowLogic.decision",
    "flowlogicdecision": "flowLogic.decision",
    "setflowvariables": "flowLogic.setFlowVariables",
    "setvariables": "flowLogic.setFlowVariables",
    "flowlogicsetflowvariables": "flowLogic.setFlowVariables",
    "endflow": "flowLogic.endFlow",
    "stopflow": "flowLogic.endFlow",
    "flowlogicendflow": "flowLogic.endFlow",
    "try": "flowLogic.try",
    "trycatch": "flowLogic.try",
    "flowlogictry": "flowLogic.try",
    "exitloop": "flowLogic.exitLoop",
    "break": "flowLogic.exitLoop",
    "flowlogicexitloop": "flowLogic.exitLoop",
    "skipiteration": "flowLogic.skipIteration",
    "continue": "flowLogic.skipIteration",
    "flowlogicskipiteration": "flowLogic.skipIteration",
    "goback": "flowLogic.goBack",
    "goto": "flowLogic.goBack",
    "flowlogicgoback": "flowLogic.goBack",
    "callaworkflow": "flowLogic.callWorkflow",
    "callworkflow": "flowLogic.callWorkflow",
    "workflow": "flowLogic.callWorkflow",
    "flowlogiccallworkflow": "flowLogic.callWorkflow",
    "assignsubflowoutputs": "flowLogic.assignSubflowOutputs",
    "assignoutputs": "flowLogic.assignSubflowOutputs",
    "flowlogicassignsubflowoutputs": "flowLogic.assignSubflowOutputs",
    "dynamicflow": "flowLogic.dynamicFlow",
    "runsubflowdynamic": "flowLogic.dynamicFlow",
    "flowlogicdynamicflow": "flowLogic.dynamicFlow",
    "getflowoutputs": "flowLogic.getFlowOutputs",
    "flowoutputs": "flowLogic.getFlowOutputs",
    "flowlogicgetflowoutputs": "flowLogic.getFlowOutputs",
}
FLOW_LOGIC_BLOCK_FIELDS = (
    "actions",
    "thenActions",
    "elseActions",
    "tryActions",
    "catchActions",
    "finallyActions",
)
FLOW_LOGIC_BRANCH_FIELDS = (
    "branches",
    "elseIf",
    "parallelBranches",
    "decisionBranches",
)
FLOW_LOGIC_BRANCH_ACTION_FIELDS = ("actions", "thenActions")
FLOW_LOGIC_LOOP_TYPES = ("flowLogic.forEach", "flowLogic.doUntil")
DEFAULT_SCOPE_ID = "global"
DEFAULT_SCOPE_NAME = "Global"
DEFAULT_STATIC_EMAIL_BODY = (
    "Incident details have been retrieved and logged. Please check the incident "
    "system for complete information."
)
SUPPORTED_RUN_AS = ("system", "user")
FLOW_SPEC_REQUIRED_FIELD_NAMES = ("id", "name", "runAs", "trigger", "actions")
SCOPE_ID_PLACEHOLDER = "<application-scope-id-or-name>"
SCOPE_NAME_PLACEHOLDER = "<application-scope-display-name>"
RECIPIENT_EMAIL_PLACEHOLDER = "<recipient-email>"
USERNAME_PLACEHOLDER = "<username>"
PLACEHOLDER_INSTANCE_URLS = (
    "https://your-instance.service-now.com",
    "http://your-instance.service-now.com",
    "your-instance.service-now.com",
    "https://example.invalid",
    "example.invalid",
)
PLACEHOLDER_USERNAMES = (USERNAME_PLACEHOLDER,)
PLACEHOLDER_SECRETS = ("<password>",)
PLACEHOLDER_SCOPE_VALUES = (
    SCOPE_ID_PLACEHOLDER,
    SCOPE_NAME_PLACEHOLDER,
    "<scopeId>",
    "<scopeName>",
)
PLACEHOLDER_RECIPIENT_VALUES = (
    RECIPIENT_EMAIL_PLACEHOLDER,
    "<email>",
    "<recipient>",
)
SUMMARY_SCALAR_KEYS = (
    "success",
    "ok",
    "status",
    "msg",
    "reqId",
    "message",
    "flowName",
    "flowSysId",
    "subflowName",
    "subflowSysId",
    "sys_id",
    "sysId",
    "verified",
    "flowDesignerUrl",
)
DATA_PILL_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
FULL_DATA_PILL_PATTERN = re.compile(r"^\{\{\s*([^}]+?)\s*\}\}$")
SUPPORTED_DATA_PILL_ROOTS = ("trigger", "action")
FLOW_DEPLOYMENT_FIELD_NAMES = (
    "instanceUrl",
    "username",
    "password",
    "scopeId",
    "scopeName",
    "reinstall",
    "skipInstallFallback",
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
SUBFLOW_SPEC_REQUIRED_FIELD_NAMES = ("id", "name", "inputs", "outputs", "actions")
SUPPORTED_SUBFLOW_IO_TYPES = ("string", "integer", "boolean")
SUBFLOW_DEPLOYMENT_FIELD_NAMES = (
    "instanceUrl",
    "username",
    "password",
    "scopeId",
    "scopeName",
    "reinstall",
    "subflowSpec",
)
SUBFLOW_VERIFY_FIELD_NAMES = (
    "instanceUrl",
    "username",
    "password",
    "scopeId",
    "scopeName",
    "flowName",
    "subflowName",
    "name",
)
SUBFLOW_DATA_PILL_ROOTS = ("input", "inputs", "action")
SUBFLOW_IO_FIELD_NAMES = ("type", "label", "mandatory")


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
    ah_body: str = Field(description="Static email body string. SDK 4.7 rejects data pills here.")


class FlowAction(BaseModel):
    """Generic Flow Server action or Flow Designer logic block."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable action or logic-block id, unique within the flow.")
    type: str = Field(
        description=(
            "Flow Server action type from /available-actions or a supported Flow Designer "
            "logic type such as flowLogic.if, flowLogic.forEach, flowLogic.parallel, "
            "or flowLogic.wait. Friendly labels such as 'If' are normalized."
        )
    )
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Flow Server action input object or Flow Designer logic configuration.",
    )
    condition: Optional[Any] = Field(
        None,
        description=(
            "Optional condition used by flowLogic.if, flowLogic.doUntil, and similar logic."
        ),
    )
    actions: Optional[list["FlowAction"]] = Field(
        None,
        description="Nested actions for block-style logic such as If, For Each, Try, or Do Until.",
    )
    branches: Optional[list[Dict[str, Any]]] = Field(
        None,
        description=(
            "Branch objects for If / Else If / Else, Parallel, or Decision logic. Each branch "
            "may include label/name, condition, and actions."
        ),
    )


class FlowSpec(BaseModel):
    """Generic flow specification passed to the Flow Server."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable lowercase flow id.")
    name: str = Field(description="Flow Designer flow name.")
    description: Optional[str] = Field(None, description="Optional flow description.")
    runAs: Literal["system", "user"] = Field(description="Execution identity for the flow.")
    trigger: FlowTrigger = Field(description="Record trigger configuration.")
    actions: list[FlowAction] = Field(description="Ordered Flow Server actions.")


class SubflowIoField(BaseModel):
    """Subflow input/output variable definition."""

    model_config = ConfigDict(extra="allow")

    type: Literal["string", "integer", "boolean"] = Field(
        description="Subflow variable type supported by the SDK generator."
    )
    label: str = Field(description="Human-readable label shown in Flow Designer.")
    mandatory: bool = Field(
        False,
        description="Whether the subflow input/output is mandatory.",
    )


class SubflowSpec(BaseModel):
    """Generic subflow specification passed to the Flow Server."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Stable lowercase subflow id.")
    name: str = Field(description="Flow Designer subflow name.")
    description: Optional[str] = Field(None, description="Optional subflow description.")
    runAs: Optional[Literal["system", "user"]] = Field(
        None,
        description=(
            "Optional execution identity for the subflow. The SDK example omits this "
            "field, so MCP does not require it."
        ),
    )
    inputs: Dict[str, SubflowIoField] = Field(
        default_factory=dict,
        description="Map of subflow input variable names to input definitions.",
    )
    outputs: Dict[str, SubflowIoField] = Field(
        default_factory=dict,
        description=("Map of subflow output variable names to output definitions."),
    )
    outputValues: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional map of declared output names to values assigned at the end of "
            "the subflow with wfa.flowLogic.assignSubflowOutputs."
        ),
    )
    actions: list[FlowAction] = Field(
        description=(
            "Ordered Flow Server actions. Subflow data pills may reference declared "
            "inputs as {{input.name}} or earlier actions as {{action.action_id.field}}; "
            "subflows do not have trigger data pills."
        )
    )


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
        description=(
            "ServiceNow application scope technical name, sys_scope sys_id, or global. "
            "If omitted or left as a placeholder, MCP normalizes this to 'global'."
        ),
    )
    scopeName: Optional[str] = Field(
        None,
        description=(
            "ServiceNow application scope display name. "
            "MCP derives this from the flow/subflow id when available and formats it with the "
            "same lowercase underscore sanitizer."
        ),
    )
    reinstall: Optional[bool] = Field(
        None,
        description=(
            "Whether the Flow Server should run the SDK install with reinstall=true. "
            "Use false for normal updates."
        ),
    )
    skipInstallFallback: Optional[bool] = Field(
        None,
        description=(
            "Optional advanced Flow Server deployment flag. Omit unless the caller "
            "explicitly needs to disable the install fallback path."
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
            "in servicenow_mcp.tools.flow_designer.config."
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


class FlowActionUpdate(BaseModel):
    """Patch one action in an existing Flow Server flowSpec."""

    id: str = Field(description="Current action id to edit.")
    new_id: Optional[str] = Field(None, description="Optional replacement action id.")
    type: Optional[str] = Field(None, description="Optional replacement action or logic type.")
    inputs: Optional[Dict[str, Any]] = Field(
        None,
        description="Input values to merge into the action inputs or replace when requested.",
    )
    replace_inputs: bool = Field(
        False,
        description="When true, replace the whole inputs object instead of merging.",
    )
    patch: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional top-level action fields to merge into the action.",
    )


class EditFlowParams(CreateFlowParams):
    """Parameters for editing an existing Flow Designer Flow Server JSON body."""

    flow_name: Optional[str] = Field(None, description="Replacement flowSpec.name.")
    description: Optional[str] = Field(None, description="Replacement flowSpec.description.")
    runAs: Optional[Literal["system", "user"]] = Field(None, description="Replacement runAs.")
    trigger_type: Optional[str] = Field(None, description="Replacement flowSpec.trigger.type.")
    trigger_table: Optional[str] = Field(None, description="Replacement flowSpec.trigger.table.")
    trigger_condition: Optional[str] = Field(
        None,
        description="Replacement flowSpec.trigger.condition.",
    )
    set_values: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dotted JSON paths to set after other edits, such as "
            "flowSpec.trigger.condition or flowSpec.actions[0].inputs.log_level."
        ),
    )
    remove_action_ids: list[str] = Field(
        default_factory=list,
        description="Action ids to remove from the flow action tree.",
    )
    update_actions: list[FlowActionUpdate] = Field(
        default_factory=list,
        description="Action patches matched by action id. Nested flow-logic actions are searched.",
    )
    append_actions: list[Dict[str, Any]] = Field(
        default_factory=list,
        description="Actions to append to flowSpec.actions after removals and updates.",
    )
    replace_actions: Optional[list[Dict[str, Any]]] = Field(
        None,
        description="Replace flowSpec.actions completely. Use sparingly for full action rewrites.",
    )
    deploy: bool = Field(
        False,
        description="When true and dry_run=false, redeploy the edited flow via /deploy-flow.",
    )
    dry_run: bool = Field(
        True,
        description="When true, validate and preview edits without calling /deploy-flow.",
    )
    include_full_request: bool = Field(
        False,
        description="When true, return the full redacted edited request instead of a compact view.",
    )


class ValidateFlowJsonParams(BaseModel):
    """Parameters for validating a Flow Server deployment JSON body."""

    instanceUrl: Optional[str] = Field(None, description="ServiceNow instance URL.")
    username: Optional[str] = Field(None, description="ServiceNow username.")
    password: Optional[str] = Field(None, description="ServiceNow password.")
    scopeId: Optional[str] = Field(
        None,
        description=(
            "Application scope id/name/sys_id/global. If omitted or placeholder, MCP defaults to "
            "'global'."
        ),
    )
    scopeName: Optional[str] = Field(
        None,
        description=(
            "Application scope display name. MCP derives it from the spec id when available and "
            "formats it like a flow/subflow id."
        ),
    )
    reinstall: Optional[bool] = Field(None, description="Whether SDK reinstall should be used.")
    skipInstallFallback: Optional[bool] = Field(
        None,
        description="Optional advanced Flow Server deployment flag; omit unless explicitly needed.",
    )
    flowSpec: Optional[FlowSpec] = Field(None, description="Generic flow specification.")
    deployment_request: Optional[Dict[str, Any]] = Field(
        None,
        description="Backward-compatible wrapper for the full Flow Server request JSON body.",
    )
    json_text: Optional[str] = Field(
        None,
        description="Optional raw JSON text to parse and validate.",
    )
    flow_server_url: Optional[str] = Field(
        None,
        description=(
            "Optional Flow Server base URL override used to read /available-actions "
            "while validating action types."
        ),
    )
    fill_runtime_credentials: bool = Field(
        True,
        description=(
            "When true, missing instanceUrl, username, and password are filled from "
            "the current MCP basic-auth runtime config before validation."
        ),
    )


class ListFlowActionsParams(BaseModel):
    """Parameters for listing Flow Server SDK actions."""

    flow_server_url: Optional[str] = Field(
        None,
        description="Optional Flow Server base URL override.",
    )
    include_snippets: bool = Field(
        False,
        description=(
            "When true, include bounded action snippets. WARNING: setting this to true for many "
            "actions will crash the context limit! "
            "When false, the response STILL includes 'snippet_input_keys' for each action, which "
            "is usually all you need to know the input names."
        ),
    )
    type_filter: Optional[str] = Field(
        None,
        description=(
            "Optional case-insensitive substring filter for action type. REQUIRED if include_"
            "snippets is true."
        ),
    )
    limit: int = Field(
        50,
        ge=1,
        le=200,
        description="Maximum number of action summaries to return.",
    )
    offset: int = Field(0, ge=0, description="Offset to start from.")


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
    trigger_condition: str = Field(
        "",
        description=(
            "Optional ServiceNow encoded query for trigger-level flow logic, for example "
            "'priority=1^active=true'. Simple trigger data-pill expressions such as "
            "'{{trigger.current.priority}} == 1' can be normalized by the Flow Server."
        ),
    )
    app_name: str = Field(
        "Test App",
        description=(
            "Application name kept for backward compatibility; global scope is used unless "
            "scopeId/scopeName are provided."
        ),
    )
    scopeId: Optional[str] = Field(
        None,
        description=("Application scope id/name/sys_id/global. If omitted, defaults to global."),
    )
    scopeName: Optional[str] = Field(
        None,
        description=("Application scope display name. MCP derives/formats it from the flow id."),
    )
    runAs: str = Field("system", description="Run as system or user.")
    reinstall: bool = Field(False, description="Whether SDK reinstall should be used.")
    include_email_action: bool = Field(
        True,
        description="Include a core.sendEmail action after the core.log action.",
    )
    recipient_email: Optional[str] = Field(
        None,
        description=(
            "Recipient used when include_email_action is true. Provide the target "
            "email explicitly; no admin email is assumed."
        ),
    )
    include_full_json: bool = Field(
        False,
        description=(
            "When true, include the complete generated request JSON. Defaults to false "
            "so MCP clients receive a compact template summary."
        ),
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
        ...,
        description="Explicit notification recipient email address.",
    )
    scopeId: str = Field(
        DEFAULT_SCOPE_ID,
        description="Application scope id/name/sys_id/global. Defaults to global.",
    )
    scopeName: str = Field(
        DEFAULT_SCOPE_NAME,
        description="Application scope display name. MCP derives/formats it from the flow id.",
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


class ValidateSubflowJsonParams(BaseModel):
    """Parameters for validating a Flow Server subflow deployment JSON body."""

    instanceUrl: Optional[str] = Field(None, description="ServiceNow instance URL.")
    username: Optional[str] = Field(None, description="ServiceNow username.")
    password: Optional[str] = Field(None, description="ServiceNow password.")
    scopeId: Optional[str] = Field(
        None,
        description=(
            "Application scope id/name/sys_id/global. If omitted or placeholder, MCP defaults to "
            "'global'."
        ),
    )
    scopeName: Optional[str] = Field(
        None,
        description=(
            "Application scope display name. MCP derives it from subflowSpec.id when available "
            "and formats it like a subflow id."
        ),
    )
    reinstall: Optional[bool] = Field(None, description="Whether SDK reinstall should be used.")
    subflowSpec: Optional[SubflowSpec] = Field(None, description="Generic subflow specification.")
    deployment_request: Optional[Dict[str, Any]] = Field(
        None,
        description="Backward-compatible wrapper for the full Subflow deployment body.",
    )
    json_text: Optional[str] = Field(None, description="Optional raw JSON text to validate.")
    flow_server_url: Optional[str] = Field(
        None,
        description="Optional Flow Server base URL used to read /available-actions.",
    )
    fill_runtime_credentials: bool = Field(
        True,
        description=(
            "When true, missing instanceUrl, username, and password are filled from "
            "the current MCP basic-auth runtime config before validation."
        ),
    )


class HintSubflowJsonParams(BaseModel):
    """Parameters for generating a ready-to-fill Subflow deployment body."""

    subflow_name: str = Field("My Subflow", description="Flow Designer subflow name.")
    subflow_id: Optional[str] = Field(
        None,
        description="Stable subflow id. If omitted, one is generated from subflow_name.",
    )
    description: str = Field(
        "Optional description for the subflow",
        description="Subflow description.",
    )
    app_name: str = Field(
        "Test App",
        description=(
            "Application name kept for backward compatibility; global scope is used unless "
            "scopeId/scopeName are provided."
        ),
    )
    scopeId: Optional[str] = Field(
        None,
        description=("Application scope id/name/sys_id/global. If omitted, defaults to global."),
    )
    scopeName: Optional[str] = Field(
        None,
        description=("Application scope display name. MCP derives/formats it from the subflow id."),
    )
    runAs: Optional[str] = Field(
        None,
        description=("Optional run identity. Omit it to match the SDK example payload."),
    )
    reinstall: bool = Field(False, description="Whether SDK reinstall should be used.")
    include_full_json: bool = Field(
        False,
        description=(
            "When true, include the complete generated request JSON. Defaults to false "
            "so MCP clients receive a compact template summary."
        ),
    )


class GenerateSubflowSourceParams(BaseModel):
    """Parameters for generating TypeScript source for a Subflow."""

    subflowSpec: Optional[SubflowSpec] = Field(None, description="Generic subflow specification.")
    generation_request: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Optional wrapper for a raw /generate-subflow-source request. If it contains "
            "subflowSpec, the inner subflowSpec is sent to the Flow Server endpoint."
        ),
    )
    json_text: Optional[str] = Field(None, description="Optional raw Subflow JSON text.")
    flow_server_url: Optional[str] = Field(None, description="Optional Flow Server base URL.")
    timeout_seconds: int = Field(
        120,
        gt=0,
        description="Timeout for the /generate-subflow-source call.",
    )
    timeout: Optional[int] = Field(
        None,
        gt=0,
        description="Compatibility timeout. Values >=1000 are treated as milliseconds.",
    )
    include_source: bool = Field(
        False,
        description=(
            "When true, include generated TypeScript source. Defaults to false and "
            "returns a bounded source preview to keep MCP context compact."
        ),
    )


class DeploySubflowParams(BaseModel):
    """Parameters for deploying a Flow Designer Subflow through the TS flow server."""

    instanceUrl: Optional[str] = Field(None, description="ServiceNow instance URL.")
    username: Optional[str] = Field(None, description="ServiceNow username.")
    password: Optional[str] = Field(None, description="ServiceNow password.")
    scopeId: Optional[str] = Field(
        None,
        description=(
            "Application scope id/name/sys_id/global. If omitted or placeholder, MCP defaults to "
            "'global'."
        ),
    )
    scopeName: Optional[str] = Field(
        None,
        description=(
            "Application scope display name. MCP derives it from subflowSpec.id when available "
            "and formats it like a subflow id."
        ),
    )
    reinstall: Optional[bool] = Field(None, description="Whether SDK reinstall should be used.")
    subflowSpec: Optional[SubflowSpec] = Field(None, description="Generic subflow specification.")
    deployment_request: Optional[Dict[str, Any]] = Field(
        None,
        description="Backward-compatible wrapper for the full /deploy-subflow JSON body.",
    )
    json_text: Optional[str] = Field(None, description="Optional raw Subflow JSON text.")
    flow_server_url: Optional[str] = Field(None, description="Optional Flow Server base URL.")
    timeout_seconds: int = Field(
        600,
        gt=0,
        description="Timeout for the /deploy-subflow call.",
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


class VerifySubflowParams(BaseModel):
    """Parameters for verifying that a Subflow exists in ServiceNow."""

    flowName: Optional[str] = Field(
        None,
        description=(
            "Flow Server verification field for the Subflow display name. "
            "subflow_name/subflowName are accepted aliases."
        ),
    )
    subflow_name: Optional[str] = Field(
        None,
        description="Subflow display name to verify. Sent as flowName.",
    )
    subflowName: Optional[str] = Field(None, description="Subflow display name.")
    name: Optional[str] = Field(None, description="Compatibility subflow name field.")
    instanceUrl: Optional[str] = Field(None, description="ServiceNow instance URL.")
    username: Optional[str] = Field(None, description="ServiceNow username.")
    password: Optional[str] = Field(None, description="ServiceNow password.")
    scopeId: Optional[str] = Field(
        None,
        description=(
            "Application scope id/name/sys_id/global. If omitted or placeholder, MCP defaults to "
            "'global'."
        ),
    )
    scopeName: Optional[str] = Field(
        None, description="Application scope display name. MCP formats it like the subflow name/id."
    )
    verify_request: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional wrapper for the full /verify-subflow JSON body.",
    )
    json_text: Optional[str] = Field(None, description="Optional raw verification JSON text.")
    flow_server_url: Optional[str] = Field(None, description="Optional Flow Server base URL.")
    timeout_seconds: int = Field(120, gt=0, description="Timeout for the /verify-subflow call.")
    timeout: Optional[int] = Field(
        None,
        gt=0,
        description="Compatibility timeout. Values >=1000 are treated as milliseconds.",
    )


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower()
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def _redact_string(value: str, secrets: tuple[str, ...] = ()) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


def _redact_sensitive_values(value: Any, secrets: tuple[str, ...] = ()) -> Any:
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
    redacted = text
    if password:
        redacted = redacted.replace(password, "***REDACTED***")
    return re.sub(
        r'(?i)("?(?:password|pwd|secret|token|authorization)"?\s*[:=]\s*")([^"]*)(")',
        r"\1***REDACTED***\3",
        redacted,
    )


def _parse_response(response: requests.Response, password: Optional[str]) -> Any:
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
    return isinstance(value, (str, int, float, bool)) or value is None


def _truncate_text(value: Any, limit: int = 500) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _is_large_response_key(key: Any) -> bool:
    normalized = str(key).lower()
    return any(marker.lower() in normalized for marker in LARGE_RESPONSE_KEYS)


def _bounded_log_preview(value: Any, limit: int = 5) -> Dict[str, Any]:
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


def _normalize_action_entry(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, str):
        return {"type": item}
    if not isinstance(item, dict):
        return None

    action_type = item.get("type") or item.get("name") or item.get("actionType")
    if not isinstance(action_type, str) or not action_type.strip():
        return None

    entry = {
        "type": action_type.strip(),
        "description": item.get("description"),
    }
    if isinstance(item.get("snippet"), dict):
        entry["snippet"] = item["snippet"]
    return entry


def _extract_action_entries(payload: Any) -> list[Dict[str, Any]]:
    raw_actions: Any = payload
    if isinstance(payload, dict):
        raw_actions = (
            payload.get("actions") or payload.get("availableActions") or payload.get("result") or []
        )

    if not isinstance(raw_actions, list):
        return []

    entries = []
    seen = set()
    for item in raw_actions:
        entry = _normalize_action_entry(item)
        if not entry:
            continue
        action_type = entry["type"]
        if action_type in seen:
            continue
        seen.add(action_type)
        entries.append(entry)
    return sorted(entries, key=lambda action: action["type"].lower())


def _fallback_action_entries() -> list[Dict[str, Any]]:
    return [
        {
            "type": action_type,
            "description": "Fallback action type used when /available-actions is unavailable.",
        }
        for action_type in FALLBACK_ACTION_TYPES
    ]


def _logic_snippet_log_action(action_id: str, message: str) -> Dict[str, Any]:
    return {
        "id": action_id,
        "type": "core.log",
        "inputs": {
            "log_level": "info",
            "log_message": message,
        },
    }


def _flow_logic_entries() -> list[Dict[str, Any]]:
    snippets: Dict[str, Dict[str, Any]] = {
        "flowLogic.if": {
            "type": "flowLogic.if",
            "inputs": {"condition": "{{trigger.current.priority}} == 1"},
            "actions": [_logic_snippet_log_action("log_if_true", "If branch ran")],
            "elseIf": [
                {
                    "label": "Priority 2",
                    "condition": "{{trigger.current.priority}} == 2",
                    "actions": [_logic_snippet_log_action("log_if_p2", "Else If branch ran")],
                }
            ],
            "elseActions": [_logic_snippet_log_action("log_if_else", "Else branch ran")],
        },
        "flowLogic.forEach": {
            "type": "flowLogic.forEach",
            "inputs": {"items": "{{action.lookup_records.records}}"},
            "actions": [_logic_snippet_log_action("log_each_item", "For Each item processed")],
        },
        "flowLogic.doUntil": {
            "type": "flowLogic.doUntil",
            "inputs": {"until": "{{action.check_status.complete}} == true"},
            "actions": [_logic_snippet_log_action("log_until_iteration", "Do Until iteration")],
        },
        "flowLogic.parallel": {
            "type": "flowLogic.parallel",
            "branches": [
                {
                    "label": "Branch A",
                    "actions": [_logic_snippet_log_action("log_parallel_a", "Parallel A ran")],
                },
                {
                    "label": "Branch B",
                    "actions": [_logic_snippet_log_action("log_parallel_b", "Parallel B ran")],
                },
            ],
        },
        "flowLogic.wait": {
            "type": "flowLogic.wait",
            "inputs": {"duration": {"value": 5, "unit": "minutes"}},
        },
        "flowLogic.decision": {
            "type": "flowLogic.decision",
            "inputs": {"decisionTable": "<decision-table-sys-id-or-name>"},
            "branches": [
                {
                    "label": "Matched Decision",
                    "actions": [_logic_snippet_log_action("log_decision", "Decision branch ran")],
                }
            ],
        },
        "flowLogic.setFlowVariables": {
            "type": "flowLogic.setFlowVariables",
            "inputs": {"variables": {"needs_approval": True}},
        },
        "flowLogic.endFlow": {
            "type": "flowLogic.endFlow",
            "inputs": {"status": "complete"},
        },
        "flowLogic.try": {
            "type": "flowLogic.try",
            "actions": [_logic_snippet_log_action("log_try", "Try block ran")],
            "catchActions": [_logic_snippet_log_action("log_catch", "Catch block ran")],
            "finallyActions": [_logic_snippet_log_action("log_finally", "Finally block ran")],
        },
        "flowLogic.exitLoop": {"type": "flowLogic.exitLoop", "inputs": {}},
        "flowLogic.skipIteration": {"type": "flowLogic.skipIteration", "inputs": {}},
        "flowLogic.goBack": {
            "type": "flowLogic.goBack",
            "inputs": {"targetActionId": "<previous-action-id>"},
        },
        "flowLogic.callWorkflow": {
            "type": "flowLogic.callWorkflow",
            "inputs": {"workflowSysId": "<published-workflow-sys-id>"},
        },
        "flowLogic.assignSubflowOutputs": {
            "type": "flowLogic.assignSubflowOutputs",
            "inputs": {"outputs": {"result": "okay"}},
        },
        "flowLogic.dynamicFlow": {
            "type": "flowLogic.dynamicFlow",
            "inputs": {"flowSysId": "<flow-or-subflow-sys-id>", "inputs": {}},
        },
        "flowLogic.getFlowOutputs": {
            "type": "flowLogic.getFlowOutputs",
            "inputs": {"dynamicFlowAction": "<dynamic-flow-action-id>"},
        },
    }
    return [
        {
            "type": logic_type,
            "description": FLOW_LOGIC_DESCRIPTIONS[logic_type],
            "snippet": snippets.get(logic_type, {"type": logic_type, "inputs": {}}),
        }
        for logic_type in SUPPORTED_FLOW_LOGIC_TYPES
    ]


def _merge_catalog_entries(*entry_groups: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for entries in entry_groups:
        for entry in entries:
            action_type = entry.get("type")
            if isinstance(action_type, str) and action_type not in merged:
                merged[action_type] = entry
    return sorted(merged.values(), key=lambda action: action["type"].lower())


def _load_available_action_catalog(flow_server_url: Optional[str]) -> Dict[str, Any]:
    resolved_url = get_flow_server_url(flow_server_url)
    endpoint = f"{resolved_url}/available-actions"

    try:
        response = requests.get(endpoint, timeout=AVAILABLE_ACTIONS_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = _parse_response(response, password=None)
        actions = _extract_action_entries(payload)
        if not actions:
            raise ValueError("/available-actions returned no action types")
        actions = _merge_catalog_entries(actions, _flow_logic_entries())

        return {
            "available": True,
            "source": "flow_server",
            "endpoint": endpoint,
            "actions": actions,
            "action_types": [action["type"] for action in actions],
        }
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "MCP_FLOW_AVAILABLE_ACTIONS_UNAVAILABLE endpoint=%s error=%s",
            endpoint,
            exc,
        )
        actions = _merge_catalog_entries(_fallback_action_entries(), _flow_logic_entries())
        return {
            "available": False,
            "source": "fallback",
            "endpoint": endpoint,
            "error": _truncate_text(exc, 300),
            "actions": actions,
            "action_types": [action["type"] for action in actions],
        }


def _compact_action_catalog_summary(action_catalog: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not action_catalog:
        return {
            "available": False,
            "source": "not_loaded",
            "count": 0,
            "sample": [],
        }

    action_types = action_catalog.get("action_types", [])
    sample = action_types[:15]
    summary = {
        "available": action_catalog.get("available", False),
        "source": action_catalog.get("source"),
        "endpoint": action_catalog.get("endpoint"),
        "count": len(action_types),
        "sample": sample,
    }
    if len(action_types) > len(sample):
        summary["omitted_count"] = len(action_types) - len(sample)
    if action_catalog.get("error"):
        summary["error"] = action_catalog["error"]
    return summary


def _action_type_validation_hint(action_catalog: Optional[Dict[str, Any]]) -> str:
    if action_catalog and action_catalog.get("available"):
        summary = _compact_action_catalog_summary(action_catalog)
        sample = ", ".join(summary.get("sample", []))
        return (
            f"Use one of the {summary['count']} action types returned by "
            f"/available-actions. Examples: {sample}."
        )
    return (
        "Use action types returned by the Flow Server /available-actions endpoint. "
        "If the endpoint is unavailable, MCP skips the action allowlist check and "
        "lets the Flow Server validate the action."
    )


def _compact_action_entry(action: Dict[str, Any], include_snippets: bool = False) -> Dict[str, Any]:
    compact = {
        "type": action.get("type"),
        "description": _truncate_text(action.get("description", ""), 180),
    }
    snippet = action.get("snippet")
    if include_snippets and isinstance(snippet, dict):
        if _is_flow_logic_type(action.get("type")):
            compact["snippet"] = deepcopy(snippet)
        else:
            compact["snippet"] = _bounded_server_return_data(snippet)
    elif isinstance(snippet, dict):
        inputs = snippet.get("inputs") if isinstance(snippet.get("inputs"), dict) else {}
        compact["snippet_input_keys"] = sorted(str(key) for key in inputs.keys())
    return compact


def _extract_source_text(result: Any) -> Optional[str]:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return None
    for key in ("source", "generatedSource", "typescript", "code", "ts"):
        value = result.get(key)
        if isinstance(value, str):
            return value
    return None


def _source_payload_summary(result: Any, include_source: bool = False) -> Dict[str, Any]:
    source = _extract_source_text(result)
    summary = _compact_server_result(result)
    if source is None:
        return summary

    summary["source_length"] = len(source)
    summary["source_line_count"] = len(source.splitlines())
    if include_source:
        summary["source"] = source
    else:
        summary["sourcePreview"] = _bounded_log_preview(source, limit=12)
        summary["sourceOmitted"] = True
    return summary


def _rethink_json_payload(
    reason: str,
    body: Optional[Dict[str, Any]] = None,
    validation: Optional[Dict[str, Any]] = None,
    server_return_data: Any = None,
    server_error: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
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
            "Use action types returned by the Flow Server /available-actions endpoint.",
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
        payload["validation"] = _compact_validation_details(validation)
    if server_return_data is not None:
        payload["flowServerReturned"] = "See deploymentResult.serverReturnData."
    if server_error is not None:
        payload["flowServerError"] = server_error
    return payload


def _nested_action_count(action: Dict[str, Any]) -> int:
    count = 0
    for _field_name, _suffix, nested_actions in _logic_action_list_fields(action):
        if not isinstance(nested_actions, list):
            continue
        count += len([item for item in nested_actions if isinstance(item, dict)])
        count += sum(
            _nested_action_count(item) for item in nested_actions if isinstance(item, dict)
        )

    for branch_field in FLOW_LOGIC_BRANCH_FIELDS:
        branches = action.get(branch_field)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            for action_field in FLOW_LOGIC_BRANCH_ACTION_FIELDS:
                nested_actions = branch.get(action_field)
                if not isinstance(nested_actions, list):
                    continue
                count += len([item for item in nested_actions if isinstance(item, dict)])
                count += sum(
                    _nested_action_count(item) for item in nested_actions if isinstance(item, dict)
                )
    return count


def _branch_count(action: Dict[str, Any]) -> int:
    return sum(
        len(action.get(branch_field, []))
        for branch_field in FLOW_LOGIC_BRANCH_FIELDS
        if isinstance(action.get(branch_field), list)
    )


def _compact_flow_step_summary(action: Dict[str, Any]) -> Dict[str, Any]:
    inputs = action.get("inputs") if isinstance(action.get("inputs"), dict) else {}
    summary = {
        "id": action.get("id"),
        "type": action.get("type"),
        "input_keys": sorted(str(key) for key in inputs.keys()),
    }
    logic_type = _canonical_flow_logic_type(action.get("type"))
    if logic_type:
        summary["logic_label"] = FLOW_LOGIC_LABELS.get(logic_type)
        summary["nested_action_count"] = _nested_action_count(action)
        summary["branch_count"] = _branch_count(action)
    return summary


def _flow_response_request_body(body: Dict[str, Any], password: str) -> Dict[str, Any]:
    flow_spec = body.get("flowSpec") if isinstance(body.get("flowSpec"), dict) else {}
    trigger = flow_spec.get("trigger") if isinstance(flow_spec.get("trigger"), dict) else {}
    actions = flow_spec.get("actions") if isinstance(flow_spec.get("actions"), list) else []
    action_summaries = []

    for action in actions[:MAX_FLOW_ACTION_SUMMARIES]:
        if not isinstance(action, dict):
            continue
        action_summaries.append(_compact_flow_step_summary(action))

    if len(actions) > MAX_FLOW_ACTION_SUMMARIES:
        action_summaries.append(
            {
                "truncated": True,
                "omitted_count": len(actions) - MAX_FLOW_ACTION_SUMMARIES,
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
    if "skipInstallFallback" in body:
        response_body["skipInstallFallback"] = body.get("skipInstallFallback")
    return _redact_sensitive_values(response_body, secrets=(password,))


def _flow_client_guidance() -> list[str]:
    return [
        (
            "If scopeId is omitted or a placeholder, MCP deploys with scopeId=global. "
            "scopeName is always formatted like the flow id."
        ),
        (
            "For core.sendEmail, MCP keeps ah_body static, coerces "
            "watermark_email to boolean, and wraps mixed data-pill text in "
            '{"template": "..."} before calling the Flow Server.'
        ),
        (
            "Use flowSpec.trigger.condition for trigger-level logic. Use action types "
            "from list_flow_actions, such as core.waitForCondition when available, "
            "for action-level Flow Designer logic."
        ),
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
    response_body = _flow_response_request_body(body, password=password)
    response_body["deploymentResult"] = deployment_result
    response_body["clientGuidance"] = _flow_client_guidance()
    return response_body


def _subflow_io_summary(io_spec: Any) -> Dict[str, Any]:
    if not isinstance(io_spec, dict):
        return {"count": 0, "items": []}

    items = []
    for name, definition in list(io_spec.items())[:MAX_SERVER_RETURN_LIST_ITEMS]:
        if not isinstance(definition, dict):
            items.append({"name": name, "type": type(definition).__name__})
            continue
        items.append(
            {
                "name": name,
                "type": definition.get("type"),
                "label": _truncate_text(definition.get("label", ""), 120),
                "mandatory": bool(definition.get("mandatory", False)),
            }
        )

    summary: Dict[str, Any] = {"count": len(io_spec), "items": items}
    if len(io_spec) > MAX_SERVER_RETURN_LIST_ITEMS:
        summary["omitted_count"] = len(io_spec) - MAX_SERVER_RETURN_LIST_ITEMS
    return summary


def _subflow_response_request_body(body: Dict[str, Any], password: str) -> Dict[str, Any]:
    subflow_spec = body.get("subflowSpec") if isinstance(body.get("subflowSpec"), dict) else {}
    actions = subflow_spec.get("actions") if isinstance(subflow_spec.get("actions"), list) else []
    action_summaries = []

    for action in actions[:MAX_FLOW_ACTION_SUMMARIES]:
        if not isinstance(action, dict):
            continue
        action_summaries.append(_compact_flow_step_summary(action))

    if len(actions) > MAX_FLOW_ACTION_SUMMARIES:
        action_summaries.append(
            {
                "truncated": True,
                "omitted_count": len(actions) - MAX_FLOW_ACTION_SUMMARIES,
            }
        )

    response_body = {
        "instanceUrl": body.get("instanceUrl"),
        "username": body.get("username"),
        "password": body.get("password"),
        "scopeId": body.get("scopeId"),
        "scopeName": body.get("scopeName"),
        "reinstall": body.get("reinstall"),
        "subflowSpec": {
            "id": subflow_spec.get("id"),
            "name": subflow_spec.get("name"),
            "description": _truncate_text(subflow_spec.get("description", ""), 180),
            "runAs": subflow_spec.get("runAs"),
            "inputs": _subflow_io_summary(subflow_spec.get("inputs")),
            "outputs": _subflow_io_summary(subflow_spec.get("outputs")),
            "action_count": len(actions),
            "actions": action_summaries,
        },
    }
    return _redact_sensitive_values(response_body, secrets=(password,))


def _subflow_client_guidance() -> list[str]:
    return [
        (
            "If scopeId is omitted or a placeholder, MCP deploys with scopeId=global. "
            "scopeName is always formatted like the subflow id."
        ),
        (
            "For core.sendEmail, MCP keeps ah_body static, coerces "
            "watermark_email to boolean, and wraps mixed data-pill text in "
            '{"template": "..."} before calling the Flow Server.'
        ),
        (
            "Generate the Subflow body in the same top-level shape as "
            "src/examples/subflow_tests/01_test_subflow.json: instanceUrl, username, "
            "password, scopeId, scopeName, and subflowSpec."
        ),
        (
            "Subflows use subflowSpec.inputs and subflowSpec.outputs instead of a "
            "record trigger; do not include flowSpec or trigger."
        ),
        ("Subflow input/output types must be string, integer, or boolean."),
        (
            "Use {{input.<name>}} or {{inputs.<name>}} only for variables declared "
            'in subflowSpec.inputs; wrap mixed text in {"template": "..."}.'
        ),
        (
            "Use {{action.<earlier_action_id>.<field>}} only for results from actions "
            "that appear earlier in subflowSpec.actions; do not use trigger.* in Subflows."
        ),
        (
            "Use subflowSpec.outputValues to assign declared outputs at the end of "
            'the subflow, for example {"status": "okay"}.'
        ),
        (
            "subflowSpec.runAs is optional for the SDK example payload; omit it unless "
            "the user explicitly asks for system or user execution."
        ),
        (
            "Use list_flow_actions to discover current SDK action types before "
            "building complex subflowSpec.actions."
        ),
    ]


def _subflow_response_body(
    body: Dict[str, Any],
    operation_result: Dict[str, Any],
    password: str,
) -> Dict[str, Any]:
    response_body = _subflow_response_request_body(body, password=password)
    response_body["operationResult"] = operation_result
    response_body["clientGuidance"] = _subflow_client_guidance()
    return response_body


def _subflow_request_log_summary(body: Dict[str, Any]) -> Dict[str, Any]:
    subflow_spec = body.get("subflowSpec") if isinstance(body.get("subflowSpec"), dict) else {}
    actions = subflow_spec.get("actions") if isinstance(subflow_spec.get("actions"), list) else []
    inputs = subflow_spec.get("inputs") if isinstance(subflow_spec.get("inputs"), dict) else {}
    outputs = subflow_spec.get("outputs") if isinstance(subflow_spec.get("outputs"), dict) else {}
    return {
        "instanceUrl": body.get("instanceUrl"),
        "scopeId": body.get("scopeId"),
        "scopeName": body.get("scopeName"),
        "reinstall": body.get("reinstall"),
        "subflow_id": subflow_spec.get("id"),
        "subflow_name": subflow_spec.get("name"),
        "runAs": subflow_spec.get("runAs"),
        "input_count": len(inputs),
        "output_count": len(outputs),
        "action_count": len(actions),
        "action_types": [
            action.get("type")
            for action in actions[:MAX_FLOW_ACTION_SUMMARIES]
            if isinstance(action, dict)
        ],
    }


def _flow_request_log_summary(body: Dict[str, Any]) -> Dict[str, Any]:
    flow_spec = body.get("flowSpec") if isinstance(body.get("flowSpec"), dict) else {}
    trigger = flow_spec.get("trigger") if isinstance(flow_spec.get("trigger"), dict) else {}
    actions = flow_spec.get("actions") if isinstance(flow_spec.get("actions"), list) else []
    summary = {
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
        "action_types": [
            action.get("type")
            for action in actions[:MAX_FLOW_ACTION_SUMMARIES]
            if isinstance(action, dict)
        ],
    }
    if "skipInstallFallback" in body:
        summary["skipInstallFallback"] = body.get("skipInstallFallback")
    return summary


def _current_basic_credentials(config: ServerConfig) -> tuple[Optional[str], Optional[str]]:
    if config.auth.type != AuthType.BASIC or not config.auth.basic:
        return None, None
    return config.auth.basic.username, config.auth.basic.password


def _is_placeholder_instance_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().rstrip("/").lower()
    return normalized in {item.lower() for item in PLACEHOLDER_INSTANCE_URLS}


def _is_valid_instance_url(value: Any) -> bool:
    if not isinstance(value, str) or re.search(r"\s", value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_placeholder_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {item.lower() for item in PLACEHOLDER_SECRETS}


def _is_placeholder_username(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {item.lower() for item in PLACEHOLDER_USERNAMES}


def _is_placeholder_scope_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {item.lower() for item in PLACEHOLDER_SCOPE_VALUES}


def _is_placeholder_recipient_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    return normalized in {item.lower() for item in PLACEHOLDER_RECIPIENT_VALUES}


def _flow_server_body_fields(
    body: Dict[str, Any],
    field_names: tuple[str, ...],
) -> Dict[str, Any]:
    return {field_name: body[field_name] for field_name in field_names if field_name in body}


def _validate_top_level_body_fields(
    body: Dict[str, Any],
    allowed_field_names: tuple[str, ...],
    spec_field_name: str,
) -> list[Dict[str, str]]:
    allowed = set(allowed_field_names)
    errors: list[Dict[str, str]] = []

    for field_name in sorted(set(body.keys()) - allowed):
        if field_name in FLOW_TOOL_CONTROL_FIELD_NAMES:
            errors.append(
                _validation_error(
                    field_name,
                    f"{field_name} is an MCP tool argument, not a Flow Server body field",
                    (
                        f"Pass {field_name} as a tool argument outside deployment_request "
                        "or remove it from json_text."
                    ),
                )
            )
        else:
            errors.append(
                _validation_error(
                    field_name,
                    "Unsupported top-level Flow Server body field",
                    (
                        "Use only instanceUrl, username, password, scopeId, scopeName, "
                        f"reinstall, optional skipInstallFallback, and {spec_field_name}."
                    ),
                )
            )

    return errors


def _merge_runtime_credentials(
    config: ServerConfig,
    deployment_request: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(deployment_request)
    username, password = _current_basic_credentials(config)

    if (
        not merged.get("instanceUrl") or _is_placeholder_instance_url(merged.get("instanceUrl"))
    ) and config.instance_url != "https://example.invalid":
        merged["instanceUrl"] = config.instance_url
    if merged.get("instanceUrl") and not _is_placeholder_instance_url(merged.get("instanceUrl")):
        merged["instanceUrl"] = normalize_servicenow_instance_url(str(merged["instanceUrl"]))
    if (
        not merged.get("username") or _is_placeholder_username(merged.get("username"))
    ) and username:
        merged["username"] = username
    if (not merged.get("password") or _is_placeholder_secret(merged.get("password"))) and password:
        merged["password"] = password
    if "reinstall" not in merged:
        merged["reinstall"] = False

    return merged


def _normalization_notice(path: str, message: str, before: Any, after: Any) -> Dict[str, Any]:
    return {
        "path": path,
        "message": message,
        "before": before,
        "after": after,
    }


def _first_non_placeholder_string(*values: Any) -> Optional[str]:
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped and not _is_placeholder_scope_value(stripped):
            return stripped
    return None


def _scope_name_seed(body: Dict[str, Any], spec_field_name: Optional[str]) -> str:
    """Pick a stable seed before formatting scopeName as an id-style value."""
    spec = body.get(spec_field_name) if spec_field_name else None
    if isinstance(spec, dict):
        # Prefer the Flow/Subflow id so scopeName follows the deployed artifact id.
        spec_seed = _first_non_placeholder_string(spec.get("id"), spec.get("name"))
        if spec_seed:
            return spec_seed

    compatibility_seed = _first_non_placeholder_string(
        body.get("flowName"),
        body.get("subflowName"),
        body.get("name"),
        body.get("scopeName"),
        DEFAULT_SCOPE_NAME,
    )
    return compatibility_seed or DEFAULT_SCOPE_NAME


def _format_scope_name(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return _flow_spec_id(value)
    return _flow_spec_id(DEFAULT_SCOPE_NAME)


def _sanitize_scope_fields(
    body: Dict[str, Any],
    spec_field_name: Optional[str] = None,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    sanitized = deepcopy(body)
    notices: list[Dict[str, Any]] = []

    if not sanitized.get("scopeId") or _is_placeholder_scope_value(sanitized.get("scopeId")):
        before = sanitized.get("scopeId")
        sanitized["scopeId"] = DEFAULT_SCOPE_ID
        notices.append(
            _normalization_notice(
                "scopeId",
                "scopeId was missing or placeholder; defaulted to global scope.",
                before,
                sanitized["scopeId"],
            )
        )

    before_scope_name = sanitized.get("scopeName")
    formatted_scope_name = _format_scope_name(_scope_name_seed(sanitized, spec_field_name))
    if before_scope_name != formatted_scope_name:
        sanitized["scopeName"] = formatted_scope_name
        notices.append(
            _normalization_notice(
                "scopeName",
                (
                    "scopeName was formatted with the same id-safe sanitizer used "
                    "for flow/subflow ids."
                ),
                before_scope_name,
                sanitized["scopeName"],
            )
        )

    return sanitized, notices


def _coerce_send_email_watermark(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "f", "no", "n", "0", "off"}:
            return False
        if normalized in {"true", "t", "yes", "y", "1", "on"}:
            return True
        return bool(normalized)
    if isinstance(value, (int, float)):
        return bool(value)
    return True


def _static_email_body_from_value(value: Any) -> Optional[str]:
    has_data_pill = any(
        DATA_PILL_PATTERN.search(text)
        for _string_path, text in _iter_data_pill_strings(value, "ah_body")
    )
    if has_data_pill:
        return DEFAULT_STATIC_EMAIL_BODY

    if isinstance(value, dict) and isinstance(value.get("template"), str):
        return value["template"]

    return None


def _template_wrapped_value(
    value: Any,
    path: str,
    notices: list[Dict[str, Any]],
) -> Any:
    if isinstance(value, str):
        if DATA_PILL_PATTERN.search(value) and not FULL_DATA_PILL_PATTERN.match(value.strip()):
            wrapped = {"template": value}
            notices.append(
                _normalization_notice(
                    path,
                    "Mixed text data pill string was wrapped in a Flow Server template object.",
                    value,
                    wrapped,
                )
            )
            return wrapped
        return value

    if isinstance(value, dict):
        if isinstance(value.get("template"), str):
            return value
        return {
            key: _template_wrapped_value(item, f"{path}.{key}", notices)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _template_wrapped_value(item, f"{path}[{index}]", notices)
            for index, item in enumerate(value)
        ]

    return value


def _sanitize_action_inputs_for_sdk47(
    action: Dict[str, Any],
    action_path: str,
    notices: list[Dict[str, Any]],
) -> None:
    inputs = action.get("inputs")
    if not isinstance(inputs, dict):
        return

    if action.get("type") == "core.sendEmail":
        if "watermark_email" in inputs and not isinstance(inputs["watermark_email"], bool):
            before = inputs["watermark_email"]
            inputs["watermark_email"] = _coerce_send_email_watermark(before)
            notices.append(
                _normalization_notice(
                    f"{action_path}.inputs.watermark_email",
                    "core.sendEmail watermark_email was coerced to the SDK boolean value.",
                    before,
                    inputs["watermark_email"],
                )
            )

        if "ah_body" in inputs:
            before = inputs["ah_body"]
            static_body = _static_email_body_from_value(before)
            if static_body is not None and before != static_body:
                inputs["ah_body"] = static_body
                notices.append(
                    _normalization_notice(
                        f"{action_path}.inputs.ah_body",
                        "core.sendEmail ah_body was converted to static SDK 4.7-safe text.",
                        before,
                        inputs["ah_body"],
                    )
                )

    skip_template_keys = {"ah_body"} if action.get("type") == "core.sendEmail" else set()
    for input_name, input_value in list(inputs.items()):
        if input_name in skip_template_keys:
            continue
        inputs[input_name] = _template_wrapped_value(
            input_value,
            f"{action_path}.inputs.{input_name}",
            notices,
        )


def _sanitize_action_tree_for_sdk47(
    actions: Any,
    path: str,
    notices: list[Dict[str, Any]],
) -> None:
    if not isinstance(actions, list):
        return

    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        action_path = f"{path}[{index}]"
        before_type = action.get("type")
        canonical_logic_type = _canonical_flow_logic_type(before_type)
        if canonical_logic_type and before_type != canonical_logic_type:
            action["type"] = canonical_logic_type
            notices.append(
                _normalization_notice(
                    f"{action_path}.type",
                    "Flow Designer logic type was normalized to its canonical flowLogic.* name.",
                    before_type,
                    canonical_logic_type,
                )
            )

        _sanitize_action_inputs_for_sdk47(action, action_path, notices)

        for _field_name, suffix, nested_actions in _logic_action_list_fields(action):
            _sanitize_action_tree_for_sdk47(nested_actions, f"{action_path}{suffix}", notices)

        for branch_field in FLOW_LOGIC_BRANCH_FIELDS:
            branches = action.get(branch_field)
            if not isinstance(branches, list):
                continue
            for branch_index, branch in enumerate(branches):
                if not isinstance(branch, dict):
                    continue
                branch_path = f"{action_path}.{branch_field}[{branch_index}]"
                for action_field in FLOW_LOGIC_BRANCH_ACTION_FIELDS:
                    if action_field in branch:
                        _sanitize_action_tree_for_sdk47(
                            branch[action_field],
                            f"{branch_path}.{action_field}",
                            notices,
                        )


def _sanitize_flow_server_request(
    body: Dict[str, Any],
    spec_field_name: str,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    sanitized, notices = _sanitize_scope_fields(body, spec_field_name)

    spec = sanitized.get(spec_field_name)
    actions = spec.get("actions") if isinstance(spec, dict) else None
    _sanitize_action_tree_for_sdk47(actions, f"{spec_field_name}.actions", notices)

    if spec_field_name == "subflowSpec" and isinstance(spec, dict):
        output_values = spec.get("outputValues")
        if isinstance(output_values, dict):
            spec["outputValues"] = _template_wrapped_value(
                output_values,
                "subflowSpec.outputValues",
                notices,
            )

    return sanitized, notices


def _strip_json_markdown_fence(text: str) -> str:
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
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return stripped
    return stripped[start : end + 1]


def _normalize_json_like_text(text: str) -> str:
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
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        logger.debug("MCP_FLOW_JSON_LITERAL_EVAL_FAILED error=%s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_json_text(json_text: Optional[str]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
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


def _build_request_body(
    params: BaseModel,
    body_field_names: tuple[str, ...],
    wrapper_field_names: tuple[str, ...],
) -> tuple[Dict[str, Any], Optional[str]]:
    values = params.model_dump(exclude_none=True)
    parsed_json, parse_error = _parse_json_text(values.get("json_text"))
    if parse_error:
        return {}, parse_error

    body = dict(parsed_json or {})
    for wrapper_field_name in wrapper_field_names:
        if values.get(wrapper_field_name):
            body.update(values[wrapper_field_name])
    for field_name in body_field_names:
        if field_name in values:
            body[field_name] = values[field_name]

    logger.debug(
        "MCP_FLOW_REQUEST_BUILT body_keys=%s control_keys=%s wrapper_keys=%s",
        sorted(body.keys()),
        sorted(
            key
            for key in values.keys()
            if key in FLOW_TOOL_CONTROL_FIELD_NAMES
            or key in {"generation_request", "verify_request"}
        ),
        sorted(wrapper_field_names),
    )
    return body, None


def _build_deployment_request(params: BaseModel) -> tuple[Dict[str, Any], Optional[str]]:
    return _build_request_body(
        params,
        body_field_names=FLOW_DEPLOYMENT_FIELD_NAMES,
        wrapper_field_names=("deployment_request",),
    )


def _ensure_flow_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(body.get("flowSpec"), dict):
        return body
    if any(field_name in body for field_name in FLOW_SPEC_REQUIRED_FIELD_NAMES):
        return {"flowSpec": body}
    return body


def _iter_flow_action_lists(actions: Any):
    if not isinstance(actions, list):
        return
    yield actions
    for action in list(actions):
        if not isinstance(action, dict):
            continue
        for _field_name, _suffix, nested_actions in _logic_action_list_fields(action):
            yield from _iter_flow_action_lists(nested_actions)
        for branch_field in FLOW_LOGIC_BRANCH_FIELDS:
            branches = action.get(branch_field)
            if not isinstance(branches, list):
                continue
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                for action_field in FLOW_LOGIC_BRANCH_ACTION_FIELDS:
                    yield from _iter_flow_action_lists(branch.get(action_field))


def _remove_flow_actions_by_id(actions: Any, action_ids: set[str]) -> list[str]:
    if not isinstance(actions, list) or not action_ids:
        return []

    removed: list[str] = []
    kept = []
    for action in actions:
        if isinstance(action, dict) and str(action.get("id") or "") in action_ids:
            removed.append(str(action.get("id")))
            continue
        kept.append(action)
    actions[:] = kept

    for action in list(actions):
        if not isinstance(action, dict):
            continue
        for _field_name, _suffix, nested_actions in _logic_action_list_fields(action):
            removed.extend(_remove_flow_actions_by_id(nested_actions, action_ids))
        for branch_field in FLOW_LOGIC_BRANCH_FIELDS:
            branches = action.get(branch_field)
            if not isinstance(branches, list):
                continue
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                for action_field in FLOW_LOGIC_BRANCH_ACTION_FIELDS:
                    removed.extend(_remove_flow_actions_by_id(branch.get(action_field), action_ids))
    return removed


def _find_flow_action(actions: Any, action_id: str) -> Optional[Dict[str, Any]]:
    for action_list in _iter_flow_action_lists(actions) or []:
        for action in action_list:
            if isinstance(action, dict) and str(action.get("id") or "") == action_id:
                return action
    return None


def _apply_flow_action_update(actions: Any, update: FlowActionUpdate) -> Optional[Dict[str, Any]]:
    action = _find_flow_action(actions, update.id)
    if action is None:
        return None

    before = deepcopy(action)
    action.update(deepcopy(update.patch))
    if update.new_id is not None:
        action["id"] = update.new_id
    if update.type is not None:
        action["type"] = update.type
    if update.inputs is not None:
        if update.replace_inputs or not isinstance(action.get("inputs"), dict):
            action["inputs"] = deepcopy(update.inputs)
        else:
            action["inputs"].update(deepcopy(update.inputs))
    return {"action_id": update.id, "before": before, "after": deepcopy(action)}


def _path_segments(path: str) -> list[tuple[str, Optional[int]]]:
    segments = []
    for raw_segment in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)(?:\[(\d+)\])?", raw_segment.strip())
        if not match:
            raise ValueError(f"Unsupported path segment {raw_segment!r} in {path!r}.")
        segments.append(
            (
                match.group(1),
                int(match.group(2)) if match.group(2) is not None else None,
            )
        )
    if not segments:
        raise ValueError("Path cannot be empty.")
    return segments


def _descend_path_container(
    container: Any,
    key: str,
    index: Optional[int],
    full_path: str,
) -> Any:
    if not isinstance(container, dict):
        raise ValueError(f"Cannot set {full_path}: parent for {key!r} is not an object.")
    if key not in container or container[key] is None:
        container[key] = [] if index is not None else {}
    value_at_key = container[key]
    if index is None:
        return value_at_key
    if not isinstance(value_at_key, list):
        raise ValueError(f"Cannot set {full_path}: {key!r} is not a list.")
    if index < 0 or index >= len(value_at_key):
        raise ValueError(f"Cannot set {full_path}: list index {index} is out of range.")
    return value_at_key[index]


def _set_json_path_value(body: Dict[str, Any], path: str, new_value: Any) -> Dict[str, Any]:
    segments = _path_segments(path)
    container: Any = body
    for key, index in segments[:-1]:
        container = _descend_path_container(container, key, index, path)

    key, index = segments[-1]
    if not isinstance(container, dict):
        raise ValueError(f"Cannot set {path}: final parent is not an object.")
    if index is None:
        before = deepcopy(container.get(key))
        container[key] = deepcopy(new_value)
        return {"path": path, "before": before, "after": deepcopy(new_value)}

    target_list = container.get(key)
    if not isinstance(target_list, list):
        raise ValueError(f"Cannot set {path}: {key!r} is not a list.")
    if index < 0 or index >= len(target_list):
        raise ValueError(f"Cannot set {path}: list index {index} is out of range.")
    before = deepcopy(target_list[index])
    target_list[index] = deepcopy(new_value)
    return {"path": path, "before": before, "after": deepcopy(new_value)}


def _apply_flow_edits(body: Dict[str, Any], params: EditFlowParams) -> Dict[str, Any]:
    flow_spec = body.setdefault("flowSpec", {})
    if not isinstance(flow_spec, dict):
        raise ValueError("flowSpec must be an object before it can be edited.")

    trigger = flow_spec.setdefault("trigger", {})
    if not isinstance(trigger, dict):
        raise ValueError("flowSpec.trigger must be an object before it can be edited.")
    actions = flow_spec.setdefault("actions", [])
    if not isinstance(actions, list):
        raise ValueError("flowSpec.actions must be a list before it can be edited.")

    summary: Dict[str, Any] = {
        "metadata_updates": [],
        "trigger_updates": [],
        "removed_action_ids": [],
        "updated_actions": [],
        "appended_action_ids": [],
        "replaced_actions": False,
        "set_values": [],
        "warnings": [],
    }

    metadata_updates = {
        "name": params.flow_name,
        "description": params.description,
        "runAs": params.runAs,
    }
    for field_name, new_value in metadata_updates.items():
        if new_value is None:
            continue
        before = deepcopy(flow_spec.get(field_name))
        flow_spec[field_name] = new_value
        summary["metadata_updates"].append(
            {"path": f"flowSpec.{field_name}", "before": before, "after": new_value}
        )

    trigger_updates = {
        "type": params.trigger_type,
        "table": params.trigger_table,
        "condition": params.trigger_condition,
    }
    for field_name, new_value in trigger_updates.items():
        if new_value is None:
            continue
        before = deepcopy(trigger.get(field_name))
        trigger[field_name] = new_value
        summary["trigger_updates"].append(
            {"path": f"flowSpec.trigger.{field_name}", "before": before, "after": new_value}
        )

    if params.replace_actions is not None:
        flow_spec["actions"] = deepcopy(params.replace_actions)
        actions = flow_spec["actions"]
        summary["replaced_actions"] = True
        summary["appended_action_ids"].extend(
            [
                str(action.get("id"))
                for action in actions
                if isinstance(action, dict) and action.get("id")
            ]
        )
    else:
        removed = _remove_flow_actions_by_id(actions, set(params.remove_action_ids))
        summary["removed_action_ids"] = removed
        missing_removed = sorted(set(params.remove_action_ids) - set(removed))
        if missing_removed:
            summary["warnings"].append(
                f"Action ids not found for removal: {', '.join(missing_removed)}"
            )

        for update in params.update_actions:
            update_result = _apply_flow_action_update(actions, update)
            if update_result:
                summary["updated_actions"].append(update_result)
            else:
                summary["warnings"].append(f"Action id not found for update: {update.id}")

        if params.append_actions:
            actions.extend(deepcopy(params.append_actions))
            summary["appended_action_ids"].extend(
                [
                    str(action.get("id"))
                    for action in params.append_actions
                    if isinstance(action, dict) and action.get("id")
                ]
            )

    for path, new_value in params.set_values.items():
        summary["set_values"].append(_set_json_path_value(body, path, new_value))

    return summary


def _build_subflow_request(params: BaseModel) -> tuple[Dict[str, Any], Optional[str]]:
    return _build_request_body(
        params,
        body_field_names=SUBFLOW_DEPLOYMENT_FIELD_NAMES,
        wrapper_field_names=("deployment_request", "generation_request"),
    )


def _build_verify_subflow_request(
    params: VerifySubflowParams,
) -> tuple[Dict[str, Any], Optional[str]]:
    body, parse_error = _build_request_body(
        params,
        body_field_names=SUBFLOW_VERIFY_FIELD_NAMES,
        wrapper_field_names=("verify_request",),
    )
    if parse_error:
        return body, parse_error
    values = params.model_dump(exclude_none=True)
    if values.get("subflow_name"):
        body["subflowName"] = values["subflow_name"]
    if not body.get("flowName"):
        body["flowName"] = body.get("subflowName") or values.get("subflow_name") or body.get("name")
    return body, None


def _ensure_subflow_request_body(body: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(body.get("subflowSpec"), dict):
        return body
    if any(field_name in body for field_name in SUBFLOW_SPEC_REQUIRED_FIELD_NAMES):
        return {"subflowSpec": body}
    return body


def _flow_spec_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized[:80] or "generated_flow"


def _safe_now_id(value: str) -> str:
    safe = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower())
    safe = re.sub(r"^_+|_+$", "", safe)
    safe = re.sub(r"_+", "_", safe)[:80]
    if not safe:
        return ""
    if not re.match(r"^[a-z]", safe):
        return f"id_{safe}"[:80]
    return safe


def _incident_admin_notification_body(
    config: ServerConfig,
    params: CreateIncidentAdminNotificationFlowParams,
) -> Dict[str, Any]:
    username, password = _current_basic_credentials(config)
    instance_url = config.instance_url if config.instance_url != "https://example.invalid" else None
    flow_id = params.flow_id or _flow_spec_id(params.flow_name)
    body = {
        "scopeId": params.scopeId or DEFAULT_SCOPE_ID,
        "scopeName": _format_scope_name(flow_id),
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
                        "ah_to": params.recipient_email,
                        "ah_subject": {
                            "template": ("New Incident Created: {{trigger.current.number}}")
                        },
                        "ah_body": DEFAULT_STATIC_EMAIL_BODY,
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
        "username": USERNAME_PLACEHOLDER,
        "password": "***REDACTED***",
        "scopeId": DEFAULT_SCOPE_ID,
        "scopeName": _format_scope_name("incident_created_notify_admin"),
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


def _compact_validation_details(validation: Dict[str, Any]) -> Dict[str, Any]:
    errors = validation.get("errors", [])
    warnings = validation.get("warnings", [])
    compact = {
        "valid": validation.get("valid", False),
        "errors": errors[:MAX_FLOW_VALIDATION_ITEMS],
        "warnings": warnings[:MAX_FLOW_VALIDATION_ITEMS],
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    if len(errors) > MAX_FLOW_VALIDATION_ITEMS:
        compact["errors_omitted_count"] = len(errors) - MAX_FLOW_VALIDATION_ITEMS
    if len(warnings) > MAX_FLOW_VALIDATION_ITEMS:
        compact["warnings_omitted_count"] = len(warnings) - MAX_FLOW_VALIDATION_ITEMS
    return compact


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


def _iter_data_pill_expressions(value: Any, path: str):
    for string_path, text in _iter_data_pill_strings(value, path):
        for match in DATA_PILL_PATTERN.finditer(text):
            yield string_path, text, match.group(1).strip()


def _validate_data_pills(
    value: Any,
    path: str,
    allowed_roots: tuple[str, ...] = SUPPORTED_DATA_PILL_ROOTS,
) -> list[Dict[str, str]]:
    errors: list[Dict[str, str]] = []
    for string_path, _text, expression in _iter_data_pill_expressions(value, path):
        root = expression.split(".", 1)[0].strip()
        if root not in allowed_roots:
            suggested = (
                f"trigger.{expression}"
                if root == "current" and "trigger" in allowed_roots
                else None
            )
            roots_hint = ", ".join(f"{allowed_root}.*" for allowed_root in allowed_roots)
            hint = (
                f"Use {{{{{suggested}}}}} instead."
                if suggested
                else f"Data pills must start with one of: {roots_hint}."
            )
            errors.append(
                _validation_error(
                    string_path,
                    (f"Unsupported data pill root in {expression!r}. Use one of: {roots_hint}"),
                    hint,
                )
            )
    return errors


def _flow_logic_alias_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _canonical_flow_logic_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if stripped in SUPPORTED_FLOW_LOGIC_TYPES:
        return stripped
    return FLOW_LOGIC_TYPE_ALIASES.get(_flow_logic_alias_key(stripped))


def _is_flow_logic_type(value: Any) -> bool:
    return _canonical_flow_logic_type(value) is not None


def _flow_logic_type_hint() -> str:
    examples = ", ".join(
        f"{logic_type} ({FLOW_LOGIC_LABELS[logic_type]})"
        for logic_type in SUPPORTED_FLOW_LOGIC_TYPES[:5]
    )
    return f"Supported Flow Designer logic types include: {examples}."


def _logic_input_map(action: Dict[str, Any]) -> Dict[str, Any]:
    inputs = action.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _logic_value(action: Dict[str, Any], *keys: str) -> Any:
    inputs = _logic_input_map(action)
    for key in keys:
        if key in action:
            return action[key]
        if key in inputs:
            return inputs[key]
    return None


def _has_logic_value(action: Dict[str, Any], *keys: str) -> bool:
    value = _logic_value(action, *keys)
    return value not in (None, "", {}, [])


def _require_logic_value(
    action: Dict[str, Any],
    action_path: str,
    errors: list[Dict[str, str]],
    field_names: tuple[str, ...],
    message: str,
    hint: str,
) -> None:
    if not _has_logic_value(action, *field_names):
        errors.append(_validation_error(action_path, message, hint))


def _validate_logic_value_data_pills(
    action: Dict[str, Any],
    action_path: str,
    errors: list[Dict[str, str]],
    allowed_roots: tuple[str, ...],
) -> None:
    for field_name in (
        "condition",
        "conditions",
        "until",
        "endCondition",
        "items",
        "records",
        "list",
        "duration",
        "dateTime",
        "relativeTime",
        "variables",
        "outputs",
        "outputValues",
        "target",
        "targetActionId",
        "workflow",
        "workflowSysId",
        "workflowName",
        "flow",
        "subflow",
        "flowSysId",
        "subflowSysId",
        "flowName",
        "subflowName",
        "execution",
        "executionId",
        "dynamicFlowAction",
    ):
        if field_name in action:
            errors.extend(
                _validate_data_pills(
                    action[field_name],
                    f"{action_path}.{field_name}",
                    allowed_roots=allowed_roots,
                )
            )


def _logic_action_list_fields(action: Dict[str, Any]):
    for field_name in FLOW_LOGIC_BLOCK_FIELDS:
        if field_name in action:
            yield field_name, f".{field_name}", action[field_name]


def _validate_action_catalog_type(
    action_type: Any,
    action_path: str,
    errors: list[Dict[str, str]],
    action_catalog: Optional[Dict[str, Any]],
) -> Optional[str]:
    logic_type = _canonical_flow_logic_type(action_type)
    if logic_type:
        return logic_type

    available_action_types = (
        set(action_catalog.get("action_types", [])) if action_catalog else set()
    )
    action_catalog_available = bool(action_catalog and action_catalog.get("available"))
    if action_catalog_available and action_type not in available_action_types:
        errors.append(
            _validation_error(
                f"{action_path}.type",
                "Unsupported action or Flow Designer logic type",
                f"{_action_type_validation_hint(action_catalog)} {_flow_logic_type_hint()}",
            )
        )
    return None


def _validate_core_action_inputs(
    action_type: str,
    inputs: Dict[str, Any],
    action_path: str,
    errors: list[Dict[str, str]],
    warnings: list[Dict[str, str]],
    *,
    email_body_data_pills_are_errors: bool = False,
) -> None:
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
        if "log_level" in inputs and inputs["log_level"] not in (
            "debug",
            "error",
            "info",
            "warn",
        ):
            errors.append(
                _validation_error(
                    f"{action_path}.inputs.log_level",
                    "core.log input log_level must be 'debug', 'error', 'info', or 'warn'",
                    "Use exactly lowercase 'debug', 'error', 'info', or 'warn'.",
                )
            )
        return

    if action_type != "core.sendEmail":
        return

    if "ah_body" in inputs and any(
        DATA_PILL_PATTERN.search(text)
        for _, text in _iter_data_pill_strings(inputs["ah_body"], f"{action_path}.inputs.ah_body")
    ):
        finding = (
            f"{action_path}.inputs.ah_body",
            "SDK 4.7 rejects data pills in email body",
            "Keep ah_body static; use data pills in ah_subject instead.",
        )
        if email_body_data_pills_are_errors:
            errors.append(_validation_error(*finding))
        else:
            warnings.append(_validation_warning(*finding))

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
    if "watermark_email" in inputs and not isinstance(inputs["watermark_email"], bool):
        errors.append(
            _validation_error(
                f"{action_path}.inputs.watermark_email",
                "core.sendEmail input watermark_email must be a boolean",
                "Use true or false without quotes.",
            )
        )
    if _is_placeholder_recipient_value(inputs.get("ah_to")):
        errors.append(
            _validation_error(
                f"{action_path}.inputs.ah_to",
                "core.sendEmail input ah_to is still a template placeholder",
                "Set ah_to to the real recipient email or a valid data pill.",
            )
        )


def _validate_flow_logic_branches(
    action: Dict[str, Any],
    logic_type: str,
    action_path: str,
    errors: list[Dict[str, str]],
    warnings: list[Dict[str, str]],
    action_catalog: Optional[Dict[str, Any]],
    allowed_roots: tuple[str, ...],
    in_loop: bool,
    depth: int,
) -> None:
    for branch_field in FLOW_LOGIC_BRANCH_FIELDS:
        if branch_field not in action:
            continue
        branches = action[branch_field]
        if not isinstance(branches, list):
            errors.append(
                _validation_error(
                    f"{action_path}.{branch_field}",
                    f"{branch_field} must be a list of branch objects",
                    'Use [{"label": "Branch name", "condition": "...", "actions": [...]}].',
                )
            )
            continue
        if logic_type == "flowLogic.parallel" and not branches:
            errors.append(
                _validation_error(
                    f"{action_path}.{branch_field}",
                    "Parallel logic requires at least one branch",
                    "Add branch objects with actions for each parallel path.",
                )
            )
        for branch_index, branch in enumerate(branches):
            branch_path = f"{action_path}.{branch_field}[{branch_index}]"
            if not isinstance(branch, dict):
                errors.append(
                    _validation_error(
                        branch_path,
                        "Branch must be an object",
                        'Use {"label": "Branch", "condition": "...", "actions": [...]}.',
                    )
                )
                continue
            if "condition" in branch:
                errors.extend(
                    _validate_data_pills(
                        branch["condition"],
                        f"{branch_path}.condition",
                        allowed_roots=allowed_roots,
                    )
                )
            if "inputs" in branch:
                if isinstance(branch["inputs"], dict):
                    errors.extend(
                        _validate_data_pills(
                            branch["inputs"],
                            f"{branch_path}.inputs",
                            allowed_roots=allowed_roots,
                        )
                    )
                else:
                    errors.append(
                        _validation_error(
                            f"{branch_path}.inputs",
                            "Branch inputs must be an object",
                            "Use key/value metadata or omit inputs.",
                        )
                    )
            has_actions = False
            for action_field in FLOW_LOGIC_BRANCH_ACTION_FIELDS:
                if action_field in branch:
                    has_actions = True
                    _validate_nested_flow_actions(
                        branch[action_field],
                        f"{branch_path}.{action_field}",
                        errors,
                        warnings,
                        action_catalog,
                        allowed_roots=allowed_roots,
                        in_loop=in_loop or logic_type in FLOW_LOGIC_LOOP_TYPES,
                        depth=depth + 1,
                    )
            if logic_type in {"flowLogic.parallel", "flowLogic.decision"} and not has_actions:
                errors.append(
                    _validation_error(
                        branch_path,
                        f"{FLOW_LOGIC_LABELS[logic_type]} branch needs actions",
                        "Add an actions array to the branch.",
                    )
                )


def _validate_flow_logic_action(
    action: Dict[str, Any],
    logic_type: str,
    action_path: str,
    errors: list[Dict[str, str]],
    warnings: list[Dict[str, str]],
    action_catalog: Optional[Dict[str, Any]],
    allowed_roots: tuple[str, ...],
    *,
    in_loop: bool,
    depth: int,
) -> None:
    if depth > 12:
        errors.append(
            _validation_error(
                action_path,
                "Flow logic nesting is too deep",
                "Keep Flow Designer logic nesting to 12 levels or fewer.",
            )
        )
        return

    _validate_logic_value_data_pills(action, action_path, errors, allowed_roots)
    inputs = action.get("inputs")
    if inputs is not None and not isinstance(inputs, dict):
        errors.append(
            _validation_error(
                f"{action_path}.inputs",
                "Flow logic inputs must be an object",
                "Use inputs for logic configuration values, or omit it.",
            )
        )
    elif isinstance(inputs, dict):
        errors.extend(
            _validate_data_pills(inputs, f"{action_path}.inputs", allowed_roots=allowed_roots)
        )

    if logic_type == "flowLogic.if":
        if not (
            _has_logic_value(action, "condition", "conditions")
            or _has_logic_value(action, "branches", "elseIf")
        ):
            errors.append(
                _validation_error(
                    action_path,
                    "If logic needs a condition or branches",
                    (
                        "Set condition for a simple If, or branches/elseIf for "
                        "If / Else If / Else logic."
                    ),
                )
            )
    elif logic_type == "flowLogic.forEach":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("items", "records", "list"),
            "For Each logic needs a list of items or records",
            (
                "Set inputs.items, inputs.records, or items to a list/data pill such as "
                '"{{action.lookup.records}}".'
            ),
        )
    elif logic_type == "flowLogic.doUntil":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("until", "condition", "endCondition"),
            "Do Until logic needs an end condition",
            "Set inputs.until or condition to the expression that ends the loop.",
        )
    elif logic_type == "flowLogic.parallel":
        if not any(field in action for field in ("branches", "parallelBranches")):
            errors.append(
                _validation_error(
                    action_path,
                    "Parallel logic needs branches",
                    "Set branches or parallelBranches with one actions array per branch.",
                )
            )
    elif logic_type == "flowLogic.wait":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("duration", "durationSeconds", "dateTime", "relativeTime"),
            "Wait logic needs a duration or date/time",
            "Set inputs.duration, inputs.durationSeconds, inputs.dateTime, or inputs.relativeTime.",
        )
    elif logic_type == "flowLogic.decision":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("decisionTable", "decisionTableSysId", "table", "name"),
            "Decision logic needs a decision table reference",
            "Set inputs.decisionTable, inputs.decisionTableSysId, or inputs.name.",
        )
    elif logic_type == "flowLogic.setFlowVariables":
        variables = _logic_value(action, "variables")
        if not isinstance(variables, dict) or not variables:
            errors.append(
                _validation_error(
                    action_path,
                    "Set Flow Variables needs a variables object",
                    'Use inputs.variables such as {"needs_approval": true}.',
                )
            )
    elif logic_type == "flowLogic.try":
        if not any(field in action for field in ("actions", "tryActions")):
            errors.append(
                _validation_error(
                    action_path,
                    "Try logic needs protected actions",
                    "Set actions or tryActions to the steps that should be protected.",
                )
            )
    elif logic_type in {"flowLogic.exitLoop", "flowLogic.skipIteration"} and not in_loop:
        errors.append(
            _validation_error(
                action_path,
                f"{FLOW_LOGIC_LABELS[logic_type]} can only be used inside a loop",
                "Place it inside flowLogic.forEach or flowLogic.doUntil actions.",
            )
        )
    elif logic_type == "flowLogic.goBack":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("target", "targetActionId", "step", "actionId"),
            "Go Back logic needs a target step",
            "Set inputs.targetActionId or inputs.target to the previous action id.",
        )
        warnings.append(
            _validation_warning(
                action_path,
                "Go Back can create infinite loops",
                "Use an explicit end condition or counter guard.",
            )
        )
    elif logic_type == "flowLogic.callWorkflow":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("workflow", "workflowSysId", "workflowName"),
            "Call Workflow needs a workflow reference",
            "Set inputs.workflowSysId, inputs.workflowName, or inputs.workflow.",
        )
    elif logic_type == "flowLogic.assignSubflowOutputs":
        outputs = _logic_value(action, "outputs", "outputValues")
        if not isinstance(outputs, dict) or not outputs:
            errors.append(
                _validation_error(
                    action_path,
                    "Assign Subflow Outputs needs output values",
                    'Use inputs.outputs or inputs.outputValues such as {"result": "okay"}.',
                )
            )
    elif logic_type == "flowLogic.dynamicFlow":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("flow", "subflow", "flowSysId", "subflowSysId", "flowName", "subflowName"),
            "Dynamic Flow needs a flow or subflow reference",
            "Set inputs.flowSysId, inputs.subflowSysId, inputs.flowName, or inputs.subflowName.",
        )
    elif logic_type == "flowLogic.getFlowOutputs":
        _require_logic_value(
            action,
            action_path,
            errors,
            ("execution", "executionId", "dynamicFlowAction", "action"),
            "Get Flow Outputs needs a dynamic-flow execution reference",
            "Set inputs.dynamicFlowAction or inputs.executionId.",
        )

    _validate_flow_logic_branches(
        action,
        logic_type,
        action_path,
        errors,
        warnings,
        action_catalog,
        allowed_roots,
        in_loop,
        depth,
    )

    nested_loop = in_loop or logic_type in FLOW_LOGIC_LOOP_TYPES
    for _field_name, suffix, nested_actions in _logic_action_list_fields(action):
        _validate_nested_flow_actions(
            nested_actions,
            f"{action_path}{suffix}",
            errors,
            warnings,
            action_catalog,
            allowed_roots=allowed_roots,
            in_loop=nested_loop,
            depth=depth + 1,
        )


def _validate_nested_flow_actions(
    actions: Any,
    path: str,
    errors: list[Dict[str, str]],
    warnings: list[Dict[str, str]],
    action_catalog: Optional[Dict[str, Any]],
    *,
    allowed_roots: tuple[str, ...],
    in_loop: bool = False,
    depth: int = 0,
) -> None:
    if not isinstance(actions, list) or not actions:
        errors.append(
            _validation_error(
                path,
                f"{path} must be a non-empty action list",
                "Add at least one action or Flow Designer logic block.",
            )
        )
        return

    action_ids: set[str] = set()
    for index, action in enumerate(actions):
        action_path = f"{path}[{index}]"
        if not isinstance(action, dict):
            errors.append(
                _validation_error(
                    action_path,
                    "Each nested step must be an object",
                    'Use {"id": "log_step", "type": "core.log", "inputs": {...}}.',
                )
            )
            continue

        action_id = action.get("id")
        if not action_id or not isinstance(action_id, str):
            errors.append(
                _validation_error(
                    f"{action_path}.id",
                    "Nested action id is required and must be a string",
                    "Use a stable action id such as log_nested_step.",
                )
            )
        elif action_id in action_ids:
            errors.append(
                _validation_error(
                    f"{action_path}.id",
                    "Nested action id must be unique within this block",
                    "Give each nested action a different stable id.",
                )
            )
        else:
            action_ids.add(action_id)

        action_type = action.get("type")
        if not action_type or not isinstance(action_type, str):
            errors.append(
                _validation_error(
                    f"{action_path}.type",
                    "Nested action type is required and must be a string",
                    "Use an action type returned by /available-actions or a flowLogic.* type.",
                )
            )
            continue

        logic_type = _validate_action_catalog_type(
            action_type,
            action_path,
            errors,
            action_catalog,
        )
        inputs = action.get("inputs")
        if inputs is None and logic_type:
            inputs = {}
        if not isinstance(inputs, dict):
            errors.append(
                _validation_error(
                    f"{action_path}.inputs",
                    "Nested action inputs must be an object",
                    "Set inputs to the expected input object, or {} for logic blocks.",
                )
            )
            continue

        errors.extend(
            _validate_data_pills(inputs, f"{action_path}.inputs", allowed_roots=allowed_roots)
        )
        _validate_core_action_inputs(
            action_type,
            inputs,
            action_path,
            errors,
            warnings,
            email_body_data_pills_are_errors=allowed_roots == SUBFLOW_DATA_PILL_ROOTS,
        )
        if logic_type:
            _validate_flow_logic_action(
                action,
                logic_type,
                action_path,
                errors,
                warnings,
                action_catalog,
                allowed_roots,
                in_loop=in_loop,
                depth=depth,
            )


def _validate_deployment_request_details(
    body: Dict[str, Any],
    action_catalog: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    errors: list[Dict[str, str]] = []
    warnings: list[Dict[str, str]] = []
    available_action_types = (
        set(action_catalog.get("action_types", [])) if action_catalog else set()
    )
    action_catalog_available = bool(action_catalog and action_catalog.get("available"))

    errors.extend(
        _validate_top_level_body_fields(
            body,
            FLOW_DEPLOYMENT_FIELD_NAMES,
            "flowSpec",
        )
    )

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
    elif body.get("instanceUrl") and not _is_valid_instance_url(body.get("instanceUrl")):
        errors.append(
            _validation_error(
                "instanceUrl",
                "instanceUrl must be an absolute http(s) URL",
                "Use a value such as https://dev305525.service-now.com.",
            )
        )

    if _is_placeholder_username(body.get("username")):
        errors.append(
            _validation_error(
                "username",
                "username is still a template placeholder",
                "Use the real username or omit it so runtime basic auth can fill it.",
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

    for field_name in ("scopeId", "scopeName"):
        if _is_placeholder_scope_value(body.get(field_name)):
            errors.append(
                _validation_error(
                    field_name,
                    f"{field_name} is still a template placeholder",
                    (
                        f"Set top-level {field_name} from the target application "
                        "or flow scope before deployment."
                    ),
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

    if "skipInstallFallback" in body and not isinstance(body["skipInstallFallback"], bool):
        errors.append(
            _validation_error(
                "skipInstallFallback",
                "skipInstallFallback must be a boolean",
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
        elif "condition" in trigger:
            errors.extend(
                _validate_data_pills(
                    trigger["condition"],
                    "flowSpec.trigger.condition",
                    allowed_roots=("trigger",),
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
        if not action_catalog_available:
            warnings.append(
                _validation_warning(
                    "flowSpec.actions",
                    "Flow Server /available-actions was unavailable",
                    (
                        "MCP skipped the local action type allowlist check. The "
                        "Flow Server will still validate action support during deployment."
                    ),
                )
            )

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
            logic_type = _canonical_flow_logic_type(action_type)
            if not action_type or not isinstance(action_type, str):
                errors.append(
                    _validation_error(
                        f"{action_path}.type",
                        "Action type is required and must be a string",
                        "Use an action type returned by /available-actions.",
                    )
                )
            elif (
                not logic_type
                and action_catalog_available
                and action_type not in available_action_types
            ):
                errors.append(
                    _validation_error(
                        f"{action_path}.type",
                        "Unsupported action or Flow Designer logic type",
                        f"{_action_type_validation_hint(action_catalog)} {_flow_logic_type_hint()}",
                    )
                )

            inputs = action.get("inputs", {}) if logic_type else action.get("inputs")
            if not isinstance(inputs, dict):
                errors.append(
                    _validation_error(
                        f"{action_path}.inputs",
                        "Action inputs must be an object",
                        "Set inputs to the action input object expected by the Flow Server.",
                    )
                )
                continue

            if action_catalog_available and not logic_type:
                catalog_action = next(
                    (a for a in action_catalog.get("actions", []) if a.get("type") == action_type),
                    None,
                )
                if catalog_action:
                    snippet_inputs = catalog_action.get("snippet", {}).get("inputs", {})
                    if snippet_inputs:
                        for input_key in inputs:
                            if input_key not in snippet_inputs:
                                valid_keys = ", ".join(snippet_inputs.keys())
                                warnings.append(
                                    _validation_warning(
                                        f"{action_path}.inputs.{input_key}",
                                        (
                                            f"Input {input_key!r} is not present in the "
                                            f"/available-actions snippet for {action_type!r}"
                                        ),
                                        (
                                            "Snippets are examples, not exhaustive schemas. "
                                            f"Snippet input keys: {valid_keys}"
                                        ),
                                    )
                                )

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
                if "log_level" in inputs and inputs["log_level"] not in (
                    "debug",
                    "error",
                    "info",
                    "warn",
                ):
                    errors.append(
                        _validation_error(
                            f"{action_path}.inputs.log_level",
                            "core.log input log_level must be 'debug', 'error', 'info', or 'warn'",
                            "Use exactly lowercase 'debug', 'error', 'info', or 'warn'.",
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
                if "watermark_email" in inputs and not isinstance(inputs["watermark_email"], bool):
                    errors.append(
                        _validation_error(
                            f"{action_path}.inputs.watermark_email",
                            "core.sendEmail input watermark_email must be a boolean",
                            "Use true or false without quotes.",
                        )
                    )
                if _is_placeholder_recipient_value(inputs.get("ah_to")):
                    errors.append(
                        _validation_error(
                            f"{action_path}.inputs.ah_to",
                            "core.sendEmail input ah_to is still a template placeholder",
                            "Set ah_to to the real recipient email or a valid data pill.",
                        )
                    )
            if logic_type:
                _validate_flow_logic_action(
                    action,
                    logic_type,
                    action_path,
                    errors,
                    warnings,
                    action_catalog,
                    SUPPORTED_DATA_PILL_ROOTS,
                    in_loop=False,
                    depth=0,
                )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _validate_subflow_io_map(
    io_map: Any,
    path: str,
    errors: list[Dict[str, str]],
) -> None:
    if not isinstance(io_map, dict):
        errors.append(
            _validation_error(
                path,
                f"{path} must be an object",
                f"Set {path} to a map of variable names to type/label definitions.",
            )
        )
        return

    for variable_name, definition in io_map.items():
        variable_path = f"{path}.{variable_name}"
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(variable_name)):
            errors.append(
                _validation_error(
                    variable_path,
                    "Subflow variable names must be valid identifiers",
                    "Use letters, numbers, and underscores; start with a letter or underscore.",
                )
            )
        if not isinstance(definition, dict):
            errors.append(
                _validation_error(
                    variable_path,
                    "Subflow variable definition must be an object",
                    'Use {"type": "string", "label": "My Label"}.',
                )
            )
            continue

        field_type = definition.get("type")
        if field_type not in SUPPORTED_SUBFLOW_IO_TYPES:
            errors.append(
                _validation_error(
                    f"{variable_path}.type",
                    "Unsupported subflow input/output type",
                    f"Use one of: {', '.join(SUPPORTED_SUBFLOW_IO_TYPES)}.",
                )
            )
        label = definition.get("label")
        if not label or not isinstance(label, str):
            errors.append(
                _validation_error(
                    f"{variable_path}.label",
                    "Subflow input/output label is required",
                    "Add a human-readable label for Flow Designer.",
                )
            )
        if "mandatory" in definition and not isinstance(definition["mandatory"], bool):
            errors.append(
                _validation_error(
                    f"{variable_path}.mandatory",
                    "mandatory must be a boolean",
                    "Use true or false.",
                )
            )


def _warn_subflow_io_extra_fields(
    io_map: Any,
    path: str,
    warnings: list[Dict[str, str]],
) -> None:
    if not isinstance(io_map, dict):
        return

    supported = set(SUBFLOW_IO_FIELD_NAMES)
    for variable_name, definition in io_map.items():
        if not isinstance(definition, dict):
            continue
        extra_fields = sorted(str(key) for key in definition if key not in supported)
        if extra_fields:
            warnings.append(
                _validation_warning(
                    f"{path}.{variable_name}",
                    "Subflow input/output metadata contains unsupported fields",
                    (
                        "The current SDK generator only uses type, label, and "
                        f"mandatory; ignored fields: {', '.join(extra_fields)}."
                    ),
                )
            )


def _warn_unrendered_partial_data_pills(
    value: Any,
    path: str,
    warnings: list[Dict[str, str]],
) -> None:
    for string_path, text in _iter_data_pill_strings(value, path):
        if not DATA_PILL_PATTERN.search(text):
            continue
        if FULL_DATA_PILL_PATTERN.match(text.strip()) or string_path.endswith(".template"):
            continue
        warnings.append(
            _validation_warning(
                string_path,
                "Mixed text data pills must use a template object",
                (
                    'Use {"template": "text {{input.name}}"} for mixed text, '
                    'or use a full data pill string like "{{input.name}}".'
                ),
            )
        )


def _validate_subflow_data_pill_references(
    value: Any,
    path: str,
    declared_input_names: set[str],
    rendered_action_ids: set[str],
    rendered_safe_action_ids: set[str],
) -> list[Dict[str, str]]:
    errors: list[Dict[str, str]] = []

    for string_path, _text, expression in _iter_data_pill_expressions(value, path):
        parts = [part.strip() for part in expression.split(".")]
        root = parts[0] if parts else ""

        if root in ("input", "inputs"):
            if len(parts) != 2 or not parts[1]:
                errors.append(
                    _validation_error(
                        string_path,
                        f"Invalid subflow input data pill path {expression!r}",
                        "Use {{input.<declared_input_name>}} or {{inputs.<declared_input_name>}}.",
                    )
                )
                continue
            if parts[1] not in declared_input_names:
                errors.append(
                    _validation_error(
                        string_path,
                        f"Unknown subflow input {parts[1]!r}",
                        "Declare the input in subflowSpec.inputs before referencing it.",
                    )
                )
            continue

        if root == "action":
            if len(parts) < 3 or not parts[1]:
                errors.append(
                    _validation_error(
                        string_path,
                        f"Invalid action data pill path {expression!r}",
                        "Use {{action.<earlier_action_id>.<field>}}.",
                    )
                )
                continue

            referenced_action_id = parts[1]
            referenced_safe_id = _safe_now_id(referenced_action_id)
            if (
                referenced_action_id not in rendered_action_ids
                and referenced_safe_id not in rendered_safe_action_ids
            ):
                errors.append(
                    _validation_error(
                        string_path,
                        f"Action data pill references {referenced_action_id!r} before it is "
                        f"rendered",
                        "Only reference outputs from actions that appear earlier in subflowSpec."
                        "actions.",
                    )
                )

    return errors


def _validate_subflow_actions(
    actions: Any,
    errors: list[Dict[str, str]],
    warnings: list[Dict[str, str]],
    action_catalog: Optional[Dict[str, Any]],
    declared_input_names: Optional[set[str]] = None,
) -> None:
    available_action_types = (
        set(action_catalog.get("action_types", [])) if action_catalog else set()
    )
    action_catalog_available = bool(action_catalog and action_catalog.get("available"))
    declared_input_names = declared_input_names or set()

    if not isinstance(actions, list) or not actions:
        errors.append(
            _validation_error(
                "subflowSpec.actions",
                "subflowSpec.actions must be a non-empty list",
                "Add at least one action such as core.log.",
            )
        )
        return

    if not action_catalog_available:
        warnings.append(
            _validation_warning(
                "subflowSpec.actions",
                "Flow Server /available-actions was unavailable",
                (
                    "MCP skipped the local action type allowlist check. The "
                    "Flow Server will still validate action support during deployment."
                ),
            )
        )

    action_ids = set()
    safe_action_ids: Dict[str, str] = {}
    rendered_action_ids: set[str] = set()
    rendered_safe_action_ids: set[str] = set()
    for index, action in enumerate(actions):
        action_path = f"subflowSpec.actions[{index}]"
        if not isinstance(action, dict):
            errors.append(
                _validation_error(
                    action_path,
                    "Each action must be an object",
                    'Use {"id": "log_action", "type": "core.log", "inputs": {...}}.',
                )
            )
            continue

        action_id = action.get("id")
        normalized_action_id = ""
        action_id_can_render = False
        if not action_id or not isinstance(action_id, str):
            errors.append(
                _validation_error(
                    f"{action_path}.id",
                    "Action id is required and must be a string",
                    "Use a stable action id such as log_subflow_event.",
                )
            )
        elif action_id in action_ids:
            errors.append(
                _validation_error(
                    f"{action_path}.id",
                    "Action id must be unique within the subflow",
                    "Give each action a different stable id.",
                )
            )
        else:
            action_ids.add(action_id)
            normalized_action_id = _safe_now_id(action_id)
            if not normalized_action_id:
                errors.append(
                    _validation_error(
                        f"{action_path}.id",
                        "Action id cannot be normalized to a ServiceNow SDK id",
                        "Use letters, numbers, and underscores; include at least one alphanumeric "
                        "character.",
                    )
                )
            elif normalized_action_id in safe_action_ids:
                errors.append(
                    _validation_error(
                        f"{action_path}.id",
                        "Action ids normalize to the same ServiceNow SDK id",
                        (
                            f"Change this id or {safe_action_ids[normalized_action_id]!r}; "
                            f"both normalize to {normalized_action_id!r}."
                        ),
                    )
                )
            else:
                safe_action_ids[normalized_action_id] = action_id
                action_id_can_render = True

        action_type = action.get("type")
        logic_type = _canonical_flow_logic_type(action_type)
        if not action_type or not isinstance(action_type, str):
            errors.append(
                _validation_error(
                    f"{action_path}.type",
                    "Action type is required and must be a string",
                    "Use an action type returned by /available-actions.",
                )
            )
        elif (
            not logic_type
            and action_catalog_available
            and action_type not in available_action_types
        ):
            errors.append(
                _validation_error(
                    f"{action_path}.type",
                    "Unsupported action or Flow Designer logic type",
                    f"{_action_type_validation_hint(action_catalog)} {_flow_logic_type_hint()}",
                )
            )

        inputs = action.get("inputs", {}) if logic_type else action.get("inputs")
        if not isinstance(inputs, dict):
            errors.append(
                _validation_error(
                    f"{action_path}.inputs",
                    "Action inputs must be an object",
                    "Set inputs to the action input object expected by the Flow Server.",
                )
            )
            continue

        if action_catalog_available and not logic_type:
            catalog_action = next(
                (a for a in action_catalog.get("actions", []) if a.get("type") == action_type),
                None,
            )
            if catalog_action:
                snippet_inputs = catalog_action.get("snippet", {}).get("inputs", {})
                if snippet_inputs:
                    for input_key in inputs:
                        if input_key not in snippet_inputs:
                            valid_keys = ", ".join(snippet_inputs.keys())
                            warnings.append(
                                _validation_warning(
                                    f"{action_path}.inputs.{input_key}",
                                    (
                                        f"Input {input_key!r} is not present in the "
                                        f"/available-actions snippet for {action_type!r}"
                                    ),
                                    (
                                        "Snippets are examples, not exhaustive schemas. "
                                        f"Snippet input keys: {valid_keys}"
                                    ),
                                )
                            )

        errors.extend(
            _validate_data_pills(
                inputs,
                f"{action_path}.inputs",
                allowed_roots=SUBFLOW_DATA_PILL_ROOTS,
            )
        )
        errors.extend(
            _validate_subflow_data_pill_references(
                inputs,
                f"{action_path}.inputs",
                declared_input_names,
                rendered_action_ids,
                rendered_safe_action_ids,
            )
        )
        _warn_unrendered_partial_data_pills(
            inputs,
            f"{action_path}.inputs",
            warnings,
        )

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
            if "log_level" in inputs and inputs["log_level"] not in ("error", "info", "warn"):
                errors.append(
                    _validation_error(
                        f"{action_path}.inputs.log_level",
                        "core.log input log_level must be 'error', 'info', or 'warn'",
                        "Use exactly lowercase 'error', 'info', or 'warn'.",
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
                errors.append(
                    _validation_error(
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
            if "watermark_email" in inputs and not isinstance(inputs["watermark_email"], bool):
                errors.append(
                    _validation_error(
                        f"{action_path}.inputs.watermark_email",
                        "core.sendEmail input watermark_email must be a boolean",
                        "Use true or false without quotes.",
                    )
                )
            if _is_placeholder_recipient_value(inputs.get("ah_to")):
                errors.append(
                    _validation_error(
                        f"{action_path}.inputs.ah_to",
                        "core.sendEmail input ah_to is still a template placeholder",
                        "Set ah_to to the real recipient email or a valid data pill.",
                    )
                )
        if logic_type:
            _validate_flow_logic_action(
                action,
                logic_type,
                action_path,
                errors,
                warnings,
                action_catalog,
                SUBFLOW_DATA_PILL_ROOTS,
                in_loop=False,
                depth=0,
            )

        if action_id_can_render:
            rendered_action_ids.add(action_id)
            rendered_safe_action_ids.add(normalized_action_id)


def _subflow_action_reference_sets(actions: Any) -> tuple[set[str], set[str]]:
    if not isinstance(actions, list):
        return set(), set()

    action_ids = set()
    safe_action_ids = set()
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("id"), str):
            continue
        action_ids.add(action["id"])
        safe_id = _safe_now_id(action["id"])
        if safe_id:
            safe_action_ids.add(safe_id)
    return action_ids, safe_action_ids


def _validate_subflow_output_values(
    output_values: Any,
    declared_output_names: set[str],
    declared_input_names: set[str],
    action_ids: set[str],
    safe_action_ids: set[str],
    errors: list[Dict[str, str]],
    warnings: list[Dict[str, str]],
) -> None:
    if not isinstance(output_values, dict):
        errors.append(
            _validation_error(
                "subflowSpec.outputValues",
                "subflowSpec.outputValues must be an object",
                'Use a map such as {"status": "okay"}.',
            )
        )
        return

    for output_name in output_values:
        if output_name not in declared_output_names:
            errors.append(
                _validation_error(
                    f"subflowSpec.outputValues.{output_name}",
                    "Output value targets an undeclared output",
                    "Declare the output in subflowSpec.outputs before assigning it.",
                )
            )

    errors.extend(
        _validate_data_pills(
            output_values,
            "subflowSpec.outputValues",
            allowed_roots=SUBFLOW_DATA_PILL_ROOTS,
        )
    )
    errors.extend(
        _validate_subflow_data_pill_references(
            output_values,
            "subflowSpec.outputValues",
            declared_input_names,
            action_ids,
            safe_action_ids,
        )
    )
    _warn_unrendered_partial_data_pills(
        output_values,
        "subflowSpec.outputValues",
        warnings,
    )


def _validate_subflow_request_details(
    body: Dict[str, Any],
    require_credentials: bool,
    action_catalog: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    errors: list[Dict[str, str]] = []
    warnings: list[Dict[str, str]] = []

    if require_credentials:
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
                    "Use the real ServiceNow instance URL or omit it so runtime config can fill "
                    "it.",
                )
            )
        elif body.get("instanceUrl") and not _is_valid_instance_url(body.get("instanceUrl")):
            errors.append(
                _validation_error(
                    "instanceUrl",
                    "instanceUrl must be an absolute http(s) URL",
                    "Use a value such as https://dev305525.service-now.com.",
                )
            )

        if _is_placeholder_username(body.get("username")):
            errors.append(
                _validation_error(
                    "username",
                    "username is still a template placeholder",
                    "Use the real username or omit it so runtime basic auth can fill it.",
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

        for field_name in ("scopeId", "scopeName"):
            if _is_placeholder_scope_value(body.get(field_name)):
                errors.append(
                    _validation_error(
                        field_name,
                        f"{field_name} is still a template placeholder",
                        (
                            f"Set top-level {field_name} from the target application "
                            "or subflow scope before deployment."
                        ),
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

    subflow_spec = body.get("subflowSpec")
    if not isinstance(subflow_spec, dict):
        errors.append(
            _validation_error(
                "subflowSpec",
                "subflowSpec is required and must be an object",
                "Set subflowSpec with id, name, runAs, inputs, outputs, and actions.",
            )
        )
        return {"valid": False, "errors": errors, "warnings": warnings}

    for field_name in SUBFLOW_SPEC_REQUIRED_FIELD_NAMES:
        if field_name not in subflow_spec:
            errors.append(
                _validation_error(
                    f"subflowSpec.{field_name}",
                    f"subflowSpec.{field_name} is required",
                    f"Add subflowSpec.{field_name}; use an empty object for no inputs/outputs.",
                )
            )

    subflow_id = subflow_spec.get("id")
    if isinstance(subflow_id, str):
        normalized_subflow_id = _flow_spec_id(subflow_id)
        if subflow_id != normalized_subflow_id:
            warnings.append(
                _validation_warning(
                    "subflowSpec.id",
                    "subflowSpec.id will be normalized by the Flow Server",
                    f"Use {normalized_subflow_id!r} to avoid surprises.",
                )
            )
    elif subflow_id is not None:
        errors.append(
            _validation_error(
                "subflowSpec.id",
                "subflowSpec.id must be a string",
                "Use a stable lowercase identifier such as my_subflow.",
            )
        )

    if subflow_spec.get("runAs") and subflow_spec["runAs"] not in SUPPORTED_RUN_AS:
        errors.append(
            _validation_error(
                "subflowSpec.runAs",
                "subflowSpec.runAs must be system or user",
                'Use runAs: "system" unless the subflow should run as the caller.',
            )
        )

    if "inputs" in subflow_spec:
        _validate_subflow_io_map(subflow_spec.get("inputs"), "subflowSpec.inputs", errors)
        _warn_subflow_io_extra_fields(
            subflow_spec.get("inputs"),
            "subflowSpec.inputs",
            warnings,
        )
    if "outputs" in subflow_spec:
        _validate_subflow_io_map(subflow_spec.get("outputs"), "subflowSpec.outputs", errors)
        _warn_subflow_io_extra_fields(
            subflow_spec.get("outputs"),
            "subflowSpec.outputs",
            warnings,
        )
        if (
            isinstance(subflow_spec.get("outputs"), dict)
            and subflow_spec["outputs"]
            and not subflow_spec.get("outputValues")
        ):
            warnings.append(
                _validation_warning(
                    "subflowSpec.outputs",
                    "Subflow outputs are declared but not assigned",
                    (
                        "Add subflowSpec.outputValues to set output values with "
                        "wfa.flowLogic.assignSubflowOutputs."
                    ),
                )
            )
    if "actions" in subflow_spec:
        input_names = (
            set(str(name) for name in subflow_spec.get("inputs", {}))
            if isinstance(subflow_spec.get("inputs"), dict)
            else set()
        )
        _validate_subflow_actions(
            subflow_spec.get("actions"),
            errors,
            warnings,
            action_catalog,
            declared_input_names=input_names,
        )
    if "outputValues" in subflow_spec:
        input_names = (
            set(str(name) for name in subflow_spec.get("inputs", {}))
            if isinstance(subflow_spec.get("inputs"), dict)
            else set()
        )
        output_names = (
            set(str(name) for name in subflow_spec.get("outputs", {}))
            if isinstance(subflow_spec.get("outputs"), dict)
            else set()
        )
        action_ids, safe_action_ids = _subflow_action_reference_sets(subflow_spec.get("actions"))
        _validate_subflow_output_values(
            subflow_spec.get("outputValues"),
            output_names,
            input_names,
            action_ids,
            safe_action_ids,
            errors,
            warnings,
        )

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _validate_deployment_request(body: Dict[str, Any]) -> Optional[str]:
    details = _validate_deployment_request_details(body)
    if details["valid"]:
        return None
    first_error = details["errors"][0]
    return f"{first_error['path']}: {first_error['message']}"


def _resolve_timeout_seconds(params: Any) -> float:
    if params.timeout is None or "timeout_seconds" in params.model_fields_set:
        return float(params.timeout_seconds)

    if params.timeout >= 1000:
        return params.timeout / 1000.0
    return float(params.timeout)


def _validation_hints(action_catalog: Optional[Dict[str, Any]] = None) -> list[str]:
    return [
        (
            "Send only instanceUrl, username, password, scopeId, scopeName, reinstall, "
            "optional skipInstallFallback, and flowSpec at the top level."
        ),
        "Omitted or placeholder scopeId values are normalized to global; scopeName is formatted "
        "like the flow id.",
        "Do not wrap the body in deployment_request unless you need backward compatibility.",
        "Do not include MCP-only fields in the Flow Server body.",
        f"Supported trigger types: {', '.join(SUPPORTED_TRIGGER_TYPES)}.",
        (
            "Put trigger-level logic in flowSpec.trigger.condition as an encoded query "
            "such as priority=1^active=true."
        ),
        _action_type_validation_hint(action_catalog),
        "Data pills must start with {{trigger.*}} or {{action.*}}.",
        "For deployment retry, call create_flow again; do not use workflow activation tools.",
    ]


def _subflow_validation_hints(action_catalog: Optional[Dict[str, Any]] = None) -> list[str]:
    return [
        "Send Subflow deployment fields at the top level.",
        "Match the SDK example shape from src/examples/subflow_tests/01_test_subflow.json.",
        "Use subflowSpec instead of flowSpec.",
        "Do not include a trigger; Subflows use subflowSpec.inputs and subflowSpec.outputs.",
        "subflowSpec.runAs is optional; omit it unless explicitly needed.",
        "Subflow input/output types must be string, integer, or boolean.",
        "Subflow input data pills must use {{input.<name>}} or {{inputs.<name>}} for declared "
        "inputs.",
        "Subflow action data pills must reference an earlier action: {{action.<earlier_action_id>."
        "<field>}}.",
        "Do not use {{trigger.*}} in subflows.",
        "Omitted or placeholder scopeId values are normalized to global; scopeName is formatted "
        "like the subflow id.",
        'Wrap mixed text data pills in a template object, for example {"template": "Created '
        '{{input.input_text}}"}.',
        "For core.sendEmail, keep ah_body static and set watermark_email to true or false.",
        "Set output values with subflowSpec.outputValues, keyed by declared subflowSpec.outputs "
        "names.",
        _action_type_validation_hint(action_catalog),
        "For source generation, the Flow Server receives the subflowSpec payload.",
    ]


def _subflow_request_shape_hint() -> Dict[str, Any]:
    return {
        "instanceUrl": "https://your-instance.service-now.com",
        "username": USERNAME_PLACEHOLDER,
        "password": "<password>",
        "scopeId": DEFAULT_SCOPE_ID,
        "scopeName": _format_scope_name("test_subflow"),
        "reinstall": False,
        "subflowSpec": {
            "id": "test_subflow",
            "name": "Test Subflow",
            "description": "My test subflow.",
            "inputs": {
                "input_text": {
                    "type": "string",
                    "label": "Input Text",
                    "mandatory": True,
                }
            },
            "outputs": {
                "result": {
                    "type": "string",
                    "label": "Result",
                }
            },
            "outputValues": {
                "result": "okay",
            },
            "actions": [
                {
                    "id": "log1",
                    "type": "core.log",
                    "inputs": {
                        "log_level": "info",
                        "log_message": {"template": "Subflow executed for {{input.input_text}}"},
                    },
                }
            ],
        },
    }


def list_flow_actions(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ListFlowActionsParams,
) -> Dict[str, Any]:
    """List Flow Server SDK actions from /available-actions."""
    del config, auth_manager

    action_catalog = _load_available_action_catalog(params.flow_server_url)
    actions = action_catalog.get("actions", [])
    if params.type_filter:
        normalized_filter = params.type_filter.lower()
        actions = [
            action for action in actions if normalized_filter in str(action.get("type", "")).lower()
        ]

    paged_actions = actions[params.offset : params.offset + params.limit]
    return {
        "success": True,
        "catalogAvailable": bool(action_catalog.get("available")),
        "message": (
            "Loaded Flow Server SDK actions from /available-actions"
            if action_catalog.get("available")
            else (
                "Flow Server /available-actions was unavailable; returning fallback "
                "action types only."
            )
        ),
        "source": action_catalog.get("source"),
        "endpoint": action_catalog.get("endpoint"),
        "count": len(actions),
        "limit": params.limit,
        "offset": params.offset,
        "omitted_count": max(len(actions) - (params.offset + len(paged_actions)), 0),
        "actions": [
            _compact_action_entry(action, include_snippets=params.include_snippets)
            for action in paged_actions
        ],
        **({"error": action_catalog["error"]} if action_catalog.get("error") else {}),
    }


def validate_subflow_json(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateSubflowJsonParams,
) -> Dict[str, Any]:
    """Validate a Flow Server Subflow JSON body without deploying it."""
    del auth_manager

    logger.info(
        "MCP_SUBFLOW_VALIDATE_START input_fields=%s fill_runtime_credentials=%s",
        sorted(params.model_fields_set),
        params.fill_runtime_credentials,
    )
    body, build_error = _build_subflow_request(params)
    body = _ensure_subflow_request_body(body)
    if not build_error and params.fill_runtime_credentials:
        body = _merge_runtime_credentials(config, body)
    if not build_error:
        body, sanitizations = _sanitize_flow_server_request(body, "subflowSpec")
    else:
        sanitizations = []

    password = str(body.get("password") or "")
    compact_request = _subflow_response_request_body(body, password=password)

    if build_error:
        logger.warning("MCP_SUBFLOW_VALIDATE_END valid=false build_error=%s", build_error)
        return {
            "valid": False,
            "errors": [
                _validation_error(
                    "json_text",
                    build_error,
                    "Provide a JSON object with a top-level subflowSpec.",
                )
            ],
            "warnings": [],
            "hints": _subflow_validation_hints(),
            "expected_request_shape": _subflow_request_shape_hint(),
        }

    action_catalog = _load_available_action_catalog(params.flow_server_url)
    validation = _validate_subflow_request_details(
        body,
        require_credentials=True,
        action_catalog=action_catalog,
    )
    compact_validation = _compact_validation_details(validation)
    logger.info(
        "MCP_SUBFLOW_VALIDATE_END valid=%s error_count=%s warning_count=%s summary=%s",
        validation["valid"],
        len(validation["errors"]),
        len(validation["warnings"]),
        _subflow_request_log_summary(body),
    )
    return {
        "valid": validation["valid"],
        "errors": compact_validation["errors"],
        "warnings": compact_validation["warnings"],
        "error_count": compact_validation["error_count"],
        "warning_count": compact_validation["warning_count"],
        **(
            {"errors_omitted_count": compact_validation["errors_omitted_count"]}
            if "errors_omitted_count" in compact_validation
            else {}
        ),
        **(
            {"warnings_omitted_count": compact_validation["warnings_omitted_count"]}
            if "warnings_omitted_count" in compact_validation
            else {}
        ),
        "hints": _subflow_validation_hints(action_catalog),
        "request": compact_request,
        "appliedSanitizations": sanitizations,
        "actionCatalog": _compact_action_catalog_summary(action_catalog),
        "clientGuidance": _subflow_client_guidance(),
        "expected_request_shape": None if validation["valid"] else _subflow_request_shape_hint(),
    }


def hint_subflow_json(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: HintSubflowJsonParams,
) -> Dict[str, Any]:
    """Return a ready-to-fill Subflow deployment body."""
    del auth_manager

    username, _password = _current_basic_credentials(config)
    instance_url = (
        config.instance_url
        if config.instance_url != "https://example.invalid"
        else "https://your-instance.service-now.com"
    )
    subflow_id = params.subflow_id or _flow_spec_id(params.subflow_name)
    placeholder_warnings = []
    generated_scope_id = DEFAULT_SCOPE_ID
    generated_scope_name = _format_scope_name(subflow_id)
    hinted_body = {
        "instanceUrl": instance_url,
        "username": username or USERNAME_PLACEHOLDER,
        "password": "<password>",
        "scopeId": params.scopeId or generated_scope_id,
        "scopeName": generated_scope_name,
        "reinstall": params.reinstall,
        "subflowSpec": {
            "id": subflow_id,
            "name": params.subflow_name,
            "description": params.description,
            "inputs": {
                "input_text": {
                    "type": "string",
                    "label": "Input Text",
                    "mandatory": True,
                }
            },
            "outputs": {
                "result": {
                    "type": "string",
                    "label": "Result",
                }
            },
            "outputValues": {
                "result": "okay",
            },
            "actions": [
                {
                    "id": "log1",
                    "type": "core.log",
                    "inputs": {
                        "log_level": "info",
                        "log_message": {"template": "Subflow executed for {{input.input_text}}"},
                    },
                }
            ],
        },
    }
    if params.runAs:
        hinted_body["subflowSpec"]["runAs"] = params.runAs
    logger.info("MCP_SUBFLOW_HINT_END summary=%s", _subflow_request_log_summary(hinted_body))
    request_summary = _subflow_request_log_summary(hinted_body)
    compact_request = _subflow_response_request_body(
        hinted_body,
        password=str(hinted_body.get("password") or ""),
    )

    if params.include_full_json:
        return {
            "responseMode": "full_json",
            "requestSummary": request_summary,
            "request": hinted_body,
            "warnings": placeholder_warnings,
            "clientGuidance": _subflow_client_guidance(),
        }

    return {
        "responseMode": "compact",
        "requestSummary": request_summary,
        "request": compact_request,
        "warnings": placeholder_warnings,
        "fullRequestOmitted": True,
        "message": (
            "Full generated Subflow JSON is omitted by default to keep MCP client "
            "context small. Set include_full_json=true only when needed."
        ),
        "clientGuidance": _subflow_client_guidance(),
    }


def generate_subflow_source(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: GenerateSubflowSourceParams,
) -> Dict[str, Any]:
    """Generate TypeScript source for a Subflow through /generate-subflow-source."""
    del config, auth_manager

    body, build_error = _build_subflow_request(params)
    body = _ensure_subflow_request_body(body)
    if not build_error:
        body, sanitizations = _sanitize_flow_server_request(body, "subflowSpec")
    else:
        sanitizations = []
    password = str(body.get("password") or "")
    compact_request = _subflow_response_request_body(body, password=password)

    if build_error:
        return {
            "success": False,
            "message": "Subflow source generation was not attempted because JSON parsing failed.",
            "error": build_error,
            "hints": _subflow_validation_hints(),
            "expected_request_shape": _subflow_request_shape_hint(),
        }

    action_catalog = _load_available_action_catalog(params.flow_server_url)
    validation = _validate_subflow_request_details(
        body,
        require_credentials=False,
        action_catalog=action_catalog,
    )
    compact_validation = _compact_validation_details(validation)
    if not validation["valid"]:
        first_error = validation["errors"][0]
        return {
            "success": False,
            "message": "Subflow source generation was not attempted; local validation failed.",
            "error": f"{first_error['path']}: {first_error['message']}",
            "validation": compact_validation,
            "hints": _subflow_validation_hints(action_catalog),
            "request": compact_request,
            "appliedSanitizations": sanitizations,
            "actionCatalog": _compact_action_catalog_summary(action_catalog),
            "expected_request_shape": _subflow_request_shape_hint(),
        }

    flow_server_url = get_flow_server_url(params.flow_server_url)
    endpoint = f"{flow_server_url}/generate-subflow-source"
    source_payload = {"subflowSpec": body.get("subflowSpec", body)}
    timeout_seconds = _resolve_timeout_seconds(params)
    logger.info(
        "MCP_SUBFLOW_SOURCE_START endpoint=%s timeout_seconds=%s summary=%s",
        endpoint,
        timeout_seconds,
        _subflow_request_log_summary(body),
    )

    try:
        response = requests.post(
            endpoint,
            json=source_payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        result = _parse_response(response, password=password)
        success = 200 <= response.status_code < 300
        operation_result = {
            "success": success,
            "message": (
                "Subflow source generation completed"
                if success
                else "Subflow source generation failed"
            ),
            "status_code": response.status_code,
            "flow_server_endpoint": endpoint,
            "serverReturnData": _source_payload_summary(
                result,
                include_source=params.include_source,
            ),
            "serverReturnDataMode": (
                "source_included" if params.include_source else "compact_source_preview"
            ),
            "validation": compact_validation,
            "appliedSanitizations": sanitizations,
            "actionCatalog": _compact_action_catalog_summary(action_catalog),
        }
        server_error = _extract_flow_server_error(result)
        if server_error:
            operation_result["serverError"] = server_error
        return _subflow_response_body(body, operation_result, password=password)
    except requests.RequestException as exc:
        operation_result = {
            "success": False,
            "message": "Error calling the generic flow server /generate-subflow-source endpoint",
            "flow_server_endpoint": endpoint,
            "error": _redact_text(str(exc), password=password),
            "serverReturnData": None,
            "appliedSanitizations": sanitizations,
        }
        return _subflow_response_body(body, operation_result, password=password)


def deploy_subflow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: DeploySubflowParams,
) -> Dict[str, Any]:
    """Deploy a Flow Designer Subflow through /deploy-subflow."""
    del auth_manager

    started_at = time.monotonic()
    body, build_error = _build_subflow_request(params)
    body = _ensure_subflow_request_body(body)
    if build_error:
        return {
            "success": False,
            "message": "Subflow deployment was not attempted because JSON parsing failed.",
            "error": build_error,
            "hints": _subflow_validation_hints(),
            "expected_request_shape": _subflow_request_shape_hint(),
        }

    body, sanitizations = _sanitize_flow_server_request(
        _merge_runtime_credentials(config, body),
        "subflowSpec",
    )
    body = _flow_server_body_fields(body, SUBFLOW_DEPLOYMENT_FIELD_NAMES)
    password = str(body.get("password") or "")
    compact_request = _subflow_response_request_body(body, password=password)
    flow_server_url = get_flow_server_url(params.flow_server_url)
    action_catalog = _load_available_action_catalog(flow_server_url)
    validation = _validate_subflow_request_details(
        body,
        require_credentials=True,
        action_catalog=action_catalog,
    )
    compact_validation = _compact_validation_details(validation)
    request_summary = _subflow_request_log_summary(body)
    logger.info("MCP_SUBFLOW_DEPLOY_REQUEST_READY summary=%s", request_summary)

    if not validation["valid"]:
        first_error = validation["errors"][0]
        return {
            "success": False,
            "message": "Subflow deployment was not attempted; local validation failed.",
            "error": f"{first_error['path']}: {first_error['message']}",
            "validation": compact_validation,
            "hints": _subflow_validation_hints(action_catalog),
            "request": compact_request,
            "appliedSanitizations": sanitizations,
            "actionCatalog": _compact_action_catalog_summary(action_catalog),
            "expected_request_shape": _subflow_request_shape_hint(),
            "clientGuidance": _subflow_client_guidance(),
        }

    endpoint = f"{flow_server_url}/deploy-subflow"
    timeout_seconds = _resolve_timeout_seconds(params)
    logger.info(
        "MCP_SUBFLOW_DEPLOY_START endpoint=%s timeout_seconds=%s summary=%s",
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
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        operation_result = {
            "success": success,
            "message": (
                "Subflow deployment request completed"
                if success
                else "Subflow deployment request failed"
            ),
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "flow_server_endpoint": endpoint,
            "server_response": "See serverReturnData.",
            "serverReturnData": server_return_data,
            "serverReturnDataMode": (
                "bounded_with_log_previews" if params.include_server_logs else "compact"
            ),
            "validation": compact_validation,
            "appliedSanitizations": sanitizations,
            "actionCatalog": _compact_action_catalog_summary(action_catalog),
        }
        server_error = _extract_flow_server_error(result)
        if server_error:
            operation_result["serverError"] = server_error
        return _subflow_response_body(body, operation_result, password=password)
    except requests.RequestException as exc:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        logger.exception(
            "MCP_SUBFLOW_DEPLOY_ERROR endpoint=%s elapsed_ms=%s error=%s summary=%s",
            endpoint,
            elapsed_ms,
            exc,
            request_summary,
        )
        operation_result = {
            "success": False,
            "message": "Error calling the generic flow server /deploy-subflow endpoint",
            "flow_server_endpoint": endpoint,
            "error": _redact_text(str(exc), password=password),
            "serverReturnData": None,
            "appliedSanitizations": sanitizations,
        }
        return _subflow_response_body(body, operation_result, password=password)


def verify_subflow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: VerifySubflowParams,
) -> Dict[str, Any]:
    """Verify that a Subflow exists in ServiceNow through /verify-subflow."""
    del auth_manager

    body, build_error = _build_verify_subflow_request(params)
    if build_error:
        return {
            "success": False,
            "message": "Subflow verification was not attempted because JSON parsing failed.",
            "error": build_error,
        }

    body, sanitizations = _sanitize_scope_fields(_merge_runtime_credentials(config, body))
    if not body.get("subflowName") and body.get("name"):
        body["subflowName"] = body["name"]
    if not body.get("flowName"):
        body["flowName"] = body.get("subflowName") or body.get("name")
    password = str(body.get("password") or "")
    if not body.get("flowName"):
        return {
            "success": False,
            "message": "flowName is required",
            "hint": "Set subflow_name, subflowName, or flowName to the Flow Designer subflow name.",
        }

    missing_fields = [
        field_name
        for field_name in ("instanceUrl", "username", "password", "scopeId", "scopeName")
        if not body.get(field_name)
    ]
    if missing_fields:
        return {
            "success": False,
            "message": "Missing required verification fields",
            "missing_fields": missing_fields,
            "appliedSanitizations": sanitizations,
        }

    flow_server_url = get_flow_server_url(params.flow_server_url)
    endpoint = f"{flow_server_url}/verify-subflow"
    timeout_seconds = _resolve_timeout_seconds(params)
    compact_request = _redact_sensitive_values(body, secrets=(password,))
    logger.info(
        "MCP_SUBFLOW_VERIFY_START endpoint=%s timeout_seconds=%s subflowName=%s",
        endpoint,
        timeout_seconds,
        body.get("flowName"),
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
        return {
            "success": success,
            "message": (
                "Subflow verification completed" if success else "Subflow verification failed"
            ),
            "status_code": response.status_code,
            "flow_server_endpoint": endpoint,
            "request": compact_request,
            "appliedSanitizations": sanitizations,
            "serverReturnData": _compact_server_result(result),
        }
    except requests.RequestException as exc:
        return {
            "success": False,
            "message": "Error calling the generic flow server /verify-subflow endpoint",
            "flow_server_endpoint": endpoint,
            "request": compact_request,
            "appliedSanitizations": sanitizations,
            "error": _redact_text(str(exc), password=password),
        }


def edit_flow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: EditFlowParams,
) -> Dict[str, Any]:
    """Edit an existing Flow Server flow request, validate it, and optionally redeploy it."""
    logger.info(
        "MCP_FLOW_EDIT_START deploy=%s dry_run=%s input_fields=%s",
        params.deploy,
        params.dry_run,
        sorted(params.model_fields_set),
    )
    body, build_error = _build_deployment_request(params)
    if build_error:
        return {
            "success": False,
            "status": "blocked",
            "operation": "edit_flow",
            "message": "Flow edit was not attempted because the supplied JSON could not be parsed.",
            "error": build_error,
            "hints": _validation_hints(),
            "expected_request_shape": _request_shape_hint(),
        }

    body = _ensure_flow_request_body(body)
    if not isinstance(body.get("flowSpec"), dict):
        return {
            "success": False,
            "status": "blocked",
            "operation": "edit_flow",
            "message": "Provide deployment_request/json_text with a flowSpec object to edit.",
            "expected_request_shape": _request_shape_hint(),
        }

    before_summary = _flow_request_log_summary(body)
    edited_body = deepcopy(body)
    try:
        edit_summary = _apply_flow_edits(edited_body, params)
    except ValueError as exc:
        return {
            "success": False,
            "status": "blocked",
            "operation": "edit_flow",
            "message": "Flow edit could not be applied.",
            "error": str(exc),
            "beforeSummary": before_summary,
        }

    edited_body, sanitizations = _sanitize_flow_server_request(
        _merge_runtime_credentials(config, edited_body),
        "flowSpec",
    )
    flow_server_url = get_flow_server_url(params.flow_server_url)
    action_catalog = _load_available_action_catalog(flow_server_url)
    validation = _validate_deployment_request_details(
        edited_body,
        action_catalog=action_catalog,
    )
    compact_validation = _compact_validation_details(validation)
    password = str(edited_body.get("password") or "")
    request_payload = (
        _redact_sensitive_values(edited_body, secrets=(password,))
        if params.include_full_request
        else _flow_response_request_body(edited_body, password=password)
    )

    response: Dict[str, Any] = {
        "success": validation["valid"],
        "status": (
            "dry_run" if params.dry_run else ("validated" if validation["valid"] else "blocked")
        ),
        "operation": "edit_flow",
        "message": (
            "Edited flow request is valid."
            if validation["valid"]
            else "Edited flow request has validation errors."
        ),
        "beforeSummary": before_summary,
        "afterSummary": _flow_request_log_summary(edited_body),
        "editSummary": edit_summary,
        "validation": compact_validation,
        "request": request_payload,
        "requestMode": "full_redacted" if params.include_full_request else "compact",
        "appliedSanitizations": sanitizations,
        "actionCatalog": _compact_action_catalog_summary(action_catalog),
        "clientGuidance": _flow_client_guidance(),
    }

    if not validation["valid"]:
        response["hints"] = _validation_hints(action_catalog)
        response["expected_request_shape"] = _request_shape_hint()
        return response

    if params.deploy and not params.dry_run:
        deployment = create_flow(
            config,
            auth_manager,
            CreateFlowParams(
                deployment_request=edited_body,
                flow_server_url=params.flow_server_url,
                timeout_seconds=params.timeout_seconds,
                timeout=params.timeout,
                include_server_logs=params.include_server_logs,
            ),
        )
        deployment_success = bool(
            deployment.get("success")
            or (
                isinstance(deployment.get("deploymentResult"), dict)
                and deployment["deploymentResult"].get("success")
            )
        )
        response["status"] = "deployed" if deployment_success else "deploy_failed"
        response["success"] = deployment_success
        response["deploymentResult"] = deployment
    elif params.deploy and params.dry_run:
        response["message"] = "Edited flow request is valid. Dry run skipped deployment."

    logger.info(
        "MCP_FLOW_EDIT_END status=%s valid=%s summary=%s",
        response["status"],
        validation["valid"],
        response["afterSummary"],
    )
    return response


def validate_flow_json(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ValidateFlowJsonParams,
) -> Dict[str, Any]:
    """Validate a Flow Server JSON body without deploying it."""
    del auth_manager

    logger.info(
        "MCP_FLOW_VALIDATE_START input_fields=%s fill_runtime_credentials=%s",
        sorted(params.model_fields_set),
        params.fill_runtime_credentials,
    )
    body, build_error = _build_deployment_request(params)
    if not build_error and params.fill_runtime_credentials:
        body = _merge_runtime_credentials(config, body)
    if not build_error:
        body, sanitizations = _sanitize_flow_server_request(body, "flowSpec")
    else:
        sanitizations = []

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

    action_catalog = _load_available_action_catalog(params.flow_server_url)
    validation = _validate_deployment_request_details(body, action_catalog=action_catalog)
    compact_validation = _compact_validation_details(validation)
    logger.info(
        "MCP_FLOW_VALIDATE_END valid=%s error_count=%s warning_count=%s summary=%s",
        validation["valid"],
        len(validation["errors"]),
        len(validation["warnings"]),
        _flow_request_log_summary(body),
    )
    return {
        "valid": validation["valid"],
        "errors": compact_validation["errors"],
        "warnings": compact_validation["warnings"],
        "error_count": compact_validation["error_count"],
        "warning_count": compact_validation["warning_count"],
        **(
            {"errors_omitted_count": compact_validation["errors_omitted_count"]}
            if "errors_omitted_count" in compact_validation
            else {}
        ),
        **(
            {"warnings_omitted_count": compact_validation["warnings_omitted_count"]}
            if "warnings_omitted_count" in compact_validation
            else {}
        ),
        "hints": _validation_hints(action_catalog),
        "request": compact_request,
        "appliedSanitizations": sanitizations,
        "actionCatalog": _compact_action_catalog_summary(action_catalog),
        "clientGuidance": _flow_client_guidance(),
        "expected_request_shape": None if validation["valid"] else _request_shape_hint(),
    }


def hint_flow_json(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: HintFlowJsonParams,
) -> Dict[str, Any]:
    """Return a ready-to-fill Flow Server JSON body."""
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
    placeholder_warnings = []
    if params.include_email_action and not params.recipient_email:
        placeholder_warnings.append(
            "recipient_email is a placeholder; set the real recipient before deployment."
        )

    generated_scope_id = DEFAULT_SCOPE_ID
    generated_scope_name = _format_scope_name(flow_id)
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
                    "ah_to": params.recipient_email or RECIPIENT_EMAIL_PLACEHOLDER,
                    "ah_subject": {
                        "template": f"{params.flow_name}: {{{{trigger.current.number}}}}"
                    },
                    "ah_body": DEFAULT_STATIC_EMAIL_BODY,
                },
            }
        )

    hinted_body = {
        "instanceUrl": instance_url,
        "username": username or USERNAME_PLACEHOLDER,
        "password": "<password>",
        "scopeId": params.scopeId or generated_scope_id,
        "scopeName": generated_scope_name,
        "reinstall": params.reinstall,
        "flowSpec": {
            "id": flow_id,
            "name": params.flow_name,
            "description": params.description,
            "runAs": params.runAs,
            "trigger": {
                "type": params.trigger_type,
                "table": params.table,
                "condition": params.trigger_condition,
            },
            "actions": actions,
        },
    }
    logger.info("MCP_FLOW_HINT_END summary=%s", _flow_request_log_summary(hinted_body))
    request_summary = _flow_request_log_summary(hinted_body)
    compact_request = _flow_response_request_body(
        hinted_body,
        password=str(hinted_body.get("password") or ""),
    )

    if params.include_full_json:
        return {
            "responseMode": "full_json",
            "requestSummary": request_summary,
            "request": hinted_body,
            "warnings": placeholder_warnings,
            "clientGuidance": _flow_client_guidance(),
        }

    return {
        "responseMode": "compact",
        "requestSummary": request_summary,
        "request": compact_request,
        "warnings": placeholder_warnings,
        "fullRequestOmitted": True,
        "message": (
            "Full generated JSON is omitted by default to keep MCP client context small. "
            "Set include_full_json=true only when the full template body is needed."
        ),
        "clientGuidance": _flow_client_guidance(),
    }


def create_incident_admin_notification_flow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateIncidentAdminNotificationFlowParams,
) -> Dict[str, Any]:
    """Create the default incident-created admin email notification flow."""
    missing_fields = [
        field_name for field_name in ("recipient_email",) if not getattr(params, field_name, None)
    ]
    if missing_fields:
        return {
            "success": False,
            "message": (
                "Flow deployment was not attempted because an explicit recipient value is required."
            ),
            "missing_fields": missing_fields,
            "hint": (
                "Provide recipient_email for the notification target. Scope defaults "
                "to scopeId=global, and scopeName is formatted like the flow id."
            ),
            "expected_request_shape": _request_shape_hint(),
        }

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
        result["explicitInputs"] = {
            "scopeId": params.scopeId,
            "scopeName": params.scopeName,
            "recipient_email": params.recipient_email,
            "trigger_table": "incident",
            "trigger_type": "record.created",
            "post_deployment_testing": "not attempted",
        }
        guidance = result.setdefault("clientGuidance", [])
        if isinstance(guidance, list):
            guidance.append(
                (
                    "recipient_email was provided explicitly. Scope defaults to "
                    "scopeId=global, and scopeName is formatted like the flow id."
                )
            )
    return result


def create_flow(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: CreateFlowParams,
) -> Dict[str, Any]:
    """Deploy a Flow Designer flow through the local generic flow server."""
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

    body, sanitizations = _sanitize_flow_server_request(
        _merge_runtime_credentials(config, body),
        "flowSpec",
    )
    flow_server_url = get_flow_server_url(params.flow_server_url)
    action_catalog = _load_available_action_catalog(flow_server_url)
    validation_details = _validate_deployment_request_details(
        body,
        action_catalog=action_catalog,
    )
    compact_validation = _compact_validation_details(validation_details)
    password = str(body.get("password") or "")
    compact_request = _flow_response_request_body(body, password=password)
    request_summary = _flow_request_log_summary(body)
    logger.info("MCP_FLOW_CREATE_REQUEST_READY summary=%s", request_summary)

    if not validation_details["valid"]:
        first_error = validation_details["errors"][0]
        logger.warning(
            "MCP_FLOW_CREATE_ABORT reason=validation_failed error_count=%s "
            "shown_errors=%s omitted_errors=%s summary=%s",
            len(validation_details["errors"]),
            [f"{error['path']}: {error['message']}" for error in compact_validation["errors"]],
            compact_validation.get("errors_omitted_count", 0),
            request_summary,
        )
        return {
            "success": False,
            "message": (
                "Flow deployment was not attempted. Ask the client to provide a "
                "top-level JSON body like the example Flow Server request files."
            ),
            "error": f"{first_error['path']}: {first_error['message']}",
            "validation": compact_validation,
            "hints": _validation_hints(action_catalog),
            "rethinkJson": _rethink_json_payload(
                "local JSON validation failed before calling the Flow Server",
                body=body,
                validation=validation_details,
            ),
            "expected_request_shape": _request_shape_hint(),
            "request": compact_request,
            "appliedSanitizations": sanitizations,
            "actionCatalog": _compact_action_catalog_summary(action_catalog),
            "clientGuidance": _flow_client_guidance(),
        }

    body = _flow_server_body_fields(body, FLOW_DEPLOYMENT_FIELD_NAMES)
    password = str(body.get("password") or "")
    compact_request = _flow_response_request_body(body, password=password)
    request_summary = _flow_request_log_summary(body)
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
            "actionCatalog": _compact_action_catalog_summary(action_catalog),
            "appliedSanitizations": sanitizations,
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
            "appliedSanitizations": sanitizations,
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
