"""Asset records: search, read, create, update, lifecycle, assignment, retirement."""

from typing import Any, Dict, List, Optional

from snow_itam import schema as S
from snow_itam.core import ITAMCore, check_date, is_sys_id, raw, today
from snow_itam.errors import ITAMError


class AssetsMixin(ITAMCore):

    # ── helpers ────────────────────────────────────────────────────────────────
    def _class(self, asset_class: str) -> Dict[str, str]:
        c = S.ASSET_CLASSES.get((asset_class or "asset").lower())
        if not c:
            raise ITAMError(f"Unknown asset_class '{asset_class}'", "INVALID_ARGS",
                            f"Use one of: {', '.join(S.ASSET_CLASSES)}")
        return c

    def _asset_fields(self, include_financials: bool, table: str) -> str:
        f = S.ASSET_FIELDS
        if table == "alm_license":
            f += "," + S.LICENSE_FIELDS
        if include_financials:
            f += "," + S.ASSET_FINANCIAL_FIELDS
        return f

    def find_asset(self, asset: Any) -> Dict[str, Any]:
        """sys_id / asset tag / serial number -> raw alm_asset row (with its class)."""
        v = str(asset or "").strip()
        if not v:
            raise ITAMError("asset is required (sys_id, asset tag or serial number)", "INVALID_ARGS")
        q = f"sys_id={v}" if is_sys_id(v) else f"asset_tag={v}^ORserial_number={v}"
        rows = self.b.query("alm_asset", q, "sys_id,sys_class_name,asset_tag,serial_number,display_name", 3)
        if not rows:
            raise ITAMError(f"No asset matches '{v}'", "NOT_FOUND", "Searched sys_id, asset tag and serial number",
                            status=404)
        if len(rows) > 1:
            raise ITAMError(f"'{v}' matches {len(rows)} assets — pass the sys_id", "AMBIGUOUS",
                            ", ".join(f"{r['sys_id']} ({r.get('display_name')})" for r in rows))
        return rows[0]

    def _raw_asset(self, sys_id: str, table: str = "alm_asset", extra: str = "") -> Dict[str, Any]:
        return self.b.get(table, sys_id, S.ASSET_FIELDS + ("," + extra if extra else ""))

    def _no_open_transfer(self, sys_id: str, action: str):
        """ServiceNow keeps assets on an open transfer order 'In stock / Pending transfer' (verified live)."""
        if not self.table_exists("alm_transfer_order_line"):
            return
        rows = self.b.query("alm_transfer_order_line",
                            f"asset={sys_id}^stageNOT INreceived,delivered,cancelled", "number,stage", 1)
        if rows:
            raise ITAMError(f"Cannot {action}: the asset is on open transfer order line {rows[0].get('number')} "
                            f"(stage {rows[0].get('stage')})", "INVALID_ARGS",
                            "Complete or cancel the transfer order first — ServiceNow would keep the asset "
                            "'Pending transfer'")

    # ── reads ──────────────────────────────────────────────────────────────────
    def search_assets(self, asset_class: str = "asset", text: str = "", model: str = "", state: str = "",
                      substatus: str = "", assigned_to: str = "", stockroom: str = "", location: str = "",
                      has_ci: Optional[bool] = None, limit: int = 20, include_financials: bool = False) -> dict:
        c = self._class(asset_class)
        parts = []
        if text:
            parts.append(f"asset_tagLIKE{text}^ORserial_numberLIKE{text}^ORdisplay_nameLIKE{text}")
        if model:
            parts.append(f"model={self.resolve('cmdb_model', model, ('name', 'display_name'), label='model')}")
        if state:
            st, sub = self.normalize_state(state, substatus, c["table"])
            parts.append(f"install_status={st}" + (f"^substatus={sub}" if sub else ""))
        elif substatus:
            parts.append(f"substatus={substatus}")
        if assigned_to:
            parts.append(f"assigned_to={self.resolve_user(assigned_to, 'assigned_to')}")
        if stockroom:
            parts.append(f"stockroom={self.resolve('alm_stockroom', stockroom, label='stockroom')}")
        if location:
            parts.append(f"location={self.resolve('cmn_location', location, label='location')}")
        if has_ci is True:
            parts.append("ciISNOTEMPTY")
        elif has_ci is False:
            parts.append("ciISEMPTY")
        q = "^".join(parts)
        rows = self.view_query(c["table"], q, self._asset_fields(include_financials, c["table"]), limit,
                               order_by="-sys_updated_on")
        return {"asset_class": asset_class, "table": c["table"], "total_matching": self.b.count(c["table"], q),
                "count": len(rows), "assets": rows}

    def get_asset(self, asset: str, include_financials: bool = False) -> dict:
        hit = self.find_asset(asset)
        table = hit.get("sys_class_name") or "alm_asset"
        rec = self.view(table, hit["sys_id"], self._asset_fields(include_financials, table))
        out: Dict[str, Any] = {"asset": rec, "table": table, "link": self.b.link(table, hit["sys_id"])}
        ci = raw(rec.get("ci"))
        if ci:
            out["configuration_item"] = self.view("cmdb_ci", ci, "sys_id,name,sys_class_name,serial_number,"
                                                                 "install_status,operational_status,asset")
        if self.table_exists("clm_m2m_contract_asset"):
            out["contracts"] = self.view_query("clm_m2m_contract_asset", f"asset={hit['sys_id']}",
                                               "contract,added,removed", 20)
        if table == "alm_license" and self.table_exists("alm_entitlement"):
            out["allocations"] = self.b.count("alm_entitlement", f"licensed_by={hit['sys_id']}")
        return out

    def get_asset_lifecycle_states(self, asset_class: str = "hardware") -> dict:
        c = self._class(asset_class)
        return {"asset_class": asset_class, "table": c["table"], "states": self.lifecycle(c["table"]),
                "confirmation_required_for": [f"{v} ({k})" for k, v in S.CONSEQUENTIAL_STATES.items()]
                + ["any disposal substate when retiring", "assignment / ownership changes"],
                "source": "sys_choice (live from the instance)"}

    # ── create ─────────────────────────────────────────────────────────────────
    def create_asset(self, model: str, asset_class: str = "", serial_number: str = "", asset_tag: str = "",
                     quantity: int = 1, state: str = "In stock", substatus: str = "", stockroom: str = "",
                     location: str = "", assigned_to: str = "", company: str = "", department: str = "",
                     vendor: str = "", purchase_date: str = "", warranty_expiration: str = "", po_number: str = "",
                     acquisition_method: str = "", cost: Optional[float] = None, comments: str = "",
                     link_existing_ci: bool = True, dry_run: bool = False) -> dict:
        model_id = self.resolve("cmdb_model", model, ("name", "display_name"), label="model")
        m = self.b.get("cmdb_model", model_id, "sys_id,sys_class_name,name,cmdb_model_category,status")
        model_cls = S.MODEL_CLASS_TO_ASSET.get(m.get("sys_class_name") or "")
        cats = [x for x in raw(m.get("cmdb_model_category")).split(",") if x]
        asset_table, category = "", ""
        for cat in cats:
            row = self.b.get("cmdb_model_category", cat, "sys_id,name,asset_class")
            if row.get("asset_class"):
                asset_table, category = row["asset_class"], cat
                break
        if not asset_table:
            raise ITAMError(f"Model '{m.get('name')}' has no model category with an asset class", "INVALID_ARGS",
                            "Set a model category (e.g. Computer) on the model so ServiceNow knows the asset type")
        derived = S.TABLE_TO_CLASS.get(asset_table, "asset")
        if asset_class and asset_class.lower() != derived:
            raise ITAMError(f"Model '{m.get('name')}' creates {derived} assets ({asset_table}), not {asset_class}",
                            "INVALID_ARGS")
        if derived == "license":
            raise ITAMError("Software licenses are created with create_license", "INVALID_ARGS")
        warnings = []
        if model_cls and S.ASSET_CLASSES[model_cls]["table"] != asset_table and derived != "asset":
            warnings.append(f"Model class {m.get('sys_class_name')} usually creates {model_cls} assets")
        if (m.get("status") or "") in ("Retired", "Sold"):
            warnings.append(f"Model status is {m.get('status')}")

        st, sub = self.normalize_state(state or "In stock", substatus, asset_table)
        payload: Dict[str, Any] = {"model": model_id, "model_category": category, "install_status": st,
                                   "quantity": str(max(1, int(quantity or 1)))}
        if derived != "consumable" and int(quantity or 1) != 1:
            raise ITAMError("Only consumables can have a quantity above 1 — create one record per device",
                            "INVALID_ARGS")
        if sub:
            payload["substatus"] = sub
        if serial_number:
            payload["serial_number"] = serial_number.strip()
        if asset_tag:
            payload["asset_tag"] = asset_tag.strip()
        if stockroom:
            payload["stockroom"] = self.resolve("alm_stockroom", stockroom, label="stockroom")
        if location:
            payload["location"] = self.resolve("cmn_location", location, label="location")
        if assigned_to:
            payload["assigned_to"] = self.resolve_user(assigned_to, "assigned_to")
        for k, v, t in (("company", company, "core_company"), ("vendor", vendor, "core_company"),
                        ("department", department, "cmn_department")):
            if v:
                payload[k] = self.resolve(t, v, label=k)
        for k, v in (("purchase_date", purchase_date), ("warranty_expiration", warranty_expiration)):
            if v:
                payload[k] = check_date(k, v)
        if po_number:
            payload["po_number"] = po_number
        if acquisition_method:
            if acquisition_method not in S.ACQUISITION_METHODS:
                raise ITAMError(f"acquisition_method must be one of {', '.join(S.ACQUISITION_METHODS)}",
                                "INVALID_ARGS")
            payload["acquisition_method"] = acquisition_method
        if cost is not None:
            payload["cost"] = str(cost)
        if comments:
            payload["comments"] = comments

        if st == S.STATE_IN_STOCK and not payload.get("stockroom"):
            raise ITAMError("Assets 'In stock' need a stockroom", "INVALID_ARGS")
        if st == S.STATE_IN_USE and not payload.get("assigned_to"):
            warnings.append("Asset is 'In use' without assigned_to")
        if st in S.CONSEQUENTIAL_STATES:
            raise ITAMError(f"New assets cannot start as {S.CONSEQUENTIAL_STATES[st]}", "INVALID_ARGS")

        # duplicate protection: asset records and CIs
        if serial_number:
            dup = self.b.query("alm_asset", f"serial_number={serial_number.strip()}^model={model_id}",
                               "sys_id,asset_tag,display_name", 2)
            if dup:
                raise ITAMError(f"An asset with serial '{serial_number}' and this model already exists",
                                "DUPLICATE", f"sys_id {dup[0]['sys_id']}", status=409)
        if asset_tag:
            dup = self.b.query("alm_asset", f"asset_tag={asset_tag.strip()}", "sys_id", 1)
            if dup:
                raise ITAMError(f"Asset tag '{asset_tag}' is already used", "DUPLICATE", f"sys_id {dup[0]['sys_id']}",
                                status=409)
        if serial_number and derived == "hardware":
            cis = self.b.query("cmdb_ci_hardware", f"serial_number={serial_number.strip()}", "sys_id,name,asset", 3)
            if cis:
                if any(raw(c.get("asset")) for c in cis):
                    raise ITAMError(f"A CI with serial '{serial_number}' already has an asset", "DUPLICATE",
                                    "Use get_asset on that CI's asset instead of creating another", status=409)
                if len(cis) == 1 and link_existing_ci:
                    payload["ci"] = cis[0]["sys_id"]
                    warnings.append(f"Linked to existing CI '{cis[0].get('name')}' with the same serial "
                                    "(prevents a duplicate CI)")
                else:
                    warnings.append(f"{len(cis)} CIs share this serial; none was linked")

        res = self.write(area="asset", action="create asset", table=asset_table, payload=payload, dry_run=dry_run,
                         warnings=warnings, context={"asset_class": derived, "model": m.get("name")})
        if res.get("success") and not dry_run:
            after = self.b.get(asset_table, res["sys_id"], "ci,asset_tag,display_name")
            res["ci"] = raw(after.get("ci"))
            if res["ci"] and "ci" not in payload:
                res["note_ci"] = "ServiceNow's asset-CI synchronization created the linked CI automatically"
            res["display_name"] = after.get("display_name")
        return res

    # ── update ─────────────────────────────────────────────────────────────────
    def update_asset(self, asset: str, fields: Dict[str, Any], dry_run: bool = False, confirm: bool = False) -> dict:
        if not fields:
            raise ITAMError("fields is required", "INVALID_ARGS")
        blocked = {k: v for k, v in S.ASSET_WORKFLOW_FIELDS.items() if k in fields}
        if blocked:
            raise ITAMError("Use the dedicated tool for: " + "; ".join(f"{k} -> {v}" for k, v in blocked.items()),
                            "INVALID_ARGS")
        hit = self.find_asset(asset)
        table = hit.get("sys_class_name") or "alm_asset"
        payload = self.resolve_refs(S.ASSET_UPDATABLE, fields, "update_asset")
        if payload.get("acquisition_method") and payload["acquisition_method"] not in S.ACQUISITION_METHODS:
            raise ITAMError(f"acquisition_method must be one of {', '.join(S.ACQUISITION_METHODS)}", "INVALID_ARGS")
        before = self._raw_asset(hit["sys_id"], table)
        reasons = []
        if "owned_by" in payload and raw(before.get("owned_by")) and not same_ref(before.get("owned_by"),
                                                                                  payload["owned_by"]):
            reasons.append("changes the asset owner")
        if payload.get("serial_number") and payload["serial_number"] != raw(before.get("serial_number")):
            dup = self.b.query("alm_asset", f"serial_number={payload['serial_number']}^model={raw(before.get('model'))}"
                                            f"^sys_id!={hit['sys_id']}", "sys_id", 1)
            if dup:
                raise ITAMError("Another asset of this model already has that serial", "DUPLICATE", status=409)
        if payload.get("asset_tag") and payload["asset_tag"] != raw(before.get("asset_tag")):
            if self.b.query("alm_asset", f"asset_tag={payload['asset_tag']}^sys_id!={hit['sys_id']}", "sys_id", 1):
                raise ITAMError("That asset tag is already used", "DUPLICATE", status=409)
        return self.write(area="asset", action="update asset", table=table, sys_id=hit["sys_id"], before=before,
                          payload=payload, dry_run=dry_run, confirm=confirm, reasons=reasons,
                          context={"asset": hit.get("display_name")})

    # ── lifecycle ────────────────────────────────────────────────────────────────
    def update_asset_lifecycle(self, asset: str, state: str, substatus: str = "", stockroom: str = "",
                               location: str = "", work_note: str = "", dry_run: bool = False,
                               confirm: bool = False) -> dict:
        hit = self.find_asset(asset)
        table = hit.get("sys_class_name") or "alm_asset"
        st, sub = self.normalize_state(state, substatus, table)
        if st == S.STATE_RETIRED:
            raise ITAMError("Use retire_asset to retire an asset (it records the disposal details)", "INVALID_ARGS")
        before = self._raw_asset(hit["sys_id"], table)
        if raw(before.get("install_status")) == S.STATE_RETIRED:
            raise ITAMError("The asset is Retired; reactivating retired assets is not supported here", "INVALID_ARGS")
        self._no_open_transfer(hit["sys_id"], "change the lifecycle state")
        payload: Dict[str, Any] = {"install_status": st, "substatus": sub}
        if stockroom:
            payload["stockroom"] = self.resolve("alm_stockroom", stockroom, label="stockroom")
        if location:
            payload["location"] = self.resolve("cmn_location", location, label="location")
        if st == S.STATE_IN_STOCK and not (payload.get("stockroom") or raw(before.get("stockroom"))):
            raise ITAMError("Moving to 'In stock' needs a stockroom", "INVALID_ARGS")
        warnings = []
        if st == S.STATE_IN_USE and not raw(before.get("assigned_to")):
            warnings.append("Asset is 'In use' without assigned_to — use assign_asset to record the user")
        if work_note:
            payload["work_notes"] = work_note
        reasons = [f"sets the asset to {S.CONSEQUENTIAL_STATES[st]}"] if st in S.CONSEQUENTIAL_STATES else []
        return self.write(area="asset", action="update asset lifecycle", table=table, sys_id=hit["sys_id"],
                          before=before, payload=payload, dry_run=dry_run, confirm=confirm, reasons=reasons,
                          warnings=warnings, critical=("install_status", "substatus"),
                          context={"asset": hit.get("display_name"),
                                   "from_state": self.state_label(raw(before.get("install_status"))),
                                   "to_state": self.state_label(st)})

    def assign_asset(self, asset: str, action: str = "assign", user: str = "", stockroom: str = "",
                     substatus: str = "", dry_run: bool = False, confirm: bool = False) -> dict:
        """assign: to a user (In use) · unassign: back to stock · reserve / unreserve: reserved_for in stock."""
        action = (action or "assign").lower()
        if action not in ("assign", "unassign", "reserve", "unreserve"):
            raise ITAMError("action must be assign, unassign, reserve or unreserve", "INVALID_ARGS")
        hit = self.find_asset(asset)
        table = hit.get("sys_class_name") or "alm_asset"
        before = self._raw_asset(hit["sys_id"], table)
        if raw(before.get("install_status")) in S.CONSEQUENTIAL_STATES:
            raise ITAMError(f"The asset is {self.state_label(raw(before.get('install_status')))} and cannot be "
                            "assigned", "INVALID_ARGS")
        self._no_open_transfer(hit["sys_id"], f"{action} the asset")
        payload: Dict[str, Any] = {}
        current = raw(before.get("assigned_to"))
        if action == "assign":
            uid = self.resolve_user(user, "user")
            st, sub = self.normalize_state(S.STATE_IN_USE, substatus, table)
            payload = {"assigned_to": uid, "install_status": st, "substatus": sub, "reserved_for": ""}
            if not raw(before.get("assigned")):
                payload["assigned"] = today()
            reasons = [f"transfers the asset from its current user to another user" if current and current != uid
                       else "assigns the asset to a user"]
        elif action == "unassign":
            room = self.resolve("alm_stockroom", stockroom, label="stockroom") if stockroom else raw(
                before.get("stockroom"))
            if not room:
                raise ITAMError("Returning an asset to stock needs a stockroom", "INVALID_ARGS")
            st, sub = self.normalize_state(S.STATE_IN_STOCK, substatus or "available", table)
            payload = {"assigned_to": "", "install_status": st, "substatus": sub, "stockroom": room}
            reasons = ["removes the asset from its current user"] if current else []
        elif action == "reserve":
            uid = self.resolve_user(user, "user")
            st, sub = self.normalize_state(S.STATE_IN_STOCK, "reserved", table)
            payload = {"reserved_for": uid, "install_status": st, "substatus": sub}
            if current:
                raise ITAMError("The asset is assigned to a user; unassign it before reserving", "INVALID_ARGS")
            reasons = ["reserves the asset for a user"]
        else:
            st, sub = self.normalize_state(S.STATE_IN_STOCK, substatus or "available", table)
            payload = {"reserved_for": "", "install_status": st, "substatus": sub}
            reasons = []
        return self.write(area="asset", action=f"{action} asset", table=table, sys_id=hit["sys_id"], before=before,
                          payload=payload, dry_run=dry_run, confirm=confirm, reasons=reasons,
                          critical=tuple(k for k in ("assigned_to", "reserved_for", "install_status") if k in payload),
                          context={"asset": hit.get("display_name")})

    def retire_asset(self, asset: str, substatus: str, disposal_reason: str = "", retirement_date: str = "",
                     dry_run: bool = False, confirm: bool = False) -> dict:
        hit = self.find_asset(asset)
        table = hit.get("sys_class_name") or "alm_asset"
        st, sub = self.normalize_state(S.STATE_RETIRED, substatus, table)
        if not sub:
            raise ITAMError("substatus is required to retire (e.g. disposed, sold, donated, vendor_credit)",
                            "INVALID_ARGS", "Allowed: " + ", ".join(
                                x["label"] for s in self.lifecycle(table) if s["value"] == S.STATE_RETIRED
                                for x in s["substates"]))
        before = self._raw_asset(hit["sys_id"], table)
        if raw(before.get("install_status")) == S.STATE_RETIRED:
            raise ITAMError("The asset is already Retired", "INVALID_ARGS")
        self._no_open_transfer(hit["sys_id"], "retire the asset")
        payload = {"install_status": st, "substatus": sub,
                   "retirement_date": check_date("retirement_date", retirement_date) or today()}
        if disposal_reason:
            payload["disposal_reason"] = disposal_reason
        warnings = []
        if raw(before.get("assigned_to")):
            warnings.append("The asset is still assigned to a user")
        if raw(before.get("ci")):
            warnings.append("The linked CI is updated by ServiceNow's asset-CI synchronization")
        return self.write(area="asset", action="retire asset", table=table, sys_id=hit["sys_id"], before=before,
                          payload=payload, dry_run=dry_run, confirm=confirm, warnings=warnings,
                          reasons=[f"retires the asset ({sub}) — this ends its lifecycle"],
                          critical=("install_status", "substatus"),
                          context={"asset": hit.get("display_name")})


def same_ref(a: Any, b: Any) -> bool:
    return raw(a) == raw(b)
