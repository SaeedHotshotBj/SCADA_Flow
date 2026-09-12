"""
SCADA_FLOW REPORT RUNTIME

Generic report snapshot worker for every company / Edge.
The saved Drawflow is the only source of report configuration.
"""

import json
import threading
import time

from database import get_connection, get_company_flow
from services.report_service import save_report_snapshot


POLL_SECONDS = 0.5
_START_LOCK = threading.Lock()
_WORKER = None


class ReportRuntime:
    def __init__(self):
        self.running = True
        self.last_id = 0
        self.last_snapshot_key = {}
        self.last_time_snapshot = {}

    def _nodes(self, company_id):
        flow = get_company_flow(company_id)
        if not flow:
            return {}
        if isinstance(flow, str):
            flow = json.loads(flow)
        return flow.get("drawflow", {}).get("Home", {}).get("data", {})

    def _report_configs(self, company_id):
        nodes = self._nodes(company_id)
        configs = []
        for node_id, node in nodes.items():
            if not isinstance(node, dict) or node.get("name") != "ReportOutput":
                continue
            inputs = node.get("inputs", {}) or {}
            connected = any(
                isinstance(input_data, dict)
                and isinstance(input_data.get("connections", []), list)
                and bool(input_data.get("connections", []))
                for input_data in inputs.values()
            )
            if not connected:
                continue
            products = (node.get("data", {}) or {}).get("products", [])
            if not isinstance(products, list) or not products:
                continue
            clean_products = [
                p for p in products
                if isinstance(p, dict) and str(p.get("tag", "")).strip()
            ]
            if clean_products:
                configs.append({"node_id": str(node_id), "products": clean_products})
        return configs

    def _definitions(self, company_id):
        nodes = self._nodes(company_id)
        result = {}
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "TagMapper":
                continue
            mappings = (node.get("data", {}) or {}).get("mappings", [])
            if isinstance(mappings, list):
                for mapping in mappings:
                    if isinstance(mapping, dict):
                        name = str(mapping.get("name", "")).strip()
                        if name:
                            result[name.lower()] = mapping
            break
        return result

    def _latest_values(self, company_id, tags):
        if not tags:
            return {}
        conn = get_connection()
        try:
            result = {}
            for tag in tags:
                row = conn.execute(
                    """
                    SELECT TagName, Value, Timestamp
                    FROM PLC_Data
                    WHERE CompanyID = ? AND LOWER(TagName) = LOWER(?)
                    ORDER BY ID DESC LIMIT 1
                    """,
                    (company_id, tag),
                ).fetchone()
                if row:
                    result[str(row["TagName"])] = {
                        "value": row["Value"],
                        "timestamp": row["Timestamp"],
                    }
            return result
        finally:
            conn.close()

    def _management_context(self, company_id):
        result = {"ContractCode": None, "ProductCode": None}
        nodes = self._nodes(company_id)
        config = None
        for node in nodes.values():
            if isinstance(node, dict) and node.get("name") == "ManagementPanel":
                data = node.get("data", {}) or {}
                config = data.get("config", data) or {}
                break
        if not isinstance(config, dict):
            return result

        contract_register = config.get("contract_code_register")
        product_register = config.get("product_code_register")

        def latest_by_register(register):
            if register in (None, ""):
                return None
            try:
                register_int = int(float(register))
            except (TypeError, ValueError):
                return None

            tag_name = None
            for node in nodes.values():
                if not isinstance(node, dict) or node.get("name") != "TagMapper":
                    continue
                mappings = (node.get("data", {}) or {}).get("mappings", [])
                for mapping in mappings if isinstance(mappings, list) else []:
                    if not isinstance(mapping, dict):
                        continue
                    try:
                        mapping_register = int(float(mapping.get("register")))
                    except (TypeError, ValueError):
                        continue
                    if mapping_register == register_int:
                        tag_name = str(mapping.get("name", "")).strip() or None
                        if tag_name:
                            break
                if tag_name:
                    break
            if not tag_name:
                return None

            conn = get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT Value FROM PLC_Data
                    WHERE CompanyID = ? AND LOWER(TagName) = LOWER(?)
                    ORDER BY ID DESC LIMIT 1
                    """,
                    (company_id, tag_name),
                ).fetchone()
                return row["Value"] if row else None
            finally:
                conn.close()

        contract = latest_by_register(contract_register)
        product = latest_by_register(product_register)
        if contract not in (None, ""):
            result["ContractCode"] = str(contract).strip()
        if product not in (None, ""):
            result["ProductCode"] = str(product).strip()
        return result

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

        incoming_key = tag_name.lower()
        for config in configs:
            products = config["products"]
            tags = [
                str(p.get("tag", "")).strip()
                for p in products
                if str(p.get("tag", "")).strip()
            ]
            if incoming_key not in {tag.lower() for tag in tags}:
                continue

            incoming_definition = definitions.get(incoming_key)
            if not incoming_definition:
                continue
            incoming_mode = str(incoming_definition.get("storage", "TIME")).strip().upper()

            latest = self._latest_values(company_id, tags)
            if len(latest) != len(tags):
                continue

            if incoming_mode == "TIME":
                try:
                    interval = float(incoming_definition.get("interval", 0) or 0)
                except (TypeError, ValueError):
                    interval = 0.0
                key = (company_id, config["node_id"], incoming_key)
                now = time.monotonic()
                last = self.last_time_snapshot.get(key, 0.0)
                if interval > 0 and now - last < interval:
                    continue
                self.last_time_snapshot[key] = now
            elif incoming_mode == "TRIGGER":
                continue
            else:
                continue

            snapshot_key = (
                company_id,
                config["node_id"],
                tuple(sorted((str(name).lower(), str(item.get("timestamp"))) for name, item in latest.items())),
            )
            report_key = (company_id, config["node_id"])
            if self.last_snapshot_key.get(report_key) == snapshot_key:
                continue

            values = {name: item["value"] for name, item in latest.items()}
            context = self._management_context(company_id)
            if context.get("ContractCode") not in (None, ""):
                values["ContractCode"] = context["ContractCode"]
            if context.get("ProductCode") not in (None, ""):
                values["ProductCode"] = context["ProductCode"]

            snapshot_products = list(products)
            if context.get("ContractCode") not in (None, "") and not any(
                isinstance(item, dict) and str(item.get("context_role", "")).strip().lower() in ("contract", "contract_code", "contractid", "contract_id")
                for item in snapshot_products
            ):
                snapshot_products.append({"tag": "ContractCode", "name": "ContractCode", "context_role": "contract_code"})
            if context.get("ProductCode") not in (None, "") and not any(
                isinstance(item, dict) and str(item.get("context_role", "")).strip().lower() in ("product", "product_code", "productid", "product_id")
                for item in snapshot_products
            ):
                snapshot_products.append({"tag": "ProductCode", "name": "ProductCode", "context_role": "product_code"})

            report_id = save_report_snapshot(company_id, values, snapshot_products, timestamp=timestamp)
            if report_id is not None:
                self.last_snapshot_key[report_key] = snapshot_key
                print("REPORT RUNTIME SNAPSHOT:", "Company=", company_id, "ReportNode=", config["node_id"], "ReportID=", report_id, "TriggerTag=", tag_name, "Mode=", incoming_mode, "ContractCode=", context.get("ContractCode"), "ProductCode=", context.get("ProductCode"), "Tags=", tags)

    def run(self):
        while self.running:
            try:
                conn = get_connection()
                try:
                    rows = conn.execute(
                        """
                        SELECT ID, CompanyID, TagName, Timestamp
                        FROM PLC_Data WHERE ID > ? ORDER BY ID ASC LIMIT 500
                        """,
                        (self.last_id,),
                    ).fetchall()
                finally:
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
    global _WORKER
    with _START_LOCK:
        if _WORKER is not None and _WORKER.running:
            return _WORKER
        _WORKER = ReportRuntime()
        thread = threading.Thread(target=_WORKER.run, name="SCADA-Report-Runtime", daemon=True)
        thread.start()
        print("REPORT RUNTIME WORKER STARTED")
        return _WORKER
