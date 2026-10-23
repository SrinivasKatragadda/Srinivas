"""Pydantic models for the ATF Framework Support tools.

Comprehensive collection of 50+ data models for ATF (Automated Test Framework)
operations including test generation, execution, coverage analysis, and quality
assessment. Organized by functional category: artifact targeting, coverage analysis,
test generation, CRUD operations, execution, results analysis, and quality gates.

MODEL CATEGORIES:
- Data Models (4): ATFArtifactTarget, ATFStepPlan, ATFTestPlan, type literals
- Discovery (1): DiscoverATFCapabilitiesParams, ArtifactFilters
- Coverage Analysis (2): AnalyzeATFCoverageParams, RegressionPackParams
- Test Generation (9): Generate* params for artifacts and scopes
- Test CRUD (10+): Create, List, Get, Update, Delete, Clone operations
- Test Steps (6): Step CRUD operations with bulk support
- Execution (6): Execute tests with runner strategy selection
- Results (5): Collect, explain, configure, route results
- Quality (3): Quality gates, readiness, scheduling
- Fallback (1): Manual test fallback when automation not possible

ARCHITECTURAL PATTERNS:

1. Parameters Pattern: All [Name]Params follow standard structure:
   - Required fields first (no defaults)
   - Optional overrides with defaults
   - dry_run: bool = False (preview/commit control)
   - Literal enums for constrained choices

2. Request Pattern: [Action]Request classes for LLM integration
   - Extends Parameters with human context
   - More narrative descriptions
   - Supports both CLI and LLM calling

3. CRUD Pattern: Create, Read, Update, Delete, Clone operations
   - List: Query/search with filters and limits
   - Get: Retrieve single record with optional includes
   - Create: Validation with dry_run preview
   - Update: Modify fields with optional rollback
   - Delete: Remove with force and cascade options
   - Clone: Duplicate to new record

4. Runner Strategy: Multiple execution contexts
   - standard: In-UI runner
   - client_runner: Client-side for desktop steps
   - scheduled_client_runner: Scheduled execution
   - cloud_runner_if_available: Cloud with fallback

VALIDATION & CONSTRAINTS:
- Query limits: max 500 tests/suites, max 250 steps
- Coverage: default 80%, configurable 0-100
- Timeouts: 120-300 seconds with sensible defaults
- test_depth: smoke (basic) | standard (normal) | regression (comprehensive)
- Role-based: Include/exclude role-based variants
- Security: Include/exclude security-focused tests

TYPE HINTS:
- ArtifactType: 10 types (catalog_item, flow, table, business_rule, etc.)
- TestCategory: 8 types (smoke, functional, regression, security, etc.)
- StepType: 12 types (open_form, set_field_values, send_rest_request, etc.)
- RunnerStrategy: 4 options (standard, client_runner, scheduled, cloud)
- TestDepth: 4 levels (smoke, standard, regression, production_release)

USAGE:
    # Generate test plans
    req = GenerateATFTestPlanParams(
        target_type='artifact',
        artifact_type='table',
        testing_depth='standard',
        dry_run=True
    )

    # Execute tests
    exec_req = ATFRunAndCollectParams(
        test_sys_id='test123',
        runner_strategy='cloud_runner_if_available',
        timeout_seconds=300
    )

    # Quality gate
    gate = ATFQualityGateRequest(
        release_name='London Patch 5',
        minimum_coverage_percent=85,
        allow_failed_tests=False
    )

DEPENDENCIES:
- ATFServiceNowClient: Query interface for ATF operations
- ATFEditDiscoveryService: ATF storage and input discovery
- Used by: quality_gate, failure_analysis, atf_service, tools
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ArtifactType = Literal[
    "catalog_item",
    "record_producer",
    "flow",
    "table",
    "business_rule",
    "script_include",
    "client_script",
    "ui_policy",
    "acl",
    "notification",
    "report",
    "dashboard",
    "scoped_app",
    "rest_integration",
    "custom",
]
TestCategory = Literal[
    "smoke",
    "configuration",
    "functional",
    "end_to_end",
    "regression",
    "negative",
    "security",
    "role_based",
    "api",
    "data_validation",
]
StepType = Literal[
    "open_form",
    "set_field_values",
    "submit_form",
    "assert_record",
    "assert_field_value",
    "impersonate",
    "run_server_script",
    "send_rest_request",
    "validate_ui_policy",
    "validate_client_script",
    "validate_flow_execution",
    "cleanup_record",
    "manual_instruction",
]


class ATFArtifactTarget(BaseModel):
    """Identifies the ServiceNow artifact that ATF tests will validate."""

    artifact_type: ArtifactType
    artifact_table: Optional[str] = None  # Table name if not inferred from type
    artifact_sys_id: Optional[str] = None  # Specific record sys_id to target
    artifact_name: Optional[str] = None  # Display name for reference


class ATFStepPlan(BaseModel):
    """Blueprint for a single ATF test step with action and expected result."""

    order: int  # Execution sequence number
    step_type: StepType  # e.g., "automated_test_step", "manual_instruction"
    description: str  # Step title and narrative
    config: Dict[str, Any] = Field(default_factory=dict)  # Step configuration
    expected_result: str  # What should happen after this step
    is_automatable: bool = True  # Whether ATF can automate this step


class ATFTestPlan(BaseModel):
    """Complete test specification including purpose, target artifact, and steps."""

    purpose: str  # What this test validates
    target_artifact: ATFArtifactTarget  # The artifact being tested
    test_category: TestCategory  # e.g., "smoke", "regression", "security"
    preconditions: List[str] = Field(default_factory=list)  # Setup requirements
    test_data_strategy: str = (
        "Use generated test data and clean up after execution."  # How to prepare test data
    )
    atf_steps: List[ATFStepPlan]  # Ordered list of test steps
    assertions: List[str] = Field(default_factory=list)  # Validation checks
    cleanup_strategy: str = "delete_created_records"  # Post-test teardown approach
    expected_result: str  # Overall test expected outcome


class DiscoverATFCapabilitiesParams(BaseModel):
    """Parameters for discovering ATF table availability and feature support."""

    instance: Optional[str] = None  # ServiceNow instance (optional override)
    username: Optional[str] = None  # User context (optional)
    app_scope: Optional[str] = None  # Application scope for scoped table checks
    check_cloud_runner: bool = True  # Verify Cloud Runner plugin availability
    check_client_runner: bool = True  # Verify Client Runner availability
    check_permissions: bool = True  # Check table read permissions
    deep_check: bool = False  # Query all ATF tables vs quick check
    include_field_details: bool = False  # Include field lists in response


class ArtifactFilters(BaseModel):
    """Filters for selecting which artifact types to include in ATF coverage analysis."""

    include_catalog_items: bool = True
    include_record_producers: bool = True
    include_flows: bool = True
    include_tables: bool = True
    include_business_rules: bool = True
    include_script_includes: bool = True
    include_client_scripts: bool = True
    include_ui_policies: bool = True
    include_acls: bool = True
    include_notifications: bool = True
    include_reports: bool = True
    include_dashboards: bool = True
    include_scoped_apps: bool = True
    include_integrations: bool = True


class AnalyzeATFCoverageParams(BaseModel):
    """Parameters for analyzing test coverage across release/update set/application."""

    release_name: Optional[str] = None
    update_set_sys_ids: List[str] = Field(default_factory=list)
    suite_sys_ids: List[str] = Field(
        default_factory=list,
        description="Optional ATF suite sys_ids to include in suite and test-case coverage.",
    )
    application_scope: Optional[str] = None
    artifact_filters: ArtifactFilters = Field(default_factory=ArtifactFilters)
    minimum_required_coverage_percent: float = 80
    minimum_required_category_coverage_percent: float = Field(
        80,
        description=(
            "Minimum percent of recommended template/test categories covered for each artifact."
        ),
    )
    include_existing_tests: bool = True
    include_suite_membership: bool = True
    dry_run: bool = False


class GenerateATFTestPlanParams(BaseModel):
    """Parameters for generating test plans from templates for an artifact or scope."""

    target_type: Literal["release", "update_set", "application_scope", "artifact"]
    target_identifier: str
    artifact_type: Optional[str] = None
    artifact_table: Optional[str] = None
    artifact_sys_id: Optional[str] = None
    testing_depth: Literal["smoke", "standard", "regression", "production_release"] = "standard"
    include_negative_tests: bool = True
    include_security_tests: bool = False
    include_role_based_tests: bool = False
    include_cleanup_steps: bool = True
    dry_run: bool = True


class ATFTestCreateRequest(BaseModel):
    """Request to create a new ATF test with steps."""

    test_name: str
    description: str
    application_scope: str = "mcp"
    target_artifact: ATFArtifactTarget
    test_category: TestCategory = "functional"
    steps: List[ATFStepPlan]
    test_data: Dict[str, Any] = Field(default_factory=dict)
    cleanup_strategy: Literal[
        "none", "delete_created_records", "deactivate_created_records", "rollback_update", "custom"
    ] = "delete_created_records"
    dry_run: bool = False


class ATFTestListParams(BaseModel):
    """Parameters for listing and filtering ATF tests."""

    query: Optional[str] = None
    application_scope: Optional[str] = None
    active: Optional[bool] = None
    include_steps: bool = True
    include_suite_membership: bool = True
    limit: int = Field(50, ge=1, le=500)


class ATFTestGetParams(BaseModel):
    """Parameters for retrieving a specific ATF test with optional related data."""

    test_sys_id: str
    include_steps: bool = True
    include_suite_membership: bool = True
    include_results: bool = False


class ATFTestUpdateParams(BaseModel):
    """Parameters for updating ATF test properties."""

    test_sys_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    dry_run: bool = False


class ATFTestDeleteParams(BaseModel):
    """Parameters for deleting an ATF test."""

    test_sys_id: str
    delete_steps: bool = True
    dry_run: bool = True
    force: bool = False


class ATFTestCloneParams(BaseModel):
    """Parameters for cloning an existing ATF test."""

    source_test_sys_id: str
    cloned_test_name: Optional[str] = None
    cloned_description: Optional[str] = None
    include_steps: bool = True
    dry_run: bool = False


class ATFStepListParams(BaseModel):
    """Parameters for listing ATF test steps within a test."""

    test_sys_id: str
    include_test: bool = False
    include_config_preview: bool = True
    limit: int = Field(250, ge=1, le=500)


class ATFStepGetParams(BaseModel):
    """Parameters for retrieving a specific ATF test step with optional details."""

    step_sys_id: str
    include_test: bool = True
    include_config_preview: bool = True


class ATFStepCreateParams(BaseModel):
    """Parameters for creating a single ATF test step."""

    test_sys_id: str
    step: ATFStepPlan
    configure_native_step: bool = True
    dry_run: bool = False


class ATFStepBulkCreateParams(BaseModel):
    """Parameters for bulk creating multiple ATF test steps at once."""

    test_sys_id: str
    steps: List[ATFStepPlan]
    replace_existing_steps: bool = False
    configure_native_steps: bool = True
    dry_run: bool = False


class ATFStepUpdateParams(BaseModel):
    """Parameters for updating a single ATF test step."""

    step_sys_id: str
    order: Optional[int] = None
    step_type: Optional[StepType] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    expected_result: Optional[str] = None
    configure_native_step: bool = True
    dry_run: bool = False


class ATFStepBulkUpdateItem(BaseModel):
    """A single step update in a bulk update operation."""

    step_sys_id: str
    order: Optional[int] = None
    step_type: Optional[StepType] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    expected_result: Optional[str] = None


class ATFStepBulkUpdateParams(BaseModel):
    """Parameters for bulk updating multiple ATF test steps."""

    test_sys_id: str
    updates: List[ATFStepBulkUpdateItem]
    configure_native_steps: bool = True
    dry_run: bool = False


class ATFStepDeleteParams(BaseModel):
    """Parameters for deleting an ATF test step."""

    step_sys_id: str
    dry_run: bool = True
    force: bool = False


class ATFStepCloneParams(BaseModel):
    """Parameters for cloning an existing ATF step to another test."""

    source_step_sys_id: str
    target_test_sys_id: Optional[str] = None
    order: Optional[int] = None
    dry_run: bool = False


class ATFStepReorderParams(BaseModel):
    """Parameters for reordering steps within a test."""

    test_sys_id: str
    ordered_step_sys_ids: List[str]
    dry_run: bool = False


class ATFSuiteCreateRequest(BaseModel):
    """Request to create a new ATF test suite with optional initial tests."""

    suite_name: str
    description: Optional[str] = None
    application_scope: str = "mcp"
    release_name: Optional[str] = None
    parent_suite_sys_id: Optional[str] = None
    suite_type: Literal[
        "smoke",
        "regression",
        "release",
        "upgrade",
        "application",
        "catalog",
        "integration",
        "security",
    ] = "regression"
    test_sys_ids: List[str] = Field(default_factory=list)
    execution_order_strategy: Literal[
        "given_order", "smoke_first", "dependency_order", "criticality_order"
    ] = "given_order"
    dry_run: bool = False


class ATFSuiteListParams(BaseModel):
    """Parameters for listing and filtering ATF test suites."""

    query: Optional[str] = None
    application_scope: Optional[str] = None
    active: Optional[bool] = None
    include_tests: bool = True
    include_results: bool = False
    limit: int = Field(50, ge=1, le=500)


class ATFSuiteGetParams(BaseModel):
    """Parameters for retrieving a specific ATF test suite with optional related data."""

    suite_sys_id: str
    include_tests: bool = True
    include_test_steps: bool = False
    include_results: bool = False


class ATFSuiteUpdateParams(BaseModel):
    """Parameters for updating ATF test suite properties."""

    suite_sys_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    suite_type: Optional[
        Literal[
            "smoke",
            "regression",
            "release",
            "upgrade",
            "application",
            "catalog",
            "integration",
            "security",
        ]
    ] = None
    execution_order_strategy: Optional[
        Literal["given_order", "smoke_first", "dependency_order", "criticality_order"]
    ] = None
    dry_run: bool = False


class ATFSuiteDeleteParams(BaseModel):
    suite_sys_id: str
    delete_suite_test_mappings: bool = True
    dry_run: bool = True
    force: bool = False


class ATFSuiteCloneParams(BaseModel):
    source_suite_sys_id: str
    cloned_suite_name: Optional[str] = None
    cloned_description: Optional[str] = None
    include_tests: bool = True
    clone_tests: bool = False
    dry_run: bool = False


class AddTestsToATFSuiteParams(BaseModel):
    suite_sys_id: str
    test_sys_ids: List[str]
    execution_order_strategy: Literal[
        "given_order", "smoke_first", "dependency_order", "criticality_order"
    ] = "given_order"
    dry_run: bool = False


class RemoveTestsFromATFSuiteParams(BaseModel):
    """Parameters for removing specific tests from an ATF test suite.

    Deletes the ``sys_atf_test_suite_test`` mapping records that link the
    specified tests to the suite. The tests themselves are not deleted.
    """

    suite_sys_id: str  # sys_id of the suite to modify
    test_sys_ids: List[str]  # sys_ids of the tests to remove from the suite
    dry_run: bool = False  # Preview which mappings would be removed without committing


class ReorderTestsInATFSuiteParams(BaseModel):
    """Parameters for reordering tests within an ATF test suite.

    Accepts the full ordered list of test sys_ids that should remain in the
    suite. Tests are assigned ``order`` values starting from 1 in the
    supplied sequence. Any test sys_id already in the suite but omitted from
    ``ordered_test_sys_ids`` keeps its current mapping and order.
    """

    suite_sys_id: str  # sys_id of the suite whose test order will change
    ordered_test_sys_ids: List[str]  # Desired test sequence (all must already be in the suite)
    dry_run: bool = False  # Preview planned reorder without committing


class UpdateSetGenerationParams(BaseModel):
    update_set_sys_id: str
    application_scope: str = "mcp"
    testing_depth: Literal["smoke", "standard", "regression", "production_release"] = "standard"
    create_suite: bool = True
    dry_run: bool = False


class CatalogItemGenerationParams(BaseModel):
    catalog_item_sys_id: str
    catalog_item_name: Optional[str] = None
    application_scope: str = "mcp"
    test_depth: Literal["smoke", "standard", "regression", "production_release"] = "standard"
    include_variable_validation: bool = True
    include_mandatory_validation: bool = True
    include_submission_test: bool = True
    include_flow_or_workflow_validation: bool = True
    include_approval_validation: bool = False
    include_task_generation_validation: bool = True
    include_negative_tests: bool = True
    include_role_based_tests: bool = False
    test_user: Optional[str] = None
    dry_run: bool = False


class RecordProducerGenerationParams(BaseModel):
    record_producer_sys_id: str
    target_table: Optional[str] = None
    application_scope: str = "mcp"
    test_depth: Literal["smoke", "standard", "regression", "production_release"] = "standard"
    include_variable_validation: bool = True
    include_target_record_assertion: bool = True
    include_mapping_validation: bool = True
    include_negative_tests: bool = True
    dry_run: bool = False


class FlowGenerationParams(BaseModel):
    flow_sys_id: str
    flow_name: Optional[str] = None
    flow_type: Literal["flow", "subflow", "action"] = "flow"
    trigger_table: Optional[str] = None
    trigger_condition: Optional[str] = None
    application_scope: str = "mcp"
    include_trigger_validation: bool = True
    include_action_output_validation: bool = True
    include_error_path_validation: bool = False
    include_record_state_validation: bool = True
    dry_run: bool = False


class TableGenerationParams(BaseModel):
    table_name: str
    application_scope: str = "mcp"
    test_crud: bool = True
    test_mandatory_fields: bool = True
    test_dictionary_rules: bool = True
    test_reference_fields: bool = True
    test_choice_fields: bool = True
    test_acl_access: bool = True
    roles_to_test: List[str] = Field(default_factory=list)
    dry_run: bool = False


class BusinessRuleGenerationParams(BaseModel):
    business_rule_sys_id: str
    application_scope: str = "mcp"
    target_table: Optional[str] = None
    include_condition_match_test: bool = True
    include_condition_mismatch_test: bool = True
    include_before_after_validation: bool = True
    include_negative_test: bool = True
    dry_run: bool = False


class ScriptIncludeGenerationParams(BaseModel):
    script_include_sys_id: str
    application_scope: str = "mcp"
    include_contract_tests: bool = True
    include_negative_tests: bool = True
    include_security_tests: bool = False
    dry_run: bool = False


class ClientScriptGenerationParams(BaseModel):
    client_script_sys_id: str
    application_scope: str = "mcp"
    target_table: Optional[str] = None
    ui_type: Literal["desktop", "portal", "workspace", "unknown"] = "unknown"
    include_onload: bool = True
    include_onchange: bool = True
    include_onsubmit: bool = True
    dry_run: bool = False


class UIPolicyGenerationParams(BaseModel):
    ui_policy_sys_id: str
    ui_policy_type: Literal["table_ui_policy", "catalog_ui_policy"] = "table_ui_policy"
    application_scope: str = "mcp"
    include_positive_condition: bool = True
    include_negative_condition: bool = True
    dry_run: bool = False


class NotificationGenerationParams(BaseModel):
    notification_sys_id: str
    notification_name: Optional[str] = None
    application_scope: str = "mcp"
    include_trigger_validation: bool = True
    include_recipient_validation: bool = True
    include_content_validation: bool = True
    include_negative_tests: bool = True
    dry_run: bool = False


class ReportGenerationParams(BaseModel):
    report_sys_id: str
    report_name: Optional[str] = None
    application_scope: str = "mcp"
    include_filter_validation: bool = True
    include_visibility_validation: bool = True
    include_aggregation_validation: bool = True
    dry_run: bool = False


class DashboardGenerationParams(BaseModel):
    dashboard_sys_id: str
    dashboard_name: Optional[str] = None
    application_scope: str = "mcp"
    include_visibility_validation: bool = True
    include_widget_filter_validation: bool = True
    dry_run: bool = False


class ScopedAppGenerationParams(BaseModel):
    application_sys_id: str
    application_name: Optional[str] = None
    application_scope: str = "mcp"
    include_role_validation: bool = True
    include_unauthorized_validation: bool = True
    include_cross_scope_validation: bool = True
    dry_run: bool = False


class ACLGenerationParams(BaseModel):
    acl_sys_id: str
    target_table: Optional[str] = None
    target_field: Optional[str] = None
    operation: Literal["read", "create", "write", "delete"] = "read"
    allowed_roles: List[str] = Field(default_factory=list)
    denied_roles: List[str] = Field(default_factory=list)
    test_users: List[str] = Field(default_factory=list)
    dry_run: bool = False


class ATFFourSuitePackParams(BaseModel):
    pack_name: str
    application_scope: str = "mcp"
    release_name: Optional[str] = None
    smoke_test_sys_ids: List[str] = Field(default_factory=list)
    configuration_test_sys_ids: List[str] = Field(default_factory=list)
    end_to_end_test_sys_ids: List[str] = Field(default_factory=list)
    security_regression_test_sys_ids: List[str] = Field(default_factory=list)
    dry_run: bool = False


class RestIntegrationGenerationParams(BaseModel):
    api_type: Literal["scripted_rest", "table_api", "import_set_api", "external_rest_message"]
    api_name: str
    endpoint: Optional[str] = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    request_body: Dict[str, Any] = Field(default_factory=dict)
    expected_status: int = 200
    expected_response_assertions: List[Dict[str, Any]] = Field(default_factory=list)
    auth_strategy: Literal[
        "current_user", "basic", "oauth", "none", "existing_connection_alias"
    ] = "current_user"
    dry_run: bool = False


class ATFExecutionRequest(BaseModel):
    """Request to execute an ATF test or suite with specified runner and options."""

    test_sys_id: Optional[str] = None
    suite_sys_id: Optional[str] = None
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "standard"
    wait_for_completion: bool = True
    timeout_seconds: int = 120
    store_evidence: bool = True
    dry_run: bool = False


class ATFRunAndCollectParams(BaseModel):
    """Parameters for executing ATF tests and collecting detailed results."""

    test_sys_id: Optional[str] = None
    suite_sys_id: Optional[str] = None
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "standard"
    wait_for_completion: bool = True
    timeout_seconds: int = 300
    include_step_results: bool = True
    include_failure_details: bool = True
    include_test_details: bool = True
    store_evidence: bool = True
    dry_run: bool = False


class RunExistingATFParams(BaseModel):
    """Parameters for running existing ATF tests (by sys_id, name, or query)."""

    test_sys_id: Optional[str] = None
    test_name: Optional[str] = None
    query: Optional[str] = None
    application_scope: Optional[str] = None
    active: Optional[bool] = True
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "cloud_runner_if_available"
    wait_for_completion: bool = True
    timeout_seconds: int = 300
    include_step_results: bool = True
    include_failure_details: bool = True
    include_test_details: bool = True
    store_evidence: bool = True
    dry_run: bool = False
    max_candidates: int = Field(10, ge=1, le=50)


class IncidentATFSuiteRunParams(BaseModel):
    suite_name: str = "Create Incident ATF Suite"
    test_name: str = "Login as Test User and Create Incident"
    application_scope: str = "mcp"
    test_user: Optional[str] = Field(
        None,
        description="User name or sys_id to impersonate with native ATF, not a password.",
    )
    short_description: str = "Test Incident - ATF Automation Test"
    description: str = "This incident was created by a ServiceNow ATF test."
    category: Optional[str] = "software"
    urgency: Optional[str] = "2"
    impact: Optional[str] = "2"
    run_after_create: bool = True
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "standard"
    wait_for_completion: bool = True
    timeout_seconds: int = 300
    dry_run: bool = False


class IncidentFlowATFSuiteRunParams(BaseModel):
    suite_name: str = "System Admin Incident Flow ATF Suite"
    test_name: str = "System Admin Incident Lifecycle Flow"
    application_scope: str = "mcp"
    admin_user: str = Field(
        "admin",
        description="Admin user_name, email, name, or sys_id to impersonate with native ATF.",
    )
    short_description: str = "ATF Test - System Admin Incident Flow"
    description: str = "This incident was created by ATF to validate the incident lifecycle."
    category: Optional[str] = "software"
    urgency: Optional[str] = "3"
    impact: Optional[str] = "3"
    assignment_group: Optional[str] = None
    run_after_create: bool = True
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "standard"
    wait_for_completion: bool = True
    timeout_seconds: int = 300
    dry_run: bool = False


class ATFRequestRouterParams(BaseModel):
    user_request: str
    application_scope: str = "mcp"
    test_user: Optional[str] = None
    dry_run: bool = True
    auto_execute: bool = False
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "standard"


class ATFReadinessParams(BaseModel):
    test_sys_id: Optional[str] = None
    suite_sys_id: Optional[str] = None
    runner_strategy: Literal[
        "standard", "client_runner", "scheduled_client_runner", "cloud_runner_if_available"
    ] = "standard"
    include_results: bool = True


class ATFConfigureStepsParams(BaseModel):
    test_sys_id: str
    dry_run: bool = True
    overwrite_existing_config: bool = False


class CollectATFResultsParams(BaseModel):
    release_name: Optional[str] = None
    suite_sys_id: Optional[str] = None
    test_sys_id: Optional[str] = None
    execution_id: Optional[str] = None
    include_step_results: bool = True
    include_logs: bool = False
    include_failure_details: bool = True


class ExplainATFFailuresParams(BaseModel):
    suite_execution_id: Optional[str] = None
    test_execution_id: Optional[str] = None
    include_recommendations: bool = True
    include_probable_root_cause: bool = True


class CollectATFEvidenceParams(BaseModel):
    release_name: str
    release_package_sys_id: Optional[str] = None
    suite_sys_ids: List[str] = Field(default_factory=list)
    include_passed_tests: bool = True
    include_failed_tests: bool = True
    include_step_details: bool = True
    include_execution_timestamps: bool = True
    include_runner_details: bool = True
    dry_run: bool = False


class ATFQualityGateRequest(BaseModel):
    release_name: str
    release_package_sys_id: Optional[str] = None
    required_suite_sys_ids: List[str] = Field(default_factory=list)
    minimum_coverage_percent: float = 85
    allow_failed_tests: bool = False
    allow_blocked_tests: bool = False
    require_smoke_tests: bool = True
    require_regression_tests: bool = True
    require_critical_artifact_coverage: bool = True


class RegressionPackParams(BaseModel):
    application_scope: str = "mcp"
    release_name: Optional[str] = None
    pack_name: str
    include_existing_tests: bool = True
    generate_missing_tests: bool = True
    artifact_types: List[str] = Field(default_factory=list)
    testing_depth: Literal["smoke", "standard", "regression", "production_release"] = "regression"
    dry_run: bool = False


class ScheduleATFSuiteParams(BaseModel):
    suite_sys_id: str
    schedule_name: str
    schedule_type: Literal[
        "once", "daily", "weekly", "pre_release", "post_clone", "post_upgrade"
    ] = "weekly"
    start_time: str
    timezone: str = "UTC"
    runner_strategy: Literal["scheduled_client_runner", "cloud_runner_if_available", "standard"] = (
        "scheduled_client_runner"
    )
    notify_users: List[str] = Field(default_factory=list)
    dry_run: bool = False


class ManualTestFallbackParams(BaseModel):
    artifact_type: str
    artifact_sys_id: str
    reason_atf_not_possible: str
    test_objective: str
    evidence_required: bool = True
    dry_run: bool = False


class ATFCapabilityReport(BaseModel):
    atf_available: bool
    capabilities: Dict[str, bool] = Field(default_factory=dict)
    verified_tables: List[Dict[str, Any]] = Field(default_factory=list)
    missing_tables: List[str] = Field(default_factory=list)
    missing_permissions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ATFTestCreateResponse(BaseModel):
    status: str
    test_sys_id: Optional[str] = None
    test_name: str
    created_steps: List[Dict[str, Any]] = Field(default_factory=list)
    reused_steps: List[Dict[str, Any]] = Field(default_factory=list)
    manual_fallback_steps: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    rollback: List[Dict[str, Any]] = Field(default_factory=list)


class ATFSuiteCreateResponse(BaseModel):
    status: str
    suite_sys_id: Optional[str] = None
    suite_name: str
    added_tests: List[Dict[str, Any]] = Field(default_factory=list)
    existing_tests: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    rollback: List[Dict[str, Any]] = Field(default_factory=list)


class ATFExecutionResult(BaseModel):
    status: str
    test_sys_id: Optional[str] = None
    suite_sys_id: Optional[str] = None
    execution_id: Optional[str] = None
    result_summary: Dict[str, Any] = Field(default_factory=dict)
    failed_steps: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


class ATFCoverageReport(BaseModel):
    status: str
    coverage_summary: Dict[str, Any] = Field(default_factory=dict)
    coverage_by_artifact_type: List[Dict[str, Any]] = Field(default_factory=list)
    existing_tests: List[Dict[str, Any]] = Field(default_factory=list)
    missing_tests: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_tests: List[Dict[str, Any]] = Field(default_factory=list)


class ATFEvidenceItem(BaseModel):
    release_name: Optional[str] = None
    suite_name: Optional[str] = None
    suite_sys_id: Optional[str] = None
    test_name: Optional[str] = None
    test_sys_id: Optional[str] = None
    execution_id: Optional[str] = None
    result: Optional[str] = None
    failed_step: Optional[str] = None
    error_message: Optional[str] = None
    executed_by: Optional[str] = None
    executed_at: Optional[str] = None
    evidence_url: Optional[str] = None
    risk_impact: Optional[str] = None
    remediation_status: Optional[str] = None


class ATFQualityGateResponse(BaseModel):
    status: Literal["go", "conditional_go", "no_go"]
    atf_quality_score: float
    coverage_percent: float
    blockers: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)


class ATFTemplateDefinition(BaseModel):
    template_id: str
    name: str
    artifact_type: str
    category: str
    description: str
    required_inputs: List[str] = Field(default_factory=list)
    required_instance_capabilities: List[str] = Field(default_factory=list)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    assertions: List[str] = Field(default_factory=list)
    cleanup: List[Dict[str, Any]] = Field(default_factory=list)
    manual_fallbacks: List[Dict[str, Any]] = Field(default_factory=list)
