# 修改记录:
#   2026-09-13  Claude  新建复权因子逐日恒等式核对的正反例
"""复权因子逐日恒等式核对: 一致 / 漏事件 / 多事件 / 比例错 / 舍入平局 / 指数排除"""
from tests.conftest import insert_stock_info
from tools.checks import adjust_invariant
from tools.checks.adjust_invariant import check_invariant

BEGIN = "2026-03-01"
END = "2026-03-31"


def _stock(conn, symbol="600000", exchange="SH", board="MAIN"):
    insert_stock_info(conn, symbol, exchange, board, "2000-01-01")
    return f"{symbol}.{exchange}"


def _bar(conn, code, date, close, pre_close, tradestatus=1):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100, 1000.0)",
        [code, date, close, close, close, close, pre_close, tradestatus])


def _factor(conn, code, date, back):
    conn.execute(
        "INSERT INTO ADJ_FACTOR_LOCAL (code, trade_date, fore_factor, "
        "back_factor, adjust_factor) VALUES (?, ?, 1.0, ?, ?)",
        [code, date, back, back])


def _run(conn, symbols=None):
    return check_invariant(conn, BEGIN, END, symbols)


# ── 正例 ─────────────────────────────────────────────────────────────────────

def test_non_event_days_produce_no_output(mem_db):
    """正例: 非除权日 pre_close == 前收、因子不动 -> 恒等式成立, 零输出"""
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
    _bar(mem_db, code, "2026-03-03", 10.20, 10.00)
    _bar(mem_db, code, "2026-03-04", 10.10, 10.20)
    assert _run(mem_db).empty


def test_event_day_with_matching_factor_jump_is_ok(mem_db):
    """正例: 除权日因子按交易所比例跳 -> 一致, 零输出

    前收 10.00、除权参考价 9.50 -> 比例 10.00/9.50；因子 1.0 -> 1.0*10/9.5。
    """
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
    _bar(mem_db, code, "2026-03-03", 9.60, 9.50)
    _factor(mem_db, code, "2026-03-03", 10.00 / 9.50)
    assert _run(mem_db).empty


def test_rounding_tie_is_not_reported(mem_db):
    """正例(回归): 偏差恰为半分的舍入平局不得报成比例错。

    容差基准必须取「两个参考价中较小者」: 此处交易所参考价 25.72, 因子隐含参考价
    25.715, 偏差 0.005。若容差按 0.005/25.72 算, 该平局会被判超差——实测这一处
    基准取错会在全市场虚报 525 条, 把工具自身的缺陷误读成被测对象的缺陷。
    """
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 25.74, 25.70)
    _bar(mem_db, code, "2026-03-03", 25.80, 25.72)
    _factor(mem_db, code, "2026-03-03", 25.74 / 25.715)
    assert _run(mem_db).empty


def test_index_rows_are_excluded(mem_db):
    """反例: 指数不做复权, 其 pre_close 与前收的差异来自编制规则, 必须排除"""
    code = _stock(mem_db, "000300", "SH", "INDEX")
    _bar(mem_db, code, "2026-03-02", 4000.0, 3990.0)
    _bar(mem_db, code, "2026-03-03", 4100.0, 3900.0)   # 大幅跳变且无因子行
    assert _run(mem_db).empty


# ── 反例: 三类缺陷 ───────────────────────────────────────────────────────────

def test_missing_event_is_reported(mem_db):
    """反例: 交易所有跳变(前收 10.00 -> 参考价 9.50)而因子表无该日行"""
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
    _bar(mem_db, code, "2026-03-03", 9.60, 9.50)
    df = _run(mem_db)
    assert len(df) == 1
    assert df.iloc[0]["issue"] == adjust_invariant.ISSUE_EVENT_MISSING


def test_spurious_event_is_reported(mem_db):
    """反例: 交易所当日没跳(pre_close == 前收)而因子跳了"""
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
    _bar(mem_db, code, "2026-03-03", 10.20, 10.00)
    _factor(mem_db, code, "2026-03-03", 1.3)
    df = _run(mem_db)
    assert len(df) == 1
    assert df.iloc[0]["issue"] == adjust_invariant.ISSUE_EVENT_SPURIOUS


def test_wrong_ratio_is_reported(mem_db):
    """反例: 两边都跳但比例不符 —— 交易所 10.00/9.50, 因子按名义的 10.00/9.00"""
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
    _bar(mem_db, code, "2026-03-03", 9.60, 9.50)
    _factor(mem_db, code, "2026-03-03", 10.00 / 9.00)
    df = _run(mem_db)
    assert len(df) == 1
    assert df.iloc[0]["issue"] == adjust_invariant.ISSUE_RATIO_WRONG


# ── 反例: 取数边界 ───────────────────────────────────────────────────────────

def test_prev_close_crosses_suspension_and_window_start(mem_db):
    """反例(取数边界): 前收必须取「窗口外的最近一个**交易**日收盘」。

    停牌行的 close 不是真实成交价(见 datasource/local_xdr.py: 停牌行 close 为前收
    结转), 不得参与取价; 而该交易日又可能落在窗口之外, 也不能因此不取。这里把停牌行
    的 close 故意写成 9.00 以区分两种实现: 只有正确跳过停牌行, 复牌日的前收才会是
    10.00 从而与 pre_close 相符; 一旦把停牌行当交易日, 前收变成 9.00, 该股会被虚报
    成漏事件。
    """
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-02-20", 10.00, 9.90)                 # 窗口之外的最后交易日
    _bar(mem_db, code, "2026-02-25", 9.00, 10.00, tradestatus=0)  # 停牌行, close 不可信
    _bar(mem_db, code, "2026-03-02", 10.30, 10.00)                # 复牌, 前收应为 10.00
    assert _run(mem_db).empty


def test_event_dated_inside_suspension_is_not_reported(mem_db):
    """反例(回归): 停牌期间除权 —— 事件日落在非交易日上, 不得误报漏事件。

    实例 600717.SH: 停牌 2026-06-09~06-22, 期间 06-18 每10股派 1.02 除权, 占位行
    价格由 4.31 落到 4.21(4.31-0.102=4.208 四舍五入 4.21), 因子表在 06-18 有行,
    整条链完全正确。若按「复牌日有没有因子行」精确 join 去找, 会在 06-23 找不到
    而误报 —— 实测这一处虚报 917 条。因子水位必须按 ASOF 取, 比较区间
    (上一交易日, 本交易日] 内的水位变化。
    """
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 4.31, 4.30)                   # 停牌前最后交易日
    _bar(mem_db, code, "2026-03-03", 4.31, 4.31, tradestatus=0)
    _bar(mem_db, code, "2026-03-04", 4.21, 4.21, tradestatus=0)    # 停牌期间除权
    _bar(mem_db, code, "2026-03-05", 4.21, 4.21, tradestatus=0)
    _bar(mem_db, code, "2026-03-06", 4.37, 4.21)                   # 复牌
    _factor(mem_db, code, "2026-03-04", 4.31 / 4.21)               # 事件落在停牌日
    assert _run(mem_db).empty


def test_multiple_events_between_two_trading_days_are_aggregated(mem_db):
    """正例: 两个交易日之间有多笔事件时, 比较的是区间内水位的累计变化。

    精确 join 只看得到其中一笔, 会把另一笔算成漏。
    """
    code = _stock(mem_db)
    _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
    _bar(mem_db, code, "2026-03-03", 9.50, 9.50, tradestatus=0)
    _bar(mem_db, code, "2026-03-04", 8.00, 8.00, tradestatus=0)
    _bar(mem_db, code, "2026-03-05", 8.10, 8.00)                   # 复牌, 累计 10.00/8.00
    _factor(mem_db, code, "2026-03-03", 10.00 / 9.50)
    _factor(mem_db, code, "2026-03-04", 10.00 / 8.00)
    assert _run(mem_db).empty


def test_symbols_filter_limits_scope(mem_db):
    """正例: 指定代码时只核对该股, 其余股票的缺陷不进结果"""
    a = _stock(mem_db, "600000", "SH")
    b = _stock(mem_db, "000001", "SZ")
    for code in (a, b):
        _bar(mem_db, code, "2026-03-02", 10.00, 9.90)
        _bar(mem_db, code, "2026-03-03", 9.60, 9.50)           # 两只都漏事件
    assert len(_run(mem_db)) == 2
    only_a = _run(mem_db, ["600000"])
    assert len(only_a) == 1
    assert only_a.iloc[0]["code"] == a


def test_empty_result_keeps_columns(mem_db):
    """反例: 无差异时返回空帧但列齐全 —— 调用方 _report_diffs 要按列写 CSV"""
    _stock(mem_db)
    df = _run(mem_db)
    assert df.empty
    assert list(df.columns) == adjust_invariant.COLUMNS
