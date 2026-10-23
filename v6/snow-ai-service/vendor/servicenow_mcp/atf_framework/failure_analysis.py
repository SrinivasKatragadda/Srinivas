"""ATF failure explanation and root cause classification.

Module provides intelligent failure analysis for ATF test execution failures.
Using keyword pattern matching, classifies failures into known categories with
probable causes and recommended fixes.

Pattern Matching Algorithm:
- Converts failed step output to lowercase
- Searches for keyword patterns in order of definition
- Returns first matching pattern (greedy match, not exhaustive)
- Falls back to generic categories if no patterns match

Failure Categories (in priority order):
1. Permission/Access: role, acl (blocks feature access)
2. Configuration: mandatory_field, catalog_variable, ui_policy, client_script (changes in system config)
3. Workflow/Integration: business_rule, flow, approval, task (expected side effects missing)
4. API/Transport: rest_status, rest_payload (HTTP/REST issues)
5. Environment: runner_unavailable, timeout (external resource issues)
6. Data/UI: field_missing (form/field configuration)
7. Test Data: invalid_test_data (fallback for data-related failures)
8. Unknown: no pattern matched (generic fallback)

Release Impact Assessment:
- HIGH: Permission, business rule, flow, approval, task, ACL failures
- MEDIUM: Config changes, runner issues, timeout, field changes, data validation
- LOW: Generic/unknown failures

Recommendation Mapping:
- For each category: specific fix action that resolves the root cause
- Examples: "Grant role", "Review ACL", "Update test data", "Refresh catalog"

Dependencies:
- ExplainATFFailuresParams: Request parameters with test/suite execution ID
- ATFServiceNowClient: Query interface for step results
"""

from __future__ import annotations

from typing import Any, Dict

from servicenow_mcp.atf_framework.models import ExplainATFFailuresParams
from servicenow_mcp.atf_framework.service import ATFServiceNowClient

KEYWORDS = [
    # Pattern tuples: (keyword, category, probable_cause, recommended_fix, release_impact)
    # Searched in order - first match wins (greedy matching, not exhaustive)
    
    # Category 1: Permission and Access Control (CRITICAL)
    # These failures prevent feature access - must be fixed before release
    (
        "role",
        "missing_role",
        "The test user likely lacks a required role.",
        "Grant or use a test user with the required role.",
        "high",
    ),
    (
        "acl",
        "acl_denied",
        "An ACL likely blocked the test action.",
        "Review ACLs and test user permissions.",
        "high",
    ),
    
    # Category 2: Form/Field Configuration Changes
    # When form layout or field requirements change, tests become invalid
    (
        "mandatory",
        "mandatory_field_changed",
        "A mandatory field requirement changed.",
        "Update test data or field policy.",
        "medium",
    ),
    (
        "variable",
        "catalog_variable_missing",
        "A catalog variable changed or is missing.",
        "Update catalog item variables or ATF steps.",
        "medium",
    ),
    (
        "ui policy",
        "ui_policy_changed",
        "A UI policy changed form behavior.",
        "Review UI policy action and ATF client steps.",
        "medium",
    ),
    (
        "client script",
        "client_script_changed",
        "A client script changed form behavior.",
        "Review script and client runner support.",
        "medium",
    ),
    
    # Category 3: Business Logic and Workflow (HIGH PRIORITY)
    # When business rules or workflows change, expected side effects disappear
    (
        "business rule",
        "business_rule_changed",
        "Server-side rule changed expected mutation.",
        "Review business rule condition/action.",
        "high",
    ),
    (
        "flow",
        "flow_not_triggered",
        "Flow execution was not observed.",
        "Validate trigger condition and flow activation.",
        "high",
    ),
    (
        "approval",
        "approval_not_created",
        "Expected approval was not created.",
        "Review approval flow and test data.",
        "high",
    ),
    (
        "task",
        "task_not_created",
        "Expected task was not created.",
        "Review fulfillment flow/workflow.",
        "high",
    ),
    
    # Category 4: REST/API Issues
    # When API contracts or HTTP status expectations fail
    (
        "status",
        "rest_status_mismatch",
        "REST status did not match expectation.",
        "Review endpoint, method, and expected status.",
        "medium",
    ),
    (
        "payload",
        "rest_payload_mismatch",
        "REST payload did not match expectation.",
        "Review response assertions.",
        "medium",
    ),
    
    # Category 5: Environment and Runner Issues
    # When test execution environment or runners are unavailable
    (
        "runner",
        "runner_unavailable",
        "Required ATF runner was unavailable.",
        "Start/configure client runner or use standard-compatible tests.",
        "medium",
    ),
    (
        "timeout",
        "timeout",
        "ATF execution timed out.",
        "Check runner availability and long-running steps.",
        "medium",
    ),
    
    # Category 6: Form/UI Visibility
    # When expected fields disappear from forms
    (
        "field",
        "field_missing",
        "Expected field is missing or hidden.",
        "Verify dictionary and form layout.",
        "medium",
    ),
]


def classify_failure(step: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify a failed ATF step into failure category with diagnosis and fix recommendations.
    
    Pattern Matching Algorithm:
    1. Concatenate all step dict values into single string (name, description, error output, etc.)
    2. Convert to lowercase for case-insensitive matching
    3. Iterate through KEYWORDS patterns in order (priority order)
    4. For each pattern: check if keyword appears anywhere in text
    5. First match wins - return matching category with probable cause and fix
    6. If no keywords match:
       - If 'data' in text: classify as invalid_test_data
       - Otherwise: classify as unknown
    
    Greedy Matching Strategy:
    - Uses first keyword match only (no exhaustive search)
    - Keyword order in KEYWORDS list determines priority
    - Examples:
      * "role permission denied" matches "role" (first in list)
      * "approval not created" matches "approval" (before generic failures)
      * "timeoutException" matches "timeout"
    
    Category Fallbacks:
    - invalid_test_data: Matches 'data' keyword, suggests test data refresh
    - unknown: No pattern matched, suggests manual inspection
    
    Args:
        step: Failed step record dict with possible keys:
            - name: Step name/label
            - step: Step identifier
            - description: Step description
            - error: Error message from step output
            - status: Step status (failed/blocked/error)
            - output: Step execution output
            - [any other]: Additional step details
        
    Returns:
        Dict with classification results:
            - failed_step: Step identifier (name or description)
            - category: Failure classification code (e.g., 'missing_role')
            - probable_cause: Human-readable root cause explanation
            - recommended_fix: Actionable remediation step
            - release_impact: Impact severity (low|medium|high)
    
    Example:
        # Permission failure
        classify_failure({
            'name': 'SetFieldValue',
            'description': 'Set priority field',
            'error': 'role permission denied for field priority'
        })
        # Returns: {
        #   'failed_step': 'SetFieldValue',
        #   'category': 'missing_role',
        #   'probable_cause': 'The test user likely lacks a required role.',
        #   'recommended_fix': 'Grant or use a test user with the required role.',
        #   'release_impact': 'high'
        # }
        
        # Data validation failure
        classify_failure({
            'name': 'CreateRecord',
            'error': 'data validation failed: required field missing'
        })
        # Returns: {
        #   'failed_step': 'CreateRecord',
        #   'category': 'invalid_test_data',
        #   'probable_cause': 'The ATF data no longer satisfies the scenario.',
        #   'recommended_fix': 'Refresh test data and rerun.',
        #   'release_impact': 'medium'
        # }
    """
    # Convert step to searchable text format
    # Concatenate all values to catch keyword in any field (name, description, error, output, etc.)
    text = " ".join(str(v) for v in step.values()).lower()
    
    # PHASE 1: Pattern Matching
    # Iterate through KEYWORDS in priority order (first match wins)
    for keyword, category, cause, fix, impact in KEYWORDS:
        if keyword in text:
            # Keyword found: return matched category with details
            return {
                "failed_step": str(
                    step.get("name") or step.get("step") or step.get("description") or step
                ),
                "category": category,
                "probable_cause": cause,
                "recommended_fix": fix,
                "release_impact": impact,
            }
    
    # PHASE 2: Fallback Classification
    # If no keyword patterns matched, check for generic data issues
    if "data" in text:
        return {
            "failed_step": str(step.get("name") or step),
            "category": "invalid_test_data",
            "probable_cause": "The ATF data no longer satisfies the scenario.",
            "recommended_fix": "Refresh test data and rerun.",
            "release_impact": "medium",
        }
    
    # PHASE 3: Generic Fallback
    # No pattern matched and no data keyword found: return generic unknown category
    # This requires manual inspection of the failure
    return {
        "failed_step": str(step.get("name") or step),
        "category": "unknown",
        "probable_cause": "The failed step did not match a known ATF failure pattern.",
        "recommended_fix": "Inspect the ATF step result output and target artifact changes.",
        "release_impact": "medium",
    }


def explain_atf_failures_service(
    client: ATFServiceNowClient, params: ExplainATFFailuresParams
) -> Dict[str, Any]:
    """
    Analyze failed ATF test execution and provide root cause explanations.
    
    Three-Phase Analysis Workflow:
    
    PHASE 1 - Execution Context Validation:
    - Validate that test_execution_id OR suite_execution_id is provided
    - If neither: return failed status with error message
    - If provided: proceed to step results query
    
    PHASE 2 - Failure Detection:
    - Query ATF step results for given execution ID
    - Filter for failed/blocked/error steps (status keyword match)
    - Collect all matching steps for analysis
    - If no failures found: return partial status
    
    PHASE 3 - Classification and Aggregation:
    - For each failed step: invoke classify_failure() for root cause
    - Aggregate results:
      * failure_summary: List of all classified failures with categories
      * root_cause_categories: Unique categories across all failures
      * recommended_actions: Unique recommended fixes
      * release_risk_impact: Highest impact level from all failures
    - Impact ordering: critical > high > medium > low
    
    Risk Assessment Logic:
    - Severity ranking: critical (4) > high (3) > medium (2) > low (1)
    - Overall impact: maximum severity across all classified failures
    - Examples:
      * 1 high + 3 medium failures = release_risk_impact: high
      * 2 critical failures = release_risk_impact: critical
      * 5 low failures = release_risk_impact: low
    
    Args:
        client: ServiceNow ATF client for querying step results
        params: ExplainATFFailuresParams with:
            - test_execution_id: Single test execution to analyze (optional)
            - suite_execution_id: Test suite execution to analyze (optional)
            - Note: At least one ID required
        
    Returns:
        Dict with analysis results:
            - status: Analysis outcome (success|partial|failed)
            - failure_summary: List of classified failures (category, cause, fix, impact)
            - root_cause_categories: Unique failure categories found
            - recommended_actions: Deduplicated remediation steps
            - release_risk_impact: Highest severity level (low|medium|high|critical)
    
    Example Return (success with failures):
        {
            'status': 'success',
            'failure_summary': [
                {
                    'failed_step': 'SetFieldValue',
                    'category': 'missing_role',
                    'probable_cause': 'The test user likely lacks a required role.',
                    'recommended_fix': 'Grant test user required role.',
                    'release_impact': 'high'
                },
                {
                    'failed_step': 'UpdateRecord',
                    'category': 'business_rule_changed',
                    'probable_cause': 'Server-side rule changed expected mutation.',
                    'recommended_fix': 'Review business rule condition/action.',
                    'release_impact': 'high'
                }
            ],
            'root_cause_categories': ['business_rule_changed', 'missing_role'],
            'recommended_actions': [
                'Grant test user required role.',
                'Review business rule condition/action.'
            ],
            'release_risk_impact': 'high'
        }
    
    Example Return (no execution ID):
        {
            'status': 'failed',
            'failure_summary': [],
            'root_cause_categories': [],
            'recommended_actions': ['Provide suite_execution_id or test_execution_id'],
            'release_risk_impact': 'high'
        }
    """
    # PHASE 1: Context Validation
    execution_id = params.test_execution_id or params.suite_execution_id
    if not execution_id:
        # Neither execution ID provided: cannot proceed
        return {
            "status": "failed",
            "failure_summary": [],
            "root_cause_categories": [],
            "recommended_actions": ["Provide suite_execution_id or test_execution_id"],
            "release_risk_impact": "high",
        }
    
    # PHASE 2: Failure Detection
    # Query all step results from execution, filter for failures
    steps = client.query_atf_step_results(execution_id)
    failed_steps = [
        step
        for step in steps
        if any(term in str(step).lower() for term in ("fail", "error", "blocked"))
    ]
    
    # PHASE 3: Classification and Aggregation
    # Classify each failed step using known patterns
    explanations = [classify_failure(step) for step in failed_steps]
    
    # Aggregate categories (deduplicated, sorted)
    categories = sorted({item["category"] for item in explanations})
    
    # Aggregate recommended actions (preserve order, deduplicate)
    actions = list(dict.fromkeys(item["recommended_fix"] for item in explanations))
    
    # Compute release risk impact (maximum severity across all failures)
    # Impact ordering: critical (4) > high (3) > medium (2) > low (1)
    impact_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    impact = max(
        (item["release_impact"] for item in explanations),
        key=lambda v: impact_order.get(v, 0),
        default="low",  # No failures found
    )
    
    return {
        "status": "success" if explanations else "partial",
        "failure_summary": explanations,
        "root_cause_categories": categories,
        "recommended_actions": actions,
        "release_risk_impact": impact,
    }
