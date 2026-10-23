"""
Real ServiceNow instance health scan (replaces the v5 record-count scoring and the
hard-coded numbers the v5 UI displayed when the scan endpoint was missing).

Every check runs a live, read-only query (mostly the Aggregate/stats API).
A check that cannot run (missing plugin, no read access) is reported as "unknown"
and is EXCLUDED from the score — it is never given free points.
If the credentials are wrong, the scan fails with 401 instead of producing a grade.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from fastapi import HTTPException

from sn_client import SNClient, SNError

DAYS = "javascript:gs.daysAgoStart({})"

CATEGORIES = [
    ("security", "Security Operations", "🛡️"),
    ("cmdb", "CMDB & Asset Management", "🗃️"),
    ("incidents", "Incident Management", "🚨"),
    ("changes", "Change Management", "🔄"),
    ("platform", "Platform & Development Hygiene", "⚡"),
    ("compliance", "Compliance & Governance", "📋"),
]


class Check:
    def __init__(self, sn: SNClient):
        self.sn = sn

    def list_link(self, table: str, query: str) -> str:
        return f"{self.sn.instance}/{table}_list.do?sysparm_query={quote(query)}"


def _r(status: str, value: Any, summary: str, severity: str = "", recommendation: str = "", link: str = "") -> dict:
    return {"status": status, "value": value, "summary": summary, "severity": severity,
            "recommendation": recommendation, "link": link}


def _threshold(n: int, warn_at: int, fail_at: int, ok: str, bad: str, sev: str, rec: str, link: str) -> dict:
    if n >= fail_at:
        return _r("fail", n, bad.format(n=n), sev, rec, link)
    if n >= warn_at:
        return _r("warn", n, bad.format(n=n), "medium" if sev in ("high", "critical") else "low", rec, link)
    return _r("pass", n, ok.format(n=n), link=link)


def _prop_bool(sn: SNClient, name: str) -> Optional[bool]:
    v = sn.property(name)
    return None if v is None else str(v).strip().lower() == "true"


# ════════════════════════════════════════════════════════════════════════════
# Checks — (id, category, title, fn(sn, link) -> result dict)
# ════════════════════════════════════════════════════════════════════════════

def _admins(sn, L):
    q = "role.name=admin^user.active=true^inherited=false"
    n = sn.count("sys_user_has_role", q)
    return _threshold(n, 6, 11, "{n} active users hold admin directly", "{n} active users hold the admin role",
                      "high", "Keep admin to a handful of named accounts; use delegated roles for everyone else.",
                      L("sys_user_has_role", q))


def _inactive_admins(sn, L):
    q = "role.name=admin^user.active=false"
    n = sn.count("sys_user_has_role", q)
    return _threshold(n, 1, 5, "No inactive users still hold admin", "{n} inactive user(s) still hold admin",
                      "medium", "Remove roles from deactivated accounts.", L("sys_user_has_role", q))


def _security_admins(sn, L):
    q = "role.name=security_admin^user.active=true^inherited=false"
    n = sn.count("sys_user_has_role", q)
    return _threshold(n, 4, 8, "{n} users hold security_admin", "{n} users hold security_admin",
                      "high", "Limit security_admin (ACL editing) to the security team.", L("sys_user_has_role", q))


def _csrf(sn, L):
    v = _prop_bool(sn, "glide.security.use_csrf_token")
    if v is None:
        return _r("unknown", None, "Property glide.security.use_csrf_token not found")
    return _r("pass", True, "CSRF token protection is on") if v else \
        _r("fail", False, "CSRF token protection is OFF", "critical",
           "Set glide.security.use_csrf_token = true.", L("sys_properties", "name=glide.security.use_csrf_token"))


def _session_timeout(sn, L):
    v = sn.property("glide.ui.session_timeout")
    if v is None:
        return _r("pass", 30, "Session timeout uses the platform default (30 min)")
    try:
        m = int(float(v))
    except ValueError:
        return _r("unknown", v, f"Unreadable session timeout value '{v}'")
    if m > 120:
        return _r("fail", m, f"Idle session timeout is {m} minutes", "medium", "Reduce glide.ui.session_timeout to ≤ 30.",
                  L("sys_properties", "name=glide.ui.session_timeout"))
    if m > 30:
        return _r("warn", m, f"Idle session timeout is {m} minutes", "low", "Consider ≤ 30 minutes.",
                  L("sys_properties", "name=glide.ui.session_timeout"))
    return _r("pass", m, f"Idle session timeout is {m} minutes")


def _mfa(sn, L):
    v = _prop_bool(sn, "glide.authenticate.multifactor")
    if v is None:
        return _r("unknown", None, "MFA property not present (Multi-factor Authentication plugin may be inactive)")
    return _r("pass", True, "Multi-factor authentication is enabled") if v else \
        _r("fail", False, "Multi-factor authentication is disabled", "high",
           "Enable MFA (glide.authenticate.multifactor) at least for admin roles.",
           L("sys_properties", "name=glide.authenticate.multifactor"))


def _ci_total(sn) -> int:
    return sn.count("cmdb_ci", "install_status!=7")


def _stale_cis(sn, L):
    total = _ci_total(sn)
    if not total:
        return _r("unknown", 0, "No active CIs in the CMDB")
    q = f"install_status!=7^sys_updated_on<{DAYS.format(90)}"
    n = sn.count("cmdb_ci", q)
    pct = round(100 * n / total, 1)
    return _threshold(int(pct), 10, 30, f"{pct}% of active CIs are stale (90+ days)",
                      f"{n} of {total} CIs ({pct}%) not updated in 90+ days", "high",
                      "Run discovery / reconciliation and retire CIs that no longer exist.", L("cmdb_ci", q))


def _ci_no_support(sn, L):
    total = _ci_total(sn)
    if not total:
        return _r("unknown", 0, "No active CIs in the CMDB")
    q = "install_status!=7^support_groupISEMPTY"
    try:
        n = sn.count("cmdb_ci", q)
    except SNError:
        return _r("unknown", None, "support_group not available on cmdb_ci")
    pct = round(100 * n / total, 1)
    return _threshold(int(pct), 30, 70, f"{pct}% of CIs have no support group",
                      f"{n} of {total} CIs ({pct}%) have no support group", "medium",
                      "Assign support groups so incidents on these CIs route automatically.", L("cmdb_ci", q))


def _ci_no_name(sn, L):
    q = "install_status!=7^nameISEMPTY"
    n = sn.count("cmdb_ci", q)
    return _threshold(n, 1, 50, "Every active CI has a name", "{n} active CIs have no name", "medium",
                      "Fix identification rules / data sources that create nameless CIs.", L("cmdb_ci", q))


def _unassigned_inc(sn, L):
    q = "active=true^assignment_groupISEMPTY"
    n = sn.count("incident", q)
    return _threshold(n, 1, 20, "All active incidents have an assignment group",
                      "{n} active incidents have no assignment group", "high",
                      "Add assignment rules / data lookups so every incident is routed on creation.", L("incident", q))


def _open_p1(sn, L):
    q = "active=true^priority=1"
    n = sn.count("incident", q)
    return _threshold(n, 1, 5, "No open P1 incidents", "{n} open P1 (critical) incident(s)", "critical",
                      "Confirm a major-incident process is running for each open P1.", L("incident", q))


def _sla_breach(sn, L):
    q = "active=true^has_breached=true^task.sys_class_name=incident"
    try:
        n = sn.count("task_sla", q)
    except SNError:
        return _r("unknown", None, "task_sla not readable (SLA plugin inactive or no access)")
    return _threshold(n, 1, 10, "No active incident SLAs are breached", "{n} active incident SLAs are breached",
                      "high", "Add escalation before breach (SLA workflow / flow at 50% and 75%).", L("task_sla", q))


def _aged_inc(sn, L):
    active = sn.count("incident", "active=true")
    if not active:
        return _r("pass", 0, "No active incidents")
    q = f"active=true^opened_at<{DAYS.format(30)}"
    n = sn.count("incident", q)
    pct = round(100 * n / active, 1)
    return _threshold(int(pct), 10, 25, f"{pct}% of active incidents are older than 30 days",
                      f"{n} of {active} active incidents ({pct}%) are older than 30 days", "medium",
                      "Review the backlog; close or convert stale incidents to problems.", L("incident", q))


def _no_close_notes(sn, L):
    q = f"stateIN6,7^resolved_at>={DAYS.format(30)}^close_notesISEMPTY"
    n = sn.count("incident", q)
    return _threshold(n, 1, 25, "Resolved incidents in the last 30 days all have close notes",
                      "{n} incidents resolved in 30 days have no close notes", "low",
                      "Make close notes mandatory on resolve (UI policy / data policy).", L("incident", q))


def _change_overdue(sn, L):
    q = "active=true^end_date<javascript:gs.nowDateTime()^end_dateISNOTEMPTY"
    n = sn.count("change_request", q)
    return _threshold(n, 1, 10, "No open changes are past their planned end",
                      "{n} open change(s) are past their planned end date", "medium",
                      "Close, reschedule, or cancel overdue changes.", L("change_request", q))


def _emergency_ratio(sn, L):
    base = f"sys_created_on>={DAYS.format(90)}"
    total = sn.count("change_request", base)
    if not total:
        return _r("unknown", 0, "No changes created in the last 90 days")
    n = sn.count("change_request", base + "^type=emergency")
    pct = round(100 * n / total, 1)
    return _threshold(int(pct), 10, 20, f"{pct}% of changes (90 days) were emergency",
                      f"{n} of {total} changes ({pct}%) in 90 days were emergency", "medium",
                      "High emergency share usually means planning gaps; review with CAB.",
                      L("change_request", base + "^type=emergency"))


def _unapproved_changes(sn, L):
    q = "stateIN-1,0,3^approval!=approved^type!=standard"
    n = sn.count("change_request", q)
    return _threshold(n, 1, 5, "No normal/emergency changes reached Implement/Review/Closed without approval",
                      "{n} normal/emergency change(s) reached Implement/Review/Closed without approval",
                      "critical", "Enforce approval before Implement (change model / state model).",
                      L("change_request", q))


def _slow_tx(sn, L):
    q = f"response_time>5000^sys_created_on>={DAYS.format(7)}"
    try:
        n = sn.count("syslog_transaction", q)
    except SNError:
        return _r("unknown", None, "Transaction log not readable with this user")
    return _threshold(n, 100, 1000, "{n} transactions over 5 s in the last 7 days",
                      "{n} transactions took longer than 5 s in the last 7 days", "medium",
                      "Open Slow Transactions / Slow Queries and fix the top offenders.", L("syslog_transaction", q))


def _br_current_update(sn, L):
    q = "active=true^scriptLIKEcurrent.update()^whenINbefore,after"
    n = sn.count("sys_script", q)
    return _threshold(n, 1, 5, "No before/after business rules call current.update()",
                      "{n} before/after business rule(s) call current.update()", "high",
                      "Remove current.update() from before/after rules (recursion, double audit).", L("sys_script", q))


def _client_gliderecord(sn, L):
    q = "active=true^scriptLIKEnew GlideRecord("
    n = sn.count("sys_script_client", q)
    return _threshold(n, 1, 10, "No client scripts use synchronous GlideRecord",
                      "{n} client script(s) use GlideRecord in the browser", "medium",
                      "Replace with GlideAjax + a client-callable script include.", L("sys_script_client", q))


def _stale_update_sets(sn, L):
    q = f"state=in progress^sys_updated_on<{DAYS.format(30)}^name!=Default"
    n = sn.count("sys_update_set", q)
    return _threshold(n, 3, 10, "{n} in-progress update sets untouched for 30+ days",
                      "{n} in-progress update sets untouched for 30+ days", "low",
                      "Complete and migrate, or merge and ignore abandoned update sets.", L("sys_update_set", q))


def _atf(sn, L):
    try:
        n = sn.count("sys_atf_test", "active=true")
    except SNError:
        return _r("unknown", None, "ATF tables not readable")
    if n == 0:
        return _r("fail", 0, "No active ATF tests", "medium",
                  "Add ATF tests for critical flows before promoting update sets.", L("sys_atf_test", "active=true"))
    if n < 10:
        return _r("warn", n, f"Only {n} active ATF tests", "low", "Grow ATF coverage for critical processes.",
                  L("sys_atf_test", "active=true"))
    return _r("pass", n, f"{n} active ATF tests")


def _custom_table_acls(sn, L):
    tables = sn.query("sys_db_object", "nameSTARTSWITHu_^ORnameSTARTSWITHx_", "name", limit=100)
    names = [t["name"] for t in tables if t.get("name")]
    if not names:
        return _r("pass", 0, "No custom tables")
    acls = sn.query("sys_security_acl", f"type=record^nameIN{','.join(names)}^active=true", "name", limit=1000)
    covered = {a.get("name") for a in acls}
    missing = [n for n in names if n not in covered]
    res = _threshold(len(missing), 1, 5, "Every custom table has table-level ACLs",
                     "{n} custom table(s) have no table-level ACL", "high",
                     "Add read/write/create/delete ACLs for these tables: " + ", ".join(missing[:10]),
                     L("sys_db_object", "nameIN" + ",".join(missing[:50])))
    res["evidence"] = missing[:25]
    return res


def _incident_audit(sn, L):
    row = sn.get_one("sys_dictionary", "name=incident^internal_type=collection", "audit")
    if row is None:
        return _r("unknown", None, "Could not read the incident table dictionary entry")
    on = str(row.get("audit", "")).lower() == "true"
    return _r("pass", True, "Auditing is on for incident") if on else \
        _r("fail", False, "Auditing is off for the incident table", "high",
           "Turn on auditing for incident (dictionary collection entry → Audit).",
           L("sys_dictionary", "name=incident^internal_type=collection"))


CHECKS: List[tuple] = [
    ("sec.admins", "security", "Admin role holders", _admins),
    ("sec.inactive_admins", "security", "Inactive users with admin", _inactive_admins),
    ("sec.security_admins", "security", "security_admin holders", _security_admins),
    ("sec.csrf", "security", "CSRF protection", _csrf),
    ("sec.session_timeout", "security", "Session timeout", _session_timeout),
    ("sec.mfa", "security", "Multi-factor authentication", _mfa),
    ("cmdb.stale", "cmdb", "Stale CIs", _stale_cis),
    ("cmdb.support_group", "cmdb", "CIs without support group", _ci_no_support),
    ("cmdb.no_name", "cmdb", "Nameless CIs", _ci_no_name),
    ("inc.unassigned", "incidents", "Unassigned incidents", _unassigned_inc),
    ("inc.p1", "incidents", "Open P1 incidents", _open_p1),
    ("inc.sla", "incidents", "Breached incident SLAs", _sla_breach),
    ("inc.aged", "incidents", "Aged incidents", _aged_inc),
    ("inc.close_notes", "incidents", "Resolutions without close notes", _no_close_notes),
    ("chg.overdue", "changes", "Overdue open changes", _change_overdue),
    ("chg.emergency", "changes", "Emergency change share", _emergency_ratio),
    ("chg.unapproved", "changes", "Changes implemented without approval", _unapproved_changes),
    ("plat.slow_tx", "platform", "Slow transactions", _slow_tx),
    ("plat.br_update", "platform", "current.update() in business rules", _br_current_update),
    ("plat.client_gr", "platform", "GlideRecord in client scripts", _client_gliderecord),
    ("plat.stale_us", "platform", "Abandoned update sets", _stale_update_sets),
    ("gov.atf", "compliance", "ATF test coverage", _atf),
    ("gov.acls", "compliance", "ACLs on custom tables", _custom_table_acls),
    ("gov.audit", "compliance", "Incident auditing", _incident_audit),
]

POINTS = {"pass": 1.0, "warn": 0.5, "fail": 0.0}


def run_scan(sn: SNClient) -> Dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        who = sn.user()
    except SNError as e:
        if e.status == 401:
            raise HTTPException(status_code=401, detail="ServiceNow rejected the credentials (401) — scan not run")
        if e.status in (503, 495):
            raise HTTPException(status_code=502, detail=f"Cannot reach ServiceNow: {e}")
        raise e.to_http("Connection check")
    build = None
    try:
        build = sn.property("glide.buildname") or sn.property("glide.war")
    except SNError:
        pass

    link = Check(sn).list_link
    checks = []
    for cid, cat, title, fn in CHECKS:
        try:
            res = fn(sn, link)
        except SNError as e:
            res = _r("unknown", None, f"Check could not run ({e.status}: {e.message})")
        except Exception as e:  # defensive: a bug in one check must not fake a result
            res = _r("unknown", None, f"Check error: {e}")
        checks.append({"id": cid, "category": cat, "title": title, **res})

    categories = []
    for key, label, icon in CATEGORIES:
        mine = [c for c in checks if c["category"] == key]
        known = [c for c in mine if c["status"] in POINTS]
        score = round(100 * sum(POINTS[c["status"]] for c in known) / len(known)) if known else None
        bad = [c for c in mine if c["status"] in ("fail", "warn")]
        summary = "; ".join(c["summary"] for c in bad[:2]) if bad else \
            ("All checks passed" if known else "No checks could run in this area")
        categories.append({"key": key, "label": label, "icon": icon, "score": score, "summary": summary,
                           "checks_run": len(known), "checks_unknown": len(mine) - len(known)})
    scored = [c["score"] for c in categories if c["score"] is not None]
    overall = round(sum(scored) / len(scored)) if scored else None
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "": 4}
    findings = sorted(
        [{"severity": c["severity"] or ("medium" if c["status"] == "fail" else "low"), "title": c["title"],
          "description": c["summary"], "recommendation": c["recommendation"], "link": c["link"],
          "status": c["status"], "category": c["category"], "evidence": c.get("evidence")}
         for c in checks if c["status"] in ("fail", "warn")],
        key=lambda f: sev_rank.get(f["severity"], 4))
    return {
        "overall_score": overall,
        "critical_findings": sum(1 for f in findings if f["severity"] == "critical"),
        "warnings": sum(1 for f in findings if f["severity"] != "critical"),
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "unknown": sum(1 for c in checks if c["status"] == "unknown"),
        "categories": categories,
        "findings": findings,
        "checks": checks,
        "instance": sn.instance,
        "build": build,
        "scanned_as": who.get("user_name"),
        "scan_time": started.isoformat(),
        "duration_s": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "api_calls": sn.calls,
        "method": "Live read-only queries (Aggregate API). Unknown checks are excluded from the score.",
    }
