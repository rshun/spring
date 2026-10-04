# 修改记录:
#   2026-10-02  Claude  新增 autouse fixture _redirect_csv_output: 所有测试的 CSV 落盘
#                       统一重定向到 tmp_path/csv, 此前未单独重定向的测试会污染项目 csv/
#   2026-10-04  Claude  check_daily / check_adjust 改为模块级 CSV_DIR, 重定向改为直接替换该变量
import pytest
import duckdb
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def _redirect_csv_output(tmp_path, monkeypatch):
    """所有测试的 CSV 落盘一律重定向到 tmp_path/csv, 不污染项目 csv/ 目录。

    项目 csv/ 是给人看核对结果的地方, 测试写进去的文件(日期是测试数据的日期)
    混在里面会被当成真实核对结果。各输出口都是模块级 CSV_DIR, 直接替换:
    util.checker / etl.import_daily / tools.check_daily / tools.check_adjust。
    datasource.tdx_offline 的 csv/gbbq 是 gbbq 输入缓存而非输出, 不在此重定向。
    单个测试若需自定目录, 在测试内再次 monkeypatch 即可覆盖本 fixture。
    """
    from util import checker
    import etl.import_daily
    import tools.check_adjust
    import tools.check_daily

    out = tmp_path / "csv"
    monkeypatch.setattr(checker, "CSV_DIR", out)
    monkeypatch.setattr(etl.import_daily, "CSV_DIR", out)
    monkeypatch.setattr(tools.check_daily, "CSV_DIR", out)
    monkeypatch.setattr(tools.check_adjust, "CSV_DIR", out)


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
