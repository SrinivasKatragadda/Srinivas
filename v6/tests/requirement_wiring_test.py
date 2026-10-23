"""
Wiring of the requirement pipeline into the agent (in-process; FakeInstance instead of ServiceNow, simulated AI):

  * REST: /snow/requirements/plan (read-only), /snow/requirements/deploy (428 without confirm=true), history
  * Implement screen path: /snow/stories/approve-and-deploy with pipeline=true plans configuration stories (no
    configuration written), deploy adds a work note to the story's tracking record
  * MCP: snow_plan_requirement (read-only) and snow_implement_requirement (dry run / confirm), through the real
    MCP Chat bridge (_mcp_chat_call): a write is a proposal; confirm=true reaches the tool only on confirmation
  * Read-only fast path: a look-up never runs the planner; the planner tool itself answers look-ups without AI
  * UI: the report renderer shows every section and a confirm button (Chromium, when Playwright is installed)

    python requirement_wiring_test.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = tempfile.mkdtemp(prefix="reqwire_")
os.environ.update(SNOW_VERIFY_TLS="false", AGENT_JOBS_DIR=STORE, MCP_API_KEY="")
sys.path.insert(0, os.path.join(HERE, "..", "snow-ai-service"))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import groq_client  # noqa: E402
import requirement_pipeline as rp  # noqa: E402
import sn_client as snc  # noqa: E402
from fake_instance import FakeInstance  # noqa: E402

PASSED, FAILED, AI_CALLS = [], [], []
AI = {}


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f"  — {str(detail)[:400]}" if detail and not cond else ""))


def fake_generate_json(prompt, system="", max_tokens=6000, temperature=0.2):
    AI_CALLS.append(prompt)
    for marker, answer in AI.items():
        if marker in prompt:
            return answer
    raise groq_client.GroqError("no simulated answer")


groq_client.generate_json = fake_generate_json
EXTRACTED = {"summary": "Urgency is mandatory on the incident form", "table": "incident",
             "request_kind": "configuration", "acceptance_criteria": ["Urgency cannot be left empty on the form"],
             "effects": [{"field": "urgency", "effect": "mandatory", "value": "true", "side": "client"}]}
AI["Request / user story"] = EXTRACTED

INSTANCE = {"sn": None}


def use_instance(sn):
    rp.clear_caches()
    INSTANCE["sn"] = sn
    snc.client_from = lambda body, **kw: INSTANCE["sn"]     # every endpoint builds its client through this


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import snow_endpoints  # noqa: E402
import snow_universal  # noqa: E402

app = FastAPI()
app.include_router(snow_endpoints.snow_router)
app.include_router(snow_universal.snow_universal_router)
client = TestClient(app)
CREDS = {"snow_url": "https://dev-a.service-now.com", "snow_user": "admin", "snow_pass": "pw"}


def t_rest():
    sn = FakeInstance()
    use_instance(sn)
    r = client.post("/snow/requirements/plan", json={**CREDS, "text": "Urgency must be mandatory on the incident form"})
    plan = r.json()
    check("REST plan: 200, awaiting confirmation, report present, nothing written",
          r.status_code == 200 and plan["status"] == "awaiting_confirmation" and plan["report"] and not sn.writes(),
          (r.status_code, plan.get("status")))
    r2 = client.post("/snow/requirements/deploy", json={**CREDS, "plan_id": plan["plan_id"]})
    check("REST deploy without confirm=true is refused with 428", r2.status_code == 428 and not sn.writes())
    r3 = client.post("/snow/requirements/deploy", json={**CREDS, "plan_id": plan["plan_id"], "confirm": True})
    check("REST deploy with confirm=true deploys and verifies", r3.status_code == 200 and r3.json()["status"] ==
          "deployed" and sn.rows("sys_ui_policy"), r3.json().get("status"))
    r4 = client.post("/snow/requirements/deploy", json={**CREDS, "plan_id": "0123456789abcdef", "confirm": True})
    check("REST deploy of an unknown plan is 404", r4.status_code == 404)
    h = client.post("/snow/requirements/history", json=CREDS).json()
    check("REST history lists the requirement for this instance", any(x["plan_id"] == plan["plan_id"]
                                                                      for x in h["history"]))
    r5 = client.post("/snow/requirements/plan", json={**CREDS})
    check("REST plan without text is 400", r5.status_code == 400)


def t_implement_screen():
    sn = FakeInstance()
    use_instance(sn)
    story = {"title": "Mandatory urgency", "as_a": "service desk agent", "i_want": "urgency to be mandatory on the "
             "incident form", "so_that": "triage is correct", "acceptance_criteria": ["Urgency cannot be empty"],
             "priority": "medium", "module": "ITSM", "story_type": "story"}
    r = client.post("/snow/stories/approve-and-deploy", json={**CREDS, "approved": True, "pipeline": True,
                                                              "user_stories": [story]})
    d = r.json()
    item = (d.get("stories_created") or [{}])[0]
    plan = item.get("requirement_plan") or {}
    check("Implement: story tracking record created and requirement planned (not built)",
          r.status_code == 200 and item.get("table") == "task" and plan.get("status") == "awaiting_confirmation"
          and d["summary"]["requirements_planned"] == 1, (r.status_code, item.get("table"), plan.get("status")))
    check("Implement: no configuration written before confirmation", not sn.writes())
    r2 = client.post("/snow/requirements/deploy", json={**CREDS, "plan_id": plan["plan_id"], "confirm": True})
    task = sn.rows("task", sys_id=item["sys_id"])
    check("Implement: confirmed deploy builds it and adds a work note to the story record",
          r2.json()["status"] == "deployed" and task and "Requirement" in str(task[0].get("work_notes")),
          (r2.json().get("status"), task and task[0].get("work_notes")))
    sn2 = FakeInstance()
    use_instance(sn2)
    r3 = client.post("/snow/stories/approve-and-deploy", json={**CREDS, "approved": True, "user_stories": [
        {"title": "Laptop refresh", "i_want": "a new laptop", "story_type": "story"}]})
    check("Implement: without pipeline=true a story gets no requirement plan (existing behaviour kept)",
          r3.status_code == 200 and not r3.json()["stories_created"][0].get("requirement_plan"))


class _StubSession:
    """Stands in for the Streamable HTTP session: lists and calls the real FastMCP tools in-process."""
    calls = []

    def __init__(self, creds=None):
        import snow_mcp
        self.mcp = snow_mcp.create_server()

    def list_tools(self):
        async def go():
            return [t.to_mcp_tool().model_dump(exclude_none=True) for t in await self.mcp.list_tools()]
        return asyncio.run(go())

    def call(self, name, arguments):
        _StubSession.calls.append((name, dict(arguments)))
        tool = asyncio.run(self.mcp.get_tool(name))
        return json.loads(json.dumps(tool.fn(**arguments), default=str))


def t_planner_preflight():
    blocked = FakeInstance()
    use_instance(blocked)
    snow_endpoints._McpSession = _StubSession
    report = client.post("/snow/planner/preflight", json=CREDS).json()
    check("Planner preflight reports release, identity, scope, Update Set and MCP readiness",
          report["release"]["family"] == "Xanadu" and report["identity"]["username"] == "admin"
          and report["mcp"]["available"] and not report["execution_ready"] and report["blockers"]
          and not blocked.writes(), {"ready": report.get("execution_ready"), "checks": report.get("checks")})

    ready = FakeInstance()
    ready.add("sys_properties", name="glide.buildtag", value="glide-xanadu-01-01-2026__patch0-01-02-2026")
    ready.add("sys_properties", name="glide.installation.production", value="false")
    ready.add("sys_user_preference", user="6816f79cc0a8016401c5a33be04be441", name="apps.current_app",
              value="global")
    ready.add("sys_user_preference", user="6816f79cc0a8016401c5a33be04be441", name="sys_update_set",
              value="default_global")
    use_instance(ready)
    report = client.post("/snow/planner/preflight", json=CREDS).json()
    check("Planner preflight becomes ready only when release, non-prod, scope, Update Set, identity and MCP verify",
          report["execution_ready"] and report["environment"]["type"] == "non-production"
          and report["release"]["patch_level"] == "0" and report["update_set"]["name"] == "Default"
          and not ready.writes(), report.get("blockers"))

    blocked = FakeInstance()
    use_instance(blocked)
    original = snow_endpoints._planner_preflight_report
    snow_endpoints._planner_preflight_report = lambda *a: {"execution_ready": False,
        "blockers": ["release/environment/scope not verified"]}
    story = {"title": "Mandatory urgency", "i_want": "urgency to be mandatory", "story_type": "story",
             "acceptance_criteria": ["Urgency is mandatory on incident forms"]}
    missing_ac = client.post("/snow/stories/approve-and-deploy", json={**CREDS, "approved": True, "pipeline": True,
        "require_preflight": True, "user_stories": [{"title": "No criteria", "i_want":
        "make the incident description mandatory"}]})
    check("Strict Planner rejects missing acceptance criteria before connecting or writing",
          missing_ac.status_code == 422 and not blocked.writes(), missing_ac.status_code)
    r = client.post("/snow/stories/approve-and-deploy", json={**CREDS, "approved": True, "pipeline": True,
                                                               "require_preflight": True,
                                                               "user_stories": [story]})
    check("Strict story processing blocks before creating its tracking record", r.status_code == 412
          and not blocked.writes(), r.status_code)

    plan = rp.plan_requirement(blocked, requirement=EXTRACTED)
    plan["require_preflight"] = True
    rp.save_plan(blocked.instance, plan)
    r = client.post("/snow/requirements/deploy", json={**CREDS, "plan_id": plan["plan_id"], "confirm": True})
    check("Strict REST deployment blocks before any write when readiness is lost", r.status_code == 412
          and not blocked.writes(), r.status_code)

    try:
        snow_endpoints._mcp_chat_call("", CREDS, confirm={"tool": "snow_implement_requirement",
            "arguments": {"plan_id": plan["plan_id"], "require_preflight": True}})
        mcp_blocked = False
    except Exception as exc:
        mcp_blocked = getattr(exc, "status_code", None) == 412
    check("Strict MCP deployment blocks before invoking the write tool", mcp_blocked and not blocked.writes())
    snow_endpoints._planner_preflight_report = original


def t_mcp():
    import snow_mcp
    sn = FakeInstance()
    use_instance(sn)
    snow_mcp._get_creds = lambda: (CREDS["snow_url"], "admin", "pw")
    snow_mcp.snc.SNClient = lambda url, user, pw: INSTANCE["sn"]
    snow_endpoints._McpSession = _StubSession
    msg = "Make urgency mandatory on the incident form"
    AI["User request: " + msg] = {"tool": "snow_plan_requirement", "arguments": {"request": msg}, "missing": [],
                                  "request_type": "instance", "reason": "configuration requirement"}
    out = snow_endpoints._mcp_chat_call(msg, CREDS)
    res = out.get("result") or {}
    check("MCP Chat: planning tool is a read tool, runs at once and returns the report",
          out["mode"] == "tool_result" and out["kind"] == "read" and res.get("report") and res.get("plan_id")
          and not sn.writes(), (out.get("mode"), out.get("kind")))
    pid = res["plan_id"]
    AI["User request: implement plan " + pid] = {"tool": "snow_implement_requirement", "arguments": {"plan_id": pid},
                                                 "missing": [], "request_type": "instance", "reason": "implement"}
    _StubSession.calls.clear()
    prop = snow_endpoints._mcp_chat_call("implement plan " + pid, CREDS)
    check("MCP Chat: implementing is a write → dry-run preview then a proposal, nothing written",
          prop["mode"] == "proposal" and prop["preview"].get("dry_run") and not sn.writes()
          and all(not a.get("confirm") for _, a in _StubSession.calls), (prop.get("mode"), _StubSession.calls))
    done = snow_endpoints._mcp_chat_call("", CREDS, confirm={"tool": "snow_implement_requirement",
                                                            "arguments": {"plan_id": pid}})
    check("MCP Chat: after confirmation the tool gets confirm=true, deploys and verifies",
          done["arguments"].get("confirm") is True and done["result"].get("status") == "deployed"
          and done["result"]["report"]["verification"]["persisted_config"]["failed"] == 0 and sn.rows("sys_ui_policy"),
          done.get("result", {}).get("status"))
    # read-only fast path
    AI_CALLS.clear()
    AI["User request: list my open incidents"] = {"tool": "snow_list_incidents", "arguments": {}, "missing": [],
                                                 "request_type": "instance", "reason": "look-up"}
    _StubSession.calls.clear()
    snow_mcp.snow_get = lambda *a, **k: []
    look = snow_endpoints._mcp_chat_call("list my open incidents", CREDS)
    check("MCP Chat: a look-up runs its read tool; the planner is not called",
          look["mode"] == "tool_result" and look["tool"] == "snow_list_incidents"
          and not any(n.startswith("snow_plan") for n, _ in _StubSession.calls), [n for n, _ in _StubSession.calls])
    tool = asyncio.run(_StubSession().mcp.get_tool("snow_plan_requirement")).fn
    AI_CALLS.clear()
    calls_before = sn.calls
    ro = tool(request="show me incident INC0010001")
    check("Planner tool answers a look-up on the fast path (no AI, no instance call)",
          ro["status"] == "read_only" and not AI_CALLS and sn.calls == calls_before, (ro["status"], len(AI_CALLS)))


def t_generate_scripts():
    """snow_plan_requirement(generate_scripts=...) — false: questions instead of scripts; true: current behaviour."""
    import snow_mcp
    story = ("As a service desk agent, I want incident descriptions to be mandatory and network incidents to display "
             "appropriate troubleshooting guidance")
    AI["Request / user story"] = {
        "summary": "Mandatory description and troubleshooting guidance for network incidents", "table": "incident",
        "request_kind": "configuration",
        "effects": [{"field": "description", "effect": "mandatory", "value": "true", "side": "client"}],
        "logic": [{"description": "Display troubleshooting guidance on the form for network incidents",
                   "side": "client", "client_type": "onChange", "field": "category"}]}
    AI["Write the client_script"] = {"script": "if (newValue == 'network') g_form.addInfoMessage('Restart the router');",
                                     "filter_condition": "", "description": "guidance"}
    tool = asyncio.run(_StubSession().mcp.get_tool("snow_plan_requirement")).fn
    snow_mcp._get_creds = lambda: (CREDS["snow_url"], "admin", "pw")
    for flag in (False, True):
        sn = FakeInstance()
        use_instance(sn)
        snow_mcp.snc.SNClient = lambda url, user, pw: INSTANCE["sn"]
        AI_CALLS.clear()
        out = tool(request=story, generate_scripts=flag) if not flag else tool(request=story)
        rep = out["report"]
        script_prompts = [p for p in AI_CALLS if "Write the client_script" in p]
        logic_items = [i for k in ("to_create", "needs_decision", "blocked") for i in rep["implementation"][k]
                       if i["mechanism"] in ("client_script", "business_rule", "script")]
        if not flag:
            qs = rep["requirement"]["missing_information"]
            check("generate_scripts=false: no script prompt is sent to the AI and no script item is planned",
                  not script_prompts and not logic_items, (len(script_prompts), logic_items))
            check("generate_scripts=false: the guidance becomes an explicit question asking for the exact text",
                  out["status"] == "needs_input" and any("exact text" in q and "Nothing was written or invented" in q
                                                         for q in qs), qs)
            check("generate_scripts=false: the declarative part is still analysed (description mandatory)",
                  any(i["mechanism"] == "ui_policy" for i in rep["implementation"]["to_create"]))
            check("generate_scripts=false: nothing written to the instance", not sn.writes())
        else:
            check("generate_scripts default (true): current behaviour — the script is written and planned",
                  len(script_prompts) == 1 and any(i["mechanism"] == "client_script" for i in logic_items)
                  and out["status"] == "awaiting_confirmation", (len(script_prompts), out["status"]))
    AI["Request / user story"] = EXTRACTED


def t_ui():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP UI report rendering (Playwright not installed)")
        return
    sn = FakeInstance()
    use_instance(sn)
    pl = rp.plan_requirement(sn, requirement={"summary": "Urgency on form", "table": "incident", "effects": [
        {"field": "urgency", "effect": "mandatory", "value": "true", "side": "client"},
        {"field": "assignment_group", "effect": "assign", "value": "Nobody Team"}]})
    p2 = rp.plan_requirement(sn, requirement={"summary": "Urgency on form", "table": "incident", "effects": [
        {"field": "urgency", "effect": "mandatory", "value": "true", "side": "client"}]})
    d2 = rp.deploy_plan(sn, p2["plan_id"], confirm=True)
    html_path = os.path.join(HERE, "..", "snow-ui", "index.html")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("file:///" + os.path.abspath(html_path).replace("\\", "/"))
        pg.wait_for_timeout(800)
        h1 = pg.evaluate("(p)=>renderReqReport(p,'impl')", json.loads(json.dumps(pl, default=str)))
        h2 = pg.evaluate("(p)=>renderReqReport(p,'mcp')", json.loads(json.dumps(d2, default=str)))
        b.close()
    check("UI: needs-input report shows the question and no confirm button",
          "Needs your input" in h1 and "Nobody Team" in h1 and "data-req-deploy" not in h1, h1[:300])
    check("UI: deployed report shows every section, read-back result and runtime status",
          all(s in h2 for s in ("Requirement understood", "Configuration inspection", "Implementation", "Deployment",
                                "Verification", "Outcome", "passed, 0 failed", "Runtime test")), h2[:300])
    check("UI: no JavaScript errors while rendering reports", not errs, errs)
    p3 = rp.plan_requirement(sn, requirement={"summary": "Impact", "table": "incident", "effects": [
        {"field": "impact", "effect": "mandatory", "value": "true", "side": "client"}]})
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        pg = b.new_page()
        pg.goto("file:///" + os.path.abspath(html_path).replace("\\", "/"))
        pg.wait_for_timeout(500)
        h3 = pg.evaluate("(p)=>renderReqReport(p,'impl')", json.loads(json.dumps(p3, default=str)))
        h4 = pg.evaluate("(p)=>{S.reqPlans[p.plan_id]=p;return renderChatBubble({role:'assistant',reqPlanId:p.plan_id,"
                         "reqCtx:'impl'});}", json.loads(json.dumps(p3, default=str)))
        b.close()
    check("UI: an awaiting-confirmation plan has the confirm button", "data-req-deploy" in h3 and "Confirm" in h3)
    check("UI: AI Agent chat shows a story's plan with the REST confirm path (same flow as Implement)",
          'data-req-ctx="impl"' in h4 and "Requirement understood" in h4)
    src = open(html_path, encoding="utf-8").read()
    check("UI: every approve-and-deploy call from the UI plans through the pipeline",
          src.count("/snow/stories/approve-and-deploy") == src.count("approved:true,pipeline:true"), src.count(
              "/snow/stories/approve-and-deploy"))


for fn in (t_rest, t_planner_preflight, t_implement_screen, t_mcp, t_generate_scripts, t_ui):
    try:
        fn()
    except Exception as e:
        traceback.print_exc()
        check(f"{fn.__name__} raised", False, e)

shutil.rmtree(STORE, ignore_errors=True)
print(f"==== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ====")
sys.exit(1 if FAILED else 0)
