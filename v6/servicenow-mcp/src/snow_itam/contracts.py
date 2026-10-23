"""Contracts (ast_contract), renewals and contract-asset links. Commercial terms only on request."""

from typing import Any, Dict, Optional

from snow_itam import schema as S
from snow_itam.core import ITAMCore, check_date, days_from_today, is_sys_id, raw, today
from snow_itam.errors import ITAMError


class ContractsMixin(ITAMCore):

    def _contract(self, contract: str) -> str:
        v = str(contract or "").strip()
        if not v:
            raise ITAMError("contract is required (sys_id, number or vendor contract number)", "INVALID_ARGS")
        q = f"sys_id={v}" if is_sys_id(v) else f"number={v}^ORvendor_contract={v}"
        rows = self.b.query("ast_contract", q, "sys_id,number", 3)
        if not rows:
            raise ITAMError(f"No contract matches '{v}'", "NOT_FOUND", status=404)
        if len(rows) > 1:
            raise ITAMError(f"'{v}' matches {len(rows)} contracts — pass the sys_id or number", "AMBIGUOUS",
                            ", ".join(r.get("number", r["sys_id"]) for r in rows))
        return rows[0]["sys_id"]

    def _fields(self, include_financials: bool) -> str:
        return S.CONTRACT_FIELDS + ("," + S.CONTRACT_COMMERCIAL_FIELDS if include_financials else "")

    def search_contracts(self, text: str = "", vendor: str = "", state: str = "",
                         expiring_within_days: Optional[int] = None, limit: int = 20,
                         include_financials: bool = False) -> dict:
        self.require_table("ast_contract", "Contract management", "com.snc.contract_management")
        parts = []
        if text:
            parts.append(f"numberLIKE{text}^ORshort_descriptionLIKE{text}^ORvendor_contractLIKE{text}")
        if vendor:
            parts.append(f"vendor={self.resolve('core_company', vendor, label='vendor')}")
        if state:
            allowed = [c["value"] for c in self.choices("ast_contract", "state")]
            if state not in allowed:
                raise ITAMError(f"state must be one of {allowed}", "INVALID_ARGS")
            parts.append(f"state={state}")
        if expiring_within_days is not None:
            parts.append(f"ends>={today()}^ends<={days_from_today(int(expiring_within_days))}")
            if not state:
                parts.append("state=active")
        q = "^".join(parts)
        rows = self.view_query("ast_contract", q, self._fields(include_financials), limit,
                               order_by="ends" if expiring_within_days is not None else "-sys_updated_on")
        return {"total_matching": self.b.count("ast_contract", q), "count": len(rows), "contracts": rows}

    def get_contract(self, contract: str, include_assets: bool = True, include_financials: bool = False,
                     asset_limit: int = 25) -> dict:
        cid = self._contract(contract)
        out: Dict[str, Any] = {"contract": self.view("ast_contract", cid, self._fields(include_financials)),
                               "link": self.b.link("ast_contract", cid)}
        if include_assets and self.table_exists("clm_m2m_contract_asset"):
            out["assets_total"] = self.b.count("clm_m2m_contract_asset", f"contract={cid}")
            out["assets"] = self.view_query("clm_m2m_contract_asset", f"contract={cid}", "sys_id,asset,added,removed",
                                            asset_limit)
        return out

    def create_contract(self, short_description: str, vendor: str, contract_model: str, vendor_contract: str,
                        starts: str, ends: str, contract_administrator: str = "", approver: str = "",
                        renewal_date: str = "", description: str = "", dry_run: bool = False) -> dict:
        self.require_table("ast_contract", "Contract management", "com.snc.contract_management")
        if not short_description.strip() or not vendor_contract.strip():
            raise ITAMError("short_description and vendor_contract are required", "INVALID_ARGS")
        vid = self.resolve("core_company", vendor, label="vendor")
        mid = self.resolve("cmdb_model", contract_model, ("name", "display_name"), label="contract model",
                           extra_query="sys_class_name=cmdb_contract_product_model")
        s, e = check_date("starts", starts), check_date("ends", ends)
        if not s or not e:
            raise ITAMError("starts and ends are required (YYYY-MM-DD)", "INVALID_ARGS")
        if e < s:
            raise ITAMError("ends is before starts", "INVALID_ARGS")
        if self.b.query("ast_contract", f"vendor={vid}^vendor_contract={vendor_contract.strip()}", "sys_id", 1):
            raise ITAMError(f"Vendor contract '{vendor_contract}' already exists for this vendor", "DUPLICATE",
                            status=409)
        payload: Dict[str, Any] = {"short_description": short_description.strip(), "vendor": vid,
                                   "contract_model": mid, "vendor_contract": vendor_contract.strip(),
                                   "starts": s, "ends": e, "state": "draft"}
        if contract_administrator:
            payload["contract_administrator"] = self.resolve_user(contract_administrator, "contract_administrator")
        if approver:
            payload["approver"] = self.resolve_user(approver, "approver")
        if renewal_date:
            payload["renewal_date"] = check_date("renewal_date", renewal_date)
        if description:
            payload["description"] = description
        return self.write(area="contract", action="create contract", table="ast_contract", payload=payload,
                          dry_run=dry_run, verify_fields="number")

    def update_contract(self, contract: str, fields: Dict[str, Any], dry_run: bool = False,
                        confirm: bool = False) -> dict:
        if not fields:
            raise ITAMError("fields is required", "INVALID_ARGS")
        cid = self._contract(contract)
        payload = self.resolve_refs(S.CONTRACT_UPDATABLE, fields, "update_contract")
        for k in ("state", "substate"):
            if k in payload:
                allowed = {c["value"] for c in self.choices("ast_contract", k)}
                if payload[k] and payload[k] not in allowed:
                    raise ITAMError(f"{k} must be one of {sorted(allowed)}", "INVALID_ARGS")
        before = self.b.get("ast_contract", cid, ",".join(["sys_id", "number", *payload.keys()]))
        s = payload.get("starts") or raw(before.get("starts"))[:10]
        e = payload.get("ends") or raw(before.get("ends"))[:10]
        if s and e and e < s:
            raise ITAMError("ends would be before starts", "INVALID_ARGS")
        reasons = [f"changes {k}" for k in payload if k in S.CONTRACT_CONSEQUENTIAL and
                   raw(before.get(k)) != str(payload[k])]
        return self.write(area="contract", action="update contract", table="ast_contract", sys_id=cid, before=before,
                          payload=payload, dry_run=dry_run, confirm=confirm, reasons=reasons,
                          context={"contract": before.get("number")})

    def link_asset_to_contract(self, contract: str, asset: str, dry_run: bool = False) -> dict:
        cid = self._contract(contract)
        aid = self.find_asset(asset)["sys_id"]
        if self.b.query("clm_m2m_contract_asset", f"contract={cid}^asset={aid}", "sys_id", 1):
            raise ITAMError("The asset is already linked to this contract", "DUPLICATE", status=409)
        return self.write(area="contract", action="link asset to contract", table="clm_m2m_contract_asset",
                          payload={"contract": cid, "asset": aid}, dry_run=dry_run)

    def unlink_asset_from_contract(self, contract: str, asset: str, dry_run: bool = False,
                                   confirm: bool = False) -> dict:
        cid = self._contract(contract)
        aid = self.find_asset(asset)["sys_id"]
        rows = self.b.query("clm_m2m_contract_asset", f"contract={cid}^asset={aid}", "sys_id", 1)
        if not rows:
            raise ITAMError("The asset is not linked to this contract", "NOT_FOUND", status=404)
        self.require_roles("contract", "unlink asset from contract")
        preview = {"action": "unlink asset from contract", "table": "clm_m2m_contract_asset", "sys_id": rows[0]["sys_id"]}
        if dry_run:
            return {"dry_run": True, **preview, "requires_confirmation": True}
        if not confirm:
            return {"success": False, "requires_confirmation": True, **preview,
                    "reasons": ["removes the asset from the contract's coverage"],
                    "message": "This change is consequential — review it and call again with confirm=true"}
        self.b.delete("clm_m2m_contract_asset", rows[0]["sys_id"])
        gone = not self.b.query("clm_m2m_contract_asset", f"sys_id={rows[0]['sys_id']}", "sys_id", 1)
        return {"success": gone, **preview, "verified": gone}
