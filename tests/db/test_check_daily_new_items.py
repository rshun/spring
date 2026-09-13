"""三个新核对项接入 check_daily 后的汇总与 JSON 结构"""
import json

from tests.conftest import insert_stock_info, insert_trade_cal

DATE = "2024-04-26"


def _setup(conn):
    insert_trade_cal(conn, DATE, 1)
    insert_stock_info(conn, "600001", "SH", "MAIN", "2010-01-01")


def test_warn_checks_include_three_new_items(mem_db, monkeypatch):
    """正例: 告警类核对项包含停牌/涨停/跌停三项"""
    from tools import check_daily
    _setup(mem_db)
    results = check_daily.run_warn_checks(mem_db, [DATE], "20240426", "20240426",
                                          "", "", [])
    labels = [r.label for r in results]
    assert "停牌核对" in labels
    assert "涨停核对" in labels
    assert "跌停核对" in labels


def test_new_items_are_source_missing_when_tables_empty(mem_db):
    """反例: 两张新表都空 -> 三项均 source_missing, 不得是 ok"""
    from tools import check_daily
    from util import checker
    _setup(mem_db)
    results = check_daily.run_warn_checks(mem_db, [DATE], "20240426", "20240426",
                                          "", "", [])
    new_items = [r for r in results
                 if r.label in ("停牌核对", "涨停核对", "跌停核对")]
    assert len(new_items) == 3
    assert all(r.status == checker.STATUS_SOURCE_MISSING for r in new_items)


def test_source_missing_does_not_count_as_warning(mem_db):
    """反例: source_missing 的 count 为 0, 不得被算进告警总数"""
    from tools import check_daily
    _setup(mem_db)
    results = check_daily.run_warn_checks(mem_db, [DATE], "20240426", "20240426",
                                          "", "", [])
    assert sum(r.count for r in results) == 0


def test_json_warning_entry_has_status_and_missing_dates(mem_db):
    """正例: JSON 每项带 status 与 missing_dates(增量兼容, 顶层不变)"""
    from tools import check_daily
    _setup(mem_db)
    results = check_daily.run_warn_checks(mem_db, [DATE], "20240426", "20240426",
                                          "", "", [])
    entry = next(r for r in results if r.label == "停牌核对").to_json()
    assert set(entry) == {"label", "count", "status", "missing_dates"}
    assert entry["missing_dates"] == [DATE]
    json.dumps(entry)  # 必须可序列化
