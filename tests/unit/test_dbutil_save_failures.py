# 修改记录:
#   2026-09-10  Claude  新增：三个 save_*_to_db 写库失败必须重抛，且经真实函数传到 CLI 退出码 1
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
