import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, call

from datasource.bstock import (
    _is_not_logged_in,
    _is_broken_pipe_error,
    _raise_for_query_error,
    BaoNotLoggedInError,
    BaoQueryError,
    fetch_stock_info,
    fetch_stock_data,
    fetch_index_data,
)


# ── _is_not_logged_in ─────────────────────────────────────────────────────────

def test_is_not_logged_in_true():
    assert _is_not_logged_in("用户未登录，请重新登录") is True


def test_is_not_logged_in_false_other():
    assert _is_not_logged_in("其他错误消息") is False


def test_is_not_logged_in_none():
    assert _is_not_logged_in(None) is False


def test_is_not_logged_in_empty():
    assert _is_not_logged_in("") is False


# ── _is_broken_pipe_error ─────────────────────────────────────────────────────

def test_is_broken_pipe_true_for_broken_pipe_error():
    assert _is_broken_pipe_error(BrokenPipeError()) is True


def test_is_broken_pipe_true_for_oserror_errno32():
    import errno
    err = OSError(errno.EPIPE, "broken pipe")
    assert _is_broken_pipe_error(err) is True


def test_is_broken_pipe_true_for_msg_containing_broken_pipe():
    assert _is_broken_pipe_error(RuntimeError("Broken pipe")) is True


def test_is_broken_pipe_false_for_other():
    assert _is_broken_pipe_error(ValueError("other error")) is False


# ── _raise_for_query_error ────────────────────────────────────────────────────

def test_raise_for_query_error_not_logged_in():
    with pytest.raises(BaoNotLoggedInError):
        _raise_for_query_error("sh.600519", "用户未登录，请重新登录")


def test_raise_for_query_error_broken_pipe():
    with pytest.raises(BrokenPipeError):
        _raise_for_query_error("sh.600519", "Broken pipe")


def test_raise_for_query_error_other():
    with pytest.raises(BaoQueryError):
        _raise_for_query_error("sh.600519", "其他查询错误")


# ── fetch_stock_info board 分类 ───────────────────────────────────────────────

def _make_bs_login_ok():
    lg = MagicMock()
    lg.error_code = "0"
    return lg


def _make_query_basic_simple(rows: list[list]):
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["code", "code_name", "ipoDate", "outDate", "status", "type"]
    rows_copy = list(rows)
    call_count = [0]

    def next_fn():
        return call_count[0] < len(rows_copy)

    def get_row_fn():
        row = rows_copy[call_count[0]]
        call_count[0] += 1
        return row

    rs.next.side_effect = next_fn
    rs.get_row_data.side_effect = get_row_fn
    return rs


@pytest.mark.parametrize("symbol,expected_board", [
    ("300001", "GEM"),
    ("301001", "GEM"),
    ("688001", "STAR"),
    ("689001", "STAR"),
    ("600519", "MAIN"),
    ("000001", "MAIN"),
])
def test_fetch_stock_info_board_classification(symbol, expected_board):
    exchange = "sh" if symbol.startswith(("6", "9")) else "sz"
    row = [f"{exchange}.{symbol}", "测试股票", "2010-01-01", "", "1", "1"]
    rs = _make_query_basic_simple([row])
    lg = _make_bs_login_ok()
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.query_stock_basic", return_value=rs):
            with patch("datasource.bstock.bs.logout"):
                df, _ = fetch_stock_info(["all"])
    row_data = df[df["symbol"] == symbol]
    assert len(row_data) == 1
    assert row_data.iloc[0]["board"] == expected_board


def test_fetch_stock_info_index_board():
    rs = _make_query_basic_simple([["sh.000001", "上证指数", "1990-12-19", "", "1", "2"]])
    lg = _make_bs_login_ok()
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.query_stock_basic", return_value=rs):
            with patch("datasource.bstock.bs.logout"):
                df, _ = fetch_stock_info(["all"])
    assert df.iloc[0]["board"] == "INDEX"


def test_fetch_stock_info_code_format():
    """黑盒：输出 code 格式为 600519.SH，不是 sh.600519。"""
    rs = _make_query_basic_simple([["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"]])
    lg = _make_bs_login_ok()
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.query_stock_basic", return_value=rs):
            with patch("datasource.bstock.bs.logout"):
                df, _ = fetch_stock_info(["all"])
    assert df.iloc[0]["code"] == "600519.SH"


def test_fetch_stock_info_login_fail_returns_empty():
    lg = MagicMock()
    lg.error_code = "9999"
    lg.error_msg = "登录失败"
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.logout"):
            df, _ = fetch_stock_info(["all"])
    assert df.empty


# ── fetch_stock_data 数据转换 ─────────────────────────────────────────────────

def _make_kdata_rs(rows: list[list]):
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["date", "code", "open", "high", "low", "close",
                 "preclose", "volume", "amount", "adjustflag",
                 "turn", "tradestatus", "pctChg", "isST", "peTTM", "pbMRQ"]
    rows_copy = list(rows)
    call_count = [0]
    rs.next.side_effect = lambda: call_count[0] < len(rows_copy)

    def get_row():
        row = rows_copy[call_count[0]]
        call_count[0] += 1
        return row

    rs.get_row_data.side_effect = get_row
    return rs


def test_fetch_stock_data_columns():
    row = ["2023-01-03", "sh.600519", "1800", "1850", "1780", "1820",
           "1790", "10000", "180000000", "3", "1.5", "1", "1.5", "0", "35.2", "12.1"]
    rs = _make_kdata_rs([row])
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        df_daily, df_basic = fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")
    assert "code" in df_daily.columns
    assert "pre_close" in df_daily.columns
    assert "pe" in df_basic.columns


def test_fetch_stock_data_price_nan_filled_zero():
    """停牌时价格为空字符串，应填 0。"""
    row = ["2023-01-03", "sh.600519", "", "", "", "",
           "1790", "0", "0", "3", "", "0", "0", "0", "", ""]
    rs = _make_kdata_rs([row])
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        df_daily, _ = fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")
    assert df_daily.iloc[0]["open"] == 0


def test_fetch_stock_data_empty_returns_empty_dfs():
    rs = MagicMock()
    rs.error_code = "0"
    rs.next.return_value = False
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        df_daily, df_basic = fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")
    assert df_daily.empty
    assert df_basic.empty


def test_fetch_stock_data_query_error_raises():
    rs = MagicMock()
    rs.error_code = "9999"
    rs.error_msg = "其他错误"
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        with pytest.raises(BaoQueryError):
            fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")


def test_fetch_stock_data_not_logged_in_raises():
    rs = MagicMock()
    rs.error_code = "9999"
    rs.error_msg = "用户未登录，请重新登录"
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        with pytest.raises(BaoNotLoggedInError):
            fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")


# ── fetch_index_data ──────────────────────────────────────────────────────────

def test_fetch_index_data_bad_code_format_returns_empty():
    row = ["2023-01-03", "000001", "3300", "3350", "3280", "3320",
           "3290", "100000000", "5e11", "0.5"]
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["date", "code", "open", "high", "low", "close",
                 "preclose", "volume", "amount", "pctChg"]
    rows_copy = [row]
    call_count = [0]
    rs.next.side_effect = lambda: call_count[0] < len(rows_copy)

    def get_row():
        r = rows_copy[call_count[0]]
        call_count[0] += 1
        return r

    rs.get_row_data.side_effect = get_row
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        result = fetch_index_data("2023-01-03", "2023-01-03", "sh.000001")
    assert isinstance(result, pd.DataFrame)
