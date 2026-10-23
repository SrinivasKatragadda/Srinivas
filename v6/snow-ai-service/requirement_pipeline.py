"""
SoftProdigy Snow Agent — requirement-driven implementation pipeline.

One user request / user story goes through the same steps every time:

  1. understand   the AI extracts a structured requirement (table, field effects, custom logic, criteria); the code then
                  checks it against the live instance: the table, every field, choice values and referenced records
                  (groups, users) must exist. Anything missing becomes a question for the user — never a guess.
  2. inspect      targeted reads of what already acts on that table (and its parents): business rules, client scripts,
                  UI policies + actions, data policies + rules, assignment rules, the priority lookup when relevant,
                  plus what earlier requirements on this instance built.
  3. analyse      deterministic, name-independent comparison of what the requirement needs with what the existing
                  configuration does (fields written, mandatory / read-only / visible states, assignments, conditions).
                  Only an exact declarative match counts as "already satisfied". A script that touches the same field,
                  a different state for the same field, or an inactive exact match becomes a decision for the user.
                  The AI is never asked whether two things are equivalent.
  4. plan         one coordinated plan: reuse / create / modify (only after the user decides) / blocked / skip.
  5. deploy       only with confirm=true. Re-reads the instance first; if anything relevant changed since planning, the
                  plan is rebuilt and confirmation is asked again. Built with sn_build.Builder (update set, rollback of
                  created records and restore of old values on failure).
  6. verify       every record written is read back from ServiceNow and compared with what was sent; duplicates are
                  counted; the requirement's effects are re-checked against the re-read configuration. Runtime
                  behaviour (browser-side scripts, server rules firing) is NOT exercised and is reported as such.
  7. report       requirement · inspection · implementation · deployment · verification · outcome, built only from
                  tool results.

Instance profiles (capabilities, columns, release) and requirement history are kept per ServiceNow instance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import groq_client
import sn_build
import sn_client as snc
from sn_client import SNClient, SNError

logger = logging.getLogger("requirement_pipeline")

PROFILE_TTL = int(os.getenv("REQ_PROFILE_TTL", "900"))   # seconds a read-only instance profile is reused for planning
STORE_DIR = Path(os.getenv("AGENT_JOBS_DIR", Path(__file__).resolve().parent / "jobs")) / "requirements"

# Mechanism -> (table, columns the agent writes or reads). Columns are checked against sys_dictionary per instance.
MECHANISMS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "business_rule": ("sys_script", ("name", "collection", "when", "action_insert", "action_update", "action_delete",
                                     "filter_condition", "condition", "script", "active", "advanced", "order")),
    "client_script": ("sys_script_client", ("name", "table", "type", "field", "script", "active", "ui_type")),
    "ui_policy": ("sys_ui_policy", ("short_description", "table", "conditions", "active", "on_load",
                                    "reverse_if_false")),
    "ui_policy_action": ("sys_ui_policy_action", ("ui_policy", "table", "field", "mandatory", "visible", "disabled")),
    "data_policy": ("sys_data_policy2", ("short_description", "model_table", "conditions", "active", "enforce_ui",
                                         "apply_import_set", "apply_soap")),
    "data_policy_rule": ("sys_data_policy_rule", ("sys_data_policy", "table", "field", "mandatory", "disabled")),
    "assignment_rule": ("sysrule_assignment", ("name", "table", "condition", "group", "user", "order", "active")),
    "script_include": ("sys_script_include", ("name", "api_name", "script", "client_callable", "active", "access")),
    "priority_lookup": ("dl_u_priority", ("impact", "urgency", "priority")),
}
# Which mechanism tables a mechanism needs (parent + child records)
NEEDS = {"ui_policy": ("ui_policy", "ui_policy_action"), "data_policy": ("data_policy", "data_policy_rule")}
DECLARATIVE = {"ui_policy_action", "data_policy_rule", "assignment_rule"}
STATE_EFFECTS = ("mandatory", "read_only", "visible")
EFFECTS = STATE_EFFECTS + ("set_value", "assign")

_CONFIG_WORDS = re.compile(
    r"\b(make|set|ensure|enforce|require[sd]?|mandatory|read[\s-]?only|hide|hidden|visible|populate|default|"
    r"assign|route|validate|prevent|block|abort|automatic(?:ally)?|should|must|when(?:ever)?|on (?:insert|update|"
    r"submit|load|change)|business rule|client script|ui policy|data policy|script include|create a rule|add a rule)\b",
    re.I)
_PERM = re.compile(r"\b403\b|forbidden|lacks the role|not authori[sz]ed|insufficient", re.I)


# ════════════════════════════════════════════════════════════════════════════
# Small helpers
# ════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def instance_key(url: str) -> str:
    """Stable, filesystem-safe key for one ServiceNow instance (profiles, plans and history never mix instances)."""
    host = (urlparse(snc.normalize_instance(url)).netloc or url).lower()
    return re.sub(r"[^a-z0-9]+", "_", host).strip("_") or "instance"


def norm_query(q: str) -> str:
    """Normalise an encoded query for equality checks: order of AND terms does not matter, OR / NQ order does."""
    parts = [p.strip() for p in str(q or "").split("^") if p.strip() and not p.strip().startswith("ORDERBY")]
    if parts == ["EQ"]:
        return ""
    parts = [p for p in parts if p != "EQ"]
    if any(p.startswith(("OR", "NQ")) for p in parts):
        return "^".join(parts)
    return "^".join(sorted(parts))


def _b(v: Any) -> str:
    return "true" if str(v).strip().lower() in ("true", "1", "yes", "on") else "false"


def _tri(v: Any) -> str:
    s = str(v if v is not None else "").strip().lower()
    return s if s in ("true", "false") else "ignore"


def classify_error(err: Any) -> str:
    """insufficient_permissions | unavailable_on_instance | execution_failure"""
    text = str(err)
    status = getattr(err, "status", None)
    if status == 403 or _PERM.search(text):
        return "insufficient_permissions"
    if status in (400, 404) and re.search(r"invalid table|not found|no such table", text, re.I):
        return "unavailable_on_instance"
    return "execution_failure"


def looks_like_configuration(text: str) -> bool:
    """Cheap, deterministic gate so plain look-ups never reach the planning pipeline (no AI call, no instance scan)."""
    return bool(_CONFIG_WORDS.search(text or ""))


def _json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


# ════════════════════════════════════════════════════════════════════════════
# Per-instance storage: plans and requirement history
# ════════════════════════════════════════════════════════════════════════════

_store_lock = threading.Lock()


def _dir(url: str) -> Path:
    d = STORE_DIR / instance_key(url)
    (d / "plans").mkdir(parents=True, exist_ok=True)
    return d


def save_plan(url: str, plan: dict) -> None:
    with _store_lock:
        (_dir(url) / "plans" / f"{plan['plan_id']}.json").write_text(json.dumps(_json_safe(plan), indent=1),
                                                                    encoding="utf-8")


def load_plan(url: str, plan_id: str) -> Optional[dict]:
    if not re.fullmatch(r"[0-9a-f]{12,32}", plan_id or ""):
        return None
    path = _dir(url) / "plans" / f"{plan_id}.json"     # only this instance's directory is ever read
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def history(url: str) -> List[dict]:
    path = _dir(url) / "history.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except ValueError:
        return []


def _add_history(url: str, entry: dict) -> None:
    with _store_lock:
        items = history(url)
        items = [h for h in items if h.get("plan_id") != entry.get("plan_id")] + [_json_safe(entry)]
        (_dir(url) / "history.json").write_text(json.dumps(items[-500:], indent=1), encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
# Instance profile (cached, read-only) and table schema
# ════════════════════════════════════════════════════════════════════════════

_profiles: Dict[Tuple[str, str], Tuple[float, dict]] = {}
_schemas: Dict[Tuple[str, str], Tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def clear_caches() -> None:
    with _cache_lock:
        _profiles.clear()
        _schemas.clear()


def _hierarchy(sn: SNClient, table: str) -> List[str]:
    """[table, parent, grandparent, ...] from sys_db_object.super_class (stops on loops / depth 10)."""
    out, cur = [], table
    for _ in range(10):
        row = sn.get_one("sys_db_object", f"name={cur}", "sys_id,name,super_class")
        if not row:
            break
        out.append(cur)
        parent = snc.ref_value(row.get("super_class"))
        if not parent:
            break
        prow = sn.get_one("sys_db_object", f"sys_id={parent}", "name")
        if not prow or not prow.get("name") or prow["name"] in out:
            break
        cur = prow["name"]
    return out


def instance_profile(sn: SNClient, force: bool = False) -> dict:
    """Capabilities of the connected instance for this user. Reused for PROFILE_TTL seconds when planning; always
    rebuilt (force=True) before a deployment."""
    key = (instance_key(sn.instance), sn.username)
    with _cache_lock:
        hit = _profiles.get(key)
    if hit and not force and time.time() - hit[0] < PROFILE_TTL:
        return {**hit[1], "cached": True}
    prof: Dict[str, Any] = {"instance": sn.instance, "instance_key": key[0], "user": sn.username, "built_at": _now(),
                            "mechanisms": {}, "roles": [], "release": {}}
    try:
        prof["release"] = sn.release_info()
    except Exception:  # release detection is informative only
        prof["release"] = {}
    try:
        user = sn.user()
        rows = sn.query("sys_user_has_role", f"user={user['sys_id']}", "role.name", limit=500)
        prof["roles"] = sorted({r.get("role.name") or "" for r in rows} - {""})
    except SNError as e:
        prof["roles_error"] = str(e)
    for mech, (table, cols) in MECHANISMS.items():
        info: Dict[str, Any] = {"table": table, "available": False, "status": "unknown", "missing_columns": []}
        try:
            if not sn.table_exists(table):
                info.update(status="unavailable_on_instance",
                            detail=f"Table {table} does not exist on this instance (plugin not installed?)")
            else:
                info.update(available=True, status="available")
                try:
                    tables = _hierarchy(sn, table) or [table]
                    have = {r.get("element") for r in sn.query(
                        "sys_dictionary", f"nameIN{','.join(tables)}^elementIN{','.join(cols)}", "element",
                        limit=200)}
                    missing = [c for c in cols if c not in have]
                    info["missing_columns"] = missing
                    if missing:
                        info.update(status="incompatible_schema",
                                    detail=f"{table} has no column(s) {', '.join(missing)} on this instance")
                except SNError as e:
                    info["schema_check"] = f"not verified ({classify_error(e)}: {e.message})"
        except SNError as e:
            info.update(status=classify_error(e), detail=str(e))
        prof["mechanisms"][mech] = info
    with _cache_lock:
        _profiles[key] = (time.time(), prof)
    return {**prof, "cached": False}


def planner_preflight(sn: SNClient, mcp_tools: Optional[Iterable[str]] = None,
                      mcp_error: str = "") -> dict:
    """Collect read-only execution prerequisites for the release-aware planner."""
    profile = instance_profile(sn, force=True)
    release = profile.get("release") or {}
    build_tag = str(release.get("build_tag") or "")
    patch_match = re.search(r"patch[-_ ]?(\d+)", build_tag, re.I)
    user = {}
    identity_error = ""
    try:
        user = sn.user()
    except SNError as exc:
        identity_error = str(exc)

    roles_verified = not profile.get("roles_error") and bool(user)
    roles = profile.get("roles") or []
    admin = any(str(role).lower() == "admin" for role in roles)

    production_value = ""
    try:
        production_value = str(sn.property("glide.installation.production") or "").strip().lower()
    except SNError:
        pass
    environment = {"type": "unknown", "verified": False, "source": "unavailable"}
    if production_value in ("true", "false"):
        environment = {"type": "production" if production_value == "true" else "non-production",
                       "verified": True, "source": "glide.installation.production"}
    else:
        host = (urlparse(sn.instance).hostname or "").lower()
        if re.search(r"(^|[.-])(dev|development|test|testing|qa|uat|stage|staging|sandbox|pdi)([0-9.-]|$)", host):
            environment = {"type": "likely-non-production", "verified": False, "source": "instance hostname"}

    def preference(name: str) -> str:
        if not user.get("sys_id"):
            return ""
        row = sn.get_one("sys_user_preference", f"user={user['sys_id']}^name={name}", "value")
        return snc.ref_value((row or {}).get("value"))

    scope = {"sys_id": "", "name": "", "verified": False}
    update_set = {"sys_id": "", "name": "", "state": "", "application_sys_id": "",
                  "verified": False}
    scope_error = ""
    try:
        scope_id = preference("apps.current_app")
        if scope_id == "global":
            scope = {"sys_id": "global", "name": "Global", "verified": True}
        elif scope_id:
            row = sn.get_one("sys_scope", f"sys_id={scope_id}", "sys_id,name,scope")
            if row:
                scope = {"sys_id": row.get("sys_id", scope_id), "name": row.get("name") or row.get("scope") or "",
                         "verified": True}
        update_set_id = preference("sys_update_set")
        if update_set_id:
            row = sn.get_one("sys_update_set", f"sys_id={update_set_id}",
                             "sys_id,name,state,application")
            if row:
                update_set = {"sys_id": row.get("sys_id", update_set_id), "name": row.get("name") or "",
                              "state": snc.ref_value(row.get("state")),
                              "application_sys_id": snc.ref_value(row.get("application")),
                              "verified": True}
    except SNError as exc:
        scope_error = str(exc)

    tool_names = sorted(set(str(name) for name in (mcp_tools or [])))
    required_tools = ["snow_plan_requirement", "snow_implement_requirement"]
    missing_tools = [name for name in required_tools if name not in tool_names]
    checks = {
        "release": bool(release.get("build_name") and release.get("build_tag") and release.get("major")),
        "environment": environment["verified"],
        "identity": roles_verified,
        "scope": scope["verified"],
        "update_set": update_set["verified"] and update_set["state"].lower() == "in progress"
        and bool(scope["sys_id"]) and update_set["application_sys_id"] == scope["sys_id"],
        "permissions": roles_verified and admin,
        "mcp": not mcp_error and not missing_tools,
    }
    messages = {
        "release": "ServiceNow release family and build/patch tag could not both be verified.",
        "environment": "Environment type was not confirmed by the instance production property.",
        "identity": identity_error or "Integration identity and roles could not be verified.",
        "scope": scope_error or "The current application scope could not be verified.",
        "update_set": "The current Update Set is missing, not in progress, or belongs to a different scope.",
        "permissions": "Admin permission was not verified; the planner cannot prove write authorization read-only.",
        "mcp": mcp_error or ("Required MCP tools unavailable: " + ", ".join(missing_tools)),
    }
    return {
        "instance_url": sn.instance,
        "environment": environment,
        "release": {"family": release.get("build_name") or release.get("release") or "unknown",
                    "build_tag": build_tag or "unknown",
                    "patch_level": patch_match.group(1) if patch_match else "unknown",
                    "verified": checks["release"]},
        "identity": {"username": user.get("user_name") or sn.username,
                     "display_name": user.get("name") or "", "sys_id": user.get("sys_id") or "",
                     "roles": roles, "verified": roles_verified},
        "permissions": {"admin_role": admin, "verified": checks["permissions"],
                        "detail": "Read-only role check; write ACLs are not probed."},
        "scope": scope,
        "update_set": update_set,
        "mcp": {"available": not bool(mcp_error), "tool_count": len(tool_names),
                "required_tools": required_tools, "missing_tools": missing_tools,
                "error": mcp_error},
        "checks": checks,
        "execution_ready": all(checks.values()),
        "blockers": [messages[key] for key, passed in checks.items() if not passed],
        "checked_at": _now(),
    }


def mechanism_status(profile: dict, mech: str) -> Tuple[bool, str, str]:
    """(usable, reason, kind). Checks the mechanism's parent and child tables; kind is the failing table's status."""
    for m in NEEDS.get(mech, (mech,)):
        info = profile["mechanisms"].get(m) or {}
        if info.get("status") != "available":
            return (False, info.get("detail") or f"{MECHANISMS[m][0]}: {info.get('status', 'unknown')}",
                    info.get("status") or "unknown")
    return True, "", "available"


def table_schema(sn: SNClient, table: str, force: bool = False) -> dict:
    """Columns of a table and its parents: {element: {type, reference, label}} plus the hierarchy."""
    key = (instance_key(sn.instance), table)
    with _cache_lock:
        hit = _schemas.get(key)
    if hit and not force and time.time() - hit[0] < PROFILE_TTL:
        return hit[1]
    tables = _hierarchy(sn, table)
    out: Dict[str, Any] = {"table": table, "exists": bool(tables), "hierarchy": tables, "fields": {}}
    if tables:
        rows = sn.query("sys_dictionary", f"nameIN{','.join(tables)}^elementISNOTEMPTY",
                        "name,element,internal_type,reference,column_label", limit=2000)
        for r in rows:
            el = r.get("element")
            # the most specific table wins (incident.state over task.state)
            if el and (el not in out["fields"] or tables.index(r.get("name")) < tables.index(
                    out["fields"][el]["defined_on"])):
                out["fields"][el] = {"type": snc.ref_value(r.get("internal_type")), "label": r.get("column_label", ""),
                                     "reference": snc.ref_value(r.get("reference")), "defined_on": r.get("name")}
    with _cache_lock:
        _schemas[key] = (time.time(), out)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 1. Understand
# ════════════════════════════════════════════════════════════════════════════

_EXTRACT_SYSTEM = ("You turn ServiceNow requests into a strict structured requirement. You never invent business data: "
                   "group names, users, values, conditions or tables that the text does not state are left empty and "
                   "listed in missing_information. JSON only.")

_EXTRACT_PROMPT = """Request / user story:
{text}

Return JSON:
{{
  "summary": "<one sentence>",
  "purpose": "<business purpose if stated, else ''>",
  "request_kind": "configuration | read_only | records | mixed",
  "table": "<ServiceNow table name the behaviour applies to, e.g. incident; '' if the text does not say>",
  "effects": [
    {{"field": "<column name>", "effect": "mandatory | read_only | visible | set_value | assign",
      "value": "<true/false for mandatory/read_only/visible; the literal value for set_value; the group NAME for assign; '' if not stated>",
      "side": "client | server | both",
      "events": ["insert", "update"],
      "condition": "<ServiceNow encoded query when it applies only sometimes, e.g. priority=1; '' = always>"}}
  ],
  "logic": [
    {{"description": "<behaviour that needs a script (validation, calculations, copying values, messages)>",
      "side": "server | client", "events": ["insert", "update"], "client_type": "onLoad | onChange | onSubmit | ''",
      "field": "<field for onChange, else ''>", "reusable_class": "<the Script Include class name exactly as the text gives it, only if a Script Include is requested, else ''>",
      "client_callable": "<true only if the text says client scripts / GlideAjax must call the Script Include, false if it says server-side only, else null>"}}
  ],
  "application_scope": "<application scope name such as x_acme_app only if the text names one, else ''>",
  "acceptance_criteria": ["<each criterion as written>"],
  "assumptions": ["<anything you had to interpret>"],
  "missing_information": ["<question for the user about anything required that the text does not state>"]
}}

Rules:
- side: "client" = on the form only; "server" = for every record (API, imports, integrations, background scripts);
  "both" only when the text asks for both (e.g. "on the form and for API-created records"). If the text says only
  "mandatory" or "on the form", use "client" and add an assumption saying server-side enforcement was not requested.
- Use effects for declarative behaviour (mandatory / read-only / visible / set a value / assign a group). Use logic
  only for behaviour that needs code. Validation ("prevent saving when ...") is logic.
- A requested Script Include is ONE logic item with side "server" and reusable_class = its name as written. If the
  text gives no name, leave reusable_class '' and ask for it in missing_information. A Script Include is never a
  record such as an incident, even when it is about incidents.
- Never fill in a group, user, priority mapping or value that is not in the text: leave value '' and ask in
  missing_information.
- request_kind is read_only when the user only wants to look something up (no change to configuration or records).
"""


def extract_requirement(text: str) -> dict:
    data = groq_client.generate_json(_EXTRACT_PROMPT.format(text=str(text)[:6000]), system=_EXTRACT_SYSTEM,
                                     max_tokens=2500)
    if not isinstance(data, dict):
        raise ValueError("AI returned no requirement object")
    return data


def normalize_requirement(raw: dict) -> dict:
    """Coerce an extracted / hand-written requirement into one strict shape (no network)."""
    r = dict(raw or {})
    out: Dict[str, Any] = {
        "summary": str(r.get("summary") or "").strip()[:300],
        "purpose": str(r.get("purpose") or "").strip()[:500],
        "request_kind": str(r.get("request_kind") or "configuration").strip().lower(),
        "table": re.sub(r"[^a-z0-9_]", "", str(r.get("table") or "").strip().lower()),
        "acceptance_criteria": [str(c).strip() for c in r.get("acceptance_criteria") or [] if str(c).strip()],
        "assumptions": [str(c).strip() for c in r.get("assumptions") or [] if str(c).strip()],
        "missing_information": [str(c).strip() for c in r.get("missing_information") or [] if str(c).strip()],
        "effects": [], "logic": [],
    }
    if out["request_kind"] not in ("configuration", "read_only", "records", "mixed"):
        out["request_kind"] = "configuration"
    for i, e in enumerate(r.get("effects") or []):
        if not isinstance(e, dict):
            continue
        effect = str(e.get("effect") or "").strip().lower().replace("-", "_").replace(" ", "_")
        value = e.get("value")
        if effect in ("hidden", "hide"):
            effect, value = "visible", "false"
        if effect in ("readonly",):
            effect = "read_only"
        if effect == "validate":   # validation needs code
            out["logic"].append({"description": e.get("message") or f"Validate {e.get('field')}",
                                 "side": e.get("side") or "server", "events": e.get("events") or ["insert", "update"],
                                 "client_type": "onSubmit", "field": e.get("field") or "", "reusable_class": ""})
            continue
        if effect not in EFFECTS:
            out["missing_information"].append(f"Effect '{effect}' for field '{e.get('field')}' is not supported — "
                                              "describe it as mandatory, read-only, visible, set a value or assign.")
            continue
        field = re.sub(r"[^a-z0-9_]", "", str(e.get("field") or "").lower())
        if effect == "assign" and not field:
            field = "assignment_group"
        if effect in STATE_EFFECTS:
            value = "" if value in (None, "") else _b(value)
        side = str(e.get("side") or "").lower()
        if side not in ("client", "server", "both"):
            side = "client" if effect in STATE_EFFECTS else "server"
        if effect == "visible":
            side = "client"            # visibility exists only on the form
        if effect == "assign":
            side = "server"            # assignment happens when the record is saved
        events = [ev for ev in (e.get("events") or ["insert", "update"]) if ev in ("insert", "update", "delete")]
        if effect == "set_value" and side == "client":   # setting a value on the form needs a client script
            out["logic"].append({"description": f"On the form, set {field} to '{value}'"
                                                + (f" when {e.get('condition')}" if e.get("condition") else ""),
                                 "side": "client", "events": events or ["insert", "update"],
                                 "client_type": "onLoad", "field": "", "reusable_class": ""})
            continue
        if effect == "set_value" and side == "both":
            side = "server"
            out["assumptions"].append(f"{field} is set by a server rule on save; the form shows it after saving.")
        out["effects"].append({"id": f"E{i + 1}", "field": field, "effect": effect,
                               "value": "" if value is None else str(value).strip(), "side": side,
                               "events": events or ["insert", "update"],
                               "condition": str(e.get("condition") or "").strip()})
    for j, lg in enumerate(r.get("logic") or []):
        if not isinstance(lg, dict) or not str(lg.get("description") or "").strip():
            continue
        side = "client" if str(lg.get("side")).lower() == "client" else "server"
        ctype = {"onload": "onLoad", "onchange": "onChange", "onsubmit": "onSubmit"}.get(
            str(lg.get("client_type") or "").replace("_", "").lower(), "onSubmit" if side == "client" else "")
        cc = lg.get("client_callable")
        cc = cc if isinstance(cc, bool) else {"true": True, "false": False}.get(str(cc).strip().lower())
        out["logic"].append({"description": str(lg["description"]).strip()[:1000], "side": side,
                             "events": [ev for ev in (lg.get("events") or ["insert", "update"])
                                        if ev in ("insert", "update", "delete")] or ["insert", "update"],
                             "client_type": ctype,
                             "field": re.sub(r"[^a-z0-9_]", "", str(lg.get("field") or "").lower()),
                             "reusable_class": re.sub(r"[^A-Za-z0-9_]", "", str(lg.get("reusable_class") or "")),
                             "client_callable": cc})
    scope = str(r.get("application_scope") or "").strip().lower()
    out["application_scope"] = scope if re.fullmatch(r"x_[a-z0-9_]{2,40}|global", scope) else ""
    for k, lg in enumerate(out["logic"]):
        lg["id"] = f"L{k + 1}"
    if not out["summary"]:
        out["summary"] = (f"Requirement on {out['table']}" if out["table"] else "Requirement")
    return out


def _resolve_reference(sn: SNClient, ref_table: str, value: str) -> Tuple[Optional[str], str]:
    """Exact lookup of a referenced record by sys_id or display name. Returns (sys_id, problem)."""
    if snc.is_sys_id(value):
        row = sn.get_one(ref_table, f"sys_id={value}", "sys_id")
        return (row["sys_id"], "") if row else (None, f"No {ref_table} record has sys_id {value}")
    fields = {"sys_user_group": ("name",), "sys_user": ("user_name", "name")}.get(ref_table, ("name",))
    for f in fields:
        try:
            rows = sn.query(ref_table, f"{f}={value}" + ("^active=true" if ref_table in ("sys_user_group", "sys_user")
                                                          else ""), "sys_id", limit=2)
        except SNError as e:
            return None, f"Could not look up '{value}' in {ref_table}: {e.message}"
        if len(rows) == 1:
            return rows[0]["sys_id"], ""
        if len(rows) > 1:
            return None, f"'{value}' matches several {ref_table} records — which one (sys_id)?"
    return None, f"No active {ref_table} record named '{value}' exists on this instance"


def validate_against_instance(sn: SNClient, req: dict, profile: dict) -> dict:
    """Check the requirement's table, fields, choice values and references live. Adds questions, never guesses."""
    req = json.loads(json.dumps(req))
    issues = req["missing_information"]
    if req["request_kind"] == "read_only":
        return req
    if not req["table"]:
        # a Script Include is not tied to a table; everything else is
        if req["effects"] or any(not lg.get("reusable_class") for lg in req["logic"]):
            issues.append("Which table does this apply to (for example incident)?")
        return req
    schema = table_schema(sn, req["table"])
    req["hierarchy"] = schema["hierarchy"]
    if not schema["exists"]:
        issues.append(f"Table '{req['table']}' does not exist on this instance. Which table did you mean?")
        return req
    fields = schema["fields"]
    for e in req["effects"]:
        f = fields.get(e["field"])
        if not f:
            close = [n for n, d in fields.items() if e["field"] and (e["field"] in n or
                                                                      e["field"] in str(d.get("label", "")).lower())]
            issues.append(f"Field '{e['field']}' is not on {req['table']} or its parent tables"
                          + (f" (similar: {', '.join(sorted(close)[:5])})" if close else "") + ". Which field?")
            continue
        e["field_type"], e["reference"] = f["type"], f["reference"]
        if e["effect"] in STATE_EFFECTS and e["value"] == "":
            e["value"] = "true"
        if e["effect"] in ("set_value", "assign") and e["value"] == "":
            what = "group" if e["effect"] == "assign" else "value"
            issues.append(f"Which {what} should {req['table']}.{e['field']} be set to"
                          + (f" when {e['condition']}" if e["condition"] else "") + "?")
            continue
        if e["effect"] in ("set_value", "assign") and f["type"] == "reference":
            sid, problem = _resolve_reference(sn, f["reference"] or "sys_user_group", e["value"])
            if problem:
                issues.append(problem + f" (for {req['table']}.{e['field']}). Which existing record should be used?")
            else:
                e["display_value"], e["value"] = e["value"], sid
        elif e["effect"] == "set_value" and f["type"] in ("choice", "integer") and fields.get(e["field"]):
            try:
                choices = sn.query("sys_choice", f"nameIN{','.join(schema['hierarchy'])}^element={e['field']}"
                                                 f"^inactive=false", "value,label", limit=200)
            except SNError:
                choices = []
            if choices:
                by_label = {str(c.get("label", "")).lower(): c.get("value") for c in choices}
                values = {str(c.get("value")) for c in choices}
                if e["value"] not in values:
                    if e["value"].lower() in by_label:
                        e["display_value"], e["value"] = e["value"], by_label[e["value"].lower()]
                    else:
                        issues.append(f"'{e['value']}' is not a valid value for {req['table']}.{e['field']} "
                                      f"(valid: {', '.join(sorted(values)[:15])}). Which value?")
    for lg in req["logic"]:
        if lg["client_type"] == "onChange" and lg["field"] and lg["field"] not in fields:
            issues.append(f"Field '{lg['field']}' (for the onChange logic) is not on {req['table']}. Which field?")
    req["missing_information"] = list(dict.fromkeys(issues))
    return req


# ════════════════════════════════════════════════════════════════════════════
# 2. Inspect — targeted reads of what already acts on the table
# ════════════════════════════════════════════════════════════════════════════

def _safe_query(sn: SNClient, notes: List[dict], what: str, table: str, query: str, fields: str,
                limit: int = 300) -> List[dict]:
    try:
        return sn.query(table, query, fields, limit=limit)
    except SNError as e:
        notes.append({"what": what, "table": table, "kind": classify_error(e), "detail": str(e)})
        return []


def inspect(sn: SNClient, req: dict, profile: dict) -> dict:
    tables = req.get("hierarchy") or ([req["table"]] if req.get("table") else [])
    inv: Dict[str, Any] = {"tables": tables, "business_rules": [], "client_scripts": [], "ui_policies": [],
                           "data_policies": [], "assignment_rules": [], "priority_lookup": [], "notes": [],
                           "not_inspected": ["Flow Designer flows (trigger table lookup is not implemented — "
                                             "check flows on this table manually)"]}
    if not tables:
        return inv
    tin = ",".join(tables)
    notes = inv["notes"]
    avail = lambda m: (profile["mechanisms"].get(m) or {}).get("status") in ("available", "incompatible_schema")
    if avail("business_rule"):
        inv["business_rules"] = _safe_query(
            sn, notes, "business rules", "sys_script", f"collectionIN{tin}",
            "sys_id,name,collection,when,active,order,action_insert,action_update,action_delete,action_query,"
            "filter_condition,condition,script,sys_updated_on")
    if avail("client_script"):
        inv["client_scripts"] = _safe_query(
            sn, notes, "client scripts", "sys_script_client", f"tableIN{tin}",
            "sys_id,name,table,type,field,active,script,sys_updated_on")
    if avail("ui_policy"):
        pols = _safe_query(sn, notes, "UI policies", "sys_ui_policy", f"tableIN{tin}",
                           "sys_id,short_description,table,conditions,active,inherit,run_scripts,script_true,"
                           "script_false,sys_updated_on")
        acts = _safe_query(sn, notes, "UI policy actions", "sys_ui_policy_action",
                           f"ui_policyIN{','.join(p['sys_id'] for p in pols)}",
                           "sys_id,ui_policy,field,mandatory,visible,disabled,sys_updated_on", 1000) if pols else []
        for p in pols:
            p["actions"] = [a for a in acts if snc.ref_value(a.get("ui_policy")) == p["sys_id"]]
        inv["ui_policies"] = pols
    if avail("data_policy"):
        dps = _safe_query(sn, notes, "data policies", "sys_data_policy2", f"model_tableIN{tin}",
                          "sys_id,short_description,model_table,conditions,active,enforce_ui,sys_updated_on")
        rules = _safe_query(sn, notes, "data policy rules", "sys_data_policy_rule",
                            f"sys_data_policyIN{','.join(d['sys_id'] for d in dps)}",
                            "sys_id,sys_data_policy,field,mandatory,disabled,sys_updated_on", 1000) if dps else []
        for d in dps:
            d["rules"] = [r for r in rules if snc.ref_value(r.get("sys_data_policy")) == d["sys_id"]]
        inv["data_policies"] = dps
    if avail("assignment_rule"):
        inv["assignment_rules"] = _safe_query(
            sn, notes, "assignment rules", "sysrule_assignment", f"tableIN{tin}",
            "sys_id,name,table,condition,group,user,active,order,sys_updated_on")
    if any(e["field"] == "priority" and e["effect"] == "set_value" for e in req.get("effects") or []) \
            and avail("priority_lookup"):
        inv["priority_lookup"] = _safe_query(sn, notes, "priority lookup", "dl_u_priority", "",
                                             "sys_id,impact,urgency,priority,sys_updated_on", 100)
    for m in ("business_rule", "client_script", "ui_policy", "data_policy", "assignment_rule"):
        info = profile["mechanisms"].get(m) or {}
        if info.get("status") not in ("available", "incompatible_schema"):
            inv["notes"].append({"what": m.replace("_", " ") + "s", "table": info.get("table"),
                                 "kind": info.get("status"), "detail": info.get("detail", "")})
    origin = {}
    for h in history(sn.instance):
        for a in h.get("artifacts") or []:
            if a.get("sys_id"):
                origin[a["sys_id"]] = {"plan_id": h.get("plan_id"), "summary": h.get("summary")}
    inv["history"] = [{"plan_id": h.get("plan_id"), "summary": h.get("summary"), "status": h.get("status"),
                       "at": h.get("at")} for h in history(sn.instance) if h.get("table") in tables][-20:]
    inv["origin"] = origin
    return inv


def fingerprint(inv: dict) -> str:
    rows = []
    for key in ("business_rules", "client_scripts", "ui_policies", "data_policies", "assignment_rules",
                "priority_lookup"):
        for r in inv.get(key) or []:
            rows.append(f"{key}:{r.get('sys_id')}:{r.get('sys_updated_on')}:{r.get('active')}")
            for c in (r.get("actions") or []) + (r.get("rules") or []):
                rows.append(f"{key}.child:{c.get('sys_id')}:{c.get('sys_updated_on')}")
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()[:20]


# ════════════════════════════════════════════════════════════════════════════
# 3. Analyse — name-independent footprints of what existing configuration does
# ════════════════════════════════════════════════════════════════════════════

_SRV_WRITES = (re.compile(r"current\.setValue\(\s*['\"](\w+)['\"]\s*,\s*([^)]*)\)"),
               re.compile(r"current\.(\w+)\.set(?:Display)?Value\(\s*([^)]*)\)"),
               re.compile(r"current\.(\w+)\s*=(?!=)\s*([^;\n]*)"))
_CLI = {"set_value": re.compile(r"g_form\.setValue\(\s*['\"](\w+)['\"]\s*,\s*([^)]*)\)"),
        "mandatory": re.compile(r"g_form\.setMandatory\(\s*['\"](\w+)['\"]\s*,\s*(true|false)"),
        "visible": re.compile(r"g_form\.set(?:Display|Visible)\(\s*['\"](\w+)['\"]\s*,\s*(true|false)"),
        "read_only": re.compile(r"g_form\.set(?:ReadOnly|Readonly|Disabled)\(\s*['\"](\w+)['\"]\s*,\s*(true|false)")}


def _lit(v: str) -> str:
    return str(v or "").strip().strip("'\"")


def script_footprint(script: str, side: str) -> List[dict]:
    """Field effects a script has, from its code (comments stripped). Deterministic; no AI."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", script or "", flags=re.S)
    out = []
    if side == "server":
        for rx in _SRV_WRITES:
            for f, v in rx.findall(code):
                if f not in ("prototype",) and not f.startswith("set"):
                    out.append({"field": f, "effect": "set_value", "value": _lit(v)})
        if re.search(r"setAbortAction\(\s*true\s*\)", code):
            out.append({"field": "*", "effect": "validate", "value": ""})
    else:
        for effect, rx in _CLI.items():
            for f, v in rx.findall(code):
                out.append({"field": f, "effect": effect, "value": _lit(v)})
    seen, uniq = set(), []
    for x in out:
        k = (x["field"], x["effect"], x["value"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


_LITERAL = r"(?:'([^'\\]*)'|\"([^\"\\]*)\"|(-?\d+(?:\.\d+)?|true|false))"
_SIMPLE_STMT = (re.compile(rf"^current\.setValue\(\s*['\"](\w+)['\"]\s*,\s*{_LITERAL}\s*\)$"),
                re.compile(rf"^current\.(\w+)\s*=\s*{_LITERAL}$"))


def simple_setters(script: str) -> Optional[Dict[str, str]]:
    """{field: literal} when the WHOLE rule body is nothing but literal assignments to current fields, else None.
    This is the only kind of script whose behaviour is treated as provably equal to a 'set value' requirement."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", script or "", flags=re.S).strip()
    m = re.match(r"^\(\s*function\s+executeRule\s*\([^)]*\)\s*\{(.*)\}\s*\)\s*\(\s*current\s*,\s*previous\s*\)\s*;?$",
                 code, re.S)
    body = m.group(1) if m else code
    stmts = [s.strip() for s in re.split(r";|\n", body) if s.strip()]
    if not stmts:
        return None
    out = {}
    for s in stmts:
        for rx in _SIMPLE_STMT:
            mm = rx.match(s)
            if mm:
                field, a, b, c = mm.groups()
                if field.startswith("set"):
                    return None
                out[field] = next(v for v in (a, b, c) if v is not None)
                break
        else:
            return None
    return out


def footprints(inv: dict) -> List[dict]:
    """Every field effect the existing configuration has, with where it comes from."""
    out: List[dict] = []

    def add(kind, rec, name, table, eff, side, events, condition, active, parent=None, enforce_ui=False, **extra):
        out.append({"kind": kind, "sys_id": rec.get("sys_id"), "name": name, "table": table, "field": eff["field"],
                    "effect": eff["effect"], "value": eff["value"], "side": side, "events": events,
                    "condition": condition, "active": active, "parent": parent, "enforce_ui": enforce_ui,
                    "origin": inv.get("origin", {}).get(rec.get("sys_id")) or
                              (inv.get("origin", {}).get((parent or {}).get("sys_id")) if parent else None),
                    **extra})

    for b in inv.get("business_rules") or []:
        if b.get("when") == "display" or _b(b.get("action_query")) == "true" and not any(
                _b(b.get(k)) == "true" for k in ("action_insert", "action_update", "action_delete")):
            continue
        events = [ev for ev in ("insert", "update", "delete") if _b(b.get(f"action_{ev}")) == "true"]
        simple = simple_setters(b.get("script") or "") if b.get("when") == "before" and not (b.get("condition") or
                                                                                            "").strip() else None
        for eff in script_footprint(b.get("script") or "", "server"):
            add("business_rule", b, b.get("name"), b.get("collection"), eff, "server", events,
                b.get("filter_condition") or "", _b(b.get("active")) == "true",
                simple=simple is not None and eff["field"] in simple)
    for c in inv.get("client_scripts") or []:
        events = [{"onLoad": "load", "onChange": f"change:{c.get('field')}", "onSubmit": "submit"}.get(c.get("type"),
                                                                                                      "other")]
        for eff in script_footprint(c.get("script") or "", "client"):
            add("client_script", c, c.get("name"), c.get("table"), eff, "client", events, "",
                _b(c.get("active")) == "true")
        if c.get("type") == "onSubmit" and re.search(r"return\s+false", c.get("script") or ""):
            add("client_script", c, c.get("name"), c.get("table"), {"field": "*", "effect": "validate", "value": ""},
                "client", events, "", _b(c.get("active")) == "true")
    for p in inv.get("ui_policies") or []:
        for a in p.get("actions") or []:
            for effect, col in (("mandatory", "mandatory"), ("visible", "visible"), ("read_only", "disabled")):
                if _tri(a.get(col)) != "ignore":
                    add("ui_policy_action", a, p.get("short_description"), p.get("table"),
                        {"field": a.get("field"), "effect": effect, "value": _tri(a.get(col))}, "client",
                        ["load", "change"], p.get("conditions") or "", _b(p.get("active")) == "true", parent=p)
        for col in ("script_true", "script_false"):
            if _b(p.get("run_scripts")) == "true" and p.get(col):
                for eff in script_footprint(p[col], "client"):
                    add("ui_policy_script", p, p.get("short_description"), p.get("table"), eff, "client",
                        ["load", "change"], p.get("conditions") or "", _b(p.get("active")) == "true")
    for d in inv.get("data_policies") or []:
        for r in d.get("rules") or []:
            for effect, col in (("mandatory", "mandatory"), ("read_only", "disabled")):
                if _tri(r.get(col)) != "ignore":
                    add("data_policy_rule", r, d.get("short_description"), d.get("model_table"),
                        {"field": r.get("field"), "effect": effect, "value": _tri(r.get(col))},
                        "both" if _b(d.get("enforce_ui")) == "true" else "server", ["insert", "update"],
                        d.get("conditions") or "", _b(d.get("active")) == "true", parent=d,
                        enforce_ui=_b(d.get("enforce_ui")) == "true")
    for a in inv.get("assignment_rules") or []:
        for field, col in (("assignment_group", "group"), ("assigned_to", "user")):
            val = snc.ref_value(a.get(col))
            if val:
                add("assignment_rule", a, a.get("name"), a.get("table"),
                    {"field": field, "effect": "assign", "value": val}, "server", ["insert", "update"],
                    a.get("condition") or "", _b(a.get("active")) == "true")
    return out


def _sides(fp: dict) -> set:
    return {"client", "server"} if fp["side"] == "both" else {fp["side"]}


def _needed(e: dict) -> set:
    return {"client", "server"} if e["side"] == "both" else {e["side"]}


def _kind_matches(e: dict, fp: dict) -> bool:
    if e["effect"] == fp["effect"]:
        return True
    # an assignment (group) and a script that sets the same reference field act on the same thing
    return {e["effect"], fp["effect"]} == {"assign", "set_value"}


def compare(e: dict, fps: List[dict]) -> dict:
    """Relations between one requirement effect and existing footprints on the same field (any name)."""
    res = {"satisfies": [], "conflicts": [], "overlaps": [], "inactive_matches": [], "related": [], "covered": set()}
    cond = norm_query(e["condition"])
    for fp in fps:
        if fp["field"] != e["field"] or not _kind_matches(e, fp):
            continue
        same_cond = norm_query(fp["condition"]) == cond
        same_val = str(fp["value"]) == str(e["value"])
        ref = {k: fp[k] for k in ("kind", "sys_id", "name", "table", "value", "condition", "active", "side", "origin")}
        if fp["kind"] in DECLARATIVE and fp["effect"] == e["effect"]:
            if same_val and same_cond:
                if fp["active"]:
                    res["satisfies"].append(ref)
                    res["covered"] |= _sides(fp) & _needed(e)
                else:
                    res["inactive_matches"].append(ref)
            elif fp["active"] and not same_val and (same_cond or not cond or not norm_query(fp["condition"])):
                res["conflicts"].append({**ref, "why": f"sets {e['field']} {fp['effect']}={fp['value']} "
                                                       f"(requirement: {e['value']})"})
            elif fp["active"]:
                res["related"].append({**ref, "why": "same field and effect under a different condition"})
        elif fp.get("simple") and e["effect"] == "set_value" and same_val and same_cond \
                and set(e["events"]) <= set(fp["events"]):
            # a before rule whose whole body is literal assignments: provably sets this value on these events
            if fp["active"]:
                res["satisfies"].append({**ref, "why": "before rule whose entire script sets this literal value"})
                res["covered"] |= {"server"} & _needed(e)
            else:
                res["inactive_matches"].append(ref)
        elif fp["active"]:
            why = (f"its script sets {e['field']} ({fp['effect']}={fp['value'] or '?'})" if fp["kind"] != "assignment_rule"
                   else f"assigns {e['field']} = {fp['value']}")
            if same_val or not fp["value"]:
                res["overlaps"].append({**ref, "why": why + " — may already do this; scripts are not assumed equivalent"})
            else:
                res["conflicts"].append({**ref, "why": why + f" — requirement wants {e['value']}"})
    res["covered"] = sorted(res["covered"])
    return res


# ════════════════════════════════════════════════════════════════════════════
# 4. Plan
# ════════════════════════════════════════════════════════════════════════════

_LOGIC_SYSTEM = ("You are a senior ServiceNow developer. You write one small, complete, production-quality script for "
                 "one requirement. JSON only.")
_LOGIC_PROMPT = """Write the {kind} for this requirement on table {table}.

Requirement: {summary}
Behaviour to implement: {description}
Side: {side}; events: {events}; client type: {client_type}; field: {field}
Table columns you may use (name: type): {columns}
Existing configuration on this table that you must NOT duplicate (already handles these effects): {existing}
{feedback}
Rules:
- business_rule: "script" is only the BODY (it is wrapped in executeRule). Use current / previous / gs. Never call
  current.update(). To reject a save: gs.addErrorMessage('...'); current.setAbortAction(true);
- client_script: "script" is only the BODY using g_form / g_user (no GlideRecord, no gs). onSubmit returns false to
  stop the save.
- script_include: full class, `var {cls} = Class.create();` ... `type: '{cls}'`. Client callable: {callable}. When
  client callable, `{cls}.prototype = Object.extendsObject(AbstractAjaxProcessor, {{ ... }})` and each method client
  scripts call reads its input with this.getParameter('sysparm_<name>') and returns a string.
- Use only the columns listed. Do not hard-code sys_ids, group names or values that the requirement does not give.
Return JSON: {{"script": "...", "filter_condition": "<encoded query or ''>", "description": "<one sentence>"}}"""


def _unique_name(base: str, taken: Iterable[str]) -> str:
    taken_l = {str(t or "").strip().lower() for t in taken}
    name, n = base[:80], 2
    while name.lower() in taken_l:
        name = f"{base[:74]} #{n}"
        n += 1
    return name


def _taken_names(inv: dict) -> List[str]:
    return ([b.get("name") for b in inv["business_rules"]] + [c.get("name") for c in inv["client_scripts"]] +
            [p.get("short_description") for p in inv["ui_policies"]] +
            [d.get("short_description") for d in inv["data_policies"]] +
            [a.get("name") for a in inv["assignment_rules"]])


def generate_logic(lg: dict, req: dict, schema: dict, existing: List[dict], issues: Optional[List[str]] = None,
                   previous: Optional[dict] = None) -> dict:
    kind = ("script_include" if lg["reusable_class"] else "client_script" if lg["side"] == "client"
            else "business_rule")
    cols = ", ".join(f"{n}: {d['type']}" for n, d in sorted(schema.get("fields", {}).items())[:150])
    exist = "; ".join(f"{x['kind']} '{x['name']}' {x['effect']} {x['field']}={x['value']}" for x in existing[:25]) or "none"
    feedback = ""
    if issues:
        feedback = ("YOUR PREVIOUS SCRIPT FAILED THESE CHECKS — fix them:\n" + "\n".join(f"- {i}" for i in issues) +
                    "\nPrevious: " + json.dumps(previous or {})[:3000])
    data = groq_client.generate_json(_LOGIC_PROMPT.format(
        kind=kind, table=req["table"], summary=req["summary"], description=lg["description"], side=lg["side"],
        events=",".join(lg["events"]), client_type=lg["client_type"] or "-", field=lg["field"] or "-", columns=cols,
        existing=exist, feedback=feedback, cls=lg["reusable_class"] or "MyClass",
        callable="yes" if lg.get("client_callable") else "no"), system=_LOGIC_SYSTEM, max_tokens=3000)
    if not isinstance(data, dict) or not str(data.get("script") or "").strip():
        raise ValueError("AI returned no script")
    return {"kind": kind, "script": str(data["script"]), "filter_condition": str(data.get("filter_condition") or ""),
            "description": str(data.get("description") or lg["description"])[:300]}


def _logic_spec(lg: dict, gen: dict, req: dict, name: str) -> Tuple[str, dict]:
    if gen["kind"] == "script_include":
        return "script_include", {"name": lg["reusable_class"], "script": gen["script"],
                                  "client_callable": lg.get("client_callable") is True,
                                  "description": gen["description"]}
    if gen["kind"] == "client_script":
        return "client_script", {"name": name, "table": req["table"], "type": lg["client_type"] or "onSubmit",
                                 "field": lg["field"], "script": gen["script"], "description": gen["description"]}
    return "business_rule", {"name": name, "table": req["table"], "when": "before",
                             "insert": "insert" in lg["events"], "update": "update" in lg["events"],
                             "delete": "delete" in lg["events"], "filter_condition": gen["filter_condition"],
                             "script": gen["script"], "description": gen["description"]}


def _check_specs(items: List[dict]) -> List[str]:
    """Run the Builder's own validation (syntax, server APIs in client code, current.update ...) on the specs."""
    plan = _builder_plan(items, "check")
    return sn_build.Builder(None, plan, {}, lambda *a: None).validate()


def _item(iid: str, action: str, mechanism: str, table: str, name: str, covers: List[str], reason: str,
          spec: Optional[dict] = None, existing: Optional[List[dict]] = None, options: Optional[List[str]] = None,
          recommended: str = "", modify: Optional[dict] = None, side: str = "") -> dict:
    return {"id": iid, "action": action, "mechanism": mechanism, "table": table, "name": name, "covers": covers,
            "reason": reason, "spec": spec, "existing": existing or [], "options": options or [],
            "recommended": recommended, "modify": modify, "side": side}


def _policy_spec(kind: str, name: str, table: str, condition: str, effects: List[dict], enforce_ui: bool = False):
    if kind == "ui_policy":
        acts = {}
        for e in effects:
            a = acts.setdefault(e["field"], {"field": e["field"], "mandatory": None, "visible": None, "read_only": None})
            a[e["effect"]] = e["value"] == "true"
        return {"short_description": name, "table": table, "conditions": condition, "actions": list(acts.values())}
    rules = {}
    for e in effects:
        r = rules.setdefault(e["field"], {"field": e["field"], "mandatory": None, "read_only": None})
        r[e["effect"]] = e["value"] == "true"
    return {"short_description": name, "table": table, "conditions": condition, "enforce_ui": enforce_ui,
            "rules": list(rules.values())}


def _priority_item(e: dict, req: dict, inv: dict, profile: dict) -> Optional[dict]:
    """priority on task tables is computed from impact + urgency by the Priority Lookup rules (dl_u_priority)."""
    if e["field"] != "priority" or e["effect"] != "set_value" or "task" not in (req.get("hierarchy") or []):
        return None
    ok, why, _kind = mechanism_status(profile, "priority_lookup")
    terms = dict(t.split("=", 1) for t in norm_query(e["condition"]).split("^") if "=" in t and "OR" not in t)
    if not ok or not {"impact", "urgency"} <= set(terms):
        return _item(e["id"], "needs_decision", "business_rule", req["table"], "", [e["id"]],
                     "priority is normally calculated from impact and urgency by the Priority Lookup rules"
                     + (f" ({why})" if not ok else "") + "; a business rule that sets it directly would fight that "
                     "calculation, so none is created. Restate the requirement with the impact and urgency this "
                     "priority is for (e.g. condition impact=1^urgency=1), or skip it.",
                     options=["skip"], recommended="skip")
    row = next((r for r in inv["priority_lookup"] if str(r.get("impact")) == terms["impact"]
                and str(r.get("urgency")) == terms["urgency"]), None)
    if not row:
        return _item(e["id"], "blocked", "priority_lookup", "dl_u_priority", "", [e["id"]],
                     f"No Priority Lookup row for impact={terms['impact']} urgency={terms['urgency']} — "
                     "creating lookup rows is not automated")
    ref = {"kind": "priority_lookup", "sys_id": row["sys_id"], "name": f"impact {terms['impact']} / urgency "
           f"{terms['urgency']}", "table": "dl_u_priority", "value": row.get("priority"), "active": True}
    if str(row.get("priority")) == e["value"]:
        return _item(e["id"], "reuse", "priority_lookup", "dl_u_priority", ref["name"], [e["id"]],
                     f"Priority Lookup already gives priority {e['value']} for this impact/urgency", existing=[ref])
    return _item(e["id"], "needs_decision", "priority_lookup", "dl_u_priority", ref["name"], [e["id"]],
                 f"The Priority Lookup gives priority {row.get('priority')} for impact={terms['impact']} "
                 f"urgency={terms['urgency']}; the requirement wants {e['value']}. Changing the row changes priority "
                 "for every record with these values.", existing=[ref], options=["modify", "skip"],
                 recommended="", modify={"table": "dl_u_priority", "sys_id": row["sys_id"], "kind": "Priority lookup",
                                         "name": ref["name"], "changes": {"priority": e["value"]}})


def build_plan(sn: SNClient, req: dict, inv: dict, profile: dict, schema: dict,
               generated: Optional[Dict[str, dict]] = None, use_ai: bool = True,
               include_logic: bool = True) -> Tuple[List[dict], Dict[str, dict]]:
    """Plan items for every effect and logic item. `generated` holds scripts already written for this plan
    (re-planning before a deployment never asks the AI again)."""
    generated = dict(generated or {})
    fps = footprints(inv)
    items: List[dict] = []
    taken = _taken_names(inv)
    table = req["table"]
    short = re.sub(r"\s+", " ", req["summary"]).strip().rstrip(".")[:60] or f"Requirement on {table}"
    groups: Dict[Tuple[str, str, bool], List[dict]] = {}

    for e in req["effects"]:
        pri = _priority_item(e, req, inv, profile)
        if pri:
            items.append(pri)
            continue
        rel = compare(e, fps)
        existing = rel["satisfies"] + rel["conflicts"] + rel["overlaps"] + rel["inactive_matches"] + rel["related"]
        remaining = sorted(_needed(e) - set(rel["covered"]))
        label = f"{e['field']} {e['effect']}={e.get('display_value') or e['value']}" + (
            f" when {e['condition']}" if e["condition"] else "")
        if rel["conflicts"] or rel["overlaps"]:
            spec_kind, spec = _effect_spec(e, remaining or sorted(_needed(e)), table, short, taken)
            opts = ["create", "skip"] + (["reuse"] if rel["overlaps"] and not rel["conflicts"] else [])
            modify = None
            decl = [c for c in rel["conflicts"] if c["kind"] in ("ui_policy_action", "data_policy_rule")]
            if decl and e["effect"] in STATE_EFFECTS:
                c = decl[0]
                col = {"read_only": "disabled"}.get(e["effect"], e["effect"])
                modify = {"table": MECHANISMS[c["kind"]][0], "sys_id": c["sys_id"], "kind": c["kind"].replace("_", " "),
                          "name": f"{c['name']} → {e['field']}", "changes": {col: e["value"]}}
                opts.append("modify")
            what = "conflicts with" if rel["conflicts"] else "may overlap"
            items.append(_item(e["id"], "needs_decision", spec_kind, table, spec.get("name") or spec.get(
                "short_description", ""), [e["id"]], f"{label}: existing configuration {what} this requirement "
                "(compared by behaviour, not by name). Decide: create a new rule anyway, reuse/modify the existing "
                "one, or skip.", spec=spec, existing=existing, options=opts, modify=modify, side="+".join(remaining)))
            continue
        if not remaining:
            items.append(_item(e["id"], "reuse", rel["satisfies"][0]["kind"], table, rel["satisfies"][0]["name"],
                               [e["id"]], f"{label}: already done by existing active configuration (exact field, "
                               "state and condition match)", existing=existing))
            continue
        if rel["inactive_matches"]:
            m = rel["inactive_matches"][0]
            parent_kind = {"ui_policy_action": "sys_ui_policy", "data_policy_rule": "sys_data_policy2",
                           "assignment_rule": "sysrule_assignment", "business_rule": "sys_script"}[m["kind"]]
            parent_id = m["sys_id"] if m["kind"] in ("assignment_rule", "business_rule") else next(
                (fp["parent"]["sys_id"] for fp in fps if fp["sys_id"] == m["sys_id"] and fp.get("parent")), m["sys_id"])
            spec_kind, spec = _effect_spec(e, remaining, table, short, taken)
            items.append(_item(e["id"], "needs_decision", spec_kind, table, m["name"], [e["id"]],
                               f"{label}: an INACTIVE record already does exactly this ('{m['name']}'). It may have been "
                               "switched off on purpose — activate it, create a new one, or skip.", spec=spec,
                               existing=existing, options=["modify", "create", "skip"],
                               modify={"table": parent_kind, "sys_id": parent_id, "kind": m["kind"].replace("_", " "),
                                       "name": m["name"], "changes": {"active": "true"}}, side="+".join(remaining)))
            continue
        if e["effect"] in STATE_EFFECTS:
            if remaining == ["client"]:
                key = ("ui_policy", norm_query(e["condition"]), False)
            else:
                key = ("data_policy", norm_query(e["condition"]), remaining == ["client", "server"])
            groups.setdefault(key, []).append({**e, "_remaining": remaining, "_existing": existing})
            continue
        spec_kind, spec = _effect_spec(e, remaining, table, short, taken)
        items.append(_item(e["id"], "create", spec_kind, table, spec.get("name", ""), [e["id"]],
                           f"{label}: not done by any existing configuration", spec=spec, existing=existing,
                           side="+".join(remaining)))

    for (kind, cond, enforce_ui), effs in groups.items():
        side_note = {"ui_policy": "form", "data_policy": "form + server" if enforce_ui else "server"}[kind]
        name = _unique_name(f"{short} ({side_note})", taken)
        taken.append(name)
        spec = _policy_spec(kind, name, table, effs[0]["condition"], effs, enforce_ui)
        why = ("declarative " + ("UI policy (form only)" if kind == "ui_policy" else
               "data policy (enforced for every record" + (", also applied on the form)" if enforce_ui else ")")))
        items.append(_item("+".join(e["id"] for e in effs), "create", kind, table, name, [e["id"] for e in effs],
                           f"{', '.join(e['field'] + ' ' + e['effect'] for e in effs)}: not done yet — {why}",
                           spec=spec, existing=[x for e in effs for x in e["_existing"]],
                           side="+".join(effs[0]["_remaining"])))

    # custom logic: scripts written by the AI, then checked like everything else
    schema_fields = schema
    for lg in (req["logic"] if include_logic else []):
        gen = generated.get(lg["id"])
        if not gen and use_ai:
            try:
                gen = generate_logic(lg, req, schema_fields, fps)
                mech, spec = _logic_spec(lg, gen, req, _unique_name(f"{short} ({lg['id']})", taken))
                issues = _check_specs([{"action": "create", "mechanism": mech, "spec": spec}])
                if issues:
                    gen = generate_logic(lg, req, schema_fields, fps, issues, gen)
            except (groq_client.GroqError, ValueError) as ex:
                items.append(_item(lg["id"], "blocked", "script", table, "", [lg["id"]],
                                   f"Could not write the script for '{lg['description']}': {ex}"))
                continue
        if not gen:
            items.append(_item(lg["id"], "blocked", "script", table, "", [lg["id"]],
                               "No script was generated for this logic (AI not used)"))
            continue
        generated[lg["id"]] = gen
        name = _unique_name(f"{short} ({lg['id']})", taken)
        taken.append(name)
        mech, spec = _logic_spec(lg, gen, req, name)
        issues = _check_specs([{"action": "create", "mechanism": mech, "spec": spec}])
        if issues:
            items.append(_item(lg["id"], "blocked", mech, table, name, [lg["id"]],
                               "The generated script failed validation: " + "; ".join(issues), spec=spec))
            continue
        if mech == "script_include":
            scope = ((req.get("scope_app") or {}).get("scope")) or "global"
            ex = sn.get_one("sys_script_include", f"api_name={scope}.{spec['name']}^ORname={spec['name']}",
                            "sys_id,name,api_name")
            if ex:
                items.append(_item(lg["id"], "needs_decision", mech, "", spec["name"], [lg["id"]],
                                   f"A script include named {spec['name']} already exists; it is not overwritten. Reuse "
                                   "it (its code is not checked for equivalence) or skip.", spec=spec,
                                   existing=[{"kind": "script_include", "sys_id": ex["sys_id"], "name": ex.get("name"),
                                              "table": "sys_script_include", "active": True}],
                                   options=["reuse", "skip"]))
                continue
        gen_fp = script_footprint(spec["script"], "client" if mech == "client_script" else "server")
        overlaps = []
        for g in gen_fp:
            for fp in fps:
                if fp["active"] and fp["field"] == g["field"] and fp["effect"] == g["effect"] and g["field"] != "*":
                    overlaps.append({k: fp[k] for k in ("kind", "sys_id", "name", "table", "value", "condition",
                                                        "active", "origin")} | {"why": f"also sets {g['field']} "
                                                                                       f"({g['effect']})"})
        if overlaps:
            items.append(_item(lg["id"], "needs_decision", mech, table, name, [lg["id"]],
                               f"'{lg['description']}': the new script would change fields that existing configuration "
                               "already changes. Decide: create anyway (both run), reuse the existing, or skip.",
                               spec=spec, existing=overlaps, options=["create", "reuse", "skip"]))
        else:
            items.append(_item(lg["id"], "create", mech, table, name, [lg["id"]],
                               f"'{lg['description']}': custom logic — no existing configuration changes these fields",
                               spec=spec))

    # capability gate: a mechanism the instance cannot provide blocks the item (never silently swapped)
    for it in items:
        if it["action"] in ("create", "needs_decision") and it["mechanism"] in MECHANISMS:
            ok, why, kind = mechanism_status(profile, it["mechanism"])
            if not ok:
                it.update(action="blocked", blocked_kind=kind, reason=it["reason"] + f" — BLOCKED: {why}")
    return items, generated


def _effect_spec(e: dict, remaining: List[str], table: str, short: str, taken: List[str]) -> Tuple[str, dict]:
    """The single-effect spec used when an item is created (also used after a 'create' decision)."""
    if e["effect"] in STATE_EFFECTS:
        if remaining == ["client"] or e["effect"] == "visible":
            name = _unique_name(f"{short} ({e['field']} form)", taken)
            taken.append(name)
            return "ui_policy", _policy_spec("ui_policy", name, table, e["condition"], [e])
        enforce = remaining == ["client", "server"]
        name = _unique_name(f"{short} ({e['field']} {'form + server' if enforce else 'server'})", taken)
        taken.append(name)
        return "data_policy", _policy_spec("data_policy", name, table, e["condition"], [e], enforce)
    if e["effect"] == "assign":
        name = _unique_name(f"{short} (assignment)", taken)
        taken.append(name)
        return "assignment_rule", {"name": name, "table": table, "condition": e["condition"],
                                   **({"group": e["value"]} if e["field"] == "assignment_group" else {"user": e["value"]})}
    name = _unique_name(f"{short} ({e['field']})", taken)
    taken.append(name)
    val = str(e["value"]).replace("\\", "\\\\").replace("'", "\\'")
    return "business_rule", {"name": name, "table": table, "when": "before", "insert": "insert" in e["events"],
                             "update": "update" in e["events"], "delete": False,
                             "filter_condition": e["condition"],
                             "script": f"current.setValue('{e['field']}', '{val}');",
                             "description": f"Sets {e['field']} to {e.get('display_value') or e['value']}"}


def plan_status(req: dict, items: List[dict]) -> str:
    if req["request_kind"] == "read_only":
        return "read_only"
    if req["missing_information"]:
        return "needs_input"
    acts = {i["action"] for i in items}
    if "needs_decision" in acts:
        return "needs_decision"
    if acts & {"create", "modify"}:
        return "awaiting_confirmation"
    if acts and acts <= {"reuse"}:
        return "already_satisfied"
    if "blocked" in acts:
        return "blocked"
    return "nothing_to_do" if not items else "already_satisfied"


def apply_decisions(items: List[dict], decisions: Dict[str, str]) -> List[str]:
    """Apply the user's choices to needs_decision items. Returns problems (unknown ids / options)."""
    problems = []
    by_id = {i["id"]: i for i in items}
    for iid, choice in (decisions or {}).items():
        it = by_id.get(iid)
        choice = str(choice or "").strip().lower()
        if not it:
            problems.append(f"Unknown plan item '{iid}'")
            continue
        if it["action"] != "needs_decision":
            continue
        if choice not in it["options"]:
            problems.append(f"'{choice}' is not an option for {iid} (options: {', '.join(it['options'])})")
            continue
        it["decision"] = choice
        if choice == "create":
            it["action"] = "create" if it.get("spec") else "blocked"
        elif choice == "modify":
            it["action"] = "modify" if it.get("modify") else "blocked"
        elif choice == "reuse":
            it["action"] = "reuse"
            it["reason"] += " — the user chose to rely on the existing configuration (not verified as equivalent)"
        else:
            it["action"] = "skip"
    return problems


# ════════════════════════════════════════════════════════════════════════════
# Public: plan
# ════════════════════════════════════════════════════════════════════════════

_SI_WORD = re.compile(r"script[\s_-]*include", re.I)
_SI_NAME = (re.compile(r"\b(?:called|named|class(?:\s+name)?)\s*[:=]?\s*['\"`]?([A-Za-z_][A-Za-z0-9_]{2,79})\b"),
            re.compile(r"script[\s_-]*include\s*[:=]?\s*['\"`]?([A-Za-z_][A-Za-z0-9_]{2,79})['\"`]?"))
_NOT_A_NAME = {"that", "which", "to", "for", "with", "the", "and", "named", "called", "class", "on", "in", "from",
               "who", "whose", "will", "should", "must", "can", "is", "was", "be", "it", "this", "using"}
_CALLABLE_YES = re.compile(r"client[\s-]*callable|glide\s*ajax|from (?:a |the )?client(?:[\s-]*side)?(?: script)?|"
                           r"called (?:from|by) (?:the |a )?(?:form|browser|client)", re.I)
_CALLABLE_NO = re.compile(r"not client[\s-]*callable|server[\s-]*side only|only (?:from|on) the server", re.I)
_SCOPE_IN_TEXT = re.compile(r"\b(x_[a-z0-9]+_[a-z0-9_]+)\b", re.I)


def _explicit_si_name(text: str) -> str:
    for rx in _SI_NAME:
        for m in rx.finditer(text or ""):
            name = m.group(1)
            if name.lower() not in _NOT_A_NAME:
                return name
    return ""


def apply_script_include_rules(req: dict, text: str) -> dict:
    """Deterministic rules for an explicitly requested Script Include (the AI's extraction is not trusted alone):
    the name the user wrote wins; a missing name, a missing behaviour or an invalid name becomes a question;
    client_callable comes from explicit wording only (default: not callable, recorded as an assumption)."""
    if not _SI_WORD.search(text or ""):
        return req
    req = json.loads(json.dumps(req))
    named = _explicit_si_name(text)
    sis = [lg for lg in req["logic"] if lg.get("reusable_class")]
    if not sis:
        host = next((lg for lg in req["logic"] if _SI_WORD.search(lg["description"])), None) or \
            next((lg for lg in req["logic"] if lg["side"] == "server"), None)
        if host is None:
            host = {"id": f"L{len(req['logic']) + 1}", "description": str(text).strip()[:1000], "side": "server",
                    "events": ["insert", "update"], "client_type": "", "field": "", "reusable_class": "",
                    "client_callable": None}
            req["logic"].append(host)
        host["reusable_class"] = named
        sis = [host]
    for lg in sis:
        lg["side"], lg["client_type"] = "server", ""
        if named and lg.get("reusable_class") and lg["reusable_class"] != named and len(sis) == 1:
            req["assumptions"].append(f"Script Include name taken from your text: {named} "
                                      f"(not {lg['reusable_class']})")
            lg["reusable_class"] = named
        name = lg.get("reusable_class") or ""
        if not name:
            req["missing_information"].append("What should the Script Include be named (its class name, e.g. "
                                              "IncidentUtils)?")
        elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{2,79}", name):
            req["missing_information"].append(f"'{name}' is not a valid Script Include class name — which name?")
        body = _SI_WORD.sub(" ", str(text))
        for w in (name, "create", "build", "add", "make", "new", "a", "an", "the", "called", "named", "please"):
            body = re.sub(rf"\b{re.escape(w)}\b", " ", body, flags=re.I) if w else body
        if len(re.findall(r"[A-Za-z]{3,}", body)) < 3:
            req["missing_information"].append(f"What should the Script Include {name or ''} do (its methods, their "
                                              "inputs and what they return)?".replace("  ", " "))
        if _CALLABLE_NO.search(text):
            lg["client_callable"] = False
        elif _CALLABLE_YES.search(text) or (re.search(r"client[\s_-]*script", text, re.I)
                                            and re.search(r"\bcall", text, re.I)):
            lg["client_callable"] = True
        elif lg.get("client_callable") is None:
            lg["client_callable"] = False
            req["assumptions"].append(f"Script Include {name or ''} is not client callable (server-side use only) — "
                                      "say so if client scripts must call it through GlideAjax.".replace("  ", " "))
    if not req.get("application_scope"):
        m = _SCOPE_IN_TEXT.search(text or "")
        if m:
            req["application_scope"] = m.group(1).lower()
    req["missing_information"] = list(dict.fromkeys(req["missing_information"]))
    req["assumptions"] = list(dict.fromkeys(req["assumptions"]))
    return req


def resolve_scope(sn: SNClient, req: dict) -> dict:
    """Application scope for the build: the one the requirement names (must exist in sys_scope), else global."""
    req = json.loads(json.dumps(req))
    scope = req.get("application_scope") or ""
    if not scope or scope == "global":
        req["scope_app"] = None
        if any(lg.get("reusable_class") for lg in req["logic"]) or req["logic"] or req["effects"]:
            req["assumptions"] = list(dict.fromkeys(req["assumptions"] + [
                "Built in the global application scope (no application scope was named)."]))
        return req
    try:
        app = snc.resolve_app(sn, scope)
    except SNError as e:
        req["missing_information"].append(f"Could not check application scope '{scope}': {e.message}")
        return req
    if not app:
        req["missing_information"].append(f"Application scope '{scope}' does not exist on this instance. Which "
                                          "existing scope (or global) should be used?")
    else:
        req["scope_app"] = {"sys_id": app.get("sys_id"), "scope": app.get("scope"), "name": app.get("name")}
    return req


_SHOWS_TEXT = re.compile(r"\b(message|guidance|text|display|show|info|inform|warn|alert|banner|hint|tip|instruction)",
                         re.I)


def script_questions(req: dict) -> List[str]:
    """Questions that replace script generation when it is turned off: nothing is written or invented for logic."""
    out = []
    for lg in req.get("logic") or []:
        q = (f"'{lg['description']}' needs a {'client script' if lg['side'] == 'client' else 'server script'}, and "
             "script generation is off for this plan. Describe exactly what it must do")
        if _SHOWS_TEXT.search(lg["description"]):
            q += " — including the exact text to show and the condition (e.g. which category values) it applies to"
        out.append(q + ". Nothing was written or invented for this part.")
    return out


def plan_requirement(sn: SNClient, text: str = "", requirement: Optional[dict] = None, use_ai: bool = True,
                     story_ref: Optional[dict] = None, generate_scripts: bool = True) -> dict:
    """Read-only: understand → inspect → analyse → plan. Stores the plan for this instance; nothing is written to
    ServiceNow. generate_scripts=False: no script is written for custom logic; each logic item becomes a question."""
    if requirement is None:
        if not looks_like_configuration(text):
            req = normalize_requirement({"summary": text[:200], "request_kind": "read_only"})
            return _finish_plan(sn, req, None, {}, [], {}, story_ref, fast_path=True)
        try:
            raw = extract_requirement(text)
        except (groq_client.GroqError, ValueError) as e:
            raise RuntimeError(f"The AI could not read the requirement ({e}); nothing was planned or changed")
    else:
        raw = requirement
    req = normalize_requirement(raw)
    req["source_text"] = str(text or "")[:6000]
    req = apply_script_include_rules(req, text or "")
    if req["request_kind"] == "read_only" and not req["logic"]:
        return _finish_plan(sn, req, None, {}, [], {}, story_ref, fast_path=True)
    req["request_kind"] = "configuration" if req["request_kind"] == "read_only" else req["request_kind"]
    profile = instance_profile(sn)
    req = validate_against_instance(sn, req, profile)
    req = resolve_scope(sn, req)
    req["scripts_generated"] = bool(generate_scripts)
    if not generate_scripts and req["logic"]:
        req["missing_information"] = list(dict.fromkeys(req["missing_information"] + script_questions(req)))
    if req["missing_information"] and not req.get("hierarchy"):
        return _finish_plan(sn, req, profile, {}, [], {}, story_ref)
    inv = inspect(sn, req, profile)
    schema = table_schema(sn, req["table"]) if req["table"] else {"table": "", "exists": False, "hierarchy": [],
                                                                    "fields": {}}
    # While questions are open, effects are still analysed (useful to the user) but no script is written yet
    items, generated = build_plan(sn, req, inv, profile, schema, use_ai=use_ai,
                                  include_logic=not req["missing_information"])
    return _finish_plan(sn, req, profile, inv, items, generated, story_ref)


def _finish_plan(sn, req, profile, inv, items, generated, story_ref, fast_path=False) -> dict:
    plan = {"plan_id": uuid.uuid4().hex[:16], "instance": sn.instance, "instance_key": instance_key(sn.instance),
            "created_at": _now(), "requirement": req, "items": items, "generated": generated,
            "fingerprint": fingerprint(inv) if inv else "", "status": plan_status(req, items),
            "profile": {k: (profile or {}).get(k) for k in ("built_at", "cached", "release", "roles")},
            "capabilities": {m: {k: v for k, v in i.items() if k in ("status", "detail", "missing_columns", "table")}
                             for m, i in ((profile or {}).get("mechanisms") or {}).items()},
            "inspection": _inspection_summary(inv) if inv else {}, "story": story_ref or None,
            "fast_path": fast_path}
    if not fast_path:
        save_plan(sn.instance, plan)
    plan["report"] = build_report(plan)
    return plan


def _inspection_summary(inv: dict) -> dict:
    return {"tables": inv.get("tables"),
            "counts": {k: len(inv.get(k) or []) for k in ("business_rules", "client_scripts", "ui_policies",
                                                          "data_policies", "assignment_rules", "priority_lookup")},
            "active_counts": {k: sum(1 for r in inv.get(k) or [] if _b(r.get("active", "true")) == "true")
                              for k in ("business_rules", "client_scripts", "ui_policies", "data_policies",
                                        "assignment_rules")},
            "notes": inv.get("notes") or [], "not_inspected": inv.get("not_inspected") or [],
            "previous_requirements": inv.get("history") or []}


# ════════════════════════════════════════════════════════════════════════════
# 5. Deploy (confirmed only) and 6. verify
# ════════════════════════════════════════════════════════════════════════════

MECH_TABLES = {t for t, _ in MECHANISMS.values()}


class _RecordingClient:
    """Wraps SNClient: records every create / patch on configuration tables so each can be read back afterwards."""

    def __init__(self, sn: SNClient):
        self._sn = sn
        self.writes: List[dict] = []

    def __getattr__(self, name):
        return getattr(self._sn, name)

    def create(self, table, payload, fields=""):
        rec = self._sn.create(table, payload, fields)
        if table in MECH_TABLES:
            self.writes.append({"op": "create", "table": table, "sys_id": rec.get("sys_id"), "payload": dict(payload)})
        return rec

    def patch(self, table, sys_id, payload):
        rec = self._sn.patch(table, sys_id, payload)
        if table in MECH_TABLES:
            self.writes.append({"op": "update", "table": table, "sys_id": sys_id, "payload": dict(payload)})
        return rec


def _builder_plan(items: List[dict], title: str, scope_app: Optional[dict] = None) -> dict:
    scope = {"scope_mode": "existing", "existing_app": scope_app["sys_id"]} if (scope_app or {}).get("sys_id") \
        else {"scope_mode": "global"}
    plan: Dict[str, Any] = {"app_name": f"Requirement {title}"[:80], **scope, "script_includes": [],
                            "business_rules": [], "client_scripts": [], "ui_policies": [], "data_policies": [],
                            "assignment_rules": [], "modifications": [], "tables": [], "roles": [], "acls": [],
                            "notifications": [], "subflows": [], "flows": []}
    key = {"script_include": "script_includes", "business_rule": "business_rules", "client_script": "client_scripts",
           "ui_policy": "ui_policies", "data_policy": "data_policies", "assignment_rule": "assignment_rules"}
    for it in items:
        if it["action"] == "create" and it.get("spec") and it["mechanism"] in key:
            plan[key[it["mechanism"]]].append(it["spec"])
        elif it["action"] == "modify" and it.get("modify"):
            plan["modifications"].append(it["modify"])
    return sn_build.normalize_plan(plan)


def _norm_val(v: Any) -> str:
    s = snc.ref_value(v) if isinstance(v, dict) else v
    if isinstance(s, bool):
        return "true" if s else "false"
    return str("" if s is None else s).strip()


def _same_stored(table: str, field: str, sent: Any, stored: Any) -> bool:
    a, b = _norm_val(sent), _norm_val(stored)
    # ServiceNow stores condition fields with a trailing ^EQ and may reorder nothing else — compare normalised
    if field in ("filter_condition", "conditions") or (table == "sysrule_assignment" and field == "condition"):
        return norm_query(a) == norm_query(b)
    return a == b


def verify_writes(sn: SNClient, writes: List[dict]) -> List[dict]:
    """Read every written record back from ServiceNow and compare stored values with what was sent."""
    checks = []
    for w in writes:
        fields = sorted(w["payload"])
        try:
            stored = sn.get(w["table"], w["sys_id"], ",".join(["sys_id", *fields]))
        except SNError as e:
            checks.append({"check": f"{w['op']} {w['table']} {w['sys_id']} read back", "type": "persisted_config",
                           "passed": False, "detail": f"could not read back: {e}"})
            continue
        if not stored.get("sys_id"):
            checks.append({"check": f"{w['table']} {w['sys_id']} exists", "type": "persisted_config",
                           "passed": False, "detail": "record not found after the write"})
            continue
        bad = {f: {"sent": _norm_val(w["payload"][f]), "stored": _norm_val(stored.get(f))} for f in fields
               if not _same_stored(w["table"], f, w["payload"][f], stored.get(f))}
        label = w["payload"].get("name") or w["payload"].get("short_description") or w["payload"].get("field") or \
            w["sys_id"]
        checks.append({"check": f"{w['table']} '{label}' stored as sent ({len(fields)} fields)",
                       "type": "persisted_config", "table": w["table"], "sys_id": w["sys_id"], "passed": not bad,
                       "detail": "" if not bad else f"differs: {bad}"})
    return checks


def _duplicate_checks(sn: SNClient, items: List[dict], builder_results: List[dict]) -> List[dict]:
    checks = []
    name_field = {"business_rule": ("sys_script", "name", "collection"),
                  "client_script": ("sys_script_client", "name", "table"),
                  "ui_policy": ("sys_ui_policy", "short_description", "table"),
                  "data_policy": ("sys_data_policy2", "short_description", "model_table"),
                  "assignment_rule": ("sysrule_assignment", "name", "table")}
    for it in items:
        if it["action"] != "create" or it["mechanism"] not in name_field or not it.get("spec"):
            continue
        table, nf, tf = name_field[it["mechanism"]]
        name = it["spec"].get("name") or it["spec"].get("short_description")
        try:
            n = len(sn.query(table, f"{nf}={name}^{tf}={it['table']}", "sys_id", limit=5))
        except SNError as e:
            checks.append({"check": f"no duplicate of '{name}'", "type": "persisted_config", "passed": False,
                           "detail": str(e)})
            continue
        checks.append({"check": f"exactly one {it['mechanism'].replace('_', ' ')} named '{name}' on {it['table']}",
                       "type": "persisted_config", "passed": n == 1, "detail": f"{n} found"})
    return checks


def deploy_plan(sn: SNClient, plan_id: str, confirm: bool = False, decisions: Optional[Dict[str, str]] = None,
                update_set_name: str = "", rollback_on_error: bool = True) -> dict:
    """Deploy a stored plan. Needs confirm=True (the existing explicit-confirmation model). Refreshes the instance
    profile and the inspection first; if anything relevant changed, a new plan is returned instead."""
    plan = load_plan(sn.instance, plan_id)
    if not plan:
        raise LookupError(f"Plan {plan_id} not found for {instance_key(sn.instance)}")
    if decisions:
        problems = apply_decisions(plan["items"], decisions)
        if problems:
            return {**plan, "status": "needs_decision", "problems": problems, "report": build_report(plan)}
        plan["status"] = plan_status(plan["requirement"], plan["items"])
        save_plan(sn.instance, plan)
    if plan["status"] not in ("awaiting_confirmation",):
        plan["report"] = build_report(plan)
        return plan
    if confirm is not True:
        plan["report"] = build_report(plan)
        return plan

    # refresh before writing: never deploy on a stale profile / inspection
    profile = instance_profile(sn, force=True)
    req = plan["requirement"]
    if req["table"]:
        table_schema(sn, req["table"], force=True)
    inv = inspect(sn, req, profile)
    fp = fingerprint(inv)
    if fp != plan["fingerprint"]:
        schema = table_schema(sn, req["table"]) if req["table"] else {"fields": {}, "hierarchy": []}
        new_items, generated = build_plan(sn, req, inv, profile, schema, plan.get("generated"), use_ai=False)
        apply_decisions(new_items, {i["id"]: i["decision"] for i in plan["items"] if i.get("decision")})
        sig = lambda its: [(i["id"], i["action"], i["mechanism"], json.dumps(i.get("spec"), sort_keys=True))
                           for i in its]
        if sig(new_items) != sig(plan["items"]):
            fresh = _finish_plan(sn, req, profile, inv, new_items, generated, plan.get("story"))
            fresh["status"] = "plan_changed" if fresh["status"] == "awaiting_confirmation" else fresh["status"]
            fresh["previous_plan_id"] = plan_id
            fresh["report"] = build_report(fresh)
            return fresh
        plan["fingerprint"] = fp
    for it in plan["items"]:     # capability re-check on the fresh profile
        if it["action"] in ("create",) and it["mechanism"] in MECHANISMS:
            ok, why, kind = mechanism_status(profile, it["mechanism"])
            if not ok:
                it.update(action="blocked", blocked_kind=kind, reason=it["reason"] + f" — BLOCKED: {why}")

    rec = _RecordingClient(sn)
    bplan = _builder_plan(plan["items"], plan_id, req.get("scope_app"))
    log_lines: List[dict] = []
    opts = {"update_set_name": update_set_name or f"HAI_Req_{datetime.now().strftime('%Y%m%d')}",
            "update_set_description": f"Requirement: {req['summary']}"[:200], "stop_on_error": True,
            "rollback_on_error": rollback_on_error, "restore_previous_context": True}
    builder = sn_build.Builder(rec, bplan, opts, lambda m, s, d: log_lines.append({"msg": m, "status": s}))
    try:
        result = builder.run()
    except Exception as e:  # unexpected bug: report, never claim success
        logger.exception("requirement deploy crashed")
        result = {"status": "failed", "error": f"internal error: {e}", "rollback": [], "verification": {}}
    results = [r for r in builder.results if r["kind"] != "Application"]
    rolled_back = bool(result.get("rollback"))
    checks = [] if rolled_back else verify_writes(sn, rec.writes) + _duplicate_checks(sn, plan["items"], results)
    # re-inspect: does the configuration now cover each declarative effect?
    acceptance = []
    if not rolled_back:
        inv_after = inspect(sn, req, profile)
        fps_after = footprints(inv_after)
        for e in req["effects"]:
            it = next((i for i in plan["items"] if e["id"] in i["covers"]), None)
            if not it or it["action"] in ("skip", "blocked"):
                acceptance.append({"check": f"{e['field']} {e['effect']}={e.get('display_value') or e['value']}",
                                   "type": "requirement", "passed": False,
                                   "detail": f"not implemented ({(it or {}).get('action', 'no plan item')})"})
                continue
            if it["mechanism"] == "priority_lookup":
                row = sn.get_one("dl_u_priority", f"sys_id={(it.get('modify') or {}).get('sys_id') or it['existing'][0]['sys_id']}",
                                 "priority")
                acceptance.append({"check": f"priority lookup gives {e['value']}", "type": "persisted_config",
                                   "passed": bool(row) and str(row.get("priority")) == e["value"], "detail": ""})
                continue
            rel = compare(e, fps_after)
            if it["mechanism"] in ("ui_policy", "data_policy", "assignment_rule") or e["effect"] in STATE_EFFECTS:
                missing = sorted(_needed(e) - set(rel["covered"]))
                user_reuse = it["action"] == "reuse" and it.get("decision") == "reuse"
                acceptance.append({"check": f"{e['field']} {e['effect']}={e.get('display_value') or e['value']}"
                                            + (f" when {e['condition']}" if e["condition"] else ""),
                                   "type": "persisted_config", "passed": not missing or user_reuse,
                                   "detail": "covered by active configuration" if not missing else
                                   "you chose to rely on existing configuration (not verified as equivalent)"
                                   if user_reuse else f"not covered on: {', '.join(missing)}"})
            else:
                acceptance.append({"check": f"{e['field']} {e['effect']}={e.get('display_value') or e['value']}",
                                   "type": "persisted_config", "passed": _item_stored(it, checks),
                                   "detail": "script stored as planned and active (behaviour not executed)"})
        for lg in req["logic"]:
            it = next((i for i in plan["items"] if lg["id"] in i["covers"]), None)
            done = bool(it) and it["action"] in ("create", "reuse") and _item_stored(it, checks)
            acceptance.append({"check": f"logic: {lg['description'][:120]}", "type": "persisted_config",
                               "passed": done, "detail": ("script stored as planned and active (behaviour not "
                                                          "executed)" if done else
                                                          f"not implemented ({(it or {}).get('action', 'no item')})")})
        cap = result.get("verification") or {}
        if cap.get("update_set"):
            acceptance.append({"check": "every record written is captured in the update set",
                               "type": "update_set_capture", "passed": not cap.get("missing"),
                               "detail": "" if not cap.get("missing") else
                               "not captured: " + ", ".join(m["name"] for m in cap["missing"])})
    deployment = {"status": _deploy_status(result, results, checks + acceptance, rolled_back),
                  "builder_status": result.get("status"), "error": result.get("error"),
                  "failed_step": result.get("failed_step"), "results": results, "rollback": result.get("rollback") or [],
                  "update_set": (result.get("update_set") or {}) and {k: result["update_set"].get(k) for k in
                                                                       ("name", "sys_id", "link")},
                  "capture": {k: (result.get("verification") or {}).get(k) for k in ("captured_count", "expected",
                                                                                      "missing")},
                  "error_kinds": sorted({classify_error(r.get("message")) for r in results if r["status"] == "failed"}),
                  "log": log_lines[-80:], "at": _now()}
    plan.update(status=deployment["status"], deployment=deployment,
                verification={"checks": checks + acceptance, "runtime": _runtime_note(plan)})
    save_plan(sn.instance, plan)
    _add_history(sn.instance, {"plan_id": plan_id, "summary": req["summary"], "table": req["table"],
                               "status": deployment["status"], "at": deployment["at"],
                               "artifacts": [{"kind": r["kind"], "name": r["name"], "status": r["status"],
                                              "table": r["table"], "sys_id": r["sys_id"]} for r in results
                                             if r["status"] in ("created", "modified", "reused") and r.get("sys_id")],
                               "story": plan.get("story")})
    plan["report"] = build_report(plan)
    return plan


def _item_stored(it: dict, checks: List[dict]) -> bool:
    """A created item counts only if its record was read back and matched what was sent; a reused one (user decision
    or exact match) has nothing to read back."""
    if it["action"] == "reuse":
        return True
    name = (it.get("spec") or {}).get("name") or (it.get("spec") or {}).get("short_description")
    mine = [c for c in checks if c.get("type") == "persisted_config" and name and f"'{name}'" in c["check"]
            and "stored as sent" in c["check"]]
    return bool(mine) and all(c["passed"] for c in mine)


def _deploy_status(result: dict, results: List[dict], checks: List[dict], rolled_back: bool) -> str:
    if result.get("status") == "failed":
        return "failed"
    ok_writes = any(r["status"] in ("created", "modified") for r in results)
    if any(r["status"] == "failed" for r in results) or any(not c["passed"] for c in checks):
        return "partially_completed" if ok_writes else "failed"
    return "deployed"


def _runtime_note(plan: dict) -> dict:
    mechs = sorted({i["mechanism"] for i in plan["items"] if i["action"] in ("create", "modify")})
    return {"status": "not_run",
            "reason": "Runtime behaviour was not exercised: client scripts and UI policies run in the browser, and "
                      "testing business rules, data policies or assignment rules would insert or change records. "
                      "Only the stored configuration was verified (read back from ServiceNow).",
            "mechanisms": mechs,
            "suggested_manual_tests": [c for c in plan["requirement"].get("acceptance_criteria") or []][:20]}


def _acceptance_criteria_evidence(plan: dict) -> List[dict]:
    criteria = plan.get("requirement", {}).get("acceptance_criteria") or []
    return [{"criterion_id": f"AC-{index:03d}", "criterion": str(criterion), "status": "not_run",
             "test_id": f"MANUAL-{index:03d}", "evidence": [],
             "reason": "No runtime test has been executed; persisted configuration checks do not prove behavior."}
            for index, criterion in enumerate(criteria, 1)]


def work_note(plan: dict) -> str:
    """Text for the story's tracking record, so the requirement links to what was (or was not) built."""
    r = plan.get("report") or build_report(plan)
    lines = [f"[SoftProdigy Snow Agent] Requirement {plan['plan_id']}: {r['deployment']['status']}", r["outcome"]]
    for rec in r["deployment"]["records"]:
        lines.append(f"- {rec['kind']} '{rec['name']}': {rec['status']}" + (f" ({rec['message']})" if rec.get(
            "message") else ""))
    pc = r["verification"]["persisted_config"]
    lines.append(f"Verification (stored configuration): {pc['passed']} passed, {pc['failed']} failed. "
                 f"Runtime behaviour: {r['verification']['runtime']['status'].replace('_', ' ')}.")
    if (r["deployment"].get("update_set") or {}).get("name"):
        lines.append(f"Update set: {r['deployment']['update_set']['name']}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# 7. Report — built only from plan / tool results
# ════════════════════════════════════════════════════════════════════════════

def build_report(plan: dict) -> dict:
    req = plan["requirement"]
    items = plan.get("items") or []
    dep = plan.get("deployment") or {}
    ver = plan.get("verification") or {}
    insp = plan.get("inspection") or {}
    by_action: Dict[str, List[dict]] = {}
    for it in items:
        by_action.setdefault(it["action"], []).append(it)

    def brief(it):
        return {"id": it["id"], "mechanism": it["mechanism"], "name": it["name"], "table": it["table"],
                "reason": it["reason"], "side": it.get("side", ""), "options": it.get("options") or [],
                "decision": it.get("decision"),
                "existing": [{k: x.get(k) for k in ("kind", "name", "sys_id", "active", "why", "origin")}
                             for x in it.get("existing") or []][:10],
                "trigger": _trigger(it)}

    status = plan["status"]
    checks = ver.get("checks") or []
    passed = [c for c in checks if c["passed"]]
    failed = [c for c in checks if not c["passed"]]
    built = [r for r in dep.get("results") or [] if r["status"] in ("created", "modified")]
    outcome = _outcome(status, req, by_action, built, failed, plan)
    return {
        "requirement": {"summary": req["summary"], "purpose": req.get("purpose"), "table": req.get("table"),
                        "effects": [{k: e.get(k) for k in ("id", "field", "effect", "value", "display_value", "side",
                                                           "events", "condition")} for e in req.get("effects") or []],
                        "application_scope": ((req.get("scope_app") or {}).get("scope")) or "global",
                        "logic": [{k: lg.get(k) for k in ("id", "description", "side", "client_type", "field",
                                                          "reusable_class", "client_callable")}
                                  for lg in req.get("logic") or []],
                        "acceptance_criteria": req.get("acceptance_criteria") or [],
                        "assumptions": req.get("assumptions") or [],
                        "missing_information": req.get("missing_information") or []},
        "inspection": {**insp, "capabilities": {m: c.get("status") for m, c in (plan.get("capabilities") or {}).items()}},
        "implementation": {"already_satisfied": [brief(i) for i in by_action.get("reuse", [])],
                           "to_create": [brief(i) for i in by_action.get("create", [])],
                           "to_modify": [{**brief(i), "changes": (i.get("modify") or {}).get("changes")}
                                         for i in by_action.get("modify", [])],
                           "needs_decision": [brief(i) for i in by_action.get("needs_decision", [])],
                           "blocked": [{**brief(i), "kind": i.get("blocked_kind") or "execution_failure"}
                                       for i in by_action.get("blocked", [])],
                           "skipped": [brief(i) for i in by_action.get("skip", [])]},
        "deployment": {"status": status, "update_set": dep.get("update_set"), "error": dep.get("error"),
                       "error_kinds": dep.get("error_kinds") or [],
                       "records": [{k: r.get(k) for k in ("kind", "name", "status", "message", "table", "sys_id")}
                                   for r in dep.get("results") or []],
                       "rolled_back": dep.get("rollback") or [], "capture": dep.get("capture")},
        "verification": {"persisted_config": {"passed": len(passed), "failed": len(failed), "checks": checks},
                         "runtime": ver.get("runtime") or {"status": "not_run", "reason": "nothing deployed yet"},
                         "acceptance_criteria": _acceptance_criteria_evidence(plan)},
        "outcome": outcome,
    }


def _trigger(it: dict) -> str:
    s = it.get("spec") or {}
    m = it["mechanism"]
    if m == "business_rule":
        ev = [e for e in ("insert", "update", "delete") if s.get(e)]
        return f"{s.get('when', 'before')} {'/'.join(ev)}" + (f" when {s['filter_condition']}" if s.get(
            "filter_condition") else "")
    if m == "client_script":
        return s.get("type", "") + (f" of {s['field']}" if s.get("field") else "")
    if m in ("ui_policy", "data_policy"):
        return "form load/change" if m == "ui_policy" else ("every save" + (" + form" if s.get("enforce_ui") else "")) \
            + (f" when {s['conditions']}" if s.get("conditions") else "")
    if m == "assignment_rule":
        return "on insert/update while unassigned" + (f" when {s['condition']}" if s.get("condition") else "")
    if m == "script_include":
        return f"class {s.get('name', '')} · client callable: {'yes' if s.get('client_callable') else 'no'}"
    return ""


def _outcome(status: str, req: dict, by_action: Dict[str, List[dict]], built: List[dict], failed: List[dict],
             plan: dict) -> str:
    t = req.get("table") or "the table"
    if status == "read_only":
        return "This is a look-up, not a configuration change; nothing was planned."
    if status == "needs_input":
        return "Nothing was changed. Please answer: " + " ".join(req["missing_information"])
    if status == "needs_decision":
        return (f"Nothing was changed. {len(by_action.get('needs_decision', []))} item(s) overlap or conflict with "
                "existing configuration and need your decision before anything is built.")
    if status == "already_satisfied":
        return f"Nothing needs to change: existing active configuration on {t} already does this."
    if status == "blocked":
        return "Nothing can be built: " + "; ".join(i["reason"] for i in by_action.get("blocked", []))[:600]
    if status == "awaiting_confirmation":
        n = len(by_action.get("create", [])) + len(by_action.get("modify", []))
        return f"Planned {n} change(s) on {t}; nothing is written until you confirm."
    if status == "plan_changed":
        return "The instance changed since this was planned; review the updated plan and confirm again."
    if status == "deployed":
        return (f"Deployed {len(built)} record(s) on {t} and verified them by reading them back. Runtime behaviour "
                "was not tested — check the acceptance criteria in a test instance.")
    if status == "partially_completed":
        return (f"Partially completed: {len(built)} record(s) written, {len(failed)} check(s) failed. See verification "
                "for what is missing.")
    if status == "failed":
        dep = plan.get("deployment") or {}
        return ("Deployment failed" + (f" at '{dep.get('failed_step')}'" if dep.get("failed_step") else "") +
                (f": {dep.get('error')}" if dep.get("error") else "") +
                (" — every change made by this run was rolled back." if dep.get("rollback") else "."))
    return status
