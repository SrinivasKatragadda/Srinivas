"""
ITOM inside the Snow Agent.

The ITOM logic lives in the framework-independent `snow_itom` package of servicenow-mcp (../servicenow-mcp/src),
the same way ITAM does (see itam_bridge). Only snow_itom / snow_itam are imported from there: the path is appended
(not prepended) so the agent's vendored `servicenow_mcp` keeps priority. Every call runs with the signed-in user's
own ServiceNow credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from fastapi import HTTPException

_SRC = Path(__file__).resolve().parent.parent / "servicenow-mcp" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

from snow_itam import make_function  # noqa: E402
from snow_itom import ITOMError, ITOMService, ItomSNBackend, TOOLS_BY_NAME  # noqa: E402
from snow_itom import ALL_TOOLS as TOOLS  # noqa: E402  (ITOM tools + the shared CMDB CI tools)

import sn_client as snc  # noqa: E402

# 424 = a plugin is missing, 412 = the instance's tables differ from the documented layout, 403 = a role is missing
_STATUS = {"INVALID_ARGS": 422, "NOT_FOUND": 404, "AMBIGUOUS": 422, "DUPLICATE": 409, "FORBIDDEN": 403,
           "PLUGIN_REQUIRED": 424, "INCOMPATIBLE_INSTANCE": 412, "WRITE_DISABLED": 403}


def is_itom_tool(name: str) -> bool:
    return name in TOOLS_BY_NAME


def kind_of(name: str) -> str:
    return TOOLS_BY_NAME[name].kind


def has_confirm(name: str) -> bool:
    return any(p.name == "confirm" for p in TOOLS_BY_NAME[name].params)


def service(url: str, user: str, password: str) -> ITOMService:
    return ITOMService(ItomSNBackend(snc.SNClient(url, user, password)))


def run(creds: Dict[str, Any], name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run one ITOM tool for the signed-in user. ITOM refusals become HTTP errors with a readable detail."""
    url, user, pw = creds.get("snow_url"), creds.get("snow_user"), creds.get("snow_pass")
    if not (url and user and pw):
        raise HTTPException(status_code=400, detail="ServiceNow instance URL, username and password are required")
    try:
        return service(url, user, pw).call(name, arguments or {})
    except ITOMError as e:
        status = _STATUS.get(e.code) or (e.status if e.status and 400 <= e.status < 600 else 502)
        raise HTTPException(status_code=status, detail=str(e)[:1500])


def register_fastmcp(mcp, get_creds: Callable[[], Tuple[str, str, str]]) -> int:
    """Register every ITOM tool on a FastMCP server; credentials come from the request headers."""
    def runner(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        url, user, pw = get_creds()
        try:
            return service(url, user, pw).call(name, arguments)
        except ITOMError as e:
            return {"success": False, **e.to_dict()}
    for spec in TOOLS:
        fn = make_function(spec, runner)
        mcp.tool(name=spec.name, description=spec.description,
                 annotations={"readOnlyHint": spec.kind == "read", "destructiveHint": spec.kind == "write"})(fn)
    return len(TOOLS)
