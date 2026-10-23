"""
Application services through ServiceNow's Application Service API (com.snc.cmdb.it_service.api):
POST /api/now/cmdb/app_service/create creates a manual application service (cmdb_ci_service_discovered) and adds
its CIs from service_relations [{parent, child}] (an empty parent marks an entry point).

The API itself UPDATES an existing service with the same name; this tool refuses that instead (duplicate
protection), so it only ever creates.
"""

import time
from typing import Any, Dict, List, Optional

from snow_itam.core import is_sys_id, raw, same
from snow_itom import schema as S
from snow_itom.core import ITOMCore
from snow_itom.errors import ITOMError

MAX_RELATIONS = 200


class ServicesMixin(ITOMCore):
    VERIFY_DELAYS = (2, 3, 5)   # seconds between read-back attempts after creating a service

    def _service_fields(self, fields: Optional[Dict[str, Any]]) -> Dict[str, str]:
        fields = dict(fields or {})
        unknown = sorted(set(fields) - set(S.SERVICE_WRITABLE))
        if unknown:
            raise ITOMError(f"Field(s) {unknown} cannot be set with this tool", "INVALID_ARGS",
                            f"Allowed: {sorted(S.SERVICE_WRITABLE)}")
        out = {}
        for k, v in fields.items():
            ref_table, lookup = S.SERVICE_WRITABLE[k]
            if v is None or str(v).strip() == "":
                continue
            if ref_table:
                out[k] = (self.resolve_user(v, label=k) if ref_table == "sys_user"
                          else self.resolve(ref_table, v, lookup, label=k))
            elif k in S.SERVICE_CHOICE_FIELDS:
                out[k] = self.live_choice(S.SERVICE_CHOICE_TABLES, k, v, k)
            else:
                out[k] = str(v)
        return out

    def _relations(self, relations: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
        rels = list(relations or [])
        if len(rels) > MAX_RELATIONS:
            raise ITOMError(f"At most {MAX_RELATIONS} relations per call", "INVALID_ARGS")
        out, keys, cache = [], set(), {}

        def ci(v: Any, what: str) -> str:
            key = str(v).strip()
            if key not in cache:
                cache[key] = self.resolve_ci(key, label=what)
            return cache[key]

        for i, r in enumerate(rels):
            if not isinstance(r, dict) or not str(r.get("child") or "").strip():
                raise ITOMError(f"relations[{i}] needs a 'child' CI (name or sys_id)", "INVALID_ARGS")
            child = ci(r["child"], f"relations[{i}].child")
            parent = ci(r["parent"], f"relations[{i}].parent") if str(r.get("parent") or "").strip() else ""
            if parent == child:
                raise ITOMError(f"relations[{i}]: a CI cannot be its own parent", "INVALID_ARGS")
            if (parent, child) not in keys:
                keys.add((parent, child))
                out.append({"parent": parent, "child": child})
        if out:
            # The API adds CIs from the entry points down and rejects a relation whose parent is never added
            reachable = {r["child"] for r in out if not r["parent"]}
            if not reachable:
                raise ITOMError("At least one relation must be an entry point (no parent)", "INVALID_ARGS")
            changed = True
            while changed:
                changed = False
                for r in out:
                    if r["parent"] in reachable and r["child"] not in reachable:
                        reachable.add(r["child"])
                        changed = True
            dangling = [r for r in out if r["parent"] and r["parent"] not in reachable]
            if dangling:
                raise ITOMError("Some relations hang off a parent that is not part of the service", "INVALID_ARGS",
                                f"Parent sys_id(s) not reachable from an entry point: "
                                f"{sorted({r['parent'] for r in dangling})}")
        return out

    def create_application_service(self, name: str, cis: str = "", relations: Optional[List[Dict[str, Any]]] = None,
                                   fields: Optional[Dict[str, Any]] = None, dry_run: bool = False,
                                   confirm: bool = False) -> dict:
        self.require_table(S.APP_SERVICE_TABLE, "Application services", "com.snc.cmdb.it_service.api")
        auth = self.require_roles("service", "Creating an application service")
        nm = (name or "").strip()
        if not nm or len(nm) > 100:
            raise ITOMError("'name' is required (at most 100 characters)", "INVALID_ARGS")
        dup = self.b.query(S.SERVICE_ROOT, f"name={nm}", "sys_id,sys_class_name", 1)
        if dup:
            raise ITOMError(f"A service named '{nm}' already exists", "DUPLICATE",
                            f"sys_id {dup[0]['sys_id']} ({dup[0].get('sys_class_name')}). The Application Service "
                            "API would overwrite it, so this tool refuses.", status=409)
        values = self._service_fields(fields)
        entry = [{"child": c.strip()} for c in str(cis or "").split(",") if c.strip()]
        rels = self._relations(entry + list(relations or []))
        payload = {"name": nm, **values, "service_relations": rels}
        ci_names = {sid: raw(d.get("name")) for sid, d in self.ci_details({r["child"] for r in rels}).items()}
        preview = {"action": "create_application_service", "table": S.APP_SERVICE_TABLE, "payload": payload,
                   "cis": [{"sys_id": r["child"], "name": ci_names.get(r["child"], ""),
                            "parent": r["parent"] or "(entry point)"} for r in rels]}
        reasons = [f"Creates application service '{nm}' in the CMDB with {len(rels)} CI relation(s)"]
        if dry_run:
            return {"dry_run": True, **preview, "requires_confirmation": True, "reasons": reasons}
        if not confirm:
            return {"success": False, "requires_confirmation": True, "reasons": reasons, **preview,
                    "message": "This creates CMDB records — review it and call again with confirm=true"}

        resp = self.b.request("POST", "/api/now/cmdb/app_service/create", json=payload)
        body = resp.get("result", resp) if isinstance(resp, dict) else {}
        url = str((body or {}).get("url") or "")
        sid = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        if not is_sys_id(sid):
            rows = self.b.query(S.APP_SERVICE_TABLE, f"name={nm}", "sys_id", 1)
            sid = rows[0]["sys_id"] if rows else ""
        if not sid:
            raise ITOMError("ServiceNow did not return the new service", "WRITE_FAILED",
                            str(body.get("info") or "")[:300] if isinstance(body, dict) else "")

        check = self.b.get(S.APP_SERVICE_TABLE, sid, ",".join(["sys_id", "name", *values]))
        if not check:
            raise ITOMError("The service could not be read back after creating it", "VERIFY_FAILED",
                            "A business rule or ACL may have rejected or removed it")
        expected = {"name": nm, **values}
        mismatches = {k: {"expected": v, "actual": raw(check.get(k))} for k, v in expected.items()
                      if not same(check.get(k), v)}
        # ServiceNow populates the service's CIs asynchronously (seen live: none listed right after the call, all
        # listed seconds later), so the check is repeated briefly before reporting anything as missing.
        missing, source = list(rels), ""
        for delay in ([0] + list(self.VERIFY_DELAYS)) if rels else [0]:
            time.sleep(delay)
            stored, source = self._stored_relations(sid)
            missing = [r for r in rels if (r["parent"], r["child"]) not in stored]
            if not missing:
                break
        out = {"success": not mismatches.get("name"), "action": "create_application_service",
               "table": S.APP_SERVICE_TABLE, "sys_id": sid, "link": self.b.link(S.APP_SERVICE_TABLE, sid),
               "api_info": (body or {}).get("info") if isinstance(body, dict) else None,
               "verified": not mismatches and not missing, "relations_expected": len(rels),
               "relations_found": len(rels) - len(missing), "relations_checked_in": source, "role_check": auth,
               "also_created": "ServiceNow adds a service environment and layers (svc_environment, svc_layer), "
                               "manual endpoint CIs (cmdb_ci_endpoint_manual) and the relationships between them"}
        if mismatches:
            out["mismatches"] = mismatches
        if missing:
            out["relations_not_yet_listed"] = [{**r, "child_name": ci_names.get(r["child"], "")} for r in missing]
            out["note"] = (f"The service exists, but after {sum(self.VERIFY_DELAYS)} s ServiceNow did not yet list "
                           "some requested relations. It may still be populating the service: check with "
                           "get_service_topology(include_service_content=true). If they stay missing, domain "
                           "separation or a business rule skipped those CIs.")
        return out

    def _stored_relations(self, sid: str):
        """(parent, child) pairs ServiceNow holds for the service. getContent answers synchronously; svc_ci_assoc
        membership (filled asynchronously on a live instance) is the fallback, matching children only."""
        try:
            content = self.b.request("GET", f"/api/now/cmdb/app_service/{sid}/getContent", params={"mode": "full"})
            svc = (content.get("result", content) if isinstance(content, dict) else {}).get("service") or {}
            pairs = {(str(r.get("parent") or ""), str(r.get("child") or ""))
                     for r in svc.get("service_relations") or []}
            return pairs, "getContent"
        except Exception:   # API unreachable or refused: fall back to membership rows
            members = set(self.service_members([sid]).get(sid, []))
            return _AnyParent(members), "svc_ci_assoc"


class _AnyParent:
    """Membership test for (parent, child) pairs that only knows the children."""

    def __init__(self, children):
        self.children = children

    def __contains__(self, pair):
        return pair[1] in self.children
