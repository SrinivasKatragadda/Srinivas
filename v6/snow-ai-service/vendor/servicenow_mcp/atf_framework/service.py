"""ServiceNow client helpers for ATF Framework Support.

This module is the shared infrastructure layer for the ATF framework.
It centralizes ServiceNow Table API access, record normalization, and the
ATF-specific helper methods used by coverage, execution, discovery, and tool
orchestration layers.

Core responsibilities:
- Normalize ServiceNow response payloads into predictable dictionaries
- Resolve reference values and display values consistently
- Provide a thin, authenticated client for Table API calls
- Offer ATF-specific convenience methods for tests, suites, steps, and results
- Cache table and field metadata to reduce repeated discovery cost

The rest of the ATF framework relies on this module as its low-level adapter
to ServiceNow. Its methods intentionally stay small and composable so higher
layers can build release-scoped workflows, coverage analysis, and execution
pipelines without duplicating API plumbing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

from servicenow_mcp.auth.auth_manager import AuthManager
from servicenow_mcp.utils.config import ServerConfig

logger = logging.getLogger(__name__)

SYS_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
ATF_TABLES = [
    "sys_atf_test",
    "sys_atf_step",
    "sys_atf_test_suite",
    "sys_atf_test_suite_test",
    "sys_atf_test_result",
    "sys_atf_test_result_step",
    "sys_atf_step_result",
    "sys_atf_test_suite_result",
    "sys_atf_step_config",
    "sys_atf_schedule",
    "sys_atf_schedule_test_suite",
    "sys_atf_runner",
    "sys_atf_parameter_set",
    "sys_atf_parameter",
]
CRITICAL_FIELDS = {
    "sys_atf_test": ["name", "description", "active"],
    "sys_atf_step": ["test", "order", "description"],
    "sys_atf_test_suite": ["name", "description"],
    "sys_atf_test_suite_test": ["test_suite", "test", "order"],
    "sys_atf_test_result": ["test", "status", "state"],
    "sys_atf_test_result_step": ["step", "status", "output"],
    "sys_atf_step_result": ["step", "status", "output"],
    "sys_atf_test_suite_result": ["test_suite", "status", "state"],
}
ARTIFACT_TABLES = {
    "catalog_item": "sc_cat_item",
    "record_producer": "sc_cat_item_producer",
    "flow": "sys_hub_flow",
    "table": "sys_db_object",
    "business_rule": "sys_script",
    "script_include": "sys_script_include",
    "client_script": "sys_script_client",
    "ui_policy": "sys_ui_policy",
    "acl": "sys_security_acl",
    "notification": "sysevent_email_action",
    "report": "sys_report",
    "dashboard": "pa_dashboards",
    "scoped_app": "sys_app",
    "rest_integration": "sys_ws_operation",
}
ATF_STEP_CONFIG_HINTS = {
    "open_form": ["Open a Form", "Open Form", "Open an Existing Record"],
    "set_field_values": ["Set Field Values", "Set Variable Values", "Set Values"],
    "submit_form": ["Submit a Form", "Submit Form", "Submit Catalog Item"],
    "assert_record": ["Record Validation", "Assert Record", "Validate Record"],
    "assert_field_value": ["Field Value Validation", "Assert Field Value", "Validate Field Value"],
    "impersonate": ["Impersonate", "Impersonate User"],
    "run_server_script": ["Run Server Side Script", "Run Server Script"],
    "send_rest_request": ["Send REST Request", "REST Request"],
    "validate_ui_policy": ["Field State Validation", "Validate UI Policy"],
    "validate_client_script": ["Field State Validation", "Validate Client Script"],
    "validate_flow_execution": ["Record Validation", "Validate Flow Execution"],
    "cleanup_record": ["Delete Record", "Record Delete", "Run Server Side Script"],
}


class ATFClientError(RuntimeError):
    """Raised when ServiceNow ATF client operations fail."""


def now_iso() -> str:
    """Get current UTC timestamp in ISO format (second precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def value(raw: Any) -> Any:
    """
    Extract raw value from a ServiceNow field reference dict.

    ServiceNow reference fields return dicts with 'value' and 'display_value' keys.
    This extracts the raw value (sys_id for references), falling back to display_value.

    Args:
        raw: Raw field value (dict or scalar)

    Returns:
        Raw value (sys_id for references, or original value for scalars).
    """
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("display_value")
    return raw


def display(raw: Any) -> Any:
    """
    Extract display value from a ServiceNow field reference dict.

    ServiceNow reference fields return dicts with 'display_value' and 'value' keys.
    This extracts the display value, falling back to raw value.

    Args:
        raw: Raw field value (dict or scalar)

    Returns:
        Display value (string for references, original value for scalars).
    """
    if isinstance(raw, dict):
        return raw.get("display_value") or raw.get("value")
    return raw


def normalize(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert all fields in a record to their display values.

    Args:
        record: ServiceNow record dict

    Returns:
        Record with all fields converted to display values.
    """
    return {key: display(val) for key, val in (record or {}).items()}


def safe_query(text: Any) -> str:
    """
    Sanitize a query string to prevent ServiceNow injection attacks.

    Removes or escapes dangerous characters like '^' (query operators) and
    'javascript:' strings.

    Args:
        text: Raw query string

    Returns:
        Sanitized query string safe for ServiceNow table API.
    """
    return str(text or "").replace("^", " ").replace("javascript:", "").replace("@javascript", "")


def scope_table(scope: str, suffix: str) -> str:
    """
    Generate a scoped table name for use in scoped applications.

    Converts a scope identifier to a ServiceNow table name with the pattern
    x_{scope}_{suffix} (for scoped apps) or {scope}_{suffix} (if already scoped).

    Args:
        scope: Application scope identifier (e.g., 'mcp' or 'x_abcd_mcp')
        suffix: Table name suffix

    Returns:
        Full scoped table name.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", scope or "mcp").strip("_").lower() or "mcp"
    return f"{cleaned}_{suffix}" if cleaned.startswith("x_") else f"x_{cleaned}_{suffix}"


def checksum(payload: Any) -> str:
    """
    Compute SHA256 checksum of a payload for integrity verification.

    Args:
        payload: Data to checksum

    Returns:
        SHA256 hex digest of the payload.
    """
    return hashlib.sha256(str(payload).encode("utf-8")).hexdigest()


class ATFServiceNowClient:
    """Small ServiceNow Table API client for ATF records and metadata discovery.

    The client wraps authenticated Table API access and adds a small set of ATF-
    specific conveniences:
    - Table existence and field discovery caching
    - Normalized CRUD helpers for tests, suites, steps, and results
    - Metadata helpers for ATF runner and configuration discovery
    - Safe wrappers around record creation, updates, and deletes

    The class is intentionally lightweight. It does not attempt to model ATF
    business logic; instead it provides the reusable transport and lookup surface
    that the higher-level services depend on.
    """

    def __init__(self, config: ServerConfig, auth_manager: AuthManager):
        """
        Initialize the ServiceNow ATF client.

        Args:
            config: Server configuration with instance URL and auth settings
            auth_manager: Authentication manager for API requests
        """
        self.config = config
        self.auth_manager = auth_manager
        self._table_exists_cache: Dict[str, bool] = {}  # Cache for table existence checks
        self._table_fields_cache: Dict[str, List[str]] = {}  # Cache for table field lists

    def _headers(self) -> Dict[str, str]:
        """Get HTTP headers for ServiceNow API requests with authentication."""
        headers = self.auth_manager.get_headers()
        headers["Accept"] = "application/json"
        headers["Content-Type"] = "application/json"
        return headers

    def _url(self, table: str, sys_id: Optional[str] = None) -> str:
        """Build ServiceNow Table API URL for a table and optional record sys_id."""
        base = f"{self.config.instance_url.rstrip('/')}/api/now/table/{table}"
        return f"{base}/{sys_id}" if sys_id else base

    def request(
        self,
        method: str,
        table: str,
        sys_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        started = time.perf_counter()
        logger.debug(
            "ATF Framework ServiceNow request method=%s table=%s sys_id=%s", method, table, sys_id
        )
        response = requests.request(
            method,
            self._url(table, sys_id),
            headers=self._headers(),
            params=params,
            json=payload,
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        logger.debug(
            "ATF Framework ServiceNow response method=%s table=%s status=%s elapsed_ms=%s",
            method,
            table,
            response.status_code,
            int((time.perf_counter() - started) * 1000),
        )
        body = response.json() if response.content else {}
        return body.get("result", body)

    def request_api_path(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        started = time.perf_counter()
        cleaned_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.config.instance_url.rstrip('/')}{cleaned_path}"
        logger.debug("ATF Framework ServiceNow API request method=%s path=%s", method, cleaned_path)
        response = requests.request(
            method,
            url,
            headers=self._headers(),
            params=params,
            json=payload,
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        logger.debug(
            "ATF Framework ServiceNow API response method=%s path=%s status=%s elapsed_ms=%s",
            method,
            cleaned_path,
            response.status_code,
            int((time.perf_counter() - started) * 1000),
        )
        body = response.json() if response.content else {}
        return body.get("result", body)

    def query_records(
        self,
        table: str,
        query: Optional[str] = None,
        fields: Optional[Iterable[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": limit,
        }
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        result = self.request("GET", table, params=params)
        return result if isinstance(result, list) else []

    def get_record(
        self, table: str, sys_id: str, fields: Optional[Iterable[str]] = None
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        }
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        result = self.request(
            "GET",
            table,
            sys_id,
            params=params,
        )
        return result if isinstance(result, dict) else {}

    def create_record(self, table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.filter_payload_for_table(table, payload)
        result = self.request("POST", table, payload=payload)
        return result if isinstance(result, dict) else {}

    def update_record(self, table: str, sys_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.filter_payload_for_table(table, payload)
        result = self.request("PATCH", table, sys_id, payload=payload)
        return result if isinstance(result, dict) else {}

    def delete_record(self, table: str, sys_id: str) -> Dict[str, Any]:
        result = self.request("DELETE", table, sys_id)
        return result if isinstance(result, dict) else {"sys_id": sys_id, "deleted": True}

    def ensure_record(
        self, table: str, unique_query: str, payload: Dict[str, Any], update_existing: bool = False
    ) -> Dict[str, Any]:
        existing = self.query_records(table, unique_query, limit=1)
        if existing:
            if update_existing:
                sys_id = str(value(existing[0].get("sys_id")))
                record = self.update_record(table, sys_id, payload)
                return {"operation": "updated", "record": record}
            return {"operation": "reused", "record": existing[0]}
        record = self.create_record(table, payload)
        return {"operation": "created", "record": record}

    def check_table_exists(self, table_name: str) -> bool:
        if table_name not in self._table_exists_cache:
            self._table_exists_cache[table_name] = bool(
                self.query_records(
                    "sys_db_object", f"name={safe_query(table_name)}", fields=["sys_id"], limit=1
                )
            )
        return self._table_exists_cache[table_name]

    def query_table_fields(self, table_name: str) -> List[str]:
        if table_name not in self._table_fields_cache:
            records = self.query_records(
                "sys_dictionary", f"name={safe_query(table_name)}", fields=["element"], limit=500
            )
            self._table_fields_cache[table_name] = [
                str(value(record.get("element")))
                for record in records
                if value(record.get("element"))
            ]
        return self._table_fields_cache[table_name]

    def check_field_exists(self, table_name: str, field_name: str) -> bool:
        return field_name in self.query_table_fields(table_name)

    def resolve_user(self, user_identifier: str) -> Optional[Dict[str, Any]]:
        """Resolve a user_name, name, email, or sys_id to a sys_user record."""
        if not user_identifier:
            return None
        identifier = safe_query(user_identifier)
        queries = []
        if SYS_ID_RE.match(identifier):
            queries.append(f"sys_id={identifier}")
        queries.extend(
            [
                f"user_name={identifier}",
                f"email={identifier}",
                f"name={identifier}",
                f"user_nameLIKE{identifier}^ORemailLIKE{identifier}^ORnameLIKE{identifier}",
            ]
        )
        for query in queries:
            records = self.query_records(
                "sys_user",
                query,
                fields=["sys_id", "user_name", "name", "email", "active"],
                limit=1,
            )
            if records:
                return normalize(records[0])
        return None

    def filter_payload_for_table(self, table_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Remove fields unsupported by the current instance to avoid 400s."""
        if not payload:
            return {}
        try:
            fields = set(self.query_table_fields(table_name))
        except Exception:
            fields = set()
        filtered = {key: val for key, val in payload.items() if not fields or key in fields}
        for key, val in list(filtered.items()):
            if isinstance(val, (dict, list)):
                filtered[key] = json.dumps(val, sort_keys=True)
        return filtered

    def get_atf_capabilities(self) -> Dict[str, Any]:
        from servicenow_mcp.atf_framework.table_discovery import discover_atf_capabilities_service

        return discover_atf_capabilities_service(self)

    def find_atf_test_by_name(
        self, name: str, scope: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        records = self.query_records("sys_atf_test", f"name={safe_query(name)}", limit=1)
        return records[0] if records else None

    def find_atf_suite_by_name(
        self, name: str, scope: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        records = self.query_records("sys_atf_test_suite", f"name={safe_query(name)}", limit=1)
        return records[0] if records else None

    def list_atf_tests(
        self,
        query: Optional[str] = None,
        application_scope: Optional[str] = None,
        active: Optional[bool] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        parts = []
        if query:
            parts.append(f"nameLIKE{safe_query(query)}^ORdescriptionLIKE{safe_query(query)}")
        if application_scope:
            parts.append(f"sys_scope={safe_query(application_scope)}")
        if active is not None:
            parts.append(f"active={str(active).lower()}")
        return [
            normalize(item)
            for item in self.query_records(
                "sys_atf_test", "^".join(parts) if parts else None, limit=limit
            )
        ]

    def list_atf_suites(
        self,
        query: Optional[str] = None,
        application_scope: Optional[str] = None,
        active: Optional[bool] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        parts = []
        if query:
            parts.append(f"nameLIKE{safe_query(query)}^ORdescriptionLIKE{safe_query(query)}")
        if application_scope:
            parts.append(f"sys_scope={safe_query(application_scope)}")
        if active is not None:
            parts.append(f"active={str(active).lower()}")
        return [
            normalize(item)
            for item in self.query_records(
                "sys_atf_test_suite", "^".join(parts) if parts else None, limit=limit
            )
        ]

    def create_atf_test(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.ensure_record(
            "sys_atf_test", f"name={safe_query(payload.get('name'))}", payload
        )

    def create_atf_step(self, test_sys_id: str, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        order = step_payload.get("order")
        unique = (
            f"test={safe_query(test_sys_id)}^order={safe_query(order)}^"
            f"description={safe_query(step_payload.get('description'))}"
        )
        payload = dict(step_payload)
        payload["test"] = test_sys_id
        payload = self.filter_payload_for_table("sys_atf_step", payload)
        return self.ensure_record("sys_atf_step", unique, payload)

    def query_atf_steps(self, test_sys_id: str) -> List[Dict[str, Any]]:
        return [
            normalize(item)
            for item in self.query_records(
                "sys_atf_step",
                f"test={safe_query(test_sys_id)}",
                limit=250,
            )
        ]

    def delete_atf_steps_for_test(self, test_sys_id: str) -> List[Dict[str, Any]]:
        deleted = []
        for step in self.query_atf_steps(test_sys_id):
            step_id = value(step.get("sys_id"))
            if step_id:
                deleted.append(self.delete_record("sys_atf_step", str(step_id)))
        return deleted

    def create_atf_suite(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.ensure_record(
            "sys_atf_test_suite", f"name={safe_query(payload.get('name'))}", payload
        )

    def add_test_to_suite(self, suite_sys_id: str, test_sys_id: str, order: int) -> Dict[str, Any]:
        payload = {"test_suite": suite_sys_id, "test": test_sys_id, "order": order}
        unique = f"test_suite={safe_query(suite_sys_id)}^test={safe_query(test_sys_id)}"
        return self.ensure_record("sys_atf_test_suite_test", unique, payload)

    def query_suite_tests(self, suite_sys_id: str) -> List[Dict[str, Any]]:
        return [
            normalize(item)
            for item in self.query_records(
                "sys_atf_test_suite_test",
                f"test_suite={safe_query(suite_sys_id)}",
                limit=500,
            )
        ]

    def query_test_suite_memberships(self, test_sys_id: str) -> List[Dict[str, Any]]:
        return [
            normalize(item)
            for item in self.query_records(
                "sys_atf_test_suite_test",
                f"test={safe_query(test_sys_id)}",
                limit=500,
            )
        ]

    def delete_suite_mappings(self, suite_sys_id: str) -> List[Dict[str, Any]]:
        deleted = []
        for mapping in self.query_suite_tests(suite_sys_id):
            mapping_id = value(mapping.get("sys_id"))
            if mapping_id:
                deleted.append(self.delete_record("sys_atf_test_suite_test", str(mapping_id)))
        return deleted

    def remove_test_from_suite(self, suite_sys_id: str, test_sys_id: str) -> Optional[Dict[str, Any]]:
        """Remove a single test from a suite by deleting its mapping record.

        Queries ``sys_atf_test_suite_test`` for a mapping that links
        ``test_sys_id`` to ``suite_sys_id`` and deletes it.  Returns the
        deleted record dict when found, or ``None`` when no mapping exists.

        Args:
            suite_sys_id: sys_id of the ATF test suite.
            test_sys_id:  sys_id of the ATF test to unlink.

        Returns:
            The deleted mapping record, or ``None`` if no mapping was found.
        """
        mappings = self.query_records(
            "sys_atf_test_suite_test",
            f"test_suite={safe_query(suite_sys_id)}^test={safe_query(test_sys_id)}",
            limit=1,
        )
        if not mappings:
            return None
        mapping_id = str(value(mappings[0].get("sys_id")) or "")
        if not mapping_id:
            return None
        return self.delete_record("sys_atf_test_suite_test", mapping_id)

    def find_step_config_for_type(self, step_type: str) -> Optional[Dict[str, Any]]:
        """Find a likely ATF step config without hardcoding sys_ids."""
        hints = ATF_STEP_CONFIG_HINTS.get(step_type, [])
        for hint in hints:
            records = self.query_records(
                "sys_atf_step_config",
                f"nameLIKE{safe_query(hint)}^ORdisplay_nameLIKE{safe_query(hint)}",
                fields=["sys_id", "name", "display_name"],
                limit=1,
            )
            if records:
                return normalize(records[0])
        return None

    def configured_atf_step_payload(self, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Attach discovered ATF step config fields when this instance supports them."""
        payload = dict(step_payload)
        step_type = str(payload.pop("_step_type", "") or "")
        try:
            fields = set(self.query_table_fields("sys_atf_step"))
        except Exception:
            fields = set()
        config = self.find_step_config_for_type(step_type) if step_type else None
        config_id = value(config.get("sys_id")) if config else None
        for candidate in ("step_config", "config", "step_config_id"):
            if config_id and candidate in fields:
                payload[candidate] = config_id
                break
        serialized_config = payload.pop("_step_values", None)
        for candidate in ("values", "input_values", "parameters"):
            if serialized_config and candidate in fields:
                payload[candidate] = serialized_config
                break
        return payload

    def query_atf_results(
        self,
        test_sys_id: Optional[str] = None,
        suite_sys_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        def ordered(query: Optional[str]) -> str:
            return f"{query}^ORDERBYDESCsys_created_on" if query else "ORDERBYDESCsys_created_on"

        def query_table(
            table: str, query: Optional[str], limit: int = 100, order: bool = True
        ) -> List[Dict[str, Any]]:
            try:
                actual_query = ordered(query) if order else query
                return [
                    normalize(item) | {"table": table}
                    for item in self.query_records(table, actual_query, limit=limit)
                ]
            except requests.exceptions.RequestException:
                return []

        def supported_fields(table: str, fields: Iterable[str]) -> List[str]:
            candidates = list(fields)
            try:
                available = set(self.query_table_fields(table))
            except Exception:
                return candidates
            return [field for field in candidates if field in available]

        def query_first_supported(
            table: str, fields: Iterable[str], sys_id: str
        ) -> List[Dict[str, Any]]:
            records: List[Dict[str, Any]] = []
            for field in supported_fields(table, fields):
                records.extend(query_table(table, f"{field}={safe_query(sys_id)}"))
            return records

        def append_suite_children(parent_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            child_records: List[Dict[str, Any]] = []
            for parent in parent_records:
                parent_id = str(value(parent.get("sys_id")) or "")
                if not parent_id:
                    continue
                child_records.extend(
                    query_first_supported(
                        "sys_atf_test_result",
                        ("suite_result", "test_suite_result", "parent", "parent_result"),
                        parent_id,
                    )
                )
            return child_records

        def dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            seen: set[tuple[str, str]] = set()
            deduped: List[Dict[str, Any]] = []
            for item in items:
                key = (str(item.get("table") or ""), str(value(item.get("sys_id")) or ""))
                if not key[1] or key in seen:
                    continue
                seen.add(key)
                deduped.append(item)
            return deduped

        if execution_id:
            query = f"sys_id={safe_query(execution_id)}"
            test_records = query_table("sys_atf_test_result", query, order=False)
            suite_records = query_table("sys_atf_test_suite_result", query, order=False)
            return dedupe(test_records + suite_records + append_suite_children(suite_records))
        if test_sys_id:
            return dedupe(query_table("sys_atf_test_result", f"test={safe_query(test_sys_id)}"))
        if suite_sys_id:
            suite_records = query_first_supported(
                "sys_atf_test_suite_result", ("test_suite", "suite"), suite_sys_id
            )
            direct_test_records = query_first_supported(
                "sys_atf_test_result", ("test_suite", "suite"), suite_sys_id
            )
            return dedupe(
                suite_records + direct_test_records + append_suite_children(suite_records)
            )
        return dedupe(
            query_table("sys_atf_test_result", None)
            + query_table("sys_atf_test_suite_result", None)
        )

    def query_atf_step_results(self, execution_id: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for table in ("sys_atf_test_result_step", "sys_atf_step_result"):
            try:
                fields = set(self.query_table_fields(table))
            except Exception:
                fields = set()
            relation_fields = [
                field
                for field in (
                    "test_result",
                    "suite_result",
                    "test_suite_result",
                    "parent",
                    "parent_result",
                )
                if not fields or field in fields
            ]
            for field in relation_fields:
                try:
                    query = f"{field}={safe_query(execution_id)}^ORDERBYorder"
                    records.extend(
                        [
                            normalize(item) | {"table": table}
                            for item in self.query_records(table, query, limit=250)
                        ]
                    )
                except requests.exceptions.RequestException:
                    continue
        seen: set[tuple[str, str]] = set()
        deduped: List[Dict[str, Any]] = []
        for record in records:
            key = (str(record.get("table") or ""), str(value(record.get("sys_id")) or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            deduped.append(record)
        return deduped

    def start_atf_cloud_runner(self, target_sys_id: str) -> Dict[str, Any]:
        result = self.request_api_path(
            "POST",
            "/api/now/sn_atf_tg/test_runner",
            payload={"testId": target_sys_id},
        )
        return result if isinstance(result, dict) else {"result": result}

    def get_atf_cloud_runner_progress(self, snboq_id: str) -> Dict[str, Any]:
        result = self.request_api_path(
            "GET",
            "/api/now/sn_atf_tg/test_runner_progress",
            params={"snboqId": snboq_id},
        )
        return result if isinstance(result, dict) else {"result": result}

    def start_atf_suite_runner(
        self, suite_sys_id: str, run_in_cloud: bool = False
    ) -> Dict[str, Any]:
        result = self.request_api_path(
            "POST",
            "/api/sn_cicd/testsuite/run",
            params={
                "test_suite_sys_id": suite_sys_id,
                "run_in_cloud": str(run_in_cloud).lower(),
            },
        )
        return result if isinstance(result, dict) else {"result": result}

    def get_cicd_progress(self, progress_id: str) -> Dict[str, Any]:
        result = self.request_api_path("GET", f"/api/sn_cicd/progress/{progress_id}")
        return result if isinstance(result, dict) else {"result": result}

    def get_cicd_testsuite_results(self, result_id: str) -> Dict[str, Any]:
        result = self.request_api_path("GET", f"/api/sn_cicd/testsuite/results/{result_id}")
        return result if isinstance(result, dict) else {"result": result}

    def create_atf_schedule(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.ensure_record(
            "sys_atf_schedule", f"name={safe_query(payload.get('name'))}", payload
        )

    def create_atf_evidence(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        table = payload.pop("_table", "x_mcp_atf_evidence")
        unique = (
            f"release_name={safe_query(payload.get('release_name'))}^test_sys_"
            f"id={safe_query(payload.get('test_sys_id'))}^execution_"
            f"id={safe_query(payload.get('execution_id'))}"
        )
        return self.ensure_record(table, unique, payload, update_existing=True)

    def mapping_table(self, scope: str) -> str:
        return scope_table(scope, "atf_artifact_map")

    def ensure_mapping_table(self, scope: str, dry_run: bool = False) -> Dict[str, Any]:
        table = self.mapping_table(scope)
        fields = [
            "artifact_type",
            "artifact_table",
            "artifact_sys_id",
            "artifact_name",
            "test_sys_id",
            "suite_sys_id",
            "test_category",
            "coverage_type",
            "last_generated_at",
            "last_executed_at",
            "last_result",
            "active",
            "notes",
        ]
        if self.check_table_exists(table):
            return {"status": "exists", "operation": "reused", "table": table}
        if dry_run:
            return {"status": "planned", "operation": "dry_run", "table": table, "fields": fields}
        table_record = self.create_record(
            "sys_db_object", {"name": table, "label": "ATF Artifact Map", "is_extendable": "false"}
        )
        created_fields = []
        for field in fields:
            created_fields.append(
                self.create_record(
                    "sys_dictionary",
                    {
                        "name": table,
                        "element": field,
                        "column_label": field.replace("_", " ").title(),
                        "internal_type": "string",
                        "max_length": 4000,
                    },
                )
            )
        return {
            "status": "created",
            "operation": "created",
            "table": table,
            "record": normalize(table_record),
            "fields": [normalize(item) for item in created_fields],
        }

    def map_artifact_to_atf_test(
        self, scope: str, payload: Dict[str, Any], dry_run: bool = False
    ) -> Dict[str, Any]:
        table_status = self.ensure_mapping_table(scope, dry_run)
        table = self.mapping_table(scope)
        if dry_run:
            return {
                "operation": "dry_run",
                "table": table,
                "payload": payload,
                "table_status": table_status,
            }
        unique = (
            f"artifact_type={safe_query(payload.get('artifact_type'))}"
            f"^artifact_sys_id={safe_query(payload.get('artifact_sys_id'))}"
            f"^test_category={safe_query(payload.get('test_category'))}"
        )
        ensured = self.ensure_record(table, unique, payload, update_existing=True)
        return {
            "operation": ensured["operation"],
            "table": table,
            "record": normalize(ensured["record"]),
            "table_status": table_status,
        }

    def get_atf_mapping_for_artifact(
        self, scope: str, artifact_type: str, artifact_sys_id: str
    ) -> List[Dict[str, Any]]:
        table = self.mapping_table(scope)
        if not self.check_table_exists(table):
            return []
        query = (
            f"artifact_type={safe_query(artifact_type)}^artifact_sys_"
            f"id={safe_query(artifact_sys_id)}^active=true"
        )
        return [normalize(item) for item in self.query_records(table, query, limit=100)]

    def update_atf_mapping_result(
        self, scope: str, mapping_sys_id: str, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self.update_record(self.mapping_table(scope), mapping_sys_id, result)


def artifact_table_for(artifact_type: str, fallback: Optional[str] = None) -> str:
    return fallback or ARTIFACT_TABLES.get(artifact_type, artifact_type)


def record_ref(table: str, record: Dict[str, Any], operation: str) -> Dict[str, Any]:
    sys_id = value(record.get("sys_id"))
    return {
        "table": table,
        "sys_id": sys_id,
        "operation": operation,
        "status": "success",
        "evidence": [{"type": "record", "table": table, "sys_id": sys_id}],
        "rollback": [
            {
                "action": "delete_record" if operation == "created" else "none",
                "table": table,
                "sys_id": sys_id,
            }
        ],
    }
