# 修改记录:
#   2026-09-13  Claude  XDR_EVENT_THS 表建表与约束(主键含 seq、ex_date 为 DATE 类型)的测试覆盖
"""XDR_EVENT_THS 建表与约束"""
import duckdb
import pytest


def test_table_exists_with_expected_columns(mem_db):
    """正例: 表建得起来且列齐全"""
    cols = {r[0] for r in mem_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'XDR_EVENT_THS'").fetchall()}
    assert cols == {
        "code", "ex_date", "seq", "dividend_per_share",
        "per_share_bonus", "allotment_ratio", "allotment_price", "source",
    }


def test_same_day_multiple_events_coexist(mem_db):
    """正例: 主键含 seq, 同股同日多笔分录可共存

    真实案例: 603883.SH 2024-06-27 在同花顺 dump 里就是两行
    (一行派息0.16 / 一行派息0.5+送转0.3), 语义不明故保留原始行。
    """
    for seq, div in ((0, 0.16), (1, 0.50)):
        mem_db.execute(
            "INSERT INTO XDR_EVENT_THS (code, ex_date, seq, dividend_per_share, source) "
            "VALUES (?, ?, ?, ?, 'parquet')", ["603883.SH", "2024-06-27", seq, div])
    assert mem_db.execute("SELECT COUNT(*) FROM XDR_EVENT_THS").fetchone()[0] == 2


def test_duplicate_primary_key_rejected(mem_db):
    """反例: (code, ex_date, seq) 重复必须被主键拒绝"""
    mem_db.execute(
        "INSERT INTO XDR_EVENT_THS (code, ex_date, seq, source) VALUES (?, ?, ?, 'parquet')",
        ["600519.SH", "2026-09-01", 0])
    with pytest.raises(duckdb.ConstraintException):
        mem_db.execute(
            "INSERT INTO XDR_EVENT_THS (code, ex_date, seq, source) VALUES (?, ?, ?, 'parquet')",
            ["600519.SH", "2026-09-01", 0])


def test_ex_date_is_date_type(mem_db):
    """正例: ex_date 必须是 DATE 而非 VARCHAR/TIMESTAMP

    毫秒时间戳在取数层就已转成日期, 表里不该再留时区隐患。
    """
    t = mem_db.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='XDR_EVENT_THS' AND column_name='ex_date'").fetchone()[0]
    assert t == "DATE"
