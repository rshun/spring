# 修改记录:
#   2026-09-10  Claude  新增：量比回填 fill_daily_basic_volume_ratio 的正反例（此前无任何测试），
#                       含 -x 交易所过滤（原本只接受不生效）
"""util.dbutil.fill_daily_basic_volume_ratio：基本计算、交易所/代码过滤、停牌与失败路径；仅内存库。"""
from unittest.mock import MagicMock

import pytest

from util.dbutil import fill_daily_basic_volume_ratio
from tests.conftest import insert_stock_info

DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08"]


def _seed(conn, code, exchange, volumes, suspended=()):
    """按 DAYS 顺序写入日线与 DAILY_BASIC 空行；suspended 中的日期 tradestatus=0"""
    symbol = code.split(".")[0]
    insert_stock_info(conn, symbol, exchange, "MAIN", DAYS[0])
    for d, v in zip(DAYS, volumes):
        conn.execute(
            "INSERT INTO STOCK_DAILY(code, date, close, volume, tradestatus) VALUES (?, ?, 10, ?, ?)",
            [code, d, v, 0 if d in suspended else 1])
        conn.execute("INSERT INTO DAILY_BASIC(code, trade_date) VALUES (?, ?)", [code, d])


def _ratios(conn, code):
    return [r[0] for r in conn.execute(
        "SELECT volume_ratio FROM DAILY_BASIC WHERE code = ? ORDER BY trade_date", [code]).fetchall()]


# ── 正例 ─────────────────────────────────────────────────────────────────────

def test_ratio_is_volume_over_prev5_mean(mem_db):
    """正例: 第 6 天量比 = 当日量 / 前 5 个交易日均量；首日无基线为 NULL"""
    _seed(mem_db, "600001.SH", "SH", [100, 100, 100, 100, 100, 300])
    fill_daily_basic_volume_ratio(DAYS[0], DAYS[-1], None, conn=mem_db)
    r = _ratios(mem_db, "600001.SH")
    assert r[0] is None
    assert r[-1] == pytest.approx(3.0)


def test_exchange_filter_limits_update_scope(mem_db):
    """正例(本次修复): 传 exchanges=['SH'] 时只更新沪市，深市保持 NULL"""
    _seed(mem_db, "600001.SH", "SH", [100] * 5 + [200])
    _seed(mem_db, "000001.SZ", "SZ", [100] * 5 + [200])
    fill_daily_basic_volume_ratio(DAYS[0], DAYS[-1], None, ["SH"], conn=mem_db)
    assert _ratios(mem_db, "600001.SH")[-1] == pytest.approx(2.0)
    assert _ratios(mem_db, "000001.SZ") == [None] * len(DAYS)


def test_no_exchange_filter_updates_all(mem_db):
    """正例: 不传 exchanges（-x all）→ 全部交易所都更新"""
    _seed(mem_db, "600001.SH", "SH", [100] * 5 + [200])
    _seed(mem_db, "000001.SZ", "SZ", [100] * 5 + [400])
    fill_daily_basic_volume_ratio(DAYS[0], DAYS[-1], None, None, conn=mem_db)
    assert _ratios(mem_db, "600001.SH")[-1] == pytest.approx(2.0)
    assert _ratios(mem_db, "000001.SZ")[-1] == pytest.approx(4.0)


def test_code_filter_still_works_with_exchange(mem_db):
    """正例: codes 与 exchanges 同时给出时取交集（与 fill_shares/turnover 一致）"""
    _seed(mem_db, "600001.SH", "SH", [100] * 5 + [200])
    _seed(mem_db, "600002.SH", "SH", [100] * 5 + [200])
    fill_daily_basic_volume_ratio(DAYS[0], DAYS[-1], ["600001.SH"], ["SH"], conn=mem_db)
    assert _ratios(mem_db, "600001.SH")[-1] == pytest.approx(2.0)
    assert _ratios(mem_db, "600002.SH") == [None] * len(DAYS)


# ── 反例 ─────────────────────────────────────────────────────────────────────

def test_suspended_day_gets_null_and_is_excluded_from_baseline(mem_db):
    """反例: 停牌日量比为 NULL，且不进入后续交易日的 5 日基线"""
    # 09-04 停牌(量 0)；09-08 的基线应为 09-01/02/03/07 + 更早不足 → 只用有效交易日
    _seed(mem_db, "600001.SH", "SH", [100, 100, 100, 0, 100, 200], suspended=(DAYS[3],))
    fill_daily_basic_volume_ratio(DAYS[0], DAYS[-1], None, conn=mem_db)
    r = _ratios(mem_db, "600001.SH")
    assert r[3] is None                       # 停牌日
    assert r[-1] == pytest.approx(2.0)        # 基线未被 0 拉低


def test_db_failure_raises_instead_of_swallowing(mem_db):
    """反例(本次修复): 库层失败必须重抛，CLI 才能返回退出码 1（此前被 logger.error 吞掉）"""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        fill_daily_basic_volume_ratio(DAYS[0], DAYS[-1], None, None, conn=conn)


def test_out_of_range_dates_leave_rows_untouched(mem_db):
    """反例: 区间外的日期不更新"""
    _seed(mem_db, "600001.SH", "SH", [100] * 5 + [200])
    fill_daily_basic_volume_ratio("2026-08-01", "2026-08-31", None, conn=mem_db)
    assert _ratios(mem_db, "600001.SH") == [None] * len(DAYS)
