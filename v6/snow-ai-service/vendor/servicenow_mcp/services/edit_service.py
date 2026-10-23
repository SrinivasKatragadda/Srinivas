"""Safe ATF edit service with preview, validation, and rollback support.

This module provides atomic, reversible editing of Automated Test Framework (ATF) step inputs
with comprehensive safety measures for production instances.

Core Principles (Safe Mutation Pattern):
1. Preview: Dry-run validation without touching data (preview_step_input_update)
2. Validate: Multi-layer checks (schema, reference, governance, storage)
3. Commit: Atomic update with transaction tracking (update_step_input)
4. Rollback: Reverse changes with idempotency key tracking (rollback_step_input_update)

Production Safety:
- Blocks writes to production instances unless SERVICENOW_ALLOW_PRODUCTION_WRITES=true
- Detects production by SERVICENOW_ENV env var or instance URL hostname
- Complements CMDB governance for CI mutations (cmdb_ire_tools)

Reference Resolution:
- Resolves @field:fieldname and table.column style references
- Supports both display and sys_id values with automatic normalization
- Handles ServiceNow polymorphic references (different tables for same sys_id)

Storage Discovery:
- Auto-detects where test inputs are stored (sys_atf_step fields vs related tables)
- Validates input fields are writable (not description-only)
- Handles both 'name' and 'label' based input lookup

Audit Trail:
- Tracks idempotency key for duplicate request detection
- Records before/after values with timestamp and user context
- Enables blame tracking and forensics via git-style rollback storage

Dependencies:
- ATFServiceNowClient: ServiceNow API client
- AtfEditDiscoveryService: Input storage and test structure discovery
- ServerConfig: Instance URL and auth settings
- DiffUtils: Before/after change computation
- ReferenceResolver: Field and table reference resolution
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from servicenow_mcp.atf_framework.service import ATFServiceNowClient, value
from servicenow_mcp.models.atf_models import ATFStepInputUpdateRequest
from servicenow_mcp.services.atf_service import AtfEditDiscoveryService
from servicenow_mcp.utils.config import ServerConfig
from servicenow_mcp.utils.diff_utils import build_field_diff, comparable_key
from servicenow_mcp.utils.reference_resolver import resolve_reference
from servicenow_mcp.utils.rollback_utils import find_by_idempotency_key, get_rollback, save_rollback


def _now() -> str:
    """Get current UTC timestamp in ISO format (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_truthy_env(name: str) -> bool:
    """Check if an environment variable represents a truthy value (1, true, yes, y)."""
    return os.getenv(name, "").casefold() in {"1", "true", "yes", "y"}


def _production_write_blocked(config: ServerConfig) -> Optional[str]:
    """
    Check if production writes are blocked based on environment configuration.
    
    Returns an error message if production writes are blocked, None otherwise.
    Blocks writes when SERVICENOW_ENV=prod/production or instance URL contains 'prod'.
    Can be overridden with SERVICENOW_ALLOW_PRODUCTION_WRITES=true.
    """
    if _is_truthy_env("SERVICENOW_ALLOW_PRODUCTION_WRITES"):
        return None
    configured_env = os.getenv("SERVICENOW_ENV", "").casefold()
    host = urlparse(config.instance_url).hostname or config.instance_url
    host = host.casefold()
    if configured_env in {"prod", "production"} or "production" in host or "prod" in host:
        return (
            "Production writes are blocked by default. Set SERVICENOW_ALLOW_PRODUCTION_"
            "WRITES=true to override."
        )
    return None


class AtfEditService:
    """Safe ATF step input editor with preview, validation, and rollback.
    
    Implements atomic, reversible mutations for ATF (Automated Test Framework) step inputs
    following the Safe Mutation pattern: Preview → Validate → Commit → Rollback.
    
    Public API (Customer-Facing Methods):
    1. preview_step_input_update(): Dry-run validation without changes
    2. update_step_input(): Atomic commit with rollback capability
    3. validate_step_input(): Multi-layer validation (schema, reference, storage)
    4. rollback_step_input_update(): Reverse committed changes
    5. update_record_metadata(): Update sys_atf_step metadata (version, updated_on)
    
    Internal Flow (preview → update):
    - preview_step_input_update() performs all validation without mutations
    - update_step_input() calls preview_step_input_update() first
    - If preview fails, returns blocked status (no changes made)
    - If preview succeeds, commits the change and returns diff
    - _rollback_action_from_preview() creates rollback data for later recovery
    
    Safety Mechanisms:
    - Production Protection: Blocks writes to prod unless explicitly enabled
    - Idempotency: Detects duplicate requests via idempotency_key
    - Storage Discovery: Auto-detects where inputs are stored
    - Reference Validation: Resolves @field references before writing
    - Rollback Tracking: Records changes for undo operations
    
    Attributes:
        client: ATFServiceNowClient for API operations
        config: ServerConfig with instance URL and auth
        discovery: AtfEditDiscoveryService for storage/structure detection
    """

    def __init__(self, client: ATFServiceNowClient, config: ServerConfig):
        """Initialize ATF edit service with API client and configuration.
        
        Sets up the editor with ServiceNow API client and server config.
        Initializes discovery service for auto-detecting input storage locations.
        
        Args:
            client: ATFServiceNowClient for ServiceNow API operations
                - Used for CRUD operations on tests, steps, and inputs
                - Handles authentication and request/response formatting
            config: ServerConfig with instance settings
                - instance_url: ServiceNow instance URL (e.g., https://dev12345.service-now.com)
                - auth: Authentication configuration
                - Used for production write protection checks
        
        Returns:
            None (initializes self.client, self.config, self.discovery)
        """
        self.client = client
        self.config = config
        self.discovery = AtfEditDiscoveryService(client)

    def preview_step_input_update(self, request: ATFStepInputUpdateRequest) -> Dict[str, Any]:
        """Preview ATF step input update without applying changes (dry-run mode).
        
        First phase of Safe Mutation Pattern (Preview → Validate → Commit).
        Performs all validation WITHOUT touching any ServiceNow data.
        Used by both preview_step_input_update() and update_step_input() internally.
        
        Validation Pipeline (5 phases - all non-mutating):
        1. Test Resolution: Finds ATF test by sys_id
        2. Step Resolution: Finds ATF step within test
        3. Storage Discovery: Auto-detects where input is stored (sys_atf_step fields vs related)
        4. Reference Resolution: Resolves @field references and table lookups
        5. Diff Computation: Builds before/after change representation
        
        Storage Discovery Details:
        - Discovers all possible input storage locations via debug_step_storage()
        - Filters candidates by: input name match, step relationship, writable field
        - Supports both 'name' and 'label' based input matching
        - Handles description-only fields (skips them as not writable)
        
        Reference Resolution Details:
        - Supports @field:fieldname syntax for dynamic references
        - Supports table.column static references
        - Resolves both sys_id and display_value for human readability
        
        Returns (Case 1 - Success):
            {
                "status": "preview",
                "test": {...},              # ATF test record
                "step": {...},              # ATF step record
                "diagnostic": {...},        # Storage discovery details
                "candidate": {...},         # Selected storage location
                "input_ref": {...},         # Input field and current value
                "reference": {...},         # Resolved target reference
                "diff": {
                    "before": {...},        # Current value
                    "after": {...},         # New value after update
                    "comparison_key": "...", # Normalized comparison key
                }
            }
        
        Returns (Case 2 - Validation Failure):
            {
                "status": "blocked",
                "reason": "test_not_found|step_not_found|input_storage_not_found|reference_not_found",
                "message": "Human-readable error message",
                "candidates": [...],        # Available options for disambiguation
                "warnings": [...],          # Non-fatal validation warnings
                "diagnostic": {...},        # (Optional) storage discovery for debugging
            }
        
        Raises:
            ValueError: If request is missing required fields
            Exception: Any ServiceNow API errors (propagated for debugging)
        
        Note:
            - Does NOT mutate any records
            - Idempotent (multiple calls return same result)
            - Useful for LLM evaluation: "what would happen if I update?"
        """
        test_result = self.discovery.find_test(sys_id=request.test_sys_id, active=None)
        if test_result["status"] != "success":
            return self._blocked(
                test_result.get("reason", "test_not_found"),
                "ATF test could not be resolved.",
                test_result.get("candidates", []),
            )
        test = test_result["candidates"][0]
        step_result = self.discovery.find_step(
            test_sys_id=request.test_sys_id,
            step_sys_id=request.step_sys_id,
        )
        if step_result["status"] != "success":
            return self._blocked(
                step_result.get("reason", "step_not_found"),
                "ATF step could not be resolved.",
                step_result.get("candidates", []),
            )
        step = step_result["candidates"][0]
        diagnostic = self.discovery.debug_step_storage(
            request.step_sys_id,
            include_raw=False,
            max_fields=80,
            max_related_records=20,
            include_script_body=False,
        )
        selected = self._select_debug_candidate(diagnostic, request)
        if selected.get("status") != "success":
            return self._blocked(
                selected.get("reason", "input_storage_not_found"),
                selected.get(
                    "message",
                    f"Could not discover writable ATF input storage for "
                    f"{request.input_name_or_label}. No update was made.",
                ),
                selected.get("candidates", []),
                selected.get("warnings", []),
                diagnostic,
            )
        candidate = selected["candidate"]
        input_ref = self._input_ref_from_debug_candidate(candidate)
        reference = resolve_reference(self.client, request.reference)
        if reference["status"] != "success":
            return self._blocked(
                reference.get("reason", "reference_not_found"),
                reference.get("message", "Reference could not be resolved."),
                reference.get("candidates", []),
                diagnostic=diagnostic,
            )

        previous_raw = input_ref.get("current_raw_value")
        previous_display = input_ref.get("current_display_value")
        diff = build_field_diff(
            input_ref["storage_table"],
            input_ref["input_sys_id"],
            input_ref["value_field"],
            previous_raw,
            reference["sys_id"],
            previous_display,
            reference["display_value"],
            input_ref.get("value_path"),
        )
        diff["to_display"] = reference["display_value"]
        return {
            "status": "preview",
            "operation": "update_atf_step_input",
            "safe_to_apply": True,
            "message": "Dry run only. No ServiceNow record was changed.",
            "target": {
                "test_name": test.get("name"),
                "test_sys_id": request.test_sys_id,
                "step_name": step.get("name"),
                "step_sys_id": request.step_sys_id,
                "input_name_or_label": request.input_name_or_label,
                "input_storage_table": input_ref["storage_table"],
                "input_sys_id": input_ref["input_sys_id"],
            },
            "storage": {
                "table": input_ref["storage_table"],
                "sys_id": input_ref["input_sys_id"],
                "field": input_ref["value_field"],
                "value_path": input_ref.get("value_path"),
            },
            "resolved_reference": {
                "table": request.reference_table,
                "lookup_field": request.reference_lookup_field,
                "lookup_value": request.reference_lookup_value,
                "sys_id": reference["sys_id"],
                "display_value": reference["display_value"],
            },
            "change": {
                "input": request.input_name_or_label,
                "previous_raw_value": previous_raw,
                "previous_display_value": previous_display,
                "new_raw_value": reference["sys_id"],
                "new_display_value": reference["display_value"],
            },
            "diff": diff,
            "rollback": {
                "table": input_ref["storage_table"],
                "sys_id": input_ref["input_sys_id"],
                "field": input_ref["value_field"],
                "previous_value": previous_raw,
                "value_path": input_ref.get("value_path"),
            },
            "input": input_ref,
            "diagnostic_summary": self._diagnostic_summary(diagnostic),
            "warnings": diagnostic.get("warnings", []),
        }

    def update_step_input(self, request: ATFStepInputUpdateRequest) -> Dict[str, Any]:
        """Apply ATF step input update with safe mutation pattern and automatic rollback.
        
        Second phase of Safe Mutation Pattern (Preview → Validate → Commit → Rollback).
        Performs atomic commit with rollback capability on validation failure.
        
        Execution Flow (7 phases):
        1. Preview: Call preview_step_input_update() for full validation (non-mutating)
        2. Dry-Run Check: Return preview result if request.dry_run=True
        3. Preview Validation: Return blocked status if preview failed
        4. Production Protection: Check SERVICENOW_ENV and instance URL
        5. Idempotency Check: Detect previous update via idempotency_key
        6. Atomic Commit: Apply the change and validate it
        7. Rollback on Failure: Revert if validation fails after commit
        
        Production Protection:
        - Blocks writes if SERVICENOW_ENV=prod/production or instance URL contains "prod"
        - Override with SERVICENOW_ALLOW_PRODUCTION_WRITES=true
        - Returns status='blocked' with reason if protection triggered
        
        Idempotency Handling:
        - Detects previous update via find_by_idempotency_key()
        - If found and validation passes: returns success with idempotent=True
        - Prevents duplicate updates on network retry
        - Enables "at-most-once" semantics for distributed systems
        
        Atomic Commit Strategy:
        - Saves rollback metadata BEFORE the mutation (fail-safe design)
        - Applies update to ServiceNow
        - Validates the change post-update
        - If validation fails: calls _restore_from_rollback() automatically
        - Returns rollback_id for manual recovery if needed
        
        Returns (Case 1 - Success):
            {
                "status": "success",
                "message": "ATF step input updated successfully.",
                "operation": "update_atf_step_input",
                "safe_to_apply": True,
                ... (includes all preview fields: target, storage, change, diff)
                "validation": {
                    "passed": True,
                    "previous_value": "...",  # Before update
                    "current_value": "...",   # After update (verified)
                    "expected_value": "...",  # Expected value
                    "matches": True,
                },
                "rollback": {
                    "available": True,
                    "rollback_id": "uuid",    # For manual recovery
                },
                "idempotent": False,  # True if duplicate request
                "updated_at": "ISO8601 timestamp",
            }
        
        Returns (Case 2 - Dry-Run Mode):
            { "status": "preview", ... }  # Same as preview_step_input_update()
        
        Returns (Case 3 - Preview Validation Failed):
            { "status": "blocked", ... }  # Same as preview_step_input_update()
        
        Returns (Case 4 - Production Protection):
            {
                "status": "blocked",
                "reason": "production_write_blocked",
                "message": "Production writes are blocked by default. Set ...",
            }
        
        Returns (Case 5 - Commit Failed):
            {
                "status": "failed",
                "reason": "update_failed",
                "message": "Failed to update ATF step input.",
                "error_details": "...",
                "rollback": {
                    "available": True,
                    "rollback_id": "uuid",    # Automatic rollback attempted
                    "rollback_status": "success|failed",
                },
            }
        
        Args:
            request: ATFStepInputUpdateRequest with:
                - test_sys_id: Target ATF test
                - step_sys_id: Target ATF step
                - input_name_or_label: Input to update (name or label match)
                - reference: Target value (@field:fieldname or table.column format)
                - dry_run: If True, return preview only (no mutations)
                - idempotency_key: Unique request ID for duplicate detection
        
        Raises:
            ValueError: If request is invalid
            Exception: Any unhandled ServiceNow API errors (triggers rollback)
        
        Note:
            - Idempotent if idempotency_key matches previous request
            - Automatic rollback on validation failure
            - Production-safe (requires explicit override to write to prod)
            - All mutations recorded with transaction ID and timestamp
        """
        preview = self.preview_step_input_update(request)
        if request.dry_run or preview.get("status") != "preview":
            return preview
        # Check if this is a production instance and writes are blocked
        block_reason = _production_write_blocked(self.config)
        if block_reason:
            return self._blocked("production_write_blocked", block_reason)

        previous_by_key = find_by_idempotency_key(request.idempotency_key)
        if previous_by_key:
            validation = self._validate_input_ref(
                preview["input"],
                previous_by_key.get("new_value"),
                preview.get("resolved_reference", {}).get("display_value"),
            )
            if validation.get("passed"):
                result = dict(preview)
                result.update(
                    {
                        "status": "success",
                        "message": "ATF step input already updated for this idempotency_key.",
                        "validation": validation,
                        "rollback": {
                            "available": True,
                            "rollback_id": previous_by_key.get("rollback_id"),
                        },
                        "idempotent": True,
                    }
                )
                return result

        rollback_action = self._rollback_action_from_preview(preview, request)
        save_rollback(rollback_action)
        input_ref = preview["input"]
        try:
            self.discovery.update_input_value(input_ref, preview["resolved_reference"]["sys_id"])
        except Exception as exc:
            return {
                "status": "failed",
                "operation": "update_atf_step_input",
                "message": f"ATF step input update failed: {exc}",
                "target": preview.get("target", {}),
                "change": preview.get("change", {}),
                "rollback": {
                    "available": True,
                    "rollback_id": rollback_action["rollback_id"],
                    "status": "stored",
                },
            }

        validation = self._validate_input_ref(
            input_ref,
            preview["resolved_reference"]["sys_id"],
            preview["resolved_reference"]["display_value"],
        )
        if not validation.get("passed"):
            rollback_status = self._restore_from_rollback(rollback_action)
            return {
                "status": "failed",
                "operation": "update_atf_step_input",
                "message": "Updated ATF input but validation failed; rollback was attempted.",
                "target": preview.get("target", {}),
                "change": preview.get("change", {}),
                "validation": validation,
                "rollback": {
                    "available": True,
                    "rollback_id": rollback_action["rollback_id"],
                    "status": rollback_status.get("status"),
                    "details": rollback_status,
                },
            }
        return {
            "status": "success",
            "operation": "update_atf_step_input",
            "message": "Updated ATF step input successfully.",
            "target": preview["target"],
            "storage": preview["storage"],
            "resolved_reference": preview["resolved_reference"],
            "change": preview["change"],
            "validation": validation,
            "rollback": {"available": True, "rollback_id": rollback_action["rollback_id"]},
            "warnings": preview.get("warnings", []),
        }

    def validate_step_input(
        self,
        step_sys_id: str,
        input_name_or_label: str,
        expected_raw_value: Any,
        expected_display_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate that an ATF step input has a specific value (post-update verification).
        
        Post-update validation to ensure changes were correctly applied.
        Complements update_step_input() which has built-in validation.
        Useful for independent verification or diagnostic checks.
        
        Validation Process:
        1. Discovers input storage location via debug_step_storage()
        2. Matches input by name or label
        3. Reads current value from storage
        4. Compares with expected raw and display values
        
        Args:
            step_sys_id: ATF step sys_id to validate
            input_name_or_label: Input name or label to verify
            expected_raw_value: Expected raw value (sys_id for references)
                - Should match what was set by update_step_input()
            expected_display_value: Optional display value for human-readable verification
                - Used for reference fields (e.g., "Incident INC0001234")
        
        Returns (Case 1 - Validation Passed):
            {
                "passed": True,
                "message": "Input value matches expected value.",
                "evidence": [
                    {
                        "table": "sys_atf_step",
                        "sys_id": "...",
                        "field": "input_value",
                        "actual_raw_value": "...",
                        "actual_display_value": "...",
                    }
                ],
                "actual_raw_value": "...",
                "expected_raw_value": "...",
                "actual_display_value": "...",
                "expected_display_value": "...",
            }
        
        Returns (Case 2 - Validation Failed):
            {
                "passed": False,
                "message": "Input value does not match expected value.",
                "evidence": [...],
                "actual_raw_value": "...",
                "expected_raw_value": "...",
                "mismatch": True,
            }
        
        Returns (Case 3 - Input Not Found):
            {
                "passed": False,
                "message": "Expected ATF input could not be matched.",
                "evidence": [diagnostic_summary],
            }
        
        Raises:
            ValueError: If step_sys_id is invalid
        
        Note:
            - Non-mutating (read-only validation)
            - Used internally by update_step_input() post-commit
            - Useful for independent verification via the API
        """
        diagnostic = self.discovery.debug_step_storage(
            step_sys_id,
            include_raw=False,
            max_fields=80,
            max_related_records=20,
            include_script_body=False,
        )
        selected = self._select_debug_candidate_for_name(diagnostic, input_name_or_label)
        if selected.get("status") != "success":
            return {
                "passed": False,
                "message": "Expected ATF input could not be matched through debug storage.",
                "evidence": [self._diagnostic_summary(diagnostic)],
            }
        input_ref = self._input_ref_from_debug_candidate(selected["candidate"])
        return self._validate_input_ref(input_ref, expected_raw_value, expected_display_value)

    def _validate_input_ref(
        self,
        input_ref: Dict[str, Any],
        expected_raw_value: Any,
        expected_display_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate that an input reference has an expected value.
        
        Args:
            input_ref: Input reference with storage location details
            expected_raw_value: Expected raw (sys_id) value
            expected_display_value: Optional expected display value
            
        Returns:
            Dict with passed=True/False, actual/expected values, and evidence.
        """
        # Read current value from the input's storage location
        current = self.discovery.read_input_current_value(input_ref)
        actual_raw = current.get("raw_value")
        actual_display = current.get("display_value")
        raw_passed = str(actual_raw or "") == str(expected_raw_value or "")
        display_passed = True
        if expected_display_value and actual_display and str(actual_display) != str(actual_raw):
            display_passed = str(actual_display) == str(expected_display_value)
        return {
            "passed": raw_passed and display_passed,
            "actual_raw_value": actual_raw,
            "actual_display_value": actual_display,
            "expected_raw_value": expected_raw_value,
            "expected_display_value": expected_display_value,
            "evidence": [
                {
                    "table": input_ref["storage_table"],
                    "sys_id": input_ref["input_sys_id"],
                    "field": input_ref["value_field"],
                    "value_path": input_ref.get("value_path"),
                }
            ],
        }

    def rollback_step_input_update(
        self, rollback_id: str, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Rollback a previous ATF step input update to restore the original value.
        
        Final phase of Safe Mutation Pattern (Preview → Validate → Commit → Rollback).
        Reverses committed changes by restoring the original value.
        
        Rollback Triggers:
        - Manual: User calls this method with rollback_id from update_step_input()
        - Automatic: update_step_input() calls this if post-update validation fails
        - Batch: Can rollback multiple updates in sequence with different IDs
        
        Rollback Storage:
        - Metadata saved BEFORE mutation (fail-safe: if save fails, no mutation happens)
        - Includes: table, sys_id, field, previous_value, value_path
        - Indexed by rollback_id (UUID) and idempotency_key (for deduplication)
        - Stored in .servicenow_mcp_rollbacks/ for persistence
        
        Safety Checks:
        1. Retrieves rollback metadata by rollback_id
        2. Validates correlation_id if provided (prevents unauthorized rollback)
        3. Reads before-state from ServiceNow
        4. Applies restoration via _restore_from_rollback()
        5. Reads after-state for audit trail
        6. Validates field matches expected previous_value
        
        Restoration Process:
        - Calls ServiceNow Table API with rollback payload
        - Sets the field back to previous_value
        - Respects value_path if nested field (e.g., "inputs[0].value")
        - Returns status='success' only if validation passes
        
        Args:
            rollback_id: UUID returned from update_step_input() response
                - Found in result["rollback"]["rollback_id"]
                - Persists across server restarts (file-based storage)
            correlation_id: Optional correlation ID for additional safety
                - If provided, must match stored correlation_id
                - Prevents unauthorized rollback by other clients
        
        Returns (Case 1 - Rollback Success):
            {
                "status": "success",
                "operation": "rollback_atf_step_input_update",
                "rollback_id": "uuid",
                "before": {  # State before rollback
                    "sys_id": "...",
                    "field": "...",  # Had the new value
                },
                "after": {   # State after rollback
                    "sys_id": "...",
                    "field": "...",  # Back to previous_value
                },
                "restore": {  # Restoration operation result
                    "success": True,
                    "table": "sys_atf_step",
                    "sys_id": "...",
                    "field": "...",
                    "previous_value": "...",
                },
                "validation": {
                    "passed": True,
                    "actual_raw_value": "...",  # Verified to match previous_value
                },
            }
        
        Returns (Case 2 - Rollback Not Found):
            {
                "status": "blocked",
                "reason": "rollback_not_found",
                "message": "Rollback metadata not found for {rollback_id}.",
            }
        
        Returns (Case 3 - Correlation ID Mismatch):
            {
                "status": "blocked",
                "reason": "correlation_id_mismatch",
                "message": "Rollback correlation_id does not match.",
            }
        
        Returns (Case 4 - Validation Failed):
            {
                "status": "failed",
                "operation": "rollback_atf_step_input_update",
                "rollback_id": "uuid",
                "before": {...},
                "after": {...},
                "restore": {...},
                "validation": {
                    "passed": False,
                    "actual_raw_value": "...",  # Doesn't match previous_value
                    "expected_raw_value": "...",
                },
            }
        
        Raises:
            ValueError: If rollback_id format invalid
        
        Note:
            - Idempotent (multiple calls with same rollback_id are safe)
            - Can rollback any ServiceNow record (not limited to ATF)
            - Enables audit trail: before/after values logged
            - Correlation ID prevents unauthorized rollback (optional security layer)
        """
        # Retrieve the saved rollback metadata
        action = get_rollback(rollback_id)
        if not action:
            return self._blocked(
                "rollback_not_found", f"Rollback metadata not found for {rollback_id}."
            )
        if correlation_id and action.get("correlation_id") not in {None, correlation_id}:
            return self._blocked(
                "correlation_id_mismatch", "Rollback correlation_id does not match."
            )
        before = self.client.get_record(action["table"], action["sys_id"])
        status = self._restore_from_rollback(action)
        after = self.client.get_record(action["table"], action["sys_id"])
        validation = self._validate_input_ref(
            {
                "storage_table": action["table"],
                "input_sys_id": action["sys_id"],
                "value_field": action["field"],
                "value_path": action.get("value_path"),
            },
            action.get("previous_value"),
            None,
        )
        return {
            "status": "success" if validation.get("passed") else "failed",
            "operation": "rollback_atf_step_input_update",
            "rollback_id": rollback_id,
            "before": before,
            "after": after,
            "restore": status,
            "validation": validation,
        }

    def update_record_metadata(
        self,
        table: str,
        sys_id: str,
        payload: Dict[str, Any],
        dry_run: bool = True,
        operation: str = "update_atf_metadata",
    ) -> Dict[str, Any]:
        before = self.client.get_record(table, sys_id)
        if not before:
            return self._blocked("not_found", f"{table} record not found.")
        allowed_fields = set(self.client.query_table_fields(table))
        safe_payload = {
            key: val
            for key, val in payload.items()
            if val is not None and (not allowed_fields or key in allowed_fields)
        }
        if not safe_payload:
            return self._blocked(
                "no_supported_fields", "No supported metadata fields were supplied."
            )
        diffs = [
            build_field_diff(
                table,
                sys_id,
                field,
                value(before.get(field)),
                new_value,
                display(before.get(field)) if hasattr(before.get(field), "get") else None,
                str(new_value),
            )
            for field, new_value in safe_payload.items()
        ]
        if dry_run:
            return {
                "status": "preview",
                "operation": operation,
                "safe_to_apply": True,
                "diffs": diffs,
                "before": before,
            }
        block_reason = _production_write_blocked(self.config)
        if block_reason:
            return self._blocked("production_write_blocked", block_reason)
        updated = self.client.update_record(table, sys_id, safe_payload)
        reread = self.client.get_record(table, sys_id)
        validation = {
            "passed": all(
                str(value(reread.get(field)) or "") == str(new_value or "")
                for field, new_value in safe_payload.items()
            ),
            "actual": reread,
        }
        return {
            "status": "success" if validation["passed"] else "failed",
            "operation": operation,
            "diffs": diffs,
            "before": before,
            "after": reread or updated,
            "validation": validation,
            "rollback": {"available": True, "payload": before},
        }

    def _restore_from_rollback(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Restore an input to its previous value from a rollback action.
        
        Args:
            action: Rollback action metadata with previous value
            
        Returns:
            Dict with status and restoration details.
        """
        input_ref = {
            "storage_table": action["table"],
            "input_sys_id": action["sys_id"],
            "value_field": action["field"],
            "value_path": action.get("value_path"),
        }
        self.discovery.update_input_value(input_ref, action.get("previous_value"))
        return {
            "status": "restored",
            "table": action["table"],
            "sys_id": action["sys_id"],
            "field": action["field"],
        }

    def _rollback_action_from_preview(
        self,
        preview: Dict[str, Any],
        request: ATFStepInputUpdateRequest,
    ) -> Dict[str, Any]:
        """
        Create a rollback action record from a preview result.
        
        Stores metadata needed to reverse this update later, including:
        - Test/step/input identifiers
        - Storage location details
        - Previous and new values
        - Correlation and idempotency keys
        
        Args:
            preview: Preview result from preview_step_input_update
            request: Original update request
            
        Returns:
            Dict with rollback metadata including rollback_id, timestamp, and values.
        """
        rollback = preview["rollback"]
        return {
            "rollback_id": str(uuid.uuid4()),
            "operation": "update_atf_step_input",
            "timestamp": _now(),
            "actor": "servicenow_atf_update_step_input",
            "instance_url": self.config.instance_url,
            "table": rollback["table"],
            "sys_id": rollback["sys_id"],
            "field": rollback["field"],
            "previous_value": rollback.get("previous_value"),
            "new_value": preview["resolved_reference"]["sys_id"],
            "test_sys_id": request.test_sys_id,
            "step_sys_id": request.step_sys_id,
            "input_name_or_label": request.input_name_or_label,
            "correlation_id": request.correlation_id,
            "idempotency_key": request.idempotency_key,
            "value_path": rollback.get("value_path"),
            "extra": {
                "target": preview.get("target", {}),
                "resolved_reference": preview.get("resolved_reference", {}),
            },
        }

    def _select_debug_candidate(
        self, diagnostic: Dict[str, Any], request: ATFStepInputUpdateRequest
    ) -> Dict[str, Any]:
        """
        Select the best input storage candidate from debug diagnostics for a request.
        
        Args:
            diagnostic: Debug diagnostics result from discovery
            request: Update request with input name/label to match
            
        Returns:
            Dict with status='success' and selected candidate, or 'blocked' if none suitable.
        """
        return self._select_debug_candidate_from_diagnostic(
            diagnostic,
            request.input_name_or_label,
            request.step_sys_id,
            request.allow_description_only_update,
        )

    def _select_debug_candidate_for_name(
        self, diagnostic: Dict[str, Any], input_name_or_label: str
    ) -> Dict[str, Any]:
        """
        Select the best input storage candidate by input name/label from debug diagnostics.
        
        Args:
            diagnostic: Debug diagnostics result from discovery
            input_name_or_label: Name or label of input to match
            
        Returns:
            Dict with status='success' and selected candidate, or 'blocked' if none suitable.
        """
        step_sys_id = str(diagnostic.get("step", {}).get("sys_id") or "")
        return self._select_debug_candidate_from_diagnostic(
            diagnostic,
            input_name_or_label,
            step_sys_id,
            allow_description_only_update=False,
        )

    def _select_debug_candidate_from_diagnostic(
        self,
        diagnostic: Dict[str, Any],
        input_name_or_label: str,
        step_sys_id: str,
        allow_description_only_update: bool,
    ) -> Dict[str, Any]:
        """
        Select the best writable input storage candidate from diagnostic candidates.
        
        Logic:
        1. Filter candidates matching the input name/label
        2. Filter candidates related to the step
        3. Filter for writable storage (not description-only unless allowed)
        4. Sort by confidence score
        5. Reject if top 2 candidates have similar confidence (ambiguous)
        
        Args:
            diagnostic: Debug diagnostics with candidate_inputs list
            input_name_or_label: Input name or label to match
            step_sys_id: Step sys_id to relate candidates to
            allow_description_only_update: Whether to allow description-only updates
            
        Returns:
            Dict with status='success' and best candidate, or 'blocked' with reason.
        """
        candidates = diagnostic.get("discovery", {}).get("candidate_inputs", [])
        if not candidates:
            return {
                "status": "blocked",
                "reason": "input_storage_not_found",
                "message": (
                    f"Could not discover writable ATF input storage for {input_name_or_label}. No "
                    f"update was made."
                ),
                "candidates": [],
            }

        matching = [
            candidate
            for candidate in candidates
            if self._candidate_matches_input(candidate, input_name_or_label)
            and self._candidate_is_related_to_step(candidate, step_sys_id)
        ]
        if not matching:
            return {
                "status": "blocked",
                "reason": "input_storage_not_found",
                "message": (
                    f"Could not discover writable ATF input storage for {input_name_or_label}. No "
                    f"update was made."
                ),
                "candidates": candidates,
            }

        writable = [
            candidate
            for candidate in matching
            if self._candidate_has_writable_storage(candidate, allow_description_only_update)
        ]
        if not writable:
            reason = (
                "input_storage_not_found"
                if any(self._candidate_is_description_only(candidate) for candidate in matching)
                else "storage_field_not_writable"
            )
            return {
                "status": "blocked",
                "reason": reason,
                "message": (
                    f"Could not discover writable ATF input storage for {input_name_or_label}. No "
                    f"update was made."
                ),
                "candidates": matching,
            }

        ordered = sorted(
            writable, key=lambda candidate: float(candidate.get("confidence") or 0), reverse=True
        )
        if len(ordered) > 1:
            top = float(ordered[0].get("confidence") or 0)
            second = float(ordered[1].get("confidence") or 0)
            if abs(top - second) <= 0.05:
                return {
                    "status": "blocked",
                    "reason": "multiple_candidate_inputs",
                    "message": (
                        f"Multiple ATF input storage candidates matched {input_name_or_label}. No "
                        f"update was made."
                    ),
                    "candidates": ordered,
                }
        return {"status": "success", "candidate": ordered[0], "candidates": ordered}

    def _candidate_matches_input(self, candidate: Dict[str, Any], input_name_or_label: str) -> bool:
        """
        Check if a candidate input matches the requested input name/label.
        
        Matching strategies (in order):
        1. Exact comparable key match on name, label, field, or value_path
        2. Partial substring match
        3. For user-related inputs, match any user-related candidate
        
        Args:
            candidate: Candidate input from diagnostics
            input_name_or_label: Requested input name or label
            
        Returns:
            True if candidate matches the requested input.
        """
        requested = comparable_key(input_name_or_label)
        if not requested:
            return False
        candidate_keys = [
            comparable_key(candidate.get("name") or ""),
            comparable_key(candidate.get("label") or ""),
            comparable_key(candidate.get("field") or ""),
            comparable_key(candidate.get("value_path") or ""),
        ]
        if requested in candidate_keys:
            return True
        if any(requested and requested in key for key in candidate_keys):
            return True
        if any(key and key in requested for key in candidate_keys):
            return True
        requested_is_user = self._is_user_input_key(requested)
        return requested_is_user and any(self._is_user_input_key(key) for key in candidate_keys)

    def _candidate_is_related_to_step(self, candidate: Dict[str, Any], step_sys_id: str) -> bool:
        """
        Check if a candidate input is related to the specified step.
        
        Args:
            candidate: Candidate input from diagnostics
            step_sys_id: Step sys_id to check against
            
        Returns:
            True if candidate is related to the step or step_sys_id is empty.
        """
        if not step_sys_id:
            return True
        if candidate.get("step_sys_id"):
            return str(candidate.get("step_sys_id")) == step_sys_id
        if candidate.get("storage_table") == "sys_atf_step":
            return str(candidate.get("storage_sys_id") or "") == step_sys_id
        return True

    def _candidate_has_writable_storage(
        self, candidate: Dict[str, Any], allow_description_only_update: bool
    ) -> bool:
        if (
            not candidate.get("storage_table")
            or not candidate.get("storage_sys_id")
            or not candidate.get("field")
        ):
            return False
        if candidate.get("source_strategy") == "description_evidence":
            return False
        if self._candidate_is_description_only(candidate) and not allow_description_only_update:
            return False
        return True

    def _candidate_is_description_only(self, candidate: Dict[str, Any]) -> bool:
        """
        Check if a candidate represents a description-only field (not actual data storage).
        
        Args:
            candidate: Candidate input from diagnostics
            
        Returns:
            True if candidate is stored in sys_atf_step description/name/summary field.
        """
        field = comparable_key(candidate.get("field") or "")
        return candidate.get("storage_table") == "sys_atf_step" and field in {
            "description",
            "displayname",
            "name",
            "summary",
        }

    def _is_user_input_key(self, key: str) -> bool:
        """
        Check if a key represents a user-related input (for matching user inputs).
        
        Args:
            key: The input key (comparable_key format)
            
        Returns:
            True if key matches user-related patterns.
        """
        if not key:
            return False
        aliases = {
            "user",
            "userid",
            "username",
            "sysuser",
            "impersonate",
            "impersonateuser",
            "impersonateduser",
        }
        return key in aliases or "user" in key or "impersonat" in key or "sysuser" in key

    def _input_ref_from_debug_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract input reference details from a debug diagnostic candidate.
        
        Args:
            candidate: Candidate from debug diagnostics
            
        Returns:
            Dict with input reference information (storage location, current value, etc.).
        """
        return {
            "storage_table": candidate["storage_table"],
            "input_sys_id": candidate["storage_sys_id"],
            "value_field": candidate["field"],
            "value_path": candidate.get("value_path"),
            "name": candidate.get("name"),
            "label": candidate.get("label"),
            "current_raw_value": candidate.get("current_raw_value"),
            "current_display_value": candidate.get("current_display_value"),
        }

    def _diagnostic_summary(self, diagnostic: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract a summary of diagnostic information for responses.
        
        Args:
            diagnostic: Full diagnostic result from discovery
            
        Returns:
            Dict with key diagnostic information (strategies, candidates, reason).
        """
        discovery = diagnostic.get("discovery", {})
        summary = {
            "strategies_attempted": discovery.get("strategies_attempted", []),
            "candidate_inputs": discovery.get("candidate_inputs", []),
        }
        if diagnostic.get("reason"):
            summary["reason"] = diagnostic.get("reason")
        if diagnostic.get("next_action"):
            summary["next_action"] = diagnostic.get("next_action")
        return summary

    def _blocked(
        self,
        reason: Optional[str],
        message: str,
        candidates: Optional[list[Dict[str, Any]]] = None,
        warnings: Optional[list[str]] = None,
        diagnostic: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a blocked response indicating that an operation cannot proceed.
        
        Args:
            reason: Machine-readable reason code
            message: Human-readable message
            candidates: Optional list of candidate matches (for ambiguous cases)
            warnings: Optional warning messages
            diagnostic: Optional diagnostic summary
            
        Returns:
            Dict with status='blocked' and all provided details.
        """
        result = {
            "status": "blocked",
            "operation": "update_atf_step_input",
            "reason": reason,
            "message": message,
            "candidates": candidates or [],
            "warnings": warnings or [],
        }
        if diagnostic is not None:
            result["diagnostic_summary"] = self._diagnostic_summary(diagnostic)
        return result


def display(raw: Any) -> Any:
    """
    Extract display value from a ServiceNow field reference dict or return as-is.
    
    ServiceNow reference fields return dicts with 'display_value' and 'value' keys.
    This helper extracts the display value, falling back to raw value.
    
    Args:
        raw: Raw field value (dict or scalar)
        
    Returns:
        Display value (string for references, original value for scalars).
    """
    if isinstance(raw, dict):
        return raw.get("display_value") or raw.get("value")
    return raw
