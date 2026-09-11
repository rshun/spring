# 修改记录:
#   2026-06-24  Claude  新增沪深主板 ST 涨跌停 2026-07-06 由 5%->10% 的正反测试
#   2026-09-11  Claude  新增新股无涨跌幅窗口「交易日 vs 自然日」口径的正反测试；
#                       新增写库失败必须重抛的反例
"""update_price_limits_by_range 涨跌停比率计算测试 (聚焦沪深主板 ST 规则切换)。"""
from unittest.mock import patch, MagicMock

import pytest

from util.dbutil import update_price_limits_by_range
from tests.conftest import insert_stock_info, insert_trade_cal


def _wrap(mem_db):
    m = MagicMock(wraps=mem_db)
    m.close = MagicMock()  # 避免关闭 fixture 持有的连接
    return m


def _seed_main_st(mem_db, trade_date: str, pre_close: float = 10.0):
    """种入一只沪深主板 ST 股: STOCK_INFO + STOCK_DAILY + DAILY_BASIC(待回填)。"""
    insert_stock_info(mem_db, "600000", "SH", "MAIN", list_date="2010-01-01")
    code = "600000.SH"
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1000, 1.0)",
        [code, trade_date, pre_close, pre_close, pre_close, pre_close, pre_close],
    )
    mem_db.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, is_st) VALUES (?, ?, 1)",
        [code, trade_date],
    )
    return code


def _limit_up(mem_db, code, trade_date):
    return mem_db.execute(
        "SELECT limit_up FROM DAILY_BASIC WHERE code = ? AND trade_date = ?",
        [code, trade_date],
    ).fetchone()[0]


# ── 正例: 2026-07-06 起沪深主板 ST 用 10% ───────────────────────────────────────

def test_main_st_uses_10pct_on_and_after_20260706(mem_db):
    """正例: 切换日当天主板 ST 涨停价 = pre_close * 1.10。"""
    code = _seed_main_st(mem_db, "2026-07-06", pre_close=10.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2026-07-06", "2026-07-06")
    assert _limit_up(mem_db, code, "2026-07-06") == 11.0  # 10.0 * 1.10


# ── 反例(边界): 切换日前一交易日仍用 5% ─────────────────────────────────────────

def test_main_st_still_uses_5pct_before_20260706(mem_db):
    """反例/边界: 2026-07-03 (切换前) 主板 ST 仍为 5%，涨停价 = pre_close * 1.05。"""
    code = _seed_main_st(mem_db, "2026-07-03", pre_close=10.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2026-07-03", "2026-07-03")
    assert _limit_up(mem_db, code, "2026-07-03") == 10.5  # 10.0 * 1.05


# ── 新股无涨跌幅限制窗口: 必须按交易日而非自然日计数 ─────────────────────────────
#
# 创业板/科创板注册制新股「上市后前 5 个交易日不设涨跌幅限制」。此前用
# date_diff('day', list_date, date)+1 按自然日计数，上市日落在周中时会提前越过 5，
# 把仍在无限制期内的新股按 20% 限价。下面用 300784.SZ 的真实日历复现:
#   2024-06-07(五) 上市 = 第 1 个交易日   自然日 1
#   2024-06-08/09 周末, 2024-06-10 端午休市
#   2024-06-11(二) = 第 2 个交易日        自然日 5
#   2024-06-12(三) = 第 3 个交易日        自然日 6  ← 旧算法在这里就误判出窗口
#   2024-06-13(四) = 第 4 个交易日        自然日 7
#   2024-06-14(五) = 第 5 个交易日        自然日 8
#   2024-06-17(一) = 第 6 个交易日        自然日 11 ← 才真正恢复 20% 限制

_GEM_LIST_DATE = "2024-06-07"
_GEM_TRADE_DAYS = ["2024-06-07", "2024-06-11", "2024-06-12",
                   "2024-06-13", "2024-06-14", "2024-06-17"]


def _seed_gem_new_stock(mem_db, trade_date: str, pre_close: float = 100.0):
    """种入一只 2024-06-07 上市的创业板新股，并按真实日历填 TRADE_CAL。"""
    insert_stock_info(mem_db, "300784", "SZ", "GEM", list_date=_GEM_LIST_DATE)
    for d in _GEM_TRADE_DAYS:
        insert_trade_cal(mem_db, d, 1)
    code = "300784.SZ"
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1000, 1.0)",
        [code, trade_date, pre_close, pre_close, pre_close, pre_close, pre_close],
    )
    mem_db.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, is_st) VALUES (?, ?, 0)",
        [code, trade_date],
    )
    return code


def test_gem_new_stock_third_trading_day_has_no_limit(mem_db):
    """正例(本次修复的回归点): 第 3 个交易日(自然日第 6 天)仍在无涨跌幅限制窗口内，
    limit_up 必须是哨兵值 999999.99 而不是 pre_close * 1.20。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-12", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-12", "2024-06-12")
    assert _limit_up(mem_db, code, "2024-06-12") == 999999.99


def test_gem_new_stock_first_trading_day_has_no_limit(mem_db):
    """正例: 上市首日(第 1 个交易日)无涨跌幅限制。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-07", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-07", "2024-06-07")
    assert _limit_up(mem_db, code, "2024-06-07") == 999999.99


def test_gem_new_stock_fifth_trading_day_still_has_no_limit(mem_db):
    """正例(边界): 第 5 个交易日(自然日第 8 天)是窗口最后一天，仍不设限。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-14", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-14", "2024-06-14")
    assert _limit_up(mem_db, code, "2024-06-14") == 999999.99


def test_gem_new_stock_sixth_trading_day_restores_20pct(mem_db):
    """反例(边界): 第 6 个交易日才真正退出窗口，恢复 20% 限制。
    修复不能矫枉过正地把无限制期一直延下去。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-17", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-17", "2024-06-17")
    assert _limit_up(mem_db, code, "2024-06-17") == 120.0


def test_old_stock_unaffected_by_trading_day_rewrite(mem_db):
    """反例: 早已上市的老股(上市日远早于日历覆盖范围)不受改写影响，仍按板块常规比率。
    早期交易日不在 TRADE_CAL 里时 days_count 取哨兵 99，不得被误判成新股。"""
    code = _seed_main_st(mem_db, "2026-07-06", pre_close=10.0)
    insert_trade_cal(mem_db, "2026-07-06", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2026-07-06", "2026-07-06")
    assert _limit_up(mem_db, code, "2026-07-06") == 11.0


# ── 写库失败必须重抛(契约 C1) ───────────────────────────────────────────────────

def test_update_failure_reraises(mem_db):
    """反例(本次修复的回归点): 批量更新 SQL 失败时必须重抛，
    否则 etl/update_limit.py 无条件 return 0，写库失败仍退出 0。"""
    broken = MagicMock()
    broken.execute.side_effect = RuntimeError("SQL 执行失败")
    broken.close = MagicMock()
    with patch("util.dbutil.get_connection", return_value=broken):
        with pytest.raises(RuntimeError, match="SQL 执行失败"):
            update_price_limits_by_range("2026-07-06", "2026-07-06")
    broken.close.assert_called_once()
