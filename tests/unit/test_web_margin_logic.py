from pathlib import Path
from unittest.mock import patch, MagicMock
import datetime

import pytest
import pandas as pd

from datasource import web


TD = datetime.date(2026, 6, 18)


def _sse_summary_raw():
    # 第 1 行为数据；末行为官网说明文字；中间为空行
    return pd.DataFrame({
        '本日融资余额(元)':     ['1495185799372', None, '注：本表格同时包含融资融券汇总信息及明细信息'],
        '本日融资买入额(元)':   ['179607982680', None, None],
        '本日融券余量':         ['2444063656', None, None],
        '本日融券余量金额(元)': ['14089378242', None, None],
        '本日融券卖出量':       ['41813060', None, None],
        '本日融资融券余额(元)': ['1509275177614', None, None],
    })


def test_clean_sse_summary_drops_note_and_maps():
    out = web._clean_sse_summary(_sse_summary_raw(), TD)
    assert list(out.columns) == web._SUMMARY_OUT_COLS
    assert len(out) == 1
    row = out.iloc[0]
    assert row['exchange_code'] == 'SH'
    assert row['trade_date'] == TD
    assert row['margin_balance'] == 1495185799372.0
    assert row['short_balance_amount'] == 14089378242.0
    assert row['margin_repay_amount'] is None or pd.isna(row['margin_repay_amount'])
    assert row['short_repay_volume'] is None or pd.isna(row['short_repay_volume'])


def test_clean_sse_detail_filters_etf_and_sets_code():
    raw = pd.DataFrame({
        '标的证券代码':     ['510050', '600000', '688981'],   # 510050=ETF 应被过滤
        '标的证券简称':     ['50ETF', '浦发银行', '中芯国际'],
        '本日融资余额(元)': ['1', '2', '3'],
        '本日融资买入额(元)': ['4', '5', '6'],
        '本日融资偿还额(元)': ['7', '8', '9'],
        '本日融券余量':     ['0', '0', '0'],
        '本日融券卖出量':   ['0', '0', '0'],
        '本日融券偿还量':   ['0', '0', '0'],
    })
    out = web._clean_sse_detail(raw, TD)
    assert list(out.columns) == web._DETAIL_OUT_COLS
    assert set(out['symbol']) == {'600000', '688981'}   # ETF 过滤
    assert out.loc[out['symbol'] == '600000', 'code'].iloc[0] == '600000.SH'
    assert out['exchange_code'].unique().tolist() == ['SH']
    assert out['short_balance_amount'].isna().all()      # 沪市明细不披露
    assert out['margin_short_balance'].isna().all()


def test_clean_sse_summary_missing_column_raises():
    bad = _sse_summary_raw().drop(columns=['本日融资余额(元)'])
    with pytest.raises(ValueError):
        web._clean_sse_summary(bad, TD)


def test_http_download_writes_content(tmp_path):
    dest = tmp_path / "f.xls"
    resp = MagicMock()
    resp.content = b"hello"
    resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=resp) as mget:
        out = web._http_download("http://x/y", dest, timeout=5, tries=2, delay=0)
    assert out == dest
    assert dest.read_bytes() == b"hello"
    mget.assert_called_once()


def test_http_download_raises_after_retries(tmp_path):
    dest = tmp_path / "f.xls"
    with patch("requests.get", side_effect=RuntimeError("boom")) as mget, \
         patch("time.sleep"):
        with pytest.raises(RuntimeError):
            web._http_download("http://x/y", dest, timeout=5, tries=3, delay=0)
    assert mget.call_count == 3
