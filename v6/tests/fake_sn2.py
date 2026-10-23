"""
Mock ServiceNow for testing the SoftProdigy agent. NOT a real instance.

Simulates what matters for the tests:
  * Basic auth (admin/pw OK, anything else 401), 404 on unknown records, 405 on POST to a record
  * Table API GET/POST/PATCH/DELETE with a small encoded-query evaluator
  * Aggregate API /api/now/stats/<table>?sysparm_count=true
  * Sessions via JSESSIONID cookie. Like ServiceNow, a session reads the user's
    `sys_update_set` preference ONCE when it starts. Metadata inserts are captured
    into sys_update_xml of that update set only if the update set's application
    matches the record's sys_scope (else they go to the scope's "Default" set).
  * sys_app inserts also create the sys_scope row.
"""
import base64, json, re, uuid
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI()
LOG = []
SESS = {}   # jsessionid -> {"update_set": sys_id}
ADMIN = "6816f79cc0a8016401c5a33be04be441"
JDOE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
USERS = {"admin": ("pw", ADMIN), "jdoe": ("pw2", JDOE)}
METADATA = {"sys_db_object", "sys_dictionary", "sys_choice", "sys_user_role", "sys_user_role_contains",
            "sys_security_acl", "sys_security_acl_role", "sys_script", "sys_script_client", "sys_ui_policy",
            "sys_ui_policy_action", "sys_script_include", "sysevent_email_action", "sys_number", "sys_app"}


def sid():
    return uuid.uuid4().hex


DB = {
    "sys_user": [{"sys_id": ADMIN, "user_name": "admin", "name": "System Administrator", "active": "true", "email": "a@x"},
                 {"sys_id": JDOE, "user_name": "jdoe", "name": "Jane Doe", "active": "true", "email": "j@x"}],
    "sys_user_role": [{"sys_id": "r_admin", "name": "admin"}, {"sys_id": "r_itil", "name": "itil"}],
    "sys_user_has_role": [{"sys_id": sid(), "user": ADMIN, "role": "r_admin", "inherited": "false", "_role_name": "admin", "_user_active": "true"},
                          {"sys_id": sid(), "user": JDOE, "role": "r_admin", "inherited": "false", "_role_name": "admin", "_user_active": "true"}]
                         + [{"sys_id": sid(), "user": sid(), "role": "r_admin", "inherited": "false", "_role_name": "admin", "_user_active": "true"} for _ in range(7)],
    "sys_properties": [{"sys_id": sid(), "name": "glide.appcreator.company.code", "value": "1234"},
                       {"sys_id": sid(), "name": "glide.security.use_csrf_token", "value": "true"},
                       {"sys_id": sid(), "name": "glide.buildname", "value": "Xanadu"},
                       {"sys_id": sid(), "name": "glide.ui.session_timeout", "value": "60"}],
    "sys_scope": [{"sys_id": "global", "name": "Global", "scope": "global"}],
    "sys_db_object": [{"sys_id": sid(), "name": n} for n in
                      ("task", "incident", "problem", "change_request", "sys_user", "sys_user_group", "cmdb_ci",
                       "sys_update_set", "sys_user_preference", "rm_story", "task_sla", "sys_atf_test", "kb_knowledge")],
    "sys_update_set": [{"sys_id": "default_global", "name": "Default", "state": "in progress", "application": "global"}],
    "sys_user_preference": [],
    "sys_update_xml": [],
    "sys_user_group": [{"sys_id": sid(), "name": "Service Desk", "active": "true"}],
    "incident": [{"sys_id": sid(), "number": f"INC00100{i}", "short_description": f"Seed {i}", "active": "true",
                  "priority": "1" if i == 0 else "3", "assignment_group": "" if i < 2 else "g"} for i in range(5)],
    "cmdb_ci": [{"sys_id": sid(), "name": f"srv{i}", "install_status": "1", "support_group": ""} for i in range(10)],
    "sys_dictionary": [{"sys_id": sid(), "name": "incident", "element": "", "internal_type": "collection", "audit": "true"}],
}
COUNTERS = {}


def cond_ok(rec, cond):
    cond = cond.strip()
    fm = re.match(r"^([\w.]+?)(ISNOTEMPTY|ISEMPTY|STARTSWITH|LIKE|!=|IN|>=|<=|=|>|<)", cond)
    if not cond or cond.startswith("ORDERBY") or "javascript:" in cond or (fm and "." in fm.group(1)):
        # dot-walks: support the two the health scan relies on, ignore the rest
        m = re.match(r"role\.name=(\w+)", cond)
        if m:
            return rec.get("_role_name") == m.group(1)
        m = re.match(r"user\.active=(\w+)", cond)
        if m:
            return rec.get("_user_active") == m.group(1)
        return True
    for op in ("ISNOTEMPTY", "ISEMPTY"):
        if cond.endswith(op):
            f = cond[: -len(op)]
            v = str(rec.get(f, "") or "")
            return bool(v) if op == "ISNOTEMPTY" else not v
    m = re.match(r"^(\w+)(STARTSWITH|LIKE|!=|IN|>=|<=|=|>|<)(.*)$", cond)
    if not m:
        return True
    f, op, val = m.groups()
    v = str(rec.get(f, "") if not isinstance(rec.get(f), bool) else str(rec.get(f)).lower())
    if op == "=":
        return v == val
    if op == "!=":
        return v != val
    if op == "STARTSWITH":
        return v.startswith(val)
    if op == "LIKE":
        return val.lower() in v.lower()
    if op == "IN":
        return v in val.split(",")
    return True


def match(rec, q):
    if not q:
        return True
    ors = q.split("^NQ")[0]
    parts = [p for p in ors.split("^") if p]
    ok_all = True
    i = 0
    while i < len(parts):
        grp = [parts[i]]
        while i + 1 < len(parts) and parts[i + 1].startswith("OR") and not parts[i + 1].startswith("ORDERBY"):
            grp.append(parts[i + 1][2:]); i += 1
        if not any(cond_ok(rec, c) for c in grp):
            ok_all = False
        i += 1
    return ok_all


def auth(r):
    """Returns the user's sys_id for valid Basic auth, else None."""
    h = r.headers.get("authorization", "")
    try:
        u, p = base64.b64decode(h[6:]).decode().split(":", 1)
    except Exception:
        return None
    return USERS[u][1] if h.startswith("Basic ") and u in USERS and USERS[u][0] == p else None


def session(request: Request, response: Response):
    js = request.cookies.get("JSESSIONID")
    user = auth(request)
    if not js or js not in SESS or SESS[js].get("user") != user:
        js = uuid.uuid4().hex
        pref = next((p for p in DB["sys_user_preference"] if p["user"] == user and p["name"] == "sys_update_set"), None)
        app_pref = next((p for p in DB["sys_user_preference"] if p["user"] == user and p["name"] == "apps.current_app"), None)
        SESS[js] = {"update_set": pref["value"] if pref else "default_global",
                    "current_app": app_pref["value"] if app_pref else "global", "user": user}
        response.set_cookie("JSESSIONID", js)
    return SESS[js]


def capture(sess, table, rec):
    if table not in METADATA:
        return
    # ServiceNow: record scope = explicit sys_scope, else the session's current application
    scope = rec["sys_id"] if table == "sys_app" else (rec.get("sys_scope") or sess.get("current_app") or "global")
    us = next((u for u in DB["sys_update_set"] if u["sys_id"] == sess["update_set"]), None)
    if not us or us.get("application") != scope or us.get("state") != "in progress":
        # ServiceNow would put it in that scope's Default update set
        us = next((u for u in DB["sys_update_set"] if u["application"] == scope and u["name"] == "Default"), None)
        if not us:
            us = {"sys_id": sid(), "name": "Default", "state": "in progress", "application": scope}
            DB["sys_update_set"].append(us)
    key = (f"sys_db_object_{rec['name']}" if table == "sys_db_object" else
           f"sys_dictionary_{rec['name']}_{rec['element']}" if table == "sys_dictionary" else
           f"sys_choice_{rec['name']}_{rec['element']}" if table == "sys_choice" else f"{table}_{rec['sys_id']}")
    DB["sys_update_xml"][:] = [x for x in DB["sys_update_xml"] if not (x["name"] == key and x["update_set"] == us["sys_id"])]
    DB["sys_update_xml"].append({"sys_id": sid(), "name": key, "type": table, "target_name": rec.get("name") or key,
                                 "action": "INSERT_OR_UPDATE", "update_set": us["sys_id"], "sys_created_on": "now"})


def J(data, status=200, response=None):
    r = JSONResponse(data, status)
    if response is not None:
        for k, v in response.headers.items():
            if k.lower() == "set-cookie":
                r.headers.append("set-cookie", v)
    return r


@app.get("/api/now/stats/{table}")
async def stats(table: str, request: Request):
    LOG.append({"m": "GET", "path": request.url.path, "q": dict(request.query_params)})
    if not auth(request):
        return J({"error": {"message": "User Not Authenticated"}}, 401)
    if table not in DB and not any(t["name"] == table for t in DB["sys_db_object"]):
        return J({"error": {"message": f"Invalid table {table}"}}, 400)
    q = request.query_params.get("sysparm_query", "")
    return {"result": {"stats": {"count": str(sum(1 for r in DB.get(table, []) if match(r, q)))}}}


@app.api_route("/api/now/table/{table}", methods=["GET", "POST"])
async def tbl(table: str, request: Request):
    resp = Response()
    body = await request.body()
    LOG.append({"m": request.method, "path": request.url.path, "q": dict(request.query_params),
                "body": json.loads(body) if body else None})
    if not auth(request):
        return J({"error": {"message": "User Not Authenticated", "detail": "Required to provide Auth information"}}, 401)
    sess = session(request, resp)
    if request.method == "POST":
        d = json.loads(body)
        d["sys_id"] = sid()
        prefix = {"incident": "INC", "problem": "PRB", "change_request": "CHG", "rm_story": "STRY", "task": "TASK",
                  "kb_knowledge": "KB"}.get(table)
        if prefix:
            COUNTERS[table] = COUNTERS.get(table, 1000) + 1
            d["number"] = f"{prefix}{COUNTERS[table]:07d}"
        if table == "sys_db_object" and d.get("super_class") and not re.fullmatch(r"[0-9a-f]{32}", d["super_class"]):
            return J({"error": {"message": "Invalid reference for super_class"}}, 400)
        if table == "sys_security_acl":
            return J({"error": {"message": "Operation Failed", "detail": "ACL creation requires security_admin elevation"}}, 403)
        d.setdefault("sys_created_by_id", sess["user"])
        DB.setdefault(table, []).append(d)
        if table == "sys_app":
            DB["sys_scope"].append({"sys_id": d["sys_id"], "name": d["name"], "scope": d["scope"]})
        if table == "sys_db_object":
            pass
        capture(sess, table, d)
        return J({"result": d}, 201, resp)
    q = request.query_params.get("sysparm_query", "")
    lim = int(request.query_params.get("sysparm_limit", 10) or 10)
    rows = [r for r in DB.get(table, []) if match(r, q)][:lim]
    if table == "sys_user_has_role" and request.query_params.get("sysparm_display_value") == "true":
        rows = [dict(r, role=r.get("_role_name")) for r in rows]
    fields = request.query_params.get("sysparm_fields")
    if fields:
        rows = [{k: r.get(k, "") for k in fields.split(",")} for r in rows]
    return J({"result": rows}, 200, resp)


@app.api_route("/api/now/table/{table}/{rid}", methods=["GET", "PATCH", "PUT", "POST", "DELETE"])
async def rec(table: str, rid: str, request: Request):
    resp = Response()
    body = await request.body()
    LOG.append({"m": request.method, "path": request.url.path, "body": json.loads(body) if body else None})
    if not auth(request):
        return J({"error": {"message": "User Not Authenticated"}}, 401)
    sess = session(request, resp)
    if request.method == "POST":
        return J({"error": {"message": "Method not Supported"}}, 405)
    for r in DB.get(table, []):
        if r["sys_id"] == rid:
            if request.method == "DELETE":
                DB[table].remove(r)
                return Response(status_code=204)
            if request.method in ("PATCH", "PUT"):
                new = json.loads(body) if body else {}
                changed = any(str(r.get(k)) != str(v) for k, v in new.items())
                r.update(new)
                if changed:
                    capture(sess, table, r)
            return J({"result": r}, 200, resp)
    return J({"error": {"message": "No Record found"}}, 404)


@app.get("/__db/{table}")
def dump(table: str):
    return DB.get(table, [])


@app.get("/__log")
def log():
    return LOG


@app.api_route("/{p:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def other(p: str, request: Request):
    LOG.append({"m": request.method, "path": "/" + p, "UNKNOWN": True})
    return J({"error": {"message": "not found"}}, 400)
