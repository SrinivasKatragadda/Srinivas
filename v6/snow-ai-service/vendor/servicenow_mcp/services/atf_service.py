"""ATF discovery service for auto-detecting input storage and test structure.

This module provides automated discovery of Automated Test Framework (ATF) tests,
steps, and inputs for use by the safe ATF edit service (edit_service.py).

Key Discovery Functions:
1. Test Discovery: Find ATF tests by sys_id or name
2. Step Discovery: Find ATF steps within a test
3. Storage Discovery: Auto-detect where inputs are stored (sys_atf_step vs related tables)
4. Reference Discovery: Find related inputs and dependencies
5. Diagnostic Discovery: Deep inspection of step structure for debugging

ATF Input Storage Models:
ServiceNow stores ATF inputs in multiple possible locations depending on configuration:
1. Direct Fields on sys_atf_step: step_input_0, step_input_1, etc.
2. Related Tables:
   - sys_atf_step_input: Input definition and value
   - sys_atf_step_input_value: Multi-row input values
   - sys_atf_step_parameter: Parameter inputs
   - sys_atf_step_config_*: Configuration-specific inputs
   - sys_variable_value: Global variable references
3. Nested JSON Fields: Complex inputs stored as JSON in single fields

Known Storage Tables:
- sys_variable_value, sys_atf_step_input, sys_atf_step_input_value, sys_atf_step_parameter, etc.
- Multiple related tables may be involved for a single step
- Storage location determines how updates are applied (field update vs related record upsert)

Relationship Detection:
- Step relations: step, atf_step, test_step, sys_atf_step, document_key, record, parent
- Config relations: step_config, config, atf_step_config
- Definition relations: variable, parameter, input, definition, question

Dependencies:
- ATFServiceNowClient: ServiceNow API client
- ATF Models: ATFTestRef, ATFStepRef, ATFStepInputRef
- DiffUtils: Field comparison and change tracking
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from servicenow_mcp.atf_framework.service import ATFServiceNowClient, display, safe_query, value
from servicenow_mcp.models.atf_models import ATFStepInputRef, ATFStepRef, ATFTestRef
from servicenow_mcp.utils.diff_utils import comparable_key

logger = logging.getLogger(__name__)

KNOWN_ATF_INPUT_TABLES = [
    "sys_variable_value",
    "sys_atf_step_input",
    "sys_atf_step_input_value",
    "sys_atf_step_parameter",
    "sys_atf_step_parameter_value",
    "sys_atf_step_config_variable",
    "sys_atf_step_config_var",
    "sys_atf_step_config_input",
    "sys_atf_step_config_input_value",
    "sys_atf_step_config_parameter",
    "sys_atf_step_config_parameter_value",
    "sys_atf_parameter",
    "sys_atf_parameter_value",
    "sys_atf_parameter_set",
]
STEP_RELATION_FIELDS = [
    "step",
    "atf_step",
    "test_step",
    "sys_atf_step",
    "step_id",
    "document_key",
    "document",
    "record",
    "record_id",
    "parent",
]
CONFIG_RELATION_FIELDS = ["step_config", "config", "atf_step_config", "test_step_config"]
DEFINITION_REFERENCE_FIELDS = [
    "variable",
    "parameter",
    "input",
    "definition",
    "question",
    "atf_parameter",
    "config_input",
    "step_config_input",
]
NAME_FIELDS = ["name", "variable_name", "input_name", "parameter_name", "element", "field"]
LABEL_FIELDS = ["label", "question_text", "display_name", "short_description"]
VALUE_FIELDS = [
    "value",
    "field_value",
    "string_value",
    "reference_value",
    "input_value",
    "parameter_value",
    "variable_value",
]
DISPLAY_FIELDS = ["display_value", "value_display", "display"]
REFERENCE_TABLE_FIELDS = ["reference", "reference_table", "table", "reference_table_name"]
TYPE_FIELDS = ["type", "input_type", "variable_type", "internal_type"]
MANDATORY_FIELDS = ["mandatory", "required"]
DIRECT_VALUE_FIELDS = [
    "values",
    "input_values",
    "parameters",
    "inputs",
    "input",
    "variables",
    "variable_values",
    "parameter_values",
    "step_values",
]
STEP_FIELDS = [
    "sys_id",
    "test",
    "step_config",
    "config",
    "step_config_id",
    "name",
    "step_name",
    "label",
    "description",
    "display_name",
    "summary",
    "order",
    "sys_class_name",
    "sys_scope",
    "sys_package",
    "sys_created_on",
    "sys_updated_on",
    *DIRECT_VALUE_FIELDS,
]
TEST_FIELDS = [
    "sys_id",
    "name",
    "active",
    "description",
    "sys_scope",
    "scope",
    "sys_package",
    "sys_created_on",
    "sys_updated_on",
]
CONFIG_FIELDS = [
    "sys_id",
    "name",
    "display_name",
    "description",
    "summary",
    "active",
    "category",
    "step_environment",
    "sys_scope",
    "sys_package",
    "description_generation_script",
    "inputs",
    "input_variables",
    "variables",
    "parameters",
]
CONFIG_SCRIPT_FIELDS = ["step_execution_script"]
USER_INPUT_TOKENS = ("user", "user_id", "impersonate", "impersonated_user", "user_name", "sys_user")
COMPACT_RECORD_FIELDS = [
    "sys_id",
    "name",
    "label",
    "display_name",
    "description",
    "test",
    "step",
    "step_config",
    "variable",
    "parameter",
    "input",
    "question",
    "value",
    "display_value",
    "reference",
    "reference_table",
    "type",
    "mandatory",
]


def _raw_value(record: Dict[str, Any], field: str) -> Any:
    """Extract raw (sys_id) value from a ServiceNow record field."""
    return value(record.get(field))


def _display_value(record: Dict[str, Any], field: str) -> Any:
    """Extract display value from a ServiceNow record field."""
    return display(record.get(field))


def _first(record: Dict[str, Any], fields: List[str]) -> Any:
    """Get the first non-empty display value from a list of field names."""
    for field in fields:
        item = _display_value(record, field)
        if item not in (None, ""):
            return item
    return None


def _parse_mapping(raw: Any) -> Dict[str, Any]:
    """Parse a raw value (JSON string or dict) into a mapping dict."""
    raw = value(raw)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _bool_or_none(raw: Any) -> Optional[bool]:
    """Convert a raw value to boolean or None if indeterminate."""
    raw = value(raw)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    text = str(raw).casefold()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _reference_hint(name: Optional[str], label: Optional[str]) -> Optional[str]:
    """Guess the reference table from input name/label (returns 'sys_user' for user inputs)."""
    key = comparable_key(name or label or "")
    return "sys_user" if key in {"user", "userid", "username", "impersonateuser"} else None


def _compact_raw(record: Dict[str, Any], include_raw: bool) -> Dict[str, Any]:
    """Extract a compact version of a record for logging/debugging."""
    if not include_raw:
        return {}
    return {key: record.get(key) for key in COMPACT_RECORD_FIELDS if key in record}


def _compact_value(raw: Any, max_length: int = 240) -> Any:
    """Compact a value for display (truncate long strings, show type for complex objects)."""
    raw = display(raw)
    if raw is None or isinstance(raw, (bool, int, float)):
        return raw
    if isinstance(raw, (dict, list)):
        return f"<{type(raw).__name__}>"
    text = str(raw)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}...<truncated {len(text)} chars>"


def _has_user_token(text: Any) -> bool:
    """Check if text contains user-related keywords (for matching user inputs)."""
    key = comparable_key(str(text or ""))
    return any(comparable_key(token) in key for token in USER_INPUT_TOKENS)


class AtfEditDiscoveryService:
    """Read-only ATF discovery for auto-detecting tests, steps, and input storage.
    
    Provides high-level discovery APIs for the safe ATF edit service (AtfEditService).
    Uses lower-level ATFServiceNowClient for actual ServiceNow API calls.
    
    Discovery Responsibilities:
    1. find_test(): Locate ATF tests by sys_id or name
    2. find_step(): Locate ATF steps within a test
    3. debug_step_storage(): Inspect all input storage locations for a step
    4. read_input_current_value(): Read current value from storage location
    
    Normalization Pipeline:
    - normalize_test(), normalize_step(), normalize_input(): Convert raw records to standard format
    - Handles ServiceNow field variations (display vs raw, nested vs flat)
    - Supports both Pydantic models (ATFTestRef, etc.) and plain dicts
    
    Storage Discovery (debug_step_storage):
    - Queries all known input tables for records related to step
    - Detects: direct fields, related records, nested JSON, config variables
    - Computes evidence: table, field, value, relationship type
    - Returns structured diagnostics for human interpretation
    
    Relationship Detection:
    - Resolves field references to related records (e.g., "step" → sys_atf_step)
    - Handles polymorphic references (same field may reference different tables)
    - Builds dependency graph for input variables
    
    Error Handling:
    - Non-blocking queries (safe_query): Returns empty results vs raising exceptions
    - Diagnostic summaries include warnings and error details
    - Graceful degradation if queries fail or return partial results
    
    Read-Only Safety:
    - All methods are read-only (no mutations)
    - Idempotent (multiple calls return same results)
    - Safe to use multiple times without side effects
    
    Attributes:
        client: ATFServiceNowClient for API operations
    """

    def __init__(self, client: ATFServiceNowClient):
        """Initialize discovery service with ServiceNow client.
        
        Args:
            client: ATFServiceNowClient for ServiceNow API operations
        
        Returns:
            None (initializes self.client)
        """
        self.client = client

    def normalize_test(self, record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
        """
        Normalize a raw ATF test record into standard ATFTestRef format.
        
        Args:
            record: Raw test record from ServiceNow
            include_raw: Whether to include full raw record in output
            
        Returns:
            Normalized test dict with standard fields.
        """
        scope = _display_value(record, "sys_scope") or _display_value(record, "scope")
        active_raw = value(record.get("active"))
        return ATFTestRef(
            sys_id=str(value(record.get("sys_id")) or ""),
            name=_display_value(record, "name"),
            active=_bool_or_none(active_raw),
            application_scope=scope,
            description=_display_value(record, "description"),
            sys_created_on=_display_value(record, "sys_created_on"),
            sys_updated_on=_display_value(record, "sys_updated_on"),
            raw=_compact_raw(record, include_raw),
        ).model_dump()

    def normalize_step(self, record: Dict[str, Any], include_raw: bool = False) -> Dict[str, Any]:
        """
        Normalize a raw ATF step record into standard ATFStepRef format.
        
        Extracts and parses order field, infers step name from multiple possible fields.
        
        Args:
            record: Raw step record from ServiceNow
            include_raw: Whether to include full raw record in output
            
        Returns:
            Normalized step dict with standard fields.
        """
        order: Optional[int]
        try:
            order = (
                int(value(record.get("order")))
                if value(record.get("order")) not in (None, "")
                else None
            )
        except Exception:
            order = None
        name = (
            _display_value(record, "name")
            or _display_value(record, "step_name")
            or _display_value(record, "label")
            or _display_value(record, "description")
        )
        return ATFStepRef(
            sys_id=str(value(record.get("sys_id")) or ""),
            test_sys_id=str(value(record.get("test")) or ""),
            name=name,
            order=order,
            step_config=str(
                value(
                    record.get("step_config")
                    or record.get("config")
                    or record.get("step_config_id")
                )
                or ""
            ),
            description=_display_value(record, "description"),
            input_summary=_display_value(record, "summary"),
            raw=_compact_raw(record, include_raw),
        ).model_dump()

    def find_test(
        self,
        query: Optional[str] = None,
        sys_id: Optional[str] = None,
        scope: Optional[str] = None,
        active: Optional[bool] = True,
        limit: int = 10,
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        """
        Find ATF tests by sys_id, query, scope, and active status.
        
        If sys_id provided, does direct lookup. Otherwise builds query from name/description match,
        scope filter, and active filter. Returns blocked if multiple matches found (ambiguous).
        
        Args:
            query: Text to search in test name/description (LIKE match)
            sys_id: Exact test sys_id for direct lookup
            scope: Filter by application scope
            active: Filter by active status (True/False/None for any)
            limit: Maximum results (default 10)
            include_raw: Include full raw record in response
            
        Returns:
            Dict with status (success/blocked), reason (not_found/multiple_matches), count,
            and candidates (normalized test records).
        """
        if not self.client.check_table_exists("sys_atf_test"):
            return {
                "status": "blocked",
                "reason": "table_unavailable",
                "candidates": [],
                "message": "sys_atf_test is unavailable.",
            }
        if sys_id:
            record = self._get_record("sys_atf_test", sys_id, TEST_FIELDS)
            if not record:
                return {
                    "status": "blocked",
                    "reason": "not_found",
                    "candidates": [],
                    "message": "ATF test not found.",
                }
            return {
                "status": "success",
                "count": 1,
                "candidates": [self.normalize_test(record, include_raw)],
            }
        parts = []
        if query:
            safe = safe_query(query)
            parts.append(f"nameLIKE{safe}^ORdescriptionLIKE{safe}")
        if scope:
            parts.append(f"sys_scope={safe_query(scope)}")
        if active is not None:
            parts.append(f"active={str(active).lower()}")
        records = self.client.query_records(
            "sys_atf_test", "^".join(parts) if parts else None, fields=TEST_FIELDS, limit=limit
        )
        candidates = [self.normalize_test(record, include_raw) for record in records]
        status = "blocked" if len(candidates) > 1 else "success" if candidates else "blocked"
        reason = "multiple_matches" if len(candidates) > 1 else None if candidates else "not_found"
        return {
            "status": status,
            "reason": reason,
            "count": len(candidates),
            "candidates": candidates,
        }

    def find_step(
        self,
        test_sys_id: Optional[str] = None,
        step_sys_id: Optional[str] = None,
        step_name_or_label: Optional[str] = None,
        order: Optional[int] = None,
        limit: int = 20,
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        """
        Find ATF steps within a test by sys_id, name, or order.
        
        Requires test_sys_id. If step_sys_id provided, does direct lookup with optional test
        verification. Otherwise searches by name/label and/or order within test.
        
        Args:
            test_sys_id: Parent test sys_id (required unless doing direct step_sys_id lookup)
            step_sys_id: Exact step sys_id for direct lookup
            step_name_or_label: Text to search in step name/label/description
            order: Step execution order number
            limit: Maximum results (default 20)
            include_raw: Include full raw record in response
            
        Returns:
            Dict with status (success/blocked), reason (not_found/step_test_mismatch), count,
            and candidates (normalized step records).
        """
        if not self.client.check_table_exists("sys_atf_step"):
            return {
                "status": "blocked",
                "reason": "table_unavailable",
                "candidates": [],
                "message": "sys_atf_step is unavailable.",
            }
        if step_sys_id:
            record = self._get_record("sys_atf_step", step_sys_id, STEP_FIELDS)
            if not record:
                return {
                    "status": "blocked",
                    "reason": "not_found",
                    "candidates": [],
                    "message": "ATF step not found.",
                }
            if test_sys_id and str(value(record.get("test")) or "") != test_sys_id:
                return {
                    "status": "blocked",
                    "reason": "step_test_mismatch",
                    "message": "ATF step does not belong to the supplied test.",
                    "candidates": [self.normalize_step(record, include_raw)],
                }
            return {
                "status": "success",
                "count": 1,
                "candidates": [self.normalize_step(record, include_raw)],
            }
        if not test_sys_id:
            return {"status": "blocked", "reason": "missing_test_sys_id", "candidates": []}
        records = self.client.query_records(
            "sys_atf_step",
            f"test={safe_query(test_sys_id)}",
            fields=STEP_FIELDS,
            limit=limit,
        )
        requested = comparable_key(step_name_or_label or "")
        matches = []
        for record in records:
            normalized = self.normalize_step(record, include_raw)
            if order is not None and normalized.get("order") != order:
                continue
            if requested:
                haystacks = [
                    normalized.get("name"),
                    normalized.get("description"),
                    _display_value(record, "summary"),
                    _display_value(record, "step_config"),
                ]
                if not any(requested in comparable_key(str(item or "")) for item in haystacks):
                    continue
            matches.append(normalized)
        status = "blocked" if len(matches) > 1 else "success" if matches else "blocked"
        reason = "multiple_matches" if len(matches) > 1 else None if matches else "not_found"
        return {"status": status, "reason": reason, "count": len(matches), "candidates": matches}

    def discover_step_inputs(
        self,
        step_sys_id: str,
        include_raw: bool = False,
        max_related_records: int = 50,
        max_fields: int = 80,
    ) -> Dict[str, Any]:
        """Discover step inputs.`r`n        `r`n        This helper supports MCP workflow operations and returns normalized results.`r`n        """
        step = self._get_record("sys_atf_step", step_sys_id, STEP_FIELDS)
        if not step:
            return {
                "status": "blocked",
                "reason": "step_not_found",
                "inputs": [],
                "warnings": ["ATF step not found."],
            }
        warnings: List[str] = []
        inputs: List[Dict[str, Any]] = []
        evidence: List[Dict[str, Any]] = [
            {"strategy": "read_step", "table": "sys_atf_step", "sys_id": step_sys_id}
        ]
        try:
            step_fields = set(self._table_fields("sys_atf_step", max_fields))
        except Exception as exc:
            step_fields = set()
            warnings.append(f"Could not inspect sys_atf_step fields: {exc}")

        step_config_id = str(
            value(step.get("step_config") or step.get("config") or step.get("step_config_id")) or ""
        )
        test_id = str(value(step.get("test")) or "")
        test = self._get_record("sys_atf_test", test_id, TEST_FIELDS) if test_id else {}
        step_config = (
            self._get_record("sys_atf_step_config", step_config_id, CONFIG_FIELDS)
            if step_config_id
            else {}
        )
        if step_config_id:
            evidence.append(
                {
                    "strategy": "read_step_config",
                    "table": "sys_atf_step_config",
                    "sys_id": step_config_id,
                }
            )

        config_definitions = self._discover_config_input_definitions(
            step_config_id,
            include_raw,
            max_related_records,
            max_fields,
            warnings,
            evidence,
        )

        for table in self._input_storage_tables(max_related_records, max_fields):
            try:
                fields = set(self._table_fields(table, max_fields))
            except Exception as exc:
                warnings.append(f"Could not inspect {table}: {exc}")
                continue
            relation_fields = [field for field in STEP_RELATION_FIELDS if field in fields]
            for relation_field in relation_fields:
                try:
                    records = self.client.query_records(
                        table,
                        f"{relation_field}={safe_query(step_sys_id)}",
                        fields=self._candidate_fields(fields),
                        limit=max_related_records,
                    )
                except Exception as exc:
                    warnings.append(f"Could not query {table}.{relation_field}: {exc}")
                    continue
                for record in records:
                    inputs.append(
                        self._input_from_related_record(
                            table,
                            relation_field,
                            record,
                            fields,
                            include_raw,
                            config_definitions,
                        )
                    )
                if records:
                    evidence.append(
                        {
                            "strategy": "related_value_rows",
                            "table": table,
                            "field": relation_field,
                            "count": len(records),
                        }
                    )
            inputs.extend(
                self._discover_values_by_config_definition(
                    table,
                    fields,
                    step_sys_id,
                    config_definitions,
                    include_raw,
                    max_related_records,
                    warnings,
                    evidence,
                )
            )
            if inputs:
                logger.debug("ATF input discovery used related table %s", table)

        for field in DIRECT_VALUE_FIELDS:
            if step_fields and field not in step_fields:
                continue
            mapping = _parse_mapping(step.get(field))
            for key, current_value in mapping.items():
                inputs.append(
                    ATFStepInputRef(
                        input_sys_id=step_sys_id,
                        storage_table="sys_atf_step",
                        name=str(key),
                        label=str(key),
                        type="json_value",
                        value_field=field,
                        value_path=str(key),
                        current_raw_value=current_value,
                        current_display_value=(
                            str(current_value) if current_value not in (None, "") else None
                        ),
                        reference_table=_reference_hint(str(key), str(key)),
                        source_relationship=f"sys_atf_step.{field}",
                        raw=_compact_raw(step, include_raw),
                    ).model_dump()
                )
                evidence.append(
                    {"strategy": "step_mapping_field", "table": "sys_atf_step", "field": field}
                )

        inputs = self._dedupe_inputs(inputs)
        return {
            "status": "success" if inputs else "blocked",
            "reason": None if inputs else "input_storage_not_found",
            "test": self.normalize_test(test) if test else {},
            "step": self.normalize_step(step),
            "step_config": (
                {
                    "sys_id": step_config_id,
                    "name": _display_value(step_config, "name") if step_config else None,
                    "display_name": (
                        _display_value(step_config, "display_name") if step_config else None
                    ),
                }
                if step_config_id
                else {}
            ),
            "inputs": inputs,
            "warnings": warnings,
            "evidence": evidence,
        }

    def debug_step_storage(
        self,
        step_sys_id: str,
        include_raw: bool = False,
        max_fields: int = 80,
        max_related_records: int = 20,
        include_script_body: bool = False,
    ) -> Dict[str, Any]:
        """Handle debug step storage.`r`n        `r`n        This helper supports MCP workflow operations and returns normalized results.`r`n        """
        strategies = [
            "related_records",
            "direct_field_scan",
            "encoded_payload_scan",
            "description_evidence",
            "impersonation_fallback",
        ]
        step = self._get_record("sys_atf_step", step_sys_id, STEP_FIELDS)
        if not step:
            return {
                "status": "blocked",
                "reason": "step_not_found",
                "step": {"sys_id": step_sys_id},
                "test": {},
                "step_config": {},
                "discovery": {
                    "strategies_attempted": strategies,
                    "candidate_inputs": [],
                    "evidence": [{"strategy": "read_step", "status": "not_found"}],
                },
                "next_action": "Resolve a valid sys_atf_step sys_id before updating.",
            }

        test_id = str(value(step.get("test")) or "")
        step_config_id = str(
            value(step.get("step_config") or step.get("config") or step.get("step_config_id")) or ""
        )
        test = self._get_record("sys_atf_test", test_id, TEST_FIELDS) if test_id else {}
        config_fields = list(CONFIG_FIELDS)
        if include_script_body:
            config_fields.extend(CONFIG_SCRIPT_FIELDS)
        step_config = (
            self._get_record("sys_atf_step_config", step_config_id, config_fields)
            if step_config_id
            else {}
        )

        evidence: List[Dict[str, Any]] = [
            {"strategy": "read_step", "table": "sys_atf_step", "sys_id": step_sys_id},
            {"strategy": "read_test", "table": "sys_atf_test", "sys_id": test_id or None},
        ]
        if step_config_id:
            evidence.append(
                {
                    "strategy": "read_step_config",
                    "table": "sys_atf_step_config",
                    "sys_id": step_config_id,
                }
            )

        candidates: List[Dict[str, Any]] = []
        related = self.discover_step_inputs(
            step_sys_id,
            include_raw=include_raw,
            max_related_records=max_related_records,
            max_fields=max_fields,
        )
        evidence.extend(related.get("evidence", []))
        evidence.extend(
            {"strategy": "related_records_warning", "message": warning}
            for warning in related.get("warnings", [])
        )
        description_text = " ".join(
            str(_display_value(step, field) or "")
            for field in ("display_name", "name", "description", "summary")
        )
        for item in related.get("inputs", []):
            confidence = self._candidate_confidence(item, description_text)
            if confidence < 0.35:
                continue
            candidates.append(
                {
                    "source_strategy": (
                        "related_records"
                        if not str(item.get("storage_table")).startswith("sys_atf_step")
                        else "direct_field_scan"
                    ),
                    "storage_table": item.get("storage_table"),
                    "storage_sys_id": item.get("input_sys_id"),
                    "field": item.get("value_field"),
                    "value_path": item.get("value_path"),
                    "name": item.get("name"),
                    "label": item.get("label"),
                    "current_raw_value": _compact_value(item.get("current_raw_value")),
                    "current_display_value": _compact_value(item.get("current_display_value")),
                    "confidence": confidence,
                    "evidence": self._candidate_evidence(item, description_text),
                }
            )

        try:
            step_fields = self._table_fields("sys_atf_step", max_fields)
        except Exception as exc:
            step_fields = []
            evidence.append(
                {"strategy": "direct_field_scan", "status": "blocked", "message": str(exc)}
            )
        direct_fields = [field for field in step_fields if self._field_name_is_relevant(field)]
        if direct_fields:
            direct_step = self._get_record("sys_atf_step", step_sys_id, ["sys_id", *direct_fields])
            evidence.append(
                {
                    "strategy": "direct_field_scan",
                    "table": "sys_atf_step",
                    "fields_checked": direct_fields[:max_fields],
                }
            )
            for field in direct_fields:
                raw = direct_step.get(field)
                if raw in (None, "") and not _has_user_token(field):
                    continue
                candidates.append(
                    {
                        "source_strategy": "direct_field_scan",
                        "storage_table": "sys_atf_step",
                        "storage_sys_id": step_sys_id,
                        "field": field,
                        "name": field,
                        "label": field,
                        "current_raw_value": _compact_value(raw),
                        "current_display_value": _compact_value(raw),
                        "confidence": 0.72 if _has_user_token(field) else 0.45,
                        "evidence": f"Readable sys_atf_step field name matched {field}.",
                    }
                )

        encoded_candidates, encoded_evidence = self._encoded_payload_scan(
            step, step_fields, step_sys_id
        )
        candidates.extend(encoded_candidates)
        evidence.extend(encoded_evidence)

        description_evidence = self._description_evidence(step)
        if description_evidence:
            evidence.append(description_evidence)

        if not any(float(item.get("confidence") or 0) >= 0.85 for item in candidates):
            fallback = self.resolve_impersonate_step_user_input(
                step_sys_id,
                step_config_id,
                include_raw=include_raw,
                max_fields=max_fields,
                max_related_records=max_related_records,
                step=step,
                step_config=step_config,
            )
            evidence.append(
                {
                    "strategy": "impersonation_fallback",
                    "status": fallback.get("status"),
                    "reason": fallback.get("reason"),
                    "candidate_count": len(
                        fallback.get("discovery", {}).get("candidate_inputs", [])
                    ),
                }
            )
            evidence.extend(fallback.get("discovery", {}).get("evidence", []))
            if fallback.get("status") == "success":
                candidates.extend(fallback.get("discovery", {}).get("candidate_inputs", []))

        candidates = self._dedupe_debug_candidates(candidates)
        high_confidence = [item for item in candidates if item.get("confidence", 0) >= 0.8]
        if len(high_confidence) == 1:
            status = "success"
            next_action = (
                "Use candidate input 0 for update"
                if candidates.index(high_confidence[0]) == 0
                else f"Use candidate input {candidates.index(high_confidence[0])} for update"
            )
            reason = None
        elif len(high_confidence) > 1:
            status = "blocked"
            reason = "multiple_high_confidence_candidates"
            next_action = (
                "Multiple high-confidence storage candidates were found; choose one explicitly "
                "before updating."
            )
        else:
            status = "blocked"
            reason = "input_storage_not_found"
            next_action = (
                "No high-confidence ATF input storage candidate was found; do not mutate the step "
                "description."
            )

        result = {
            "status": status,
            "step": self._debug_step_summary(step),
            "test": self._debug_test_summary(test),
            "step_config": self._debug_config_summary(step_config, include_script_body),
            "discovery": {
                "strategies_attempted": strategies,
                "candidate_inputs": candidates[:max_related_records],
                "evidence": evidence[: max_related_records * 4],
            },
            "next_action": next_action,
        }
        if reason:
            result["reason"] = reason
        return result

    def resolve_impersonate_step_user_input(
        self,
        step_sys_id: str,
        step_config_sys_id: str,
        include_raw: bool = False,
        max_fields: int = 80,
        max_related_records: int = 20,
        step: Optional[Dict[str, Any]] = None,
        step_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Find native Impersonate step user input storage without mutating anything."""

        evidence: List[Dict[str, Any]] = []
        step = step or self._get_record("sys_atf_step", step_sys_id, STEP_FIELDS)
        if not step:
            return {
                "status": "blocked",
                "reason": "step_not_found",
                "discovery": {
                    "candidate_inputs": [],
                    "evidence": [
                        {"strategy": "impersonation_fallback", "status": "step_not_found"}
                    ],
                },
            }
        if step_config_sys_id and step_config is None:
            step_config = self._get_record("sys_atf_step_config", step_config_sys_id, CONFIG_FIELDS)
        step_config = step_config or {}

        detection = self._impersonation_detection(step, step_config)
        evidence.append({"strategy": "impersonation_fallback_detection", **detection})
        if not detection["is_impersonate"]:
            return {
                "status": "blocked",
                "reason": "not_impersonate_step",
                "discovery": {"candidate_inputs": [], "evidence": evidence},
            }

        warnings: List[str] = []
        definitions = self._discover_config_input_definitions(
            step_config_sys_id,
            include_raw,
            max_related_records,
            max_fields,
            warnings,
            evidence,
        )
        candidate_inputs: List[Dict[str, Any]] = []
        for table in self._input_storage_tables(max_related_records, max_fields):
            try:
                fields = set(self._table_fields(table, max_fields))
            except Exception as exc:
                evidence.append(
                    {
                        "strategy": "impersonation_fallback_schema",
                        "table": table,
                        "status": "blocked",
                        "message": str(exc),
                    }
                )
                continue
            relation_fields = self._impersonation_step_relation_fields(fields)
            writable_fields = self._impersonation_writable_fields(fields, relation_fields)
            if not relation_fields or not writable_fields:
                continue
            query_fields = self._candidate_fields(fields)
            for field in relation_fields:
                if field in fields and field not in query_fields:
                    query_fields.append(field)
            for field in writable_fields:
                if field in fields and field not in query_fields:
                    query_fields.append(field)
            for relation_field in relation_fields:
                try:
                    records = self.client.query_records(
                        table,
                        f"{relation_field}={safe_query(step_sys_id)}",
                        fields=query_fields,
                        limit=max_related_records,
                    )
                except Exception as exc:
                    evidence.append(
                        {
                            "strategy": "impersonation_fallback_query",
                            "table": table,
                            "field": relation_field,
                            "status": "blocked",
                            "message": str(exc),
                        }
                    )
                    continue
                if records:
                    evidence.append(
                        {
                            "strategy": "impersonation_fallback_query",
                            "table": table,
                            "field": relation_field,
                            "count": len(records),
                        }
                    )
                for record in records:
                    relation_value = str(value(record.get(relation_field)) or "")
                    if relation_value and relation_value != step_sys_id:
                        evidence.append(
                            {
                                "strategy": "impersonation_fallback_skip",
                                "table": table,
                                "field": relation_field,
                                "reason": "record_not_related_to_exact_step",
                                "storage_sys_id": str(value(record.get("sys_id")) or ""),
                            }
                        )
                        continue
                    candidate = self._impersonation_candidate_from_record(
                        table,
                        relation_field,
                        record,
                        fields,
                        writable_fields,
                        definitions,
                        detection,
                        include_raw,
                    )
                    if candidate:
                        candidate_inputs.append(candidate)

        candidate_inputs = self._dedupe_debug_candidates(candidate_inputs)
        high_confidence = [
            candidate
            for candidate in candidate_inputs
            if float(candidate.get("confidence") or 0) >= 0.85
        ]
        if len(high_confidence) == 1:
            return {
                "status": "success",
                "reason": None,
                "discovery": {
                    "candidate_inputs": high_confidence,
                    "evidence": evidence,
                    "warnings": warnings,
                },
            }
        if len(high_confidence) > 1:
            return {
                "status": "blocked",
                "reason": "multiple_impersonation_storage_candidates",
                "discovery": {
                    "candidate_inputs": high_confidence,
                    "evidence": evidence,
                    "warnings": warnings,
                },
            }
        return {
            "status": "blocked",
            "reason": (
                "impersonation_storage_low_confidence"
                if candidate_inputs
                else "input_storage_not_found"
            ),
            "discovery": {
                "candidate_inputs": candidate_inputs,
                "evidence": evidence,
                "warnings": warnings,
            },
        }

    def _impersonation_detection(
        self, step: Dict[str, Any], step_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        step_text = " ".join(
            str(_display_value(step, field) or "")
            for field in ("display_name", "name", "step_name", "label", "description", "summary")
        )
        config_text = " ".join(
            str(_display_value(step_config, field) or "")
            for field in ("display_name", "name", "description", "summary", "category")
        )
        step_key = comparable_key(step_text)
        config_key = comparable_key(config_text)
        description = str(
            _display_value(step, "description") or _display_value(step, "summary") or ""
        )
        step_impersonate = "impersonat" in step_key
        config_impersonate = "impersonat" in config_key
        generated_user_id = bool(
            re.search(r"user\s*id|user_id|userid|sys_user", description, flags=re.IGNORECASE)
        )
        signals = []
        if step_impersonate:
            signals.append("step_text_contains_impersonate")
        if config_impersonate:
            signals.append("step_config_name_contains_impersonate")
        if generated_user_id:
            signals.append("generated_description_contains_user_id")
        return {
            "is_impersonate": step_impersonate or config_impersonate,
            "step_impersonate": step_impersonate,
            "config_impersonate": config_impersonate,
            "generated_user_id": generated_user_id,
            "signals": signals,
            "step_text": _compact_value(step_text, 240),
            "config_text": _compact_value(config_text, 240),
        }

    def _impersonation_step_relation_fields(self, fields: set[str]) -> List[str]:
        relation_fields = []
        for field in fields:
            key = comparable_key(field)
            if field in STEP_RELATION_FIELDS:
                relation_fields.append(field)
                continue
            if "config" in key:
                continue
            if (
                "atfstep" in key
                or "teststep" in key
                or key.endswith("step")
                or key
                in {"document", "documentkey", "record", "recordid", "targetstep", "sourceid"}
            ):
                relation_fields.append(field)
        return list(dict.fromkeys(relation_fields))

    def _impersonation_writable_fields(
        self, fields: set[str], relation_fields: List[str]
    ) -> List[str]:
        excluded = {
            "sys_id",
            *relation_fields,
            *CONFIG_RELATION_FIELDS,
            *DEFINITION_REFERENCE_FIELDS,
            *NAME_FIELDS,
            *LABEL_FIELDS,
            *DISPLAY_FIELDS,
            *REFERENCE_TABLE_FIELDS,
            *TYPE_FIELDS,
            *MANDATORY_FIELDS,
        }
        user_fields = [
            field
            for field in fields
            if field not in excluded and not field.startswith("sys_") and _has_user_token(field)
        ]
        value_fields = [
            field for field in VALUE_FIELDS if field in fields and field not in excluded
        ]
        return list(dict.fromkeys([*user_fields, *value_fields]))

    def _impersonation_candidate_from_record(
        self,
        table: str,
        relation_field: str,
        record: Dict[str, Any],
        fields: set[str],
        writable_fields: List[str],
        definitions: Dict[str, Dict[str, Any]],
        detection: Dict[str, Any],
        include_raw: bool,
    ) -> Optional[Dict[str, Any]]:
        storage_sys_id = str(value(record.get("sys_id")) or "")
        if not storage_sys_id:
            return None
        definition_field = next(
            (
                field
                for field in DEFINITION_REFERENCE_FIELDS
                if field in fields and record.get(field)
            ),
            None,
        )
        definition_id = str(value(record.get(definition_field)) or "") if definition_field else ""
        definition_display = (
            str(display(record.get(definition_field)) or "") if definition_field else ""
        )
        definition = definitions.get(definition_id, {})
        if definition_id and not definition:
            definition = self._lookup_definition_by_sys_id(definition_id, include_raw)
        name = (
            _first(record, [field for field in NAME_FIELDS if field in fields])
            or definition.get("name")
            or definition_display
        )
        label = (
            _first(record, [field for field in LABEL_FIELDS if field in fields])
            or definition.get("label")
            or definition_display
        )
        reference_table = (
            _first(record, [field for field in REFERENCE_TABLE_FIELDS if field in fields])
            or definition.get("reference_table")
            or ""
        )
        user_hint_values = [
            name,
            label,
            definition_display,
            definition.get("name"),
            definition.get("label"),
            reference_table,
            table,
            *writable_fields,
        ]
        has_user_hint = (
            any(_has_user_token(item) for item in user_hint_values)
            or str(reference_table).casefold() == "sys_user"
        )
        value_field = self._select_impersonation_value_field(writable_fields, has_user_hint)
        if not value_field:
            return None
        display_field = next((field for field in DISPLAY_FIELDS if field in fields), value_field)
        confidence = 0.0
        confidence += 0.25
        confidence += 0.20
        if detection.get("step_impersonate"):
            confidence += 0.18
        if detection.get("config_impersonate"):
            confidence += 0.14
        if detection.get("generated_user_id"):
            confidence += 0.14
        if has_user_hint:
            confidence += 0.22
        if str(reference_table).casefold() == "sys_user":
            confidence += 0.12
        if definition_id and definition_id in definitions:
            confidence += 0.08
        if not has_user_hint:
            confidence = min(confidence, 0.74)
        confidence = round(min(confidence, 0.98), 2)
        return {
            "source_strategy": "impersonation_fallback",
            "storage_table": table,
            "storage_sys_id": storage_sys_id,
            "field": value_field,
            "name": str(name or "user_id"),
            "label": str(label or "User"),
            "current_raw_value": _compact_value(_raw_value(record, value_field)),
            "current_display_value": _compact_value(_display_value(record, display_field)),
            "confidence": confidence,
            "step_sys_id": str(value(record.get(relation_field)) or ""),
            "evidence": (
                f"Native impersonation fallback matched {table}.{relation_field} to exact step; "
                f"writable field {value_field}; user_hint={has_user_hint}; "
                f"signals={','.join(detection.get('signals', []))}."
            ),
        }

    def _select_impersonation_value_field(
        self, writable_fields: List[str], has_user_hint: bool
    ) -> Optional[str]:
        for field in writable_fields:
            if _has_user_token(field):
                return field
        value_field = next((field for field in writable_fields if field in VALUE_FIELDS), None)
        if value_field:
            return value_field
        return writable_fields[0] if has_user_hint and writable_fields else None

    def _candidate_confidence(self, item: Dict[str, Any], description_text: str) -> float:
        confidence = 0.4
        if _has_user_token(item.get("name")):
            confidence += 0.25
        if _has_user_token(item.get("label")):
            confidence += 0.2
        if str(item.get("reference_table") or "").casefold() == "sys_user":
            confidence += 0.18
        if item.get("storage_table") != "sys_atf_step":
            confidence += 0.12
        if _has_user_token(description_text):
            confidence += 0.08
        if item.get("value_field"):
            confidence += 0.05
        return round(min(confidence, 0.98), 2)

    def _candidate_evidence(self, item: Dict[str, Any], description_text: str) -> str:
        pieces = []
        if _has_user_token(item.get("name")):
            pieces.append(f"name matched {item.get('name')}")
        if _has_user_token(item.get("label")):
            pieces.append(f"label matched {item.get('label')}")
        if item.get("reference_table"):
            pieces.append(f"reference table is {item.get('reference_table')}")
        if _has_user_token(description_text):
            pieces.append("step description/display text references impersonated user")
        pieces.append(f"storage is {item.get('storage_table')}.{item.get('value_field')}")
        return "; ".join(pieces)

    def _field_name_is_relevant(self, field: str) -> bool:
        key = comparable_key(field)
        tokens = [*USER_INPUT_TOKENS, "input", "variable", "values", "parameters", "params"]
        return any(comparable_key(token) in key for token in tokens)

    def _encoded_payload_scan(
        self,
        step: Dict[str, Any],
        step_fields: List[str],
        step_sys_id: str,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        candidates: List[Dict[str, Any]] = []
        evidence: List[Dict[str, Any]] = []
        fields_to_scan = [
            field
            for field in step_fields
            if field in step
            and "script" not in field.casefold()
            and isinstance(value(step.get(field)), str)
            and len(str(value(step.get(field)) or "")) <= 2000
        ]
        if fields_to_scan:
            evidence.append(
                {
                    "strategy": "encoded_payload_scan",
                    "table": "sys_atf_step",
                    "fields_checked": fields_to_scan,
                }
            )
        for field in fields_to_scan:
            text = str(value(step.get(field)) or "")
            parsed = _parse_mapping(text)
            if parsed:
                for key_path, found_value in self._walk_json_keys(parsed):
                    if not _has_user_token(key_path):
                        continue
                    candidates.append(
                        {
                            "source_strategy": "encoded_payload_scan",
                            "storage_table": "sys_atf_step",
                            "storage_sys_id": step_sys_id,
                            "field": field,
                            "value_path": key_path,
                            "name": key_path.split(".")[-1],
                            "label": key_path.split(".")[-1],
                            "current_raw_value": _compact_value(found_value),
                            "current_display_value": _compact_value(found_value),
                            "confidence": 0.86,
                            "evidence": (
                                f"JSON payload key {key_path} matched user/impersonation tokens."
                            ),
                        }
                    )
            for match in re.finditer(
                r"<(?P<key>[^>/]*?(?:user|impersonat|sys_user)[^>/]*?)>(?P<value>.*?)</[^>]+>",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                current = match.group("value").strip()[:240]
                key = match.group("key").strip()
                candidates.append(
                    {
                        "source_strategy": "encoded_payload_scan",
                        "storage_table": "sys_atf_step",
                        "storage_sys_id": step_sys_id,
                        "field": field,
                        "name": key,
                        "label": key,
                        "current_raw_value": current or None,
                        "current_display_value": current or None,
                        "confidence": 0.78,
                        "evidence": (
                            f"XML-like payload tag {key} matched user/impersonation tokens."
                        ),
                    }
                )
            for match in re.finditer(
                r"(?P<key>user_id|user_name|impersonated_user|impersonate|sys_user|user)\s*[:=]\s*(?P<value>[^,;\n|]*)",
                text,
                flags=re.IGNORECASE,
            ):
                key = match.group("key")
                current = match.group("value").strip() or None
                candidates.append(
                    {
                        "source_strategy": "encoded_payload_scan",
                        "storage_table": "sys_atf_step",
                        "storage_sys_id": step_sys_id,
                        "field": field,
                        "name": key,
                        "label": key,
                        "current_raw_value": _compact_value(current),
                        "current_display_value": _compact_value(current),
                        "confidence": 0.82,
                        "evidence": (
                            f"Key-value payload key {key} matched user/impersonation tokens."
                        ),
                    }
                )
        return candidates, evidence

    def _walk_json_keys(self, payload: Any, prefix: str = "") -> List[tuple[str, Any]]:
        found: List[tuple[str, Any]] = []
        if isinstance(payload, dict):
            for key, val in payload.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                found.append((path, val))
                found.extend(self._walk_json_keys(val, path))
        elif isinstance(payload, list):
            for index, val in enumerate(payload[:20]):
                path = f"{prefix}.{index}" if prefix else str(index)
                found.extend(self._walk_json_keys(val, path))
        return found

    def _description_evidence(self, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        description = str(
            _display_value(step, "description")
            or _display_value(step, "summary")
            or _display_value(step, "display_name")
            or ""
        )
        if not description:
            return None
        user_match = re.search(
            r"(impersonat.*?user|user\s+id|user_id|sys_user).*?(null|empty|[0-9a-f]{32}|[A-Za-z0-9_.@-]+)?",
            description,
            flags=re.IGNORECASE,
        )
        if not user_match:
            return None
        return {
            "strategy": "description_evidence",
            "field": "description",
            "evidence": _compact_value(description, 320),
            "note": "Description is evidence only and is not a mutation target.",
        }

    def _dedupe_debug_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in sorted(
            candidates, key=lambda candidate: candidate.get("confidence", 0), reverse=True
        ):
            key = (
                str(item.get("storage_table") or ""),
                str(item.get("storage_sys_id") or ""),
                str(item.get("field") or ""),
                str(item.get("value_path") or item.get("name") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _debug_step_summary(self, step: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "sys_id": str(value(step.get("sys_id")) or ""),
            "display_name": _display_value(step, "display_name") or _display_value(step, "name"),
            "name": _display_value(step, "name"),
            "description": _compact_value(_display_value(step, "description"), 320),
            "order": _display_value(step, "order"),
            "step_config": str(
                value(step.get("step_config") or step.get("config") or step.get("step_config_id"))
                or ""
            ),
            "test": str(value(step.get("test")) or ""),
            "sys_class_name": _display_value(step, "sys_class_name"),
            "sys_scope": _display_value(step, "sys_scope"),
            "sys_package": _display_value(step, "sys_package"),
            "sys_created_on": _display_value(step, "sys_created_on"),
            "sys_updated_on": _display_value(step, "sys_updated_on"),
        }

    def _debug_test_summary(self, test: Dict[str, Any]) -> Dict[str, Any]:
        if not test:
            return {}
        return {
            "sys_id": str(value(test.get("sys_id")) or ""),
            "name": _display_value(test, "name"),
            "active": _display_value(test, "active"),
            "sys_scope": _display_value(test, "sys_scope") or _display_value(test, "scope"),
            "sys_package": _display_value(test, "sys_package"),
        }

    def _debug_config_summary(
        self, config: Dict[str, Any], include_script_body: bool
    ) -> Dict[str, Any]:
        if not config:
            return {}
        summary = {
            "sys_id": str(value(config.get("sys_id")) or ""),
            "name": _display_value(config, "name"),
            "active": _display_value(config, "active"),
            "category": _display_value(config, "category"),
            "step_environment": _display_value(config, "step_environment"),
            "sys_scope": _display_value(config, "sys_scope"),
            "sys_package": _display_value(config, "sys_package"),
        }
        script = str(value(config.get("description_generation_script")) or "")
        summary["description_generation_script"] = {
            "present": bool(script),
            "length": len(script),
        }
        if include_script_body:
            execution_script = str(value(config.get("step_execution_script")) or "")
            summary["step_execution_script"] = _compact_value(execution_script, 1000)
        return summary

    def _dedupe_inputs(self, inputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in inputs:
            key = (
                str(item.get("storage_table") or ""),
                str(item.get("input_sys_id") or ""),
                str(item.get("value_field") or ""),
                str(item.get("value_path") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def match_input(self, inputs: List[Dict[str, Any]], input_name_or_label: str) -> Dict[str, Any]:
        """Handle match input.`r`n        `r`n        This helper supports MCP workflow operations and returns normalized results.`r`n        """
        requested = comparable_key(input_name_or_label)
        user_reference_requested = requested in {"user", "userid", "username", "impersonateuser"}
        matches = [
            item
            for item in inputs
            if requested
            in {comparable_key(item.get("name") or ""), comparable_key(item.get("label") or "")}
            or (
                user_reference_requested
                and str(item.get("reference_table") or "").casefold() == "sys_user"
                and comparable_key(item.get("label") or item.get("name") or "")
                in {"user", "impersonateuser"}
            )
        ]
        if not matches:
            matches = [
                item
                for item in inputs
                if requested in comparable_key(item.get("name") or "")
                or requested in comparable_key(item.get("label") or "")
            ]
        if len(matches) == 1:
            return {"status": "success", "input": matches[0]}
        if len(matches) > 1:
            return {"status": "blocked", "reason": "multiple_input_matches", "candidates": matches}
        return {"status": "blocked", "reason": "input_not_found", "candidates": []}

    def read_input_current_value(self, input_ref: Dict[str, Any]) -> Dict[str, Any]:
        """Handle read input current value.`r`n        `r`n        This helper supports MCP workflow operations and returns normalized results.`r`n        """
        field = input_ref["value_field"]
        record = self._get_record(
            input_ref["storage_table"],
            input_ref["input_sys_id"],
            ["sys_id", field, *DISPLAY_FIELDS],
        )
        if input_ref.get("value_path"):
            mapping = _parse_mapping(record.get(field))
            raw = mapping.get(input_ref["value_path"])
            return {
                "raw_value": raw,
                "display_value": str(raw) if raw not in (None, "") else None,
                "record": record,
            }
        raw = _raw_value(record, field)
        return {"raw_value": raw, "display_value": _display_value(record, field), "record": record}

    def update_input_value(self, input_ref: Dict[str, Any], new_raw_value: Any) -> Dict[str, Any]:
        """Update input value.`r`n        `r`n        This helper supports MCP workflow operations and returns normalized results.`r`n        """
        table = input_ref["storage_table"]
        sys_id = input_ref["input_sys_id"]
        field = input_ref["value_field"]
        if input_ref.get("value_path"):
            current = self._get_record(table, sys_id, ["sys_id", field])
            mapping = _parse_mapping(current.get(field))
            mapping[input_ref["value_path"]] = new_raw_value
            existing_raw = value(current.get(field))
            payload_value: Any = (
                mapping if isinstance(existing_raw, dict) else json.dumps(mapping, sort_keys=True)
            )
        else:
            payload_value = new_raw_value
        return self.client.update_record(table, sys_id, {field: payload_value})

    def _get_record(self, table: str, sys_id: str, fields: List[str]) -> Dict[str, Any]:
        try:
            return self.client.get_record(table, sys_id, fields=fields)
        except TypeError:
            return self.client.get_record(table, sys_id)

    def _table_fields(self, table: str, max_fields: int) -> List[str]:
        records = self.client.query_records(
            "sys_dictionary",
            f"name={safe_query(table)}",
            fields=["element"],
            limit=max_fields,
        )
        fields = [
            str(value(record.get("element"))) for record in records if value(record.get("element"))
        ]
        if fields:
            return fields
        return self.client.query_table_fields(table)[:max_fields]

    def _candidate_fields(self, fields: set[str]) -> List[str]:
        candidates = [
            "sys_id",
            *STEP_RELATION_FIELDS,
            *CONFIG_RELATION_FIELDS,
            *DEFINITION_REFERENCE_FIELDS,
            *NAME_FIELDS,
            *LABEL_FIELDS,
            *VALUE_FIELDS,
            *DISPLAY_FIELDS,
            *REFERENCE_TABLE_FIELDS,
            *TYPE_FIELDS,
            *MANDATORY_FIELDS,
        ]
        return list(dict.fromkeys(field for field in candidates if field in fields))

    def _input_storage_tables(
        self, max_related_records: int = 50, max_fields: int = 80
    ) -> List[str]:
        tables = list(KNOWN_ATF_INPUT_TABLES)
        try:
            records = self.client.query_records(
                "sys_db_object",
                "nameLIKEsys_atf^ORname=sys_variable_value",
                fields=["name"],
                limit=max_related_records,
            )
        except Exception:
            records = []
        for record in records:
            table_name = str(value(record.get("name")) or "")
            if (
                "input" in table_name
                or "parameter" in table_name
                or "variable" in table_name
                or "value" in table_name
                or "impersonat" in table_name
            ):
                if table_name not in tables:
                    tables.append(table_name)
        available = []
        for table in tables:
            try:
                if self.client.check_table_exists(table):
                    available.append(table)
            except Exception:
                continue
        return available

    def _discover_config_input_definitions(
        self,
        step_config_id: str,
        include_raw: bool,
        max_related_records: int,
        max_fields: int,
        warnings: List[str],
        evidence: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        definitions: Dict[str, Dict[str, Any]] = {}
        if not step_config_id:
            return definitions
        for table in self._input_storage_tables(max_related_records, max_fields):
            try:
                fields = set(self._table_fields(table, max_fields))
            except Exception as exc:
                warnings.append(f"Could not inspect config definition table {table}: {exc}")
                continue
            relation_fields = [field for field in CONFIG_RELATION_FIELDS if field in fields]
            if not relation_fields:
                continue
            for relation_field in relation_fields:
                try:
                    records = self.client.query_records(
                        table,
                        f"{relation_field}={safe_query(step_config_id)}",
                        fields=self._candidate_fields(fields),
                        limit=max_related_records,
                    )
                except Exception as exc:
                    warnings.append(f"Could not query {table}.{relation_field}: {exc}")
                    continue
                for record in records:
                    definition_id = str(value(record.get("sys_id")) or "")
                    if not definition_id:
                        continue
                    name = _first(record, [field for field in NAME_FIELDS if field in fields])
                    label = _first(record, [field for field in LABEL_FIELDS if field in fields])
                    definitions[definition_id] = {
                        "sys_id": definition_id,
                        "table": table,
                        "name": str(name or label or definition_id),
                        "label": str(label or name or ""),
                        "reference_table": _first(
                            record, [field for field in REFERENCE_TABLE_FIELDS if field in fields]
                        ),
                        "raw": _compact_raw(record, include_raw),
                    }
                if records:
                    evidence.append(
                        {
                            "strategy": "config_input_definitions",
                            "table": table,
                            "field": relation_field,
                            "count": len(records),
                        }
                    )
        return definitions

    def _discover_values_by_config_definition(
        self,
        table: str,
        fields: set[str],
        step_sys_id: str,
        definitions: Dict[str, Dict[str, Any]],
        include_raw: bool,
        max_related_records: int,
        warnings: List[str],
        evidence: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not definitions:
            return []
        relation_fields = [field for field in STEP_RELATION_FIELDS if field in fields]
        definition_fields = [field for field in DEFINITION_REFERENCE_FIELDS if field in fields]
        if not relation_fields or not definition_fields:
            return []
        inputs = []
        seen: set[tuple[str, str]] = set()
        for relation_field in relation_fields:
            for definition_field in definition_fields:
                for definition_id in definitions:
                    query = (
                        f"{relation_field}={safe_query(step_sys_id)}"
                        f"^{definition_field}={safe_query(definition_id)}"
                    )
                    try:
                        records = self.client.query_records(
                            table,
                            query,
                            fields=self._candidate_fields(fields),
                            limit=max_related_records,
                        )
                    except Exception as exc:
                        warnings.append(
                            f"Could not query {table} by {relation_field}+{definition_field}: {exc}"
                        )
                        continue
                    for record in records:
                        row_id = str(value(record.get("sys_id")) or "")
                        key = (table, row_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        inputs.append(
                            self._input_from_related_record(
                                table,
                                relation_field,
                                record,
                                fields,
                                include_raw,
                                definitions,
                            )
                        )
                    if records:
                        evidence.append(
                            {
                                "strategy": "step_value_by_config_definition",
                                "table": table,
                                "step_field": relation_field,
                                "definition_field": definition_field,
                                "count": len(records),
                            }
                        )
        return inputs

    def _inputs_from_step_config_payloads(
        self,
        step_sys_id: str,
        step_config: Dict[str, Any],
        include_raw: bool,
        evidence: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        inputs = []
        for field in ("inputs", "input_variables", "variables", "parameters"):
            mapping = _parse_mapping(step_config.get(field))
            for key, definition in mapping.items():
                if isinstance(definition, dict) and "value" in definition:
                    current_value = definition.get("value")
                    inputs.append(
                        ATFStepInputRef(
                            input_sys_id=step_sys_id,
                            storage_table="sys_atf_step",
                            name=str(key),
                            label=str(definition.get("label") or definition.get("name") or key),
                            type=str(definition.get("type") or "json_value"),
                            value_field=field,
                            value_path=f"{key}.value",
                            current_raw_value=current_value,
                            current_display_value=(
                                str(current_value) if current_value not in (None, "") else None
                            ),
                            reference_table=str(
                                definition.get("reference_table")
                                or _reference_hint(str(key), str(key))
                                or ""
                            ),
                            source_relationship=f"sys_atf_step_config.{field}:definition_payload",
                            raw=_compact_raw(step_config, include_raw),
                        ).model_dump()
                    )
                    evidence.append(
                        {
                            "strategy": "step_config_payload_with_value",
                            "table": "sys_atf_step_config",
                            "field": field,
                        }
                    )
        return inputs

    def _input_from_related_record(
        self,
        table: str,
        relation_field: str,
        record: Dict[str, Any],
        fields: set[str],
        include_raw: bool,
        definitions: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        definitions = definitions or {}
        name = _first(record, [field for field in NAME_FIELDS if field in fields])
        label = _first(record, [field for field in LABEL_FIELDS if field in fields])
        definition_field = next(
            (
                field
                for field in DEFINITION_REFERENCE_FIELDS
                if field in fields and record.get(field)
            ),
            None,
        )
        definition_id = str(value(record.get(definition_field)) or "") if definition_field else ""
        definition_display = (
            str(display(record.get(definition_field)) or "") if definition_field else ""
        )
        definition = definitions.get(definition_id, {})
        if definition_id and not definition:
            definition = self._lookup_definition_by_sys_id(definition_id, include_raw)
        name = name or definition.get("name") or definition_display
        label = label or definition.get("label") or definition_display
        value_field = next((field for field in VALUE_FIELDS if field in fields), "value")
        display_field = next((field for field in DISPLAY_FIELDS if field in fields), value_field)
        reference_table = _first(
            record, [field for field in REFERENCE_TABLE_FIELDS if field in fields]
        ) or definition.get("reference_table")
        return ATFStepInputRef(
            input_sys_id=str(value(record.get("sys_id")) or ""),
            storage_table=table,
            name=str(name or label or value(record.get("sys_id")) or ""),
            label=str(label or name or ""),
            type=str(_first(record, [field for field in TYPE_FIELDS if field in fields]) or ""),
            value_field=value_field,
            current_raw_value=_raw_value(record, value_field),
            current_display_value=_display_value(record, display_field),
            reference_table=str(
                reference_table or _reference_hint(str(name or ""), str(label or "")) or ""
            ),
            mandatory=_bool_or_none(
                _first(record, [field for field in MANDATORY_FIELDS if field in fields])
            ),
            source_relationship=f"{table}.{relation_field}",
            raw=_compact_raw(record, include_raw),
        ).model_dump()

    def _lookup_definition_by_sys_id(self, definition_id: str, include_raw: bool) -> Dict[str, Any]:
        for table in (
            "sys_atf_step_config_input",
            "sys_atf_step_config_variable",
            "sys_atf_step_config_parameter",
            "sys_atf_parameter",
            "item_option_new",
            "question",
        ):
            try:
                if not self.client.check_table_exists(table):
                    continue
                record = self._get_record(
                    table,
                    definition_id,
                    ["sys_id", *NAME_FIELDS, *LABEL_FIELDS, *REFERENCE_TABLE_FIELDS, *TYPE_FIELDS],
                )
            except Exception:
                continue
            if not record:
                continue
            name = _first(record, NAME_FIELDS)
            label = _first(record, LABEL_FIELDS)
            return {
                "sys_id": definition_id,
                "table": table,
                "name": str(name or label or definition_id),
                "label": str(label or name or ""),
                "reference_table": _first(record, REFERENCE_TABLE_FIELDS),
                "raw": _compact_raw(record, include_raw),
            }
        return {}
