"""
Live check — tests the RUNNING agent against your REAL ServiceNow instance and the REAL Groq AI.

    python live_check.py                 # read-only: changes nothing in ServiceNow
    python live_check.py --write         # also creates test records, verifies them, then deletes them
    python live_check.py --agent http://localhost:8001 --user admin

Start the agent first (start_agent.bat). You are asked for your ServiceNow password (not stored).
Results: printed + tests/live/output/live_report.json; generated documents in tests/live/output/.
"""
import argparse
import getpass
import json
import os
import random
import sys
import time
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings()  # only relevant when SNOW_VERIFY_TLS=false (lab instances)

HERE = Path(__file__).resolve().parent
LIVE = HERE / "live"
OUT = LIVE / "output"
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ap = argparse.ArgumentParser()
ap.add_argument("--agent", default=os.getenv("AGENT_URL", "http://localhost:8001"))
ap.add_argument("--user", default=os.getenv("LIVE_USER"))
ap.add_argument("--write", action="store_true", help="create -> verify -> delete test records, and a test app build")
ap.add_argument("--skip-docs", action="store_true", help="skip the slower AI document generation")
args = ap.parse_args()
A = args.agent.rstrip("/")
S = requests.Session()
S.trust_env = False
if os.getenv("AGENT_API_KEY"):
    S.headers["X-API-Key"] = os.environ["AGENT_API_KEY"]
OUT.mkdir(parents=True, exist_ok=True)
RESULTS = []
CTX = {}


def call(method, path, **kw):
    kw.setdefault("timeout", 900)
    r = S.request(method, A + path, **kw)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r, body


def err(r, body):
    d = (body or {}).get("detail") if isinstance(body, dict) else None
    return f"HTTP {r.status_code}: {d or r.text[:300]}"


def step(name, fn, slow_after=30):
    t = time.time()
    try:
        status, detail = fn()
    except Exception as e:  # a crash in a step is a FAIL, never hidden
        status, detail = "FAIL", f"{type(e).__name__}: {e}"
    secs = round(time.time() - t, 1)
    if status == "PASS" and secs > slow_after:
        detail = f"{detail}  [slow: {secs}s]"
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}[status]
    print(f"{icon} {name:<52} {secs:>6}s  {detail}", flush=True)
    RESULTS.append({"check": name, "status": status, "seconds": secs, "detail": detail})
    return status == "PASS"


def creds():
    return {"snow_url": CTX["url"], "snow_user": CTX["user"], "snow_pass": CTX["pw"]}


# ─────────────────────────── checks ───────────────────────────
def c_agent():
    r, b = call("GET", "/health")
    return ("PASS", "agent is running") if r.ok else ("FAIL", err(r, b))


def c_config():
    r, b = call("GET", "/snow/config")
    if not r.ok:
        return "FAIL", err(r, b)
    CTX["url"] = b.get("instance_url") or CTX.get("url")
    if not CTX.get("url"):
        CTX["url"] = input("ServiceNow instance URL: ").strip()
    return "PASS", f"instance {CTX['url']} · flows {'on' if b.get('flow_server_configured') else 'off (skipped)'}"


def c_ai():
    r, b = call("GET", "/snow/ai-status")
    if r.ok and b.get("ok"):
        return "PASS", f"Groq model {b.get('model')}"
    return "FAIL", f"Groq not working: {(b or {}).get('error') or err(r, b)}"


def c_login_bad():
    r, b = call("POST", "/snow/login", json=dict(creds(), snow_pass=CTX["pw"] + "_wrong"))
    return ("PASS", "wrong password refused") if r.status_code == 401 else ("FAIL", f"expected 401, got {err(r, b)}")


def c_login():
    r, b = call("POST", "/snow/login", json=creds())
    if not r.ok:
        return "FAIL", err(r, b)
    CTX["login"] = b
    w = "; ".join(b.get("warnings") or [])
    return ("WARN" if w else "PASS"), f"{b['user']['name']} · admin={b['is_admin']} · {b.get('build') or ''} {('· ' + w) if w else ''}"


def stories_summary(stories):
    return ", ".join(f"{s['title'][:40]} ({len(s['acceptance_criteria'])} AC)" for s in stories[:8])


def c_extract_notes():
    text = (LIVE / "01_meeting_notes_itsm.txt").read_text(encoding="utf-8")
    r, b = call("POST", "/snow/extract/universal", data={"text": text})
    if not r.ok:
        return "FAIL", err(r, b)
    st = b["user_stories"]
    CTX["notes_stories"] = st
    (OUT / "stories_from_meeting_notes.json").write_text(json.dumps(b, indent=1), encoding="utf-8")
    no_ac = [s["title"] for s in st if not s["acceptance_criteria"]]
    ok = len(st) >= 5 and not no_ac
    return ("PASS" if ok else "WARN"), f"{len(st)} stories (expected ~6+): {stories_summary(st)}" + \
        (f" · WITHOUT criteria: {no_ac}" if no_ac else "")


def c_extract_usecase():
    text = (LIVE / "02_use_case_hr_onboarding.txt").read_text(encoding="utf-8")
    r, b = call("POST", "/snow/extract/universal", data={"text": text})
    if not r.ok:
        return "FAIL", err(r, b)
    st = b["user_stories"]
    (OUT / "stories_from_use_case.json").write_text(json.dumps(b, indent=1), encoding="utf-8")
    return ("PASS" if len(st) >= 4 else "WARN"), f"{len(st)} stories: {stories_summary(st)}"


def c_import(path, n, ac):
    def f():
        r, b = call("POST", "/snow/extract/universal", files={"file": (path.name, path.read_bytes())})
        if not r.ok:
            return "FAIL", err(r, b)
        got_n, got_ac = len(b["user_stories"]), sum(len(s["acceptance_criteria"]) for s in b["user_stories"])
        CTX.setdefault("imports", {})[path.name] = b["user_stories"]
        ok = got_n == n and got_ac == ac and b["source"]["mode"].startswith("direct")
        return ("PASS" if ok else "FAIL"), f"{got_n}/{n} stories, {got_ac}/{ac} criteria, mode: {b['source']['mode']}"
    return f


def payload(stories):
    return [{"id": s.get("id"), "summary": s["title"], "description": s.get("description") or
             f"As a {s['as_a']}, I want {s['i_want']}" + (f", so that {s['so_that']}." if s.get("so_that") else "."),
             "acceptance_criteria": s["acceptance_criteria"]} for s in stories]


def c_download():
    st = CTX.get("notes_stories") or CTX.get("imports", {}).get("03_test_user_stories.xlsx")
    if not st:
        return "SKIP", "no stories from the previous steps"
    r, b = call("POST", "/snow/userstories/excel", json={"project_name": "Live check", "user_stories": payload(st)})
    if not r.ok:
        return "FAIL", err(r, b)
    (OUT / "User_Stories.xlsx").write_bytes(r.content)
    r2, b2 = call("POST", "/snow/documents/export", json={"doc_type": "USER_STORIES", "project_name": "Live check",
                                                          "requirements": "x", "user_stories": payload(st)})
    if not r2.ok:
        return "FAIL", err(r2, b2)
    (OUT / "User_Stories_and_Acceptance_Criteria.docx").write_bytes(r2.content)
    return "PASS", f"User_Stories.xlsx + .docx saved in tests\\live\\output ({len(st)} stories)"


def c_doc(doc_type):
    def f():
        text = (LIVE / "01_meeting_notes_itsm.txt").read_text(encoding="utf-8")
        r, b = call("POST", "/snow/documents/generate", json={"doc_type": doc_type, "project_name": "ITSM Improvement",
                                                              "requirements": text})
        if not r.ok:
            return "FAIL", err(r, b)
        secs = b.get("sections") or []
        empty = [s["heading"] for s in secs if len((s.get("content") or "").strip()) < 80 and not s.get("diagram")]
        diagram = next((s["diagram"] for s in secs if s.get("diagram")), None)
        r2, _ = call("POST", "/snow/documents/download", json={"document": b})
        if r2.ok:
            (OUT / f"{doc_type}.docx").write_bytes(r2.content)
        extra = f" · diagram: {len(diagram['layers'])} layers, {len(diagram['flows'])} flows" if diagram else ""
        if doc_type == "TSD" and not diagram:
            return "FAIL", "TSD has no architecture diagram"
        return ("WARN" if empty else "PASS"), f"{len(secs)} sections{extra}" + \
            (f" · thin sections: {empty}" if empty else "") + f" · saved {doc_type}.docx"
    return f


def c_query():
    r, b = call("POST", "/snow/interactive/query", json={**creds(), "question": "show me the 5 most recent incidents"})
    if not r.ok:
        return "FAIL", err(r, b)
    return "PASS", f"{b['total_found']} records from {b['table']} · query: {b['query'][:60]}"


def c_health():
    r, b = call("POST", "/snow/health-scan", json=creds())
    if not r.ok:
        return "FAIL", err(r, b)
    (OUT / "health_scan.json").write_text(json.dumps(b, indent=1), encoding="utf-8")
    return "PASS", f"score {b['overall_score']} · {b['passed']} passed · {len(b['findings'])} findings · {b['unknown']} unknown"


def c_tools_catalog():
    r, b = call("GET", "/snow/tools/packages")
    if not r.ok:
        return "FAIL", err(r, b)
    return ("PASS" if b["total_tools"] == 522 else "WARN"), f"{b['total_tools']} tools in {len(b['packages'])} bundles"


def c_tool(name, arguments):
    def f():
        r, b = call("POST", "/snow/tools/run", json={**creds(), "name": name, "arguments": arguments})
        if not r.ok:
            return "FAIL", err(r, b)
        res = b["result"]
        msg = res.get("message") if isinstance(res, dict) else ""
        return ("PASS" if b["success"] else "FAIL"), (msg or json.dumps(res)[:120])
    return f


def c_ask():
    r, b = call("POST", "/snow/tools/ask", json={**creds(), "message": "list the 5 most recent problems"})
    if not r.ok:
        return "FAIL", err(r, b)
    if b.get("mode") != "tool":
        return "WARN", f"no tool picked: {b.get('reason')}"
    ok = "result" in b and not b.get("error")
    return ("PASS" if ok else "WARN"), f"picked {b['tool']} {json.dumps(b.get('arguments'))[:80]}" + \
        (f" · error: {b.get('error')}" if b.get("error") else "")


def c_updatesets():
    r, b = call("POST", "/snow/updatesets/list", json=creds())
    if not r.ok:
        return "FAIL", err(r, b)
    CTX["prev_us"], CTX["prev_app"] = b.get("current_update_set"), b.get("current_app")
    return "PASS", f"{len(b['update_sets'])} open update sets · current: {next((u['name'] for u in b['update_sets'] if u['current']), 'Default/unknown')}"


def c_plan():
    st = CTX.get("imports", {}).get("03_test_user_stories.xlsx")
    if not st:
        return "SKIP", "test stories not imported"
    text = "\n\n".join(f"{s['id']} {s['title']}: {s['description']}\nAcceptance Criteria:\n" + "\n".join(s["acceptance_criteria"]) for s in st)
    r, b = call("POST", "/snow/plan", json={**creds(), "requirements": text})
    if not r.ok:
        return "FAIL", err(r, b)
    v = b.get("_validation", {})
    (OUT / "build_plan.json").write_text(json.dumps(b, indent=1), encoding="utf-8")
    return ("PASS" if v.get("valid") else "WARN"), f"counts {v.get('counts')} · issues: {v.get('issues') or 'none'}"


# ── write checks (create -> verify -> delete) ──
def sn_delete(table, sys_id):
    verify = os.getenv("SNOW_VERIFY_TLS", "true").lower() not in ("0", "false", "no")
    r = requests.delete(f"{CTX['url'].rstrip('/')}/api/now/table/{table}/{sys_id}", auth=(CTX["user"], CTX["pw"]),
                        headers={"Accept": "application/json"}, timeout=60, verify=verify)
    return r.status_code in (200, 204)


def w_incident():
    r, b = call("POST", "/snow/tools/run", json={**creds(), "name": "create_incident", "confirm": True,
                                                 "arguments": {"short_description": "HAI live check — safe to delete"}})
    if not r.ok or not b["success"]:
        return "FAIL", err(r, b) if not r.ok else json.dumps(b["result"])[:200]
    res = b["result"]
    sid = res.get("incident_id") or res.get("sys_id")
    num = res.get("incident_number") or res.get("number")
    deleted = sid and sn_delete("incident", sid)
    return ("PASS" if deleted else "WARN"), f"created {num} through the tool library · deleted: {bool(deleted)}"


def w_deploy_story():
    st = (CTX.get("imports", {}).get("03_test_user_stories.xlsx") or [])[:1]
    if not st:
        return "SKIP", "no test story"
    s = dict(st[0], story_type="story")
    r, b = call("POST", "/snow/stories/approve-and-deploy", json={**creds(), "approved": True, "user_stories": [s]})
    if not r.ok:
        return "FAIL", err(r, b)
    c = (b.get("stories_created") or [None])[0]
    if not c:
        return "FAIL", json.dumps(b.get("errors"))[:200]
    deleted = sn_delete(c["table"], c["sys_id"])
    return "PASS", f"created {c['table']} {c['number']} · deleted: {deleted}"


def w_build():
    tag = f"{random.randint(1000, 9999)}"
    plan = {"app_name": "HAI Live Check", "scope_mode": "global",
            "tables": [{"name": f"hai_livecheck_{tag}", "label": f"HAI Live Check {tag}",
                        "fields": [{"name": "note", "label": "Note", "type": "string"},
                                   {"name": "level", "label": "Level", "type": "choice",
                                    "choices": [{"value": "low", "label": "Low"}, {"value": "high", "label": "High"}]}]}],
            "business_rules": [{"name": f"HAI live check {tag}", "table": f"hai_livecheck_{tag}", "when": "before",
                                "insert": True, "script": "if (!current.u_note) current.u_note = 'created by live check';"}]}
    us_name = f"HAI_LIVECHECK_{tag}"
    r, b = call("POST", "/snow/build", json={**creds(), "plan": plan, "update_set_name": us_name,
                                            "stop_on_error": False, "rollback_on_error": True})
    if not r.ok:
        return "FAIL", err(r, b)
    job_id = b["job_id"]
    for _ in range(300):
        _, j = call("GET", f"/snow/build/{job_id}")
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(2)
    (OUT / "build_job.json").write_text(json.dumps(j, indent=1), encoding="utf-8")
    v = j.get("verification") or {}
    created = [x for x in j.get("results", []) if x["status"] == "created" and x.get("sys_id")]
    ok = j["status"] in ("completed", "completed_with_issues") and not v.get("missing") and \
        not [x for x in j.get("results", []) if x["status"] == "failed"]
    detail = f"status {j['status']} · created {len(created)} · captured {v.get('captured_count')} · " \
             f"not captured: {[m['name'] for m in v.get('missing', [])] or 'none'}"
    # clean up: delete created records while the test update set is still current, then the update set itself
    removed = sum(1 for x in reversed(created) if sn_delete(x["table"], x["sys_id"]))
    us = j.get("update_set") or {}
    us_removed = bool(us.get("sys_id")) and sn_delete("sys_update_set", us["sys_id"])
    if CTX.get("prev_us"):
        call("POST", "/snow/updatesets/context", json={**creds(), "scope_mode": "existing",
                                                       "application": CTX.get("prev_app") or "global",
                                                       "update_set_sys_id": CTX["prev_us"]})
    detail += f" · cleanup: {removed}/{len(created)} records + update set {'deleted' if us_removed else 'NOT deleted'}" + \
              (" · your previous update set is current again" if CTX.get("prev_us") else "")
    return ("PASS" if ok else "FAIL"), detail


# ─────────────────────────── run ───────────────────────────
print(f"SoftProdigy live check → agent {A}  ({'WRITE mode: test records are created and deleted' if args.write else 'read-only'})\n")
if not step("Agent reachable", c_agent):
    print("\nStart the agent first: start_agent.bat")
    sys.exit(1)
step("Agent config / instance", c_config)
CTX["user"] = args.user or input(f"ServiceNow username for {CTX['url']}: ").strip()
CTX["pw"] = os.getenv("LIVE_PASS") or getpass.getpass("Password (not shown, not stored): ")
print()
step("Groq AI (hard-coded key)", c_ai, slow_after=15)
step("Login refuses a wrong password", c_login_bad)
if not step("Login with your account", c_login):
    print("\nLogin failed — fix the username/password or instance and run again.")
    sys.exit(1)
step("AI: stories from meeting notes (01)", c_extract_notes, slow_after=60)
step("AI: stories from a use case (02)", c_extract_usecase, slow_after=60)
step("Import test stories exactly (03, 6 stories/28 AC)", c_import(LIVE / "03_test_user_stories.xlsx", 6, 28))
if (HERE / "sample_user_stories.xlsx").exists():
    step("Import your sample exactly (22 stories/342 AC)", c_import(HERE / "sample_user_stories.xlsx", 22, 342))
step("Download User Stories Word + Excel", c_download)
if not args.skip_docs:
    step("AI document: BRD", c_doc("BRD"), slow_after=120)
    step("AI document: TSD with architecture diagram", c_doc("TSD"), slow_after=120)
    step("AI document: Test Plan", c_doc("TEST_PLAN"), slow_after=120)
step("Plain-English query (5 recent incidents)", c_query)
step("Health scan (24 live checks)", c_health, slow_after=90)
step("Tool catalog (522 tools)", c_tools_catalog)
step("Tool: list_incidents", c_tool("list_incidents", {"limit": 3}))
step("Tool: list_users", c_tool("list_users", {"limit": 2}))
step("Tool picker: 'list the 5 most recent problems'", c_ask, slow_after=30)
step("Update sets list", c_updatesets)
step("AI build plan from the test stories", c_plan, slow_after=90)
if args.write:
    print("\n— write checks (records are deleted again) —")
    step("Create + delete an incident via the tools", w_incident)
    step("Deploy + delete a story (approve-and-deploy)", w_deploy_story)
    step("Build test app into its own update set + clean up", w_build, slow_after=180)

npass = sum(r["status"] == "PASS" for r in RESULTS)
nfail = sum(r["status"] == "FAIL" for r in RESULTS)
nwarn = sum(r["status"] == "WARN" for r in RESULTS)
total_s = round(sum(r["seconds"] for r in RESULTS), 1)
print(f"\n══════ {npass} passed · {nwarn} warnings · {nfail} failed · {total_s}s total ══════")
print(f"Report and documents: {OUT}")
(OUT / "live_report.json").write_text(json.dumps({"agent": A, "instance": CTX.get("url"), "write_mode": args.write,
                                                  "results": RESULTS}, indent=1), encoding="utf-8")
sys.exit(1 if nfail else 0)
