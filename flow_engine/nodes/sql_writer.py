# =====================================================
# SCADA_FLOW SQL WRITER NODE
# HISTORIAN STORAGE ENGINE
# TIME + TRIGGER STORAGE
# =====================================================

import json

from services.historian_service import HistorianService
from services.tag_registry import TagRegistry


class SQLWriter:

    def __init__(self, config):
        self.config = config or {}
        self.company_id = self.config.get("company_id", 1)
        self.historian = HistorianService()
        self._last_definition_signature = None

    def _signature(self, value):
        try:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
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

        definition_signature = self._signature(definitions)
        if definition_signature != self._last_definition_signature:
            TagRegistry.sync(
                self.company_id,
                definitions,
            )
            self._last_definition_signature = definition_signature

        # ReportOutput owns report persistence. SQLWriter only writes the
        # normal TIME/TRIGGER historian data.
        written = self.historian.process(
            self.company_id,
            tags,
            definitions,
            registers,
            report_tags=None,
        )

        data["SQL_Written"] = written
        data["Report_Written"] = 0

        return data
