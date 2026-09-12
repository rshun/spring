"""sync_suspension 的参数面与纯函数逻辑(不触网、不连库)。"""
import pandas as pd

from etl import sync_suspension


def _actions(module):
    return {a.dest: a for a in module.build_parser()._actions if a.dest != "help"}


def test_build_parser_has_common_flags():
    """正例: MCP 通用调用面 -b/-e/-c/-x 齐全"""
    actions = _actions(sync_suspension)
    for dest, short in {"begin": "-b", "end": "-e",
                        "codes": "-c", "exchanges": "-x"}.items():
        assert dest in actions
        assert short in actions[dest].option_strings


def test_exchanges_choices_match_contract():
    """正例: 交易所枚举与其他 ETL 一致"""
    action = _actions(sync_suspension)["exchanges"]
    assert list(action.choices) == ["sh", "sz", "bj", "all"]
    assert action.default == ["all"]


def test_source_defaults_to_akstock():
    """正例: -s 默认 akstock"""
    action = _actions(sync_suspension)["source"]
    assert action.default == "akstock"
    assert "akstock" in action.choices


def test_build_parser_has_no_side_effect():
    """反例: build_parser 不得触发解析, 否则自省会读到 pytest 的 argv"""
    sync_suspension.build_parser()


def test_requested_exchanges_bj_only_is_empty():
    """反例: 只要北交所 -> 沪深集合为空, 调用方据此空跑并告警"""
    assert sync_suspension.requested_exchanges(["bj"]) == set()


def test_requested_exchanges_all_covers_sh_sz():
    """正例: all 覆盖沪深(不含北交所, 本源不支持)"""
    assert sync_suspension.requested_exchanges(["all"]) == {"SH", "SZ"}


def test_filter_by_codes_keeps_only_requested():
    """正例: -c 在本地按裸码过滤(接口不支持按代码查)"""
    df = pd.DataFrame({"symbol": ["600001", "000002", "300003"],
                       "name": ["A", "B", "C"]})
    out = sync_suspension.filter_by_codes(df, ["600001", "300003"])
    assert out["symbol"].tolist() == ["600001", "300003"]


def test_filter_by_codes_none_returns_all():
    """正例: 不传 -c 返回全量"""
    df = pd.DataFrame({"symbol": ["600001", "000002"], "name": ["A", "B"]})
    assert len(sync_suspension.filter_by_codes(df, [])) == 2


def test_filter_by_codes_no_match_returns_empty():
    """反例: 指定代码全不在帧内 -> 空帧而非全量"""
    df = pd.DataFrame({"symbol": ["600001", "000002"], "name": ["A", "B"]})
    assert sync_suspension.filter_by_codes(df, ["999999"]).empty
