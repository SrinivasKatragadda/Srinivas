"""
Event Management (com.glide.em): events, alerts, acknowledge / close, alert groups, incident from an alert, service
health from open alerts.

Built from ServiceNow's documented em_event / em_alert layout and checked against the connected instance on every
call (tables -> PLUGIN_REQUIRED, fields -> INCOMPATIBLE_INSTANCE, roles -> FORBIDDEN). State and severity values are
read from sys_choice, never hard-coded, so the same code follows the instance's release.
"""

import json
from typing import Any, Dict, List, Optional

from snow_itam.core import check_date, is_sys_id, raw
from snow_itom import schema as S
from snow_itom.core import ITOMCore, batches, bounded
from snow_itom.errors import ITOMError

ALERT_READ = S.Needs("Event Management alerts", S.EM_PLUGIN, ("em_alert",),
                     {"em_alert": ("number", "state", "severity")}, {"em_alert": S.ALERT_OPTIONAL})
EVENT_READ = S.Needs("Event Management events", S.EM_PLUGIN, ("em_event",),
                     {"em_event": ("source", "node", "severity", "state")}, {"em_event": S.EVENT_OPTIONAL})


def _fields(base, optional_missing: Dict[str, List[str]], table: str) -> str:
    gone = set(optional_missing.get(table, []))
    return ",".join(f for f in base if f not in gone)


class EventsMixin(ITOMCore):
    ALERT_VIEW = ("sys_id", "number", "state", "severity", "acknowledged", "description", "source", "node", "type",
                  "resource", "metric_name", "message_key", "cmdb_ci", "incident", "parent", "group_source",
                  "initial_event_time", "last_event_time", "maintenance")
    EVENT_VIEW = ("sys_id", "source", "node", "type", "resource", "metric_name", "severity", "state",
                  "resolution_state", "description", "message_key", "time_of_event", "alert", "processing_notes",
                  "sys_created_on")

    # ── choice helpers (live) ──────────────────────────────────────────────────
    def _state_value(self, table: str, label: str, required: bool = True) -> str:
        """Stored value of a state by its label (e.g. 'Closed'); refuses when the instance has no such state."""
        for c in self.choices(table, "state"):
            if label.lower() in (c["value"].lower(), c["label"].lower()):
                return c["value"]
        if required:
            raise ITOMError(f"This instance's {table}.state has no '{label}' choice", "INCOMPATIBLE_INSTANCE",
                            "Allowed: " + ", ".join(f"{c['label']} ({c['value']})" for c in self.choices(table, "state")),
                            status=412)
        return ""

    def _alert(self, alert: str, fields: str) -> Dict[str, Any]:
        v = (alert or "").strip()
        if not v:
            raise ITOMError("alert is required (number or sys_id)", "INVALID_ARGS")
        rows = self.view_query("em_alert", f"sys_id={v}" if is_sys_id(v) else f"number={v}", fields, 1)
        if not rows:
            raise ITOMError(f"No alert '{v}'", "NOT_FOUND", status=404)
        return rows[0]

    # ── events ──────────────────────────────────────────────────────────────────
    def list_events(self, source: str = "", node: str = "", state: str = "", severity: str = "", since: str = "",
                    limit: int = 20) -> dict:
        info = self.ensure(EVENT_READ)
        parts = []
        if source:
            parts.append(f"source={source}")
        if node:
            parts.append(f"node={node}")
        if state:
            parts.append(f"state={self.live_choice(['em_event'], 'state', state, 'event state')}")
        if severity:
            parts.append(f"severity={self.live_choice(['em_event', 'em_alert'], 'severity', severity, 'severity')}")
        if since:
            parts.append(f"sys_created_on>={check_date('since', since, with_time=True)}")
        rows = self.view_query("em_event", "^".join(parts), _fields(self.EVENT_VIEW, info["optional_missing"],
                                                                     "em_event"),
                               bounded(limit, 20, 1, 200), order_by="-sys_created_on")
        return {"count": len(rows), "events": rows, **self._absent(info)}

    def get_event(self, event: str) -> dict:
        info = self.ensure(EVENT_READ)
        v = (event or "").strip()
        if not is_sys_id(v):
            raise ITOMError("event must be the sys_id of an em_event record", "INVALID_ARGS",
                            "Use list_events to find it")
        rec = self.view("em_event", v, _fields(self.EVENT_VIEW, info["optional_missing"], "em_event"))
        return {"event": rec, "link": self.b.link("em_event", v), **self._absent(info)}

    def create_event(self, source: str, node: str, severity: str, description: str = "", type: str = "",
                     resource: str = "", metric_name: str = "", message_key: str = "",
                     additional_info: Optional[Dict[str, Any]] = None, ci: str = "", allow_duplicate: bool = False,
                     dry_run: bool = False, confirm: bool = False) -> dict:
        """Send one event to Event Management (inserted into em_event, the documented ingestion table)."""
        info = self.ensure(S.NEEDS_EVENTS)
        if not (source or "").strip() or not (node or "").strip():
            raise ITOMError("source and node are required", "INVALID_ARGS")
        sev = self.live_choice(["em_event", "em_alert"], "severity", severity, "severity")
        if not sev:
            raise ITOMError("severity is required", "INVALID_ARGS")
        payload: Dict[str, Any] = {"source": source.strip(), "node": node.strip(), "severity": sev,
                                   "description": description or "", "type": type or "", "resource": resource or ""}
        opt_missing = set(info["optional_missing"].get("em_event", []))
        extras = {"metric_name": metric_name, "message_key": message_key}
        for k, v in extras.items():
            if v:
                if k in opt_missing:
                    raise ITOMError(f"This instance's em_event has no '{k}' field", "INCOMPATIBLE_INSTANCE",
                                    "Leave it out and try again", status=412)
                payload[k] = v
        if additional_info:
            if "additional_info" in opt_missing:
                raise ITOMError("This instance's em_event has no 'additional_info' field", "INCOMPATIBLE_INSTANCE",
                                "Leave it out and try again", status=412)
            payload["additional_info"] = json.dumps(additional_info)
        warnings = []
        if ci:
            cid = self.resolve_ci(ci)
            if "additional_info" in opt_missing:
                warnings.append("ci given, but this instance's em_event cannot carry it; Event Management will "
                                "bind the CI from node / additional_info by its own rules")
            else:
                extra = json.loads(payload.get("additional_info") or "{}")
                extra.setdefault("cmdb_ci", cid)
                payload["additional_info"] = json.dumps(extra)
        key = payload.get("message_key")
        if not allow_duplicate:
            pending = [self._state_value("em_event", s, required=False) for s in ("Ready", "Processing")]
            pending = [p for p in pending if p]
            dup_q = (f"message_key={key}" if key else
                     "^".join(f"{k}={payload[k]}" for k in ("source", "node", "type", "resource", "severity",
                                                             "description") if payload.get(k)))
            if pending:
                dup_q += "^stateIN" + ",".join(pending)
            dup = self.b.query("em_event", dup_q, "sys_id,state", 1) if pending else []
            if dup:
                raise ITOMError("An identical event is still waiting to be processed", "DUPLICATE",
                                f"sys_id {dup[0]['sys_id']} (state {dup[0].get('state')}). Pass allow_duplicate=true "
                                "to send it again.", status=409)
        reasons = ["Event Management rules may create or update alerts, and alert rules may open incidents or run "
                   "remediation"]
        return self.write(area="event_ingest", action="create_event", table="em_event", payload=payload,
                          dry_run=dry_run, confirm=confirm, reasons=reasons, warnings=warnings,
                          verify_fields="state,alert", critical=("source", "node", "severity"))

    # ── alerts ──────────────────────────────────────────────────────────────────
    def get_alert(self, alert: str) -> dict:
        info = self.ensure(ALERT_READ)
        fields = _fields(self.ALERT_VIEW, info["optional_missing"], "em_alert")
        rec = self._alert(alert, fields)
        sid = raw(rec.get("sys_id"))
        out: Dict[str, Any] = {"alert": rec, "link": self.b.link("em_alert", sid)}
        if "parent" not in info["optional_missing"].get("em_alert", []):
            out["secondary_alerts"] = self.view_query("em_alert", f"parent={sid}", "sys_id,number,state,severity,"
                                                      "description,cmdb_ci", 50)
        inc = raw(rec.get("incident"))
        if inc:
            out["incident"] = (self.view_query("incident", f"sys_id={inc}", S.INCIDENT_FIELDS, 1) or [None])[0]
        return {**out, **self._absent(info)}

    def acknowledge_alert(self, alert: str, acknowledged: bool = True, work_note: str = "",
                          dry_run: bool = False) -> dict:
        self.ensure(S.NEEDS_ALERTS)
        rec = self._alert(alert, "sys_id,number,state,acknowledged")
        payload: Dict[str, Any] = {"acknowledged": "true" if acknowledged else "false"}
        if work_note:
            payload["work_notes"] = work_note
        return self.write(area="alert_operate", action="acknowledge_alert", table="em_alert", payload=payload,
                          sys_id=raw(rec.get("sys_id")), before={"acknowledged": raw(rec.get("acknowledged"))},
                          dry_run=dry_run, context={"alert": raw(rec.get("number"))}, critical=("acknowledged",))

    def close_alert(self, alert: str, close_note: str = "", dry_run: bool = False, confirm: bool = False) -> dict:
        self.ensure(S.NEEDS_ALERTS)
        closed = self._state_value("em_alert", "Closed")
        rec = self._alert(alert, "sys_id,number,state,incident")
        payload: Dict[str, Any] = {"state": closed}
        if close_note:
            payload["work_notes"] = close_note
        warnings = []
        if raw(rec.get("incident")):
            warnings.append("The alert has an incident; closing the alert does not resolve the incident")
        return self.write(area="alert_operate", action="close_alert", table="em_alert", payload=payload,
                          sys_id=raw(rec.get("sys_id")), before={"state": raw(rec.get("state"))}, dry_run=dry_run,
                          confirm=confirm, reasons=[f"Closes alert {raw(rec.get('number'))}"], warnings=warnings,
                          context={"alert": raw(rec.get("number"))}, critical=("state",))

    def list_alert_groups(self, include_closed: bool = False, limit: int = 20) -> dict:
        """Primary alerts with their grouped (secondary) alerts — Event Management's correlation result."""
        info = self.ensure(S.NEEDS_ALERT_GROUPS)
        closed = self._state_value("em_alert", "Closed", required=False)
        q = "parentISNOTEMPTY" + (f"^state!={closed}" if closed and not include_closed else "")
        groups = [g for g in self.b.aggregate("em_alert", q, group_by=("parent",)) if g["group"].get("parent")]
        groups.sort(key=lambda g: -g["count"])
        groups = groups[:bounded(limit, 20, 1, 100)]
        counts = {g["group"]["parent"]: g["count"] for g in groups}
        primaries = []
        for chunk in batches(list(counts)):
            primaries += self.view_query("em_alert", "sys_idIN" + ",".join(chunk),
                                         _fields(("sys_id", "number", "state", "severity", "description", "cmdb_ci",
                                                  "group_source"), info["optional_missing"], "em_alert"), len(chunk))
        for p in primaries:
            p["secondary_count"] = counts.get(raw(p.get("sys_id")), 0)
        primaries.sort(key=lambda p: -p["secondary_count"])
        return {"count": len(primaries), "groups": primaries,
                "note": "Groups are formed by Event Management's correlation rules; use get_alert for the members",
                **self._absent(info)}

    def create_incident_from_alert(self, alert: str, short_description: str = "", assignment_group: str = "",
                                   urgency: str = "", impact: str = "", dry_run: bool = False,
                                   confirm: bool = False) -> dict:
        """Open an incident for an alert and link it on the alert (em_alert.incident). Returns the existing incident
        instead when the alert already has an active one."""
        self.ensure(S.NEEDS_ALERT_INCIDENT)
        rec = self._alert(alert, "sys_id,number,state,severity,description,cmdb_ci,incident,node,source")
        aid, number = raw(rec.get("sys_id")), raw(rec.get("number"))
        existing = raw(rec.get("incident"))
        if existing:
            inc = self.view_query("incident", f"sys_id={existing}", S.INCIDENT_FIELDS + ",active", 1)
            if inc and raw(inc[0].get("active")) in ("true", "1"):
                return {"success": False, "duplicate": True, "alert": number, "incident": inc[0],
                        "message": f"Alert {number} already has an active incident"}
        sev = raw(rec.get("severity"))
        level = {"1": "1", "2": "2"}.get(sev, "3")   # critical -> high, major -> medium, else low (overridable)
        payload: Dict[str, Any] = {
            "short_description": (short_description or f"Alert {number}: {raw(rec.get('description'))}")[:160],
            "description": f"Created from Event Management alert {number} (source {raw(rec.get('source'))}, node "
                           f"{raw(rec.get('node'))}, severity {sev}).\n\n{raw(rec.get('description'))}",
            "urgency": str(urgency or level), "impact": str(impact or level),
        }
        if raw(rec.get("cmdb_ci")):
            payload["cmdb_ci"] = raw(rec.get("cmdb_ci"))
        if assignment_group:
            payload["assignment_group"] = self.resolve("sys_user_group", assignment_group, ("name",),
                                                       label="assignment_group")
        reasons = [f"Creates an incident for alert {number} and links it on the alert"]
        if dry_run or not confirm:
            res = self.write(area="alert_incident", action="create_incident_from_alert", table="incident",
                             payload=payload, dry_run=dry_run, confirm=confirm, reasons=reasons,
                             context={"alert": number})
            return res
        created = self.write(area="alert_incident", action="create_incident_from_alert", table="incident",
                             payload=payload, confirm=True, reasons=reasons, context={"alert": number},
                             verify_fields="number", critical=("short_description",))
        inc_id = created.get("sys_id")
        link = self.write(area="alert_incident", action="link incident to alert", table="em_alert",
                          payload={"incident": inc_id, "work_notes": "Incident created from this alert"},
                          sys_id=aid, before={"incident": existing}, confirm=True, reasons=reasons,
                          critical=("incident",))
        inc_number = raw((self.b.get("incident", inc_id, "number") or {}).get("number"))
        return {"success": bool(created.get("success") and link.get("success")), "alert": number,
                "incident_sys_id": inc_id, "incident_number": inc_number, "incident_link": created.get("link"),
                "alert_linked": bool(link.get("success")), "verified": bool(created.get("verified") and
                                                                            link.get("verified")),
                **({"error": link.get("error")} if link.get("error") else {})}

    # ── service health ──────────────────────────────────────────────────────────
    def get_service_health(self, service: str, depth: int = 3, limit: int = 20) -> dict:
        """Worst open alert severity across the service and the CIs it depends on."""
        info = self.ensure(ALERT_READ)
        sid = self.resolve_service(service)
        levels, _, truncated = self.walk([sid], "down", bounded(depth, 3, 1, S.MAX_DEPTH), S.MAX_NODES)
        for ci in self.service_members([sid]).get(sid, []):
            levels.setdefault(ci, 1)
        closed = self._state_value("em_alert", "Closed", required=False)
        alerts: List[Dict[str, Any]] = []
        for chunk in batches(list(levels)):
            alerts += self.view_query("em_alert", "cmdb_ciIN" + ",".join(chunk) + (f"^state!={closed}" if closed else ""),
                                      "sys_id,number,state,severity,description,cmdb_ci,acknowledged", 200)
        sev_choices = self.choices("em_alert", "severity")
        label = {c["value"]: c["label"] for c in sev_choices}
        by_sev: Dict[str, int] = {}
        for a in alerts:
            s = raw(a.get("severity"))
            by_sev[label.get(s, s)] = by_sev.get(label.get(s, s), 0) + 1

        def rank(v: str) -> float:   # lower number = more severe; 0 is "Clear" on documented layouts
            try:
                n = float(v)
                return n if n > 0 else 99.0
            except ValueError:
                return 50.0
        worst = min((raw(a.get("severity")) for a in alerts), key=rank, default="")
        alerts.sort(key=lambda a: rank(raw(a.get("severity"))))
        return {"service": self.view(S.SERVICE_ROOT, sid, "sys_id,name,sys_class_name"),
                "health": label.get(worst, worst) if worst else "OK (no open alerts)",
                "open_alerts": len(alerts), "by_severity": by_sev, "cis_checked": len(levels),
                "truncated": truncated, "alerts": alerts[:bounded(limit, 20, 1, 200)],
                "method": "Worst severity of open alerts on the service and the CIs it depends on. Event "
                          "Management's own impact calculation (if configured) can differ.", **self._absent(info)}

    @staticmethod
    def _absent(info: Dict[str, Any]) -> Dict[str, Any]:
        miss = info.get("optional_missing") or {}
        return {"fields_not_on_this_instance": miss} if miss else {}
