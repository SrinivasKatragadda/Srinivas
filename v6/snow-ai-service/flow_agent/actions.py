"""Mapping between build-plan flow specs (core.* actions, record.* triggers) and Flow Designer UI labels."""

from __future__ import annotations

from typing import Any, Dict, List

# plan action type -> label shown in the Flow Designer action picker
CORE_TO_UI = {
    "core.log": "Log",
    "core.sendemail": "Send Email",
    "core.sendnotification": "Send Notification",
    "core.createtask": "Create Task",
    "core.createrecord": "Create Record",
    "core.updaterecord": "Update Record",
    "core.deleterecord": "Delete Record",
    "core.lookuprecord": "Look Up Record",
    "core.lookuprecords": "Look Up Records",
    "core.askforapproval": "Ask For Approval",
    "core.waitforcondition": "Wait For Condition",
    "core.script": "Script",
}

# what the user must check in each action panel before it is saved (from now2.MANDATORY_FIELDS)
MANDATORY_FIELDS = {
    "Log": ["Message"],
    "Send Email": ["To (recipient)", "Subject"],
    "Send Notification": ["Notification"],
    "Create Task": ["Task table", "Short description"],
    "Create Record": ["Table", "Fields"],
    "Update Record": ["Record (drag the data pill, e.g. Trigger > Record)", "Table", "Fields to update"],
    "Delete Record": ["Record (data pill)"],
    "Look Up Record": ["Table", "Conditions"],
    "Look Up Records": ["Table", "Conditions"],
    "Ask For Approval": ["Record (data pill)", "Table", "Approvers / rules"],
    "Wait For Condition": ["Record (data pill)", "Condition"],
    "Script": ["Script body"],
}

TRIGGER_EVENTS = {
    "record.created": "Created",
    "record.updated": "Updated",
    "record.created_or_updated": "Created or Updated",
}


def ui_action_name(core_type: str) -> str:
    t = (core_type or "").strip()
    if not t.lower().startswith("core."):
        return t  # already a UI label (e.g. entries imported from now3's library)
    return CORE_TO_UI.get(t.lower(), t.split(".", 1)[1])


def inp(action: dict, *keys: str, default: Any = "") -> Any:
    """First non-empty input value among several accepted spellings."""
    inputs = action.get("inputs") or {}
    for k in keys:
        v = inputs.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def fields_of(action: dict) -> Dict[str, str]:
    v = inp(action, "fields", "values", "field_values", default={})
    return {str(k): str(x) for k, x in v.items()} if isinstance(v, dict) else {}


def describe(action: dict) -> Dict[str, Any]:
    """Values the agent will try to pre-fill for one action (shown to the user in the pause panel)."""
    ui = ui_action_name(action.get("type", ""))
    pre: Dict[str, Any] = {}
    if ui == "Log":
        pre["Message"] = inp(action, "log_message", "message", default="Flow executed")
    elif ui in ("Send Email", "Send Notification"):
        pre.update({"To": inp(action, "to", "recipients", "recipient"), "Subject": inp(action, "subject"),
                    "Body": inp(action, "body", "html", "message")})
    elif ui == "Create Task":
        pre.update({"Table": inp(action, "table", "task_table", default="task"),
                    "Short description": inp(action, "short_description", "description")})
    elif ui in ("Create Record", "Update Record", "Delete Record", "Look Up Record", "Look Up Records"):
        pre.update({"Table": inp(action, "table", "table_name"), "Fields": fields_of(action),
                    "Conditions": inp(action, "conditions", "condition")})
    elif ui == "Ask For Approval":
        pre.update({"Table": inp(action, "table"), "Approvers": inp(action, "approvers", "approver", "rules"),
                    "Due date": inp(action, "due_date", "due_date_offset")})
    elif ui == "Script":
        pre["Script"] = inp(action, "script")
    return {k: v for k, v in pre.items() if v not in ("", None, {}, [])}


def action_summary(actions: List[dict]) -> List[str]:
    return [ui_action_name(a.get("type", "")) for a in actions or []]
