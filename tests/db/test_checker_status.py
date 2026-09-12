"""核对框架状态机: 三条判定规则(按日判、按 limit_type 判、0 行即未取到)。"""
import pytest

from util import checker


def _insert_pool(conn, trade_date, limit_type, code):
    conn.execute(
        "INSERT INTO LIMIT_POOL_DAILY (code, trade_date, limit_type, source) "
        "VALUES (?, ?, ?, 'akstock')", [code, trade_date, limit_type])


def _insert_susp(conn, trade_date, code):
    conn.execute(
        "INSERT INTO SUSPENSION_DAILY (code, trade_date, source) "
        "VALUES (?, ?, 'akstock')", [code, trade_date])


# ── 规则 1: 按日逐天判定 ──────────────────────────────────────────────

def test_all_dates_present(mem_db):
    """正例: 区间内每天都有数据 -> 无缺失日期"""
    for d in ("2024-04-24", "2024-04-25", "2024-04-26"):
        _insert_susp(mem_db, d, "600001.SH")
    has, missing = checker.split_dates_by_source(
        mem_db, "SUSPENSION_DAILY", ["2024-04-24", "2024-04-25", "2024-04-26"])
    assert has == ["2024-04-24", "2024-04-25", "2024-04-26"]
    assert missing == []


def test_partial_dates_missing(mem_db):
    """反例: 区间内部分日期无数据 -> 精确列出缺的那几天"""
    _insert_susp(mem_db, "2024-04-24", "600001.SH")
    _insert_susp(mem_db, "2024-04-26", "600001.SH")
    has, missing = checker.split_dates_by_source(
        mem_db, "SUSPENSION_DAILY", ["2024-04-24", "2024-04-25", "2024-04-26"])
    assert has == ["2024-04-24", "2024-04-26"]
    assert missing == ["2024-04-25"]


def test_all_dates_missing(mem_db):
    """反例: 区间内全部日期无数据"""
    has, missing = checker.split_dates_by_source(
        mem_db, "SUSPENSION_DAILY", ["2024-04-24", "2024-04-25"])
    assert has == []
    assert missing == ["2024-04-24", "2024-04-25"]


# ── 规则 2: LIMIT_POOL_DAILY 必须按 limit_type 分别判 ──────────────────

def test_limit_type_judged_independently(mem_db):
    """反例(关键): 当日有涨停行、无跌停行时, 跌停方向必须判为缺数据

    若按整表判, 表里有涨停行就会被当成「有数据」, 跌停核对便会拿空集合
    去比库内 is_limit_down=1, 把每条跌停标记都误报成「库内多标跌停」。
    """
    _insert_pool(mem_db, "2026-09-11", "U", "600001.SH")
    has_u, missing_u = checker.split_dates_by_source(
        mem_db, "LIMIT_POOL_DAILY", ["2026-09-11"],
        extra_where="limit_type = ?", extra_params=["U"])
    has_d, missing_d = checker.split_dates_by_source(
        mem_db, "LIMIT_POOL_DAILY", ["2026-09-11"],
        extra_where="limit_type = ?", extra_params=["D"])
    assert has_u == ["2026-09-11"] and missing_u == []
    assert has_d == [] and missing_d == ["2026-09-11"]


# ── 规则 3 + 状态归并 ─────────────────────────────────────────────────

def test_resolve_status_ok():
    """正例: 全有数据且零差异 -> ok"""
    assert checker.resolve_status(["d1", "d2"], [], 0) == checker.STATUS_OK


def test_resolve_status_mismatch():
    """正例: 全有数据但有差异 -> mismatch"""
    assert checker.resolve_status(["d1"], [], 3) == checker.STATUS_MISMATCH


def test_resolve_status_partial():
    """反例: 部分日期缺数据 -> partial, 不得因另几天一致就报 ok"""
    assert checker.resolve_status(["d1"], ["d2"], 0) == checker.STATUS_PARTIAL


def test_resolve_status_source_missing():
    """反例(最关键): 一天数据都没有 -> source_missing, 绝不能是 ok"""
    assert checker.resolve_status([], ["d1", "d2"], 0) == checker.STATUS_SOURCE_MISSING


def test_resolve_status_empty_range_is_source_missing():
    """反例: 连日期都没有(区间无交易日) -> 同样按未核对处理"""
    assert checker.resolve_status([], [], 0) == checker.STATUS_SOURCE_MISSING
