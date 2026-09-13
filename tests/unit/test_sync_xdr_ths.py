# 修改记录:
#   2026-09-13  Claude  新建 sync_xdr_ths 的参数面与纯函数逻辑测试
"""sync_xdr_ths 的参数面与纯函数逻辑(不触网、不连库)。"""
import datetime

import pandas as pd
import pytest

from etl import sync_xdr_ths


def _actions(module):
    return {a.dest: a for a in module.build_parser()._actions if a.dest != "help"}


def test_build_parser_has_common_flags():
    """正例: -b/-e/-c/-x 公共参数面齐全"""
    actions = _actions(sync_xdr_ths)
    for dest, short in {"begin": "-b", "end": "-e",
                        "codes": "-c", "exchanges": "-x"}.items():
        assert dest in actions
        assert short in actions[dest].option_strings


def test_exchanges_choices_match_convention():
    """正例: 交易所枚举与其他 ETL 一致"""
    action = _actions(sync_xdr_ths)["exchanges"]
    assert list(action.choices) == ["sh", "sz", "bj", "all"]
    assert action.default == ["all"]


def test_source_choices_and_default():
    """正例: -s 支持 parquet/api, 默认 parquet"""
    action = _actions(sync_xdr_ths)["source"]
    assert set(action.choices) == {"parquet", "api"}
    assert action.default == "parquet"


def test_build_parser_has_no_side_effect():
    """反例: build_parser 不得触发解析"""
    sync_xdr_ths.build_parser()


def test_resolve_parquet_picks_largest_date_suffix(tmp_path):
    """正例: 按文件名日期后缀取最新, 不按 mtime

    重新下载会刷新 mtime 但内容可能更旧, 文件名里的 YYYYMMDD 才是数据截止日。
    """
    older = tmp_path / "a_share_adjustment_factors_event_none_all_20260101.parquet"
    newer = tmp_path / "a_share_adjustment_factors_event_none_all_20260912.parquet"
    newer.write_bytes(b"x")
    older.write_bytes(b"x")          # 后写, mtime 更新, 但日期后缀更旧
    assert sync_xdr_ths.resolve_parquet_path(search_dir=tmp_path).name == newer.name


def test_resolve_parquet_explicit_wins(tmp_path):
    """正例: 显式 --parquet 优先于自动查找"""
    p = tmp_path / "custom.parquet"
    p.write_bytes(b"x")
    assert sync_xdr_ths.resolve_parquet_path(explicit=str(p), search_dir=tmp_path) == p


def test_resolve_parquet_no_match_raises(tmp_path):
    """反例: 找不到文件必须报错, 不静默跳过"""
    with pytest.raises(FileNotFoundError):
        sync_xdr_ths.resolve_parquet_path(search_dir=tmp_path)


def test_requested_exchanges_all():
    """正例: all 覆盖三个交易所(本源含北交所 1102 条)"""
    assert sync_xdr_ths.requested_exchanges(["all"]) == {"SH", "SZ", "BJ"}


def test_requested_exchanges_subset():
    """正例: 指定子集"""
    assert sync_xdr_ths.requested_exchanges(["sh"]) == {"SH"}


def test_filter_by_codes_keeps_only_requested():
    """正例: -c 按裸码本地过滤"""
    df = pd.DataFrame({"code": ["600519.SH", "000001.SZ"],
                       "ex_date": [datetime.date(2026, 9, 1)] * 2})
    out = sync_xdr_ths.filter_by_codes(df, ["600519"])
    assert out["code"].tolist() == ["600519.SH"]


def test_filter_by_codes_no_match_returns_empty():
    """反例: 指定代码全不在帧内 -> 空帧而非全量"""
    df = pd.DataFrame({"code": ["600519.SH"], "ex_date": [datetime.date(2026, 9, 1)]})
    assert sync_xdr_ths.filter_by_codes(df, ["999999"]).empty
