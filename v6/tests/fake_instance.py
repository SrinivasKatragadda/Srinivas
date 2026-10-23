"""
In-memory ServiceNow instance for the requirement-pipeline tests. NOT a real instance.

FakeInstance subclasses the agent's real SNClient and replaces only request(), so every SNClient method, sn_build's
Builder, update-set context handling, rollback and the pipeline run their real code against this data.

Each test builds its own instance, so scenarios can differ in plugins (missing tables), schemas (missing columns),
permissions (403 on writes) and stored-value behaviour without touching the shared HTTPS mock (fake_sn2).
"""
import copy
import re
import uuid
from typing import Any, Dict, List, Optional, Set

import sn_client as snc
from sn_client import SNClient, SNError

ADMIN = "6816f79cc0a8016401c5a33be04be441"
METADATA = {"sys_script", "sys_script_client", "sys_ui_policy", "sys_ui_policy_action", "sys_data_policy2",
            "sys_data_policy_rule", "sysrule_assignment", "sys_script_include", "dl_u_priority"}

MECH_COLUMNS = {
    "sys_script": ["name", "collection", "when", "action_insert", "action_update", "action_delete", "action_query",
                   "filter_condition", "condition", "script", "active", "advanced", "order", "description"],
    "sys_script_client": ["name", "table", "type", "field", "script", "active", "ui_type", "description"],
    "sys_ui_policy": ["short_description", "table", "conditions", "active", "on_load", "reverse_if_false", "global",
                      "inherit", "run_scripts", "script_true", "script_false"],
    "sys_ui_policy_action": ["ui_policy", "table", "field", "mandatory", "visible", "disabled"],
    "sys_data_policy2": ["short_description", "model_table", "conditions", "active", "enforce_ui", "apply_import_set",
                         "apply_soap", "reverse_if_false", "description"],
    "sys_data_policy_rule": ["sys_data_policy", "table", "field", "mandatory", "disabled"],
    "sysrule_assignment": ["name", "table", "condition", "group", "user", "order", "active"],
    "sys_script_include": ["name", "api_name", "script", "client_callable", "active", "access", "description"],
    "dl_u_priority": ["impact", "urgency", "priority"],
}
TASK_FIELDS = {"short_description": "string", "description": "string", "priority": "integer", "impact": "integer",
               "urgency": "integer", "assignment_group": ("reference", "sys_user_group"),
               "assigned_to": ("reference", "sys_user"), "state": "integer", "active": "boolean"}
INCIDENT_FIELDS = {"category": "choice", "caller_id": ("reference", "sys_user"), "subcategory": "string"}


def sid() -> str:
    return uuid.uuid4().hex


class FakeInstance(SNClient):
    def __init__(self, url: str = "https://dev-a.service-now.com", *, missing_tables: Set[str] = frozenset(),
                 missing_columns: Optional[Dict[str, List[str]]] = None, forbid_writes: Set[str] = frozenset(),
                 store_override: Optional[Dict[str, Dict[str, Any]]] = None, drop_incident_fields: Set[str] = frozenset()):
        super().__init__(url, "admin", "pw")
        self.missing_tables = set(missing_tables)
        self.forbid_writes = set(forbid_writes)          # POST/PATCH on these tables -> 403
        self.store_override = store_override or {}       # {table: {field: value}} stored instead of what was sent
        self.log: List[dict] = []
        self.db: Dict[str, List[dict]] = {}
        self._seed(missing_columns or {}, set(drop_incident_fields))

    # -- seed data ---------------------------------------------------------------------------------------------------
    def _seed(self, missing_columns, drop_fields):
        db = self.db
        tables = {"task": None, "incident": "task", "sys_user": None, "sys_user_group": None, "sys_update_set": None,
                  "sys_user_preference": None, "sys_update_xml": None, "sys_dictionary": None, "sys_choice": None,
                  "sys_user_has_role": None, "sys_properties": None, "sys_scope": None, "sys_db_object": None}
        tables.update({t: None for t in MECH_COLUMNS})
        ids = {t: sid() for t in tables}
        db["sys_db_object"] = [{"sys_id": ids[t], "name": t, "super_class": ids[p] if p else ""}
                               for t, p in tables.items() if t not in self.missing_tables]
        db["sys_user"] = [{"sys_id": ADMIN, "user_name": "admin", "name": "System Administrator", "active": "true"}]
        db["sys_user_has_role"] = [{"sys_id": sid(), "user": ADMIN, "role.name": "admin"}]
        db["sys_properties"] = [{"sys_id": sid(), "name": "glide.buildname", "value": "Xanadu"}]
        db["sys_scope"] = [{"sys_id": "global", "name": "Global", "scope": "global"}]
        db["sys_update_set"] = [{"sys_id": "default_global", "name": "Default", "state": "in progress",
                                 "application": "global"}]
        db["sys_user_preference"], db["sys_update_xml"] = [], []
        self.groups = {"Network Support": sid(), "Service Desk": sid(), "Hardware": sid()}
        db["sys_user_group"] = [{"sys_id": v, "name": k, "active": "true"} for k, v in self.groups.items()]
        dic = []
        for name, fields in (("task", TASK_FIELDS), ("incident", INCIDENT_FIELDS)):
            for el, typ in fields.items():
                if name == "incident" and el in drop_fields or name == "task" and el in drop_fields:
                    continue
                t, ref = (typ if isinstance(typ, tuple) else (typ, ""))
                dic.append({"sys_id": sid(), "name": name, "element": el, "internal_type": t, "reference": ref,
                            "column_label": el.replace("_", " ").title()})
        for table, cols in MECH_COLUMNS.items():
            for c in cols:
                if c not in (missing_columns.get(table) or []):
                    dic.append({"sys_id": sid(), "name": table, "element": c, "internal_type": "string",
                                "reference": "", "column_label": c})
        db["sys_dictionary"] = dic
        db["sys_choice"] = ([{"sys_id": sid(), "name": "incident", "element": "category", "value": v, "label": v.title(),
                              "inactive": "false"} for v in ("network", "software", "hardware", "inquiry")] +
                            [{"sys_id": sid(), "name": "task", "element": f, "value": str(i), "label": str(i),
                              "inactive": "false"} for f in ("priority", "impact", "urgency") for i in range(1, 6)])
        for t in MECH_COLUMNS:
            db.setdefault(t, [])
        db["dl_u_priority"] = [{"sys_id": sid(), "impact": str(i), "urgency": str(u), "priority": str(min(5, i + u - 1)),
                                "sys_updated_on": "2026-01-01"} for i in (1, 2, 3) for u in (1, 2, 3)]
        db["incident"] = []

    # -- helpers for tests ----------------------------------------------------------------------------------------------
    def add(self, _t: str, **fields) -> dict:
        rec = {"sys_id": sid(), "sys_updated_on": "2026-01-01 00:00:00", **{k: (v if not isinstance(v, bool) else
                                                                               ("true" if v else "false"))
                                                                           for k, v in fields.items()}}
        self.db.setdefault(_t, []).append(rec)
        return rec

    def rows(self, _t: str, **match) -> List[dict]:
        return [r for r in self.db.get(_t, []) if all(str(r.get(k)) == str(v) for k, v in match.items())]

    def writes(self) -> List[dict]:
        return [l for l in self.log if l["method"] in ("POST", "PATCH", "DELETE") and l["table"] in METADATA]

    def reset_session(self):
        pass

    # -- query evaluation ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _cond(rec: dict, cond: str) -> bool:
        if not cond or cond.startswith("ORDERBY") or cond == "EQ":
            return True
        for op in ("ISNOTEMPTY", "ISEMPTY"):
            if cond.endswith(op):
                v = str(rec.get(cond[: -len(op)], "") or "")
                return bool(v) if op == "ISNOTEMPTY" else not v
        m = re.match(r"^([\w.]+?)(STARTSWITH|LIKE|!=|IN|=)(.*)$", cond)
        if not m:
            return True
        f, op, val = m.groups()
        v = rec.get(f, "")
        v = ("true" if v else "false") if isinstance(v, bool) else str(v if v is not None else "")
        if op == "=":
            return v == val
        if op == "!=":
            return v != val
        if op == "IN":
            return v in val.split(",")
        if op == "LIKE":
            return val.lower() in v.lower()
        return v.startswith(val)

    def _match(self, rec: dict, q: str) -> bool:
        parts = [p for p in (q or "").split("^") if p]
        i = 0
        while i < len(parts):
            grp = [parts[i]]
            while i + 1 < len(parts) and parts[i + 1].startswith("OR") and not parts[i + 1].startswith("ORDERBY"):
                grp.append(parts[i + 1][2:])
                i += 1
            if not any(self._cond(rec, c) for c in grp):
                return False
            i += 1
        return True

    def _exists(self, table: str) -> bool:
        return table not in self.missing_tables and (table in self.db or any(
            t["name"] == table for t in self.db["sys_db_object"]))

    def _capture(self, table: str, rec: dict):
        if table not in METADATA:
            return
        pref = next((p for p in self.db["sys_user_preference"] if p["user"] == ADMIN and p["name"] == "sys_update_set"),
                    None)
        us = pref["value"] if pref else "default_global"
        key = f"{table}_{rec['sys_id']}"
        self.db["sys_update_xml"] = [x for x in self.db["sys_update_xml"]
                                     if not (x["name"] == key and x["update_set"] == us)]
        self.db["sys_update_xml"].append({"sys_id": sid(), "name": key, "type": table, "target_name": key,
                                          "action": "INSERT_OR_UPDATE", "update_set": us, "sys_created_on": "now"})

    # -- the only network method, replaced ------------------------------------------------------------------------------
    def request(self, method: str, path: str, params: Optional[dict] = None, json: Optional[dict] = None) -> dict:
        self.calls += 1
        params = params or {}
        m = re.match(r"^/api/now/(table|stats)/([\w]+)(?:/(\w+))?$", path)
        if not m:
            raise SNError(400, "not found", url=path)
        kind, table, rid = m.groups()
        self.log.append({"method": method, "table": table, "id": rid, "params": dict(params),
                         "body": copy.deepcopy(json)})
        if not self._exists(table):
            raise SNError(400, f"Invalid table {table}", url=path)
        if kind == "stats":
            n = sum(1 for r in self.db.get(table, []) if self._match(r, params.get("sysparm_query", "")))
            return {"result": {"stats": {"count": str(n)}}}
        rows = self.db.setdefault(table, [])
        if method in ("POST", "PATCH") and table in self.forbid_writes:
            raise SNError(403, "Operation Failed", "Insufficient rights to create or update this record", path)
        if method == "POST":
            rec = {k: (("true" if v else "false") if isinstance(v, bool) else
                       str(v) if isinstance(v, (int, float)) else v) for k, v in (json or {}).items()}
            rec["sys_id"] = sid()
            rec["sys_updated_on"] = "2026-10-17 12:00:00"
            rec.update(self.store_override.get(table, {}))
            rows.append(rec)
            self._capture(table, rec)
            return {"result": self._fields(rec, params)}
        if rid:
            rec = next((r for r in rows if r["sys_id"] == rid), None)
            if not rec:
                raise SNError(404, "No Record found", url=path)
            if method == "DELETE":
                rows.remove(rec)
                return {}
            if method == "PATCH":
                rec.update({k: (("true" if v else "false") if isinstance(v, bool) else str(v)) for k, v in
                            (json or {}).items()})
                rec["sys_updated_on"] = "2026-10-17 12:30:00"
                self._capture(table, rec)
            return {"result": self._fields(rec, params)}
        q = params.get("sysparm_query", "")
        found = [r for r in rows if self._match(r, q)]
        off = int(params.get("sysparm_offset", 0) or 0)
        lim = int(params.get("sysparm_limit", 100) or 100)
        return {"result": [self._fields(r, params) for r in found[off:off + lim]]}

    @staticmethod
    def _fields(rec: dict, params: dict) -> dict:
        f = params.get("sysparm_fields")
        if not f:
            return dict(rec)
        return {k: rec.get(k, "") for k in f.split(",")}
