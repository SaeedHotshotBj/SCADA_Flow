# =====================================================
# SCADA_FLOW SQL WRITER NODE
# HISTORIAN STORAGE ENGINE
# TIME + TRIGGER + REPORT GROUP STORAGE
# =====================================================

import json
import time
from datetime import datetime

from services.historian_service import HistorianService
from services.tag_registry import TagRegistry
from services.report_service import save_report_snapshot
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
        self._report_time_memory = {}

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

        except Exception as exc:
            print("SQLWRITER REPORT CONFIG ERROR:", exc)
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

    def _save_time_report_snapshot(self, tags, definitions, report_products, timestamp=None):
        """Save a report snapshot when any selected ReportOutput tag is TIME-based.

        ReportOutput.products is the sole source of report columns. The
        storage mode for each selected tag comes from the TagMapper definition
        in the same saved Flow. No other tags are introduced.
        """
        if not report_products or not isinstance(tags, dict):
            return 0

        definition_map = {}
        for definition in definitions or []:
            if not isinstance(definition, dict):
                continue
            name = str(definition.get("name", "")).strip()
            if name:
                definition_map[name.lower()] = definition

        selected = []
        time_due = False
        now = time.monotonic()

        for product in report_products:
            if not isinstance(product, dict):
                continue

            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue

            definition = definition_map.get(tag.lower())
            if not definition:
                continue

            mode = str(definition.get("storage", "TIME")).strip().upper()
            if mode != "TIME":
                continue

            selected.append(product)

            try:
                interval = float(definition.get("interval", 0) or 0)
            except (TypeError, ValueError):
                interval = 0.0

            key = (int(self.company_id), tag.lower())
            last = self._report_time_memory.get(key, 0.0)

            if interval <= 0 or now - last >= interval:
                time_due = True

        if not selected or not time_due:
            return 0

        # A TIME report snapshot contains only the columns explicitly selected
        # in ReportOutput.products, but may include mixed TIME/TRIGGER products
        # that already have current values in the same packet.
        snapshot_tags = {}
        tag_lookup = {
            str(name).strip().lower(): (name, value)
            for name, value in tags.items()
        }

        for product in report_products:
            if not isinstance(product, dict):
                continue
            tag = str(product.get("tag", "")).strip()
            if not tag:
                continue
            item = tag_lookup.get(tag.lower())
            if item is None or item[1] is None:
                continue
            snapshot_tags[item[0]] = item[1]

        if not snapshot_tags:
            return 0

        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report_id = save_report_snapshot(
            self.company_id,
            snapshot_tags,
            report_products,
            timestamp=timestamp,
        )

        if report_id is None:
            return 0

        for product in selected:
            tag = str(product.get("tag", "")).strip().lower()
            self._report_time_memory[(int(self.company_id), tag)] = now

        print(
            "REPORT TIME SNAPSHOT SAVED:",
            "Company=", self.company_id,
            "ReportID=", report_id,
            "Tags=", [p.get("tag") for p in report_products if isinstance(p, dict)]
        )

        return 1

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

        # TRIGGER-based report snapshots keep using the existing Historian
        # implementation. TIME-based report products are handled here so a
        # report does not depend on a rising-edge trigger.
        trigger_written = self.historian.process_report_group(
            self.company_id,
            tags,
            definitions,
            registers,
            report_products
        )

        time_written = self._save_time_report_snapshot(
            tags,
            definitions,
            report_products,
            timestamp=data.get("Timestamp"),
        ) if trigger_written == 0 else 0

        report_written = trigger_written + time_written

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
