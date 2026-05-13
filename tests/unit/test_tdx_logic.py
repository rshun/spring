import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from datasource.tdx import fetch_stock_data, _to_market

EXPECTED_COLS = ["code", "date", "open", "high", "low", "close",
                 "pre_close", "tradestatus", "volume", "amount"]


def make_raw_row(date_str: str, open_: float, high: float, low: float,
                 close: float, vol: int, amount: float) -> dict:
    return {
        "datetime": f"{date_str} 00:00",
        "open": open_, "high": high, "low": low, "close": close,
        "vol": vol, "amount": amount,
    }


def make_api(rows: list):
    """构造只返回一页数据的 mock API。

    实际代码流程:
      raw = api.get_security_bars(...)
      if not raw: break
      df_page = api.to_df(raw)
      if df_page.empty: break
    """
    api = MagicMock()
    if rows:
        df = pd.DataFrame(rows)
        # 第一次调用返回 rows（truthy），后续调用（若有）返回 []
        api.get_security_bars.side_effect = [rows, []]
        # to_df 将列表转为 DataFrame
        api.to_df.side_effect = lambda x: pd.DataFrame(x) if x else pd.DataFrame()
    else:
        api.get_security_bars.return_value = []
        api.to_df.return_value = pd.DataFrame()
    return api


# ── _to_market ────────────────────────────────────────────────────────────────

def test_to_market_sh_upper():
    assert _to_market("SH") == 1


def test_to_market_sh_lower():
    assert _to_market("sh") == 1


def test_to_market_sz():
    assert _to_market("SZ") == 0


def test_to_market_bj():
    assert _to_market("BJ") == 2


# ── fetch_stock_data 黑盒：输出列固定 ────────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_output_columns_fixed(mock_pages):
    rows = [make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0)]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert list(result.columns) == EXPECTED_COLS


# ── fetch_stock_data 正常：行数 = 区间内交易日数 ──────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_row_count_equals_trade_dates(mock_pages):
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),
        make_raw_row("2023-01-04", 10.5, 11.5, 10.0, 11.0, 120, 132000.0),
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    assert len(result) == 2


# ── fetch_stock_data 正常：volume 手→股 ──────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_volume_converted_from_lots_to_shares(mock_pages):
    rows = [make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 50, 52500.0)]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert result.iloc[0]["volume"] == 50 * 100


# ── fetch_stock_data 黑盒：停牌占位行四价=前收，量=0 ─────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_suspension_placeholder_uses_prev_close(mock_pages):
    """pytdx 未返回 20230104 → 产生停牌占位行，四价=前收，vol=0。"""
    rows = [make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0)]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    suspension = result[result["date"] == "2023-01-04"].iloc[0]
    assert suspension["tradestatus"] == 0
    assert suspension["volume"] == 0
    assert suspension["open"] == suspension["close"] == 10.5   # 前收
    assert suspension["pre_close"] == 10.5


# ── fetch_stock_data 黑盒：停牌后恢复，pre_close 正确接续 ────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_pre_close_after_suspension(mock_pages):
    """停牌日之后有正常行情，pre_close 应等于停牌前的最后收盘价。"""
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),
        make_raw_row("2023-01-05", 10.5, 12.0, 10.0, 11.5, 80,  92000.0),
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-05",
                              ["20230103", "20230104", "20230105"])
    row_0105 = result[result["date"] == "2023-01-05"].iloc[0]
    assert row_0105["pre_close"] == 10.5  # 停牌前的收盘价（01-03）


# ── fetch_stock_data 异常：trade_dates 为空 ──────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_empty_trade_dates_returns_empty_df(mock_pages):
    api = MagicMock()
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04", [])
    assert result.empty


# ── fetch_stock_data 异常：api 返回 None ─────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_api_returns_none_all_suspended(mock_pages):
    """api 返回 None 时，prev_close 不存在，所有交易日均跳过。"""
    api = MagicMock()
    api.get_security_bars.return_value = None
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert result.empty


# ── fetch_stock_data 异常：api 返回空列表 ────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_api_returns_empty_list(mock_pages):
    api = MagicMock()
    api.get_security_bars.return_value = []
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert result.empty


# ── fetch_stock_data 停牌识别 ────────────────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_tradestatus_zero_when_vol_zero_and_prices_equal(mock_pages):
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),  # 有前收
        make_raw_row("2023-01-04", 10.5, 10.5, 10.5, 10.5, 0, 0.0),       # 四价相等 vol=0
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    row_0104 = result[result["date"] == "2023-01-04"].iloc[0]
    assert row_0104["tradestatus"] == 0


@patch("datasource.tdx._get_max_pages", return_value=1)
def test_tradestatus_one_when_vol_zero_prices_differ(mock_pages):
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),
        make_raw_row("2023-01-04", 10.0, 10.5, 9.8, 10.2, 0, 0.0),       # vol=0 但四价不同
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    row_0104 = result[result["date"] == "2023-01-04"].iloc[0]
    assert row_0104["tradestatus"] == 1
