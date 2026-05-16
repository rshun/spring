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


# ── fetch_xdxr_data: 按 category 分别映射 (除权除息 vs 股本变化) ──────────

def _xdxr_raw():
    """两条记录: 除权除息(1) 与 股本变化(5),股本字段与分红字段填不同值,
    用于验证按 category 取对列。"""
    base = dict(year=2026, month=5, day=15,
                fenhong=None, peigujia=None, songzhuangu=None, peigu=None,
                panqianliutong=None, panhouliutong=None,
                qianzongguben=None, houzongguben=None)
    xd = {**base, "category": 1,
          "fenhong": 2.0, "peigujia": 3.0, "songzhuangu": 4.0, "peigu": 5.0,
          "panqianliutong": 999, "qianzongguben": 999,
          "panhouliutong": 999, "houzongguben": 999}
    gb = {**base, "category": 5,
          "fenhong": 888, "peigujia": 888, "songzhuangu": 888, "peigu": 888,
          "panqianliutong": 10.0, "qianzongguben": 20.0,
          "panhouliutong": 30.0, "houzongguben": 40.0}
    return [xd, gb]


@patch("datasource.tdx._get_max_fail", return_value=10)
@patch("datasource.tdx._get_category",
       return_value={"1": "除权除息", "5": "股本变化"})
@patch("datasource.tdx._connect_api")
def test_fetch_xdxr_maps_by_category(mock_conn, _mc, _mf):
    api = MagicMock()
    api.get_xdxr_info.return_value = _xdxr_raw()
    api.to_df.side_effect = lambda raw: pd.DataFrame(raw)
    mock_conn.return_value = api

    from datasource.tdx import fetch_xdxr_data
    df = fetch_xdxr_data([("600000", "SH")])

    assert df is not None
    assert list(df.columns) == ["code", "date", "category", "dividend",
                                "allotment_price", "bonus_share",
                                "allotment_share"]
    xd = df[df["category"] == "除权除息"].iloc[0]
    assert (xd["dividend"], xd["allotment_price"],
            xd["bonus_share"], xd["allotment_share"]) == (2.0, 3.0, 4.0, 5.0)

    gb = df[df["category"] == "股本变化"].iloc[0]
    # 股本变化必须取 前流通/前总股本/后流通/后总股本,不能是 888
    assert (gb["dividend"], gb["allotment_price"],
            gb["bonus_share"], gb["allotment_share"]) == (10.0, 20.0, 30.0, 40.0)


@patch("datasource.tdx._get_max_fail", return_value=10)
@patch("datasource.tdx._get_category",
       return_value={"1": "除权除息", "5": "股本变化"})
@patch("datasource.tdx._connect_api")
def test_fetch_xdxr_share_change_not_null(mock_conn, _mc, _mf):
    """回归: 股本变化记录的股本字段不再因列映射缺陷而全为 NULL。"""
    api = MagicMock()
    api.get_xdxr_info.return_value = [_xdxr_raw()[1]]  # 仅股本变化
    api.to_df.side_effect = lambda raw: pd.DataFrame(raw)
    mock_conn.return_value = api

    from datasource.tdx import fetch_xdxr_data
    df = fetch_xdxr_data([("600000", "SH")])
    row = df.iloc[0]
    assert row["allotment_share"] == 40.0  # 后总股本
    assert row[["dividend", "allotment_price",
                "bonus_share", "allotment_share"]].notna().all()
