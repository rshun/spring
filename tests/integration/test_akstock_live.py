import pytest

pytestmark = pytest.mark.integration


def _skip_on_network_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        pytest.skip(f"AkShare 接口不可达: {e}")


def test_akstock_fetch_bj_info():
    from datasource.akstock import fetch_bj_stock_data
    df_info, df_basic = _skip_on_network_error(fetch_bj_stock_data, "2024-01-02")
    if df_info.empty:
        pytest.skip("北交所接口返回空数据（网络不可达或接口限流）")
    assert (df_info["exchange"] == "BJ").all()
    assert all(len(s) == 6 for s in df_info["symbol"])


def test_akstock_fetch_single_stock():
    from datasource.akstock import fetch_stock_data
    df_daily, df_basic = _skip_on_network_error(
        fetch_stock_data, "20240102", "20240105", "000001", "SZ"
    )
    assert not df_daily.empty
    assert "code" in df_daily.columns
    assert "volume" in df_daily.columns


def test_akstock_fetch_industry_hist():
    from datasource.akstock import fetch_stock_industry_clf_hist_sw, _INDUSTRY_OUT_COLS
    result = _skip_on_network_error(fetch_stock_industry_clf_hist_sw)
    assert list(result.columns) == _INDUSTRY_OUT_COLS
    assert all(len(s) == 6 for s in result["symbol"])


def test_akstock_fetch_margin_detail():
    from datasource.akstock import fetch_margin_detail, _DETAIL_OUT_COLS
    result = _skip_on_network_error(fetch_margin_detail, "20240102", ["sh"])
    assert list(result.columns) == _DETAIL_OUT_COLS
