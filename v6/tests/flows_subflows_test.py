"""
Offline checks for Build App flows & subflows (sn_build.step_flows + flow_agent). No ServiceNow, no Node, no browser:
a fake SN client stands in for sys_hub_flow, a fake Flow Server answers /deploy-subflow and /deploy-flow, and a
fake Flow Designer UI records what would be clicked in the browser fallback.

    python flows_subflows_test.py
"""
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "snow-ai-service"))
os.environ["FLOW_KB_FILE"] = os.path.join(tempfile.mkdtemp(), "kb.json")
os.environ.pop("FLOW_SERVER_URL", None)
os.environ.pop("FLOW_ENGINE", None)

import sn_build  # noqa: E402
import flow_agent.browser as fb  # noqa: E402
from flow_agent import server_specs as ss  # noqa: E402
from flow_agent.kb import SubflowKB  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))


INSTANCE = "https://dev402135.service-now.com"


class FakeSN:
    def __init__(self, records=None):
        self.instance, self.username, self.password = INSTANCE, "admin", "pw"
        self.records = list(records or [])
        self.people = {("sys_user_group", "name=Hardware"): "g" * 32, ("sys_user", "email=boss@corp.com"): "u" * 32}

    def query(self, table, query="", fields="", limit=100, **kw):
        assert table == "sys_hub_flow"
        parts = dict(p.split("=", 1) for p in query.split("^") if "=" in p and not p.startswith("ORDERBY"))
        rows = [r for r in self.records if all(r.get(k) == v for k, v in parts.items())]
        return rows[:limit]

    def get_one(self, table, query, fields=""):
        if table == "sys_hub_flow":
            rows = self.query(table, query)
            return rows[0] if rows else None
        sid = self.people.get((table, query.split("^")[0]))
        return {"sys_id": sid} if sid else None


class FakeServer:
    """Stands in for v6/flow-server: records requests, 'installs' into the fake instance."""
    def __init__(self, sn, up=True, fail_paths=()):
        self.sn, self.up, self.fail_paths, self.calls = sn, up, set(fail_paths), []

    def get(self, url, timeout=None):
        if not self.up:
            raise ConnectionError("refused")
        return type("R", (), {"ok": True})()

    def post(self, url, json=None, timeout=None):
        path = url.rsplit("/", 1)[-1]
        self.calls.append((path, json))
        kind, key = ("subflow", "subflowSpec") if path == "deploy-subflow" else ("flow", "flowSpec")
        if path in self.fail_paths:
            body = {"status": "build_failed", "logs": {"build": {"exitCode": 1, "stderr": "TS2345: bad input"}}}
        else:
            sid = f"{kind}_{len(self.sn.records):030d}"[-32:]
            self.sn.records.append({"sys_id": sid, "name": json[key]["name"], "type": kind,
                                    "status": "published", "active": "true"})
            body = {"status": "installed", "sys_id": sid, "name": json[key]["name"], "active": "true"}
        import json as _j
        return type("R", (), {"status_code": 200, "content": b"x", "json": lambda self: body,
                              "text": _j.dumps(body)})()


class FakeUI:
    instances = []
    publish_ok = True

    def __init__(self, instance, username, password, log, ask=None, pause_steps=True, **kw):
        self.log, self.ask, self.calls, self.current = log, ask, [], None
        self.sn = FakeUI.sn
        FakeUI.instances.append(self)

    def start(self): self.calls.append("start")
    def close(self): self.calls.append("close")
    def shot(self, tag): return None

    def open_new(self, kind, name, desc=""):
        self.current = (kind, name)
        self.calls.append(f"new {kind}:{name}")

    def fill_io(self, i, o): self.calls.append(f"io {len(i)}/{len(o)}")
    def set_trigger(self, t): self.calls.append(f"trigger {t['type']} {t['table']}")

    def add_action(self, a):
        self.calls.append(f"action {a['type']}")
        return "added"

    def call_subflow(self, name, inputs): self.calls.append(f"call {name}")

    def pause(self, title, message, fields=(), prefilled=None, options=("continue",), required=False):
        ans = (self.ask(dict(title=title, options=list(options))) if self.ask else {"action": "continue"})
        if ans.get("action") in ("cancel", "timeout"):
            raise fb.FlowCancelled(ans["action"])
        return ans.get("action")

    def publish(self, kind):
        k, name = self.current
        self.calls.append(f"publish {name}")
        if not any(r["name"] == name for r in self.sn.records):
            self.sn.records.append({"sys_id": f"b{len(self.sn.records):031d}", "name": name, "type": k.lower(),
                                    "status": "published" if FakeUI.publish_ok else "draft",
                                    "active": "true" if FakeUI.publish_ok else "false"})


fb.FlowDesignerUI = FakeUI
BROWSER = {"ok": True}
fb.playwright_available = lambda: BROWSER["ok"]


def make_builder(plan, records=None, opts=None, ask=None, server_up=True, fail_paths=()):
    sn = FakeSN(records)
    FakeUI.sn, FakeUI.instances = sn, []
    server = FakeServer(sn, up=server_up, fail_paths=fail_paths)
    sn_build.requests.get, sn_build.requests.post = server.get, server.post
    norm = sn_build.normalize_plan(plan)
    logs = []
    b = sn_build.Builder(sn, norm, {"stop_on_error": False, **(opts or {})}, lambda m, s, d: logs.append((s, m)))
    b.app = {"sys_id": "a" * 32, "scope": "x_1_demo"}
    b._resolve_table = lambda t: f"x_1_demo_{t}" if t == "laptop_request" else t
    b.ask = ask
    return b, sn, server, logs


PLAN = {
    "app_name": "Demo",
    "subflows": [{"id": "notify_oncall", "name": "Notify on-call",
                  "inputs": [{"name": "record_id", "label": "Record ID", "type": "string"}],
                  "outputs": [{"name": "status", "type": "String"}],
                  "actions": [{"id": "send", "type": "core.sendEmail", "inputs": {"to": "oncall@corp.com", "subject": "P1"}},
                              {"id": "approve", "type": "core.askForApproval", "inputs": {"approvers": "Hardware"}},
                              {"id": "log", "type": "core.log", "inputs": {"log_message": "{{action.send.email}}"}}]}],
    "flows": [{"name": "P1 incident created", "trigger": {"type": "record.created", "table": "incident",
                                                          "condition": "priority=1"},
               "actions": [{"type": "core.log", "inputs": {"log_message": "P1 {{trigger.current.number}}"}}],
               "subflow": "notify_oncall", "subflow_inputs": {"record_id": "Trigger > Incident > Sys ID"}}],
}

_sleep = sn_build.time.sleep
sn_build.time.sleep = lambda s: None

print("Engine selection")
v = sn_build.validate_plan(PLAN)
check("plan with a subflow is valid", v["valid"], v["issues"])
check("subflows counted", v["counts"]["subflows"] == 1 and v["counts"]["flows"] == 1)
check("flow keeps its subflow reference", v["plan"]["flows"][0]["subflow"] == "notify_oncall")
up = {"server": True, "browser": True}
check("auto = server when the Flow Server is up (even with subflows)", sn_build.flow_engine(v["plan"], {}, up) == "server")
check("auto = browser when the Flow Server is down", sn_build.flow_engine(v["plan"], {}, {"server": False, "browser": True}) == "browser")
check("nothing available → none", sn_build.flow_engine(v["plan"], {}, {"server": False, "browser": False}) is None)
check("forced browser honoured", sn_build.flow_engine(v["plan"], {"flow_engine": "browser"}, up) == "browser")
bad = sn_build.validate_plan({"subflows": [{"name": "Empty"}],
                              "flows": [{"name": "Nothing", "trigger": {"table": "incident"}}]})
check("empty subflow / no-op flow rejected", len(bad["issues"]) == 2, bad["issues"])

print("Plan → Flow Server specs")
norm = sn_build.normalize_plan(PLAN)
spec = ss.subflow_spec(norm["subflows"][0], lambda k, n: {"group": "g" * 32}.get(k, "") if n == "Hardware" else "", "incident")
acts = {a["id"]: a for a in spec["actions"]}
check("subflow action ids prefixed with the subflow id", set(acts) == {"notify_oncall__send", "notify_oncall__approve",
                                                                         "notify_oncall__log"}, list(acts))
check("action data pills follow the prefix", acts["notify_oncall__log"]["inputs"]["log_message"] ==
      "{{action.notify_oncall__send.email}}")
check("email in a subflow targets the record input, not a trigger",
      acts["notify_oncall__send"]["inputs"]["record"] == "{{input.record_id}}"
      and acts["notify_oncall__send"]["inputs"]["table_name"] == "incident")
cond = acts["notify_oncall__approve"]["inputs"].get("approval_conditions", {})
check("approver group name resolved to a sys_id rule",
      cond.get("ruleSets", [{}])[0].get("rules", [[{}]])[0][0].get("groups") == ["g" * 32], cond)
check("subflow input types mapped for the SDK", spec["inputs"]["record_id"]["type"] == "string")
fspec = ss.flow_spec(norm["flows"][0], "incident", "s" * 32, norm["subflows"][0])
call = fspec["actions"][-1]
check("flow ends with a subflow.call to the subflow sys_id",
      call["type"] == "subflow.call" and call["inputs"]["subflow_sys_id"] == "s" * 32)
check("'Trigger > Incident > Sys ID' becomes a data pill",
      call["inputs"]["subflow_inputs"]["record_id"] == "{{trigger.current.sys_id}}")
check("human pill forms", ss.human_to_pill("Trigger > Incident > Number") == "{{trigger.current.number}}"
      and ss.human_to_pill("Trigger > Incident") == "{{trigger.current.sys_id}}" and ss.human_to_pill("plain") == "plain")
task = ss._action({"id": "t", "type": "core.createTask", "inputs": {"table": "sc_task", "short_description": "Prep"}},
                  table="incident", record="{{input.record_id}}", resolve=None)
check("createTask mapped to task_table + field_values",
      task["inputs"]["task_table"] == "sc_task" and task["inputs"]["field_values"]["short_description"] == "Prep")

print("Automatic build: subflow then the flow that calls it, no browser")
b, sn, server, logs = make_builder(PLAN)
b.step_flows()
paths = [c[0] for c in server.calls]
check("deploy-subflow then deploy-flow", paths == ["deploy-subflow", "deploy-flow"], paths)
sub_sid = b.results[0]["sys_id"]
flow_call = server.calls[1][1]["flowSpec"]["actions"][-1]
check("flow calls the subflow by the sys_id the server returned", flow_call["inputs"]["subflow_sys_id"] == sub_sid)
check("request carries app scope + credentials", server.calls[0][1]["scopeId"] == "a" * 32 and server.calls[0][1]["username"] == "admin")
check("no browser opened", not FakeUI.instances)
check("both created, marked automatic", [r["status"] for r in b.results] == ["created", "created"]
      and all("automatically" in r["message"] for r in b.results), b.results)
check("SDK installs not rolled back by raw delete / not expected in update set",
      all(c.get("no_rollback") and c.get("capture_key") is None for c in b.created))
check("subflow added to the KB", any(e["published_name"] == "Notify on-call" for e in SubflowKB().entries(INSTANCE)))

print("Re-run: everything already exists → reused, nothing deployed")
b2, _, server2, _ = make_builder(PLAN, records=sn.records)
b2.step_flows()
check("nothing deployed again", not server2.calls and not FakeUI.instances)
check("both reported reused", [r["status"] for r in b2.results] == ["reused", "reused"])

print("KB reuse: a similar published subflow under another name is wired instead of building")
similar = {**PLAN, "subflows": [{**PLAN["subflows"][0], "id": "page_oncall", "name": "Page on-call engineer"}],
           "flows": [{**PLAN["flows"][0], "name": "P1 created v2", "subflow": "page_oncall"}]}
b3, _, server3, _ = make_builder(similar, records=sn.records)
b3.step_flows()
check("similar subflow reused from KB", b3.results[0]["status"] == "reused" and "subflow library" in b3.results[0]["message"])
check("only the new flow deployed, calling the reused subflow",
      [c[0] for c in server3.calls] == ["deploy-flow"]
      and server3.calls[0][1]["flowSpec"]["actions"][-1]["inputs"]["subflow_sys_id"] == sub_sid)
b3b, _, server3b, _ = make_builder(similar, records=sn.records, opts={"reuse_subflows": False})
b3b.step_flows()
check("reuse_subflows=false builds it", [c[0] for c in server3b.calls] == ["deploy-subflow", "deploy-flow"])

print("Browser fallback only for the item the Flow Server could not build")
b4, sn4, server4, logs4 = make_builder(PLAN, fail_paths={"deploy-subflow"})
b4.step_flows()
ui = FakeUI.instances[0] if FakeUI.instances else None
check("subflow built in the browser after the automatic build failed",
      ui is not None and "new Subflow:Notify on-call" in ui.calls and "fallback" in b4.results[0]["message"], b4.results)
check("the reason is logged", any("Automatic build of subflow" in m and "TS2345" in m for _, m in logs4))
check("flow still built automatically, calling the browser-built subflow",
      server4.calls[-1][0] == "deploy-flow" and "automatically" in b4.results[1]["message"]
      and server4.calls[-1][1]["flowSpec"]["actions"][-1]["inputs"]["subflow_sys_id"] == b4.results[0]["sys_id"])
check("browser closed at the end", ui and ui.calls[-1] == "close")
b5, _, _, _ = make_builder(PLAN, fail_paths={"deploy-subflow"}, opts={"flow_fallback": False})
b5.step_flows()
check("fallback off → failed, no browser", b5.results[0]["status"] == "failed" and not FakeUI.instances)
check("…and the flow that needs it fails too, not half-wired", b5.results[1]["status"] == "failed")

print("Flow Server down → browser engine with pauses")
answers = iter(["continue"])
FakeUI.publish_ok = False
b6, _, _, _ = make_builder({**PLAN, "flows": []}, server_up=False, ask=lambda p: {"action": next(answers, "continue")})
b6.step_flows()
check("unpublished subflow kept as draft", "DRAFT" in b6.results[0]["message"], b6.results)
b7, _, _, _ = make_builder({**PLAN, "flows": []}, server_up=False, ask=lambda p: {"action": "cancel"})
try:
    b7.step_flows()
    check("cancel stops the build", False)
except sn_build.SNError as e:
    check("cancel stops the build", e.status == 499)
FakeUI.publish_ok = True

print("Pause gate between the build thread and the API")
job_id = "testgate"
sn_build._jobs[job_id] = {"job_id": job_id, "status": "running", "steps": []}
ask = sn_build._make_ask(job_id, lambda m, s, d: None)
out = {}
t = threading.Thread(target=lambda: out.update(ans=ask({"title": "Check", "options": ["continue", "skip"]})))
t.start()
for _ in range(100):
    if sn_build._jobs[job_id].get("pause"):
        break
    _sleep(0.05)
job = sn_build._jobs[job_id]
check("job shows waiting_input with the pause", job["status"] == "waiting_input" and job["pause"]["title"] == "Check")
check("wrong pause id refused", sn_build.respond(job_id, "nope", "continue") is False)
check("answer accepted", sn_build.respond(job_id, job["pause"]["id"], "skip") is True)
t.join(2)
check("build thread receives the answer and resumes", out.get("ans", {}).get("action") == "skip"
      and job["status"] == "running" and job["pause"] is None)

print("No engine available → skipped, never faked")
BROWSER["ok"] = False
b8, _, _, _ = make_builder(PLAN, server_up=False)
b8.step_flows()
check("all skipped", [r["status"] for r in b8.results] == ["skipped", "skipped"])
BROWSER["ok"] = True

print("Stale KB entry is dropped")
before = len(SubflowKB().entries(INSTANCE))
stale = {"subflows": [{"id": "x", "name": "On-Call Engineer Notification", "description": "Notify on-call engineer",
                       "actions": [{"type": "core.updateRecord", "inputs": {"table": "incident"}}],
                       "inputs": [{"name": "incident", "label": "Incident", "type": "String"}]}], "flows": []}
b9, _, _, logs9 = make_builder(stale)
b9.step_flows()
check("stale KB entry removed", any("no longer exists" in m for _, m in logs9), [m for _, m in logs9])

sn_build.time.sleep = _sleep
print(f"==== {len(PASS)}/{len(PASS) + len(FAIL)} passed")
sys.exit(1 if FAIL else 0)
