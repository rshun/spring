"""涨停/跌停池取数: 两池列对齐, 独有字段补 None。不触网, 全部 mock。"""
import pandas as pd
import pytest

from datasource import akstock

LIMIT_POOL_COLUMNS = [
    "symbol", "name", "pct_change", "close", "amount", "float_mv", "total_mv",
    "turnover_rate", "seal_amount", "last_seal_time", "industry",
    "first_seal_time", "broken_times", "limit_stat", "boards",
    "pe_dynamic", "board_amount", "down_days", "open_times",
]


def _raw_zt():
    return pd.DataFrame({
        "序号": [1, 2],
        "代码": ["600001", "300002"],
        "名称": ["测试A", "测试B"],
        "涨跌幅": [10.0, 20.0],
        "最新价": [11.00, 24.00],
        "成交额": [1.0e8, 2.0e8],
        "流通市值": [5.0e9, 6.0e9],
        "总市值": [8.0e9, 9.0e9],
        "换手率": [3.5, 4.5],
        "封板资金": [1.2e8, 3.4e8],
        "首次封板时间": ["09:31:00", "10:05:00"],
        "最后封板时间": ["09:35:00", "14:30:00"],
        "炸板次数": [0, 2],
        "涨停统计": ["1/1", "3/5"],
        "连板数": [1, 3],
        "所属行业": ["电子", "医药"],
    })


def _raw_dt():
    return pd.DataFrame({
        "序号": [1],
        "代码": ["000003"],
        "名称": ["测试C"],
        "涨跌幅": [-10.0],
        "最新价": [9.00],
        "成交额": [3.0e7],
        "流通市值": [1.0e9],
        "总市值": [1.5e9],
        "动态市盈率": [15.5],
        "换手率": [1.2],
        "封单资金": [5.0e6],
        "最后封板时间": ["13:20:00"],
        "板上成交额": [2.0e6],
        "连续跌停": [2],
        "开板次数": [1],
        "所属行业": ["地产"],
    })


def test_zt_pool_columns_aligned_and_down_fields_none(monkeypatch):
    """正例: 涨停池列对齐到统一列集, 跌停独有字段为 None"""
    monkeypatch.setattr(akstock.ak, "stock_zt_pool_em", lambda date: _raw_zt())
    df = akstock.fetch_limit_pool("20260911")
    assert list(df.columns) == LIMIT_POOL_COLUMNS
    assert df["seal_amount"].tolist() == [1.2e8, 3.4e8]
    assert df["boards"].tolist() == [1, 3]
    assert df["pe_dynamic"].isna().all()
    assert df["down_days"].isna().all()


def test_dt_pool_columns_aligned_and_up_fields_none(monkeypatch):
    """正例: 跌停池「封单资金」并入 seal_amount, 涨停独有字段为 None"""
    monkeypatch.setattr(akstock.ak, "stock_zt_pool_dtgc_em", lambda date: _raw_dt())
    df = akstock.fetch_limit_down_pool("20260911")
    assert list(df.columns) == LIMIT_POOL_COLUMNS
    assert df["seal_amount"].tolist() == [5.0e6]
    assert df["pe_dynamic"].tolist() == [15.5]
    assert df["first_seal_time"].isna().all()
    assert df["boards"].isna().all()


def test_empty_zt_pool_returns_empty_frame_with_columns(monkeypatch):
    """反例: 空帧 -> 列齐全的空帧, 不抛错"""
    monkeypatch.setattr(akstock.ak, "stock_zt_pool_em", lambda date: pd.DataFrame())
    df = akstock.fetch_limit_pool("20260911")
    assert df.empty
    assert list(df.columns) == LIMIT_POOL_COLUMNS


def test_empty_dt_pool_returns_empty_frame_with_columns(monkeypatch):
    """反例: 跌停池为空在牛市是可能的, 同样返回列齐全空帧"""
    monkeypatch.setattr(akstock.ak, "stock_zt_pool_dtgc_em", lambda date: pd.DataFrame())
    df = akstock.fetch_limit_down_pool("20260911")
    assert df.empty
    assert list(df.columns) == LIMIT_POOL_COLUMNS


def test_zt_missing_column_raises_clear_error(monkeypatch):
    """反例: 上游少列 -> 点名缺哪列"""
    monkeypatch.setattr(akstock.ak, "stock_zt_pool_em",
                        lambda date: _raw_zt().drop(columns=["连板数"]))
    with pytest.raises(ValueError, match="连板数"):
        akstock.fetch_limit_pool("20260911")


def test_dt_missing_column_raises_clear_error(monkeypatch):
    """反例: 跌停池上游少列 -> 点名缺哪列"""
    monkeypatch.setattr(akstock.ak, "stock_zt_pool_dtgc_em",
                        lambda date: _raw_dt().drop(columns=["开板次数"]))
    with pytest.raises(ValueError, match="开板次数"):
        akstock.fetch_limit_down_pool("20260911")
