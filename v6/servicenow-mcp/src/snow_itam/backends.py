"""
ServiceNow access for the ITAM service.

The service talks to a small Backend interface so the same code runs inside the servicenow-mcp server
(servicenow_mcp.client.ServiceNowClient) and inside the Snow Agent (sn_client.SNClient). Adapters are
duck-typed: this module imports neither client package.
"""

from typing import Any, Dict, List, Optional, Sequence

from snow_itam.errors import ITAMError


def _status_of(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def _wrap(exc: Exception) -> ITAMError:
    if isinstance(exc, ITAMError):
        return exc
    status = _status_of(exc)
    code = getattr(exc, "code", None)
    msg = getattr(exc, "message", None) or str(exc)
    detail = getattr(exc, "detail", "") or ""
    return ITAMError(str(msg)[:500], code=str(code) if isinstance(code, str) else f"HTTP_{status or 'ERROR'}",
                     detail=str(detail)[:800], status=status)


class Backend:
    """Interface used by the ITAM service. All methods raise ITAMError on failure."""

    instance_url: str = ""
    username: str = ""

    def query(self, table: str, query: str = "", fields: str = "", limit: int = 20,
              display_value: str = "false", order_by: str = "", offset: int = 0) -> List[dict]:
        raise NotImplementedError

    def get(self, table: str, sys_id: str, fields: str = "", display_value: str = "false") -> dict:
        raise NotImplementedError

    def create(self, table: str, payload: dict) -> dict:
        raise NotImplementedError

    def update(self, table: str, sys_id: str, payload: dict) -> dict:
        raise NotImplementedError

    def delete(self, table: str, sys_id: str) -> None:
        raise NotImplementedError

    def aggregate(self, table: str, query: str = "", group_by: Sequence[str] = (),
                  sum_fields: Sequence[str] = ()) -> List[Dict[str, Any]]:
        """[{"group": {field: value}, "count": int, "sum": {field: float}}]"""
        raise NotImplementedError

    # -- conveniences built on the primitives ------------------------------------
    def count(self, table: str, query: str = "") -> int:
        rows = self.aggregate(table, query)
        return rows[0]["count"] if rows else 0

    def link(self, table: str, sys_id: str) -> str:
        return f"{self.instance_url}/{table}.do?sys_id={sys_id}" if self.instance_url and sys_id else ""


def _parse_stats(result: Any, group_by: Sequence[str], sum_fields: Sequence[str]) -> List[Dict[str, Any]]:
    rows = result if isinstance(result, list) else [result or {}]
    out = []
    for r in rows:
        stats = (r or {}).get("stats") or {}
        group = {g.get("field"): g.get("value") for g in (r or {}).get("groupby_fields") or []}
        sums = {}
        for f in sum_fields:
            try:
                sums[f] = float(((stats.get("sum") or {}).get(f)) or 0)
            except (TypeError, ValueError):
                sums[f] = 0.0
        try:
            n = int(stats.get("count") or 0)
        except (TypeError, ValueError):
            n = 0
        out.append({"group": group, "count": n, "sum": sums})
    if not group_by and not out:
        out = [{"group": {}, "count": 0, "sum": {f: 0.0 for f in sum_fields}}]
    return out


def _with_order(query: str, order_by: str) -> str:
    if not order_by:
        return query
    clause = f"ORDERBYDESC{order_by[1:]}" if order_by.startswith("-") else f"ORDERBY{order_by}"
    return f"{query}^{clause}" if query else clause


class McpClientBackend(Backend):
    """Adapter for servicenow_mcp.client.ServiceNowClient (servicenow-mcp server)."""

    def __init__(self, client):
        self.c = client
        self.instance_url = getattr(client, "instance_url", "")
        self.username = getattr(client, "username", "")

    def query(self, table, query="", fields="", limit=20, display_value="false", order_by="", offset=0):
        try:
            return self.c.query_records(table=table, query=query, fields=fields or None, limit=limit,
                                        offset=offset, display_value=display_value, order_by=order_by)
        except Exception as e:
            raise _wrap(e)

    def get(self, table, sys_id, fields="", display_value="false"):
        try:
            return self.c.get_record(table, sys_id, fields=fields or None, display_value=display_value)
        except Exception as e:
            raise _wrap(e)

    def create(self, table, payload):
        try:
            return self.c.create_record(table, payload)
        except Exception as e:
            raise _wrap(e)

    def update(self, table, sys_id, payload):
        try:
            return self.c.update_record(table, sys_id, payload)
        except Exception as e:
            raise _wrap(e)

    def delete(self, table, sys_id):
        try:
            self.c.delete_record(table, sys_id)
        except Exception as e:
            raise _wrap(e)

    def aggregate(self, table, query="", group_by=(), sum_fields=()):
        try:
            res = self.c.get_stats(table, query=query, count=True, group_by=",".join(group_by),
                                   sum_fields=",".join(sum_fields))
        except Exception as e:
            raise _wrap(e)
        return _parse_stats(res, group_by, sum_fields)


class SNClientBackend(Backend):
    """Adapter for the Snow Agent's sn_client.SNClient."""

    def __init__(self, sn):
        self.sn = sn
        self.instance_url = getattr(sn, "instance", "")
        self.username = getattr(sn, "username", "")

    def query(self, table, query="", fields="", limit=20, display_value="false", order_by="", offset=0):
        try:
            return self.sn.query(table, _with_order(query, order_by), fields, limit=limit,
                                 display_value=display_value, offset=offset)
        except Exception as e:
            raise _wrap(e)

    def get(self, table, sys_id, fields="", display_value="false"):
        try:
            return self.sn.get(table, sys_id, fields, display_value=display_value)
        except Exception as e:
            raise _wrap(e)

    def create(self, table, payload):
        try:
            return self.sn.create(table, payload)
        except Exception as e:
            raise _wrap(e)

    def update(self, table, sys_id, payload):
        try:
            return self.sn.patch(table, sys_id, payload)
        except Exception as e:
            raise _wrap(e)

    def delete(self, table, sys_id):
        try:
            self.sn.delete(table, sys_id)
        except Exception as e:
            raise _wrap(e)

    def aggregate(self, table, query="", group_by=(), sum_fields=()):
        params = {"sysparm_count": "true", "sysparm_query": query}
        if group_by:
            params["sysparm_group_by"] = ",".join(group_by)
        if sum_fields:
            params["sysparm_sum_fields"] = ",".join(sum_fields)
        try:
            res = self.sn.request("GET", f"/api/now/stats/{table}", params=params).get("result")
        except Exception as e:
            raise _wrap(e)
        return _parse_stats(res, group_by, sum_fields)
