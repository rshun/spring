import pytest
import pandas as pd

from etl.adjust import process_and_save_adjust_factors
from tests.conftest import insert_trade_cal


def _insert_trade_cals(conn, dates):
    for d in dates:
        insert_trade_cal(conn, d, 1)


def _stock_list(symbol="600519", exchange="SH",
                start="2023-01-03", end="2023-01-06"):
    return [(symbol, exchange, start, end, "L")]


def _adj_df(code="600519.SH", dates=None, factors=None):
    dates = dates or ["2023-01-03"]
    factors = factors or [1.05]
    return pd.DataFrame({
        "code": [code] * len(dates),
        "date": dates,
        "fore_factor": factors,
        "back_factor": factors,
        "adjust_factor": factors,
    })


TRADE_DATES_JAN = ["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"]


# ── 正常测试 ──────────────────────────────────────────────────────────────────

def test_process_new_stock_default_factor_one(mem_db):
    """新股首次运行，无复权事件 → 默认因子 1.0。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(pd.DataFrame(), _stock_list(), mem_db)
    rows = mem_db.execute(
        "SELECT fore_factor FROM ADJ_FACTOR WHERE code = '600519.SH' ORDER BY trade_date"
    ).fetchall()
    assert len(rows) == 4
    assert all(r[0] == 1.0 for r in rows)


def test_process_with_event_densifies_from_event_date(mem_db):
    """有复权事件 → 从事件日起稠密化，ADJ_FACTOR 行数 = 区间内交易日数。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    adj = _adj_df(dates=["2023-01-04"], factors=[1.05])
    process_and_save_adjust_factors(adj, _stock_list(), mem_db)
    count = mem_db.execute(
        "SELECT COUNT(*) FROM ADJ_FACTOR WHERE code = '600519.SH'"
    ).fetchone()[0]
    assert count == 4


def test_process_asof_join_forward_fill(mem_db):
    """ASOF JOIN 向前填充：事件后无新事件时，因子沿用最近一次事件值。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    adj = _adj_df(dates=["2023-01-04"], factors=[1.05])
    process_and_save_adjust_factors(adj, _stock_list(), mem_db)
    rows = mem_db.execute(
        "SELECT trade_date, fore_factor FROM ADJ_FACTOR "
        "WHERE code = '600519.SH' ORDER BY trade_date"
    ).fetchall()
    # 01-03: 无事件，默认 1.0；01-04以后: 1.05
    assert rows[0][1] == 1.0   # 2023-01-03
    assert rows[1][1] == 1.05  # 2023-01-04
    assert rows[2][1] == 1.05  # 2023-01-05（ASOF 沿用）
    assert rows[3][1] == 1.05  # 2023-01-06


def test_process_incremental_append(mem_db):
    """已有稠密历史，增量续接：仅补 last_dense_date+1 之后的数据。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN + ["2023-01-09", "2023-01-10"])
    # 第一次处理 01-03 到 01-06
    process_and_save_adjust_factors(pd.DataFrame(), _stock_list(), mem_db)
    count_before = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]

    # 第二次处理 01-03 到 01-10（续接）
    process_and_save_adjust_factors(
        pd.DataFrame(),
        [("600519", "SH", "2023-01-03", "2023-01-10", "L")],
        mem_db
    )
    count_after = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]
    assert count_after == count_before + 2  # 补了 01-09 和 01-10


# ── 异常测试 ──────────────────────────────────────────────────────────────────

def test_process_empty_stock_list_returns_early(mem_db):
    process_and_save_adjust_factors(pd.DataFrame(), [], mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]
    assert count == 0


def test_process_adj_df_missing_columns_raises_value_error(mem_db):
    bad_df = pd.DataFrame({"code": ["600519.SH"], "date": ["2023-01-04"]})
    with pytest.raises(ValueError, match="缺少列"):
        process_and_save_adjust_factors(bad_df, _stock_list(), mem_db)


class _FaultInjectConn:
    """Wrap a DuckDB connection to inject a fault on the first ADJ_FACTOR INSERT."""

    def __init__(self, real_conn):
        self._real = real_conn
        self._call_count = 0

    def execute(self, sql, *args, **kwargs):
        if "INSERT OR REPLACE INTO ADJ_FACTOR" in sql:
            self._call_count += 1
            if self._call_count == 1:
                raise RuntimeError("模拟 DB 写入失败")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_process_rollback_on_error(mem_db):
    """黑盒：事务原子性——中途失败后 ADJ_FACTOR 无脏数据。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    # 先写入一些数据
    process_and_save_adjust_factors(pd.DataFrame(), _stock_list(), mem_db)
    count_before = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]

    # 通过包装对象注入故障，使第一次 ADJ_FACTOR INSERT 抛出异常
    faulty_conn = _FaultInjectConn(mem_db)
    with pytest.raises(RuntimeError):
        process_and_save_adjust_factors(
            pd.DataFrame(), _stock_list("000001", "SZ"), faulty_conn
        )

    # 原有数据应不受影响
    count_after = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]
    assert count_after == count_before
