import pandas as pd
import pytest

from etl import sync_margin


def test_requested_codes():
    assert sync_margin._requested_codes(['all']) == {'SH', 'SZ'}
    assert sync_margin._requested_codes(['sh']) == {'SH'}
    assert sync_margin._requested_codes(['sz']) == {'SZ'}


def test_fill_missing_calls_fallback_for_missing_exchange():
    ak_df = pd.DataFrame({'exchange_code': ['SH'], 'margin_balance': [1.0]})
    calls = {}

    def fallback(ex):
        calls['ex'] = ex
        return pd.DataFrame({'exchange_code': ['SZ'], 'margin_balance': [2.0]})

    out = sync_margin._fill_missing_exchanges(ak_df, {'SH', 'SZ'}, fallback)
    assert calls['ex'] == ['sz']                      # 仅缺失的 SZ
    assert set(out['exchange_code']) == {'SH', 'SZ'}  # 合并


def test_fill_missing_skips_fallback_when_complete():
    ak_df = pd.DataFrame({'exchange_code': ['SH', 'SZ'], 'margin_balance': [1.0, 2.0]})

    def fallback(ex):
        raise AssertionError("不应调用回退")

    out = sync_margin._fill_missing_exchanges(ak_df, {'SH', 'SZ'}, fallback)
    assert len(out) == 2


def test_fill_missing_empty_akstock_falls_back_all():
    calls = {}

    def fallback(ex):
        calls['ex'] = sorted(ex)
        return pd.DataFrame({'exchange_code': ['SH', 'SZ'], 'margin_balance': [1.0, 2.0]})

    out = sync_margin._fill_missing_exchanges(None, {'SH', 'SZ'}, fallback)
    assert calls['ex'] == ['sh', 'sz']
    assert len(out) == 2
