"""6 位裸码 -> 标准代码。北交所返回 None(本次范围只沪深), 非法输入抛错。"""
import pytest

from util.myutil import symbol_to_std_code


@pytest.mark.parametrize("symbol, expected", [
    ("600519", "600519.SH"),
    ("601398", "601398.SH"),
    ("500001", "500001.SH"),
    ("000001", "000001.SZ"),
    ("300750", "300750.SZ"),
    ("159915", "159915.SZ"),
])
def test_sh_sz_symbols_get_correct_suffix(symbol, expected):
    """正例: 沪深前缀规则"""
    assert symbol_to_std_code(symbol) == expected


@pytest.mark.parametrize("symbol", ["430047", "830799", "871981"])
def test_bj_symbols_return_none(symbol):
    """反例: 北交所不在本次范围, 返回 None 由调用方计数丢弃"""
    assert symbol_to_std_code(symbol) is None


@pytest.mark.parametrize("symbol", ["", "60051", "6005199", "60A519", None, "  "])
def test_invalid_symbols_raise(symbol):
    """反例: 非法输入必须抛错, 不得静默产出脏代码"""
    with pytest.raises(ValueError):
        symbol_to_std_code(symbol)


def test_symbol_is_stripped_before_parsing():
    """正例: 接口返回值可能带空白, 需先 strip"""
    assert symbol_to_std_code(" 600519 ") == "600519.SH"
