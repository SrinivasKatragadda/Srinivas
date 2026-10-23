"""Internal MCP tools for governed CMDB and CI operations through ServiceNow IRE only.

This module provides high-governance tooling for Configuration Item (CI) and relationship
ingestion into ServiceNow's Configuration Management Database (CMDB) using the IRE
(Identify, Reconcile, Enrich) API. Key features include:

1. Strict CMDB/CSDM class validation: Only approved CI classes and relationships allowed
2. Data governance: Enforces stable identifiers, source tracking, and audit trails
3. Preflight validation: Tests data before committing to prevent bad data ingestion
4. Batch processing: Managed ingestion with chunking, error handling, and reporting
5. Relationship management: CSDM-compliant relationship creation between CIs
6. Governance reporting: Health dashboards, compliance checks, and remediation guidance

Workflow patterns:
- Use instance_preflight() to validate data source connectivity
- Use validate_ci_payload() for data quality checks before commit
- Use upsert_ci() for standard CI ingestion with optional dry-run
- Use managed_ingestion() for large batch operations with error recovery
- Use governance_insight() for compliance validation and recommendations

Security model:
- Direct Table API CMDB mutations are blocked (use IRE only)
- All operations require explicit data_source tracking
- Audit trail captured for all changes (transaction_id, elapsed time, operation summary)
- Strict CSDM validation prevents invalid CI class ingestion
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections import Counter
from copy import copy
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, model_validator

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.internal_tools.cmdb_ire_client import (
    ire_commit,
    ire_commit_enhanced,
    ire_preflight,
    ire_query,
    ire_query_enhanced,
)
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

# Namespace identifier for this tool module (used for feature flagging, metrics, audit)
NAMESPACE = "servicenow_internal_cmdb"
# Error message shown when CMDB mutations are attempted via Table API (should use IRE instead)
TABLE_API_BLOCK_MESSAGE = (
    "CMDB/CI mutation is blocked in Table API path. Use servicenow_internal_cmdb IRE tools."
)
# CMDB table names that are subject to IRE-only mutation governance. Requests targeting these
# tables will be routed to IRE tools instead of allowing direct Table API mutations
CMDB_TABLE_TARGETS = (
    "cmdb_ci",  # Base CI table
    "cmdb_rel_ci",  # CI relationships table
    "cmdb_ci_service",  # Service CI
    "cmdb_ci_business_app",  # Business application CI
    "service_offering",  # Service offering table
)

# Regex pattern for detecting CMDB-related requests in natural language or task descriptions.
# Matches CI terminology, CMDB concepts, relationship types, and infrastructure terms.
# Used to route user requests to appropriate CMDB governance tools.
CMDB_ROUTE_PATTERN = re.compile(
    r"\b("  # Word boundary
    r"ci|cmdb|cmdb_ci|cmdb_ci_[a-z0-9_]+|configuration item|csdm|business application|"  # CI types
    r"business service|application service|technical service|service offering|relationship|"  # Service types
    r"dependency|hosted on|runs on|depends on|contains|manages|owned by|used by|"  # Relationships
    r"service mapping|infrastructure ci|server|vm|database|network gear|application|"  # Infrastructure
    r"service|endpoint|cluster"  # Endpoints and clusters
    r")\b",  # Word boundary
    re.IGNORECASE,  # Case-insensitive matching
)

# Mapping of user-friendly or alternate CI class names to canonical ServiceNow CMDB class names.
# Allows flexible input while normalizing to the authoritative class names used by ServiceNow.
# Example: "linux_server" normalizes to "cmdb_ci_linux_server"
CLASS_ALIASES = {
    # Server types
    "linux_server": "cmdb_ci_linux_server",
    "windows_server": "cmdb_ci_win_server",
    # Application types
    "business_app": "cmdb_ci_business_app",
    "business_application": "cmdb_ci_business_app",
    # Service types with auto-discovery hints
    "business_service": "cmdb_ci_service_business",
    "application_service_auto": "cmdb_ci_service_auto",
    "application_service_discovered": "cmdb_ci_service_discovered",
    "technical_service": "cmdb_ci_service_technical",
}

# Whitelist of CI classes approved for CSDM (Common Service Definition Model) operations.
# These classes align with ServiceNow's service mapping and configuration standards.
# Only these classes are allowed for ingestion via this tool to maintain governance.
APPROVED_CSDM_CLASSES = {
    # Service-level CIs
    "cmdb_ci_service",
    "cmdb_ci_service_business",  # Business service
    "cmdb_ci_service_auto",  # Auto-discovered service
    "cmdb_ci_service_discovered",  # Discovery-sourced service
    "cmdb_ci_service_technical",  # Technical service
    # Application CIs
    "cmdb_ci_business_app",  # Business application
    "cmdb_ci_appl",  # Generic application
    # Infrastructure endpoint
    "cmdb_ci_endpoint",
    # Service offering
    "service_offering",
}

# Fields that can serve as stable, unique identifiers for CIs.
# IRE uses these to identify which CI already exists vs which is new.
# Items must have at least one of these fields populated (or source_native_key).
STABLE_IDENTIFIER_FIELDS = {
    "serial_number",  # Hardware serial number
    "fqdn",  # Fully qualified domain name (FQDN)
    "name",  # CI name
    "asset_tag",  # Asset tracking tag
    "correlation_id",  # External correlation ID
    "object_id",  # Object identifier in source system
    "ip_address",  # IP address for network CIs
    "dns_domain",  # DNS domain name
}

# CSDM relationship rules: Define which CI classes can be related via specific relationship types.
# Each rule has parent and child class lists; relationships are validated against these.
# Used for strict CSDM validation to prevent invalid CI relationships.
RELATIONSHIP_RULES = {
    # Dependency relationships: child depends on parent service/application
    "depends on": {
        "parent": ("cmdb_ci_business_app", "cmdb_ci_service_auto", "cmdb_ci_service_discovered"),
        "child": ("cmdb_ci_service", "cmdb_ci_service_technical", "cmdb_ci_server", "cmdb_ci_"),
    },
    # Execution relationships: application runs on infrastructure
    "runs on": {
        "parent": ("cmdb_ci_appl", "cmdb_ci_service_auto", "cmdb_ci_service_discovered"),
        "child": (
            "cmdb_ci_server",
            "cmdb_ci_linux_server",
            "cmdb_ci_win_server",
            "cmdb_ci_vm",
            "cmdb_ci_cluster",
        ),
    },
    # Hosting relationships: application hosted on infrastructure
    "hosted on": {
        "parent": ("cmdb_ci_appl", "cmdb_ci_service_auto", "cmdb_ci_service_discovered"),
        "child": ("cmdb_ci_server", "cmdb_ci_vm", "cmdb_ci_cluster", "cmdb_ci_container"),
    },
    # Compositional relationships: service contains CI
    "contains": {"parent": ("cmdb_ci_service", "cmdb_ci_cluster"), "child": ("cmdb_ci_",)},
    # Generic management relationships
    "manages": {"parent": ("cmdb_ci_",), "child": ("cmdb_ci_",)},
    # Generic dependency relationships
    "uses": {"parent": ("cmdb_ci_",), "child": ("cmdb_ci_",)},
}


class CmdbSourceInfo(BaseModel):
    """Source tracking information for CMDB CI data ingestion.
    
    Tracks the external data source, native keys, and temporal information for
    each CI submitted through IRE. Used by ServiceNow for audit trails, reconciliation,
    and identifying duplicate/updated CIs from the same source.
    
    Attributes:
        source_name: Name of the external data source (e.g., 'splunk_cmdb', 'servicenow_discovery')
        source_native_key: Unique identifier for this CI in the external system
        source_feed: Optional categorization/grouping of the data feed (e.g., 'cmdb_sync')
        source_recency_timestamp: Optional timestamp indicating when the data was last updated
                                in the source system (ISO 8601 format)
    """
    source_name: str  # External data source identifier
    source_native_key: str  # Unique key in source system
    source_feed: Optional[str] = None  # Optional feed categorization
    source_recency_timestamp: Optional[str] = None  # Optional source recency timestamp


class CmdbCiItem(BaseModel):
    """Configuration Item (CI) for CMDB ingestion via IRE.
    
    Represents a single CI to be submitted to ServiceNow's CMDB through the IRE API.
    Contains all data needed for IRE to identify, reconcile, and persist the CI.
    
    Attributes:
        internal_id: Unique identifier for this CI within the submission (used for relating CIs)
        className: CMDB CI class name (e.g., 'cmdb_ci_linux_server', 'cmdb_ci_service')
        values: Dict of CI attributes/fields (e.g., {'name': 'web-app-01', 'fqdn': 'web01.prod'})
        sys_object_source_info: Source tracking information (data source, native key, recency)
        lookup: Optional list of lookups for reference field resolution
        related: Optional list of related CIs for batch processing
        settings: Optional dict of IRE-specific processing settings
    """
    internal_id: str  # Unique ID within submission batch
    className: str  # CI class name (validated against APPROVED_CSDM_CLASSES)
    values: Dict[str, Any]  # CI attribute values
    sys_object_source_info: CmdbSourceInfo  # Source tracking
    lookup: Optional[List[Any]] = None  # Reference lookups
    related: Optional[List[Any]] = None  # Related CIs
    settings: Optional[Dict[str, Any]] = None  # IRE processing settings


class CmdbRelation(BaseModel):
    """Relationship between two CIs in the CMDB.
    
    Defines how two CIs are related (e.g., 'runs on', 'depends on', 'contains').
    Can reference CIs by internal_id (within batch) or by sys_id (already in CMDB).
    
    Attributes:
        parent_id: Sys_id of parent CI if it already exists in CMDB
        child_id: Sys_id of child CI if it already exists in CMDB
        parent: Index of parent CI within items list (for batch relationships)
        child: Index of child CI within items list (for batch relationships)
        type: Relationship type (e.g., 'runs on', 'depends on', 'hosted on')
        sys_rel_source_info: Optional source tracking for the relationship itself
    """
    parent_id: Optional[str] = None  # Parent CI sys_id in CMDB
    child_id: Optional[str] = None  # Child CI sys_id in CMDB
    parent: Optional[int] = None  # Parent index in items list
    child: Optional[int] = None  # Child index in items list
    type: str  # Relationship type
    sys_rel_source_info: Optional[Dict[str, Any]] = None  # Relationship source info


class CmdbIreRequest(BaseModel):
    """Request model for CMDB CI ingestion via IRE.
    
    Contains all parameters needed to execute a CI ingestion operation:
    validation options, dry-run flags, commit settings, and governance rules.
    
    Attributes:
        data_source: External data source identifier (required, tracked in audit)
        items: List of CIs to ingest
        relations: Optional list of relationships between CIs
        referenceItems: Optional list of reference CIs for lookups
        enhanced: If True, use enhanced query/commit endpoints with advanced options
        enhanced_options: Optional dict of advanced IRE processing parameters
        dry_run_first: If True, perform query (dry-run) before committing
        commit: If True, persist changes to CMDB (final operation)
        strict_csdm_validation: If True, enforce CSDM class/relationship rules
        allow_incomplete_payload: If True, skip stable identifier validation
    """
    data_source: str  # External data source identifier
    items: List[CmdbCiItem]  # CIs to ingest
    relations: Optional[List[CmdbRelation]] = None  # CI relationships
    referenceItems: Optional[List[Dict[str, Any]]] = None  # Reference data
    enhanced: bool = False  # Use enhanced endpoints
    enhanced_options: Optional[Dict[str, Any]] = None  # Advanced options
    dry_run_first: bool = True  # Perform dry-run first
    commit: bool = False  # Persist to CMDB
    strict_csdm_validation: bool = True  # Enforce CSDM rules
    allow_incomplete_payload: bool = False  # Allow incomplete data


class CmdbIreResult(BaseModel):
    """Result/response model for CMDB CI ingestion operations.
    
    Returned after executing a CI ingestion request. Contains operation status,
    summary of changes, warnings/errors, and the raw response for debugging.
    
    Attributes:
        ok: True if operation succeeded (no fatal errors)
        dry_run: True if this was a dry-run (query) operation
        committed: True if changes were persisted to CMDB
        endpoint_used: IRE endpoint that was used (query, commit, enhanced, etc.)
        operation_summary: Dict with operation counts (CREATED, UPDATED, SKIPPED, etc.)
        items: List of processed item results from IRE
        relations: List of processed relationship results from IRE
        warnings: List of warning messages from processing
        errors: List of error messages (if any)
        raw_response: Full IRE API response for debugging
    """
    ok: bool  # Success flag
    dry_run: bool  # Was this a dry-run
    committed: bool  # Were changes committed
    endpoint_used: str  # Which IRE endpoint
    operation_summary: Dict[str, Any]  # Summary of operations
    items: List[Any]  # Processed items
    relations: List[Any]  # Processed relationships
    warnings: List[str]  # Warning messages
    errors: List[str]  # Error messages
    raw_response: Optional[Dict[str, Any]] = None  # Raw IRE response


class InstancePreflightParams(BaseModel):
    """Parameters for instance preflight validation tool.
    
    Validates that a ServiceNow instance is properly configured for CMDB CI ingestion
    via IRE with the specified data source. Performs connectivity, authentication, and
    data source setup checks without committing any data.
    
    Attributes:
        instance_url: ServiceNow instance URL (e.g., 'https://dev12345.service-now.com')
        username: Optional username for basic auth (if not using auth_profile_reference)
        auth_profile_reference: Optional reference to stored auth profile
        password: Optional password for basic auth
        token: Optional token for API key or OAuth auth
        secret_reference: Optional reference to stored credentials/secret
        data_source: Data source identifier to validate (must match external system)
        test_mode: If True, performs test without persisting (preflight check only)
    """
    instance_url: str  # ServiceNow instance URL
    username: Optional[str] = None  # Username for auth
    auth_profile_reference: Optional[str] = None  # Reference to auth profile
    password: Optional[str] = None  # Password for auth
    token: Optional[str] = None  # Token for auth
    secret_reference: Optional[str] = None  # Reference to stored secret
    data_source: str  # Data source to validate
    test_mode: bool = True  # Preflight mode (no persistence)


class ValidateCiPayloadParams(BaseModel):
    """Parameters for CI payload validation tool.
    
    Validates a batch of CIs for data quality, governance compliance, and readiness
    for ingestion. Performs schema validation, CSDM class checks, stable identifier
    verification, and relationship rule validation without committing to CMDB.
    
    Attributes:
        data_source: External data source identifier being validated
        items: List of CI dicts to validate (each dict has className, values, etc.)
        relations: Optional list of relationship dicts between CIs
        referenceItems: Optional list of reference CI data for lookups
        enhanced_options: Optional dict of advanced validation options
        strict_csdm_validation: If True, enforce strict CSDM class/relationship rules
        allow_incomplete_payload: If True, skip stable identifier validation
    """
    data_source: str  # External data source
    items: List[Dict[str, Any]]  # CIs to validate
    relations: Optional[List[Dict[str, Any]]] = None  # Relationships
    referenceItems: Optional[List[Dict[str, Any]]] = None  # Reference data
    enhanced_options: Optional[Dict[str, Any]] = None  # Advanced options
    strict_csdm_validation: bool = True  # Enforce CSDM rules
    allow_incomplete_payload: bool = False  # Allow incomplete data


class UpsertCiParams(ValidateCiPayloadParams):
    """Parameters for CI upsert (insert/update) operation tool.
    
    Extends ValidateCiPayloadParams with workflow control flags for performing
    dry-run queries and commits to CMDB. Supports enhanced endpoints for advanced
    reconciliation scenarios.
    
    Attributes (inherited from ValidateCiPayloadParams):
        data_source, items, relations, referenceItems, enhanced_options,
        strict_csdm_validation, allow_incomplete_payload
    
    Additional Attributes:
        dry_run_first: If True, perform query (dry-run) before committing
        commit: If True, persist changes to CMDB after validation
        enhanced: If True, use enhanced endpoints with advanced options
        skip_preflight: If True, skip instance preflight check
    """
    dry_run_first: bool = True  # Perform dry-run first
    commit: bool = False  # Persist to CMDB
    enhanced: bool = False  # Use enhanced endpoints
    skip_preflight: bool = False  # Skip preflight check


class CreateCsdmRelationshipParams(BaseModel):
    """Parameters for creating a CSDM-compliant CI relationship.
    
    Creates a relationship between two CIs with full CSDM governance and validation.
    Supports both dry-run and commit modes. Can create new CIs or relate to existing ones.
    
    Attributes:
        data_source: External data source identifier
        parent_ci: Parent CI identifier (internal_id or sys_id)
        child_ci: Child CI identifier (internal_id or sys_id)
        relationship_type: Type of relationship (e.g., 'runs on', 'depends on')
        parent_class: CMDB class for parent CI (validated against APPROVED_CSDM_CLASSES)
        child_class: CMDB class for child CI (validated against APPROVED_CSDM_CLASSES)
        parent_values: Dict of attributes for parent CI if creating new
        child_values: Dict of attributes for child CI if creating new
        source_native_keys: Dict with native keys in external system
        strict_csdm_validation: If True, enforce CSDM relationship rules
        dry_run_first: If True, perform dry-run before committing
        commit: If True, persist relationship to CMDB
    """
    data_source: str  # External data source
    parent_ci: str  # Parent CI ID
    child_ci: str  # Child CI ID
    relationship_type: str  # Relationship type
    parent_class: str  # Parent CI class
    child_class: str  # Child CI class
    parent_values: Dict[str, Any]  # Parent attributes
    child_values: Dict[str, Any]  # Child attributes
    source_native_keys: Dict[str, str]  # Native keys
    strict_csdm_validation: bool = True  # Enforce CSDM rules
    dry_run_first: bool = True  # Perform dry-run first
    commit: bool = False  # Persist to CMDB


class ManagedIngestionParams(ValidateCiPayloadParams):
    """Parameters for managed batch CI ingestion with error handling.
    
    For large CI batches, breaks payload into chunks with automatic retry, error
    recovery, and comprehensive reporting. Supports validation-only or full commit modes.
    
    Attributes (inherited from ValidateCiPayloadParams):
        data_source, items, relations, referenceItems, enhanced_options,
        strict_csdm_validation, allow_incomplete_payload
    
    Additional Attributes:
        batch_id: Unique identifier for this batch (for audit trail)
        mode: Operation mode: 'validate_only' or 'commit'
        chunk_size: Number of CIs per chunk (default 50)
        stop_on_error: If True, halt on first error; if False, continue with warnings
        generate_audit_report: If True, generate detailed audit report after batch
    """
    batch_id: str  # Batch identifier
    mode: Literal["validate_only", "commit"] = "validate_only"  # Operation mode
    chunk_size: int = 50  # CIs per chunk
    stop_on_error: bool = True  # Halt on error
    generate_audit_report: bool = True  # Generate audit report

    @model_validator(mode="after")
    def _validate_chunk_size(self) -> "ManagedIngestionParams":
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        return self


class GovernanceInsightParams(BaseModel):
    """Parameters for governance compliance and quality insights tool.
    
    Analyzes CI payload and previous IRE results for governance compliance, data quality issues,
    CSDM conformance, and provides remediation recommendations. Used for pre-commit validation
    and post-commit quality assurance.
    
    Attributes:
        payload: Optional IRE request payload to analyze
        previous_result: Optional result from previous IRE operation
        data_source: Data source identifier being analyzed
        expected_classes: Optional list of expected CI classes for validation
        required_fields_by_class: Optional dict mapping classes to required field lists
        csdm_mode: Validation strictness: 'strict' (fail on issues) or 'advisory' (warnings)
        include_recommendations: If True, provide remediation recommendations
    """
    payload: Optional[Dict[str, Any]] = None  # IRE payload
    previous_result: Optional[Dict[str, Any]] = None  # Previous result
    data_source: str  # Data source
    expected_classes: Optional[List[str]] = None  # Expected CI classes
    required_fields_by_class: Optional[Dict[str, List[str]]] = None  # Required fields
    csdm_mode: Literal["strict", "advisory"] = "strict"  # Validation mode
    include_recommendations: bool = True  # Include recommendations


class HealthDashboardParams(BaseModel):
    """Parameters for ingestion health dashboard generation.
    
    Generates a comprehensive health dashboard summarizing CI ingestion results,
    governance compliance status, and operation metrics. Useful for post-commit
    reporting and stakeholder communication.
    
    Attributes:
        ingestion_result: Result from upsert_ci() or managed_ingestion() operation
        governance_result: Result from governance_insight() analysis
        include_charts_data: If True, include structured data for chart generation
    """
    ingestion_result: Dict[str, Any]  # Ingestion result
    governance_result: Dict[str, Any]  # Governance analysis result
    include_charts_data: bool = True  # Include chart data


def route_cmdb_ci_request(user_request: str, parsed_task: dict) -> bool:
    """Determine if a user request should be routed to CMDB governance tools.
    
    Uses pattern matching against CMDB_ROUTE_PATTERN to detect CMDB-related terminology
    in natural language requests. Used by the MCP coordinator to route requests to the
    servicenow_internal_cmdb namespace tools instead of generic Table API tools.
    
    Args:
        user_request: Natural language request from user (e.g., 'Create a new CI for our server')
        parsed_task: Dict of parsed task metadata from LLM task breakdown
    
    Returns:
        True if request matches CMDB patterns and should be routed to CMDB tools,
        False if generic table operations should be used instead.
    
    Examples:
        route_cmdb_ci_request('Create a business service CI', {}) -> True
        route_cmdb_ci_request('Update my incident', {}) -> False
    """
    # Combine user request and parsed task metadata into single searchable text
    haystack = f"{user_request or ''} {parsed_task or {}}"
    # Search for CMDB-related patterns in the combined text
    return bool(CMDB_ROUTE_PATTERN.search(haystack))


def is_cmdb_table_api_mutation(table_or_payload: Any) -> bool:
    """Detect if a table mutation request targets CMDB tables.
    
    Checks if the table name or payload contains references to CMDB tables.
    Used to block direct Table API mutations against CMDB tables and route
    them through IRE governance tools instead.
    
    Args:
        table_or_payload: Table name (str) or payload dict to check
    
    Returns:
        True if any CMDB_TABLE_TARGETS (cmdb_ci, cmdb_rel_ci, etc.) are found,
        False if no CMDB tables are targeted.
    
    Examples:
        is_cmdb_table_api_mutation('cmdb_ci') -> True
        is_cmdb_table_api_mutation('incident') -> False
        is_cmdb_table_api_mutation({'table': 'cmdb_ci_service'}) -> True
    """
    # Convert input to lowercase string for case-insensitive matching
    text = str(table_or_payload).lower()
    # Check if any CMDB table target appears in the text
    return any(term in text for term in CMDB_TABLE_TARGETS)


def has_explicit_cmdb_mutation_target(payload: Any) -> bool:
    """Check if payload has explicit CMDB mutation targets in known keys.
    
    More strict than is_cmdb_table_api_mutation() - only returns True if CMDB
    tables are explicitly specified in known mutation target keys like 'table',
    'className', 'target_table', etc. Used to distinguish between generic
    references to CMDB terminology vs explicit mutation intent.
    
    Args:
        payload: Dict with potential mutation targets, list of items, or any object
    
    Returns:
        True if explicit CMDB table/class keys are found in payload,
        False if no explicit mutation targets are detected.
    
    Logic:
        - Searches dict keys: table, className, target_table, relationship_table, etc.
        - Recursively checks nested dicts and lists
        - Only matches if key is in known mutation keys AND value targets CMDB
    """
    if isinstance(payload, dict):
        # Known keys that indicate mutation target specification
        target_keys = {
            "table",
            "table_name",
            "class",
            "className",
            "class_name",
            "target_table",
            "relationship_table",
        }
        for key, value in payload.items():
            # Check if key is a mutation target and value targets CMDB
            if key in target_keys and is_cmdb_table_api_mutation(value):
                return True
            # Recursively check nested structures
            if isinstance(value, (dict, list)) and has_explicit_cmdb_mutation_target(value):
                return True
    elif isinstance(payload, list):
        # Recursively check all items in list
        return any(has_explicit_cmdb_mutation_target(value) for value in payload)
    return False


def _normalize_class(class_name: str, values: Optional[Dict[str, Any]] = None) -> str:
    """Normalize user-provided CI class name to canonical ServiceNow class name.
    
    Applies CLASS_ALIASES mappings and handles special cases for service classes
    that require discovery source hints. Used during payload building to ensure
    all CIs are classified with authorized CMDB class names.
    
    Special Logic:
    - 'application_service' + discovery_source hint -> cmdb_ci_service_discovered
    - 'application_service' without hint -> cmdb_ci_service_auto
    - 'service_offering' -> service_offering (special case, may not be supported)
    
    Args:
        class_name: User-provided class name (may be alias or full name)
        values: Optional dict of CI attributes (checked for discovery_source)
    
    Returns:
        Normalized/canonical class name (e.g., 'cmdb_ci_linux_server')
    
    Examples:
        _normalize_class('linux_server') -> 'cmdb_ci_linux_server'
        _normalize_class('application_service', {'discovery_source': 'splunk'})
        -> 'cmdb_ci_service_discovered'
    """
    # Clean and normalize input class name
    cleaned = (class_name or "").strip()
    
    # Special case: application_service requires discovery hint to distinguish variants
    if cleaned == "application_service":
        # Check if CI data includes discovery_source hint
        discovery_hint = str((values or {}).get("discovery_source", "")).lower()
        # Use discovered variant if hint present, otherwise use auto-discovered variant
        return "cmdb_ci_service_discovered" if discovery_hint else "cmdb_ci_service_auto"
    
    # Special case: service_offering (may have governance restrictions)
    if cleaned == "service_offering":
        return "service_offering"
    
    # Apply alias mappings for friendly names, fallback to original if no alias
    return CLASS_ALIASES.get(cleaned, cleaned)


def _class_allowed(class_name: str) -> bool:
    """Check if a CI class name is approved for CMDB ingestion via IRE.
    
    Validates class against governance whitelist. Only CMDB-prefixed classes
    and approved CSDM classes are allowed to prevent invalid CI types from being
    ingested into the CMDB.
    
    Args:
        class_name: CI class name to validate
    
    Returns:
        True if class is allowed (cmdb_ci* OR in APPROVED_CSDM_CLASSES),
        False if class is not approved.
    
    Examples:
        _class_allowed('cmdb_ci_linux_server') -> True
        _class_allowed('cmdb_ci_service_business') -> True
        _class_allowed('cmdb_ci_invalid') -> False (unless explicitly approved)
    """
    # Allow any class that starts with 'cmdb_ci' OR is in the approved list
    return class_name.startswith("cmdb_ci") or class_name in APPROVED_CSDM_CLASSES


def _has_stable_identifier(item: CmdbCiItem) -> bool:
    """Check if a CI item has at least one stable identifier for reconciliation.
    
    IRE uses stable identifiers to determine if a CI already exists in CMDB
    (update) vs is new (create). At least one identifier must be present:
    internal_id, source_native_key, or one of STABLE_IDENTIFIER_FIELDS.
    
    Args:
        item: CmdbCiItem to validate for stable identifier
    
    Returns:
        True if item has internal_id, source_native_key, or at least one
        stable identifier field (serial_number, fqdn, name, etc.),
        False if no stable identifier is found.
    
    Note:
        - Checks are: non-None and non-empty string
        - Validating this early prevents bad data ingestion
        - Can be skipped if allow_incomplete_payload=True
    """
    # Check for explicit internal_id or source_native_key first (highest priority)
    if item.internal_id or item.sys_object_source_info.source_native_key:
        return True
    # Check if any stable identifier field has a non-empty value
    return any(
        item.values.get(field) not in (None, "") 
        for field in STABLE_IDENTIFIER_FIELDS
    )


def _model_dump(model: Any) -> Dict[str, Any]:
    """Safely serialize a Pydantic model or dict to dict, excluding None values.
    
    Compatibility wrapper that works with both Pydantic v1 and v2 APIs.
    Used for converting Pydantic models to plain dicts for JSON serialization.
    
    Args:
        model: Pydantic model instance or dict
    
    Returns:
        Dict with all fields, None values excluded (exclude_none=True)
    """
    # Try v2 API first (model_dump), fallback to v1 API (dict)
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


def _coerce_request(raw: CmdbIreRequest | Dict[str, Any]) -> CmdbIreRequest:
    """Normalize input to CmdbIreRequest type, converting dicts if needed.
    
    Used by public functions to accept either CmdbIreRequest objects or
    plain dicts, normalizing to the model type for type safety.
    
    Args:
        raw: CmdbIreRequest instance or dict to convert
    
    Returns:
        CmdbIreRequest instance (either returned as-is or constructed from dict)
    
    Raises:
        ValidationError: If dict doesn't have required fields for CmdbIreRequest
    """
    # If already correct type, return as-is
    if isinstance(raw, CmdbIreRequest):
        return raw
    # Otherwise construct from dict
    return CmdbIreRequest(**raw)


def _config_for_instance(config: ServerConfig, instance_url: str) -> ServerConfig:
    """Create or return a ServerConfig for a specific instance URL.
    
    If the provided instance URL matches the config's current URL, returns the
    config as-is. Otherwise creates a copy with the new instance URL. Handles
    both Pydantic v1 and v2 copy mechanisms.
    
    Args:
        config: Current ServerConfig
        instance_url: Target instance URL to use
    
    Returns:
        ServerConfig with instance_url set to target (either original or new copy)
    """
    # Normalize URLs by removing trailing slashes for comparison
    cleaned_url = instance_url.rstrip("/")
    
    # If URL matches, return original config
    if cleaned_url == config.instance_url.rstrip("/"):
        return config
    
    # Try Pydantic v2 API first (model_copy)
    if hasattr(config, "model_copy"):
        return config.model_copy(update={"instance_url": cleaned_url})
    
    # Fallback to v1 API (copy)
    if hasattr(config, "copy"):
        return config.copy(update={"instance_url": cleaned_url})
    
    # Last resort: manual copy and update
    cloned = copy(config)
    cloned.instance_url = cleaned_url
    return cloned


def build_ire_payload(request: CmdbIreRequest) -> dict:
    """Build a sanitized IRE API payload from a CmdbIreRequest with full validation.
    
    Core validation and transformation function that:
    1. Validates each CI: class name, values, stable identifiers
    2. Normalizes CI class names using CLASS_ALIASES
    3. Checks CSDM governance: approved classes, required fields
    4. Constructs IRE-compliant payload structure
    
    Validation Rules (when strict_csdm_validation=True):
    - Each CI must have values (non-empty dict)
    - Each CI must have stable identifier (unless allow_incomplete_payload=True)
    - Each CI class must be approved (APPROVED_CSDM_CLASSES or cmdb_ci*)
    - service_offering class raises error (unsupported without explicit approval)
    - source_native_key must be present (unless allow_incomplete_payload=True)
    
    Args:
        request: CmdbIreRequest with items, relations, and validation options
    
    Returns:
        Dict with 'items', 'relations' (if any), 'referenceItems' (if any)
        Ready to pass to IRE API endpoints.
    
    Raises:
        ValueError: On validation failures:
            - Missing or invalid data_source
            - CI values empty
            - No stable identifier found
            - Unsupported class (service_offering)
            - Class not approved
            - Missing source_native_key
    
    Note:
        - Normalizes all class names through _normalize_class()
        - source_name defaults to data_source if not explicit
        - internal_id auto-generated as f'item_{index}' if not provided
    """
    # Normalize request to ensure it's a proper CmdbIreRequest object
    req = _coerce_request(request)
    
    # Validate data_source is present (required for IRE operations)
    if not req.data_source:
        raise ValueError("data_source is required and is sent as sysparm_data_source")

    # Build items list with full validation for each CI
    items: List[Dict[str, Any]] = []
    for index, item in enumerate(req.items):
        # Normalize the CI class name (applies aliases and discovery logic)
        class_name = _normalize_class(item.className, item.values)
        
        # Check for unsupported service_offering class
        if class_name == "service_offering":
            raise ValueError(
                "service_offering IRE mutation support is instance-dependent and unsupported by "
                "this tool layer without an explicit approved class mapping"
            )
        
        # Validate class is in approved list
        if not _class_allowed(class_name):
            raise ValueError(f"Unsupported CMDB/CSDM class for IRE: {class_name}")
        
        # Validate item has values
        if not item.values:
            raise ValueError(f"Item {index} values cannot be empty")
        
        # Validate stable identifier exists (can be skipped if allow_incomplete_payload)
        if not _has_stable_identifier(item) and not req.allow_incomplete_payload:
            raise ValueError(f"Item {index} has no stable identifier")

        # Extract source info (use defaults if not explicit)
        source = item.sys_object_source_info
        source_name = source.source_name or req.data_source
        source_native_key = source.source_native_key or item.internal_id
        
        # Validate source_native_key exists (can be skipped if allow_incomplete_payload)
        if not source_native_key and not req.allow_incomplete_payload:
            raise ValueError(f"Item {index} is missing source_native_key")
        
        # Build IRE item payload structure
        body = {
            "className": class_name,  # Normalized class name
            "internal_id": item.internal_id or f"item_{index}",  # Auto-generate if missing
            "values": dict(item.values),  # CI attribute values
            "sys_object_source_info": {
                "source_name": source_name,  # External data source
                "source_native_key": source_native_key,  # Unique ID in source
                "source_feed": source.source_feed,  # Optional feed identifier
                "source_recency_timestamp": source.source_recency_timestamp,  # Optional timestamp
            },
        }
        
        # Add optional nested structures (lookup, related, settings) if present
        for key in ("lookup", "related", "settings"):
            value = getattr(item, key)
            if value is not None:
                body[key] = value
        
        # Filter out None values and add to items list
        items.append({k: v for k, v in body.items() if v is not None})

    # Build final payload dict
    payload: Dict[str, Any] = {"items": items}
    
    # Add optional sections if present in request
    if req.referenceItems:
        payload["referenceItems"] = req.referenceItems
    if req.relations:
        payload["relations"] = [_model_dump(relation) for relation in req.relations]
    
    return payload


def _parse_ire_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse IRE API response and aggregate operation results with error handling.
    
    Core aggregation function that processes IRE endpoint responses (both successful
    and error states) and normalizes them into a consistent result structure. Handles
    multiple response format variants from ServiceNow IRE API (items vs itemResults,
    relations vs relationResults, etc.).
    
    Parsing Strategy:
    1. Extract base result object (handles nested 'result' wrapper)
    2. Identify items and relations (supports multiple field naming conventions)
    3. Iterate through results: aggregate operation types (CREATED, UPDATED, SKIPPED)
    4. Collect warnings and errors from multiple sources
    5. Return normalized summary with counts and detailed lists
    
    Response Format Handling:
    - Items/itemResults: CI operation results
    - Relations/relationResults: Relationship operation results
    - Supports nested warnings/errors in items vs top-level warnings/errorMessages
    - Handles both structured and flat response formats
    
    Args:
        response: Raw response from IRE API endpoint, may contain nested 'result' wrapper
    
    Returns:
        Dict with normalized structure containing:
        - operation_summary: Counter with CREATED/UPDATED/SKIPPED/etc counts
        - items: List of processed item results
        - relations: List of processed relation results
        - warnings: Aggregated warning messages
        - errors: Aggregated error messages
    
    Note:
        - Handles 4 variations of operation field names: operation, operationType, status, action
        - Handles 6 variations of warning/error field names for robustness
        - Safe for empty or null collections (returns empty lists)
    """
    result = response.get("result", response)
    raw_items = result.get("items") or result.get("itemResults") or []
    raw_relations = result.get("relations") or result.get("relationResults") or []
    warnings: List[str] = []
    errors: List[str] = []
    summary: Counter[str] = Counter()

    def consume(entry: Dict[str, Any]) -> None:
        """Consume one item/relation response entry into summary buckets.

        Aggregates operation type counters while collecting warning/error strings
        across supported response field-name variants.
        """
        operation = (
            entry.get("operation")
            or entry.get("operationType")
            or entry.get("status")
            or entry.get("action")
            or "NO_CHANGE"
        )
        summary[str(operation).upper()] += 1
        for key in ("warnings", "warning", "warningMessages"):
            value = entry.get(key)
            if isinstance(value, list):
                warnings.extend(str(v) for v in value)
            elif value:
                warnings.append(str(value))
        for key in ("errors", "error", "errorMessages"):
            value = entry.get(key)
            if isinstance(value, list):
                errors.extend(str(v) for v in value)
            elif value:
                errors.append(str(value))

    for collection in (raw_items, raw_relations):
        if isinstance(collection, list):
            for entry in collection:
                if isinstance(entry, dict):
                    consume(entry)

    for key in ("warnings", "warningMessages"):
        value = result.get(key)
        if isinstance(value, list):
            warnings.extend(str(v) for v in value)
    for key in ("errors", "errorMessages"):
        value = result.get(key)
        if isinstance(value, list):
            errors.extend(str(v) for v in value)

    if not summary and result.get("summary"):
        summary.update({str(k).upper(): v for k, v in result["summary"].items()})

    return {
        "operation_summary": dict(summary),
        "items": raw_items if isinstance(raw_items, list) else [],
        "relations": raw_relations if isinstance(raw_relations, list) else [],
        "warnings": warnings,
        "errors": errors,
    }


def _audit(
    config: ServerConfig,
    data_source: str,
    endpoint_used: str,
    dry_run: bool,
    payload: Dict[str, Any],
    parsed: Dict[str, Any],
    started: float,
    transaction_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate audit trail entry for CMDB IRE operation with comprehensive tracking.
    
    Creates complete audit record for every CMDB governance operation including:
    - Transaction identification (unique transaction_id and timestamp)
    - Operation context (endpoint, dry_run status, data source)
    - Payload metadata (item and relation counts)
    - Result summary (success/failure/skip counts)
    - Performance metrics (elapsed time in milliseconds)
    - Error tracking (warning and error counts)
    
    Audit Purpose:
    - Compliance: Complete trail of all CMDB mutations for governance review
    - Performance: Track operation duration for optimization
    - Debugging: Correlate issues across distributed calls using transaction_id
    - Trending: Analyze operation types and error patterns
    
    Args:
        config: ServerConfig with instance URL and configuration
        data_source: Origin of the data (user identifier, system name, etc)
        endpoint_used: Which IRE endpoint was used (preflight, query, commit, enhanced)
        dry_run: Whether this was a dry-run (preflight) or production commit
        payload: Request payload with items and relations sent to IRE
        parsed: Parsed response from IRE containing results and errors
        started: Unix timestamp when operation started (for elapsed calculation)
        transaction_id: Optional explicit transaction ID (generates UUID if not provided)
    
    Returns:
        Dict with audit fields:
        - transaction_id: Unique identifier for this operation
        - instance: ServiceNow instance URL
        - data_source: Origin identifier
        - endpoint_used: Which IRE endpoint was called
        - dry_run: Whether this was preflight validation
        - commit: Whether result was committed (opposite of dry_run)
        - item_count: Count of CIs in request
        - relation_count: Count of relationships in request
        - operation_summary: Dict with CREATED/UPDATED/SKIPPED counts
        - warning_count: Total warnings from IRE response
        - error_count: Total errors from IRE response
        - elapsed_ms: Operation duration in milliseconds
    
    Side Effects:
        Logs complete audit record at INFO level for archival
    """
    audit = {
        "transaction_id": transaction_id or str(uuid.uuid4()),
        "instance": config.instance_url,
        "data_source": data_source,
        "endpoint_used": endpoint_used,
        "dry_run": dry_run,
        "commit": not dry_run,
        "item_count": len(payload.get("items", [])),
        "relation_count": len(payload.get("relations", [])),
        "operation_summary": parsed.get("operation_summary", {}),
        "warning_count": len(parsed.get("warnings", [])),
        "error_count": len(parsed.get("errors", [])),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
    logger.info("CMDB IRE audit %s", audit)
    return audit


def _request_from_params(params: ValidateCiPayloadParams | UpsertCiParams) -> CmdbIreRequest:
    """Convert tool parameter models into a normalized internal IRE request."""
    return CmdbIreRequest(
        data_source=params.data_source,
        items=[CmdbCiItem(**item) for item in params.items],
        relations=[CmdbRelation(**rel) for rel in (params.relations or [])],
        referenceItems=params.referenceItems,
        enhanced=bool(getattr(params, "enhanced", bool(params.enhanced_options))),
        enhanced_options=params.enhanced_options,
        dry_run_first=getattr(params, "dry_run_first", True),
        commit=getattr(params, "commit", False),
        strict_csdm_validation=params.strict_csdm_validation,
        allow_incomplete_payload=params.allow_incomplete_payload,
    )


def _dry_run(
    config: ServerConfig,
    auth_manager: AuthManager,
    req: CmdbIreRequest,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute IRE query mode (standard or enhanced) without committing changes."""
    if req.enhanced or req.enhanced_options:
        return ire_query_enhanced(
            config, auth_manager, payload, req.data_source, req.enhanced_options
        )
    return ire_query(config, auth_manager, payload, req.data_source)


def _commit(
    config: ServerConfig,
    auth_manager: AuthManager,
    req: CmdbIreRequest,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute IRE commit mode (standard or enhanced) based on request options."""
    if req.enhanced:
        return ire_commit_enhanced(
            config, auth_manager, payload, req.data_source, req.enhanced_options
        )
    return ire_commit(config, auth_manager, payload, req.data_source)


def _error_result(message: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a standardized error payload for CI validation/upsert workflows."""
    return {
        "ok": False,
        "dry_run": True,
        "operation_summary": {"ERROR": 1},
        "items": [],
        "relations": [],
        "warnings": [],
        "errors": [message],
        "payload": payload or {},
    }


def instance_preflight(
    config: ServerConfig, auth_manager: AuthManager, params: InstancePreflightParams
) -> Dict[str, Any]:
    """Handle instance preflight.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    errors: List[str] = []
    warnings: List[str] = []
    parsed_url = urlparse(params.instance_url)
    if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
        errors.append("Invalid instance URL format")
    if not params.data_source:
        errors.append("missing/invalid discovery source")
    if not (params.username or params.auth_profile_reference or config.auth):
        errors.append("authentication failure: no credentials or auth profile supplied")
    if errors:
        return {
            "ok": False,
            "ire_accessible": False,
            "query_endpoint_accessible": False,
            "enhanced_endpoint_accessible": None,
            "data_source": params.data_source,
            "warnings": warnings,
            "errors": errors,
            "recommended_action": (
                "Fix instance URL, authentication, and data_source before CI work."
            ),
        }
    try:
        target_config = _config_for_instance(config, params.instance_url)
        query_response = ire_preflight(target_config, auth_manager, params.data_source)
        enhanced_accessible: Optional[bool] = None
        try:
            payload = {
                "items": [
                    {
                        "className": "cmdb_ci",
                        "internal_id": "preflight_enhanced_ci",
                        "values": {"name": "mcp_ire_preflight_enhanced_do_not_commit"},
                        "sys_object_source_info": {
                            "source_name": params.data_source,
                            "source_native_key": "mcp_ire_preflight_enhanced_do_not_commit",
                        },
                    }
                ]
            }
            ire_query_enhanced(target_config, auth_manager, payload, params.data_source, {})
            enhanced_accessible = True
        except Exception as exc:
            enhanced_accessible = False
            warnings.append(f"Enhanced IRE query unavailable or unauthorized: {exc}")
        return {
            "ok": True,
            "ire_accessible": True,
            "query_endpoint_accessible": True,
            "enhanced_endpoint_accessible": enhanced_accessible,
            "data_source": params.data_source,
            "warnings": warnings,
            "errors": [],
            "recommended_action": "Proceed with validate_ci_payload before any commit.",
            "raw_status": {k: query_response.get(k) for k in ("status_code", "elapsed_ms")},
        }
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 401:
            errors.append("authentication failure")
        elif status == 403:
            errors.append("authorization/role issue")
        elif status == 400:
            errors.append("malformed payload or missing/invalid discovery source")
        elif status == 404:
            errors.append("endpoint unavailable")
        else:
            errors.append(f"IRE endpoint error: {exc}")
    except requests.exceptions.Timeout:
        errors.append("network/timeout error")
    except requests.exceptions.RequestException as exc:
        errors.append(f"network/timeout error: {exc}")
    except Exception as exc:
        errors.append(str(exc))
    return {
        "ok": False,
        "ire_accessible": False,
        "query_endpoint_accessible": False,
        "enhanced_endpoint_accessible": None,
        "data_source": params.data_source,
        "warnings": warnings,
        "errors": errors,
        "recommended_action": "Resolve IRE access before attempting CMDB/CI work.",
    }


def validate_ci_payload(
    config: ServerConfig, auth_manager: AuthManager, params: ValidateCiPayloadParams
) -> Dict[str, Any]:
    """Validate ci payload.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    started = time.perf_counter()
    try:
        req = _request_from_params(params)
        payload = build_ire_payload(req)
        response = _dry_run(config, auth_manager, req, payload)
        parsed = _parse_ire_response(response)
        endpoint = (
            "/api/now/identifyreconcile/queryEnhanced"
            if req.enhanced_options
            else "/api/now/identifyreconcile/query"
        )
        audit = _audit(config, req.data_source, endpoint, True, payload, parsed, started)
        return {
            "ok": not parsed["errors"],
            "dry_run": True,
            "operation_summary": parsed["operation_summary"],
            "items": parsed["items"],
            "relations": parsed["relations"],
            "warnings": parsed["warnings"],
            "errors": parsed["errors"],
            "payload": payload,
            "audit": audit,
        }
    except Exception as exc:
        return _error_result(str(exc))


def upsert_ci(
    config: ServerConfig, auth_manager: AuthManager, params: UpsertCiParams
) -> Dict[str, Any]:
    """Handle upsert ci.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    try:
        req = _request_from_params(params)
        payload = build_ire_payload(req)
    except Exception as exc:
        return _error_result(str(exc))

    preflight_result = None
    if not params.skip_preflight:
        preflight_result = instance_preflight(
            config,
            auth_manager,
            InstancePreflightParams(instance_url=config.instance_url, data_source=req.data_source),
        )
        if not preflight_result.get("ok"):
            result = _error_result("IRE preflight failed", payload)
            result["preflight"] = preflight_result
            return result

    dry_result: Optional[Dict[str, Any]] = None
    if params.dry_run_first:
        dry_params = ValidateCiPayloadParams(
            data_source=params.data_source,
            items=params.items,
            relations=params.relations,
            referenceItems=params.referenceItems,
            enhanced_options=params.enhanced_options if params.enhanced else None,
            strict_csdm_validation=params.strict_csdm_validation,
        )
        dry_result = validate_ci_payload(config, auth_manager, dry_params)
        if dry_result.get("errors"):
            dry_result["committed"] = False
            dry_result["preflight"] = preflight_result
            return dry_result
    if not params.commit:
        if dry_result:
            dry_result["committed"] = False
            dry_result["preflight"] = preflight_result
            return dry_result
        return {"ok": True, "dry_run": True, "committed": False, "payload": payload}

    started = time.perf_counter()
    try:
        response = _commit(config, auth_manager, req, payload)
        parsed = _parse_ire_response(response)
        endpoint = (
            "/api/now/identifyreconcile/enhanced"
            if params.enhanced
            else "/api/now/identifyreconcile"
        )
        audit = _audit(config, req.data_source, endpoint, False, payload, parsed, started)
        return {
            "ok": not parsed["errors"],
            "dry_run": False,
            "committed": not parsed["errors"],
            "endpoint_used": endpoint,
            "operation_summary": parsed["operation_summary"],
            "items": parsed["items"],
            "relations": parsed["relations"],
            "warnings": parsed["warnings"],
            "errors": parsed["errors"],
            "payload": payload,
            "dry_run_result": dry_result,
            "preflight": preflight_result,
            "audit": audit,
            "raw_response": response if config.debug else None,
        }
    except Exception as exc:
        return _error_result(str(exc), payload)


def _source_info(data_source: str, native_key: str, feed: str = "mcp_cmdb") -> Dict[str, Any]:
    """Create sys_object_source_info/sys_rel_source_info metadata payload."""
    return {
        "source_name": data_source,
        "source_native_key": native_key,
        "source_feed": feed,
        "source_recency_timestamp": None,
    }


def _relation_warnings(parent_class: str, child_class: str, relation_type: str) -> List[str]:
    """Validate relationship direction against local CSDM guidance and warn on risk."""
    relation_key = relation_type.strip().lower()
    rule = RELATIONSHIP_RULES.get(relation_key)
    if not rule:
        return [f"Relationship type '{relation_type}' is not in local CSDM validation map"]
    parent_ok = any(parent_class.startswith(prefix) for prefix in rule["parent"])
    child_ok = any(child_class.startswith(prefix) for prefix in rule["child"])
    warnings = []
    if not parent_ok or not child_ok:
        warnings.append(
            f"Relationship direction may violate CSDM guidance: {parent_class} "
            f"{relation_type} {child_class}"
        )
    return warnings


def create_csdm_relationship(
    config: ServerConfig, auth_manager: AuthManager, params: CreateCsdmRelationshipParams
) -> Dict[str, Any]:
    """Create csdm relationship.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    parent_class = _normalize_class(params.parent_class, params.parent_values)
    child_class = _normalize_class(params.child_class, params.child_values)
    warnings = _relation_warnings(parent_class, child_class, params.relationship_type)
    parent_key = params.source_native_keys.get("parent") or params.parent_ci
    child_key = params.source_native_keys.get("child") or params.child_ci
    items = [
        {
            "internal_id": "parent",
            "className": parent_class,
            "values": params.parent_values,
            "sys_object_source_info": _source_info(params.data_source, parent_key),
        },
        {
            "internal_id": "child",
            "className": child_class,
            "values": params.child_values,
            "sys_object_source_info": _source_info(params.data_source, child_key),
        },
    ]
    relations = [
        {
            "parent_id": "parent",
            "child_id": "child",
            "type": params.relationship_type,
            "sys_rel_source_info": _source_info(
                params.data_source, f"{parent_key}:{params.relationship_type}:{child_key}"
            ),
        }
    ]
    result = upsert_ci(
        config,
        auth_manager,
        UpsertCiParams(
            data_source=params.data_source,
            items=items,
            relations=relations,
            dry_run_first=params.dry_run_first,
            commit=params.commit,
            skip_preflight=False,
            strict_csdm_validation=params.strict_csdm_validation,
        ),
    )
    result.setdefault("warnings", []).extend(warnings)
    result["cmdb_rel_ci_table_api_used"] = False
    return result


def managed_ingestion(
    config: ServerConfig, auth_manager: AuthManager, params: ManagedIngestionParams
) -> Dict[str, Any]:
    """Handle managed ingestion.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    preflight = instance_preflight(
        config,
        auth_manager,
        InstancePreflightParams(instance_url=config.instance_url, data_source=params.data_source),
    )
    if not preflight.get("ok"):
        return {"ok": False, "batch_id": params.batch_id, "preflight": preflight, "chunks": []}

    chunks = [
        params.items[index : index + params.chunk_size]
        for index in range(0, len(params.items), params.chunk_size)
    ]
    results = []
    aggregate = Counter()
    warnings: List[str] = []
    errors: List[str] = []
    for idx, chunk in enumerate(chunks):
        rel_chunk = params.relations if idx == 0 else []
        result = upsert_ci(
            config,
            auth_manager,
            UpsertCiParams(
                data_source=params.data_source,
                items=chunk,
                relations=rel_chunk,
                referenceItems=params.referenceItems if idx == 0 else None,
                dry_run_first=True,
                commit=params.mode == "commit",
                skip_preflight=True,
                strict_csdm_validation=params.strict_csdm_validation,
            ),
        )
        results.append({"chunk_index": idx, "item_count": len(chunk), "result": result})
        aggregate.update(result.get("operation_summary", {}))
        warnings.extend(result.get("warnings", []))
        errors.extend(result.get("errors", []))
        if result.get("errors") and params.stop_on_error:
            break

    payload = {"items": params.items, "relations": params.relations or []}
    governance = (
        _compute_governance(payload, params.data_source, None, None, "strict", True)
        if params.generate_audit_report
        else None
    )
    return {
        "ok": not errors,
        "batch_id": params.batch_id,
        "mode": params.mode,
        "preflight": preflight,
        "chunks": results,
        "operation_summary": dict(aggregate),
        "inserted": aggregate.get("INSERT", 0),
        "updated": aggregate.get("UPDATE", 0),
        "unchanged": aggregate.get("NO_CHANGE", 0),
        "skipped": aggregate.get("SKIPPED", 0),
        "incomplete": aggregate.get("INCOMPLETE", 0),
        "warnings": warnings,
        "errors": errors,
        "governance_report": governance,
    }


def _score(parts: List[bool]) -> int:
    """Convert boolean checks into a percentage score rounded to nearest integer."""
    if not parts:
        return 100
    return round((sum(1 for part in parts if part) / len(parts)) * 100)


def _payload_from_source(
    payload: Optional[Dict[str, Any]], previous_result: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Resolve governance input payload from explicit payload or prior tool result."""
    if payload:
        return payload
    if previous_result:
        return previous_result.get("payload") or {
            "items": previous_result.get("items", []),
            "relations": previous_result.get("relations", []),
        }
    return {"items": [], "relations": []}


def _compute_governance(
    payload: Dict[str, Any],
    data_source: str,
    expected_classes: Optional[List[str]],
    required_fields_by_class: Optional[Dict[str, List[str]]],
    csdm_mode: str,
    include_recommendations: bool,
) -> Dict[str, Any]:
    """Compute CMDB governance quality metrics and risk recommendations.

    Evaluates payload completeness, class correctness, source compliance, and
    relationship quality to produce an overall governance score plus risks.

    Args:
        payload: IRE-like payload with items and optional relations.
        data_source: Expected source_name for source metadata validation.
        expected_classes: Optional allow-list of acceptable class names.
        required_fields_by_class: Optional required field mapping per class.
        csdm_mode: Governance strictness mode (used for recommendation text).
        include_recommendations: Include remediation guidance in output.

    Returns:
        Dict[str, Any]: Governance report with component scores, risks, and
        recommendations when enabled.
    """
    items = payload.get("items", [])
    relations = payload.get("relations", [])
    risks: List[str] = []
    recommendations: List[str] = []
    source_checks = []
    identifier_checks = []
    required_checks = []
    class_checks = []
    stable_checks = []
    duplicate_keys = Counter()

    for idx, item in enumerate(items):
        class_name = _normalize_class(item.get("className", ""), item.get("values", {}))
        source = item.get("sys_object_source_info", {})
        values = item.get("values", {})
        class_checks.append(_class_allowed(class_name))
        source_checks.append(
            source.get("source_name") == data_source and bool(source.get("source_native_key"))
        )
        identifier = source.get("source_native_key") or item.get("internal_id")
        identifier_checks.append(bool(identifier))
        stable_checks.append(
            bool(identifier) or any(values.get(f) for f in STABLE_IDENTIFIER_FIELDS)
        )
        if identifier:
            duplicate_keys[(class_name, identifier)] += 1
        for field in (required_fields_by_class or {}).get(class_name, []):
            present = values.get(field) not in (None, "")
            required_checks.append(present)
            if not present:
                risks.append(f"Item {idx} missing required field {field}")
        if expected_classes and class_name not in expected_classes:
            risks.append(f"Unexpected class {class_name}")

    for key, count in duplicate_keys.items():
        if count > 1:
            risks.append(f"Duplicate-risk source key {key[1]} for class {key[0]}")

    orphan_relations = 0
    relation_warnings = []
    item_ids = {item.get("internal_id") for item in items}
    for rel in relations:
        parent = rel.get("parent_id")
        child = rel.get("child_id")
        if (parent and parent not in item_ids) or (child and child not in item_ids):
            orphan_relations += 1
        if str(rel.get("type", "")).lower() not in RELATIONSHIP_RULES:
            relation_warnings.append(rel.get("type"))

    completeness_score = _score(required_checks + identifier_checks + source_checks)
    correctness_score = _score(class_checks + stable_checks + [orphan_relations == 0])
    compliance_score = _score(source_checks + class_checks + [True])
    relationship_score = _score([orphan_relations == 0, not relation_warnings])
    if relation_warnings:
        risks.append(f"Weak or unmapped relationship types: {sorted(set(relation_warnings))}")
    if include_recommendations:
        if completeness_score < 90:
            recommendations.append(
                "Add source metadata, stable identifiers, and required fields before commit."
            )
        if relationship_score < 100:
            recommendations.append(
                "Review relationship endpoints and direction against CSDM governance."
            )
        if risks and csdm_mode == "strict":
            recommendations.append(
                "Run validate_ci_payload and resolve strict CSDM risks before ingestion."
            )

    return {
        "overall_score": round(
            (completeness_score + correctness_score + compliance_score + relationship_score) / 4
        ),
        "completeness": {"score": completeness_score, "items": len(items)},
        "correctness": {"score": correctness_score, "invalid_classes": class_checks.count(False)},
        "compliance": {
            "score": compliance_score,
            "data_source": data_source,
            "no_direct_table_api_usage": True,
        },
        "relationship_health": {
            "score": relationship_score,
            "orphan_relationships": orphan_relations,
            "weak_relationship_types": sorted(set(str(v) for v in relation_warnings)),
        },
        "risks": risks,
        "recommendations": recommendations,
    }


def governance_insight(
    config: ServerConfig, auth_manager: AuthManager, params: GovernanceInsightParams
) -> Dict[str, Any]:
    """Handle governance insight.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    payload = _payload_from_source(params.payload, params.previous_result)
    return _compute_governance(
        payload,
        params.data_source,
        params.expected_classes,
        params.required_fields_by_class,
        params.csdm_mode,
        params.include_recommendations,
    )


def health_dashboard(
    config: ServerConfig, auth_manager: AuthManager, params: HealthDashboardParams
) -> Dict[str, Any]:
    """Handle health dashboard.`r`n    `r`n    This helper supports MCP workflow operations and returns normalized results.`r`n    """
    ingestion = params.ingestion_result
    governance = params.governance_result
    summary_counts = ingestion.get("operation_summary", {})
    warnings = ingestion.get("warnings", [])
    errors = ingestion.get("errors", [])
    items = ingestion.get("payload", {}).get("items") or []
    relations = ingestion.get("payload", {}).get("relations") or []
    class_counter = Counter(item.get("className", "unknown") for item in items)
    source_counter = Counter(
        (item.get("sys_object_source_info") or {}).get("source_name", "unknown") for item in items
    )
    relation_counter = Counter(rel.get("type", "unknown") for rel in relations)
    result = {
        "summary": {
            "total_items": len(items) or sum(summary_counts.values()),
            "inserted": summary_counts.get("INSERT", ingestion.get("inserted", 0)),
            "updated": summary_counts.get("UPDATE", ingestion.get("updated", 0)),
            "unchanged": summary_counts.get("NO_CHANGE", ingestion.get("unchanged", 0)),
            "skipped": summary_counts.get("SKIPPED", ingestion.get("skipped", 0)),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "kpis": {
            "completeness": governance.get("completeness", {}).get("score", 0),
            "correctness": governance.get("correctness", {}).get("score", 0),
            "compliance": governance.get("compliance", {}).get("score", 0),
            "relationship_health": governance.get("relationship_health", {}).get("score", 0),
        },
        "risk_breakdown": governance.get("risks", []),
        "class_breakdown": [
            {"className": key, "count": value} for key, value in class_counter.items()
        ],
        "data_source_breakdown": [
            {"data_source": key, "count": value} for key, value in source_counter.items()
        ],
        "relationship_breakdown": [
            {"type": key, "count": value} for key, value in relation_counter.items()
        ],
        "next_actions": governance.get("recommendations", []),
    }
    if not params.include_charts_data:
        for key in ("class_breakdown", "data_source_breakdown", "relationship_breakdown"):
            result[key] = []
    return result
