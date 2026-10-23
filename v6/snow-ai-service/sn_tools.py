"""
All SoftProdigy ServiceNow tools (the platform's now_mcp library, vendored in ./vendor) inside the agent.

  GET  /snow/tools                  catalog: name, description, packages, read/write   (?package=&q=)
  GET  /snow/tools/packages         role packages with tool counts
  GET  /snow/tools/{name}           one tool with its full input schema
  POST /snow/tools/run              run a tool as the signed-in user        {name, arguments, confirm, creds}
  POST /snow/tools/ask              plain English -> AI picks tool + arguments; reads run at once,
                                    writes come back as a proposal to confirm   {message, creds}

Every call runs with the credentials the user signed in with (one tool server per user,
cached for 30 minutes). Tools that change data need confirm=true.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException

_VENDOR = Path(__file__).resolve().parent / "vendor"
os.environ.setdefault("TOOL_PACKAGE_CONFIG_PATH", str(_VENDOR / "config" / "tool_packages.yaml"))
os.environ.setdefault("MCP_TOOL_PACKAGE", "full")
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

import groq_client  # noqa: E402
import itam_bridge  # noqa: E402  (ITAM tools from servicenow-mcp's snow_itam package)
import itom_bridge  # noqa: E402  (ITOM tools from servicenow-mcp's snow_itom package)
import sn_client as snc  # noqa: E402
import tool_match  # noqa: E402

logger = logging.getLogger("sn_tools")
snow_tools_router = APIRouter(tags=["ServiceNow tools (SoftProdigy now_mcp)"])

READ_PREFIXES = ("list_", "get_", "search_", "query_", "discover_", "validate_", "analyze_", "explain_", "hint_",
                 "verify_", "collect_", "describe_", "check_", "preview_", "find_", "lookup_", "count_",
                 "route_atf_request", "health_", "governance_", "instance_preflight", "summarize_", "compare_",
                 "export_", "show_", "read_", "inspect_", "audit_", "diagnose_", "recommend_", "suggest_",
                 "estimate_", "review_", "test_connection", "detect_", "run_report", "generate_asset_",
                 "generate_duplicate_asset_report", "generate_lifecycle_report", "generate_missing_asset_report",
                 "generate_stockroom_inventory_report", "generate_warranty_expiry_report", "generate_subflow_source",
                 "generate_manual_test_fallback", "servicenow_atf_find_", "servicenow_atf_get_",
                 "servicenow_atf_debug_", "servicenow_atf_preview_", "servicenow_atf_validate_",
                 "servicenow_resolve_reference", "test_acl_access")

_catalog_lock = threading.Lock()
_catalog: Optional[Dict[str, Any]] = None
_servers: Dict[str, Tuple[float, Any]] = {}
_servers_lock = threading.Lock()
SERVER_TTL = 1800


def _base(name: str) -> str:
    return name.split(".", 1)[-1]


# The vendored asset module is superseded by the ITAM tools (snow_itam), which validate lifecycle states live
# against the instance. Its tools stay reachable through the /assets/* REST routes but not in this catalog.
SUPERSEDED_MODULES = ("servicenow_mcp.tools.asset_management_tools",)

# Runs arbitrary server-side JavaScript through the sys.scripts.do UI page, whose HTTP 200 does not prove the script ran
# (a login page is also 200). Off unless an administrator opts in.
UNSAFE_TOOLS = {"execute_background_script": "AGENT_ALLOW_BACKGROUND_SCRIPTS"}


def _disabled(name: str) -> bool:
    flag = UNSAFE_TOOLS.get(name)
    return bool(flag) and os.getenv(flag, "false").strip().lower() != "true"


def tool_kind(name: str) -> str:
    if itam_bridge.is_itam_tool(name):
        return itam_bridge.kind_of(name)
    if itom_bridge.is_itom_tool(name):
        return itom_bridge.kind_of(name)
    return "read" if _base(name).startswith(READ_PREFIXES) else "write"


def catalog() -> Dict[str, Any]:
    """Static tool catalog (no ServiceNow call)."""
    global _catalog
    with _catalog_lock:
        if _catalog is not None:
            return _catalog
        import yaml
        from servicenow_mcp.tools.knowledge_base import create_category, list_categories
        from servicenow_mcp.utils.tool_utils import get_tool_definitions
        defs = get_tool_definitions(create_category, list_categories)
        packages = yaml.safe_load(open(os.environ["TOOL_PACKAGE_CONFIG_PATH"], encoding="utf-8")) or {}
        enabled_pkg = os.environ.get("MCP_TOOL_PACKAGE", "full")
        if enabled_pkg == "full":   # every tool of every role package
            enabled = {n for v in packages.values() for n in (v or [])} & set(defs)
        else:
            enabled = set(packages.get(enabled_pkg) or []) & set(defs)
        member: Dict[str, List[str]] = {}
        for pkg, names in packages.items():
            if pkg in ("full", "none"):
                continue
            for n in names or []:
                member.setdefault(n, []).append(pkg)
        tools = {}
        for n in sorted(enabled):
            impl, model, _ret, desc, _ser = defs[n]
            if getattr(impl, "__module__", "") in SUPERSEDED_MODULES or itam_bridge.is_itam_tool(n) \
                    or itom_bridge.is_itom_tool(n) or _disabled(n):
                continue
            tools[n] = {"name": n, "description": (desc or "").strip(), "packages": member.get(n, []),
                        "kind": tool_kind(n), "_model": model}
        for spec in itam_bridge.TOOLS:
            tools[spec.name] = {"name": spec.name, "description": spec.description, "packages": ["itam"],
                                "kind": spec.kind, "_itam": spec}
        for spec in itom_bridge.TOOLS:   # same ToolSpec type, so schema_of() works through "_itam"
            pkg = "cmdb" if spec.area == "cmdb" else "itom"
            tools[spec.name] = {"name": spec.name, "description": spec.description, "packages": [pkg],
                                "kind": spec.kind, "_itam": spec}
        pkgs = {p: [n for n in (v or []) if n in tools] for p, v in packages.items() if p not in ("full", "none")}
        pkgs["itam"] = [t.name for t in itam_bridge.TOOLS]
        pkgs["itom"] = [t.name for t in itom_bridge.TOOLS if t.area != "cmdb"]
        pkgs["cmdb"] = [t.name for t in itom_bridge.TOOLS if t.area == "cmdb"]
        _catalog = {"tools": tools, "packages": pkgs, "enabled_package": enabled_pkg}
        return _catalog


def schema_of(name: str) -> Dict[str, Any]:
    t = catalog()["tools"].get(name)
    if not t:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{name}'")
    if t.get("_itam"):
        return t["_itam"].json_schema()
    try:
        return t["_model"].model_json_schema()
    except Exception as e:
        return {"error": f"schema unavailable: {e}"}


def _public(t: dict) -> dict:
    return {k: v for k, v in t.items() if not k.startswith("_")}


# ════════════════════════════════════════════════════════════════════════════
# Per-user tool server
# ════════════════════════════════════════════════════════════════════════════

def _tls_setup():
    """SNOW_VERIFY_TLS=false (lab only) must also apply to the tool library's own requests calls."""
    if snc.verify_tls() or getattr(_tls_setup, "_done", False):
        return
    import requests
    orig = requests.Session.request

    def no_verify(self, method, url, **kw):
        kw["verify"] = False
        return orig(self, method, url, **kw)
    requests.Session.request = no_verify
    _tls_setup._done = True
    logger.warning("SNOW_VERIFY_TLS=false: TLS verification disabled for ServiceNow tool calls")


def server_for(url: str, user: str, password: str):
    from servicenow_mcp.server import ServiceNowMCP
    _tls_setup()
    url = snc.normalize_instance(url)
    key = hashlib.sha256(f"{url}\n{user}\n{password}".encode()).hexdigest()
    now = time.time()
    with _servers_lock:
        for k in [k for k, (ts, _) in _servers.items() if now - ts > SERVER_TTL]:
            _servers.pop(k, None)
        if key in _servers:
            _servers[key] = (now, _servers[key][1])
            return _servers[key][1]
    srv = ServiceNowMCP({"instance_url": url, "auth": {"type": "basic",
                                                       "basic": {"username": user, "password": password}}})
    srv.enabled_tool_names = list(catalog()["tools"])   # same tool set as the catalog
    with _servers_lock:
        if len(_servers) > 50:
            _servers.pop(min(_servers, key=lambda k: _servers[k][0]), None)
        _servers[key] = (now, srv)
    return srv


def _parse_result(r: Any) -> Any:
    content = getattr(r, "structuredContent", None)
    if content is not None:
        return content
    items = getattr(r, "content", None) or (r if isinstance(r, list) else [])
    text = "".join(getattr(i, "text", "") for i in items)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"text": text}


# ── vendored scripting writes: duplicate check before, read-back after ─────────────────────────────────────────
# tool -> (record table, record name field, argument names for the name, record scope field, argument for the scope)
SCRIPT_WRITE_GUARDS: Dict[str, Tuple[str, str, Tuple[str, ...], str, str]] = {
    "create_business_rule": ("sys_script", "name", ("name",), "collection", "table"),
    "create_script_include": ("sys_script_include", "name", ("name",), "", ""),
    "create_ui_policy": ("sys_ui_policy", "short_description", ("short_description", "name"), "table", "table"),
    "create_data_policy": ("sys_data_policy2", "short_description", ("short_description",), "model_table", "table"),
    "create_ui_action": ("sys_ui_action", "name", ("name",), "table", "table"),
    "create_fix_script": ("sys_script_fix", "name", ("name",), "", ""),
    "create_catalog_client_script": ("catalog_script_client", "name", ("name",), "cat_item", "catalog_item_id"),
    "create_data_policy_rule": ("sys_data_policy_rule", "field", ("field",), "sys_data_policy", "data_policy_id"),
    # guarded only when the parent is given by sys_id (a parent name is resolved inside the tool)
    "create_ui_policy_action": ("sys_ui_policy_action", "field", ("field",), "ui_policy", "ui_policy_id"),
}
_SID = re.compile(r"^[0-9a-f]{32}$")


def _guard_spec(name: str, args: dict) -> Optional[Tuple[str, str, str, str, str]]:
    """(table, name_field, name_value, scope_field, scope_value) for a guarded create, else None."""
    if name == "create_server_side_script":
        from servicenow_mcp.tools.server_side_script_tools import _resolve_table
        try:
            table = _resolve_table(args.get("script_type"), args.get("table"))
        except ValueError:
            return None          # the tool itself refuses it
        fields = args.get("fields") or {}
        scope = ("collection", fields.get("collection") or "") if table == "sys_script" else ("", "")
        return (table, "name", str(args.get("name") or fields.get("name") or ""), *scope) if (
            args.get("name") or fields.get("name")) else None
    spec = SCRIPT_WRITE_GUARDS.get(name)
    if not spec:
        return None
    table, name_field, arg_names, scope_field, scope_arg = spec
    if name == "create_ui_policy_action" and not _SID.match(str(args.get(scope_arg) or "")):
        return None
    value = next((str(args[a]) for a in arg_names if args.get(a)), "")
    return (table, name_field, value, scope_field, str(args.get(scope_arg) or "") if scope_arg else "") if value \
        else None


def _created_sys_id(result: Any) -> str:
    if isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, str) and _SID.match(v) and (k == "sys_id" or k.endswith("_id")):
                return v
        for v in result.values():
            found = _created_sys_id(v)
            if found:
                return found
    return ""


def _guarded_write(creds: dict, name: str, arguments: dict, run: Callable[[dict], Any]) -> Any:
    """Duplicate check (refuses unless allow_duplicate=true), run, then read the created record back."""
    args = dict(arguments or {})
    allow_dup = bool(args.pop("allow_duplicate", False))
    spec = _guard_spec(name, args)
    if not spec:
        return run(args)
    table, name_field, value, scope_field, scope_value = spec
    sn = snc.SNClient(creds["snow_url"], creds["snow_user"], creds["snow_pass"])
    query = f"{name_field}={value}" + (f"^{scope_field}={scope_value}" if scope_field and scope_value else "")
    try:
        existing = sn.query(table, query, "sys_id", limit=2)
    except snc.SNError as e:
        raise e.to_http(f"Duplicate check on {table}")
    if existing and not allow_dup:
        raise HTTPException(status_code=409, detail=(
            f"DUPLICATE: a {table} record with {name_field}='{value}'"
            + (f" on {scope_value}" if scope_value else "") + f" already exists (sys_id {existing[0]['sys_id']}). "
            "Nothing was created. Update that record instead, or pass allow_duplicate=true to create another."))
    result = run(args)
    if _looks_failed(result):
        return result
    sys_id = _created_sys_id(result)
    issues: List[str] = []
    record: Dict[str, Any] = {}
    if not sys_id:
        issues.append("the tool did not return the new record's sys_id, so it could not be read back")
    else:
        fields = ",".join(f for f in ("sys_id", name_field, scope_field, "active",
                                      "advanced" if table == "sys_script" else "", "script" if table == "sys_script"
                                      else "") if f)
        try:
            record = sn.get(table, sys_id, fields)
        except snc.SNError as e:
            issues.append(f"read-back failed: {e}")
        if record:
            if str(record.get(name_field, "")) != value:
                issues.append(f"{name_field} is '{record.get(name_field)}', expected '{value}'")
            if scope_field and scope_value and snc.ref_value(record.get(scope_field)) != scope_value:
                issues.append(f"{scope_field} is '{record.get(scope_field)}', expected '{scope_value}'")
            if table == "sys_script" and record.get("script") and str(record.get("advanced")) != "true":
                issues.append("advanced is not true, so ServiceNow ignores this rule's script")
        elif sys_id and not issues:
            issues.append(f"{table} {sys_id} was not found after the create")
    verification = {"verified": not issues, "table": table, "sys_id": sys_id, "issues": issues}
    if isinstance(result, dict):
        return {**result, "verification": verification}
    return {"result": result, "verification": verification}


def run_tool_sync(creds: dict, name: str, arguments: dict) -> Any:
    url, user, pw = creds.get("snow_url"), creds.get("snow_user"), creds.get("snow_pass")
    if not (url and user and pw):
        raise HTTPException(status_code=400, detail="ServiceNow instance URL, username and password are required")
    if _disabled(name):
        raise HTTPException(status_code=403, detail=f"'{name}' is disabled in this agent (it runs arbitrary server "
                                                    f"scripts and cannot verify the result). An administrator can "
                                                    f"enable it with {UNSAFE_TOOLS[name]}=true.")
    if name not in catalog()["tools"]:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{name}'")
    if name in SCRIPT_WRITE_GUARDS or name == "create_server_side_script":
        return _guarded_write(creds, name, arguments, lambda a: _run_vendored(creds, name, a))
    return _run_vendored(creds, name, arguments)


def _run_vendored(creds: dict, name: str, arguments: dict) -> Any:
    url, user, pw = creds.get("snow_url"), creds.get("snow_user"), creds.get("snow_pass")
    if itam_bridge.is_itam_tool(name):
        return itam_bridge.run(creds, name, arguments or {})
    if itom_bridge.is_itom_tool(name):
        return itom_bridge.run(creds, name, arguments or {})
    srv = server_for(url, user, pw)
    try:
        r = asyncio.run(srv._call_tool_impl(name, arguments or {}))
    except ValueError as e:        # bad arguments / tool disabled
        raise HTTPException(status_code=422, detail=str(e)[:1500])
    except RuntimeError as e:      # the tool raised
        _raise_if_table_missing(creds, name, str(e))
        raise HTTPException(status_code=502, detail=str(e)[:1500])
    result = _parse_result(r)
    if _looks_failed(result):
        _raise_if_table_missing(creds, name, json.dumps(result, default=str)[:6000])
    return result


# ── vendored tools: a table that is not on the instance means a plugin is missing ──────────────────────────
# The vendored library reports ServiceNow failures inside its result. When a failure names a table (ServiceNow's
# "Invalid table X", or a Table API URL), the table is looked up in sys_db_object: only a table that really does not
# exist turns into 424 "plugin required"; any other failure is returned unchanged.
_TABLE_REF = re.compile(r"(?:Invalid table\s+|/api/now/(?:v\d+/)?table/)([A-Za-z0-9_]+)")
# Plugins whose table prefix is certain (documented); other tables are named so the user can find the plugin.
_KNOWN_TABLE_PLUGINS = (("em_", "Event Management (com.glide.em)"), ("discovery_", "Discovery (com.snc.discovery)"),
                        ("alm_", "Asset Management (com.snc.asset_management)"),
                        ("ast_contract", "Contract Management (com.snc.contract_management)"),
                        ("proc_", "Procurement (com.snc.procurement)"))


def _looks_failed(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("success") is False or result.get("ok") is False or result.get("error"):
        return True
    return "Invalid table" in str(result.get("text") or result.get("message") or "")


def _raise_if_table_missing(creds: dict, name: str, text: str) -> None:
    tables = list(dict.fromkeys(_TABLE_REF.findall(text or "")))[:3]
    if not tables:
        return
    try:
        sn = snc.SNClient(creds["snow_url"], creds["snow_user"], creds["snow_pass"])
        missing = [t for t in tables if not sn.query("sys_db_object", f"name={t}", "sys_id", limit=1)]
    except Exception:   # cannot check: leave the original error as it is
        return
    if not missing:
        return
    t = missing[0]
    # A near-identical table (e.g. pa_indicator vs pa_indicators) means the tool uses a wrong name — not a plugin
    try:
        near = [r["name"] for r in sn.query("sys_db_object", f"nameIN{t}s,{t[:-1]}", "name", limit=2)]
    except Exception:
        near = []
    if near:
        raise HTTPException(status_code=412, detail=f"{name} uses table {t}, but this instance's table is {near[0]} "
                                                    f"— the tool needs correcting; no plugin is missing. Nothing was "
                                                    f"changed.")
    plugin = next((p for prefix, p in _KNOWN_TABLE_PLUGINS if t.startswith(prefix)), "")
    what = (f"activate {plugin}" if plugin else
            f"activate the ServiceNow plugin that provides the '{t}' table (System Definition > Plugins)")
    raise HTTPException(status_code=424, detail=f"{name} is not available on this instance — table {t} is missing; "
                                                f"{what}, then try again. Nothing was changed.")


def _creds(body: dict) -> dict:
    return {"snow_url": body.get("snow_url") or body.get("instance_url") or snc.default_instance_url(),
            "snow_user": body.get("snow_user") or body.get("username"),
            "snow_pass": body.get("snow_pass") or body.get("password")}


# ════════════════════════════════════════════════════════════════════════════
# Endpoints
# ════════════════════════════════════════════════════════════════════════════

@snow_tools_router.get("/snow/tools")
async def list_tools(package: str = "", q: str = "", kind: str = ""):
    cat = await asyncio.to_thread(catalog)
    names = cat["packages"].get(package, []) if package else list(cat["tools"])
    ql = q.lower().strip()
    out = []
    for n in names:
        t = cat["tools"].get(n)
        if not t or (kind and t["kind"] != kind):
            continue
        if ql and ql not in n.lower() and ql not in t["description"].lower():
            continue
        out.append(_public(t))
    return {"count": len(out), "total": len(cat["tools"]), "tools": out}


@snow_tools_router.get("/snow/tools/packages")
async def list_packages():
    cat = await asyncio.to_thread(catalog)
    return {"total_tools": len(cat["tools"]), "enabled_package": cat["enabled_package"],
            "packages": [{"name": p, "tools": len(v),
                          "write_tools": sum(1 for n in v if tool_kind(n) == "write")}
                         for p, v in cat["packages"].items()]}


@snow_tools_router.get("/snow/tools/{name}")
async def get_tool(name: str):
    cat = await asyncio.to_thread(catalog)
    t = cat["tools"].get(name)
    if not t:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{name}'")
    return {**_public(t), "input_schema": schema_of(name)}


@snow_tools_router.post("/snow/tools/run")
async def run_tool(body: dict):
    name = (body.get("name") or "").strip()
    args = body.get("arguments") or {}
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if tool_kind(name) == "write" and body.get("confirm") is not True:
        raise HTTPException(status_code=428, detail=f"'{name}' changes data in ServiceNow — send confirm=true "
                                                    f"after the user approves it")
    bridged = (itam_bridge.is_itam_tool(name) and itam_bridge.has_confirm(name)) or \
              (itom_bridge.is_itom_tool(name) and itom_bridge.has_confirm(name))
    if bridged and body.get("confirm") is True and not args.get("dry_run"):
        args = {**args, "confirm": True}   # the user approved this exact change in the UI
    started = time.time()
    result = await asyncio.to_thread(run_tool_sync, _creds(body), name, args)
    ok = not (isinstance(result, dict) and (result.get("success") is False or result.get("ok") is False))
    return {"tool": name, "kind": tool_kind(name), "success": ok, "result": result,
            "duration_s": round(time.time() - started, 2)}


# ── plain English -> tool ─────────────────────────────────────────────────

_STOP = set("a an the to of for in on and or with my me i is are be please show give get all what which "
            "how many can you from by at as it this that".split())


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]


def shortlist(message: str, k: int = 14) -> List[dict]:
    cat = catalog()["tools"]
    ranked = tool_match.rank(message, [{"name": n, "description": t["description"], "_key": n}
                                       for n, t in cat.items()], k)
    return [cat[r["_key"]] for r in ranked]


def _compact_schema(name: str) -> dict:
    sch = schema_of(name)
    props = sch.get("properties") or {}
    req = set(sch.get("required") or [])
    out = {}
    for p, d in list(props.items())[:18]:
        typ = d.get("type") or ("enum" if "enum" in d else (d.get("anyOf") or [{}])[0].get("type", "any"))
        if typ in ("array", "object") and d.get("description"):   # give the AI the shape, not just the type
            typ = f"{typ}: {str(d['description'])[:120]}"
        out[p + ("*" if p in req else "")] = typ + (f" {d['enum'][:6]}" if d.get("enum") else "")
    return out


@snow_tools_router.post("/snow/tools/ask")
async def ask(body: dict):
    msg = (body.get("message") or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message is required")
    cands = await asyncio.to_thread(shortlist, msg)
    menu = [{"tool": c["name"], "does": c["description"][:180], "kind": c["kind"],
             "args": _compact_schema(c["name"])} for c in cands]
    prompt = (f"User request: {msg}\n\nPick the ONE ServiceNow tool that does exactly what is asked and fill its "
              f"arguments from the request (arguments marked * are required; omit ones you do not know; never invent "
              f"sys_ids). Never pick a tool for a different kind of record.\n"
              f"If a tool fits but the request does not give all required values, still pick it and list those "
              f"values in missing (do not return null for that). If none fits, return tool null. request_type is "
              f"'instance' when the user wants data from or a change in their ServiceNow instance, 'general' for "
              f"general questions, explanations or code.\n\n"
              f"Tools:\n{json.dumps(menu, indent=0)}\n\n"
              'Return JSON: {"tool": "<name or null>", "arguments": {...}, "missing": ["<required args not in the '
              'request>"], "request_type": "instance|general", "reason": "<one sentence>"}')
    try:
        pick = await groq_client.agenerate_json(prompt, system="You map requests to ServiceNow tool calls. JSON only.",
                                                max_tokens=900)
    except groq_client.GroqError as e:
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI returned an unreadable answer: {e}")
    name = (pick or {}).get("tool")
    if not name or name not in catalog()["tools"]:
        # request_type 'instance' + no tool: the UI says so instead of a general AI answer that could invent data
        return {"mode": "no_tool", "reason": (pick or {}).get("reason") or "No matching tool",
                "request_type": str((pick or {}).get("request_type") or "").lower() or "general",
                "candidates": [c["name"] for c in cands[:6]]}
    args = pick.get("arguments") or {}
    missing = [m.rstrip("*") for m in (pick.get("missing") or []) if m]
    kind = tool_kind(name)
    out = {"mode": "tool", "tool": name, "kind": kind, "arguments": args, "missing": missing,
           "reason": pick.get("reason", ""), "description": catalog()["tools"][name]["description"][:300]}
    if missing:
        out["needs_input"] = True
        return out
    if kind == "write":
        # Dry run first when the tool supports it: a missing plugin / role, an unknown record or a duplicate is
        # shown now, instead of asking the user to approve a change that cannot work.
        if "dry_run" in (schema_of(name).get("properties") or {}):
            try:
                preview = await asyncio.to_thread(run_tool_sync, _creds(body), name, {**args, "dry_run": True})
            except HTTPException as e:
                return _with_requirement(out, e)
            if isinstance(preview, dict) and preview.get("success") is False and not preview.get("dry_run") \
                    and not preview.get("requires_confirmation"):
                out["result"] = preview
                return out
            out["preview"] = preview
        out["needs_confirmation"] = True
        return out
    try:
        out["result"] = await asyncio.to_thread(run_tool_sync, _creds(body), name, args)
    except HTTPException as e:
        return _with_requirement(out, e)
    return out


def _with_requirement(out: dict, e: HTTPException) -> dict:
    out["error"] = e.detail
    # what the instance / user lacks: 424 plugin (any tool), 412 table layout differs, 403 role (ITAM / ITOM)
    req = {424: "plugin", 412: "incompatible", 403: "role"}.get(e.status_code)
    if req:
        out["requirement"] = req
        out["plugin_required"] = req == "plugin"
    return out
