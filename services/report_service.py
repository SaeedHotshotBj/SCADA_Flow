# SCADA_FLOW REPORT SERVICE WRAPPER
# Delegates legacy report behavior while adding ManagementPanel
# ContractCode/ProductCode context to ReportHistory.

import json

from services import report_service_legacy as _legacy
from database import get_connection, get_company_flow


_CONTEXT_CONTRACT_ROLES = {
    "contract",
    "contract_code",
    "contractid",
    "contract_id",
}

_CONTEXT_PRODUCT_ROLES = {
    "product",
    "product_code",
    "productid",
    "product_id",
}


def ensure_report_tables():
    _legacy.ensure_report_tables()

    conn = get_connection()
    try:
        columns = {
            row["name"]
            for row in conn.execute(
                'PRAGMA table_info("ReportHistory")'
            ).fetchall()
        }

        if "ContractCode" not in columns:
            conn.execute(
                'ALTER TABLE "ReportHistory" ADD COLUMN ContractCode TEXT'
            )

        if "ProductCode" not in columns:
            conn.execute(
                'ALTER TABLE "ReportHistory" ADD COLUMN ProductCode TEXT'
            )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_report_history_company_context
            ON ReportHistory (
                CompanyID,
                ContractCode,
                ProductCode,
                Timestamp
            )
            """
        )

        conn.commit()

    finally:
        conn.close()


def _flow_nodes(company_id):
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


def get_report_products(company_id):
    products = []

    try:
        nodes = _flow_nodes(company_id)

        for node in nodes.values():
            if not isinstance(node, dict):
                continue

            if node.get("name") != "ReportOutput":
                continue

            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            configured = config.get("products", [])

            if not isinstance(configured, list):
                continue

            for item in configured:
                if not isinstance(item, dict):
                    continue

                tag = str(item.get("tag", "")).strip()
                if not tag:
                    continue

                products.append({
                    "name": str(
                        item.get("name", tag)
                    ).strip() or tag,
                    "tag": tag,
                    "unit": str(
                        item.get("unit", "")
                    ).strip(),
                    "context_role": str(
                        item.get(
                            "context_role",
                            item.get("context", "")
                        )
                    ).strip().lower(),
                })

        unique = []
        seen = set()

        for product in products:
            key = (
                product["tag"].lower(),
                product.get("context_role", ""),
            )

            if key in seen:
                continue

            seen.add(key)
            unique.append(product)

        return unique

    except Exception as exc:
        print("REPORT CONFIG ERROR:", exc)
        return []


def _latest_tag_value(conn, company_id, tag_name):
    if not tag_name:
        return None

    row = conn.execute(
        """
        SELECT Value
        FROM PLC_Data
        WHERE CompanyID = ?
          AND LOWER(TagName) = LOWER(?)
        ORDER BY ID DESC
        LIMIT 1
        """,
        (
            int(company_id),
            str(tag_name),
        ),
    ).fetchone()

    if row is None:
        return None

    return row["Value"]


def _management_context_from_registers(company_id, fallback_tags=None):
    """Resolve ManagementPanel Contract/Product values from configured PLC registers.

    ManagementPanel owns the register settings. TagMapper owns the mapping from
    register address to a persisted tag name. PLC_Data is the persisted Edge
    stream used here to obtain the latest value of that mapped tag.
    """
    result = {
        "ContractCode": None,
        "ProductCode": None,
    }

    try:
        nodes = _flow_nodes(company_id)

        management_config = None
        tag_mappings = []

        for node in nodes.values():
            if not isinstance(node, dict):
                continue

            node_name = node.get("name")
            data = node.get("data", {}) or {}

            if node_name == "ManagementPanel":
                management_config = data.get(
                    "config",
                    data
                ) or {}

            elif node_name == "TagMapper":
                mappings = data.get("mappings", [])
                if isinstance(mappings, list):
                    tag_mappings = mappings

        if not isinstance(management_config, dict):
            return result

        register_to_tag = {}

        for mapping in tag_mappings:
            if not isinstance(mapping, dict):
                continue

            name = str(
                mapping.get("name", "")
            ).strip()

            if not name:
                continue

            try:
                register = int(
                    float(mapping.get("register"))
                )
            except (TypeError, ValueError):
                continue

            register_to_tag[register] = name

        try:
            contract_register = int(
                float(
                    management_config.get(
                        "contract_code_register"
                    )
                )
            )
        except (TypeError, ValueError):
            contract_register = None

        try:
            product_register = int(
                float(
                    management_config.get(
                        "product_code_register"
                    )
                )
            )
        except (TypeError, ValueError):
            product_register = None

        conn = get_connection()
        try:
            if contract_register is not None:
                tag_name = register_to_tag.get(contract_register)
                value = _latest_tag_value(
                    conn,
                    company_id,
                    tag_name,
                )
                if value not in (None, ""):
                    result["ContractCode"] = str(value).strip()

            if product_register is not None:
                tag_name = register_to_tag.get(product_register)
                value = _latest_tag_value(
                    conn,
                    company_id,
                    tag_name,
                )
                if value not in (None, ""):
                    result["ProductCode"] = str(value).strip()

            # Optional fallback: if the caller already supplied explicit
            # context tags, prefer those when present.
            explicit = {
                str(name).strip().lower(): value
                for name, value in (fallback_tags or {}).items()
            }

            for key in ("ContractCode", "ProductCode"):
                explicit_value = explicit.get(key.lower())
                if explicit_value not in (None, ""):
                    result[key] = str(
                        explicit_value
                    ).strip()

        finally:
            conn.close()

    except Exception as exc:
        print(
            "REPORT MANAGEMENT CONTEXT ERROR:",
            exc,
        )

    return result


def _context_from_products(report_products, tags):
    lookup = {
        str(k).strip().lower(): (k, v)
        for k, v in (tags or {}).items()
    }

    contract_code = None
    product_code = None

    for product in report_products or []:
        if not isinstance(product, dict):
            continue

        role = str(
            product.get(
                "context_role",
                product.get("context", "")
            )
        ).strip().lower()

        tag = str(
            product.get("tag", "")
        ).strip().lower()

        if role not in (
            _CONTEXT_CONTRACT_ROLES
            | _CONTEXT_PRODUCT_ROLES
        ):
            continue

        item = lookup.get(tag)
        if item is None or item[1] is None:
            continue

        value = str(item[1]).strip()

        if role in _CONTEXT_CONTRACT_ROLES:
            contract_code = value
        else:
            product_code = value

    return contract_code, product_code


def save_report_snapshot(
    company_id,
    tags,
    report_products,
    timestamp=None,
    trigger_tag=None,
    trigger_register=None,
    trigger_value=None,
):
    if (
        company_id is None
        or not isinstance(tags, dict)
        or not report_products
    ):
        return None

    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    ensure_report_tables()

    contract_code, product_code = _context_from_products(
        report_products,
        tags,
    )

    management_context = _management_context_from_registers(
        company_id,
        fallback_tags=tags,
    )

    if contract_code in (None, ""):
        contract_code = management_context.get(
            "ContractCode"
        )

    if product_code in (None, ""):
        product_code = management_context.get(
            "ProductCode"
        )

    values = []
    seen = set()
    lookup = {
        str(k).strip().lower(): (k, v)
        for k, v in tags.items()
    }

    for product in report_products:
        if not isinstance(product, dict):
            continue

        requested = str(
            product.get("tag", "")
        ).strip()

        role = str(
            product.get(
                "context_role",
                product.get("context", "")
            )
        ).strip().lower()

        if not requested or role:
            continue

        key = requested.lower()

        if key in seen:
            continue

        seen.add(key)

        item = lookup.get(key)
        if item is None or item[1] is None:
            continue

        try:
            values.append(
                (
                    item[0],
                    float(item[1]),
                )
            )
        except (TypeError, ValueError):
            continue

    if not values:
        return None

    conn = get_connection()

    try:
        columns = {
            row["name"]
            for row in conn.execute(
                'PRAGMA table_info("ReportHistory")'
            ).fetchall()
        }

        history_columns = [
            "CompanyID",
            "Timestamp",
        ]
        history_values = [
            company_id,
            timestamp,
        ]

        if "TriggerTag" in columns:
            history_columns.append("TriggerTag")
            history_values.append(
                trigger_tag
            )

        if "TriggerRegister" in columns:
            history_columns.append("TriggerRegister")
            history_values.append(
                trigger_register
            )

        if "TriggerValue" in columns:
            history_columns.append("TriggerValue")
            history_values.append(
                trigger_value
            )

        if "ContractCode" in columns:
            history_columns.append("ContractCode")
            history_values.append(
                contract_code
            )

        if "ProductCode" in columns:
            history_columns.append("ProductCode")
            history_values.append(
                product_code
            )

        placeholders = ",".join(
            "?"
            for _ in history_columns
        )

        conn.execute(
            f"""
            INSERT INTO ReportHistory
            ({','.join(history_columns)})
            VALUES ({placeholders})
            """,
            history_values,
        )

        report_id = conn.execute(
            "SELECT last_insert_rowid() AS id"
        ).fetchone()["id"]

        conn.executemany(
            """
            INSERT INTO ReportValues
            (ReportID, TagName, Value)
            VALUES (?, ?, ?)
            """,
            [
                (
                    report_id,
                    tag_name,
                    value,
                )
                for tag_name, value in values
            ],
        )

        conn.commit()
        return report_id

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_report_data(company_id, start, end):
    products = [
        p
        for p in get_report_products(company_id)
        if not p.get("context_role")
    ]

    result = {
        "columns": products,
        "rows": [],
        "totals": [0.0 for _ in products],
        "grand_total": 0.0,
    }

    if (
        company_id is None
        or not products
        or not start
        or not end
    ):
        return result

    ensure_report_tables()

    tags = [
        p["tag"]
        for p in products
    ]
    tag_keys = [
        str(tag).lower()
        for tag in tags
    ]
    placeholders = ",".join(
        "?"
        for _ in tags
    )

    conn = get_connection()

    try:
        fetched = conn.execute(
            f"""
            SELECT
                h.ReportID,
                h.Timestamp,
                h.ContractCode,
                h.ProductCode,
                v.TagName,
                v.Value,
                v.ReportValueID
            FROM ReportHistory h
            INNER JOIN ReportValues v
                ON v.ReportID = h.ReportID
            WHERE h.CompanyID = ?
              AND datetime(h.Timestamp) >= datetime(?)
              AND datetime(h.Timestamp) <= datetime(?)
              AND LOWER(v.TagName) IN ({placeholders})
            ORDER BY
                datetime(h.Timestamp),
                h.ReportID,
                v.ReportValueID
            """,
            [
                company_id,
                start.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                end.strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                *tag_keys,
            ],
        ).fetchall()

    finally:
        conn.close()

    grouped = {}

    for row in fetched:
        item = grouped.setdefault(
            row["ReportID"],
            {
                "timestamp": str(
                    row["Timestamp"]
                ),
                "contract_code": row["ContractCode"],
                "product_code": row["ProductCode"],
                "values": [
                    None
                    for _ in products
                ],
            },
        )

        tag_key = str(
            row["TagName"]
        ).strip().lower()

        try:
            index = tag_keys.index(
                tag_key
            )
        except ValueError:
            continue

        try:
            item["values"][index] = float(
                row["Value"]
            )
        except (TypeError, ValueError):
            pass

    totals = [
        0.0
        for _ in products
    ]
    rows = []

    for item in grouped.values():
        row_total = 0.0

        for index, value in enumerate(
            item["values"]
        ):
            if value is None:
                continue

            totals[index] += value
            row_total += value

        rows.append({
            "timestamp": item["timestamp"],
            "contract_code": item[
                "contract_code"
            ],
            "product_code": item[
                "product_code"
            ],
            "values": item["values"],
            "row_total": row_total,
        })

    result["rows"] = rows
    result["totals"] = [
        round(value, 3)
        for value in totals
    ]
    result["grand_total"] = round(
        sum(totals),
        3,
    )

    return result


def __getattr__(name):
    return getattr(
        _legacy,
        name,
    )
