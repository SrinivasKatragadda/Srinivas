"""Models for ServiceNow ATF edit and discovery tools.

This module defines the reference and update models used by ATF discovery and
safe-edit workflows. The models describe ATF tests, steps, step inputs, and
input-update requests/results in a shape that supports preview, validation,
rollback, and audit-friendly reporting.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from servicenow_mcp.models.edit_models import ReferenceLookup, RollbackAction, ValidationResult


class ATFTestRef(BaseModel):
    """Reference to an ATF test record with metadata."""
    sys_id: str  # Unique test identifier
    name: Optional[str] = None  # Test name
    active: Optional[bool] = None  # Whether test is active
    application_scope: Optional[str] = None  # Application scope (for scoped apps)
    description: Optional[str] = None  # Test description
    sys_created_on: Optional[str] = None  # Creation timestamp
    sys_updated_on: Optional[str] = None  # Last update timestamp
    raw: Dict[str, Any] = Field(default_factory=dict)  # Full record data from ServiceNow


class ATFStepRef(BaseModel):
    """Reference to an ATF step within a test."""
    sys_id: str  # Unique step identifier
    test_sys_id: Optional[str] = None  # Parent test sys_id
    name: Optional[str] = None  # Step name/description
    order: Optional[int] = None  # Order within test (1-based)
    step_config: Optional[str] = None  # Step type/configuration
    description: Optional[str] = None  # Step description
    input_summary: Optional[str] = None  # Summary of step inputs
    raw: Dict[str, Any] = Field(default_factory=dict)  # Full record data from ServiceNow


class ATFStepInputRef(BaseModel):
    """Reference to an ATF step input with storage location details."""
    input_sys_id: str  # Unique input identifier
    storage_table: str  # Table where value is stored
    name: Optional[str] = None  # Input name
    label: Optional[str] = None  # Input label (for UI display)
    type: Optional[str] = None  # Input type (reference, string, etc.)
    value_field: str  # Field name where value is stored
    value_path: Optional[str] = None  # Path for nested values (e.g., 'nested.field')
    current_raw_value: Any = None  # Current raw (sys_id) value
    current_display_value: Optional[str] = None  # Current display value (for references)
    reference_table: Optional[str] = None  # Table for reference inputs
    mandatory: Optional[bool] = None  # Whether input is required
    source_relationship: Optional[str] = None  # Relationship to parent
    raw: Dict[str, Any] = Field(default_factory=dict)  # Full record data from ServiceNow


class ATFStepInputUpdateRequest(BaseModel):
    """Request to update an ATF step input value."""
    test_sys_id: str  # Target test sys_id
    step_sys_id: str  # Target step sys_id
    input_name_or_label: str  # Input name or label to update
    reference_table: str = "sys_user"  # Table to lookup new value in
    reference_lookup_field: str = "user_name"  # Field to search in reference table
    reference_lookup_value: str  # Value to search for
    dry_run: bool = True  # Preview-only (don't apply changes)
    allow_description_only_update: bool = False  # Allow updating description fields
    correlation_id: Optional[str] = None  # Correlation ID for tracking
    idempotency_key: Optional[str] = None  # Key for detecting duplicate requests

    @property
    def reference(self) -> ReferenceLookup:
        """Convert request to ReferenceLookup for resolution."""
        return ReferenceLookup(
            table=self.reference_table,
            lookup_field=self.reference_lookup_field,
            lookup_value=self.reference_lookup_value,
        )


class ATFStepInputUpdateResult(BaseModel):
    """Result of an ATF step input update operation."""
    status: Literal["success", "preview", "blocked", "failed"]  # Operation status
    operation: str = "update_atf_step_input"  # Operation type
    message: str  # Result message
    target: Dict[str, Any] = Field(default_factory=dict)  # Target test/step details
    resolved_reference: Dict[str, Any] = Field(default_factory=dict)  # Resolved reference details
    change: Dict[str, Any] = Field(default_factory=dict)  # Change details (before/after)
    validation: Optional[ValidationResult] = None  # Validation result
    rollback: Dict[str, Any] = Field(default_factory=dict)  # Rollback information
    rollback_action: Optional[RollbackAction] = None  # Rollback metadata
    safe_to_apply: Optional[bool] = None
    diff: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
