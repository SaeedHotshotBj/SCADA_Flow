# =====================================================
# SCADA_FLOW SQL WRITER NODE
# HISTORIAN STORAGE ENGINE
# TIME + TRIGGER + REPORT GROUP STORAGE
# =====================================================

import json

from services.historian_service import HistorianService
from services.tag_registry import TagRegistry
from database import get_company_flow


class SQLWriter:

    _report_product_cache = {}

    def __init__(self, config):
        self.config = config or {}
        self.company_id = self.config.get("company_id", 1)
        self.historian = HistorianService()
        self._last_definition_signature = None
        self._last_report_signature = None
        self._cached_report_products = []

    def _get_report_products(self):
        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return []

            if isinstance(flow, str):
                flow = json.loads(flow)

            nodes = (
                flow
                .get("drawflow", {})
                .get("Home", {})
                .get("data", {})
            )

            for node in nodes.values():
                if node.get("name") != "ReportOutput":
                    continue

                data = node.get("data", {}) or {}
                config = data.get("config", data) or {}
                products = config.get("products", [])

                if isinstance(products, list):
                    return products

        except Exception:
            return []

        return []

    def _signature(self, value):
        try:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False
            )
        except (TypeError, ValueError):
            return repr(value)

    def execute(self, data=None):

        if data is None:
            data = {}

        tags = data.get("Tags", {})
        definitions = data.get("TagDefinitions", [])
        registers = data.get("Registers", {})

        if not definitions:
            return data

        definition_signature = self._signature(
            definitions
        )

        if definition_signature != self._last_definition_signature:
            TagRegistry.sync(
                self.company_id,
                definitions
            )
            self._last_definition_signature = definition_signature

        report_products = self._get_report_products()
        report_signature = self._signature(
            report_products
        )

        if report_signature != self._last_report_signature:
            self._cached_report_products = report_products
            self._last_report_signature = report_signature

        report_products = self._cached_report_products

        report_tags = [
            str(item.get("tag", "")).strip()
            for item in report_products
            if (
                isinstance(item, dict)
                and str(item.get("tag", "")).strip()
            )
        ]

        report_written = self.historian.process_report_group(
            self.company_id,
            tags,
            definitions,
            registers,
            report_products
        )

        written = self.historian.process(
            self.company_id,
            tags,
            definitions,
            registers,
            report_tags=report_tags
        )

        data["SQL_Written"] = written + report_written
        data["Report_Written"] = report_written

        return data
