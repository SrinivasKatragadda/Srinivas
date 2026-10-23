"""
ITAM inside the Snow Agent.

The ITAM logic lives in the framework-independent `snow_itam` package of servicenow-mcp (../servicenow-mcp/src).
Only that package is imported from there: the path is appended (not prepended) so the agent's vendored
`servicenow_mcp` keeps priority. Every call runs with the signed-in user's own ServiceNow credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import HTTPException

_SRC = Path(__file__).resolve().parent.parent / "servicenow-mcp" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

from snow_itam import ITAMError, ITAMService, SNClientBackend, TOOLS, TOOLS_BY_NAME, make_function  # noqa: E402

import sn_client as snc  # noqa: E402

_STATUS = {"INVALID_ARGS": 422, "NOT_FOUND": 404, "AMBIGUOUS": 422, "DUPLICATE": 409, "FORBIDDEN": 403,
           "PLUGIN_REQUIRED": 424}


def is_itam_tool(name: str) -> bool:
    return name in TOOLS_BY_NAME


def kind_of(name: str) -> str:
    return TOOLS_BY_NAME[name].kind


def has_confirm(name: str) -> bool:
    return any(p.name == "confirm" for p in TOOLS_BY_NAME[name].params)


def service(url: str, user: str, password: str) -> ITAMService:
    return ITAMService(SNClientBackend(snc.SNClient(url, user, password)))


def run(creds: Dict[str, Any], name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run one ITAM tool for the signed-in user. ITAM refusals become HTTP errors with a readable detail."""
    url, user, pw = creds.get("snow_url"), creds.get("snow_user"), creds.get("snow_pass")
    if not (url and user and pw):
        raise HTTPException(status_code=400, detail="ServiceNow instance URL, username and password are required")
    try:
        return service(url, user, pw).call(name, arguments or {})
    except ITAMError as e:
        status = _STATUS.get(e.code) or (e.status if e.status and 400 <= e.status < 600 else 502)
        raise HTTPException(status_code=status, detail=str(e)[:1500])


def register_fastmcp(mcp, get_creds: Callable[[], Tuple[str, str, str]]) -> int:
    """Register every ITAM tool on a FastMCP server; credentials come from the request headers."""
    def runner(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        url, user, pw = get_creds()
        try:
            return service(url, user, pw).call(name, arguments)
        except ITAMError as e:
            return {"success": False, **e.to_dict()}
    for spec in TOOLS:
        fn = make_function(spec, runner)
        mcp.tool(name=spec.name, description=spec.description,
                 annotations={"readOnlyHint": spec.kind == "read", "destructiveHint": spec.kind == "write"})(fn)
    return len(TOOLS)
