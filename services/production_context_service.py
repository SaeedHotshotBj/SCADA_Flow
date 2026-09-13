import json
from datetime import datetime

from database import get_connection, get_company_flow

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    from pymodbus.client.sync import ModbusTcpClient


def _flow_nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        flow = json.loads(flow)
    return flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}


def _node_config(node):
    data = node.get("data", {}) or {}
    config = data.get("config", data) or {}
    return config if isinstance(config, dict) else {}


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _numeric_code(value, field_name):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    try:
        numeric = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric because it is written to one holding register") from exc
    if not 0 <= numeric <= 65535:
        raise ValueError(f"{field_name} must be between 0 and 65535")
    return numeric


def get_production_context_definition(company_id):
    nodes = _flow_nodes(company_id)
    context_nodes = []
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("name") != "ProductionContext":
            continue
        config = _node_config(node)
        context_nodes.append({
            "node_id": str(node_id),
            "plc_id": _to_int(config.get("plc_id", config.get("PLC_ID"))),
            "contract_code_register": _to_int(config.get("contract_code_register")),
            "product_code_register": _to_int(config.get("product_code_register")),
        })
    if not context_nodes:
        raise ValueError("ProductionContext node is not configured in the Flow")
    if len(context_nodes) > 1:
        raise ValueError("Only one ProductionContext node is supported per company Flow")
    context = context_nodes[0]
    if context["plc_id"] is None:
        raise ValueError("ProductionContext PLC ID is not configured")
    if context["contract_code_register"] is None or context["product_code_register"] is None:
        raise ValueError("ProductionContext contract/product registers are not configured")
    return context


def get_plc_connection_config(company_id, plc_id):
    nodes = _flow_nodes(company_id)
    matches = []
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("name") != "PLCReader":
            continue
        config = _node_config(node)
        node_plc_id = _to_int(config.get("plc_id", config.get("PLC_ID")))
        if node_plc_id == plc_id:
            matches.append(config)
    if len(matches) != 1:
        raise ValueError("Flow must contain exactly one PLCReader for the selected ProductionContext PLC")
    config = matches[0]
    ip = str(config.get("ip", "")).strip()
    port = _to_int(config.get("port", 502))
    slave = _to_int(config.get("slave", 1))
    if not ip or port is None or slave is None:
        raise ValueError("The matching PLCReader has incomplete PLC connection settings")
    return {"ip": ip, "port": port, "slave": slave}


def get_contract_product_options(company_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.ContractCode, c.ContractName, p.ProductCode, p.ProductName, p.Unit
            FROM Contracts c
            JOIN ContractProducts cp ON cp.ContractID=c.ContractID
            JOIN Products p ON p.ProductID=cp.ProductID
            WHERE c.CompanyID=?
            ORDER BY c.ContractCode, p.ProductCode
            """,
            (company_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _validate_pair(company_id, contract_code, product_code):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM Contracts c
            JOIN ContractProducts cp ON cp.ContractID=c.ContractID
            JOIN Products p ON p.ProductID=cp.ProductID
            WHERE c.CompanyID=?
              AND TRIM(CAST(c.ContractCode AS TEXT))=?
              AND TRIM(CAST(p.ProductCode AS TEXT))=?
            LIMIT 1
            """,
            (company_id, str(contract_code).strip(), str(product_code).strip()),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def write_production_context(company_id, contract_code, product_code):
    context = get_production_context_definition(company_id)
    contract_numeric = _numeric_code(contract_code, "Contract code")
    product_numeric = _numeric_code(product_code, "Product code")

    if not _validate_pair(company_id, str(contract_code), str(product_code)):
        raise ValueError("Selected product is not defined for the selected contract")

    plc = get_plc_connection_config(company_id, context["plc_id"])
    client = ModbusTcpClient(plc["ip"], port=plc["port"], timeout=3)
    try:
        if not client.connect():
            raise ConnectionError(f"Unable to connect to PLC {plc['ip']}:{plc['port']}")

        def _write(address, value):
            try:
                result = client.write_register(address=address, value=value, slave=plc["slave"])
            except TypeError:
                result = client.write_register(address, value, unit=plc["slave"])
            if result.isError():
                raise RuntimeError(f"PLC write error at register {address}: {result}")

        _write(context["contract_code_register"], contract_numeric)
        _write(context["product_code_register"], product_numeric)

        return {
            "status": "ok",
            "PLC_ID": context["plc_id"],
            "ContractCode": str(contract_code).strip(),
            "ProductCode": str(product_code).strip(),
            "ContractRegister": context["contract_code_register"],
            "ProductRegister": context["product_code_register"],
            "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        client.close()
