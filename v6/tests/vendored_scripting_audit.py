"""
Offline audit of the vendored scripting tools (the AI Agent / Operations catalog), run through the agent's real tool
path (sn_tools.run_tool_sync) with every HTTP call answered by a recorder instead of ServiceNow.

For each create_* scripting tool it reports:
  registered   the tool is in the agent catalog
  table        the table it actually POSTs to
  unknown      fields it writes that the verified field registry (servicenow-mcp script_types, checked against
               sys_dictionary on a live instance) does not list for that table
  advanced     for business rules: whether a script is sent with advanced=true (ServiceNow ignores it otherwise)
  dup_check    whether it refuses to create when a record with the same name already exists
  read_back    whether it reads the created record back after the POST

    python vendored_scripting_audit.py           # prints a table and a JSON summary; exits 0 (it is an audit)

Nothing here proves a tool works on a real instance; it shows what the tool would send.
"""
import json
import os
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "snow-ai-service"))
_MCP_SRC = os.path.join(HERE, "..", "servicenow-mcp", "src", "servicenow_mcp", "tools")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests  # noqa: E402

import sn_tools  # noqa: E402

# The verified registry lives in servicenow-mcp's script_types.py. The agent vendors a different `servicenow_mcp`
# package, so load that one file by path instead of importing the package.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("verified_script_types", os.path.join(_MCP_SRC, "script_types.py"))
_types = importlib.util.module_from_spec(_spec)
sys.modules["verified_script_types"] = _spec.name and _types
_spec.loader.exec_module(_types)
VERIFIED = {t.table: set(t.fields) | set(t.lookup_fields) for t in _types.SCRIPT_TYPES.values()}
SYSTEM_FIELDS = {"sys_id", "sys_scope", "sys_class_name", "sys_package", "sys_policy"}

STUB_ID = "0" * 31 + "1"
CALLS: List[Dict[str, Any]] = []
MODE = {"existing": False}
STORED: Dict[str, dict] = {}


def _response(status: int, data: Any, url: str) -> requests.Response:
    r = requests.Response()
    r.status_code, r.url = status, url
    r._content = json.dumps(data).encode()
    r.headers["Content-Type"] = "application/json"
    return r


def fake_request(self, method, url, params=None, data=None, headers=None, json_=None, **kw):
    body = kw.get("json") if json_ is None else json_
    if body is None and isinstance(data, (str, bytes)):
        try:
            body = json.loads(data)
        except ValueError:
            body = None
    CALLS.append({"method": method.upper(), "url": url, "params": params or {}, "body": body})
    table = url.split("/api/now/table/")[-1].split("?")[0] if "/api/now/table/" in url else ""
    if method.upper() == "GET":
        if "/" in table:          # GET by sys_id: what the last POST to that table stored
            stored = STORED.get(table.split("/")[0]) or {"name": "Existing"}
            return _response(200, {"result": {**stored, "sys_id": STUB_ID}}, url)
        rows = [{"sys_id": STUB_ID, "name": "Audit rule", "short_description": "Audit rule"}] if MODE["existing"] else []
        return _response(200, {"result": rows}, url)
    if method.upper() in ("POST", "PATCH", "PUT"):
        STORED[table.split("/")[0]] = dict(body or {})
        return _response(201, {"result": {**(body or {}), "sys_id": STUB_ID}}, url)
    return _response(204, {}, url)


requests.Session.request = fake_request

ARGS = {   # minimal, valid-looking arguments for each create tool
    "create_business_rule": {"name": "Audit rule", "table": "incident", "when": "before", "insert": True,
                             "script": "current.urgency = 1;"},
    "create_script_include": {"name": "AuditUtils", "script": "var AuditUtils = Class.create();"},
    "create_ui_policy": {"short_description": "Audit rule", "table": "incident"},
    "create_ui_policy_action": {"ui_policy": STUB_ID, "field": "urgency", "mandatory": True},
    "create_data_policy": {"short_description": "Audit rule", "table": "incident"},
    "create_data_policy_rule": {"data_policy": STUB_ID, "field": "urgency", "mandatory": True},
    "create_ui_action": {"name": "Audit rule", "table": "incident", "script": "current.update();"},
    "create_fix_script": {"name": "Audit rule", "script": "gs.info('x');"},
    "create_catalog_client_script": {"name": "Audit rule", "type": "onLoad", "script": "function onLoad(){}",
                                     "cat_item": STUB_ID},
    "create_server_side_script": {"script_type": "business_rule", "name": "Audit rule",
                                  "script": "current.urgency = 1;", "fields": {"collection": "incident"}},
}


def _fill_required(name: str, args: dict) -> dict:
    schema = sn_tools.schema_of(name)
    out = dict(args)
    for req in schema.get("required") or []:
        if req not in out:
            prop = (schema.get("properties") or {}).get(req) or {}
            typ = prop.get("type") or (prop.get("anyOf") or [{}])[0].get("type")
            out[req] = True if typ == "boolean" else 1 if typ == "integer" else STUB_ID if "id" in req else "Audit rule"
    return out


def run(name: str, args: dict, existing: bool) -> Dict[str, Any]:
    CALLS.clear()
    MODE["existing"] = existing
    creds = {"snow_url": "https://audit.service-now.com", "snow_user": "audit", "snow_pass": "audit"}
    try:
        result = sn_tools.run_tool_sync(creds, name, args)
        err = ""
    except Exception as e:  # HTTPException or tool error
        result, err = None, str(getattr(e, "detail", e))[:200]
    posts = [c for c in CALLS if c["method"] == "POST" and "/api/now/table/" in c["url"]]
    return {"result": result, "error": err, "posts": posts, "calls": list(CALLS)}


def audit_create(name: str) -> Dict[str, Any]:
    cat = sn_tools.catalog()["tools"]
    row: Dict[str, Any] = {"tool": name, "registered": name in cat}
    if not row["registered"]:
        return row
    args = _fill_required(name, ARGS.get(name, {"name": "Audit rule"}))
    fresh = run(name, args, existing=False)
    if not fresh["posts"]:
        row.update(table="", note=f"no POST made ({fresh['error'] or 'returned without writing'})")
        return row
    post = fresh["posts"][-1]
    table = post["url"].split("/api/now/table/")[-1].split("?")[0]
    body = post["body"] or {}
    known = VERIFIED.get(table)
    row["table"] = table
    row["fields_written"] = sorted(body)
    row["unknown"] = sorted(set(body) - known - SYSTEM_FIELDS) if known is not None else "table not in registry"
    if table == "sys_script":
        row["advanced"] = str(body.get("advanced")).lower() == "true" if body.get("script") else "no script"
    after = fresh["calls"][fresh["calls"].index(post) + 1:]
    row["read_back"] = any(c["method"] == "GET" and STUB_ID in c["url"] for c in after)
    row["verified"] = ((fresh["result"] or {}).get("verification") or {}).get("verified") \
        if isinstance(fresh["result"], dict) else None
    row["body"] = body
    dup = run(name, args, existing=True)
    row["dup_check"] = not dup["posts"]
    return row


PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f"  — {str(detail)[:300]}" if detail and not cond else ""))


def main():
    cat = sn_tools.catalog()["tools"]
    creates = [n for n in ARGS]
    rows = [audit_create(n) for n in creates]
    by = {r["tool"]: r for r in rows}
    print(f"Vendored catalog: {len(cat)} tools\n")
    print(f"{'tool':32} {'table':26} {'adv':5} {'dup':5} {'read':5} unknown fields")
    for r in rows:
        if not r["registered"]:
            print(f"{r['tool']:32} NOT REGISTERED")
            continue
        print(f"{r['tool']:32} {r.get('table') or '-':26} {str(r.get('advanced', '')):5} {str(r.get('dup_check', '')):5} "
              f"{str(r.get('read_back', '')):5} {r.get('unknown', '') or r.get('note', '')}")
    gaps = {
        "client_script (sys_script_client) create/list": any("sys_script_client" == r.get("table") for r in rows)
        or any(n in cat for n in ("create_client_script", "list_client_scripts")),
        "assignment rule (sysrule_assignment) create/list": any("assignment_rule" in n for n in cat),
        "list_ui_policies": "list_ui_policies" in cat,
        "list_data_policies": "list_data_policies" in cat,
    }
    print("\nCoverage gaps (False = no vendored tool):")
    for k, v in gaps.items():
        print(f"  {'yes' if v else 'NO '}  {k}")
    print()

    # ── regression checks for the fixed write paths ──
    bad = run("create_server_side_script", {"script_type": "business_rule", "name": "Audit rule", "table": "incident",
                                            "script": "current.urgency = 1;"}, existing=False)
    check("create_server_side_script with table=incident creates nothing (not an incident, not a rule)",
          not bad["posts"], [p["url"] for p in bad["posts"]])
    bad2 = run("create_server_side_script", {"script_type": "made_up", "name": "x", "script": "x"}, existing=False)
    check("create_server_side_script with an unknown script_type creates nothing", not bad2["posts"])
    bad3 = run("create_server_side_script", {"script_type": "business_rule", "name": "Audit rule",
                                             "script": "current.urgency = 1;"}, existing=False)
    check("create_server_side_script business rule without fields.collection creates nothing", not bad3["posts"])
    ss = by.get("create_server_side_script", {})
    check("create_server_side_script business rule goes to sys_script with collection and advanced=true",
          ss.get("table") == "sys_script" and (ss.get("body") or {}).get("collection") == "incident"
          and ss.get("advanced") is True, ss)
    br = by.get("create_business_rule", {})
    check("create_business_rule with a script sets advanced=true", br.get("advanced") is True, br.get("body"))
    check("create_business_rule sends no sys_class_name / action_display",
          not {"sys_class_name", "action_display"} & set(br.get("body") or {}), br.get("fields_written"))
    never = run("create_business_rule", {"name": "Never runs", "table": "incident", "script": "x"}, existing=False)
    check("create_business_rule that would never run is refused", not never["posts"])
    for tool in list(sn_tools.SCRIPT_WRITE_GUARDS) + ["create_server_side_script"]:
        r = by.get(tool)
        if not r:
            continue
        check(f"{tool}: refuses a duplicate, reads the new record back and verifies it",
              r.get("dup_check") is True and r.get("read_back") is True and r.get("verified") is True,
              {k: r.get(k) for k in ("dup_check", "read_back", "verified", "note")})
    check("scripting writes still need confirm=true through /snow/tools/run",
          all(sn_tools.tool_kind(n) == "write" for n in ARGS))
    bg = run("execute_background_script", {"script": "gs.info(1)", "allow_execution": True}, existing=False)
    check("execute_background_script is disabled by default (not in the catalog, refused, nothing sent)",
          "execute_background_script" not in cat and "disabled" in bg["error"]
          and not any("sys.scripts.do" in c["url"] for c in bg["calls"]), bg["error"])
    import snow_tools_router
    from servicenow_mcp.tools import business_rule_tools
    try:
        snow_tools_router._run(business_rule_tools.create_business_rule, None, None, None)
        raw = "allowed"
    except Exception as e:
        raw = str(getattr(e, "status_code", e))
    check("raw /sn/tools scripting write routes refuse (403) instead of bypassing confirmation", raw == "403", raw)
    try:
        snow_tools_router._run(business_rule_tools.list_business_rules, None, None, None)
        raw_read = "allowed"
    except Exception as e:
        raw_read = "blocked" if getattr(e, "status_code", None) == 403 else "ran (error from the fake config)"
    check("raw /sn/tools read routes are not blocked", raw_read != "blocked", raw_read)
    print(f"==== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ====")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
