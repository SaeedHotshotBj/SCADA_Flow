"""
SCADA_FLOW REPORT RUNTIME

Generic report snapshot worker for every company / Edge.
The saved Drawflow is the only source of report configuration.
"""

import json
import threading
import time
from datetime import datetime

from database import get_connection, get_company_flow
from services.report_service import save_report_snapshot


POLL_SECONDS = 0.5
BURST_WINDOW_SECONDS = 5.0


class ReportRuntime:
    def __init__(self):
        self.running = True
        self.last_id = 0
        self.last_snapshot_key = {}
        self.last_time_snapshot = {}

    # -------------------------------------------------
    # FLOW HELPERS
    # -------------------------------------------------
    def _nodes(self, company_id):
        flow = get_company_flow(company_id)
        if not flow:
            return {}

        if isinstance(flow, str):
            flow = json.loads(flow)

        return (
            flow.get("drawflow", {})
                .get("Home", {})
                .get("data", {})
        )

    def _report_configs(self, company_id):
        nodes = self._nodes(company_id)
        configs = []

        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            if node.get("name") != "ReportOutput":
                continue

            inputs = node.get("inputs", {}) or {}
            connected = False
            for input_data in inputs.values():
                if not isinstance(input_data, dict):
                    continue
                connections = input_data.get("connections", [])
                if isinstance(connections, list) and connections:
                    connected = True
                    break

            if not connected:
                continue

            data = node.get("data", {}) or {}
            products = data.get("products", [])
            if not isinstance(products, list) or not products:
                continue

            configs.append({
                "node_id": str(node_id),
                "products": [p for p in products if isinstance(p, dict) and str(p.get("tag", "")).strip()],
            })

        return configs

    def _definitions(self, company_id):
        nodes = self._nodes(company_id)
        result = {}

        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            if node.get("name") != "TagMapper":
                continue

            mappings = (node.get("data", {}) or {}).get("mappings", [])
            if not isinstance(mappings, list):
                continue

            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                name = str(mapping.get("name", "")).strip()
                if name:
                    result[name.lower()] = mapping
            break

        return result

    # -------------------------------------------------
    # DATABASE HELPERS
    # -------------------------------------------------
    def _latest_values(self, company_id, tags):
        if not tags:
            return {}

        conn = get_connection()
        cursor = conn.cursor()
        result = {}

        try:
            for tag in tags:
                cursor.execute(
                    """
                    SELECT TagName, Value, Timestamp
                    FROM PLC_Data
                    WHERE CompanyID = ?
                      AND LOWER(TagName) = LOWER(?)
                    ORDER BY ID DESC
                    LIMIT 1
                    """,
                    (company_id, tag),
                )
                row = cursor.fetchone()
                if row:
                    result[str(row["TagName"])] = {
                        "value": row["Value"],
                        "timestamp": row["Timestamp"],
                    }
        finally:
            cursor.close()
            conn.close()

        return result

    # -------------------------------------------------
    # SNAPSHOT LOGIC
    # -------------------------------------------------
    def _fresh_enough(self, timestamp, latest):
        if not timestamp:
            return False

        try:
            base = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except Exception:
            return False

        for item in latest.values():
            try:
                value_time = datetime.fromisoformat(str(item["timestamp"]).replace("Z", "+00:00"))
            except Exception:
                return False
            if abs((base - value_time).total_seconds()) > BURST_WINDOW_SECONDS:
                return False

        return True

    def _save_from_edge_row(self, row):
        company_id = int(row["CompanyID"])
        tag_name = str(row["TagName"] or "").strip()
        timestamp = row["Timestamp"]
        if not tag_name:
            return

        configs = self._report_configs(company_id)
        if not configs:
            return

        definitions = self._definitions(company_id)
        if not definitions:
            return

        for config in configs:
            products = config["products"]
            tags = [str(p.get("tag", "")).strip() for p in products]
            tags = [t for t in tags if t]
            if not tags or tag_name.lower() not in {t.lower() for t in tags}:
                continue

            modes = []
            for tag in tags:
                definition = definitions.get(tag.lower())
                if not definition:
                    continue
                modes.append((
                    tag,
                    str(definition.get("storage", "TIME")).upper(),
                    definition,
                ))

            if not modes:
                continue

            incoming_mode = None
            incoming_definition = None
            for tag, mode, definition in modes:
                if tag.lower() == tag_name.lower():
                    incoming_mode = mode
                    incoming_definition = definition
                    break

            if incoming_mode is None:
                continue

            latest = self._latest_values(company_id, tags)
            if len(latest) != len(tags):
                continue

            if not self._fresh_enough(timestamp, latest):
                continue

            # TIME: respect the configured minimum interval for the report tag.
            if incoming_mode == "TIME":
                try:
                    interval = float(incoming_definition.get("interval", 0) or 0)
                except (TypeError, ValueError):
                    interval = 0

                key = (company_id, config["node_id"])
                now = time.monotonic()
                last = self.last_time_snapshot.get(key, 0.0)
                if interval > 0 and now - last < interval:
                    continue
                self.last_time_snapshot[key] = now

            # TRIGGER: one snapshot per burst of report-tag arrivals.
            snapshot_key = (
                company_id,
                config["node_id"],
                tuple(
                    sorted(
                        (str(name).lower(), str(item["timestamp"]))
                        for name, item in latest.items()
                    )
                ),
            )
            if self.last_snapshot_key.get((company_id, config["node_id"])) == snapshot_key:
                continue

            values = {
                name: item["value"]
                for name, item in latest.items()
            }

            report_id = save_report_snapshot(
                company_id,
                values,
                products,
                timestamp=timestamp,
            )

            if report_id is not None:
                self.last_snapshot_key[(company_id, config["node_id"])] = snapshot_key
                print(
                    "REPORT RUNTIME SNAPSHOT:",
                    "Company=", company_id,
                    "ReportNode=", config["node_id"],
                    "ReportID=", report_id,
                    "Tags=", tags,
                )

    # -------------------------------------------------
    # LOOP
    # -------------------------------------------------
    def run(self):
        while self.running:
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT ID, CompanyID, TagName, Timestamp
                    FROM PLC_Data
                    WHERE ID > ?
                    ORDER BY ID ASC
                    LIMIT 500
                    """,
                    (self.last_id,),
                )
                rows = cursor.fetchall()
                cursor.close()
                conn.close()

                for row in rows:
                    self.last_id = int(row["ID"])
                    try:
                        self._save_from_edge_row(row)
                    except Exception as exc:
                        print("REPORT RUNTIME ROW ERROR:", exc)

            except Exception as exc:
                print("REPORT RUNTIME LOOP ERROR:", exc)
                time.sleep(1.0)

            time.sleep(POLL_SECONDS)

    def stop(self):
        self.running = False


def start():
    worker = ReportRuntime()
    thread = threading.Thread(
        target=worker.run,
        name="SCADA-Report-Runtime",
        daemon=True,
    )
    thread.start()
    print("REPORT RUNTIME WORKER STARTED")
    return worker
