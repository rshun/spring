"""fetch_suspension: 列名标准化与异常路径。不触网, 全部 mock。"""
import pandas as pd
import pytest

from datasource import akstock

SUSPENSION_COLUMNS = [
    "symbol", "name", "suspend_time", "resume_deadline",
    "suspend_period", "suspend_reason", "market", "expect_resume",
]


def _raw_frame():
    """接口原始帧。注意: 列字典全部用等长数组, 不混标量。"""
    return pd.DataFrame({
        "序号": [1, 2],
        "代码": ["600001", "000002"],
        "名称": ["测试A", "测试B"],
        "停牌时间": ["2024-04-26 10:30:00", "2024-04-26 09:30:00"],
        "停牌截止时间": ["2024-04-26 15:00:00", "2024-04-30 15:00:00"],
        "停牌期限": ["盘中停牌", "连续停牌"],
        "停牌原因": ["异常波动", "重大资产重组"],
        "所属市场": ["上交所", "深交所"],
        "预计复牌时间": ["2024-04-26 13:00:00", "2024-05-06 09:30:00"],
    })


def test_returns_standard_columns(monkeypatch):
    """正例: 中文列名被标准化, 顺序固定, 序号列被丢弃"""
    monkeypatch.setattr(akstock.ak, "stock_tfp_em", lambda date: _raw_frame())
    df = akstock.fetch_suspension("20240426")
    assert list(df.columns) == SUSPENSION_COLUMNS
    assert df["symbol"].tolist() == ["600001", "000002"]
    assert str(df["suspend_time"].dtype).startswith("datetime64")


def test_symbol_kept_as_six_digit_string(monkeypatch):
    """正例: 代码必须保持字符串, 不能被推断成 int 丢掉前导零"""
    raw = _raw_frame()
    raw["代码"] = ["000002", "000001"]
    monkeypatch.setattr(akstock.ak, "stock_tfp_em", lambda date: raw)
    df = akstock.fetch_suspension("20240426")
    assert df["symbol"].tolist() == ["000002", "000001"]


def test_empty_response_returns_empty_frame_with_columns(monkeypatch):
    """反例: 接口返回空帧 -> 返回列齐全的空帧, 不抛错(空是合法结果)"""
    monkeypatch.setattr(akstock.ak, "stock_tfp_em",
                        lambda date: pd.DataFrame())
    df = akstock.fetch_suspension("20240426")
    assert df.empty
    assert list(df.columns) == SUSPENSION_COLUMNS


def test_missing_upstream_column_raises_clear_error(monkeypatch):
    """反例: 上游少列 -> 抛出点名缺哪列的错误, 而不是裸 KeyError"""
    raw = _raw_frame().drop(columns=["停牌原因"])
    monkeypatch.setattr(akstock.ak, "stock_tfp_em", lambda date: raw)
    with pytest.raises(ValueError, match="停牌原因"):
        akstock.fetch_suspension("20240426")
