from database import get_connection
from flow_engine.nodes.expression_node import ExpressionNode
from services.management_service import (
    ensure_management_tables,
    _management_calculations,
    _parse_jalali,
    _query_base,
    jalali_display,
)


def _report_values_for_pairs(conn, company_id, base_rows, start=None, end=None):
    pairs = []
    seen = set()
    for row in base_rows:
        key = (
            str(row["ContractCode"]).strip().lower(),
            str(row["ProductCode"]).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        pairs.append((row["ContractCode"], row["ProductCode"]))

    if not pairs:
        return {}

    args = [int(company_id)]
    conditions = []
    for contract_code, product_code in pairs:
        conditions.append(
            "(LOWER(COALESCE(h.ContractCode,''))=LOWER(?) "
            "AND LOWER(COALESCE(h.ProductCode,''))=LOWER(?))"
        )
        args.extend([contract_code, product_code])

    where = [f"({' OR '.join(conditions)})"]
    if start:
        where.append("datetime(h.Timestamp) >= datetime(?)")
        args.append(start)
    if end:
        where.append("datetime(h.Timestamp) <= datetime(?)")
        args.append(end)

    rows = conn.execute(
        f"""
        SELECT h.ReportID, h.ContractCode, h.ProductCode, h.Timestamp,
               v.TagName, v.Value, v.ReportValueID
        FROM ReportHistory h
        INNER JOIN ReportValues v ON v.ReportID=h.ReportID
        WHERE h.CompanyID=? AND {' AND '.join(where)}
        ORDER BY h.ReportID ASC, v.ReportValueID ASC
        """,
        args,
    ).fetchall()

    grouped = {}
    for row in rows:
        key = (
            str(row["ContractCode"] or "").strip().lower(),
            str(row["ProductCode"] or "").strip().lower(),
        )
        group = grouped.setdefault(key, {"tags": {}})
        tag = str(row["TagName"] or "").strip()
        if tag:
            group["tags"].setdefault(tag, []).append(row["Value"])
    return grouped


def get_production_management_data(company_id, filters=None):
    filters = filters or {}
    ensure_management_tables()
    where, args = _query_base(company_id, filters)

    production_start = _parse_jalali(filters.get("production_date_from"))
    production_end = _parse_jalali(filters.get("production_date_to"), True)

    conn = get_connection()
    try:
        base_rows = conn.execute(
            f"""
            SELECT c.ContractID, c.ContractCode, c.ContractDate, c.ContractName,
                   c.Description AS ContractDescription,
                   cp.ContractProductID, cp.OrderedQuantity, cp.DeliveryDate,
                   p.ProductID, p.ProductCode, p.ProductName, p.Unit,
                   COALESCE(b.CostPerKg,0) AS CostPerKg,
                   COALESCE(b.CostPerMeter,0) AS CostPerMeter,
                   COALESCE(b.Notes,'') AS BOMNotes
            FROM Contracts c
            INNER JOIN ContractProducts cp ON cp.ContractID=c.ContractID
            INNER JOIN Products p ON p.ProductID=cp.ProductID
            LEFT JOIN ProductBOM b ON b.ProductID=p.ProductID
            WHERE {' AND '.join(where)}
            ORDER BY datetime(c.ContractDate) DESC, c.ContractID DESC, p.ProductCode ASC
            """,
            args,
        ).fetchall()

        if not base_rows:
            return {"columns": [], "rows": [], "count": 0}

        groups = _report_values_for_pairs(
            conn,
            company_id,
            base_rows,
            start=production_start,
            end=production_end,
        )
        calculations = _management_calculations(company_id)
        expression_node = ExpressionNode({"expressions": calculations}) if calculations else None

        columns = [
            {"key": "ContractCode", "label": "کد قرارداد", "unit": ""},
            {"key": "ContractDate", "label": "تاریخ عقد قرارداد", "unit": ""},
            {"key": "ContractName", "label": "نام قرارداد", "unit": ""},
            {"key": "ProductCode", "label": "کد محصول", "unit": ""},
            {"key": "ProductName", "label": "نوع محصول", "unit": ""},
            {"key": "OrderedQuantity", "label": "مقدار سفارش", "unit": ""},
            {"key": "DeliveryDate", "label": "تاریخ تحویل", "unit": ""},
            {"key": "Description", "label": "توضیحات", "unit": ""},
            {"key": "CostPerKg", "label": "BOM / kg", "unit": ""},
            {"key": "CostPerMeter", "label": "BOM / m", "unit": ""},
        ]
        columns.extend(
            {
                "key": str(item["name"]),
                "label": str(item.get("label", item["name"])),
                "unit": str(item.get("unit", "")),
                "source": "ManagementPanel",
                "expression": str(item.get("expression", "")),
            }
            for item in calculations
        )

        output_rows = []
        for row in base_rows:
            pair_key = (
                str(row["ContractCode"]).strip().lower(),
                str(row["ProductCode"]).strip().lower(),
            )
            source = groups.get(pair_key, {"tags": {}})
            tags = {}
            for tag, values in source["tags"].items():
                numeric_values = []
                for value in values:
                    try:
                        numeric_values.append(float(value))
                    except (TypeError, ValueError):
                        pass
                tags[tag] = numeric_values[-1] if numeric_values else 0
                tags[f"{tag}_values"] = numeric_values
                tags[f"{tag}_last"] = numeric_values[-1] if numeric_values else 0

            tags["OrderedQuantity"] = row["OrderedQuantity"]
            tags["CostPerKg"] = row["CostPerKg"]
            tags["CostPerMeter"] = row["CostPerMeter"]
            tags["ContractCode"] = row["ContractCode"]
            tags["ProductCode"] = row["ProductCode"]

            calculated = {}
            if expression_node:
                try:
                    result = expression_node.execute({"Tags": dict(tags)}) or {}
                    calculated = dict(result.get("Tags", {}))
                except Exception as exc:
                    print("MANAGEMENT PRODUCTION CALCULATION ERROR:", exc)

            display = {
                "ContractCode": row["ContractCode"],
                "ContractDate": jalali_display(row["ContractDate"]),
                "ContractName": row["ContractName"],
                "ProductCode": row["ProductCode"],
                "ProductName": row["ProductName"],
                "OrderedQuantity": row["OrderedQuantity"],
                "DeliveryDate": jalali_display(row["DeliveryDate"]),
                "Description": row["ContractDescription"] or "",
                "CostPerKg": row["CostPerKg"],
                "CostPerMeter": row["CostPerMeter"],
            }
            for item in calculations:
                key = str(item["name"])
                display[key] = calculated.get(key)
            output_rows.append(display)

        return {"columns": columns, "rows": output_rows, "count": len(output_rows)}
    finally:
        conn.close()
