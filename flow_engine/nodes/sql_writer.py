# =====================================================
# SCADA_FLOW SQL WRITER NODE
# Flow-defined historian persistence only.
# =====================================================

import json

from services.historian_service import HistorianService
from services.plc_identity import ensure_plc_identity_schema
from services.tag_registry import TagRegistry


class SQLWriter:
    """Persist historian data defined by the current Flow payload.

    Production report persistence belongs exclusively to ReportOutput. This
    node does not inspect ReportOutput/ManagementPanel nodes or create reports
    on its own.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.company_id = self.config.get("company_id")
        self.historian = HistorianService()
        self._last_definition_signature = None

    @staticmethod
    def _plc_id(data):
        value = data.get("PLC_ID")
        if value is None and isinstance(data.get("PLC"), dict):
            value = data["PLC"].get("PLC_ID")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _signature(value):
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
        data = data or {}
        plc_id = self._plc_id(data)
        if plc_id is None:
            raise ValueError("SQLWriter requires PLC_ID in the runtime payload")

        definitions = data.get("TagDefinitions", []) or []
        if not isinstance(definitions, list) or not definitions:
            data["PLC_ID"] = plc_id
            data["SQL_Written"] = 0
            return data

        if self.company_id is None:
            self.company_id = data.get("CompanyID")
        if self.company_id is None:
            raise ValueError("SQLWriter requires CompanyID in the Flow payload")

        signature = self._signature(definitions)
        if signature != self._last_definition_signature:
            ensure_plc_identity_schema()
            TagRegistry.sync(self.company_id, definitions)
            self._last_definition_signature = signature

        # TIME/TRIGGER historian storage is still controlled by TagMapper.
        # ReportOutput persistence is intentionally not performed here.
        written = self.historian.process(
            self.company_id,
            plc_id,
            data.get("Tags", {}) or {},
            definitions,
            data.get("Registers", {}) or {},
        )

        data["PLC_ID"] = plc_id
        data["SQL_Written"] = written
        data["Report_Written"] = 0
        return data


__all__ = ["SQLWriter"]
