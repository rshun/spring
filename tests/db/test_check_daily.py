import pytest

from tools.check_daily import _query_xdr_preclose_mismatches
from tests.conftest import insert_stock_info, insert_trade_cal


def _seed_stock(conn, symbol="600519", exchange="SH", board="MAIN"):
    insert_stock_info(conn, symbol, exchange, board, "2020-01-01")


def _ins_cal(conn, dates):
    for d in dates:
        insert_trade_cal(conn, d, 1)


def _ins_daily(conn, code, date, close=None, pre_close=None, tradestatus=1):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, 0, 0, 0, ?, ?, ?, 0, 0)",
        [code, date, close, pre_close, tradestatus],
    )


def _ins_xdr(conn, code, date, dividend=0, allotment_price=0,
             bonus_share=0, allotment_share=0):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share, updated_at) "
        "VALUES (?, ?, '除权除息', ?, ?, ?, ?, now())",
        [code, date, dividend, allotment_price, bonus_share, allotment_share],
    )


def test_mismatch_detected_with_bonus_and_dividend(mem_db):
    """送股+分红:theory=(close_prev - div/10 + 0)/(1+bonus/10).
    close_prev=11, dividend=5(每10股), bonus_share=10(每10股) →
    theory=(11-0.5)/(1+1)=5.25。pre_close=11 与 theory 差远 → 命中。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-01-04", "2023-01-05"])
    _ins_daily(mem_db, "600519.SH", "2023-01-04", close=11.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-01-05", close=5.2, pre_close=11.0)
    _ins_xdr(mem_db, "600519.SH", "2023-01-05", dividend=5, bonus_share=10)

    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-01-05", "2023-01-05", "", "", []
    )
    assert len(rows) == 1
    xdr_date, code, name, close_prev, pre_close, theory = rows[0]
    assert code == "600519.SH"
    assert close_prev == 11.0
    assert pre_close == 11.0
    assert abs(theory - 5.25) < 1e-9
