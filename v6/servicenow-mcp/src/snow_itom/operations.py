"""Capability report, MID Servers, Discovery status / schedules, Event Management alerts (all read-only)."""

from typing import Any, Dict, List

from snow_itam.core import flatten, is_sys_id
from snow_itam.errors import ITAMError
from snow_itom import schema as S
from snow_itom.core import ITOMCore, bounded
from snow_itom.errors import ITOMError

ITOM_ROLE_NAMES = ("admin", "itom_admin", "discovery_admin", "mid_server", "sm_admin", "sm_read", "app_service_admin",
                   "sn_cmdb_admin", "sn_cmdb_editor", "cmdb_read", "itil", "flow_operator", "flow_designer",
                   "flow_admin", "evt_mgmt_admin", "evt_mgmt_operator", "evt_mgmt_user", "evt_mgmt_integration")


class OperationsMixin(ITOMCore):

    def _plugins(self, ids: List[str]) -> Dict[str, str]:
        ids = sorted({i for i in ids if i})
        try:
            rows = self.b.query("v_plugin", "idIN" + ",".join(ids), "id,active", 100)
            return {r["id"]: r.get("active", "") for r in rows}
        except ITAMError:
            return {}   # v_plugin is admin-only: table checks still decide availability

    def get_itom_capabilities(self, include_plugin_state: bool = False) -> dict:
        # v_plugin is a virtual table: on a busy instance one query took from 0.5 s to a 60 s transaction cancel,
        # so plugin states are opt-in. Availability is always decided by the tables.
        plugins = self._plugins([f["plugin"] for f in S.FEATURES] + [f["plugin"] for f in S.NOT_IMPLEMENTED]) \
            if include_plugin_state else {}
        features = []
        for f in S.FEATURES:
            missing = [t for t in f["tables"] if not self.table_exists(t)]
            state = plugins.get(f["plugin"], "not installed" if plugins else "not checked")
            features.append({"feature": f["feature"], "available": not missing, "tools": f["tools"],
                             "verification": f.get("verification", "verified on a live instance"),
                             **({"plugin": f["plugin"], "plugin_state": state} if f["plugin"] else {}),
                             **({"missing_tables": missing} if missing else {})})
        mids = {"total": self.b.count("ecc_agent"), "up": self.b.count("ecc_agent", "status=Up")} \
            if self.table_exists("ecc_agent") else {"total": 0, "up": 0}
        uid = self.me().get("sys_id")
        try:
            rows = self.b.query("sys_user_has_role", f"user={uid}^role.nameIN{','.join(ITOM_ROLE_NAMES)}",
                                "role.name", 100) if uid else []
            roles: Any = sorted({r.get("role.name") for r in rows if r.get("role.name")})
        except ITAMError:
            roles = "not readable (sys_user_has_role); ServiceNow ACLs apply"
        return {
            "instance": self.b.instance_url, "user": self.b.username, "features": features,
            "mid_servers": mids,
            "not_implemented": [{"feature": f["feature"], "requires": f["requires"],
                                 **({"plugin_state": plugins.get(f["plugin"], "not installed")}
                                    if f["plugin"] and plugins else {}),
                                 **({"table_present": self.table_exists(f["table"])} if f["table"] else {})}
                                for f in S.NOT_IMPLEMENTED],
            "your_itom_roles": roles,
            "note": "A feature is available only when its tables exist. Not-implemented features are named, never "
                    "emulated: this server does not produce health scores, anomalies or forecasts.",
        }

    def list_mid_servers(self, active_only: bool = False, limit: int = 20) -> dict:
        self.require_table("ecc_agent", "MID Server records", "")
        rows = self.view_query("ecc_agent", "status=Up" if active_only else "", S.MID_FIELDS,
                               bounded(limit, 20, 1, 200), order_by="name")
        out = {"count": len(rows), "mid_servers": rows}
        if not rows:
            out["note"] = ("No MID Servers match. Discovery, Service Mapping and orchestration on your network need a "
                           "validated MID Server (MID Server > Downloads on the instance).")
        return out

    def list_discovery_schedules(self, active_only: bool = True, limit: int = 15) -> dict:
        self.require_table("discovery_schedule", "Discovery schedules", "com.snc.discovery")
        rows = self.view_query("discovery_schedule", "active=true" if active_only else "", S.DISCOVERY_SCHEDULE_FIELDS,
                               bounded(limit, 15, 1, 200), order_by="name")
        return {"count": len(rows), "schedules": rows}

    def get_discovery_status(self, status: str = "", state: str = "", limit: int = 10) -> dict:
        self.require_table("discovery_status", "Discovery status", "com.snc.discovery")
        ident = (status or "").strip()
        if ident:
            q = f"sys_id={ident}" if is_sys_id(ident) else f"number={ident}"
            rows = self.view_query("discovery_status", q, S.DISCOVERY_STATUS_FIELDS, 1)
            if not rows:
                raise ITOMError(f"No discovery status '{ident}'", "NOT_FOUND", status=404)
            rec = rows[0]
            if self.table_exists("discovery_log"):
                rec["recent_log"] = self.view_query("discovery_log", f"status={rec.get('sys_id')}",
                                                    "sys_created_on,level,message,source", 20, order_by="-sys_created_on")
            return {"discovery_status": rec}
        q = f"state={self.live_choice(['discovery_status'], 'state', state, 'discovery state')}" if state else ""
        rows = self.view_query("discovery_status", q, S.DISCOVERY_STATUS_FIELDS, bounded(limit, 10, 1, 100),
                               order_by="-sys_created_on")
        return {"count": len(rows), "statuses": rows,
                **({} if rows else {"note": "No discovery runs recorded on this instance"})}

    def list_active_events(self, severity: str = "", state: str = "Open", limit: int = 15) -> dict:
        from snow_itom.events import ALERT_READ
        self.ensure(ALERT_READ)
        parts = []
        if state:
            parts.append(f"state={self.live_choice(['em_alert'], 'state', state, 'alert state')}")
        if severity:
            parts.append(f"severity={self.live_choice(['em_alert'], 'severity', severity, 'alert severity')}")
        rows = self.view_query("em_alert", "^".join(parts), S.ALERT_FIELDS, bounded(limit, 15, 1, 200),
                               order_by="-initial_event_time")
        return {"count": len(rows), "alerts": rows}
