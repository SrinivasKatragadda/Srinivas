# Mock of the Generic Flow Server (v6/flow-server) /deploy-flow and /deploy-subflow contract
import uuid
from fastapi import FastAPI, Request
app = FastAPI(); CALLS = []


@app.get("/health")
def health():
    return {"status": "ok", "deployInProgress": False}


@app.post("/deploy-flow")
async def deploy(req: Request):
    b = await req.json(); CALLS.append({k: v for k, v in b.items() if k != "password"})
    if not b.get("flowSpec", {}).get("actions"):
        return {"status": "validation_failed", "error": "no actions"}
    return {"status": "installed", "sys_id": uuid.uuid4().hex, "name": b["flowSpec"]["name"], "active": True}


@app.post("/deploy-subflow")
async def deploy_subflow(req: Request):
    b = await req.json(); CALLS.append({k: v for k, v in b.items() if k != "password"})
    if not b.get("subflowSpec", {}).get("actions"):
        return {"status": "validation_failed", "error": "no actions"}
    return {"status": "installed", "sys_id": uuid.uuid4().hex, "name": b["subflowSpec"]["name"], "active": True}


@app.get("/__calls")
def calls(): return CALLS
