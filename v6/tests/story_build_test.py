"""
Story -> platform artifacts (Implement flow) against the mock ServiceNow. NOT a real instance.

Start the mock first (see README):  uvicorn fake_sn2:app --port 9443 --ssl-keyfile key.pem --ssl-certfile cert.pem
Then:                               python story_build_test.py

Checks that /snow/stories/approve-and-deploy, for the UserGroupUtils story:
  * creates the tracking record AND the sys_script_include (global scope, real class script)
  * repairs an invalid first AI plan (client callable without AbstractAjaxProcessor) with one extra AI round
  * captures the script include in the update set, adds a work note to the tracking record
  * reuses (never duplicates) the script include when the story is deployed again
  * leaves plain process stories as tracking records only
"""
import asyncio
import json
import os
import sys

os.environ.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost", SNOW_VERIFY_TLS="false",
                  AGENT_JOBS_DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "snow-ai-service"))

import mock_ai  # noqa: F401  (patches groq_client)
import groq_client
import requests
import snow_universal

MOCK = "https://127.0.0.1:9443"
CREDS = {"snow_url": MOCK, "snow_user": "admin", "snow_pass": "pw"}

GOOD_SCRIPT = """var UserGroupUtils = Class.create();
UserGroupUtils.prototype = Object.extendsObject(AbstractAjaxProcessor, {
    isMemberOf: function(userId, groupName) {
        var gr = new GlideRecord('sys_user_grmember');
        gr.addQuery('user', userId || this.getParameter('sysparm_user'));
        gr.addQuery('group.name', groupName || this.getParameter('sysparm_group'));
        gr.setLimit(1);
        gr.query();
        return gr.hasNext() ? 'true' : 'false';
    },
    getDepartment: function(userId) {
        var u = new GlideRecord('sys_user');
        if (!u.get(userId || this.getParameter('sysparm_user'))) return '';
        return u.getDisplayValue('department');
    },
    type: 'UserGroupUtils'
});"""
BAD_SCRIPT = "var UserGroupUtils = Class.create();\nUserGroupUtils.prototype = { type: 'UserGroupUtils' };"
AI_CALLS = []


def story_ai(messages, max_tokens=4096, temperature=0.3, model=None, json_mode=False, vision=False):
    p = " ".join(m["content"] for m in messages if isinstance(m.get("content"), str))
    if "Implement this user story as ServiceNow platform artifacts" not in p:
        return ORIGINAL_CHAT(messages, max_tokens, temperature, model, json_mode, vision)
    AI_CALLS.append(p)
    repaired = "YOUR PREVIOUS PLAN FAILED THESE CHECKS" in p
    return json.dumps({"script_includes": [{"name": "UserGroupUtils", "client_callable": True,
                                            "description": "User department and group validation",
                                            "script": GOOD_SCRIPT if repaired else BAD_SCRIPT}],
                       "business_rules": [], "client_scripts": [], "ui_policies": [],
                       "notes": "Call it from catalog client scripts with GlideAjax('UserGroupUtils')."})


ORIGINAL_CHAT = groq_client.chat
groq_client.chat = story_ai

STORY = {"title": "UserGroupUtils script include", "as_a": "Platform Developer / System Administrator",
         "i_want": "a reusable Script Include named UserGroupUtils",
         "so_that": "I can centralize user department and group validation logic across Business Rules and "
                    "Catalog Client Scripts without duplicating code.",
         "priority": "high", "module": "Platform", "story_type": "story"}

results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))


def db(table):
    return requests.get(f"{MOCK}/__db/{table}", verify=False, timeout=10).json()


def deploy(stories):
    return asyncio.run(snow_universal.approve_and_deploy({**CREDS, "approved": True, "user_stories": stories,
                                                          "update_set_name": "HAI_story_build_test"}))


out = deploy([STORY])
c = (out.get("stories_created") or [{}])[0]
b = c.get("build") or {}
check("tracking record created", bool(c.get("sys_id")), json.dumps(out)[:400])
check("build ran and succeeded", b.get("status") in ("completed", "completed_with_issues"), json.dumps(b)[:600])
check("invalid first plan repaired with one extra AI round", len(AI_CALLS) == 2, f"{len(AI_CALLS)} AI calls")
si = [r for r in db("sys_script_include") if r.get("name") == "UserGroupUtils"]
check("script include exists once", len(si) == 1, f"{len(si)} rows")
if si:
    check("script is the full AbstractAjaxProcessor class", "Object.extendsObject(AbstractAjaxProcessor" in si[0].get("script", ""))
    check("client callable + active", str(si[0].get("client_callable")).lower() == "true" and
          str(si[0].get("active")).lower() == "true", json.dumps(si[0])[:300])
art = [a for a in b.get("artifacts") or [] if a["kind"] == "Script include"]
check("artifact reported as created with a link", bool(art) and art[0]["status"] == "created" and art[0].get("link"),
      json.dumps(art))
check("update set reported", (b.get("update_set") or {}).get("name") == "HAI_story_build_test", json.dumps(b.get("update_set")))
captured = [x for x in db("sys_update_xml") if "UserGroupUtils" in json.dumps(x)]
check("script include captured in sys_update_xml", bool(captured))
check("client-callable ACL note returned", "client_callable_script_include" in (b.get("notes") or ""), b.get("notes"))
track = [r for r in db(c.get("table") or "task") if r.get("sys_id") == c.get("sys_id")]
check("work note added to tracking record", bool(track) and "UserGroupUtils" in json.dumps(track[0]), json.dumps(track)[:300])

AI_CALLS.clear()
again = deploy([STORY])
b2 = (again.get("stories_created") or [{}])[0].get("build") or {}
si2 = [r for r in db("sys_script_include") if r.get("name") == "UserGroupUtils"]
check("re-deploy reuses the script include (no duplicate)", len(si2) == 1 and any(
    a["status"] == "reused" for a in b2.get("artifacts") or []), json.dumps(b2.get("artifacts")))

AI_CALLS.clear()
plain = deploy([{"title": "Route P1 incidents", "i_want": "P1 incidents routed to the on-call group",
                 "priority": "critical", "module": "ITSM", "story_type": "story"}])
pc = (plain.get("stories_created") or [{}])[0]
check("plain process story: tracking record only, no AI build", bool(pc.get("sys_id")) and "build" not in pc
      and not AI_CALLS, json.dumps(pc)[:300])

print(f"\n{sum(results)}/{len(results)} story build checks passed")
sys.exit(0 if all(results) else 1)
