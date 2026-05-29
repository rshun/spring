# 修改记录:
#   2026-05-29  Claude  新增 FINANCE_REPORT 入库的正反测试
"""save_finance_report_to_db 入库测试(使用内存 DuckDB)"""
import pandas as pd

from datasource import cw_fields
from util import dbutil


def _make_df(eps=1.23, net_profit=2.0e9, report_date="20241231", code="000001"):
    raw = pd.DataFrame([{0: code, 1: eps, 40: 1.0e11, 95: net_profit}])
    return cw_fields.cw_df_to_finance_report(raw, report_date)


# ── 正例 ────────────────────────────────────────────────

def test_save_inserts_row(mem_db):
    """正例：正常写入一条财务报表"""
    dbutil.save_finance_report_to_db(_make_df(), mem_db)
    rows = mem_db.execute("SELECT COUNT(*) FROM FINANCE_REPORT").fetchone()[0]
    assert rows == 1
    eps = mem_db.execute(
        "SELECT eps FROM FINANCE_REPORT WHERE code='000001' AND report_date=DATE '2024-12-31'"
    ).fetchone()[0]
    assert eps == 1.23


def test_upsert_updates_existing(mem_db):
    """正例：同 (code, report_date) 再次写入走 UPSERT，更新而非新增"""
    dbutil.save_finance_report_to_db(_make_df(eps=1.0), mem_db)
    dbutil.save_finance_report_to_db(_make_df(eps=9.9), mem_db)
    rows = mem_db.execute("SELECT COUNT(*) FROM FINANCE_REPORT").fetchone()[0]
    assert rows == 1
    eps = mem_db.execute("SELECT eps FROM FINANCE_REPORT").fetchone()[0]
    assert eps == 9.9


def test_views_expose_report_columns(mem_db):
    """正例：三张报表视图能读到对应字段"""
    dbutil.save_finance_report_to_db(_make_df(net_profit=5.0e8), mem_db)
    np = mem_db.execute("SELECT net_profit FROM V_INCOME_STATEMENT").fetchone()[0]
    assert np == 5.0e8
    ta = mem_db.execute("SELECT total_assets FROM V_BALANCE_SHEET").fetchone()[0]
    assert ta == 1.0e11


# ── 反例 ────────────────────────────────────────────────

def test_save_empty_df_noop(mem_db):
    """反例：空数据跳过写入，不报错且无记录"""
    dbutil.save_finance_report_to_db(pd.DataFrame(), mem_db)
    dbutil.save_finance_report_to_db(None, mem_db)
    rows = mem_db.execute("SELECT COUNT(*) FROM FINANCE_REPORT").fetchone()[0]
    assert rows == 0


def test_save_partial_columns(mem_db):
    """反例：df 只含部分字段时，仅写入存在列，缺失列为 NULL"""
    raw = pd.DataFrame([{0: "600000", 1: 0.5}])  # 仅 code + eps
    dbutil.save_finance_report_to_db(cw_fields.cw_df_to_finance_report(raw, "20231231"), mem_db)
    row = mem_db.execute(
        "SELECT eps, net_profit FROM FINANCE_REPORT WHERE code='600000'"
    ).fetchone()
    assert row[0] == 0.5
    assert row[1] is None
