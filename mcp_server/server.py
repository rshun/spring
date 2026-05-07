from __future__ import annotations

import os
import sys
import re
import json
import threading

# Fix Windows GBK encoding issues
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd

try:
    # Most common official-style import
    from mcp.server.fastmcp import FastMCP
except Exception as e:
    raise RuntimeError(
        "Cannot import FastMCP. Please ensure MCP Python SDK is installed and import path is correct."
    ) from e

DB_PATH = os.environ.get("DUCKDB_PATH", "").strip()
if not DB_PATH:
    raise RuntimeError("DUCKDB_PATH env is required, but not set.")

# Safety / stability guards
MAX_ROWS_DEFAULT = int(os.environ.get("MAX_ROWS", "2000"))
MAX_DAYS_DEFAULT = int(os.environ.get("MAX_DAYS", "800"))  # limit per request
ALLOW_RAW_QUERY = os.environ.get("ALLOW_RAW_QUERY", "0").strip() == "1"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# DuckDB connection:
# Removed global CON and LOCK to implement short connections (connect-per-request).

mcp = FastMCP("duckdb-quant-readonly")

# -----------------------------
# Helpers
# -----------------------------
_CODE_RE = re.compile(r"^\s*(\d{6})\.(SZ|SH|BJ)\s*$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DANGEROUS_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|COPY|EXPORT|IMPORT|PRAGMA|CALL|SET|LOAD|INSTALL)\b",
    re.IGNORECASE,
)

_DANGEROUS_TABLE_FUNCTION_RE = re.compile(
    r"\b(read_[A-Za-z0-9_]*|glob|filename|parquet_scan|csv_scan|sqlite_scan|postgres_scan|httpfs)\s*\(",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    # stderr logging is best for stdio MCP servers; but keep it minimal
    if LOG_LEVEL in ("DEBUG", "INFO"):
        print(f"[{LOG_LEVEL}] {msg}", file=os.sys.stderr)


def parse_code(code: str) -> Tuple[str, str]:
    """
    "300085.SZ" -> ("300085","SZ")
    """
    m = _CODE_RE.match(code or "")
    if not m:
        raise ValueError("code must be like '300085.SZ' (6 digits + .SZ/.SH/.BJ)")
    symbol = m.group(1)
    exch = m.group(2).upper()
    return symbol, exch


def validate_date(d: str) -> str:
    if not _DATE_RE.match(d or ""):
        raise ValueError("date must be YYYY-MM-DD")
    # also validate real date
    datetime.strptime(d, "%Y-%m-%d")
    return d


def ensure_span(start: str, end: str, max_days: int) -> None:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        raise ValueError("end_date must be >= start_date")
    days = (e - s).days + 1
    if days > max_days:
        raise ValueError(f"date span too large: {days} days > max_days={max_days}")


def df_to_payload(df: pd.DataFrame, max_rows: int) -> Dict[str, Any]:
    truncated = False
    if len(df) > max_rows:
        df = df.iloc[:max_rows].copy()
        truncated = True
    # make JSON-safe
    df = df.where(pd.notnull(df), None)
    return {
        "columns": list(df.columns),
        "rows": df.to_dict(orient="records"),
        "rowcount": int(len(df)),
        "truncated": truncated,
    }


def run_sql(sql: str, params: Optional[List[Any]] = None) -> pd.DataFrame:
    # Short connection implementation: Open -> Execute -> Close
    _log(f"SQL: {sql} | params={params}")
    with duckdb.connect(DB_PATH, read_only=True) as con:
        if params:
            return con.execute(sql, params).fetchdf()
        return con.execute(sql).fetchdf()


def table_exists(table: str) -> bool:
    df = run_sql(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = ?
        LIMIT 1
        """,
        [table],
    )
    return len(df) > 0

def resolve_stock(code: str) -> Dict[str, Any]:
    """
    code: '300085.SZ' (primary key in STOCK_INFO)
    """
    _ = parse_code(code)  # only for validation
    if table_exists("STOCK_INFO"):
        df = run_sql(
            """
            SELECT *
            FROM STOCK_INFO
            WHERE UPPER(code) = UPPER(?)
            LIMIT 1
            """,
            [code],
        )
        if len(df) == 1:
            out = df_to_payload(df, 1)
            return out["rows"][0]

        # optional fallback: derive symbol/exchange and try (symbol, exchange)
        symbol, exch = parse_code(code)
        df2 = run_sql(
            """
            SELECT *
            FROM STOCK_INFO
            WHERE symbol = ? AND UPPER(exchange) = ?
            LIMIT 1
            """,
            [symbol, exch],
        )
        if len(df2) == 1:
            out = df_to_payload(df2, 1)
            return out["rows"][0]

    return {"code": code}


def add_limit_if_missing(sql: str, max_rows: int) -> str:
    # simple heuristic: if no LIMIT, append one
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return sql
    return sql.rstrip().rstrip(";") + f" LIMIT {int(max_rows)}"


def validate_raw_query(sql: str) -> None:
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if len(statements) != 1:
        raise ValueError("only one SQL statement is allowed.")

    q = statements[0]
    if not re.match(r"^(SELECT|WITH)\b", q, re.IGNORECASE):
        raise ValueError("only SELECT/WITH queries are allowed.")

    if _DANGEROUS_SQL_RE.search(q):
        raise ValueError("DDL/DML/admin statements are not allowed in read-only server.")

    if _DANGEROUS_TABLE_FUNCTION_RE.search(q):
        raise ValueError("file/network table functions are not allowed in raw query.")


# -----------------------------
# Core MCP Tools
# -----------------------------
@mcp.tool()
def list_tables() -> Dict[str, Any]:
    """
    List all base tables in DuckDB.
    """
    df = run_sql(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type='BASE TABLE'
        ORDER BY table_schema, table_name
        """
    )
    return df_to_payload(df, MAX_ROWS_DEFAULT)


@mcp.tool()
def describe_table(table: str) -> Dict[str, Any]:
    """
    Describe columns for a given table.
    """
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table or ""):
        raise ValueError("invalid table name")
    df = run_sql(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
        """,
        [table],
    )
    return df_to_payload(df, MAX_ROWS_DEFAULT)

@mcp.tool()
def search_stock(keyword: str, limit: int = 20) -> Dict[str, Any]:
    """
    Returns STOCK_INFO rows; canonical code is STOCK_INFO.code (already has suffix).
    """
    if not table_exists("STOCK_INFO"):
        raise RuntimeError("STOCK_INFO table not found.")
    kw = (keyword or "").strip()
    if not kw:
        raise ValueError("keyword is required")
    limit = max(1, min(int(limit), 200))

    # exact code
    if _CODE_RE.match(kw):
        df = run_sql(
            """
            SELECT *
            FROM STOCK_INFO
            WHERE UPPER(code) = UPPER(?)
            LIMIT 1
            """,
            [kw],
        )
        if len(df) > 0:
            return df_to_payload(df, limit)

    # fuzzy: match code/symbol/name
    df = run_sql(
        """
        SELECT *
        FROM STOCK_INFO
        WHERE
            UPPER(code) LIKE UPPER(?) OR
            symbol LIKE ? OR
            name LIKE ?
        ORDER BY symbol
        LIMIT ?
        """,
        [f"{kw}%", f"{kw}%", f"%{kw}%", limit],
    )
    return df_to_payload(df, limit)



@mcp.tool()
def get_stock_info(code: str) -> Dict[str, Any]:
    """
    Get single stock info by code (e.g., 300085.SZ).
    """
    info = resolve_stock(code)
    return {"stock": info}

@mcp.tool()
def get_trade_days(start_date: str, end_date: str, open_only: bool = True, limit: int = 5000) -> Dict[str, Any]:
    """
    TRADE_CAL(cal_date, is_open)
    """
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, max_days=5000)
    limit = max(1, min(int(limit), 20000))

    if not table_exists("TRADE_CAL"):
        raise RuntimeError("TRADE_CAL table not found.")

    if open_only:
        df = run_sql(
            """
            SELECT cal_date, is_open
            FROM TRADE_CAL
            WHERE cal_date BETWEEN ? AND ?
              AND is_open = 1
            ORDER BY cal_date
            LIMIT ?
            """,
            [start_date, end_date, limit],
        )
    else:
        df = run_sql(
            """
            SELECT cal_date, is_open
            FROM TRADE_CAL
            WHERE cal_date BETWEEN ? AND ?
            ORDER BY cal_date
            LIMIT ?
            """,
            [start_date, end_date, limit],
        )
    return df_to_payload(df, limit)

@mcp.tool()
def get_stock_daily(
    code: str,
    start_date: str,
    end_date: str,
    fields: Optional[List[str]] = None,
    max_rows: int = 2000,
) -> Dict[str, Any]:
    """
    STOCK_DAILY(code, date, open, high, low, close, volume, amount)
    """
    _ = parse_code(code)  # validate '300085.SZ'
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists("STOCK_DAILY"):
        raise RuntimeError("STOCK_DAILY table not found.")

    max_rows = max(1, min(int(max_rows), 20000))
    default_fields = ["date", "open", "high", "low", "close", "volume", "amount"]
    use_fields = fields if fields else default_fields

    clean_fields = []
    for c in use_fields:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", c):
            raise ValueError(f"invalid field: {c}")
        clean_fields.append(c)
    cols = ", ".join(clean_fields)

    df = run_sql(
        f"""
        SELECT {cols}
        FROM STOCK_DAILY
        WHERE UPPER(code) = UPPER(?)
          AND date BETWEEN ? AND ?
        ORDER BY date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)

@mcp.tool()
def get_daily_basic(
    code: str,
    start_date: str,
    end_date: str,
    max_rows: int = 2000,
) -> Dict[str, Any]:
    """
    DAILY_BASIC(code, trade_date, turnover_rate, ... is_st)
    """
    _ = parse_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists("DAILY_BASIC"):
        raise RuntimeError("DAILY_BASIC table not found.")

    max_rows = max(1, min(int(max_rows), 20000))

    df = run_sql(
        """
        SELECT *
        FROM DAILY_BASIC
        WHERE UPPER(code) = UPPER(?)
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)

@mcp.tool()
def calc_indicators(
    code: str,
    start_date: str,
    end_date: str,
    ma_windows: Optional[List[int]] = None,
    max_rows: int = 2000,
) -> Dict[str, Any]:
    """
    Calculate simple indicators from STOCK_DAILY:
    - returns (pct)
    - MA(close) for given windows
    - VOL_MA(volume) for given windows
    """
    if ma_windows is None or len(ma_windows) == 0:
        ma_windows = [5, 10, 20, 60]

    # fetch base data
    base = get_stock_daily(code, start_date, end_date, fields=["date", "close", "volume"], max_rows=50000)
    rows = base["rows"]
    if not rows:
        return {"columns": [], "rows": [], "rowcount": 0, "truncated": False}

    df = pd.DataFrame(rows)
    # ensure types
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    df["ret_1d"] = df["close"].pct_change()

    for w in ma_windows:
        w = int(w)
        if w <= 1 or w > 400:
            continue
        df[f"ma_{w}"] = df["close"].rolling(w, min_periods=max(2, w // 2)).mean()
        df[f"vol_ma_{w}"] = df["volume"].rolling(w, min_periods=max(2, w // 2)).mean()

    # keep JSON-friendly
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    out = df_to_payload(df, max_rows)
    return out


@mcp.tool()
def get_adj_factor(
    code: str,
    start_date: str,
    end_date: str,
    max_rows: int = 2000,
) -> Dict[str, Any]:
    """
    ADJ_FACTOR(code, trade_date, fore_factor, back_factor, adjust_factor)
    获取复权因子，用于计算前/后复权价格。
    """
    _ = parse_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists("ADJ_FACTOR"):
        raise RuntimeError("ADJ_FACTOR table not found.")

    max_rows = max(1, min(int(max_rows), 20000))

    df = run_sql(
        """
        SELECT code, trade_date, fore_factor, back_factor, adjust_factor
        FROM ADJ_FACTOR
        WHERE UPPER(code) = UPPER(?)
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_stock_industry(
    code: str,
    trade_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    查询股票的申万行业分类（一/二/三级）。
    如果指定 trade_date，返回该日期生效的行业归属；否则返回最新记录。
    """
    symbol, _ = parse_code(code)

    if not table_exists("STOCK_SW_INDUSTRY_VIEW"):
        raise RuntimeError("STOCK_SW_INDUSTRY_VIEW view not found.")

    if trade_date:
        trade_date = validate_date(trade_date)
        df = run_sql(
            """
            SELECT
                ? AS code,
                symbol,
                start_date,
                sw_version,
                sw_l1_code,
                sw_l1_name,
                sw_l2_code,
                sw_l2_name,
                sw_l3_code,
                sw_l3_name,
                industry_code,
                update_time,
                updated_at
            FROM STOCK_SW_INDUSTRY_VIEW
            WHERE symbol = ?
              AND start_date <= ?
            ORDER BY start_date DESC
            LIMIT 1
            """,
            [code, symbol, trade_date],
        )
    else:
        df = run_sql(
            """
            SELECT
                ? AS code,
                symbol,
                start_date,
                sw_version,
                sw_l1_code,
                sw_l1_name,
                sw_l2_code,
                sw_l2_name,
                sw_l3_code,
                sw_l3_name,
                industry_code,
                update_time,
                updated_at
            FROM STOCK_SW_INDUSTRY_VIEW
            WHERE symbol = ?
            ORDER BY start_date DESC
            LIMIT 1
            """,
            [code, symbol],
        )
    return df_to_payload(df, 1)


@mcp.tool()
def get_stock_industry_history(
    code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_rows: int = 2000,
) -> Dict[str, Any]:
    """
    查询股票申万行业分类历史（一/二/三级展开）。
    可选 start_date / end_date 按计入日期过滤。
    """
    symbol, _ = parse_code(code)

    if start_date:
        start_date = validate_date(start_date)
    if end_date:
        end_date = validate_date(end_date)
    if start_date and end_date:
        ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists("STOCK_SW_INDUSTRY_VIEW"):
        raise RuntimeError("STOCK_SW_INDUSTRY_VIEW view not found.")

    max_rows = max(1, min(int(max_rows), 20000))

    filters = ["symbol = ?"]
    params: List[Any] = [symbol]
    if start_date:
        filters.append("start_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("start_date <= ?")
        params.append(end_date)

    df = run_sql(
        f"""
        SELECT
            ? AS code,
            symbol,
            start_date,
            sw_version,
            sw_l1_code,
            sw_l1_name,
            sw_l2_code,
            sw_l2_name,
            sw_l3_code,
            sw_l3_name,
            industry_code,
            update_time,
            updated_at
        FROM STOCK_SW_INDUSTRY_VIEW
        WHERE {' AND '.join(filters)}
        ORDER BY start_date
        """,
        [code] + params,
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_margin_data(
    code: str,
    start_date: str,
    end_date: str,
    max_rows: int = 2000,
) -> Dict[str, Any]:
    """
    MARGIN_DATA(code, trade_date, margin_buy, margin_balance, short_sell_vol, ...)
    获取融资融券明细数据。
    """
    _ = parse_code(code)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)
    ensure_span(start_date, end_date, MAX_DAYS_DEFAULT)

    if not table_exists("MARGIN_DATA"):
        raise RuntimeError("MARGIN_DATA table not found.")

    max_rows = max(1, min(int(max_rows), 20000))

    df = run_sql(
        """
        SELECT *
        FROM MARGIN_DATA
        WHERE UPPER(code) = UPPER(?)
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [code, start_date, end_date],
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_capital_detail(
    code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    max_rows: int = 500,
) -> Dict[str, Any]:
    """
    CAPITAL_DETAIL (GBBQ 股本变动/权息资料)
    获取除权除息、送配股、股本变化等记录。
    category 可选: 除权除息 / 股本变化 / 送配股上市
    若不传日期则返回该股票全部历史记录。
    """
    _ = parse_code(code)

    if not table_exists("CAPITAL_DETAIL"):
        raise RuntimeError("CAPITAL_DETAIL table not found.")

    max_rows = max(1, min(int(max_rows), 20000))

    conditions = ["UPPER(code) = UPPER(?)"]
    params: List[Any] = [code]

    if start_date:
        start_date = validate_date(start_date)
        conditions.append("date >= ?")
        params.append(start_date)

    if end_date:
        end_date = validate_date(end_date)
        conditions.append("date <= ?")
        params.append(end_date)

    if start_date and end_date:
        ensure_span(start_date, end_date, 20000)

    if category:
        conditions.append("category = ?")
        params.append(category)

    where = " AND ".join(conditions)
    df = run_sql(
        f"""
        SELECT *
        FROM CAPITAL_DETAIL
        WHERE {where}
        ORDER BY date
        """,
        params,
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def get_model_pool(
    status: Optional[str] = None,
    code: Optional[str] = None,
    model_name: Optional[str] = None,
    max_rows: int = 500,
) -> Dict[str, Any]:
    """
    MODEL_STOCK_POOL - 查询模型股票池。
    可按 status(OBSERVE/FOCUS/TRIGGERED/REMOVED)、code、model_name 过滤。
    不传参数则返回所有非 REMOVED 记录。
    """
    if not table_exists("MODEL_STOCK_POOL"):
        raise RuntimeError("MODEL_STOCK_POOL table not found.")

    max_rows = max(1, min(int(max_rows), 20000))

    conditions: List[str] = []
    params: List[Any] = []

    if code:
        _ = parse_code(code)
        conditions.append("UPPER(code) = UPPER(?)")
        params.append(code)

    if status:
        valid = ("OBSERVE", "FOCUS", "TRIGGERED", "REMOVED")
        s = status.upper()
        if s not in valid:
            raise ValueError(f"status must be one of {valid}")
        conditions.append("status = ?")
        params.append(s)
    elif not code:
        # default: exclude REMOVED
        conditions.append("status != 'REMOVED'")

    if model_name:
        conditions.append("model_name = ?")
        params.append(model_name)

    where = " AND ".join(conditions) if conditions else "1=1"
    df = run_sql(
        f"""
        SELECT *
        FROM MODEL_STOCK_POOL
        WHERE {where}
        ORDER BY updated_date DESC, code
        """,
        params,
    )
    return df_to_payload(df, max_rows)


@mcp.tool()
def query(sql: str, max_rows: int = 2000) -> Dict[str, Any]:
    """
    Raw SQL query (read-only). DDL/DML is blocked. LIMIT will be appended if missing.
    You can disable this tool by setting env ALLOW_RAW_QUERY=0.
    """
    if not ALLOW_RAW_QUERY:
        raise RuntimeError("Raw query tool is disabled by server policy (ALLOW_RAW_QUERY=0).")

    q = (sql or "").strip()
    if not q:
        raise ValueError("sql is required")

    validate_raw_query(q)

    max_rows = max(1, min(int(max_rows), 20000))
    q2 = add_limit_if_missing(q, max_rows)

    df = run_sql(q2)
    return df_to_payload(df, max_rows)


def main() -> None:
    # Optional: set duckdb pragmas for stability (best-effort; may vary by version)
    # with LOCK:
    #     CON.execute("PRAGMA threads=4")
    #     CON.execute("PRAGMA enable_progress_bar=false")
    mcp.run()


if __name__ == "__main__":
    main()
