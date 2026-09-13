# 修改记录:
#   2026-09-13  Claude  新增 save_xdr_event_ths_to_db 写库测试(seq 分配、先删后插、区间隔离)
"""XDR_EVENT_THS 写库: seq 分配、先删后插、区间隔离。"""
import datetime

import pandas as pd

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
