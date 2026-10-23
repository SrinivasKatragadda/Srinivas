"""
SoftProdigy Snow Agent — build the platform artifacts a single user story asks for.

Used by POST /snow/stories/approve-and-deploy: after the story's tracking record (rm_story / task) is created,
a story that describes platform code (script include, business rule, client script, UI policy) is turned into
an artifacts-only build plan by the AI and built with the regular sn_build.Builder in the global scope, on
existing tables only. A single story never creates tables, roles, ACLs or flows — those belong in Build App.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import groq_client
import sn_build
from sn_client import SNClient, SNError

logger = logging.getLogger("story_build")

ARTIFACT_KINDS = ("script_includes", "business_rules", "client_scripts", "ui_policies")

# A story is built only when it names a platform artifact (keeps plain ITSM / process stories as tracking only).
_BUILDABLE = re.compile(
    r"script[\s_-]*include|business[\s_-]*rule|client[\s_-]*script|ui[\s_-]*polic|glide[\s_-]*ajax|"
    r"abstractajaxprocessor|class\.create|server[\s-]*side (?:script|logic|function|class)|"
    r"reusable (?:server|script|function|class|utility)|utility class|\bon(?:load|change|submit)\b",
    re.I,
)

_SYSTEM = ("You are a senior ServiceNow platform developer. You implement one user story with the smallest set of "
           "platform artifacts, with complete, production-quality scripts. Respond with JSON only.")

_PROMPT = """Implement this user story as ServiceNow platform artifacts.

USER STORY:
{story}

Constraints:
- Build ONLY what the story and its acceptance criteria ask for. Allowed artifact lists: script_includes,
  business_rules, client_scripts, ui_policies. Return an empty list for kinds the story does not need.
- Artifacts are built in the GLOBAL scope on EXISTING tables (incident, task, sys_user, sys_user_group,
  sc_req_item, change_request, ...). Never invent tables, fields, roles, ACLs or flows.
- script_includes: "name" is the exact class name from the story (PascalCase, no spaces). "script" is the FULL
  class and must start with `var <name> = Class.create();`. When client scripts call it, the prototype is
  `<name>.prototype = Object.extendsObject(AbstractAjaxProcessor, {{ ... }});`. Always include `type: '<name>'`. Implement every method the story describes with real GlideRecord /
  GlideAggregate logic, null checks and boolean/string return values — no TODOs or placeholders.
  Set "client_callable": true only when client-side code must call it via GlideAjax; then each AJAX method reads
  its input with this.getParameter('sysparm_<name>') and returns a string. Keep plain server methods callable
  from business rules with normal arguments too.
- business_rules: "script" is only the BODY (it is wrapped in executeRule). Use GlideRecord/gs; never call
  current.update() in before/after rules. Give "table", "when" (before|after|async|display) and insert/update/delete.
- client_scripts: "type" onLoad|onChange|onSubmit (onChange needs "field"); "script" is only the BODY using g_form;
  never GlideRecord or gs — call a client-callable script include with GlideAjax instead.
- ui_policies: {{"short_description", "table", "conditions" (encoded query), "actions": [{{"field", "mandatory",
  "visible", "read_only"}}]}}.
- Catalog client scripts and catalog UI policies are NOT built here: mention them in "notes" instead.
{feedback}
Return JSON:
{{
  "script_includes": [{{"name": "IncidentUtils", "client_callable": false, "description": "...",
                       "script": "var IncidentUtils = Class.create();\\nIncidentUtils.prototype = {{\\n    initialize: function() {{}},\\n    isP1: function(incidentId) {{\\n        var gr = new GlideRecord('incident');\\n        return gr.get(incidentId) && gr.getValue('priority') == '1';\\n    }},\\n    type: 'IncidentUtils'\\n}};"}}],
  "business_rules": [],
  "client_scripts": [],
  "ui_policies": [],
  "notes": "anything the developer must still do by hand"
}}"""


def is_buildable(story: Dict[str, Any]) -> bool:
    text = " ".join(str(story.get(k) or "") for k in ("title", "i_want", "so_that", "description"))
    text += " " + " ".join(str(c) for c in story.get("acceptance_criteria") or [])
    return bool(_BUILDABLE.search(text))


def story_text(story: Dict[str, Any]) -> str:
    lines = [f"Title: {story.get('title', '')}",
             f"As a {story.get('as_a') or 'user'}, I want {story.get('i_want') or story.get('title', '')}"
             + (f", so that {story['so_that']}" if story.get("so_that") else "") + "."]
    if story.get("description"):
        lines.append(f"Description: {story['description']}")
    ac = story.get("acceptance_criteria") or []
    if ac:
        lines.append("Acceptance criteria:\n" + "\n".join(f"- {c}" for c in ac))
    return "\n".join(lines)


def generate_plan(story: Dict[str, Any], issues: Optional[List[str]] = None,
                  previous: Optional[dict] = None) -> dict:
    """Ask the AI for an artifacts-only plan. With `issues`, ask it to fix its previous plan."""
    feedback = ""
    if issues:
        feedback = ("\nYOUR PREVIOUS PLAN FAILED THESE CHECKS — return a corrected plan:\n"
                    + "\n".join(f"- {i}" for i in issues)
                    + "\nPrevious plan:\n" + json.dumps(previous or {}, indent=1)[:6000] + "\n")
    data = groq_client.generate_json(_PROMPT.format(story=story_text(story)[:6000], feedback=feedback),
                                     system=_SYSTEM, max_tokens=7000)
    if not isinstance(data, dict):
        raise ValueError("AI returned no plan object")
    return data


def to_build_plan(ai_plan: dict, title: str) -> dict:
    """Force the plan into artifacts-only, global-scope shape and normalise it like Build App plans."""
    plan = {k: ai_plan.get(k) or [] for k in ARTIFACT_KINDS}
    plan.update(app_name=f"Story {title}"[:80], scope_mode="global", tables=[], roles=[], acls=[],
                notifications=[], subflows=[], flows=[])
    return sn_build.normalize_plan(plan)


def _check(plan: dict) -> List[str]:
    builder = sn_build.Builder(None, plan, {}, lambda *a: None)
    issues = builder.validate()
    if not any(plan[k] for k in ARTIFACT_KINDS):
        issues.append("The plan contains no artifacts, but the story asks for platform code")
    return issues


def build_story_artifacts(sn: SNClient, story: Dict[str, Any], update_set_name: str = "") -> Dict[str, Any]:
    """Generate, validate (one AI repair round) and build the artifacts for one story. Never raises."""
    title = story.get("title") or "story"
    steps: List[Dict[str, Any]] = []

    def log(msg: str, status: str, data: dict):
        steps.append({"msg": msg, "status": status})
        logger.info(f"[story build] {msg}")

    try:
        ai_plan = generate_plan(story)
        plan = to_build_plan(ai_plan, title)
        issues = _check(plan)
        if issues:
            log(f"Plan needed fixes: {'; '.join(issues)}", "warning", {})
            ai_plan = generate_plan(story, issues, ai_plan)
            plan = to_build_plan(ai_plan, title)
            issues = _check(plan)
        if issues:
            return {"status": "failed", "error": "The generated artifacts failed validation", "issues": issues,
                    "artifacts": [], "notes": ai_plan.get("notes") or "", "steps": steps}
    except (groq_client.GroqError, ValueError) as e:
        return {"status": "failed", "error": f"AI could not generate the artifacts: {e}", "artifacts": [],
                "steps": steps}

    opts = {"update_set_name": update_set_name or f"HAI_Implement_{datetime.now().strftime('%Y%m%d')}",
            "update_set_description": "Artifacts built from user stories by SoftProdigy Snow Agent",
            "stop_on_error": False, "rollback_on_error": False, "restore_previous_context": True}
    builder = sn_build.Builder(sn, plan, opts, log)
    try:
        result = builder.run()
    except Exception as e:  # unexpected bug — report it, the tracking record already exists
        logger.exception("story build crashed")
        result = {"status": "failed", "error": f"internal error: {e}"}

    artifacts = []
    for r in builder.results:
        if r["kind"] == "Application":
            continue
        item = {k: r.get(k) for k in ("kind", "name", "status", "message", "table", "sys_id")}
        if item["sys_id"] and item["table"]:
            item["link"] = sn.link(item["table"], item["sys_id"])
        artifacts.append(item)
    if not artifacts and result.get("status") != "failed":
        result["status"] = "failed"
        result["error"] = result.get("error") or "Nothing was built"

    notes = [ai_plan.get("notes")] if ai_plan.get("notes") else []
    if any(s["client_callable"] for s in plan["script_includes"]):
        notes.append("Client-callable script include: if GlideAjax calls are rejected for non-admin users, add an "
                     "ACL of type client_callable_script_include that grants the required role.")
    us = (builder.ctx or {}).get("update_set") or {}
    return {"status": result.get("status"), "error": result.get("error"), "artifacts": artifacts,
            "update_set": {"name": us.get("name"), "sys_id": us.get("sys_id"), "link": us.get("link")} if us else None,
            "verification": result.get("verification") or {}, "notes": " ".join(n for n in notes if n),
            "steps": steps}


def work_note(build: Dict[str, Any]) -> str:
    """Text added to the story's tracking record so the requirement links to what was built."""
    lines = [f"[SoftProdigy Snow Agent] Build {build.get('status')}:"]
    for a in build.get("artifacts") or []:
        lines.append(f"- {a['kind']} '{a['name']}': {a['status']}" + (f" ({a['message']})" if a.get("message") else ""))
    if build.get("update_set"):
        lines.append(f"Update set: {build['update_set'].get('name')}")
    if build.get("error"):
        lines.append(f"Error: {build['error']}")
    if build.get("notes"):
        lines.append(f"Notes: {build['notes']}")
    return "\n".join(lines)
