"""
In-memory stand-in for servicenow_mcp.client.ServiceNowClient, for ITAM tests. NOT a real instance.

Supports: table inheritance (querying alm_asset returns alm_hardware rows), encoded queries with
=, !=, ^OR, IN, NOT IN, ISEMPTY, ISNOTEMPTY, LIKE, STARTSWITH, >=, <=, dot-walked fields (ci.serial_number),
ORDERBY, display_value=all, and the stats API (count, group_by, sum).
"""

import re
import uuid
from typing import Any, Dict, List

PARENTS = {
    "alm_hardware": "alm_asset", "alm_consumable": "alm_asset", "alm_license": "alm_asset", "alm_facility": "alm_asset",
    "cmdb_hardware_product_model": "cmdb_model", "cmdb_consumable_product_model": "cmdb_model",
    "cmdb_software_product_model": "cmdb_model", "cmdb_contract_product_model": "cmdb_model",
    "alm_entitlement_user": "alm_entitlement", "alm_entitlement_asset": "alm_entitlement",
    "cmdb_ci_hardware": "cmdb_ci", "cmdb_ci_computer": "cmdb_ci_hardware", "cmdb_ci_appl": "cmdb_ci",
    "cmdb_ci_server": "cmdb_ci_computer", "cmdb_ci_linux_server": "cmdb_ci_server", "cmdb_ci_database": "cmdb_ci",
    "cmdb_ci_service": "cmdb_ci", "cmdb_ci_service_business": "cmdb_ci_service",
    "cmdb_ci_service_auto": "cmdb_ci_service", "cmdb_ci_service_discovered": "cmdb_ci_service_auto",
}


class FakeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status
        self.message = message
        self.code = f"HTTP_{status}"
        self.detail = ""


def _chain(t):
    out = [t]
    while t in PARENTS:
        t = PARENTS[t]
        out.append(t)
    return out


class FakeSN:
    instance_url = "https://fake.service-now.com"

    def __init__(self, username="admin"):
        self.username = username
        self.rows: List[Dict[str, Any]] = []      # every row has sys_class_name = its own table
        self.writes: List[tuple] = []
        self.deny_tables = set()
        self.read_only = set()

    # ── data helpers ───────────────────────────────────────────────────────
    def add(self, table, **fields):
        row = {"sys_id": fields.pop("sys_id", uuid.uuid4().hex), "sys_class_name": table, **fields}
        self.rows.append(row)
        return row

    def by_id(self, sys_id):
        return next((r for r in self.rows if r["sys_id"] == sys_id), None)

    def table(self, table):
        return [r for r in self.rows if table in _chain(r["sys_class_name"])]

    def _field(self, row, name):
        cur = row
        for part in name.split("."):
            if cur is None:
                return ""
            v = cur.get(part, "")
            if part != name.split(".")[-1]:
                cur = self.by_id(str(v)) if v else None
            else:
                return "" if v is None else str(v)
        return ""

    def _cond(self, row, c):
        for op in ("NOT IN", "ISNOTEMPTY", "ISEMPTY", "STARTSWITH", "LIKE", "IN", ">=", "<=", "!=", "="):
            if op in c:
                f, _, v = c.partition(op)
                val = self._field(row, f)
                if op == "ISEMPTY":
                    return val == ""
                if op == "ISNOTEMPTY":
                    return val != ""
                if op == "LIKE":
                    return v.lower() in val.lower()
                if op == "STARTSWITH":
                    return val.startswith(v)
                if op == "IN":
                    return val in v.split(",")
                if op == "NOT IN":
                    return val not in v.split(",")
                if op == ">=":
                    return val != "" and val >= v
                if op == "<=":
                    return val != "" and val <= v
                if op == "!=":
                    return val != v
                return val == v
        return True

    def _match(self, row, query):
        clauses = []
        for part in (query or "").split("^"):
            if not part or part.startswith("ORDERBY"):
                continue
            if part.startswith("OR") and clauses:
                clauses[-1].append(part[2:])
            else:
                clauses.append([part])
        return all(any(self._cond(row, alt) for alt in cl) for cl in clauses)

    def _select(self, table, query):
        if table in self.deny_tables:
            raise FakeError(403, "User Not Authorized")
        rows = [r for r in self.table(table) if self._match(r, query)]
        m = re.search(r"ORDERBY(DESC)?([a-z_.]+)", query or "")
        if m:
            rows.sort(key=lambda r: self._field(r, m.group(2)), reverse=bool(m.group(1)))
        return rows

    def _out(self, row, fields, display_value):
        keys = [f.strip() for f in fields.split(",")] if fields else list(row)
        out = {}
        for k in keys:
            v = self._field(row, k)
            if display_value == "all":
                ref = self.by_id(v) if re.fullmatch(r"[0-9a-f]{32}", v or "") else None
                disp = (ref.get("name") or ref.get("display_name") or ref.get("user_name")) if ref else v
                out[k] = {"value": v, "display_value": disp or v}
            else:
                out[k] = v
        return out

    # ── ServiceNowClient interface ─────────────────────────────────────────
    def query_records(self, table, query="", fields=None, limit=20, offset=0, display_value="false", order_by=""):
        if order_by:
            query = f"{query}^ORDERBY{'DESC' + order_by[1:] if order_by.startswith('-') else order_by}"
        rows = self._select(table, query)[offset:offset + limit]
        return [self._out(r, fields or "", display_value) for r in rows]

    def get_record(self, table, sys_id, fields=None, display_value="false"):
        row = self.by_id(sys_id)
        if not row or table not in _chain(row["sys_class_name"]):
            raise FakeError(404, "No Record found")
        return self._out(row, fields or "", display_value)

    def create_record(self, table, fields):
        self.writes.append(("create", table, dict(fields)))
        row = self.add(table, **{k: v for k, v in fields.items() if k not in ("work_notes",)})
        return dict(row)

    def update_record(self, table, sys_id, fields):
        row = self.by_id(sys_id)
        if not row:
            raise FakeError(404, "No Record found")
        self.writes.append(("update", table, sys_id, dict(fields)))
        # read_only: fields the "instance" silently ignores on update (like a business rule or read-only column)
        row.update({k: v for k, v in fields.items() if k not in ("work_notes",) and k not in self.read_only})
        return dict(row)

    def delete_record(self, table, sys_id):
        self.writes.append(("delete", table, sys_id))
        self.rows = [r for r in self.rows if r["sys_id"] != sys_id]
        return {"status": "success"}

    def _request(self, method, path, params=None, json_data=None):
        """Scripted REST APIs. Emulates the Application Service API as read from the instance's
        CreateOrUpdateITService (upsert by name into cmdb_ci_service_discovered) — membership rows are an assumption
        of this fake, checked on a live instance separately."""
        if method == "POST" and path == "/api/now/cmdb/app_service/create":
            self.writes.append(("api", path, dict(json_data or {})))
            body = dict(json_data or {})
            rels = body.pop("service_relations", []) or []
            row = next((r for r in self.table("cmdb_ci_service_discovered") if r.get("name") == body.get("name")), None)
            if row:
                row.update(body)
            else:
                row = self.add("cmdb_ci_service_discovered", type="1", **body)
            row["_relations"] = [dict(r) for r in rels]
            for rel in rels:
                self.add("svc_ci_assoc", service_id=row["sys_id"], ci_id=rel["child"])
            return {"result": {"url": f"/api/now/table/cmdb_ci_service_discovered/{row['sys_id']}",
                               "getContentUrl": f"/api/now/cmdb/app_service/{row['sys_id']}/getContent",
                               "info": f"{len({r['child'] for r in rels})} CIs added to business service"}}
        m = re.fullmatch(r"/api/now/cmdb/app_service/([0-9a-f]{32})/getContent", path)
        if method == "GET" and m:
            row = self.by_id(m.group(1))
            if not row or "cmdb_ci_service_discovered" not in _chain(row["sys_class_name"]):
                raise FakeError(400, f"Failed to get service content: Business service {m.group(1)} not found")
            # shape returned by a live instance (no "result" wrapper: the API streams the body)
            return {"service": {"name": row.get("name"),
                                "url": f"/api/now/table/cmdb_ci_service_discovered/{row['sys_id']}",
                                "service_relations": row.get("_relations", [])},
                    "cmdb": {"relations": [], "items": []}}
        raise FakeError(400, f"Requested URI does not represent any resource: {path}")

    def get_stats(self, table, query="", count=True, group_by="", sum_fields="", **_):
        rows = self._select(table, query)
        groups: Dict[tuple, list] = {}
        keys = [g for g in group_by.split(",") if g]
        for r in rows:
            groups.setdefault(tuple(self._field(r, k) for k in keys), []).append(r)
        def stats(rs):
            s = {"count": str(len(rs))}
            if sum_fields:
                s["sum"] = {f: str(sum(float(self._field(r, f) or 0) for r in rs)) for f in sum_fields.split(",")}
            return s
        if not keys:
            return {"stats": stats(rows)}
        return [{"stats": stats(rs), "groupby_fields": [{"field": k, "value": v} for k, v in zip(keys, g)]}
                for g, rs in groups.items()]


def seeded(username="admin", with_roles=("admin",)) -> FakeSN:
    sn = FakeSN(username)
    for t in ["alm_asset", "alm_hardware", "alm_consumable", "alm_license", "alm_stockroom", "alm_stock_rule",
              "alm_transfer_order", "alm_transfer_order_line", "alm_entitlement", "cmdb_model", "ast_contract",
              "clm_m2m_contract_asset", "proc_po", "proc_po_item", "proc_rec_slip", "proc_rec_slip_item",
              "cmdb_ci_hardware"]:
        sn.add("sys_db_object", name=t)
    me = sn.add("sys_user", user_name=username, name="Test Admin", email="admin@example.com", active="true")
    sn.add("sys_user", sys_id="b" * 32, user_name="jdoe", name="Jane Doe", email="jane@example.com", active="true")
    sn.add("sys_user", sys_id="c" * 32, user_name="rroe", name="Rick Roe", email="rick@example.com", active="true")
    for role in with_roles:
        r = sn.add("sys_user_role", name=role)
        sn.add("sys_user_has_role", user=me["sys_id"], role=r["sys_id"])
    for i, (v, lab) in enumerate([("2", "On order"), ("6", "In stock"), ("9", "In transit"), ("1", "In use"),
                                  ("10", "Consumed"), ("3", "In maintenance"), ("7", "Retired"), ("8", "Missing"),
                                  ("11", "Build")]):
        sn.add("sys_choice", name="alm_asset", element="install_status", value=v, label=lab, inactive="false",
               language="en", sequence=str(i), dependent_value="")
    for v, lab, dep in [("available", "Available", "6"), ("reserved", "Reserved", "6"), ("defective", "Defective", "6"),
                        ("in_repair", "In repair", "6"), ("in_repair", "In repair", "1"), ("pending_repair", "Pending repair", "1"),
                        ("disposed", "Disposed", "7"), ("sold", "Sold", "7"), ("obsolete", "Obsolete", "7"),
                        ("lost", "Lost", "8"), ("stolen", "Stolen", "8"), ("available", "Available", "9")]:
        sn.add("sys_choice", name="alm_asset", element="substatus", value=v, label=lab, dependent_value=dep,
               inactive="false", language="en", sequence="1")
    for v in ("draft", "active", "expired", "cancelled"):
        sn.add("sys_choice", name="ast_contract", element="state", value=v, label=v.title(), inactive="false",
               language="en", sequence="1", dependent_value="")
    sn.add("cmdb_model_category", sys_id="1" * 32, name="Computer", asset_class="alm_hardware")
    sn.add("cmdb_model_category", sys_id="2" * 32, name="Consumable", asset_class="alm_consumable")
    sn.add("cmdb_model_category", sys_id="3" * 32, name="Software License", asset_class="alm_license")
    sn.add("core_company", sys_id="4" * 32, name="Dell", vendor="true", manufacturer="true")
    sn.add("cmdb_hardware_product_model", sys_id="5" * 32, name="Dell XPS 13", display_name="Dell XPS 13",
           cmdb_model_category="1" * 32, manufacturer="4" * 32, status="In Production")
    sn.add("cmdb_consumable_product_model", sys_id="6" * 32, name="USB-C Cable", display_name="USB-C Cable",
           cmdb_model_category="2" * 32, manufacturer="4" * 32, status="In Production")
    sn.add("cmdb_software_product_model", sys_id="7" * 32, name="Office 365", display_name="Office 365",
           cmdb_model_category="3" * 32, manufacturer="4" * 32, status="In Production")
    sn.add("cmdb_contract_product_model", sys_id="8" * 32, name="Maintenance", display_name="Maintenance")
    sn.add("cmn_location", sys_id="9" * 32, name="Pune")
    sn.add("alm_stockroom", sys_id="a" * 32, name="Main Stockroom", location="9" * 32)
    sn.add("alm_stockroom", sys_id="d" * 32, name="Second Stockroom", location="9" * 32)
    sn.add("cmdb_ci_computer", sys_id="e" * 32, name="PUNE-LT-01", serial_number="SER-CI-1", asset="")
    return sn
