"""Production-aware facade for the Flow-based management service.

Keeps the existing contract/product/BOM logic intact while making Management
calculations consume the same ReportHistory production records that are
filtered by production date, ContractCode and ProductCode.
"""

from services import management_service as _legacy
from services.management_service import *  # noqa: F401,F403


def _production_report_values_for_pairs(conn, company_id, base_rows, filters):
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

    date_from = _legacy._parse_jalali(filters.get("production_date_from"))
    date_to = _legacy._parse_jalali(filters.get("production_date_to"), True)

    where = ["h.CompanyID=?", "(" + " OR ".join(conditions) + ")"]
    if date_from:
        where.append("datetime(h.Timestamp) >= datetime(?)")
        args.append(date_from)
    if date_to:
        where.append("datetime(h.Timestamp) <= datetime(?)")
        args.append(date_to)

    rows = conn.execute(
        f"""
        SELECT h.ReportID, h.ContractCode, h.ProductCode, h.Timestamp,
               v.TagName, v.Value, v.ReportValueID
        FROM ReportHistory h
        INNER JOIN ReportValues v ON v.ReportID=h.ReportID
        WHERE {' AND '.join(where)}
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
        group = grouped.setdefault(key, {"tags": {}, "report_ids": set()})
        group["report_ids"].add(int(row["ReportID"]))
        tag = str(row["TagName"] or "").strip()
        if tag:
            group["tags"].setdefault(tag, []).append(row["Value"])

    return grouped


def get_management_data(company_id, filters=None):
    filters = dict(filters or {})
    date_from = str(filters.get("production_date_from", "")).strip()
    date_to = str(filters.get("production_date_to", "")).strip()

    if not date_from and not date_to:
        return _legacy.get_management_data(company_id, filters)

    original = _legacy._report_values_for_pairs
    _legacy._report_values_for_pairs = lambda conn, cid, rows: (
        _production_report_values_for_pairs(conn, cid, rows, filters)
    )
    try:
        result = _legacy.get_management_data(company_id, filters)
    finally:
        _legacy._report_values_for_pairs = original

    # When a production-date filter is present, only show contract/product
    # rows for which at least one production snapshot exists in that window.
    allowed = set()
    conn = _legacy.get_connection()
    try:
        where = ["CompanyID=?"]
        args = [int(company_id)]
        if date_from:
            parsed = _legacy._parse_jalali(date_from)
            if parsed:
                where.append("datetime(Timestamp)>=datetime(?)")
                args.append(parsed)
        if date_to:
            parsed = _legacy._parse_jalali(date_to, True)
            if parsed:
                where.append("datetime(Timestamp)<=datetime(?)")
                args.append(parsed)
        rows = conn.execute(
            f"SELECT ContractCode, ProductCode FROM ReportHistory WHERE {' AND '.join(where)}",
            args,
        ).fetchall()
        for row in rows:
            allowed.add((
                str(row["ContractCode"] or "").strip().lower(),
                str(row["ProductCode"] or "").strip().lower(),
            ))
    finally:
        conn.close()

    if allowed:
        result["rows"] = [
            row for row in result.get("rows", [])
            if (
                str(row.get("ContractCode") or "").strip().lower(),
                str(row.get("ProductCode") or "").strip().lower(),
            ) in allowed
        ]
        result["count"] = len(result["rows"])
    else:
        result["rows"] = []
        result["count"] = 0

    return result


__all__ = [name for name in globals() if not name.startswith("_")]
