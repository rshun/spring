import datetime

import pandas as pd
from unittest.mock import patch

from datasource.web import (
    read_classify_xls,
    _clean_industry_df,
    fetch_stock_industry_clf_hist_sw,
    _INDUSTRY_OUT_COLS,
)


def _raw_xls_df():
    """模拟 read_excel 读出的申万 xls 原始内容（中文列、字符串值）"""
    return pd.DataFrame({
        '股票代码': ['1', '000002', '000002'],
        '计入日期': ['1991-04-03 00:00:00', '1991-01-29 00:00:00', '1991-01-29 00:00:00'],
        '行业代码': ['440101', '430101', '430101'],
        '更新日期': ['2015-10-27 15:29:00', '2024-09-27 09:08:00', '2024-09-27 09:08:00'],
    })


# ── read_classify_xls ─────────────────────────────────────────────────────────

def test_read_classify_xls_renames_columns():
    with patch("datasource.web.pd.read_excel", return_value=_raw_xls_df()):
        df = read_classify_xls("dummy.xls")
    assert list(df.columns) == _INDUSTRY_OUT_COLS


def test_read_classify_xls_missing_column_raises():
    bad = _raw_xls_df().drop(columns=['行业代码'])
    with patch("datasource.web.pd.read_excel", return_value=bad):
        try:
            read_classify_xls("dummy.xls")
            assert False, "应抛出缺少字段异常"
        except ValueError as e:
            assert "行业代码" in str(e)


# ── _clean_industry_df ────────────────────────────────────────────────────────

def test_clean_zfills_symbol_to_6():
    df = _clean_industry_df(_rename(_raw_xls_df()))
    assert df['symbol'].iloc[0] == '000001'
    assert (df['symbol'].str.len() == 6).all()


def test_clean_parses_dates_and_dedups():
    df = _clean_industry_df(_rename(_raw_xls_df()))
    # 000002 的两行完全重复，去重后只剩一条
    assert len(df) == 2
    assert isinstance(df['start_date'].iloc[0], datetime.date)
    assert pd.api.types.is_datetime64_any_dtype(df['update_time'])


def test_clean_drops_invalid_start_date():
    raw = _rename(_raw_xls_df())
    raw.loc[0, 'start_date'] = 'not-a-date'
    df = _clean_industry_df(raw)
    assert (df['symbol'] != '000001').all()


# ── fetch_stock_industry_clf_hist_sw ──────────────────────────────────────────

def test_fetch_returns_empty_on_download_failure():
    with patch("datasource.web._download", side_effect=Exception("网络错误")):
        df = fetch_stock_industry_clf_hist_sw()
    assert df.empty
    assert list(df.columns) == _INDUSTRY_OUT_COLS


def test_fetch_happy_path():
    with patch("datasource.web._download", return_value=None), \
         patch("datasource.web.pd.read_excel", return_value=_raw_xls_df()):
        df = fetch_stock_industry_clf_hist_sw()
    assert list(df.columns) == _INDUSTRY_OUT_COLS
    assert df['symbol'].iloc[0] == '000001'
    assert len(df) == 2  # 去重后


def _rename(raw: pd.DataFrame) -> pd.DataFrame:
    from datasource.web import _CLASSIFY_COL_MAP
    return raw.rename(columns=_CLASSIFY_COL_MAP)[_INDUSTRY_OUT_COLS].copy()
