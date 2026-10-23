"""
Build-plan flows/subflows -> Flow Server (ServiceNow SDK) request specs.

  subflow_spec(subflow, resolve)              -> body["subflowSpec"] for POST /deploy-subflow
  flow_spec(flow, trigger_table, call, ...)   -> body["flowSpec"]    for POST /deploy-flow

Rules that keep several flows/subflows of one application buildable together:
  * every action id is prefixed with its flow/subflow id (the SDK's Now.ID keys are per application), and
    {{action.<id>.…}} data pills are rewritten to match;
  * inside a subflow there is no trigger: record targets point at the subflow's record input instead;
  * approvers given as names/emails are resolved to sys_ids by the caller (`resolve`).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

PILL = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
IO_TYPES = {"string": "string", "integer": "integer", "int": "integer", "number": "integer",
            "true/false": "boolean", "boolean": "boolean", "bool": "boolean"}
RECORD_INPUT_HINTS = ("record_id", "sys_id", "record", "record_sys_id")

# resolve(kind, value) -> sys_id or "" ; kind is "user" or "group"
Resolver = Callable[[str, str], str]


def _slug(v: str) -> str:
    v = re.sub(r"[^a-z0-9_]", "_", (v or "").strip().lower())
    return re.sub(r"_+", "_", v).strip("_")


def io_type(t: str) -> str:
    return IO_TYPES.get(str(t or "string").strip().lower(), "string")


def record_input(subflow: dict) -> str:
    """Name of the subflow input that carries the record sys_id ('' when there is none)."""
    names = [i["name"] for i in subflow.get("inputs") or []]
    for hint in RECORD_INPUT_HINTS:
        if hint in names:
            return hint
    for n in names:
        if n.endswith(("_sys_id", "_id")):
            return n
    return ""


def human_to_pill(value: Any) -> Any:
    """'Trigger > Incident > Sys ID' / 'trigger.current.number' -> '{{trigger.current.sys_id}}' etc."""
    if not isinstance(value, str) or PILL.search(value):
        return value
    v = value.strip()
    if re.match(r"^trigger(\s*[>.]|$)", v, re.I):
        # Trigger > Incident > Number | trigger.current.number | Trigger > Incident (the record itself)
        parts = [p.strip() for p in re.split(r"\s*[>.]\s*", v) if p.strip()]
        field = parts[-1] if len(parts) >= 3 else "sys_id"
        if field.lower() in ("current", "record") or field.lower().replace(" ", "") in ("sysid", "sys_id"):
            field = "sys_id"
        return "{{trigger.current." + _slug(field) + "}}"
    return value


def _rewrite(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return PILL.sub(lambda m: "{{" + fn(m.group(1).strip()) + "}}", value)
    if isinstance(value, list):
        return [_rewrite(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite(v, fn) for k, v in value.items()}
    return value


def _prefix_actions(owner: str, actions: List[dict]) -> List[dict]:
    ids = {a["id"]: f"{owner}__{a['id']}" for a in actions}

    def fix(path: str) -> str:
        parts = path.split(".")
        if parts[0] == "action" and len(parts) > 1 and parts[1] in ids:
            parts[1] = ids[parts[1]]
        return ".".join(parts)

    return [{"id": ids[a["id"]], "type": a["type"], "inputs": _rewrite(a.get("inputs") or {}, fix)} for a in actions]


def _first(inputs: dict, *keys: str) -> Any:
    for k in keys:
        if inputs.get(k) not in (None, "", [], {}):
            return inputs[k]
    return None


def _approval_conditions(approvers: Any, resolve: Optional[Resolver]) -> Optional[dict]:
    if isinstance(approvers, dict):          # already in Flow Server format
        return approvers
    names = approvers if isinstance(approvers, list) else re.split(r"[,;]", str(approvers or ""))
    users, groups = [], []
    for n in (x.strip() for x in names if str(x).strip()):
        if re.fullmatch(r"[0-9a-f]{32}", n):
            users.append(n)
            continue
        sid = resolve("group", n) if resolve else ""
        if sid:
            groups.append(sid)
            continue
        sid = resolve("user", n) if resolve else ""
        if sid:
            users.append(sid)
    if not users and not groups:
        return None
    return {"conditionType": "OR", "ruleSets": [{"action": "ApprovesRejects", "conditionType": "AND",
                                                 "rules": [[{"ruleType": "Any", "users": users, "groups": groups,
                                                             "manual": False}]]}]}


def _action(a: dict, *, table: str, record: str, resolve: Optional[Resolver]) -> dict:
    """Fill the inputs the Flow Server needs that a build plan usually leaves out."""
    t, inp = a["type"], dict(a.get("inputs") or {})
    if t == "core.log":
        inp.setdefault("log_message", _first(inp, "message", "log_message") or "Flow executed")
        inp.pop("message", None)
        inp.setdefault("log_level", "info")
    elif t == "core.sendEmail":
        inp.setdefault("table_name", _first(inp, "table", "table_name") or table)
        inp.pop("table", None)
        inp.setdefault("record", record)
        if inp.get("body") is None and inp.get("ah_body") is None:
            inp["body"] = _first(inp, "message", "html") or str(_first(inp, "subject") or "")
    elif t in ("core.updateRecord", "core.deleteRecord"):
        inp.setdefault("table", _first(inp, "table", "table_name") or table)
        inp.setdefault("record", record)
    elif t == "core.createRecord":
        inp.setdefault("table_name", _first(inp, "table", "table_name") or table)
        inp.pop("table", None)
        if "values" not in inp:
            inp["values"] = _first(inp, "fields", "field_values") or {}
            inp.pop("fields", None)
            inp.pop("field_values", None)
    elif t == "core.createTask":
        fv = dict(_first(inp, "field_values", "fields", "values") or {})
        for k in ("short_description", "description", "assigned_to", "assignment_group", "priority"):
            if inp.get(k) not in (None, ""):
                fv.setdefault(k, inp.pop(k))
        if record and "parent" not in fv:
            fv["parent"] = record
        inp = {"task_table": _first(inp, "task_table", "table", "table_name") or "task",
               "wait": bool(inp.get("wait", False)), "field_values": fv}
    elif t == "core.askForApproval":
        cond = _approval_conditions(_first(inp, "approval_conditions", "approvers", "approver", "rules"), resolve)
        for k in ("approvers", "approver", "rules"):
            inp.pop(k, None)
        if cond:
            inp["approval_conditions"] = cond
        inp.setdefault("table", _first(inp, "table", "table_name") or table)
        inp.setdefault("record", record)
        inp.setdefault("approval_field", "approval")
        inp.setdefault("journal_field", "work_notes")
        inp.pop("due_date_offset", None)
    return {"id": a["id"], "type": t, "inputs": inp}


def subflow_spec(sf: dict, resolve: Optional[Resolver] = None, table: str = "") -> dict:
    rec_in = record_input(sf)
    record = "{{input." + rec_in + "}}" if rec_in else ""

    def no_trigger(path: str) -> str:
        # a subflow has no trigger: the record it works on arrives as an input
        if path.startswith("trigger.") and rec_in and path.endswith("sys_id"):
            return "input." + rec_in
        return path

    actions = [_action({**a, "inputs": _rewrite(a.get("inputs") or {}, no_trigger)},
                       table=table, record=record, resolve=resolve)
               for a in _prefix_actions(sf["id"], sf["actions"])]
    spec = {"id": sf["id"], "name": sf["name"], "description": sf.get("description") or "",
            "runAs": "system",
            "inputs": {i["name"]: {"type": io_type(i.get("type")), "label": i.get("label") or i["name"],
                                   "mandatory": bool(i.get("mandatory"))} for i in sf.get("inputs") or []},
            "outputs": {o["name"]: {"type": io_type(o.get("type")), "label": o.get("label") or o["name"]}
                        for o in sf.get("outputs") or []},
            "actions": actions}
    values = sf.get("output_values") or {}
    if values:
        spec["outputValues"] = {k: v for k, v in values.items() if k in spec["outputs"]}
    return spec


def subflow_call_inputs(flow: dict, subflow: Optional[dict]) -> Dict[str, Any]:
    """Values passed to the subflow; record-like inputs default to the trigger record."""
    given = {k: human_to_pill(v) for k, v in (flow.get("subflow_inputs") or {}).items()}
    if subflow:
        for i in subflow.get("inputs") or []:
            if i["name"] not in given and i["name"] == record_input(subflow):
                given[i["name"]] = "{{trigger.current.sys_id}}"
    return given


def flow_spec(flow: dict, trigger_table: str, subflow_sys_id: str = "", subflow: Optional[dict] = None,
              resolve: Optional[Resolver] = None) -> dict:
    actions = [_action(a, table=trigger_table, record="{{trigger.current.sys_id}}", resolve=resolve)
               for a in _prefix_actions(flow["id"], flow["actions"])]
    if subflow_sys_id:
        actions.append({"id": f"{flow['id']}__call_subflow", "type": "subflow.call",
                        "inputs": {"subflow_sys_id": subflow_sys_id,
                                   "subflow_inputs": subflow_call_inputs(flow, subflow),
                                   "wait_for_completion": True}})
    return {"id": flow["id"], "name": flow["name"], "description": flow.get("description") or "",
            "runAs": flow.get("runAs") if flow.get("runAs") in ("system", "user") else "system",
            "trigger": {"type": flow["trigger"]["type"], "table": trigger_table,
                        "condition": flow["trigger"].get("condition") or ""},
            "actions": actions}
