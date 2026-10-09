"""
SQL Query Executor for NetBox Hub structured tables.

Provides safe, parameterised query execution against the local SQLite database
so the agent can run arbitrary SELECT statements without an arbitrary row limit.
Write operations are explicitly blocked.
"""

import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.db_manager import DB_PATH, init_db

# Tables that may be queried (structured + backup search tables)
QUERYABLE_TABLES = {
    "interfaces",
    "cables",
    "backup_records",
    "sites_records",
    "ipam_records",
    "inventory_records",
}


def execute_query(
    sql: str,
    params: Optional[List[Any]] = None,
    limit: Optional[int] = None,
    output_format: str = "json",
) -> Dict[str, Any]:
    """Execute a read-only SQL query against the NetBox Hub database.

    Args:
        sql:          SELECT statement (WRITE operations are rejected).
        params:       Parameter bindings for the query.
        limit:        Hard cap on returned rows (None = unlimited).
        output_format: "json" | "csv" | "summary"

    Returns:
        {
            "status": "ok" | "error",
            "columns": [...],
            "rows": [...],
            "row_count": int,
            "sql": str,
            "format": str,
        }
    """
    init_db()
    sql_stripped = sql.strip()

    # Block any write / schema-modifying statement
    first_token = sql_stripped.split()[0].upper() if sql_stripped else ""
    if first_token in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER",
                        "DROP", "REPLACE", "ATTACH", "DETACH", "BEGIN",
                        "COMMIT", "ROLLBACK", "PRAGMA"):
        return {
            "status": "error",
            "error": "Write operations are not allowed via the query executor.",
            "sql": sql,
        }

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        effective_params = list(params) if params else []

        # Append LIMIT if requested
        if limit is not None and limit > 0:
            # Only add LIMIT for SELECT statements that don't already have one
            if "LIMIT" not in sql_stripped.upper():
                sql_stripped = sql_stripped.rstrip().rstrip(";") + f" LIMIT {int(limit)}"

        cursor.execute(sql_stripped, effective_params)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []

        row_dicts = [dict(r) for r in rows]
        row_count = len(row_dicts)

        conn.close()
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "sql": sql,
        }

    if output_format == "csv":
        csv_lines = [",".join(columns)]
        for row in row_dicts:
            csv_lines.append(",".join(
                _csv_escape(str(v) if v is not None else "") for v in row.values()
            ))
        return {
            "status": "ok",
            "columns": columns,
            "rows": row_dicts,
            "row_count": row_count,
            "sql": sql,
            "format": "csv",
            "csv": "\n".join(csv_lines),
        }

    if output_format == "summary":
        return {
            "status": "ok",
            "columns": columns,
            "row_count": row_count,
            "sql": sql,
            "format": "summary",
            "summary": _render_summary(columns, row_dicts),
        }

    return {
        "status": "ok",
        "columns": columns,
        "rows": row_dicts,
        "row_count": row_count,
        "sql": sql,
        "format": output_format,
    }


def _csv_escape(value: str) -> str:
    if '"' in value or "\n" in value or "," in value:
        return '"' + value.replace('"', '""') + '"'
    return value


def _render_summary(columns: List[str], rows: List[Dict]) -> str:
    lines: List[str] = []
    lines.append(f"Total rows: {len(rows)}")
    lines.append(f"Columns: {', '.join(columns)}")
    lines.append("")
    for i, row in enumerate(rows[:50]):
        parts = [f"{col}={row.get(col, '')}" for col in columns]
        lines.append(f"  [{i+1}] {' | '.join(parts)}")
    if len(rows) > 50:
        lines.append(f"  ... and {len(rows) - 50} more rows (use limit param to fetch more)")
    return "\n".join(lines)


def get_table_schema(table_name: str) -> Dict[str, Any]:
    """Return column definitions and row count for a queryable table."""
    init_db()
    if table_name not in QUERYABLE_TABLES:
        return {"error": f"Table '{table_name}' is not queryable. Allowed: {QUERYABLE_TABLES}"}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = []
    for row in cursor.fetchall():
        columns.append({
            "cid": row[0],
            "name": row[1],
            "type": row[2],
            "notnull": row[3],
            "default_value": row[4],
            "pk": row[5],
        })

    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]

    conn.close()
    return {
        "table": table_name,
        "columns": columns,
        "column_names": [c["name"] for c in columns],
        "row_count": int(count),
    }


def list_queryable_tables() -> List[Dict[str, Any]]:
    """Return schema info for every queryable table."""
    return [get_table_schema(t) for t in sorted(QUERYABLE_TABLES)]


def export_table_to_json(
    table_name: str,
    output_path: str,
    where_clause: str = "",
    order_by: str = "",
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Export an entire table (or filtered subset) to a JSON file on disk.

    Args:
        table_name:  Name of the table to export.
        output_path: Destination file path (must be under the data/ directory).
        where_clause: Optional WHERE clause (without the WHERE keyword).
        order_by:    Optional ORDER BY clause (without the ORDER BY keyword).
        limit:       Optional row limit.

    Returns:
        {"status": "ok"|"error", "rows_exported": int, "path": str}
    """
    init_db()
    if table_name not in QUERYABLE_TABLES:
        return {"status": "error", "error": f"Table '{table_name}' is not queryable."}

    # Safety: only allow writes under the data/ directory
    abs_path = os.path.abspath(output_path)
    data_dir = os.path.abspath("data")
    if not abs_path.startswith(data_dir):
        return {"status": "error", "error": "Output path must be within the data/ directory."}

    query = f"SELECT * FROM {table_name}"
    params: List[Any] = []

    if where_clause:
        query += f" WHERE {where_clause}"

    if order_by:
        query += f" ORDER BY {order_by}"

    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)

    return {"status": "ok", "rows_exported": len(rows), "path": abs_path}


def export_table_to_csv(
    table_name: str,
    output_path: str,
    where_clause: str = "",
    order_by: str = "",
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Export an entire table (or filtered subset) to a CSV file on disk."""
    init_db()
    if table_name not in QUERYABLE_TABLES:
        return {"status": "error", "error": f"Table '{table_name}' is not queryable."}

    abs_path = os.path.abspath(output_path)
    data_dir = os.path.abspath("data")
    if not abs_path.startswith(data_dir):
        return {"status": "error", "error": "Output path must be within the data/ directory."}

    query = f"SELECT * FROM {table_name}"
    params: List[Any] = []

    if where_clause:
        query += f" WHERE {where_clause}"
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit is not None and limit > 0:
        query += f" LIMIT {int(limit)}"

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    import csv as _csv
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", newline="", encoding="utf-8") as f:
        writer = _csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])

    return {"status": "ok", "rows_exported": len(rows), "path": abs_path}


def export_query_to_csv(sql: str, output_path: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Export the result of an arbitrary SELECT query to CSV."""
    init_db()
    abs_path = os.path.abspath(output_path)
    data_dir = os.path.abspath("data")
    if not abs_path.startswith(data_dir):
        return {"status": "error", "error": "Output path must be within the data/ directory."}

    sql_stripped = sql.strip().rstrip(";")
    first_token = sql_stripped.split()[0].upper() if sql_stripped else ""
    if first_token not in ("SELECT", "WITH"):
        return {"status": "error", "error": "Only SELECT queries can be exported."}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql_stripped, params or [])
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])

    return {"status": "ok", "rows_exported": len(rows), "path": abs_path}


def export_query_to_json(sql: str, output_path: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Export the result of an arbitrary SELECT query to JSON."""
    init_db()
    abs_path = os.path.abspath(output_path)
    data_dir = os.path.abspath("data")
    if not abs_path.startswith(data_dir):
        return {"status": "error", "error": "Output path must be within the data/ directory."}

    sql_stripped = sql.strip().rstrip(";")
    first_token = sql_stripped.split()[0].upper() if sql_stripped else ""
    if first_token not in ("SELECT", "WITH"):
        return {"status": "error", "error": "Only SELECT queries can be exported."}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql_stripped, params or [])
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)

    return {"status": "ok", "rows_exported": len(rows), "path": abs_path}
