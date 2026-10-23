"""
Shared ITOM plumbing on top of snow_itam's generic core (reference resolution, role checks, live choices, views):
CI class tree, bounded relationship walks, choice validation across a class chain.
"""

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from snow_itam.core import ITAMCore, flatten, is_sys_id, raw
from snow_itam.errors import ITAMError
from snow_itom import schema as S
from snow_itom.errors import ITOMError


def batches(items: Sequence[str], size: int = S.BATCH) -> Iterable[List[str]]:
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def bounded(value: Any, default: int, low: int, high: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(low, min(v, high))


class ITOMCore(ITAMCore):
    ROLES = S.ROLES

    def require_table(self, table: str, feature: str, plugin: str):
        if not self.table_exists(table):
            raise ITOMError(f"{feature} is not available on this instance", "PLUGIN_REQUIRED",
                            f"Table {table} is missing" + (f" — install the {plugin} plugin" if plugin else ""),
                            status=404)

    # ── capability checks (run on every call: nothing is cached between requests) ──────────────
    def table_chain(self, table: str) -> List[str]:
        """table and its parent classes (fields can be inherited)."""
        key = f"chain:{table}"
        if key not in self._cache:
            chain, name = [], table
            for _ in range(15):
                rows = self.b.query("sys_db_object", f"name={name}", "name,super_class.name", 1)
                if not rows:
                    break
                chain.append(name)
                name = rows[0].get("super_class.name") or ""
                if not name:
                    break
            self._cache[key] = chain
        return self._cache[key]

    def fields_present(self, table: str, fields: Sequence[str]) -> Optional[set]:
        """Which of `fields` exist on table (own or inherited). None when the dictionary cannot be read."""
        key = f"fields:{table}:{','.join(sorted(fields))}"
        if key not in self._cache:
            chain = ",".join(self.table_chain(table))
            try:
                # No dictionary rows at all for an existing table means it cannot be read (ACLs often return an
                # empty list instead of 403): then nothing is concluded and ServiceNow decides at write time.
                if not self.b.query("sys_dictionary", f"nameIN{chain}", "element", 1):
                    self._cache[key] = None
                    return None
                rows = self.b.query("sys_dictionary", f"nameIN{chain}^elementIN{','.join(fields)}", "element", 500)
                self._cache[key] = {r.get("element") for r in rows}
            except ITAMError as e:
                if e.status != 403:
                    raise
                self._cache[key] = None   # no read access to sys_dictionary: ServiceNow decides at write time
        return self._cache[key]

    def ensure(self, needs: "S.Needs", action: str = "") -> Dict[str, Any]:
        """Check what a feature needs on THIS instance, in order: tables (plugin) -> fields (release) -> roles.
        Returns {"optional_missing": {table: [fields]}, "role_check": ...}; raises PLUGIN_REQUIRED,
        INCOMPATIBLE_INSTANCE or FORBIDDEN with what to do."""
        for t in needs.tables:
            if not self.table_exists(t):
                raise ITOMError(f"{needs.feature} is not available on this instance", "PLUGIN_REQUIRED",
                                f"Table {t} is missing" + (f" — install the {needs.plugin} plugin"
                                                           if needs.plugin else ""), status=404)
        optional_missing: Dict[str, List[str]] = {}
        for t, req in needs.fields.items():
            opt = needs.optional.get(t, ())
            have = self.fields_present(t, tuple(req) + tuple(opt))
            if have is None:
                continue
            missing = [f for f in req if f not in have]
            if missing:
                raise ITOMError(f"{needs.feature}: this instance's {t} table does not have the expected field(s) "
                                f"{', '.join(missing)}", "INCOMPATIBLE_INSTANCE",
                                "The plugin is installed but its tables differ from the documented layout (another "
                                "release or a customisation); nothing was changed", status=412)
            gone = [f for f in opt if f not in have]
            if gone:
                optional_missing[t] = gone
        out: Dict[str, Any] = {"optional_missing": optional_missing}
        if needs.roles:
            out["role_check"] = self.require_any_role(needs.roles, action or needs.feature)
        return out

    def require_any_role(self, roles: Sequence[str], action: str) -> Dict[str, Any]:
        """admin or one of `roles`. When sys_user_has_role cannot be read, ServiceNow ACLs decide."""
        wanted = ["admin", *roles]
        key = f"anyrole:{','.join(wanted)}"
        if key not in self._cache:
            uid = self.me().get("sys_id")
            try:
                rows = self.b.query("sys_user_has_role", f"user={uid}^role.nameIN{','.join(wanted)}",
                                    "role.name", 50) if uid else []
                self._cache[key] = {"checked": True, "matched": sorted({r.get("role.name") for r in rows
                                                                         if r.get("role.name")})}
            except ITAMError as e:
                if e.status != 403:
                    raise
                self._cache[key] = {"checked": False, "matched": [],
                                    "note": "role check skipped (cannot read sys_user_has_role); ServiceNow ACLs apply"}
        res = self._cache[key]
        if res["checked"] and not res["matched"]:
            raise ITOMError(f"{action} needs one of these roles: {', '.join(wanted)}", "FORBIDDEN",
                            f"User {self.b.username} has none of them — ask an administrator to grant one",
                            status=403)
        return res

    # ── CI classes ──────────────────────────────────────────────────────────────
    def class_tree(self, root: str = "cmdb_ci", max_depth: int = 12) -> List[Dict[str, Any]]:
        """root and every class extending it (breadth first, from sys_db_object.super_class)."""
        key = f"tree:{root}:{max_depth}"
        if key in self._cache:
            return self._cache[key]
        rows = self.b.query("sys_db_object", f"name={root}", "name,label,super_class.name", 1)
        if not rows:
            raise ITOMError(f"Class '{root}' does not exist", "NOT_FOUND", status=404)
        out = [{"name": root, "label": rows[0].get("label", ""), "super_class": rows[0].get("super_class.name", ""),
                "depth": 0}]
        seen, frontier = {root}, [root]
        for depth in range(1, max_depth + 1):
            nxt = []
            for chunk in batches(frontier):
                for r in self.b.query("sys_db_object", "super_class.nameIN" + ",".join(chunk),
                                      "name,label,super_class.name", 1000):
                    n = r.get("name")
                    if n and n not in seen:
                        seen.add(n)
                        nxt.append(n)
                        out.append({"name": n, "label": r.get("label", ""),
                                    "super_class": r.get("super_class.name", ""), "depth": depth})
            if not nxt:
                break
            frontier = nxt
        self._cache[key] = out
        return out

    def service_classes(self) -> set:
        return {c["name"] for c in self.class_tree(S.SERVICE_ROOT)}

    # ── lookups ───────────────────────────────────────────────────────────────
    def resolve_ci(self, value: Any, label: str = "CI", table: str = "cmdb_ci") -> str:
        return self.resolve(table, value, ("name",), label=label)

    def resolve_service(self, value: Any) -> str:
        v = str(value or "").strip()
        if v and not is_sys_id(v):
            rows = self.b.query(S.SERVICE_ROOT, f"name={v}^ORnumber={v}", "sys_id", 3)
            if len(rows) == 1:
                return rows[0]["sys_id"]
        return self.resolve(S.SERVICE_ROOT, v, ("name",), label="service")

    def ci_details(self, ids: Iterable[str], fields: str = S.CI_FIELDS) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for chunk in batches(sorted(set(ids))):
            for r in self.b.query("cmdb_ci", "sys_idIN" + ",".join(chunk), fields, len(chunk), display_value="all"):
                f = flatten(r)
                out[raw(f.get("sys_id"))] = f
        return out

    def live_choice(self, tables: Sequence[str], field: str, value: Any, label: str = "") -> str:
        """value or label -> stored value, from sys_choice of the first table in the chain that defines choices.
        Refuses unknown values with the allowed list. With no choices defined anywhere, the value passes as is."""
        v = str(value or "").strip()
        if not v:
            return ""
        for t in tables:
            opts = self.choices(t, field)
            if opts:
                hit = next((o["value"] for o in opts if v.lower() in (o["value"].lower(), o["label"].lower())), None)
                if hit is None:
                    allowed = ", ".join(sorted({f"{o['label']} ({o['value']})" for o in opts}))
                    raise ITOMError(f"'{v}' is not a valid {label or field}", "INVALID_ARGS", f"Allowed: {allowed}")
                return hit
        return v

    # ── relationship walk ─────────────────────────────────────────────────────
    def walk(self, start: Sequence[str], direction: str, depth: int, max_nodes: int,
             rel_type: str = "") -> Tuple[Dict[str, int], List[Dict[str, Any]], bool]:
        """Breadth-first walk over cmdb_rel_ci. direction 'down' follows parent -> child (what a service or CI
        depends on), 'up' follows child -> parent (what depends on it). Returns ({sys_id: level}, edges, truncated)."""
        down = direction == "down"
        near, far = ("parent", "child") if down else ("child", "parent")
        type_q = ""
        if rel_type:
            type_q = f"^type={rel_type}" if is_sys_id(rel_type) else f"^type.name={rel_type}"
        levels = {sid: 0 for sid in start}
        edges, seen_edges, truncated = [], set(), False
        frontier = list(start)
        for level in range(1, depth + 1):
            nxt = []
            for chunk in batches(frontier):
                rows = self.b.query("cmdb_rel_ci", f"{near}IN{','.join(chunk)}{type_q}", S.REL_FIELDS, 1000,
                                    display_value="all")
                if len(rows) >= 1000:
                    truncated = True
                for r in map(flatten, rows):
                    rid = raw(r.get("sys_id"))
                    other = raw(r.get(far))
                    if not other or rid in seen_edges:
                        continue
                    if other not in levels:
                        if len(levels) >= max_nodes:
                            truncated = True
                            continue
                        levels[other] = level
                        nxt.append(other)
                    seen_edges.add(rid)
                    edges.append({"sys_id": rid, "parent": r.get("parent"), "child": r.get("child"),
                                  "type": r.get("type")})
            if not nxt:
                break
            frontier = nxt
        else:
            if frontier and self.b.query("cmdb_rel_ci", f"{near}IN{','.join(frontier[:S.BATCH])}{type_q}",
                                         "sys_id", 1):
                truncated = True   # relationships continue beyond the depth limit
        return levels, edges, truncated

    def service_members(self, service_ids: Sequence[str]) -> Dict[str, List[str]]:
        """svc_ci_assoc: service sys_id -> member CI sys_ids (populated by application services / Service Mapping)."""
        out: Dict[str, List[str]] = {}
        if not service_ids or not self.table_exists("svc_ci_assoc"):
            return out
        for chunk in batches(service_ids):
            for r in self.b.query("svc_ci_assoc", "service_idIN" + ",".join(chunk), "service_id,ci_id", S.MAX_NODES):
                out.setdefault(r.get("service_id", ""), []).append(r.get("ci_id", ""))
        return out
