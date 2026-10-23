"""Generic edit models for safe mutation tools (ATF, CMDB, etc.).

This module provides Pydantic data models for representing ServiceNow edits,
validations, and rollback operations across multiple tool packages.

Core Model Classes:
1. TargetRef: Reference to a ServiceNow record (table + sys_id/name)
2. ReferenceLookup: Configuration for resolving @field references
3. EditIntent: High-level intent to edit a record (usually ATF input)
4. EditDiff: Before/after diff representation for a single field change
5. ValidationResult: Pass/fail result with evidence for validation checks
6. RollbackAction: Metadata for reversing a committed change
7. EditResult: Comprehensive result including preview, validation, and rollback info

Usage Patterns:
- Tool receives ATFStepInputUpdateRequest
- Converts to EditIntent for planning
- Generates EditDiff for preview
- Creates RollbackAction for safety
- Returns EditResult with all metadata

Safety Features:
- Pydantic validation prevents invalid data
- Field constraints (e.g., limit: ge=1, le=100)
- Aliases for compatibility (@Field(alias="from"))
- populate_by_name for flexible input parsing

Data Flow:
EditIntent → preview → EditDiff → validation → RollbackAction → EditResult

Integration Points:
- preview_step_input_update() uses EditIntent and generates EditDiff
- validate_step_input() returns ValidationResult
- rollback_step_input_update() uses RollbackAction metadata
- Safe mutation pattern: Preview → Validate → Commit → Rollback
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TargetRef(BaseModel):
    """Reference to a ServiceNow target record (table + sys_id or name).
    
    Identifies which record to operate on using either:
    - table + sys_id: Precise targeting (primary key)
    - table + name: Lookup by name field (if unique)
    - table + display_value: Human-readable reference
    
    Used in edit operations to specify which record/field to modify.
    
    Attributes:
        table: Table name (e.g., 'sys_atf_step', 'incident')
        sys_id: ServiceNow sys_id (unique record identifier)
        name: Record name field (usually 'name' or 'title')
        display_value: Human-readable label for the record
    
    Example:
        TargetRef(table='sys_atf_step', sys_id='123abc...')
        TargetRef(table='incident', name='INC0001234')
    """
    table: Optional[str] = None
    sys_id: Optional[str] = None
    name: Optional[str] = None
    display_value: Optional[str] = None


class ReferenceLookup(BaseModel):
    """Configuration for resolving a reference value in ServiceNow.
    
    Used to look up and resolve @field:fieldname or table.column references
    before applying updates. Finds the target sys_id and display_value.
    
    Reference Resolution Process:
    1. Query specified table
    2. Match lookup_field == lookup_value
    3. Filter to active records (if active_only=True)
    4. Return first N matches (limit)
    5. Return sys_id and display_value
    
    Attributes:
        table: Table to search (e.g., 'sys_user', 'incident', 'cmdb_ci')
        lookup_field: Field to match against (e.g., 'user_name', 'number')
        lookup_value: Value to search for (e.g., 'admin', 'INC0001234')
        active_only: Filter to active records only (default True)
        limit: Max results to return (1-100, default 5)
            - If multiple matches: returns first N, caller must disambiguate
    
    Example:
        ReferenceLookup(table='sys_user', lookup_field='user_name', lookup_value='admin')
        ReferenceLookup(table='incident', lookup_field='number', lookup_value='INC0001234')
    """
    table: str  # Table to search (e.g., 'sys_user')
    lookup_field: str  # Field to match (e.g., 'user_name')
    lookup_value: str  # Value to search for
    active_only: bool = True  # Only return active records
    limit: int = Field(5, ge=1, le=100)  # Max results to return


class EditIntent(BaseModel):
    """Intent to edit a ServiceNow record (usually ATF test input).
    
    High-level representation of what the user wants to change.
    Used by edit tools to plan and execute safe mutations.
    
    Safe Mutation Pattern:
    1. Parse EditIntent from tool request
    2. Call preview_step_input_update() to generate EditDiff (dry-run)
    3. Return preview to user for confirmation
    4. User calls update_step_input() with confirmed intent
    5. Tool applies change and generates EditResult with rollback
    
    Attributes:
        operation: Type of edit operation (default: 'update_atf_step_input')
        target: Target record details (table, sys_id, name, etc.)
        input_name_or_label: Name or label of input to update (ATF-specific)
        reference: ReferenceLookup config for resolving target reference
        dry_run: If True, return preview only (don't apply changes)
    
    Example:
        EditIntent(
            operation='update_atf_step_input',
            target={'test_sys_id': '...', 'step_sys_id': '...'},
            input_name_or_label='user_input_1',
            reference=ReferenceLookup(table='sys_user', lookup_field='user_name', lookup_value='admin'),
            dry_run=False,
        )
    """
    operation: str = "update_atf_step_input"  # Type of operation
    target: Dict[str, Any] = Field(default_factory=dict)  # Target record details
    input_name_or_label: Optional[str] = None  # Input name/label to update
    reference: Optional[ReferenceLookup] = None  # Reference lookup config
    dry_run: bool = True  # Preview-only (don't apply changes)


class EditDiff(BaseModel):
    """Represents a single field change (before and after values).
    
    Generated by preview operations to show what would change.
    Included in EditResult for user confirmation before committing.
    
    Change Representation:
    - table: Which table the field is in
    - sys_id: Which record is changing
    - field: Which field is changing
    - from_value/to_value: Raw values (sys_id for references)
    - from_display_value/to_display_value: Human-readable values
    - value_path: For nested fields (e.g., 'inputs[0].value')
    
    Field Aliases:
    - from_value uses alias "from" (Python reserved keyword)
    - to_value uses alias "to" (for consistency)
    - model_config populate_by_name=True allows both forms in input
    
    Attributes:
        table: Table name (e.g., 'sys_atf_step')
        sys_id: Record sys_id being modified
        field: Field name being changed
        from_value: Previous value (raw, often sys_id for references)
        to_value: New value (raw, often sys_id for references)
        from_display_value: Previous display value (human-readable)
        to_display_value: New display value (human-readable)
        value_path: Path into nested JSON field (optional)
    
    Example:
        EditDiff(
            table='sys_atf_step',
            sys_id='123abc...',
            field='step_input_1',
            from_value='old_user_id',
            to_value='new_user_id',
            from_display_value='Alice',
            to_display_value='Bob',
        )
    """
    table: str  # Table containing the field
    sys_id: str  # Record sys_id
    field: str  # Field name
    from_value: Any = Field(None, alias="from")  # Previous value
    to_value: Any = Field(None, alias="to")  # New value
    from_display_value: Optional[str] = None  # Previous display value (for references)
    to_display_value: Optional[str] = None  # New display value (for references)
    value_path: Optional[str] = None  # Path into nested value (e.g., 'nested.field.value')

    model_config = {"populate_by_name": True}


class ValidationResult(BaseModel):
    """Result of validating a change against expected value.
    
    Post-mutation validation to confirm the change was applied correctly.
    Compares actual field value (after change) with expected value.
    
    Validation Use Cases:
    1. Post-update verification: Confirm value was set correctly
    2. Rollback verification: Confirm rollback restored original value
    3. Independent audit: Verify a change was applied without making it
    
    Evidence Trail:
    - Includes proof of where value was found (table, sys_id, field)
    - Supports multiple evidence types (direct field, related record, JSON path)
    - Enables tracing for audit and debugging
    
    Attributes:
        passed: True if actual matches expected, False otherwise
        actual_raw_value: Raw value found in storage (sys_id for references)
        actual_display_value: Display value (human-readable for references)
        expected_raw_value: Raw value that was expected
        expected_display_value: Display value that was expected
        evidence: List of proof (table, sys_id, field, value, path)
        message: Human-readable validation message/reason
    
    Example:
        ValidationResult(
            passed=True,
            actual_raw_value='user123',
            actual_display_value='Bob',
            expected_raw_value='user123',
            expected_display_value='Bob',
            evidence=[{
                'table': 'sys_atf_step',
                'sys_id': 'step456',
                'field': 'step_input_1',
            }],
            message='Input value matches expected value.',
        )
    """
    passed: bool  # Whether validation passed
    actual_raw_value: Any = None  # Actual raw value found
    actual_display_value: Optional[str] = None  # Actual display value found
    expected_raw_value: Any = None  # Expected raw value
    expected_display_value: Optional[str] = None  # Expected display value
    evidence: List[Dict[str, Any]] = Field(default_factory=list)  # Validation evidence
    message: Optional[str] = None  # Validation message/reason


class RollbackAction(BaseModel):
    """Metadata for rolling back a record change.
    
    Captures all necessary information to reverse a committed change.
    Created BEFORE mutation (fail-safe: if save fails, mutation doesn't happen).
    Persisted to file system for availability across server restarts.
    
    Rollback Storage:
    - rollback_id: Unique identifier (UUID)
    - Stored in .servicenow_mcp_rollbacks/{rollback_id}.json
    - Indexed by idempotency_key for duplicate detection
    - Persists across server restarts
    
    Restoration Process:
    1. Read RollbackAction by rollback_id
    2. Update storage record with previous_value
    3. Re-read field to verify restoration
    4. Return validation result
    
    Attributes:
        rollback_id: Unique identifier for this rollback action
        operation: Type of operation (e.g., 'update_atf_step_input')
        timestamp: ISO format timestamp of when change was made
        actor: Who/what made the change (default: 'servicenow_atf_update_step_input')
        instance_url: ServiceNow instance URL
        table: Table containing the changed record
        sys_id: sys_id of the changed record
        field: Field that was changed
        previous_value: Value before change (for restoring)
        new_value: Value after change (for audit trail)
        test_sys_id: Related ATF test sys_id (if applicable)
        step_sys_id: Related ATF step sys_id (if applicable)
        input_name_or_label: Input name or label that was updated
        correlation_id: Correlation ID for security/tracking
        idempotency_key: Idempotency key for duplicate detection
        value_path: Path into nested JSON field (if applicable)
        extra: Additional metadata (generic)
    
    Example:
        RollbackAction(
            rollback_id='550e8400-e29b-41d4-a716-446655440000',
            operation='update_atf_step_input',
            table='sys_atf_step',
            sys_id='step123',
            field='step_input_1',
            previous_value='old_user_id',
            new_value='new_user_id',
            test_sys_id='test456',
            step_sys_id='step123',
            input_name_or_label='user_input',
        )
    """
    rollback_id: str  # Unique rollback identifier
    operation: str  # Type of operation performed
    timestamp: str  # When the change was made (ISO format)
    actor: str = "servicenow_atf_update_step_input"  # Who/what made the change
    instance_url: Optional[str] = None  # ServiceNow instance URL
    table: str  # Table that was changed
    sys_id: str  # Record that was changed
    field: str  # Field that was changed
    previous_value: Any = None  # Value before change (for rollback)
    new_value: Any = None  # Value after change
    test_sys_id: Optional[str] = None  # Related ATF test (if applicable)
    step_sys_id: Optional[str] = None  # Related ATF step (if applicable)
    input_name_or_label: Optional[str] = None  # Input that was updated (if applicable)
    correlation_id: Optional[str] = None  # Correlation ID for tracking
    idempotency_key: Optional[str] = None  # Idempotency key for preventing duplicates
    value_path: Optional[str] = None  # Path into nested value
    extra: Dict[str, Any] = Field(default_factory=dict)  # Additional metadata


class EditResult(BaseModel):
    """Result of an edit operation (update, preview, or blocked).
    
    Comprehensive response containing status, changes, validation, and rollback info.
    Returned by safe mutation tools to inform users of what happened.
    
    Status Codes:
    - success: Change committed and validated successfully
    - preview: Dry-run preview (no changes made)
    - blocked: Operation blocked by policy or validation failure
    - failed: Change attempted but validation failed (rollback attempted)
    
    Result Structure:
    1. Status and message: Operation outcome
    2. Target: What was attempted
    3. Diff: What changed (if successful)
    4. Validation: Post-commit verification results
    5. Rollback: Undo capability information
    6. Warnings/Candidates: Issues or disambiguation options
    
    Response Use:
    - Success: User may proceed; rollback available if needed
    - Preview: User reviews and decides to commit
    - Blocked: Policy prevents operation (production write, invalid reference)
    - Failed: Validation failed after commit; automatic rollback attempted
    
    Attributes:
        status: Result status (success|preview|blocked|failed)
        operation: Type of operation performed
        message: Human-readable result message
        target: Target record details (test, step, input, etc.)
        diff: EditDiff showing what changed
        change: Change details (input name, before/after values)
        validation: ValidationResult from post-commit verification
        rollback: Rollback availability and ID for recovery
        warnings: Non-blocking issues (e.g., deprecated fields)
        candidates: Alternative matches for disambiguation
        reason: Reason for blocked/failed status
    
    Example (Success):
        EditResult(
            status='success',
            operation='update_atf_step_input',
            message='ATF step input updated successfully.',
            target={...},
            diff=EditDiff(...),
            validation=ValidationResult(passed=True, ...),
            rollback={'available': True, 'rollback_id': '...'},
        )
    
    Example (Blocked):
        EditResult(
            status='blocked',
            operation='update_atf_step_input',
            message='Production writes are blocked by default.',
            reason='production_write_blocked',
        )
    """
    status: Literal["success", "preview", "blocked", "failed"]  # Operation status
    operation: str  # Type of operation
    message: str  # Result message
    target: Dict[str, Any] = Field(default_factory=dict)  # Target record details
    diff: Optional[EditDiff] = None
    change: Dict[str, Any] = Field(default_factory=dict)
    validation: Optional[ValidationResult] = None
    rollback: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    reason: Optional[str] = None
