# snow_interactive.py
# Interactive ServiceNow operations: Incidents, Stories, Changes, Test Cases,
# Process Flow, Requirement Classification, AI chat, connection check.
#
# Request models accept BOTH naming styles used across the project:
#   username / sn_username,  password / sn_password,  incident_id / incident_number,
#   query / filter_query,    close_notes / resolution_notes,  requirements / requirements_text
# so the bundled UI (snow-ui/index.html) and direct API callers both work.

import os
import json
import base64
import logging
import tempfile
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

import groq_client
import docx_render as dr

logger = logging.getLogger(__name__)

snow_interactive_router = APIRouter(prefix="/snow/interactive", tags=["Snow Interactive"])

# Server-side credentials are only used when SNOW_ALLOW_ENV_CREDS=true (otherwise every caller must send its own)
_ENV = os.getenv("SNOW_ALLOW_ENV_CREDS", "false").lower() == "true"
_DEFAULT_URL = os.getenv("SNOW_INSTANCE_URL", "").strip()
_DEFAULT_USER = os.getenv("SNOW_USER", os.getenv("SERVICENOW_USERNAME", "")) if _ENV else ""
_DEFAULT_PASS = os.getenv("SNOW_PASS", os.getenv("SERVICENOW_PASSWORD", "")) if _ENV else ""
_VERIFY = os.getenv("SNOW_VERIFY_TLS", "true").strip().lower() not in ("0", "false", "no")


# ─────────────────────────── helpers ───────────────────────────

def get_snow_headers(username: str, password: str) -> dict:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _resolve_update_set_sys_id(instance_url: str, username: str, password: str,
                               update_set_name: Optional[str] = None) -> Optional[str]:
    """
    Resolve which update set sys_id to use for a write operation.

    Priority order:
      1. If update_set_name is given → find or create an "in progress" update set with that name.
      2. Else → find ANY "in progress" update set on the instance (first one found).
      3. If none exists at all → return None (write still succeeds, just uncaptured).

    Returns sys_id string or None.
    """
    try:
        base = instance_url.rstrip("/")
        headers = get_snow_headers(username, password)
        us_url = f"{base}/api/now/table/sys_update_set"

        if update_set_name:
            # Try to find existing in-progress set with this name
            r = requests.get(us_url, headers=headers,
                             params={"sysparm_query": f"name={update_set_name}^state=in progress",
                                     "sysparm_fields": "sys_id,name,state",
                                     "sysparm_limit": 1},
                             timeout=15, verify=_VERIFY)
            if r.ok:
                rows = r.json().get("result", [])
                if rows:
                    logger.info("Update set matched by name '%s': %s", update_set_name, rows[0]["sys_id"])
                    return rows[0]["sys_id"]

            # Not found — create a new one with that name
            r2 = requests.post(us_url, headers=headers,
                               json={"name": update_set_name, "state": "in progress",
                                     "description": f"Created by SoftProdigy Snow Agent"},
                               timeout=15, verify=_VERIFY)
            if r2.ok:
                new_sys_id = r2.json().get("result", {}).get("sys_id", "")
                if new_sys_id:
                    logger.info("Created new update set '%s': %s", update_set_name, new_sys_id)
                    return new_sys_id
            return None

        else:
            # No name given — find any in-progress update set on the instance
            r = requests.get(us_url, headers=headers,
                             params={"sysparm_query": "state=in progress^ORDERBYDESCsys_updated_on",
                                     "sysparm_fields": "sys_id,name,state",
                                     "sysparm_limit": 1},
                             timeout=15, verify=_VERIFY)
            if r.ok:
                rows = r.json().get("result", [])
                if rows:
                    logger.info("Auto-selected in-progress update set '%s': %s",
                                rows[0].get("name"), rows[0]["sys_id"])
                    return rows[0]["sys_id"]
            return None

    except Exception as e:
        logger.warning("Could not resolve update set: %s", e)
        return None


def get_snow_headers_with_update_set(instance_url: str, username: str, password: str,
                                     update_set_name: Optional[str] = None) -> dict:
    """
    Returns standard auth headers PLUS X-Update-Set-Sys-Id so that every
    create/patch call is captured in an update set automatically.

    - If update_set_name is provided → uses (or creates) that named update set.
    - Otherwise → picks the most-recently-updated in-progress update set on the instance.
    - If no in-progress set exists → headers are returned without the update-set header
      (the write still works, just won't be tracked).
    """
    headers = get_snow_headers(username, password)
    us_sys_id = _resolve_update_set_sys_id(instance_url, username, password, update_set_name)
    if us_sys_id:
        headers["X-Update-Set-Sys-Id"] = us_sys_id
        logger.info("Capturing change in update set: %s", us_sys_id)
    else:
        logger.warning("No in-progress update set found — change will not be captured in an update set")
    return headers


def _raise_for_snow(resp: requests.Response, what: str):
    if resp.ok:
        return
    detail = resp.text[:300]
    try:
        err = resp.json().get("error", {})
        detail = err.get("message", "") + (" — " + err.get("detail", "") if err.get("detail") else "")
    except Exception:
        pass
    code = 401 if resp.status_code == 401 else 404 if resp.status_code == 404 else 502
    raise HTTPException(status_code=code, detail=f"ServiceNow {what} failed ({resp.status_code}): {detail}")


def snow_get(instance_url: str, username: str, password: str, table: str, params: dict = None):
    url = f"{instance_url.rstrip('/')}/api/now/table/{table}"
    resp = requests.get(url, headers=get_snow_headers(username, password), params=params or {}, timeout=30, verify=_VERIFY)
    _raise_for_snow(resp, f"read of {table}")
    return resp.json().get("result", [])


def snow_post(instance_url: str, username: str, password: str, table: str, data: dict,
              update_set_name: Optional[str] = None):
    url = f"{instance_url.rstrip('/')}/api/now/table/{table}"
    headers = get_snow_headers_with_update_set(instance_url, username, password, update_set_name)
    resp = requests.post(url, headers=headers, json=data, timeout=30, verify=_VERIFY)
    _raise_for_snow(resp, f"create in {table}")
    return resp.json().get("result", {})


def snow_patch(instance_url: str, username: str, password: str, table: str, sys_id: str, data: dict,
               update_set_name: Optional[str] = None):
    url = f"{instance_url.rstrip('/')}/api/now/table/{table}/{sys_id}"
    headers = get_snow_headers_with_update_set(instance_url, username, password, update_set_name)
    resp = requests.patch(url, headers=headers, json=data, timeout=30, verify=_VERIFY)
    _raise_for_snow(resp, f"update of {table}")
    return resp.json().get("result", {})


def groq_generate(prompt: str, max_tokens: int = 3000) -> str:
    return groq_client.generate(prompt, max_tokens=max_tokens, temperature=0.7)


def _ai_json(prompt: str, system: str, max_tokens: int = 6000) -> dict:
    try:
        data = groq_client.generate_json(prompt, system=system, max_tokens=max_tokens)
    except groq_client.GroqError as e:
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"AI returned an unreadable answer, please retry: {e}")
    return data if isinstance(data, dict) else {"items": data}


def _plain(result: dict, key: str) -> str:
    v = result.get(key, "")
    if isinstance(v, dict):
        return v.get("display_value") or v.get("value") or ""
    return v or ""


def _is_sys_id(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value.lower())


def _lookup_sys_id(req, table: str, number_or_id: str) -> str:
    if _is_sys_id(number_or_id):
        return number_or_id
    rows = snow_get(req.instance_url, req.username, req.password, table,
                    {"sysparm_query": f"number={number_or_id}", "sysparm_limit": 1, "sysparm_fields": "sys_id"})
    if not rows:
        raise HTTPException(status_code=404, detail=f"{number_or_id} not found in {table}")
    return rows[0]["sys_id"]


def _with_labels(rows: List[dict]) -> List[dict]:
    """sysparm_display_value=all returns {value, display_value}; flatten and add *_label keys for the UI."""
    out = []
    for r in rows:
        flat = {}
        for k, v in r.items():
            if isinstance(v, dict) and ("display_value" in v or "value" in v):
                flat[k] = v.get("value", "")
                if k in ("state", "priority", "urgency", "impact", "risk", "type"):
                    flat[f"{k}_label"] = v.get("display_value", "")
                elif k in ("assigned_to", "assignment_group", "caller_id"):
                    flat[k] = v.get("display_value", "")
            else:
                flat[k] = v
        flat.setdefault("state_label", flat.get("state", ""))
        out.append(flat)
    return out


# ─────────────────────────── request models ───────────────────────────

class SnowAuth(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    instance_url: str = Field(default=_DEFAULT_URL, validation_alias=AliasChoices("instance_url", "snow_url", "sn_url"))
    username: str = Field(default=_DEFAULT_USER, validation_alias=AliasChoices("username", "sn_username", "snow_user", "sn_user"))
    password: str = Field(default=_DEFAULT_PASS, validation_alias=AliasChoices("password", "sn_password", "snow_pass", "sn_pass"))


class IncidentCreate(SnowAuth):
    short_description: str
    description: Optional[str] = None
    category: Optional[str] = None
    urgency: Optional[str] = "3"
    impact: Optional[str] = "3"
    priority: Optional[str] = None
    caller_id: Optional[str] = None
    assigned_to: Optional[str] = None
    assignment_group: Optional[str] = None
    update_set_name: Optional[str] = None   # capture in this update set (auto-picks if omitted)


class IncidentUpdate(SnowAuth):
    incident_id: str = Field(validation_alias=AliasChoices("incident_id", "incident_number", "number", "sys_id"))
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    work_notes: Optional[str] = None
    close_notes: Optional[str] = Field(default=None, validation_alias=AliasChoices("close_notes", "resolution_notes"))
    close_code: Optional[str] = None
    priority: Optional[str] = None
    urgency: Optional[str] = None
    impact: Optional[str] = None
    assigned_to: Optional[str] = None
    assignment_group: Optional[str] = None
    update_set_name: Optional[str] = None   # capture in this update set (auto-picks if omitted)


class IncidentList(SnowAuth):
    limit: Optional[int] = 10
    state: Optional[str] = None
    query: Optional[str] = Field(default=None, validation_alias=AliasChoices("query", "filter_query"))


class TestCaseGenerate(SnowAuth):
    requirements: str = Field(validation_alias=AliasChoices("requirements", "requirements_text", "description"))
    doc_type: Optional[str] = "functional"   # functional | regression | integration
    project_name: Optional[str] = Field(default="SoftProdigy Snow", validation_alias=AliasChoices("project_name", "feature_title", "title"))


class TestCaseDownload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    test_cases: Any = None              # the object returned by /testcases/generate (or its list)
    feature_title: Optional[str] = None
    project_name: Optional[str] = None
    requirements: Optional[str] = None  # fallback: regenerate if no test_cases passed


class ProcessFlowGenerate(SnowAuth):
    description: str = Field(validation_alias=AliasChoices("description", "requirements", "requirements_text"))
    process_name: Optional[str] = Field(default="ServiceNow Process", validation_alias=AliasChoices("process_name", "title"))


class ProcessFlowDownload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    process_flow: Optional[dict] = None
    process_name: Optional[str] = None
    description: Optional[str] = None


class StoryCreate(SnowAuth):
    short_description: str
    acceptance_criteria: Optional[str] = ""
    description: Optional[str] = None
    state: Optional[str] = "1"
    story_points: Optional[int] = 5
    assigned_to: Optional[str] = None
    update_set_name: Optional[str] = None   # capture in this update set (auto-picks if omitted)


class StoryList(SnowAuth):
    limit: Optional[int] = 10
    state: Optional[str] = None
    query: Optional[str] = Field(default=None, validation_alias=AliasChoices("query", "filter_query"))


class ChangeRequestCreate(SnowAuth):
    short_description: str
    description: Optional[str] = None
    type: Optional[str] = "normal"
    risk: Optional[str] = "moderate"
    priority: Optional[str] = "3"
    assignment_group: Optional[str] = None
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    implementation_plan: Optional[str] = None
    update_set_name: Optional[str] = None   # capture in this update set (auto-picks if omitted)


class ChangeList(SnowAuth):
    limit: Optional[int] = 10
    query: Optional[str] = Field(default=None, validation_alias=AliasChoices("query", "filter_query"))


class RequirementClassify(SnowAuth):
    requirements: str = Field(validation_alias=AliasChoices("requirements", "requirements_text"))
    project_name: Optional[str] = "SoftProdigy Snow"


class ChatMessage(SnowAuth):
    message: str
    conversation_history: Optional[list] = []


def _need_creds(req: SnowAuth):
    if not (req.instance_url and req.username and req.password):
        raise HTTPException(status_code=400, detail="ServiceNow instance URL, username and password are required")


# ─────────────────────────── Incidents ───────────────────────────

@snow_interactive_router.post("/incidents/create")
def create_incident(req: IncidentCreate):
    _need_creds(req)
    data = {"short_description": req.short_description}
    for f in ("description", "category", "urgency", "impact", "priority", "caller_id", "assigned_to", "assignment_group"):
        v = getattr(req, f)
        if v not in (None, ""):
            data[f] = v
    result = snow_post(req.instance_url, req.username, req.password, "incident", data,
                       update_set_name=req.update_set_name)
    number = _plain(result, "number")
    return {
        "success": True,
        "status": "created",
        "message": f"Incident created: {number or 'N/A'}",
        "number": number,
        "incident_number": number,
        "sys_id": _plain(result, "sys_id"),
        "state": _plain(result, "state") or "1",
    }


@snow_interactive_router.post("/incidents/list")
def list_incidents(req: IncidentList):
    _need_creds(req)
    params = {
        "sysparm_limit": req.limit or 10,
        "sysparm_display_value": "all",
        "sysparm_fields": "number,short_description,state,priority,urgency,assigned_to,category,sys_created_on,sys_id",
    }
    filters = []
    if req.state:
        filters.append(f"state={req.state}")
    if req.query:
        q = req.query.strip()
        # accept either an encoded query (contains = or ^) or plain search text
        filters.append(q if ("=" in q or "^" in q) else f"short_descriptionLIKE{q}^ORdescriptionLIKE{q}")
    filters.append("ORDERBYDESCsys_created_on")
    params["sysparm_query"] = "^".join(filters)
    incidents = _with_labels(snow_get(req.instance_url, req.username, req.password, "incident", params))
    return {"success": True, "incidents": incidents, "count": len(incidents),
            "message": f"{len(incidents)} incident(s) found"}


@snow_interactive_router.post("/incidents/update")
def update_incident(req: IncidentUpdate):
    _need_creds(req)
    sys_id = _lookup_sys_id(req, "incident", req.incident_id.strip())
    data = {}
    for f in ("short_description", "description", "state", "work_notes", "close_notes", "close_code",
              "priority", "urgency", "impact", "assigned_to", "assignment_group"):
        v = getattr(req, f)
        if v not in (None, ""):   # never blank a field because the UI sent an empty box
            data[f] = v
    if not data:
        raise HTTPException(status_code=400, detail="Nothing to update — fill in at least one field")
    result = snow_patch(req.instance_url, req.username, req.password, "incident", sys_id, data,
                        update_set_name=req.update_set_name)
    number = _plain(result, "number") or req.incident_id
    return {"success": True, "status": "updated", "message": f"Incident updated: {number}",
            "number": number, "incident_number": number, "sys_id": _plain(result, "sys_id") or sys_id}


@snow_interactive_router.post("/incidents/resolve")
def resolve_incident(req: IncidentUpdate):
    req.state = "6"
    if not req.close_code:
        req.close_code = "Solution provided"
    if not req.close_notes:
        req.close_notes = "Issue resolved by SoftProdigy Snow agent."
    out = update_incident(req)
    out["status"] = "resolved"
    out["message"] = out["message"].replace("updated", "resolved")
    return out


# ─────────────────────────── Test cases ───────────────────────────

_TC_SYSTEM = ("You are a senior ServiceNow QA lead. You write precise, executable test cases that trace back "
              "to the requirement's acceptance criteria.")


@snow_interactive_router.post("/testcases/generate")
def generate_test_cases(req: TestCaseGenerate):
    prompt = f"""Generate {req.doc_type} test cases for this ServiceNow feature.

FEATURE: {req.project_name}
REQUIREMENTS / USER STORY / ACCEPTANCE CRITERIA:
{req.requirements[:8000]}

Rules:
- 10 to 15 test cases: about half "positive", the rest "negative" and "edge".
- Every acceptance criterion in the input must be covered by at least one test case.
- Steps are concrete ServiceNow UI actions (navigate, fill field X, click Submit ...).
- "acceptance_criterion" is the Given/When/Then criterion the test proves.

Return JSON:
{{
  "test_cases": [
    {{
      "id": "TC-001",
      "title": "short title",
      "type": "positive | negative | edge",
      "priority": "High | Medium | Low",
      "preconditions": "roles, data and state needed",
      "steps": ["step 1", "step 2"],
      "test_data": "values used",
      "expected": "expected result",
      "acceptance_criterion": "Given ..., When ..., Then ...",
      "requirement_ref": "story / requirement id if known"
    }}
  ]
}}"""
    data = _ai_json(prompt, _TC_SYSTEM, max_tokens=7000)
    cases = data.get("test_cases") or data.get("items") or []
    clean = []
    for i, tc in enumerate(cases if isinstance(cases, list) else [], 1):
        if not isinstance(tc, dict):
            continue
        t = str(tc.get("type", "positive")).lower()
        t = "negative" if "neg" in t else "edge" if "edge" in t or "boundary" in t else "positive"
        clean.append({
            "id": dr.s(tc.get("id")) or f"TC-{i:03d}",
            "title": dr.s(tc.get("title")) or f"Test case {i}",
            "type": t,
            "priority": dr.s(tc.get("priority")) or "Medium",
            "preconditions": dr.s(tc.get("preconditions")),
            "steps": dr.as_list(tc.get("steps")),
            "test_data": dr.s(tc.get("test_data")),
            "expected": dr.s(tc.get("expected") or tc.get("expected_result")),
            "acceptance_criterion": dr.s(tc.get("acceptance_criterion") or tc.get("pass_fail_criteria")),
            "requirement_ref": dr.s(tc.get("requirement_ref")),
        })
    if not clean:
        raise HTTPException(status_code=502, detail="AI did not return any test cases, please retry")
    counts = Counter(tc["type"] for tc in clean)
    return {
        "success": True,
        "project_name": req.project_name,
        "feature_title": req.project_name,
        "doc_type": req.doc_type,
        "test_cases": clean,
        "total_count": len(clean),
        "counts": dict(counts),
        "message": f"{len(clean)} test cases generated",
    }


def _build_test_case_docx(result: dict, title: str) -> str:
    doc = dr.new_document()
    cases = result.get("test_cases", [])
    dr.title_page(doc, "Test Case Specification", title,
                  f"{len(cases)} test cases | Generated by SoftProdigy Snow Agent", page_break=False)
    counts = Counter(tc.get("type", "") for tc in cases)
    dr.heading(doc, "Summary", 1)
    dr.table(doc, ["Total", "Positive", "Negative", "Edge"],
             [[len(cases), counts.get("positive", 0), counts.get("negative", 0), counts.get("edge", 0)]])
    dr.heading(doc, "Test Case Overview", 1)
    dr.table(doc, ["ID", "Title", "Type", "Priority", "Acceptance criterion"],
             [[tc.get("id"), tc.get("title"), tc.get("type"), tc.get("priority"), tc.get("acceptance_criterion")] for tc in cases],
             widths=[0.7, 2.0, 0.8, 0.7, 2.6])
    dr.heading(doc, "Test Case Details", 1)
    for tc in cases:
        dr.heading(doc, f"{tc.get('id')} — {tc.get('title')}", 2, color=None)
        dr.table(doc, ["Field", "Value"], [
            ["Type / Priority", f"{tc.get('type', '')} / {tc.get('priority', '')}"],
            ["Requirement", tc.get("requirement_ref", "")],
            ["Preconditions", tc.get("preconditions", "")],
            ["Test steps", [f"{n}. {st}" for n, st in enumerate(tc.get("steps", []), 1)]],
            ["Test data", tc.get("test_data", "")],
            ["Expected result", tc.get("expected", "")],
            ["Acceptance criterion", tc.get("acceptance_criterion", "")],
            ["Actual result", ""],
            ["Status (Pass/Fail)", ""],
        ], widths=[1.6, 5.0])
    path = os.path.join(tempfile.gettempdir(), f"testcases_{uuid.uuid4().hex}.docx")
    doc.save(path)
    return path


@snow_interactive_router.post("/testcases/download")
def download_test_cases(req: TestCaseDownload):
    """Download the SAME test cases that were shown on screen as a Word document."""
    result = req.test_cases
    if isinstance(result, list):
        result = {"test_cases": result}
    if not (isinstance(result, dict) and result.get("test_cases")):
        if not req.requirements:
            raise HTTPException(status_code=400, detail="Pass the generated test_cases (or requirements to generate them)")
        result = generate_test_cases(TestCaseGenerate(requirements=req.requirements,
                                                      project_name=req.feature_title or req.project_name or "Feature"))
    title = req.feature_title or result.get("feature_title") or req.project_name or "Test Cases"
    path = _build_test_case_docx(result, title)
    return FileResponse(path, filename=f"TestCases_{dr.safe_filename(title)}.docx",
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ─────────────────────────── Process flow ───────────────────────────

@snow_interactive_router.post("/processflow/generate")
def generate_process_flow(req: ProcessFlowGenerate):
    prompt = f"""Design the end-to-end ServiceNow process flow for:

PROCESS NAME: {req.process_name}
DESCRIPTION:
{req.description[:8000]}

Rules:
- 6 to 15 steps in execution order. First step type "start", last step type "end".
- Use type "decision" for every approval / yes-no branch, "task" for everything else.
- Name the actor (role) and the ServiceNow component (table, flow, approval, notification) for each step.
- mermaid_code must be a valid Mermaid "flowchart TD" diagram of the same steps, with labelled Yes/No branches.

Return JSON:
{{
  "process_name": "{req.process_name}",
  "narrative": "3-5 sentence description of the flow",
  "steps": [
    {{"name": "short step name", "type": "start | task | decision | end", "actor": "role",
      "system": "ServiceNow component", "description": "what happens"}}
  ],
  "actors": ["role 1", "role 2"],
  "mermaid_code": "flowchart TD\\n  A([Start]) --> B{{Decision}}"
}}"""
    data = _ai_json(prompt, "You are a ServiceNow process architect.", max_tokens=5000)
    steps = []
    for st in data.get("steps") or []:
        if isinstance(st, dict):
            t = str(st.get("type", "task")).lower()
            t = t if t in ("start", "task", "decision", "end") else "task"
            steps.append({"name": dr.s(st.get("name")), "type": t, "actor": dr.s(st.get("actor")),
                          "system": dr.s(st.get("system")), "description": dr.s(st.get("description"))})
        elif st:
            steps.append({"name": dr.s(st), "type": "task", "actor": "", "system": "", "description": ""})
    mermaid = dr.s(data.get("mermaid_code")).strip()
    if mermaid.startswith("```"):
        mermaid = mermaid.strip("`").removeprefix("mermaid").strip()
    if not steps:
        raise HTTPException(status_code=502, detail="AI did not return process steps, please retry")
    return {
        "success": True,
        "process_name": dr.s(data.get("process_name")) or req.process_name,
        "narrative": dr.s(data.get("narrative")),
        "steps": steps,
        "steps_count": len(steps),
        "decision_points": sum(1 for x in steps if x["type"] == "decision"),
        "actors": dr.as_list(data.get("actors")),
        "mermaid_code": mermaid,
        "message": "Process flow generated",
    }


@snow_interactive_router.post("/processflow/download")
def download_process_flow(req: ProcessFlowDownload):
    """Download the SAME process flow that was shown on screen (steps + Mermaid code) as text."""
    pf = req.process_flow
    if not (pf and pf.get("steps")):
        if not req.description:
            raise HTTPException(status_code=400, detail="Pass the generated process_flow (or a description)")
        pf = generate_process_flow(ProcessFlowGenerate(description=req.description,
                                                       process_name=req.process_name or "ServiceNow Process"))
    name = pf.get("process_name") or req.process_name or "Process"
    lines = [f"PROCESS FLOW - {name}", "=" * 60, ""]
    if pf.get("narrative"):
        lines += [pf["narrative"], ""]
    lines += ["STEPS", "-" * 60]
    for i, st in enumerate(pf.get("steps", []), 1):
        lines.append(f"{i}. [{st.get('type', 'task').upper()}] {st.get('name', '')}")
        extra = " | ".join(x for x in (st.get("actor"), st.get("system")) if x)
        if extra:
            lines.append(f"   Actor/System: {extra}")
        if st.get("description"):
            lines.append(f"   {st['description']}")
    if pf.get("actors"):
        lines += ["", "ACTORS: " + ", ".join(pf["actors"])]
    lines += ["", "MERMAID DIAGRAM (paste into https://mermaid.live to view)", "-" * 60, pf.get("mermaid_code", "")]
    body = "\n".join(lines)
    return StreamingResponse(
        iter([body.encode("utf-8")]), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=ProcessFlow_{dr.safe_filename(name)}.txt"},
    )


# ─────────────────────────── Stories ───────────────────────────

@snow_interactive_router.post("/stories/create")
def create_story_in_snow(req: StoryCreate):
    _need_creds(req)
    data = {
        "short_description": req.short_description,
        "acceptance_criteria": req.acceptance_criteria or "",
        "state": req.state or "1",
        "story_points": req.story_points or 5,
    }
    if req.description:
        data["description"] = req.description
    if req.assigned_to:
        data["assigned_to"] = req.assigned_to
    result = snow_post(req.instance_url, req.username, req.password, "rm_story", data,
                       update_set_name=req.update_set_name)
    number = _plain(result, "number")
    return {"success": True, "status": "created", "message": f"Story created: {number or 'N/A'}",
            "number": number, "story_number": number, "sys_id": _plain(result, "sys_id")}


@snow_interactive_router.post("/stories/list")
def list_stories(req: StoryList):
    _need_creds(req)
    params = {
        "sysparm_limit": req.limit or 10,
        "sysparm_display_value": "all",
        "sysparm_fields": "number,short_description,state,story_points,assigned_to,acceptance_criteria,sys_created_on,sys_id",
    }
    q = []
    if req.state:
        q.append(f"state={req.state}")
    if req.query:
        q.append(req.query if ("=" in req.query or "^" in req.query) else f"short_descriptionLIKE{req.query}")
    q.append("ORDERBYDESCsys_created_on")
    params["sysparm_query"] = "^".join(q)
    stories = _with_labels(snow_get(req.instance_url, req.username, req.password, "rm_story", params))
    return {"success": True, "stories": stories, "count": len(stories), "message": f"{len(stories)} story(ies) found"}


# ─────────────────────────── Change requests ───────────────────────────

_RISK = {"very high": "1", "high": "2", "medium": "3", "moderate": "3", "low": "4"}


@snow_interactive_router.post("/changes/create")
def create_change_request(req: ChangeRequestCreate):
    _need_creds(req)
    risk = _RISK.get(str(req.risk or "").lower(), req.risk or "3")
    data = {"short_description": req.short_description, "type": (req.type or "normal").lower(),
            "risk": risk, "priority": req.priority or "3"}
    optional = {"description": req.description, "assignment_group": req.assignment_group,
                "start_date": (req.planned_start or "").replace("T", " "), "end_date": (req.planned_end or "").replace("T", " "),
                "implementation_plan": req.implementation_plan}
    data.update({k: v for k, v in optional.items() if v})
    result = snow_post(req.instance_url, req.username, req.password, "change_request", data,
                       update_set_name=req.update_set_name)
    number = _plain(result, "number")
    return {"success": True, "status": "created", "message": f"Change request created: {number or 'N/A'}",
            "number": number, "change_number": number, "sys_id": _plain(result, "sys_id")}


@snow_interactive_router.post("/changes/list")
def list_changes(req: ChangeList):
    _need_creds(req)
    params = {
        "sysparm_limit": req.limit or 10,
        "sysparm_display_value": "all",
        "sysparm_fields": "number,short_description,state,type,risk,priority,assigned_to,sys_created_on,sys_id",
    }
    q = []
    if req.query:
        q.append(req.query if ("=" in req.query or "^" in req.query) else f"short_descriptionLIKE{req.query}")
    q.append("ORDERBYDESCsys_created_on")
    params["sysparm_query"] = "^".join(q)
    changes = _with_labels(snow_get(req.instance_url, req.username, req.password, "change_request", params))
    return {"success": True, "changes": changes, "count": len(changes), "message": f"{len(changes)} change(s) found"}


# ─────────────────────────── Requirement classification ───────────────────────────

@snow_interactive_router.post("/requirements/classify")
def classify_requirements(req: RequirementClassify):
    prompt = f"""Classify each requirement below for a ServiceNow implementation of project "{req.project_name}".
Treat each line / sentence / bullet as one requirement. Do not merge or drop requirements.

REQUIREMENTS:
{req.requirements[:8000]}

For each requirement give:
- module: the ServiceNow product/module (ITSM, Service Catalog, CMDB, HRSD, CSM, SecOps, ITOM, Platform, Integration Hub, Reporting ...)
- type: "Functional" or "Non-Functional"
- priority: High | Medium | Low
- complexity: High | Medium | Low
- servicenow_artifacts: the artifacts needed (e.g. "Business Rule, Notification, SLA Definition")
- rationale: one short sentence

Return JSON:
{{"classifications": [{{"requirement": "text", "module": "", "type": "", "priority": "", "complexity": "",
  "servicenow_artifacts": "", "rationale": ""}}]}}"""
    data = _ai_json(prompt, "You are a ServiceNow requirements analyst.", max_tokens=5000)
    items = []
    for c in data.get("classifications") or data.get("items") or []:
        if isinstance(c, dict) and c.get("requirement"):
            items.append({k: dr.s(c.get(k)) for k in ("requirement", "module", "type", "priority", "complexity",
                                                        "servicenow_artifacts", "rationale")})
    if not items:
        raise HTTPException(status_code=502, detail="AI did not return a classification, please retry")
    summary = dict(Counter(i["module"] or "Other" for i in items))
    return {
        "success": True,
        "project_name": req.project_name,
        "classifications": items,
        "summary": summary,
        "total_count": len(items),
        "by_type": dict(Counter(i["type"] for i in items)),
        "by_priority": dict(Counter(i["priority"] for i in items)),
        "message": f"{len(items)} requirements classified",
    }


# ─────────────────────────── Natural-language query ───────────────────────────

class NLQuery(SnowAuth):
    question: str
    conversation_history: Optional[list] = []


_QUERY_TABLES = {"incident", "problem", "change_request", "change_task", "problem_task", "task", "sc_request",
                 "sc_req_item", "sc_task", "rm_story", "rm_epic", "kb_knowledge", "sys_user", "sys_user_group",
                 "sysapproval_approver", "task_sla", "cmdb_ci", "sn_customerservice_case", "sn_hr_core_case"}


@snow_interactive_router.post("/query")
def natural_language_query(req: NLQuery):
    """
    Translate a natural-language question into a ServiceNow REST query,
    execute it against the connected instance, and summarise the real results.
    """
    _need_creds(req)

    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.strftime("%Y-%m-%d")          # e.g. 2026-09-27
    today_js  = now_utc.strftime("%Y-%m-%d 00:00:00") # ServiceNow datetime format

    # ── Step 1: AI translates the question into a structured query plan ──
    plan_system = """You are a ServiceNow REST API expert.
Convert a natural-language question into a JSON query plan to call the ServiceNow Table REST API.

Return ONLY valid JSON (no markdown, no explanation) with this exact shape:
{
  "table": "<table_name>",
  "sysparm_query": "<encoded_query_string>",
  "sysparm_fields": "<comma_separated_fields>",
  "sysparm_limit": <integer>,
  "sysparm_display_value": "true",
  "intent_summary": "<one-line description of what you are querying>"
}

Key ServiceNow tables: incident, problem, change_request, task, cmdb_ci, sc_request, sys_user
Key field names for incident: number, short_description, description, state, priority, urgency, impact, assigned_to, assignment_group, caller_id, opened_at, resolved_at, close_notes, category, sys_created_on
Priority values: 1=Critical, 2=High, 3=Moderate, 4=Low, 5=Planning
State values: 1=New, 2=In Progress, 3=On Hold, 6=Resolved, 7=Closed

ServiceNow encoded query syntax:
- AND between conditions uses ^
- priority=1 means Critical (P1)
- Today filter: sys_created_on>=javascript:gs.beginningOfToday()
- Date range: sys_created_on>=2026-09-27 00:00:00^sys_created_on<=2026-09-27 23:59:59
- State filter: state=1 (New), state=2 (In Progress)
- Order by: ORDERBY<field> or ORDERBYDESC<field>
- Example for "P1 incidents created today": priority=1^sys_created_on>=javascript:gs.beginningOfToday()

Always include: number, short_description, state, priority, assigned_to, assignment_group, sys_created_on in fields.
Add extra fields relevant to the question. Limit 50 unless user asks for more."""

    plan_prompt = f"""Today's date (UTC): {today_str}
Today datetime for ServiceNow queries: {today_js}

User question: {req.question}

Return the JSON query plan."""

    try:
        plan = _ai_json(plan_prompt, plan_system, max_tokens=1000)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"AI query planning failed: {e}")

    table = str(plan.get("table", "incident")).strip()
    if table not in _QUERY_TABLES and not table.startswith("cmdb_ci"):
        raise HTTPException(status_code=400, detail=f"Natural-language queries are limited to operational tables; "
                                                    f"the AI chose '{table}'. Rephrase the question.")
    query_str = plan.get("sysparm_query", "")
    fields = plan.get("sysparm_fields", "number,short_description,state,priority,assigned_to,assignment_group,sys_created_on")
    limit = min(int(plan.get("sysparm_limit", 25)), 100)
    intent_summary = plan.get("intent_summary", req.question)

    # ── Step 2: Execute the real ServiceNow query ──
    params = {
        "sysparm_query": query_str,
        "sysparm_fields": fields,
        "sysparm_limit": limit,
        "sysparm_display_value": "true",
    }
    try:
        raw_rows = snow_get(req.instance_url, req.username, req.password, table, params)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ServiceNow query failed: {e}")

    rows = _with_labels(raw_rows)

    # ── Step 3: AI summarises the real results ──
    rows_text = json.dumps(rows[:50], indent=2) if rows else "[]"

    summary_system = """You are SoftProdigy Snow, a ServiceNow AI assistant.
You have just queried the user's live ServiceNow instance. Summarise the results clearly and concisely.
- Start with a headline: total count and what was found
- Group or highlight key patterns (e.g. by priority, state, assignment group)
- Call out anything critical or urgent
- List individual records in a clean table format if there are ≤15 results
- For each record include: Number, Description, Priority/State, Assigned To
- End with 1-2 actionable recommendations if the data warrants it
- Use plain text with light markdown (bold, bullets). No JSON, no code blocks."""

    summary_prompt = f"""User asked: "{req.question}"
Query executed on table '{table}' with filter: {query_str}
Instance: {req.instance_url}
Results ({len(rows)} records found):

{rows_text}

Provide a clear summary answer to the user's question based on these real results."""

    try:
        summary_text = groq_client.chat(
            [{"role": "system", "content": summary_system},
             {"role": "user", "content": summary_prompt}],
            max_tokens=2000,
            temperature=0.3,
        )
    except Exception as e:
        # Fallback: build summary manually
        summary_text = f"Found **{len(rows)}** records in `{table}`.\n\n"
        for r in rows[:20]:
            num = r.get("number", r.get("sys_id", ""))
            desc = r.get("short_description", "")[:80]
            pri = r.get("priority_label") or r.get("priority", "")
            state = r.get("state_label") or r.get("state", "")
            ag = r.get("assignment_group", "")
            summary_text += f"• **{num}** — {desc} | {pri} | {state} | {ag}\n"

    return {
        "success": True,
        "question": req.question,
        "intent_summary": intent_summary,
        "table": table,
        "query": query_str,
        "total_found": len(rows),
        "records": rows,
        "summary": summary_text,
    }


# ─────────────────────────── AI chat ───────────────────────────

@snow_interactive_router.post("/chat")
def interactive_chat(req: ChatMessage):
    """Conversational ServiceNow assistant (answers questions; does not change the instance)."""
    system_prompt = f"""You are SoftProdigy Snow, an expert ServiceNow assistant.
You answer questions about ServiceNow configuration, ITSM processes, scripting (GlideRecord, Business Rules,
Client Scripts, Flow Designer), and best practices. The user's instance is: {req.instance_url or 'not connected'}.
Be specific and practical. Use short markdown sections and code blocks where useful.
You cannot change the instance from this chat; if the user asks you to create or update records, tell them which
screen of the agent to use (Incidents, Stories, Changes, Implement)."""
    messages = [{"role": "system", "content": system_prompt}]
    for msg in (req.conversation_history or [])[-10:]:
        if isinstance(msg, dict) and msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": str(msg["content"])})
    messages.append({"role": "user", "content": req.message})
    try:
        response_text = groq_client.chat(messages, max_tokens=2000, temperature=0.5)
    except groq_client.GroqError as e:
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {e}")
    return {"success": True, "response": response_text, "message": "Chat response generated"}


# ─────────────────────────── Connection check ───────────────────────────

@snow_interactive_router.post("/health")
def snow_health_check(req: SnowAuth):
    """Check ServiceNow instance connectivity and return a few recent incidents."""
    _need_creds(req)
    try:
        resp = requests.get(
            f"{req.instance_url.rstrip('/')}/api/now/table/incident",
            headers=get_snow_headers(req.username, req.password),
            params={"sysparm_limit": 5, "sysparm_fields": "number,state,short_description", "sysparm_display_value": "true"},
            timeout=15,
            verify=_VERIFY,
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to ServiceNow instance: {e}")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Authentication failed — check username/password")
    _raise_for_snow(resp, "connection check")
    return {
        "success": True,
        "instance_url": req.instance_url,
        "status": "connected",
        "recent_incidents": resp.json().get("result", []),
        "message": f"Successfully connected to {req.instance_url}",
    }


# ═══════════════════════════ v6 additions ═══════════════════════════
# Problems, knowledge, approvals, service catalog and ATF — parity with the
# SoftProdigy platform MCP packages (service_desk / problem_manager / knowledge_author /
# approval_manager / catalog / ATF) for the most-used operations.

_IU = {"critical": ("1", "1"), "high": ("1", "2"), "medium": ("2", "2"), "low": ("3", "3")}


def _iu(priority: Optional[str]):
    return _IU.get(str(priority or "medium").lower(), _IU["medium"])


# ── Problems ──────────────────────────────────────────────────────────

class ProblemCreate(SnowAuth):
    short_description: str
    description: Optional[str] = None
    priority: Optional[str] = "medium"            # critical | high | medium | low
    assignment_group: Optional[str] = None
    related_incidents: Optional[List[str]] = None  # incident numbers to link
    update_set_name: Optional[str] = None   # capture in this update set (auto-picks if omitted)


class ProblemUpdate(SnowAuth):
    problem_id: str = Field(validation_alias=AliasChoices("problem_id", "number", "sys_id"))
    work_notes: Optional[str] = None
    state: Optional[str] = None
    cause_notes: Optional[str] = None
    fix_notes: Optional[str] = None
    workaround: Optional[str] = None
    known_error: Optional[bool] = None


@snow_interactive_router.post("/problems/create")
def create_problem(req: ProblemCreate):
    _need_creds(req)
    impact, urgency = _iu(req.priority)
    data = {"short_description": req.short_description, "impact": impact, "urgency": urgency}
    if req.description:
        data["description"] = req.description
    if req.assignment_group:
        data["assignment_group"] = req.assignment_group
    result = snow_post(req.instance_url, req.username, req.password, "problem", data,
                       update_set_name=req.update_set_name)
    number, sys_id = _plain(result, "number"), _plain(result, "sys_id")
    linked, not_found = [], []
    for inc in req.related_incidents or []:
        rows = snow_get(req.instance_url, req.username, req.password, "incident",
                        {"sysparm_query": f"number={inc}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
        if rows:
            snow_patch(req.instance_url, req.username, req.password, "incident", rows[0]["sys_id"],
                       {"problem_id": sys_id}, update_set_name=req.update_set_name)
            linked.append(inc)
        else:
            not_found.append(inc)
    return {"success": True, "status": "created", "number": number, "sys_id": sys_id,
            "linked_incidents": linked, "incidents_not_found": not_found,
            "message": f"Problem created: {number}" + (f" (linked {len(linked)} incidents)" if linked else "")}


@snow_interactive_router.post("/problems/list")
def list_problems(req: IncidentList):
    _need_creds(req)
    q = [f"state={req.state}"] if req.state else ["active=true"]
    if req.query:
        q.append(req.query if ("=" in req.query or "^" in req.query) else f"short_descriptionLIKE{req.query}")
    q.append("ORDERBYDESCsys_created_on")
    rows = _with_labels(snow_get(req.instance_url, req.username, req.password, "problem", {
        "sysparm_limit": req.limit or 10, "sysparm_display_value": "all", "sysparm_query": "^".join(q),
        "sysparm_fields": "number,short_description,state,priority,assigned_to,assignment_group,known_error,sys_id"}))
    return {"success": True, "problems": rows, "count": len(rows)}


@snow_interactive_router.post("/problems/update")
def update_problem(req: ProblemUpdate):
    _need_creds(req)
    sys_id = _lookup_sys_id(req, "problem", req.problem_id.strip())
    data = {k: getattr(req, k) for k in ("work_notes", "state", "cause_notes", "fix_notes", "workaround")
            if getattr(req, k) not in (None, "")}
    if req.known_error is not None:
        data["known_error"] = req.known_error
    if not data:
        raise HTTPException(status_code=400, detail="Nothing to update")
    result = snow_patch(req.instance_url, req.username, req.password, "problem", sys_id, data)
    return {"success": True, "status": "updated", "number": _plain(result, "number"), "sys_id": sys_id}


# ── Knowledge ─────────────────────────────────────────────────────────

class KBCreate(SnowAuth):
    short_description: str
    text: Optional[str] = None                   # HTML or plain text
    knowledge_base: Optional[str] = None         # title or sys_id; default: first active KB named "IT"
    generate_from: Optional[str] = None          # e.g. resolution notes -> AI writes the article
    publish: Optional[bool] = False
    update_set_name: Optional[str] = None   # capture in this update set (auto-picks if omitted)


class KBSearch(SnowAuth):
    query: str
    limit: Optional[int] = 10


@snow_interactive_router.post("/knowledge/create")
def create_kb_article(req: KBCreate):
    _need_creds(req)
    kb_q = (f"sys_id={req.knowledge_base}" if req.knowledge_base and _is_sys_id(req.knowledge_base)
            else f"active=true^title={req.knowledge_base}" if req.knowledge_base else "active=true^titleSTARTSWITHIT")
    kbs = snow_get(req.instance_url, req.username, req.password, "kb_knowledge_base",
                   {"sysparm_query": kb_q, "sysparm_fields": "sys_id,title", "sysparm_limit": 1}) or \
        snow_get(req.instance_url, req.username, req.password, "kb_knowledge_base",
                 {"sysparm_query": "active=true", "sysparm_fields": "sys_id,title", "sysparm_limit": 1})
    if not kbs:
        raise HTTPException(status_code=404, detail="No active knowledge base found")
    body = req.text or ""
    if not body and req.generate_from:
        body = groq_client.generate(
            f"Write a ServiceNow knowledge article titled '{req.short_description}' from these notes. Use short "
            f"HTML (<h3>, <p>, <ol>): Symptoms, Cause, Resolution steps. Do not invent facts.\n\n{req.generate_from[:6000]}",
            max_tokens=1500)
    if not body:
        raise HTTPException(status_code=400, detail="Provide text or generate_from")
    data = {"short_description": req.short_description, "text": body, "kb_knowledge_base": kbs[0]["sys_id"],
            "workflow_state": "published" if req.publish else "draft"}
    result = snow_post(req.instance_url, req.username, req.password, "kb_knowledge", data,
                       update_set_name=req.update_set_name)
    return {"success": True, "number": _plain(result, "number"), "sys_id": _plain(result, "sys_id"),
            "knowledge_base": kbs[0].get("title"), "state": data["workflow_state"]}


@snow_interactive_router.post("/knowledge/search")
def search_kb(req: KBSearch):
    _need_creds(req)
    rows = snow_get(req.instance_url, req.username, req.password, "kb_knowledge", {
        "sysparm_query": f"workflow_state=published^123TEXTQUERY321={req.query}",
        "sysparm_fields": "number,short_description,sys_id,kb_knowledge_base",
        "sysparm_display_value": "true", "sysparm_limit": req.limit or 10})
    return {"success": True, "articles": rows, "count": len(rows)}


# ── Approvals ─────────────────────────────────────────────────────────

class ApprovalList(SnowAuth):
    mine_only: Optional[bool] = True
    limit: Optional[int] = 20


class ApprovalAct(SnowAuth):
    approval_sys_id: str
    comments: Optional[str] = None


@snow_interactive_router.post("/approvals/list")
def list_approvals(req: ApprovalList):
    _need_creds(req)
    q = "state=requested"
    if req.mine_only:
        me = snow_get(req.instance_url, req.username, req.password, "sys_user",
                      {"sysparm_query": f"user_name={req.username}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
        if me:
            q += f"^approver={me[0]['sys_id']}"
    rows = snow_get(req.instance_url, req.username, req.password, "sysapproval_approver", {
        "sysparm_query": q + "^ORDERBYDESCsys_created_on", "sysparm_display_value": "true",
        "sysparm_fields": "sys_id,approver,sysapproval,state,sys_created_on,due_date", "sysparm_limit": req.limit or 20})
    return {"success": True, "approvals": rows, "count": len(rows)}


def _act(req: ApprovalAct, state: str):
    _need_creds(req)
    data = {"state": state}
    if req.comments:
        data["comments"] = req.comments
    result = snow_patch(req.instance_url, req.username, req.password, "sysapproval_approver", req.approval_sys_id, data)
    return {"success": True, "state": _plain(result, "state") or state, "sys_id": req.approval_sys_id}


@snow_interactive_router.post("/approvals/approve")
def approve(req: ApprovalAct):
    return _act(req, "approved")


@snow_interactive_router.post("/approvals/reject")
def reject(req: ApprovalAct):
    if not req.comments:
        raise HTTPException(status_code=400, detail="A comment is required when rejecting")
    return _act(req, "rejected")


# ── Service catalog ───────────────────────────────────────────────────

class CatalogSearch(SnowAuth):
    query: Optional[str] = ""
    limit: Optional[int] = 20


class CatalogOrder(SnowAuth):
    item_sys_id: str
    quantity: Optional[int] = 1
    variables: Optional[Dict[str, Any]] = None
    requested_for: Optional[str] = None


@snow_interactive_router.post("/catalog/items")
def catalog_items(req: CatalogSearch):
    _need_creds(req)
    q = "active=true" + (f"^nameLIKE{req.query}^ORshort_descriptionLIKE{req.query}" if req.query else "")
    rows = snow_get(req.instance_url, req.username, req.password, "sc_cat_item", {
        "sysparm_query": q + "^ORDERBYname", "sysparm_fields": "sys_id,name,short_description,category",
        "sysparm_display_value": "true", "sysparm_limit": req.limit or 20})
    return {"success": True, "items": rows, "count": len(rows)}


@snow_interactive_router.post("/catalog/order")
def catalog_order(req: CatalogOrder):
    """Order a catalog item through the Service Catalog API (runs the item's real fulfilment flow)."""
    _need_creds(req)
    body = {"sysparm_quantity": str(req.quantity or 1), "variables": req.variables or {}}
    if req.requested_for:
        body["sysparm_requested_for"] = req.requested_for
    url = f"{req.instance_url.rstrip('/')}/api/sn_sc/servicecatalog/items/{req.item_sys_id}/order_now"
    resp = requests.post(url, headers=get_snow_headers(req.username, req.password), json=body, timeout=60,
                         verify=_VERIFY)
    _raise_for_snow(resp, "catalog order")
    r = resp.json().get("result", {})
    return {"success": True, "request_number": r.get("request_number"), "request_id": r.get("request_id"),
            "message": f"Ordered — {r.get('request_number')}"}


# ── ATF (requires the CI/CD API: sn_cicd, available on most instances) ────────

class ATFRun(SnowAuth):
    suite: str                                    # suite sys_id or name


class ATFProgress(SnowAuth):
    progress_id: str


@snow_interactive_router.post("/atf/suites")
def atf_suites(req: SnowAuth):
    _need_creds(req)
    rows = snow_get(req.instance_url, req.username, req.password, "sys_atf_test_suite", {
        "sysparm_query": "active=true^ORDERBYname", "sysparm_fields": "sys_id,name,description",
        "sysparm_limit": 100})
    return {"success": True, "suites": rows, "count": len(rows)}


@snow_interactive_router.post("/atf/run")
def atf_run(req: ATFRun):
    _need_creds(req)
    sid = req.suite if _is_sys_id(req.suite) else None
    if not sid:
        rows = snow_get(req.instance_url, req.username, req.password, "sys_atf_test_suite",
                        {"sysparm_query": f"name={req.suite}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
        if not rows:
            raise HTTPException(status_code=404, detail=f"ATF suite '{req.suite}' not found")
        sid = rows[0]["sys_id"]
    url = f"{req.instance_url.rstrip('/')}/api/sn_cicd/testsuite/run"
    resp = requests.post(url, headers=get_snow_headers(req.username, req.password),
                         params={"test_suite_sys_id": sid}, timeout=60, verify=_VERIFY)
    _raise_for_snow(resp, "ATF run (needs the sn_cicd role / CI/CD API)")
    r = resp.json().get("result", {})
    return {"success": True, "progress_id": (r.get("links", {}).get("progress", {}) or {}).get("id"),
            "status": r.get("status_label"), "suite_sys_id": sid}


@snow_interactive_router.post("/atf/progress")
def atf_progress(req: ATFProgress):
    _need_creds(req)
    base = req.instance_url.rstrip("/")
    resp = requests.get(f"{base}/api/sn_cicd/progress/{req.progress_id}",
                        headers=get_snow_headers(req.username, req.password), timeout=30, verify=_VERIFY)
    _raise_for_snow(resp, "ATF progress")
    r = resp.json().get("result", {})
    out = {"success": True, "status": r.get("status_label"), "percent": r.get("percent_complete"),
           "message": r.get("status_message")}
    res_id = (r.get("links", {}).get("results", {}) or {}).get("id")
    if res_id and str(r.get("status")) in ("2", "3", "4"):
        rr = requests.get(f"{base}/api/sn_cicd/testsuite/results/{res_id}",
                          headers=get_snow_headers(req.username, req.password), timeout=30, verify=_VERIFY)
        if rr.ok:
            out["results"] = rr.json().get("result", {})
    return out


# ─────────────────────────── Business Rule Generator ───────────────────────────

class BusinessRuleCreate(SnowAuth):
    description: str = Field(..., description="Natural language description of the business rule to create")
    table: Optional[str] = Field(None, description="ServiceNow table the rule applies to (e.g. 'incident')")
    when: Optional[str] = Field("before", description="When to run: before, after, async, display")
    operation: Optional[str] = Field("insert,update", description="Operation: insert, update, delete, or combination")
    update_set_name: Optional[str] = None


@snow_interactive_router.post("/business_rule/generate")
def generate_business_rule(req: BusinessRuleCreate):
    """
    Generate a ServiceNow Business Rule script from a natural language description,
    then create it on the connected ServiceNow instance via the Table API.
    Business rules ARE configuration items and WILL be captured in update sets.
    """
    _need_creds(req)

    # Step 1: Use Groq AI to generate the business rule script
    table_hint = req.table or "incident"
    when_hint = req.when or "before"
    op_hint = req.operation or "insert,update"

    system_prompt = """You are a ServiceNow Business Rule expert. Generate a complete, working
ServiceNow Business Rule script in GlideScript (Server-Side JavaScript / ES5 compatible).

Rules for your output:
1. Output ONLY a JSON object with these exact keys:
   - "name": string — short descriptive name for the business rule
   - "table": string — ServiceNow table name (e.g. "incident", "change_request", "sys_user")
   - "when": string — one of: "before", "after", "async", "display"
   - "insert": boolean — true if rule fires on insert
   - "update": boolean — true if rule fires on update
   - "delete": boolean — true if rule fires on delete
   - "query": boolean — true if rule fires on query (display rules only)
   - "condition": string — optional GlideScript condition (empty string if always runs)
   - "script": string — the complete GlideScript code
   - "description": string — what the rule does (1-2 sentences)

2. Script must be wrapped in: (function executeBusinessRule(current, previous) { ... })(current, previous);
3. Use GlideRecord, GlideSystem (gs), current, previous — standard server-side API
4. Follow ES5 (no const/let/arrow functions unless using ES2021 mode)
5. Include error handling with try/catch
6. Add clear comments explaining each section
7. The script must be production-ready and free of hallucinations

Output ONLY the JSON object, no markdown, no explanation."""

    user_prompt = f"""Create a ServiceNow Business Rule for this requirement:

{req.description}

Suggested table: {table_hint}
Suggested when: {when_hint}
Suggested operations: {op_hint}

Generate the complete business rule JSON now."""

    raw = ""
    try:
        raw = groq_client.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=3000,
            json_mode=True,
        )
        # Strip markdown code blocks if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```", 2)[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.rsplit("```", 1)[0].strip()
        br_data = json.loads(clean)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"AI failed to generate valid JSON for business rule: {str(e)}\nRaw: {raw[:500]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI generation error: {str(e)}")

    # Step 2: Resolve update set if specified
    base = req.instance_url.rstrip("/")
    headers = get_snow_headers(req.username, req.password)
    update_set_sys_id = _resolve_update_set_sys_id(base, req.username, req.password, req.update_set_name)

    write_headers = dict(headers)
    if update_set_sys_id:
        write_headers["X-Update-Set-Sys-Id"] = update_set_sys_id

    # Step 3: Build the ServiceNow Business Rule payload
    # Map operations to boolean strings
    ops = [o.strip().lower() for o in (br_data.get("when", "insert,update") if "," in op_hint else op_hint).split(",")]

    snow_payload = {
        "name": br_data.get("name", f"SoftProdigy Generated - {table_hint}"),
        "collection": br_data.get("table", table_hint),   # 'collection' = the table field in sys_script
        "when": br_data.get("when", when_hint),
        "insert": "true" if br_data.get("insert", True) else "false",
        "update": "true" if br_data.get("update", True) else "false",
        "delete": "true" if br_data.get("delete", False) else "false",
        "query": "true" if br_data.get("query", False) else "false",
        "condition": br_data.get("condition", ""),
        "script": br_data.get("script", ""),
        "active": "true",
        "advanced": "true",
    }

    # Step 4: Create the Business Rule via ServiceNow Table API (sys_script table)
    url = f"{base}/api/now/table/sys_script"
    try:
        resp = requests.post(url, headers=write_headers, json=snow_payload, timeout=60, verify=_VERIFY)
        _raise_for_snow(resp, "Business Rule creation")
        result = resp.json().get("result", {})
        sys_id = result.get("sys_id", "")
        rule_name = result.get("name", snow_payload["name"])
        table_created = result.get("collection", table_hint)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create business rule in ServiceNow: {str(e)}")

    # Step 5: Verify it was created by fetching it back
    verify_url = f"{base}/api/now/table/sys_script/{sys_id}"
    verified = False
    try:
        vr = requests.get(verify_url, headers=headers, timeout=30, verify=_VERIFY)
        if vr.ok:
            verified = True
    except Exception:
        pass

    return {
        "success": True,
        "name": rule_name,
        "sys_id": sys_id,
        "table": table_created,
        "when": snow_payload["when"],
        "script": br_data.get("script", ""),
        "description": br_data.get("description", req.description),
        "insert": snow_payload["insert"],
        "update": snow_payload["update"],
        "verified": verified,
        "update_set_sys_id": update_set_sys_id,
        "sn_url": f"{base}/sys_script.do?sys_id={sys_id}",
        "message": f"✅ Business Rule '{rule_name}' created successfully on table [{table_created}]."
                   + (f" Captured in update set." if update_set_sys_id else " (No active update set found — rule created but not captured.)")
    }


@snow_interactive_router.get("/business_rule/list")
def list_business_rules(instance_url: str, username: str, password: str,
                         table: Optional[str] = None, limit: int = 20):
    """List recent business rules, optionally filtered by table."""
    base = instance_url.rstrip("/")
    headers = get_snow_headers(username, password)
    params = {
        "sysparm_limit": limit,
        "sysparm_fields": "name,collection,when,active,sys_id,sys_updated_on",
        "sysparm_orderby": "sys_updated_on",
        "sysparm_order": "desc",
    }
    if table:
        params["sysparm_query"] = f"collection={table}"
    resp = requests.get(f"{base}/api/now/table/sys_script",
                        headers=headers, params=params, timeout=30, verify=_VERIFY)
    _raise_for_snow(resp, "Business Rule list")
    rules = resp.json().get("result", [])
    return {"success": True, "count": len(rules), "rules": rules}


# ─────────────────────────── User Management ───────────────────────────

class UserCreate(SnowAuth):
    first_name: str = Field(..., description="User's first name")
    last_name: str = Field(..., description="User's last name")
    email: str = Field(..., description="User's email address")
    user_name: Optional[str] = Field(None, description="Login username (defaults to email prefix)")
    title: Optional[str] = Field(None, description="Job title")
    department: Optional[str] = Field(None, description="Department name")
    phone: Optional[str] = Field(None, description="Phone number")
    manager: Optional[str] = Field(None, description="Manager username or sys_id")
    active: bool = Field(True, description="Whether the user is active")


class UserUpdate(SnowAuth):
    user_id: str = Field(..., description="Username, email, or sys_id of the user to update")
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    active: Optional[bool] = None
    manager: Optional[str] = None


class UserGroupAssign(SnowAuth):
    user_id: str = Field(..., description="Username, email, or sys_id of the user")
    group_name: str = Field(..., description="Group name or sys_id to assign the user to")


def _resolve_user_sys_id(instance_url: str, username: str, password: str, user_id: str) -> Optional[str]:
    """Resolve a username, email, or sys_id to a user sys_id."""
    if _is_sys_id(user_id):
        return user_id
    base = instance_url.rstrip("/")
    headers = get_snow_headers(username, password)
    # Try user_name first, then email
    for field in ("user_name", "email"):
        r = requests.get(
            f"{base}/api/now/table/sys_user",
            headers=headers,
            params={"sysparm_query": f"{field}={user_id}", "sysparm_fields": "sys_id", "sysparm_limit": 1},
            timeout=15, verify=_VERIFY
        )
        if r.ok:
            rows = r.json().get("result", [])
            if rows:
                return rows[0]["sys_id"]
    return None


@snow_interactive_router.post("/user/create")
def create_user(req: UserCreate):
    """Create a new ServiceNow user."""
    base = req.instance_url.rstrip("/")
    headers = get_snow_headers(req.username, req.password)

    # Auto-generate username from email if not provided
    user_name = req.user_name or req.email.split("@")[0]

    payload = {
        "first_name": req.first_name,
        "last_name": req.last_name,
        "email": req.email,
        "user_name": user_name,
        "active": req.active,
    }
    if req.title:
        payload["title"] = req.title
    if req.department:
        payload["department"] = req.department
    if req.phone:
        payload["phone"] = req.phone
    if req.manager:
        # Try to resolve manager sys_id
        mgr_sys_id = _resolve_user_sys_id(req.instance_url, req.username, req.password, req.manager)
        if mgr_sys_id:
            payload["manager"] = mgr_sys_id

    resp = requests.post(
        f"{base}/api/now/table/sys_user",
        headers=headers,
        json=payload,
        timeout=30, verify=_VERIFY
    )
    _raise_for_snow(resp, "User creation")
    result = resp.json().get("result", {})
    sys_id = result.get("sys_id", "")

    return {
        "success": True,
        "sys_id": sys_id,
        "user_name": result.get("user_name", user_name),
        "full_name": f"{req.first_name} {req.last_name}",
        "email": req.email,
        "active": req.active,
        "sn_url": f"{base}/sys_user.do?sys_id={sys_id}",
        "message": f"✅ User '{req.first_name} {req.last_name}' ({user_name}) created successfully."
    }


@snow_interactive_router.post("/user/update")
def update_user(req: UserUpdate):
    """Update an existing ServiceNow user."""
    sys_id = _resolve_user_sys_id(req.instance_url, req.username, req.password, req.user_id)
    if not sys_id:
        raise HTTPException(status_code=404, detail=f"User '{req.user_id}' not found in ServiceNow.")

    payload = {}
    if req.first_name is not None: payload["first_name"] = req.first_name
    if req.last_name is not None: payload["last_name"] = req.last_name
    if req.email is not None: payload["email"] = req.email
    if req.title is not None: payload["title"] = req.title
    if req.department is not None: payload["department"] = req.department
    if req.phone is not None: payload["phone"] = req.phone
    if req.active is not None: payload["active"] = req.active
    if req.manager is not None:
        mgr_sys_id = _resolve_user_sys_id(req.instance_url, req.username, req.password, req.manager)
        if mgr_sys_id:
            payload["manager"] = mgr_sys_id

    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update provided.")

    base = req.instance_url.rstrip("/")
    headers = get_snow_headers(req.username, req.password)
    resp = requests.patch(
        f"{base}/api/now/table/sys_user/{sys_id}",
        headers=headers, json=payload, timeout=30, verify=_VERIFY
    )
    _raise_for_snow(resp, "User update")
    result = resp.json().get("result", {})

    return {
        "success": True,
        "sys_id": sys_id,
        "user_name": _plain(result, "user_name"),
        "full_name": f"{_plain(result, 'first_name')} {_plain(result, 'last_name')}".strip(),
        "active": result.get("active", True),
        "sn_url": f"{base}/sys_user.do?sys_id={sys_id}",
        "message": f"✅ User updated successfully."
    }


@snow_interactive_router.post("/user/deactivate")
def deactivate_user(req: UserUpdate):
    """Deactivate (disable) a ServiceNow user."""
    req.active = False
    return update_user(req)


@snow_interactive_router.get("/user/list")
def list_users(instance_url: str, username: str, password: str,
               search: Optional[str] = None,
               department: Optional[str] = None,
               active: Optional[bool] = None,
               limit: int = 20):
    """List users, optionally filtered by name/email search, department, or active status."""
    base = instance_url.rstrip("/")
    headers = get_snow_headers(username, password)

    query_parts = []
    if search:
        query_parts.append(f"first_nameLIKE{search}^ORlast_nameLIKE{search}^ORemail STARTSWITH{search}^ORuser_nameLIKE{search}")
    if department:
        query_parts.append(f"department.name={department}")
    if active is not None:
        query_parts.append(f"active={'true' if active else 'false'}")

    params = {
        "sysparm_limit": limit,
        "sysparm_fields": "sys_id,first_name,last_name,email,user_name,title,department,active,phone,sys_created_on",
        "sysparm_orderby": "last_name",
    }
    if query_parts:
        params["sysparm_query"] = "^".join(query_parts)

    resp = requests.get(
        f"{base}/api/now/table/sys_user",
        headers=headers, params=params, timeout=30, verify=_VERIFY
    )
    _raise_for_snow(resp, "User list")
    users = resp.json().get("result", [])

    # Clean up display values
    for u in users:
        for k in ("department",):
            if isinstance(u.get(k), dict):
                u[k] = u[k].get("display_value", "")

    return {"success": True, "count": len(users), "users": users}


@snow_interactive_router.get("/user/get")
def get_user(instance_url: str, username: str, password: str, user_id: str):
    """Get a single user by username, email, or sys_id."""
    sys_id = _resolve_user_sys_id(instance_url, username, password, user_id)
    if not sys_id:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found.")

    base = instance_url.rstrip("/")
    headers = get_snow_headers(username, password)
    resp = requests.get(
        f"{base}/api/now/table/sys_user/{sys_id}",
        headers=headers,
        params={"sysparm_display_value": "true"},
        timeout=30, verify=_VERIFY
    )
    _raise_for_snow(resp, "User fetch")
    result = resp.json().get("result", {})
    return {"success": True, "user": result, "sn_url": f"{base}/sys_user.do?sys_id={sys_id}"}


@snow_interactive_router.post("/user/assign_group")
def assign_user_to_group(req: UserGroupAssign):
    """Add a user to a ServiceNow group."""
    base = req.instance_url.rstrip("/")
    headers = get_snow_headers(req.username, req.password)

    # Resolve user sys_id
    user_sys_id = _resolve_user_sys_id(req.instance_url, req.username, req.password, req.user_id)
    if not user_sys_id:
        raise HTTPException(status_code=404, detail=f"User '{req.user_id}' not found.")

    # Resolve group sys_id
    group_sys_id = None
    if _is_sys_id(req.group_name):
        group_sys_id = req.group_name
    else:
        r = requests.get(
            f"{base}/api/now/table/sys_user_group",
            headers=headers,
            params={"sysparm_query": f"name={req.group_name}", "sysparm_fields": "sys_id,name", "sysparm_limit": 1},
            timeout=15, verify=_VERIFY
        )
        if r.ok:
            rows = r.json().get("result", [])
            if rows:
                group_sys_id = rows[0]["sys_id"]
    if not group_sys_id:
        raise HTTPException(status_code=404, detail=f"Group '{req.group_name}' not found.")

    # Add to group via sys_user_grmember table
    payload = {"user": user_sys_id, "group": group_sys_id}
    resp = requests.post(
        f"{base}/api/now/table/sys_user_grmember",
        headers=headers, json=payload, timeout=30, verify=_VERIFY
    )
    _raise_for_snow(resp, "Group assignment")
    result = resp.json().get("result", {})

    return {
        "success": True,
        "member_sys_id": result.get("sys_id", ""),
        "user_id": req.user_id,
        "group": req.group_name,
        "message": f"✅ User '{req.user_id}' added to group '{req.group_name}'."
    }


@snow_interactive_router.get("/user/groups")
def list_user_groups(instance_url: str, username: str, password: str,
                     user_id: str):
    """List all groups a user belongs to."""
    base = instance_url.rstrip("/")
    headers = get_snow_headers(username, password)

    user_sys_id = _resolve_user_sys_id(instance_url, username, password, user_id)
    if not user_sys_id:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found.")

    resp = requests.get(
        f"{base}/api/now/table/sys_user_grmember",
        headers=headers,
        params={
            "sysparm_query": f"user={user_sys_id}",
            "sysparm_fields": "group.name,group.sys_id,group.description",
            "sysparm_display_value": "true",
            "sysparm_limit": 50,
        },
        timeout=30, verify=_VERIFY
    )
    _raise_for_snow(resp, "User groups")
    rows = resp.json().get("result", [])
    groups = [
        {"name": r.get("group.name", "") or (r.get("group", {}) or {}).get("display_value", ""),
         "sys_id": r.get("group.sys_id", "") or (r.get("group", {}) or {}).get("value", "")}
        for r in rows
    ]
    return {"success": True, "user_id": user_id, "count": len(groups), "groups": groups}
