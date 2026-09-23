import ast
import json
import math
import sqlite3
from datetime import datetime

from database import get_connection, get_company_flow

_CONTEXT_CONTRACT_ROLES = {"contract", "contract_code", "contractid", "contract_id"}
_CONTEXT_PRODUCT_ROLES = {"product", "product_code", "productid", "product_id"}

TAGMAPPER_EVENT_NODE_ID = "__TAGMAPPER_EVENT__"


def _flow_nodes(company_id):
    flow = get_company_flow(company_id)
    if not flow:
        return {}
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            return {}
    return flow.get("drawflow", {}).get("Home", {}).get("data", {}) or {}


def _plc_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _roles(value):
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    text = str(value or "").replace(";", ",").replace("\n", ",")
    return {item.strip().lower() for item in text.split(",") if item.strip()}


def _allowed(item, user_role):
    configured = _roles(item.get("allowed_roles")) if isinstance(item, dict) else set()
    if not configured:
        return True
    return str(user_role or "").strip().lower() in configured


def _tagmapper_names(company_id, plc_id=None):
    """Return TagMapper-defined tag names for the report's PLC."""
    names = set()
    for node in _flow_nodes(company_id).values():
        if not isinstance(node, dict) or node.get("name") != "TagMapper":
            continue
        data = node.get("data", {}) or {}
        config = data.get("config", data) or {}
        mappings = config.get("mappings", []) if isinstance(config, dict) else []
        if not isinstance(mappings, list):
            continue
        for item in mappings:
            if not isinstance(item, dict):
                continue
            mapping_plc_id = _plc_id(item.get("plc_id", item.get("PLC_ID")))
            if plc_id is not None and mapping_plc_id != plc_id:
                continue
            tag_name = str(item.get("name", "")).strip()
            if tag_name:
                names.add(tag_name)
    return names


def _collect_tagmapper_values(tags, company_id, plc_id):
    """Return every numeric TagMapper value available in the report payload."""
    lookup = {
        str(key).strip().lower(): value
        for key, value in (tags or {}).items()
    }
    values = []
    seen = set()

    for tag_name in sorted(_tagmapper_names(company_id, plc_id), key=str.lower):
        key = tag_name.lower()
        if key in seen:
            continue
        value = lookup.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        values.append((tag_name, number))
        seen.add(key)

    return values


def persist_tagmapper_snapshot(
    company_id,
    tags,
    plc_id,
    timestamp=None,
    trigger_event_id=None,
):
    """Persist numeric TagMapper values independently of ReportOutput."""
    if company_id is None or not isinstance(tags, dict):
        return 0

    ensure_report_tables()
    timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plc_id = _plc_id(plc_id)
    contract = tags.get("ContractCode")
    product = tags.get("ProductCode")
    tag_values = _collect_tagmapper_values(tags, company_id, plc_id)
    if not tag_values:
        return 0

    conn = get_connection()
    try:
        _persist_tagmapper_values(
            conn,
            company_id,
            plc_id,
            timestamp,
            contract,
            product,
            tag_values,
            trigger_event_id=trigger_event_id,
            report_node_id=TAGMAPPER_EVENT_NODE_ID,
        )
        conn.commit()
        return len(tag_values)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _persist_tagmapper_values(
    conn,
    company_id,
    plc_id,
    timestamp,
    contract_code,
    product_code,
    tag_values,
    trigger_event_id=None,
    report_node_id=None,
):
    if not tag_values:
        return

    event_id = str(trigger_event_id) if trigger_event_id else None
    node_id = str(report_node_id) if report_node_id else None

    for name, value in tag_values:
        tag_name = str(name).strip()
        if not tag_name:
            continue

        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue

        existing = None
        if event_id and node_id:
            existing = conn.execute(
                """
                SELECT TagMapperValueID
                FROM TagMapperValues
                WHERE CompanyID=?
                  AND TriggerEventID=?
                  AND ReportNodeID=?
                  AND LOWER(TagName)=LOWER(?)
                ORDER BY TagMapperValueID
                LIMIT 1
                """,
                (int(company_id), event_id, node_id, tag_name),
            ).fetchone()

        if existing:
            # The first event snapshot can be written before TagMapper/context
            # is available. A later SQLWriter pass must enrich that same row
            # instead of skipping it because the Tag already exists.
            conn.execute(
                """
                UPDATE TagMapperValues
                SET PLC_ID=?,
                    Timestamp=?,
                    ContractCode=CASE
                        WHEN ? IS NOT NULL AND TRIM(CAST(? AS TEXT))<>'' THEN ?
                        ELSE ContractCode
                    END,
                    ProductCode=CASE
                        WHEN ? IS NOT NULL AND TRIM(CAST(? AS TEXT))<>'' THEN ?
                        ELSE ProductCode
                    END,
                    Value=?
                WHERE TagMapperValueID=?
                """,
                (
                    plc_id,
                    timestamp,
                    contract_code,
                    contract_code,
                    contract_code,
                    product_code,
                    product_code,
                    product_code,
                    number,
                    int(existing["TagMapperValueID"]),
                ),
            )
            continue

        conn.execute(
            """
            INSERT INTO TagMapperValues
            (
                CompanyID,
                PLC_ID,
                Timestamp,
                ContractCode,
                ProductCode,
                TagName,
                Value,
                TriggerEventID,
                ReportNodeID
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(company_id),
                plc_id,
                timestamp,
                contract_code,
                product_code,
                tag_name,
                number,
                event_id,
                node_id,
            ),
        )


def _management_calculations(company_id, user_role=None):
    result = []
    seen = set()
    for node in _flow_nodes(company_id).values():
        if not isinstance(node, dict) or node.get("name") != "ManagementPanel":
            continue
        data = node.get("data", {}) or {}
        config = data.get("config", data) or {}
        calculations = config.get("calculations", []) if isinstance(config, dict) else []
        if not isinstance(calculations, list):
            continue
        for item in calculations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", item.get("result_name", ""))).strip()
            expression = str(item.get("expression", "")).strip()
            if not name or not expression or name.lower() in seen:
                continue
            if not _allowed(item, user_role):
                continue
            seen.add(name.lower())
            result.append({
                "name": name,
                "label": str(item.get("label", name)).strip() or name,
                "expression": expression,
                "unit": str(item.get("unit", "")).strip(),
                "allowed_roles": item.get("allowed_roles", ""),
                "source": "management_calculation",
            })
    return result


def _all_management_calculations(company_id):
    return _management_calculations(company_id, None)


def _safe_eval(expression, variables):
    tree = ast.parse(expression, mode="eval")
    allowed_nodes = {
        ast.Expression,
        ast.Constant,
        ast.Name,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Mod,
        ast.Pow,
        ast.USub,
        ast.UAdd,
        ast.FloorDiv,
    }
    for node in ast.walk(tree):
        if not isinstance(node, tuple(allowed_nodes)):
            raise ValueError("Unsupported expression operation")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("Expression must contain numeric constants only")
        if isinstance(node, ast.Name) and node.id not in variables:
            raise ValueError(f"Unknown variable: {node.id}")
    value = eval(compile(tree, "<flow-report-expression>", "eval"), {"__builtins__": {}}, variables)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Expression result is not finite")
    return value


def _formula_variables(tags, duration_seconds=0.0):
    variables = {}
    for name, value in (tags or {}).items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        key = str(name).strip()
        if not key:
            continue
        variables[key] = number
        alias = "".join(char if (char.isalnum() or char == "_") else "_" for char in key)
        if alias and alias[0].isdigit():
            alias = "_" + alias
        if alias:
            variables.setdefault(alias, number)
    duration = float(duration_seconds or 0.0)
    variables.update({
        "DurationSeconds": duration,
        "WorkTimeSeconds": duration,
        "WorkTimeMinutes": duration / 60.0,
        "WorkTimeHours": duration / 3600.0,
        "ProductionTimeSeconds": duration,
    })
    return variables


def ensure_report_tables():
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ReportHistory(
                ReportID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER,
                PLC_ID INTEGER,
                Timestamp TEXT NOT NULL,
                TriggerTag TEXT,
                TriggerRegister TEXT,
                TriggerValue REAL,
                TriggerEdge TEXT,
                StartTimestamp TEXT,
                EndTimestamp TEXT,
                DurationSeconds REAL DEFAULT 0,
                StartComplete INTEGER DEFAULT 1,
                TriggerEventID TEXT,
                ReportNodeID TEXT,
                ContractCode TEXT,
                ProductCode TEXT,
                FOREIGN KEY(CompanyID) REFERENCES Companies(CompanyID)
            );
            CREATE TABLE IF NOT EXISTS ReportValues(
                ReportValueID INTEGER PRIMARY KEY AUTOINCREMENT,
                ReportID INTEGER NOT NULL,
                TagName TEXT NOT NULL,
                Value REAL,
                FOREIGN KEY(ReportID) REFERENCES ReportHistory(ReportID) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS TagMapperValues(
                TagMapperValueID INTEGER PRIMARY KEY AUTOINCREMENT,
                CompanyID INTEGER NOT NULL,
                PLC_ID INTEGER,
                Timestamp TEXT NOT NULL,
                ContractCode TEXT,
                ProductCode TEXT,
                TagName TEXT NOT NULL,
                Value REAL,
                TriggerEventID TEXT,
                ReportNodeID TEXT
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ReportHistory)").fetchall()}
        for name, typ in [
            ("TriggerEdge", "TEXT"),
            ("StartTimestamp", "TEXT"),
            ("EndTimestamp", "TEXT"),
            ("DurationSeconds", "REAL"),
            ("StartComplete", "INTEGER"),
            ("TriggerEventID", "TEXT"),
            ("ReportNodeID", "TEXT"),
        ]:
            if name not in columns:
                conn.execute(f"ALTER TABLE ReportHistory ADD COLUMN {name} {typ}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_history_company_plc_time "
            "ON ReportHistory(CompanyID, PLC_ID, Timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_values_report_tag "
            "ON ReportValues(ReportID, TagName)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tagmapper_values_company_context_tag_time "
            "ON TagMapperValues(CompanyID, ContractCode, ProductCode, TagName, Timestamp)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_report_event_node "
            "ON ReportHistory(TriggerEventID, ReportNodeID) "
            "WHERE TriggerEventID IS NOT NULL AND ReportNodeID IS NOT NULL"
        )

        # Backfill production-event TagMapper snapshots independently of ReportOutput.
        production_events = []
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ProductionEvents' LIMIT 1"
        ).fetchone():
            production_events = conn.execute(
                """
                SELECT EventID, CompanyID, PLC_ID, TriggerTimestamp, TagsJSON
                FROM ProductionEvents
                WHERE TagsJSON IS NOT NULL AND TRIM(TagsJSON) <> ''
                """
            ).fetchall()

        for event in production_events:
            existing_event = conn.execute(
                """
                SELECT 1
                FROM TagMapperValues
                WHERE CompanyID=?
                  AND TriggerEventID=?
                  AND ReportNodeID=?
                LIMIT 1
                """,
                (
                    int(event["CompanyID"]),
                    str(event["EventID"]),
                    TAGMAPPER_EVENT_NODE_ID,
                ),
            ).fetchone()
            if existing_event:
                continue

            try:
                event_tags = json.loads(event["TagsJSON"] or "{}")
            except Exception:
                event_tags = {}
            if not isinstance(event_tags, dict):
                continue

            tag_values = _collect_tagmapper_values(
                event_tags,
                event["CompanyID"],
                event["PLC_ID"],
            )
            _persist_tagmapper_values(
                conn,
                event["CompanyID"],
                event["PLC_ID"],
                event["TriggerTimestamp"],
                event_tags.get("ContractCode"),
                event_tags.get("ProductCode"),
                tag_values,
                trigger_event_id=str(event["EventID"]),
                report_node_id=TAGMAPPER_EVENT_NODE_ID,
            )

        # Repair TagMapper rows that were written before the complete event
        # context was available. ReportHistory is the authoritative event
        # context for the same TriggerEventID + ReportNodeID.
        conn.execute(
            """
            UPDATE TagMapperValues
            SET PLC_ID=COALESCE(
                    PLC_ID,
                    (
                        SELECT h.PLC_ID
                        FROM ReportHistory h
                        WHERE h.TriggerEventID=TagMapperValues.TriggerEventID
                          AND h.ReportNodeID=TagMapperValues.ReportNodeID
                        ORDER BY h.ReportID DESC
                        LIMIT 1
                    )
                ),
                ContractCode=COALESCE(
                    NULLIF(TRIM(CAST(ContractCode AS TEXT)), ''),
                    (
                        SELECT h.ContractCode
                        FROM ReportHistory h
                        WHERE h.TriggerEventID=TagMapperValues.TriggerEventID
                          AND h.ReportNodeID=TagMapperValues.ReportNodeID
                        ORDER BY h.ReportID DESC
                        LIMIT 1
                    )
                ),
                ProductCode=COALESCE(
                    NULLIF(TRIM(CAST(ProductCode AS TEXT)), ''),
                    (
                        SELECT h.ProductCode
                        FROM ReportHistory h
                        WHERE h.TriggerEventID=TagMapperValues.TriggerEventID
                          AND h.ReportNodeID=TagMapperValues.ReportNodeID
                        ORDER BY h.ReportID DESC
                        LIMIT 1
                    )
                )
            WHERE TriggerEventID IS NOT NULL
              AND ReportNodeID IS NOT NULL
        """
        )

        legacy_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ReportTagValues' LIMIT 1"
        ).fetchone()
        if legacy_exists:
            conn.execute(
                """
                INSERT INTO TagMapperValues
                (
                    CompanyID,
                    PLC_ID,
                    Timestamp,
                    ContractCode,
                    ProductCode,
                    TagName,
                    Value,
                    TriggerEventID,
                    ReportNodeID
                )
                SELECT
                    h.CompanyID,
                    v.PLC_ID,
                    h.Timestamp,
                    h.ContractCode,
                    h.ProductCode,
                    v.TagName,
                    v.Value,
                    h.TriggerEventID,
                    h.ReportNodeID
                FROM ReportTagValues v
                INNER JOIN ReportHistory h ON h.ReportID = v.ReportID
                """
            )
            conn.execute("DROP TABLE ReportTagValues")
        conn.commit()
    finally:
        conn.close()


def get_report_products(company_id, user_role=None):
    products = []
    seen = set()
    try:
        for node_id, node in _flow_nodes(company_id).items():
            if not isinstance(node, dict) or node.get("name") != "ReportOutput":
                continue
            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            configured = config.get("products", []) if isinstance(config, dict) else []
            if not isinstance(configured, list):
                continue
            for item in configured:
                if not isinstance(item, dict):
                    continue
                tag = str(item.get("tag", "")).strip()
                if not tag or not _allowed(item, user_role):
                    continue
                result = {
                    "name": str(item.get("name", tag)).strip() or tag,
                    "tag": tag,
                    "plc_id": _plc_id(item.get("plc_id", item.get("PLC_ID"))),
                    "unit": str(item.get("unit", "")).strip(),
                    "context_role": str(item.get("context_role", item.get("context", ""))).strip().lower(),
                    "allowed_roles": item.get("allowed_roles", ""),
                    "report_node_id": str(node_id),
                }
                key = (tag.lower(), result["plc_id"], result["context_role"])
                if key not in seen:
                    seen.add(key)
                    products.append(result)

        for calculation in _management_calculations(company_id, user_role):
            key = (calculation["name"].lower(), None, "")
            if key in seen:
                continue
            seen.add(key)
            products.append(calculation)
        return products
    except Exception as exc:
        print("REPORT CONFIG ERROR:", exc)
        return []


def _context(report_products, tags):
    lookup = {str(key).strip().lower(): value for key, value in (tags or {}).items()}
    contract = None
    product = None
    for item in report_products or []:
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        tag = str(item.get("tag", "")).strip().lower()
        if role in _CONTEXT_CONTRACT_ROLES and lookup.get(tag) not in (None, ""):
            contract = str(lookup[tag]).strip()
        if role in _CONTEXT_PRODUCT_ROLES and lookup.get(tag) not in (None, ""):
            product = str(lookup[tag]).strip()
    return contract, product


def save_report_snapshot(
    company_id,
    tags,
    report_products,
    timestamp=None,
    trigger_tag=None,
    trigger_register=None,
    trigger_value=None,
    plc_id=None,
    trigger_edge=None,
    start_timestamp=None,
    end_timestamp=None,
    duration_seconds=0,
    start_complete=1,
    trigger_event_id=None,
    report_node_id=None,
):
    if company_id is None or not isinstance(tags, dict):
        return None

    ensure_report_tables()
    all_products = [item for item in (report_products or []) if isinstance(item, dict)]
    calculation_definitions = _all_management_calculations(company_id)

    existing_report_id = None
    if trigger_event_id and report_node_id:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT ReportID FROM ReportHistory WHERE TriggerEventID=? AND ReportNodeID=? LIMIT 1",
                (str(trigger_event_id), str(report_node_id)),
            ).fetchone()
            if row:
                existing_report_id = int(row["ReportID"])
        finally:
            conn.close()

    duration = float(duration_seconds or 0.0)
    timestamp = timestamp or end_timestamp or start_timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plc_id = _plc_id(plc_id)
    contract, product = _context(all_products, tags)
    if contract in (None, ""):
        contract = tags.get("ContractCode")
    if product in (None, ""):
        product = tags.get("ProductCode")
    lookup = {str(key).strip().lower(): (key, value) for key, value in tags.items()}
    values = []
    used_names = set()

    # Normal ReportOutput columns.
    for item in all_products:
        tag = str(item.get("tag", "")).strip()
        role = str(item.get("context_role", item.get("context", ""))).strip().lower()
        source = item.get("source")
        item_plc = _plc_id(item.get("plc_id", item.get("PLC_ID", plc_id)))
        if role or source == "management_calculation" or not tag:
            continue
        if item_plc is not None and plc_id is not None and item_plc != plc_id:
            continue
        found = lookup.get(tag.lower())
        if found is None or found[1] is None:
            continue
        try:
            values.append((str(item.get("name", tag)).strip() or tag, float(found[1])))
            used_names.add(tag.lower())
        except (TypeError, ValueError):
            pass

    tagmapper_values = _collect_tagmapper_values(tags, company_id, plc_id)

    # Flow-designed ManagementPanel calculations become persisted report columns.
    variables = _formula_variables(tags, duration)
    for calculation in calculation_definitions:
        try:
            result = _safe_eval(calculation["expression"], variables)
            values.append((calculation["name"], result))
            variables[calculation["name"]] = result
            alias = "".join(char if (char.isalnum() or char == "_") else "_" for char in calculation["name"])
            if alias:
                variables[alias] = result
        except Exception as exc:
            print(
                "REPORT CALCULATION ERROR:",
                calculation["name"],
                calculation["expression"],
                exc,
            )

    # Persist common lifecycle values as report values too, so they are
    # available to older report layouts even when not explicitly configured.
    lifecycle_values = {
        "WorkTimeSeconds": duration,
        "WorkTimeMinutes": duration / 60.0,
        "WorkTimeHours": duration / 3600.0,
    }
    for name, value in lifecycle_values.items():
        if name.lower() not in {key.lower() for key, _ in values}:
            values.append((name, value))

    if existing_report_id is not None:
        # The first call for this event may happen before SQLWriter has placed
        # the current TagMapper/context values into the payload. A later call
        # has the complete TagMapper payload; use it to finish the independent
        # TagMapperValues store instead of returning too early.
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE ReportHistory
                SET ContractCode=COALESCE(ContractCode, ?),
                    ProductCode=COALESCE(ProductCode, ?)
                WHERE ReportID=?
                """,
                (contract, product, existing_report_id),
            )
            _persist_tagmapper_values(
                conn,
                company_id,
                plc_id,
                timestamp,
                contract,
                product,
                tagmapper_values,
                trigger_event_id=trigger_event_id,
                report_node_id=report_node_id,
            )
            conn.commit()
            return existing_report_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    if not values:
        return None

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO ReportHistory
            (CompanyID, PLC_ID, Timestamp, TriggerTag, TriggerRegister,
             TriggerValue, TriggerEdge, StartTimestamp, EndTimestamp,
             DurationSeconds, StartComplete, TriggerEventID, ReportNodeID,
             ContractCode, ProductCode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                plc_id,
                timestamp,
                trigger_tag,
                trigger_register,
                trigger_value,
                trigger_edge,
                start_timestamp,
                end_timestamp,
                duration,
                int(bool(start_complete)),
                str(trigger_event_id) if trigger_event_id else None,
                str(report_node_id) if report_node_id else None,
                contract,
                product,
            ),
        )
        report_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO ReportValues(ReportID,TagName,Value) VALUES(?,?,?)",
            [(name, value) for name, value in values],
        )
        _persist_tagmapper_values(
            conn,
            company_id,
            plc_id,
            timestamp,
            contract,
            product,
            tagmapper_values,
            trigger_event_id=trigger_event_id,
            report_node_id=report_node_id,
        )
        conn.commit()
        return report_id
    except sqlite3.IntegrityError:
        conn.rollback()
        if trigger_event_id and report_node_id:
            row = conn.execute(
                "SELECT ReportID FROM ReportHistory WHERE TriggerEventID=? AND ReportNodeID=? LIMIT 1",
                (str(trigger_event_id), str(report_node_id)),
            ).fetchone()
            return int(row["ReportID"]) if row else None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_report_data(company_id, start, end, plc_id=None, user_role=None):
    products = [item for item in get_report_products(company_id, user_role) if not item.get("context_role")]
    if plc_id is not None:
        products = [item for item in products if item.get("plc_id") in (None, int(plc_id))]

    result = {
        "columns": products,
        "rows": [],
        "totals": [0.0 for _ in products],
        "grand_total": 0.0,
    }
    if company_id is None or not products or not start or not end:
        return result

    ensure_report_tables()
    keys = [str(item.get("name", item.get("tag", ""))).strip().lower() for item in products]
    placeholders = ",".join("?" for _ in keys)

    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT h.ReportID, h.Timestamp, h.ContractCode, h.ProductCode,
                   h.PLC_ID, h.TriggerTag, h.TriggerRegister, h.TriggerValue,
                   h.TriggerEdge, h.StartTimestamp, h.EndTimestamp,
                   h.DurationSeconds, h.StartComplete, h.TriggerEventID,
                   v.TagName, v.Value, v.ReportValueID
            FROM ReportHistory h
            LEFT JOIN ReportValues v ON v.ReportID=h.ReportID
            WHERE h.CompanyID=?
              AND datetime(h.Timestamp)>=datetime(?)
              AND datetime(h.Timestamp)<=datetime(?)
              AND (v.TagName IS NULL OR LOWER(v.TagName) IN ({placeholders}))
            ORDER BY datetime(h.Timestamp), h.ReportID, COALESCE(v.ReportValueID,0)
            """,
            [
                company_id,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
            ] + keys,
        ).fetchall()
    finally:
        conn.close()

    index = {key: position for position, key in enumerate(keys)}
    grouped = {}

    for row in rows:
        item = grouped.setdefault(
            row["ReportID"],
            {
                "timestamp": str(row["Timestamp"]),
                "PLC_ID": row["PLC_ID"],
                "contract_code": row["ContractCode"],
                "product_code": row["ProductCode"],
                "trigger_tag": row["TriggerTag"],
                "trigger_register": row["TriggerRegister"],
                "trigger_value": row["TriggerValue"],
                "trigger_edge": row["TriggerEdge"],
                "start_timestamp": row["StartTimestamp"],
                "end_timestamp": row["EndTimestamp"],
                "duration_seconds": row["DurationSeconds"],
                "start_complete": row["StartComplete"],
                "trigger_event_id": row["TriggerEventID"],
                "values": [None] * len(products),
            },
        )
        tag = str(row["TagName"] or "").strip().lower()
        if tag not in index:
            continue
        try:
            item["values"][index[tag]] = float(row["Value"])
        except (TypeError, ValueError):
            pass

    totals = [0.0] * len(products)
    for item in grouped.values():
        row_total = 0.0
        for position, value in enumerate(item["values"]):
            if value is not None:
                totals[position] += value
                row_total += value
        item["row_total"] = row_total
        result["rows"].append(item)

    result["totals"] = [round(value, 3) for value in totals]
    result["grand_total"] = round(sum(totals), 3)
    return result__all__ = [
    "TAGMAPPER_EVENT_NODE_ID",
    "persist_tagmapper_snapshot",
    "ensure_report_tables",
    "get_report_products",
    "get_report_data",
    "save_report_snapshot",
]
