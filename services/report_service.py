# =====================================================
# SCADA_FLOW REPORT SERVICE
# Dynamic report historian and query layer
# =====================================================

import json
from datetime import datetime

from database import get_connection, get_company_flow


# =====================================================
# DATABASE
# =====================================================

def ensure_report_tables():
    """Create the report-specific database tables if they do not exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS ReportHistory (
            ReportID INTEGER PRIMARY KEY AUTOINCREMENT,
            CompanyID INTEGER NOT NULL,
            Timestamp TEXT NOT NULL,
            FOREIGN KEY (CompanyID)
                REFERENCES Companies(CompanyID)
        );

        CREATE TABLE IF NOT EXISTS ReportValues (
            ReportValueID INTEGER PRIMARY KEY AUTOINCREMENT,
            ReportID INTEGER NOT NULL,
            TagName TEXT NOT NULL,
            Value REAL,
            FOREIGN KEY (ReportID)
                REFERENCES ReportHistory(ReportID)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_report_history_company_time
        ON ReportHistory (CompanyID, Timestamp);

        CREATE INDEX IF NOT EXISTS idx_report_values_report_tag
        ON ReportValues (ReportID, TagName);
        """
    )

    conn.commit()
    cursor.close()
    conn.close()


# =====================================================
# FLOW CONFIGURATION
# =====================================================

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


def _flow_tag_mapper_products(company_id):
    """Return report-capable tags defined by TagMapper in the saved Flow.

    The Flow is the source of truth. No company, tag, register, or product
    name is hard-coded here.
    """
    products = []
    seen = set()

    try:
        for node in _flow_nodes(company_id).values():
            if node.get("name") != "TagMapper":
                continue

            mappings = (
                node.get("data", {}) or {}
            ).get("mappings", [])

            if not isinstance(mappings, list):
                continue

            for item in mappings:
                if not isinstance(item, dict):
                    continue

                tag = str(item.get("name", "")).strip()
                if not tag:
                    continue

                storage = str(
                    item.get("storage", "TIME")
                ).strip().upper()

                if storage not in ("TIME", "TRIGGER"):
                    continue

                key = tag.lower()
                if key in seen:
                    continue
                seen.add(key)

                products.append({
                    "name": tag,
                    "tag": tag,
                    "unit": str(item.get("unit", "")).strip(),
                })

            # One TagMapper is the normal flow source. Do not merge unrelated
            # disconnected mapper nodes into this report configuration.
            break

    except Exception as exc:
        print("REPORT TAGMAPPER CONFIG ERROR:", exc)

    return products


def get_report_products(company_id):
    """Return ReportOutput selections, with Flow-driven TagMapper fallback.

    Explicit ReportOutput products are preferred when they actually match
    tags defined by the same Flow. If the ReportOutput was left empty or was
    copied from another company and no configured product matches the current
    Flow's TagMapper, the report automatically uses the TagMapper report-capable
    tags. This keeps the report fully Flow-driven and prevents empty reports
    caused by stale product names.
    """
    configured = []

    try:
        for node in _flow_nodes(company_id).values():
            if node.get("name") != "ReportOutput":
                continue

            data = node.get("data", {}) or {}
            config = data.get("config", data) or {}
            values = config.get("products", [])

            if isinstance(values, list):
                for item in values:
                    if not isinstance(item, dict):
                        continue

                    tag = str(item.get("tag", "")).strip()
                    if not tag:
                        continue

                    configured.append({
                        "name": str(item.get("name", tag)).strip() or tag,
                        "tag": tag,
                        "unit": str(item.get("unit", "")).strip(),
                    })
            break

    except Exception as exc:
        print("REPORT CONFIG ERROR:", exc)

    flow_tags = _flow_tag_mapper_products(company_id)
    flow_keys = {
        str(item["tag"]).strip().lower()
        for item in flow_tags
    }

    matching = [
        item
        for item in configured
        if str(item["tag"]).strip().lower() in flow_keys
    ]

    if matching:
        return matching

    return flow_tags


# =====================================================
# SAVE REPORT SNAPSHOT
# =====================================================

def save_report_snapshot(
    company_id,
    tags,
    report_products,
    timestamp=None,
):
    """
    Store one complete report snapshot.

    The list of tags is taken from ReportOutput or, when its configuration
    does not match the current Flow, from TagMapper in that same Flow. No
    register/tag names are hard-coded here.
    """
    if company_id is None or not isinstance(tags, dict):
        return None

    if not report_products:
        return None

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    values = []
    seen = set()

    tag_lookup = {
        str(name).strip().lower(): (name, value)
        for name, value in tags.items()
    }

    for product in report_products:
        if not isinstance(product, dict):
            continue

        requested = str(product.get("tag", "")).strip()
        if not requested:
            continue

        key = requested.lower()
        if key in seen:
            continue
        seen.add(key)

        item = tag_lookup.get(key)
        if item is None:
            continue

        actual_name, value = item
        if value is None:
            continue

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue

        values.append((actual_name, numeric_value))

    if not values:
        return None

    ensure_report_tables()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO ReportHistory
            (CompanyID, Timestamp)
            VALUES (?, ?)
            """,
            (company_id, timestamp),
        )

        report_id = cursor.lastrowid

        cursor.executemany(
            """
            INSERT INTO ReportValues
            (ReportID, TagName, Value)
            VALUES (?, ?, ?)
            """,
            [
                (report_id, tag_name, value)
                for tag_name, value in values
            ],
        )

        conn.commit()
        return report_id

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# =====================================================
# REPORT QUERY
# =====================================================

def get_report_data(company_id, start, end):
    """Return dynamic report columns and rows for the requested date range."""
    products = get_report_products(company_id)

    result = {
        "columns": products,
        "rows": [],
        "totals": [0.0 for _ in products],
        "grand_total": 0.0,
    }

    if company_id is None or not products or not start or not end:
        return result

    ensure_report_tables()

    tags = [item["tag"] for item in products]
    tag_keys = [tag.lower() for tag in tags]
    placeholders = ",".join("?" for _ in tags)

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            f"""
            SELECT
                h.ReportID,
                h.Timestamp,
                v.TagName,
                v.Value
            FROM ReportHistory h
            INNER JOIN ReportValues v
                ON v.ReportID = h.ReportID
            WHERE h.CompanyID = ?
              AND datetime(h.Timestamp) >= datetime(?)
              AND datetime(h.Timestamp) <= datetime(?)
              AND LOWER(v.TagName) IN ({placeholders})
            ORDER BY datetime(h.Timestamp) ASC, h.ReportID ASC,
                     v.ReportValueID ASC
            """,
            [
                company_id,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
                *tag_keys,
            ],
        )

        fetched = cursor.fetchall()

        # -------------------------------------------------
        # FALLBACK TO HISTORIAN DATA
        # -------------------------------------------------
        # If older snapshots are missing ReportValues, use the same company's
        # historian data for the Flow-selected report tags. This keeps the
        # report usable without requiring a manual database repair.
        if not fetched:
            cursor.execute(
                f"""
                SELECT
                    ID AS ReportID,
                    Timestamp,
                    TagName,
                    Value
                FROM PLC_Data
                WHERE CompanyID = ?
                  AND datetime(Timestamp) >= datetime(?)
                  AND datetime(Timestamp) <= datetime(?)
                  AND LOWER(TagName) IN ({placeholders})
                ORDER BY datetime(Timestamp) ASC, ID ASC
                """,
                [
                    company_id,
                    start.strftime("%Y-%m-%d %H:%M:%S"),
                    end.strftime("%Y-%m-%d %H:%M:%S"),
                    *tag_keys,
                ],
            )
            fetched = cursor.fetchall()

    finally:
        cursor.close()
        conn.close()

    grouped = {}

    for row in fetched:
        report_id = row["ReportID"]
        item = grouped.setdefault(
            report_id,
            {
                "timestamp": str(row["Timestamp"]),
                "values": [None for _ in products],
            },
        )

        tag_key = str(row["TagName"]).strip().lower()

        try:
            index = tag_keys.index(tag_key)
        except ValueError:
            continue

        try:
            value = float(row["Value"])
        except (TypeError, ValueError):
            value = None

        item["values"][index] = value

    totals = [0.0 for _ in products]
    rows = []

    for item in grouped.values():
        row_total = 0.0

        for index, value in enumerate(item["values"]):
            if value is None:
                continue
            totals[index] += value
            row_total += value

        rows.append({
            "timestamp": item["timestamp"],
            "values": item["values"],
            "row_total": row_total,
        })

    totals = [round(value, 3) for value in totals]

    result["rows"] = rows
    result["totals"] = totals
    result["grand_total"] = round(sum(totals), 3)

    return result
