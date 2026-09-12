"""sync_limit_pool 的参数面与纯函数逻辑(不触网、不连库)。"""
import pandas as pd

from etl import sync_limit_pool


def _actions(module):
    return {a.dest: a for a in module.build_parser()._actions if a.dest != "help"}


def test_build_parser_has_common_flags():
    """正例: MCP 通用调用面 -b/-e/-c/-x 齐全"""
    actions = _actions(sync_limit_pool)
    for dest, short in {"begin": "-b", "end": "-e",
                        "codes": "-c", "exchanges": "-x"}.items():
        assert dest in actions
        assert short in actions[dest].option_strings


def test_exchanges_choices_match_contract():
    """正例: 交易所枚举与其他 ETL 一致"""
    action = _actions(sync_limit_pool)["exchanges"]
    assert list(action.choices) == ["sh", "sz", "bj", "all"]
    assert action.default == ["all"]


def test_only_defaults_to_all():
    """正例: --only 默认两池都拉"""
    action = _actions(sync_limit_pool)["only"]
    assert action.default == "all"
    assert set(action.choices) == {"up", "down", "all"}


def test_build_parser_has_no_side_effect():
    """反例: build_parser 不得触发解析"""
    sync_limit_pool.build_parser()


def test_requested_exchanges_bj_only_is_empty():
    """反例: 只要北交所 -> 沪深集合为空"""
    assert sync_limit_pool.requested_exchanges(["bj"]) == set()


def test_filter_by_codes_keeps_only_requested():
    """正例: -c 本地过滤"""
    df = pd.DataFrame({"symbol": ["600001", "000002", "300003"],
                       "name": ["A", "B", "C"]})
    out = sync_limit_pool.filter_by_codes(df, ["000002"])
    assert out["symbol"].tolist() == ["000002"]


def test_filter_by_codes_no_match_returns_empty():
    """反例: 指定代码全不在帧内 -> 空帧而非全量"""
    df = pd.DataFrame({"symbol": ["600001"], "name": ["A"]})
    assert sync_limit_pool.filter_by_codes(df, ["999999"]).empty
