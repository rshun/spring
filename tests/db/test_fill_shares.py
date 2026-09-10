# 修改记录:
#   2026-09-10  Claude  新增反例：库层失败必须重抛（此前 fill_daily_basic_shares / _mv 吞异常）
from unittest.mock import MagicMock

import pytest

from util.dbutil import fill_daily_basic_shares, fill_daily_basic_mv
from tests.conftest import insert_stock_info


def _insert_daily_basic(conn, code, trade_date):
    conn.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date) VALUES (?, ?)",
        [code, trade_date],
    )


def _insert_capital_detail(conn, symbol, trade_date, category,
                           prev_float, prev_total, float_after, total_after):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL "
        "(code, date, category, dividend, allotment_price, bonus_share, allotment_share) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [symbol, trade_date, category, prev_float, prev_total, float_after, total_after],
    )


def test_fill_shares_uses_transfer_listing_events(mem_db):
    insert_stock_info(mem_db, "600399", "SH", "MAIN", "2018-01-01")
    _insert_daily_basic(mem_db, "600399.SH", "2018-12-27")
    _insert_daily_basic(mem_db, "600399.SH", "2018-12-28")
    _insert_daily_basic(mem_db, "600399.SH", "2018-12-29")
    _insert_capital_detail(
        mem_db,
        "600399",
        "2018-12-28",
        "转配股上市",
        23246.4902,
        52000,
        197210,
        197210,
    )

    fill_daily_basic_shares("2018-12-27", "2018-12-29", conn=mem_db)

    rows = mem_db.execute(
        "SELECT CAST(trade_date AS VARCHAR), float_shares, total_shares "
        "FROM DAILY_BASIC ORDER BY trade_date"
    ).fetchall()
    assert rows == [
        ("2018-12-27", 232464902, 520000000),
        ("2018-12-28", 1972100000, 1972100000),
        ("2018-12-29", 1972100000, 1972100000),
    ]


def test_fill_shares_ignores_unknown_new_category(mem_db):
    insert_stock_info(mem_db, "000908", "SZ", "MAIN", "2020-01-01")
    _insert_daily_basic(mem_db, "000908.SZ", "2026-03-11")
    _insert_capital_detail(
        mem_db,
        "000908",
        "2026-03-11",
        "未知新类别",
        0,
        0,
        10,
        0,
    )

    fill_daily_basic_shares("2026-03-11", "2026-03-11", conn=mem_db)

    row = mem_db.execute(
        "SELECT float_shares, total_shares FROM DAILY_BASIC"
    ).fetchone()
    assert row == (None, None)


def test_fill_shares_ignores_private_placement_plan_events(mem_db):
    insert_stock_info(mem_db, "600000", "SH", "MAIN", "2020-01-01")
    _insert_daily_basic(mem_db, "600000.SH", "2024-01-02")
    _insert_capital_detail(
        mem_db,
        "600000",
        "2024-01-02",
        "增发新股",
        0,
        0,
        8888,
        9999,
    )

    fill_daily_basic_shares("2024-01-02", "2024-01-02", conn=mem_db)

    row = mem_db.execute(
        "SELECT float_shares, total_shares FROM DAILY_BASIC"
    ).fetchone()
    assert row == (None, None)


# ── 2026-09-10：库层失败必须重抛（契约 C1，CLI 才能返回退出码 1）─────────────────

@pytest.mark.parametrize("func", [fill_daily_basic_shares, fill_daily_basic_mv],
                         ids=["shares", "mv"])
def test_db_failure_raises_instead_of_swallowing(func):
    """反例: 连接执行抛错时函数必须把异常抛给调用方, 不能 logger.error 后静默返回"""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        func("2026-09-01", "2026-09-08", None, None, conn=conn)
