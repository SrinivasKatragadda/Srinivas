"""
Regression checks for three scripting bugs (no ServiceNow, no AI — both are patched in-process):

  * snow_generate_business_rule deploys with advanced=true (otherwise ServiceNow ignores the script), refuses a
    duplicate name on the same table, refuses a script with a syntax error, and reads the stored rule back
  * sn_build writes an onChange client script's field to sys_script_client.field (not 'field_name')
  * the UI has one renderBuild (Build App screen) and a separate renderStoryBuild (story build results)

    python scripting_fixes_test.py
"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.update(SNOW_VERIFY_TLS="false", AGENT_JOBS_DIR=os.path.join(HERE, "jobs"))
sys.path.insert(0, os.path.join(HERE, "..", "snow-ai-service"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import groq_client  # noqa: E402
import sn_build  # noqa: E402
import snow_mcp  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f"  — {detail}" if detail and not cond else ""))


# ── in-memory sys_script used by the patched snow_get / snow_post ───────────────────────────────
STORE = {}


def fake_get(url, user, pwd, table, params=None):
    q = (params or {}).get("sysparm_query", "")
    conds = dict(c.split("=", 1) for c in q.split("^") if "=" in c)
    return [dict(r) for r in STORE.get(table, []) if all(str(r.get(k)) == v for k, v in conds.items())]


def fake_post(url, user, pwd, table, data, update_set_name=None):
    rec = {**data, "sys_id": f"{len(STORE.get(table, [])) + 1:032x}"}
    STORE.setdefault(table, []).append(rec)
    return rec


snow_mcp.snow_get, snow_mcp.snow_post = fake_get, fake_post
snow_mcp._get_creds = lambda: ("https://example.service-now.com", "u", "p")
AI_SCRIPT = {"text": "```javascript\nif (!current.short_description.nil()) current.urgency = 1;\n```"}
groq_client.generate = lambda *a, **k: AI_SCRIPT["text"]


def tool():
    return asyncio.run(snow_mcp.create_server().get_tool("snow_generate_business_rule")).fn


def t_generate_business_rule():
    fn = tool()
    r = fn(description="set urgency", table_name="incident", when="before", action="insert,update",
           rule_name="Set urgency", deploy=True)
    rec = (STORE.get("sys_script") or [{}])[0]
    check("Generated rule is deployed with advanced=true", rec.get("advanced") == "true", rec)
    check("Insert and update actions both set from 'insert,update'",
          rec.get("action_insert") == "true" and rec.get("action_update") == "true" and rec.get("action_delete") == "false")
    check("Markdown fence stripped and script wrapped in executeRule",
          "```" not in rec.get("script", "") and "executeRule" in rec.get("script", ""))
    check("Stored rule read back and verified", r.get("deployed") is True and r.get("verified") is True, r)

    r2 = fn(description="set urgency", table_name="incident", when="before", action="insert",
            rule_name="Set urgency", deploy=True)
    check("Duplicate name on the same table is refused (no second record)",
          len(STORE["sys_script"]) == 1 and r2.get("deployed") is False and r2.get("existing_sys_id"), r2)

    r3 = fn(description="x", table_name="incident", when="sometimes", action="insert", deploy=True)
    check("Invalid 'when' is rejected", r3.get("success") is False)
    r4 = fn(description="x", table_name="incident", when="before", action="nothing", deploy=True)
    check("Action that never runs is rejected", r4.get("success") is False)

    if sn_build.js_syntax_error("var x = ;"):   # only meaningful when node is available
        AI_SCRIPT["text"] = "current.urgency = ;"
        r5 = fn(description="x", table_name="incident", when="before", action="insert", rule_name="Broken",
                deploy=True)
        check("Script with a syntax error is not deployed",
              r5.get("deployed") is False and "syntax" in str(r5.get("deploy_error")) and len(STORE["sys_script"]) == 1, r5)
    else:
        print("SKIP syntax-error refusal (node is not installed, so syntax is only bracket-checked)")


class _FakeSN:
    def __init__(self):
        self.created = []

    def table_sys_id(self, t):
        return "t" * 32

    def get_one(self, table, query, fields=""):
        return None

    def create(self, table, payload, fields=""):
        self.created.append((table, payload))
        return {"sys_id": f"{len(self.created):032x}"}


def t_client_script_field():
    plan = sn_build.normalize_plan({"app_name": "x", "scope_mode": "global", "client_scripts": [
        {"name": "Watch level", "table": "incident", "type": "onChange", "field": "urgency",
         "script": "g_form.addInfoMessage('changed');"}]})
    sn = _FakeSN()
    b = sn_build.Builder(sn, plan, {"stop_on_error": True}, lambda *a: None)
    b.step_client_scripts()
    payload = next((p for t, p in sn.created if t == "sys_script_client"), {})
    check("onChange client script writes sys_script_client.field", payload.get("field") == "urgency", payload)
    check("onChange client script does not write the non-existent 'field_name'", "field_name" not in payload)


def t_ui_render_functions():
    html = open(os.path.join(HERE, "..", "snow-ui", "index.html"), encoding="utf-8").read()
    names = re.findall(r"function\s+(renderBuild|renderStoryBuild)\s*\(", html)
    check("UI defines renderBuild exactly once", names.count("renderBuild") == 1, names)
    check("UI defines renderStoryBuild exactly once", names.count("renderStoryBuild") == 1, names)
    check("No story-result call still uses renderBuild(<arg>)", not re.search(r"renderBuild\(\s*[a-z]", html))


for fn in (t_generate_business_rule, t_client_script_field, t_ui_render_functions):
    try:
        fn()
    except Exception as e:  # report and keep going
        import traceback
        traceback.print_exc()
        check(f"{fn.__name__} raised", False, str(e))

print(f"==== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ====")
sys.exit(1 if FAILED else 0)
