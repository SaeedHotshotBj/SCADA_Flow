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

    def __init__(self, config):
        self.config = config or {}
        self.company_id = self.config.get("company_id", 1)
        self.historian = HistorianService()

    def _get_report_products(self):
        try:
            flow = get_company_flow(self.company_id)
            if not flow:
                return []
            if isinstance(flow, str):
                flow = json.loads(flow)

            nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
            for node in nodes.values():
                if node.get("name") != "ReportOutput":
                    continue
                data = node.get("data", {}) or {}
                config = data.get("config", data) or {}
                products = config.get("products", [])
                if isinstance(products, list):
                    return products
        except Exception as e:
            print("REPORT CONFIG LOAD ERROR:", e)
        return []

    def execute(self, data=None):
        if data is None:
            data = {}

        tags = data.get("Tags", {})
        definitions = data.get("TagDefinitions", [])
        registers = data.get("Registers", {})

        if not definitions:
            print("SQL WRITER: NO DEFINITIONS")
            return data

        registered = TagRegistry.sync(self.company_id, definitions)

        print()
        print("TAG REGISTRY:", registered, "TAGS SYNCHRONIZED")
        print()
        print("SQL DEFINITIONS:")
        print(definitions)
        print()

        report_products = self._get_report_products()
        report_tags = [
            str(item.get("tag", "")).strip()
            for item in report_products
            if isinstance(item, dict) and str(item.get("tag", "")).strip()
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

        print()
        print("SQL WRITER:")
        print(written + report_written, "VALUES INSERTED")
        print()

        data["SQL_Written"] = written + report_written
        data["Report_Written"] = report_written
        data["Tags_Registered"] = registered

        return data
