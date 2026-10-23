"""Software license assets (alm_license) and their allocations (alm_entitlement). No compliance conclusions."""

from typing import Any, Dict, Optional

from snow_itam import schema as S
from snow_itam.core import ITAMCore, check_date, is_sys_id, raw
from snow_itam.errors import ITAMError

LICENSE_VIEW = ("sys_id,display_name,asset_tag,model,model_category,rights,install_status,substatus,vendor,"
                "company,purchase_date,po_number,assigned_condition,entitlement_condition,sys_updated_on")


class LicensesMixin(ITAMCore):

    def _license(self, license: str) -> str:
        v = str(license or "").strip()
        if not v:
            raise ITAMError("license is required (sys_id, asset tag or display name)", "INVALID_ARGS")
        q = f"sys_id={v}" if is_sys_id(v) else f"asset_tag={v}^ORdisplay_name={v}"
        rows = self.b.query("alm_license", q, "sys_id,display_name", 3)
        if not rows:
            raise ITAMError(f"No software license matches '{v}'", "NOT_FOUND", status=404)
        if len(rows) > 1:
            raise ITAMError(f"'{v}' matches {len(rows)} licenses — pass the sys_id", "AMBIGUOUS",
                            ", ".join(r["sys_id"] for r in rows))
        return rows[0]["sys_id"]

    def _allocated(self, license_id: str) -> int:
        return self.b.count("alm_entitlement", f"licensed_by={license_id}")

    def search_licenses(self, text: str = "", model: str = "", vendor: str = "", limit: int = 20,
                        include_allocation_counts: bool = True) -> dict:
        parts = []
        if text:
            parts.append(f"display_nameLIKE{text}^ORasset_tagLIKE{text}")
        if model:
            parts.append(f"model={self.resolve('cmdb_software_product_model', model, ('name', 'display_name'), label='software model')}")
        if vendor:
            parts.append(f"vendor={self.resolve('core_company', vendor, label='vendor')}")
        q = "^".join(parts)
        rows = self.view_query("alm_license", q, LICENSE_VIEW, limit, order_by="display_name")
        if include_allocation_counts:
            for r in rows:
                r["allocated"] = self._allocated(r["sys_id"])
        return {"total_matching": self.b.count("alm_license", q), "count": len(rows), "licenses": rows,
                "note": S.NOT_A_COMPLIANCE_POSITION}

    def get_license(self, license: str, include_license_key: bool = False, sample_size: int = 10) -> dict:
        lid = self._license(license)
        rec = self.view("alm_license", lid, LICENSE_VIEW + ("," + S.LICENSE_SECRET_FIELDS if include_license_key else ""))
        users = self.b.count("alm_entitlement", f"licensed_by={lid}^assigned_toISNOTEMPTY")
        cis = self.b.count("alm_entitlement", f"licensed_by={lid}^allocated_toISNOTEMPTY")
        rights = int(float(raw(rec.get("rights")) or 0))
        sample = self.view_query("alm_entitlement", f"licensed_by={lid}", "sys_id,assigned_to,allocated_to,sys_class_name",
                                 sample_size)
        return {"license": rec, "rights": rights, "allocated_to_users": users, "allocated_to_cis": cis,
                "allocated_total": users + cis, "unallocated_rights": rights - users - cis,
                "allocation_sample": sample, "note": S.NOT_A_COMPLIANCE_POSITION}

    def create_license(self, model: str, rights: int, vendor: str = "",
                       license_key: str = "", purchase_date: str = "", po_number: str = "",
                       dry_run: bool = False) -> dict:
        mid = self.resolve("cmdb_software_product_model", model, ("name", "display_name"), label="software model")
        m = self.b.get("cmdb_model", mid, "name,cmdb_model_category")
        cat = ""
        for c in [x for x in raw(m.get("cmdb_model_category")).split(",") if x]:
            if (self.b.get("cmdb_model_category", c, "asset_class") or {}).get("asset_class") == "alm_license":
                cat = c
                break
        if not cat:
            cats = self.b.query("cmdb_model_category", "asset_class=alm_license", "sys_id", 2)
            cat = cats[0]["sys_id"] if len(cats) == 1 else ""
        if not cat:
            raise ITAMError("No model category for software licenses was found", "INVALID_ARGS",
                            "Set a model category with asset class alm_license on the software model")
        if int(rights) < 0:
            raise ITAMError("rights cannot be negative", "INVALID_ARGS")
        if license_key and self.b.query("alm_license", f"license_key={license_key}", "sys_id", 1):
            raise ITAMError("A license with this license key already exists", "DUPLICATE", status=409)
        payload: Dict[str, Any] = {"model": mid, "model_category": cat, "rights": str(int(rights)), "quantity": "1"}
        if vendor:
            payload["vendor"] = self.resolve("core_company", vendor, label="vendor")
        if license_key:
            payload["license_key"] = license_key
        if purchase_date:
            payload["purchase_date"] = check_date("purchase_date", purchase_date)
        if po_number:
            payload["po_number"] = po_number
        res = self.write(area="license", action="create license", table="alm_license", payload=payload,
                         dry_run=dry_run, context={"model": m.get("name")})
        if license_key:  # never echo product keys
            if "payload" in res:
                res["payload"] = {**res["payload"], "license_key": "(hidden)"}
            if "license_key" in (res.get("mismatches") or {}):
                res["mismatches"]["license_key"] = {"expected": "(hidden)", "actual": "(hidden)"}
        return res

    def list_license_allocations(self, license: str, limit: int = 25) -> dict:
        lid = self._license(license)
        rows = self.view_query("alm_entitlement", f"licensed_by={lid}",
                               "sys_id,sys_class_name,assigned_to,allocated_to,sys_created_on", limit,
                               order_by="-sys_created_on")
        return {"license": lid, "total": self._allocated(lid), "count": len(rows), "allocations": rows}

    def allocate_license(self, license: str, user: str = "", ci: str = "", dry_run: bool = False,
                         confirm: bool = False) -> dict:
        if bool(user) == bool(ci):
            raise ITAMError("Give exactly one of user or ci", "INVALID_ARGS")
        lid = self._license(license)
        lic = self.b.get("alm_license", lid, "rights,display_name,install_status")
        if user:
            uid = self.resolve_user(user, "user")
            table, payload, dup_q = "alm_entitlement_user", {"licensed_by": lid, "assigned_to": uid}, f"assigned_to={uid}"
        else:
            cid = self.resolve("cmdb_ci", ci, ("name", "serial_number"), label="CI")
            table, payload, dup_q = "alm_entitlement_asset", {"licensed_by": lid, "allocated_to": cid}, f"allocated_to={cid}"
        if self.b.query("alm_entitlement", f"licensed_by={lid}^{dup_q}", "sys_id", 1):
            raise ITAMError("This license is already allocated to that target", "DUPLICATE", status=409)
        rights = int(float(lic.get("rights") or 0))
        used = self._allocated(lid)
        reasons = [f"allocation {used + 1} exceeds the {rights} right(s) recorded on the license"] if used >= rights else []
        return self.write(area="license", action="allocate license", table=table, payload=payload, dry_run=dry_run,
                          confirm=confirm, reasons=reasons,
                          context={"license": lic.get("display_name"), "rights": rights, "allocated_before": used})

    def deallocate_license(self, license: str = "", user: str = "", ci: str = "", allocation_sys_id: str = "",
                           dry_run: bool = False, confirm: bool = False) -> dict:
        if allocation_sys_id:
            if not is_sys_id(allocation_sys_id):
                raise ITAMError("allocation_sys_id must be a sys_id", "INVALID_ARGS")
            row = self.b.get("alm_entitlement", allocation_sys_id, "sys_id,licensed_by,sys_class_name")
        else:
            if bool(user) == bool(ci):
                raise ITAMError("Give allocation_sys_id, or license plus exactly one of user / ci", "INVALID_ARGS")
            lid = self._license(license)
            q = (f"assigned_to={self.resolve_user(user, 'user')}" if user
                 else f"allocated_to={self.resolve('cmdb_ci', ci, ('name', 'serial_number'), label='CI')}")
            rows = self.b.query("alm_entitlement", f"licensed_by={lid}^{q}", "sys_id,licensed_by,sys_class_name", 2)
            row = rows[0] if rows else {}
        if not row.get("sys_id"):
            raise ITAMError("No matching allocation", "NOT_FOUND", status=404)
        self.require_roles("license", "deallocate license")
        preview = {"action": "deallocate license", "table": "alm_entitlement", "sys_id": row["sys_id"]}
        if dry_run:
            return {"dry_run": True, **preview, "requires_confirmation": True}
        if not confirm:
            return {"success": False, "requires_confirmation": True, **preview,
                    "reasons": ["removes a license allocation"],
                    "message": "This change is consequential — review it and call again with confirm=true"}
        self.b.delete(row.get("sys_class_name") or "alm_entitlement", row["sys_id"])
        gone = not self.b.query("alm_entitlement", f"sys_id={row['sys_id']}", "sys_id", 1)
        return {"success": gone, **preview, "verified": gone,
                **({} if gone else {"error": "The allocation still exists after the delete"})}

    def license_allocation_summary(self, license: str = "", limit: int = 25) -> dict:
        if license:
            ids = [self._license(license)]
        else:
            ids = [r["sys_id"] for r in self.b.query("alm_license", "", "sys_id", max(1, min(limit, 200)),
                                                       order_by="display_name")]
        counts = {}
        for g in self.b.aggregate("alm_entitlement", "licensed_byIN" + ",".join(ids), group_by=("licensed_by",)):
            counts[g["group"].get("licensed_by")] = g["count"]
        out = []
        for lid in ids:
            lic = self.b.get("alm_license", lid, "display_name,rights")
            rights = int(float(lic.get("rights") or 0))
            n = counts.get(lid, 0)
            out.append({"license": lic.get("display_name"), "sys_id": lid, "rights": rights, "allocated": n,
                        "unallocated_rights": rights - n, "allocations_exceed_rights": n > rights})
        return {"count": len(out), "licenses": out,
                "allocations_exceed_rights_count": sum(1 for x in out if x["allocations_exceed_rights"]),
                "note": S.NOT_A_COMPLIANCE_POSITION}
