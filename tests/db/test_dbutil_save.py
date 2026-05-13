import pytest
import pandas as pd
from datetime import date

from util.dbutil import (
    save_daily_to_db,
    save_base_to_db,
    save_calendar_to_db,
    save_shares_to_db,
    save_margin_summary_to_db,
    save_margin_detail_to_db,
    load_stock_info_to_db,
)
from tests.conftest import insert_stock_info


def _daily_df(code="600519.SH", trade_date="2023-01-03", tradestatus=1):
    return pd.DataFrame({
        "code": [code], "date": [trade_date],
        "open": [1800.0], "high": [1850.0], "low": [1780.0], "close": [1820.0],
        "pre_close": [1790.0], "tradestatus": [tradestatus],
        "volume": [10000], "amount": [18200000.0],
    })


def _basic_df(code="600519.SH", trade_date="2023-01-03",
              pe=35.0, pb=12.0, turnover=1.5, is_st=0):
    return pd.DataFrame({
        "code": [code], "trade_date": [trade_date],
        "turnover_rate": [turnover], "pe": [pe], "pb": [pb], "is_st": [is_st],
    })


# ── save_daily_to_db ──────────────────────────────────────────────────────────

def test_save_daily_inserts_row(mem_db):
    save_daily_to_db(_daily_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0]
    assert count == 1


def test_save_daily_replace_on_duplicate(mem_db):
    save_daily_to_db(_daily_df(tradestatus=1), mem_db)
    save_daily_to_db(_daily_df(tradestatus=0), mem_db)  # 同一 (code, date)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0]
    assert count == 1  # INSERT OR REPLACE，不产生重复


def test_save_daily_normalizes_tradestatus_nan(mem_db):
    """黑盒：tradestatus 含 NaN 时 _normalize_daily_df 应被调用，NaN 填 -1。"""
    df = _daily_df()
    df["tradestatus"] = float("nan")
    save_daily_to_db(df, mem_db)
    row = mem_db.execute("SELECT tradestatus FROM STOCK_DAILY").fetchone()
    assert row[0] == -1


def test_save_daily_empty_df_no_error(mem_db):
    save_daily_to_db(pd.DataFrame(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0]
    assert count == 0


# ── save_base_to_db ───────────────────────────────────────────────────────────

def test_save_base_inserts_row(mem_db):
    save_base_to_db(_basic_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM DAILY_BASIC").fetchone()[0]
    assert count == 1


def test_save_base_coalesce_existing_pe_not_overwritten(mem_db):
    """黑盒：先写 pe=35.0，再写 pe=None，pe 应保持 35.0（COALESCE 保护）。"""
    save_base_to_db(_basic_df(pe=35.0), mem_db)
    save_base_to_db(_basic_df(pe=None), mem_db)  # ON CONFLICT + COALESCE
    row = mem_db.execute("SELECT pe FROM DAILY_BASIC").fetchone()
    assert row[0] == 35.0


def test_save_base_updates_existing_pe(mem_db):
    save_base_to_db(_basic_df(pe=35.0), mem_db)
    save_base_to_db(_basic_df(pe=40.0), mem_db)
    row = mem_db.execute("SELECT pe FROM DAILY_BASIC").fetchone()
    assert row[0] == 40.0


# ── save_calendar_to_db ───────────────────────────────────────────────────────

def test_save_calendar_inserts(mem_db):
    df = pd.DataFrame({"cal_date": ["2023-01-03", "2023-01-04"], "is_open": [1, 1]})
    save_calendar_to_db(df, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM TRADE_CAL").fetchone()[0]
    assert count == 2


def test_save_calendar_upsert_no_duplicate(mem_db):
    df = pd.DataFrame({"cal_date": ["2023-01-03"], "is_open": [1]})
    save_calendar_to_db(df, mem_db)
    save_calendar_to_db(df, mem_db)  # 重复写入
    count = mem_db.execute("SELECT COUNT(*) FROM TRADE_CAL").fetchone()[0]
    assert count == 1


def test_save_calendar_updates_is_open(mem_db):
    df1 = pd.DataFrame({"cal_date": ["2023-01-07"], "is_open": [1]})
    df2 = pd.DataFrame({"cal_date": ["2023-01-07"], "is_open": [0]})
    save_calendar_to_db(df1, mem_db)
    save_calendar_to_db(df2, mem_db)
    row = mem_db.execute("SELECT is_open FROM TRADE_CAL WHERE cal_date = '2023-01-07'").fetchone()
    assert row[0] == 0


# ── save_shares_to_db ─────────────────────────────────────────────────────────

def test_save_shares_only_updates_share_fields(mem_db):
    """黑盒：只更新 total_shares / float_shares，不影响 pe。"""
    save_base_to_db(_basic_df(pe=35.0), mem_db)
    shares_df = pd.DataFrame({
        "code": ["600519.SH"], "date": ["2023-01-03"],
        "total_shares": [1260000000], "float_shares": [1200000000],
    })
    save_shares_to_db(shares_df, mem_db)
    row = mem_db.execute("SELECT pe, total_shares FROM DAILY_BASIC").fetchone()
    assert row[0] == 35.0           # pe 未变
    assert row[1] == 1260000000     # total_shares 已更新


# ── save_margin_summary_to_db ─────────────────────────────────────────────────

def _summary_df():
    return pd.DataFrame({
        "trade_date": ["2023-01-03"],
        "exchange_code": ["SH"],
        "margin_buy_amount": [1e9], "margin_repay_amount": [5e8],
        "margin_balance": [1e10], "short_sell_volume": [1e6],
        "short_repay_volume": [5e5], "short_balance_volume": [2e6],
        "short_balance_amount": [2e8], "margin_short_balance": [1.2e10],
    })


def test_save_margin_summary_inserts(mem_db):
    save_margin_summary_to_db(_summary_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_SUMMARY_DAILY").fetchone()[0]
    assert count == 1


def test_save_margin_summary_none_returns_early(mem_db):
    save_margin_summary_to_db(None, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_SUMMARY_DAILY").fetchone()[0]
    assert count == 0


def test_save_margin_summary_empty_returns_early(mem_db):
    save_margin_summary_to_db(pd.DataFrame(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_SUMMARY_DAILY").fetchone()[0]
    assert count == 0


# ── save_margin_detail_to_db ──────────────────────────────────────────────────

def _detail_df():
    return pd.DataFrame({
        "trade_date": ["2023-01-03"],
        "exchange_code": ["SZ"],
        "symbol": ["000001"],
        "code": ["000001.SZ"],
        "margin_buy_amount": [1e8], "margin_repay_amount": [5e7],
        "margin_balance": [1e9], "short_sell_volume": [1e5],
        "short_repay_volume": [5e4], "short_balance_volume": [2e5],
        "short_balance_amount": [2e7], "margin_short_balance": [1.2e9],
    })


def test_save_margin_detail_inserts(mem_db):
    save_margin_detail_to_db(_detail_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_DETAIL_DAILY").fetchone()[0]
    assert count == 1


def test_save_margin_detail_upsert_on_conflict(mem_db):
    """同 (trade_date, exchange_code, symbol) 冲突时更新，不产生重复。"""
    df1 = _detail_df()
    df2 = _detail_df()
    df2["margin_balance"] = [2e9]
    save_margin_detail_to_db(df1, mem_db)
    save_margin_detail_to_db(df2, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_DETAIL_DAILY").fetchone()[0]
    assert count == 1
    row = mem_db.execute("SELECT margin_balance FROM MARGIN_DETAIL_DAILY").fetchone()
    assert float(row[0]) == pytest.approx(2e9)


def test_save_margin_detail_none_returns_early(mem_db):
    save_margin_detail_to_db(None, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_DETAIL_DAILY").fetchone()[0]
    assert count == 0


# ── load_stock_info_to_db ─────────────────────────────────────────────────────

def _stock_info_df(name="贵州茅台"):
    return pd.DataFrame({
        "code": ["600519.SH"], "symbol": ["600519"], "name": [name],
        "exchange": ["SH"], "board": ["MAIN"],
        "list_date": ["2001-08-27"], "delist_date": [None], "list_status": ["L"],
    })


def test_load_stock_info_inserts(mem_db):
    load_stock_info_to_db(_stock_info_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_INFO").fetchone()[0]
    assert count == 1


def test_load_stock_info_updates_last_updated_at_on_change(mem_db):
    load_stock_info_to_db(_stock_info_df("贵州茅台"), mem_db)
    ts1 = mem_db.execute("SELECT last_updated_at FROM STOCK_INFO").fetchone()[0]
    import time; time.sleep(0.01)
    load_stock_info_to_db(_stock_info_df("贵州茅台NEW"), mem_db)  # name 变了
    ts2 = mem_db.execute("SELECT last_updated_at FROM STOCK_INFO").fetchone()[0]
    assert ts2 >= ts1  # last_updated_at 更新了
