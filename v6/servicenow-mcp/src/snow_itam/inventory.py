"""Product models, stockrooms, stock levels, transfer orders and consumable quantities."""

from typing import Any, Dict, List

from snow_itam import schema as S
from snow_itam.core import ITAMCore, check_date, now_utc, raw
from snow_itam.errors import ITAMError

MODEL_TABLES = {"hardware": "cmdb_hardware_product_model", "consumable": "cmdb_consumable_product_model",
                "software": "cmdb_software_product_model", "license": "cmdb_software_product_model",
                "": "cmdb_model"}
MODEL_FIELDS = "sys_id,sys_class_name,name,display_name,manufacturer,model_number,cmdb_model_category,status,short_description"


class InventoryMixin(ITAMCore):

    # ── models ───────────────────────────────────────────────────────────────────
    def list_asset_models(self, asset_class: str = "", name: str = "", manufacturer: str = "",
                          limit: int = 20) -> dict:
        table = MODEL_TABLES.get((asset_class or "").lower())
        if not table:
            raise ITAMError(f"asset_class must be one of: hardware, consumable, software", "INVALID_ARGS")
        parts = []
        if name:
            parts.append(f"nameLIKE{name}^ORdisplay_nameLIKE{name}^ORmodel_numberLIKE{name}")
        if manufacturer:
            parts.append(f"manufacturer={self.resolve('core_company', manufacturer, label='manufacturer')}")
        rows = self.view_query(table, "^".join(parts), MODEL_FIELDS, limit, order_by="name")
        return {"table": table, "count": len(rows), "models": rows}

    def create_asset_model(self, asset_class: str, name: str, manufacturer: str, model_category: str = "",
                           model_number: str = "", short_description: str = "", dry_run: bool = False) -> dict:
        cls = (asset_class or "").lower()
        if cls not in ("hardware", "consumable", "software"):
            raise ITAMError("asset_class must be hardware, consumable or software", "INVALID_ARGS")
        table = MODEL_TABLES[cls]
        if not name.strip():
            raise ITAMError("name is required", "INVALID_ARGS")
        maker = self.resolve("core_company", manufacturer, label="manufacturer")
        expected_asset = {"hardware": "alm_hardware", "consumable": "alm_consumable", "software": "alm_license"}[cls]
        if model_category:
            cat = self.resolve("cmdb_model_category", model_category, label="model category")
            cat_row = self.b.get("cmdb_model_category", cat, "name,asset_class")
            if cat_row.get("asset_class") and cat_row["asset_class"] != expected_asset:
                raise ITAMError(f"Model category '{cat_row.get('name')}' creates {cat_row['asset_class']} assets, "
                                f"not {expected_asset}", "INVALID_ARGS")
        elif cls == "software":
            cats = self.b.query("cmdb_model_category", "asset_class=alm_license", "sys_id,name", 2)
            cat = cats[0]["sys_id"] if len(cats) == 1 else ""
        else:
            raise ITAMError("model_category is required (e.g. Computer, Monitor) — it decides the asset type",
                            "INVALID_ARGS")
        dup = self.b.query("cmdb_model", f"name={name.strip()}^manufacturer={maker}", "sys_id", 1)
        if dup:
            raise ITAMError(f"Model '{name}' from this manufacturer already exists", "DUPLICATE",
                            f"sys_id {dup[0]['sys_id']}", status=409)
        payload = {"name": name.strip(), "manufacturer": maker, "status": "In Production"}
        if cat:
            payload["cmdb_model_category"] = cat
        if model_number:
            payload["model_number"] = model_number
        if short_description:
            payload["short_description"] = short_description
        return self.write(area="model", action="create asset model", table=table, payload=payload, dry_run=dry_run)

    # ── stockrooms ────────────────────────────────────────────────────────────────
    def list_stockrooms(self, name: str = "", type: str = "", location: str = "", limit: int = 25) -> dict:
        parts = []
        if name:
            parts.append(f"nameLIKE{name}")
        if type:
            parts.append(f"type={self.resolve('alm_stockroom_type', type, label='stockroom type')}")
        if location:
            parts.append(f"location={self.resolve('cmn_location', location, label='location')}")
        rows = self.view_query("alm_stockroom", "^".join(parts),
                               "sys_id,name,type,location,manager,assignment_group,external", limit, order_by="name")
        return {"count": len(rows), "stockrooms": rows}

    def create_stockroom(self, name: str, location: str, type: str = "", manager: str = "",
                         assignment_group: str = "", dry_run: bool = False) -> dict:
        if not (name or "").strip():
            raise ITAMError("name is required", "INVALID_ARGS")
        if self.b.query("alm_stockroom", f"name={name.strip()}", "sys_id", 1):
            raise ITAMError(f"Stockroom '{name}' already exists", "DUPLICATE", status=409)
        payload = {"name": name.strip(), "location": self.resolve("cmn_location", location, label="location")}
        if type:
            payload["type"] = self.resolve("alm_stockroom_type", type, label="stockroom type")
        if manager:
            payload["manager"] = self.resolve_user(manager, "manager")
        if assignment_group:
            payload["assignment_group"] = self.resolve("sys_user_group", assignment_group, label="assignment group")
        return self.write(area="inventory", action="create stockroom", table="alm_stockroom", payload=payload,
                          dry_run=dry_run)

    def get_stockroom_inventory(self, stockroom: str, include_stock_rules: bool = True) -> dict:
        room = self.resolve("alm_stockroom", stockroom, label="stockroom")
        info = self.view("alm_stockroom", room, "sys_id,name,type,location,manager")
        rows = self.b.aggregate("alm_asset", f"stockroom={room}", group_by=("model", "install_status", "substatus"),
                                sum_fields=("quantity",))
        names: Dict[str, str] = {}
        lines = []
        for r in rows:
            g = r["group"]
            mid = g.get("model") or ""
            if mid and mid not in names:
                names[mid] = (self.b.get("cmdb_model", mid, "display_name,name") or {}).get("display_name") or mid
            lines.append({"model": names.get(mid, mid), "model_sys_id": mid,
                          "state": self.state_label(g.get("install_status", "")), "substatus": g.get("substatus", ""),
                          "records": r["count"], "units": int(r["sum"].get("quantity") or 0)})
        out: Dict[str, Any] = {"stockroom": info, "lines": sorted(lines, key=lambda x: (x["model"], x["state"])),
                               "total_records": sum(x["records"] for x in lines),
                               "total_units": sum(x["units"] for x in lines)}
        if include_stock_rules and self.table_exists("alm_stock_rule"):
            rules = []
            for rule in self.b.query("alm_stock_rule", f"stockroom={room}^active=true",
                                     "sys_id,model,threshold,order_size,restocking_option,parent_stockroom", 50):
                mid = raw(rule.get("model"))
                available = sum(x["units"] for x in lines if x["model_sys_id"] == mid
                                and x["state"] == self.state_label(S.STATE_IN_STOCK)
                                and x["substatus"] in ("available", ""))
                threshold = int(float(rule.get("threshold") or 0))
                rules.append({"model": names.get(mid) or (self.b.get("cmdb_model", mid, "display_name") or {}).get(
                    "display_name", mid), "threshold": threshold, "available_units": available,
                    "below_threshold": available < threshold, "order_size": rule.get("order_size"),
                    "restocking_option": rule.get("restocking_option")})
            out["stock_rules"] = rules
        return out

    # ── transfers ────────────────────────────────────────────────────────────────
    def list_transfer_orders(self, stage: str = "", stockroom: str = "", limit: int = 20) -> dict:
        parts = []
        if stage:
            parts.append(f"stage={stage}")
        if stockroom:
            room = self.resolve("alm_stockroom", stockroom, label="stockroom")
            parts.append(f"from_stockroom={room}^ORto_stockroom={room}")
        rows = self.view_query("alm_transfer_order", "^".join(parts),
                               "sys_id,number,stage,from_stockroom,to_stockroom,requested_date,delivery_by_date,"
                               "delivery_method,sys_updated_on", limit, order_by="-sys_created_on")
        return {"count": len(rows), "transfer_orders": rows}

    def transfer_asset(self, asset: str, to_stockroom: str, quantity: int = 1, delivery_by_date: str = "",
                       dry_run: bool = False, confirm: bool = False) -> dict:
        """Create a Draft transfer order + line. Shipping and receiving follow ServiceNow's transfer order tasks."""
        self.require_table("alm_transfer_order", "Transfer orders", "com.snc.asset_management")
        hit = self.find_asset(asset)
        a = self.b.get("alm_asset", hit["sys_id"], "sys_id,model,stockroom,install_status,quantity,sys_class_name,"
                                                    "assigned_to,display_name")
        src = raw(a.get("stockroom"))
        if not src:
            raise ITAMError("The asset has no stockroom to transfer from", "INVALID_ARGS",
                            "Return it to stock first (assign_asset action=unassign)")
        dst = self.resolve("alm_stockroom", to_stockroom, label="to_stockroom")
        if dst == src:
            raise ITAMError("The asset is already in that stockroom", "INVALID_ARGS")
        if raw(a.get("install_status")) != S.STATE_IN_STOCK:
            raise ITAMError("Only assets 'In stock' can be transferred", "INVALID_ARGS",
                            f"Current state: {self.state_label(raw(a.get('install_status')))}")
        qty = max(1, int(quantity or 1))
        if qty > int(float(a.get("quantity") or 1)):
            raise ITAMError(f"Only {a.get('quantity')} unit(s) available on this asset record", "INVALID_ARGS")
        open_line = self.b.query("alm_transfer_order_line", f"asset={hit['sys_id']}^stageNOT INreceived,delivered,cancelled",
                                 "sys_id,number", 1)
        if open_line:
            raise ITAMError("The asset already has an open transfer order line", "DUPLICATE",
                            f"Line {open_line[0].get('number')}", status=409)
        order = {"from_stockroom": src, "to_stockroom": dst, "requested_date": now_utc(), "stage": "draft"}
        if delivery_by_date:
            order["delivery_by_date"] = check_date("delivery_by_date", delivery_by_date, with_time=True)
        line = {"asset": hit["sys_id"], "model": raw(a.get("model")), "quantity_requested": str(qty),
                "from_stockroom": src, "to_stockroom": dst}
        reasons = ["creates a transfer order that moves the asset between stockrooms"]
        res = self.write(area="inventory", action="create transfer order", table="alm_transfer_order", payload=order,
                         dry_run=dry_run, confirm=confirm, reasons=reasons,
                         context={"asset": a.get("display_name"), "line": line})
        if not res.get("success") or dry_run:
            return res
        line["transfer_order"] = res["sys_id"]
        line_res = self.write(area="inventory", action="create transfer order line", table="alm_transfer_order_line",
                              payload=line, confirm=True, reasons=reasons)
        res["line"] = {k: line_res.get(k) for k in ("sys_id", "verified", "mismatches", "link") if k in line_res}
        res["next_steps"] = ("The transfer order is in Draft. Submit it in ServiceNow (Ready for fulfillment) to "
                             "start the shipment and receiving tasks; the asset moves to In transit / In stock "
                             "as those tasks complete.")
        return res

    # ── consumables ───────────────────────────────────────────────────────────────
    def adjust_consumable_quantity(self, asset: str, quantity: int, work_note: str = "", dry_run: bool = False,
                                   confirm: bool = False) -> dict:
        hit = self.find_asset(asset)
        if hit.get("sys_class_name") != "alm_consumable":
            raise ITAMError("Only consumable assets have an adjustable quantity", "INVALID_ARGS")
        new = int(quantity)
        if new < 0:
            raise ITAMError("quantity cannot be negative", "INVALID_ARGS")
        before = self.b.get("alm_consumable", hit["sys_id"], "sys_id,quantity,install_status,display_name")
        old = int(float(before.get("quantity") or 0))
        reasons = [f"lowers the quantity from {old} to {new}"] if new < old else []
        payload: Dict[str, Any] = {"quantity": str(new)}
        if work_note:
            payload["work_notes"] = work_note
        return self.write(area="inventory", action="adjust consumable quantity", table="alm_consumable",
                          sys_id=hit["sys_id"], before=before, payload=payload, dry_run=dry_run, confirm=confirm,
                          reasons=reasons, critical=("quantity",),
                          context={"asset": before.get("display_name"), "from_quantity": old})
