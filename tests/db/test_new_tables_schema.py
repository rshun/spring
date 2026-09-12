"""SUSPENSION_DAILY / LIMIT_POOL_DAILY 建表与约束"""
import pytest
import duckdb


def test_suspension_daily_exists_with_expected_columns(mem_db):
    """正例: 表建得起来且列齐全"""
    cols = {r[0] for r in mem_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'SUSPENSION_DAILY'").fetchall()}
    assert cols == {
        "code", "trade_date", "name", "suspend_time", "resume_deadline",
        "suspend_period", "suspend_reason", "market", "expect_resume", "source",
    }


def test_limit_pool_daily_exists_with_expected_columns(mem_db):
    """正例: 表建得起来且列齐全"""
    cols = {r[0] for r in mem_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'LIMIT_POOL_DAILY'").fetchall()}
    assert cols == {
        "code", "trade_date", "limit_type", "name", "pct_change", "close",
        "amount", "float_mv", "total_mv", "turnover_rate", "seal_amount",
        "last_seal_time", "industry", "first_seal_time", "broken_times",
        "limit_stat", "boards", "pe_dynamic", "board_amount", "down_days",
        "open_times", "source",
    }


def test_limit_pool_allows_same_code_both_directions(mem_db):
    """正例: 主键含 limit_type, 同股同日两个方向可共存(炸板后尾盘跌停等极端情形)"""
    for lt in ("U", "D"):
        mem_db.execute(
            "INSERT INTO LIMIT_POOL_DAILY (code, trade_date, limit_type, source) "
            "VALUES (?, ?, ?, ?)", ["600519.SH", "2024-04-26", lt, "akstock"])
    assert mem_db.execute("SELECT COUNT(*) FROM LIMIT_POOL_DAILY").fetchone()[0] == 2


def test_limit_pool_rejects_invalid_limit_type(mem_db):
    """反例: limit_type 只允许 U/D, 写入 X 必须被 CHECK 约束拒绝"""
    with pytest.raises(duckdb.ConstraintException):
        mem_db.execute(
            "INSERT INTO LIMIT_POOL_DAILY (code, trade_date, limit_type, source) "
            "VALUES (?, ?, ?, ?)", ["600519.SH", "2024-04-26", "X", "akstock"])


def test_suspension_rejects_duplicate_primary_key(mem_db):
    """反例: (code, trade_date) 重复必须被主键拒绝"""
    mem_db.execute(
        "INSERT INTO SUSPENSION_DAILY (code, trade_date, source) VALUES (?, ?, ?)",
        ["600519.SH", "2024-04-26", "akstock"])
    with pytest.raises(duckdb.ConstraintException):
        mem_db.execute(
            "INSERT INTO SUSPENSION_DAILY (code, trade_date, source) VALUES (?, ?, ?)",
            ["600519.SH", "2024-04-26", "akstock"])
