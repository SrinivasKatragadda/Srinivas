import os

os.environ["PYTHONIOENCODING"] = "utf-8"

import logging
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# Langfuse tracing is optional: without keys, keep it off and silence its warning.
if not os.getenv("LANGFUSE_PUBLIC_KEY"):
    os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    logging.getLogger("langfuse").setLevel(logging.ERROR)

from typing import Any

from fastapi import APIRouter, FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm

from agent_security import require_api_key, allowed_origins
from snow_login import router as snow_login_router
from snow_endpoints import snow_router
from snow_universal import snow_universal_router
from snow_interactive import snow_interactive_router
from sn_tools import snow_tools_router
from snow_tools_router import router as sn_full_tools_router   # all 522 vendor tools
from acc_deploy import acc_router

# The agent can run inside the SoftProdigy platform-service folder (full platform APIs)
# or on its own. When the platform modules are missing it starts in standalone mode.
try:
    from platform_services import api_router, websocket_router, public_router
    from auth import authenticate, authenticate_user, create_access_token
    from config import settings
    from local_limiter import RateLimit
    PLATFORM = True
except ImportError as _e:  # standalone
    PLATFORM = False
    logging.getLogger(__name__).info(f"Platform modules not found ({_e}); starting the Snow agent standalone")

    class _Settings:
        PROJECT_NAME = "SoftProdigy Snow Agent"
        API_V1_STR = "/api/v1"
    settings = _Settings()

app = FastAPI(
    title=settings.PROJECT_NAME,
    root_path=os.getenv("AGENT_ROOT_PATH", "/fastapi" if PLATFORM else ""),
    openapi_tags=[{"name": "Platform Services", "description": "SoftProdigy Platform APIs"}],
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
)

# CORS — explicit origins (AGENT_ALLOWED_ORIGINS), localhost dev ports by default. No credentials/cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Session-ID"],
)

# ---------------- Public routes ----------------

# /ui/ — serves the SoftProdigy Snow Agent UI (public, no auth)
@app.get("/ui", include_in_schema=False)
@app.get("/ui/", include_in_schema=False)
def serve_ui():
    """Serve the SoftProdigy Snow Agent demo UI."""
    # Try to serve actual UI file if it exists
    ui_candidates = [
        Path(__file__).resolve().parent.parent / "snow-ui" / "index.html",
        Path(__file__).resolve().parent / "snow-ui" / "index.html",
    ]
    for p in ui_candidates:
        if p.exists():
            return HTMLResponse(content=p.read_text(encoding="utf-8"))
    # Fallback: built-in UI page
    body = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SoftProdigy Snow Agent</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f0f4f8; color: #1a202c; }
    header { background: #1a73e8; color: white; padding: 16px 24px;
             display: flex; align-items: center; gap: 12px; }
    header h1 { font-size: 1.4rem; font-weight: 700; }
    header span { font-size: 0.85rem; opacity: 0.85; }
    .hero { background: linear-gradient(135deg, #1a73e8 0%, #0d47a1 100%);
            color: white; padding: 48px 24px; text-align: center; }
    .hero h2 { font-size: 2rem; margin-bottom: 12px; }
    .hero p  { font-size: 1.1rem; opacity: 0.9; max-width: 600px; margin: 0 auto; }
    .features { display: flex; flex-wrap: wrap; gap: 16px;
                padding: 32px 24px; justify-content: center; }
    .card { background: white; border-radius: 12px; padding: 20px;
            width: 220px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
    .card h3 { font-size: 0.95rem; font-weight: 600; color: #1a73e8; margin-bottom: 6px; }
    .card p  { font-size: 0.82rem; color: #4a5568; }
    .api { background: white; margin: 0 24px 32px; border-radius: 12px;
           padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
    .api h3 { color: #1a73e8; margin-bottom: 12px; }
    .api a  { color: #1a73e8; font-weight: 500; }
    footer  { text-align: center; padding: 16px; color: #718096; font-size: 0.8rem; }
  </style>
</head>
<body>
  <header>
    <h1>🤖 SoftProdigy Snow Agent</h1>
    <span>Powered by Groq AI · ServiceNow Automation</span>
  </header>
  <div class="hero">
    <h2>ServiceNow AI Automation Platform</h2>
    <p>Generate BRD/FRD/TSD documents, user stories, test cases, process flows,
       and manage incidents — all powered by Groq AI.</p>
  </div>
  <div class="features">
    <div class="card"><h3>📄 Document Generation</h3>
      <p>AI-powered BRD, FRD, and TSD documents with acceptance criteria.</p></div>
    <div class="card"><h3>📋 User Stories</h3>
      <p>Extract user stories from any input — text, audio, PDF, or image.</p></div>
    <div class="card"><h3>🧪 Test Cases</h3>
      <p>Generate comprehensive test cases with Given/When/Then scenarios.</p></div>
    <div class="card"><h3>🔄 Process Flows</h3>
      <p>Visualize workflows with Mermaid diagrams and step-by-step flows.</p></div>
    <div class="card"><h3>🚨 Incident Management</h3>
      <p>Create, update, and resolve ServiceNow incidents via REST API.</p></div>
    <div class="card"><h3>💊 Health Scans</h3>
      <p>CMDB health, SecOps diagnostics, and instance connectivity checks.</p></div>
  </div>
  <div class="api">
    <h3>🔗 API Access</h3>
    <p>Interactive API docs: <a href="/fastapi/docs" target="_blank">/fastapi/docs</a>
       &nbsp;|&nbsp; Health: <a href="/health">/health</a>
       &nbsp;|&nbsp; AI Status: <a href="/snow/ai-status">/snow/ai-status</a></p>
  </div>
  <footer>SoftProdigy Snow Agent · v6</footer>
</body>
</html>"""
    return HTMLResponse(content=body)


@app.get("/health", include_in_schema=False)
def health_check() -> dict:
    return {"status": "ok", "platform": PLATFORM}


# ---------------- SoftProdigy Snow routes ----------------
# Protected by X-API-Key when AGENT_API_KEY is set (see agent_security.py). /snow/config stays public.
_snow_deps = [Depends(require_api_key)]
app.include_router(snow_login_router, dependencies=_snow_deps)
app.include_router(snow_router, dependencies=_snow_deps)
app.include_router(snow_universal_router, dependencies=_snow_deps)
app.include_router(snow_interactive_router, dependencies=_snow_deps)
app.include_router(snow_tools_router, dependencies=_snow_deps)        # SoftProdigy now_mcp catalog tools
app.include_router(sn_full_tools_router, dependencies=_snow_deps)    # all 522 vendor ServiceNow tools
app.include_router(acc_router, dependencies=_snow_deps)              # ACC-VC deployment program


@app.get("/snow/config", include_in_schema=False)
async def public_config():
    from snow_login import agent_config
    return await agent_config()


# ---------------- SoftProdigy platform routes (only inside platform-service) ----------------
if PLATFORM:
    @app.post(
        "/getToken",
        tags=["Auth"],
        dependencies=[
            Depends(RateLimit("10/minute", scope="auth_login_min")),
            Depends(RateLimit("100/hour", scope="auth_login_hr")),
        ],
    )
    def login(form_data: OAuth2PasswordRequestForm = Depends()):
        if not authenticate_user(form_data.username, form_data.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        access_token = create_access_token(data={"sub": form_data.username})
        return {"access_token": access_token, "token_type": "bearer"}

    root_router = APIRouter(dependencies=[Depends(authenticate)])

    @root_router.get("/", dependencies=[Depends(RateLimit("120/minute", scope="root_index"))])
    def index() -> Any:
        return HTMLResponse("<html><body style='padding:10px'><h1>SoftProdigy Platform APIs</h1>"
                            "<div>Check the API spec: <a href='/fastapi/docs'>here</a></div></body></html>")

    secured_api_router = APIRouter(dependencies=[Depends(authenticate),
                                                 Depends(RateLimit("1000/minute", scope="api_v1"))])
    secured_api_router.include_router(api_router)
    app.include_router(websocket_router, prefix=settings.API_V1_STR)
    app.include_router(public_router, prefix=settings.API_V1_STR)
    app.include_router(secured_api_router, prefix=settings.API_V1_STR)
    app.include_router(root_router)

# Uvicorn startup
def _startup_banner(port: int):
    """As soon as the server accepts connections, print a clear 'RUNNING' block; the AI check follows."""
    import socket
    import threading

    def run():
        import groq_client
        from sn_client import default_instance_url
        for _ in range(240):
            with socket.socket() as s_:
                s_.settimeout(0.5)
                if s_.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        try:
            from sn_tools import catalog
            tools = f"{len(catalog()['tools'])} loaded"
        except Exception as e:  # visible, not fatal
            tools = f"NOT loaded ({e})"
        line = "=" * 64
        print(f"\n{line}")
        print("  SoftProdigy Snow Agent is RUNNING")
        print(f"  Open in your browser:  http://localhost:{port}/ui")
        print("                         (or http://localhost:3000 if you started the snow-ui window)")
        print(f"  ServiceNow instance:   {default_instance_url() or 'entered on the login page (first time only)'}")
        print(f"  ServiceNow tools:      {tools}")
        print("  AI (Groq):             checking ...")
        print("  Keep this window open. Press Ctrl+C to stop the agent.")
        print(f"{line}", flush=True)
        ai = groq_client.quick_status()
        if ai.get("ok"):
            print(f"  AI (Groq):             READY - model {ai.get('model')}{' (' + ai['note'] + ')' if ai.get('note') else ''}\n", flush=True)
        else:
            print(f"  AI (Groq):             NOT WORKING - {ai.get('error')}")
            print("                         (login, ServiceNow actions and tools still work; AI features will show an error)\n", flush=True)

    threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    import time
    import uvicorn

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
    port = int(os.getenv("AGENT_PORT", "8001"))
    print(f"Starting SoftProdigy Snow Agent on port {port} ...", flush=True)
    _startup_banner(port)
    uvicorn.run(
        app,
        host=os.getenv("AGENT_HOST", "127.0.0.1"),
        port=port,
        timeout_keep_alive=300,
        timeout_graceful_shutdown=30,
        log_level="warning",
        access_log=False,
        ws_ping_interval=30,
        ws_ping_timeout=300,
    )
