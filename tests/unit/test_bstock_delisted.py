# 修改记录:
#   2026-09-14  Claude  新建: 锁住「bstock 逐股日线不再按 status 跳过退市股」
"""bstock.fetch_batch_data 对退市股(status='D')的处理

2026-09-14 之前这里有一句 `if status == "D": continue`，与上游重复判断且静默生效：
候选集已由 get_candidate_codes(is_delist=True) 放行，数据源却再否决一次，
且不计 processed、不进 failed、不打日志 —— 表现为「成功获取 0 条记录」，
从外面完全看不出被跳过了。实测 baostock 对 002898/600193/000004 都有数据。

「日常跑批不会误拉退市股」由窗口裁剪保证，测试在
test_dbutil_logic.py::test_get_candidate_data_delist_excluded_from_daily_run。
"""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from datasource import bstock


def _row(code: str) -> pd.DataFrame:
    return pd.DataFrame({"code": [code], "date": ["2026-07-01"], "close": [0.35]})


@pytest.fixture
def bs_env():
    """登录/重试/心跳全部打桩，只留取数逻辑"""
    with ExitStack() as stack:
        stack.enter_context(patch.object(bstock.bs, "login",
                                         return_value=MagicMock(error_code="0")))
        stack.enter_context(patch.object(bstock.bs, "logout"))
        stack.enter_context(patch.object(bstock.socket, "setdefaulttimeout"))
        stack.enter_context(patch.object(bstock, "_get_max_fetch_attempts", return_value=1))
        stack.enter_context(patch.object(bstock, "_get_progress_heartbeat_seconds",
                                         return_value=30))
        yield


def test_delisted_stock_is_actually_requested(bs_env):
    """正例: status='D' 的候选必须真的发出请求，其数据必须被收下。

    这是本次修复的核心 —— 此前退市股在这里被静默丢弃，
    291 只退市股「有 STOCK_DAILY 却零 DAILY_BASIC」即源于此
    (行情从本地 .day 进库，basic 只有 bstock 提供而 bstock 拒收)。
    """
    stocks = [("002898", "SZ", "2026-07-01", "2026-07-17", "D")]
    with patch.object(bstock, "fetch_stock_data",
                      return_value=(_row("002898.SZ"), _row("002898.SZ"))) as f:
        daily, basic = bstock.fetch_batch_data(stocks)

    f.assert_called_once_with("2026-07-01", "2026-07-17", "sz.002898")
    assert len(daily) == 1 and daily.iloc[0]["code"] == "002898.SZ"
    assert len(basic) == 1


def test_delisted_and_listed_both_requested(bs_env):
    """正例: 退市股与在市股混编时两只都取，不因 status 差异区别对待"""
    stocks = [("600000", "SH", "2026-07-01", "2026-07-17", "L"),
              ("002898", "SZ", "2026-07-01", "2026-07-17", "D")]
    with patch.object(bstock, "fetch_stock_data",
                      side_effect=[(_row("600000.SH"), pd.DataFrame()),
                                   (_row("002898.SZ"), pd.DataFrame())]) as f:
        daily, _ = bstock.fetch_batch_data(stocks)

    assert [c.args[2] for c in f.call_args_list] == ["sh.600000", "sz.002898"]
    assert sorted(daily["code"]) == ["002898.SZ", "600000.SH"]


def test_delisted_failure_is_not_silent(bs_env):
    """反例: 退市股取数失败必须计入失败并汇总抛错，不得回到「静默跳过」。

    这条把「不跳过」钉死在可观测行为上：只有退市股确实进了 processed，
    全批失败判定才会触发。若哪天 status 跳过被改回来，这里会变成
    processed=0 → 不抛错 → 测试失败。
    """
    stocks = [("002898", "SZ", "2026-07-01", "2026-07-17", "D")]
    with patch.object(bstock, "fetch_stock_data",
                      side_effect=RuntimeError("query failed")):
        with pytest.raises(bstock.BaoQueryError, match="全部 1 只"):
            bstock.fetch_batch_data(stocks)


def test_bj_prefix_9_still_skipped(bs_env):
    """反例(边界): 只删了退市判断，9 开头(北交所老代码)的跳过必须保留。

    baostock 不提供北交所数据，这条跳过与 status 无关，不在本次改动范围内。
    """
    stocks = [("920001", "BJ", "2026-07-01", "2026-07-17", "L")]
    with patch.object(bstock, "fetch_stock_data") as f:
        daily, _ = bstock.fetch_batch_data(stocks)
    f.assert_not_called()
    assert daily.empty
