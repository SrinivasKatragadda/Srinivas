"""The ITAM service: one object exposing every ITAM operation over a Backend."""

from typing import Any, Dict

from snow_itam.assets import AssetsMixin
from snow_itam.backends import Backend
from snow_itam.contracts import ContractsMixin
from snow_itam.errors import ITAMError
from snow_itam.inventory import InventoryMixin
from snow_itam.licenses import LicensesMixin
from snow_itam.procurement import ProcurementMixin
from snow_itam.reports import ReportsMixin


# The table each area needs, and the plugin that provides it (checked before any work, cached per service)
AREA_REQUIREMENT = {
    "asset": ("alm_asset", "Asset Management", "com.snc.asset_management"),
    "inventory": ("alm_stockroom", "Asset Management inventory", "com.snc.asset_management"),
    "license": ("alm_license", "Software license management", "com.snc.asset_management"),
    "contract": ("ast_contract", "Contract management", "com.snc.contract_management"),
    "procurement": ("proc_po", "Procurement (purchase orders and receiving)", "com.snc.procurement"),
    "reports": ("alm_asset", "Asset Management reporting", "com.snc.asset_management"),
}
ALWAYS_AVAILABLE = {"get_itam_capabilities"}


class ITAMService(AssetsMixin, InventoryMixin, LicensesMixin, ContractsMixin, ProcurementMixin, ReportsMixin):
    """Create one per signed-in user / request (it caches roles and choices for that user)."""

    def __init__(self, backend: Backend):
        super().__init__(backend)

    def call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from snow_itam.tools import TOOLS_BY_NAME
        spec = TOOLS_BY_NAME.get(tool_name)
        if not spec:
            raise ITAMError(f"Unknown ITAM tool '{tool_name}'", "INVALID_ARGS")
        kwargs = spec.bind(arguments or {})
        if spec.name not in ALWAYS_AVAILABLE and spec.area in AREA_REQUIREMENT:
            self.require_table(*AREA_REQUIREMENT[spec.area])
        self.matched = []
        result = getattr(self, spec.method)(**kwargs)
        if self.matched and isinstance(result, dict):
            result["matched"] = list(self.matched)   # which records the loosely typed names resolved to
        return result
