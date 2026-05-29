# 修改记录:
#   2026-05-29  Claude  新增 cw_fields 转换逻辑的正反测试
"""cw_fields.cw_df_to_finance_report 纯逻辑测试(无外部依赖)"""
import pandas as pd
import pytest

from datasource import cw_fields


def _make_raw_df():
    """构造一行 historyfinancialreader 风格的位置列 DataFrame(列为整数索引)"""
    # 0=code, 1=基本每股收益, 4=每股净资产, 40=资产总计, 95=净利润
    return pd.DataFrame([{0: "000001", 1: 1.23, 4: 12.5, 40: 1.0e11, 95: 2.0e9}])


# ── 正例 ────────────────────────────────────────────────

def test_convert_maps_positions_to_named_columns():
    """正例：位置列正确映射为具名字段，并补上 code/report_date"""
    out = cw_fields.cw_df_to_finance_report(_make_raw_df(), "20241231")
    assert out.loc[0, "code"] == "000001"
    assert out.loc[0, "report_date"] == "2024-12-31"
    assert out.loc[0, "eps"] == pytest.approx(1.23)
    assert out.loc[0, "bps"] == pytest.approx(12.5)
    assert out.loc[0, "total_assets"] == pytest.approx(1.0e11)
    assert out.loc[0, "net_profit"] == pytest.approx(2.0e9)


def test_normalize_report_date_accepts_dashed():
    """正例：已是 YYYY-MM-DD 的报告期原样保留"""
    out = cw_fields.cw_df_to_finance_report(_make_raw_df(), "2024-12-31")
    assert out.loc[0, "report_date"] == "2024-12-31"


# ── 反例 ────────────────────────────────────────────────

def test_empty_df_returns_empty():
    """反例：空 DataFrame 安全降级为空，不抛异常"""
    assert cw_fields.cw_df_to_finance_report(pd.DataFrame(), "20241231").empty
    assert cw_fields.cw_df_to_finance_report(None, "20241231").empty


def test_missing_columns_are_skipped():
    """反例：旧版 cw 文件字段缺失时，只产出存在的列，不报错"""
    df = pd.DataFrame([{0: "600000", 1: 0.5}])  # 仅 code + eps
    out = cw_fields.cw_df_to_finance_report(df, "20241231")
    assert out.loc[0, "eps"] == pytest.approx(0.5)
    assert "net_profit" not in out.columns
    assert "total_assets" not in out.columns


def test_missing_code_column_raises():
    """反例：缺少位置列 0(code) 时显式报错"""
    df = pd.DataFrame([{1: 1.0, 2: 2.0}])
    with pytest.raises(ValueError):
        cw_fields.cw_df_to_finance_report(df, "20241231")
