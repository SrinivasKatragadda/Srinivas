"""
ServiceNow REST client + application-scope / update-set context for the SoftProdigy Snow Agent.

Why this module exists
----------------------
v5 created an update set but never made it *current*, so every artifact the agent
created landed in whatever update set the user happened to have selected (usually
"Default"). ServiceNow decides where a change is captured from the calling user's
preferences, so this module does it the same way the SoftProdigy platform does
(platform-service/snow_config/set_sn_app_update_set_context.py):

  1. resolve (or create) the application scope          -> sys_scope / sys_app
  2. find or create the update set, tied to that app     -> sys_update_set.application
  3. point the user at it via sys_user_preference:
        apps.current_app                 = <app sys_id>
        sys_update_set                   = <update set sys_id>
        updateSetForScope<app sys_id>    = <update set sys_id>   (scoped picker)
  4. start a *fresh* HTTP session so ServiceNow reads the new preferences
  5. after the build, read sys_update_xml to prove what was captured

TLS certificates are verified by default (SNOW_VERIFY_TLS=false only for lab use).
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import HTTPException

SYSID_RE = re.compile(r"^[0-9a-f]{32}$", re.I)
GLOBAL_SCOPE_SYS_ID = "global"


def verify_tls() -> bool:
    return os.getenv("SNOW_VERIFY_TLS", "true").strip().lower() not in ("0", "false", "no")


def is_sys_id(v: Any) -> bool:
    return isinstance(v, str) and bool(SYSID_RE.match(v.strip()))


def ref_value(v: Any) -> str:
    if isinstance(v, dict):
        return (v.get("value") or "").strip()
    return (v or "").strip() if isinstance(v, str) else ""


def normalize_instance(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="ServiceNow instance URL is required")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


class SNError(Exception):
    def __init__(self, status: int, message: str, detail: str = "", url: str = ""):
        super().__init__(f"{status}: {message}{' — ' + detail if detail else ''}")
        self.status = status
        self.message = message
        self.detail = detail
        self.url = url

    def to_http(self, what: str = "ServiceNow call") -> HTTPException:
        code = self.status if self.status in (400, 401, 403, 404, 409, 429) else 502
        return HTTPException(status_code=code, detail=f"{what} failed ({self.status}): {self.message}"
                             + (f" — {self.detail}" if self.detail else ""))


class SNClient:
    """Small, strict Table API client. Every error raises SNError — nothing is swallowed."""

    def __init__(self, instance_url: str, username: str, password: str, timeout: int = 45):
        if not username or not password:
            raise HTTPException(status_code=400, detail="ServiceNow username and password are required")
        self.instance = normalize_instance(instance_url)
        self.username = username
        self.password = password
        self.timeout = timeout
        self.calls = 0
        self._new_session()

    # -- plumbing ---------------------------------------------------------------
    def _oauth_token(self) -> str:
        """Password-grant token, used when SNOW_CLIENT_ID/SNOW_CLIENT_SECRET are set (instances that block Basic auth)."""
        if self._token and time.time() < self._token_exp:
            return self._token
        try:
            r = requests.post(f"{self.instance}/oauth_token.do", timeout=self.timeout, verify=verify_tls(),
                              data={"grant_type": "password", "client_id": self.client_id,
                                    "client_secret": self.client_secret,
                                    "username": self.username, "password": self.password})
        except requests.exceptions.SSLError as e:
            raise SNError(495, "TLS certificate verification failed", str(e)[:200])
        except requests.exceptions.RequestException as e:
            raise SNError(503, "Cannot reach ServiceNow instance", str(e)[:200])
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not r.ok or "access_token" not in data:
            raise SNError(401, "OAuth token request failed",
                          data.get("error_description") or data.get("error") or r.text[:200])
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 1800)) - 60
        return self._token

    def _new_session(self):
        self.session = requests.Session()
        self.client_id = os.getenv("SNOW_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("SNOW_CLIENT_SECRET", "").strip()
        self._token, self._token_exp = "", 0.0
        if self.client_id and self.client_secret:
            self.session.auth = None
        else:
            self.session.auth = (self.username, self.password)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.verify = verify_tls()

    def reset_session(self):
        """Drop cookies so the next call starts a new ServiceNow session that re-reads user preferences."""
        try:
            self.session.close()
        except Exception:
            pass
        self._new_session()

    def request(self, method: str, path: str, params: Optional[dict] = None, json: Optional[dict] = None) -> dict:
        url = f"{self.instance}{path}"
        self.calls += 1
        headers = {"Authorization": f"Bearer {self._oauth_token()}"} if self.client_id and self.client_secret else None
        try:
            # verify passed per request: requests would otherwise let REQUESTS_CA_BUNDLE override session.verify
            r = self.session.request(method, url, params=params, json=json, headers=headers,
                                    timeout=self.timeout, verify=self.verify)
        except requests.exceptions.SSLError as e:
            raise SNError(495, "TLS certificate verification failed", str(e)[:200], url)
        except requests.exceptions.RequestException as e:
            raise SNError(503, "Cannot reach ServiceNow instance", str(e)[:200], url)
        if r.status_code == 204 or not r.content:
            if r.ok:
                return {}
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:300]}
        if not r.ok:
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err or {}).get("message") if isinstance(err, dict) else None
            det = (err or {}).get("detail") if isinstance(err, dict) else None
            raise SNError(r.status_code, msg or r.reason or "error", det or "", url)
        return data if isinstance(data, dict) else {"result": data}

    # -- Table API ----------------------------------------------------------------
    def query(self, table: str, query: str = "", fields: str = "", limit: int = 100,
              display_value: str = "false", offset: int = 0) -> List[dict]:
        params = {"sysparm_query": query, "sysparm_limit": limit, "sysparm_offset": offset,
                  "sysparm_display_value": display_value, "sysparm_exclude_reference_link": "true"}
        if fields:
            params["sysparm_fields"] = fields
        res = self.request("GET", f"/api/now/table/{table}", params=params).get("result", [])
        return res if isinstance(res, list) else [res]

    def get_one(self, table: str, query: str, fields: str = "") -> Optional[dict]:
        rows = self.query(table, query, fields, limit=1)
        return rows[0] if rows else None

    def get(self, table: str, sys_id: str, fields: str = "", display_value: str = "false") -> dict:
        params = {"sysparm_display_value": display_value, "sysparm_exclude_reference_link": "true"}
        if fields:
            params["sysparm_fields"] = fields
        return self.request("GET", f"/api/now/table/{table}/{sys_id}", params=params).get("result", {})

    def create(self, table: str, payload: dict, fields: str = "") -> dict:
        params = {"sysparm_exclude_reference_link": "true"}
        if fields:
            params["sysparm_fields"] = fields
        return self.request("POST", f"/api/now/table/{table}", params=params, json=payload).get("result", {})

    def patch(self, table: str, sys_id: str, payload: dict) -> dict:
        return self.request("PATCH", f"/api/now/table/{table}/{sys_id}",
                            params={"sysparm_exclude_reference_link": "true"}, json=payload).get("result", {})

    def delete(self, table: str, sys_id: str) -> None:
        self.request("DELETE", f"/api/now/table/{table}/{sys_id}")

    def count(self, table: str, query: str = "") -> int:
        """Aggregate API count (no rows transferred)."""
        data = self.request("GET", f"/api/now/stats/{table}",
                            params={"sysparm_count": "true", "sysparm_query": query})
        try:
            return int(((data.get("result") or {}).get("stats") or {}).get("count") or 0)
        except (TypeError, ValueError):
            return 0

    def group_count(self, table: str, group_by: str, query: str = "", having: str = "",
                    display_value: bool = False) -> List[tuple]:
        """Aggregate API grouped count: [({field: value, ...}, count), ...]."""
        params = {"sysparm_count": "true", "sysparm_group_by": group_by, "sysparm_query": query}
        if having:
            params["sysparm_having"] = having
        if display_value:
            params["sysparm_display_value"] = "true"
        res = self.request("GET", f"/api/now/stats/{table}", params=params).get("result") or []
        if isinstance(res, dict):
            res = [res]
        out = []
        for r in res:
            vals = {g.get("field"): (g.get("display_value") if display_value and g.get("display_value") not in (None, "")
                                     else g.get("value")) for g in (r.get("groupby_fields") or [])}
            try:
                n = int(((r.get("stats") or {}).get("count")) or 0)
            except (TypeError, ValueError):
                n = 0
            out.append((vals, n))
        return out

    def table_exists(self, table: str) -> bool:
        return bool(self.get_one("sys_db_object", f"name={table}", "sys_id"))

    def table_sys_id(self, table: str) -> Optional[str]:
        row = self.get_one("sys_db_object", f"name={table}", "sys_id")
        return row["sys_id"] if row else None

    def property(self, name: str) -> Optional[str]:
        row = self.get_one("sys_properties", f"name={name}", "value")
        return None if row is None else row.get("value")

    def user(self, user_name: Optional[str] = None) -> dict:
        row = self.get_one("sys_user", f"user_name={user_name or self.username}",
                           "sys_id,user_name,name,email,active")
        if not row:
            raise SNError(404, f"User '{user_name or self.username}' not found in sys_user")
        return row

    def link(self, table: str, sys_id: str) -> str:
        return f"{self.instance}/{table}.do?sys_id={sys_id}"


def default_instance_url() -> str:
    """The instance everyone signs in to (SNOW_INSTANCE_URL in .env). A URL is not a secret; credentials never come from here."""
    url = os.getenv("SNOW_INSTANCE_URL", "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def client_from(body: Any, *, url_keys=("snow_url", "instance_url", "sn_url"),
                user_keys=("snow_user", "username", "sn_username"),
                pass_keys=("snow_pass", "password", "sn_password")) -> SNClient:
    """Build a client from a request body dict. Falls back to .env only when SNOW_ALLOW_ENV_CREDS=true."""
    get = (lambda k: body.get(k)) if isinstance(body, dict) else (lambda k: getattr(body, k, None))
    url = next((get(k) for k in url_keys if get(k)), None)
    user = next((get(k) for k in user_keys if get(k)), None)
    pw = next((get(k) for k in pass_keys if get(k)), None)
    url = url or default_instance_url()
    if os.getenv("SNOW_ALLOW_ENV_CREDS", "false").lower() == "true":
        user = user or os.getenv("SNOW_USER")
        pw = pw or os.getenv("SNOW_PASS")
    if not (url and user and pw):
        raise HTTPException(status_code=400, detail="ServiceNow instance URL, username and password are required")
    return SNClient(url, user, pw)


# ════════════════════════════════════════════════════════════════════════════
# Application scope
# ════════════════════════════════════════════════════════════════════════════

def vendor_prefix(sn: SNClient) -> str:
    """Company code used for new scopes (x_<prefix>_<app>). PDIs usually have a numeric one."""
    for prop in ("glide.appcreator.company.code", "glide.appcreator.vendor.prefix"):
        try:
            v = (sn.property(prop) or "").strip().lower()
        except SNError:
            v = ""
        if v:
            return re.sub(r"[^a-z0-9]", "", v)[:8] or "hai"
    return "hai"


def make_scope_name(sn: SNClient, app_name: str, requested: str = "") -> str:
    prefix = vendor_prefix(sn)
    requested = (requested or "").strip().lower()
    if requested.startswith(f"x_{prefix}_") and re.fullmatch(r"x_[a-z0-9_]{3,18}", requested):
        return requested
    base = re.sub(r"[^a-z0-9]", "", (requested.split("_")[-1] if requested else "") or app_name.lower())
    base = base[: max(3, 18 - len(prefix) - 3)] or "app"
    return f"x_{prefix}_{base}"[:18]


def resolve_app(sn: SNClient, identifier: str) -> Optional[Dict[str, str]]:
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if identifier.lower() == "global":
        return {"sys_id": GLOBAL_SCOPE_SYS_ID, "name": "Global", "scope": "global"}
    if is_sys_id(identifier):
        row = sn.get_one("sys_scope", f"sys_id={identifier}", "sys_id,name,scope")
        if row:
            return row
    for q in (f"scope={identifier}", f"name={identifier}"):
        row = sn.get_one("sys_scope", q, "sys_id,name,scope")
        if row:
            return row
    return None


def ensure_app(sn: SNClient, *, mode: str, app_name: str, app_scope: str = "",
               existing_app: str = "") -> Dict[str, Any]:
    """
    mode = "global"   -> Global scope (tables become u_*)
    mode = "existing" -> use an existing application (sys_id, scope or name)
    mode = "scoped"   -> reuse the app if it already exists, otherwise create a new scoped app (sys_app)
    """
    if mode == "global":
        return {"sys_id": GLOBAL_SCOPE_SYS_ID, "name": "Global", "scope": "global", "created": False}
    if mode == "existing":
        app = resolve_app(sn, existing_app or app_scope or app_name)
        if not app:
            raise SNError(404, f"Application '{existing_app or app_scope or app_name}' not found in sys_scope")
        return {**app, "created": False}
    scope = make_scope_name(sn, app_name, app_scope)
    app = resolve_app(sn, scope) or resolve_app(sn, app_name)
    if app:
        return {**app, "created": False}
    try:
        rec = sn.create("sys_app", {"name": app_name, "scope": scope, "version": "1.0.0",
                                    "short_description": f"{app_name} — created by SoftProdigy Snow Agent"},
                        fields="sys_id,name,scope")
    except SNError as e:
        raise SNError(e.status, f"Could not create application '{app_name}' ({scope}) through the Table API: "
                                f"{e.message}", "Create the app once in App Engine Studio (or with the SoftProdigy "
                                "platform's /createStudioApp) and build with scope_mode='existing'.")
    if not rec.get("sys_id"):
        raise SNError(500, "ServiceNow did not return a sys_id for the new application")
    return {"sys_id": rec["sys_id"], "name": rec.get("name", app_name), "scope": rec.get("scope", scope), "created": True}


# ════════════════════════════════════════════════════════════════════════════
# Update set context
# ════════════════════════════════════════════════════════════════════════════

def _upsert_pref(sn: SNClient, user_sys_id: str, name: str, value: str) -> Dict[str, Any]:
    row = sn.get_one("sys_user_preference", f"user={user_sys_id}^name={name}", "sys_id,value")
    if row:
        previous = row.get("value", "")
        if previous != value:
            sn.patch("sys_user_preference", row["sys_id"], {"value": value})
        return {"name": name, "sys_id": row["sys_id"], "previous": previous, "created": False}
    rec = sn.create("sys_user_preference", {"user": user_sys_id, "name": name, "value": value}, fields="sys_id")
    return {"name": name, "sys_id": rec.get("sys_id"), "previous": None, "created": True}


def ensure_update_set(sn: SNClient, *, app_sys_id: str, name: str, description: str = "",
                      existing_sys_id: str = "", reuse_open: bool = True) -> Tuple[dict, bool]:
    if existing_sys_id:
        rec = sn.get("sys_update_set", existing_sys_id, "sys_id,name,state,application")
        if not rec.get("sys_id"):
            raise SNError(404, f"Update set {existing_sys_id} not found")
        if rec.get("state") != "in progress":
            raise SNError(409, f"Update set '{rec.get('name')}' is '{rec.get('state')}', not 'in progress' — "
                               "choose an open update set or let the agent create one")
        if ref_value(rec.get("application")) not in ("", app_sys_id):
            raise SNError(409, f"Update set '{rec.get('name')}' belongs to a different application; "
                               "changes would be captured in the wrong scope")
        return rec, False
    if reuse_open:
        rec = sn.get_one("sys_update_set", f"name={name}^application={app_sys_id}^state=in progress",
                         "sys_id,name,state,application")
        if rec:
            return rec, False
    rec = sn.create("sys_update_set", {"name": name, "description": description, "state": "in progress",
                                       "application": app_sys_id}, fields="sys_id,name,state,application")
    if not rec.get("sys_id"):
        raise SNError(500, "ServiceNow did not return a sys_id for the new update set")
    return rec, True


def set_context(sn: SNClient, *, app: Dict[str, Any], update_set_name: str, description: str = "",
                existing_update_set: str = "") -> Dict[str, Any]:
    """Make <app, update set> current for the integration user and return what changed."""
    user = sn.user()
    us, created = ensure_update_set(sn, app_sys_id=app["sys_id"], name=update_set_name,
                                    description=description, existing_sys_id=existing_update_set)
    prefs = [
        _upsert_pref(sn, user["sys_id"], "apps.current_app", app["sys_id"]),
        _upsert_pref(sn, user["sys_id"], "sys_update_set", us["sys_id"]),
        _upsert_pref(sn, user["sys_id"], f"updateSetForScope{app['sys_id']}", us["sys_id"]),
    ]
    sn.reset_session()  # new ServiceNow session -> preferences take effect for every following call
    return {
        "user_sys_id": user["sys_id"],
        "app": app,
        "update_set": {"sys_id": us["sys_id"], "name": us.get("name", update_set_name), "created": created,
                       "link": sn.link("sys_update_set", us["sys_id"])},
        "preferences": prefs,
        "set_at": datetime.now(timezone.utc).isoformat(),
    }


def restore_context(sn: SNClient, ctx: Dict[str, Any]) -> List[str]:
    """Put the user's previous app / update set preferences back (best effort, reported)."""
    notes = []
    for p in ctx.get("preferences", []):
        try:
            if p.get("created"):
                sn.delete("sys_user_preference", p["sys_id"])
            elif p.get("previous") is not None:
                sn.patch("sys_user_preference", p["sys_id"], {"value": p["previous"]})
            notes.append(f"restored {p['name']}")
        except SNError as e:
            notes.append(f"could not restore {p['name']}: {e}")
    sn.reset_session()
    return notes


def captured_changes(sn: SNClient, update_set_sys_id: str, limit: int = 500) -> List[dict]:
    """What ServiceNow actually recorded in the update set (sys_update_xml)."""
    return sn.query("sys_update_xml", f"update_set={update_set_sys_id}^ORDERBYsys_created_on",
                    "sys_id,name,type,target_name,action,sys_created_on", limit=limit)


def complete_update_set(sn: SNClient, update_set_sys_id: str) -> dict:
    rec = sn.get("sys_update_set", update_set_sys_id, "sys_id,name,state")
    if not rec.get("sys_id"):
        raise SNError(404, "Update set not found")
    n = sn.count("sys_update_xml", f"update_set={update_set_sys_id}")
    if n == 0:
        raise SNError(409, f"Update set '{rec.get('name')}' has no captured changes — refusing to mark it complete")
    sn.patch("sys_update_set", update_set_sys_id, {"state": "complete"})
    return {"sys_id": update_set_sys_id, "name": rec.get("name"), "state": "complete", "captured_changes": n,
            "link": sn.link("sys_update_set", update_set_sys_id),
            "export_xml_url": f"{sn.instance}/export_update_set.do?sysparm_sys_id={update_set_sys_id}"
                              f"&sysparm_delete_when_done=false"}


def instance_name(url: str) -> str:
    return (urlparse(url).hostname or "").split(".")[0]


# ── Extended discovery helpers ────────────────────────────────────────────────

class _DiscoveryMixin:
    """Extra read-only helpers added to SNClient for release detection and conflict checking."""

    def release_info(self) -> dict:
        """Detect ServiceNow release name, version, build tag from sys_properties."""
        out = {"build_name": "", "build_tag": "", "release": "", "major": 0, "minor": 0}
        try:
            for prop in ("glide.buildname", "glide.buildtag", "glide.build.date"):
                try:
                    val = self.property(prop)
                    if val:
                        if prop == "glide.buildname":
                            out["build_name"] = val.strip()
                            # e.g. "Washington DC" or "Xanadu"
                            out["release"] = val.strip().split()[0].lower()
                        elif prop == "glide.buildtag":
                            out["build_tag"] = val.strip()
                            # e.g. "glide-washington-11-19-2023__patch0-11-29-2023"
                            m = re.search(r"glide-([a-z]+)-(\d+)-(\d+)", val)
                            if m:
                                out["release"] = m.group(1)
                except Exception:
                    pass
            # Map release to major version number (approximate)
            RELEASE_ORDER = ["orlando", "paris", "quebec", "rome", "san_diego", "tokyo", "utah",
                             "vancouver", "washington", "xanadu", "yokohama", "zurich"]
            rel = out["release"].replace(" ", "_").lower()
            if rel in RELEASE_ORDER:
                out["major"] = RELEASE_ORDER.index(rel) + 20  # Orlando=20, Paris=21...
        except Exception:
            pass
        return out

    def active_plugins(self, prefix_filter: str = "") -> list:
        """Return list of active plugin IDs (optionally filtered by prefix)."""
        try:
            params = {"sysparm_fields": "source,name,active,version", "sysparm_limit": 200,
                      "sysparm_query": "active=true"}
            if prefix_filter:
                params["sysparm_query"] += f"^sourceSTARTSWITH{prefix_filter}"
            result = self.request("GET", "/api/now/table/sys_plugins", params, None)
            return result.get("result", [])
        except Exception:
            return []

    def existing_business_rules(self, table: str) -> list:
        """Read existing BRs on a table for conflict detection."""
        try:
            result = self.request("GET", "/api/now/table/sys_script", {
                "sysparm_query": f"collection={table}^active=true",
                "sysparm_fields": "name,when,order,action_insert,action_update,filter_condition",
                "sysparm_limit": 50
            }, None)
            return result.get("result", [])
        except Exception:
            return []

    def existing_acls(self, table: str) -> list:
        """Read existing ACLs on a table for overlap detection."""
        try:
            result = self.request("GET", "/api/now/table/sys_security_acl", {
                "sysparm_query": f"name={table}^active=true",
                "sysparm_fields": "name,operation,type,condition",
                "sysparm_limit": 50
            }, None)
            return result.get("result", [])
        except Exception:
            return []

    def oob_table_fields(self, table: str) -> list:
        """Return field names already on an OOB table."""
        try:
            result = self.request("GET", "/api/now/table/sys_dictionary", {
                "sysparm_query": f"name={table}^internal_type!=collection",
                "sysparm_fields": "element,column_label,internal_type",
                "sysparm_limit": 200
            }, None)
            return result.get("result", [])
        except Exception:
            return []


# Mixin in the methods onto SNClient so existing code that already holds SNClient instances picks them up.
for _name in ("release_info", "active_plugins", "existing_business_rules", "existing_acls", "oob_table_fields"):
    setattr(SNClient, _name, getattr(_DiscoveryMixin, _name))
