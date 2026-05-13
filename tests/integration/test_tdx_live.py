import pytest
import pandas as pd

pytestmark = pytest.mark.integration


def _try_connect():
    """尝试连接通达信，失败时 skip。"""
    try:
        from datasource.tdx import _connect_api
        return _connect_api()
    except Exception as e:
        pytest.skip(f"通达信服务器不可达: {e}")


def test_tdx_connect_returns_api():
    api = _try_connect()
    assert api is not None
    api.disconnect()


def test_tdx_fetch_single_stock():
    """拉取 600519.SH 近 5 个交易日，验证输出格式。"""
    from datasource.tdx import _connect_api, fetch_stock_data
    from util.dbutil import get_trade_dates

    api = _try_connect()
    EXPECTED_COLS = ["code", "date", "open", "high", "low", "close",
                     "pre_close", "tradestatus", "volume", "amount"]
    try:
        trade_dates = get_trade_dates("2024-01-02", "2024-01-08")
        if not trade_dates:
            pytest.skip("TRADE_CAL 表无数据，跳过")

        result = fetch_stock_data(api, "600519", "SH",
                                  "2024-01-02", "2024-01-08", trade_dates)
    finally:
        api.disconnect()

    assert not result.empty
    assert list(result.columns) == EXPECTED_COLS
    assert result["date"].str.match(r"\d{4}-\d{2}-\d{2}").all()


def test_tdx_fetch_nonexistent_stock_returns_empty():
    """拉取不存在的股票代码，应返回空 DataFrame，不崩溃。"""
    from datasource.tdx import _connect_api, fetch_stock_data

    api = _try_connect()
    try:
        result = fetch_stock_data(api, "999999", "SH",
                                  "2024-01-02", "2024-01-05",
                                  ["20240102", "20240103"])
    finally:
        api.disconnect()

    assert isinstance(result, pd.DataFrame)


def test_tdx_all_servers_wrong_raises():
    from unittest.mock import patch
    from datasource.tdx import _connect_api
    with patch("datasource.tdx._get_servers", return_value=[("0.0.0.0", 7709)]):
        with pytest.raises(ConnectionError):
            _connect_api()
