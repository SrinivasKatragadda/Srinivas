"""ServiceNow ATF edit tools with input discovery, safe preview, validation, and rollback.

This module exposes the user-facing wrappers around the ATF edit service. It
bridges the edit request models, discovery helpers, and safe mutation service
so callers can preview, apply, validate, and roll back ATF input changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from servicenow_mcp.atf_framework.service import ATFServiceNowClient, value
from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.models.atf_models import ATFStepInputUpdateRequest
from servicenow_mcp.models.edit_models import ReferenceLookup
from servicenow_mcp.services.atf_service import AtfEditDiscoveryService
from servicenow_mcp.services.edit_service import AtfEditService, _production_write_blocked
from servicenow_mcp.utils.config import ServerConfig
from servicenow_mcp.utils.reference_resolver import resolve_reference


def _client(config: ServerConfig, auth_manager: AuthManager) -> ATFServiceNowClient:
    return ATFServiceNowClient(config, auth_manager)


def _error(operation: str, exc: Exception) -> Dict[str, Any]:
    return {
        "status": "failed",
        "operation": operation,
        "message": str(exc),
        "errors": [{"type": exc.__class__.__name__, "message": str(exc)}],
    }


class ATFFindTestParams(BaseModel):
    """Parameters for finding ATF tests by query, sys_id, or scope."""
    query: Optional[str] = None
    sys_id: Optional[str] = None
    scope: Optional[str] = None
    active: Optional[bool] = True
    limit: int = Field(10, ge=1, le=100)
    max_candidates: Optional[int] = Field(None, ge=1, le=100)
    include_raw: bool = False


class ATFFindStepParams(BaseModel):
    """Parameters for finding ATF steps within a test."""
    test_sys_id: Optional[str] = None
    step_sys_id: Optional[str] = None
    step_name_or_label: Optional[str] = None
    order: Optional[int] = None
    limit: int = Field(20, ge=1, le=100)
    max_candidates: Optional[int] = Field(None, ge=1, le=100)
    include_raw: bool = False


class ATFGetStepInputsParams(BaseModel):
    """Parameters for retrieving ATF step inputs and storage locations."""
    step_sys_id: str
    include_raw: bool = False
    max_fields: int = Field(80, ge=10, le=500)
    max_related_records: int = Field(50, ge=1, le=200)


class ATFDebugStepStorageParams(BaseModel):
    """Parameters for debugging ATF step input storage and field mappings."""
    step_sys_id: str
    include_raw: bool = False
    max_fields: int = Field(80, ge=10, le=500)
    max_related_records: int = Field(20, ge=1, le=200)
    include_script_body: bool = False


class ServiceNowResolveReferenceParams(BaseModel):
    """Parameters for resolving a reference to a ServiceNow record."""
    table: str
    lookup_field: str
    lookup_value: str
    active_only: bool = True
    limit: int = Field(5, ge=1, le=100)


class ATFPreviewStepInputUpdateParams(BaseModel):
    """Parameters for ATFPreview Step Input Update tool calls."""
    test_sys_id: str
    step_sys_id: str
    input_name_or_label: str
    reference_table: str = "sys_user"
    reference_lookup_field: str = "user_name"
    reference_lookup_value: str
    allow_description_only_update: bool = False
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class ATFUpdateStepInputParams(ATFPreviewStepInputUpdateParams):
    """Parameters for updating an ATF step input with validation and rollback support."""
    dry_run: bool = True


class ATFValidateStepInputParams(BaseModel):
    """Parameters for validating an ATF step input after update."""
    step_sys_id: str
    input_name_or_label: str
    expected_raw_value: str
    expected_display_value: Optional[str] = None


class ATFRollbackStepInputUpdateParams(BaseModel):
    """Parameters for rolling back an ATF step input update."""
    rollback_id: str
    correlation_id: Optional[str] = None


class ATFUpdateTestMetadataParams(BaseModel):
    """Parameters for updating ATF test metadata (name, description, active status)."""
    test_sys_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    owner: Optional[str] = None
    sys_scope: Optional[str] = None
    dry_run: bool = True
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class ATFUpdateStepMetadataParams(BaseModel):
    """Parameters for updating ATF step metadata (name, description, order, expected result)."""
    step_sys_id: str
    order: Optional[int] = None
    description: Optional[str] = None
    timeout: Optional[int] = None
    active: Optional[bool] = None
    dry_run: bool = True
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class ATFReorderStepsParams(BaseModel):
    """Parameters for reordering ATF steps within a test."""
    test_sys_id: str
    ordered_step_sys_ids: List[str]
    dry_run: bool = True
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None


def _update_request(
    params: ATFPreviewStepInputUpdateParams, dry_run: bool = True
) -> ATFStepInputUpdateRequest:
    return ATFStepInputUpdateRequest(
        test_sys_id=params.test_sys_id,
        step_sys_id=params.step_sys_id,
        input_name_or_label=params.input_name_or_label,
        reference_table=params.reference_table,
        reference_lookup_field=params.reference_lookup_field,
        reference_lookup_value=params.reference_lookup_value,
        dry_run=dry_run,
        allow_description_only_update=params.allow_description_only_update,
        correlation_id=params.correlation_id,
        idempotency_key=params.idempotency_key,
    )


def servicenow_resolve_reference(
    config: ServerConfig, auth_manager: AuthManager, params: ServiceNowResolveReferenceParams
) -> Dict[str, Any]:
    """Resolve a ServiceNow reference record for MCP edit workflows.

    Performs table/field lookup resolution through the shared ATF edit
    reference resolver and returns standardized candidate match details.
    """
    try:
        return resolve_reference(
            _client(config, auth_manager),
            ReferenceLookup(
                table=params.table,
                lookup_field=params.lookup_field,
                lookup_value=params.lookup_value,
                active_only=params.active_only,
                limit=params.limit,
            ),
        )
    except Exception as exc:
        return _error("servicenow_resolve_reference", exc)


def servicenow_atf_find_test(
    config: ServerConfig, auth_manager: AuthManager, params: ATFFindTestParams
) -> Dict[str, Any]:
    """Find candidate ATF tests for edit operations.

    Uses the ATF discovery service to search by query, sys_id, and scope and
    returns ranked candidates for deterministic edit selection.
    """
    try:
        service = AtfEditDiscoveryService(_client(config, auth_manager))
        result = service.find_test(
            params.query,
            params.sys_id,
            params.scope,
            params.active,
            params.max_candidates or params.limit,
            params.include_raw,
        )
        result["operation"] = "find_atf_test"
        return result
    except Exception as exc:
        return _error("servicenow_atf_find_test", exc)


def servicenow_atf_find_step(
    config: ServerConfig, auth_manager: AuthManager, params: ATFFindStepParams
) -> Dict[str, Any]:
    """Find ATF test steps that match edit criteria.

    Searches within a target test using sys_id, label, and order hints so
    callers can safely identify the exact step to mutate.
    """
    try:
        service = AtfEditDiscoveryService(_client(config, auth_manager))
        result = service.find_step(
            test_sys_id=params.test_sys_id,
            step_sys_id=params.step_sys_id,
            step_name_or_label=params.step_name_or_label,
            order=params.order,
            limit=params.max_candidates or params.limit,
            include_raw=params.include_raw,
        )
        result["operation"] = "find_atf_step"
        return result
    except Exception as exc:
        return _error("servicenow_atf_find_step", exc)


def servicenow_atf_get_step_inputs(
    config: ServerConfig, auth_manager: AuthManager, params: ATFGetStepInputsParams
) -> Dict[str, Any]:
    """Discover editable input fields for an ATF step.

    Extracts step input metadata and related references so edit tools can choose
    the correct native storage location for updates.
    """
    try:
        service = AtfEditDiscoveryService(_client(config, auth_manager))
        result = service.discover_step_inputs(
            params.step_sys_id,
            params.include_raw,
            params.max_related_records,
            params.max_fields,
        )
        result["operation"] = "get_atf_step_inputs"
        return result
    except Exception as exc:
        return _error("servicenow_atf_get_step_inputs", exc)


def servicenow_atf_debug_step_storage(
    config: ServerConfig, auth_manager: AuthManager, params: ATFDebugStepStorageParams
) -> Dict[str, Any]:
    """Inspect low-level storage details for an ATF step.

    Returns field-level storage diagnostics to help troubleshoot why a step
    input update resolved to a specific table or field.
    """
    try:
        service = AtfEditDiscoveryService(_client(config, auth_manager))
        result = service.debug_step_storage(
            params.step_sys_id,
            params.include_raw,
            params.max_fields,
            params.max_related_records,
            params.include_script_body,
        )
        result["operation"] = "debug_atf_step_storage"
        return result
    except Exception as exc:
        return _error("servicenow_atf_debug_step_storage", exc)


def servicenow_atf_preview_step_input_update(
    config: ServerConfig, auth_manager: AuthManager, params: ATFPreviewStepInputUpdateParams
) -> Dict[str, Any]:
    """Preview an ATF step input update without writing changes.

    Runs full validation and storage-resolution logic while forcing dry-run mode
    so callers can inspect planned changes safely.
    """
    try:
        service = AtfEditService(_client(config, auth_manager), config)
        return service.preview_step_input_update(_update_request(params, dry_run=True))
    except Exception as exc:
        return _error("servicenow_atf_preview_step_input_update", exc)


def servicenow_atf_update_step_input(
    config: ServerConfig, auth_manager: AuthManager, params: ATFUpdateStepInputParams
) -> Dict[str, Any]:
    """Apply an ATF step input update using resolved native storage mappings."""
    try:
        service = AtfEditService(_client(config, auth_manager), config)
        return service.update_step_input(_update_request(params, dry_run=params.dry_run))
    except Exception as exc:
        return _error("servicenow_atf_update_step_input", exc)


def servicenow_atf_validate_step_input(
    config: ServerConfig, auth_manager: AuthManager, params: ATFValidateStepInputParams
) -> Dict[str, Any]:
    """Validate current ATF step input values against expected values."""
    try:
        service = AtfEditService(_client(config, auth_manager), config)
        return {
            "status": "success",
            "operation": "validate_atf_step_input",
            "validation": service.validate_step_input(
                params.step_sys_id,
                params.input_name_or_label,
                params.expected_raw_value,
                params.expected_display_value,
            ),
        }
    except Exception as exc:
        return _error("servicenow_atf_validate_step_input", exc)


def servicenow_atf_rollback_step_input_update(
    config: ServerConfig, auth_manager: AuthManager, params: ATFRollbackStepInputUpdateParams
) -> Dict[str, Any]:
    """Rollback a previously recorded ATF step input update operation."""
    try:
        service = AtfEditService(_client(config, auth_manager), config)
        return service.rollback_step_input_update(params.rollback_id, params.correlation_id)
    except Exception as exc:
        return _error("servicenow_atf_rollback_step_input_update", exc)


def servicenow_atf_update_test_metadata(
    config: ServerConfig, auth_manager: AuthManager, params: ATFUpdateTestMetadataParams
) -> Dict[str, Any]:
    """Update editable metadata fields on a sys_atf_test record."""
    try:
        service = AtfEditService(_client(config, auth_manager), config)
        payload = {
            "name": params.name,
            "description": params.description,
            "active": None if params.active is None else str(params.active).lower(),
            "owner": params.owner,
            "sys_scope": params.sys_scope,
        }
        return service.update_record_metadata(
            "sys_atf_test",
            params.test_sys_id,
            payload,
            params.dry_run,
            "update_atf_test_metadata",
        )
    except Exception as exc:
        return _error("servicenow_atf_update_test_metadata", exc)


def servicenow_atf_update_step_metadata(
    config: ServerConfig, auth_manager: AuthManager, params: ATFUpdateStepMetadataParams
) -> Dict[str, Any]:
    """Update editable metadata fields on a sys_atf_step record."""
    try:
        service = AtfEditService(_client(config, auth_manager), config)
        payload = {
            "order": params.order,
            "description": params.description,
            "timeout": params.timeout,
            "active": None if params.active is None else str(params.active).lower(),
        }
        return service.update_record_metadata(
            "sys_atf_step",
            params.step_sys_id,
            payload,
            params.dry_run,
            "update_atf_step_metadata",
        )
    except Exception as exc:
        return _error("servicenow_atf_update_step_metadata", exc)


def servicenow_atf_reorder_steps(
    config: ServerConfig, auth_manager: AuthManager, params: ATFReorderStepsParams
) -> Dict[str, Any]:
    """Reorder ATF steps within a test using explicit step id ordering."""
    try:
        client = _client(config, auth_manager)
        AtfEditService(client, config)
        existing = client.query_atf_steps(params.test_sys_id)
        existing_ids = {str(value(step.get("sys_id")) or "") for step in existing}
        supplied = [step_id for step_id in params.ordered_step_sys_ids if step_id]
        if len(supplied) != len(set(supplied)):
            return {
                "status": "blocked",
                "operation": "reorder_atf_steps",
                "reason": "duplicate_step_ids",
                "message": "Duplicate step ids supplied.",
            }
        missing = [step_id for step_id in supplied if step_id not in existing_ids]
        if missing:
            return {
                "status": "blocked",
                "operation": "reorder_atf_steps",
                "reason": "step_not_in_test",
                "missing": missing,
            }
        updates = [
            {"step_sys_id": step_id, "order": index} for index, step_id in enumerate(supplied, 1)
        ]
        if params.dry_run:
            return {
                "status": "preview",
                "operation": "reorder_atf_steps",
                "safe_to_apply": True,
                "planned_updates": updates,
            }
        block_reason = _production_write_blocked(config)
        if block_reason:
            return {
                "status": "blocked",
                "operation": "reorder_atf_steps",
                "reason": "production_write_blocked",
                "message": block_reason,
            }
        before = existing
        updated = []
        for item in updates:
            updated.append(
                client.update_record("sys_atf_step", item["step_sys_id"], {"order": item["order"]})
            )
        after = client.query_atf_steps(params.test_sys_id)
        return {
            "status": "success",
            "operation": "reorder_atf_steps",
            "updated_steps": updated,
            "validation": {"passed": len(after) >= len(supplied), "after": after},
            "rollback": {"available": True, "records": before},
        }
    except Exception as exc:
        return _error("servicenow_atf_reorder_steps", exc)
