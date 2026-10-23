"""
Approval management tools for the ServiceNow MCP server.

This module manages approval records, approval definitions, approval rules,
approver groups, delegations, and approval history. It is designed for the
parts of the approval lifecycle that can be expressed through the Table API,
including record creation, inspection, and best-effort updates.

ServiceNow approval schemas can vary by plugin and release, so the module is
intentionally flexible: tool entry points accept attribute overrides and table
customization points where the underlying instance exposes different approval
tables or fields.
"""

import logging
import re
from typing import Any, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, Field

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

SYS_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")

APPROVAL_RECORD_TABLE = "sysapproval_approver"
APPROVAL_GROUP_TABLE = "sysapproval_group"
APPROVAL_DELEGATION_TABLE = "sys_user_delegate"
APPROVAL_RULE_TABLE = "sysrule_approvals"
APPROVAL_DEFINITION_TABLE = "sysapproval_rule"

COMMON_DISPLAY_FIELDS = (
    "name",
    "number",
    "short_description",
    "description",
    "sys_name",
    "approver",
    "group",
    "user",
)


class ApprovalRecordInput(BaseModel):
    """Common fields for sysapproval_approver records."""

    approval_for: str = Field(..., description="Target record sys_id in the sysapproval field")
    approver: str = Field(..., description="Approver user or group sys_id/reference")
    state: str = Field(
        "requested", description="Approval state such as requested/approved/rejected"
    )
    source_table: Optional[str] = Field(None, description="Target table name, when known")
    comments: Optional[str] = Field(None, description="Approval comments")
    due_date: Optional[str] = Field(None, description="Due date/time in ServiceNow format")
    order: Optional[int] = Field(None, description="Approval order")
    attributes: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional sysapproval_approver fields to send directly to ServiceNow",
    )


class ListApprovalRecordsParams(BaseModel):
    """Parameters for listing approval records."""

    limit: int = Field(25, description="Maximum number of approval records to return")
    offset: int = Field(0, description="Offset for pagination")
    state: Optional[str] = Field(None, description="Filter by approval state")
    approver: Optional[str] = Field(None, description="Filter by approver sys_id")
    approval_for: Optional[str] = Field(None, description="Filter by target record sys_id")
    source_table: Optional[str] = Field(None, description="Filter by source_table")
    query: Optional[str] = Field(None, description="Additional encoded query")
    include_raw: bool = Field(False, description="Include the raw ServiceNow record")


class GetApprovalRecordParams(BaseModel):
    """Parameters for getting an approval record."""

    approval_id: str = Field(..., description="Approval sys_id")
    include_raw: bool = Field(True, description="Include the raw ServiceNow record")


class CreateApprovalRecordParams(ApprovalRecordInput):
    """Parameters for creating an approval record."""


class UpdateApprovalRecordParams(BaseModel):
    """Parameters for updating an approval record."""

    approval_id: str = Field(..., description="Approval sys_id")
    state: Optional[str] = Field(None, description="Approval state")
    approver: Optional[str] = Field(None, description="Approver user or group sys_id/reference")
    approval_for: Optional[str] = Field(None, description="Target record sys_id")
    source_table: Optional[str] = Field(None, description="Target table name")
    comments: Optional[str] = Field(None, description="Approval comments")
    due_date: Optional[str] = Field(None, description="Due date/time in ServiceNow format")
    order: Optional[int] = Field(None, description="Approval order")
    attributes: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional sysapproval_approver fields to patch directly",
    )


class DeleteApprovalRecordParams(BaseModel):
    """Parameters for deleting an approval record."""

    approval_id: str = Field(..., description="Approval sys_id")


class ApprovalActionParams(BaseModel):
    """Parameters for approve/reject/cancel approval actions."""

    approval_id: str = Field(..., description="Approval sys_id")
    comments: Optional[str] = Field(None, description="Optional approval comments")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Additional fields to patch with the state change"
    )


class ListApprovalDefinitionsParams(BaseModel):
    """Parameters for listing approval definition artifacts."""

    limit: int = Field(25, description="Maximum number of approval definitions to return")
    offset: int = Field(0, description="Offset for pagination")
    table: str = Field(
        APPROVAL_DEFINITION_TABLE,
        description="Approval definition table, override when your instance uses another table",
    )
    active: Optional[bool] = Field(None, description="Filter by active status")
    target_table: Optional[str] = Field(None, description="Filter by target applies-to table")
    query: Optional[str] = Field(None, description="Additional encoded query")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetApprovalDefinitionParams(BaseModel):
    """Parameters for getting an approval definition."""

    definition_id: str = Field(..., description="Approval definition sys_id or display value")
    table: str = Field(APPROVAL_DEFINITION_TABLE, description="Approval definition table")
    include_raw: bool = Field(True, description="Include raw ServiceNow record")


class CreateApprovalDefinitionParams(BaseModel):
    """Parameters for creating an approval definition."""

    name: Optional[str] = Field(None, description="Definition name")
    target_table: Optional[str] = Field(
        None, description="Table this approval definition applies to"
    )
    active: Optional[bool] = Field(True, description="Whether the definition is active")
    condition: Optional[str] = Field(None, description="Encoded condition")
    description: Optional[str] = Field(None, description="Description")
    table: str = Field(APPROVAL_DEFINITION_TABLE, description="Approval definition table")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="All additional approval definition fields"
    )


class UpdateApprovalDefinitionParams(CreateApprovalDefinitionParams):
    """Parameters for updating an approval definition."""

    definition_id: str = Field(..., description="Approval definition sys_id or display value")
    active: Optional[bool] = Field(None, description="Whether the definition is active")


class DeleteApprovalDefinitionParams(BaseModel):
    """Parameters for deleting an approval definition."""

    definition_id: str = Field(..., description="Approval definition sys_id or display value")
    table: str = Field(APPROVAL_DEFINITION_TABLE, description="Approval definition table")


class ListApprovalRulesParams(BaseModel):
    """Parameters for listing approval rules."""

    limit: int = Field(25, description="Maximum number of approval rules to return")
    offset: int = Field(0, description="Offset for pagination")
    table: str = Field(APPROVAL_RULE_TABLE, description="Approval rule table")
    active: Optional[bool] = Field(None, description="Filter by active status")
    target_table: Optional[str] = Field(None, description="Filter by target applies-to table")
    query: Optional[str] = Field(None, description="Additional encoded query")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetApprovalRuleParams(BaseModel):
    """Parameters for getting an approval rule."""

    rule_id: str = Field(..., description="Approval rule sys_id or display value")
    table: str = Field(APPROVAL_RULE_TABLE, description="Approval rule table")
    include_raw: bool = Field(True, description="Include raw ServiceNow record")


class CreateApprovalRuleParams(BaseModel):
    """Parameters for creating an approval rule."""

    name: Optional[str] = Field(None, description="Rule name")
    target_table: Optional[str] = Field(None, description="Table this approval rule applies to")
    condition: Optional[str] = Field(None, description="Encoded condition")
    approver: Optional[str] = Field(None, description="Approver user sys_id/reference")
    group: Optional[str] = Field(None, description="Approver group sys_id/reference")
    script: Optional[str] = Field(None, description="Rule script, if supported by the table")
    order: Optional[int] = Field(None, description="Execution order")
    active: Optional[bool] = Field(True, description="Whether the rule is active")
    table: str = Field(APPROVAL_RULE_TABLE, description="Approval rule table")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="All additional approval rule fields"
    )


class UpdateApprovalRuleParams(CreateApprovalRuleParams):
    """Parameters for updating an approval rule."""

    rule_id: str = Field(..., description="Approval rule sys_id or display value")
    active: Optional[bool] = Field(None, description="Whether the rule is active")


class DeleteApprovalRuleParams(BaseModel):
    """Parameters for deleting an approval rule."""

    rule_id: str = Field(..., description="Approval rule sys_id or display value")
    table: str = Field(APPROVAL_RULE_TABLE, description="Approval rule table")


class ListApproverGroupsParams(BaseModel):
    """Parameters for listing approval group records."""

    limit: int = Field(25, description="Maximum number of approval groups to return")
    offset: int = Field(0, description="Offset for pagination")
    table: str = Field(APPROVAL_GROUP_TABLE, description="Approval group table")
    approval_for: Optional[str] = Field(None, description="Filter by target approval record")
    group: Optional[str] = Field(None, description="Filter by group sys_id/reference")
    state: Optional[str] = Field(None, description="Filter by group approval state")
    query: Optional[str] = Field(None, description="Additional encoded query")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetApproverGroupParams(BaseModel):
    """Parameters for getting an approver group record."""

    approver_group_id: str = Field(..., description="Approver group sys_id or display value")
    table: str = Field(APPROVAL_GROUP_TABLE, description="Approval group table")
    include_raw: bool = Field(True, description="Include raw ServiceNow record")


class CreateApproverGroupParams(BaseModel):
    """Parameters for creating an approver group record."""

    approval_for: Optional[str] = Field(None, description="Target record sys_id")
    group: Optional[str] = Field(None, description="Approver group sys_id/reference")
    state: Optional[str] = Field("requested", description="Group approval state")
    comments: Optional[str] = Field(None, description="Group approval comments")
    table: str = Field(APPROVAL_GROUP_TABLE, description="Approval group table")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="All additional approval group fields"
    )


class UpdateApproverGroupParams(CreateApproverGroupParams):
    """Parameters for updating an approver group record."""

    approver_group_id: str = Field(..., description="Approver group sys_id or display value")
    state: Optional[str] = Field(None, description="Group approval state")


class DeleteApproverGroupParams(BaseModel):
    """Parameters for deleting an approver group record."""

    approver_group_id: str = Field(..., description="Approver group sys_id or display value")
    table: str = Field(APPROVAL_GROUP_TABLE, description="Approval group table")


class ListApprovalDelegationsParams(BaseModel):
    """Parameters for listing approval delegations."""

    limit: int = Field(25, description="Maximum number of delegations to return")
    offset: int = Field(0, description="Offset for pagination")
    user: Optional[str] = Field(None, description="Delegating user sys_id/reference")
    delegate: Optional[str] = Field(None, description="Delegate user sys_id/reference")
    active: Optional[bool] = Field(None, description="Filter by active status")
    approvals_only: bool = Field(False, description="Filter to approval-enabled delegations")
    query: Optional[str] = Field(None, description="Additional encoded query")
    include_raw: bool = Field(False, description="Include raw ServiceNow records")


class GetApprovalDelegationParams(BaseModel):
    """Parameters for getting an approval delegation."""

    delegation_id: str = Field(..., description="Delegation sys_id")
    include_raw: bool = Field(True, description="Include raw ServiceNow record")


class CreateApprovalDelegationParams(BaseModel):
    """Parameters for creating an approval delegation."""

    user: str = Field(..., description="Delegating user sys_id/reference")
    delegate: str = Field(..., description="Delegate user sys_id/reference")
    starts: Optional[str] = Field(None, description="Delegation start date/time")
    ends: Optional[str] = Field(None, description="Delegation end date/time")
    approvals: Optional[bool] = Field(True, description="Delegate approvals")
    assignments: Optional[bool] = Field(None, description="Delegate assignments")
    notifications: Optional[bool] = Field(None, description="Delegate notifications")
    active: Optional[bool] = Field(True, description="Whether the delegation is active")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="All additional sys_user_delegate fields"
    )


class UpdateApprovalDelegationParams(BaseModel):
    """Parameters for updating an approval delegation."""

    delegation_id: str = Field(..., description="Delegation sys_id")
    user: Optional[str] = Field(None, description="Delegating user sys_id/reference")
    delegate: Optional[str] = Field(None, description="Delegate user sys_id/reference")
    starts: Optional[str] = Field(None, description="Delegation start date/time")
    ends: Optional[str] = Field(None, description="Delegation end date/time")
    approvals: Optional[bool] = Field(None, description="Delegate approvals")
    assignments: Optional[bool] = Field(None, description="Delegate assignments")
    notifications: Optional[bool] = Field(None, description="Delegate notifications")
    active: Optional[bool] = Field(None, description="Whether the delegation is active")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="All additional sys_user_delegate fields"
    )


class DeleteApprovalDelegationParams(BaseModel):
    """Parameters for deleting an approval delegation."""

    delegation_id: str = Field(..., description="Delegation sys_id")


class ListApprovalHistoryParams(ListApprovalRecordsParams):
    """Parameters for listing approval history."""

    states: List[str] = Field(
        default_factory=list,
        description="Optional list of approval states to include, such as approved/rejected",
    )


class GetApprovalManagementMetadataParams(BaseModel):
    """Parameters for approval management metadata."""

    include_examples: bool = Field(True, description="Include create/update field examples")


def _api_url(config: ServerConfig, table: str, sys_id: Optional[str] = None) -> str:
    base = f"{config.instance_url}/api/now/table/{table}"
    return f"{base}/{sys_id}" if sys_id else base


def _value(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("value") or value.get("display_value") or value.get("link") or value
    return value


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("display_value") or value.get("value")
    return value


def _bool_text(value: Optional[bool]) -> Optional[str]:
    if value is None:
        return None
    return "true" if value else "false"


def _extract_result(response: requests.Response) -> Any:
    response.raise_for_status()
    payload = response.json() if response.content else {}
    return payload.get("result", payload)


def _record_summary(record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
    summary = {
        "sys_id": record.get("sys_id"),
        "display": next(
            (_display(record.get(field)) for field in COMMON_DISPLAY_FIELDS if record.get(field)),
            record.get("sys_id"),
        ),
        "state": _display(record.get("state")),
        "active": _display(record.get("active")),
        "target_table": _display(record.get("table") or record.get("collection")),
        "approval_for": _display(record.get("sysapproval") or record.get("document_id")),
        "approver": _display(record.get("approver")),
        "group": _display(record.get("group")),
        "user": _display(record.get("user")),
        "delegate": _display(record.get("delegate")),
        "condition": _display(record.get("condition")),
        "order": _display(record.get("order")),
        "created": _display(record.get("sys_created_on")),
        "updated": _display(record.get("sys_updated_on")),
    }
    summary = {key: value for key, value in summary.items() if value not in (None, "")}
    if include_raw:
        summary["raw"] = record
    return summary


def _merge_fields(body: Dict[str, Any], attributes: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    clean = {key: value for key, value in body.items() if value is not None}
    clean.update(attributes or {})
    return clean


def _list_records(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    query_parts: List[str],
    limit: int,
    offset: int,
    include_raw: bool,
) -> Dict[str, Any]:
    params = {
        "sysparm_limit": limit,
        "sysparm_offset": offset,
        "sysparm_display_value": "all",
    }
    query = "^".join(part for part in query_parts if part)
    if query:
        params["sysparm_query"] = query

    response = requests.get(
        _api_url(config, table),
        headers=auth_manager.get_headers(),
        params=params,
        timeout=config.timeout,
    )
    records = _extract_result(response)
    return {
        "success": True,
        "table": table,
        "count": len(records),
        "records": [_record_summary(record, include_raw) for record in records],
    }


def _get_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    include_raw: bool,
) -> Dict[str, Any]:
    sys_id = _resolve_identifier(config, auth_manager, table, identifier)
    response = requests.get(
        _api_url(config, table, sys_id),
        headers=auth_manager.get_headers(),
        params={"sysparm_display_value": "all"},
        timeout=config.timeout,
    )
    record = _extract_result(response)
    return {"success": True, "table": table, "record": _record_summary(record, include_raw)}


def _create_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    response = requests.post(
        _api_url(config, table),
        headers=auth_manager.get_headers(),
        json=body,
        timeout=config.timeout,
    )
    record = _extract_result(response)
    return {
        "success": True,
        "message": f"Created record in {table}",
        "table": table,
        "record": _record_summary(record, True),
    }


def _update_record(
    config: ServerConfig,
    auth_manager: AuthManager,
    table: str,
    identifier: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    sys_id = _resolve_identifier(config, auth_manager, table, identifier)
    response = requests.patch(
        _api_url(config, table, sys_id),
        headers=auth_manager.get_headers(),
        json=body,
        timeout=config.timeout,
    )
    record = _extract_result(response)
    return {
        "success": True,
        "message": f"Updated record in {table}",
        "table": table,
        "record": _record_summary(record, True),
    }


def _delete_record(
    config: ServerConfig, auth_manager: AuthManager, table: str, identifier: str
) -> Dict[str, Any]:
    sys_id = _resolve_identifier(config, auth_manager, table, identifier)
    response = requests.delete(
        _api_url(config, table, sys_id),
        headers=auth_manager.get_headers(),
        timeout=config.timeout,
    )
    response.raise_for_status()
    return {"success": True, "message": f"Deleted record from {table}", "table": table}


def _resolve_identifier(
    config: ServerConfig, auth_manager: AuthManager, table: str, identifier: str
) -> str:
    if SYS_ID_PATTERN.match(identifier):
        return identifier

    query = "^OR".join(f"{field}={identifier}" for field in COMMON_DISPLAY_FIELDS)
    response = requests.get(
        _api_url(config, table),
        headers=auth_manager.get_headers(),
        params={"sysparm_query": query, "sysparm_limit": 1, "sysparm_fields": "sys_id"},
        timeout=config.timeout,
    )
    records = _extract_result(response)
    if not records:
        raise ValueError(f"No {table} record found for identifier '{identifier}'")
    return records[0]["sys_id"]


def _standard_query(
    query: Optional[str] = None,
    active: Optional[bool] = None,
    target_table: Optional[str] = None,
) -> List[str]:
    parts: List[str] = []
    if active is not None:
        parts.append(f"active={_bool_text(active)}")
    if target_table:
        parts.append(f"table={target_table}^ORcollection={target_table}")
    if query:
        parts.append(query)
    return parts


def get_approval_management_metadata(
    config: ServerConfig, auth_manager: AuthManager, params: GetApprovalManagementMetadataParams
) -> Dict[str, Any]:
    """Return supported approval-management tables, tools, and field guidance."""
    del config, auth_manager
    metadata: Dict[str, Any] = {
        "success": True,
        "artifacts": {
            "approval_records": {
                "table": APPROVAL_RECORD_TABLE,
                "tools": [
                    "list_approval_records",
                    "get_approval_record",
                    "create_approval_record",
                    "update_approval_record",
                    "delete_approval_record",
                    "approve_approval",
                    "reject_approval",
                    "cancel_approval",
                ],
            },
            "approval_definitions": {
                "default_table": APPROVAL_DEFINITION_TABLE,
                "table_override": True,
            },
            "approval_rules": {"default_table": APPROVAL_RULE_TABLE, "table_override": True},
            "approver_groups": {"default_table": APPROVAL_GROUP_TABLE, "table_override": True},
            "delegations": {"table": APPROVAL_DELEGATION_TABLE},
            "approval_history": {"table": APPROVAL_RECORD_TABLE},
        },
        "notes": [
            "Use attributes to set any ServiceNow field not exposed as a first-class parameter.",
            (
                "Use table overrides for approval definitions/rules if your instance uses "
                "custom tables."
            ),
        ],
    }
    if params.include_examples:
        metadata["examples"] = {
            "approval_record": {
                "approval_for": "target_record_sys_id",
                "approver": "user_sys_id",
                "state": "requested",
                "attributes": {"source_table": "change_request", "due_date": "2026-04-30 09:00:00"},
            },
            "approval_rule": {
                "name": "Manager approval for high priority requests",
                "target_table": "sc_req_item",
                "condition": "priority=1",
                "group": "assignment_group_sys_id",
                "attributes": {"when_to_run": "before"},
            },
            "delegation": {
                "user": "delegating_user_sys_id",
                "delegate": "delegate_user_sys_id",
                "approvals": True,
                "starts": "2026-04-28 00:00:00",
                "ends": "2026-05-05 23:59:59",
            },
        }
    return metadata


def list_approval_records(
    config: ServerConfig, auth_manager: AuthManager, params: ListApprovalRecordsParams
) -> Dict[str, Any]:
    """List sysapproval_approver records."""
    parts = []
    if params.state:
        parts.append(f"state={params.state}")
    if params.approver:
        parts.append(f"approver={params.approver}")
    if params.approval_for:
        parts.append(f"sysapproval={params.approval_for}")
    if params.source_table:
        parts.append(f"source_table={params.source_table}")
    if params.query:
        parts.append(params.query)
    return _list_records(
        config,
        auth_manager,
        APPROVAL_RECORD_TABLE,
        parts,
        params.limit,
        params.offset,
        params.include_raw,
    )


def get_approval_record(
    config: ServerConfig, auth_manager: AuthManager, params: GetApprovalRecordParams
) -> Dict[str, Any]:
    """Get a single approval record."""
    return _get_record(
        config, auth_manager, APPROVAL_RECORD_TABLE, params.approval_id, params.include_raw
    )


def create_approval_record(
    config: ServerConfig, auth_manager: AuthManager, params: CreateApprovalRecordParams
) -> Dict[str, Any]:
    """Create a sysapproval_approver record."""
    body = _merge_fields(
        {
            "sysapproval": params.approval_for,
            "approver": params.approver,
            "state": params.state,
            "source_table": params.source_table,
            "comments": params.comments,
            "due_date": params.due_date,
            "order": params.order,
        },
        params.attributes,
    )
    return _create_record(config, auth_manager, APPROVAL_RECORD_TABLE, body)


def update_approval_record(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateApprovalRecordParams
) -> Dict[str, Any]:
    """Update a sysapproval_approver record."""
    body = _merge_fields(
        {
            "state": params.state,
            "approver": params.approver,
            "sysapproval": params.approval_for,
            "source_table": params.source_table,
            "comments": params.comments,
            "due_date": params.due_date,
            "order": params.order,
        },
        params.attributes,
    )
    return _update_record(config, auth_manager, APPROVAL_RECORD_TABLE, params.approval_id, body)


def delete_approval_record(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteApprovalRecordParams
) -> Dict[str, Any]:
    """Delete a sysapproval_approver record."""
    return _delete_record(config, auth_manager, APPROVAL_RECORD_TABLE, params.approval_id)


def _approval_action(
    config: ServerConfig,
    auth_manager: AuthManager,
    params: ApprovalActionParams,
    state: Literal["approved", "rejected", "cancelled"],
) -> Dict[str, Any]:
    body = _merge_fields({"state": state, "comments": params.comments}, params.attributes)
    return _update_record(config, auth_manager, APPROVAL_RECORD_TABLE, params.approval_id, body)


def approve_approval(
    config: ServerConfig, auth_manager: AuthManager, params: ApprovalActionParams
) -> Dict[str, Any]:
    """Approve an approval record."""
    return _approval_action(config, auth_manager, params, "approved")


def reject_approval(
    config: ServerConfig, auth_manager: AuthManager, params: ApprovalActionParams
) -> Dict[str, Any]:
    """Reject an approval record."""
    return _approval_action(config, auth_manager, params, "rejected")


def cancel_approval(
    config: ServerConfig, auth_manager: AuthManager, params: ApprovalActionParams
) -> Dict[str, Any]:
    """Cancel an approval record."""
    return _approval_action(config, auth_manager, params, "cancelled")


def list_approval_definitions(
    config: ServerConfig, auth_manager: AuthManager, params: ListApprovalDefinitionsParams
) -> Dict[str, Any]:
    """List approval definitions."""
    return _list_records(
        config,
        auth_manager,
        params.table,
        _standard_query(params.query, params.active, params.target_table),
        params.limit,
        params.offset,
        params.include_raw,
    )


def get_approval_definition(
    config: ServerConfig, auth_manager: AuthManager, params: GetApprovalDefinitionParams
) -> Dict[str, Any]:
    """Get an approval definition."""
    return _get_record(config, auth_manager, params.table, params.definition_id, params.include_raw)


def create_approval_definition(
    config: ServerConfig, auth_manager: AuthManager, params: CreateApprovalDefinitionParams
) -> Dict[str, Any]:
    """Create an approval definition."""
    body = _merge_fields(
        {
            "name": params.name,
            "table": params.target_table,
            "active": _bool_text(params.active),
            "condition": params.condition,
            "description": params.description,
        },
        params.attributes,
    )
    return _create_record(config, auth_manager, params.table, body)


def update_approval_definition(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateApprovalDefinitionParams
) -> Dict[str, Any]:
    """Update an approval definition."""
    body = _merge_fields(
        {
            "name": params.name,
            "table": params.target_table,
            "active": _bool_text(params.active),
            "condition": params.condition,
            "description": params.description,
        },
        params.attributes,
    )
    return _update_record(config, auth_manager, params.table, params.definition_id, body)


def delete_approval_definition(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteApprovalDefinitionParams
) -> Dict[str, Any]:
    """Delete an approval definition."""
    return _delete_record(config, auth_manager, params.table, params.definition_id)


def list_approval_rules(
    config: ServerConfig, auth_manager: AuthManager, params: ListApprovalRulesParams
) -> Dict[str, Any]:
    """List approval rules."""
    return _list_records(
        config,
        auth_manager,
        params.table,
        _standard_query(params.query, params.active, params.target_table),
        params.limit,
        params.offset,
        params.include_raw,
    )


def get_approval_rule(
    config: ServerConfig, auth_manager: AuthManager, params: GetApprovalRuleParams
) -> Dict[str, Any]:
    """Get an approval rule."""
    return _get_record(config, auth_manager, params.table, params.rule_id, params.include_raw)


def create_approval_rule(
    config: ServerConfig, auth_manager: AuthManager, params: CreateApprovalRuleParams
) -> Dict[str, Any]:
    """Create an approval rule."""
    body = _merge_fields(
        {
            "name": params.name,
            "table": params.target_table,
            "condition": params.condition,
            "approver": params.approver,
            "group": params.group,
            "script": params.script,
            "order": params.order,
            "active": _bool_text(params.active),
        },
        params.attributes,
    )
    return _create_record(config, auth_manager, params.table, body)


def update_approval_rule(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateApprovalRuleParams
) -> Dict[str, Any]:
    """Update an approval rule."""
    body = _merge_fields(
        {
            "name": params.name,
            "table": params.target_table,
            "condition": params.condition,
            "approver": params.approver,
            "group": params.group,
            "script": params.script,
            "order": params.order,
            "active": _bool_text(params.active),
        },
        params.attributes,
    )
    return _update_record(config, auth_manager, params.table, params.rule_id, body)


def delete_approval_rule(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteApprovalRuleParams
) -> Dict[str, Any]:
    """Delete an approval rule."""
    return _delete_record(config, auth_manager, params.table, params.rule_id)


def list_approver_groups(
    config: ServerConfig, auth_manager: AuthManager, params: ListApproverGroupsParams
) -> Dict[str, Any]:
    """List approval group records."""
    parts = []
    if params.approval_for:
        parts.append(f"sysapproval={params.approval_for}")
    if params.group:
        parts.append(f"group={params.group}")
    if params.state:
        parts.append(f"state={params.state}")
    if params.query:
        parts.append(params.query)
    return _list_records(
        config, auth_manager, params.table, parts, params.limit, params.offset, params.include_raw
    )


def get_approver_group(
    config: ServerConfig, auth_manager: AuthManager, params: GetApproverGroupParams
) -> Dict[str, Any]:
    """Get an approval group record."""
    return _get_record(
        config, auth_manager, params.table, params.approver_group_id, params.include_raw
    )


def create_approver_group(
    config: ServerConfig, auth_manager: AuthManager, params: CreateApproverGroupParams
) -> Dict[str, Any]:
    """Create an approval group record."""
    body = _merge_fields(
        {
            "sysapproval": params.approval_for,
            "group": params.group,
            "state": params.state,
            "comments": params.comments,
        },
        params.attributes,
    )
    return _create_record(config, auth_manager, params.table, body)


def update_approver_group(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateApproverGroupParams
) -> Dict[str, Any]:
    """Update an approval group record."""
    body = _merge_fields(
        {
            "sysapproval": params.approval_for,
            "group": params.group,
            "state": params.state,
            "comments": params.comments,
        },
        params.attributes,
    )
    return _update_record(config, auth_manager, params.table, params.approver_group_id, body)


def delete_approver_group(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteApproverGroupParams
) -> Dict[str, Any]:
    """Delete an approval group record."""
    return _delete_record(config, auth_manager, params.table, params.approver_group_id)


def list_approval_delegations(
    config: ServerConfig, auth_manager: AuthManager, params: ListApprovalDelegationsParams
) -> Dict[str, Any]:
    """List approval delegations."""
    parts = []
    if params.user:
        parts.append(f"user={params.user}")
    if params.delegate:
        parts.append(f"delegate={params.delegate}")
    if params.active is not None:
        parts.append(f"active={_bool_text(params.active)}")
    if params.approvals_only:
        parts.append("approvals=true")
    if params.query:
        parts.append(params.query)
    return _list_records(
        config,
        auth_manager,
        APPROVAL_DELEGATION_TABLE,
        parts,
        params.limit,
        params.offset,
        params.include_raw,
    )


def get_approval_delegation(
    config: ServerConfig, auth_manager: AuthManager, params: GetApprovalDelegationParams
) -> Dict[str, Any]:
    """Get an approval delegation."""
    return _get_record(
        config, auth_manager, APPROVAL_DELEGATION_TABLE, params.delegation_id, params.include_raw
    )


def create_approval_delegation(
    config: ServerConfig, auth_manager: AuthManager, params: CreateApprovalDelegationParams
) -> Dict[str, Any]:
    """Create an approval delegation."""
    body = _merge_fields(
        {
            "user": params.user,
            "delegate": params.delegate,
            "starts": params.starts,
            "ends": params.ends,
            "approvals": _bool_text(params.approvals),
            "assignments": _bool_text(params.assignments),
            "notifications": _bool_text(params.notifications),
            "active": _bool_text(params.active),
        },
        params.attributes,
    )
    return _create_record(config, auth_manager, APPROVAL_DELEGATION_TABLE, body)


def update_approval_delegation(
    config: ServerConfig, auth_manager: AuthManager, params: UpdateApprovalDelegationParams
) -> Dict[str, Any]:
    """Update an approval delegation."""
    body = _merge_fields(
        {
            "user": params.user,
            "delegate": params.delegate,
            "starts": params.starts,
            "ends": params.ends,
            "approvals": _bool_text(params.approvals),
            "assignments": _bool_text(params.assignments),
            "notifications": _bool_text(params.notifications),
            "active": _bool_text(params.active),
        },
        params.attributes,
    )
    return _update_record(
        config, auth_manager, APPROVAL_DELEGATION_TABLE, params.delegation_id, body
    )


def delete_approval_delegation(
    config: ServerConfig, auth_manager: AuthManager, params: DeleteApprovalDelegationParams
) -> Dict[str, Any]:
    """Delete an approval delegation."""
    return _delete_record(config, auth_manager, APPROVAL_DELEGATION_TABLE, params.delegation_id)


def list_approval_history(
    config: ServerConfig, auth_manager: AuthManager, params: ListApprovalHistoryParams
) -> Dict[str, Any]:
    """List approval history from sysapproval_approver."""
    parts = []
    if params.states:
        parts.append("^OR".join(f"state={state}" for state in params.states))
    elif params.state:
        parts.append(f"state={params.state}")
    if params.approver:
        parts.append(f"approver={params.approver}")
    if params.approval_for:
        parts.append(f"sysapproval={params.approval_for}")
    if params.source_table:
        parts.append(f"source_table={params.source_table}")
    if params.query:
        parts.append(params.query)
    parts.append("ORDERBYDESCsys_updated_on")
    return _list_records(
        config,
        auth_manager,
        APPROVAL_RECORD_TABLE,
        parts,
        params.limit,
        params.offset,
        params.include_raw,
    )
