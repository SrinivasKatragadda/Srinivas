"""
SoftProdigy Snow Agent - Core Endpoints
All ServiceNow AI agent features:
- Minimum text → full BRD/FRD/TSD generation
- Extract from any file
- Plan generation
- Build pipeline (app scope → current update set → roles/tables/fields/ACLs/scripts/flows → capture check)
- Universal artifact manager (schema, list, read, create, update and delete for any permitted table)
- Update set management (list / set current / changes / complete)
- Live health scan (sn_health)
- User Stories from documents
- Document download (DOCX)
"""

import io
import os
import re
import json
import base64
import asyncio
import logging
import tempfile
import traceback
import requests
from collections import Counter
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

import groq_client
import tool_match
import docx_render as dr
import sn_client as snc
import sn_build
import sn_health
import arch_diagram
import requirement_pipeline as rp
from sn_client import SNError

logger = logging.getLogger(__name__)
snow_router = APIRouter()

# Table API paths are constructed from this value.  Keep it deliberately
# narrower than a URL path so callers cannot turn an artifact request into a
# request for another ServiceNow endpoint.
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,79}$")
_FIELD_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.]{0,127}$")
_SYSTEM_MANAGED_FIELDS = {"sys_id", "sys_created_on", "sys_created_by", "sys_updated_on", "sys_updated_by", "sys_mod_count"}

# Server-side ServiceNow credentials are only used when SNOW_ALLOW_ENV_CREDS=true
_ENV_CREDS = os.getenv("SNOW_ALLOW_ENV_CREDS", "false").lower() == "true"
SNOW_INSTANCE_URL = os.getenv("SNOW_INSTANCE_URL", "") if _ENV_CREDS else ""
SNOW_USER = os.getenv("SNOW_USER", "") if _ENV_CREDS else ""
SNOW_PASS = os.getenv("SNOW_PASS", "") if _ENV_CREDS else ""


class AIUnavailable(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=503, detail=detail)


# ─── AI Provider (Groq, via groq_client with automatic model fallback) ─────

async def _call_claude(prompt: str, system: str = "", max_tokens: int = 8192) -> str:
    """AI text call. Raises 503 when the AI is unavailable (never returns error text as content)."""
    try:
        text = await groq_client.agenerate(prompt, system=system, max_tokens=min(max_tokens, 8192))
    except Exception as e:
        logger.error(f"Groq call failed: {e}")
        raise AIUnavailable(f"AI service unavailable: {e}")
    if not (text or "").strip():
        raise AIUnavailable("AI returned an empty answer, please retry")
    return text


async def _ai_json(prompt: str, system: str = "", max_tokens: int = 7000) -> dict:
    """Ask Groq for JSON; raise a clear HTTP error instead of returning half-empty data."""
    try:
        data = await groq_client.agenerate_json(prompt, system=system, max_tokens=max_tokens)
    except groq_client.GroqError as e:
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI returned an unreadable answer, please retry: {e}")
    if not isinstance(data, dict):
        data = {"items": data}
    return data


@snow_router.get("/snow/ai-status")
async def ai_status():
    """Check that the Groq key works and which model is being used."""
    return await asyncio.to_thread(groq_client.status)


# ─── Local MCP bridge ───
# The browser never connects to the MCP server directly: that would expose its
# optional MCP API key and require browser CORS support.  This bridge opens a
# short-lived Streamable HTTP session per request, passing the signed-in user's
# ServiceNow credentials as headers (the MCP server never stores them).
# Chat flow: list the MCP tools -> AI picks one -> read tools run at once,
# write tools come back as a proposal the user must confirm -> no tool fits:
# the conversational snow_ai_query tool answers.
_MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:8002/mcp").rstrip("/")
_MCP_PROTOCOL_VERSION = "2025-03-26"
_MCP_CHAT_ONLY = {"snow_ai_query", "snow_list_tools"}
_MCP_READ_PREFIXES = ("snow_list_", "snow_get_", "snow_search_", "snow_check_", "snow_health_")


def _mcp_event_payload(response: requests.Response) -> dict:
    """Read the JSON-RPC result embedded in a Streamable HTTP SSE response.
    One SSE event may span several `data:` lines (long answers); they are joined with newlines per the SSE spec."""
    events, current = [], []
    # Decode as UTF-8 (the MCP spec's encoding): text/event-stream carries no charset, so requests would guess
    # Latin-1 and turn every non-ASCII character (e.g. '—' in a tool message) into mojibake or break the JSON.
    for line in response.content.decode("utf-8", errors="replace").splitlines():
        if line.startswith("data:"):
            current.append(line[5:].lstrip(" "))
        elif not line.strip() and current:
            events.append("\n".join(current))
            current = []
    if current:
        events.append("\n".join(current))
    for data in events:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MCP returned malformed JSON") from exc
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            return parsed
    if events:
        raise RuntimeError("MCP returned no JSON-RPC result")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("MCP returned no JSON-RPC result") from exc


class _McpSession:
    """One Streamable HTTP MCP session to the local Snow Agent MCP server."""

    def __init__(self, creds: Optional[dict] = None):
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        mcp_key = os.getenv("MCP_API_KEY", "").strip()
        if mcp_key:
            self.headers["x-api-key"] = mcp_key
        creds = creds or {}
        if creds.get("snow_url") and creds.get("snow_user") and creds.get("snow_pass"):
            self.headers["x-servicenow-instance-url"] = str(creds["snow_url"])
            self.headers["x-servicenow-username"] = str(creds["snow_user"])
            self.headers["x-servicenow-password-b64"] = base64.b64encode(str(creds["snow_pass"]).encode()).decode()
        self._id = 0
        envelope, started = self._rpc("initialize", {"protocolVersion": _MCP_PROTOCOL_VERSION, "capabilities": {},
                                                     "clientInfo": {"name": "snow-agent-ui", "version": "1.1"}})
        session_id = started.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("MCP did not return a session id")
        self.headers["mcp-session-id"] = session_id
        self.headers["MCP-Protocol-Version"] = _MCP_PROTOCOL_VERSION
        try:
            requests.post(_MCP_URL, headers=self.headers,
                          json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                          timeout=20).raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"MCP server is unavailable at {_MCP_URL}: {exc}") from exc

    def _rpc(self, method: str, params: dict, timeout: int = 20):
        self._id += 1
        try:
            response = requests.post(_MCP_URL, headers=self.headers, timeout=timeout,
                                     json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"MCP server is unavailable at {_MCP_URL}: {exc}") from exc
        envelope = _mcp_event_payload(response)
        if envelope.get("error"):
            raise RuntimeError(str(envelope["error"].get("message") or "MCP request failed"))
        return envelope.get("result") or {}, response

    def list_tools(self) -> List[dict]:
        return (self._rpc("tools/list", {})[0] or {}).get("tools") or []

    def call(self, name: str, arguments: dict) -> Any:
        result, _ = self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout=120)
        if result.get("structuredContent") is not None:
            data = result["structuredContent"]
            data = data.get("result", data) if isinstance(data, dict) and list(data) == ["result"] else data
        else:
            text = "\n".join(str(i.get("text") or "") for i in result.get("content") or []
                             if i.get("type") == "text").strip()
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                data = {"text": text}
        if result.get("isError"):
            msg = data.get("text") if isinstance(data, dict) else str(data)
            raise HTTPException(status_code=422, detail=str(msg or "The tool reported an error")[:1500])
        return data


def _mcp_tool_kind(tool: dict) -> str:
    hint = (tool.get("annotations") or {}).get("readOnlyHint")
    if hint is not None:
        return "read" if hint else "write"
    return "read" if tool.get("name", "").startswith(_MCP_READ_PREFIXES) else "write"


def _mcp_shortlist(message: str, tools: List[dict], k: int = 12) -> List[dict]:
    return tool_match.rank(message, tools, k, skip=_MCP_CHAT_ONLY)


_REQUIREMENT_CODES = {"PLUGIN_REQUIRED": "plugin", "INCOMPATIBLE_INSTANCE": "incompatible", "FORBIDDEN": "role"}


def _plugin_required(name: str, result: Any) -> Optional[dict]:
    """A tool result saying something the instance or user lacks (a plugin, the documented table layout, a role)
    becomes a clear reply saying what to do — never a general AI answer. Tools check the instance on every call, so
    the same request works as soon as the plugin / role is in place."""
    kind = _REQUIREMENT_CODES.get(result.get("code")) if isinstance(result, dict) else None
    if not kind:
        return None
    what = str(result.get("error") or "This is not available on this instance")
    detail = str(result.get("detail") or "")
    plugin = (re.findall(r"\b(com\.[a-z0-9_.\-]+)", detail) or [""])[0].rstrip(".") if kind == "plugin" else ""
    if kind == "plugin":
        steps = (f"Activate {plugin} in ServiceNow (System Definition > Plugins), then send the same request again."
                 if plugin else "Activate the plugin named above in ServiceNow, then send the same request again.")
    elif kind == "role":
        steps = "Ask a ServiceNow administrator for one of the roles named above, then send the same request again."
    else:
        steps = "Nothing was changed. This instance differs from the documented layout; tell your administrator."
    return {"ok": True, "mode": "plugin_required" if kind == "plugin" else "requirement_missing",
            "requirement": kind, "tool": name, "plugin": plugin, "message": what, "detail": detail,
            "answer": ". ".join(p.rstrip(".") for p in (what, detail, steps) if p) + ".", "via": "ServiceNow MCP"}


def _planner_preflight_report(sn: snc.SNClient, creds: dict) -> dict:
    try:
        session = _McpSession(creds)
        tools = session.list_tools()
        return rp.planner_preflight(sn, [tool.get("name", "") for tool in tools])
    except Exception as exc:
        return rp.planner_preflight(sn, mcp_error=f"MCP readiness could not be verified: {exc}")


def _compact_input(schema: dict) -> dict:
    props, req = schema.get("properties") or {}, set(schema.get("required") or [])
    out = {}
    for name, d in list(props.items())[:20]:
        typ = d.get("type") or (d.get("anyOf") or [{}])[0].get("type", "any")
        # lists / objects need their shape, or the AI guesses keys (e.g. a CI name put into 'fields')
        if typ in ("array", "object") and d.get("description"):
            typ = f"{typ}: {str(d['description'])[:120]}"
        out[name + ("*" if name in req else "")] = typ
    return out


def _mcp_answer(session: _McpSession, message: str) -> dict:
    data = session.call("snow_ai_query", {"question": message})
    if isinstance(data, dict) and data.get("success") is False:
        raise HTTPException(status_code=502, detail=str(data.get("error") or "MCP chat failed"))
    answer = (data.get("answer") if isinstance(data, dict) else None) or (data.get("text") if isinstance(data, dict)
                                                                         else str(data))
    return {"ok": True, "mode": "answer", "answer": answer or "(empty answer)", "via": "ServiceNow MCP"}


def _mcp_chat_call(message: str, creds: dict, confirm: Optional[dict] = None) -> dict:
    session = _McpSession(creds)
    if confirm:   # the user approved a proposal shown earlier
        name = str(confirm.get("tool") or "")
        tools = {t["name"]: t for t in session.list_tools()}
        if name not in tools or name in _MCP_CHAT_ONLY:
            raise HTTPException(status_code=404, detail=f"Unknown MCP tool '{name}'")
        args = dict(confirm.get("arguments") or {})
        if name == "snow_implement_requirement" and args.get("require_preflight") is True:
            sn = snc.client_from(creds)
            readiness = rp.planner_preflight(sn, list(tools))
            if not readiness["execution_ready"]:
                raise HTTPException(status_code=412,
                                    detail="Planner preflight blocked MCP execution: "
                                    + "; ".join(readiness["blockers"]))
        if "confirm" in ((tools[name].get("inputSchema") or {}).get("properties") or {}) and not args.get("dry_run"):
            args["confirm"] = True
        result = session.call(name, args)
        return _plugin_required(name, result) or {
            "ok": True, "mode": "tool_result", "tool": name, "kind": _mcp_tool_kind(tools[name]),
            "arguments": args, "result": result, "via": "ServiceNow MCP"}
    if not (creds.get("snow_user") and creds.get("snow_pass")):
        return _mcp_answer(session, message)
    cands = _mcp_shortlist(message, session.list_tools())
    menu = [{"tool": t["name"], "does": (t.get("description") or "")[:180], "kind": _mcp_tool_kind(t),
             "args": _compact_input(t.get("inputSchema") or {})} for t in cands]
    prompt = (f"User request: {message}\n\nIf the request asks to look up, create or change ServiceNow data, pick "
              f"the ONE tool that does exactly that and fill its arguments from the request (* = required; omit what "
              f"you do not know; never invent sys_ids). Questions about the user's own instance or data (what is "
              f"installed or supported, which records exist, what depends on what) are lookups: pick the tool. Never "
              f"pick a tool for a different kind of record (e.g. changes for a request about requests): return tool "
              f"null instead. If a tool fits but the request does not give all required values, still pick it and "
              f"list those values in missing. request_type is 'instance' when the user wants data from or a change "
              f"in their ServiceNow instance, 'general' for general questions, explanations or code."
              f"\n\nTools:\n{json.dumps(menu, indent=0)}\n\n"
              'Return JSON: {"tool": "<name or null>", "arguments": {...}, "missing": ["<required args not in the '
              'request>"], "request_type": "instance|general", "reason": "<one sentence>"}')
    try:
        pick = groq_client.generate_json(prompt, system="You map requests to ServiceNow MCP tool calls. JSON only.",
                                         max_tokens=900)
    except (groq_client.GroqError, ValueError):
        pick = {}
    by_name = {t["name"]: t for t in cands}
    name = (pick or {}).get("tool")
    if not name or name not in by_name:
        if str((pick or {}).get("request_type") or "").lower() == "instance":
            # An instance request no tool can do: say so — a general AI answer would invent records or steps
            return {"ok": True, "mode": "no_tool", "via": "ServiceNow MCP",
                    "answer": "No MCP Chat tool can do this yet, so nothing was looked up or changed. The AI Agent page "
                              "has more ServiceNow tools — try the same request there."}
        return _mcp_answer(session, message)
    tool = by_name[name]
    args = pick.get("arguments") or {}
    out = {"ok": True, "tool": name, "kind": _mcp_tool_kind(tool), "arguments": args,
           "reason": pick.get("reason", ""), "description": (tool.get("description") or "")[:300],
           "via": "ServiceNow MCP"}
    missing = [m.rstrip("*") for m in (pick.get("missing") or []) if m]
    if missing:
        return {**out, "mode": "needs_input", "missing": missing}
    if out["kind"] == "write":
        # Dry run first when the tool supports it: a missing plugin / role, an unknown record or a duplicate is shown
        # now, instead of asking the user to approve a change that cannot work.
        if "dry_run" in ((tool.get("inputSchema") or {}).get("properties") or {}):
            try:
                preview = session.call(name, {**args, "dry_run": True})
            except HTTPException as exc:
                return {**out, "mode": "tool_result", "result": {"success": False, "error": str(exc.detail)}}
            blocked = _plugin_required(name, preview)
            if blocked:
                return blocked
            if isinstance(preview, dict) and preview.get("success") is False and not preview.get("dry_run") \
                    and not preview.get("requires_confirmation"):
                return {**out, "mode": "tool_result", "result": preview}
            out["preview"] = preview
        return {**out, "mode": "proposal"}
    result = session.call(name, args)
    return _plugin_required(name, result) or {**out, "mode": "tool_result", "result": result}


@snow_router.post("/snow/mcp/chat")
async def mcp_chat(body: dict):
    """
    Chat through the local MCP server. Body: message, snow_url / snow_user / snow_pass (the signed-in user),
    optional confirm: {tool, arguments} to run a proposal the user approved.
    Response mode: answer | tool_result | proposal (needs the user's confirmation) | needs_input.
    """
    message = str(body.get("message") or "").strip()
    confirm = body.get("confirm") if isinstance(body.get("confirm"), dict) else None
    if not message and not confirm:
        raise HTTPException(status_code=400, detail="message is required")
    if len(message) > 6000:
        raise HTTPException(status_code=400, detail="message is too long (maximum 6000 characters)")
    creds = {k: body.get(k) for k in ("snow_url", "snow_user", "snow_pass")}
    try:
        result = await asyncio.to_thread(_mcp_chat_call, message, creds, confirm)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return JSONResponse(content=json.loads(json.dumps(result, default=str)))


# ─── Requirement pipeline (plan → confirm → deploy → verify → report) ───────────
# Planning is read-only. Deployment needs confirm=true and re-reads the instance before writing.

def _req_json(plan: dict) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(plan, default=str)))


@snow_router.post("/snow/requirements/plan")
async def requirements_plan(body: dict):
    """Body: creds + text (a request or user story) or requirement (structured). Writes nothing to ServiceNow."""
    text = str(body.get("text") or "").strip()
    requirement = body.get("requirement") if isinstance(body.get("requirement"), dict) else None
    if not text and not requirement:
        raise HTTPException(status_code=400, detail="text or requirement is required")
    if len(text) > 12000:
        raise HTTPException(status_code=400, detail="text is too long (maximum 12000 characters)")
    sn = snc.client_from(body)
    try:
        plan = await asyncio.to_thread(rp.plan_requirement, sn, text, requirement,
                                       generate_scripts=body.get("generate_scripts", True) is not False)
    except SNError as e:
        raise e.to_http("Requirement inspection")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if body.get("require_preflight") is True:
        plan["require_preflight"] = True
        plan["planner_preflight"] = await asyncio.to_thread(_planner_preflight_report, sn, body)
        rp.save_plan(sn.instance, plan)
    return _req_json(plan)


@snow_router.post("/snow/planner/preflight")
async def planner_preflight(body: dict):
    """Read-only readiness snapshot for release-aware planning and confirmed execution."""
    sn = snc.client_from(body)
    return await asyncio.to_thread(_planner_preflight_report, sn, body)


@snow_router.post("/snow/requirements/deploy")
async def requirements_deploy(body: dict):
    """Body: creds + plan_id, confirm=true, optional decisions {item_id: create|reuse|modify|skip}, update_set_name.
    Without confirm=true nothing is written (428)."""
    plan_id = str(body.get("plan_id") or "")
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id is required")
    decisions = body.get("decisions") if isinstance(body.get("decisions"), dict) else None
    if body.get("confirm") is not True and not decisions:
        raise HTTPException(status_code=428, detail="Deployment not confirmed — send confirm=true after the user "
                                                    "reviews the plan (this call changes ServiceNow configuration)")
    sn = snc.client_from(body)
    saved_plan = rp.load_plan(sn.instance, plan_id)
    require_preflight = body.get("require_preflight") is True or bool((saved_plan or {}).get("require_preflight"))
    if require_preflight:
        readiness = await asyncio.to_thread(_planner_preflight_report, sn, body)
        if not readiness["execution_ready"]:
            blockers = "; ".join(readiness["blockers"])
            raise HTTPException(status_code=412, detail=f"Planner preflight blocked execution: {blockers}")

    def work():
        plan = rp.deploy_plan(sn, plan_id, confirm=body.get("confirm") is True, decisions=decisions,
                              update_set_name=str(body.get("update_set_name") or ""))
        story = plan.get("story") or {}
        if plan.get("deployment") and story.get("table") and story.get("sys_id"):
            try:
                sn.patch(story["table"], story["sys_id"], {"work_notes": rp.work_note(plan)})
            except SNError as e:
                logger.warning(f"could not add the requirement work note to {story['table']} {story['sys_id']}: {e}")
        return plan
    try:
        plan = await asyncio.to_thread(work)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SNError as e:
        raise e.to_http("Requirement deployment")
    return _req_json(plan)


@snow_router.post("/snow/requirements/history")
async def requirements_history(body: dict):
    """Requirements implemented on the signed-in user's instance (per-instance history)."""
    sn = snc.client_from(body)
    return {"instance": sn.instance, "history": rp.history(sn.instance)[-100:]}


# ─── ServiceNow Connection ────────────────────────────────────────────────────

def _get_snow_auth(snow_url=None, snow_user=None, snow_pass=None):
    return ((snow_url or SNOW_INSTANCE_URL).rstrip("/"), snow_user or SNOW_USER, snow_pass or SNOW_PASS)


async def _sn_get(path: str, auth: tuple, params: dict = None) -> dict:
    """GET via the shared client (TLS verified, clear errors, off the event loop)."""
    url, user, password = auth
    sn = snc.SNClient(url, user, password)
    return await asyncio.to_thread(sn.request, "GET", path, params or {}, None)


async def _sn_post(path: str, auth: tuple, data: dict) -> dict:
    url, user, password = auth
    sn = snc.SNClient(url, user, password)
    return await asyncio.to_thread(sn.request, "POST", path, None, data)


async def _read_instance_context(snow_url: str, snow_user: str, snow_pass: str) -> str:
    """Read current state of ServiceNow instance for AI context — deep assessment."""
    auth = (snow_url.rstrip("/"), snow_user, snow_pass)
    context_parts = []

    # Release info
    try:
        for prop in [("glide.buildname", "Release"), ("glide.buildtag", "Build tag")]:
            val = await asyncio.to_thread(lambda p=prop[0]: snc.SNClient(*auth).property(p))
            if val:
                context_parts.append(f"ServiceNow {prop[1]}: {val}")
    except Exception:
        context_parts.append("ServiceNow Release: Unknown")

    # Active plugins (key ones)
    try:
        sn = snc.SNClient(*auth)
        plugins_raw = await asyncio.to_thread(sn.active_plugins)
        key_plugins = [p["source"] for p in plugins_raw if p.get("source") and
                       any(kw in p.get("source","") for kw in
                           ["agile", "itsm", "csm", "hrsd", "itom", "discovery", "orchestration",
                            "flow", "studio", "devops", "vulnerability"])]
        if key_plugins:
            context_parts.append(f"Active Key Plugins: {', '.join(key_plugins[:10])}")
        else:
            context_parts.append("Active Key Plugins: None detected")
    except Exception:
        context_parts.append("Active Key Plugins: Could not read")

    # Existing apps
    try:
        apps = await _sn_get("/api/now/table/sys_scope", auth, {
            "sysparm_limit": 10, "sysparm_fields": "name,scope,version",
            "sysparm_query": "scopeNOT INglobal^scopeNOT INrhino"
        })
        app_names = [f"{a.get('name','')} ({a.get('scope','')})" for a in apps.get("result", [])]
        context_parts.append(f"Existing Apps: {', '.join(app_names[:5]) or 'None'}")
    except Exception:
        context_parts.append("Existing Apps: Could not read")

    # Custom tables
    try:
        tables = await _sn_get("/api/now/table/sys_db_object", auth, {
            "sysparm_limit": 15, "sysparm_fields": "name,label,super_class.name",
            "sysparm_query": "nameSTARTSWITHx_^ORnameSTARTSWITHu_"
        })
        table_names = [f"{t.get('label',t.get('name',''))} [{t.get('name','')}]"
                       for t in tables.get("result", [])]
        context_parts.append(f"Custom Tables: {', '.join(table_names[:8]) or 'None'}")
    except Exception:
        context_parts.append("Custom Tables: None found")

    # Existing business rules (custom ones)
    try:
        brs = await _sn_get("/api/now/table/sys_script", auth, {
            "sysparm_limit": 10, "sysparm_fields": "name,collection,when",
            "sysparm_query": "sys_scope.scopeNOT INglobal^active=true"
        })
        br_names = [f"{b.get('name','')} on {b.get('collection','')}" for b in brs.get("result", [])]
        context_parts.append(f"Custom Business Rules: {', '.join(br_names[:5]) or 'None'}")
    except Exception:
        context_parts.append("Custom Business Rules: None found")

    # Existing flows
    try:
        flows = await _sn_get("/api/now/table/sys_hub_flow", auth, {
            "sysparm_limit": 5, "sysparm_fields": "name,description,active"
        })
        flow_names = [f.get("name", "") for f in flows.get("result", [])]
        context_parts.append(f"Existing Flows: {', '.join(flow_names[:3]) or 'None'}")
    except Exception:
        context_parts.append("Existing Flows: None found")

    return "\n".join(context_parts)


# ─── Text Extraction Helpers ──────────────────────────────────────────────────

def _extract_pdf_text(file_bytes: bytes) -> str:
    try:
        import PyPDF2
        import io
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception as e:
            return f"[PDF extraction error: {e}]"

def _extract_docx_text(file_bytes: bytes) -> str:
    try:
        import docx
        import io
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        return f"[DOCX extraction error: {e}]"

def _clean_json_response(raw: str) -> str:
    """Remove markdown code fences from AI JSON response."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip().rstrip("```").strip()


# ─── Minimum Input → Requirements ─────────────────────────────────────────────

@snow_router.post("/snow/extract/text")
async def extract_from_text(body: dict):
    """
    Accept any amount of text (even one sentence) and generate
    comprehensive requirements: user stories, tables, flows.
    """
    text = body.get("text", "")
    snow_url = body.get("snow_url", SNOW_INSTANCE_URL)
    snow_user = body.get("snow_user", SNOW_USER)
    snow_pass = body.get("snow_pass", SNOW_PASS)

    if not text.strip():
        raise HTTPException(status_code=400, detail="Text input required")

    # Read instance context if credentials provided
    instance_context = ""
    if snow_url and snow_user and snow_pass:
        try:
            instance_context = await _read_instance_context(snow_url, snow_user, snow_pass)
        except Exception:
            instance_context = "Could not read instance"

    system = """You are an expert ServiceNow Business Analyst and Solutions Architect.
    Even from minimal input, generate comprehensive, professional requirements.
    Always respond with valid JSON only."""

    prompt = f"""Generate comprehensive ServiceNow implementation requirements from this input.
Even if the input is just one sentence, expand it into a full, detailed requirements set.

INSTANCE CONTEXT:
{instance_context or 'No instance connected'}

USER INPUT:
{text}

Generate DETAILED JSON with this structure:
{{
  "project_title": "Clear project name",
  "project_overview": "3-4 sentence description of what will be built",
  "business_problem": "The problem this solves",
  "business_value": "Expected business benefits",
  "user_stories": [
    {{
      "id": "US-001",
      "title": "Story title",
      "as_a": "User role (e.g., IT Manager, Employee, HR Admin)",
      "i_want": "Specific capability",
      "so_that": "Business value/benefit",
      "priority": "High",
      "story_points": 5,
      "acceptance_criteria": [
        "Given [context], When [action], Then [expected result]",
        "Given [context], When [action], Then [expected result]"
      ],
      "technical_notes": "ServiceNow-specific implementation notes"
    }}
  ],
  "tables": [
    {{
      "name": "x_custom_tablename",
      "display_name": "Table Display Name",
      "extends": "task",
      "description": "What this table stores",
      "fields": [
        {{"name": "short_description", "type": "string", "label": "Short Description", "required": true}},
        {{"name": "assigned_to", "type": "reference", "label": "Assigned To", "required": false}}
      ]
    }}
  ],
  "flows": [
    {{
      "name": "Flow Name",
      "description": "What this flow does",
      "trigger": "Record Created",
      "trigger_table": "table_name",
      "steps": [
        {{"type": "approval", "description": "Manager approval required"}},
        {{"type": "email", "description": "Send notification to requester"}},
        {{"type": "update_record", "description": "Update record status"}}
      ]
    }}
  ],
  "roles": [
    {{"name": "x_custom_admin", "description": "Administrator role"}},
    {{"name": "x_custom_user", "description": "End user role"}}
  ],
  "integrations": [],
  "estimated_effort": "X sprints / Y weeks",
  "estimated_story_points": 0
}}

Make it comprehensive - at least 3-5 user stories, proper tables with all needed fields."""

    data = await _ai_json(prompt, system=system)
    for st in data.get("user_stories") or []:
        if isinstance(st, dict):
            st["acceptance_criteria"] = dr.as_list(st.get("acceptance_criteria"))
            try:
                st["story_points"] = int(float(st.get("story_points") or 0))
            except (TypeError, ValueError):
                st["story_points"] = 0
    data["estimated_story_points"] = sum(st.get("story_points", 0) for st in data.get("user_stories") or [] if isinstance(st, dict))
    return JSONResponse(content=data)


# ─── File Upload Extraction ───────────────────────────────────────────────────

@snow_router.post("/snow/extract")
async def extract_from_file(
    file: UploadFile = File(...),
    snow_url: Optional[str] = Form(None),
    snow_user: Optional[str] = Form(None),
    snow_pass: Optional[str] = Form(None)
):
    """Extract requirements from PDF, DOCX, or TXT file."""
    file_bytes = await file.read()
    filename = file.filename.lower()

    if filename.endswith(".pdf"):
        text = _extract_pdf_text(file_bytes)
    elif filename.endswith((".docx", ".doc")):
        text = _extract_docx_text(file_bytes)
    elif filename.endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="replace")
    else:
        text = file_bytes.decode("utf-8", errors="replace")

    if not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from file")

    # Reuse text extraction endpoint
    return await extract_from_text({
        "text": text[:8000],
        "snow_url": snow_url or SNOW_INSTANCE_URL,
        "snow_user": snow_user or SNOW_USER,
        "snow_pass": snow_pass or SNOW_PASS
    })


# ─── Generate Plan ────────────────────────────────────────────────────────────

@snow_router.post("/snow/plan")
async def generate_plan(body: dict):
    """
    Generate a build plan (the input for /snow/build) from requirements or user stories.
    The plan is normalised and validated before it is returned, so the UI can show
    problems for the user to fix before anything is written to the instance.
    """
    requirements = body.get("requirements", body.get("text", ""))
    if not requirements:
        raise HTTPException(status_code=400, detail="Requirements text required")

    instance_ctx = "No instance connected"
    if body.get("snow_url") and body.get("snow_user") and body.get("snow_pass"):
        try:
            instance_ctx = await _read_instance_context(body["snow_url"], body["snow_user"], body["snow_pass"])
        except Exception:
            instance_ctx = "Instance could not be read"

    system = ("You are a senior ServiceNow platform architect. You design minimal, OOB-first solutions: "
              "reuse existing tables (incident, task, sc_req_item, cmdb_ci ...) and add custom tables only when "
              "the data genuinely does not fit. Respond with JSON only.")
    prompt = f"""Create a ServiceNow build plan for these requirements.

INSTANCE STATE:
{instance_ctx}

REQUIREMENTS:
{requirements[:6000]}

Rules:
- Table/field/role names: lowercase snake_case WITHOUT scope prefixes (the agent adds x_<vendor>_<app>_ or u_).
- "extends": an existing table name (e.g. "task") or "" for a standalone table.
- reference fields MUST give "reference" (e.g. "sys_user", "sys_user_group", "cmdb_ci", or one of the plan's tables).
- choice fields MUST list "choices".
- Business rule / client script "script": only the BODY (the agent wraps it). Server: GlideRecord/gs. Client: g_form only.
  Never call current.update() in before/after rules. Never use GlideRecord in client scripts.
- script_includes: whenever the requirements ask for a Script Include, a reusable server-side class/utility, or
  logic shared across business rules / client scripts, add an entry. "name" is the exact class name (PascalCase, no
  spaces). "script" is the FULL class: `var <name> = Class.create(); <name>.prototype = {{ initialize: function()
  {{}}, <methods>, type: '<name>' }};`. When client scripts must call it (GlideAjax) set "client_callable": true and
  use `Object.extendsObject(global.AbstractAjaxProcessor, {{ ... }})` with methods that read
  this.getParameter('sysparm_...'). Implement every method the requirements describe with real GlideRecord logic.
- Flows: trigger.type is record.created | record.updated | record.created_or_updated; every action type is a
  Flow Designer core action (core.sendEmail, core.createTask, core.updateRecord, core.askForApproval, core.log,
  core.lookUpRecords, core.createRecord) with concrete "inputs".
- Subflows: put reusable work (notify, approve, create task, update record) in "subflows" with "inputs" and
  "outputs" typed String, Integer or True/False only. When the subflow works on a record give it a String input
  named "record_id". Let the flow call it with "subflow": "<subflow id>" plus "subflow_inputs"
  (input name -> value, e.g. {{"record_id": "{{{{trigger.current.sys_id}}}}"}}). A flow keeps only a core.log action
  of its own when it calls a subflow. Use no subflows for one-off, single-action flows.
- Data pills: in flows use {{{{trigger.current.<field>}}}}; in subflows use {{{{input.<input name>}}}}; the result of an
  earlier action is {{{{action.<action id>.<output>}}}}. Never use trigger pills inside a subflow.
- Action inputs: core.sendEmail {{to, subject, body}}; core.createTask {{table, short_description, assignment_group}};
  core.updateRecord / core.createRecord {{table, fields: {{field: value}}}}; core.askForApproval {{table,
  approvers: "<group name or user email>"}}; core.log {{log_message}}.
  Use real email addresses / groups from the requirements; never invent example.com addresses.
- acls: only for tables this plan creates (never for existing platform tables such as incident or task), one entry
  per table with the operations each role may perform.

Return JSON:
{{
  "app_name": "Short app name",
  "app_scope": "short_scope_suffix",
  "scope_mode": "scoped",
  "description": "What the app does",
  "roles": [{{"name": "admin", "description": "...", "contains": ["user"]}}, {{"name": "user", "description": "..."}}],
  "tables": [{{"name": "request", "label": "Request", "extends": "task", "number_prefix": "REQX",
              "fields": [{{"name": "requested_for", "label": "Requested for", "type": "reference", "reference": "sys_user",
                          "mandatory": true}},
                         {{"name": "category", "label": "Category", "type": "choice",
                          "choices": [{{"value": "hardware", "label": "Hardware"}}]}}]}}],
  "acls": [{{"table": "request", "operations": ["read", "create"], "roles": ["user"]}},
           {{"table": "request", "operations": ["write", "delete"], "roles": ["admin"]}}],
  "business_rules": [{{"name": "Set default category", "table": "request", "when": "before", "insert": true,
                      "update": false, "filter_condition": "categoryISEMPTY", "script": "current.category = 'hardware';"}}],
  "client_scripts": [{{"name": "Warn on category change", "table": "request", "type": "onChange", "field": "category",
                      "script": "g_form.showFieldMsg('category', 'Check the assignment group', 'info');"}}],
  "ui_policies": [{{"short_description": "Mandatory requested for", "table": "request", "conditions": "",
                   "actions": [{{"field": "requested_for", "mandatory": true}}]}}],
  "script_includes": [{{"name": "RequestUtils", "client_callable": false,
                       "description": "Shared request validation helpers",
                       "script": "var RequestUtils = Class.create();\\nRequestUtils.prototype = {{\\n    initialize: function() {{}},\\n    isVip: function(userId) {{\\n        var u = new GlideRecord('sys_user');\\n        return u.get(userId) && u.getValue('vip') == '1';\\n    }},\\n    type: 'RequestUtils'\\n}};"}}],
  "notifications": [{{"name": "Request created", "table": "request", "on_insert": true,
                     "recipient_fields": ["requested_for"], "subject": "Your request was created",
                     "message": "We have received your request."}}],
  "subflows": [{{"id": "notify_requester", "name": "Notify requester", "description": "Email the requester",
                "inputs": [{{"name": "record_id", "label": "Record ID", "type": "String"}}],
                "outputs": [{{"name": "status", "label": "Status", "type": "String"}}],
                "actions": [{{"id": "send", "type": "core.sendEmail",
                             "inputs": {{"to": "requester@company.com", "subject": "Request received",
                                        "body": "We have received your request."}}}},
                            {{"id": "note", "type": "core.log",
                             "inputs": {{"log_message": "Notified for {{{{input.record_id}}}}"}}}}]}}],
  "flows": [{{"id": "request_created_notify", "name": "Request created - notify", "description": "...",
             "trigger": {{"type": "record.created", "table": "request", "condition": ""}},
             "actions": [{{"id": "log_it", "type": "core.log",
                          "inputs": {{"log_level": "info", "log_message": "Request created"}}}}],
             "subflow": "notify_requester",
             "subflow_inputs": {{"record_id": "{{{{trigger.current.sys_id}}}}"}}}}]
}}"""
    # Req 4: Solution documentation gate — warn if no TSD/BRD provided
    doc_keywords = ["tsd", "technical spec", "brd", "business requirement", "frd", "functional req",
                    "architecture", "design doc", "solution doc"]
    has_docs = any(kw in requirements.lower() for kw in doc_keywords)
    if not has_docs and len(requirements) < 500:
        # Short requirements without docs: add guidance note to the returned plan
        _doc_warning = ("⚠️ No TSD/BRD detected in requirements. For production builds, "
                        "generate a Technical Specification Document first via 'Generate Documents'. "
                        "The plan below is based on the provided requirements only.")
    else:
        _doc_warning = None

    data = await _ai_json(prompt, system=system)
    if _doc_warning:
        data["_doc_warning"] = _doc_warning
    check = await asyncio.to_thread(sn_build.validate_plan, data)
    return JSONResponse(content={**check["plan"], "_validation": {"valid": check["valid"], "issues": check["issues"],
                                                                  "counts": check["counts"],
                                                                  "flow_server_configured": check["flow_server_configured"],
                                                                  "flow_engines": check["flow_engines"],
                                                                  "flow_engine": check["flow_engine"],
                                                                  "notes": check["notes"]}})


# ─── Document Generation ──────────────────────────────────────────────────────

@snow_router.post("/snow/documents/generate")
async def generate_document(body: dict):
    """Generate full BRD, FRD, or TSD document — section by section for rich content."""
    doc_type = body.get("doc_type", "BRD").upper()
    requirements = body.get("requirements", body.get("text", ""))
    project_name = body.get("project_name", "ServiceNow Project")

    if not requirements:
        raise HTTPException(status_code=400, detail="Requirements text required")

    today = datetime.now().strftime("%B %d, %Y")

    doc_configs = {
        "BRD": {
            "title": "Business Requirements Document",
            "sections": [
                ("Executive Summary", "Write a 3-paragraph executive summary covering: (1) the business problem being solved, (2) the proposed ServiceNow solution and its key capabilities, (3) expected business outcomes and ROI. Be specific to the user story provided."),
                ("Business Objectives", "List and explain 5-7 specific, measurable business objectives for this project. For each objective explain WHY it matters and HOW the ServiceNow implementation achieves it. Include KPIs and success metrics for each."),
                ("Stakeholder Analysis", "Identify all stakeholders: IT Admin, End Users, Support Manager, CISO, and any others relevant to this story. For each: their role, their pain points today, what they gain from this solution, and their level of involvement."),
                ("Current State & Pain Points", "Describe the current state of operations WITHOUT this solution. Include manual steps, inefficiencies, SLA risks, human errors, and cost of the current approach. Use specific scenarios from the user story."),
                ("Future State & Solution Overview", "Describe the future state AFTER implementation. Include the end-to-end automated workflow, how ServiceNow tables and modules are used, integration points, and the step-by-step process from trigger to resolution."),
                ("Business Requirements", "List 10-15 detailed business requirements (BR-001 to BR-015) derived from the user story. Each requirement must have: ID, description, priority (High/Medium/Low), acceptance criteria, and ServiceNow module it maps to."),
                ("Solution Architecture", "Describe the full ServiceNow architecture for this solution: which tables are used (incident, rm_story, etc.), which modules (ITSM, CSM, Flow Designer, Business Rules, Assignment Rules), data flow between components, integration architecture diagram description, and role-based access model."),
                ("Implementation Plan", "Provide a phased implementation plan: Phase 1 (Discovery & Design, 2 weeks), Phase 2 (Development & Configuration, 3 weeks), Phase 3 (Testing & UAT, 2 weeks), Phase 4 (Go-Live & Hypercare, 1 week). For each phase list specific tasks, owners, and deliverables."),
                ("Business Rules & Constraints", "List all business rules that govern this solution: assignment logic, escalation thresholds, SLA definitions, notification triggers, approval workflows, and any compliance or audit requirements."),
                ("Success Metrics & ROI", "Define 6-8 measurable success metrics: response time reduction %, SLA compliance improvement %, manual effort saved (hours/week), cost savings, user satisfaction score target. Include how each will be measured post go-live."),
            ]
        },
        "FRD": {
            "title": "Functional Requirements Document",
            "sections": [
                ("Overview & Scope", "Write a comprehensive overview: purpose of this document, scope of the ServiceNow solution, what is IN scope and OUT of scope, assumptions, dependencies, and document conventions."),
                ("System Architecture", "Detail the full system architecture: ServiceNow modules involved, table relationships (ERD description), integration layers, data flow diagram description, and how each component interacts. Include REST API endpoints used, authentication method, and data formats."),
                ("Functional Requirements", "List 12-15 detailed functional requirements (FR-001 to FR-015). Each must include: ID, title, detailed description, trigger/input, expected system behavior, output/result, ServiceNow configuration needed, and acceptance criteria."),
                ("User Interface Requirements", "Describe all UI components: ServiceNow portal pages, form layouts, list views, dashboards, and widgets needed. For each screen: fields displayed, user actions available, validation rules, and mobile responsiveness requirements."),
                ("Workflow & Automation Design", "Detail every automated workflow: Flow Designer flows, Business Rules (before/after insert/update), Assignment Rules, Escalation Rules, SLA definitions, Notification rules. For each automation: trigger, conditions, actions, and expected outcome."),
                ("Integration Requirements", "Describe all integrations: email notifications (inbound/outbound), REST API integrations with external systems, LDAP/SSO for user authentication, monitoring tool integration, and any webhook configurations."),
                ("Reporting & Dashboard Requirements", "List all reports and dashboards needed: report name, data source (table/query), fields displayed, filters, grouping, scheduling, and who receives it. Include the main operational dashboard layout."),
                ("Non-Functional Requirements", "Cover: Performance (page load < 3s, API response < 2s), Scalability (support 10,000 concurrent users), Availability (99.9% uptime SLA), Security (role-based access, audit logs, encryption at rest/transit), and Compliance requirements."),
            ]
        },
        "TSD": {
            "title": "Technical Specification Document",
            "sections": [
                ("Technical Overview", "Provide a technical overview: ServiceNow version/release, instance type (PDI/Production), scripting languages used (JavaScript for Business Rules/Client Scripts, Glide API), REST API version (v1), and overall technical approach."),
                ("Solution Architecture", "IMPORTANT: Generate a detailed ASCII architecture diagram for this solution using box-drawing characters (+-|) showing all layers: (1) User/Client Layer, (2) ServiceNow Platform Layer with specific modules, (3) Integration Layer, (4) Data Layer. After the diagram, explain each component. Format the diagram inside a code block. Example structure:\n+--[Browser/Portal]--+\n         |\n+--[ServiceNow Instance]--+\n|  [ITSM] [Flow Designer] |\n|  [REST API] [CMDB]      |\n+------------------------+\n         |\n+--[External Systems]--+"),
                ("Database Design", "Specify all database changes: new tables (name, label, fields, types, max length, mandatory flags), extended tables, new fields on existing tables (incident, rm_story, etc.), indexes for performance, and data retention policies. Include an entity relationship description."),
                ("Business Rules Specification", "Write technical specification for each Business Rule: Name, Table, When (before/after insert/update/delete), Condition (GlideRecord filter), Script (pseudo-code with Glide API calls), and expected behavior. Cover assignment logic, state transitions, and field calculations."),
                ("Client Scripts & UI Policies", "Specify all client-side scripts: Client Script name, type (onLoad/onChange/onSubmit), table, field, script logic. UI Policy name, conditions, actions (mandatory/visible/read-only fields). Include form validation logic."),
                ("Flow Designer & Automation", "Detail all Flow Designer flows: flow name, trigger (record created/updated/scheduled), each action step with inputs/outputs, subflows, decision branches, error handling, and rollback strategy."),
                ("REST API Specifications", "Document all REST API calls: endpoint URL, HTTP method, request headers, request body schema, response schema, error codes, rate limits, and authentication. Include example request/response for each endpoint used."),
                ("Security & Access Control", "Define ACL rules: table/field level, operation (read/write/create/delete), role conditions, script conditions. List all new roles, role inheritance, groups, and group membership rules. Data encryption requirements."),
                ("Deployment & Update Set Strategy", "Specify deployment approach: update set naming convention, order of deployment, pre-deployment checklist, post-deployment verification steps, rollback plan, and environment promotion path (Dev → Test → Production)."),
                ("Testing Strategy", "Define test strategy: unit test cases for each Business Rule, integration test scenarios, UAT test scripts with expected results, performance test plan, and go-live acceptance criteria checklist."),
            ]
        },
        "TEST_PLAN": {
            "title": "Test Plan & Test Cases",
            "sections": [
                ("Test Strategy & Scope", "Define what is tested and what is not, test levels (unit, integration, system, UAT, regression), entry and exit criteria, and how ServiceNow ATF will be used for automated regression."),
                ("Test Environment & Data", "List the instances (Dev/Test/UAT), required plugins, roles and test users per persona, and the exact test data records to prepare."),
                ("Functional Test Cases", "Write one test case per acceptance criterion as a Markdown table with columns: TC ID | Story | Title | Preconditions | Steps | Expected Result | Priority. Cover every story and every acceptance criterion in the input."),
                ("Negative & Edge Test Cases", "Write negative and boundary test cases as a Markdown table (TC ID | Title | Steps | Expected Result): invalid data, missing mandatory fields, unauthorised roles (ACL checks), concurrent updates, SLA boundaries."),
                ("Integration & Notification Tests", "Test cases for flows, notifications, integrations and scheduled jobs: trigger, expected outcome, how to verify in ServiceNow (email log, flow execution context, system log)."),
                ("ATF Automation Candidates", "List which test cases should become ATF tests, the ATF step types to use (Open a New Form, Set Field Values, Submit, Field Values Validation, Impersonate, Run Server Side Validation Script), and the suite structure."),
                ("Defect Management & Sign-off", "Severity definitions, defect workflow, retest rules, and the UAT sign-off checklist with owners."),
            ]
        },
        "PROCESS_FLOW": {
            "title": "Process Flow Document",
            "sections": [
                ("Process Overview", "Describe the end-to-end business process, its trigger, its outcome, and the roles involved."),
                ("Actors & Responsibilities", "Markdown table: Actor | ServiceNow role | Responsibilities | Hand-offs."),
                ("Step-by-Step Flow", "Numbered list of every step from trigger to closure. For each step: actor, ServiceNow component (table, state, flow action, approval, notification) and the data that changes."),
                ("Decision Points & Exceptions", "Every decision (approve/reject, priority, SLA breach) with the condition, the Yes path and the No path, plus exception handling and escalations."),
                ("Process Diagram (Mermaid)", "Output ONLY a Mermaid 'flowchart TD' diagram of the full process inside a ```mermaid code block, with labelled Yes/No branches. No prose."),
                ("States, SLAs & Notifications", "Markdown tables for the state model (State | Meaning | Entry condition | Exit condition), the SLA definitions, and the notifications (Event | Recipient | Content)."),
            ]
        },
        "DEPLOYMENT_GUIDE": {
            "title": "Deployment Guide",
            "sections": [
                ("Release Overview", "What is being deployed, target instances, the application scope, and the update sets that carry the changes (naming convention: HAI_<app>_<yyyymmdd>)."),
                ("Pre-Deployment Checklist", "Checklist: change request raised and CAB-approved, clone/backup plan, plugins activated on target, update set marked Complete, exported XML retained, test evidence (ATF results) attached."),
                ("Update Set Migration Steps", "Numbered steps to move the update sets: retrieve (remote instance) or import XML, preview, resolve every preview problem (explain Accept remote vs Skip), commit order when there are several update sets, batch update sets."),
                ("Post-Deployment Configuration", "Manual steps that update sets do not carry: data (groups, users, role assignments), system properties per environment, MID servers, credentials, scheduled job activation, email settings."),
                ("Verification & Smoke Tests", "Smoke test checklist per feature with the expected result and who verifies it, plus how to confirm flows are active and notifications fire."),
                ("Rollback Plan", "How to back out: rollback of the committed update set (Back out / Rollback context), data restore, disabling flows and business rules, and communication."),
                ("Hypercare & Handover", "Monitoring during hypercare, known issues, support contacts, and handover to operations."),
            ]
        },
    }

    # ── USER_STORIES doc type: handled separately, not via AI section-by-section ──
    if doc_type == "USER_STORIES":
        stories_raw = requirements.split("\n\n") if "\n\n" in requirements else [requirements]
        sections = []
        story_num = 1
        for raw in stories_raw:
            lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
            if not lines:
                continue
            # Extract story title (first line after "N. ")
            title_line = re.sub(r"^\d+\.\s*", "", lines[0])
            # Extract acceptance criteria block
            ac_lines = []
            story_body_lines = []
            in_ac = False
            for ln in lines[1:]:
                if re.match(r"(?i)acceptance criteria", ln):
                    in_ac = True
                    continue
                if in_ac:
                    ac_lines.append(ln.lstrip("-• ").strip())
                else:
                    story_body_lines.append(ln)
            content = f"**User Story {story_num:02d}:** {title_line}\n\n"
            if story_body_lines:
                content += "\n".join(story_body_lines) + "\n\n"
            if ac_lines:
                content += "**Acceptance Criteria:**\n" + "\n".join(f"- {c}" for c in ac_lines if c)
            else:
                content += "**Acceptance Criteria:**\n- _Not provided — to be defined with the product owner._"
            sections.append({"heading": f"US-{story_num:02d}: {title_line[:80]}", "content": content})
            story_num += 1

        return JSONResponse(content={
            "doc_type": "USER_STORIES",
            "title": "User Stories & Acceptance Criteria",
            "project_name": project_name,
            "version": "1.0",
            "date": today,
            "sections": sections,
            "revision_history": [
                {"version": "1.0", "date": datetime.now().strftime("%Y-%m-%d"), "author": "SoftProdigy Snow Agent", "changes": "Initial release"}
            ]
        })

    if doc_type not in doc_configs:
        raise HTTPException(status_code=400, detail=f"Unknown doc_type '{doc_type}'. Use one of: "
                            + ", ".join(list(doc_configs) + ["USER_STORIES"]))
    config = doc_configs[doc_type]

    # Generate each section individually — no JSON parsing, plain text per section
    system_prompt = (
        f"You are a senior ServiceNow Solution Architect and Technical Writer. "
        f"Write detailed, professional, implementation-specific content. "
        f"Always base your content on the exact user story and project provided. "
        f"Never write generic placeholders. Write as if this is a real enterprise project document."
    )

    async def one_section(heading: str, instruction: str) -> dict:
        is_arch = doc_type == "TSD" and "architecture" in heading.lower()
        if is_arch:
            spec = await _ai_json(f"""Project: {project_name}
Requirements:
{requirements[:5000]}

Design the ServiceNow solution architecture for THIS project and return JSON:
{{
  "title": "<project> — Solution Architecture",
  "layers": [
    {{"name": "Users & Channels", "components": ["<actual personas/channels from the requirements>"]}},
    {{"name": "Experience Layer", "components": ["Service Portal", "Service Operations Workspace", "..."]}},
    {{"name": "ServiceNow Platform", "components": ["<only the modules THIS project uses: Incident, Problem, SLA, CMDB, Flow Designer ...>"]}},
    {{"name": "Integration Layer", "components": ["IntegrationHub", "MID Server", "Inbound Email", "..."]}},
    {{"name": "External Systems", "components": ["<systems named in the requirements>"]}}
  ],
  "flows": [{{"from": "<component name exactly as above>", "to": "<component name>", "label": "<2-3 words>"}}],
  "description": "3-4 paragraphs explaining each layer, the data flows and how they meet the requirements"
}}
Rules: 3-6 layers, max 6 components per layer, component names max 4 words, 4-10 flows that use the
exact component names. Only include systems/modules supported by the requirements.""",
                                  system=system_prompt, max_tokens=2500)
            diagram = arch_diagram.normalize_spec(spec)
            if not diagram["layers"]:
                raise AIUnavailable("AI returned an architecture without layers, please retry")
            text = str(spec.get("description") or "").strip()
            return {"heading": heading, "content": text, "diagram": diagram,
                    "diagram_ascii": arch_diagram.render_ascii(diagram)}
        else:
            prompt = f"""Project: {project_name}
User Story / Requirements:
{requirements[:3000]}

Write the '{heading}' section of a {config['title']}.

Instructions: {instruction}

Be specific to the project above. Use Markdown lists or tables where the instructions ask for them, otherwise
professional prose. Do NOT use JSON and do NOT start with a heading."""
            content = await _call_claude(prompt, system=system_prompt, max_tokens=1800)
        return {"heading": heading, "content": content.strip().lstrip("#").strip()}

    # Sections run concurrently (groq_client throttles to GROQ_MAX_CONCURRENCY). If ANY section fails the
    # whole document fails with 503 — a document is never returned with placeholder or error text inside.
    sections = await asyncio.gather(*(one_section(h, i) for h, i in config["sections"]))

    return JSONResponse(content={
        "doc_type": doc_type,
        "title": config["title"],
        "project_name": project_name,
        "version": "1.0",
        "date": today,
        "sections": sections,
        "revision_history": [
            {"version": "1.0", "date": datetime.now().strftime("%Y-%m-%d"), "author": "SoftProdigy Snow Agent", "changes": "Initial release"}
        ]
    })


# ─── Word rendering shared by download + export ───────────────────────────────

def _render_section(doc, i: int, section: dict):
    """Heading + (architecture image when present) + Markdown body."""
    from docx.shared import Inches, Pt
    dr.heading(doc, f"{i}. {section.get('heading', f'Section {i}')}", level=1)
    diagram = section.get("diagram")
    if diagram:
        try:
            png = arch_diagram.render_png(diagram)
            doc.add_picture(io.BytesIO(png), width=Inches(6.4))
            cap = doc.add_paragraph()
            r = cap.add_run(f"Figure {i}: {diagram.get('title', 'Solution architecture')}")
            r.italic = True
            r.font.size = Pt(9)
            r.font.color.rgb = dr.GREY
        except Exception as e:  # drawing failed -> aligned ASCII version, never nothing
            logger.warning(f"architecture diagram render failed: {e}")
            p = doc.add_paragraph()
            run = p.add_run(section.get("diagram_ascii") or arch_diagram.render_ascii(diagram))
            run.font.name = "Consolas"
            run.font.size = Pt(7.5)
    content = section.get("content", "")
    if isinstance(content, (list, tuple)):
        for item in content:
            dr.add_markdown(doc, dr.s(item))
    elif content:
        dr.add_markdown(doc, content)
    doc.add_paragraph()


@snow_router.post("/snow/documents/diagram")
async def architecture_png(body: dict):
    """PNG of an architecture spec (the `diagram` object of a TSD section) — for previews."""
    from fastapi.responses import Response
    spec = body.get("diagram") or body
    try:
        png = await asyncio.to_thread(arch_diagram.render_png, spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(content=png, media_type="image/png")


# ─── Document Download ────────────────────────────────────────────────────────

@snow_router.post("/snow/documents/download")
async def download_document(body: dict):
    """Build and download DOCX for a generated document with proper formatting."""
    import tempfile

    doc_data = body.get("document", body)
    doc_type = doc_data.get("doc_type", "DOC")
    title = doc_data.get("title", f"{doc_type} Document")
    project_name = doc_data.get("project_name", "ServiceNow Project")
    sections = doc_data.get("sections", [])
    version = doc_data.get("version", "1.0")
    date_str = doc_data.get("date", datetime.now().strftime("%B %d, %Y"))

    doc = dr.new_document()

    # Title page using docx_render helper
    dr.title_page(
        doc,
        title=title,
        subtitle=project_name,
        meta=f"Version {version}  |  {date_str}  |  Generated by SoftProdigy Snow Agent",
        page_break=True,
    )

    for i, section in enumerate(sections, 1):
        _render_section(doc, i, section)

    # Revision History table
    if doc_data.get("revision_history"):
        dr.heading(doc, "Revision History", level=1)
        rev_rows = [
            [r.get("version", ""), r.get("date", ""), r.get("author", ""), r.get("changes", "")]
            for r in doc_data["revision_history"]
        ]
        dr.table(doc, ["Version", "Date", "Author", "Changes"], rev_rows,
                 widths=[0.8, 1.2, 1.5, 3.5])

    safe_name = dr.safe_filename(project_name)
    out_path = os.path.join(tempfile.gettempdir(), f"{doc_type}_{safe_name}_{os.getpid()}.docx")
    doc.save(out_path)

    return FileResponse(
        out_path,
        filename=f"{doc_type}_{safe_name}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


# ─── Document Export (generate + download in one call) ──────────────────────────
# Handles BRD, FRD, TSD, USER_STORIES — returns .docx directly

@snow_router.post("/snow/documents/export")
async def export_document(body: dict):
    """
    Generate a document AND immediately return it as a .docx file.
    Accepts: doc_type, project_name, requirements, snow_url, snow_user, snow_pass
    For USER_STORIES, also accepts: user_stories (list of {id, summary, description, acceptance_criteria})
    """
    doc_type = body.get("doc_type", "BRD").upper()
    project_name = body.get("project_name", "ServiceNow Project")
    requirements = body.get("requirements", body.get("text", ""))
    today = datetime.now().strftime("%B %d, %Y")
    safe_name = dr.safe_filename(project_name)

    # ── USER STORIES → .docx (table format, each story is a section) ──
    if doc_type == "USER_STORIES":
        user_stories = body.get("user_stories", [])
        doc = dr.new_document()
        dr.title_page(doc,
            title="User Stories & Acceptance Criteria",
            subtitle=project_name,
            meta=f"Version 1.0  |  {today}  |  Generated by SoftProdigy Snow Agent",
            page_break=True)

        dr.heading(doc, "User Stories & Acceptance Criteria", level=1)
        p = doc.add_paragraph(f"Project: {project_name}  |  Generated: {today}")
        p.runs[0].font.color.rgb = dr.GREY
        doc.add_paragraph()

        if user_stories:
            dr.heading(doc, "Overview", level=2)
            dr.table(doc, ["User Story ID", "Summary", "Acceptance criteria"],
                     [[s.get("id", f"US-{i:03d}"), s.get("summary", s.get("story", "")),
                       str(len(dr.as_list(s.get("acceptance_criteria", s.get("criteria", "")))))]
                      for i, s in enumerate(user_stories, 1)], widths=[2.6, 3.2, 0.9])
            for i, s in enumerate(user_stories, 1):
                us_id = s.get("id", f"US-{i:03d}")
                summary = s.get("summary", s.get("story", ""))
                description = s.get("description", "")
                ac = s.get("acceptance_criteria", s.get("criteria", ""))
                if not description and summary:
                    description = f"As a user, I want to {summary.lower()} so that business requirements are fulfilled."
                # Story heading (first story starts on a new page, without leaving an empty page)
                h = dr.heading(doc, f"{us_id}: {summary[:100]}", level=2)
                if i == 1:
                    h.paragraph_format.page_break_before = True
                # Description
                if description:
                    dr.add_markdown(doc, f"**Description:** {description}")
                doc.add_paragraph()
                # Acceptance Criteria as a formatted list
                dr.heading(doc, "Acceptance Criteria", level=3)
                if ac:
                    for line in (ac.splitlines() if isinstance(ac, str) else ac):
                        line = line.strip().lstrip("-•✓ ")
                        if line:
                            p = doc.add_paragraph(style="List Bullet")
                            p.add_run(line)
                doc.add_paragraph()
        else:
            # Parse from requirements text
            story_blocks = [b.strip() for b in requirements.split("\n\n") if b.strip()]
            for i, block in enumerate(story_blocks, 1):
                lines = block.splitlines()
                title_line = re.sub(r"^\d+\.\s*", "", lines[0]).strip() if lines else f"Story {i}"
                ac_lines, body_lines = [], []
                in_ac = False
                for ln in lines[1:]:
                    if re.match(r"(?i)acceptance criteria", ln.strip()):
                        in_ac = True; continue
                    (ac_lines if in_ac else body_lines).append(ln.lstrip("-• ").strip())
                dr.heading(doc, f"US-{i:03d}: {title_line[:100]}", level=2)
                if body_lines:
                    dr.add_markdown(doc, " ".join(b for b in body_lines if b))
                doc.add_paragraph()
                dr.heading(doc, "Acceptance Criteria", level=3)
                for ac_line in (ac_lines or ["Criteria to be defined during sprint planning."]):
                    if ac_line:
                        p = doc.add_paragraph(style="List Bullet")
                        p.add_run(ac_line)
                doc.add_paragraph()

        # Revision history
        dr.heading(doc, "Revision History", level=1)
        dr.table(doc, ["Version", "Date", "Author", "Changes"],
                 [["1.0", datetime.now().strftime("%Y-%m-%d"), "SoftProdigy Snow Agent", "Initial release"]],
                 widths=[0.8, 1.2, 1.8, 3.2])

        out_path = os.path.join(tempfile.gettempdir(), f"USER_STORIES_{safe_name}_{os.getpid()}.docx")
        doc.save(out_path)
        return FileResponse(out_path, filename=f"User_Stories_{safe_name}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # ── BRD / FRD / TSD — generate then render to .docx ──
    # Reuse the generate endpoint logic inline
    doc_resp = await generate_document({
        "doc_type": doc_type,
        "project_name": project_name,
        "requirements": requirements,
        "snow_url": body.get("snow_url", ""),
        "snow_user": body.get("snow_user", ""),
        "snow_pass": body.get("snow_pass", ""),
    })
    doc_data = doc_resp.body
    import json as _json
    doc_data = _json.loads(doc_data)

    # Render to Word
    word_doc = dr.new_document()
    dr.title_page(word_doc,
        title=doc_data.get("title", doc_type),
        subtitle=project_name,
        meta=f"Version {doc_data.get('version','1.0')}  |  {doc_data.get('date', today)}  |  Generated by SoftProdigy Snow Agent",
        page_break=True)

    for i, section in enumerate(doc_data.get("sections", []), 1):
        _render_section(word_doc, i, section)

    if doc_data.get("revision_history"):
        dr.heading(word_doc, "Revision History", level=1)
        rev_rows = [
            [r.get("version",""), r.get("date",""), r.get("author",""), r.get("changes","")]
            for r in doc_data["revision_history"]
        ]
        dr.table(word_doc, ["Version","Date","Author","Changes"], rev_rows, widths=[0.8,1.2,1.5,3.5])

    out_path = os.path.join(tempfile.gettempdir(), f"{doc_type}_{safe_name}_{os.getpid()}.docx")
    word_doc.save(out_path)
    return FileResponse(out_path, filename=f"{doc_type}_{safe_name}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ─── User Stories Excel Export ───────────────────────────────────────────────────
# Generates .xlsx in the exact format: User Story ID | Summary | Description | Acceptance Criteria

@snow_router.post("/snow/userstories/excel")
async def export_userstories_excel(body: dict):
    """
    Generate User Stories & Acceptance Criteria as a .xlsx file.
    Accepts: project_name, user_stories (list of {id, summary, description, acceptance_criteria})
    OR: requirements (text to AI-extract stories from)
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    project_name = body.get("project_name", "ServiceNow Project")
    user_stories = body.get("user_stories", [])
    requirements = body.get("requirements", "")

    # If no structured stories, AI-extract them from requirements text
    if not user_stories and requirements:
        extract_prompt = f"""Extract all user stories from the requirements below.
For each story return a JSON array with objects containing:
- id: unique ID like "US-001"
- summary: short title (under 80 chars)
- description: full "As a [role], I want [goal] so that [benefit]" sentence
- acceptance_criteria: array of acceptance criteria strings (each is one bullet point)

Requirements:
{requirements[:4000]}

Return ONLY a JSON array, no markdown."""

        try:
            raw = await _call_claude(extract_prompt, max_tokens=4000)
            # Parse JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"```[a-z]*\n?", "", raw).replace("```", "").strip()
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                user_stories = parsed
            elif isinstance(parsed, dict) and "stories" in parsed:
                user_stories = parsed["stories"]
        except Exception as e:
            logger.warning(f"AI story extraction failed: {e}, using requirements as-is")
            # Parse manually from text
            story_blocks = [b.strip() for b in requirements.split("\n\n") if b.strip()]
            for i, block in enumerate(story_blocks, 1):
                lines = block.splitlines()
                if not lines:
                    continue
                title = re.sub(r"^\d+\.\s*", "", lines[0]).strip()
                ac, body_l = [], []
                in_ac = False
                for ln in lines[1:]:
                    if re.match(r"(?i)acceptance criteria", ln.strip()):
                        in_ac = True; continue
                    (ac if in_ac else body_l).append(ln.lstrip("-• ").strip())
                user_stories.append({
                    "id": f"US-{i:03d}",
                    "summary": title[:80],
                    "description": " ".join(b for b in body_l if b) or f"As a user, I want to {title.lower()}.",
                    "acceptance_criteria": [c for c in ac if c] or ["Criteria to be defined."]
                })

    # ── Build Excel ──
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "User Stories"

    # Header style — matching the sample file (Calibri 12, theme color, bold for readability)
    header_font = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_align = Alignment(wrap_text=True, vertical="center")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = ["User Story ID", "Summary", "Description", "Acceptance Criteria"]
    col_widths = [45, 40, 60, 80]

    for col_idx, (header, width) in enumerate(zip(headers, col_widths), 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = border
        ws.column_dimensions[chr(64 + col_idx)].width = width

    ws.row_dimensions[1].height = 22

    # Data rows
    row_font = Font(name="Calibri", size=11)
    row_align_wrap = Alignment(wrap_text=True, vertical="top")

    for row_idx, story in enumerate(user_stories, 2):
        us_id = story.get("id", story.get("user_story_id", f"US-{row_idx-1:03d}"))
        summary = story.get("summary", story.get("story", ""))[:200]
        description = story.get("description", "")
        ac = story.get("acceptance_criteria", story.get("criteria", ""))

        # Format acceptance criteria: newline-separated strings
        if isinstance(ac, list):
            ac_text = "\n".join(c.strip().lstrip("-•✓ ") for c in ac if str(c).strip())
        elif isinstance(ac, str):
            ac_text = ac.strip()
        else:
            ac_text = str(ac)

        row_data = [us_id, summary, description, ac_text]
        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = row_font
            cell.alignment = row_align_wrap
            cell.border = border

        # Alternate row shading
        if row_idx % 2 == 0:
            light_fill = PatternFill(start_color="EBF3FB", end_color="EBF3FB", fill_type="solid")
            for col_idx in range(1, 5):
                ws.cell(row=row_idx, column=col_idx).fill = light_fill

    # Freeze header row
    ws.freeze_panes = "A2"

    safe_name = dr.safe_filename(project_name)
    out_path = os.path.join(tempfile.gettempdir(), f"UserStories_{safe_name}_{os.getpid()}.xlsx")
    wb.save(out_path)

    return FileResponse(out_path, filename=f"UserStories_{safe_name}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ─── Build Pipeline (v6: sn_build) ────────────────────────────────────────────
# Plan -> validate -> app scope + current update set -> artifacts -> verify capture.

@snow_router.post("/snow/build/validate")
async def validate_build_plan(body: dict):
    """Check a plan without touching the instance: names, parents, references, script syntax."""
    plan = body.get("plan", body)
    return JSONResponse(content=await asyncio.to_thread(sn_build.validate_plan, plan))


@snow_router.post("/snow/build")
async def build_app(body: dict, background_tasks: BackgroundTasks):
    """
    Start a build. Body:
      plan: {...}                                   (from /snow/plan, reviewed by the user)
      snow_url / snow_user / snow_pass
      scope_mode: scoped | global | existing        (overrides plan.scope_mode)
      existing_app: sys_id / scope / name           (for scope_mode=existing)
      update_set_name, update_set_sys_id, update_set_description
      stop_on_error (default true), rollback_on_error (default true),
      restore_previous_context (default false), ignore_validation (default false)
    """
    plan = dict(body.get("plan") or {})
    if not plan:
        raise HTTPException(status_code=400, detail="plan is required")
    for k in ("scope_mode", "existing_app", "app_scope", "app_name"):
        if body.get(k):
            plan[k] = body[k]
    sn = snc.client_from(body)
    try:
        await asyncio.to_thread(sn.user)  # fail fast on bad credentials, before a job is queued
    except SNError as e:
        raise e.to_http("ServiceNow login")
    opts = {k: body.get(k) for k in ("update_set_name", "update_set_sys_id", "update_set_description",
                                      "ignore_validation", "restore_previous_context",
                                      "flow_engine", "flow_pause", "reuse_subflows") if body.get(k) is not None}
    opts["stop_on_error"] = body.get("stop_on_error", True)
    opts["rollback_on_error"] = body.get("rollback_on_error", True)
    if body.get("dry_run"):
        opts["dry_run"] = True
    job_id = sn_build.start_build(sn, plan, opts)
    background_tasks.add_task(sn_build.run_build, job_id, sn, plan, opts)
    return JSONResponse(content={"job_id": job_id, "status": "queued",
                                 "message": "Build started — poll /snow/build/{job_id}"})


@snow_router.get("/snow/build/{job_id}")
async def get_build_status(job_id: str):
    job = sn_build.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(content=json.loads(json.dumps(job, default=str)))


@snow_router.post("/snow/build/{job_id}/respond")
async def respond_to_build(job_id: str, body: dict):
    """Answer a paused build (Flow Designer step): {"pause_id": "...", "action": "continue|skip|retry|cancel"}."""
    try:
        ok = sn_build.respond(job_id, body.get("pause_id") or "", body.get("action") or "continue",
                              str(body.get("value") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=409, detail="This build is not waiting for an answer (or the pause changed)")
    return JSONResponse(content={"ok": True})


@snow_router.get("/snow/flows/engines")
async def flow_engines():
    """Which flow engines are available: Flow Server (FLOW_SERVER_URL) and/or the Flow Designer browser engine."""
    return JSONResponse(content=sn_build.flow_engines())


@snow_router.post("/snow/flows/subflow-library")
async def subflow_library(body: dict):
    """Subflows the agent remembers for this instance (reused by later builds instead of rebuilding)."""
    from flow_agent.kb import SubflowKB
    url = snc.normalize_instance(body.get("snow_url") or body.get("instance_url") or snc.default_instance_url())
    return JSONResponse(content={"instance": url, "subflows": SubflowKB().entries(url)})


@snow_router.post("/snow/flows/subflow-library/remove")
async def subflow_library_remove(body: dict):
    from flow_agent.kb import SubflowKB
    url = snc.normalize_instance(body.get("snow_url") or body.get("instance_url") or snc.default_instance_url())
    if not body.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    return JSONResponse(content={"removed": SubflowKB().remove(url, body["name"])})


@snow_router.get("/snow/builds")
async def list_builds(limit: int = 20):
    return JSONResponse(content={"builds": sn_build.list_jobs(limit)})


# ─── Update sets & applications ────────────────────────────────────────────────

def _dv(v, key="display_value"):
    return v.get(key) if isinstance(v, dict) else v


@snow_router.post("/snow/updatesets/list")
async def list_update_sets(body: dict):
    """Update sets (default: in progress, optionally for one application) + the user's current one."""
    sn = snc.client_from(body)

    def work():
        q = "state=" + (body.get("state") or "in progress")
        if body.get("application"):
            app = snc.resolve_app(sn, body["application"])
            if not app:
                raise SNError(404, f"Application '{body['application']}' not found")
            q += f"^application={app['sys_id']}"
        rows = sn.query("sys_update_set", q + "^ORDERBYDESCsys_updated_on",
                        "sys_id,name,state,application,sys_updated_on", limit=int(body.get("limit", 50)),
                        display_value="all")
        user = sn.user()
        cur = sn.get_one("sys_user_preference", f"user={user['sys_id']}^name=sys_update_set", "value")
        app_pref = sn.get_one("sys_user_preference", f"user={user['sys_id']}^name=apps.current_app", "value")
        out = []
        for r in rows:
            sid = _dv(r.get("sys_id"), "value")
            out.append({"sys_id": sid, "name": _dv(r.get("name")), "state": _dv(r.get("state"), "value"),
                        "application": _dv(r.get("application")),
                        "application_sys_id": _dv(r.get("application"), "value"),
                        "updated": _dv(r.get("sys_updated_on")),
                        "captured_changes": sn.count("sys_update_xml", f"update_set={sid}"),
                        "current": bool(cur and cur.get("value") == sid),
                        "link": sn.link("sys_update_set", sid)})
        return {"update_sets": out, "current_update_set": (cur or {}).get("value"),
                "current_app": (app_pref or {}).get("value")}
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Update set list")


@snow_router.post("/snow/apps/list")
async def list_apps(body: dict):
    sn = snc.client_from(body)

    def work():
        rows = sn.query("sys_scope", "scopeSTARTSWITHx_^ORscope=global^ORDERBYname", "sys_id,name,scope,version",
                        int(body.get("limit", 200)))
        return {"apps": rows, "vendor_prefix": snc.vendor_prefix(sn)}
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Application list")


@snow_router.post("/snow/updatesets/context")
async def set_update_set_context(body: dict):
    """
    Make an application + update set current for the connected user (same approach as the
    SoftProdigy platform's /setServiceNowContext). Creates the update set if it does not exist.
    Body: scope_mode (existing|global|scoped), application, app_name, update_set_name | update_set_sys_id
    """
    sn = snc.client_from(body)

    def work():
        mode = body.get("scope_mode") or ("existing" if body.get("application") else "global")
        app = snc.ensure_app(sn, mode=mode, app_name=body.get("app_name") or body.get("application") or "SoftProdigy App",
                             app_scope=body.get("app_scope") or "", existing_app=body.get("application") or "")
        name = body.get("update_set_name") or f"HAI_{datetime.now().strftime('%Y%m%d_%H%M')}"
        return snc.set_context(sn, app=app, update_set_name=name, description=body.get("description", ""),
                               existing_update_set=body.get("update_set_sys_id") or "")
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Set update set context")


@snow_router.post("/snow/updatesets/changes")
async def update_set_changes(body: dict):
    """What is captured in an update set (sys_update_xml)."""
    sn = snc.client_from(body)
    if not body.get("update_set_sys_id"):
        raise HTTPException(status_code=400, detail="update_set_sys_id is required")
    try:
        rows = await asyncio.to_thread(snc.captured_changes, sn, body["update_set_sys_id"],
                                       int(body.get("limit", 500)))
        by_type = Counter(r.get("type") or "?" for r in rows)
        return JSONResponse(content={"count": len(rows), "by_type": dict(by_type), "changes": rows,
                                     "link": sn.link("sys_update_set", body["update_set_sys_id"])})
    except SNError as e:
        raise e.to_http("Read update set")


@snow_router.post("/snow/updatesets/complete")
async def complete_update_set(body: dict):
    """Mark an update set Complete (refuses when it captured nothing) and return the export link."""
    sn = snc.client_from(body)
    if not body.get("update_set_sys_id"):
        raise HTTPException(status_code=400, detail="update_set_sys_id is required")
    try:
        return JSONResponse(content=await asyncio.to_thread(snc.complete_update_set, sn, body["update_set_sys_id"]))
    except SNError as e:
        raise e.to_http("Complete update set")


# ─── Universal artifact manager ───
#
# Feature-specific endpoints are convenient for common tasks, but ServiceNow
# implementations contain many more artifact tables than a static UI can name.
# These endpoints provide the Table API's general capability without hiding
# which table or fields will be changed.  The caller's ServiceNow ACLs remain
# authoritative, and every mutation requires an explicit confirm=true.

def _artifact_table(body: dict) -> str:
    table = str(body.get("table") or "").strip()
    if not _TABLE_NAME_RE.fullmatch(table):
        raise HTTPException(status_code=400, detail="table must be a ServiceNow table name (letters, digits and underscores only)")
    return table


def _artifact_sys_id(body: dict) -> str:
    sys_id = str(body.get("sys_id") or "").strip()
    if not snc.is_sys_id(sys_id):
        raise HTTPException(status_code=400, detail="sys_id must be a 32-character ServiceNow sys_id")
    return sys_id


def _artifact_limit(body: dict) -> int:
    try:
        return max(1, min(int(body.get("limit", 100)), 500))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit must be a whole number from 1 to 500")


def _artifact_query(body: dict) -> str:
    query = str(body.get("query") or "").strip()
    if len(query) > 4000:
        raise HTTPException(status_code=400, detail="query is too long (maximum 4000 characters)")
    # Encoded-query JavaScript executes on the instance.  The manager supports
    # normal encoded queries, but not server-side JavaScript supplied over HTTP.
    if "javascript:" in query.lower():
        raise HTTPException(status_code=400, detail="javascript: is not allowed in an artifact query")
    return query


def _artifact_return_fields(body: dict) -> str:
    raw = body.get("return_fields", body.get("fields", ""))
    if isinstance(raw, dict):
        return ""
    if raw in (None, ""):
        return ""
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts or any(not _FIELD_NAME_RE.fullmatch(p) for p in parts):
        raise HTTPException(status_code=400, detail="return_fields must be a comma-separated list of field names")
    return ",".join(parts)


def _artifact_payload(body: dict) -> dict:
    fields = body.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(status_code=400, detail="fields must be a non-empty JSON object")
    if len(fields) > 100:
        raise HTTPException(status_code=400, detail="fields may contain at most 100 values per request")
    invalid = [str(k) for k in fields if not _FIELD_NAME_RE.fullmatch(str(k))]
    managed = [str(k) for k in fields if str(k).lower() in _SYSTEM_MANAGED_FIELDS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid field name(s): {', '.join(invalid[:5])}")
    if managed:
        raise HTTPException(status_code=400, detail=f"System-managed field(s) cannot be written: {', '.join(managed)}")
    return fields


def _require_artifact_confirmation(body: dict, action: str):
    if body.get("confirm") is not True:
        raise HTTPException(status_code=428, detail=f"{action} changes ServiceNow data; resend with confirm=true after review")


def _artifact_context(sn: snc.SNClient, body: dict) -> Optional[dict]:
    """Optionally make a scoped app/update set current before a metadata write.

    Data records such as incidents are not normally update-set artifacts.  For
    configuration records, passing `context` is the supported, session-based
    way to select a scope and update set; a made-up HTTP header is not enough.
    """
    context = body.get("context")
    if context in (None, {}):
        return None
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail="context must be an object")
    update_set_name = str(context.get("update_set_name") or "").strip()
    update_set_sys_id = str(context.get("update_set_sys_id") or "").strip()
    if not (update_set_name or update_set_sys_id):
        raise HTTPException(status_code=400, detail="context requires update_set_name or update_set_sys_id")
    mode = str(context.get("scope_mode") or ("existing" if context.get("application") else "global")).lower()
    if mode not in ("global", "existing", "scoped"):
        raise HTTPException(status_code=400, detail="context.scope_mode must be global, existing or scoped")
    application = str(context.get("application") or "").strip()
    if mode == "existing" and not application:
        raise HTTPException(status_code=400, detail="context.application is required for scope_mode='existing'")
    app_name = str(context.get("app_name") or application or "SoftProdigy App").strip()
    app = snc.ensure_app(sn, mode=mode, app_name=app_name,
                         app_scope=str(context.get("app_scope") or ""), existing_app=application)
    return snc.set_context(sn, app=app, update_set_name=update_set_name or "Selected update set",
                           description=str(context.get("description") or ""),
                           existing_update_set=update_set_sys_id)


def _artifact_response(sn: snc.SNClient, table: str, record: dict, action: str,
                       context: Optional[dict] = None) -> dict:
    sys_id = snc.ref_value(record.get("sys_id"))
    out = {"ok": True, "action": action, "table": table, "record": record}
    if sys_id:
        out["sys_id"] = sys_id
        out["link"] = sn.link(table, sys_id)
    if context:
        out["context"] = context
        out["capture_note"] = ("The selected scope/update set is current. ServiceNow captures configuration "
                               "metadata only; transactional data records are not update-set artifacts.")
    return out


@snow_router.post("/snow/artifacts/schema")
async def artifact_schema(body: dict):
    """Describe a table's dictionary fields, including types, references and mandatory flags."""
    sn = snc.client_from(body)
    table = _artifact_table(body)

    def work():
        fields = sn.query("sys_dictionary", f"name={table}^internal_type!=collection^active=true",
                          "element,column_label,internal_type,reference,mandatory,max_length,default_value,choice,read_only,active",
                          limit=500)
        return {"table": table, "field_count": len(fields),
                "fields": sorted(fields, key=lambda item: item.get("element") or "")}
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Read artifact schema")


@snow_router.post("/snow/artifacts/list")
async def list_artifacts(body: dict):
    """List artifacts from any table using a normal (non-JavaScript) encoded query."""
    sn = snc.client_from(body)
    table, query, fields, limit = _artifact_table(body), _artifact_query(body), _artifact_return_fields(body), _artifact_limit(body)
    try:
        offset = max(0, int(body.get("offset", 0)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="offset must be a whole number zero or greater")
    display_value = str(body.get("display_value") or "false").lower()
    if display_value not in ("false", "true", "all"):
        raise HTTPException(status_code=400, detail="display_value must be false, true or all")
    try:
        records = await asyncio.to_thread(sn.query, table, query, fields, limit, display_value, offset)
        return JSONResponse(content={"table": table, "count": len(records), "limit": limit,
                                     "offset": offset, "records": records})
    except SNError as e:
        raise e.to_http("List artifacts")


@snow_router.post("/snow/artifacts/get")
async def get_artifact(body: dict):
    """Read one artifact by sys_id."""
    sn = snc.client_from(body)
    table, sys_id, fields = _artifact_table(body), _artifact_sys_id(body), _artifact_return_fields(body)
    try:
        record = await asyncio.to_thread(sn.get, table, sys_id, fields, str(body.get("display_value") or "false"))
        return JSONResponse(content=_artifact_response(sn, table, record, "read"))
    except SNError as e:
        raise e.to_http("Read artifact")


@snow_router.post("/snow/artifacts/create")
async def create_artifact(body: dict):
    """Create an artifact. confirm=true is required; optional context selects scope/update set."""
    _require_artifact_confirmation(body, "Create")
    sn, table, payload = snc.client_from(body), _artifact_table(body), _artifact_payload(body)

    def work():
        context = _artifact_context(sn, body)
        return _artifact_response(sn, table, sn.create(table, payload, _artifact_return_fields(body)), "created", context)
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Create artifact")


@snow_router.post("/snow/artifacts/update")
async def update_artifact(body: dict):
    """Update an artifact by sys_id. confirm=true is required."""
    _require_artifact_confirmation(body, "Update")
    sn, table, sys_id, payload = snc.client_from(body), _artifact_table(body), _artifact_sys_id(body), _artifact_payload(body)

    def work():
        context = _artifact_context(sn, body)
        return _artifact_response(sn, table, sn.patch(table, sys_id, payload), "updated", context)
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Update artifact")


@snow_router.post("/snow/artifacts/delete")
async def delete_artifact(body: dict):
    """Delete an artifact by sys_id. A reason and confirm=true are mandatory."""
    _require_artifact_confirmation(body, "Delete")
    table, sys_id = _artifact_table(body), _artifact_sys_id(body)
    if not str(body.get("reason") or "").strip():
        raise HTTPException(status_code=400, detail="reason is required when deleting an artifact")
    sn = snc.client_from(body)

    def work():
        context = _artifact_context(sn, body)
        sn.delete(table, sys_id)
        out = {"ok": True, "action": "deleted", "table": table, "sys_id": sys_id,
               "reason": str(body["reason"]).strip()}
        if context:
            out["context"] = context
            out["capture_note"] = ("The selected scope/update set is current. ServiceNow captures configuration "
                                   "metadata only; transactional data records are not update-set artifacts.")
        return out
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Delete artifact")


# ─── Health Scan ──────────────────────────────────────────────────────────────

@snow_router.post("/snow/healthscan")
async def health_scan(body: dict):
    """Live instance health scan (same engine as /snow/health-scan). Fails with 401 on bad credentials."""
    sn = snc.client_from(body)
    return JSONResponse(content=await asyncio.to_thread(sn_health.run_scan, sn))


# ─── Extract User Stories from Documents ──────────────────────────────────────

@snow_router.post("/snow/extract/from-document")
async def extract_from_document(
    file: UploadFile = File(None),
    doc_type: str = Form("BRD"),
    text: str = Form(None)
):
    """Extract user stories from BRD, FRD, or TSD document differently."""

    raw_text = text or ""
    if file and file.filename:
        file_bytes = await file.read()
        fname = file.filename.lower()
        if fname.endswith(".pdf"):
            raw_text = _extract_pdf_text(file_bytes)
        elif fname.endswith((".docx", ".doc")):
            raw_text = _extract_docx_text(file_bytes)
        else:
            raw_text = file_bytes.decode("utf-8", errors="replace")

    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No content to extract from")

    doc_type = doc_type.upper()

    # Different extraction strategy per doc type
    if doc_type == "BRD":
        focus = "Focus on BUSINESS requirements: business objectives, stakeholder needs, business rules, high-level process flows. Extract stories at the business/functional level."
    elif doc_type == "FRD":
        focus = "Focus on FUNCTIONAL requirements: specific system behaviors, UI interactions, data validation rules, workflow steps. Extract stories at the feature/function level."
    elif doc_type == "TSD":
        focus = "Focus on TECHNICAL requirements: system design, API specifications, data models, integration points, performance requirements. Extract stories at the technical/implementation level."
    else:
        focus = "Extract all types of requirements comprehensively."

    system = f"You are an expert Agile BA extracting user stories from {doc_type} documents. {focus} Respond with JSON only."

    prompt = f"""Extract comprehensive user stories from this {doc_type} document.

{focus}

DOCUMENT CONTENT:
{raw_text[:7000]}

Return JSON:
{{
  "doc_type": "{doc_type}",
  "extracted_from": "Document analysis",
  "user_stories": [
    {{
      "id": "US-001",
      "title": "Story title",
      "as_a": "Role",
      "i_want": "Feature",
      "so_that": "Value",
      "priority": "High/Medium/Low",
      "story_points": 5,
      "acceptance_criteria": ["AC1", "AC2"],
      "source_section": "Which section of doc this came from",
      "servicenow_table": "Suggested SN table",
      "fields": [
        {{"name": "field_name", "type": "string", "label": "Label", "required": true}}
      ]
    }}
  ],
  "tables_required": [
    {{
      "name": "x_custom_table",
      "display_name": "Display Name",
      "description": "Purpose",
      "fields": []
    }}
  ],
  "total_stories": 0,
  "summary": "Summary of what was extracted and from which sections"
}}"""

    data = await _ai_json(prompt, system=system)
    for st in data.get("user_stories") or []:
        if isinstance(st, dict):
            st["acceptance_criteria"] = dr.as_list(st.get("acceptance_criteria"))
    data["total_stories"] = len(data.get("user_stories") or [])
    return JSONResponse(content=data)


# ─── Create Stories in ServiceNow ────────────────────────────────────────────

@snow_router.post("/snow/stories/create-in-snow")
async def create_stories_in_snow(body: dict):
    """
    Create user stories in ServiceNow. Target table: rm_story (Agile Development) when it exists,
    otherwise the table given in body.fallback_table (default: none -> the story is reported as failed).
    """
    stories = body.get("user_stories", body.get("stories", []))
    if not stories:
        raise HTTPException(status_code=400, detail="No stories provided")
    sn = snc.client_from(body)

    def work():
        target = "rm_story" if sn.table_exists("rm_story") else (body.get("fallback_table") or "")
        if not target:
            raise SNError(404, "rm_story does not exist (Agile Development plugin not active). "
                               "Activate the plugin or pass fallback_table (e.g. 'task').")
        results = {"table": target, "created": [], "failed": []}
        pri = {"critical": "1", "high": "2", "medium": "3", "low": "4"}
        for st in stories:
            title = (st.get("title") or st.get("i_want") or st.get("summary") or "User Story")[:160]
            ac = dr.as_list(st.get("acceptance_criteria"))
            desc = st.get("description") or (f"As a {st.get('as_a', 'user')}, I want {st.get('i_want', title)}"
                                             f"{', so that ' + st['so_that'] if st.get('so_that') else ''}.")
            payload = {"short_description": title, "description": desc,
                       "priority": pri.get(str(st.get("priority", "medium")).lower(), "3")}
            if target == "rm_story":
                payload["acceptance_criteria"] = "\n".join(ac)
                try:
                    payload["story_points"] = int(st.get("story_points") or 0) or None
                except (TypeError, ValueError):
                    pass
            else:
                payload["description"] += ("\n\nAcceptance Criteria:\n" + "\n".join(f"- {c}" for c in ac)) if ac else ""
            try:
                rec = sn.create(target, {k: v for k, v in payload.items() if v is not None}, "sys_id,number")
                results["created"].append({"title": title, "sys_id": rec.get("sys_id"), "number": rec.get("number"),
                                           "table": target, "link": sn.link(target, rec.get("sys_id", ""))})
            except SNError as e:
                results["failed"].append({"title": title, "error": str(e)})
        return results
    try:
        return JSONResponse(content=await asyncio.to_thread(work))
    except SNError as e:
        raise e.to_http("Create stories")
