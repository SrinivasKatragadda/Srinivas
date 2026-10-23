"""Client helpers for ServiceNow CMDB Identification and Reconciliation API.

The IRE (Identify, Reconcile, Enrich) API is used for submitting data sources to ServiceNow's
CMDB (Configuration Management Database) for identification against existing CIs (Configuration Items)
and reconciliation with the target system's data model. This module provides simplified wrappers
around the IRE REST API endpoints for:

1. Preflight checks: Validate data source connectivity without committing data
2. Query operations: Submit CI data for identification/reconciliation without persisting
3. Commit operations: Persist identified/reconciled data to the CMDB
4. Enhanced variants: Advanced query/commit with additional processing options

Usage patterns:
- Use ire_preflight() to verify data source setup before processing large batches
- Use ire_query() for dry-run operations to test data transformation/mapping
- Use ire_commit() for production data ingestion with automatic retry on transient failures
- Use enhanced variants for complex reconciliation scenarios requiring custom options
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import requests

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

# Base path for all IRE API endpoints in the ServiceNow REST API
IRE_BASE_PATH = "/api/now/identifyreconcile"

# HTTP status codes that are considered transient errors and should trigger automatic retry.
# These include: request timeout (408), rate limit (429), and various server errors (502, 503, 504)
TRANSIENT_STATUS_CODES = {408, 429, 502, 503, 504}


def _headers(auth_manager: AuthManager) -> Dict[str, str]:
    """Build HTTP headers for IRE API requests.
    
    Combines authentication headers from AuthManager with required content type headers
    for JSON request/response handling.
    
    Args:
        auth_manager: AuthManager instance that provides authentication credentials.
    
    Returns:
        Dict of HTTP headers ready for IRE API requests, including:
        - Authorization header from auth_manager (Basic, OAuth, or API Key)
        - Accept: application/json (for response parsing)
        - Content-Type: application/json (for JSON request body)
    """
    headers = auth_manager.get_headers()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    return headers


def _endpoint(config: ServerConfig, suffix: str = "") -> str:
    """Construct the full URL for an IRE API endpoint.
    
    Combines the ServiceNow instance URL with the IRE base path and optional endpoint-specific suffix.
    Ensures proper URL construction by removing trailing slashes before concatenation.
    
    Args:
        config: ServerConfig containing the ServiceNow instance URL.
        suffix: Optional endpoint suffix (e.g., "/query", "/queryEnhanced", "/enhanced").
                If empty, returns the base IRE endpoint.
    
    Returns:
        Complete URL for the IRE endpoint, e.g.,
        "https://dev12345.service-now.com/api/now/identifyreconcile/query"
    """
    return f"{config.instance_url.rstrip('/')}{IRE_BASE_PATH}{suffix}"


def _post_ire(
    config: ServerConfig,
    auth_manager: AuthManager,
    endpoint_suffix: str,
    payload: Dict[str, Any],
    data_source: str,
    options: Optional[Dict[str, Any]] = None,
    allow_retry: bool = True,
) -> Dict[str, Any]:
    """Execute an HTTP POST request to an IRE API endpoint with automatic retry logic.
    
    Core internal function that handles all communication with ServiceNow's IRE API endpoints.
    Implements resilience patterns including transient error retry, configurable timeout,
    performance tracking, and comprehensive request/response logging.
    
    Retry Behavior:
    - If allow_retry=True: Attempts request twice with automatic retry on transient errors
      (408, 429, 502, 503, 504) without explicit backoff delay
    - If allow_retry=False: Single attempt, no retry regardless of error type
    
    Args:
        config: ServerConfig with instance_url and timeout settings.
        auth_manager: AuthManager for constructing authentication headers.
        endpoint_suffix: Relative path appended to IRE base path (e.g., "/query", "/enhanced").
                        Empty string routes to the base commit endpoint.
        payload: Dict with 'items' and 'relations' lists containing CI data and relationships
                 for submission to IRE API.
        data_source: Required identifier for the external data source (e.g., "splunk_cmdb").
                    Used as sysparm_data_source query parameter for ServiceNow data source tracking.
        options: Optional dict of additional query parameters to include in the request
                (e.g., {"sysparm_exclude_reference_link": "true"}).
        allow_retry: If True, retry on transient HTTP errors (408, 429, 502-504).
                    If False, fail immediately on any HTTP error (used for commit operations).
    
    Returns:
        Dict containing:
        - status_code: HTTP status code from the response (200, 400, etc.)
        - elapsed_ms: Total elapsed time in milliseconds for the request
        - result: Parsed response body or nested 'result' field if present
    
    Raises:
        ValueError: If data_source parameter is empty or not provided.
        requests.exceptions.RequestException: On HTTP errors after retry exhausted,
                                            connection failures, or timeouts.
        RuntimeError: If request fails with no response exception (edge case).
    """
    # Validate data_source parameter - required for IRE API to track data source
    if not data_source:
        raise ValueError("sysparm_data_source is required for IRE operations")

    # Build query parameters: data_source is mandatory, additional options are merged if provided
    params: Dict[str, Any] = {"sysparm_data_source": data_source}
    if options:
        params.update(options)

    # Construct full endpoint URL and start performance timer
    url = _endpoint(config, endpoint_suffix)
    started = time.perf_counter()
    
    # Determine attempt count: 2 attempts if retrying allowed, 1 attempt otherwise
    attempts = 2 if allow_retry else 1
    last_exc: Optional[Exception] = None

    # Retry loop: iterate up to max attempts
    for attempt in range(1, attempts + 1):
        try:
            # Log request details for debugging and audit trail
            logger.info(
                "IRE request endpoint=%s data_source=%s item_count=%s relation_count=%s attempt=%s",
                endpoint_suffix or "/",
                data_source,
                len(payload.get("items", [])),
                len(payload.get("relations", [])),
                attempt,
            )
            
            # Execute HTTP POST request to IRE endpoint with authentication and timeout
            response = requests.post(
                url,
                headers=_headers(auth_manager),
                params=params,
                json=payload,
                timeout=config.timeout,
            )
            
            # Check for transient errors: if this is a retryable status and not the last attempt,
            # continue to next attempt (automatic retry without raising)
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < attempts:
                continue
            
            # For successful responses (2xx) or non-transient errors (4xx, 5xx),
            # raise exception if status indicates error
            response.raise_for_status()
            
            # Parse response JSON body; default to empty dict if no content
            body = response.json() if response.content else {}
            
            # Log full response body in debug mode for troubleshooting
            if config.debug:
                logger.debug("IRE response endpoint=%s body=%s", endpoint_suffix or "/", body)
            
            # Calculate elapsed time and return standardized response dict
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "result": body.get("result", body),  # Extract 'result' field if present, fallback to full body
            }
        except requests.exceptions.RequestException as exc:
            # Capture the exception and check if it contains a response object
            last_exc = exc
            response = getattr(exc, "response", None)
            
            # If the exception has a response with transient status and retries remain,
            # continue to next attempt; otherwise re-raise the exception
            if (
                response is not None
                and response.status_code in TRANSIENT_STATUS_CODES
                and attempt < attempts
            ):
                continue
            raise

    # Safety check: if retry loop exhausted, re-raise the last captured exception
    if last_exc:
        raise last_exc
    # Edge case: should not reach here but raise error if no exception was recorded
    raise RuntimeError("IRE request failed without a response")


def ire_preflight(
    config: ServerConfig, auth_manager: AuthManager, data_source: str
) -> Dict[str, Any]:
    """Validate IRE connectivity and data source configuration without committing data.
    
    Performs a preflight check by submitting a harmless test CI through the IRE query endpoint.
    Useful for:
    - Verifying data source is properly configured in ServiceNow before processing real data
    - Testing authentication and network connectivity to the IRE API
    - Validating payload structure and API contract before bulk operations
    
    Args:
        config: ServerConfig with instance URL and authentication settings.
        auth_manager: AuthManager for API authentication.
        data_source: Data source identifier (e.g., "mcp_ire_test").
    
    Returns:
        Dict with status_code, elapsed_ms, and result from the test query.
        A successful response indicates the data source is properly configured.
    
    Raises:
        requests.exceptions.RequestException: On authentication, network, or API errors.
    """
    # Construct minimal test payload with a single benign CI
    # This CI will not be persisted to CMDB but allows validation of data source setup
    payload = {
        "items": [
            {
                "className": "cmdb_ci",  # Base CI class for all configuration items
                "internal_id": "preflight_ci",  # Unique identifier within the submission
                "values": {"name": "mcp_ire_preflight_do_not_commit"},  # Recognizable test name
                "sys_object_source_info": {
                    "source_name": data_source,  # Data source identifier for audit
                    "source_native_key": "mcp_ire_preflight_do_not_commit",  # Native key in external system
                    "source_feed": "mcp_preflight",  # Feed identifier for grouping
                    "source_recency_timestamp": None,  # No timestamp for test data
                },
            }
        ]
    }
    # Submit test payload through query endpoint (dry-run, not committed)
    return ire_query(config, auth_manager, payload, data_source)


def ire_query(
    config: ServerConfig,
    auth_manager: AuthManager,
    payload: Dict[str, Any],
    data_source: str,
) -> Dict[str, Any]:
    """Submit CI data to IRE for dry-run identification/reconciliation without persisting to CMDB.
    
    Performs a query-only operation: CI data is matched, identified, and reconciled against
    the CMDB but NOT persisted. Useful for:
    - Data validation and transformation testing
    - Previewing identification/reconciliation results before commit
    - Detecting potential data quality issues without side effects
    
    The query endpoint supports automatic retry on transient errors (408, 429, 502-504),
    making it suitable for large batch operations.
    
    Args:
        config: ServerConfig with instance URL and timeout settings.
        auth_manager: AuthManager for API authentication.
        payload: Dict with 'items' list of CIs and optional 'relations' list of relationships.
        data_source: Data source identifier for tracking and auditing.
    
    Returns:
        Dict with status_code (200 = success), elapsed_ms, and result containing
        reconciliation output and identified CI sys_ids.
    
    Raises:
        requests.exceptions.RequestException: On connection/authentication errors.
    """
    # POST to /query endpoint with automatic retry enabled (safe for dry-run operations)
    return _post_ire(config, auth_manager, "/query", payload, data_source, allow_retry=True)


def ire_commit(
    config: ServerConfig,
    auth_manager: AuthManager,
    payload: Dict[str, Any],
    data_source: str,
) -> Dict[str, Any]:
    """Submit CI data to IRE for identification, reconciliation, AND persistence to CMDB.
    
    Performs a commit operation: CI data is matched, identified, reconciled, and persisted
    to the CMDB. This is a production operation that modifies the CMDB state. Use after
    validating data with ire_query() if possible.
    
    Commit operations do NOT automatically retry on transient errors (allow_retry=False)
    to prevent duplicate data ingestion. Callers should implement retry logic at a higher
    level if needed.
    
    Args:
        config: ServerConfig with instance URL and timeout settings.
        auth_manager: AuthManager for API authentication.
        payload: Dict with 'items' list of CIs and optional 'relations' list of relationships.
        data_source: Data source identifier for tracking and auditing.
    
    Returns:
        Dict with status_code (200 = success), elapsed_ms, and result containing
        confirmation of committed changes.
    
    Raises:
        requests.exceptions.RequestException: On any error including transient errors
                                            (no automatic retry).
    """
    # POST to base endpoint (commit) with automatic retry DISABLED to prevent duplicate data
    # Caller should handle retry/idempotency at application level if needed
    return _post_ire(config, auth_manager, "", payload, data_source, allow_retry=False)


def ire_query_enhanced(
    config: ServerConfig,
    auth_manager: AuthManager,
    payload: Dict[str, Any],
    data_source: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Submit CI data to enhanced IRE query endpoint with custom processing options.
    
    Similar to ire_query() but supports additional parameters for advanced reconciliation
    scenarios:
    - Custom field mapping
    - Relationship processing rules
    - Reference link exclusion/inclusion
    - Custom scoring or matching algorithms
    
    Enhanced queries are dry-run operations (data not persisted) with automatic retry support.
    
    Args:
        config: ServerConfig with instance URL and timeout settings.
        auth_manager: AuthManager for API authentication.
        payload: Dict with 'items' list of CIs and optional 'relations' list.
        data_source: Data source identifier for tracking and auditing.
        options: Optional dict of additional query parameters to pass to the enhanced endpoint
                (e.g., {"sysparm_exclude_reference_link": "true", "sysparm_use_custom_matcher": "yes"}).
    
    Returns:
        Dict with status_code, elapsed_ms, and enhanced result data.
    
    Raises:
        requests.exceptions.RequestException: On connection/authentication errors.
    """
    # POST to /queryEnhanced endpoint with custom options and automatic retry enabled
    return _post_ire(
        config, auth_manager, "/queryEnhanced", payload, data_source, options, allow_retry=True
    )


def ire_commit_enhanced(
    config: ServerConfig,
    auth_manager: AuthManager,
    payload: Dict[str, Any],
    data_source: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Submit CI data to enhanced IRE commit endpoint with custom processing options.
    
    Similar to ire_commit() but supports additional parameters for advanced reconciliation
    scenarios. This is a production operation that persists data to the CMDB with enhanced
    processing rules.
    
    Enhanced commits do NOT automatically retry on transient errors to prevent duplicates.
    Callers should implement retry logic at a higher level if idempotency is required.
    
    Args:
        config: ServerConfig with instance URL and timeout settings.
        auth_manager: AuthManager for API authentication.
        payload: Dict with 'items' list of CIs and optional 'relations' list.
        data_source: Data source identifier for tracking and auditing.
        options: Optional dict of additional parameters for enhanced processing
                (e.g., {"sysparm_use_custom_reconciliation": "true"}).
    
    Returns:
        Dict with status_code, elapsed_ms, and confirmation of committed changes.
    
    Raises:
        requests.exceptions.RequestException: On any error including transient errors
                                            (no automatic retry).
    """
    # POST to /enhanced endpoint with custom options and automatic retry DISABLED
    # Caller responsible for idempotency and retry logic at application level
    return _post_ire(
        config, auth_manager, "/enhanced", payload, data_source, options, allow_retry=False
    )
