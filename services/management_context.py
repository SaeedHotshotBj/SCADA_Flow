# =====================================================
# MANAGEMENT PANEL CONTEXT
# Reads ContractCode/ProductCode registers configured by
# ManagementPanel and attaches them to historian writes.
# =====================================================

import json
import threading
from datetime import datetime

from database import get_connection, insert_tag_value as _database_insert_tag_value
from database import get_company_flow


_context = threading.local()
_installed = False
_originals = {}


def set_context(company_id, registers):
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


def _trigger_register_for_tag(company_id, tag):
    """Return the TagMapper trigger register configured for a tag."""
    if company_id is None or not tag:
        return None

    try:
        flow_json = get_company_flow(company_id)
        if not flow_json:
            return None

        flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
        nodes = flow.get("drawflow", {}).get("Home", {}).get("data", {})

        wanted = str(tag).strip().lower()
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("name") != "TagMapper":
                continue

            mappings = (node.get("data", {}) or {}).get("mappings", [])
            if not isinstance(mappings, list):
                continue

            for mapping in mappings:
                if not isinstance(mapping, dict):
                    continue
                if str(mapping.get("name", "")).strip().lower() != wanted:
                    continue
                if str(mapping.get("storage", "")).strip().upper() != "TRIGGER":
                    return None
                value = mapping.get("trigger_register")
                if value in (None, ""):
                    return None
                try:
                    return str(int(float(value)))
                except (TypeError, ValueError):
                    return str(value).strip()

            break

    except Exception as exc:
        print("MANAGEMENT TRIGGER REGISTER ERROR:", exc)

    return None


def _backfill_report_context(company_id, tag, value, timestamp=None):
    """Complete the newest unfinished ReportHistory row for a context tag.

    Edge sends each TagMapper trigger tag in its own HTTP request. B1/B2/B3
    can therefore create ReportHistory before ContractCode/ProductCode arrive.
    This function repairs that ordering at the server boundary without creating
    a second report and without touching completed/older reports.
    """
    if company_id is None or tag is None or value in (None, ""):
        return None

    context_tag = str(tag).strip()
    field = {
        "contractcode": "ContractCode",
        "productcode": "ProductCode",
    }.get(context_tag.lower())
    if field is None:
        return None

    trigger_register = _trigger_register_for_tag(company_id, context_tag)

    if timestamp in (None, ""):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        columns = {
            row["name"]
            for row in conn.execute(
                'PRAGMA table_info("ReportHistory")'
            ).fetchall()
        }

        required = {
            "ReportID",
            "CompanyID",
            "Timestamp",
            "TriggerRegister",
            "ContractCode",
            "ProductCode",
        }
        if not required.issubset(columns):
            print(
                "REPORT CONTEXT BACKFILL SKIP - MISSING COLUMNS:",
                sorted(required - columns),
            )
            return None

        sql = f"""
            SELECT
                ReportID,
                Timestamp,
                TriggerTag,
                TriggerRegister,
                ContractCode,
                ProductCode
            FROM ReportHistory
            WHERE CompanyID = ?
              AND TriggerRegister IS NOT NULL
              AND TRIM(COALESCE({field}, '')) = ''
              AND datetime(Timestamp) >= datetime(?, '-15 seconds')
              AND datetime(Timestamp) <= datetime(?, '+2 seconds')
        """
        params = [int(company_id), str(timestamp), str(timestamp)]

        if trigger_register is not None:
            sql += " AND CAST(TriggerRegister AS TEXT) = ?"
            params.append(str(trigger_register))

        sql += " ORDER BY ReportID DESC LIMIT 1"
        report = conn.execute(sql, params).fetchone()

        if report is None:
            print(
                "REPORT CONTEXT BACKFILL NO MATCH:",
                "Company=", company_id,
                "Tag=", context_tag,
                "Value=", value,
                "TriggerRegister=", trigger_register,
                "Timestamp=", timestamp,
            )
            return None

        new_value = str(value).strip()
        update = conn.execute(
            f"""
            UPDATE ReportHistory
            SET {field} = ?
            WHERE ReportID = ?
              AND CompanyID = ?
              AND TRIM(COALESCE({field}, '')) = ''
            """,
            (new_value, int(report["ReportID"]), int(company_id)),
        )
        conn.commit()

        delta_seconds = None
        try:
            incoming_dt = datetime.fromisoformat(
                str(timestamp).replace("Z", "")
            )
            report_dt = datetime.fromisoformat(
                str(report["Timestamp"]).replace("Z", "")
            )
            delta_seconds = (incoming_dt - report_dt).total_seconds()
        except Exception:
            pass

        verified = conn.execute(
            """
            SELECT ReportID, ContractCode, ProductCode
            FROM ReportHistory
            WHERE ReportID = ?
              AND CompanyID = ?
            """,
            (int(report["ReportID"]), int(company_id)),
        ).fetchone()

        print(
            "REPORT CONTEXT BACKFILL:",
            "Company=", company_id,
            "ReportID=", report["ReportID"],
            "Field=", field,
            "Value=", new_value,
            "TriggerRegister=", report["TriggerRegister"],
            "ContextTimestamp=", timestamp,
            "ReportTimestamp=", report["Timestamp"],
            "DeltaSeconds=", delta_seconds,
            "RowsUpdated=", update.rowcount,
            "Verified=", dict(verified) if verified is not None else None,
        )
        return dict(verified) if verified is not None else None

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(
            "REPORT CONTEXT BACKFILL ERROR:",
            "Company=", company_id,
            "Tag=", context_tag,
            "Error=", repr(exc),
        )
        return None
    finally:
        conn.close()


def _context_aware_insert(company_id, tag, value, storage_type, timestamp=None):
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
        from datetime import datetime as _datetime
        timestamp = _datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

        # Context is needed while SQLWriter performs its own historian writes.
        set_context(company_id, registers)
        try:
            result = super().execute(data)

            # The Edge sends ContractCode/ProductCode as independent HTTP
            # messages. If a Trigger report was already created by B1/B2/B3,
            # complete that exact unfinished row now.
            output = result if isinstance(result, dict) else payload
            output_tags = output.get("Tags", {}) if isinstance(output, dict) else {}
            if isinstance(output_tags, dict):
                context_timestamp = output.get("Timestamp") if isinstance(output, dict) else None
                for context_tag in ("ContractCode", "ProductCode"):
                    if context_tag in output_tags and output_tags[context_tag] not in (None, ""):
                        _backfill_report_context(
                            company_id,
                            context_tag,
                            output_tags[context_tag],
                            timestamp=context_timestamp,
                        )

            return result
        finally:
            clear_context()
