"""The ITOM service: one object exposing every ITOM operation over a Backend."""

from typing import Any, Dict

from snow_itam.errors import ITAMError
from snow_itom.cmdb import CmdbMixin
from snow_itom.errors import ITOMError
from snow_itom.events import EventsMixin
from snow_itom.flows import FlowsMixin
from snow_itom.operations import OperationsMixin
from snow_itom.services import ServicesMixin
from snow_itom.topology import TopologyMixin


class ITOMService(OperationsMixin, TopologyMixin, ServicesMixin, FlowsMixin, EventsMixin, CmdbMixin):
    """Create one per signed-in user / request (it caches roles, choices and the class tree for that user)."""

    def call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from snow_itom.tools import TOOLS_BY_NAME
        spec = TOOLS_BY_NAME.get(tool_name)
        if not spec:
            raise ITOMError(f"Unknown ITOM tool '{tool_name}'", "INVALID_ARGS")
        try:
            kwargs = spec.bind(arguments or {})
            self.matched = []
            result = getattr(self, spec.method)(**kwargs)
        except ITOMError:
            raise
        except ITAMError as e:   # shared plumbing / backend errors: same fields, ITOM type
            raise ITOMError(e.message, e.code, e.detail, e.status) from e
        if self.matched and isinstance(result, dict):
            result["matched"] = list(self.matched)
        return result
