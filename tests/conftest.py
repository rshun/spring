import pytest
import duckdb
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture
def mem_db():
    """每个测试函数独立的 in-memory DuckDB，已初始化全部 schema 表。"""
    conn = duckdb.connect(":memory:")
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    conn.execute(schema_path.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def insert_stock_info(conn, symbol: str, exchange: str, board: str,
                      list_date: str, delist_date: str = None,
                      list_status: str = "L"):
    code = f"{symbol}.{exchange}"
    conn.execute(
        "INSERT INTO STOCK_INFO (code, symbol, name, exchange, board, "
        "list_date, delist_date, list_status, created_at, last_updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, now(), now())",
        [code, symbol, f"Test {symbol}", exchange, board,
         list_date, delist_date, list_status]
    )


def insert_trade_cal(conn, cal_date: str, is_open: int):
    conn.execute(
        "INSERT INTO TRADE_CAL (cal_date, is_open) VALUES (?, ?)",
        [cal_date, is_open]
    )
