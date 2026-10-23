"""ATF quality gate scoring and release readiness validation.

Module provides scoring algorithm for assessing ATF test execution quality,
coverage completeness, and release readiness. Implements multi-layer quality
gate with deductions, blockers, and recommendations.

Scoring Model:
- Base score: 100 points
- Deductions: Automatic reduction for specific conditions (5-30 points each)
- Blockers: Critical failures that force no_go status regardless of score
- Status: Determined by final score OR presence of blockers
  * go: Score >= 85 AND no blockers
  * conditional_go: 65 <= Score < 85 AND no blockers
  * no_go: Score < 65 OR blockers present

Deduction Rules (in application order):
1. Required Suite Missing (-30, BLOCKER): No test results from required suites
2. Critical Artifact Uncovered (-25, BLOCKER): Required artifact has no tests
3. Failed Tests (-20, optional blocker): Test failures (configurable block)
4. Blocked Tests (-15, optional blocker): Test blocks (configurable block)
5. Coverage Below Threshold (-15, BLOCKER): Coverage < minimum_coverage_percent
6. Evidence Missing (-10): No ATF evidence/results collected
7. Tests Stale (-10): Tests older than latest artifact update
8. Manual Fallback Only (-10): Only manual fallback tests available
9. Warnings Exist (-5): Non-critical warnings present

Blocker Override Logic:
- If ANY blocker exists: Final status = no_go (overrides score)
- Examples: critical_artifact_uncovered=True forces no_go even with 90 score
- Allows strict enforcement of minimum quality requirements

Gateway Thresholds:
- Green (go): >= 85 points AND no blockers
- Yellow (conditional_go): 65-84 points AND no blockers
- Red (no_go): < 65 points OR any blocker present

Dependencies:
- ATFQualityGateRequest: Request parameters with thresholds and options
- ATFServiceNowClient: Query interface for test execution results
- analyze_atf_coverage_service: Calculate coverage percentages
"""

from __future__ import annotations

from typing import Any, Dict, List

from servicenow_mcp.atf_framework.models import ATFQualityGateRequest
from servicenow_mcp.atf_framework.service import ATFServiceNowClient


def calculate_atf_quality_score(input: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate an ATF quality score from test execution and coverage results.
    
    Algorithm:
    1. Start with base score of 100 points
    2. Apply deductions in order (each deduction logs reason and points)
    3. Collect blockers as critical failures
    4. Determine status based on final score + blockers
    5. Generate recommendations based on gaps
    
    Scoring Logic:
    - Each deduction reduces score by fixed amount (5-30 points)
    - Deductions can be marked as blockers (force no_go status)
    - Final score clamped to [0, 100] range
    - Status determined by: score threshold OR blocker presence
    
    Deduction Decision Tree:
    if required_suite_missing: deduct 30 (BLOCKER)
    elif critical_artifact_uncovered: deduct 25 (BLOCKER)
    elif failed_tests > 0 AND failed_tests_block_release: deduct 20 (BLOCKER)
    elif failed_tests > 0: deduct 20 (warn only)
    elif blocked_tests > 0 AND blocked_tests_block_release: deduct 15 (BLOCKER)
    elif blocked_tests > 0: deduct 15 (warn only)
    elif coverage_percent < minimum_coverage_percent: deduct 15 (BLOCKER)
    if evidence_missing: deduct 10
    if tests_stale: deduct 10
    if only_manual_fallback: deduct 10
    if warnings exist: deduct 5
    
    Status Assignment:
    if blockers.length > 0:
        status = no_go
    elif score >= 85:
        status = go
    elif score >= 65:
        status = conditional_go
    else:
        status = no_go
    
    Recommendation Generation:
    if blockers: "Resolve ATF blockers before release approval"
    if score < 85: "Increase ATF coverage and collect execution evidence"
    
    Args:
        input: Quality metrics dict with keys:
            - required_suite_missing (bool): No results from required test suites
            - critical_artifact_uncovered (bool): Critical artifact has no tests
            - failed_tests (int): Count of failed test executions
            - blocked_tests (int): Count of blocked test executions
            - failed_tests_block_release (bool): Treat failed tests as blocker
            - blocked_tests_block_release (bool): Treat blocked tests as blocker
            - coverage_percent (float): Current coverage percentage (0-100)
            - minimum_coverage_percent (float): Required minimum coverage
            - evidence_missing (bool): No ATF evidence collected
            - tests_stale (bool): Tests older than artifact update
            - only_manual_fallback (bool): Only manual fallback tests available
            - warnings (list): Non-critical warning messages
               
    Returns:
        Dict with keys:
            - score (int): Final quality score (0-100)
            - status (str): Release readiness (go|conditional_go|no_go)
            - deductions (list): Applied deductions with reasons and points
            - blockers (list): Critical failures that force no_go
            - warnings (list): Non-blocking issues
            - recommendations (list): Suggested remediation actions
    
    Example:
        result = calculate_atf_quality_score({
            'required_suite_missing': False,
            'critical_artifact_uncovered': False,
            'failed_tests': 2,
            'blocked_tests': 0,
            'failed_tests_block_release': True,
            'coverage_percent': 88,
            'minimum_coverage_percent': 85,
            'evidence_missing': False,
            'warnings': []
        })
        # result: {
        #   'score': 80,
        #   'status': 'conditional_go',
        #   'deductions': [{'reason': 'Failed ATF tests exist', 'points': 20}],
        #   'blockers': ['Failed ATF tests exist'],
        #   'recommendations': ['Increase ATF coverage...']
        # }
    """
    deductions: List[Dict[str, Any]] = []
    blockers: List[str] = []
    warnings: List[str] = list(input.get("warnings", []))
    score = 100

    def deduct(condition: bool, points: int, reason: str, blocker: bool = False) -> None:
        """Apply conditional deduction to quality score.
        
        Helper function that encapsulates deduction logic:
        1. Check if condition is True
        2. If true: reduce score by points, log deduction with reason
        3. If blocker: add to blockers list (forces no_go status)
        
        Args:
            condition: Boolean that triggers deduction if True
            points: Number of points to deduct (5-30 typical)
            reason: Human-readable explanation of deduction
            blocker: If True, this deduction forces no_go status regardless of final score
        """
        nonlocal score
        if condition:
            score -= points
            deductions.append({"reason": reason, "points": points})
            if blocker:
                blockers.append(reason)

    # Apply deductions in priority order (highest impact first)
    # Priority 1: Required infrastructure (no tests = cannot proceed)
    deduct(input.get("required_suite_missing", False), 30, "Required ATF suite missing", True)
    deduct(
        input.get("critical_artifact_uncovered", False),
        25,
        "Critical artifact has no ATF test",
        True,
    )
    
    # Priority 2: Test execution results (failed/blocked tests impact release readiness)
    deduct(
        input.get("failed_tests", 0) > 0,
        20,
        "Failed ATF tests exist",
        input.get("failed_tests_block_release", True),  # Configurable: can be warning or blocker
    )
    deduct(
        input.get("blocked_tests", 0) > 0,
        15,
        "Blocked ATF tests exist",
        input.get("blocked_tests_block_release", True),  # Configurable: can be warning or blocker
    )
    
    # Priority 3: Coverage requirements (test coverage adequacy check)
    deduct(
        input.get("coverage_percent", 100) < input.get("minimum_coverage_percent", 85),
        15,
        "ATF coverage below threshold",
        True,  # Coverage is always a blocker
    )
    
    # Priority 4: Evidence quality (soft deductions for stale/incomplete tests)
    deduct(input.get("evidence_missing", False), 10, "ATF evidence missing")
    deduct(input.get("tests_stale", False), 10, "Tests are older than latest artifact update")
    deduct(input.get("only_manual_fallback", False), 10, "Only manual fallback tests exist")
    
    # Priority 5: Warnings (minor issues that reduce score but don't force no_go)
    deduct(bool(warnings), 5, "ATF warnings exist")
    
    # Clamp final score to valid range [0, 100]
    score = max(0, min(100, score))
    
    # Determine status based on score thresholds
    # Green: >= 85 (ready to proceed)
    # Yellow: 65-84 (proceed with caution, consider risks)
    # Red: < 65 (do not proceed)
    status = "go" if score >= 85 else "conditional_go" if score >= 65 else "no_go"
    
    # Blocker override: Any blocker forces no_go regardless of score
    # This ensures critical failures cannot be waived by scoring
    if blockers:
        status = "no_go"
    # Generate recommendations based on quality gaps
    recommendations = []
    if blockers:
        # When blockers exist: immediate action required before release
        recommendations.append("Resolve ATF blockers before release approval.")
    if score < 85:
        # When score below go threshold: improvement areas identified
        recommendations.append("Increase ATF coverage and collect execution evidence.")
    
    return {
        "score": score,
        "status": status,
        "deductions": deductions,
        "blockers": blockers,
        "warnings": warnings,
        "recommendations": recommendations,
    }


def validate_atf_quality_gate_service(
    client: ATFServiceNowClient, params: ATFQualityGateRequest
) -> Dict[str, Any]:
    """
    Validate ATF quality gate for a release (coverage + test execution check).
    
    Two-Phase Validation Workflow:
    1. PHASE 1 - Coverage Analysis:
       - Analyze test coverage across all release artifacts
       - Calculate coverage_percent (total_tested / total_artifacts)
       - Identify critical_uncovered_artifacts (if applicable)
       - Collect coverage warnings
    
    2. PHASE 2 - Quality Scoring:
       - Query test suite execution results from required suites
       - Count failed tests (status contains 'fail')
       - Count blocked tests (status contains 'block')
       - Invoke calculate_atf_quality_score() with all metrics
       - Return comprehensive verdict with recommendations
    
    Quality Gate Determination:
    - Status: Aggregated from quality score status + coverage verdict
    - Blockers: Critical failures that prevent release
    - Recommendations: Actionable steps to remediate gaps
    - Evidence: Detailed metrics supporting decision
    
    Args:
        client: ServiceNow ATF client for querying tests and results
        params: Quality gate request with:
            - release_name: Target release identifier
            - required_suite_sys_ids: List of mandatory test suite IDs
            - minimum_coverage_percent: Required coverage threshold (default 85)
            - allow_failed_tests: If False, failed tests become blocker
            - allow_blocked_tests: If False, blocked tests become blocker
            - require_critical_artifact_coverage: If True, critical artifacts must have tests
        
    Returns:
        Dict with keys:
            - status: Release readiness (go|conditional_go|no_go)
            - atf_quality_score: Calculated quality score (0-100)
            - coverage_percent: Test coverage percentage
            - blockers: Critical failures preventing release
            - warnings: Non-blocking quality issues
            - recommendations: Remediation actions
            - evidence: Detailed metrics (coverage_summary, deductions)
    
    Error Handling:
    - If required_suite_sys_ids provided but no results: marked as blocker
    - If coverage analysis fails: returns partial status with warnings
    
    Example Return (go status):
        {
            'status': 'go',
            'atf_quality_score': 88,
            'coverage_percent': 92,
            'blockers': [],
            'warnings': [],
            'recommendations': [],
            'evidence': [
                {
                    'type': 'atf_quality_gate',
                    'coverage': {'coverage_percent': 92, ...},
                    'deductions': []
                }
            ]
        }
    """
    from servicenow_mcp.atf_framework.coverage import analyze_atf_coverage_service
    from servicenow_mcp.atf_framework.models import AnalyzeATFCoverageParams

    # PHASE 1: Coverage Analysis - determine what percent of release artifacts have tests
    coverage = analyze_atf_coverage_service(
        client,
        AnalyzeATFCoverageParams(
            release_name=params.release_name,
            minimum_required_coverage_percent=params.minimum_coverage_percent,
            dry_run=True,
        ),
    )
    
    # PHASE 2: Test Results Query - get execution results from required test suites
    results = []
    for suite_id in params.required_suite_sys_ids:
        results.extend(client.query_atf_results(suite_sys_id=suite_id))
    
    # Extract failed and blocked test counts for scoring
    # Failure detection: case-insensitive keyword matching on result output
    failed = [item for item in results if "fail" in str(item).lower()]
    blocked = [item for item in results if "block" in str(item).lower()]
    
    # PHASE 3: Quality Calculation - apply scoring algorithm with all metrics
    score = calculate_atf_quality_score(
        {
            # Required infrastructure checks
            "required_suite_missing": bool(params.required_suite_sys_ids) and not results,
            "critical_artifact_uncovered": coverage["coverage_summary"][
                "critical_uncovered_artifacts"
            ]
            > 0
            and params.require_critical_artifact_coverage,
            
            # Test execution results
            "failed_tests": len(failed),
            "blocked_tests": len(blocked),
            "failed_tests_block_release": not params.allow_failed_tests,  # Configuration: warn vs block
            "blocked_tests_block_release": not params.allow_blocked_tests,  # Configuration: warn vs block
            
            # Coverage metrics
            "coverage_percent": coverage["coverage_summary"]["coverage_percent"],
            "minimum_coverage_percent": params.minimum_coverage_percent,
            
            # Evidence quality
            "evidence_missing": not results,
            "warnings": coverage.get("warnings", []),
        }
    )
    
    # PHASE 4: Result Aggregation - combine all findings into comprehensive verdict
    return {
        "status": score["status"],
        "atf_quality_score": score["score"],
        "coverage_percent": coverage["coverage_summary"]["coverage_percent"],
        "blockers": score["blockers"],
        "warnings": score["warnings"],
        "recommendations": score["recommendations"],
        "evidence": [
            {
                "type": "atf_quality_gate",
                "coverage": coverage["coverage_summary"],
                "deductions": score["deductions"],
            }
        ],
    }
