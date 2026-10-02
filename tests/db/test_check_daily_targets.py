# 修改记录:
#   2026-10-02  Claude  新建: check_daily -t/--targets 类别选择与股本/行业/两融三个核对项的正反例
#   2026-10-02  Claude  新增 limit(涨跌停价) / volratio(量比空值) 两类的正反例
"""check_daily -t/--targets 按类别核对, 以及新增的涨跌停价 / 量比 / 股本资料 / 行业 / 融资融券核对项"""
import argparse
import json

import pytest

from tests.conftest import insert_stock_info, insert_trade_cal
from tools import check_daily
from tools.checks.capital import check_capital
from tools.checks.industry import check_industry
from tools.checks.limit_price import check_limit_price
from tools.checks.margin import check_margin
from tools.checks.volume_ratio import check_volume_ratio
from util import checker

DATE = "2024-04-26"
DATE2 = "2024-04-29"


@pytest.fixture(autouse=True)
def _csv_to_tmp(tmp_path, monkeypatch):
    """差异 CSV 落到 tmp_path, 不污染仓库 csv/"""
    monkeypatch.setattr(checker, "CSV_DIR", tmp_path)


def _ins_capital(conn, symbol, date="2020-01-01", category="股本变化"):
    conn.execute("INSERT INTO CAPITAL_DETAIL (code, date, category) VALUES (?, ?, ?)",
                 [symbol, date, category])


def _ins_sw_levels(conn, version="2021", l1="110000", l2="110100", l3="110101"):
    for code, level, parent in ((l1, 1, None), (l2, 2, l1), (l3, 3, l2)):
        conn.execute(
            "INSERT INTO SW_INDUSTRY (sw_version, industry_code, industry_name, "
            "sw_level, parent_code) VALUES (?, ?, ?, ?, ?)",
            [version, code, f"行业{code}", level, parent])


def _ins_industry(conn, symbol, industry_code="110101", start_date="2022-01-01"):
    conn.execute(
        "INSERT INTO STOCK_INDUSTRY_CLF_HIST_SW_RAW (symbol, start_date, industry_code) "
        "VALUES (?, ?, ?)", [symbol, start_date, industry_code])


def _ins_margin(conn, date, exchange, summary=True, detail=True):
    if summary:
        conn.execute("INSERT INTO MARGIN_SUMMARY_DAILY (trade_date, exchange_code) "
                     "VALUES (?, ?)", [date, exchange])
    if detail:
        symbol = "600000" if exchange == "SH" else "000001"
        conn.execute("INSERT INTO MARGIN_DETAIL_DAILY (trade_date, exchange_code, "
                     "symbol, code) VALUES (?, ?, ?, ?)",
                     [date, exchange, symbol, f"{symbol}.{exchange}"])


# ── 类别解析 ──────────────────────────────────────────────────────────────────

def test_resolve_targets_default_excludes_index():
    """正例: 不传 -t 时核对除 index 外的全部类别(与引入 -t 之前一致)"""
    targets = check_daily.resolve_targets(None, include_index=False)
    assert targets == list(check_daily.DEFAULT_TARGETS)
    assert "index" not in targets
    assert {"daily", "adj", "basic", "limit", "volratio",
            "capital", "industry", "margin"} <= set(targets)


def test_resolve_targets_keeps_fixed_order_and_dedups():
    """正例: 多选时按固定顺序返回且去重, 与命令行书写顺序无关"""
    assert check_daily.resolve_targets(["adj", "daily", "daily"], False) == ["daily", "adj"]


def test_resolve_targets_include_index_adds_index():
    """正例: -i 在 -t 所选类别之外追加 index(兼容旧用法)"""
    assert check_daily.resolve_targets(["daily"], True) == ["daily", "index"]
    assert "index" in check_daily.resolve_targets(None, True)


def test_parser_accepts_multiple_targets_case_insensitive():
    """正例: -t 可多选, 大小写不敏感"""
    args = check_daily.build_parser().parse_args(["-t", "Daily", "ADJ"])
    assert args.targets == ["daily", "adj"]


def test_parser_targets_defaults_to_none():
    """正例: 不传 -t 时为 None, 由 resolve_targets 落到默认集合"""
    assert check_daily.build_parser().parse_args([]).targets is None


def test_parser_rejects_unknown_target():
    """反例: 未知类别必须在 argparse 层报错, 不能静默忽略"""
    with pytest.raises(SystemExit):
        check_daily.build_parser().parse_args(["-t", "foo"])


# ── 按类别分流 ────────────────────────────────────────────────────────────────

def _setup_basic(conn):
    insert_trade_cal(conn, DATE, 1)
    insert_stock_info(conn, "600001", "SH", "MAIN", "2010-01-01")


def test_core_checks_follow_targets(mem_db):
    """正例: 只选日线只跑 STOCK_DAILY; 日线+复权因子跑两张表"""
    _setup_basic(mem_db)
    only_daily = check_daily.run_core_checks(mem_db, ["daily"], DATE, DATE, "", "", [])
    assert [c["table"] for c in only_daily] == ["STOCK_DAILY"]
    two = check_daily.run_core_checks(mem_db, ["daily", "adj"], DATE, DATE, "", "", [])
    assert [c["table"] for c in two] == ["STOCK_DAILY", "ADJ_FACTOR"]


def test_core_checks_empty_for_warn_only_targets(mem_db):
    """反例: 只选行业/两融/股本时没有核心检查, 不得偷跑日线"""
    _setup_basic(mem_db)
    assert check_daily.run_core_checks(
        mem_db, ["capital", "industry", "margin"], DATE, DATE, "", "", []) == []


def test_warn_checks_follow_targets(mem_db):
    """正例: 只选日线只出日线的两项告警核对"""
    _setup_basic(mem_db)
    results = check_daily.run_warn_checks(mem_db, [DATE], DATE, DATE, "", "", [],
                                          targets=["daily"])
    assert [r.label for r in results] == ["日线价量空值", "停牌核对"]


def test_warn_checks_limit_and_volratio(mem_db):
    """正例: limit 含涨停/跌停池核对与涨跌停价; volratio 只含量比空值; basic 不再含涨跌停"""
    _setup_basic(mem_db)
    labels = [r.label for r in check_daily.run_warn_checks(
        mem_db, [DATE], DATE, DATE, "", "", [], targets=["limit"])]
    assert labels == ["涨停核对", "跌停核对", "涨跌停价"]
    labels = [r.label for r in check_daily.run_warn_checks(
        mem_db, [DATE], DATE, DATE, "", "", [], targets=["volratio"])]
    assert labels == ["量比空值"]
    labels = [r.label for r in check_daily.run_warn_checks(
        mem_db, [DATE], DATE, DATE, "", "", [], targets=["basic"])]
    assert labels == ["is_st 空值", "指标空值"]


def test_warn_checks_index_only_has_none(mem_db):
    """反例(边界): index 没有告警类核对项, 只选 index 时结果为空"""
    _setup_basic(mem_db)
    assert check_daily.run_warn_checks(mem_db, [DATE], DATE, DATE, "", "", [],
                                       targets=["index"]) == []


def test_build_summary_without_core_does_not_claim_core_ok():
    """反例(关键): 没跑核心检查时不得说「核心日线数据完整」——没查过不能报 OK"""
    ok = checker.CheckResult(label="行业覆盖", status=checker.STATUS_OK)
    line = check_daily.build_summary(0, [ok], core_checked=False)
    assert "核心日线" not in line
    assert "所选核对项均正常 OK" in line

    bad = checker.CheckResult(label="行业覆盖", status=checker.STATUS_MISMATCH, count=2)
    line = check_daily.build_summary(0, [bad], core_checked=False)
    assert "核心日线" not in line
    assert "OK" not in line
    assert "2 条告警" in line


def test_main_json_with_warn_only_target(mem_db, monkeypatch, capsys):
    """正例: -t industry --json 时 core.checks 为空、params 带 targets、退出码 0"""
    _setup_basic(mem_db)
    _ins_sw_levels(mem_db)
    _ins_industry(mem_db, "600001")
    monkeypatch.setattr(check_daily.myutil, "configure_etl_logging", lambda **k: None)
    monkeypatch.setattr(check_daily, "check_parameters", lambda *a: True)
    monkeypatch.setattr(check_daily.dbutil, "get_connection", lambda: mem_db)
    monkeypatch.setattr(check_daily.dbutil, "get_trade_dates", lambda b, e: ["20240426"])
    monkeypatch.setattr(check_daily, "parse_arguments", lambda: argparse.Namespace(
        begin="20240426", end="20240426", codes=None, exchanges=["all"],
        include_index=False, targets=["industry"], forcerun=False, json=True,
        json_max_detail=check_daily.DEFAULT_JSON_MAX_DETAIL))

    code = check_daily.main()
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["params"]["targets"] == ["industry"]
    assert payload["core"]["checks"] == []
    assert [c["label"] for c in payload["warnings"]["checks"]] == ["行业覆盖"]


# ── 股本资料覆盖 ──────────────────────────────────────────────────────────────

def test_capital_ok_when_all_listed_covered(mem_db):
    """正例: 在市股票都有 CAPITAL_DETAIL 记录 -> ok; 同号指数不被误报"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    insert_stock_info(mem_db, "000300", "SH", "INDEX", "2005-01-01")
    _ins_capital(mem_db, "600001")
    r = check_capital(mem_db, DATE, DATE, "", "", [])
    assert r.status == checker.STATUS_OK
    assert r.count == 0
    assert r.csv_path is None


def test_capital_flags_listed_stock_without_records(mem_db, tmp_path):
    """反例: 在市股票在 CAPITAL_DETAIL 一行都没有 -> 报出并写 CSV"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    insert_stock_info(mem_db, "600002", "SH", "MAIN", "2010-01-01")
    _ins_capital(mem_db, "600001")
    r = check_capital(mem_db, DATE, DATE, "", "", [])
    assert r.status == checker.STATUS_MISMATCH
    assert [row["code"] for row in r.rows] == ["600002.SH"]
    assert r.csv_path and r.csv_path.startswith(str(tmp_path))


def test_capital_ignores_delisted_and_not_yet_listed(mem_db):
    """反例(边界): 已退市、检查结束日之后才上市的股票不在核对范围"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    insert_stock_info(mem_db, "600002", "SH", "MAIN", "2010-01-01",
                      delist_date="2020-01-01", list_status="D")
    insert_stock_info(mem_db, "600003", "SH", "MAIN", "2025-01-01")
    _ins_capital(mem_db, "600001")
    assert check_capital(mem_db, DATE, DATE, "", "", []).status == checker.STATUS_OK


def test_capital_empty_table_is_source_missing(mem_db):
    """反例: 整表为空(sync_capital 未运行) -> source_missing, 不逐只报异常、不当 ok"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    r = check_capital(mem_db, DATE, DATE, "", "", [])
    assert r.status == checker.STATUS_SOURCE_MISSING
    assert r.count == 0


def test_capital_respects_code_filter(mem_db):
    """正例: -c 只核对指定代码"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    insert_stock_info(mem_db, "600002", "SH", "MAIN", "2010-01-01")
    _ins_capital(mem_db, "600001")
    code_filter, code_params = check_daily._build_code_filter(["600001"])
    r = check_capital(mem_db, DATE, DATE, "", code_filter, code_params)
    assert r.status == checker.STATUS_OK


# ── 行业覆盖 ──────────────────────────────────────────────────────────────────

def test_industry_ok_when_mapped(mem_db):
    """正例: 有行业记录且最新一条能映射到一级 -> ok"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    _ins_sw_levels(mem_db)
    _ins_industry(mem_db, "600001")
    r = check_industry(mem_db, DATE, DATE, "", "", [])
    assert r.status == checker.STATUS_OK
    assert r.count == 0


def test_industry_flags_missing_record(mem_db):
    """反例: 在市股票无申万行业记录"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    insert_stock_info(mem_db, "600002", "SH", "MAIN", "2010-01-01")
    _ins_sw_levels(mem_db)
    _ins_industry(mem_db, "600001")
    r = check_industry(mem_db, DATE, DATE, "", "", [])
    assert r.status == checker.STATUS_MISMATCH
    assert r.rows == [{"code": "600002.SH", "name": "Test 600002",
                       "issue": "无申万行业记录"}]


def test_industry_flags_latest_code_unmapped(mem_db):
    """反例: 旧记录能映射但最新一条映射不到一级 -> 按最新一条报"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    _ins_sw_levels(mem_db)
    _ins_industry(mem_db, "600001", "110101", "2022-01-01")
    _ins_industry(mem_db, "600001", "999999", "2023-01-01")
    r = check_industry(mem_db, DATE, DATE, "", "", [])
    assert [row["issue"] for row in r.rows] == ["最新行业无法映射到申万一级"]


def test_industry_empty_table_is_source_missing(mem_db):
    """反例: 原始表为空(sync_industry 未运行) -> source_missing"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2010-01-01")
    r = check_industry(mem_db, DATE, DATE, "", "", [])
    assert r.status == checker.STATUS_SOURCE_MISSING
    assert r.count == 0


# ── 融资融券 ──────────────────────────────────────────────────────────────────

def test_margin_ok_when_both_exchanges_present(mem_db):
    """正例: 每个交易日沪深汇总+明细都在 -> ok"""
    for d in (DATE, DATE2):
        for ex in ("SH", "SZ"):
            _ins_margin(mem_db, d, ex)
    r = check_margin(mem_db, [DATE, DATE2], DATE, DATE2, ["all"], today="2024-05-10")
    assert r.status == checker.STATUS_OK
    assert r.count == 0


def test_margin_flags_missing_summary_and_detail(mem_db, tmp_path):
    """反例: 某日深市汇总缺失、另一日沪市明细缺失, 分别报出"""
    _ins_margin(mem_db, DATE, "SH")
    _ins_margin(mem_db, DATE, "SZ", summary=False)
    _ins_margin(mem_db, DATE2, "SH", detail=False)
    _ins_margin(mem_db, DATE2, "SZ")
    r = check_margin(mem_db, [DATE, DATE2], DATE, DATE2, ["all"], today="2024-05-10")
    assert r.status == checker.STATUS_MISMATCH
    assert r.rows == [
        {"date": DATE, "exchange": "SZ", "issue": "汇总缺失"},
        {"date": DATE2, "exchange": "SH", "issue": "明细缺失"},
    ]
    assert r.csv_path and r.csv_path.startswith(str(tmp_path))


def test_margin_today_not_checked_and_not_ok(mem_db):
    """反例(关键): 检查日当天两融尚未披露 -> 未核对(source_missing), 既不报缺也不当 ok"""
    r = check_margin(mem_db, [DATE], DATE, DATE, ["all"], today=DATE)
    assert r.status == checker.STATUS_SOURCE_MISSING
    assert r.count == 0
    assert r.missing_dates == [DATE]


def test_margin_partial_when_range_includes_today(mem_db):
    """正例: 区间含当天 -> 之前的日期照常核对, 当天记入未核对, 状态 partial"""
    _ins_margin(mem_db, DATE, "SH")
    _ins_margin(mem_db, DATE, "SZ")
    r = check_margin(mem_db, [DATE, DATE2], DATE, DATE2, ["all"], today=DATE2)
    assert r.status == checker.STATUS_PARTIAL
    assert r.missing_dates == [DATE2]
    assert r.count == 0


def test_margin_exchange_filter(mem_db):
    """正例: -x sh 只核对沪市, 深市缺数据不报"""
    _ins_margin(mem_db, DATE, "SH")
    r = check_margin(mem_db, [DATE], DATE, DATE, ["sh"], today="2024-05-10")
    assert r.status == checker.STATUS_OK


def test_margin_bj_only_is_source_missing(mem_db):
    """反例: -x bj 无两融数据源 -> source_missing, 不得判 ok"""
    r = check_margin(mem_db, [DATE], DATE, DATE, ["bj"], today="2024-05-10")
    assert r.status == checker.STATUS_SOURCE_MISSING
    assert r.count == 0


# ── 涨跌停价 ──────────────────────────────────────────────────────────────────

def _ins_traded(conn, code, date, close=10.5, volume=1000.0, tradestatus=1,
                limit_up=11.0, limit_down=9.0, is_up=0, is_down=0,
                volume_ratio=1.0, with_basic=True):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, pre_close, "
        "tradestatus, volume, amount) VALUES (?, ?, 10, 11, 9, ?, 10, ?, ?, 1)",
        [code, date, close, tradestatus, volume])
    if with_basic:
        conn.execute(
            "INSERT INTO DAILY_BASIC (code, trade_date, limit_up, limit_down, "
            "is_limit_up, is_limit_down, volume_ratio) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [code, date, limit_up, limit_down, is_up, is_down, volume_ratio])


def _setup_two_days(conn):
    insert_trade_cal(conn, DATE, 1)
    insert_trade_cal(conn, DATE2, 1)
    insert_stock_info(conn, "600001", "SH", "MAIN", "2010-01-01")


def test_limit_price_ok(mem_db):
    """正例: 普通日 / 收盘涨停且标志为 1 / 无涨跌幅限制哨兵日, 均不报"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE)
    _ins_traded(mem_db, "600001.SH", DATE2, close=11.0, is_up=1)
    insert_stock_info(mem_db, "600002", "SH", "MAIN", "2024-04-26")
    _ins_traded(mem_db, "600002.SH", DATE, close=30.0,
                limit_up=999999.99, limit_down=0.01)
    r = check_limit_price(mem_db, DATE, DATE2, "", "", [])
    assert r.status == checker.STATUS_OK
    assert r.count == 0


def test_limit_price_flags_absent(mem_db, tmp_path):
    """反例: 正常交易日涨跌停价为 NULL / 0 -> 报缺失并写 CSV"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, limit_up=None)
    _ins_traded(mem_db, "600001.SH", DATE2, limit_down=0)
    r = check_limit_price(mem_db, DATE, DATE2, "", "", [])
    assert r.status == checker.STATUS_MISMATCH
    assert [x["issue"] for x in r.rows] == ["涨跌停价缺失(NULL 或 ≤0)"] * 2
    assert r.csv_path and r.csv_path.startswith(str(tmp_path))


def test_limit_price_flags_close_out_of_range(mem_db):
    """反例: 收盘价高于涨停价(创业板被按 10% 算涨停价的情形)"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, close=11.8)
    r = check_limit_price(mem_db, DATE, DATE, "", "", [])
    assert [x["issue"] for x in r.rows] == ["收盘价超出涨跌停区间"]


def test_limit_price_flags_flag_mismatch(mem_db):
    """反例: 收盘等于涨停价但 is_limit_up=0; 哨兵日却标了涨停, 都要报"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, close=11.0, is_up=0)
    _ins_traded(mem_db, "600001.SH", DATE2, close=30.0,
                limit_up=999999.99, limit_down=0.01, is_up=1)
    r = check_limit_price(mem_db, DATE, DATE2, "", "", [])
    assert [x["issue"] for x in r.rows] == ["涨跌停标志与收盘价不一致"] * 2


def test_limit_price_ignores_suspended_and_index(mem_db):
    """反例(边界): 停牌行、指数不参与"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, tradestatus=0, limit_up=None)
    insert_stock_info(mem_db, "000300", "SH", "INDEX", "2005-01-01")
    _ins_traded(mem_db, "000300.SH", DATE, limit_up=None)
    assert check_limit_price(mem_db, DATE, DATE, "", "", []).status == checker.STATUS_OK


# ── 量比空值 ──────────────────────────────────────────────────────────────────

def test_volume_ratio_ok(mem_db):
    """正例: 量比有值; 量比为 0(成交极小取整)也不报"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, volume_ratio=1.2)
    _ins_traded(mem_db, "600001.SH", DATE2, volume_ratio=0.0)
    r = check_volume_ratio(mem_db, DATE, DATE2, "", "", [])
    assert r.status == checker.STATUS_OK
    assert r.count == 0


def test_volume_ratio_first_trading_day_null_not_flagged(mem_db):
    """反例(边界): 新股首日此前无正常交易日, 量比 NULL 是正常的, 不报"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, volume_ratio=None)
    assert check_volume_ratio(mem_db, DATE, DATE, "", "", []).status == checker.STATUS_OK


def test_volume_ratio_null_after_history_flagged(mem_db, tmp_path):
    """反例: 此前有正常交易日、当日量比仍为 NULL -> 报出; 负值也报"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE, volume_ratio=-1.0)
    _ins_traded(mem_db, "600001.SH", DATE2, volume_ratio=None)
    r = check_volume_ratio(mem_db, DATE, DATE2, "", "", [])
    assert r.status == checker.STATUS_MISMATCH
    assert [(x["date"], x["issue"]) for x in r.rows] == [
        (DATE, "量比为负"), (DATE2, "量比为空")]
    assert r.csv_path and r.csv_path.startswith(str(tmp_path))


def test_volume_ratio_ignores_suspended(mem_db):
    """反例(边界): 停牌日不参与, 即使量比为空"""
    _setup_two_days(mem_db)
    _ins_traded(mem_db, "600001.SH", DATE)
    _ins_traded(mem_db, "600001.SH", DATE2, tradestatus=0, volume_ratio=None)
    assert check_volume_ratio(mem_db, DATE, DATE2, "", "", []).status == checker.STATUS_OK
