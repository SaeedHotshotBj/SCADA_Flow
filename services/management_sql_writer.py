"""Explicit ManagementPanel-aware SQLWriter.

Report snapshots remain owned by ReportOutput. This class only supplies the
management-context backfill behavior that used to be injected by monkey patch.
"""

import json
from datetime import datetime

from database import get_company_flow, get_connection
from flow_engine.nodes.sql_writer import SQLWriter


class ManagementSQLWriter(SQLWriter):
    def _get_report_products(self):
        # ReportOutput is the single owner of ReportHistory snapshots.
        # SQLWriter continues to handle historian/tag persistence only.
        return []

    def _trigger_register_for_tag(self, tag):
        flow_json = get_company_flow(self.company_id)
        if not flow_json:
            return None
        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})
        wanted = str(tag).strip().lower()
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "TagMapper":
                continue
            mappings = (node.get("data", {}) or {}).get("mappings", [])
            for mapping in mappings if isinstance(mappings, list) else []:
                if not isinstance(mapping, dict):
                    continue
                if str(mapping.get("name", "")).strip().lower() != wanted:
                    continue
                if str(mapping.get("storage", "TIME")).strip().upper() != "TRIGGER":
                    return None
                value = mapping.get("trigger_register")
                try:
                    return str(int(float(value)))
                except (TypeError, ValueError):
                    return str(value).strip() if value not in (None, "") else None
            break
        return None

    def _backfill_context(self, tag, value, timestamp=None):
        field = {
            "contractcode": "ContractCode",
            "productcode": "ProductCode",
        }.get(str(tag).strip().lower())
        if field is None or value in (None, ""):
            return None

        timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trigger_register = self._trigger_register_for_tag(tag)
        conn = get_connection()
        try:
            columns = {
                row["name"]
                for row in conn.execute('PRAGMA table_info("ReportHistory")').fetchall()
            }
            required = {"ReportID", "CompanyID", "Timestamp", "TriggerRegister", field}
            if not required.issubset(columns):
                return None

            sql = """
                SELECT ReportID FROM ReportHistory
                WHERE CompanyID = ?
                  AND TRIM(COALESCE({field}, '')) = ''
                  AND datetime(Timestamp) >= datetime(?, '-15 seconds')
                  AND datetime(Timestamp) <= datetime(?, '+2 seconds')
            """.format(field=field)
            params = [int(self.company_id), str(timestamp), str(timestamp)]
            if trigger_register is not None:
                sql += " AND CAST(TriggerRegister AS TEXT) = ?"
                params.append(str(trigger_register))
            sql += " ORDER BY ReportID DESC LIMIT 1"
            report = conn.execute(sql, params).fetchone()
            if report is None:
                return None

            conn.execute(
                f"""
                UPDATE ReportHistory
                SET {field} = ?
                WHERE ReportID = ?
                  AND CompanyID = ?
                  AND TRIM(COALESCE({field}, '')) = ''
                """,
                (str(value).strip(), int(report["ReportID"]), int(self.company_id)),
            )
            conn.commit()
            return int(report["ReportID"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, data=None):
        result = super().execute(data)
        payload = result if isinstance(result, dict) else (data if isinstance(data, dict) else {})
        tags = payload.get("Tags", {})
        if isinstance(tags, dict):
            for context_tag in ("ContractCode", "ProductCode"):
                value = tags.get(context_tag)
                if value not in (None, ""):
                    self._backfill_context(context_tag, value, payload.get("Timestamp"))
        return result


__all__ = ["ManagementSQLWriter"]
