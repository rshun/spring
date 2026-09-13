# 修改记录:
#   2026-09-13  Claude  build_summary 总结行量词改动(N 项告警 -> N 条告警)后
#                       同步更新断言，防止断言与实现失配
"""日志形态: 正确的一律不输出, 结尾保留一行总结"""
from tests.conftest import insert_stock_info, insert_trade_cal
from util import checker

DATE = "2024-04-26"


def _ok(label):
    return checker.CheckResult(label=label, status=checker.STATUS_OK, count=0)


def _mismatch(label, count):
    return checker.CheckResult(label=label, status=checker.STATUS_MISMATCH,
                               count=count)


def _missing(label):
    return checker.CheckResult(label=label, status=checker.STATUS_SOURCE_MISSING,
                               count=0, missing_dates=[DATE])


def test_summary_all_clean(mem_db):
    """正例: 核心完整且无告警"""
    from tools import check_daily
    line = check_daily.build_summary(0, [_ok("停牌核对"), _ok("涨停核对")])
    assert "核心日线数据完整 OK" in line
    assert "未核对" not in line


def test_summary_counts_warnings_and_unchecked(mem_db):
    """正例: 告警数与未核对项数分别计入"""
    from tools import check_daily
    line = check_daily.build_summary(
        0, [_mismatch("停牌核对", 3), _missing("跌停核对"), _ok("涨停核对")])
    assert "3 条告警" in line
    assert "1 项未核对" in line


def test_summary_reports_core_missing(mem_db):
    """反例: 核心有缺失时总结必须说出来"""
    from tools import check_daily
    line = check_daily.build_summary(12, [_ok("停牌核对")])
    assert "12" in line
    assert "完整 OK" not in line


def test_passing_core_check_emits_no_log(mem_db, caplog):
    """反例(行为变更): 通过的核心检查不再打「完整 OK」行"""
    from tools.check_daily import _check_table
    insert_trade_cal(mem_db, DATE, 1)
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, close, tradestatus) "
        "VALUES ('600001.SH', ?, 10.0, 1)", [DATE])
    with caplog.at_level("INFO"):
        result = _check_table(mem_db, "日线数据", "STOCK_DAILY", "date",
                              DATE, DATE, "", "", [], is_self_table=True)
    assert result["missing"] == 0
    assert "完整 OK" not in caplog.text


def test_no_ok_line_remains_in_source(mem_db):
    """反例(守规则): 源码里不得再有「完整 OK」的逐项日志

    需求是「不管哪一类核对项, 正确的一律不输出日志」。
    结尾总结行里的「核心日线数据完整 OK」是唯一例外, 它由 build_summary 产出。
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "tools" / "check_daily.py"
    text = src.read_text(encoding="utf-8")
    # 逐项日志的特征是 [{label}] 前缀; 结尾总结行没有这个前缀
    assert 'logger.info(f"[{label}]    完整 OK")' not in text
