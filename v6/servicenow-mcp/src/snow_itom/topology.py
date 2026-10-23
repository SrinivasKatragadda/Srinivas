"""CI classes, service topology, CI impact and unmapped CIs — read-only walks over cmdb_rel_ci and svc_ci_assoc."""

from typing import Any, Dict, List

from snow_itam.core import raw
from snow_itom import schema as S
from snow_itom.core import ITOMCore, batches, bounded
from snow_itom.errors import ITOMError


def _nodes(levels: Dict[str, int], details: Dict[str, Dict[str, Any]], via: Dict[str, str]) -> List[Dict[str, Any]]:
    out = []
    for sid, level in sorted(levels.items(), key=lambda kv: (kv[1], kv[0])):
        d = details.get(sid) or {"sys_id": sid, "note": "not readable or deleted"}
        out.append({**d, "level": level, **({"via": via[sid]} if sid in via else {})})
    return out


class TopologyMixin(ITOMCore):

    def list_ci_classes(self, root: str = "cmdb_ci", text: str = "", max_depth: int = 12, limit: int = 200,
                        include_counts: bool = False) -> dict:
        tree = self.class_tree((root or "cmdb_ci").strip(), bounded(max_depth, 12, 1, 20))
        t = (text or "").strip().lower()
        rows = [c for c in tree if not t or t in c["name"].lower() or t in (c["label"] or "").lower()]
        lim = bounded(limit, 200, 1, 1000)
        shown = rows[:lim]
        if include_counts:
            for c in shown[:50]:
                c["records_incl_subclasses"] = self.b.count(c["name"])
        return {"root": root, "total_classes": len(tree), "matched": len(rows), "count": len(shown),
                "classes": shown, **({"truncated": True} if len(rows) > lim else {}),
                **({"note": "Counts only for the first 50 classes"} if include_counts and len(shown) > 50 else {})}

    def get_service_topology(self, service: str, depth: int = 3, max_nodes: int = 200,
                             relationship_type: str = "", include_service_content: bool = False) -> dict:
        sid = self.resolve_service(service)
        depth, max_nodes = bounded(depth, 3, 1, S.MAX_DEPTH), bounded(max_nodes, 200, 1, S.MAX_NODES)
        svc = self.view(S.SERVICE_ROOT, sid, S.SERVICE_FIELDS)
        levels, edges, truncated = self.walk([sid], "down", depth, max_nodes, relationship_type)
        via = {}
        for ci in self.service_members([sid]).get(sid, []):
            if ci and ci not in levels and len(levels) < max_nodes:
                levels[ci] = 1
                via[ci] = "svc_ci_assoc"
        levels.pop(sid, None)
        details = self.ci_details(levels)
        out: Dict[str, Any] = {"service": svc, "node_count": len(levels), "edge_count": len(edges),
                               "nodes": _nodes(levels, details, via), "edges": edges, "truncated": truncated,
                               "depth": depth}
        if not levels:
            out["note"] = "The service has no related CIs (no cmdb_rel_ci children and no svc_ci_assoc members)"
        if include_service_content:
            out["service_content"] = self._service_content(sid, raw(svc.get("sys_class_name")))
        return out

    def _service_content(self, sid: str, cls: str) -> Any:
        """Application Service API view of a manual application service (cmdb_ci_service_discovered only)."""
        if cls not in {c["name"] for c in self.class_tree(S.APP_SERVICE_TABLE)}:
            return {"available": False, "reason": f"{cls} is not an application service (cmdb_ci_service_discovered)"}
        try:
            return self.b.request("GET", f"/api/now/cmdb/app_service/{sid}/getContent", params={"mode": "full"})
        except ITOMError as e:
            return {"available": False, "reason": e.message}
        except Exception as e:  # backend errors (ITAMError) — reported, not raised: the topology above stands
            return {"available": False, "reason": getattr(e, "message", str(e))[:300]}

    def get_ci_impact(self, ci: str, depth: int = 4, max_nodes: int = 300, include_incidents: bool = True) -> dict:
        cid = self.resolve_ci(ci)
        depth, max_nodes = bounded(depth, 4, 1, S.MAX_DEPTH), bounded(max_nodes, 300, 1, S.MAX_NODES)
        levels, edges, truncated = self.walk([cid], "up", depth, max_nodes)
        details = self.ci_details(levels)
        svc_classes = self.service_classes()
        services = {sid: "relationship" for sid in levels
                    if sid != cid and raw((details.get(sid) or {}).get("sys_class_name")) in svc_classes}
        if self.table_exists("svc_ci_assoc"):
            for chunk in batches(list(levels)):
                for r in self.b.query("svc_ci_assoc", "ci_idIN" + ",".join(chunk), "service_id", S.MAX_NODES):
                    services.setdefault(r.get("service_id", ""), "svc_ci_assoc")
        services.pop("", None)
        svc_rows = []
        for chunk in batches(list(services)):
            svc_rows += self.view_query(S.SERVICE_ROOT, "sys_idIN" + ",".join(chunk), S.SERVICE_FIELDS, len(chunk))
        for s in svc_rows:
            s["found_via"] = services.get(raw(s.get("sys_id")), "")
        ci_rec = details.get(cid) or self.view("cmdb_ci", cid, S.CI_FIELDS)
        levels.pop(cid, None)
        out: Dict[str, Any] = {"ci": ci_rec, "impacted_services": svc_rows, "impacted_service_count": len(svc_rows),
                               "upstream_nodes": _nodes(levels, details, {}), "edges": edges, "truncated": truncated,
                               "depth": depth}
        if include_incidents:
            ids = [cid, *levels]
            inc = []
            for chunk in batches(ids):
                inc += self.view_query("incident", "active=true^cmdb_ciIN" + ",".join(chunk), S.INCIDENT_FIELDS, 50)
            for chunk in batches(list(services)):
                inc += self.view_query("incident", "active=true^business_serviceIN" + ",".join(chunk),
                                       S.INCIDENT_FIELDS, 50)
            uniq = {raw(i.get("sys_id")): i for i in inc}
            out["open_incidents"] = list(uniq.values())[:100]
        return out

    def find_unmapped_cis(self, ci_class: str = "cmdb_ci_server", scan_limit: int = 500, operational_only: bool = False,
                          check_service_membership: bool = True, limit: int = 50) -> dict:
        cls = (ci_class or "").strip()
        if cls not in {c["name"] for c in self.class_tree("cmdb_ci")}:
            raise ITOMError(f"'{cls}' is not a CMDB CI class", "INVALID_ARGS", "Use list_ci_classes to find one")
        scan = bounded(scan_limit, 500, 1, S.MAX_SCAN)
        q = "operational_status=1" if operational_only else ""
        total = self.b.count(cls, q)
        cis: List[Dict[str, Any]] = []
        while len(cis) < scan:
            page = self.b.query(cls, q, "sys_id,name,sys_class_name,operational_status", min(200, scan - len(cis)),
                                order_by="name", offset=len(cis))
            cis += page
            if len(page) < 200:
                break
        ids = [c["sys_id"] for c in cis]
        related, members = set(), set()
        for chunk in batches(ids):
            joined = ",".join(chunk)
            for r in self.b.query("cmdb_rel_ci", f"parentIN{joined}^ORchildIN{joined}", "parent,child", 2000):
                related.update((r.get("parent"), r.get("child")))
            if check_service_membership and self.table_exists("svc_ci_assoc"):
                for r in self.b.query("svc_ci_assoc", f"ci_idIN{joined}", "ci_id", 2000):
                    members.add(r.get("ci_id"))
        unmapped = [c for c in cis if c["sys_id"] not in related and c["sys_id"] not in members]
        lim = bounded(limit, 50, 1, 500)
        return {"ci_class": cls, "total_in_class": total, "scanned": len(cis), "complete": len(cis) >= total,
                "unmapped_count_in_scan": len(unmapped), "unmapped": unmapped[:lim],
                "definition": "No cmdb_rel_ci relationship as parent or child" +
                              (" and not a member of any service (svc_ci_assoc)" if check_service_membership else ""),
                **({} if len(cis) >= total else {"note": f"Scanned {len(cis)} of {total}; raise scan_limit "
                                                         f"(max {S.MAX_SCAN}) or narrow the class"})}
