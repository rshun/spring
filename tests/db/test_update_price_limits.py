# 修改记录:
#   2026-06-24  Claude  新增沪深主板 ST 涨跌停 2026-07-06 由 5%->10% 的正反测试
"""update_price_limits_by_range 涨跌停比率计算测试 (聚焦沪深主板 ST 规则切换)。"""
from unittest.mock import patch, MagicMock

from util.dbutil import update_price_limits_by_range
from tests.conftest import insert_stock_info


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
