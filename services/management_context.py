# =====================================================
# MANAGEMENT PANEL CONTEXT
# Reads ContractCode/ProductCode registers configured by
# ManagementPanel and attaches them to historian writes.
# =====================================================

import json
import threading

from database import get_connection, insert_tag_value as _database_insert_tag_value
from database import get_company_flow


_context = threading.local()
_installed = False
_originals = {}


def set_context(company_id, registers):
    """Set the current ManagementPanel context for the active flow thread."""
    try:
        company_id = int(company_id)
    except (TypeError, ValueError):
        clear_context()
        return

    _context.company_id = company_id
    _context.values = _read_context(company_id, registers)


def clear_context():
    for name in ("company_id", "values"):
        try:
            delattr(_context, name)
        except AttributeError:
            pass


def current_context():
    values = getattr(_context, "values", None)
    return dict(values) if isinstance(values, dict) else {}


def _register_value(registers, register):
    if register in (None, ""):
        return None
    key = str(register)
    if key in registers:
        return registers[key]
    try:
        numeric_key = int(register)
        if numeric_key in registers:
            return registers[numeric_key]
    except (TypeError, ValueError):
        pass
    return None


def _read_context(company_id, registers):
    result = {"ContractCode": None, "ProductCode": None}

    try:
        flow_json = get_company_flow(company_id)
        if not flow_json:
            return result

        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})

        management_config = None
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "ManagementPanel":
                continue
            data = node.get("data", {}) or {}
            management_config = data.get("config", data) or {}

        if not isinstance(management_config, dict):
            return result

        contract_value = _register_value(
            registers or {},
            management_config.get("contract_code_register"),
        )
        product_value = _register_value(
            registers or {},
            management_config.get("product_code_register"),
        )

        if contract_value not in (None, ""):
            result["ContractCode"] = str(contract_value).strip()
        if product_value not in (None, ""):
            result["ProductCode"] = str(product_value).strip()

    except Exception as exc:
        print("MANAGEMENT CONTEXT ERROR:", exc)

    return result


def _ensure_context_columns(conn):
    columns = {
        row["name"]
        for row in conn.execute('PRAGMA table_info("TagHistory")').fetchall()
    }
    if "ContractCode" not in columns:
        conn.execute('ALTER TABLE "TagHistory" ADD COLUMN ContractCode TEXT')
    if "ProductCode" not in columns:
        conn.execute('ALTER TABLE "TagHistory" ADD COLUMN ProductCode TEXT')


def _store_tag_history(company_id, tag, value, timestamp, context):
    conn = get_connection()
    try:
        _ensure_context_columns(conn)
        conn.execute(
            """
            INSERT INTO TagHistory
            (
                CompanyID,
                PLC_ID,
                TagName,
                Value,
                Timestamp,
                ContractCode,
                ProductCode
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                int(company_id),
                str(tag),
                float(value),
                timestamp,
                context.get("ContractCode"),
                context.get("ProductCode"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _context_aware_insert(company_id, tag, value, storage_type, timestamp=None):
    # Preserve the existing PLC_Data write exactly as before.
    _database_insert_tag_value(
        company_id,
        tag,
        value,
        storage_type,
        timestamp=timestamp,
    )

    context = current_context()
    if not context:
        return

    if context.get("ContractCode") in (None, "") and context.get("ProductCode") in (None, ""):
        return

    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        _store_tag_history(company_id, tag, value, timestamp, context)
        print(
            "MANAGEMENT CONTEXT SAVED:",
            "Company=", company_id,
            "Tag=", tag,
            "ContractCode=", context.get("ContractCode"),
            "ProductCode=", context.get("ProductCode"),
        )
    except Exception as exc:
        print("MANAGEMENT CONTEXT TAGHISTORY ERROR:", exc)


def _update_new_report_history_rows(company_id, first_report_id, context):
    """Attach the active ManagementPanel context to ReportHistory rows created
    during this SQLWriter execution. This is intentionally done by ReportID,
    so it also covers report snapshots created by the existing trigger wrapper.
    """
    if not context:
        return

    contract_code = context.get("ContractCode")
    product_code = context.get("ProductCode")
    if contract_code in (None, "") and product_code in (None, ""):
        return

    conn = get_connection()
    try:
        columns = {
            row["name"]
            for row in conn.execute(
                'PRAGMA table_info("ReportHistory")'
            ).fetchall()
        }

        if "ContractCode" not in columns or "ProductCode" not in columns:
            return

        if first_report_id is None:
            conn.execute(
                "UPDATE ReportHistory SET ContractCode = ?, ProductCode = ? WHERE CompanyID = ? AND (ContractCode IS NULL OR ContractCode = '')",
                (contract_code, product_code, int(company_id)),
            )
        else:
            conn.execute(
                "UPDATE ReportHistory SET ContractCode = ?, ProductCode = ? WHERE CompanyID = ? AND ReportID > ?",
                (contract_code, product_code, int(company_id), int(first_report_id)),
            )

        conn.commit()
        print(
            "MANAGEMENT REPORT CONTEXT SAVED:",
            "Company=", company_id,
            "FromReportID=", first_report_id,
            "ContractCode=", contract_code,
            "ProductCode=", product_code,
        )
    except Exception as exc:
        print("MANAGEMENT REPORT CONTEXT ERROR:", exc)
    finally:
        conn.close()


def _latest_report_id(company_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(ReportID) AS ReportID FROM ReportHistory WHERE CompanyID = ?",
            (int(company_id),),
        ).fetchone()
        if row is None:
            return None
        return row["ReportID"]
    except Exception:
        return None
    finally:
        conn.close()


def install():
    """Patch only the historian/SQLWriter insert references."""
    global _installed
    if _installed:
        return

    try:
        import services.historian_service as historian_module
        import flow_engine.nodes.sql_writer as sql_writer_module

        for module, name in (
            (historian_module, "insert_tag_value"),
            (sql_writer_module, "insert_tag_value"),
        ):
            original = getattr(module, name, None)
            if original is None or original is _context_aware_insert:
                continue
            _originals[(module.__name__, name)] = original
            setattr(module, name, _context_aware_insert)

        _installed = True
        print("MANAGEMENT CONTEXT PATCH INSTALLED")
    except Exception as exc:
        print("MANAGEMENT CONTEXT PATCH ERROR:", exc)


# =====================================================
# SQL WRITER WRAPPER
# =====================================================

from flow_engine.nodes.sql_writer import SQLWriter as _BaseSQLWriter


class ManagementSQLWriter(_BaseSQLWriter):
    """Existing SQLWriter plus ManagementPanel Contract/Product context."""

    def __init__(self, config=None):
        install()
        super().__init__(config)

    def execute(self, data=None):
        payload = data if isinstance(data, dict) else {}
        registers = payload.get("Registers", payload.get("registers", {})) or {}
        company_id = self.company_id

        context = _read_context(company_id, registers)
        set_context(company_id, registers)
        before_report_id = _latest_report_id(company_id)
        try:
            result = super().execute(data)
            _update_new_report_history_rows(
                company_id,
                before_report_id,
                context,
            )
            return result
        finally:
            clear_context()
