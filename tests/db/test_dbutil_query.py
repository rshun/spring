import pytest
from unittest.mock import patch, MagicMock

from util.dbutil import check_is_trading_day, get_trade_dates
from tests.conftest import insert_trade_cal


def _wrap(mem_db):
    m = MagicMock(wraps=mem_db)
    m.close = MagicMock()
    return m


# ── check_is_trading_day ──────────────────────────────────────────────────────

def test_check_trading_day_is_open(mem_db):
    insert_trade_cal(mem_db, "2023-01-03", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert check_is_trading_day("2023-01-03") is True


def test_check_trading_day_not_open(mem_db):
    insert_trade_cal(mem_db, "2023-01-01", 0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert check_is_trading_day("2023-01-01") is False


def test_check_trading_day_not_in_table(mem_db):
    """日期不在表中 → 返回 False，打 warning，不抛异常。"""
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert check_is_trading_day("2099-01-01") is False


# ── get_trade_dates ───────────────────────────────────────────────────────────

def test_get_trade_dates_returns_sorted_list(mem_db):
    for d, o in [("2023-01-03", 1), ("2023-01-04", 1), ("2023-01-05", 1),
                 ("2023-01-06", 0)]:
        insert_trade_cal(mem_db, d, o)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-03", "2023-01-06")
    assert result == ["20230103", "20230104", "20230105"]


def test_get_trade_dates_format_is_yyyymmdd(mem_db):
    """黑盒：返回格式严格为 YYYYMMDD（8位，不含连字符）。"""
    insert_trade_cal(mem_db, "2023-01-03", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-03", "2023-01-03")
    assert len(result) == 1
    assert len(result[0]) == 8
    assert "-" not in result[0]


def test_get_trade_dates_boundary_inclusive(mem_db):
    """黑盒：start_date 和 end_date 当天若是交易日也要包含。"""
    for d in ["2023-01-03", "2023-01-04", "2023-01-05"]:
        insert_trade_cal(mem_db, d, 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-03", "2023-01-05")
    assert "20230103" in result
    assert "20230105" in result


def test_get_trade_dates_no_trading_days_returns_empty(mem_db):
    insert_trade_cal(mem_db, "2023-01-07", 0)  # 周六，非交易日
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-07", "2023-01-07")
    assert result == []
