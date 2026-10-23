"""
CMDB configuration items: search, get, create (duplicate checks, dry run, read-back), update (diff, read-back).

Moved here from servicenow-mcp's tools/cmdb.py so servicenow-mcp and the Snow Agent run the same code (same tool
names, arguments and results). Field names are checked against the class's dictionary (own and inherited fields) and
reference fields accept a display value (e.g. location 'Pune'), so the tools follow whatever the instance defines.
"""

from typing import Any, Dict, List, Optional

from snow_itam.core import USER_LOOKUP, is_sys_id
from snow_itam.errors import ITAMError
from snow_itom.core import ITOMCore
from snow_itom.errors import ITOMError

HARDWARE_WARNING = ("Hardware CI: ServiceNow's asset-CI synchronization will create a linked asset record "
                    "(use create_asset instead when you are registering a physical device)")


def compute_diff(before: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Fields whose stored value differs from the intended one (same rule as servicenow_mcp.tools.core)."""
    diff = {}
    for key, new_val in fields.items():
        old_val = before.get(key)
        if isinstance(old_val, dict) and "value" in old_val:
            old_val = old_val["value"]
        if str(old_val or "") != str(new_val or ""):
            diff[key] = {"from": old_val, "to": new_val}
    return diff


class CmdbMixin(ITOMCore):

    def _extends(self, table: str, ancestor: str) -> bool:
        return ancestor in self.table_chain(table)

    def _ci_fields(self, table: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Check field names against the class dictionary and turn reference display values into sys_ids.
        When the dictionary cannot be read (no rows / no access), values pass as given and ServiceNow decides."""
        if not fields:
            return {}
        chain = self.table_chain(table)
        try:
            known = self.b.query("sys_dictionary", f"nameIN{','.join(chain)}", "element", 1)
            rows = self.b.query("sys_dictionary", f"nameIN{','.join(chain)}^elementIN{','.join(fields)}",
                                "element,internal_type,reference", 500) if known else []
        except ITAMError as e:
            if e.status != 403:
                raise
            known, rows = [], []
        if not known:
            return dict(fields)
        info = {r.get("element"): r for r in rows}
        unknown = sorted(set(fields) - set(info) - {"work_notes", "comments"})
        if unknown:
            raise ITOMError(f"Field(s) {unknown} do not exist on {table}", "INVALID_ARGS",
                            "Check the field names (get_table_schema) — ServiceNow would silently ignore them")
        out = {}
        for k, v in fields.items():
            d = info.get(k) or {}
            ref = d.get("reference") or ""
            if d.get("internal_type") == "reference" and ref and isinstance(v, str) and v.strip() and not is_sys_id(v):
                lookup = USER_LOOKUP if ref == "sys_user" else ("name",)
                out[k] = self.resolve(ref, v, lookup, label=k)
            else:
                out[k] = v
        return out

    def search_cmdb_ci(self, name: str = "", sys_class_name: str = "cmdb_ci", ip_address: str = "",
                       operational_status: str = "", query: str = "", limit: int = 15) -> dict:
        parts = []
        if name:
            parts.append(f"nameLIKE{name}")
        if ip_address:
            parts.append(f"ip_address={ip_address}")
        if operational_status:
            parts.append(f"operational_status={operational_status}")
        if query:
            parts.append(query)
        fields = "sys_id,name,sys_class_name,ip_address,operational_status,install_status,serial_number,asset_tag"
        cis = self.b.query(sys_class_name or "cmdb_ci", "^".join(parts), fields, limit, display_value="true")
        return {"count": len(cis), "cis": cis}

    def get_cmdb_ci(self, ci_sys_id: str, table: str = "cmdb_ci") -> dict:
        return {"ci": self.b.get(table or "cmdb_ci", ci_sys_id, "", display_value="true")}

    def _ci_class(self, value: str) -> str:
        """Class table name, or its label as people say it ('Linux Server' -> cmdb_ci_linux_server)."""
        v = (value or "").strip()
        if v and self.b.query("sys_db_object", f"name={v}", "sys_id", 1):
            return v
        if v:
            classes = {c["name"] for c in self.class_tree("cmdb_ci")}
            hits = [r["name"] for r in self.b.query("sys_db_object", f"label={v}", "name", 10) if r["name"] in classes]
            if len(hits) == 1:
                self.matched.append(f"class '{v}' matched {hits[0]}")
                return hits[0]
            if len(hits) > 1:
                raise ITOMError(f"'{v}' matches several CI classes — say which one", "AMBIGUOUS",
                                f"Candidates: {', '.join(hits)}")
        raise ITOMError(f"CI class '{v}' does not exist", "INVALID_ARGS",
                        "Use list_ci_classes to find the class, e.g. cmdb_ci_linux_server")

    def create_cmdb_ci(self, name: str, sys_class_name: str, fields: Optional[Dict[str, Any]] = None,
                       dry_run: bool = False) -> dict:
        sys_class_name = self._ci_class(sys_class_name)
        payload = self._ci_fields(sys_class_name, dict(fields or {}))
        payload["name"] = name
        dup = self.b.query(sys_class_name, f"name={name}", "sys_id", 1)
        if dup:
            raise ITOMError(f"A {sys_class_name} CI named '{name}' already exists", "DUPLICATE",
                            f"sys_id {dup[0]['sys_id']}", 409)
        serial = str(payload.get("serial_number") or "").strip()
        if serial:
            dup = self.b.query("cmdb_ci", f"serial_number={serial}", "sys_id,name", 1)
            if dup:
                raise ITOMError(f"A CI with serial '{serial}' already exists ({dup[0].get('name')})", "DUPLICATE",
                                f"sys_id {dup[0]['sys_id']}", 409)
        warnings = [HARDWARE_WARNING] if self._extends(sys_class_name, "cmdb_ci_hardware") else []
        if dry_run:
            return {"dry_run": True, "class": sys_class_name, "payload": payload, "warnings": warnings}
        res = self.b.create(sys_class_name, payload)
        sys_id = res.get("sys_id")
        check = self.b.get(sys_class_name, sys_id, "sys_id,name,asset") if sys_id else {}
        return {"success": True, "sys_id": sys_id, "name": name, "class": sys_class_name,
                "verified": bool(check) and check.get("name") == name,
                "asset": check.get("asset") if check else None, "warnings": warnings,
                "link": self.b.link(sys_class_name, sys_id), "record": res}

    def update_cmdb_ci(self, ci_sys_id: str, fields: Dict[str, Any], table: str = "cmdb_ci",
                       dry_run: bool = False) -> dict:
        if not fields:
            raise ITOMError("fields is required", "INVALID_ARGS")
        table = table or "cmdb_ci"
        fields = self._ci_fields(table, dict(fields))
        keys = ",".join(["sys_id", *fields.keys()])
        before = self.b.get(table, ci_sys_id, keys)
        diff = compute_diff(before, fields)
        if dry_run:
            return {"dry_run": True, "sys_id": ci_sys_id, "diff": diff}
        if not diff:
            return {"success": True, "sys_id": ci_sys_id, "message": "No changes; CI already matches."}
        res = self.b.update(table, ci_sys_id, fields)
        after = self.b.get(table, ci_sys_id, keys)
        remaining = {k: v for k, v in compute_diff(after, fields).items() if k not in ("work_notes", "comments")}
        return {"success": True, "sys_id": ci_sys_id, "changed_fields": sorted(diff), "verified": not remaining,
                **({"mismatches": remaining} if remaining else {}), "record": res}
