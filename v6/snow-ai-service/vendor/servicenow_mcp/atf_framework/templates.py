"""Deterministic ServiceNow ATF template library.

This module defines the canonical ATF test templates used to generate tests
for common ServiceNow artifacts. The templates are deterministic so higher-
level generation code can rely on stable steps, assertions, and cleanup rules.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


def _step(
    order: int, step_type: str, description: str, expected: str, automatable: bool = True
) -> Dict[str, Any]:
    """Create a normalized ATF step descriptor used by template definitions.

    Args:
        order: Step order in the generated ATF scenario.
        step_type: Canonical step category (open_form, assert_record, etc.).
        description: Human-readable step action text.
        expected: Expected result statement used for assertions/evidence.
        automatable: Whether the step can be fully automated in the instance.

    Returns:
        Dict[str, Any]: Standardized step object consumed by ATF generators.
    """
    return {
        "order": order,
        "step_type": step_type,
        "description": description,
        "config": {},
        "expected_result": expected,
        "is_automatable": automatable,
    }


TEMPLATES: Dict[str, Dict[str, Any]] = {
    "catalog_item_smoke": {
        "template_id": "catalog_item_smoke",
        "name": "Catalog Item Smoke Test",
        "artifact_type": "catalog_item",
        "category": "smoke",
        "description": (
            "Verify the catalog item exists, is active, is available in the intended catalog/"
            "category, renders variables, submits, and creates a request item."
        ),
        "required_inputs": ["catalog_item_sys_id"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "assert_record",
                "Verify catalog item exists, is active, and is in the intended catalog/category",
                "Item is active and available to the expected audience",
            ),
            _step(2, "open_form", "Open catalog item", "Catalog item is visible"),
            _step(3, "assert_record", "Validate item variables render", "Variables are present"),
            _step(4, "submit_form", "Submit catalog item with valid data", "Request is submitted"),
            _step(5, "assert_record", "Assert RITM/request created", "RITM exists"),
        ],
        "assertions": [
            "catalog item active",
            "catalog/category availability correct",
            "variables render",
            "RITM created",
        ],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "catalog_item_variable_configuration": {
        "template_id": "catalog_item_variable_configuration",
        "name": "Catalog Item Variable Configuration Test",
        "artifact_type": "catalog_item",
        "category": "configuration",
        "description": (
            "Validate each catalog variable type, mandatory/read-only/default setting, choices, "
            "and reference lookup configuration."
        ),
        "required_inputs": ["catalog_item_sys_id", "expected_variable_matrix"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "assert_record",
                "Load expected variable metadata",
                "Variable metadata is available for comparison",
            ),
            _step(
                2,
                "assert_record",
                "Validate variable type, mandatory, read-only, and default settings",
                "All variable configuration matches the expected design",
            ),
            _step(
                3,
                "assert_record",
                "Validate variable choices and reference lookups",
                "Choices and references resolve to allowed records",
            ),
        ],
        "assertions": [
            "all expected variables exist",
            "variable type and behavior settings match",
            "choices and references are valid",
        ],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "catalog_item_mandatory": {
        "template_id": "catalog_item_mandatory",
        "name": "Catalog Item Mandatory Validation Test",
        "artifact_type": "catalog_item",
        "category": "negative",
        "description": "Attempt submission with missing mandatory variables.",
        "required_inputs": ["catalog_item_sys_id"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "open_form", "Open catalog item", "Catalog item is visible"),
            _step(
                2,
                "submit_form",
                "Attempt submit without mandatory data",
                "Validation prevents submit",
            ),
        ],
        "assertions": ["mandatory validation error"],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "catalog_item_submission_ritm": {
        "template_id": "catalog_item_submission_ritm",
        "name": "Catalog Item Submission and RITM Test",
        "artifact_type": "catalog_item",
        "category": "functional",
        "description": "Submit valid catalog request and validate generated RITM/task.",
        "required_inputs": ["catalog_item_sys_id"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "open_form", "Open catalog item", "Catalog item is visible"),
            _step(2, "set_field_values", "Populate valid request variables", "Variables populated"),
            _step(3, "submit_form", "Submit request", "Request submitted"),
            _step(4, "assert_record", "Assert RITM exists", "RITM created"),
            _step(5, "assert_record", "Assert fulfillment task where applicable", "Task created"),
        ],
        "assertions": ["RITM created", "task generated"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "record_producer_target": {
        "template_id": "record_producer_target",
        "name": "Record Producer Target Record Creation Test",
        "artifact_type": "record_producer",
        "category": "functional",
        "description": "Submit producer and validate target table record, not sc_req_item only.",
        "required_inputs": ["record_producer_sys_id", "target_table"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "open_form", "Open record producer", "Producer is visible"),
            _step(2, "set_field_values", "Populate producer variables", "Variables populated"),
            _step(3, "submit_form", "Submit producer", "Producer submitted"),
            _step(
                4, "assert_record", "Assert target table record was created", "Target record exists"
            ),
        ],
        "assertions": ["target record created", "field mapping correct"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "flow_trigger_outcome": {
        "template_id": "flow_trigger_outcome",
        "name": "Flow Trigger and Outcome Test",
        "artifact_type": "flow",
        "category": "functional",
        "description": "Create/update trigger record and validate observable flow outcomes.",
        "required_inputs": ["flow_sys_id"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "set_field_values",
                "Create or update trigger record",
                "Trigger condition satisfied",
            ),
            _step(
                2,
                "validate_flow_execution",
                "Validate flow context if accessible",
                "Flow context or observable outcome exists",
            ),
            _step(3, "assert_record", "Assert final record state", "Expected state reached"),
            _step(4, "cleanup_record", "Clean up generated trigger data", "Test data removed"),
        ],
        "assertions": ["flow triggered", "record outcome correct"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "flow context tables may be restricted"}],
    },
    "flow_dynamic_approval_routing": {
        "template_id": "flow_dynamic_approval_routing",
        "name": "Flow Dynamic Approval Routing Test",
        "artifact_type": "flow",
        "category": "end_to_end",
        "description": (
            "Validate that the flow selects the correct approver such as CISO, manager, or "
            "another dynamic approver based on request data."
        ),
        "required_inputs": ["flow_sys_id", "approval_route_matrix"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "set_field_values",
                "Create request data for each approver route",
                "Routes are prepared",
            ),
            _step(
                2,
                "validate_flow_execution",
                "Trigger flow and inspect generated approval records",
                "Expected approver receives the approval",
            ),
            _step(
                3,
                "assert_record",
                "Assert CISO/manager/dynamic approver assignment",
                "Approval records match route matrix",
            ),
        ],
        "assertions": ["dynamic approver selected", "unexpected approvers excluded"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "approval impersonation may require role-specific data"}],
    },
    "flow_approval_lifecycle_paths": {
        "template_id": "flow_approval_lifecycle_paths",
        "name": "Flow Approval Lifecycle Paths Test",
        "artifact_type": "flow",
        "category": "end_to_end",
        "description": "Exercise approve, reject, timeout, cancellation, and reassignment paths.",
        "required_inputs": ["flow_sys_id", "approval_test_users"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "set_field_values", "Trigger approval request", "Approval is requested"),
            _step(
                2, "assert_record", "Approve request and assert state", "Approved path completes"
            ),
            _step(3, "assert_record", "Reject request and assert state", "Rejected path completes"),
            _step(
                4,
                "manual_instruction",
                "Validate timeout, cancellation, and reassignment variants",
                "Alternate approval paths update state correctly",
                False,
            ),
        ],
        "assertions": [
            "approve path correct",
            "reject path correct",
            "timeout/cancel/reassign paths correct",
        ],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "timeout windows may be impractical in a fast ATF run"}],
    },
    "flow_request_state_work_notes": {
        "template_id": "flow_request_state_work_notes",
        "name": "Flow Request State and Notes Test",
        "artifact_type": "flow",
        "category": "functional",
        "description": "Validate flow-updated request state, work notes, and comments.",
        "required_inputs": ["flow_sys_id", "expected_state", "expected_work_notes"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "set_field_values", "Create or update trigger request", "Flow starts"),
            _step(
                2,
                "validate_flow_execution",
                "Wait for flow outcome",
                "Flow reaches terminal action",
            ),
            _step(
                3,
                "assert_field_value",
                "Assert request state and work notes/comments",
                "State and notes match expected values",
            ),
        ],
        "assertions": ["request state updated", "work notes added", "comments added when expected"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "flow_failure_retry_behavior": {
        "template_id": "flow_failure_retry_behavior",
        "name": "Flow Failure and Retry Behavior Test",
        "artifact_type": "flow",
        "category": "negative",
        "description": "Force an action error and verify failure handling, retry behavior, and audit evidence.",
        "required_inputs": ["flow_sys_id", "retryable_error_fixture"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "set_field_values",
                "Create request that causes a controlled action error",
                "Error fixture starts",
            ),
            _step(
                2,
                "validate_flow_execution",
                "Validate failure path and retry attempt",
                "Retry behavior matches design",
            ),
            _step(
                3, "assert_record", "Assert failure notes/audit evidence", "Failure evidence exists"
            ),
        ],
        "assertions": ["failure path reached", "retry behavior correct", "error evidence recorded"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "some action retry internals are family/plugin dependent"}],
    },
    "flow_subflow_business_journey": {
        "template_id": "flow_subflow_business_journey",
        "name": "Flow and Subflow Business Journey Test",
        "artifact_type": "flow",
        "category": "end_to_end",
        "description": (
            "Test the parent Flow and Subflow together as one business journey rather than as "
            "isolated components only."
        ),
        "required_inputs": ["flow_sys_id", "subflow_sys_id", "journey_test_data"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1, "set_field_values", "Create full business request", "Parent flow trigger matches"
            ),
            _step(
                2,
                "validate_flow_execution",
                "Validate subflow is invoked",
                "Subflow output is produced",
            ),
            _step(
                3,
                "assert_record",
                "Assert final business outcome across flow and subflow",
                "End-to-end journey completes correctly",
            ),
        ],
        "assertions": ["parent flow triggered", "subflow invoked", "journey outcome correct"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "subflow context visibility may be restricted"}],
    },
    "table_crud": {
        "template_id": "table_crud",
        "name": "Table CRUD Test",
        "artifact_type": "table",
        "category": "data_validation",
        "description": (
            "Validate form opens, mandatory fields, create/update/read behavior, and cleanup."
        ),
        "required_inputs": ["table_name"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "open_form", "Open table form", "Form opens"),
            _step(2, "set_field_values", "Set required values", "Required values accepted"),
            _step(3, "submit_form", "Insert record", "Record inserted"),
            _step(4, "assert_field_value", "Validate persisted values", "Fields persisted"),
            _step(5, "cleanup_record", "Clean up record", "Record cleaned"),
        ],
        "assertions": ["record inserted", "values persisted"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "table_dictionary_configuration": {
        "template_id": "table_dictionary_configuration",
        "name": "Table Dictionary Configuration Test",
        "artifact_type": "table",
        "category": "configuration",
        "description": (
            "Validate table, columns, dictionary attributes, choices, references, and default "
            "values against the intended schema."
        ),
        "required_inputs": ["table_name", "expected_dictionary_matrix"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "assert_record", "Validate table definition", "Table metadata is correct"),
            _step(
                2,
                "assert_record",
                "Validate columns, dictionary attributes, and defaults",
                "Dictionary configuration matches expected schema",
            ),
            _step(
                3,
                "assert_record",
                "Validate choices and reference target tables",
                "Choices and references are configured correctly",
            ),
        ],
        "assertions": [
            "table exists",
            "dictionary attributes correct",
            "choices correct",
            "references correct",
            "defaults correct",
        ],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "table_field_validation": {
        "template_id": "table_field_validation",
        "name": "Table Field Validation Test",
        "artifact_type": "table",
        "category": "data_validation",
        "description": (
            "Validate mandatory values, allowed choices, and reference lookup enforcement at "
            "the field level."
        ),
        "required_inputs": ["table_name", "field_validation_matrix"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "open_form", "Open table form", "Form opens"),
            _step(
                2,
                "submit_form",
                "Attempt insert with missing mandatory values",
                "Mandatory validation blocks insert",
            ),
            _step(
                3,
                "set_field_values",
                "Try invalid choices and invalid references",
                "Invalid field values are rejected",
            ),
            _step(4, "set_field_values", "Set allowed values", "Allowed values are accepted"),
        ],
        "assertions": [
            "mandatory validation blocks empty values",
            "choice validation blocks invalid choices",
            "reference validation blocks invalid references",
        ],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "table_acl_crud_by_role": {
        "template_id": "table_acl_crud_by_role",
        "name": "Table ACL CRUD by Role Test",
        "artifact_type": "table",
        "category": "security",
        "description": "Validate create, read, write, and delete access by each configured role.",
        "required_inputs": ["table_name", "role_access_matrix", "test_users"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "impersonate", "Impersonate allowed role user", "Allowed user impersonated"),
            _step(
                2,
                "assert_record",
                "Validate create/read/write/delete allowed operations",
                "Allowed operations succeed",
            ),
            _step(3, "impersonate", "Impersonate denied role user", "Denied user impersonated"),
            _step(
                4,
                "assert_record",
                "Validate create/read/write/delete denied operations",
                "Denied operations fail",
            ),
        ],
        "assertions": ["CRUD ACL matrix enforced for allowed and denied roles"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "business_rule_positive": {
        "template_id": "business_rule_positive",
        "name": "Business Rule Positive Condition Test",
        "artifact_type": "business_rule",
        "category": "functional",
        "description": "Trigger business rule condition and assert expected mutation.",
        "required_inputs": ["business_rule_sys_id"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "set_field_values", "Create matching record", "Condition matches"),
            _step(2, "assert_record", "Assert mutation", "Expected mutation observed"),
        ],
        "assertions": ["business rule effect observed"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "business_rule_negative": {
        "template_id": "business_rule_negative",
        "name": "Business Rule Negative Condition Test",
        "artifact_type": "business_rule",
        "category": "negative",
        "description": "Create non-matching data and assert no unintended mutation.",
        "required_inputs": ["business_rule_sys_id"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "set_field_values", "Create non-matching record", "Condition does not match"),
            _step(2, "assert_record", "Assert no unintended mutation", "No mutation observed"),
        ],
        "assertions": ["no unintended mutation"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "business_rule_duplicate_similarity_threshold": {
        "template_id": "business_rule_duplicate_similarity_threshold",
        "name": "Business Rule Duplicate Similarity Threshold Test",
        "artifact_type": "business_rule",
        "category": "negative",
        "description": (
            "Validate duplicate-exception rejection below and above the 85 percent similarity "
            "threshold."
        ),
        "required_inputs": [
            "business_rule_sys_id",
            "below_threshold_fixture",
            "above_threshold_fixture",
        ],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "set_field_values",
                "Create below-85-percent similarity fixture",
                "Below-threshold record is accepted or routed as designed",
            ),
            _step(
                2,
                "set_field_values",
                "Create above-85-percent similarity duplicate fixture",
                "Duplicate rejection is triggered",
            ),
            _step(
                3,
                "assert_record",
                "Assert duplicate-exception message and record outcome",
                "Duplicate behavior matches threshold design",
            ),
        ],
        "assertions": ["below threshold allowed", "above threshold rejected", "message correct"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "business_rule_audit_hash_chain": {
        "template_id": "business_rule_audit_hash_chain",
        "name": "Business Rule Audit Hash Chain Test",
        "artifact_type": "business_rule",
        "category": "security",
        "description": "Validate audit-log creation and hash-chain continuity after record mutation.",
        "required_inputs": ["business_rule_sys_id", "audit_table", "hash_fields"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1, "set_field_values", "Create or update triggering record", "Business rule runs"
            ),
            _step(2, "assert_record", "Assert audit-log record creation", "Audit record exists"),
            _step(
                3,
                "run_server_script",
                "Validate previous/current hash-chain continuity",
                "Hash chain remains continuous",
            ),
        ],
        "assertions": ["audit log created", "hash chain continuous", "hash mismatch rejected"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "hash-chain validation may need app-specific script"}],
    },
    "business_rule_condition_false_guard": {
        "template_id": "business_rule_condition_false_guard",
        "name": "Business Rule Condition False Guard Test",
        "artifact_type": "business_rule",
        "category": "negative",
        "description": "Verify the script does not execute when business-rule conditions are false.",
        "required_inputs": ["business_rule_sys_id", "non_matching_fixture"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "set_field_values", "Create non-matching data", "Condition remains false"),
            _step(
                2,
                "assert_record",
                "Assert protected fields and side-effect records are unchanged",
                "No side effects occur",
            ),
        ],
        "assertions": ["condition false", "script skipped", "no side effects"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "script_include_contract": {
        "template_id": "script_include_contract",
        "name": "Script Include Contract Test",
        "artifact_type": "script_include",
        "category": "configuration",
        "description": (
            "Validate script include API contract, guard conditions, negative cases, and audit "
            "side effects where applicable."
        ),
        "required_inputs": ["script_include_sys_id", "method_contract_matrix"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "run_server_script",
                "Invoke script include with valid input",
                "Expected output returned",
            ),
            _step(
                2,
                "run_server_script",
                "Invoke script include with invalid or unauthorized input",
                "Exception or rejection matches design",
            ),
            _step(
                3,
                "assert_record",
                "Assert expected record updates, audit logs, or no-op behavior",
                "Side effects match contract",
            ),
        ],
        "assertions": ["valid inputs pass", "invalid inputs fail", "side effects match contract"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [
            {"reason": "script include methods may require app-specific fixtures"}
        ],
    },
    "client_script_onchange": {
        "template_id": "client_script_onchange",
        "name": "Client Script OnChange Test",
        "artifact_type": "client_script",
        "category": "functional",
        "description": (
            "Set triggering fields such as risk tier, environment, or request state and assert "
            "client-side show/hide, mandatory, read-only, and value behavior."
        ),
        "required_inputs": ["client_script_sys_id"],
        "required_instance_capabilities": ["client_runner"],
        "steps": [
            _step(1, "open_form", "Open target form", "Form opens"),
            _step(
                2,
                "set_field_values",
                "Set risk/environment/state trigger field",
                "Client script runs",
            ),
            _step(
                3,
                "validate_client_script",
                "Validate visible/mandatory/read-only/value behavior",
                "Expected UI behavior observed",
                False,
            ),
        ],
        "assertions": ["client-side behavior matches expectation"],
        "cleanup": [],
        "manual_fallbacks": [
            {"reason": "workspace/portal client behavior may require manual evidence"}
        ],
    },
    "client_script_submit_integrity": {
        "template_id": "client_script_submit_integrity",
        "name": "Client Script Submit Integrity Test",
        "artifact_type": "client_script",
        "category": "end_to_end",
        "description": "Validate form submission remains valid after client-script behavior runs.",
        "required_inputs": ["client_script_sys_id", "valid_submission_data"],
        "required_instance_capabilities": ["client_runner"],
        "steps": [
            _step(1, "open_form", "Open target form", "Form opens"),
            _step(
                2,
                "set_field_values",
                "Apply client-script-triggering values",
                "Client behavior applies",
            ),
            _step(
                3,
                "submit_form",
                "Submit form after client-script changes",
                "Form submits successfully",
            ),
            _step(4, "assert_record", "Assert submitted record fields", "Persisted data is valid"),
        ],
        "assertions": ["client behavior does not break submit", "submitted data valid"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "client runner is required for browser-side validation"}],
    },
    "ui_policy_positive_negative": {
        "template_id": "ui_policy_positive_negative",
        "name": "UI Policy Positive and Negative Test",
        "artifact_type": "ui_policy",
        "category": "functional",
        "description": (
            "Validate UI policy actions for matching and non-matching risk tier, environment, "
            "or request state conditions."
        ),
        "required_inputs": ["ui_policy_sys_id"],
        "required_instance_capabilities": ["client_runner"],
        "steps": [
            _step(1, "open_form", "Open target form/catalog item", "Form opens"),
            _step(
                2,
                "validate_ui_policy",
                "Set condition true and validate actions",
                "Actions applied",
            ),
            _step(
                3,
                "validate_ui_policy",
                "Set condition false and validate reverse behavior",
                "Reverse behavior applied",
            ),
        ],
        "assertions": ["mandatory/visible/read-only behavior correct"],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "ui_policy_submit_integrity": {
        "template_id": "ui_policy_submit_integrity",
        "name": "UI Policy Submit Integrity Test",
        "artifact_type": "ui_policy",
        "category": "end_to_end",
        "description": "Validate form submission remains valid after UI policy actions run.",
        "required_inputs": ["ui_policy_sys_id", "valid_submission_data"],
        "required_instance_capabilities": ["client_runner"],
        "steps": [
            _step(1, "open_form", "Open target form/catalog item", "Form opens"),
            _step(2, "validate_ui_policy", "Apply UI policy actions", "Actions applied"),
            _step(3, "submit_form", "Submit after UI policy changes", "Submission succeeds"),
            _step(4, "assert_record", "Assert persisted values", "Record data is valid"),
        ],
        "assertions": ["mandatory/read-only state correct", "submission remains valid"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "acl_allowed_role": {
        "template_id": "acl_allowed_role",
        "name": "ACL Allowed Role Test",
        "artifact_type": "acl",
        "category": "security",
        "description": "Impersonate allowed role and verify operation succeeds.",
        "required_inputs": ["acl_sys_id", "allowed_roles"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "impersonate", "Impersonate allowed user", "User impersonated"),
            _step(2, "assert_record", "Verify operation succeeds", "Access allowed"),
        ],
        "assertions": ["allowed role can perform operation"],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "acl_denied_role": {
        "template_id": "acl_denied_role",
        "name": "ACL Denied Role Test",
        "artifact_type": "acl",
        "category": "security",
        "description": "Impersonate denied role and verify operation fails.",
        "required_inputs": ["acl_sys_id", "denied_roles"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "impersonate", "Impersonate denied user", "User impersonated"),
            _step(2, "assert_record", "Verify operation is denied", "Access denied"),
        ],
        "assertions": ["denied role cannot perform operation"],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "notification_trigger_recipient_content": {
        "template_id": "notification_trigger_recipient_content",
        "name": "Notification Trigger Recipient and Content Test",
        "artifact_type": "notification",
        "category": "end_to_end",
        "description": (
            "Validate notification trigger state/change, recipient selection, request details, "
            "and approval links."
        ),
        "required_inputs": ["notification_sys_id", "expected_recipients", "content_assertions"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "set_field_values",
                "Create or update record to trigger notification",
                "Notification condition matches",
            ),
            _step(
                2,
                "assert_record",
                "Assert generated email recipient list",
                "Recipients match expected users/groups",
            ),
            _step(
                3,
                "assert_record",
                "Assert email content includes request details and approval links",
                "Email content contains required details",
            ),
        ],
        "assertions": ["trigger correct", "recipients correct", "content correct", "links present"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [{"reason": "outbound email capture may depend on instance settings"}],
    },
    "notification_unrelated_update_suppression": {
        "template_id": "notification_unrelated_update_suppression",
        "name": "Notification Unrelated Update Suppression Test",
        "artifact_type": "notification",
        "category": "negative",
        "description": "Validate no notification is sent for unrelated updates.",
        "required_inputs": ["notification_sys_id", "unrelated_update_fixture"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "set_field_values",
                "Apply unrelated record update",
                "Notification condition stays false",
            ),
            _step(
                2,
                "assert_record",
                "Assert no outbound email/event was created",
                "No notification is sent",
            ),
        ],
        "assertions": ["unrelated updates do not send notification"],
        "cleanup": [{"strategy": "delete_created_records"}],
        "manual_fallbacks": [],
    },
    "report_filters_visibility_aggregation": {
        "template_id": "report_filters_visibility_aggregation",
        "name": "Report Filters Visibility and Aggregation Test",
        "artifact_type": "report",
        "category": "configuration",
        "description": (
            "Validate report filters, role-based visibility, aggregations, grouping, and date "
            "filters."
        ),
        "required_inputs": ["report_sys_id", "expected_filter_result_set", "roles_to_test"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1,
                "assert_record",
                "Validate report filter encoded query",
                "Filters are configured correctly",
            ),
            _step(
                2,
                "assert_record",
                "Validate aggregation, grouping, and date filters",
                "Report calculations match",
            ),
            _step(
                3,
                "impersonate",
                "Impersonate visible and restricted users",
                "Visibility users impersonated",
            ),
            _step(
                4, "assert_record", "Assert role-based report visibility", "Visibility is enforced"
            ),
        ],
        "assertions": [
            "filters return right records",
            "visibility enforced",
            "aggregations correct",
        ],
        "cleanup": [],
        "manual_fallbacks": [{"reason": "visual chart rendering may require manual evidence"}],
    },
    "dashboard_role_visibility": {
        "template_id": "dashboard_role_visibility",
        "name": "Dashboard Role Visibility Test",
        "artifact_type": "dashboard",
        "category": "security",
        "description": "Validate dashboard role-based visibility and widget/date-filter behavior.",
        "required_inputs": ["dashboard_sys_id", "roles_to_test"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1, "impersonate", "Impersonate dashboard viewer role", "Allowed user impersonated"
            ),
            _step(
                2,
                "assert_record",
                "Assert dashboard and widgets are visible",
                "Dashboard is visible",
            ),
            _step(
                3, "impersonate", "Impersonate unauthorized user", "Unauthorized user impersonated"
            ),
            _step(
                4,
                "assert_record",
                "Assert dashboard is not visible",
                "Protected dashboard is hidden",
            ),
        ],
        "assertions": [
            "dashboard visible to allowed roles",
            "dashboard hidden from unauthorized users",
        ],
        "cleanup": [],
        "manual_fallbacks": [{"reason": "dashboard widget rendering may need screenshot evidence"}],
    },
    "scoped_app_roles_security_dependencies": {
        "template_id": "scoped_app_roles_security_dependencies",
        "name": "Scoped App Roles Security and Dependencies Test",
        "artifact_type": "scoped_app",
        "category": "security",
        "description": (
            "Validate required roles can use the app, unauthorized users cannot access protected "
            "data or privileged actions, and cross-scope dependencies are valid."
        ),
        "required_inputs": ["application_sys_id", "required_roles", "protected_tables"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(
                1, "impersonate", "Impersonate required role user", "Authorized user impersonated"
            ),
            _step(
                2,
                "assert_record",
                "Assert app data/actions are available",
                "Authorized access succeeds",
            ),
            _step(
                3, "impersonate", "Impersonate unauthorized user", "Unauthorized user impersonated"
            ),
            _step(
                4,
                "assert_record",
                "Assert protected data/actions are blocked",
                "Unauthorized access denied",
            ),
            _step(
                5,
                "assert_record",
                "Validate cross-scope access and application dependency records",
                "Dependencies are valid",
            ),
        ],
        "assertions": [
            "required roles can use app",
            "unauthorized users blocked",
            "cross-scope access valid",
            "dependencies valid",
        ],
        "cleanup": [],
        "manual_fallbacks": [{"reason": "some cross-scope checks may require admin review"}],
    },
    "rest_api_status_payload": {
        "template_id": "rest_api_status_payload",
        "name": "REST API Status and Payload Test",
        "artifact_type": "rest_integration",
        "category": "api",
        "description": "Send REST request and assert HTTP status and response fields.",
        "required_inputs": ["api_name", "method", "expected_status"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "send_rest_request", "Send REST request", "Response returned"),
            _step(
                2, "assert_record", "Assert HTTP status and payload", "Response matches assertions"
            ),
        ],
        "assertions": ["status matches", "payload matches"],
        "cleanup": [{"strategy": "custom"}],
        "manual_fallbacks": [],
    },
    "integration_smoke": {
        "template_id": "integration_smoke",
        "name": "Integration Smoke Test",
        "artifact_type": "rest_integration",
        "category": "smoke",
        "description": "Validate integration endpoint/connection and response shape.",
        "required_inputs": ["api_name"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "send_rest_request", "Validate connection", "Connection succeeds"),
            _step(2, "assert_record", "Validate response shape", "Response shape valid"),
        ],
        "assertions": ["connection ok", "response shape ok"],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "platform_smoke_artifacts_exist": {
        "template_id": "platform_smoke_artifacts_exist",
        "name": "Platform Artifact Smoke Test",
        "artifact_type": "custom",
        "category": "smoke",
        "description": "Verify table, catalog item, flow, and required role records exist and are active.",
        "required_inputs": ["table_name", "catalog_item_sys_id", "flow_sys_id", "required_roles"],
        "required_instance_capabilities": ["tests", "test_steps"],
        "steps": [
            _step(1, "assert_record", "Verify custom table exists", "Table exists"),
            _step(
                2,
                "assert_record",
                "Verify catalog item exists and is active",
                "Catalog item active",
            ),
            _step(
                3,
                "assert_record",
                "Verify flow exists and is active/published",
                "Flow is available",
            ),
            _step(4, "assert_record", "Verify required role records exist", "Roles exist"),
        ],
        "assertions": ["table exists", "catalog item active", "flow exists", "roles exist"],
        "cleanup": [],
        "manual_fallbacks": [],
    },
    "post_deployment_smoke": {
        "template_id": "post_deployment_smoke",
        "name": "Post-Deployment Smoke Test",
        "artifact_type": "custom",
        "category": "smoke",
        "description": "Validate critical paths after deployment.",
        "required_inputs": [],
        "required_instance_capabilities": ["tests"],
        "steps": [
            _step(
                1,
                "manual_instruction",
                "Run smoke validation checklist",
                "Critical paths validated",
                False,
            )
        ],
        "assertions": ["deployment smoke checks complete"],
        "cleanup": [],
        "manual_fallbacks": [{"reason": "business validation often needs manual evidence"}],
    },
    "upgrade_regression": {
        "template_id": "upgrade_regression",
        "name": "Upgrade Regression Test",
        "artifact_type": "custom",
        "category": "regression",
        "description": "Validate release-critical behavior after upgrade or clone.",
        "required_inputs": [],
        "required_instance_capabilities": ["tests"],
        "steps": [
            _step(
                1,
                "manual_instruction",
                "Execute upgrade regression suite",
                "Regression suite completed",
                False,
            )
        ],
        "assertions": ["upgrade regression complete"],
        "cleanup": [],
        "manual_fallbacks": [{"reason": "upgrade validation may include manual checks"}],
    },
}


ARTIFACT_TEMPLATE_MAP = {
    "catalog_item": [
        "catalog_item_smoke",
        "catalog_item_variable_configuration",
        "catalog_item_mandatory",
        "catalog_item_submission_ritm",
    ],
    "record_producer": ["record_producer_target"],
    "flow": [
        "flow_trigger_outcome",
        "flow_dynamic_approval_routing",
        "flow_approval_lifecycle_paths",
        "flow_request_state_work_notes",
        "flow_failure_retry_behavior",
        "flow_subflow_business_journey",
    ],
    "table": [
        "table_crud",
        "table_dictionary_configuration",
        "table_field_validation",
        "table_acl_crud_by_role",
    ],
    "business_rule": [
        "business_rule_positive",
        "business_rule_negative",
        "business_rule_duplicate_similarity_threshold",
        "business_rule_audit_hash_chain",
        "business_rule_condition_false_guard",
    ],
    "script_include": ["script_include_contract"],
    "client_script": ["client_script_onchange", "client_script_submit_integrity"],
    "ui_policy": ["ui_policy_positive_negative", "ui_policy_submit_integrity"],
    "acl": ["acl_allowed_role", "acl_denied_role"],
    "notification": [
        "notification_trigger_recipient_content",
        "notification_unrelated_update_suppression",
    ],
    "report": ["report_filters_visibility_aggregation"],
    "dashboard": ["dashboard_role_visibility"],
    "scoped_app": ["scoped_app_roles_security_dependencies"],
    "rest_integration": ["rest_api_status_payload", "integration_smoke"],
    "custom": ["platform_smoke_artifacts_exist", "post_deployment_smoke", "upgrade_regression"],
}


ATF_SUITE_ORDER = ["smoke", "configuration", "end_to_end", "security_regression"]

ATF_SUITE_STRUCTURE: Dict[str, Dict[str, Any]] = {
    "smoke": {
        "suite_key": "smoke",
        "name": "Smoke",
        "description": "Verify table, catalog item, flow, and role records exist and are active.",
    },
    "configuration": {
        "suite_key": "configuration",
        "name": "Configuration",
        "description": "Validate fields, ACLs, choices, scripts, and settings.",
    },
    "end_to_end": {
        "suite_key": "end_to_end",
        "name": "End-to-End",
        "description": (
            "Run complete catalog/request/flow/subflow/approval/notification journeys."
        ),
    },
    "security_regression": {
        "suite_key": "security_regression",
        "name": "Security & Regression",
        "description": (
            "Validate unauthorized access, protected data, retry/negative paths, reports, "
            "dashboards, cross-scope access, and release regression risk."
        ),
    },
}

TEMPLATE_SUITE_MAP = {
    "catalog_item_smoke": "smoke",
    "platform_smoke_artifacts_exist": "smoke",
    "integration_smoke": "smoke",
    "post_deployment_smoke": "smoke",
    "catalog_item_variable_configuration": "configuration",
    "table_dictionary_configuration": "configuration",
    "table_field_validation": "configuration",
    "business_rule_positive": "configuration",
    "script_include_contract": "configuration",
    "client_script_onchange": "configuration",
    "ui_policy_positive_negative": "configuration",
    "report_filters_visibility_aggregation": "configuration",
    "rest_api_status_payload": "configuration",
    "catalog_item_submission_ritm": "end_to_end",
    "record_producer_target": "end_to_end",
    "flow_trigger_outcome": "end_to_end",
    "flow_dynamic_approval_routing": "end_to_end",
    "flow_approval_lifecycle_paths": "end_to_end",
    "flow_request_state_work_notes": "end_to_end",
    "flow_subflow_business_journey": "end_to_end",
    "client_script_submit_integrity": "end_to_end",
    "ui_policy_submit_integrity": "end_to_end",
    "notification_trigger_recipient_content": "end_to_end",
    "catalog_item_mandatory": "security_regression",
    "business_rule_negative": "security_regression",
    "business_rule_duplicate_similarity_threshold": "security_regression",
    "business_rule_audit_hash_chain": "security_regression",
    "business_rule_condition_false_guard": "security_regression",
    "flow_failure_retry_behavior": "security_regression",
    "table_acl_crud_by_role": "security_regression",
    "acl_allowed_role": "security_regression",
    "acl_denied_role": "security_regression",
    "notification_unrelated_update_suppression": "security_regression",
    "dashboard_role_visibility": "security_regression",
    "scoped_app_roles_security_dependencies": "security_regression",
    "upgrade_regression": "security_regression",
}


def _suite_key_for_template(template_id: str, category: str) -> str:
    if template_id in TEMPLATE_SUITE_MAP:
        return TEMPLATE_SUITE_MAP[template_id]
    if category == "smoke":
        return "smoke"
    if category in {"configuration", "data_validation", "api"}:
        return "configuration"
    if category in {"functional", "end_to_end"}:
        return "end_to_end"
    return "security_regression"


def get_template(template_id: str) -> Dict[str, Any]:
    """Return a deep-copied ATF template by identifier.

    Deep copy protects canonical in-memory templates from runtime mutation.
    """
    template = deepcopy(TEMPLATES[template_id])
    suite_key = _suite_key_for_template(template_id, template.get("category", "functional"))
    template["suite_key"] = suite_key
    template["suite_name"] = ATF_SUITE_STRUCTURE[suite_key]["name"]
    return template


def select_templates(
    artifact_type: str,
    depth: str = "standard",
    include_negative: bool = True,
    include_security: bool = True,
) -> List[Dict[str, Any]]:
    """Select templates for an artifact type with optional category filtering.

    Args:
        artifact_type: Artifact family key (flow, acl, catalog_item, etc.).
        depth: Selection depth; ``smoke`` restricts to smoke templates where possible.
        include_negative: Include negative-path templates when True.
        include_security: Include security-category templates when True.

    Returns:
        List[Dict[str, Any]]: Deep-copied templates ready for downstream planning.
    """
    ids = list(ARTIFACT_TEMPLATE_MAP.get(artifact_type, ["post_deployment_smoke"]))
    if depth == "smoke":
        ids = [item for item in ids if TEMPLATES[item]["category"] == "smoke"] or ids[:1]
    if not include_negative:
        ids = [item for item in ids if TEMPLATES[item]["category"] != "negative"]
    if not include_security:
        ids = [item for item in ids if TEMPLATES[item]["category"] != "security"]
    return [get_template(item) for item in ids]
