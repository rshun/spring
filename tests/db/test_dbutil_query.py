import pytest
from unittest.mock import patch, MagicMock

from util.dbutil import check_is_trading_day, get_trade_dates, get_last_trade_date
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


# ── get_last_trade_date ───────────────────────────────────────────────────────

def test_get_last_trade_date_returns_most_recent_before(mem_db):
    """正例：返回严格早于 before 的最近一个交易日，YYYYMMDD。"""
    for d, o in [("2023-01-03", 1), ("2023-01-04", 1), ("2023-01-05", 1)]:
        insert_trade_cal(mem_db, d, o)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert get_last_trade_date("20230105") == "20230104"


def test_get_last_trade_date_skips_weekend(mem_db):
    """正例：周一(20230109)取上一交易日应跨过周末回到周五(20230106)。"""
    for d, o in [("2023-01-06", 1),   # 周五 交易日
                 ("2023-01-07", 0),   # 周六
                 ("2023-01-08", 0)]:  # 周日
        insert_trade_cal(mem_db, d, o)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert get_last_trade_date("20230109") == "20230106"


def test_get_last_trade_date_strictly_before(mem_db):
    """正例：before 当天即使是交易日也不算，必须严格早于。"""
    for d in ["2023-01-03", "2023-01-04"]:
        insert_trade_cal(mem_db, d, 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert get_last_trade_date("20230104") == "20230103"


def test_get_last_trade_date_no_earlier_returns_none(mem_db):
    """反例：没有更早的交易日 → None。"""
    insert_trade_cal(mem_db, "2023-01-03", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert get_last_trade_date("20230103") is None


def test_get_last_trade_date_empty_calendar_returns_none(mem_db):
    """反例：日历表为空 → None，不抛异常。"""
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert get_last_trade_date("20230103") is None
