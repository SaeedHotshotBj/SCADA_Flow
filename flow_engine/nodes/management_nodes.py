# =============================================================
# SCADA_FLOW MANAGEMENT NODES
# Contract / Product / BOM management is executed through Flow.
# =============================================================

from services.management_service import (
    get_management_config,
    list_products,
    get_product_catalog,
    save_product_bom,
    save_contract,
    query_contracts,
)


class ManagementInput:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}

    def execute(self, data=None):
        return dict(data or {})


class ContractRepository:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self.company_id = int(self.config.get("company_id"))

    def execute(self, data=None):
        data = dict(data or {})
        request = data.get("ManagementRequest", {}) or {}
        resource = str(request.get("resource", "")).strip().lower()
        if resource != "contracts":
            return data

        operation = str(request.get("operation", "list")).strip().lower()
        try:
            if operation == "save":
                result = save_contract(self.company_id, request.get("payload", {}) or {})
                data["ManagementResponse"] = {"status": "ok", "operation": operation, "result": result}
            elif operation in ("list", "filter"):
                rows = query_contracts(self.company_id, request.get("filters", {}) or {})
                data["ManagementResponse"] = {"status": "ok", "operation": operation, "rows": rows}
            else:
                data["ManagementResponse"] = {"status": "error", "message": "Unsupported contract operation"}
        except Exception as exc:
            data["ManagementResponse"] = {"status": "error", "message": str(exc)}
        return data


class ProductBOMRepository:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self.company_id = int(self.config.get("company_id"))

    def execute(self, data=None):
        data = dict(data or {})
        request = data.get("ManagementRequest", {}) or {}
        resource = str(request.get("resource", "")).strip().lower()
        if resource not in ("products", "bom"):
            return data

        operation = str(request.get("operation", "list")).strip().lower()
        try:
            if operation in ("list", "catalog"):
                data["ManagementResponse"] = {
                    "status": "ok",
                    "operation": operation,
                    "products": get_product_catalog(self.company_id),
                }
            elif operation == "save":
                result = save_product_bom(self.company_id, request.get("payload", {}) or {})
                data["ManagementResponse"] = {"status": "ok", "operation": operation, "result": result}
            else:
                data["ManagementResponse"] = {"status": "error", "message": "Unsupported product/BOM operation"}
        except Exception as exc:
            data["ManagementResponse"] = {"status": "error", "message": str(exc)}
        return data


class ManagementOutput:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    def execute(self, data=None):
        data = dict(data or {})
        if "ManagementResponse" not in data:
            data["ManagementResponse"] = {
                "status": "ok",
                "config": get_management_config(self.company_id),
            }
        return data


class ManagementPanelOutput:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}
        self.company_id = self.config.get("company_id")

    def execute(self, data=None):
        data = dict(data or {})
        data["ManagementConfig"] = self.config
        return data


class ManagementCostCalculator:
    def __init__(self, config=None, *args, **kwargs):
        self.config = config or {}

    def execute(self, data=None):
        # Cost calculation is performed and snapshotted by the repository
        # transaction using the latest BOM. This node marks the calculation
        # stage explicitly in the Flow without duplicating persistence.
        data = dict(data or {})
        response = data.get("ManagementResponse")
        if isinstance(response, dict):
            response["cost_stage"] = "calculated"
            data["ManagementResponse"] = response
        return data
