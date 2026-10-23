"""
SoftProdigy Snow Agent — build pipeline (plan -> ServiceNow artifacts), v6.

Replaces the v5 `_run_build`, which reported "✅" for steps that had failed, created
empty flow records, forced every table to extend `task`, and never made its update
set current.

Order of work
-------------
  1. validate + normalise the plan (names, scopes, parents, reference targets)
  2. application scope   (existing / new scoped app / global)
  3. update set context  (see sn_client.set_context)
  4. roles -> tables -> fields -> choices -> ACLs -> script includes -> business rules
     -> client scripts -> UI policies (+ actions) -> notifications -> flows
  5. verify every created record is captured in sys_update_xml
  6. on failure: optional rollback (reverse order) of everything this run created

Existing records are *reused and left unchanged* (never silently overwritten).
Every step is logged with its real outcome: ok / reused / skipped / warning / failed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

import sn_client as snc
from sn_client import SNClient, SNError

logger = logging.getLogger("sn_build")

JOBS_DIR = Path(os.getenv("AGENT_JOBS_DIR", Path(__file__).resolve().parent / "jobs"))
_jobs: Dict[str, dict] = {}
_lock = threading.Lock()

FIELD_TYPES = {
    "string": "string", "text": "string", "textarea": "string", "str": "string",
    "integer": "integer", "int": "integer", "number": "integer",
    "decimal": "decimal", "float": "float", "currency": "currency", "percent": "percent_complete",
    "boolean": "boolean", "bool": "boolean", "true_false": "boolean",
    "date": "glide_date", "datetime": "glide_date_time", "date_time": "glide_date_time",
    "glide_date": "glide_date", "glide_date_time": "glide_date_time",
    "reference": "reference", "choice": "choice", "email": "email", "url": "url",
    "html": "html", "journal": "journal_input", "user": "reference", "group": "reference",
    "duration": "glide_duration", "phone": "phone_number_e164", "script": "script",
}
VALID_NAME = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
SCOPED_ONLY_BAD_API = [  # global-only APIs that fail in scoped apps
    (re.compile(r"\bgs\.log\("), "gs.info("),
    (re.compile(r"\bgs\.print\("), "gs.info("),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(v: str) -> str:
    v = re.sub(r"[^a-z0-9_]", "_", (v or "").strip().lower())
    return re.sub(r"_+", "_", v).strip("_")


def _bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")


# ════════════════════════════════════════════════════════════════════════════
# Job store (memory + json on disk so status survives a restart)
# ════════════════════════════════════════════════════════════════════════════

def _save(job: dict):
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        (JOBS_DIR / f"{job['job_id']}.json").write_text(json.dumps(job, default=str, indent=1), encoding="utf-8")
    except Exception as e:  # disk problems must not break a build
        logger.warning(f"could not persist job: {e}")


def get_job(job_id: str) -> Optional[dict]:
    with _lock:
        if job_id in _jobs:
            return _jobs[job_id]
    p = JOBS_DIR / f"{job_id}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def list_jobs(limit: int = 20) -> List[dict]:
    items = []
    if JOBS_DIR.exists():
        for p in sorted(JOBS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                items.append({k: j.get(k) for k in ("job_id", "status", "started", "ended", "app", "update_set",
                                                     "summary", "dry_run")})
            except Exception:
                continue
    return items


# ════════════════════════════════════════════════════════════════════════════
# Script checks
# ════════════════════════════════════════════════════════════════════════════

def js_syntax_error(code: str) -> Optional[str]:
    """Return a syntax error message or None. Uses node --check when available."""
    node = shutil.which("node")
    if not node:
        depth = 0
        for ch in re.sub(r"(['\"`]).*?\1", "", code, flags=re.S):
            depth += ch in "({["
            depth -= ch in ")}]"
            if depth < 0:
                return "unbalanced brackets"
        return None if depth == 0 else "unbalanced brackets"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return None
        lines = [l for l in r.stderr.splitlines() if "SyntaxError" in l]
        return lines[0].strip() if lines else r.stderr.strip()[:200]
    except Exception:
        return None
    finally:
        os.unlink(path)


def wrap_br(script: str) -> str:
    s = (script or "").strip() or "// TODO: implement"
    if "executeRule" in s:
        return s
    body = "\n".join("    " + l for l in s.splitlines())
    return f"(function executeRule(current, previous /*null when async*/) {{\n{body}\n}})(current, previous);"


def wrap_client(script: str, ctype: str) -> str:
    s = (script or "").strip()
    fn = {"onLoad": "onLoad()", "onChange": "onChange(control, oldValue, newValue, isLoading, isTemplate)",
          "onSubmit": "onSubmit()", "onCellEdit": "onCellEdit(sysIDs, table, oldValues, newValue, callback)"}[ctype]
    if re.search(rf"function\s+{ctype}\s*\(", s):
        return s
    pre = "    if (isLoading || newValue === '') {\n        return;\n    }\n" if ctype == "onChange" else ""
    body = "\n".join("    " + l for l in (s or "// TODO: implement").splitlines())
    return f"function {fn} {{\n{pre}{body}\n}}"


# ════════════════════════════════════════════════════════════════════════════
# Plan normalisation
# ════════════════════════════════════════════════════════════════════════════

def normalize_plan(plan: dict) -> dict:
    """Coerce AI/hand-written plans into one strict shape (no network calls)."""
    p = dict(plan or {})
    out: Dict[str, Any] = {
        "app_name": (p.get("app_name") or p.get("project_title") or "SoftProdigy App").strip()[:80],
        "app_scope": (p.get("app_scope") or "").strip().lower(),
        "scope_mode": (p.get("scope_mode") or "scoped").lower(),
        "existing_app": p.get("existing_app") or "",
        "description": p.get("description") or "",
    }
    if out["scope_mode"] not in ("scoped", "global", "existing"):
        out["scope_mode"] = "scoped"

    out["roles"] = []
    for r in p.get("roles") or []:
        r = {"name": r} if isinstance(r, str) else dict(r or {})
        if r.get("name"):
            out["roles"].append({"name": str(r["name"]).strip(), "description": r.get("description", ""),
                                 "contains": [c for c in (r.get("contains") or []) if c]})

    out["tables"] = []
    for t in p.get("tables") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        fields = []
        for f in t.get("fields") or []:
            if isinstance(f, str):
                f = {"name": f}
            if not isinstance(f, dict) or not (f.get("name") or f.get("label")):
                continue
            ftype = FIELD_TYPES.get(str(f.get("type", "string")).lower(), "string")
            ref = f.get("reference") or f.get("reference_table") or ""
            if str(f.get("type", "")).lower() == "user" and not ref:
                ref = "sys_user"
            if str(f.get("type", "")).lower() == "group" and not ref:
                ref = "sys_user_group"
            choices = []
            for c in f.get("choices") or []:
                c = {"value": c, "label": c} if isinstance(c, str) else dict(c or {})
                if c.get("value") not in (None, ""):
                    choices.append({"value": _slug(str(c["value"])) or str(c["value"]),
                                    "label": str(c.get("label") or c["value"])})
            fields.append({
                "name": _slug(f.get("name") or f.get("label")), "label": f.get("label") or f.get("name"),
                "type": ftype, "reference": _slug(ref) if ref else "", "choices": choices,
                "mandatory": _bool(f.get("mandatory", f.get("required"))),
                "max_length": f.get("max_length"), "default_value": f.get("default_value", ""),
            })
        out["tables"].append({
            "name": _slug(t["name"]), "label": t.get("label") or t.get("display_name") or "",
            "extends": _slug(t.get("extends") or t.get("extend") or ""),
            "number_prefix": re.sub(r"[^A-Z]", "", str(t.get("number_prefix") or "").upper())[:6],
            "description": t.get("description", ""), "fields": fields,
        })

    out["acls"] = []
    for a in p.get("acls") or []:
        if isinstance(a, dict) and a.get("table"):
            ops = a.get("operations") or [a.get("operation") or "read"]
            for op in ops:
                op = str(op).lower()
                if op in ("read", "write", "create", "delete"):
                    out["acls"].append({"table": _slug(a["table"]), "operation": op,
                                        "roles": [r for r in (a.get("roles") or []) if r]})

    def _named(key, extra):
        items = []
        for x in p.get(key) or []:
            if isinstance(x, dict) and (x.get("name") or x.get("short_description")):
                items.append(extra(x))
        return items

    out["business_rules"] = _named("business_rules", lambda b: {
        "name": b.get("name"), "table": _slug(b.get("table") or b.get("collection") or ""),
        "when": (b.get("when") or "before").lower() if (b.get("when") or "before").lower() in
                ("before", "after", "async", "display") else "before",
        "insert": _bool(b.get("insert"), b.get("operation") in ("insert", "all", None)),
        "update": _bool(b.get("update"), b.get("operation") in ("update", "all")),
        "delete": _bool(b.get("delete"), b.get("operation") in ("delete", "all")),
        "filter_condition": b.get("filter_condition") or b.get("condition_query") or "",
        "condition": b.get("condition") or "", "order": int(b.get("order") or 100),
        "script": b.get("script") or "", "description": b.get("description", "")})
    out["client_scripts"] = _named("client_scripts", lambda c: {
        "name": c.get("name"), "table": _slug(c.get("table") or ""),
        "type": {"onload": "onLoad", "onchange": "onChange", "onsubmit": "onSubmit", "oncelledit": "onCellEdit"}
        .get(str(c.get("type", "onLoad")).replace("_", "").lower(), "onLoad"),
        "field": _slug(c.get("field") or c.get("field_name") or ""), "script": c.get("script") or "",
        "description": c.get("description", "")})
    out["ui_policies"] = _named("ui_policies", lambda u: {
        "name": u.get("short_description") or u.get("name"), "table": _slug(u.get("table") or ""),
        "conditions": u.get("conditions") or u.get("condition") or "",
        "actions": [{"field": _slug(a.get("field") or ""),
                     "mandatory": a.get("mandatory"), "visible": a.get("visible"),
                     "read_only": a.get("read_only", a.get("disabled"))}
                    for a in (u.get("actions") or []) if isinstance(a, dict) and a.get("field")]})
    tri_in = lambda v: None if v is None else _bool(v)
    out["data_policies"] = _named("data_policies", lambda d: {
        "name": d.get("short_description") or d.get("name"), "table": _slug(d.get("table") or d.get("model_table") or ""),
        "conditions": d.get("conditions") or d.get("condition") or "",
        "enforce_ui": _bool(d.get("enforce_ui")), "apply_import_set": _bool(d.get("apply_import_set"), True),
        "apply_soap": _bool(d.get("apply_soap"), True), "description": d.get("description", ""),
        "rules": [{"field": _slug(r.get("field") or ""), "mandatory": tri_in(r.get("mandatory")),
                   "read_only": tri_in(r.get("read_only", r.get("disabled")))}
                  for r in (d.get("rules") or []) if isinstance(r, dict) and r.get("field")]})
    # Assignment rules reference an existing group / user by sys_id (resolved by the caller, never guessed here)
    out["assignment_rules"] = _named("assignment_rules", lambda a: {
        "name": a.get("name"), "table": _slug(a.get("table") or ""), "condition": a.get("condition") or "",
        "group": a.get("group") or "", "user": a.get("user") or "", "order": int(a.get("order") or 100),
        "description": a.get("description", "")})
    # Changes to records that already exist (sys_id known). Old values are read and kept for rollback.
    out["modifications"] = [
        {"kind": m.get("kind") or "Record", "name": m.get("name") or m.get("sys_id"), "table": m["table"],
         "sys_id": m["sys_id"], "changes": dict(m.get("changes") or {})}
        for m in (p.get("modifications") or [])
        if isinstance(m, dict) and m.get("table") and m.get("sys_id") and m.get("changes")]
    out["script_includes"] = _named("script_includes", lambda s: {
        "name": re.sub(r"[^A-Za-z0-9_]", "", s.get("name") or ""), "script": s.get("script") or "",
        "client_callable": _bool(s.get("client_callable")), "description": s.get("description", "")})
    out["notifications"] = _named("notifications", lambda n: {
        "name": n.get("name"), "table": _slug(n.get("table") or n.get("collection") or ""),
        "on_insert": _bool(n.get("on_insert"), True), "on_update": _bool(n.get("on_update")),
        "condition": n.get("condition") or "",
        "recipient_fields": ",".join(n.get("recipient_fields") or []) if isinstance(n.get("recipient_fields"), list)
        else (n.get("recipient_fields") or ""),
        "subject": n.get("subject") or n.get("name"), "message": n.get("message") or n.get("body") or ""})

    def _actions(items):
        acts = []
        for j, a in enumerate(items or [], 1):
            if isinstance(a, dict) and (a.get("type") or "").startswith("core."):
                acts.append({"id": _slug(a.get("id") or a.get("name") or f"action_{j}") or f"action_{j}",
                             "type": a["type"], "inputs": a.get("inputs") or {}})
        return acts

    def _io(items):
        rows = []
        for x in items or []:
            x = {"name": x} if isinstance(x, str) else dict(x or {})
            if x.get("name") or x.get("label"):
                rows.append({"name": _slug(x.get("name") or x.get("label")), "label": x.get("label") or x.get("name"),
                             "type": str(x.get("type") or "String").strip().title()})
        return rows

    # Subflows: reusable inputs -> actions -> outputs blocks, built and published before the flows that call them
    out["subflows"] = []
    for i, s in enumerate(p.get("subflows") or [], 1):
        if not isinstance(s, dict) or not s.get("name"):
            continue
        out["subflows"].append({"id": _slug(s.get("id") or s["name"])[:80] or f"subflow_{i}",
                                "name": str(s["name"]).replace("^", " ").strip()[:100],
                                "description": s.get("description", ""),
                                "inputs": _io(s.get("inputs")), "outputs": _io(s.get("outputs")),
                                "actions": _actions(s.get("actions") or s.get("steps"))})

    out["flows"] = []
    for i, f in enumerate(p.get("flows") or [], 1):
        if not isinstance(f, dict) or not f.get("name"):
            continue
        trig = f.get("trigger") or {}
        if isinstance(trig, str):  # v5 style: "Record Created" + trigger_table
            t = trig.lower()
            trig = {"type": "record.updated" if "updat" in t and "creat" not in t else
                    "record.created_or_updated" if "updat" in t else "record.created",
                    "table": f.get("trigger_table") or ""}
        sub = f.get("subflow") or f.get("call_subflow") or ""
        if isinstance(sub, dict):
            sub = sub.get("id") or sub.get("name") or ""
        out["flows"].append({"id": _slug(f.get("id") or f["name"])[:80] or f"flow_{i}",
                             "name": str(f["name"]).replace("^", " ").strip()[:100],
                             "description": f.get("description", ""), "runAs": f.get("runAs", "system"),
                             "trigger": {"type": trig.get("type", "record.created"),
                                         "table": _slug(trig.get("table") or f.get("trigger_table") or ""),
                                         "condition": trig.get("condition", "")},
                             "actions": _actions(f.get("actions") or f.get("steps")),
                             "subflow": str(sub).strip(),
                             "subflow_inputs": f.get("subflow_inputs") or {}})
    return out


# ════════════════════════════════════════════════════════════════════════════
# Builder
# ════════════════════════════════════════════════════════════════════════════

class Builder:
    def __init__(self, sn: Optional[SNClient], plan: dict, opts: dict, log: Callable[[str, str, dict], None]):
        self.sn = sn
        self.plan = plan
        self.opts = opts
        self.log = log
        self.scope = ""        # e.g. x_1234_p1router, "" for global
        self.app: Dict[str, Any] = {}
        self.ctx: Dict[str, Any] = {}
        self.created: List[Dict[str, Any]] = []   # rollback + verification list
        self.modified: List[Dict[str, Any]] = []  # existing records changed: old values kept for rollback
        self.results: List[Dict[str, Any]] = []
        self.table_map: Dict[str, str] = {}       # plan name -> real table name
        self.role_map: Dict[str, str] = {}
        self.issues: List[str] = []
        self.notes: List[str] = []                # non-blocking plan remarks
        self.ask: Optional[Callable[[dict], dict]] = None   # pause gate (set by run_build), None = never pause
        self.subflow_names: Dict[str, str] = {}   # plan subflow id/name -> name of the subflow actually wired
        self.subflow_ids: Dict[str, str] = {}     # subflow name -> sys_hub_flow sys_id (for flows that call it)
        self._approver_ids: Dict[tuple, str] = {}
        self._ui = None                           # Flow Designer browser, started only when needed
        self._ui_error = ""

    # -- naming -----------------------------------------------------------------
    def table_name(self, raw: str) -> str:
        raw = _slug(raw)
        if raw in self.table_map:
            return self.table_map[raw]
        if self.scope:
            if raw.startswith(self.scope + "_"):
                return raw
            base = re.sub(r"^(u_|x_[a-z0-9]+_[a-z0-9]+_)", "", raw)
            return f"{self.scope}_{base}"
        return raw if raw.startswith(("u_", "x_")) else f"u_{raw}"

    def field_name(self, table: str, raw: str, table_is_new: bool) -> str:
        raw = _slug(raw)
        if table_is_new:
            if self.scope:
                return re.sub(r"^u_", "", raw)
            return raw if raw.startswith("u_") else f"u_{raw}"
        # adding a column to an existing (OOB/other) table
        if self.scope:
            return raw if raw.startswith(self.scope + "_") else f"{self.scope}_{re.sub(r'^u_', '', raw)}"
        return raw if raw.startswith("u_") else f"u_{raw}"

    def role_name(self, raw: str) -> str:
        raw = (raw or "").strip()
        if raw in self.role_map:
            return self.role_map[raw]
        if self.scope:
            if raw.startswith(self.scope + "."):
                return raw
            base = re.sub(r"^(x_[a-z0-9]+_[a-z0-9]+[._]|u_)", "", _slug(raw))
            return f"{self.scope}.{base}"
        return _slug(raw)

    def scoped(self, payload: dict) -> dict:
        if self.app.get("sys_id") and self.app["sys_id"] != snc.GLOBAL_SCOPE_SYS_ID:
            payload["sys_scope"] = self.app["sys_id"]
        return payload

    # -- result helpers -----------------------------------------------------------
    def record(self, kind: str, name: str, status: str, msg: str = "", table: str = "", sys_id: str = "",
               capture_key: str = ""):
        item = {"kind": kind, "name": name, "status": status, "message": msg, "table": table, "sys_id": sys_id}
        self.results.append(item)
        if status == "created":
            self.created.append({"table": table, "sys_id": sys_id, "kind": kind, "name": name,
                                 "capture_key": capture_key or sys_id})
        icon = {"created": "✅", "modified": "✏️", "reused": "♻️", "skipped": "⏭️", "warning": "⚠️",
                "failed": "❌"}.get(status, "•")
        self.log(f"{icon} {kind}: {name}" + (f" — {msg}" if msg else ""),
                 {"created": "ok", "modified": "ok", "reused": "info", "skipped": "info"}.get(status, status), item)

    def upsert(self, kind: str, name: str, table: str, unique: str, payload: dict,
               capture_key: str = "") -> Optional[str]:
        existing = self.sn.get_one(table, unique, "sys_id,sys_scope")
        if existing:
            self.record(kind, name, "reused", "already exists — left unchanged", table, existing["sys_id"])
            return existing["sys_id"]
        rec = self.sn.create(table, payload, fields="sys_id")
        sid = rec.get("sys_id")
        if not sid:
            raise SNError(500, f"ServiceNow did not return a sys_id for {kind} '{name}'")
        self.record(kind, name, "created", "", table, sid, capture_key)
        return sid

    def fail(self, kind: str, name: str, err: Exception):
        msg = str(err)
        if isinstance(err, SNError) and err.status == 403:
            msg += " (the integration user lacks the role for this — e.g. security_admin elevation for ACLs, " \
                   "or admin for dictionary changes)"
        self.record(kind, name, "failed", msg)
        if self.opts.get("stop_on_error", True):
            raise err

    # -- validation (no writes) ------------------------------------------------------
    def validate(self) -> List[str]:
        issues = []
        p = self.plan
        plan_tables = {t["name"] for t in p["tables"]}
        for t in p["tables"]:
            if not VALID_NAME.match(t["name"]):
                issues.append(f"Table name '{t['name']}' is invalid")
            for f in t["fields"]:
                if not VALID_NAME.match(f["name"] or "x"):
                    issues.append(f"Field '{t['name']}.{f['name']}' has an invalid name")
                if f["type"] == "reference" and not f["reference"]:
                    issues.append(f"Reference field '{t['name']}.{f['name']}' has no reference table")
                if f["type"] == "choice" and not f["choices"]:
                    issues.append(f"Choice field '{t['name']}.{f['name']}' has no choices")
        for kind in ("business_rules", "client_scripts", "ui_policies", "notifications", "data_policies",
                     "assignment_rules"):
            for x in p.get(kind) or []:
                if not x.get("table"):
                    issues.append(f"{kind[:-1].replace('_', ' ').title()} '{x['name']}' has no table")
        for d in p.get("data_policies") or []:
            if not d["rules"]:
                issues.append(f"Data policy '{d['name']}' has no field rules — it would do nothing")
        for a in p.get("assignment_rules") or []:
            if not (a["group"] or a["user"]):
                issues.append(f"Assignment rule '{a['name']}' has no group or user to assign")
            for ref in (a["group"], a["user"]):
                if ref and not snc.is_sys_id(ref):
                    issues.append(f"Assignment rule '{a['name']}' must reference a group/user by sys_id, got '{ref}'")
        for c in p["client_scripts"]:
            if c["type"] == "onChange" and not c["field"]:
                issues.append(f"onChange client script '{c['name']}' has no field")
            if re.search(r"\bGlideRecord\b|\bgs\.", c["script"]):
                issues.append(f"Client script '{c['name']}' uses server-side APIs (GlideRecord/gs) — "
                              "use GlideAjax + a client-callable script include")
        for b in p["business_rules"]:
            if re.search(r"current\.update\(\)", b["script"]) and b["when"] in ("before", "after"):
                issues.append(f"Business rule '{b['name']}' calls current.update() in a {b['when']} rule "
                              "(causes recursion / double updates)")
            err = js_syntax_error(wrap_br(b["script"]))
            if err:
                issues.append(f"Business rule '{b['name']}' script syntax error: {err}")
        for c in p["client_scripts"]:
            err = js_syntax_error(wrap_client(c["script"], c["type"]))
            if err:
                issues.append(f"Client script '{c['name']}' syntax error: {err}")
        for s in p["script_includes"]:
            if not s["name"]:
                issues.append("A script include has no valid class name")
                continue
            err = js_syntax_error(s["script"] or "var x;")
            if err:
                issues.append(f"Script include '{s['name']}' syntax error: {err}")
            elif s["script"] and not re.search(rf"\bvar\s+{re.escape(s['name'])}\s*=", s["script"]):
                issues.append(f"Script include '{s['name']}' script does not define 'var {s['name']} = ...' — "
                              "the class name must match the record name or callers cannot find it")
            if s["client_callable"] and s["script"] and "AbstractAjaxProcessor" not in s["script"]:
                issues.append(f"Script include '{s['name']}' is client callable but does not extend "
                              "AbstractAjaxProcessor, so GlideAjax calls will fail")
        sub_keys = {s["id"] for s in p["subflows"]} | {s["name"] for s in p["subflows"]}
        for s in p["subflows"]:
            if not s["actions"]:
                issues.append(f"Subflow '{s['name']}' has no core.* actions — it would do nothing")
            if len({x["name"] for x in s["inputs"]}) != len(s["inputs"]):
                issues.append(f"Subflow '{s['name']}' has duplicate input names")
        for f in p["flows"]:
            if not f["trigger"]["table"]:
                issues.append(f"Flow '{f['name']}' has no trigger table")
            if not f["actions"] and not f["subflow"]:
                issues.append(f"Flow '{f['name']}' has no core.* actions and calls no subflow — it would do nothing")
            if f["subflow"] and f["subflow"] not in sub_keys:
                # not in the plan: allowed only as the exact name of a subflow that already exists in the instance
                self.notes.append(f"Flow '{f['name']}' calls subflow '{f['subflow']}', which is not in the plan — "
                                  "it must already exist (published) in the instance")
        return issues

    # -- steps ---------------------------------------------------------------------
    def step_check_release_compat(self) -> dict:
        """
        Req 1: Release-aware compatibility.
        Detects the ServiceNow release and guards release-specific features.
        Returns release info dict.
        """
        release = self.sn.release_info()
        self.log(f"🏷️ ServiceNow release: {release.get('build_name') or 'Unknown'} "
                 f"(tag: {release.get('build_tag') or 'n/a'})", "info", release)
        # Guard: Flow Designer (sys_hub_flow) requires San Diego (major>=22) or later
        if (self.plan["flows"] or self.plan["subflows"]) and 0 < release.get("major", 0) < 22:
            for f in self.plan["subflows"] + self.plan["flows"]:
                self.log(f"⚠️ '{f['name']}' skipped — Flow Designer is not fully supported on "
                         f"{release.get('build_name','this release')} (requires San Diego or later). "
                         f"Deploy via Workflow Editor instead.", "warning", {})
            self.plan["flows"] = []
            self.plan["subflows"] = []
        # Store release on opts for downstream use
        self.opts["_release"] = release
        return release

    def step_assess_instance(self) -> dict:
        """
        Req 3: Deep existing-instance assessment.
        For each table in the plan that already exists, read existing BRs, ACLs, and fields.
        Log conflicts. Returns assessment dict.
        """
        assessment = {"conflicts": [], "oob_fields_available": {}, "acl_overlaps": [], "br_order_conflicts": []}
        for t in self.plan["tables"]:
            table_name = t["name"]
            existing_sys_id = self.sn.table_sys_id(table_name)
            if not existing_sys_id:
                continue  # new table, no conflicts possible
            # Check field conflicts
            existing_fields = self.sn.oob_table_fields(table_name)
            existing_field_names = {f.get("element", "") for f in existing_fields}
            assessment["oob_fields_available"][table_name] = [f.get("element") for f in existing_fields]
            for field in t["fields"]:
                if field["name"] in existing_field_names:
                    fname = field["name"]
                    assessment["conflicts"].append(
                        f"Field '{table_name}.{fname}' already exists OOB — will reuse it"
                    )
            # Check BR order conflicts
            existing_brs = self.sn.existing_business_rules(table_name)
            for new_br in self.plan["business_rules"]:
                if new_br.get("table") != table_name:
                    continue
                for ex_br in existing_brs:
                    if (ex_br.get("order") == str(new_br.get("order", 100)) and
                            ex_br.get("when") == new_br.get("when", "before")):
                        msg = (f"BR order conflict: '{new_br['name']}' and existing '{ex_br['name']}' "
                               f"both run at order={new_br.get('order',100)} ({new_br.get('when','before')}) "
                               f"on {table_name}")
                        assessment["br_order_conflicts"].append(msg)
                        self.log(f"⚠️ {msg}", "warning", {})
            # Check ACL overlaps
            existing_acls = self.sn.existing_acls(table_name)
            for new_acl in self.plan["acls"]:
                if new_acl.get("table") != table_name:
                    continue
                for ex_acl in existing_acls:
                    if ex_acl.get("operation") == new_acl.get("operation"):
                        msg = (f"ACL overlap: '{table_name}' [{new_acl['operation']}] already has an ACL — "
                               f"new one will be additive")
                        assessment["acl_overlaps"].append(msg)
                        self.log(f"⚠️ {msg}", "warning", {})

        total_issues = len(assessment["conflicts"]) + len(assessment["br_order_conflicts"]) + len(assessment["acl_overlaps"])
        self.log(f"🔍 Instance assessment: {total_issues} potential conflict(s) found",
                 "warning" if total_issues > 0 else "ok", assessment)
        return assessment

    def step_global_scope_warning(self):
        """Req 2: Warn when scope_mode is global — OOB-first principle."""
        if self.plan.get("scope_mode") == "global":
            self.log("⚠️ GLOBAL SCOPE: Building in global scope bypasses application isolation. "
                     "OOB best practice is to use a scoped application. "
                     "Set scope_mode='scoped' unless you have a specific reason for global.", "warning", {})
        for br in self.plan["business_rules"]:
            if re.search(r"current\.update\(\)", br.get("script", "")):
                self.log(f"⚠️ BR '{br['name']}': current.update() in a {br.get('when','before')} rule "
                         f"causes infinite recursion / double saves.", "warning", {})
        for f in self.plan["flows"]:
            if f.get("runAs") == "system":
                self.log(f"⚠️ Flow '{f['name']}' runs as system — bypasses ACLs. "
                         f"Consider runAs='user' for security compliance.", "warning", {})

    def step_dry_run_preview(self) -> list:
        """
        Req 5: Dry-run mode - generate a preview of what WOULD be created without making any API calls.
        Returns a list of planned actions.
        """
        preview = []
        p = self.plan
        for r in p["roles"]:
            preview.append({"kind": "Role", "name": self.role_name(r["name"]), "action": "would_create",
                            "detail": f"Role: {r.get('description','')}"})
        for t in p["tables"]:
            action = "would_reuse" if self.sn and self.sn.table_sys_id(t["name"]) else "would_create"
            table_name = self.table_name(t["name"])
            preview.append({"kind": "Table", "name": table_name, "action": action,
                            "detail": f"Extends: {t.get('extends','none')}, Fields: {len(t['fields'])}"})
            for f in t["fields"]:
                preview.append({"kind": "Field", "name": f"{table_name}.{self.field_name(table_name, f['name'], True)}",
                                "action": "would_create", "detail": f"Type: {f['type']}"})
        for a in p["acls"]:
            preview.append({"kind": "ACL", "name": f"{a['table']} [{a['operation']}]",
                            "action": "would_create", "detail": f"Roles: {', '.join(a['roles'])}"})
        for b in p["business_rules"]:
            preview.append({"kind": "Business Rule", "name": b["name"], "action": "would_create",
                            "detail": f"Table: {b['table']}, When: {b['when']}, Order: {b.get('order',100)}"})
        for c in p["client_scripts"]:
            preview.append({"kind": "Client Script", "name": c["name"], "action": "would_create",
                            "detail": f"Table: {c['table']}, Type: {c['type']}"})
        for s in p["script_includes"]:
            preview.append({"kind": "Script Include", "name": s["name"], "action": "would_create",
                            "detail": f"Client callable: {s['client_callable']}"})
        for n in p["notifications"]:
            preview.append({"kind": "Notification", "name": n["name"], "action": "would_create",
                            "detail": f"Table: {n['table']}"})
        for d in p.get("data_policies") or []:
            preview.append({"kind": "Data Policy", "name": d["name"], "action": "would_create",
                            "detail": f"Table: {d['table']}, rules: {', '.join(r['field'] for r in d['rules'])}"})
        for a in p.get("assignment_rules") or []:
            preview.append({"kind": "Assignment Rule", "name": a["name"], "action": "would_create",
                            "detail": f"Table: {a['table']}, condition: {a['condition'] or 'always'}"})
        for m in p.get("modifications") or []:
            preview.append({"kind": m["kind"], "name": m["name"], "action": "would_modify",
                            "detail": f"{m['table']} {m['sys_id']}: " + ", ".join(m["changes"])})
        engine = flow_engine(self.plan, self.opts)
        for s in p["subflows"]:
            preview.append({"kind": "Subflow", "name": s["name"], "action": "would_create" if engine else "skipped",
                            "detail": f"{len(s['inputs'])} input(s), actions: "
                                      f"{', '.join(a['type'] for a in s['actions']) or 'none'} · engine: "
                                      f"{engine or 'none available'} · a published subflow with the same name, "
                                      f"or a close match in the subflow library, is reused"})
        for f in p["flows"]:
            preview.append({"kind": "Flow", "name": f["name"], "action": "would_create" if engine else "skipped",
                            "detail": f"Trigger: {f['trigger']['type']} on {f['trigger']['table']}"
                                      + (f" · calls subflow {f['subflow']}" if f["subflow"] else "")
                                      + f" · engine: {engine or 'none available'}"})
            if f.get("runAs") == "system":
                preview.append({"kind": "Warning", "name": f["name"], "action": "warning",
                                "detail": "⚠️ Flow runs as 'system' — bypasses ACLs. Consider 'user' for safer execution."})
        counts = {}
        for item in preview:
            counts[item["action"]] = counts.get(item["action"], 0) + 1
        self.log(f"📋 Dry-run preview: {counts.get('would_create',0)} to create, "
                 f"{counts.get('would_reuse',0)} to reuse, {counts.get('warning',0)} warning(s)",
                 "info", {"preview": preview, "counts": counts})
        return preview

    def step_scope_and_update_set(self):
        p, o = self.plan, self.opts
        self.app = snc.ensure_app(self.sn, mode=p["scope_mode"], app_name=p["app_name"],
                                  app_scope=p["app_scope"], existing_app=p["existing_app"])
        self.scope = "" if self.app["sys_id"] == snc.GLOBAL_SCOPE_SYS_ID else (self.app.get("scope") or "")
        if self.app.get("created"):
            self.record("Application", f"{self.app['name']} ({self.scope})", "created", "", "sys_app",
                        self.app["sys_id"], capture_key=self.app["sys_id"])
        else:
            self.record("Application", f"{self.app['name']} ({self.app.get('scope')})", "reused",
                        "existing application scope")
        us_name = (o.get("update_set_name") or
                   f"HAI_{_slug(p['app_name'])[:30]}_{datetime.now().strftime('%Y%m%d_%H%M')}")
        self.ctx = snc.set_context(self.sn, app=self.app, update_set_name=us_name,
                                   description=o.get("update_set_description") or
                                   f"{p['app_name']} — built by SoftProdigy Snow Agent",
                                   existing_update_set=o.get("update_set_sys_id") or "")
        us = self.ctx["update_set"]
        if self.app.get("created"):
            # The sys_app record was inserted before its update set existed. Touch it now that the new update set
            # is current, so the application record itself travels with the update set.
            self.sn.patch("sys_app", self.app["sys_id"],
                          {"short_description": f"{self.app['name']} — built by SoftProdigy Snow Agent"})
        if us["created"]:
            self.created.append({"table": "sys_update_set", "sys_id": us["sys_id"], "kind": "Update set",
                                 "name": us["name"], "capture_key": None})
        self.log(f"📦 Update set '{us['name']}' is now current for {self.sn.username} in "
                 f"{self.app.get('scope') or 'global'} ({'created' if us['created'] else 'existing'})", "ok",
                 {"update_set": us, "preferences": self.ctx["preferences"]})

    def step_roles(self):
        for r in self.plan["roles"]:
            name = self.role_name(r["name"])
            self.role_map[r["name"]] = name
            try:
                self.upsert("Role", name, "sys_user_role", f"name={name}",
                            self.scoped({"name": name, "description": r["description"] or f"{name} role",
                                         "suffix": name.split(".")[-1] if self.scope else ""}))
            except SNError as e:
                self.fail("Role", name, e)
        # Req 6: pre-fetch all roles to avoid N+1 query-in-loop
        all_role_names = [self.role_name(r["name"]) for r in self.plan["roles"]]
        all_role_names += [c for r in self.plan["roles"] for c in r.get("contains", [])]
        for r in self.plan["roles"]:  # role containment (e.g. admin contains user)
            for c in r["contains"]:
                parent, child = self.role_name(r["name"]), self.role_name(c)
                try:
                    pr = self.sn.get_one("sys_user_role", f"name={parent}", "sys_id")
                    ch = self.sn.get_one("sys_user_role", f"name={child}", "sys_id") or \
                        self.sn.get_one("sys_user_role", f"name={c}", "sys_id")
                    if pr and ch:
                        self.upsert("Role contains", f"{parent} ⊃ {child}", "sys_user_role_contains",
                                    f"role={pr['sys_id']}^contains={ch['sys_id']}",
                                    self.scoped({"role": pr["sys_id"], "contains": ch["sys_id"]}))
                except SNError as e:
                    self.fail("Role contains", f"{parent} ⊃ {child}", e)

    def step_tables(self):
        for t in self.plan["tables"]:
            raw = t["name"]
            existing = self.sn.table_sys_id(raw)
            if existing:  # OOB / already there: reuse, never create a scoped copy
                self.table_map[raw] = raw
                t["_new"] = False
                self.record("Table", raw, "reused", "table already exists", "sys_db_object", existing)
                # Req 2: 80/20 OOB-first gate — suggest using existing table's fields instead of adding custom table
                if t.get("extends") == t["name"]:
                    self.log(f"⚠️ OOB table '{raw}' exists: consider using its existing fields "
                             f"rather than extending. Adding fields only.", "warning", {})
                continue
            name = self.table_name(raw)
            self.table_map[raw] = name
            t["_new"] = True
            if self.sn.table_sys_id(name):
                t["_new"] = True  # ours from a previous run; fields may still need adding
                self.record("Table", name, "reused", "already exists — left unchanged")
                continue
            payload = self.scoped({"name": name, "label": t["label"] or name.replace("_", " ").title(),
                                   "create_access_controls": True, "is_extendable": True})
            if t["extends"]:
                parent = self.table_map.get(t["extends"]) or t["extends"]
                parent_id = self.sn.table_sys_id(parent) or self.sn.table_sys_id(self.table_name(parent))
                if not parent_id:
                    self.fail("Table", name, SNError(404, f"parent table '{t['extends']}' not found"))
                    continue
                payload["super_class"] = parent_id
            try:
                self.upsert("Table", name, "sys_db_object", f"name={name}", payload,
                            capture_key=f"sys_db_object_{name}")
            except SNError as e:
                self.fail("Table", name, e)
                continue
            if t["number_prefix"]:
                try:
                    self.upsert("Number prefix", f"{name} → {t['number_prefix']}", "sys_number",
                                f"category={name}", self.scoped({"category": name, "prefix": t["number_prefix"],
                                                                 "number": 1000, "maximum_digits": 7}))
                except SNError as e:
                    self.fail("Number prefix", name, e)

    def step_fields(self):
        for t in self.plan["tables"]:
            table = self.table_map.get(t["name"], t["name"])
            for f in t["fields"]:
                element = self.field_name(table, f["name"], t.get("_new", True))
                label = f"{table}.{element}"
                try:
                    if self.sn.get_one("sys_dictionary", f"name={table}^element={element}", "sys_id"):
                        self.record("Field", label, "reused", "column already exists — left unchanged")
                        continue
                    payload = self.scoped({"name": table, "element": element,
                                           "column_label": f["label"] or element.replace("_", " ").title(),
                                           "internal_type": f["type"], "mandatory": f["mandatory"],
                                           "active": True})
                    if f["type"] == "string":
                        payload["max_length"] = int(f["max_length"] or 100)
                    if f["type"] == "reference":
                        ref = self.table_map.get(f["reference"]) or f["reference"]
                        if not self.sn.table_sys_id(ref):
                            self.fail("Field", label, SNError(404, f"reference table '{f['reference']}' not found"))
                            continue
                        payload["reference"] = ref
                    if f["type"] == "choice":
                        payload["internal_type"] = "string"
                        payload["choice"] = 1          # "Dropdown with --None--"
                        payload["max_length"] = 40
                    if f["default_value"]:
                        payload["default_value"] = str(f["default_value"])
                    self.upsert("Field", label, "sys_dictionary", f"name={table}^element={element}", payload,
                                capture_key=f"sys_dictionary_{table}_{element}")
                    for seq, c in enumerate(f["choices"], 1):
                        self.upsert("Choice", f"{label} = {c['value']}", "sys_choice",
                                    f"name={table}^element={element}^value={c['value']}^language=en",
                                    self.scoped({"name": table, "element": element, "value": c["value"],
                                                 "label": c["label"], "sequence": seq * 10, "language": "en",
                                                 "inactive": False}),
                                    capture_key=f"sys_choice_{table}_{element}")
                except SNError as e:
                    self.fail("Field", label, e)

    def step_acls(self):
        denied = ""   # first 403: REST sessions cannot elevate to security_admin, so later ACL inserts fail too
        for a in self.plan["acls"]:
            table = self.table_map.get(a["table"])
            if not table:
                if self.sn.table_sys_id(a["table"]):
                    # an existing platform table (incident, task ...): the app must not rewrite its security
                    self.record("ACL", f"{a['table']} [{a['operation']}]", "skipped",
                                "existing platform table — its ACLs are left unchanged by the build")
                    continue
                table = self.table_name(a["table"])
            name = f"{table} [{a['operation']}]"
            manual = ("not created — ServiceNow only allows ACL inserts in a session elevated to security_admin, "
                      "which the REST API cannot do. Create it in the instance (elevate role → System Security → "
                      "Access Control) with roles: " + (", ".join(self.role_name(r) for r in a["roles"]) or "none"))
            if denied:
                self.record("ACL", name, "warning", manual)
                continue
            try:
                acl_id = self.upsert("ACL", name, "sys_security_acl",
                                     f"name={table}^operation={a['operation']}^type=record",
                                     self.scoped({"name": table, "operation": a["operation"], "type": "record",
                                                  "active": True, "admin_overrides": True,
                                                  "description": "Created by SoftProdigy Snow Agent"}))
                for r in a["roles"]:
                    role = self.sn.get_one("sys_user_role", f"name={self.role_name(r)}", "sys_id") or \
                        self.sn.get_one("sys_user_role", f"name={r}", "sys_id")
                    if not role:
                        self.record("ACL role", f"{name} ← {r}", "warning", "role not found")
                        continue
                    self.upsert("ACL role", f"{name} ← {self.role_name(r)}", "sys_security_acl_role",
                                f"sys_security_acl={acl_id}^sys_user_role={role['sys_id']}",
                                self.scoped({"sys_security_acl": acl_id, "sys_user_role": role["sys_id"]}))
            except SNError as e:
                if e.status == 403:
                    denied = "yes"
                    self.record("ACL", name, "warning", manual)
                    continue
                self.fail("ACL", name, e)

    def step_script_includes(self):
        for s in self.plan["script_includes"]:
            script = self._scoped_script(s["script"] or
                                         f"var {s['name']} = Class.create();\n{s['name']}.prototype = {{\n"
                                         f"    initialize: function() {{}},\n    type: '{s['name']}'\n}};")
            # ServiceNow stores api_name as <scope>.<name>, "global.<name>" in the global scope
            api = f"{self.scope or 'global'}.{s['name']}"
            payload = {"name": s["name"], "api_name": api, "script": script,
                       "client_callable": s["client_callable"], "active": True,
                       "access": "package_private" if self.scope else "public", "description": s["description"]}
            try:
                self.upsert("Script include", api, "sys_script_include", f"api_name={api}", self.scoped(payload))
            except SNError as e:
                self.fail("Script include", api, e)

    def _scoped_script(self, script: str) -> str:
        if not self.scope:
            return script
        for rx, repl in SCOPED_ONLY_BAD_API:
            script = rx.sub(repl, script)
        return script

    def _resolve_table(self, raw: str) -> Optional[str]:
        name = self.table_map.get(raw) or (raw if self.sn.table_sys_id(raw) else self.table_name(raw))
        return name if self.sn.table_sys_id(name) else None

    def _resolve_fields(self, table: str, raw: str) -> str:
        """Comma-separated plan field names -> the column names actually built (e.g. u_host -> host in a scope)."""
        out = []
        for f in [x.strip() for x in (raw or "").split(",") if x.strip()]:
            for cand in (f, self.field_name(table, f, True), self.field_name(table, f, False)):
                if self.sn.get_one("sys_dictionary", f"name={table}^element={cand}", "sys_id"):
                    f = cand
                    break
            out.append(f)
        return ",".join(out)

    def step_business_rules(self):
        for b in self.plan["business_rules"]:
            table = self._resolve_table(b["table"])
            if not table:
                self.fail("Business rule", b["name"], SNError(404, f"table '{b['table']}' not found"))
                continue
            try:
                self.upsert("Business rule", f"{b['name']} ({table})", "sys_script",
                            f"name={b['name']}^collection={table}",
                            self.scoped({"name": b["name"], "collection": table, "when": b["when"],
                                         "action_insert": b["insert"], "action_update": b["update"],
                                         "action_delete": b["delete"], "action_query": False,
                                         "filter_condition": b["filter_condition"], "condition": b["condition"],
                                         "order": b["order"], "advanced": True, "active": True,
                                         "script": self._scoped_script(wrap_br(b["script"])),
                                         "description": b["description"]}))
            except SNError as e:
                self.fail("Business rule", b["name"], e)

    def step_client_scripts(self):
        for c in self.plan["client_scripts"]:
            table = self._resolve_table(c["table"])
            if not table:
                self.fail("Client script", c["name"], SNError(404, f"table '{c['table']}' not found"))
                continue
            payload = self.scoped({"name": c["name"], "table": table, "type": c["type"], "ui_type": 10,
                                   "active": True, "script": wrap_client(c["script"], c["type"]),
                                   "description": c["description"]})
            if c["type"] == "onChange":
                payload["field"] = c["field"]   # sys_script_client column is 'field' (label "Field name")
            try:
                self.upsert("Client script", f"{c['name']} ({table})", "sys_script_client",
                            f"name={c['name']}^table={table}", payload)
            except SNError as e:
                self.fail("Client script", c["name"], e)

    def step_ui_policies(self):
        tri = lambda v: "ignore" if v is None else ("true" if _bool(v) else "false")
        for u in self.plan["ui_policies"]:
            table = self._resolve_table(u["table"])
            if not table:
                self.fail("UI policy", u["name"], SNError(404, f"table '{u['table']}' not found"))
                continue
            try:
                pid = self.upsert("UI policy", f"{u['name']} ({table})", "sys_ui_policy",
                                  f"short_description={u['name']}^table={table}",
                                  self.scoped({"short_description": u["name"], "table": table,
                                               "conditions": u["conditions"], "on_load": True,
                                               "reverse_if_false": True, "active": True, "global": True}))
                for a in u["actions"]:
                    self.upsert("UI policy action", f"{u['name']} → {a['field']}", "sys_ui_policy_action",
                                f"ui_policy={pid}^field={a['field']}",
                                self.scoped({"ui_policy": pid, "table": table, "field": a["field"],
                                             "mandatory": tri(a["mandatory"]), "visible": tri(a["visible"]),
                                             "disabled": tri(a["read_only"])}))
            except SNError as e:
                self.fail("UI policy", u["name"], e)

    def step_data_policies(self):
        tri = lambda v: "ignore" if v is None else ("true" if _bool(v) else "false")
        for d in self.plan.get("data_policies") or []:
            table = self._resolve_table(d["table"])
            if not table:
                self.fail("Data policy", d["name"], SNError(404, f"table '{d['table']}' not found"))
                continue
            try:
                pid = self.upsert("Data policy", f"{d['name']} ({table})", "sys_data_policy2",
                                  f"short_description={d['name']}^model_table={table}",
                                  self.scoped({"short_description": d["name"], "model_table": table,
                                               "conditions": d["conditions"], "active": True,
                                               "enforce_ui": d["enforce_ui"], "apply_import_set": d["apply_import_set"],
                                               "apply_soap": d["apply_soap"], "reverse_if_false": True,
                                               "description": d["description"]}))
                for r in d["rules"]:
                    self.upsert("Data policy rule", f"{d['name']} → {r['field']}", "sys_data_policy_rule",
                                f"sys_data_policy={pid}^field={r['field']}",
                                self.scoped({"sys_data_policy": pid, "table": table, "field": r["field"],
                                             "mandatory": tri(r["mandatory"]), "disabled": tri(r["read_only"])}))
            except SNError as e:
                self.fail("Data policy", d["name"], e)

    def step_assignment_rules(self):
        for a in self.plan.get("assignment_rules") or []:
            table = self._resolve_table(a["table"])
            if not table:
                self.fail("Assignment rule", a["name"], SNError(404, f"table '{a['table']}' not found"))
                continue
            payload = {"name": a["name"], "table": table, "condition": a["condition"], "order": a["order"],
                       "active": True}
            if a["group"]:
                payload["group"] = a["group"]
            if a["user"]:
                payload["user"] = a["user"]
            try:
                self.upsert("Assignment rule", f"{a['name']} ({table})", "sysrule_assignment",
                            f"name={a['name']}^table={table}", self.scoped(payload))
            except SNError as e:
                self.fail("Assignment rule", a["name"], e)

    def step_modifications(self):
        """Change existing records. The current values of every changed field are read first and kept, so a failed
        run (or a later rollback) puts them back exactly."""
        for m in self.plan.get("modifications") or []:
            label = f"{m['name']} ({m['table']})"
            try:
                current = self.sn.get(m["table"], m["sys_id"], ",".join(["sys_id", *m["changes"]]))
                if not current.get("sys_id"):
                    raise SNError(404, f"{m['kind']} {m['sys_id']} no longer exists")
                old = {k: current.get(k, "") for k in m["changes"]}
                if all(str(old[k]) == str(v) for k, v in m["changes"].items()):
                    self.record(m["kind"], label, "reused", "already has the requested values — left unchanged",
                                m["table"], m["sys_id"])
                    continue
                self.sn.patch(m["table"], m["sys_id"], m["changes"])
                self.modified.append({"table": m["table"], "sys_id": m["sys_id"], "kind": m["kind"],
                                      "name": label, "old": old, "new": dict(m["changes"]),
                                      "capture_key": m["sys_id"]})
                self.record(m["kind"], label, "modified",
                            "changed " + ", ".join(f"{k}: '{old[k]}' → '{v}'" for k, v in m["changes"].items()),
                            m["table"], m["sys_id"])
            except SNError as e:
                self.fail(m["kind"], label, e)

    def step_notifications(self):
        for n in self.plan["notifications"]:
            table = self._resolve_table(n["table"])
            if not table:
                self.fail("Notification", n["name"], SNError(404, f"table '{n['table']}' not found"))
                continue
            try:
                self.upsert("Notification", f"{n['name']} ({table})", "sysevent_email_action",
                            f"name={n['name']}^collection={table}",
                            self.scoped({"name": n["name"], "collection": table, "generation_type": "engine",
                                         "action_insert": n["on_insert"], "action_update": n["on_update"],
                                         "condition": n["condition"], "recipient_fields": self._resolve_fields(table, n["recipient_fields"]),
                                         "subject": n["subject"], "message_html": n["message"] or n["subject"],
                                         "active": True}))
            except SNError as e:
                self.fail("Notification", n["name"], e)

    # -- flows & subflows -----------------------------------------------------------------
    # Subflows first (the flows that call them need their sys_id), then flows. Each item is built automatically by
    # the Flow Server (ServiceNow SDK); only when that fails for an item is it built in the Flow Designer browser.
    def _hub_flow(self, name: str, kind: str) -> Optional[dict]:
        """Newest sys_hub_flow record with this name; kind is 'flow' or 'subflow'."""
        rows = self.sn.query("sys_hub_flow", f"name={name}^type={kind}^ORDERBYDESCsys_updated_on",
                             "sys_id,name,status,active,type,sys_scope", limit=1)
        return rows[0] if rows else None

    @staticmethod
    def _is_live(rec: Optional[dict], kind: str) -> bool:
        if not rec:
            return False
        return (str(rec.get("status") or "").lower() == "published"
                or (kind == "flow" and str(rec.get("active")).lower() == "true"))

    def _map_subflow(self, s: dict, actual_name: str, sys_id: str = ""):
        self.subflow_names[s["id"]] = actual_name
        self.subflow_names[s["name"]] = actual_name
        if sys_id:
            self.subflow_ids[actual_name] = sys_id

    def step_flows(self):
        subflows, flows = self.plan["subflows"], self.plan["flows"]
        if not subflows and not flows:
            return
        have = flow_engines()
        engine = flow_engine(self.plan, self.opts, have)
        if not engine:
            why = ("no flow engine is available — start the Flow Server (v6/flow-server, started by start_agent.bat) "
                   "for automatic builds, or install Playwright (pip install playwright && python -m playwright "
                   "install chromium) to build them in the Flow Designer UI. No empty flow record was created.")
            for s in subflows:
                self.record("Subflow", s["name"], "skipped", why)
            for f in flows:
                self.record("Flow", f["name"], "skipped", why)
            return
        fallback = engine == "server" and have["browser"] and _bool(self.opts.get("flow_fallback"), True)
        self.log("🔀 Flow engine: " + ("automatic — Flow Server (ServiceNow SDK)" if engine == "server"
                                      else "Flow Designer in the browser")
                 + (" · browser fallback if an automatic build fails" if fallback else ""), "info", {})

        from flow_agent.browser import FlowCancelled
        from flow_agent.kb import SubflowKB
        kb = SubflowKB()
        plan_sub = {s["id"]: s for s in subflows} | {s["name"]: s for s in subflows}
        try:
            for s in subflows:
                if not self._reuse_subflow(s, kb):
                    self._build_item("Subflow", s, kb, engine, fallback)
            for f in flows:
                existing = self._hub_flow(f["name"], "flow")
                if existing:
                    self.record("Flow", f["name"], "reused", "already exists — left unchanged", "sys_hub_flow",
                                existing["sys_id"])
                    continue
                target, target_id, target_spec = "", "", plan_sub.get(f["subflow"])
                if f["subflow"]:
                    target = self.subflow_names.get(f["subflow"], "")
                    if not target and not target_spec:
                        rec = self._hub_flow(f["subflow"], "subflow")  # an existing subflow named in the plan
                        if self._is_live(rec, "subflow") or str((rec or {}).get("active")).lower() == "true":
                            target = f["subflow"]
                            self.subflow_ids[target] = rec["sys_id"]
                    if not target:
                        self.fail("Flow", f["name"], SNError(409, f"its subflow '{f['subflow']}' was not built or "
                                                                  "is not published in the instance"))
                        continue
                    target_id = self.subflow_ids.get(target) or (self._hub_flow(target, "subflow") or {}).get("sys_id", "")
                self._build_item("Flow", f, kb, engine, fallback, target, target_id, target_spec)
        except FlowCancelled as e:
            raise SNError(499, f"Flow building stopped: {e}")
        finally:
            if self._ui:
                self._ui.close()
                self._ui = None

    def _reuse_subflow(self, s: dict, kb) -> bool:
        """A subflow with the same name, or a similar published one from the subflow library, is wired instead."""
        existing = self._hub_flow(s["name"], "subflow")
        if existing:
            self._map_subflow(s, s["name"], existing["sys_id"])
            self.record("Subflow", s["name"], "reused", f"already exists (status "
                        f"{existing.get('status') or 'unknown'}) — left unchanged", "sys_hub_flow", existing["sys_id"])
            return True
        if not _bool(self.opts.get("reuse_subflows"), True):
            return False
        match = kb.best_match(self.sn.instance, s)
        if not match:
            return False
        score, entry = match
        rec = self._hub_flow(entry["published_name"], "subflow")
        if self._is_live(rec, "subflow") or str((rec or {}).get("active")).lower() == "true":
            self._map_subflow(s, entry["published_name"], rec["sys_id"])
            self.record("Subflow", s["name"], "reused",
                        f"wired to the published subflow '{entry['published_name']}' from the subflow library "
                        f"(similarity {score:.2f}; actions {', '.join(entry.get('action_types') or [])}) "
                        "instead of building a new one", "sys_hub_flow", rec["sys_id"])
            return True
        if not rec:
            kb.remove(self.sn.instance, entry["published_name"])
            self.log(f"🧹 '{entry['published_name']}' no longer exists in the instance — removed it from the "
                     "subflow library", "info", {})
        return False

    def _build_item(self, kind: str, spec: dict, kb, engine: str, fallback: bool, target: str = "",
                    target_id: str = "", target_spec: Optional[dict] = None):
        reason = ""
        if engine == "server":
            ok, reason = self._deploy_via_server(kind, spec, kb, target_id, target_spec)
            if ok:
                return
            if not fallback:
                self.fail(kind, spec["name"], SNError(502, f"automatic build failed — {reason}"))
                return
            self.log(f"⚠️ Automatic build of {kind.lower()} '{spec['name']}' failed: {reason} → opening Flow "
                     "Designer in the browser", "warning", {})
        ui = self._browser()
        if ui is None:
            self.fail(kind, spec["name"], SNError(502, f"Flow Designer browser could not start: {self._ui_error}"
                                                       + (f" (automatic build failed first: {reason})" if reason else "")))
            return
        self._browser_build(ui, kind, spec, kb, target, fallback_reason=reason)

    def _browser(self):
        """The Flow Designer browser, started on first use and shared by the rest of this build."""
        if self._ui or self._ui_error:
            return self._ui
        from flow_agent.browser import FlowCancelled, FlowDesignerUI, FlowUIError
        pause = _bool(self.opts.get("flow_pause"), True)
        self.log("🌐 Opening Flow Designer in a browser window" +
                 (" — the build pauses so you can check each step there" if pause else ""), "info", {})
        ui = FlowDesignerUI(self.sn.instance, self.sn.username, self.sn.password,
                            log=lambda m, st="info": self.log(m, st, {}), ask=self.ask, pause_steps=pause,
                            shots_dir=JOBS_DIR / "shots" / str(self.opts.get("_job_id") or "build"),
                            verify_tls=snc.verify_tls())
        try:
            ui.start()
        except (FlowUIError, FlowCancelled) as e:
            self._ui_error = str(e) or "unknown error"
            return None
        self._ui = ui
        return ui

    def _resolve_approver(self, kind: str, value: str) -> str:
        key = (kind, value.lower())
        if key not in self._approver_ids:
            if kind == "group":
                row = self.sn.get_one("sys_user_group", f"name={value}", "sys_id")
            elif "@" in value:
                row = self.sn.get_one("sys_user", f"email={value}", "sys_id")
            else:
                row = self.sn.get_one("sys_user", f"user_name={value}^ORname={value}", "sys_id")
            self._approver_ids[key] = (row or {}).get("sys_id", "")
        return self._approver_ids[key]

    def _subflow_table(self, s: dict) -> str:
        """Table a subflow works on: the trigger table of the first flow that calls it."""
        for f in self.plan["flows"]:
            if f["subflow"] in (s["id"], s["name"]):
                return self._resolve_table(f["trigger"]["table"]) or f["trigger"]["table"]
        return ""

    @staticmethod
    def _server_reason(data: dict, http_status: int) -> str:
        parts = [f"status {data.get('status') or http_status}"]
        msg = data.get("message") or data.get("error")
        if msg:
            parts.append(str(msg)[:240])
        for step in ("build", "install"):
            log = (data.get("logs") or {}).get(step) or {}
            if log.get("exitCode"):
                text = re.sub(r"\x1b\[[0-9;]*m", "", (log.get("stderr") or "") + "\n" + (log.get("stdout") or ""))
                lines = [l.strip() for l in text.splitlines() if l.strip() and not set(l.strip()) <= set("~^ ")]
                # the compiler's own diagnostics ("file.ts:12:5 - error TS2322: ...") say what is wrong;
                # the SDK's closing "Build failed" lines do not
                errs = [l for l in lines if re.search(r"\berror\b", l, re.I) and "[now-sdk]" not in l]
                parts.append(f"{step}: " + " | ".join((errs[:3] or lines[-3:]))[:600])
        return " — ".join(parts)

    def _deploy_via_server(self, kind: str, spec: dict, kb, target_id: str = "",
                           target_spec: Optional[dict] = None) -> tuple:
        """Generate, build and install one subflow/flow with the Flow Server. Returns (ok, reason)."""
        from flow_agent import server_specs as ss
        if not self.sn.instance.startswith("https://"):
            return False, "the Flow Server needs an https instance URL"
        try:
            if kind == "Subflow":
                path, key = "/deploy-subflow", "subflowSpec"
                body_spec = ss.subflow_spec(spec, self._resolve_approver, table=self._subflow_table(spec))
            else:
                if spec["subflow"] and not target_id:
                    return False, f"the sys_id of subflow '{spec['subflow']}' is unknown"
                path, key = "/deploy-flow", "flowSpec"
                table = self._resolve_table(spec["trigger"]["table"]) or spec["trigger"]["table"]
                body_spec = ss.flow_spec(spec, table, target_id, target_spec, self._resolve_approver)
        except SNError as e:
            return False, f"could not prepare it: {e}"
        body = {"instanceUrl": self.sn.instance, "username": self.sn.username, "password": self.sn.password,
                "scopeId": self.app["sys_id"], "scopeName": self.app.get("scope") or "global", key: body_spec}
        self.log(f"⚙️ {kind} '{spec['name']}': generating, building and installing it with the ServiceNow SDK "
                 "(takes a minute or two)…", "info", {})
        try:
            r = requests.post(flow_server_url() + path, json=body, timeout=900)
            data = r.json() if r.content else {}
        except Exception as e:
            return False, f"Flow Server unreachable ({str(e)[:150]})"
        if not isinstance(data, dict):
            data = {}
        if data.get("status") != "installed" or not data.get("sys_id"):
            return False, self._server_reason(data, r.status_code)
        sid = data["sys_id"]
        k = kind.lower()
        rec = self.sn.get_one("sys_hub_flow", f"sys_id={sid}", "sys_id,name,status,active,type")
        live = self._is_live(rec, k) or str((rec or {}).get("active")).lower() == "true"
        verb = "published" if kind == "Subflow" else "activated"
        note = (f"built automatically (Flow Server · ServiceNow SDK) and {verb}" if live else
                f"installed automatically, but ServiceNow does not show it as {verb} yet — open it in Flow Designer "
                f"and {'publish' if kind == 'Subflow' else 'activate'} it")
        note += " · installed with the application package (SDK installs are not captured in update sets)"
        if data.get("warning"):
            note += f" · {str(data['warning'])[:160]}"
        self.record(kind, spec["name"], "created", note, "sys_hub_flow", sid)
        self.created[-1]["no_rollback"] = True   # SDK-installed; remove via the app, not a raw delete
        self.created[-1]["capture_key"] = None   # not expected in sys_update_xml
        if not live:
            self.log(f"⚠️ {kind} '{spec['name']}' is not {verb} yet — it will not run until it is", "warning", {})
        if kind == "Subflow":
            self._map_subflow(spec, spec["name"], sid)
            if live:
                kb.add(self.sn.instance, spec["name"], spec, sid)
                self.log(f"🧠 Added subflow '{spec['name']}' to the subflow library for reuse", "info", {})
        return True, ""

    def _browser_build(self, ui, kind: str, spec: dict, kb, subflow_target: str = "", fallback_reason: str = ""):
        from flow_agent.browser import FlowUIError
        k = kind.lower()
        skipped: List[str] = []
        rec, live = None, False
        try:
            ui.open_new(kind, spec["name"], spec.get("description") or "")
            if kind == "Subflow":
                ui.fill_io(spec["inputs"], spec["outputs"])
            else:
                trig = dict(spec["trigger"])
                trig["table"] = self._resolve_table(trig["table"]) or trig["table"]
                ui.set_trigger(trig)
            skipped = [a["type"] for a in spec["actions"] if ui.add_action(a) == "skipped"]
            if subflow_target:
                ui.call_subflow(subflow_target, spec.get("subflow_inputs") or {})
            for _ in range(3):
                ui.publish(kind)
                for _ in range(5):
                    rec = self._hub_flow(spec["name"], k)
                    if self._is_live(rec, k):
                        live = True
                        break
                    time.sleep(3)
                if live:
                    break
                verb = "published" if kind == "Subflow" else "active"
                answer = ui.pause(f"{kind} is not {verb} yet",
                                  f"ServiceNow does not show '{spec['name']}' as {verb} (status: "
                                  f"{(rec or {}).get('status') or 'not saved'}). Fix the error shown in the browser "
                                  "(usually an empty mandatory field), then Retry — or Continue to keep it as a "
                                  "draft.", options=("retry", "continue", "cancel"), required=True)
                if answer != "retry":
                    break
        except FlowUIError as e:
            shot = ui.shot(f"failed {spec['name']}")
            self.fail(kind, spec["name"], SNError(502, f"Flow Designer: {e}" + (f" (screenshot: {shot})" if shot else "")
                                                       + (f" — automatic build failed first: {fallback_reason}"
                                                          if fallback_reason else "")))
            return
        if not rec:
            self.fail(kind, spec["name"], SNError(502, "Flow Designer did not save it — no sys_hub_flow record"))
            return
        verb = "published" if kind == "Subflow" else "activated"
        how = "built in the Flow Designer browser" + (" (fallback — automatic build failed)" if fallback_reason else "")
        note = (f"{how} and {verb}" if live else
                f"{how}, saved as a DRAFT — open it in Flow Designer and {'publish' if kind == 'Subflow' else 'activate'} it")
        if skipped:
            note += f"; skipped actions: {', '.join(skipped)}"
        self.record(kind, spec["name"], "created", note, "sys_hub_flow", rec["sys_id"])
        if not live:
            self.log(f"⚠️ {kind} '{spec['name']}' is a draft — it will not run until it is {verb}", "warning", {})
        elif kind == "Subflow":
            self._map_subflow(spec, spec["name"], rec["sys_id"])
            kb.add(self.sn.instance, spec["name"], spec, rec["sys_id"])
            self.log(f"🧠 Added subflow '{spec['name']}' to the subflow library for reuse", "info", {})

    # -- verification & rollback -------------------------------------------------------
    def verify_capture(self) -> dict:
        us = self.ctx.get("update_set") or {}
        if not us.get("sys_id"):
            return {}
        caps = snc.captured_changes(self.sn, us["sys_id"])
        names = [c.get("name") or "" for c in caps]
        expected = [c for c in self.created + self.modified if c.get("capture_key")]
        missing = [c for c in expected if not any(c["capture_key"] in n for n in names)]
        out = {"update_set": us, "captured_count": len(caps), "expected": len(expected),
               "missing": [{"kind": m["kind"], "name": m["name"], "sys_id": m["sys_id"]} for m in missing],
               "captured": [{"name": c.get("name"), "type": c.get("type"), "target": c.get("target_name")}
                            for c in caps[:200]]}
        if missing:
            self.log(f"⚠️ {len(missing)} created record(s) are NOT in update set '{us['name']}' — they will not "
                     f"move with it: " + ", ".join(m['name'] for m in missing[:8]), "warning", out)
        else:
            self.log(f"🔎 Verified: all {len(expected)} created records are captured in update set "
                     f"'{us['name']}' ({len(caps)} update records)", "ok", out)
        return out

    def rollback(self) -> List[dict]:
        out = []
        for m in reversed(self.modified):   # put changed existing records back first
            try:
                self.sn.patch(m["table"], m["sys_id"], m["old"])
                out.append({k: m[k] for k in ("table", "sys_id", "kind", "name")} | {"ok": True, "restored": m["old"]})
            except SNError as e:
                out.append({k: m[k] for k in ("table", "sys_id", "kind", "name")} | {"ok": False, "note": str(e)})
        for c in reversed(self.created):
            if c.get("no_rollback"):
                out.append({**c, "ok": False, "note": "installed via SDK — remove from the application manually"})
                continue
            try:
                self.sn.delete(c["table"], c["sys_id"])
                out.append({**c, "ok": True})
            except SNError as e:
                out.append({**c, "ok": False, "note": str(e)})
        ok = sum(1 for o in out if o["ok"])
        self.log(f"↩️ Rollback: undid {ok}/{len(out)} changes made by this run (created records removed, changed "
                 f"records restored)", "warning" if ok < len(out) else "ok", {"rollback": out})
        return out

    # -- orchestration -------------------------------------------------------------------
    def run(self) -> dict:
        # Req 5: real dry_run support
        if self.opts.get("dry_run"):
            self.log("🔍 DRY RUN MODE — no records will be written to ServiceNow", "info", {})
            preview = self.step_dry_run_preview()
            return {
                "status": "dry_run",
                "dry_run": True,
                "preview": preview,
                "results": self.results,
                "counts": {"preview": len(preview)},
                "rollback": [],
                "verification": {},
                "app": {},
                "update_set": None,
            }

        steps = [("Release compatibility check", self.step_check_release_compat),
                 ("Application scope & update set", self.step_scope_and_update_set),
                 ("Instance assessment", self.step_assess_instance),
                 ("Best practices check", self.step_global_scope_warning),
                 ("Roles", self.step_roles), ("Tables", self.step_tables), ("Fields & choices", self.step_fields),
                 ("Access controls", self.step_acls), ("Script includes", self.step_script_includes),
                 ("Business rules", self.step_business_rules), ("Client scripts", self.step_client_scripts),
                 ("UI policies", self.step_ui_policies), ("Data policies", self.step_data_policies),
                 ("Assignment rules", self.step_assignment_rules), ("Changes to existing records",
                                                                     self.step_modifications),
                 ("Notifications", self.step_notifications),
                 ("Subflows & flows", self.step_flows)]
        failed_step = None
        error = None
        for label, fn in steps:
            self.log(f"▶ {label}", "info", {})
            try:
                fn()
            except Exception as e:  # stop_on_error
                failed_step, error = label, e
                self.log(f"❌ Stopped at '{label}': {e}", "failed", {})
                break
        rollback = []
        verification = {}
        if error and self.opts.get("rollback_on_error", True) and (self.created or self.modified):
            rollback = self.rollback()
        else:
            try:
                verification = self.verify_capture()
            except SNError as e:
                self.log(f"⚠️ Could not read sys_update_xml to verify capture: {e}", "warning", {})
        if self.opts.get("restore_previous_context") and self.ctx:
            for note in snc.restore_context(self.sn, self.ctx):
                self.log(f"↩️ {note}", "info", {})
        counts: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            counts.setdefault(r["kind"], {}).setdefault(r["status"], 0)
            counts[r["kind"]][r["status"]] += 1
        n_failed = sum(1 for r in self.results if r["status"] == "failed")
        n_warn = sum(1 for r in self.results if r["status"] in ("warning", "skipped"))
        status = "failed" if error else ("completed_with_issues" if n_failed or n_warn or
                                          (verification.get("missing")) else "completed")
        return {"status": status, "failed_step": failed_step, "error": str(error) if error else None,
                "results": self.results, "counts": counts, "rollback": rollback, "verification": verification,
                "app": self.app, "update_set": (self.ctx or {}).get("update_set")}


# ════════════════════════════════════════════════════════════════════════════
# Public entry points
# ════════════════════════════════════════════════════════════════════════════

DEFAULT_FLOW_SERVER_URL = "http://127.0.0.1:3000"


def flow_server_url() -> str:
    return (os.getenv("FLOW_SERVER_URL") or DEFAULT_FLOW_SERVER_URL).rstrip("/")


def flow_server_up(timeout: float = 2.0) -> bool:
    """True when the Flow Server (ServiceNow SDK deployer, v6/flow-server) answers /health."""
    try:
        return requests.get(flow_server_url() + "/health", timeout=timeout).ok
    except Exception:
        return False


def flow_engines() -> dict:
    """Which flow engines this agent can use right now."""
    from flow_agent.browser import playwright_available
    return {"server": flow_server_up(), "server_url": flow_server_url(), "browser": playwright_available()}


def flow_engine(plan: dict, opts: dict, have: Optional[dict] = None) -> Optional[str]:
    """Primary engine: 'server' (automatic, Flow Server / ServiceNow SDK), 'browser' (Flow Designer UI via
    Playwright) or None (nothing available). auto = the Flow Server whenever it is running; the browser is then
    only used per item, as a fallback, when the automatic build of that item fails."""
    if not (plan.get("flows") or plan.get("subflows")):
        return None
    have = have or flow_engines()
    want = str(opts.get("flow_engine") or os.getenv("FLOW_ENGINE") or "auto").lower()
    if want in ("server", "browser"):
        return want if have[want] else None
    if have["server"]:
        return "server"
    return "browser" if have["browser"] else None


# ── pause gate: the Flow Designer engine waits here for the user's answer from the Build App screen ──
_gates: Dict[str, dict] = {}
PAUSE_TIMEOUT = int(os.getenv("FLOW_PAUSE_TIMEOUT", "1800"))


def _make_ask(job_id: str, log: Callable[[str, str, dict], None]) -> Callable[[dict], dict]:
    def ask(pause: dict) -> dict:
        job = _jobs[job_id]
        pause_id = uuid.uuid4().hex[:8]
        gate = {"pause_id": pause_id, "event": threading.Event(), "answer": None}
        with _lock:
            _gates[job_id] = gate
        job["pause"] = {**pause, "id": pause_id, "since": _now(), "timeout_s": PAUSE_TIMEOUT}
        job["status"] = "waiting_input"
        log(f"⏸️ Waiting for you: {pause.get('title')}", "info", {})
        answered = gate["event"].wait(PAUSE_TIMEOUT)
        with _lock:
            _gates.pop(job_id, None)
        job["pause"] = None
        job["status"] = "running"
        answer = gate["answer"] if answered else {"action": "timeout"}
        log(f"▶ {pause.get('title')} → {answer.get('action')}", "info", {})
        return answer
    return ask


def respond(job_id: str, pause_id: str, action: str, value: str = "") -> bool:
    """Answer the pause a running build is waiting on. Returns False when there is no such pause."""
    if action not in ("continue", "skip", "retry", "cancel"):
        raise ValueError(f"unknown action '{action}'")
    with _lock:
        gate = _gates.get(job_id)
        if not gate or (pause_id and gate["pause_id"] != pause_id):
            return False
        gate["answer"] = {"action": action, "value": value}
        gate["event"].set()
    return True


def validate_plan(plan: dict) -> dict:
    norm = normalize_plan(plan)
    engines = flow_engines() if (norm["flows"] or norm["subflows"]) else         {"server": False, "server_url": flow_server_url(), "browser": False}
    b = Builder(None, norm, {}, lambda *a: None)
    issues = b.validate()
    return {"valid": not issues, "issues": issues, "plan": norm,
            "notes": b.notes,
            "counts": {k: len(norm[k]) for k in ("roles", "tables", "acls", "script_includes", "business_rules",
                                                  "client_scripts", "ui_policies", "notifications", "subflows",
                                                  "flows")},
            "flow_server_configured": bool(os.getenv("FLOW_SERVER_URL")),
            "flow_engines": engines, "flow_engine": flow_engine(norm, {}, engines)}


def start_build(sn: SNClient, plan: dict, opts: dict) -> str:
    job_id = uuid.uuid4().hex[:10]
    job = {"job_id": job_id, "status": "queued", "steps": [], "started": _now(), "options":
           {k: v for k, v in opts.items() if "pass" not in k}, "instance": sn.instance}
    with _lock:
        _jobs[job_id] = job
    _save(job)
    return job_id


def run_build(job_id: str, sn: SNClient, plan: dict, opts: dict):
    job = _jobs[job_id]
    job["status"] = "running"

    def log(msg: str, status: str, data: dict):
        job["steps"].append({"msg": msg, "status": status, "time": _now(),
                             **({"data": data} if data and status in ("failed", "warning") else {})})
        logger.info(f"[build {job_id}] {msg}")
        _save(job)

    norm = normalize_plan(plan)
    opts = {**opts, "_job_id": job_id}
    b = Builder(sn, norm, opts, log)
    b.ask = _make_ask(job_id, log)
    issues = b.validate()
    for n in b.notes:
        log(f"ℹ️ {n}", "info", {})
    if issues and not opts.get("ignore_validation"):
        for i in issues:
            log(f"❌ Plan check: {i}", "failed", {})
        job.update(status="failed", error="Plan failed validation — fix the plan or set ignore_validation",
                   issues=issues, ended=_now())
        _save(job)
        return
    for i in issues:
        log(f"⚠️ Plan check (ignored): {i}", "warning", {})
    try:
        result = b.run()
    except Exception as e:  # unexpected bug — never report success
        logger.exception("build crashed")
        result = {"status": "failed", "error": f"internal error: {e}", "results": b.results}
    job.update(result)
    job["ended"] = _now()
    job["summary"] = {
        "created": sum(1 for r in b.results if r["status"] == "created"),
        "reused": sum(1 for r in b.results if r["status"] == "reused"),
        "skipped": sum(1 for r in b.results if r["status"] == "skipped"),
        "failed": sum(1 for r in b.results if r["status"] == "failed"),
        "update_set": (b.ctx or {}).get("update_set", {}).get("name"),
        "captured_in_update_set": (result.get("verification") or {}).get("captured_count"),
        "not_captured": len((result.get("verification") or {}).get("missing") or []),
        "api_calls": sn.calls,
    }
    if job["status"] == "dry_run":
        log("🔍 Dry run finished — nothing was written to ServiceNow", "ok", {})
        return
    icon = {"completed": "🎉", "completed_with_issues": "⚠️", "failed": "❌"}[job["status"]]
    log(f"{icon} Build {job['status'].replace('_', ' ')} — created {job['summary']['created']}, reused "
        f"{job['summary']['reused']}, skipped {job['summary']['skipped']}, failed {job['summary']['failed']}",
        {"completed": "ok", "completed_with_issues": "warning", "failed": "failed"}[job["status"]], {})
    _save(job)
