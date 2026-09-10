# 修改记录:
#   2026-09-10  Claude  新增：baostock 批量取数的失败路径——首登失败必须抛错、网络类错误码必须重试
"""datasource/bstock.py 失败路径：登录失败不得返回空表；10002xxx 网络错误码走重试；非网络错误不重试。
全部 mock，不联网。"""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from datasource import bstock

STOCKS = [("600000", "SH", "2026-09-08", "2026-09-08", "L")]
K_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST,peTTM,pbMRQ".split(",")
K_ROW = ["2026-09-08", "sh.600000", "10", "11", "9", "10.5", "10", "100", "1050", "3", "1.5", "1", "5", "0", "8", "1"]
IDX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg".split(",")
IDX_ROW = ["2026-09-08", "sh.000001", "3000", "3010", "2990", "3005", "2998", "1000", "2000000", "0.2"]
ADJ_FIELDS = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]
ADJ_ROW = ["sh.600000", "2026-09-08", "0.5", "2", "1.1"]


def _rs(fields, rows, error="0", msg="ok"):
    rs = MagicMock(error_code=error, error_msg=msg, fields=fields)
    rs.next.side_effect = [True] * len(rows) + [False]
    rs.get_row_data.side_effect = rows
    return rs


@pytest.fixture
def session():
    """已登录的假会话：重试次数 3、各延迟 0、relogin 可计数"""
    with ExitStack() as stack:
        stack.enter_context(patch.object(bstock.bs, "login", return_value=MagicMock(error_code="0")))
        stack.enter_context(patch.object(bstock.bs, "logout"))
        stack.enter_context(patch.object(bstock.socket, "setdefaulttimeout"))
        stack.enter_context(patch.object(bstock, "_get_max_fetch_attempts", return_value=3))
        stack.enter_context(patch.object(bstock, "_get_progress_heartbeat_seconds", return_value=30))
        for name in ("_get_retry_delay_login", "_get_retry_delay_pipe", "_get_retry_delay_missing"):
            stack.enter_context(patch.object(bstock, name, return_value=0))
        stack.enter_context(patch.object(bstock.time, "sleep"))
        relogin = stack.enter_context(patch.object(bstock, "relogin"))
        yield relogin


# ── #2 首次登录失败必须抛错（此前返回空表，CLI 当作「无数据」退出 0）────────────

@pytest.mark.parametrize("func", [bstock.fetch_batch_data, bstock.fetch_adjust_factors, bstock.fetch_batch_index],
                         ids=["batch_data", "adjust_factors", "batch_index"])
def test_login_failure_raises(func):
    """反例: bs.login() 返回非 0 错误码 → 抛 BaoQueryError，而不是静默返回空表"""
    with patch.object(bstock.bs, "login", return_value=MagicMock(error_code="10001002", error_msg="用户名或密码错误")), \
         patch.object(bstock.socket, "setdefaulttimeout"):
        with pytest.raises(bstock.BaoQueryError, match="登录失败"):
            func(STOCKS)


# ── #3 网络类错误码 10002xxx 必须进入重试分支 ───────────────────────────────────

def test_index_network_error_is_retried_then_succeeds(session):
    """正例: 单只指数首次返回 10002007，重登重试后拿到数据；结果含该指数、relogin 一次"""
    with patch.object(bstock.bs, "query_history_k_data_plus",
                      side_effect=[_rs([], [], "10002007", "网络接收错误"), _rs(IDX_FIELDS, [IDX_ROW])]) as q:
        df = bstock.fetch_batch_index([("000001", "SH", "2026-09-08", "2026-09-08", "L")])
    assert q.call_count == 2
    session.assert_called_once()
    assert df["code"].tolist() == ["000001.SH"]


def test_stock_network_error_is_retried_then_succeeds(session):
    """正例: 逐股路径同样重试（fetch_stock_data 补传 error_code）"""
    with patch.object(bstock.bs, "query_history_k_data_plus",
                      side_effect=[_rs([], [], "10002005", "网络发送错误"), _rs(K_FIELDS, [K_ROW])]):
        daily, _ = bstock.fetch_batch_data(STOCKS)
    session.assert_called_once()
    assert daily["code"].tolist() == ["600000.SH"]


def test_adjust_network_error_is_retried_then_succeeds(session):
    """正例: 逐股复权因子路径同样重试"""
    with patch.object(bstock.bs, "query_adjust_factor",
                      side_effect=[_rs([], [], "10002008", "网络接收超时"), _rs(ADJ_FIELDS, [ADJ_ROW])]):
        df = bstock.fetch_adjust_factors(STOCKS)
    session.assert_called_once()
    assert df["code"].tolist() == ["600000.SH"]


def test_non_network_error_is_not_retried(session):
    """反例: 参数错误一类的非瞬时错误码不得重试——查一次、不 relogin、该指数跳过"""
    with patch.object(bstock.bs, "query_history_k_data_plus",
                      return_value=_rs([], [], "10004006", "参数错误")) as q:
        df = bstock.fetch_batch_index([("000001", "SH", "2026-09-08", "2026-09-08", "L")])
    assert q.call_count == 1
    session.assert_not_called()
    assert df.empty


def test_network_error_exhausts_retries_then_skips(session):
    """反例: 网络错误持续 3 次 → 重试耗尽后跳过该指数（不整批中止），relogin 2 次"""
    with patch.object(bstock.bs, "query_history_k_data_plus",
                      return_value=_rs([], [], "10002007", "网络接收错误")) as q:
        df = bstock.fetch_batch_index([("000001", "SH", "2026-09-08", "2026-09-08", "L")])
    assert q.call_count == 3
    assert session.call_count == 2
    assert df.empty
