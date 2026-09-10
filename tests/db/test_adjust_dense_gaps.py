# 修改记录:
#   2026-09-10  Claude  新增：ADJ_FACTOR 运行前缺口预检 check_dense_gaps 的正反例（B006/B007 拦截）
"""etl/adjust.py check_dense_gaps 预检：漏跑 / 上市日起未稠密化 / 区间内部空洞；仅内存库。"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import adjust
from etl.adjust import check_dense_gaps
from tests.conftest import insert_stock_info, insert_trade_cal

# 连续 6 个交易日；下标即"第几天"
DAYS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08"]


def _cal(conn, days=DAYS):
    for d in days:
        insert_trade_cal(conn, d, 1)


def _dense(conn, code, days, factor=1.0):
    for d in days:
        conn.execute(
            "INSERT INTO ADJ_FACTOR(code,trade_date,fore_factor,back_factor,adjust_factor,updated_at) "
            "VALUES (?,?,?,?,?,now())", [code, d, factor, factor, factor])


def _stocks(symbol="000001", ex="SZ", start=DAYS[-1], end=DAYS[-1]):
    return [(symbol, ex, start, end, "L")]


# ── 正例：不应报缺口 ──────────────────────────────────────────────────────────

def test_no_rows_and_start_equals_list_date_is_clean(mem_db):
    """正例: 首次初始化——无稠密行且 start_date == list_date → 无缺口"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    assert check_dense_gaps(mem_db, _stocks(start=DAYS[0])) == []


def test_contiguous_daily_run_is_clean(mem_db):
    """正例: 稠密到 D-1，今天从 D 起 → 无缺口（正常日常增量）"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    _dense(mem_db, "000001.SZ", DAYS[:-1])
    assert check_dense_gaps(mem_db, _stocks(start=DAYS[-1])) == []


def test_old_stock_listed_before_calendar_is_clean(mem_db):
    """正例(暂不要 2000 年前): 上市日早于 TRADE_CAL 起点、无稠密行、start=list_date
    → 日历里没有那段日期，不误报"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")
    assert check_dense_gaps(mem_db, _stocks(start="1991-04-03")) == []


def test_backfill_window_before_history_is_clean(mem_db):
    """正例: 显式回填一个早于现有历史的窗口（start < min）不算缺口"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    _dense(mem_db, "000001.SZ", DAYS[3:])
    assert check_dense_gaps(mem_db, _stocks(start=DAYS[0], end=DAYS[2])) == []


# ── 反例：必须报缺口 ──────────────────────────────────────────────────────────

def test_tail_gap_after_missed_runs(mem_db):
    """反例(B006): 稠密到 D-3，漏跑两天，今天从 D 起 → tail，首缺 D-2，缺 2 天"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    _dense(mem_db, "000001.SZ", DAYS[:3])            # 到 09-03
    gaps = check_dense_gaps(mem_db, _stocks(start=DAYS[-1]))   # 起点 09-08
    assert [(g["kind"], str(g["first_missing"]), g["missing_days"]) for g in gaps] == \
        [("tail", "2026-09-04", 2)]
    assert str(gaps[0]["last_dense"]) == "2026-09-03"


def test_init_gap_when_new_stock_picked_up_late(mem_db):
    """反例(新股上市日被错过 / B007): 无稠密行但 list_date 早于 start_date → init"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[3])     # 09-04 上市
    gaps = check_dense_gaps(mem_db, _stocks(start=DAYS[-1]))       # 今天 09-08 才跑
    assert [(g["kind"], str(g["first_missing"]), g["missing_days"]) for g in gaps] == \
        [("init", "2026-09-04", 2)]
    assert gaps[0]["last_dense"] is None


def test_interior_hole_detected(mem_db):
    """反例: 区间内部少了一天（手工删行/半途失败）→ hole，指向缺的那天"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    _dense(mem_db, "000001.SZ", [DAYS[0], DAYS[1], DAYS[3], DAYS[4]])   # 缺 09-03
    gaps = check_dense_gaps(mem_db, _stocks(start=DAYS[-1]))
    assert [(g["kind"], str(g["first_missing"]), g["missing_days"]) for g in gaps] == \
        [("hole", "2026-09-03", 1)]


def test_multiple_stocks_sorted_by_first_missing(mem_db):
    """反例: 多只股票不同缺口，按首缺日排序，回填命令取最早日"""
    _cal(mem_db)
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    insert_stock_info(mem_db, "600000", "SH", "MAIN", DAYS[2])       # 09-03 上市，从未稠密化
    _dense(mem_db, "000001.SZ", DAYS[:4])                             # 到 09-04
    stocks = _stocks(start=DAYS[-1]) + _stocks("600000", "SH", start=DAYS[-1])
    gaps = check_dense_gaps(mem_db, stocks)
    assert [(g["code"], g["kind"], str(g["first_missing"])) for g in gaps] == [
        ("600000.SH", "init", "2026-09-03"),
        ("000001.SZ", "tail", "2026-09-07"),
    ]


def test_empty_stock_list_returns_empty(mem_db):
    """反例: 空候选 → 空结果，不报错"""
    assert check_dense_gaps(mem_db, []) == []


def test_bj_and_delisted_are_not_checked(mem_db):
    """正例(实库复现): 9 开头与退市股被两个源跳过、永远没有稠密行，
    预检必须同样跳过它们，否则 344 只北交所会让每次日常跑都被 init 规则拦下"""
    _cal(mem_db)
    insert_stock_info(mem_db, "920001", "BJ", "BJ", DAYS[0])
    insert_stock_info(mem_db, "600001", "SH", "MAIN", DAYS[0], list_status="D")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", DAYS[0])
    stocks = [
        ("920001", "BJ", DAYS[-1], DAYS[-1], "L"),   # 9 开头，无稠密行
        ("600001", "SH", DAYS[-1], DAYS[-1], "D"),   # 退市，无稠密行
        ("000001", "SZ", DAYS[-1], DAYS[-1], "L"),   # 正常股，无稠密行 → 应报 init
    ]
    gaps = check_dense_gaps(mem_db, stocks)
    assert [g["code"] for g in gaps] == ["000001.SZ"]


# ── main() 集成：有缺口 → 退出码 1 且不进入取数 ──────────────────────────────

def _run_main(gaps, source="local"):
    args = adjust.build_parser().parse_args(["-s", source, "-b", "20260908", "-e", "20260908"])
    module = MagicMock()
    module.fetch_adjust_factors.return_value = pd.DataFrame()
    with patch.object(adjust, "parse_arguments", return_value=args), \
         patch.object(adjust, "check_parameters", return_value=True), \
         patch.object(adjust, "myutil") as util, \
         patch.object(adjust, "dbutil") as db, \
         patch.object(adjust, "check_dense_gaps", return_value=gaps) as chk, \
         patch.object(adjust, "process_and_save_adjust_factors"):
        util.trans_datestr_format.side_effect = ["2026-09-08", "2026-09-08"]
        util.import_source_module.return_value = module
        db.get_candidate_codes.return_value = _stocks()
        rc = adjust.main()
    return rc, chk, util, module


def test_main_refuses_when_gaps_and_skips_fetch(caplog):
    """反例: 预检命中 → 退出码 1，日志含缺口与回填命令，且未调用取数"""
    gap = dict(code="000001.SZ", kind="tail", last_dense=pd.Timestamp("2026-09-03").date(),
               expected_from=pd.Timestamp("2026-09-03").date(), start_date=pd.Timestamp("2026-09-08").date(),
               first_missing=pd.Timestamp("2026-09-04").date(), missing_days=2)
    with caplog.at_level("ERROR", logger="etl.adjust"):
        rc, chk, util, module = _run_main([gap])
    assert rc == 1
    chk.assert_called_once()
    util.import_source_module.assert_not_called()
    module.fetch_adjust_factors.assert_not_called()
    assert "拒绝运行" in caplog.text
    assert "-b 20260904" in caplog.text and "-c 000001" in caplog.text


def test_main_proceeds_when_no_gaps():
    """正例: 预检为空 → 继续取数、退出码 0"""
    rc, chk, util, module = _run_main([])
    assert rc == 0
    chk.assert_called_once()
    module.fetch_adjust_factors.assert_called_once()


def test_main_skips_check_when_densify_off():
    """正例: densify=off 不写稠密表 → 不做预检（bstock 默认 densify 关）"""
    rc, chk, *_ = _run_main([], source="bstock")
    assert rc == 0
    chk.assert_not_called()
