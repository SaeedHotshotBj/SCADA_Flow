#!/usr/bin/env python3
"""SCADA_FLOW historical SQLite data cleanup utility.

Standalone by design: it does not modify Flask routes, Trend runtime,
Edge ingest, Flow logic, or dashboard behavior.
"""

import argparse
import re
import sqlite3
from datetime import datetime, timezone

from database import get_connection


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DATE_NAME_RE = re.compile(
    r"(?:date|time|timestamp|period|created|updated|modified|received|event|occurred|logged)",
    re.IGNORECASE,
)


def quote_identifier(name):
    if not isinstance(name, str) or not IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"Invalid SQLite identifier: {name!r}")
    return f'"{name}"'


def parse_cutoff(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Cutoff date/time is required")
    normalized = text.replace("T", " ").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        dt = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
    if dt is None:
        raise ValueError("Use YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def cutoff_text(dt):
    if dt.microsecond:
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def list_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows if not str(row[0]).startswith("sqlite_")]


def table_columns(conn, table):
    quote_identifier(table)
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or ""),
            "pk": bool(row[5]),
        }
        for row in rows
    ]


def temporal_columns(conn, table):
    result = []
    for column in table_columns(conn, table):
        declared = column["type"].upper()
        if "DATE" in declared or "TIME" in declared or DATE_NAME_RE.search(column["name"]):
            result.append(column)
    return result


def table_exists(conn, table):
    quote_identifier(table)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def resolve_column(conn, table, column):
    for item in table_columns(conn, table):
        if item["name"] == column:
            return item
    raise ValueError(f"Column {column!r} does not exist in table {table!r}")


def comparison_expression(column):
    declared = column["type"].upper()
    name = column["name"]

    # SCADA_FLOW historical timestamps are Gregorian ISO text.
    if "INT" not in declared and "REAL" not in declared and "NUM" not in declared:
        return f"datetime({quote_identifier(name)}) < datetime(?)", "text"

    # Also support Unix-second timestamp columns when their names clearly
    # identify them as date/time fields.
    if DATE_NAME_RE.search(name):
        return f"{quote_identifier(name)} < ?", "epoch"

    raise ValueError(
        f"Numeric column {name!r} is not recognized as a date/time field"
    )


def parameter_for_strategy(dt, strategy):
    if strategy == "text":
        return cutoff_text(dt)
    return dt.replace(tzinfo=timezone.utc).timestamp()


def date_range(conn, table, column):
    resolve_column(conn, table, column)
    row = conn.execute(
        f"SELECT MIN({quote_identifier(column)}), MAX({quote_identifier(column)}) "
        f"FROM {quote_identifier(table)}"
    ).fetchone()
    return row[0], row[1]


def count_rows_before(conn, table, column, cutoff):
    if not table_exists(conn, table):
        raise ValueError(f"Table {table!r} does not exist")
    metadata = resolve_column(conn, table, column)
    expression, strategy = comparison_expression(metadata)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {quote_identifier(table)} WHERE {expression}",
        (parameter_for_strategy(cutoff, strategy),),
    ).fetchone()
    return int(row[0] or 0)


def preview(conn, table, column, before):
    cutoff = parse_cutoff(before)
    count = count_rows_before(conn, table, column, cutoff)
    minimum, maximum = date_range(conn, table, column)
    print("\nPREVIEW")
    print("=" * 72)
    print(f"Table          : {table}")
    print(f"Date column    : {column}")
    print(f"Delete before  : {cutoff_text(cutoff)}")
    print(f"Current minimum: {minimum}")
    print(f"Current maximum: {maximum}")
    print(f"Rows to delete : {count}")
    return count, cutoff


def delete_rows(conn, table, column, cutoff):
    metadata = resolve_column(conn, table, column)
    expression, strategy = comparison_expression(metadata)
    conn.execute(
        f"DELETE FROM {quote_identifier(table)} WHERE {expression}",
        (parameter_for_strategy(cutoff, strategy),),
    )
    return int(conn.total_changes)


def print_schema(conn):
    print("\nTABLES WITH DETECTED DATE/TIME COLUMNS")
    print("=" * 72)
    found = False
    for table in list_tables(conn):
        columns = temporal_columns(conn, table)
        if not columns:
            continue
        found = True
        total = conn.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(table)}"
        ).fetchone()[0]
        print(f"\n{table}  (rows={int(total)})")
        for column in columns:
            minimum, maximum = date_range(conn, table, column["name"])
            print(
                f"  - {column['name']} [{column['type'] or 'no type'}] "
                f"range={minimum!s} .. {maximum!s}"
            )
    if not found:
        print("No date/time-related columns were detected.")


def interactive(conn):
    total_deleted = 0
    while True:
        print_schema(conn)
        print("\nEnter q to quit or r to refresh.")
        table = input("Table > ").strip()
        if table.lower() == "q":
            print(f"Total deleted in this session: {total_deleted}")
            return 0
        if table.lower() == "r":
            continue
        if not table_exists(conn, table):
            print("Table not found.")
            continue

        columns = temporal_columns(conn, table)
        if not columns:
            print("No date/time-related column detected in this table.")
            print("For an intentional non-datetime column use --table/--column explicitly.")
            continue

        print(f"\nDate/time columns in {table}:")
        for index, column in enumerate(columns, 1):
            print(f"  {index}. {column['name']} [{column['type'] or 'no type'}]")
        try:
            selected = int(input("Column number > ").strip()) - 1
            column = columns[selected]["name"]
        except (ValueError, IndexError):
            print("Invalid column selection.")
            continue

        before = input("Delete rows before (YYYY-MM-DD [HH:MM:SS]) > ").strip()
        try:
            count, cutoff = preview(conn, table, column, before)
            if count == 0:
                print("Nothing to delete.")
                continue
            phrase = f"DELETE {table}"
            print(f"\nType exactly: {phrase}")
            if input("> ").strip() != phrase:
                print("Delete cancelled.")
                continue
            conn.execute("BEGIN")
            try:
                deleted = delete_rows(conn, table, column, cutoff)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            total_deleted += deleted
            remaining = count_rows_before(conn, table, column, cutoff)
            print(f"Deleted rows : {deleted}")
            print(f"Remaining    : {remaining}")
        except Exception as exc:
            print(f"ERROR: {exc}")


def parser():
    p = argparse.ArgumentParser(
        description="Delete SCADA_FLOW SQLite rows before a selected date/time."
    )
    p.add_argument("--list", action="store_true", help="List tables and detected date/time columns")
    p.add_argument("--preview", action="store_true", help="Preview rows matching the cutoff")
    p.add_argument("--delete", action="store_true", help="Delete rows matching the cutoff")
    p.add_argument("--table", help="Exact SQLite table name")
    p.add_argument("--column", help="Exact date/time column name")
    p.add_argument("--before", help="YYYY-MM-DD or YYYY-MM-DD HH:MM[:SS]")
    p.add_argument("--yes", action="store_true", help="Skip delete phrase confirmation")
    return p


def main():
    args = parser().parse_args()
    conn = get_connection()
    try:
        if args.list:
            print_schema(conn)
            return 0

        if not args.preview and not args.delete:
            return interactive(conn)

        if not args.table or not args.column or not args.before:
            raise ValueError("--table, --column and --before are required")

        count, cutoff = preview(conn, args.table, args.column, args.before)
        if not args.delete or count == 0:
            return 0

        if not args.yes:
            phrase = f"DELETE {args.table}"
            print(f"\nType exactly: {phrase}")
            if input("> ").strip() != phrase:
                print("Delete cancelled.")
                return 0

        conn.execute("BEGIN")
        try:
            deleted = delete_rows(conn, args.table, args.column, cutoff)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"Deleted rows: {deleted}")
        print(f"Remaining rows before cutoff: {count_rows_before(conn, args.table, args.column, cutoff)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
