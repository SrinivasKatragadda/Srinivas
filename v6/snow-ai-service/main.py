"""Entry point for:  uvicorn main:app --host 0.0.0.0 --port 8001 --reload

Same agent as `python api.py`; this file only exposes `app` under the name uvicorn expects
and prints the "RUNNING" block once the server is up.
"""
import os

from api import app, _startup_banner


def _show_banner():
    _startup_banner(int(os.getenv("AGENT_PORT", "8001")))


app.router.add_event_handler("startup", _show_banner)
