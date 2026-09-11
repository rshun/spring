import pytest
import pandas as pd

pytestmark = pytest.mark.integration


def _skip_on_bs_error(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        if result is None:
            pytest.skip("Baostock 返回 None，可能未登录成功")
        return result
    except Exception as e:
        pytest.skip(f"Baostock 接口不可达: {e}")


def test_bstock_fetch_calendar():
    from datasource.bstock import fetch_sync_calendar
    df = _skip_on_bs_error(fetch_sync_calendar, "2024-01-01", "2024-01-07")
    assert "cal_date" in df.columns
    assert "is_open" in df.columns
    assert not df.empty


def test_bstock_fetch_single_stock():
    from datasource.bstock import fetch_stock_data
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        pytest.skip(f"Baostock 登录失败: {lg.error_msg}")
    try:
        df_daily, df_basic = fetch_stock_data("2024-01-02", "2024-01-05", "sh.600519")
    finally:
        bs.logout()
    assert not df_daily.empty
    assert "code" in df_daily.columns
    assert "pre_close" in df_daily.columns


def test_bstock_fetch_adjust_factors():
    from datasource.bstock import fetch_adjust_factors
    stock_list = [("600519", "SH", "2024-01-02", "2024-01-05", "L")]
    result = _skip_on_bs_error(fetch_adjust_factors, stock_list)
    if result.empty:
        pytest.skip("无复权因子数据（可能无分红记录）")
    assert "fore_factor" in result.columns
    assert "back_factor" in result.columns


def test_bstock_login_fail_raises():
    """模拟登录失败：自 B024(2026-09-10) 起必须抛 BaoQueryError，让调用方退出 1，
    而不是返回空 DataFrame 被当成「无数据」。"""
    from datasource.bstock import fetch_adjust_factors, BaoQueryError
    from unittest.mock import patch, MagicMock

    mock_lg = MagicMock()
    mock_lg.error_code = "9999"
    mock_lg.error_msg = "模拟登录失败"
    with patch("datasource.bstock.bs.login", return_value=mock_lg):
        with patch("datasource.bstock.bs.logout"):
            with pytest.raises(BaoQueryError, match="登录失败"):
                fetch_adjust_factors([("600519", "SH", "2024-01-02", "2024-01-05", "L")])
