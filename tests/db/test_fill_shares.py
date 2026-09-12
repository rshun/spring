# 修改记录:
#   2026-09-10  Claude  新增反例：库层失败必须重抛（此前 fill_daily_basic_shares / _mv 吞异常）
#   2026-09-12  Claude  新增正反测试：float_shares 改以 turnover_rate 反推的
#                       市场隐含流通盘为准、gbbq 降为 fallback
#   2026-09-12  Claude  新增反例：吸附候选不得超过当日总股本
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from util.dbutil import fill_daily_basic_shares, fill_daily_basic_mv
from tests.conftest import insert_stock_info


def _insert_daily_basic(conn, code, trade_date, turnover_rate=None):
    conn.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, turnover_rate) VALUES (?, ?, ?)",
        [code, trade_date, turnover_rate],
    )


def _insert_stock_daily(conn, code, trade_date, volume, tradestatus=1, close=10.0):
    conn.execute(
        "INSERT INTO STOCK_DAILY "
        "(code, date, open, high, low, close, pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [code, trade_date, close, close, close, close, close,
         tradestatus, volume, volume * close],
    )


def _insert_capital_detail(conn, symbol, trade_date, category,
                           prev_float, prev_total, float_after, total_after):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL "
        "(code, date, category, dividend, allotment_price, bonus_share, allotment_share) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [symbol, trade_date, category, prev_float, prev_total, float_after, total_after],
    )


def test_fill_shares_uses_transfer_listing_events(mem_db):
    insert_stock_info(mem_db, "600399", "SH", "MAIN", "2018-01-01")
    _insert_daily_basic(mem_db, "600399.SH", "2018-12-27")
    _insert_daily_basic(mem_db, "600399.SH", "2018-12-28")
    _insert_daily_basic(mem_db, "600399.SH", "2018-12-29")
    _insert_capital_detail(
        mem_db,
        "600399",
        "2018-12-28",
        "转配股上市",
        23246.4902,
        52000,
        197210,
        197210,
    )

    fill_daily_basic_shares("2018-12-27", "2018-12-29", conn=mem_db)

    rows = mem_db.execute(
        "SELECT CAST(trade_date AS VARCHAR), float_shares, total_shares "
        "FROM DAILY_BASIC ORDER BY trade_date"
    ).fetchall()
    assert rows == [
        ("2018-12-27", 232464902, 520000000),
        ("2018-12-28", 1972100000, 1972100000),
        ("2018-12-29", 1972100000, 1972100000),
    ]


def test_fill_shares_ignores_unknown_new_category(mem_db):
    insert_stock_info(mem_db, "000908", "SZ", "MAIN", "2020-01-01")
    _insert_daily_basic(mem_db, "000908.SZ", "2026-03-11")
    _insert_capital_detail(
        mem_db,
        "000908",
        "2026-03-11",
        "未知新类别",
        0,
        0,
        10,
        0,
    )

    fill_daily_basic_shares("2026-03-11", "2026-03-11", conn=mem_db)

    row = mem_db.execute(
        "SELECT float_shares, total_shares FROM DAILY_BASIC"
    ).fetchone()
    assert row == (None, None)


def test_fill_shares_ignores_private_placement_plan_events(mem_db):
    insert_stock_info(mem_db, "600000", "SH", "MAIN", "2020-01-01")
    _insert_daily_basic(mem_db, "600000.SH", "2024-01-02")
    _insert_capital_detail(
        mem_db,
        "600000",
        "2024-01-02",
        "增发新股",
        0,
        0,
        8888,
        9999,
    )

    fill_daily_basic_shares("2024-01-02", "2024-01-02", conn=mem_db)

    row = mem_db.execute(
        "SELECT float_shares, total_shares FROM DAILY_BASIC"
    ).fetchone()
    assert row == (None, None)


# ── 2026-09-10：库层失败必须重抛（契约 C1，CLI 才能返回退出码 1）─────────────────

@pytest.mark.parametrize("func", [fill_daily_basic_shares, fill_daily_basic_mv],
                         ids=["shares", "mv"])
def test_db_failure_raises_instead_of_swallowing(func):
    """反例: 连接执行抛错时函数必须把异常抛给调用方, 不能 logger.error 后静默返回"""
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        func("2026-09-01", "2026-09-08", None, None, conn=conn)


# ── 2026-09-12：float_shares 以 turnover_rate 反推的市场隐含流通盘为准 ──────────
#
# 背景: gbbq 的股本「数值」可信、「生效日期」不可信。实测 001223.SZ 的
# 4988.2万 流通盘事件被 gbbq 记成 2025-12-31, 而市场真实生效日是 2026-04-28,
# ASOF JOIN 无条件信任该日期, 导致 2026-01~04 整整 4 个月提前套用了未来股本。
#
# 采信市场值需过两道关: 当日反推值吸附到 gbbq 已知股本取值, 且回看窗口内所有
# 吸附成功的日子都指向同一个候选(一致性闸门)。故以下用例都要喂满一个窗口。

_WINDOW = 20        # 与 dbutil._SHARE_GATE_WINDOW 对齐
_SEED_DAYS = 25     # 喂满窗口并留出余量


def _seed_days(conn, code, start, days, turnover_rate, volume,
               tradestatus=1):
    """连续写入 days 天的 DAILY_BASIC + STOCK_DAILY。

    按自然日递增即可: fill_daily_basic_shares 不查交易日历, 窗口是「按行回看」。
    turnover_rate / volume 可传单值或长度为 days 的序列。
    """
    if not isinstance(turnover_rate, (list, tuple)):
        turnover_rate = [turnover_rate] * days
    if not isinstance(volume, (list, tuple)):
        volume = [volume] * days
    d = date.fromisoformat(start)
    for i in range(days):
        iso = d.isoformat()
        _insert_daily_basic(conn, code, iso, turnover_rate[i])
        _insert_stock_daily(conn, code, iso, volume[i], tradestatus=tradestatus)
        d += timedelta(days=1)
    return d - timedelta(days=1)


def _setup_oukeji(mem_db):
    """复现 001223.SZ: gbbq 把 4988.2万 事件的生效日记早了 4 个月。

    真实流通盘序列: 2335.2万 -(2025-12-17)-> 9335.2万 -(2026-04-28)-> 4988.2万
    gbbq 记录的日期:  2335.2万 -(2025-12-17)-> 9335.2万 -(2025-12-31)-> 4988.2万
    """
    insert_stock_info(mem_db, "001223", "SZ", "MAIN", "2022-12-12")
    _insert_capital_detail(mem_db, "001223", "2025-12-17", "股本变化",
                           2335.2, 9335.2, 9335.2, 9335.2)
    _insert_capital_detail(mem_db, "001223", "2025-12-31", "股本变化",
                           9335.2, 9335.2, 4988.2, 9335.2)


def _setup_leshan(mem_db, event_date="2020-01-02"):
    """全流通个股: gbbq 只知道 8000万 流通盘, 总股本 1亿, 解禁未进 gbbq"""
    insert_stock_info(mem_db, "600644", "SH", "MAIN", "1993-01-01")
    _insert_capital_detail(mem_db, "600644", event_date, "股本变化",
                           5000.0, 10000.0, 8000.0, 10000.0)


def _float_on(mem_db, trade_date):
    return mem_db.execute(
        "SELECT float_shares, total_shares FROM DAILY_BASIC WHERE trade_date = ?",
        [trade_date],
    ).fetchone()


def test_fill_shares_prefers_turnover_implied_float(mem_db):
    """正例: gbbq 生效日记早时, 按 turnover 反推吸附回当日真实流通盘 9335.2万"""
    _setup_oukeji(mem_db)
    # implied = 1713756 * 100 / 1.8358 ≈ 93,351,998 → 吸附到 93,352,000
    last = _seed_days(mem_db, "001223.SZ", "2026-02-01", _SEED_DAYS,
                      turnover_rate=1.8358, volume=1713756)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    # 若仍信任 gbbq 日期, float_shares 会是错误的 49882000
    assert _float_on(mem_db, last.isoformat()) == (93352000, 93352000)


def test_fill_shares_still_follows_gbbq_after_real_effective_date(mem_db):
    """正例: 事件真正生效后, 反推值指向 4988.2万, 与 gbbq 数值一致, 不产生改动"""
    _setup_oukeji(mem_db)
    # implied = 915700 * 100 / 1.8358 ≈ 49,880,161 → 吸附到 49,882,000
    last = _seed_days(mem_db, "001223.SZ", "2026-05-01", _SEED_DAYS,
                      turnover_rate=1.8358, volume=915700)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    assert _float_on(mem_db, last.isoformat())[0] == 49882000


def test_fill_shares_snaps_to_total_when_fully_circulated(mem_db):
    """正例: 自然解禁不进 gbbq(如 600644.SH), 反推值≈总股本时吸附到总股本"""
    _setup_leshan(mem_db)
    # implied = 2000000 * 100 / 2.0 = 100,000,000 = 总股本
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=2.0, volume=2000000)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    # gbbq 只知道 8000万, 市场已全流通 1亿
    assert _float_on(mem_db, last.isoformat()) == (100000000, 100000000)


def test_fill_shares_snap_absorbs_turnover_rounding_noise(mem_db):
    """正例: turnover_rate 只有 4 位小数, 逐日反推带噪声, 吸附后取值必须逐日稳定"""
    _setup_leshan(mem_db)
    # 反推值在 79,993,600 与 80,006,400 之间摆动, 都在 80,000,000 的 0.5% 内
    rates = [1.2501 if i % 2 else 1.2499 for i in range(_SEED_DAYS)]
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=rates, volume=1000000)
    first_filled = (last - timedelta(days=2)).isoformat()

    fill_daily_basic_shares(first_filled, last.isoformat(), conn=mem_db)

    rows = mem_db.execute(
        "SELECT DISTINCT float_shares FROM DAILY_BASIC "
        "WHERE trade_date BETWEEN ? AND ?",
        [first_filled, last.isoformat()],
    ).fetchall()
    assert rows == [(80000000,)]


def test_fill_shares_noop_when_turnover_derived_from_gbbq(mem_db):
    """正例(免疫): turnover_rate 若是 fill_turnover 由 gbbq float 派生的,
    反推恰好回到原值, 不得产生任何「修正」。"""
    _setup_leshan(mem_db)
    # 派生口径: 1600000 / 80000000 * 100 = 2.0
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=2.0, volume=1600000)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    assert _float_on(mem_db, last.isoformat())[0] == 80000000


# ── 反例: 闸门必须拦住不可信的反推 ───────────────────────────────────────────

def test_fill_shares_gate_rejects_unstable_implied(mem_db):
    """反例: 反推值在两个候选之间跳动(turnover_rate 口径本身不稳)时不得采信"""
    _setup_leshan(mem_db)
    # 隔日在 50,000,000 与 100,000,000 之间跳: 两者都是合法候选, 但窗口内不一致
    volumes = [1000000 if i % 2 else 2000000 for i in range(_SEED_DAYS)]
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=2.0, volume=volumes)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    # 必须回退 gbbq 的 8000万, 而不是随当天摆动写 5000万 / 1亿
    assert _float_on(mem_db, last.isoformat())[0] == 80000000


def test_fill_shares_gate_requires_full_window(mem_db):
    """反例: 回看窗口尚未填满时不得采信反推值, 哪怕这几天完全一致"""
    _setup_leshan(mem_db)
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _WINDOW - 5,
                      turnover_rate=2.0, volume=2000000)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    assert _float_on(mem_db, last.isoformat())[0] == 80000000


@pytest.mark.parametrize(
    "turnover_rate, volume, tradestatus, case",
    [
        (None, 2000000, 1, "turnover_rate 为空(baostock 未回 turn)"),
        (0.0, 2000000, 1, "turnover_rate 为 0"),
        (2.0, 0, 1, "无成交量"),
        (2.0, 2000000, 0, "停牌"),
    ],
    ids=["turnover_null", "turnover_zero", "no_volume", "suspended"],
)
def test_fill_shares_falls_back_to_gbbq_when_implied_unavailable(
    mem_db, turnover_rate, volume, tradestatus, case
):
    """反例: 反推所需输入缺失时必须安全回退到 gbbq 值, 不得写 NULL 或垃圾"""
    _setup_leshan(mem_db)
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=turnover_rate, volume=volume,
                      tradestatus=tradestatus)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    assert _float_on(mem_db, last.isoformat()) == (80000000, 100000000), case


def test_fill_shares_falls_back_when_implied_is_absurd(mem_db):
    """反例: turnover_rate 异常导致反推值远超总股本时, 回退 gbbq 而非写入垃圾"""
    _setup_leshan(mem_db)
    # implied = 2000000 * 100 / 0.0001 = 2e12, 是总股本的 2 万倍, 吸附不中
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=0.0001, volume=2000000)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    assert _float_on(mem_db, last.isoformat())[0] == 80000000


def test_fill_shares_never_exceeds_total_shares(mem_db):
    """反例(不变量): 任何路径下流通股本都不得大于总股本"""
    _setup_leshan(mem_db)
    # 反推值 1.0004 亿, 略高于总股本
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=1.9992, volume=2000000)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    float_shares, total_shares = _float_on(mem_db, last.isoformat())
    assert float_shares <= total_shares


def test_fill_shares_ignores_candidates_above_that_day_total(mem_db):
    """反例: 吸附候选是该股全历史的股本取值, 早期交易日不得吸附到未来才出现的大流通盘。

    不加「候选 <= 当日总股本」的过滤时, 这里会写出 float=2亿 > total=1亿 的非法行
    (2011-2025 历史回补实测因此净增 1305 行违反)。
    """
    insert_stock_info(mem_db, "600644", "SH", "MAIN", "1993-01-01")
    # 当期: 流通 5000万 / 总股本 1亿
    _insert_capital_detail(mem_db, "600644", "2020-01-02", "股本变化",
                           5000.0, 10000.0, 5000.0, 10000.0)
    # 多年以后才扩股到 2亿 —— 该取值会进入候选集
    _insert_capital_detail(mem_db, "600644", "2030-01-02", "股本变化",
                           10000.0, 10000.0, 20000.0, 20000.0)
    # 测试日落在 2026 年, 当日总股本仍是 1亿;
    # implied = 2000000 * 100 / 1.0 = 2亿, 正好等于未来那个候选
    last = _seed_days(mem_db, "600644.SH", "2026-08-01", _SEED_DAYS,
                      turnover_rate=1.0, volume=2000000)

    fill_daily_basic_shares(last.isoformat(), last.isoformat(), conn=mem_db)

    float_shares, total_shares = _float_on(mem_db, last.isoformat())
    assert total_shares == 100000000
    assert float_shares <= total_shares
    # 2亿候选被排除后, 剩余候选与 implied 差距超出容差 -> 回退 gbbq 的 5000万
    assert float_shares == 50000000
