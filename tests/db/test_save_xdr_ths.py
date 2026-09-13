# 修改记录:
#   2026-09-13  Claude  新增 save_xdr_event_ths_to_db 写库测试(seq 分配、先删后插、区间隔离)
#   2026-09-13  Claude  补充半区间(begin/end 只给一侧)必须报错的测试：BETWEEN ? AND NULL
#                       三值逻辑恒不匹配, 会让先删后插静默失效
#   2026-09-13  Claude  新增 codes 参数收窄删除范围的测试：api 模式候选集来自 gbbq 日期、
#                       删除按 THS 自己的 ex_date, 两套口径不一致时不收窄会误删候选集外的行
"""XDR_EVENT_THS 写库: seq 分配、先删后插、区间隔离。"""
import datetime

import pandas as pd
import pytest

from util import dbutil

COLS = ["code", "ex_date", "dividend_per_share", "per_share_bonus",
        "allotment_ratio", "allotment_price"]


def _df(rows):
    """rows: list of (code, ex_date, div, bonus, ar, ap)。列全用等长数组。"""
    return pd.DataFrame({
        "code":               [r[0] for r in rows],
        "ex_date":            [r[1] for r in rows],
        "dividend_per_share": [r[2] for r in rows],
        "per_share_bonus":    [r[3] for r in rows],
        "allotment_ratio":    [r[4] for r in rows],
        "allotment_price":    [r[5] for r in rows],
    })


D1 = datetime.date(2026, 9, 1)
D2 = datetime.date(2026, 9, 2)


def test_single_event_gets_seq_zero(mem_db):
    """正例: 同股同日只有一笔时 seq=0"""
    n = dbutil.save_xdr_event_ths_to_db(_df([("600519.SH", D1, 0.1, 0.0, 0.0, 0.0)]), mem_db)
    assert n == 1
    assert mem_db.execute("SELECT seq FROM XDR_EVENT_THS").fetchone()[0] == 0


def test_same_day_multiple_events_get_distinct_seq(mem_db):
    """正例: 同股同日两笔分录拿到不同 seq, 两行都保留

    真实案例 603883.SH 2024-06-27 就是这种形态, 语义不明故不合并。
    """
    dbutil.save_xdr_event_ths_to_db(_df([
        ("603883.SH", D1, 0.16, 0.0, 0.0, 0.0),
        ("603883.SH", D1, 0.50, 0.3, 0.0, 0.0),
    ]), mem_db)
    seqs = [r[0] for r in mem_db.execute(
        "SELECT seq FROM XDR_EVENT_THS ORDER BY seq").fetchall()]
    assert seqs == [0, 1]


def test_seq_is_deterministic_across_runs(mem_db):
    """正例(可复现): 同一份输入换个行顺序, seq 分配结果一致

    seq 按数值排序分配而非按输入顺序, 否则重跑会产生不同的 seq,
    导致「先删后插」之外的行为差异。
    """
    rows = [("603883.SH", D1, 0.16, 0.0, 0.0, 0.0),
            ("603883.SH", D1, 0.50, 0.3, 0.0, 0.0)]
    dbutil.save_xdr_event_ths_to_db(_df(rows), mem_db)
    first = mem_db.execute(
        "SELECT seq, dividend_per_share FROM XDR_EVENT_THS ORDER BY seq").fetchall()
    dbutil.save_xdr_event_ths_to_db(_df(list(reversed(rows))), mem_db)
    second = mem_db.execute(
        "SELECT seq, dividend_per_share FROM XDR_EVENT_THS ORDER BY seq").fetchall()
    assert first == second


def test_full_replace_clears_whole_table(mem_db):
    """正例: begin/end 为 None 时整表替换"""
    dbutil.save_xdr_event_ths_to_db(_df([("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0)]), mem_db)
    dbutil.save_xdr_event_ths_to_db(_df([("600519.SH", D2, 0.2, 0.0, 0.0, 0.0)]), mem_db)
    codes = [r[0] for r in mem_db.execute("SELECT code FROM XDR_EVENT_THS").fetchall()]
    assert codes == ["600519.SH"]


def test_range_mode_removes_stale_rows_in_range(mem_db):
    """反例(幽灵行): 区间重跑时成员减少, 区间内旧行必须被删除

    事件集合会因数据源修订而变化; upsert 只覆盖同主键行、不删多余旧行。
    """
    dbutil.save_xdr_event_ths_to_db(_df([
        ("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0),
        ("600519.SH", D1, 0.2, 0.0, 0.0, 0.0),
    ]), mem_db, begin=D1, end=D1)
    dbutil.save_xdr_event_ths_to_db(_df([
        ("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0),
    ]), mem_db, begin=D1, end=D1)
    codes = {r[0] for r in mem_db.execute("SELECT code FROM XDR_EVENT_THS").fetchall()}
    assert codes == {"000001.SZ"}


def test_range_mode_does_not_touch_other_dates(mem_db):
    """反例: 区间删除不得波及区间外的行"""
    dbutil.save_xdr_event_ths_to_db(_df([("000001.SZ", D2, 0.1, 0.0, 0.0, 0.0)]),
                                    mem_db, begin=D2, end=D2)
    dbutil.save_xdr_event_ths_to_db(_df([("600519.SH", D1, 0.2, 0.0, 0.0, 0.0)]),
                                    mem_db, begin=D1, end=D1)
    assert mem_db.execute("SELECT COUNT(*) FROM XDR_EVENT_THS").fetchone()[0] == 2


def test_empty_frame_in_range_still_clears_range(mem_db):
    """正例: 空帧 + 区间 -> 清空该区间并返回 0"""
    dbutil.save_xdr_event_ths_to_db(_df([("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0)]),
                                    mem_db, begin=D1, end=D1)
    n = dbutil.save_xdr_event_ths_to_db(_df([]), mem_db, begin=D1, end=D1)
    assert n == 0
    assert mem_db.execute("SELECT COUNT(*) FROM XDR_EVENT_THS").fetchone()[0] == 0


def test_source_column_recorded(mem_db):
    """正例: source 列如实记录数据来源"""
    dbutil.save_xdr_event_ths_to_db(_df([("600519.SH", D1, 0.1, 0.0, 0.0, 0.0)]),
                                    mem_db, source="api")
    assert mem_db.execute("SELECT source FROM XDR_EVENT_THS").fetchone()[0] == "api"


@pytest.mark.parametrize("begin, end", [(D1, None), (None, D1)])
def test_half_open_range_rejected(mem_db, begin, end):
    """反例: 只给一侧边界必须报错, 不得静默删 0 行

    BETWEEN ? AND NULL 在 SQL 三值逻辑下恒不匹配, 会让「先删后插」
    防幽灵行的设计悄悄失效。
    """
    with pytest.raises(ValueError, match="begin/end"):
        dbutil.save_xdr_event_ths_to_db(
            _df([("600519.SH", D1, 0.1, 0.0, 0.0, 0.0)]), mem_db,
            begin=begin, end=end)


def test_half_open_range_does_not_write_anything(mem_db):
    """反例: 守卫必须在任何写库动作之前触发, 不能删了一半才报错"""
    dbutil.save_xdr_event_ths_to_db(_df([("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0)]), mem_db)
    before = mem_db.execute("SELECT COUNT(*) FROM XDR_EVENT_THS").fetchone()[0]
    with pytest.raises(ValueError):
        dbutil.save_xdr_event_ths_to_db(
            _df([("600519.SH", D1, 0.2, 0.0, 0.0, 0.0)]), mem_db, begin=D1, end=None)
    after = mem_db.execute("SELECT COUNT(*) FROM XDR_EVENT_THS").fetchone()[0]
    assert before == after == 1


def test_range_mode_with_codes_only_deletes_those_codes(mem_db):
    """反例(防误删): 传了 codes 时, 区间内不在 codes 里的行必须保留

    api 模式的候选集来自 gbbq 日期, 与本表的 ex_date 是两套口径;
    不收窄删除范围会把「ex_date 在窗口内但 gbbq 日期在窗口外」的股票记录
    删掉且不重新插入, 而退出码仍是 0。
    """
    dbutil.save_xdr_event_ths_to_db(_df([
        ("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0),
        ("600519.SH", D1, 0.2, 0.0, 0.0, 0.0),
    ]), mem_db)
    dbutil.save_xdr_event_ths_to_db(
        _df([("000001.SZ", D1, 0.3, 0.0, 0.0, 0.0)]), mem_db,
        begin=D1, end=D1, codes=["000001.SZ"])
    rows = {r[0]: r[1] for r in mem_db.execute(
        "SELECT code, dividend_per_share FROM XDR_EVENT_THS").fetchall()}
    assert rows == {"000001.SZ": 0.3, "600519.SH": 0.2}   # 600519 未被误删


def test_range_mode_with_codes_still_removes_ghost_rows(mem_db):
    """正例: 候选集内的代码仍走先删后插, 防幽灵行的保护不能因此失效"""
    dbutil.save_xdr_event_ths_to_db(_df([
        ("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0),
        ("000001.SZ", D2, 0.2, 0.0, 0.0, 0.0),
    ]), mem_db)
    dbutil.save_xdr_event_ths_to_db(
        _df([("000001.SZ", D1, 0.1, 0.0, 0.0, 0.0)]), mem_db,
        begin=D1, end=D2, codes=["000001.SZ"])
    dates = [str(r[0]) for r in mem_db.execute(
        "SELECT ex_date FROM XDR_EVENT_THS ORDER BY ex_date").fetchall()]
    assert dates == [str(D1)]      # D2 那条(本轮不再有)被清掉
