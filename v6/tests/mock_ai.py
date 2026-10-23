import json
import groq_client
AI = {"down": False}
def fake_chat(messages, max_tokens=4096, temperature=0.3, model=None, json_mode=False, vision=False):
    if AI["down"]:
        raise groq_client.GroqError("All Groq models failed (simulated outage)")
    p = " ".join(m["content"] if isinstance(m["content"], str) else "" for m in messages)
    if "Create a ServiceNow build plan" in p:
        return json.dumps(PLAN)
    if "Extract every user story" in p:
        return json.dumps({"project_title": "P1 Routing", "project_overview": "Route P1s.", "user_stories": [
            {"title": "Route P1 incidents", "as_a": "service desk manager", "i_want": "P1s routed to on-call",
             "so_that": "SLA is met", "acceptance_criteria": ["Given a P1, When created, Then assigned in 2 min"],
             "priority": "critical", "module": "ITSM", "story_type": "story", "story_points": 5},
            {"title": "VPN outage in Pune", "i_want": "log the VPN outage", "priority": "high", "module": "ITSM",
             "story_type": "incident", "acceptance_criteria": []},
            {"title": "Recurring DB crash", "i_want": "find root cause", "priority": "medium", "story_type": "problem"},
            {"title": "Upgrade firewall", "i_want": "upgrade firewall firmware", "priority": "low", "story_type": "change"}],
            "complete": True})
    if "Pick the ONE ServiceNow tool" in p:
        if "create a problem" in p.lower():
            return json.dumps({"tool": "create_problem", "arguments": {"short_description": "Printers keep failing"},
                               "missing": [], "reason": "User wants a new problem record"})
        if "incidents" in p.lower():
            return json.dumps({"tool": "list_incidents", "arguments": {"limit": 5}, "missing": [], "reason": "List incidents"})
        return json.dumps({"tool": None, "reason": "no match"})
    if "solution architecture" in p and '"layers"' in p:
        return json.dumps({"title": "P1 — Solution Architecture", "layers": [
            {"name": "Users", "components": ["Service desk", "End users"]},
            {"name": "ServiceNow", "components": ["Incident", "Assignment rules", "SLA engine"]},
            {"name": "External", "components": ["On-call tool"]}],
            "flows": [{"from": "End users", "to": "Incident", "label": "raise"}], "description": "How it fits."})
    if "query plan" in p:
        return json.dumps({"table": "incident", "sysparm_query": "priority=1", "sysparm_limit": 10})
    if "Reply with exactly: OK" in p:
        return "OK"
    return "Section text specific to the project.\n\n| A | B |\n|---|---|\n| 1 | 2 |"
groq_client.chat = fake_chat
groq_client.list_available_models = lambda force=False: []
groq_client.transcribe = lambda b, name, language="en": "In the meeting we agreed: route P1 incidents to on-call within 2 minutes."
groq_client.describe_image = lambda b64, mime, instr, max_tokens=3000: "Whiteboard: 1. Route P1 2. Notify manager"

PLAN = {
    "app_name": "P1 Router", "app_scope": "p1router", "scope_mode": "scoped", "description": "Routes P1 incidents",
    "roles": [{"name": "admin", "contains": ["user"]}, {"name": "user"}],
    "tables": [{"name": "route_rule", "label": "Route Rule", "extends": "task", "number_prefix": "RR",
                "fields": [{"name": "on_call_group", "label": "On-call group", "type": "reference", "reference": "sys_user_group"},
                           {"name": "level", "label": "Level", "type": "choice",
                            "choices": [{"value": "p1", "label": "P1"}, {"value": "p2", "label": "P2"}]},
                           {"name": "minutes", "type": "integer", "label": "Minutes"}]}],
    "acls": [{"table": "route_rule", "operations": ["read"], "roles": ["user"]}],
    "business_rules": [{"name": "Default minutes", "table": "route_rule", "when": "before", "insert": True,
                        "script": "if (!current.minutes) current.minutes = 2;\ngs.log('set');"}],
    "client_scripts": [{"name": "Level hint", "table": "route_rule", "type": "onChange", "field": "level",
                        "script": "g_form.showFieldMsg('level','Check group','info');"}],
    "ui_policies": [{"short_description": "Group mandatory", "table": "route_rule",
                     "actions": [{"field": "on_call_group", "mandatory": True}]}],
    "notifications": [{"name": "Rule created", "table": "route_rule", "recipient_fields": ["opened_by"],
                       "subject": "New rule", "message": "A rule was created"}],
    "flows": [{"name": "Notify on rule", "trigger": {"type": "record.created", "table": "route_rule"},
               "actions": [{"id": "log", "type": "core.log", "inputs": {"log_message": "x"}}]}],
}

