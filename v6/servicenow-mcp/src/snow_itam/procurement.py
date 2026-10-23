"""
Purchase orders and receiving (Procurement plugin, com.snc.procurement).

Receiving follows ServiceNow's own flow: a receiving slip line (proc_rec_slip_item) is inserted and the
platform's "Create Assets" business rule creates the assets in the stockroom and rolls quantities up to the
purchase order line and order. This module never creates the assets itself.
"""

import time
from typing import Any, Dict, List

from snow_itam import schema as S
from snow_itam.core import ITAMCore, check_date, is_sys_id, now_utc, raw
from snow_itam.errors import ITAMError

PLUGIN = "com.snc.procurement"


class ProcurementMixin(ITAMCore):

    def _need_procurement(self):
        self.require_table("proc_po", "Procurement (purchase orders and receiving)", PLUGIN)

    def _po(self, po: str) -> str:
        v = str(po or "").strip()
        if not v:
            raise ITAMError("purchase order is required (sys_id or number)", "INVALID_ARGS")
        rows = self.b.query("proc_po", f"sys_id={v}" if is_sys_id(v) else f"number={v}", "sys_id", 1)
        if not rows:
            raise ITAMError(f"No purchase order matches '{v}'", "NOT_FOUND", status=404)
        return rows[0]["sys_id"]

    def list_purchase_orders(self, status: str = "", vendor: str = "", text: str = "", limit: int = 20,
                             include_financials: bool = False) -> dict:
        self._need_procurement()
        parts = []
        if status:
            parts.append(f"status={status}")
        if vendor:
            parts.append(f"vendor={self.resolve('core_company', vendor, label='vendor')}")
        if text:
            parts.append(f"numberLIKE{text}^ORshort_descriptionLIKE{text}")
        q = "^".join(parts)
        fields = S.PO_FIELDS + ("," + S.PO_FINANCIAL_FIELDS if include_financials else "")
        rows = self.view_query("proc_po", q, fields, limit, order_by="-sys_created_on")
        return {"total_matching": self.b.count("proc_po", q), "count": len(rows), "purchase_orders": rows}

    def get_purchase_order(self, po: str, include_financials: bool = False) -> dict:
        self._need_procurement()
        pid = self._po(po)
        fields = S.PO_FIELDS + ("," + S.PO_FINANCIAL_FIELDS if include_financials else "")
        line_fields = S.PO_LINE_FIELDS + ("," + S.PO_LINE_FINANCIAL_FIELDS if include_financials else "")
        return {"purchase_order": self.view("proc_po", pid, fields),
                "lines": self.view_query("proc_po_item", f"purchase_order={pid}", line_fields, 100),
                "receiving_slips": self.view_query("proc_rec_slip", f"purchase_order={pid}",
                                                   "sys_id,number,received,stockroom", 50),
                "link": self.b.link("proc_po", pid)}

    def create_purchase_order(self, vendor: str, ship_to: str, lines: List[Dict[str, Any]],
                              short_description: str = "", requested_for: str = "", due_by: str = "",
                              dry_run: bool = False) -> dict:
        """Creates the order in status Requested (nothing is ordered until order_purchase_order)."""
        self._need_procurement()
        if not lines:
            raise ITAMError("lines is required: [{model, quantity, cost?, short_description?}]", "INVALID_ARGS")
        vid = self.resolve("core_company", vendor, label="vendor")
        room = self.resolve("alm_stockroom", ship_to, label="ship_to stockroom")
        po: Dict[str, Any] = {"vendor": vid, "ship_to": room, "status": "requested",
                              "requested_by": self.me().get("sys_id", "")}
        if short_description:
            po["short_description"] = short_description
        if requested_for:
            po["requested_for"] = self.resolve_user(requested_for, "requested_for")
        if due_by:
            po["due_by"] = check_date("due_by", due_by)
        line_payloads = []
        for i, ln in enumerate(lines, 1):
            if not isinstance(ln, dict) or not ln.get("model"):
                raise ITAMError(f"Line {i} needs a model", "INVALID_ARGS")
            qty = int(ln.get("quantity") or ln.get("ordered_quantity") or 0)
            if qty < 1:
                raise ITAMError(f"Line {i} needs a quantity of at least 1", "INVALID_ARGS")
            lp = {"model": self.resolve("cmdb_model", ln["model"], ("name", "display_name"), label=f"line {i} model"),
                  "ordered_quantity": str(qty), "vendor": vid, "status": "requested"}
            if ln.get("cost") not in (None, ""):
                lp["cost"] = str(float(ln["cost"]))
            if ln.get("short_description"):
                lp["short_description"] = str(ln["short_description"])
            if ln.get("part_number"):
                lp["part_number"] = str(ln["part_number"])
            line_payloads.append(lp)
        res = self.write(area="procurement", action="create purchase order", table="proc_po", payload=po,
                         dry_run=dry_run, context={"lines": line_payloads}, verify_fields="number")
        if dry_run or not res.get("success"):
            return res
        made = []
        for lp in line_payloads:
            r = self.write(area="procurement", action="create purchase order line", table="proc_po_item",
                           payload={**lp, "purchase_order": res["sys_id"]})
            made.append({k: r.get(k) for k in ("sys_id", "verified", "mismatches") if k in r})
        res["lines"] = made
        res["number"] = (self.b.get("proc_po", res["sys_id"], "number") or {}).get("number")
        res["next_steps"] = "The order is Requested. Use order_purchase_order to place it."
        return res

    def order_purchase_order(self, po: str, dry_run: bool = False, confirm: bool = False) -> dict:
        self._need_procurement()
        pid = self._po(po)
        before = self.b.get("proc_po", pid, "sys_id,number,status")
        if before.get("status") != "requested":
            raise ITAMError(f"Only Requested orders can be ordered (this one is {before.get('status')})",
                            "INVALID_ARGS")
        if not self.b.query("proc_po_item", f"purchase_order={pid}^status!=canceled", "sys_id", 1):
            raise ITAMError("The order has no active lines", "INVALID_ARGS")
        dictionary = self.b.query("sys_dictionary", "name=proc_po^element=status", "read_only", 1)
        if dictionary and str(dictionary[0].get("read_only")).lower() == "true":
            # Verified live: the API silently ignores status writes; only the server-side Order UI action sets it.
            self.require_roles("procurement", "place purchase order")
            return {"success": False, "action_required": True, "purchase_order": before.get("number"),
                    "link": self.b.link("proc_po", pid),
                    "message": "On this instance the purchase order status is read-only to the API. Open the "
                               "purchase order in ServiceNow and click Order; then receive it with "
                               "receive_purchase_order_item."}
        return self.write(area="procurement", action="place purchase order", table="proc_po", sys_id=pid,
                          before=before, payload={"status": "ordered"}, dry_run=dry_run, confirm=confirm,
                          reasons=["places the order with the vendor (status Ordered)"], critical=("status",),
                          context={"purchase_order": before.get("number")})

    def receive_purchase_order_item(self, po_line: str, quantity: int, stockroom: str = "", dry_run: bool = False,
                                    confirm: bool = False) -> dict:
        self._need_procurement()
        v = str(po_line or "").strip()
        rows = self.b.query("proc_po_item", f"sys_id={v}" if is_sys_id(v) else f"number={v}",
                            "sys_id,number,purchase_order,model,status,ordered_quantity,received_quantity,"
                            "remaining_quantity", 1)
        if not rows:
            raise ITAMError(f"No purchase order line matches '{v}'", "NOT_FOUND", status=404)
        line = rows[0]
        if line.get("status") not in ("ordered", "pending"):
            raise ITAMError(f"Only ordered lines can be received (this line is {line.get('status')})",
                            "INVALID_ARGS", "Place the order first with order_purchase_order")
        qty = int(quantity)
        remaining = int(float(line.get("remaining_quantity") or 0))
        if qty < 1 or qty > remaining:
            raise ITAMError(f"quantity must be between 1 and the remaining {remaining}", "INVALID_ARGS")
        pid = raw(line.get("purchase_order"))
        if not pid:
            raise ITAMError(f"Purchase order line {line.get('number')} is not attached to a purchase order",
                            "INVALID_ARGS")
        po = self.b.get("proc_po", pid, "number,ship_to")
        room = self.resolve("alm_stockroom", stockroom, label="stockroom") if stockroom else raw(po.get("ship_to"))
        model = raw(line.get("model"))
        context = {"purchase_order": po.get("number"), "line": line.get("number"), "quantity": qty,
                   "remaining_before": remaining}
        reasons = [f"receives {qty} unit(s) into stock — ServiceNow creates the asset records"]
        self.require_roles("procurement", "receive purchase order item")
        if dry_run:
            return {"dry_run": True, "action": "receive purchase order item", **context,
                    "requires_confirmation": True, "reasons": reasons}
        if not confirm:
            return {"success": False, "requires_confirmation": True, "reasons": reasons,
                    "action": "receive purchase order item", **context,
                    "message": "This change is consequential — review it and call again with confirm=true"}
        before_assets = self.b.count("alm_asset", f"model={model}^stockroom={room}")
        slip = self.write(area="procurement", action="create receiving slip", table="proc_rec_slip",
                          payload={"purchase_order": pid, "stockroom": room, "received": now_utc()})
        item = self.write(area="procurement", action="create receiving slip line", table="proc_rec_slip_item",
                          payload={"receiving_slip": slip["sys_id"], "purchase_line": line["sys_id"],
                                   "quantity": str(qty), "received": now_utc(),
                                   "received_by": self.me().get("sys_id", "")})
        after_line, after_assets = {}, before_assets
        for _ in range(6):   # the platform's rules run synchronously; allow a short settle for async rollups
            after_line = self.b.get("proc_po_item", line["sys_id"], "received_quantity,remaining_quantity,status")
            after_assets = self.b.count("alm_asset", f"model={model}^stockroom={room}")
            if after_assets > before_assets:
                break
            time.sleep(1)
        received_delta = int(float(after_line.get("received_quantity") or 0)) - int(float(line.get("received_quantity") or 0))
        out = {"success": True, "action": "receive purchase order item", **context,
               "receiving_slip": slip.get("sys_id"), "receiving_slip_line": item.get("sys_id"),
               "line_after": after_line, "assets_in_stockroom_before": before_assets,
               "assets_in_stockroom_after": after_assets,
               "verified": received_delta == qty}
        if after_assets <= before_assets:
            out["note"] = ("No new asset records were seen in the stockroom yet; consumable and software models may "
                           "update quantity/rights on existing records instead")
        return out
