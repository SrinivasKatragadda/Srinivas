"""
Requirement pipeline (requirement_pipeline.py) against in-memory instances (fake_instance.py). NOT a real instance.
The AI is replaced by deterministic answers; ServiceNow by FakeInstance (real SNClient / Builder code paths).

Scenarios (the 14 asked for, plus drift, caching and isolation):
  S1  read-only request: fast path, no AI call, no instance call
  S2  requirement already satisfied by an existing business rule (different name)
  S3  requirement needing a new server-side rule: confirm, build, read back
  S4  requirement needing client-side behaviour (client script, onChange field stored in 'field')
  S5  requirement needing client- and server-side behaviour (data policy on form + server, server validation rule;
      an existing UI policy already covering the form side is reused, only the server side is added)
  S6  requirement better handled natively (UI policy instead of a script; assignment rule instead of a script)
  S7  second user story reuses / coexists with the first (no overwrite, no duplicate)
  S8  conflicting and ambiguous requirements (decision required; modify keeps old values; ambiguous group asks)
  S9  missing group mapping / missing value -> question, nothing written
  S10 unsupported operation (no assignment-rule plugin) and insufficient permissions (403)
  S11 failed deployment (rollback) and failed verification (stored value differs)
  S12 duplicate-rule prevention (same requirement twice, name collision with an unrelated rule)
  S13 different instance capabilities and schemas; per-instance isolation of plans / history / profiles
  S14 accurate execution reports for success, failure and partial completion
  S15 plan drift: the instance changed between plan and confirm -> new plan, nothing written
  S16 profile cache reused for planning, refreshed before deployment

    python requirement_pipeline_test.py
"""
import json
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = tempfile.mkdtemp(prefix="reqpipe_")
os.environ.update(SNOW_VERIFY_TLS="false", AGENT_JOBS_DIR=STORE)
sys.path.insert(0, os.path.join(HERE, "..", "snow-ai-service"))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import groq_client  # noqa: E402
import requirement_pipeline as rp  # noqa: E402
from fake_instance import FakeInstance  # noqa: E402

PASSED, FAILED = [], []
AI_CALLS = []
AI_ANSWERS = {}          # marker -> answer (dict)


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f"  — {str(detail)[:400]}" if detail and not cond else ""))


def fake_generate_json(prompt, system="", max_tokens=6000, temperature=0.2):
    AI_CALLS.append(prompt)
    for marker, answer in AI_ANSWERS.items():
        if marker in prompt:
            return answer() if callable(answer) else answer
    raise groq_client.GroqError("no simulated answer for this prompt")


groq_client.generate_json = fake_generate_json


def fresh(**kw) -> FakeInstance:
    rp.clear_caches()
    return FakeInstance(**kw)


def items(plan, action=None):
    return [i for i in plan["items"] if action is None or i["action"] == action]


def req(table="incident", effects=(), logic=(), **kw):
    return {"summary": kw.pop("summary", "Test requirement"), "table": table, "effects": list(effects),
            "logic": list(logic), "acceptance_criteria": kw.pop("criteria", ["AC1"]), **kw}


def eff(field, effect, value="", side="", condition="", events=("insert", "update")):
    return {"field": field, "effect": effect, "value": value, "side": side, "condition": condition,
            "events": list(events)}


# ════════════════════════════════════════════════════════════════════════════
def s1_read_only():
    sn = fresh()
    AI_CALLS.clear()
    p = rp.plan_requirement(sn, text="list the open P1 incidents for the network team")
    check("S1 read-only text takes the fast path", p["status"] == "read_only" and p["fast_path"], p["status"])
    check("S1 no AI call and no ServiceNow call for a look-up", not AI_CALLS and sn.calls == 0, (len(AI_CALLS), sn.calls))
    check("S1 report says nothing was planned", "look-up" in p["report"]["outcome"])
    check("S1 gate recognises configuration language", rp.looks_like_configuration(
        "Make the description mandatory when category is network") and not rp.looks_like_configuration(
        "show me incident INC0010001"))


def s2_already_satisfied():
    sn = fresh()
    sn.add("sys_script", name="Network default urgency", collection="incident", when="before", active=True,
           action_insert=True, action_update=False, filter_condition="category=network^EQ",
           script="(function executeRule(current, previous /*null when async*/) {\n    current.urgency = 2;\n})(current, previous);")
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("urgency", "set_value", "2", "server", "category=network",
                                                             ["insert"])]))
    check("S2 existing business rule with a different name satisfies the requirement",
          p["status"] == "already_satisfied" and items(p, "reuse"), [(i["action"], i["reason"]) for i in p["items"]])
    ex = items(p, "reuse")[0]["existing"][0] if items(p, "reuse") else {}
    check("S2 evidence names the existing rule, found by behaviour", ex.get("name") == "Network default urgency")
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    check("S2 nothing is written for an already satisfied requirement", not sn.writes() and d["status"] ==
          "already_satisfied", d["status"])
    # a script that does more than set the value is NOT treated as equivalent
    sn2 = fresh()
    sn2.add("sys_script", name="Network urgency + log", collection="incident", when="before", active=True,
            action_insert=True, filter_condition="category=network",
            script="current.urgency = 2;\ngs.eventQueue('x', current);")
    p2 = rp.plan_requirement(sn2, requirement=req(effects=[eff("urgency", "set_value", "2", "server",
                                                               "category=network", ["insert"])]))
    check("S2 a script with extra logic needs a decision instead of being assumed equivalent",
          p2["status"] == "needs_decision" and "reuse" in items(p2)[0]["options"], [i["action"] for i in p2["items"]])


def s3_new_server_rule():
    sn = fresh()
    p = rp.plan_requirement(sn, requirement=req(summary="Hardware incidents get impact 2",
                                                effects=[eff("impact", "set_value", "2", "server", "category=hardware",
                                                             ["insert"])]))
    it = items(p, "create")
    check("S3 plan creates one business rule", p["status"] == "awaiting_confirmation" and len(it) == 1
          and it[0]["mechanism"] == "business_rule", [(i["action"], i["mechanism"]) for i in p["items"]])
    d0 = rp.deploy_plan(sn, p["plan_id"], confirm=False)
    check("S3 without confirm=true nothing is written", not sn.writes() and d0["status"] == "awaiting_confirmation")
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    br = sn.rows("sys_script", collection="incident")
    check("S3 deployed and exactly one rule exists", d["status"] == "deployed" and len(br) == 1, d["status"])
    check("S3 rule is active, advanced, before insert with the condition",
          br and br[0]["advanced"] == "true" and br[0]["active"] == "true" and br[0]["when"] == "before"
          and br[0]["action_insert"] == "true" and br[0]["filter_condition"] == "category=hardware"
          and "current.setValue('impact', '2')" in br[0]["script"], br and br[0])
    checks = d["verification"]["checks"]
    check("S3 the stored rule was read back and matched", any("stored as sent" in c["check"] and c["passed"]
                                                              for c in checks) and all(c["passed"] for c in checks),
          [c for c in checks if not c["passed"]])
    check("S3 captured in the requirement's update set", any(c["type"] == "update_set_capture" and c["passed"]
                                                             for c in checks))
    check("S3 runtime test reported as not run", d["verification"]["runtime"]["status"] == "not_run")
    return sn, p, d


def s4_client_side():
    sn = fresh()
    AI_ANSWERS["Write the client_script"] = {"script": "if (newValue == 'network') {\n    g_form.addInfoMessage("
                                                       "'Network incidents are routed to the network team');\n}",
                                             "filter_condition": "", "description": "Info message for network"}
    p = rp.plan_requirement(sn, requirement=req(logic=[{"description": "Show an info message when category becomes "
                                                       "network", "side": "client", "client_type": "onChange",
                                                       "field": "category"}]))
    it = items(p, "create")
    check("S4 plan creates a client script for client-side logic", it and it[0]["mechanism"] == "client_script",
          [(i["action"], i["mechanism"], i["reason"]) for i in p["items"]])
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    cs = sn.rows("sys_script_client", table="incident")
    check("S4 onChange client script stored with its field in 'field'", d["status"] == "deployed" and cs
          and cs[0]["type"] == "onChange" and cs[0]["field"] == "category" and "function onChange" in cs[0]["script"],
          (d["status"], cs))
    # an AI script that uses server APIs on the client is refused by the existing validator
    AI_ANSWERS["Write the client_script"] = {"script": "var gr = new GlideRecord('sys_user'); gr.query();"}
    p2 = rp.plan_requirement(fresh(), requirement=req(logic=[{"description": "Look up the user", "side": "client",
                                                              "client_type": "onLoad"}]))
    check("S4 client script with GlideRecord is blocked by validation (after one repair round)",
          items(p2, "blocked") and "GlideRecord" in items(p2, "blocked")[0]["reason"],
          [(i["action"], i["reason"]) for i in p2["items"]])


def s5_client_and_server():
    sn = fresh()
    pol = sn.add("sys_ui_policy", short_description="Description on form", table="incident", conditions="",
                 active=True)
    sn.add("sys_ui_policy_action", ui_policy=pol["sys_id"], field="description", mandatory="true", visible="ignore",
           disabled="ignore")
    AI_ANSWERS["Write the business_rule"] = {"script": "if (current.description.toString().length < 10) {\n"
                                                       "    gs.addErrorMessage('Description is too short');\n"
                                                       "    current.setAbortAction(true);\n}",
                                             "filter_condition": "", "description": "Reject short descriptions"}
    p = rp.plan_requirement(sn, requirement=req(
        effects=[eff("description", "mandatory", "true", "both")],
        logic=[{"description": "Reject descriptions shorter than 10 characters", "side": "server"}]))
    mechs = sorted(i["mechanism"] for i in items(p, "create"))
    check("S5 plan adds a server data policy and a validation business rule", mechs == ["business_rule",
                                                                                         "data_policy"], mechs)
    dp_item = next(i for i in items(p, "create") if i["mechanism"] == "data_policy")
    check("S5 form side already covered by the existing UI policy, so the data policy is server-only",
          dp_item["spec"]["enforce_ui"] is False and dp_item["side"] == "server", dp_item)
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    dp = sn.rows("sys_data_policy2", model_table="incident")
    rules = sn.rows("sys_data_policy_rule", field="description")
    check("S5 data policy + rule + validation rule deployed and verified", d["status"] == "deployed" and dp and rules
          and rules[0]["mandatory"] == "true" and sn.rows("sys_script", collection="incident"),
          (d["status"], [c for c in d["verification"]["checks"] if not c["passed"]]))
    check("S5 acceptance: description mandatory now covered on client and server",
          any("description mandatory" in c["check"] and c["passed"] for c in d["verification"]["checks"]))


def s6_native_preferred():
    sn = fresh()
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("subcategory", "read_only", "true", "client",
                                                             "state=6"),
                                                         eff("assignment_group", "assign", "Network Support", "server",
                                                             "category=network", ["insert"])]))
    mechs = {i["mechanism"] for i in items(p, "create")}
    check("S6 form read-only uses a UI policy, assignment uses an assignment rule (no scripts)",
          mechs == {"ui_policy", "assignment_rule"}, mechs)
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    ar = sn.rows("sysrule_assignment", table="incident")
    check("S6 assignment rule references the resolved group sys_id", d["status"] == "deployed" and ar
          and ar[0]["group"] == sn.groups["Network Support"] and ar[0]["condition"] == "category=network",
          (d["status"], ar))
    act = sn.rows("sys_ui_policy_action", field="subcategory")
    check("S6 UI policy action makes the field read-only", act and act[0]["disabled"] == "true")


def s7_second_story(sn, first_plan):
    # same behaviour asked again in a later story: reused, with the earlier requirement named as its origin
    p = rp.plan_requirement(sn, requirement=req(summary="Hardware impact again", effects=[
        eff("impact", "set_value", "2", "server", "category=hardware", ["insert"])]))
    reuse = items(p, "reuse")
    check("S7 later story is satisfied by the rule the first story built", p["status"] == "already_satisfied"
          and reuse, [(i["action"], i["reason"]) for i in p["items"]])
    origin = (reuse[0]["existing"][0].get("origin") or {}) if reuse else {}
    check("S7 origin points to the first requirement", origin.get("plan_id") == first_plan["plan_id"], origin)
    before = json.dumps(sn.rows("sys_script", collection="incident"), sort_keys=True)
    p2 = rp.plan_requirement(sn, requirement=req(summary="Urgency on form", effects=[
        eff("urgency", "mandatory", "true", "client")]))
    d2 = rp.deploy_plan(sn, p2["plan_id"], confirm=True)
    after = json.dumps(sn.rows("sys_script", collection="incident"), sort_keys=True)
    check("S7 a different second story coexists: new UI policy, first rule untouched",
          d2["status"] == "deployed" and before == after and sn.rows("sys_ui_policy", table="incident"),
          d2["status"])
    hist = rp.history(sn.instance)
    check("S7 history keeps both requirements for this instance", len(hist) >= 2)


def s8_conflict_and_ambiguity():
    sn = fresh()
    pol = sn.add("sys_ui_policy", short_description="Category required", table="incident", conditions="", active=True)
    act = sn.add("sys_ui_policy_action", ui_policy=pol["sys_id"], field="category", mandatory="true",
                 visible="ignore", disabled="ignore")
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("category", "mandatory", "false", "client")]))
    it = items(p, "needs_decision")
    check("S8 opposite state on the same field is a conflict needing a decision",
          p["status"] == "needs_decision" and it and "modify" in it[0]["options"], [i["action"] for i in p["items"]])
    d0 = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    check("S8 confirm without a decision writes nothing", not sn.writes() and d0["status"] == "needs_decision")
    bad = rp.deploy_plan(sn, p["plan_id"], confirm=True, decisions={it[0]["id"]: "delete everything"})
    check("S8 an invalid decision is refused", bad.get("problems") and not sn.writes())
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True, decisions={it[0]["id"]: "modify"})
    check("S8 'modify' changes only the existing action, old value reported",
          d["status"] == "deployed" and sn.rows("sys_ui_policy_action", sys_id=act["sys_id"])[0]["mandatory"] == "false"
          and any(r["status"] == "modified" and "'true' → 'false'" in r["message"]
                  for r in d["deployment"]["results"]), (d["status"], d["deployment"]["results"]))
    check("S8 no new policy was created when modifying", len(sn.rows("sys_ui_policy")) == 1)
    # ambiguous group name
    sn2 = fresh()
    sn2.add("sys_user_group", name="Network Support", active="true")   # second group with the same name
    p2 = rp.plan_requirement(sn2, requirement=req(effects=[eff("assignment_group", "assign", "Network Support")]))
    check("S8 an ambiguous group asks which one", p2["status"] == "needs_input"
          and any("several" in q for q in p2["requirement"]["missing_information"]), p2["requirement"]["missing_information"])


def s9_missing_information():
    sn = fresh()
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("assignment_group", "assign", "Network Team", "server",
                                                             "category=network")]))
    check("S9 an unknown group is a question, not a guess", p["status"] == "needs_input" and any(
        "Network Team" in q for q in p["requirement"]["missing_information"]), p["requirement"]["missing_information"])
    p2 = rp.plan_requirement(sn, requirement=req(effects=[eff("assignment_group", "assign", "")]))
    check("S9 a missing group mapping asks which group", any("Which group" in q for q in
                                                            p2["requirement"]["missing_information"]))
    p3 = rp.plan_requirement(sn, requirement=req(table="", effects=[eff("urgency", "mandatory", "true")]))
    check("S9 a missing table asks which table", p3["status"] == "needs_input")
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    check("S9 deploy refuses while questions are open", d["status"] == "needs_input" and not sn.writes())
    AI_ANSWERS["Request / user story"] = {"summary": "Assign network incidents", "table": "incident",
                                          "request_kind": "configuration",
                                          "effects": [{"field": "assignment_group", "effect": "assign", "value": "",
                                                       "side": "server", "condition": "category=network"}],
                                          "missing_information": ["Which group handles network incidents?"]}
    AI_CALLS.clear()
    p4 = rp.plan_requirement(sn, text="Network incidents should be assigned automatically")
    check("S9 text path: AI extraction keeps the group empty and the question is shown",
          p4["status"] == "needs_input" and len(AI_CALLS) == 1 and "Which group" in p4["report"]["outcome"],
          p4["report"]["outcome"])


def s10_unsupported_and_permissions():
    sn = fresh(missing_tables={"sysrule_assignment"})
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("assignment_group", "assign", "Service Desk")]))
    bl = items(p, "blocked")
    check("S10 no assignment-rule table: blocked as unavailable, not swapped for a script",
          p["status"] == "blocked" and bl and bl[0]["mechanism"] == "assignment_rule"
          and bl[0].get("blocked_kind") == "unavailable_on_instance", [(i["action"], i["mechanism"]) for i in p["items"]])
    check("S10 report lists the capability as unavailable",
          p["report"]["inspection"]["capabilities"].get("assignment_rule") == "unavailable_on_instance")
    sn2 = fresh(forbid_writes={"sys_ui_policy"})
    p2 = rp.plan_requirement(sn2, requirement=req(effects=[eff("urgency", "mandatory", "true", "client")]))
    d2 = rp.deploy_plan(sn2, p2["plan_id"], confirm=True)
    check("S10 403 on write: failed, classified as insufficient permissions",
          d2["status"] == "failed" and "insufficient_permissions" in d2["deployment"]["error_kinds"],
          (d2["status"], d2["deployment"]["error_kinds"], d2["deployment"]["error"]))
    check("S10 no UI policy left behind", not sn2.rows("sys_ui_policy"))


def s11_failed_deploy_and_verification():
    sn = fresh(forbid_writes={"sys_data_policy_rule"})
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("description", "mandatory", "true", "server")]))
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    check("S11 failure after the data policy was created rolls it back", d["status"] == "failed"
          and not sn.rows("sys_data_policy2") and d["deployment"]["rollback"], (d["status"], sn.rows("sys_data_policy2")))
    check("S11 failed deployment skips read-back checks (nothing left to verify)",
          d["report"]["verification"]["persisted_config"]["passed"] == 0)
    sn2 = fresh(store_override={"sys_script": {"active": "false"}})   # instance stores something else
    p2 = rp.plan_requirement(sn2, requirement=req(effects=[eff("impact", "set_value", "3", "server")]))
    d2 = rp.deploy_plan(sn2, p2["plan_id"], confirm=True)
    bad = [c for c in d2["verification"]["checks"] if not c["passed"]]
    check("S11 stored value differs from what was sent: verification fails, status partially_completed",
          d2["status"] == "partially_completed" and any("active" in c["detail"] for c in bad), (d2["status"], bad))


def s12_duplicates():
    sn = fresh()
    r = req(summary="Urgency mandatory", effects=[eff("urgency", "mandatory", "true", "client")])
    d = rp.deploy_plan(sn, rp.plan_requirement(sn, requirement=r)["plan_id"], confirm=True)
    p2 = rp.plan_requirement(sn, requirement=r)
    check("S12 the same requirement again is already satisfied (no duplicate policy)",
          d["status"] == "deployed" and p2["status"] == "already_satisfied" and len(sn.rows("sys_ui_policy")) == 1,
          p2["status"])
    sn2 = fresh()
    sn2.add("sys_script", name="Impact rule (impact)", collection="incident", when="after", active=True,
            action_update=True, script="gs.info('unrelated');")
    p3 = rp.plan_requirement(sn2, requirement=req(summary="Impact rule", effects=[eff("impact", "set_value", "2",
                                                                                      "server")]))
    name = items(p3, "create")[0]["name"]
    check("S12 name collision with an unrelated rule gets a unique name", name != "Impact rule (impact)"
          and name.startswith("Impact rule (impact)"), name)
    d3 = rp.deploy_plan(sn2, p3["plan_id"], confirm=True)
    check("S12 exactly-one check passes and the unrelated rule is untouched",
          d3["status"] == "deployed" and any(c["check"].startswith("exactly one") and c["passed"]
                                             for c in d3["verification"]["checks"])
          and sn2.rows("sys_script", name="Impact rule (impact)")[0]["script"] == "gs.info('unrelated');")


def s13_instances_and_schemas():
    sn = fresh(missing_columns={"sys_data_policy_rule": ["disabled"]})
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("description", "mandatory", "true", "server")]))
    bl = items(p, "blocked")
    check("S13 missing column on this instance blocks the data policy (incompatible schema)",
          bl and bl[0].get("blocked_kind") == "incompatible_schema", [(i["action"], i.get("blocked_kind")) for i in
                                                                      p["items"]])
    sn2 = fresh(drop_incident_fields={"category"})
    p2 = rp.plan_requirement(sn2, requirement=req(effects=[eff("category", "mandatory", "true")]))
    check("S13 a field this instance does not have becomes a question", p2["status"] == "needs_input"
          and any("category" in q for q in p2["requirement"]["missing_information"]))
    p3 = rp.plan_requirement(sn2, requirement=req(effects=[eff("priority", "set_value", "Critical", "server")]))
    check("S13 an invalid choice value asks for a valid one", any("not a valid value" in q for q in
                                                                  p3["requirement"]["missing_information"]))
    a = fresh(url="https://dev-a.service-now.com")
    pa = rp.plan_requirement(a, requirement=req(effects=[eff("urgency", "mandatory", "true", "client")]))
    b = FakeInstance(url="https://dev-b.service-now.com")
    try:
        rp.deploy_plan(b, pa["plan_id"], confirm=True)
        isolated = False
    except LookupError:
        isolated = True
    check("S13 a plan from instance A cannot be deployed on instance B", isolated and not b.writes())
    check("S13 plans and history are stored per instance", rp.instance_key(a.instance) != rp.instance_key(b.instance)
          and os.path.isdir(os.path.join(STORE, "requirements", rp.instance_key(a.instance))))
    pb = rp.instance_profile(b)
    check("S13 profiles are per instance", pb["instance"] == b.instance and not pb["cached"])


def s14_reports(success_plan):
    r = success_plan["report"]
    rec = r["deployment"]["records"]
    check("S14 success report: deployed, records with real sys_ids, runtime not run",
          r["deployment"]["status"] == "deployed" and rec and all(x["sys_id"] for x in rec)
          and r["verification"]["runtime"]["status"] == "not_run" and r["verification"]["persisted_config"]["failed"] == 0,
          r["deployment"])
    check("S14 success report has every section", set(r) == {"requirement", "inspection", "implementation",
                                                              "deployment", "verification", "outcome"})
    sn = fresh(forbid_writes={"sys_script"})
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("impact", "set_value", "2", "server")]))
    f = rp.deploy_plan(sn, p["plan_id"], confirm=True)["report"]
    check("S14 failure report: failed, error shown, outcome says failed", f["deployment"]["status"] == "failed"
          and f["deployment"]["error"] and f["outcome"].startswith("Deployment failed"), f["outcome"])
    sn2 = fresh(store_override={"sys_ui_policy": {"conditions": "something=else"}})
    p2 = rp.plan_requirement(sn2, requirement=req(effects=[eff("urgency", "mandatory", "true", "client",
                                                               "priority=1")]))
    pr = rp.deploy_plan(sn2, p2["plan_id"], confirm=True)["report"]
    check("S14 partial report: partially_completed with the failed checks listed",
          pr["deployment"]["status"] == "partially_completed" and pr["verification"]["persisted_config"]["failed"] >= 1
          and pr["outcome"].startswith("Partially completed"), pr["outcome"])
    sys_ids = {x["sys_id"] for x in rec}
    check("S14 report sys_ids exist in the instance (nothing fabricated)", sys_ids and all(
        any(row["sys_id"] == s for t in ("sys_script",) for row in SUCCESS_SN.rows(t)) for s in sys_ids))


def s15_drift():
    sn = fresh()
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("impact", "set_value", "2", "server",
                                                             "category=hardware", ["insert"])]))
    sn.add("sys_script", name="Someone else's rule", collection="incident", when="before", active=True,
           action_insert=True, filter_condition="category=hardware", script="current.impact = 3;")
    d = rp.deploy_plan(sn, p["plan_id"], confirm=True)
    check("S15 instance changed since planning: new plan returned, nothing written",
          d["status"] in ("plan_changed", "needs_decision") and d.get("previous_plan_id") == p["plan_id"]
          and not sn.writes(), d["status"])


def s16_profile_cache():
    sn = fresh()
    rp.plan_requirement(sn, requirement=req(effects=[eff("urgency", "mandatory", "true", "client")]))
    n1 = sn.calls
    p = rp.plan_requirement(sn, requirement=req(effects=[eff("impact", "mandatory", "true", "client")]))
    n2 = sn.calls - n1
    check("S16 second plan reuses the cached profile (fewer calls)", p["profile"]["cached"] and n2 < n1, (n1, n2))
    before = len([l for l in sn.log if l["table"] == "sys_dictionary"])
    rp.deploy_plan(sn, p["plan_id"], confirm=True)
    after = len([l for l in sn.log if l["table"] == "sys_dictionary"])
    check("S16 deployment refreshes the profile and schema first", after > before)


SUCCESS_SN = None
for fn in (s1_read_only, s2_already_satisfied):
    try:
        fn()
    except Exception as e:
        traceback.print_exc()
        check(f"{fn.__name__} raised", False, e)
try:
    SUCCESS_SN, first_plan, first_deploy = s3_new_server_rule()
    s7_second_story(SUCCESS_SN, first_plan)
    s14_reports(first_deploy)
except Exception as e:
    traceback.print_exc()
    check("S3/S7/S14 raised", False, e)
for fn in (s4_client_side, s5_client_and_server, s6_native_preferred, s8_conflict_and_ambiguity,
           s9_missing_information, s10_unsupported_and_permissions, s11_failed_deploy_and_verification, s12_duplicates,
           s13_instances_and_schemas, s15_drift, s16_profile_cache):
    try:
        fn()
    except Exception as e:
        traceback.print_exc()
        check(f"{fn.__name__} raised", False, e)

shutil.rmtree(STORE, ignore_errors=True)
print(f"==== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ====")
sys.exit(1 if FAILED else 0)
