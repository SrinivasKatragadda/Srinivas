"""
Shared ITAM plumbing: reference resolution, role checks, live lifecycle choices, field views, and the write
pipeline (dry run -> confirmation -> write -> read-back verification).
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from snow_itam import schema as S
from snow_itam.backends import Backend
from snow_itam.errors import ITAMError

SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")
USER_LOOKUP = ("user_name", "email", "name")
JOURNAL_FIELDS = {"work_notes", "comments"}  # write-only journals: never read back for verification


def is_sys_id(v: Any) -> bool:
    return isinstance(v, str) and bool(SYS_ID_RE.match(v.strip()))


def today() -> str:
    return date.today().isoformat()


def days_from_today(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def check_date(name: str, value: str, with_time: bool = False) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if DATE_RE.match(v) or (with_time and DATETIME_RE.match(v)):
        return v.replace("T", " ")
    raise ITAMError(f"'{name}' must be a date like 2026-12-31" + (" or 2026-12-31 14:00:00" if with_time else ""),
                    "INVALID_ARGS")


def flatten(record: Dict[str, Any]) -> Dict[str, Any]:
    """display_value=all rows -> plain values, or {"value", "display"} where they differ (references, choices)."""
    out = {}
    for k, v in (record or {}).items():
        if isinstance(v, dict) and ("display_value" in v or "value" in v):
            val, disp = v.get("value", ""), v.get("display_value", "")
            out[k] = val if (disp in (None, "") or disp == val) else {"value": val, "display": disp}
        else:
            out[k] = v
    return out


def raw(v: Any) -> str:
    if isinstance(v, dict):
        return str(v.get("value") or "")
    return "" if v is None else str(v)


def same(actual: Any, expected: Any) -> bool:
    a, e = raw(actual).strip(), raw(expected).strip()
    if a == e:
        return True
    if e.lower() in ("true", "false") and a.lower() in ("true", "false", "1", "0"):
        return (a.lower() in ("true", "1")) == (e.lower() == "true")
    if DATE_RE.match(e) and a.startswith(e):
        return True
    try:
        return abs(float(a) - float(e)) < 1e-9
    except ValueError:
        return False


def diff(before: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {k: {"from": raw(before.get(k)), "to": v} for k, v in payload.items() if not same(before.get(k), v)}


class ITAMCore:
    # Tables whose names people type loosely ("Apple MacBook Pro 17" for 'MacBook Pro 17"'). A close match is used
    # only when it is the single candidate, and the result says which record was used. Never for users or assets.
    FUZZY_TABLES = {"cmdb_model", "cmdb_hardware_product_model", "cmdb_consumable_product_model",
                    "cmdb_software_product_model", "alm_stockroom", "cmn_location", "core_company",
                    "cmdb_model_category", "alm_stockroom_type", "cmn_department", "sys_user_group"}
    ROLES = S.ROLES   # area -> roles that allow it (snow_itom's core supplies its own)

    def __init__(self, backend: Backend):
        self.b = backend
        self._cache: Dict[str, Any] = {}
        self.matched: List[str] = []   # close-match notes for the current call

    # ── lookups ────────────────────────────────────────────────────────────────
    def _names(self, table: str, rows: List[dict], fields: Sequence[str]) -> str:
        out = []
        for r in rows[:6]:
            rec = self.b.get(table, r["sys_id"], ",".join(fields)) or {}
            out.append(next((str(rec.get(f)) for f in fields if rec.get(f)), r["sys_id"]))
        return ", ".join(f"'{n}'" for n in out)

    def resolve(self, table: str, value: Any, fields: Sequence[str] = ("name",), *, label: str = "",
                extra_query: str = "", required: bool = True) -> Optional[str]:
        """sys_id or display value -> sys_id. Exact match first; for FUZZY_TABLES a single close match is used and
        reported. Errors on not found / ambiguous, listing the candidates."""
        v = str(value or "").strip()
        label = label or table
        extra = f"^{extra_query}" if extra_query else ""
        if not v:
            if required:
                raise ITAMError(f"{label} is required", "INVALID_ARGS")
            return None
        if is_sys_id(v):
            rows = self.b.query(table, f"sys_id={v}{extra}", "sys_id", 1)
            if not rows:
                raise ITAMError(f"No {label} with sys_id {v}", "NOT_FOUND", status=404)
            return v
        rows = self.b.query(table, "^OR".join(f"{f}={v}" for f in fields) + extra, "sys_id", 3)
        if len(rows) == 1:
            return rows[0]["sys_id"]
        if len(rows) > 1:
            raise ITAMError(f"'{v}' matches several {label} records — say which one", "AMBIGUOUS",
                            f"Candidates: {self._names(table, rows, fields)} (or pass a sys_id)")
        if table in self.FUZZY_TABLES:
            words = [w for w in re.split(r"[\s\"'”“″,]+", v) if w]
            cands: Dict[str, dict] = {}
            for f in fields:   # every word must appear in the field, in any order (case-insensitive LIKE)
                if words:
                    for r in self.b.query(table, "^".join(f"{f}LIKE{w}" for w in words) + extra, "sys_id", 6):
                        cands[r["sys_id"]] = r
            if len(cands) == 1:
                sid = next(iter(cands))
                self.matched.append(f"{label} '{v}' matched {self._names(table, [cands[sid]], fields)}")
                return sid
            if len(cands) > 1:
                raise ITAMError(f"'{v}' matches several {label} records — say which one", "AMBIGUOUS",
                                f"Candidates: {self._names(table, list(cands.values()), fields)}")
        raise ITAMError(f"No {label} matches '{v}'", "NOT_FOUND", f"Looked in {table}.{'/'.join(fields)}",
                        status=404)

    def resolve_user(self, value: Any, label: str = "user", required: bool = True) -> Optional[str]:
        return self.resolve("sys_user", value, USER_LOOKUP, label=label, extra_query="active=true",
                            required=required)

    def resolve_refs(self, spec: Dict[str, Tuple[str, Tuple[str, ...]]], fields: Dict[str, Any],
                     what: str) -> Dict[str, Any]:
        unknown = sorted(set(fields) - set(spec))
        if unknown:
            raise ITAMError(f"Field(s) {unknown} cannot be changed with this tool", "INVALID_ARGS",
                            f"Allowed for {what}: {sorted(spec)}")
        out = {}
        for k, v in fields.items():
            ref_table, lookup = spec[k]
            if ref_table and str(v or "").strip():
                out[k] = self.resolve(ref_table, v, lookup, label=k)
            elif k in ("warranty_expiration", "install_date", "purchase_date", "order_date", "starts", "ends",
                       "renewal_date", "notice_date"):
                out[k] = check_date(k, str(v or ""))
            elif isinstance(v, bool):
                out[k] = "true" if v else "false"
            else:
                out[k] = "" if v is None else str(v)
        return out

    def me(self) -> Dict[str, str]:
        if "me" not in self._cache:
            rows = self.b.query("sys_user", f"user_name={self.b.username}", "sys_id,user_name,name", 1)
            self._cache["me"] = rows[0] if rows else {"sys_id": "", "user_name": self.b.username, "name": ""}
        return self._cache["me"]

    def table_exists(self, table: str) -> bool:
        key = f"table:{table}"
        if key not in self._cache:
            self._cache[key] = bool(self.b.query("sys_db_object", f"name={table}", "sys_id", 1))
        return self._cache[key]

    def require_table(self, table: str, feature: str, plugin: str):
        if not self.table_exists(table):
            raise ITAMError(f"{feature} is not available on this instance", "PLUGIN_REQUIRED",
                            f"Table {table} is missing — activate the {plugin} plugin", status=404)

    # ── authorization ────────────────────────────────────────────────────────────
    def require_roles(self, area: str, action: str) -> Dict[str, Any]:
        """The signed-in user needs admin or one of the area's roles. ServiceNow ACLs still apply after this."""
        wanted = ["admin"] + list(self.ROLES.get(area, []))
        key = f"roles:{area}"
        if key not in self._cache:
            uid = self.me().get("sys_id")
            try:
                rows = self.b.query("sys_user_has_role", f"user={uid}^role.nameIN{','.join(wanted)}",
                                    "role.name", 50) if uid else []
                self._cache[key] = {"checked": True, "matched": sorted({r.get("role.name") for r in rows
                                                                         if r.get("role.name")})}
            except ITAMError as e:
                if e.status != 403:
                    raise
                # The user may not read sys_user_has_role: ServiceNow's own ACLs then decide.
                self._cache[key] = {"checked": False, "matched": [],
                                    "note": "role check skipped (cannot read sys_user_has_role); ServiceNow ACLs apply"}
        res = self._cache[key]
        if res["checked"] and not res["matched"]:
            raise ITAMError(f"{action} needs one of these roles: {', '.join(wanted)}", "FORBIDDEN",
                            f"User {self.b.username} has none of them", status=403)
        return res

    # ── live choices / lifecycle ────────────────────────────────────────────────
    def choices(self, table: str, field: str) -> List[Dict[str, str]]:
        key = f"choice:{table}.{field}"
        if key not in self._cache:
            rows = self.b.query("sys_choice", f"name={table}^element={field}^inactive=false^language=en",
                                "value,label,dependent_value,sequence", 300, order_by="sequence")
            seen, out = set(), []
            for r in rows:
                k = (r.get("value"), r.get("dependent_value") or "")
                if k not in seen:
                    seen.add(k)
                    out.append({"value": r.get("value", ""), "label": r.get("label", ""),
                                "dependent_value": r.get("dependent_value") or ""})
            self._cache[key] = out
        return self._cache[key]

    def lifecycle(self, asset_table: str = "alm_asset") -> List[Dict[str, Any]]:
        """States with their allowed substates, from sys_choice (class table first, then alm_asset)."""
        states = self.choices(asset_table, "install_status") or self.choices("alm_asset", "install_status")
        subs = self.choices(asset_table, "substatus") or self.choices("alm_asset", "substatus")
        if not states:
            raise ITAMError("No install_status choices found for alm_asset", "PLUGIN_REQUIRED",
                            "Asset Management (com.snc.asset_management) must be active")
        return [{"value": s["value"], "label": s["label"],
                 "substates": [{"value": x["value"], "label": x["label"]} for x in subs
                               if x["dependent_value"] == s["value"]]} for s in states]

    def normalize_state(self, state: Any, substatus: Any = "", asset_table: str = "alm_asset") -> Tuple[str, str]:
        st, sub = str(state or "").strip(), str(substatus or "").strip()
        states = self.lifecycle(asset_table)
        match = next((s for s in states if st.lower() in (s["value"].lower(), s["label"].lower())), None)
        if not match:
            raise ITAMError(f"Unknown asset state '{st}'", "INVALID_ARGS",
                            "Allowed: " + ", ".join(f"{s['label']} ({s['value']})" for s in states))
        if not sub:
            return match["value"], ""
        sub_match = next((x for x in match["substates"] if sub.lower() in (x["value"].lower(), x["label"].lower())),
                         None)
        if not sub_match:
            allowed = ", ".join(x["label"] for x in match["substates"]) or "none"
            raise ITAMError(f"Substate '{sub}' is not allowed with state '{match['label']}'", "INVALID_ARGS",
                            f"Allowed substates for {match['label']}: {allowed}")
        return match["value"], sub_match["value"]

    def state_label(self, value: str) -> str:
        return next((s["label"] for s in self.lifecycle() if s["value"] == str(value)), str(value))

    # ── reads ─────────────────────────────────────────────────────────────────────
    def view(self, table: str, sys_id: str, fields: str) -> Dict[str, Any]:
        rec = self.b.get(table, sys_id, fields, display_value="all")
        if not rec:
            raise ITAMError(f"Record {sys_id} not found in {table}", "NOT_FOUND", status=404)
        return flatten(rec)

    def view_query(self, table: str, query: str, fields: str, limit: int = 20, order_by: str = "") -> List[dict]:
        return [flatten(r) for r in self.b.query(table, query, fields, max(1, min(int(limit or 20), 200)),
                                                 display_value="all", order_by=order_by)]

    # ── the write pipeline ────────────────────────────────────────────────────────
    def write(self, *, area: str, action: str, table: str, payload: Dict[str, Any], sys_id: str = "",
              before: Optional[Dict[str, Any]] = None, dry_run: bool = False, confirm: bool = False,
              reasons: Iterable[str] = (), warnings: Iterable[str] = (), context: Optional[dict] = None,
              verify_fields: str = "", critical: Iterable[str] = ()) -> Dict[str, Any]:
        """
        Role check -> diff -> dry run / confirmation gate -> create or update -> read back and compare.
        `reasons` non-empty means the change is consequential and needs confirm=true.
        `critical` fields must read back exactly as written, otherwise the result is a failure (a business rule
        or ACL kept the old value) — e.g. the lifecycle state of a retire.
        """
        auth = self.require_roles(area, action)
        reasons, warnings = [r for r in reasons if r], [w for w in warnings if w]
        changes = diff(before or {}, payload) if sys_id else None
        base: Dict[str, Any] = {"action": action, "table": table, **({"sys_id": sys_id} if sys_id else {}),
                                **(context or {})}
        if warnings:
            base["warnings"] = warnings
        if sys_id and not changes:
            return {"success": True, "no_change": True, **base, "message": "Nothing to change"}
        preview = {"payload": payload} if not sys_id else {"diff": changes}
        if dry_run:
            return {"dry_run": True, **base, **preview,
                    **({"requires_confirmation": True, "reasons": reasons} if reasons else {})}
        if reasons and not confirm:
            return {"success": False, "requires_confirmation": True, "reasons": reasons, **base, **preview,
                    "message": "This change is consequential — review it and call again with confirm=true"}
        rec = self.b.update(table, sys_id, payload) if sys_id else self.b.create(table, payload)
        sid = sys_id or (rec or {}).get("sys_id", "")
        if not sid:
            raise ITAMError(f"ServiceNow did not return a record for {action}", "WRITE_FAILED")
        check = self.b.get(table, sid, ",".join(["sys_id", *(k for k in payload if k not in JOURNAL_FIELDS)] +
                                               ([verify_fields] if verify_fields else [])))
        if not check:
            raise ITAMError(f"{action}: the record could not be read back after writing", "VERIFY_FAILED",
                            "A business rule or ACL may have rejected or removed it")
        mismatches = {k: {"expected": v, "actual": raw(check.get(k))} for k, v in payload.items()
                      if k not in JOURNAL_FIELDS and not same(check.get(k), v)}
        out = {"success": True, **base, "sys_id": sid, "link": self.b.link(table, sid),
               "verified": not mismatches, "role_check": auth}
        if changes is not None:
            out["changed"] = changes
        if mismatches:
            out["mismatches"] = mismatches
            out["note"] = ("ServiceNow stored different values for some fields (business rules can adjust them) "
                           "— review the mismatches")
        failed = sorted(set(critical) & set(mismatches))
        if failed:
            out["success"] = False
            out["error"] = (f"ServiceNow did not apply {', '.join(failed)} — a business rule, ACL or read-only field "
                            "kept the previous value, so the change did not take effect")
        return out
