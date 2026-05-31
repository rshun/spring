import pandas as pd

from util.dbutil import fill_daily_basic_turnover
from tests.conftest import insert_stock_info


def _insert_daily_basic(conn, code, trade_date, float_shares=None, turnover_rate=None):
    conn.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, float_shares, turnover_rate) "
        "VALUES (?, ?, ?, ?)",
        [code, trade_date, float_shares, turnover_rate],
    )


def _insert_stock_daily(conn, code, date, volume, tradestatus=1):
    conn.execute(
        "INSERT INTO STOCK_DAILY "
        "(code, date, open, high, low, close, pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [code, date, 10, 10, 10, 10, 10, tradestatus, volume, 0.0],
    )


def _turnover(conn, code, trade_date):
    return conn.execute(
        "SELECT turnover_rate FROM DAILY_BASIC WHERE code = ? AND trade_date = ?",
        [code, trade_date],
    ).fetchone()[0]


# ── 正常测试 ──────────────────────────────────────────────────────────────────

def test_turnover_basic_calculation(mem_db):
    """换手率(%) = 成交量 / 流通股本 × 100。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    _insert_daily_basic(mem_db, "600519.SH", "2023-01-03", float_shares=1_000_000)
    _insert_stock_daily(mem_db, "600519.SH", "2023-01-03", volume=100_000)

    fill_daily_basic_turnover("2023-01-03", "2023-01-03", conn=mem_db)

    assert _turnover(mem_db, "600519.SH", "2023-01-03") == 10.0


def test_turnover_returns_updated_row_count(mem_db):
    """返回实际更新的行数。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    for d in ("2023-01-03", "2023-01-04"):
        _insert_daily_basic(mem_db, "600519.SH", d, float_shares=1_000_000)
        _insert_stock_daily(mem_db, "600519.SH", d, volume=100_000)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-04", conn=mem_db)

    assert updated == 2


def test_turnover_overwrite_replaces_existing(mem_db):
    """overwrite=True 重算并覆盖已有换手率。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    _insert_daily_basic(mem_db, "600519.SH", "2023-01-03",
                        float_shares=1_000_000, turnover_rate=99.9)
    _insert_stock_daily(mem_db, "600519.SH", "2023-01-03", volume=100_000)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-03",
                                        overwrite=True, conn=mem_db)

    assert updated == 1
    assert _turnover(mem_db, "600519.SH", "2023-01-03") == 10.0


# ── 反向 / 边界测试 ──────────────────────────────────────────────────────────

def test_turnover_no_overwrite_keeps_existing(mem_db):
    """默认 overwrite=False 不触碰已有换手率, 返回 0 行。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    _insert_daily_basic(mem_db, "600519.SH", "2023-01-03",
                        float_shares=1_000_000, turnover_rate=99.9)
    _insert_stock_daily(mem_db, "600519.SH", "2023-01-03", volume=100_000)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-03", conn=mem_db)

    assert updated == 0
    assert _turnover(mem_db, "600519.SH", "2023-01-03") == 99.9


def test_turnover_skips_null_float_shares(mem_db):
    """float_shares 为空(无股本数据)的行跳过, 换手率保持 NULL。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    _insert_daily_basic(mem_db, "600519.SH", "2023-01-03", float_shares=None)
    _insert_stock_daily(mem_db, "600519.SH", "2023-01-03", volume=100_000)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-03", conn=mem_db)

    assert updated == 0
    assert _turnover(mem_db, "600519.SH", "2023-01-03") is None


def test_turnover_zero_float_shares_no_div_error(mem_db):
    """float_shares 为 0 时安全跳过, 不抛除零异常。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    _insert_daily_basic(mem_db, "600519.SH", "2023-01-03", float_shares=0)
    _insert_stock_daily(mem_db, "600519.SH", "2023-01-03", volume=100_000)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-03", conn=mem_db)

    assert updated == 0
    assert _turnover(mem_db, "600519.SH", "2023-01-03") is None


def test_turnover_skips_suspended_day(mem_db):
    """停牌日(tradestatus=0)跳过, 换手率保持 NULL。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    _insert_daily_basic(mem_db, "600519.SH", "2023-01-03", float_shares=1_000_000)
    _insert_stock_daily(mem_db, "600519.SH", "2023-01-03", volume=0, tradestatus=0)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-03", conn=mem_db)

    assert updated == 0
    assert _turnover(mem_db, "600519.SH", "2023-01-03") is None


def test_turnover_code_filter(mem_db):
    """指定 codes 时只回填该股票。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2023-01-01")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "2023-01-01")
    for code in ("600519.SH", "000001.SZ"):
        _insert_daily_basic(mem_db, code, "2023-01-03", float_shares=1_000_000)
        _insert_stock_daily(mem_db, code, "2023-01-03", volume=100_000)

    updated = fill_daily_basic_turnover("2023-01-03", "2023-01-03",
                                        codes=["600519.SH"], conn=mem_db)

    assert updated == 1
    assert _turnover(mem_db, "600519.SH", "2023-01-03") == 10.0
    assert _turnover(mem_db, "000001.SZ", "2023-01-03") is None
