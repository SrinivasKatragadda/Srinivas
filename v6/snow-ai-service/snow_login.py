"""
ServiceNow login check for the SoftProdigy Snow Agent UI.

v5's connect screen said "✅ Connected" without ever calling ServiceNow. This endpoint
actually authenticates, and reports what the account can do so the UI can warn early
(e.g. no admin role -> builds will fail; Agile plugin missing -> no rm_story).
"""

import asyncio
import os
import secrets

import requests
from fastapi import APIRouter, HTTPException

import sn_client as snc
from sn_client import SNError
import sn_adapter

router = APIRouter(tags=["ServiceNow login"])


@router.post("/snow/login")
async def snow_login(body: dict):
    sn = snc.client_from(body)

    # Generate a session ID now so we can register it on success.
    session_id = secrets.token_hex(32)

    def work():
        try:
            user = sn.user()
        except SNError as e:
            if e.status == 401:
                detail = e.detail or e.message or ""
                if "required to provide auth information" in detail.lower():
                    # ServiceNow sends this same answer for a wrong password AND for an instance that refuses Basic
                    # auth, so both causes are named — never only the OAuth one.
                    raise HTTPException(status_code=401,
                        detail=f"ServiceNow rejected the sign-in for '{sn.username}' on {sn.instance}. Either the "
                               "username or password is wrong (check them exactly as in your developer portal), or "
                               "this instance refuses Basic authentication for its REST API — then enable inbound "
                               "Basic API authentication, or create an OAuth client (System OAuth > Application "
                               "Registry) and set SNOW_CLIENT_ID and SNOW_CLIENT_SECRET in the agent's .env, then "
                               "restart it.")
                raise HTTPException(status_code=401,
                    detail=f"ServiceNow rejected the credentials for '{sn.username}' on {sn.instance}"
                           + (f" — {detail}" if detail else "") +
                           ". Check the username and password exactly as shown in your developer portal.")
            if e.status == 404:
                raise HTTPException(status_code=401,
                    detail=f"User '{sn.username}' not found on {sn.instance}. "
                           "Make sure the username matches exactly (e.g. 'admin', not 'Admin').")
            if e.status == 495:
                raise HTTPException(status_code=502, detail=f"TLS certificate problem: {e.detail}")
            if e.status == 503:
                raise HTTPException(status_code=502, detail=f"Cannot reach {sn.instance}: {e.detail}")
            raise e.to_http("Login")
        roles = sn.query("sys_user_has_role", f"user={user['sys_id']}", "role", limit=200, display_value="true")
        role_names = sorted({(r.get("role") or {}).get("display_value") if isinstance(r.get("role"), dict)
                             else r.get("role") for r in roles} - {None, ""})
        is_admin = "admin" in role_names
        caps = {}
        for label, table in (("agile_rm_story", "rm_story"), ("problem", "problem"), ("change", "change_request"),
                             ("knowledge", "kb_knowledge"), ("catalog", "sc_cat_item"), ("atf", "sys_atf_test")):
            try:
                caps[label] = sn.table_exists(table)
            except SNError:
                caps[label] = None
        build = None
        try:
            build = sn.property("glide.buildname")
        except SNError:
            pass
        warnings = []
        if not is_admin:
            warnings.append("This account does not have the admin role — building apps, tables and update sets "
                            "will fail. Record operations (incidents, changes) still work.")
        if not caps.get("agile_rm_story"):
            warnings.append("rm_story does not exist (Agile Development plugin inactive) — stories will be "
                            "created as tasks instead.")
        # Register session so X-Session-ID header flow works for all /sn/tools/* endpoints.
        sn_adapter.register_session(session_id, {
            "instance_url": sn.instance,
            "username": sn.username,
            "password": body.get("snow_pass") or body.get("password", ""),
        })

        return {"connected": True, "instance": sn.instance, "instance_name": snc.instance_name(sn.instance),
                "session_id": session_id,
                "user": {"sys_id": user["sys_id"], "user_name": user.get("user_name"), "name": user.get("name")},
                "roles": role_names[:50], "is_admin": is_admin, "build": build, "capabilities": caps,
                "tls_verified": snc.verify_tls(), "warnings": warnings}
    return await asyncio.to_thread(work)


@router.post("/snow/logout")
async def snow_logout(body: dict):
    """Remove a session from the adapter store (clears X-Session-ID credentials)."""
    session_id = body.get("session_id", "")
    if session_id:
        sn_adapter.unregister_session(session_id)
    return {"logged_out": True}


@router.post("/snow/debug-login")
async def debug_login(body: dict):
    """Raw debug endpoint — shows exactly what ServiceNow returns. Use when login fails."""
    url = (body.get("snow_url") or "").strip().rstrip("/")
    user = (body.get("snow_user") or "").strip()
    pw = body.get("snow_pass") or ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        url = f"{parsed.scheme}://{parsed.hostname}"
    except Exception:
        pass
    target = f"{url}/api/now/table/sys_user?sysparm_query=user_name%3D{user}&sysparm_limit=1&sysparm_fields=sys_id,user_name,name"
    verify = snc.verify_tls()
    try:
        r = requests.get(target, auth=(user, pw), timeout=20,
                         headers={"Accept": "application/json"}, verify=verify)
        try:
            body_json = r.json()
        except Exception:
            body_json = None
        return {
            "url_called": target,
            "http_status": r.status_code,
            "ok": r.ok,
            "response_body": body_json if body_json else r.text[:500],
            "username_sent": user,
            "password_length": len(pw),
        }
    except requests.exceptions.SSLError as e:
        return {"error": "TLS", "detail": str(e)[:300]}
    except requests.exceptions.RequestException as e:
        return {"error": "Network", "detail": str(e)[:300]}


async def agent_config():
    """Public, non-secret feature flags for the UI."""
    return {"api_key_required": bool(os.getenv("AGENT_API_KEY")),
            "flow_server_configured": bool(os.getenv("FLOW_SERVER_URL")),
            "jira_configured": bool(os.getenv("JIRA_URL") and os.getenv("JIRA_API_TOKEN")),
            "tls_verified": snc.verify_tls(), "version": "6.1",
            "instance_url": snc.default_instance_url()}
