# 修改记录:
#   2026-09-10  Claude  新增：三个 save_*_to_db 写库失败必须重抛，且经真实函数传到 CLI 退出码 1
#   2026-09-11  Claude  覆盖其余 8 个 save_*_to_db 的重抛、三个新增空表早退、
#                       申万两函数缺列改抛 ValueError
"""util/dbutil 的 save_index_to_db / save_daily_to_db / save_base_to_db 失败路径，
以及 fetch_index / import_daily 走真实 save 函数时的退出码。不碰真实库。"""
import argparse
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import fetch_index, import_daily
from util import dbutil

DAILY = pd.DataFrame({"code": ["000001.SH"], "date": ["2026-09-08"], "open": [1.0], "high": [1.0],
                      "low": [1.0], "close": [1.0], "pre_close": [1.0], "volume": [1], "amount": [1.0],
                      "trade_status": [1]})
BASIC = pd.DataFrame({"code": ["600000.SH"], "trade_date": ["2026-09-08"], "turnover_rate": [1.0],
                      "pe": [10.0], "pb": [1.0], "is_st": [0]})


def _bad_conn():
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("模拟写库失败")
    return conn


# ── 库层：三个 save 函数失败必须重抛 ──────────────────────────────────────────

@pytest.mark.parametrize("func, df", [
    (dbutil.save_index_to_db, DAILY),
    (dbutil.save_daily_to_db, DAILY),
    (dbutil.save_base_to_db, BASIC),
], ids=["index", "daily", "base"])
def test_save_failure_raises(func, df):
    """反例: 连接执行抛错时必须抛给调用方，不能 logger.error 后静默返回"""
    with pytest.raises(RuntimeError, match="模拟写库失败"):
        func(df.copy(), _bad_conn())


def test_save_success_does_not_raise():
    """正例: 正常连接下 save_index_to_db 顺利返回（对照组）"""
    dbutil.save_index_to_db(DAILY.copy(), MagicMock())


@pytest.mark.parametrize("func", [dbutil.save_index_to_db, dbutil.save_daily_to_db, dbutil.save_base_to_db],
                         ids=["index", "daily", "base"])
def test_save_empty_frame_returns_early_without_touching_db(func):
    """正例: 空表是合法输入——显式早退、不碰连接、不抛错
    （此前空表触发的 DuckDB Binder Error 只是被 except 吞掉才「不报错」，重抛后必须显式处理）"""
    conn = _bad_conn()          # 任何 execute 都会抛错，借此证明根本没碰连接
    func(pd.DataFrame(), conn)
    conn.execute.assert_not_called()
    conn.register.assert_not_called()


# ── CLI 层：不 mock save 函数，写库失败要一路传到退出码 1 ──────────────────────

def _run_fetch_index(conn):
    args = argparse.Namespace(begin="20260908", end="20260908", exchanges=["all"], codes=None, source="bstock")
    src = MagicMock()
    src.fetch_batch_index.return_value = DAILY.copy()
    # myutil 整体 mock（与 test_etl_exit_codes 同款）：否则 main() 里真实的
    # configure_etl_logging() 会改写 etl.* logger，污染后续依赖 caplog 的用例
    with patch.object(fetch_index, "parse_arguments", return_value=args), \
         patch.object(fetch_index, "check_parameters", return_value=True), \
         patch.object(fetch_index, "myutil") as myutil, \
         patch.object(dbutil, "get_candidate_index", return_value=[("000001", "SH", "2026-09-08", "2026-09-08", "L")]), \
         patch.object(dbutil, "get_connection", return_value=conn):
        myutil.import_source_module.return_value = src
        return fetch_index.main()


def test_fetch_index_real_save_failure_returns_1():
    """反例: fetch_index 走真实 save_index_to_db，写库抛错 → 1（此前为 0）"""
    assert _run_fetch_index(_bad_conn()) == 1


def test_fetch_index_real_save_success_returns_0():
    """正例: 写库成功 → 0"""
    assert _run_fetch_index(MagicMock()) == 0


def test_import_daily_real_save_failure_returns_1():
    """反例: import_daily 走真实 save_daily_to_db，写库抛错 → 1（既有用例是 mock 掉函数后的空测）"""
    args = argparse.Namespace(begin="20260908", end="20260908", exchanges=["all"], codes=None,
                              source="bstock", print_only=False, date_range_only=False)
    src = MagicMock()
    src.fetch_batch_data.return_value = (DAILY.copy(), BASIC.copy())
    with patch.object(import_daily, "parse_arguments", return_value=args), \
         patch.object(import_daily, "check_parameters", return_value=True), \
         patch.object(import_daily, "myutil") as myutil, \
         patch.object(dbutil, "get_candidate_codes", return_value=[("600000", "SH", "2026-09-08", "2026-09-08", "L")]), \
         patch.object(dbutil, "get_connection", return_value=_bad_conn()):
        myutil.import_source_module.return_value = src
        assert import_daily.main() == 1


# ── 2026-09-11: 其余 8 个 save_*_to_db 也必须重抛 ──────────────────────────────

SHARES = pd.DataFrame({"code": ["600000.SH"], "date": ["2026-09-08"],
                       "total_shares": [1000], "float_shares": [800]})
STOCK_INFO = pd.DataFrame({"code": ["600000.SH"], "symbol": ["600000"], "name": ["浦发银行"],
                           "exchange": ["SH"], "board": ["MAIN"], "list_date": ["1999-11-10"],
                           "delist_date": [None], "list_status": ["L"]})
CALENDAR = pd.DataFrame({"cal_date": ["2026-09-08"], "is_open": [1]})
MARGIN_SUM = pd.DataFrame({"trade_date": ["2026-09-08"], "exchange_code": ["SH"],
                           "margin_buy_amount": [1.0], "margin_repay_amount": [1.0],
                           "margin_balance": [1.0], "short_sell_volume": [1.0],
                           "short_repay_volume": [1.0], "short_balance_volume": [1.0],
                           "short_balance_amount": [1.0], "margin_short_balance": [1.0]})
MARGIN_DET = MARGIN_SUM.assign(symbol=["600000"], code=["600000.SH"])
CAPITAL = pd.DataFrame({"code": ["600000"], "date": ["20260908"], "category": ["除权除息"],
                        "dividend": [1.0], "allotment_price": [0.0],
                        "bonus_share": [0.0], "allotment_share": [0.0]})
FINANCE = pd.DataFrame({"code": ["600000"], "report_date": ["2026-06-30"], "eps": [1.0]})
SW_RAW = pd.DataFrame({"symbol": ["600000"], "start_date": ["2026-09-08"],
                       "industry_code": ["801780"], "update_time": ["2026-09-08 00:00:00"]})
SW_HIER = pd.DataFrame({"sw_version": ["2021"], "industry_code": ["801780"],
                        "industry_name": ["银行"], "sw_level": [1], "parent_code": [None]})


@pytest.mark.parametrize("func, df", [
    (dbutil.save_shares_to_db, SHARES),
    (dbutil.load_stock_info_to_db, STOCK_INFO),
    (dbutil.save_calendar_to_db, CALENDAR),
    (dbutil.save_margin_summary_to_db, MARGIN_SUM),
    (dbutil.save_margin_detail_to_db, MARGIN_DET),
    (dbutil.save_capital_detail_to_db, CAPITAL),
    (dbutil.save_finance_report_to_db, FINANCE),
    (dbutil.save_stock_industry_clf_hist_sw_raw_to_db, SW_RAW),
    (dbutil.save_sw_industry_hierarchy_to_db, SW_HIER),
], ids=["shares", "stock_info", "calendar", "margin_summary", "margin_detail",
        "capital_detail", "finance_report", "sw_raw", "sw_hierarchy"])
def test_remaining_save_failures_raise(func, df):
    """反例: 写库失败必须抛给调用方，不能 logger.error 后静默返回。

    这些函数原本吞异常，结果是 sync_basic / trade_cal / sync_margin / sync_capital /
    sync_finance / sync_industry 写库失败仍退出 0（契约 C1）。
    """
    with pytest.raises(RuntimeError, match="模拟写库失败"):
        func(df.copy(), _bad_conn())


@pytest.mark.parametrize("func", [
    dbutil.save_shares_to_db,
    dbutil.load_stock_info_to_db,
    dbutil.save_calendar_to_db,
], ids=["shares", "stock_info", "calendar"])
def test_newly_guarded_empty_frame_returns_early(func):
    """正例: 空表是合法输入——早退、不碰连接、不抛错。

    这三个此前没有空表早退：加上 raise 之后若不补早退，空表会由 Binder Error 变成
    真正的失败退出（此前只是被 except 吞掉才「看起来没事」）。
    """
    conn = _bad_conn()
    func(pd.DataFrame(), conn)
    conn.execute.assert_not_called()
    conn.register.assert_not_called()


@pytest.mark.parametrize("func, df, table", [
    (dbutil.save_stock_industry_clf_hist_sw_raw_to_db, SW_RAW, "STOCK_INDUSTRY_CLF_HIST_SW_RAW"),
    (dbutil.save_sw_industry_hierarchy_to_db, SW_HIER, "SW_INDUSTRY"),
], ids=["sw_raw", "sw_hierarchy"])
def test_missing_required_column_raises(func, df, table):
    """反例: 数据源少列是契约被破坏，必须抛出。

    此前只 logger.error 后 return，etl/sync_industry.py 一条都没写也退出 0。
    """
    broken = df.drop(columns=[df.columns[-1]])
    with pytest.raises(ValueError, match="缺少字段"):
        func(broken, MagicMock())
