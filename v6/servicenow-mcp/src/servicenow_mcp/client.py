"""
Comprehensive ServiceNow REST Client for Python.
Provides high-level methods for Table API, Aggregate Stats API, Schema Inspection,
Data Policies, and Diagnostics.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Union
import requests

from servicenow_mcp.config import (
    get_instance_url,
    get_username,
    get_password,
    get_verify_tls,
    get_oauth_client_id,
    get_oauth_client_secret,
    get_oauth_grant,
    is_write_enabled,
)
from servicenow_mcp.errors import (
    ServiceNowError,
    basic_auth_diagnostic,
    forbidden_diagnostic,
    business_rule_abort_diagnostic,
)

logger = logging.getLogger(__name__)


class ServiceNowClient:
    """Client for interacting with ServiceNow REST APIs."""

    def __init__(
        self,
        instance_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_tls: Optional[bool] = None,
        timeout: int = 30,
    ):
        self.instance_url = get_instance_url(instance_url)
        self.username = get_username(username)
        self.password = get_password(password)
        self.verify_tls = get_verify_tls() if verify_tls is None else verify_tls
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        # OAuth 2.0 (System OAuth > Application Registry): used when a client id + secret are configured
        self.client_id = get_oauth_client_id()
        self.client_secret = get_oauth_client_secret()
        self.oauth_grant = get_oauth_grant()
        self._token = ""
        self._refresh_token = ""
        self._token_expires = 0.0

    @property
    def uses_oauth(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _ensure_configured(self):
        if not self.instance_url:
            raise ServiceNowError(
                "ServiceNow instance URL is not configured. Set SERVICENOW_INSTANCE_URL or SNOW_INSTANCE_URL.",
                code="CONFIG_ERROR",
            )
        if self.uses_oauth and self.oauth_grant == "client_credentials":
            return
        if not self.username or not self.password:
            raise ServiceNowError(
                "ServiceNow credentials are not configured. Set SERVICENOW_USERNAME and SERVICENOW_PASSWORD.",
                code="CONFIG_ERROR",
            )

    def _oauth_token(self, force_refresh: bool = False) -> str:
        """Access token from /oauth_token.do (password or client_credentials grant), cached until shortly before expiry."""
        if self._token and not force_refresh and time.time() < self._token_expires:
            return self._token
        data = {"client_id": self.client_id, "client_secret": self.client_secret}
        if self._refresh_token and force_refresh:
            data.update(grant_type="refresh_token", refresh_token=self._refresh_token)
        elif self.oauth_grant == "client_credentials":
            data.update(grant_type="client_credentials")
        else:
            data.update(grant_type="password", username=self.username, password=self.password)
        try:
            resp = requests.post(f"{self.instance_url}/oauth_token.do", data=data, verify=self.verify_tls,
                                 timeout=self.timeout, headers={"Accept": "application/json"})
        except requests.exceptions.RequestException as exc:
            raise ServiceNowError(f"OAuth token request failed: {exc}", code="OAUTH_ERROR") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not resp.ok or "access_token" not in body:
            if data.get("grant_type") == "refresh_token":   # refresh token expired: start over with the grant
                self._refresh_token = ""
                return self._oauth_token(force_refresh=True)
            raise ServiceNowError(
                f"OAuth token request failed (HTTP {resp.status_code})", code="OAUTH_ERROR",
                detail="Check SERVICENOW_CLIENT_ID / SERVICENOW_CLIENT_SECRET and the Application Registry entry "
                       "(System OAuth > Application Registry) — error: " + str(body.get("error") or "")[:100],
                status_code=401,
            )
        self._token = body["access_token"]
        self._refresh_token = body.get("refresh_token") or self._refresh_token
        self._token_expires = time.time() + max(60, int(body.get("expires_in") or 1800) - 60)
        return self._token

    def _auth(self, force_refresh: bool = False) -> Dict[str, Any]:
        if self.uses_oauth:
            return {"auth": None, "headers": {"Authorization": f"Bearer {self._oauth_token(force_refresh)}"}}
        return {"auth": (self.username, self.password), "headers": None}

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_configured()
        url = f"{self.instance_url}{path}"

        try:
            resp = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                verify=self.verify_tls,
                timeout=self.timeout,
                **self._auth(),
            )
            if resp.status_code == 401 and self.uses_oauth:   # token revoked / expired early: refresh once
                resp = self.session.request(
                    method=method, url=url, params=params, json=json_data, verify=self.verify_tls,
                    timeout=self.timeout, **self._auth(force_refresh=True),
                )
        except requests.exceptions.SSLError as exc:
            raise ServiceNowError(
                f"SSL certificate verification failed for {self.instance_url}: {exc}",
                code="SSL_ERROR",
                detail="Consider setting SERVICENOW_VERIFY_TLS=false if using a self-signed lab certificate.",
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise ServiceNowError(
                f"Cannot connect to ServiceNow at {self.instance_url}: {exc}",
                code="CONNECTION_ERROR",
                detail="Check network, proxy, or if instance is hibernating.",
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise ServiceNowError(
                f"ServiceNow request timed out after {self.timeout}s: {exc}",
                code="TIMEOUT_ERROR",
            ) from exc

        if resp.status_code == 204 or not resp.content:
            return {"status": "success", "status_code": resp.status_code}

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}

        if not resp.ok:
            error_obj = data.get("error", {}) if isinstance(data, dict) else {}
            msg = error_obj.get("message") if isinstance(error_obj, dict) else str(resp.reason)
            detail = error_obj.get("detail", "") if isinstance(error_obj, dict) else ""

            if resp.status_code == 401:
                detail = f"{detail}\n\n{basic_auth_diagnostic()}"
            elif resp.status_code == 403:
                if "business rule" in (detail or "").lower() or "data policy" in (detail or "").lower():
                    detail = f"{detail}\n\n{business_rule_abort_diagnostic(detail)}"
                else:
                    detail = f"{detail}\n\n{forbidden_diagnostic()}"

            raise ServiceNowError(
                message=f"ServiceNow API error ({resp.status_code}): {msg}",
                code=f"HTTP_{resp.status_code}",
                detail=detail,
                status_code=resp.status_code,
            )

        return data

    # ── Table API ─────────────────────────────────────────────────────────────

    def query_records(
        self,
        table: str,
        query: str = "",
        fields: Optional[Union[str, List[str]]] = None,
        limit: int = 20,
        offset: int = 0,
        display_value: str = "false",
        order_by: str = "",
    ) -> List[Dict[str, Any]]:
        """Query records from any ServiceNow table."""
        final_query = query or ""
        if order_by:
            if order_by.startswith("-"):
                final_query = f"{final_query}^ORDERBYDESC{order_by[1:]}" if final_query else f"ORDERBYDESC{order_by[1:]}"
            else:
                final_query = f"{final_query}^ORDERBY{order_by}" if final_query else f"ORDERBY{order_by}"

        params: Dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_display_value": display_value,
            "sysparm_exclude_reference_link": "true",
        }
        if final_query:
            params["sysparm_query"] = final_query
        if fields:
            params["sysparm_fields"] = fields if isinstance(fields, str) else ",".join(fields)

        data = self._request("GET", f"/api/now/table/{table}", params=params)
        res = data.get("result", [])
        return res if isinstance(res, list) else [res]

    def get_record(
        self,
        table: str,
        sys_id: str,
        fields: Optional[Union[str, List[str]]] = None,
        display_value: str = "false",
    ) -> Dict[str, Any]:
        """Fetch a single record by its sys_id."""
        params: Dict[str, Any] = {
            "sysparm_display_value": display_value,
            "sysparm_exclude_reference_link": "true",
        }
        if fields:
            params["sysparm_fields"] = fields if isinstance(fields, str) else ",".join(fields)

        data = self._request("GET", f"/api/now/table/{table}/{sys_id}", params=params)
        return data.get("result", {})

    def create_record(self, table: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a record in any ServiceNow table."""
        if not is_write_enabled():
            raise ServiceNowError("Writes are disabled. Set SERVICENOW_WRITE_ENABLED=true to allow writes.", "WRITE_DISABLED")
        data = self._request("POST", f"/api/now/table/{table}", json_data=fields)
        return data.get("result", {})

    def update_record(self, table: str, sys_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing record by sys_id."""
        if not is_write_enabled():
            raise ServiceNowError("Writes are disabled. Set SERVICENOW_WRITE_ENABLED=true to allow writes.", "WRITE_DISABLED")
        data = self._request("PATCH", f"/api/now/table/{table}/{sys_id}", json_data=fields)
        return data.get("result", {})

    def delete_record(self, table: str, sys_id: str) -> Dict[str, Any]:
        """Delete a record by sys_id."""
        if not is_write_enabled():
            raise ServiceNowError("Writes are disabled. Set SERVICENOW_WRITE_ENABLED=true to allow writes.", "WRITE_DISABLED")
        return self._request("DELETE", f"/api/now/table/{table}/{sys_id}")

    # ── Schema & Data Policies ────────────────────────────────────────────────

    def get_table_schema(self, table: str) -> List[Dict[str, Any]]:
        """Fetch field definitions, types, labels and mandatory status for a table."""
        query = f"name={table}^ORname=task" if table != "task" else "name=task"
        fields = "element,column_label,internal_type,max_length,mandatory,reference,read_only"
        rows = self.query_records(
            table="sys_dictionary",
            query=f"{query}^elementISNOTEMPTY^ORDERBYcolumn_label",
            fields=fields,
            limit=500,
        )
        return rows

    def get_mandatory_fields(self, table: str) -> Dict[str, Any]:
        """
        List fields made mandatory by dictionary and data policies.
        Helps prevent 403 Data Policy Exception errors.
        """
        # 1. Dictionary mandatory fields
        dict_fields = self.query_records(
            table="sys_dictionary",
            query=f"name={table}^mandatory=true^elementISNOTEMPTY",
            fields="element,column_label,internal_type",
            limit=100,
        )

        # 2. Data policy rules
        policy_rules = []
        try:
            policy_rules = self.query_records(
                table="sys_data_policy_rule",
                query=f"data_policy.model_table={table}^mandatory=true",
                fields="field,mandatory,disabled",
                limit=100,
            )
        except Exception:
            pass

        return {
            "table": table,
            "dictionary_mandatory": dict_fields,
            "data_policy_rules": policy_rules,
            "mandatory_field_names": sorted(list({
                r.get("element") for r in dict_fields if r.get("element")
            } | {
                r.get("field") for r in policy_rules if r.get("field")
            })),
        }

    # ── Aggregate Stats API ───────────────────────────────────────────────────

    def get_stats(
        self,
        table: str,
        query: str = "",
        count: bool = True,
        group_by: str = "",
        avg_fields: str = "",
        min_fields: str = "",
        max_fields: str = "",
        sum_fields: str = "",
    ) -> Dict[str, Any]:
        """Aggregate data using ServiceNow Stats API (/api/now/stats/{table})."""
        params: Dict[str, Any] = {}
        if query:
            params["sysparm_query"] = query
        if count:
            params["sysparm_count"] = "true"
        if group_by:
            params["sysparm_group_by"] = group_by
        if avg_fields:
            params["sysparm_avg_fields"] = avg_fields
        if min_fields:
            params["sysparm_min_fields"] = min_fields
        if max_fields:
            params["sysparm_max_fields"] = max_fields
        if sum_fields:
            params["sysparm_sum_fields"] = sum_fields

        data = self._request("GET", f"/api/now/stats/{table}", params=params)
        return data.get("result", {})

    # ── Connection Diagnostic ─────────────────────────────────────────────────

    def check_connection(self) -> Dict[str, Any]:
        """Verify connectivity, authentication, and retrieve basic instance info."""
        self._ensure_configured()
        t0 = time.time()
        try:
            data = self._request(
                "GET",
                "/api/now/table/sys_properties",
                params={"sysparm_query": "name=instance_name^ORname=glide.war", "sysparm_fields": "name,value", "sysparm_limit": 2},
            )
            elapsed = round((time.time() - t0) * 1000, 2)
            results = data.get("result", [])
            info = {r.get("name"): r.get("value") for r in results if isinstance(r, dict)}
            return {
                "connected": True,
                "instance_url": self.instance_url,
                "latency_ms": elapsed,
                "instance_name": info.get("instance_name", self.instance_url.split("//")[-1].split(".")[0]),
                "build": info.get("glide.war", "Unknown"),
            }
        except Exception as e:
            elapsed = round((time.time() - t0) * 1000, 2)
            return {
                "connected": False,
                "instance_url": self.instance_url,
                "latency_ms": elapsed,
                "error": str(e),
            }


# Shared default client instance
default_client = ServiceNowClient()
