"""
AI Agent chat: configuration artifacts (Script Include, Client Script, Business Rule) are planned and built through the
requirement pipeline — never routed to Incident creation. In-memory instances (fake_instance.py) and a simulated AI;
the browser part stubs `post`, so nothing leaves the page. NOT a real instance.

    python ai_agent_artifact_test.py
"""
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = tempfile.mkdtemp(prefix="artifact_")
os.environ.update(SNOW_VERIFY_TLS="false", AGENT_JOBS_DIR=STORE)
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
            return answer(prompt) if callable(answer) else answer
    raise groq_client.GroqError("no simulated answer")


groq_client.generate_json = fake_generate_json

GOOD_SI = ("var IncidentUtils = Class.create();\nIncidentUtils.prototype = {\n    initialize: function() {},\n"
           "    isP1: function(incidentId) {\n        var gr = new GlideRecord('incident');\n"
           "        return gr.get(incidentId) && gr.getValue('priority') == '1';\n    },\n    type: 'IncidentUtils'\n};")
AJAX_SI = ("var UserGroupUtils = Class.create();\nUserGroupUtils.prototype = Object.extendsObject(AbstractAjaxProcessor, {\n"
           "    isMember: function() {\n        var gr = new GlideRecord('sys_user_grmember');\n"
           "        gr.addQuery('user', this.getParameter('sysparm_user'));\n"
           "        gr.addQuery('group.name', this.getParameter('sysparm_group'));\n        gr.query();\n"
           "        return gr.hasNext() ? 'true' : 'false';\n    },\n    type: 'UserGroupUtils'\n});")


def extraction(**over):
    """What the AI typically returns — deliberately imperfect (no class name, an 'incident' table)."""
    base = {"summary": "Script Include for incident priority checks", "table": "incident",
            "request_kind": "configuration", "effects": [],
            "logic": [{"description": "Reusable function that tells whether an incident is P1", "side": "server"}]}
    base.update(over)
    return base


def plan(text, sn, **extr):
    AI["Request / user story"] = extraction(**extr)
    rp.clear_caches()
    return rp.plan_requirement(sn, text=text)


def items(p, action=None):
    return [i for i in p["items"] if action is None or i["action"] == action]


# ════════════════════════════════════════════════════════════════════════════
def t_script_include_global():
    sn = FakeInstance()
    AI["Write the script_include"] = {"script": GOOD_SI, "description": "Incident helpers"}
    text = ("Create a script include called IncidentUtils with a method isP1 that returns true when the incident "
            "has priority 1 — it must not raise an incident or error")
    p = plan(text, sn)
    it = items(p, "create")
    check("SI: plan creates a script_include named exactly as written (AI gave no class name)",
          p["status"] == "awaiting_confirmation" and len(it) == 1 and it[0]["mechanism"] == "script_include"
          and it[0]["spec"]["name"] == "IncidentUtils", [(i["action"], i["mechanism"], i.get("name")) for i in p["items"]])
    check("SI: not client callable by default, recorded as an assumption; global scope recorded",
          it and it[0]["spec"]["client_callable"] is False
          and any("not client callable" in a for a in p["requirement"]["assumptions"])
          and any("global application scope" in a for a in p["requirement"]["assumptions"]))
    check("SI: the plan report says what will be built (class, callable)",
          "class IncidentUtils" in p["report"]["implementation"]["to_create"][0]["trigger"])
    check("SI: nothing written while planning", not sn.writes())
    d0 = rp.deploy_plan(sn, p["plan_id"], confirm=False)
    check("SI: no write without explicit confirmation", not sn.writes() and d0["status"] == "awaiting_confirmation")
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    rec = sn.rows("sys_script_include", name="IncidentUtils")
    check("SI: deployed to sys_script_include (one record)", d["status"] == "deployed" and len(rec) == 1, d["status"])
    r = rec[0] if rec else {}
    check("SI: fields mapped — api_name, script, client_callable, active, access",
          r.get("api_name") == "global.IncidentUtils" and r.get("script") == GOOD_SI and r.get("client_callable") == "false"
          and r.get("active") == "true" and r.get("access") == "public", r)
    checks = d["verification"]["checks"]
    rb = [c for c in checks if c.get("table") == "sys_script_include" and "stored as sent" in c["check"]]
    check("SI: read back from sys_script_include by sys_id and matched", rb and rb[0]["passed"]
          and rb[0]["sys_id"] == r.get("sys_id"), rb)
    check("SI: update-set capture verified (not just claimed)",
          any(c["type"] == "update_set_capture" and c["passed"] for c in checks))
    check("SI: no incident was created", not sn.rows("incident"))


def t_script_include_callable_and_scope():
    sn = FakeInstance()
    app = sn.add("sys_scope", name="Acme App", scope="x_acme_app")
    AI["Write the script_include"] = {"script": AJAX_SI, "description": "Group membership for client scripts"}
    text = ("Create a client callable script include named UserGroupUtils in scope x_acme_app that client scripts "
            "call through GlideAjax to check whether the current user is in a group")
    p = plan(text, sn, summary="Group membership helper", table="",
             logic=[{"description": "Check whether a user belongs to a group", "side": "server",
                     "reusable_class": "GroupUtils"}])
    it = items(p, "create")
    check("SI callable: name from the text wins over the AI's, client_callable true, scope resolved",
          it and it[0]["spec"]["name"] == "UserGroupUtils" and it[0]["spec"]["client_callable"] is True
          and (p["requirement"].get("scope_app") or {}).get("scope") == "x_acme_app",
          (it and it[0]["spec"], p["requirement"].get("scope_app"), p["requirement"]["missing_information"]))
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    rec = sn.rows("sys_script_include", name="UserGroupUtils")
    r = rec[0] if rec else {}
    check("SI callable: stored in the app scope (api_name, sys_scope, package_private) and client callable",
          d["status"] == "deployed" and r.get("api_name") == "x_acme_app.UserGroupUtils"
          and r.get("sys_scope") == app["sys_id"] and r.get("access") == "package_private"
          and r.get("client_callable") == "true", (d["status"], r))
    sn2 = FakeInstance()
    p2 = plan("Create a script include named Foo in scope x_missing_app that formats dates for reports", sn2,
              logic=[{"description": "format dates", "side": "server", "reusable_class": "Foo"}])
    check("SI scope: an application scope that does not exist is a question",
          p2["status"] == "needs_input" and any("x_missing_app" in q for q in p2["requirement"]["missing_information"]))
    AI["Write the script_include"] = {"script": GOOD_SI.replace("IncidentUtils", "UserGroupUtils")}
    p3 = plan("Create a script include named UserGroupUtils that client scripts call through GlideAjax to check group "
              "membership", FakeInstance())
    check("SI callable: a client-callable script without AbstractAjaxProcessor is blocked (after one repair round)",
          items(p3, "blocked") and "AbstractAjaxProcessor" in items(p3, "blocked")[0]["reason"],
          [(i["action"], i["reason"][:120]) for i in p3["items"]])


def t_script_include_questions_and_duplicates():
    sn = FakeInstance()
    p = plan("Create a script include that checks incident priority for the service desk", sn,
             logic=[{"description": "check incident priority", "side": "server"}])
    check("SI: no name given → question, nothing planned to write", p["status"] == "needs_input"
          and any("Script Include be named" in q for q in p["requirement"]["missing_information"]) and not sn.writes())
    p2 = plan("Create a script include named IncidentUtils", sn, logic=[])
    check("SI: no behaviour given → question about what it must do", p2["status"] == "needs_input"
          and any("should the Script Include IncidentUtils do" in q for q in p2["requirement"]["missing_information"]),
          p2["requirement"]["missing_information"])
    sn3 = FakeInstance()
    sn3.add("sys_script_include", name="IncidentUtils", api_name="global.IncidentUtils", script="// existing",
            active=True)
    AI["Write the script_include"] = {"script": GOOD_SI}
    p3 = plan("Create a script include called IncidentUtils with isP1 returning true for priority 1 incidents", sn3)
    nd = items(p3, "needs_decision")
    check("SI duplicate: an existing IncidentUtils is not overwritten — decision (reuse / skip)",
          p3["status"] == "needs_decision" and nd and set(nd[0]["options"]) == {"reuse", "skip"})
    d = rp.deploy_plan(sn3, p3["plan_id"], confirm=True)
    check("SI duplicate: confirm without a decision writes nothing", not sn3.writes() and d["status"] == "needs_decision")


def t_script_include_verification_failure():
    sn = FakeInstance(store_override={"sys_script_include": {"client_callable": "true"}})
    AI["Write the script_include"] = {"script": GOOD_SI}
    p = plan("Create a script include called IncidentUtils with isP1 returning true for priority 1 incidents", sn)
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    bad = [c for c in d["verification"]["checks"] if not c["passed"]]
    check("SI: stored value differs from what was sent → partially_completed and the check is reported",
          d["status"] == "partially_completed" and any("client_callable" in c["detail"] for c in bad)
          and d["report"]["outcome"].startswith("Partially completed"), (d["status"], bad))


def t_client_script_and_business_rule():
    sn = FakeInstance()
    AI["Write the client_script"] = {"script": "if (newValue == 'network') {\n    g_form.showFieldMsg('category', "
                                               "'Network incident', 'error');\n}", "description": "network hint"}
    p = plan("Create a client script on the incident form that shows an error message when category changes to "
             "network", sn, summary="Network hint on incident", table="incident",
             logic=[{"description": "Show an error message when category becomes network", "side": "client",
                     "client_type": "onChange", "field": "category"}])
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    cs = sn.rows("sys_script_client", table="incident")
    check("Client script: built in sys_script_client (onChange, field=category), read back, no incident",
          d["status"] == "deployed" and cs and cs[0]["type"] == "onChange" and cs[0]["field"] == "category"
          and not sn.rows("incident") and any("sys_script_client" == c.get("table") and c["passed"]
                                              for c in d["verification"]["checks"]), (d["status"], cs))
    sn2 = FakeInstance()
    p2 = plan("Create a business rule that sets impact to 2 when a hardware incident is created", sn2,
              summary="Hardware impact", table="incident", logic=[],
              effects=[{"field": "impact", "effect": "set_value", "value": "2", "side": "server",
                        "condition": "category=hardware", "events": ["insert"]}])
    d2 = rp.deploy_plan(sn2, p2["plan_id"], confirm=True)
    br = sn2.rows("sys_script", collection="incident")
    check("Business rule: built in sys_script (advanced, before insert), read back, no incident",
          d2["status"] == "deployed" and br and br[0]["advanced"] == "true" and br[0]["action_insert"] == "true"
          and not sn2.rows("incident"), (d2["status"], br))


def t_approve_and_deploy_backstop():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import snow_universal
    app = FastAPI()
    app.include_router(snow_universal.snow_universal_router)
    client = TestClient(app)
    creds = {"snow_url": "https://dev-a.service-now.com", "snow_user": "admin", "snow_pass": "pw"}
    sn = FakeInstance()
    snc.client_from = lambda body, **kw: sn
    AI["Request / user story"] = extraction()
    rp.clear_caches()
    story = {"title": "IncidentUtils script include", "i_want": "a script include IncidentUtils that tells whether "
             "an incident is down or in error", "story_type": "incident", "priority": "high", "module": "ITSM"}
    r = client.post("/snow/stories/approve-and-deploy", json={**creds, "approved": True, "pipeline": True,
                                                              "user_stories": [story]})
    c = (r.json().get("stories_created") or [{}])[0]
    check("approve-and-deploy: a script-include story typed 'incident' is NOT written as an incident",
          r.status_code == 200 and c.get("table") == "task" and c.get("retyped_from") == "incident"
          and not sn.rows("incident") and c.get("requirement_plan"), (r.status_code, c.get("table"), c.get("retyped_from")))
    real = {"title": "VPN outage in Pune", "i_want": "log the VPN outage", "story_type": "incident", "priority": "high"}
    r2 = client.post("/snow/stories/approve-and-deploy", json={**creds, "approved": True, "pipeline": True,
                                                               "user_stories": [real]})
    c2 = (r2.json().get("stories_created") or [{}])[0]
    check("approve-and-deploy: a real incident story still creates an incident", c2.get("table") == "incident"
          and len(sn.rows("incident")) == 1 and not c2.get("retyped_from"))


def t_ui_routing():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP UI routing (Playwright not installed)")
        return
    html = "file:///" + os.path.abspath(os.path.join(HERE, "..", "snow-ui", "index.html")).replace("\\", "/")
    build = ["Create a script include called IncidentUtils with a method isP1 that returns true for P1 incidents",
             "Create a client script on the incident form that shows an error message when category changes",
             "client script for the dropdown on the incident form",
             "Build a script include that alerts when incidents are down",
             "I need a script include to check whether a user is in a group",
             "Create a business rule that sets urgency to 2 when an incident is raised",
             "Add a UI policy to make description mandatory on incident"]
    incident = ["create incident for VPN outage in Pune", "log an incident: email is down"]
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/*", lambda route: route.abort() if not route.request.url.startswith("file:") else route.continue_())
        pg.goto(html)
        pg.wait_for_timeout(500)
        got = {m: pg.evaluate("(m)=>detectIntent(m)", m) for m in build + incident}
        check("detectIntent: Script Include / Client Script / Business Rule / UI Policy requests → build_artifact",
              all(got[m] == "build_artifact" for m in build), {m[:40]: got[m] for m in build if got[m] != "build_artifact"})
        check("detectIntent: real incident requests still → create_incident",
              all(got[m] == "create_incident" for m in incident), {m: got[m] for m in incident})
        looks = {m: pg.evaluate("(m)=>detectIntent(m)", m) for m in ("list business rules", "what is a script include?",
                                                                     "show client scripts on incident")}
        check("detectIntent: questions / look-ups about artifacts are not build requests",
              looks["list business rules"] == "list_business_rules" and all(v != "build_artifact" for v in looks.values()),
              looks)
        check("detectIntent: 'As a … I want a script include' stays a user story (parse)",
              pg.evaluate("()=>detectIntent('As a developer, I want a script include IncidentUtils')") == "parse")
        check("detectStoryType: configuration stories are never incidents; real outages still are",
              pg.evaluate("()=>detectStoryType('Client script alert','show an error when the form is down')") == "task"
              and pg.evaluate("()=>detectStoryType('VPN outage','users cannot access, error')") == "incident")
        # build_artifact handler: plans through the pipeline, never calls incident creation
        res = pg.evaluate("""async () => {
            const calls = [];
            window.post = async (path, body) => { calls.push(path);
                if (path === '/snow/requirements/plan') return {plan_id: 'abc123abc123abcd', status: 'awaiting_confirmation',
                    report: {requirement: {summary: 'IncidentUtils', missing_information: []}, inspection: {},
                             implementation: {to_create: [{id: 'L1', mechanism: 'script_include', name: 'IncidentUtils',
                                 table: '', reason: 'custom logic', trigger: 'class IncidentUtils · client callable: no'}]},
                             deployment: {status: 'awaiting_confirmation', records: []},
                             verification: {persisted_config: {passed: 0, failed: 0, checks: []}}, outcome: 'Planned'}};
                throw new Error('unexpected call ' + path); };
            S.chatMessages = [];
            await handleIntent('Create a script include called IncidentUtils for P1 incidents', 'build_artifact');
            const last = S.chatMessages[S.chatMessages.length - 1];
            return {calls, reqPlanId: last.reqPlanId || null, ctx: last.reqCtx || null,
                    html: renderChatBubble(last)};
        }""")
        check("AI Agent chat: build_artifact calls only /snow/requirements/plan (no incident create, no write)",
              res["calls"] == ["/snow/requirements/plan"], res["calls"])
        check("AI Agent chat: the plan shows with Confirm & build (REST deploy path, explicit confirmation)",
              res["reqPlanId"] == "abc123abc123abcd" and res["ctx"] == "impl" and "data-req-deploy" in res["html"]
              and "script include" in res["html"].lower())
        # Implement deploy: a configuration story typed 'incident' is sent as a story with the pipeline
        sent = pg.evaluate("""async () => {
            let body = null;
            window.post = async (path, b) => { body = b; return {stories_created: [{index: 0, sys_id: 'x', table: 'task',
                number: 'TASK1', story_type: 'story'}]}; };
            window.showApprovalDialog = async () => true;
            S.parsedStories = [{story: 'a client script that shows an error alert when the form is down',
                                criteria: '', story_type: 'incident', priority: 'high'}];
            S.deployResults = [];
            await deploySingleStory(0);
            return body && {type: body.user_stories[0].story_type, pipeline: body.pipeline};
        }""")
        check("Implement: a configuration story typed 'incident' is deployed as a story through the pipeline",
              sent == {"type": "story", "pipeline": True}, sent)
        b.close()
    check("UI: no JavaScript errors", not errs, errs)


for fn in (t_script_include_global, t_script_include_callable_and_scope, t_script_include_questions_and_duplicates,
           t_script_include_verification_failure, t_client_script_and_business_rule, t_approve_and_deploy_backstop,
           t_ui_routing):
    try:
        fn()
    except Exception as e:
        traceback.print_exc()
        check(f"{fn.__name__} raised", False, e)

shutil.rmtree(STORE, ignore_errors=True)
print(f"==== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ====")
sys.exit(1 if FAILED else 0)
