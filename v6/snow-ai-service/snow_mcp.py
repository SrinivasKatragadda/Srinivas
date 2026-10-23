"""
snow_mcp.py — FastMCP entrypoint for SoftProdigy Snow Agent.

Exposes all Snow Agent operations as MCP tools so any MCP-compatible
AI client (Claude Desktop, LangChain, etc.) can call them directly.

Credentials are normally passed per-request via HTTP headers (stateless):
  x-servicenow-instance-url   (required)
  x-servicenow-username        (required)
  x-servicenow-password        (required)
  x-api-key                    (optional — enforced when MCP_API_KEY env var is set)

When SNOW_ALLOW_ENV_CREDS=true, SNOW_INSTANCE_URL, SNOW_USER and SNOW_PASS
in the service `.env` are used as a fallback. Headers always take precedence.

Start with:
  python snow_mcp.py
  (or)  python snow_mcp.py --host 0.0.0.0 --port 8002 --transport streamable-http

Then add to any MCP client:
  URL:  http://localhost:8002/mcp
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

# ── FastMCP ────────────────────────────────────────────────────────────────────
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp import McpError
from mcp.types import ErrorData

# ── Snow Agent helpers (re-use existing logic) ─────────────────────────────────
from snow_interactive import (
    get_snow_headers,
    snow_get,
    snow_post,
    snow_patch,
    _resolve_update_set_sys_id,
    _VERIFY,
)
import groq_client
import requirement_pipeline as rp
import sn_build
import sn_client as snc
import itam_bridge
import itom_bridge

logger = logging.getLogger(__name__)

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8002"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")
MCP_LOG_LEVEL = os.getenv("MCP_LOG_LEVEL", "INFO")

MCP_API_KEY_HEADER = "x-api-key"
SNOW_INSTANCE_HEADER = "x-servicenow-instance-url"
SNOW_USERNAME_HEADER = "x-servicenow-username"
SNOW_PASSWORD_HEADER = "x-servicenow-password"
SNOW_PASSWORD_B64_HEADER = "x-servicenow-password-b64"


# ──────────────────────────────────────────────────────────────────────────────
# Middleware: optional API-key auth
# ──────────────────────────────────────────────────────────────────────────────

class ApiKeyAuthMiddleware(Middleware):
    """Reject requests whose x-api-key header doesn't match MCP_API_KEY env var."""

    async def on_request(self, context: MiddlewareContext, call_next):
        expected = os.getenv("MCP_API_KEY", "").strip()
        if not expected:
            return await call_next(context)

        try:
            get_http_request()
        except RuntimeError:
            return await call_next(context)

        headers = {k.lower(): v for k, v in (get_http_headers() or {}).items()}
        if headers.get(MCP_API_KEY_HEADER) != expected:
            raise McpError(
                ErrorData(
                    code=-32001,
                    message=f"Unauthorized: missing or invalid {MCP_API_KEY_HEADER} header",
                )
            )
        return await call_next(context)


# ──────────────────────────────────────────────────────────────────────────────
# Per-request credential extraction
# ──────────────────────────────────────────────────────────────────────────────

def _request_headers() -> dict[str, str]:
    try:
        raw = get_http_headers() or {}
    except RuntimeError:
        raw = {}
    return {k.lower(): v for k, v in raw.items()}


def _get_creds() -> tuple[str, str, str]:
    """Return request credentials, with the explicitly enabled .env account as a fallback."""
    h = _request_headers()
    instance_url = h.get(SNOW_INSTANCE_HEADER, "").strip()
    username = h.get(SNOW_USERNAME_HEADER, "").strip()
    password = h.get(SNOW_PASSWORD_HEADER, "").strip()
    pw_b64 = h.get(SNOW_PASSWORD_B64_HEADER, "").strip()

    if not password and pw_b64:
        try:
            password = base64.b64decode(pw_b64).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid {SNOW_PASSWORD_B64_HEADER} header") from exc

    if os.getenv("SNOW_ALLOW_ENV_CREDS", "false").strip().lower() == "true":
        instance_url = instance_url or os.getenv("SNOW_INSTANCE_URL", "").strip()
        username = username or os.getenv("SNOW_USER", "").strip()
        password = password or os.getenv("SNOW_PASS", "")

    if not instance_url:
        raise ValueError(f"Missing required header: {SNOW_INSTANCE_HEADER}")
    if not username:
        raise ValueError(f"Missing required header: {SNOW_USERNAME_HEADER}")
    if not password:
        raise ValueError(
            f"Missing required password header. Provide {SNOW_PASSWORD_HEADER} "
            f"or {SNOW_PASSWORD_B64_HEADER}."
        )
    return instance_url.rstrip("/"), username, password


# ──────────────────────────────────────────────────────────────────────────────
# Helper: resolve user sys_id
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_user_sys_id(instance_url: str, username: str, password: str,
                          user_id: str) -> Optional[str]:
    """Resolve a username, email, or sys_id to a sys_id."""
    headers = get_snow_headers(username, password)
    base = instance_url.rstrip("/")
    url = f"{base}/api/now/table/sys_user"

    for field in ("user_name", "email", "sys_id"):
        r = requests.get(url, headers=headers,
                         params={"sysparm_query": f"{field}={user_id}",
                                 "sysparm_fields": "sys_id,user_name,email",
                                 "sysparm_limit": 1},
                         timeout=15, verify=_VERIFY)
        if r.ok:
            rows = r.json().get("result", [])
            if rows:
                return rows[0]["sys_id"]
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Create MCP server
# ──────────────────────────────────────────────────────────────────────────────

def create_server() -> FastMCP:
    mcp = FastMCP(name="SoftProdigy Snow Agent")
    mcp.add_middleware(ApiKeyAuthMiddleware())

    # ── 1. Connection Test ─────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_check_connection",
        description="Test connectivity to a ServiceNow instance. Returns instance info if successful.",
    )
    def snow_check_connection() -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            headers = get_snow_headers(user, pwd)
            r = requests.get(
                f"{url}/api/now/table/sys_properties",
                headers=headers,
                params={"sysparm_limit": 1, "sysparm_fields": "name"},
                timeout=15, verify=_VERIFY,
            )
            if r.ok:
                return {"connected": True, "instance_url": url, "status_code": r.status_code}
            return {"connected": False, "instance_url": url, "status_code": r.status_code, "error": r.text[:200]}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    # ── 2. Incidents ──────────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_create_incident",
        description=(
            "Create a new ServiceNow incident. "
            "Returns the new incident number and sys_id."
        ),
    )
    def snow_create_incident(
        short_description: str,
        description: str = "",
        caller_id: str = "",
        urgency: int = 3,
        impact: int = 3,
        category: str = "inquiry",
        assignment_group: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            short_description: One-line summary of the issue (required).
            description: Detailed description of the incident.
            caller_id: Username or sys_id of the person reporting the issue.
            urgency: 1=High, 2=Medium, 3=Low.
            impact: 1=High, 2=Medium, 3=Low.
            category: Incident category (e.g. 'inquiry', 'software', 'hardware').
            assignment_group: Group name or sys_id to assign to.
        """
        try:
            url, user, pwd = _get_creds()
            payload: Dict[str, Any] = {
                "short_description": short_description,
                "description": description,
                "urgency": str(urgency),
                "impact": str(impact),
                "category": category,
                "state": "1",
            }
            if caller_id:
                payload["caller_id"] = caller_id
            if assignment_group:
                payload["assignment_group"] = assignment_group

            result = snow_post(url, user, pwd, "incident", payload)
            return {
                "success": True,
                "incident_number": result.get("number"),
                "sys_id": result.get("sys_id"),
                "short_description": result.get("short_description"),
                "state": result.get("state", {}).get("display_value", "New"),
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_list_incidents",
        description="List ServiceNow incidents with optional filters.",
    )
    def snow_list_incidents(
        state: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        search: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Args:
            state: Filter by state code: 1=New, 2=In Progress, 6=Resolved, 7=Closed.
            assigned_to: Filter by assignee username.
            assignment_group: Filter by group name.
            search: Text search in short_description.
            limit: Max records to return (default 20).
        """
        try:
            url, user, pwd = _get_creds()
            query_parts = []
            if state:
                query_parts.append(f"state={state}")
            if assigned_to:
                query_parts.append(f"assigned_to.user_name={assigned_to}")
            if assignment_group:
                query_parts.append(f"assignment_group.name={assignment_group}")
            if search:
                query_parts.append(f"short_descriptionLIKE{search}")
            query = "^".join(query_parts) if query_parts else "active=true"

            rows = snow_get(url, user, pwd, "incident", {
                "sysparm_query": query + "^ORDERBYDESCsys_created_on",
                "sysparm_limit": limit,
                "sysparm_fields": "number,short_description,state,urgency,impact,assigned_to,assignment_group,sys_created_on",
                "sysparm_display_value": "true",
            })
            return {"success": True, "count": len(rows), "incidents": rows}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_get_incident",
        description="Get details of a specific ServiceNow incident by number (e.g. INC0001234) or sys_id.",
    )
    def snow_get_incident(incident_id: str) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            field = "number" if incident_id.upper().startswith("INC") else "sys_id"
            rows = snow_get(url, user, pwd, "incident", {
                "sysparm_query": f"{field}={incident_id}",
                "sysparm_limit": 1,
                "sysparm_display_value": "true",
            })
            if not rows:
                return {"success": False, "error": f"Incident {incident_id} not found"}
            return {"success": True, "incident": rows[0]}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_update_incident",
        description="Update an existing ServiceNow incident (state, notes, assignment, etc.).",
    )
    def snow_update_incident(
        incident_id: str,
        state: Optional[int] = None,
        work_notes: str = "",
        close_notes: str = "",
        resolution_code: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            incident_id: Incident number (INC0001234) or sys_id.
            state: New state: 1=New, 2=In Progress, 6=Resolved, 7=Closed.
            work_notes: Internal work notes to add.
            close_notes: Resolution notes (required when resolving).
            resolution_code: Close code (e.g. 'Solved (Permanently)').
            assigned_to: Reassign to this username.
            assignment_group: Reassign to this group.
        """
        try:
            url, user, pwd = _get_creds()
            field = "number" if incident_id.upper().startswith("INC") else "sys_id"
            rows = snow_get(url, user, pwd, "incident", {
                "sysparm_query": f"{field}={incident_id}",
                "sysparm_limit": 1,
                "sysparm_fields": "sys_id,number",
            })
            if not rows:
                return {"success": False, "error": f"Incident {incident_id} not found"}
            sys_id = rows[0]["sys_id"]

            payload: Dict[str, Any] = {}
            if state is not None:
                payload["state"] = str(state)
            if work_notes:
                payload["work_notes"] = work_notes
            if close_notes:
                payload["close_notes"] = close_notes
            if resolution_code:
                payload["close_code"] = resolution_code
            if assigned_to:
                payload["assigned_to"] = assigned_to
            if assignment_group:
                payload["assignment_group"] = assignment_group

            result = snow_patch(url, user, pwd, "incident", sys_id, payload)
            return {"success": True, "number": result.get("number"), "sys_id": sys_id}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 3. Problems ──────────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_create_problem",
        description="Create a new ServiceNow problem record.",
    )
    def snow_create_problem(
        short_description: str,
        description: str = "",
        assigned_to: str = "",
        assignment_group: str = "",
        impact: int = 3,
        urgency: int = 3,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            payload: Dict[str, Any] = {
                "short_description": short_description,
                "description": description,
                "impact": str(impact),
                "urgency": str(urgency),
                "state": "101",
            }
            if assigned_to:
                payload["assigned_to"] = assigned_to
            if assignment_group:
                payload["assignment_group"] = assignment_group

            result = snow_post(url, user, pwd, "problem", payload)
            return {
                "success": True,
                "problem_number": result.get("number"),
                "sys_id": result.get("sys_id"),
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_list_problems",
        description="List ServiceNow problem records.",
    )
    def snow_list_problems(
        state: str = "",
        search: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            query_parts = []
            if state:
                query_parts.append(f"state={state}")
            if search:
                query_parts.append(f"short_descriptionLIKE{search}")
            query = "^".join(query_parts) if query_parts else "active=true"

            rows = snow_get(url, user, pwd, "problem", {
                "sysparm_query": query + "^ORDERBYDESCsys_created_on",
                "sysparm_limit": limit,
                "sysparm_display_value": "true",
            })
            return {"success": True, "count": len(rows), "problems": rows}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 4. Changes ───────────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_create_change",
        description="Create a new ServiceNow change request.",
    )
    def snow_create_change(
        short_description: str,
        description: str = "",
        change_type: str = "normal",
        risk: str = "3",
        impact: str = "3",
        assignment_group: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            short_description: One-line summary (required).
            description: Detailed description.
            change_type: 'normal', 'standard', or 'emergency'.
            risk: 1=Critical, 2=High, 3=Moderate, 4=Low.
            impact: 1=High, 2=Medium, 3=Low.
            assignment_group: Group responsible for the change.
            start_date: Planned start date (ISO format: YYYY-MM-DD HH:MM:SS).
            end_date: Planned end date (ISO format: YYYY-MM-DD HH:MM:SS).
        """
        try:
            url, user, pwd = _get_creds()
            payload: Dict[str, Any] = {
                "short_description": short_description,
                "description": description,
                "type": change_type,
                "risk": risk,
                "impact": impact,
                "state": "-5",
            }
            if assignment_group:
                payload["assignment_group"] = assignment_group
            if start_date:
                payload["start_date"] = start_date
            if end_date:
                payload["end_date"] = end_date

            result = snow_post(url, user, pwd, "change_request", payload)
            return {
                "success": True,
                "change_number": result.get("number"),
                "sys_id": result.get("sys_id"),
                "type": change_type,
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_list_changes",
        description="List ServiceNow change requests with optional filters.",
    )
    def snow_list_changes(
        state: str = "",
        change_type: str = "",
        search: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            query_parts = []
            if state:
                query_parts.append(f"state={state}")
            if change_type:
                query_parts.append(f"type={change_type}")
            if search:
                query_parts.append(f"short_descriptionLIKE{search}")
            query = "^".join(query_parts) if query_parts else "active=true"

            rows = snow_get(url, user, pwd, "change_request", {
                "sysparm_query": query + "^ORDERBYDESCsys_created_on",
                "sysparm_limit": limit,
                "sysparm_display_value": "true",
            })
            return {"success": True, "count": len(rows), "changes": rows}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 5. Knowledge Base ────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_search_knowledge",
        description="Search ServiceNow Knowledge Base articles.",
    )
    def snow_search_knowledge(
        search: str,
        limit: int = 10,
        kb_category: str = "",
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            query = f"short_descriptionLIKE{search}^ORtextLIKE{search}^workflow_state=published"
            if kb_category:
                query += f"^kb_category.label={kb_category}"

            rows = snow_get(url, user, pwd, "kb_knowledge", {
                "sysparm_query": query,
                "sysparm_limit": limit,
                "sysparm_fields": "number,short_description,category,text,sys_id",
                "sysparm_display_value": "true",
            })
            return {"success": True, "count": len(rows), "articles": rows}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_create_kb_article",
        description="Create a new ServiceNow Knowledge Base article.",
    )
    def snow_create_kb_article(
        short_description: str,
        text: str,
        category: str = "",
        kb_knowledge_base: str = "",
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            payload: Dict[str, Any] = {
                "short_description": short_description,
                "text": text,
                "workflow_state": "draft",
            }
            if category:
                payload["category"] = category
            if kb_knowledge_base:
                payload["kb_knowledge_base"] = kb_knowledge_base

            result = snow_post(url, user, pwd, "kb_knowledge", payload)
            return {
                "success": True,
                "article_number": result.get("number"),
                "sys_id": result.get("sys_id"),
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 6. User Management ───────────────────────────────────────────────────

    @mcp.tool(
        name="snow_list_users",
        description="List ServiceNow users with optional filters.",
    )
    def snow_list_users(
        search: str = "",
        department: str = "",
        active: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            query_parts = [f"active={str(active).lower()}"]
            if search:
                query_parts.append(f"user_nameLIKE{search}^ORfirst_nameLIKE{search}^ORlast_nameLIKE{search}^ORemailLIKE{search}")
            if department:
                query_parts.append(f"department.name={department}")
            query = "^".join(query_parts)

            rows = snow_get(url, user, pwd, "sys_user", {
                "sysparm_query": query + "^ORDERBYuser_name",
                "sysparm_limit": limit,
                "sysparm_fields": "sys_id,user_name,first_name,last_name,email,title,department,active",
                "sysparm_display_value": "true",
            })
            return {"success": True, "count": len(rows), "users": rows}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_get_user",
        description="Get details of a specific ServiceNow user by username, email, or sys_id.",
    )
    def snow_get_user(user_id: str) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            sys_id = _resolve_user_sys_id(url, user, pwd, user_id)
            if not sys_id:
                return {"success": False, "error": f"User '{user_id}' not found"}

            rows = snow_get(url, user, pwd, "sys_user", {
                "sysparm_query": f"sys_id={sys_id}",
                "sysparm_limit": 1,
                "sysparm_display_value": "true",
            })
            return {"success": True, "user": rows[0] if rows else {}}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_create_user",
        description="Create a new user in ServiceNow.",
    )
    def snow_create_user(
        first_name: str,
        last_name: str,
        email: str,
        user_name: str = "",
        title: str = "",
        department: str = "",
        phone: str = "",
        active: bool = True,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            payload: Dict[str, Any] = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "active": str(active).lower(),
            }
            if user_name:
                payload["user_name"] = user_name
            if title:
                payload["title"] = title
            if department:
                payload["department"] = department
            if phone:
                payload["phone"] = phone

            result = snow_post(url, user, pwd, "sys_user", payload)
            return {
                "success": True,
                "user_name": result.get("user_name"),
                "sys_id": result.get("sys_id"),
                "email": result.get("email"),
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_update_user",
        description="Update an existing ServiceNow user's profile.",
    )
    def snow_update_user(
        user_id: str,
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        title: str = "",
        department: str = "",
        phone: str = "",
        active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            sys_id = _resolve_user_sys_id(url, user, pwd, user_id)
            if not sys_id:
                return {"success": False, "error": f"User '{user_id}' not found"}

            payload: Dict[str, Any] = {}
            if first_name:
                payload["first_name"] = first_name
            if last_name:
                payload["last_name"] = last_name
            if email:
                payload["email"] = email
            if title:
                payload["title"] = title
            if department:
                payload["department"] = department
            if phone:
                payload["phone"] = phone
            if active is not None:
                payload["active"] = str(active).lower()

            result = snow_patch(url, user, pwd, "sys_user", sys_id, payload)
            return {"success": True, "user_name": result.get("user_name"), "sys_id": sys_id}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_deactivate_user",
        description="Deactivate (disable) a ServiceNow user account.",
    )
    def snow_deactivate_user(user_id: str) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            sys_id = _resolve_user_sys_id(url, user, pwd, user_id)
            if not sys_id:
                return {"success": False, "error": f"User '{user_id}' not found"}
            snow_patch(url, user, pwd, "sys_user", sys_id, {"active": "false"})
            return {"success": True, "message": f"User '{user_id}' has been deactivated", "sys_id": sys_id}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(
        name="snow_assign_user_to_group",
        description="Add a ServiceNow user to a group.",
    )
    def snow_assign_user_to_group(user_id: str, group_name: str) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            headers = get_snow_headers(user, pwd)
            base = url

            sys_id = _resolve_user_sys_id(base, user, pwd, user_id)
            if not sys_id:
                return {"success": False, "error": f"User '{user_id}' not found"}

            # Look up group
            r = requests.get(
                f"{base}/api/now/table/sys_user_group",
                headers=headers,
                params={"sysparm_query": f"name={group_name}", "sysparm_limit": 1, "sysparm_fields": "sys_id,name"},
                timeout=15, verify=_VERIFY,
            )
            if not r.ok or not r.json().get("result"):
                return {"success": False, "error": f"Group '{group_name}' not found"}
            group_sys_id = r.json()["result"][0]["sys_id"]

            result = snow_post(base, user, pwd, "sys_user_grmember",
                               {"user": sys_id, "group": group_sys_id})
            return {
                "success": True,
                "message": f"User added to group '{group_name}'",
                "member_sys_id": result.get("sys_id"),
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 7. Business Rules (AI-powered GlideScript generation) ────────────────

    @mcp.tool(
        name="snow_generate_business_rule",
        description=(
            "AI-powered: Generate a ServiceNow Business Rule GlideScript from a plain-English description. "
            "Optionally deploys it directly to ServiceNow."
        ),
    )
    def snow_generate_business_rule(
        description: str,
        table_name: str,
        when: str = "before",
        action: str = "insert",
        rule_name: str = "",
        deploy: bool = False,
        update_set_name: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            description: Plain-English description of what the rule should do.
            table_name: ServiceNow table the rule runs on (e.g. 'incident').
            when: 'before', 'after', or 'async'.
            action: 'insert', 'update', 'delete', or 'query'.
            rule_name: Name for the rule (auto-generated if blank).
            deploy: If True, create the rule in ServiceNow immediately.
            update_set_name: Update set to capture the change (optional).
        """
        try:
            url, user, pwd = _get_creds()
        except ValueError as e:
            return {"error": str(e)}
        if when not in ("before", "after", "async", "display"):
            return {"success": False, "error": f"Invalid 'when' value '{when}': use before, after, async or display"}
        actions = {a.strip().lower() for a in str(action).replace("/", ",").split(",") if a.strip()}
        if when != "display" and not actions & {"insert", "update", "delete", "query"}:
            return {"success": False, "error": f"Invalid 'action' value '{action}': use insert, update, delete or "
                                               "query (comma-separated for several) — otherwise the rule never runs"}

        # AI generation
        prompt = (
            f"Generate a ServiceNow Business Rule GlideScript for the following requirement.\n\n"
            f"Table: {table_name}\n"
            f"When: {when}\n"
            f"Action: {action}\n"
            f"Requirement: {description}\n\n"
            "Return ONLY valid GlideScript JavaScript code, no explanations."
        )
        system = (
            "You are a ServiceNow expert. Generate clean, production-ready GlideScript business rule code. "
            "Use current.setValue(), current.getValue(), gs.addInfoMessage() etc. correctly. "
            "Return only the JavaScript code block, no markdown."
        )
        try:
            script = groq_client.generate(prompt, max_tokens=2000, temperature=0.3)
        except Exception as e:
            return {"success": False, "error": f"AI generation failed: {e}"}

        # The model sometimes wraps the code in a markdown fence; the rule needs the executeRule wrapper
        script = re.sub(r"^\s*```[a-zA-Z]*\s*\n?|\n?\s*```\s*$", "", script or "").strip()
        script = sn_build.wrap_br(script)
        generated_name = rule_name or f"AI Rule - {table_name} {when} {action}".title()
        result: Dict[str, Any] = {
            "success": True,
            "rule_name": generated_name,
            "table": table_name,
            "when": when,
            "action": action,
            "script": script,
            "deployed": False,
        }
        syntax = sn_build.js_syntax_error(script)
        if syntax:
            result["syntax_error"] = syntax

        if deploy:
            if syntax:
                result["deploy_error"] = f"Not deployed: the generated script has a syntax error ({syntax})"
                return result
            payload = {
                "name": generated_name,
                "collection": table_name,
                "when": when,
                "action_insert": "true" if "insert" in actions else "false",
                "action_update": "true" if "update" in actions else "false",
                "action_delete": "true" if "delete" in actions else "false",
                "action_query": "true" if "query" in actions else "false",
                "script": script,
                # Without advanced=true ServiceNow ignores the script field and the rule does nothing
                "advanced": "true",
                "active": "true",
            }
            try:
                existing = snow_get(url, user, pwd, "sys_script", {
                    "sysparm_query": f"name={generated_name}^collection={table_name}",
                    "sysparm_fields": "sys_id,name", "sysparm_limit": "1"})
                if existing:
                    result["deploy_error"] = (f"Not deployed: a business rule named '{generated_name}' already exists "
                                              f"on '{table_name}' (sys_id {existing[0].get('sys_id')}). Review or "
                                              "update that rule instead of creating a duplicate.")
                    result["existing_sys_id"] = existing[0].get("sys_id")
                    return result
                deployed = snow_post(url, user, pwd, "sys_script", payload, update_set_name)
                sys_id = deployed.get("sys_id")
                result["sys_id"] = sys_id
                # Read the persisted record back: report what ServiceNow stored, not what was sent
                stored = snow_get(url, user, pwd, "sys_script", {
                    "sysparm_query": f"sys_id={sys_id}", "sysparm_limit": "1",
                    "sysparm_fields": ",".join(k for k in payload if k != "script") + ",script"})
                stored = stored[0] if stored else {}
                mismatched = [k for k, v in payload.items() if str(stored.get(k, "")).strip() != str(v).strip()]
                result["deployed"] = bool(stored)
                result["verified"] = bool(stored) and not mismatched
                if not stored:
                    result["deploy_error"] = "Created, but the rule could not be read back to verify it"
                elif mismatched:
                    result["verification_mismatch"] = mismatched
            except Exception as e:
                result["deploy_error"] = str(e)

        return result

    # ── 7b. Requirement pipeline: plan (read-only) and confirmed implementation ──────

    def _req_out(plan: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: plan.get(k) for k in ("plan_id", "status", "instance", "previous_plan_id", "problems")
               if plan.get(k) is not None}
        out["report"] = plan.get("report")
        return json.loads(json.dumps(out, default=str))

    @mcp.tool(
        name="snow_plan_requirement",
        description=(
            "Plan a configuration requirement or user story (make a field mandatory / read-only / hidden on a form or "
            "for every record, set or assign values, business rule, client script, UI policy, data policy, assignment "
            "rule, validation). Inspects the existing rules on the table, finds what already does it, conflicts and "
            "missing information, and returns a plan with a report. Changes nothing."
        ),
        annotations={"readOnlyHint": True},
    )
    def snow_plan_requirement(request: str, generate_scripts: bool = True) -> Dict[str, Any]:
        """
        Args:
            request: The requirement or user story in plain language (include acceptance criteria if any).
            generate_scripts: When false, no script is written for custom logic (and no text is invented for it);
                each such part comes back as a question instead.
        """
        try:
            url, user, pwd = _get_creds()
            plan = rp.plan_requirement(snc.SNClient(url, user, pwd), text=request, generate_scripts=generate_scripts)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except snc.SNError as e:
            return {"success": False, "error": str(e), "error_kind": rp.classify_error(e)}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        return _req_out(plan)

    @mcp.tool(
        name="snow_implement_requirement",
        description=(
            "Implement a requirement plan made by snow_plan_requirement (by plan_id): builds the planned rules / "
            "policies in an update set, then reads every record back to verify it. Needs the user's confirmation. "
            "decisions answers items that overlap or conflict with existing configuration."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    def snow_implement_requirement(plan_id: str, decisions: Optional[Dict[str, str]] = None, confirm: bool = False,
                                   dry_run: bool = False, require_preflight: bool = False) -> Dict[str, Any]:
        """
        Args:
            plan_id: The plan_id returned by snow_plan_requirement.
            decisions: Optional {item_id: create | reuse | modify | skip} for items that need a decision.
            confirm: Must be true to change ServiceNow (set by the client after the user confirms).
            dry_run: Return the plan with the decisions applied, without saving or deploying.
            require_preflight: Require verified release, environment, identity, scope, Update Set and planner tools.
        """
        try:
            url, user, pwd = _get_creds()
            sn = snc.SNClient(url, user, pwd)
            if require_preflight:
                readiness = rp.planner_preflight(
                    sn, ["snow_plan_requirement", "snow_implement_requirement"])
                if not readiness["execution_ready"]:
                    return {"success": False, "status": "preflight_blocked",
                            "error": "; ".join(readiness["blockers"]), "preflight": readiness}
            if dry_run or not confirm:
                plan = rp.load_plan(sn.instance, plan_id)
                if not plan:
                    return {"success": False, "error": f"Plan {plan_id} not found for this instance"}
                problems = rp.apply_decisions(plan["items"], decisions or {})
                plan["status"] = rp.plan_status(plan["requirement"], plan["items"])
                plan["report"] = rp.build_report(plan)
                out = _req_out({**plan, "problems": problems or None})
                return {**out, "dry_run": True, "requires_confirmation": plan["status"] == "awaiting_confirmation"}
            plan = rp.deploy_plan(sn, plan_id, confirm=True, decisions=decisions)
            story = plan.get("story") or {}
            if plan.get("deployment") and story.get("table") and story.get("sys_id"):
                try:
                    sn.patch(story["table"], story["sys_id"], {"work_notes": rp.work_note(plan)})
                except snc.SNError as e:
                    logger.warning("could not add the requirement work note: %s", e)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except LookupError as e:
            return {"success": False, "error": str(e)}
        except snc.SNError as e:
            return {"success": False, "error": str(e), "error_kind": rp.classify_error(e)}
        out = _req_out(plan)
        out["success"] = plan.get("status") in ("deployed", "already_satisfied")
        return out

    # ── 8. Health Scan ──────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_health_scan",
        description=(
            "Run a health scan on a ServiceNow instance: checks open incidents, "
            "in-progress changes, active problems, and empty update sets."
        ),
    )
    def snow_health_scan(limit: int = 50) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()

            # Open incidents
            open_incidents = snow_get(url, user, pwd, "incident", {
                "sysparm_query": "state!=6^state!=7",
                "sysparm_limit": limit,
                "sysparm_fields": "number,short_description,state,urgency,priority",
                "sysparm_display_value": "true",
            })

            # In-progress changes
            active_changes = snow_get(url, user, pwd, "change_request", {
                "sysparm_query": "state!=-4^state!=3",
                "sysparm_limit": limit,
                "sysparm_fields": "number,short_description,state,type",
                "sysparm_display_value": "true",
            })

            # Active problems
            active_problems = snow_get(url, user, pwd, "problem", {
                "sysparm_query": "active=true",
                "sysparm_limit": limit,
                "sysparm_fields": "number,short_description,state",
                "sysparm_display_value": "true",
            })

            # High-urgency incidents
            p1_incidents = [i for i in open_incidents if str(i.get("urgency", {}).get("value", i.get("urgency", ""))).strip() in ("1", "2")]

            summary = {
                "open_incidents": len(open_incidents),
                "active_changes": len(active_changes),
                "active_problems": len(active_problems),
                "high_urgency_incidents": len(p1_incidents),
            }

            health_score = 100
            if summary["open_incidents"] > 20:
                health_score -= 20
            if summary["high_urgency_incidents"] > 0:
                health_score -= min(30, summary["high_urgency_incidents"] * 10)
            if summary["active_problems"] > 5:
                health_score -= 10
            health_score = max(0, health_score)

            return {
                "success": True,
                "health_score": health_score,
                "summary": summary,
                "open_incidents": open_incidents[:10],
                "active_changes": active_changes[:10],
                "active_problems": active_problems[:5],
                "p1_p2_incidents": p1_incidents[:5],
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 9. Update Sets ──────────────────────────────────────────────────────

    @mcp.tool(
        name="snow_list_update_sets",
        description="List ServiceNow update sets.",
    )
    def snow_list_update_sets(
        state: str = "in progress",
        limit: int = 20,
    ) -> Dict[str, Any]:
        try:
            url, user, pwd = _get_creds()
            rows = snow_get(url, user, pwd, "sys_update_set", {
                "sysparm_query": f"state={state}^ORDERBYDESCsys_updated_on",
                "sysparm_limit": limit,
                "sysparm_fields": "sys_id,name,state,description,sys_created_on,sys_updated_on",
                "sysparm_display_value": "true",
            })
            return {"success": True, "count": len(rows), "update_sets": rows}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 10. AI: GlideScript / Script query ────────────────────────────────────

    @mcp.tool(
        name="snow_ai_query",
        description=(
            "Ask an AI question about ServiceNow or generate any ServiceNow script, policy, "
            "or workflow using natural language."
        ),
    )
    def snow_ai_query(question: str) -> Dict[str, Any]:
        """
        Args:
            question: Natural-language question or code generation request.
        """
        system = (
            "You are a ServiceNow expert AI assistant. Answer questions about ServiceNow "
            "configuration, ITSM processes, GlideScript, REST API, and best practices. "
            "When generating code, produce clean, production-ready GlideScript or REST API examples."
        )
        try:
            answer = groq_client.generate(question, max_tokens=2000, temperature=0.5)
            return {"success": True, "answer": answer}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 11. List available MCP tools ─────────────────────────────────────────

    @mcp.tool(
        name="snow_list_tools",
        description="List all available Snow Agent MCP tools and their descriptions.",
    )
    def snow_list_tools() -> Dict[str, Any]:
        tools = [
            {"name": "snow_check_connection", "description": "Test connectivity to ServiceNow"},
            {"name": "snow_create_incident", "description": "Create a new incident"},
            {"name": "snow_list_incidents", "description": "List incidents with filters"},
            {"name": "snow_get_incident", "description": "Get a specific incident by number or sys_id"},
            {"name": "snow_update_incident", "description": "Update an existing incident"},
            {"name": "snow_create_problem", "description": "Create a problem record"},
            {"name": "snow_list_problems", "description": "List problem records"},
            {"name": "snow_create_change", "description": "Create a change request"},
            {"name": "snow_list_changes", "description": "List change requests"},
            {"name": "snow_search_knowledge", "description": "Search Knowledge Base articles"},
            {"name": "snow_create_kb_article", "description": "Create a Knowledge Base article"},
            {"name": "snow_list_users", "description": "List users with filters"},
            {"name": "snow_get_user", "description": "Get a specific user"},
            {"name": "snow_create_user", "description": "Create a new user"},
            {"name": "snow_update_user", "description": "Update user profile"},
            {"name": "snow_deactivate_user", "description": "Deactivate a user account"},
            {"name": "snow_assign_user_to_group", "description": "Add user to a group"},
            {"name": "snow_generate_business_rule", "description": "AI: Generate & optionally deploy a Business Rule"},
            {"name": "snow_plan_requirement", "description": "Plan a configuration requirement (inspect, compare, plan)"},
            {"name": "snow_implement_requirement", "description": "Implement a confirmed requirement plan and verify it"},
            {"name": "snow_health_scan", "description": "Run instance health scan"},
            {"name": "snow_list_update_sets", "description": "List update sets"},
            {"name": "snow_ai_query", "description": "Ask AI anything about ServiceNow"},
        ] + [{"name": t.name, "description": t.description} for t in itam_bridge.TOOLS] \
          + [{"name": t.name, "description": t.description} for t in itom_bridge.TOOLS]
        return {
            "server": "SoftProdigy Snow Agent MCP",
            "version": "1.0.0",
            "tool_count": len(tools),
            "tools": tools,
            "itam_tools": len(itam_bridge.TOOLS),
            "itom_tools": len(itom_bridge.TOOLS),
            "transport": MCP_TRANSPORT,
            "endpoint": f"http://{MCP_HOST}:{MCP_PORT}{MCP_PATH}",
            "credential_headers": [
                SNOW_INSTANCE_HEADER,
                SNOW_USERNAME_HEADER,
                SNOW_PASSWORD_HEADER,
            ],
            "environment_credentials_enabled": os.getenv("SNOW_ALLOW_ENV_CREDS", "false").strip().lower() == "true",
            "optional_headers": [
                MCP_API_KEY_HEADER,
                SNOW_PASSWORD_B64_HEADER,
            ],
        }

    # ── 12. IT Asset Management (shared snow_itam package, see itam_bridge) ───
    itam_bridge.register_fastmcp(mcp, _get_creds)

    # ── 13. IT Operations Management (shared snow_itom package, see itom_bridge) ───
    itom_bridge.register_fastmcp(mcp, _get_creds)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="SoftProdigy Snow Agent — MCP server")
    parser.add_argument("--host", default=MCP_HOST)
    parser.add_argument("--port", type=int, default=MCP_PORT)
    parser.add_argument("--path", default=MCP_PATH)
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=MCP_TRANSPORT,
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=MCP_LOG_LEVEL,
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    logger.info(
        "Starting SoftProdigy Snow Agent MCP on %s:%s%s (transport=%s)",
        args.host, args.port, args.path, args.transport,
    )

    server = create_server()
    server.run(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()
