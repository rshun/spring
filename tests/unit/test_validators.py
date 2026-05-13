import pytest
from unittest.mock import patch

from util.validators import (
    ValidationError,
    run,
    v_yyyymmdd,
    v_date_order,
    v_single_day_must_be_trading_day,
)


# ── v_yyyymmdd ────────────────────────────────────────────────────────────────

def test_v_yyyymmdd_valid():
    assert v_yyyymmdd("d")({"d": "20230103"}) == []


def test_v_yyyymmdd_invalid_text():
    errors = v_yyyymmdd("d")({"d": "abc"})
    assert len(errors) == 1
    assert errors[0].field == "d"


def test_v_yyyymmdd_invalid_month_13():
    errors = v_yyyymmdd("d")({"d": "20231301"})
    assert len(errors) == 1


def test_v_yyyymmdd_empty_string():
    errors = v_yyyymmdd("d")({"d": ""})
    assert len(errors) == 1


def test_v_yyyymmdd_none_value():
    """None 值不应抛异常，应返回 ValidationError。"""
    errors = v_yyyymmdd("d")({"d": None})
    assert len(errors) == 1


def test_v_yyyymmdd_missing_key():
    errors = v_yyyymmdd("d")({"other": "20230101"})
    assert len(errors) == 1


# ── v_date_order ──────────────────────────────────────────────────────────────

def test_v_date_order_valid():
    assert v_date_order("b", "e")({"b": "20230101", "e": "20230131"}) == []


def test_v_date_order_same_day():
    assert v_date_order("b", "e")({"b": "20230101", "e": "20230101"}) == []


def test_v_date_order_reversed():
    errors = v_date_order("b", "e")({"b": "20230201", "e": "20230101"})
    assert len(errors) == 1


def test_v_date_order_reversed_field_contains_both_names():
    """黑盒：field 应包含 begin 和 end 两个字段名。"""
    errors = v_date_order("begin", "end")({"begin": "20230201", "end": "20230101"})
    assert "begin" in errors[0].field
    assert "end" in errors[0].field


def test_v_date_order_parse_fail_silent():
    """日期解析失败时静默跳过，不重复报错。"""
    assert v_date_order("b", "e")({"b": "invalid", "e": "20230101"}) == []


# ── v_single_day_must_be_trading_day ─────────────────────────────────────────

def test_v_single_day_unequal_skips():
    """begin != end 时直接跳过，不查数据库。"""
    v = v_single_day_must_be_trading_day("b", "e")
    assert v({"b": "20230103", "e": "20230131"}) == []


def test_v_single_day_equal_trading_day():
    with patch("util.validators.dbutil.check_is_trading_day", return_value=True):
        v = v_single_day_must_be_trading_day("b", "e")
        assert v({"b": "20230103", "e": "20230103"}) == []


def test_v_single_day_equal_non_trading_day():
    with patch("util.validators.dbutil.check_is_trading_day", return_value=False):
        v = v_single_day_must_be_trading_day("b", "e")
        errors = v({"b": "20230101", "e": "20230101"})
    assert len(errors) == 1
    assert "2023-01-01" in errors[0].message


def test_v_single_day_only_one_field_raises():
    """构造时传一个字段应立即抛 ValueError。"""
    with pytest.raises(ValueError):
        v_single_day_must_be_trading_day("begin", None)


def test_v_single_day_parse_fail_silent():
    """日期格式错误时静默跳过。"""
    v = v_single_day_must_be_trading_day("b", "e")
    assert v({"b": "invalid", "e": "invalid"}) == []


# ── run ───────────────────────────────────────────────────────────────────────

def test_run_all_pass_returns_true():
    assert run({"d": "20230101"}, [v_yyyymmdd("d")]) is True


def test_run_one_fail_returns_false():
    assert run({"d": "abc"}, [v_yyyymmdd("d")]) is False


def test_run_collects_all_errors():
    """黑盒：不是遇到第一个失败就停，所有 validator 都要执行。"""
    v1 = v_yyyymmdd("begin")
    v2 = v_yyyymmdd("end")
    result = run({"begin": "abc", "end": "xyz"}, [v1, v2])
    assert result is False


def test_run_empty_validators_returns_true():
    assert run({}, []) is True
