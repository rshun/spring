from util.dbutil import fill_daily_basic_shares
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
