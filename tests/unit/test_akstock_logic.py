import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from datasource.akstock import (
    fetch_bj_stock_data,
    fetch_stock_info,
    fetch_stock_data,
    fetch_margin_detail,
    fetch_stock_industry_clf_hist_sw,
    _fetch_summary_sse,
    _fetch_summary_szse,
    _SUMMARY_OUT_COLS,
    _DETAIL_OUT_COLS,
    _INDUSTRY_OUT_COLS,
)


# ── fetch_bj_stock_data ───────────────────────────────────────────────────────

def _bj_raw_df():
    return pd.DataFrame({
        "证券代码": ["430047", "832566"],
        "证券简称": ["诺思兰德", "奥迪威"],
        "上市日期": ["2014-04-25", "2021-12-02"],
        "总股本": ["10000", "20000"],
        "流通股本": ["8000", "15000"],
    })


def test_fetch_bj_stock_data_exchange_is_bj():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=_bj_raw_df()):
        df_info, df_basic = fetch_bj_stock_data("2024-01-02")
    assert (df_info["exchange"] == "BJ").all()


def test_fetch_bj_stock_data_code_format():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=_bj_raw_df()):
        df_info, _ = fetch_bj_stock_data("2024-01-02")
    assert all(c.endswith(".BJ") for c in df_info["code"])


def test_fetch_bj_stock_data_basic_has_shares():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=_bj_raw_df()):
        _, df_basic = fetch_bj_stock_data("2024-01-02")
    assert "total_shares" in df_basic.columns
    assert "float_shares" in df_basic.columns


def test_fetch_bj_stock_data_empty_response():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=pd.DataFrame()):
        df_info, df_basic = fetch_bj_stock_data("2024-01-02")
    assert df_info.empty
    assert df_basic.empty


def test_fetch_bj_stock_data_ak_raises():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", side_effect=Exception("网络错误")):
        df_info, df_basic = fetch_bj_stock_data("2024-01-02")
    assert df_info.empty
    assert df_basic.empty


# ── fetch_stock_info 路由逻辑 ─────────────────────────────────────────────────

def test_fetch_stock_info_bj_triggers_bj_fetch():
    with patch("datasource.akstock.fetch_bj_stock_data",
               return_value=(pd.DataFrame({"code": ["1.BJ"]}), pd.DataFrame())) as mock_bj:
        fetch_stock_info(["BJ"])
    mock_bj.assert_called_once()


def test_fetch_stock_info_all_triggers_bj_fetch():
    with patch("datasource.akstock.fetch_bj_stock_data",
               return_value=(pd.DataFrame(), pd.DataFrame())) as mock_bj:
        fetch_stock_info(["all"])
    mock_bj.assert_called_once()


def test_fetch_stock_info_sh_only_returns_empty():
    # SH is not supported; the function returns empty DataFrames immediately
    # without calling any AkShare API.
    df_info, df_basic = fetch_stock_info(["SH"])
    assert df_info.empty


# ── fetch_stock_data 列映射 ───────────────────────────────────────────────────

def _ak_hist_df():
    return pd.DataFrame({
        "日期": ["2023-01-03"],
        "开盘": [10.0], "最高": [11.0], "最低": [9.5], "收盘": [10.5],
        "成交量": [100], "成交额": [105000.0], "换手率": [1.5],
    })


def test_fetch_stock_data_daily_columns():
    with patch("datasource.akstock.ak.stock_zh_a_hist", return_value=_ak_hist_df()):
        with patch("datasource.akstock._get_request_timeout", return_value=10):
            df_daily, _ = fetch_stock_data("20230103", "20230103", "000001", "SZ")
    assert "code" in df_daily.columns
    assert "volume" in df_daily.columns


def test_fetch_stock_data_volume_multiplied():
    with patch("datasource.akstock.ak.stock_zh_a_hist", return_value=_ak_hist_df()):
        with patch("datasource.akstock._get_request_timeout", return_value=10):
            df_daily, _ = fetch_stock_data("20230103", "20230103", "000001", "SZ")
    assert df_daily.iloc[0]["volume"] == 100 * 100


def test_fetch_stock_data_basic_has_turnover_rate():
    with patch("datasource.akstock.ak.stock_zh_a_hist", return_value=_ak_hist_df()):
        with patch("datasource.akstock._get_request_timeout", return_value=10):
            _, df_basic = fetch_stock_data("20230103", "20230103", "000001", "SZ")
    assert "turnover_rate" in df_basic.columns


# ── _fetch_summary_sse 列重命名 + exchange_code ────────────────────────────────

def _sse_raw():
    # Column names match what ak.stock_margin_sse returns and what
    # _fetch_summary_sse() expects to rename.
    return pd.DataFrame({
        "信用交易日期": ["20230103"],
        "融资余额": [1e10], "融资买入额": [1e9],
        "融券余量": [1e6], "融券余量金额": [1e8],
        "融券卖出量": [5e5], "融资融券余额": [2e10],
    })


def test_fetch_summary_sse_exchange_code():
    with patch("datasource.akstock.ak.stock_margin_sse", return_value=_sse_raw()):
        result = _fetch_summary_sse("20230103", "20230103")
    assert (result["exchange_code"] == "SH").all()


def test_fetch_summary_sse_output_columns():
    """黑盒：输出列固定为 _SUMMARY_OUT_COLS。"""
    with patch("datasource.akstock.ak.stock_margin_sse", return_value=_sse_raw()):
        result = _fetch_summary_sse("20230103", "20230103")
    assert list(result.columns) == _SUMMARY_OUT_COLS


def test_fetch_summary_sse_ak_raises_returns_empty():
    with patch("datasource.akstock.ak.stock_margin_sse", side_effect=Exception("超时")):
        result = _fetch_summary_sse("20230103", "20230103")
    assert result.empty
    assert list(result.columns) == _SUMMARY_OUT_COLS


# ── _fetch_summary_szse 逐日 + exchange_code ─────────────────────────────────

def _szse_raw():
    # Column names match what ak.stock_margin_szse returns and what
    # _fetch_summary_szse() expects to rename.
    # The '项目' column must contain '融资融券' for the row to be kept.
    return pd.DataFrame({
        "数据日期": ["20230103"],
        "项目": ["融资融券"],
        "融资买入额": [1e9], "融资余额": [1e10],
        "融券卖出量": [5e5], "融券余量金额": [1e8],
        "融券余量": [1e6], "融资融券余额": [2e10],
    })


def test_fetch_summary_szse_exchange_code():
    with patch("datasource.akstock.ak.stock_margin_szse", return_value=_szse_raw()):
        result = _fetch_summary_szse(["20230103"])
    assert (result["exchange_code"] == "SZ").all()


def test_fetch_summary_szse_skip_failed_day():
    """某日接口失败时跳过，继续处理其余日期。"""
    call_count = 0

    def side_effect(date):
        nonlocal call_count
        call_count += 1
        if date == "20230103":
            raise Exception("接口错误")
        return _szse_raw()

    with patch("datasource.akstock.ak.stock_margin_szse", side_effect=side_effect):
        result = _fetch_summary_szse(["20230103", "20230104"])
    assert call_count == 2
    assert len(result) == 1


# ── fetch_margin_detail symbol 过滤正则 ──────────────────────────────────────

def _sz_detail_raw():
    # SZ regex is ^[03]\d{5}$  → keeps 000001 and 300001; filters 600519 and abc
    return pd.DataFrame({
        "证券代码": ["000001", "600519", "300001", "abc"],
        "融资买入额": [1e8] * 4, "融资余额": [1e9] * 4,
        "融券卖出量": [0] * 4, "融券余量": [0] * 4,
        "融券余额": [0] * 4, "融资融券余额": [1e9] * 4,
    })


def _sh_detail_raw():
    # SH regex is ^6\d{5}$  → keeps 600519 and 688001; filters 000001 (starts with 0)
    # Note: 688001 is STAR Market (科创板) which IS SH exchange, regex ^6\d{5}$ matches it.
    return pd.DataFrame({
        "标的证券代码": ["600519", "000001", "1234567"],
        "融资余额": [1e9] * 3, "融资买入额": [1e8] * 3,
        "融资偿还额": [5e7] * 3, "融券余量": [0] * 3,
        "融券卖出量": [0] * 3, "融券偿还量": [0] * 3,
    })


def test_fetch_margin_detail_sz_filters_non_sz_symbols():
    with patch("datasource.akstock.ak.stock_margin_detail_szse", return_value=_sz_detail_raw()):
        with patch("datasource.akstock.ak.stock_margin_detail_sse", return_value=pd.DataFrame()):
            result = fetch_margin_detail("20230103", ["sz"])
    sz_codes = result["code"].tolist()
    assert all(c.endswith(".SZ") for c in sz_codes)
    assert "600519.SZ" not in sz_codes   # 6开头不是SZ股


def test_fetch_margin_detail_sh_filters_non_sh_symbols():
    with patch("datasource.akstock.ak.stock_margin_detail_sse", return_value=_sh_detail_raw()):
        with patch("datasource.akstock.ak.stock_margin_detail_szse", return_value=pd.DataFrame()):
            result = fetch_margin_detail("20230103", ["sh"])
    sh_codes = result["code"].tolist()
    assert all(c.endswith(".SH") for c in sh_codes)
    assert "000001.SH" not in sh_codes   # 0开头不是SH股
    assert "1234567.SH" not in sh_codes  # 7位代码不符合 ^6\d{5}$


def test_fetch_margin_detail_output_columns():
    """黑盒：输出列固定为 _DETAIL_OUT_COLS。"""
    with patch("datasource.akstock.ak.stock_margin_detail_szse", return_value=_sz_detail_raw()):
        with patch("datasource.akstock.ak.stock_margin_detail_sse", return_value=pd.DataFrame()):
            result = fetch_margin_detail("20230103", ["sz"])
    assert list(result.columns) == _DETAIL_OUT_COLS


def test_fetch_margin_detail_both_fail_returns_empty():
    with patch("datasource.akstock.ak.stock_margin_detail_szse", side_effect=Exception("err")):
        with patch("datasource.akstock.ak.stock_margin_detail_sse", side_effect=Exception("err")):
            result = fetch_margin_detail("20230103", ["all"])
    assert result.empty
    assert list(result.columns) == _DETAIL_OUT_COLS


# ── fetch_stock_industry_clf_hist_sw 数据清洗 ─────────────────────────────────

def _sw_raw():
    # Column names must match _INDUSTRY_OUT_COLS exactly:
    # ['symbol', 'start_date', 'industry_code', 'update_time']
    return pd.DataFrame({
        "symbol": ["1", "60001", None, "000002"],
        "start_date": ["2021-01-01", "2020-06-01", "2021-01-01", "2021-01-01"],
        "industry_code": ["110101", "220101", "330101", "110101"],
        "update_time": ["2023-01-01 00:00:00"] * 4,
    })


def test_fetch_industry_symbol_zfill():
    """黑盒：symbol 补零至 6 位。"""
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=_sw_raw()):
        result = fetch_stock_industry_clf_hist_sw()
    assert all(len(s) == 6 for s in result["symbol"])


def test_fetch_industry_none_symbol_dropped():
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=_sw_raw()):
        result = fetch_stock_industry_clf_hist_sw()
    assert None not in result["symbol"].tolist()


def test_fetch_industry_output_columns():
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=_sw_raw()):
        result = fetch_stock_industry_clf_hist_sw()
    assert list(result.columns) == _INDUSTRY_OUT_COLS


def test_fetch_industry_ak_raises_returns_empty():
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", side_effect=Exception("超时")):
        result = fetch_stock_industry_clf_hist_sw()
    assert result.empty
    assert list(result.columns) == _INDUSTRY_OUT_COLS


def test_fetch_industry_missing_column_returns_empty():
    bad_df = pd.DataFrame({"symbol": ["000001"], "start_date": ["2021-01-01"]})
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=bad_df):
        result = fetch_stock_industry_clf_hist_sw()
    assert result.empty
