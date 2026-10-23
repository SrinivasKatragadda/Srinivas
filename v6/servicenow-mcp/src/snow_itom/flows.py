"""Flow Designer flows / subflows and legacy workflows, and their executions (read-only).

Starting or cancelling a flow is not offered: the instance has no public REST API for it (it needs an
IntegrationHub REST trigger or a scripted REST API, see get_itom_capabilities)."""

from typing import Any, Dict, List

from snow_itam.core import check_date, is_sys_id, raw
from snow_itom import schema as S
from snow_itom.core import ITOMCore, bounded
from snow_itom.errors import ITOMError

KINDS = ("flow", "subflow", "workflow", "all")
ENGINES = ("flow", "workflow")


class FlowsMixin(ITOMCore):

    def list_flows(self, kind: str = "flow", active_only: bool = True, text: str = "", limit: int = 25) -> dict:
        k = (kind or "flow").strip().lower()
        if k not in KINDS:
            raise ITOMError(f"kind must be one of {', '.join(KINDS)}", "INVALID_ARGS")
        lim = bounded(limit, 25, 1, 200)
        t = (text or "").strip()
        out: Dict[str, Any] = {"kind": k}
        if k in ("flow", "subflow", "all"):
            self.require_table("sys_hub_flow", "Flow Designer", "com.glide.hub.flow_engine")
            parts = [f"type={k}"] if k != "all" else ["typeINflow,subflow"]
            if active_only:
                parts.append("active=true")
            if t:
                parts.append(f"nameLIKE{t}^ORinternal_nameLIKE{t}")
            out["flows"] = self.view_query("sys_hub_flow", "^".join(parts), S.FLOW_FIELDS, lim, order_by="name")
        if k in ("workflow", "all"):
            self.require_table("wf_workflow", "Legacy workflows", "com.glideapp.workflow")
            rows = self.view_query("wf_workflow", f"nameLIKE{t}" if t else "", S.WORKFLOW_FIELDS, lim, order_by="name")
            published = {raw(r.get("workflow")) for r in self.b.query(
                "wf_workflow_version", "active=true^published=true", "workflow", 1000)}
            for r in rows:
                r["published_active"] = raw(r.get("sys_id")) in published
            out["workflows"] = [r for r in rows if r["published_active"]] if active_only else rows
        out["count"] = len(out.get("flows", [])) + len(out.get("workflows", []))
        return out

    def _flow_id(self, flow: str, engine: str) -> str:
        if engine == "flow":
            v = flow.strip()
            if not is_sys_id(v):
                rows = self.b.query("sys_hub_flow", f"name={v}^ORinternal_name={v}", "sys_id", 3)
                if len(rows) == 1:
                    return rows[0]["sys_id"]
            return self.resolve("sys_hub_flow", v, ("name", "internal_name"), label="flow")
        return self.resolve("wf_workflow", flow, ("name",), label="workflow")

    def list_flow_executions(self, flow: str = "", state: str = "", engine: str = "flow", since: str = "",
                             limit: int = 20) -> dict:
        e = (engine or "flow").strip().lower()
        if e not in ENGINES:
            raise ITOMError(f"engine must be one of {', '.join(ENGINES)}", "INVALID_ARGS")
        table, fields, ref, created = (("sys_flow_context", S.FLOW_CONTEXT_FIELDS, "flow", "sys_created_on") if e == "flow"
                                       else ("wf_context", S.WF_CONTEXT_FIELDS, "workflow", "started"))
        self.require_table(table, "Flow executions" if e == "flow" else "Workflow executions",
                           "com.glide.hub.flow_engine" if e == "flow" else "com.glideapp.workflow")
        parts: List[str] = []
        if flow:
            parts.append(f"{ref}={self._flow_id(flow, e)}")
        if state:
            parts.append(f"state={self.live_choice([table], 'state', state, 'execution state')}")
        if since:
            parts.append(f"{created}>={check_date('since', since, with_time=True)}")
        rows = self.view_query(table, "^".join(parts), fields, bounded(limit, 20, 1, 200), order_by=f"-{created}")
        return {"engine": e, "count": len(rows), "executions": rows}

    def get_flow_execution(self, execution: str) -> dict:
        v = (execution or "").strip()
        if not is_sys_id(v):
            raise ITOMError("execution must be the sys_id of a flow or workflow execution", "INVALID_ARGS",
                            "Use list_flow_executions to find it")
        for table, fields, engine in (("sys_flow_context", S.FLOW_CONTEXT_FIELDS, "flow"),
                                      ("wf_context", S.WF_CONTEXT_FIELDS, "workflow")):
            if self.table_exists(table) and self.b.query(table, f"sys_id={v}", "sys_id", 1):
                rec = self.view(table, v, fields)
                return {"engine": engine, "execution": rec, "link": self.b.link(table, v)}
        raise ITOMError(f"No flow or workflow execution {v}", "NOT_FOUND", status=404)
