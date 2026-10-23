"""Capabilities, overview, warranty and asset-CI reconciliation reports (counts plus small samples)."""

from typing import Any, Dict, List

from snow_itam import schema as S
from snow_itam.core import ITAMCore, days_from_today, raw, today
from snow_itam.errors import ITAMError


class ReportsMixin(ITAMCore):

    def _plugins(self, ids: List[str]) -> Dict[str, str]:
        try:
            rows = self.b.query("v_plugin", "idIN" + ",".join(sorted(set(ids))), "id,active", 100)
            return {r["id"]: r.get("active", "") for r in rows}
        except ITAMError:
            return {}   # v_plugin is admin-only: fall back to table checks

    def get_itam_capabilities(self) -> dict:
        ids = [f["plugin"] for f in S.FEATURES] + [f["plugin"] for f in S.LICENSED_FEATURES if f.get("plugin")] + \
              [f["plugin"] for f in S.UI_ONLY_FEATURES]
        plugins = self._plugins(ids)
        available = []
        for f in S.FEATURES:
            missing = [t for t in f["tables"] if not self.table_exists(t)]
            state = plugins.get(f["plugin"])
            ok = not missing and (state in (None, "", "active") or not plugins)
            available.append({"feature": f["feature"], "available": ok, "tools": f["tools"],
                              "plugin": f["plugin"], "plugin_state": state or "unknown",
                              **({"missing_tables": missing} if missing else {}),
                              **({} if ok else {"requires": f"Activate {f['plugin']}"})})
        roles = {}
        for area in S.ROLES:
            try:
                roles[area] = self.require_roles(area, area)["matched"] or ["(not checked)"]
            except ITAMError as e:
                roles[area] = [f"missing: {e.message}"]
        return {"instance": self.b.instance_url, "user": self.b.username, "features": available,
                "not_implemented": [{"feature": f["feature"], "requires": f["requires"],
                                     **({"plugin_state": plugins.get(f["plugin"], "not installed")}
                                        if f.get("plugin") else {})} for f in S.LICENSED_FEATURES],
                "ui_only": [{**f, "plugin_state": plugins.get(f["plugin"], "unknown")} for f in S.UI_ONLY_FEATURES],
                "your_roles_by_area": roles,
                "note": "Licensed features are named, not emulated: this server never produces compliance, "
                        "SaaS spend or cloud cost figures."}

    def itam_overview(self) -> dict:
        """Asset estate summary (the API equivalent of the Asset Management Workspace landing page)."""
        by_class = {g["group"].get("sys_class_name"): g["count"]
                    for g in self.b.aggregate("alm_asset", "", group_by=("sys_class_name",))}
        by_state = {self.state_label(g["group"].get("install_status", "")): g["count"]
                    for g in self.b.aggregate("alm_asset", "", group_by=("install_status",))}
        out: Dict[str, Any] = {
            "assets_by_class": by_class, "assets_by_state": by_state,
            "stockrooms": self.b.count("alm_stockroom"),
            "warranties_expiring": {f"{d}_days": self.b.count("alm_hardware", f"warranty_expiration>={today()}"
                                                                             f"^warranty_expiration<={days_from_today(d)}")
                                    for d in (30, 90)},
            "data_quality": {
                "hardware_without_serial": self.b.count("alm_hardware", "serial_numberISEMPTY^install_status!=7"),
                "in_use_without_assignee": self.b.count("alm_asset", "install_status=1^assigned_toISEMPTY"
                                                                     "^sys_class_name!=alm_license"),
                "hardware_without_ci": self.b.count("alm_hardware", "ciISEMPTY^install_status!=7"),
                "hardware_cis_without_asset": self.b.count("cmdb_ci_hardware", "assetISEMPTY"),
            },
        }
        if self.table_exists("alm_transfer_order"):
            out["open_transfer_orders"] = self.b.count("alm_transfer_order", "stageNOT INreceived,delivered,cancelled")
        if self.table_exists("alm_license"):
            out["licenses"] = self.b.count("alm_license")
            out["license_allocations"] = self.b.count("alm_entitlement") if self.table_exists("alm_entitlement") else 0
        if self.table_exists("ast_contract"):
            out["contracts_active"] = self.b.count("ast_contract", "state=active")
            out["contracts_expiring"] = {f"{d}_days": self.b.count("ast_contract", f"state=active^ends>={today()}"
                                                                                  f"^ends<={days_from_today(d)}")
                                         for d in (30, 60, 90)}
        if self.table_exists("proc_po"):
            out["purchase_orders_open"] = self.b.count("proc_po", "statusINrequested,ordered,pending")
        return out

    def list_expiring_warranties(self, within_days: int = 90, include_expired: bool = False, location: str = "",
                                 limit: int = 25, include_assignee: bool = False) -> dict:
        start = "1900-01-01" if include_expired else today()
        q = f"warranty_expiration>={start}^warranty_expiration<={days_from_today(int(within_days))}^install_status!=7"
        if location:
            q += f"^location={self.resolve('cmn_location', location, label='location')}"
        fields = "sys_id,asset_tag,display_name,serial_number,model,install_status,location,warranty_expiration,vendor"
        if include_assignee:
            fields += ",assigned_to"
        rows = self.view_query("alm_hardware", q, fields, limit, order_by="warranty_expiration")
        return {"window": {"from": start, "to": days_from_today(int(within_days))},
                "total": self.b.count("alm_hardware", q), "count": len(rows), "assets": rows}

    def asset_ci_reconciliation_report(self, sample_size: int = 10) -> dict:
        """Where asset records and CMDB CIs disagree. Read-only; ServiceNow's asset-CI sync owns the fixes."""
        n = max(1, min(int(sample_size or 10), 50))
        checks = [
            ("hardware_assets_without_ci", "alm_hardware", "ciISEMPTY^install_status!=7",
             "sys_id,asset_tag,display_name,serial_number"),
            ("hardware_cis_without_asset", "cmdb_ci_hardware", "assetISEMPTY", "sys_id,name,serial_number,sys_class_name"),
            ("serial_mismatch", "alm_hardware", "ciISNOTEMPTY^serial_numberISNOTEMPTY^ci.serial_numberISNOTEMPTY",
             "sys_id,asset_tag,display_name,serial_number,ci.serial_number"),
            ("retired_asset_with_active_ci", "alm_hardware", "install_status=7^ciISNOTEMPTY^ci.install_status!=7",
             "sys_id,asset_tag,display_name,ci.install_status"),
            ("active_asset_with_retired_ci", "alm_hardware", "install_status!=7^ci.install_status=7",
             "sys_id,asset_tag,display_name,install_status"),
            ("model_mismatch", "alm_hardware", "ciISNOTEMPTY^modelISNOTEMPTY^ci.model_idISNOTEMPTY",
             "sys_id,asset_tag,display_name,model,ci.model_id"),
        ]
        out: Dict[str, Any] = {}
        for key, table, q, fields in checks:
            if key in ("serial_mismatch", "model_mismatch"):
                # the Table API cannot compare two columns: scan a bounded window and compare here
                a, b = ("serial_number", "ci.serial_number") if key == "serial_mismatch" else ("model", "ci.model_id")
                rows = self.b.query(table, q, fields, 1000)
                bad = [r for r in rows if raw(r.get(a)).strip().lower() != raw(r.get(b)).strip().lower()]
                out[key] = {"count": len(bad), "scanned": len(rows), "sample": bad[:n],
                            **({"note": "scanned the first 1000 linked assets"} if len(rows) == 1000 else {})}
            else:
                out[key] = {"count": self.b.count(table, q), "sample": self.b.query(table, q, fields, n)}
        out["note"] = ("Asset↔CI fields are kept in step by ServiceNow's AssetAndCISynchronizer. Fix the source "
                       "record (asset or CI) and let the sync update the other side.")
        return out
