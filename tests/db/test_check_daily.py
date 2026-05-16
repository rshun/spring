import pytest

from tools.check_daily import (
    _query_xdr_preclose_mismatches,
    _count_xdr_uncomputable,
    _check_xdr_preclose,
    _check_daily_basic_nulls,
)
from tests.conftest import insert_stock_info, insert_trade_cal


def _seed_stock(conn, symbol="600519", exchange="SH", board="MAIN"):
    insert_stock_info(conn, symbol, exchange, board, "2020-01-01")


def _ins_cal(conn, dates):
    for d in dates:
        insert_trade_cal(conn, d, 1)


def _ins_daily(conn, code, date, close=None, pre_close=None, tradestatus=1):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, 0, 0, 0, ?, ?, ?, 0, 0)",
        [code, date, close, pre_close, tradestatus],
    )


def _ins_xdr(conn, code, date, dividend=0, allotment_price=0,
             bonus_share=0, allotment_share=0):
    # 生产数据中 CAPITAL_DETAIL.code 是不带交易所后缀的裸 symbol
    # (来自通达信 gbbq),与 STOCK_INFO.code/STOCK_DAILY.code 的带后缀形式不同。
    # fixture 必须复现这一点,否则会掩盖 symbol/code join 失配的 bug。
    symbol = str(code).split(".")[0]
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share, updated_at) "
        "VALUES (?, ?, '除权除息', ?, ?, ?, ?, now())",
        [symbol, date, dividend, allotment_price, bonus_share, allotment_share],
    )


def _ins_adj(conn, code, date, factor):
    conn.execute(
        "INSERT INTO ADJ_FACTOR (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, updated_at) VALUES (?, ?, ?, ?, ?, now())",
        [code, date, factor, factor, factor],
    )


def _ins_basic(conn, code, trade_date, pb=1.0, pe=10.0,
               total_shares=100000000, float_shares=80000000):
    conn.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, pb, pe, "
        "total_shares, float_shares) VALUES (?, ?, ?, ?, ?, ?)",
        [code, trade_date, pb, pe, total_shares, float_shares],
    )


def _real_xdr(conn, code, prev_date, xdr_date):
    """标记一次"真实除权": ADJ_FACTOR 在 prev_date→xdr_date 之间发生变化。
    没有这一变化,新版校验会判定当天并未真实除权而跳过(防误报闸门)。"""
    _ins_adj(conn, code, prev_date, 1.0)
    _ins_adj(conn, code, xdr_date, 1.1)


def test_mismatch_detected_with_bonus_and_dividend(mem_db):
    """送股+分红:theory=(close_prev - div/10 + 0)/(1+bonus/10).
    close_prev=11, dividend=5(每10股), bonus_share=10(每10股) →
    theory=(11-0.5)/(1+1)=5.25。pre_close=11 与 theory 差远 → 命中。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-01-04", "2023-01-05"])
    _ins_daily(mem_db, "600519.SH", "2023-01-04", close=11.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-01-05", close=5.2, pre_close=11.0)
    _ins_xdr(mem_db, "600519.SH", "2023-01-05", dividend=5, bonus_share=10)
    _real_xdr(mem_db, "600519.SH", "2023-01-04", "2023-01-05")

    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-01-05", "2023-01-05", "", "", []
    )
    assert len(rows) == 1
    xdr_date, code, name, close_prev, pre_close, theory = rows[0]
    assert code == "600519.SH"
    assert close_prev == 11.0
    assert pre_close == 11.0
    assert abs(theory - 5.25) < 1e-9


def test_tolerance_boundary(mem_db):
    """纯现金分红场景:close_prev=10, dividend=1(每10股=0.1/股) →
    theory=(10-0.1)/1=9.9。pre_close=9.91 → diff=0.01 不报;
    pre_close=9.92 → diff=0.02 报。(均为真实除权)"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-02-01", "2023-02-02"])
    _ins_daily(mem_db, "600519.SH", "2023-02-01", close=10.0, pre_close=10.0)
    _real_xdr(mem_db, "600519.SH", "2023-02-01", "2023-02-02")

    # 边界内:diff = 0.01,不报
    _ins_daily(mem_db, "600519.SH", "2023-02-02", close=9.9, pre_close=9.91)
    _ins_xdr(mem_db, "600519.SH", "2023-02-02", dividend=1)
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-02-02", "2023-02-02", "", "", []
    )
    assert rows == []

    # 改成 diff = 0.02,应报
    mem_db.execute(
        "UPDATE STOCK_DAILY SET pre_close = 9.92 "
        "WHERE code = '600519.SH' AND date = '2023-02-02'"
    )
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-02-02", "2023-02-02", "", "", []
    )
    assert len(rows) == 1


def test_suspended_skipped(mem_db):
    """除权日停牌(tradestatus=0)不参与校验(即便是真实除权)。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-03-01", "2023-03-02"])
    _ins_daily(mem_db, "600519.SH", "2023-03-01", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-03-02", close=10.0,
               pre_close=10.0, tradestatus=0)
    _ins_xdr(mem_db, "600519.SH", "2023-03-02", dividend=20)  # theory≠10
    _real_xdr(mem_db, "600519.SH", "2023-03-01", "2023-03-02")
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-03-02", "2023-03-02", "", "", []
    )
    assert rows == []


def test_prev_close_missing_not_returned(mem_db):
    """真实除权但上一交易日无收盘记录 → 无法计算,不在不一致结果中返回。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-04-03", "2023-04-04"])
    # 不写 04-03 的 STOCK_DAILY(close_prev 缺失);ADJ_FACTOR 仍齐全
    _ins_daily(mem_db, "600519.SH", "2023-04-04", close=9.0, pre_close=9.0)
    _ins_xdr(mem_db, "600519.SH", "2023-04-04", dividend=20)
    _real_xdr(mem_db, "600519.SH", "2023-04-03", "2023-04-04")
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-04-04", "2023-04-04", "", "", []
    )
    assert rows == []


def test_count_uncomputable_counts_missing_prev_close(mem_db):
    """真实除权、非停牌、但上一交易日收盘缺失 → 计入"无法计算"计数。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-04-03", "2023-04-04"])
    _ins_daily(mem_db, "600519.SH", "2023-04-04", close=9.0, pre_close=9.0)
    _ins_xdr(mem_db, "600519.SH", "2023-04-04", dividend=20)
    _real_xdr(mem_db, "600519.SH", "2023-04-03", "2023-04-04")
    n = _count_xdr_uncomputable(
        mem_db, "2023-04-04", "2023-04-04", "", "", []
    )
    assert n == 1


def test_count_uncomputable_excludes_suspended(mem_db):
    """除权日停牌(tradestatus=0)即便上一交易日收盘缺失,也不计入无法计算数。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-05-08", "2023-05-09"])
    # 不写 05-08 的 STOCK_DAILY;05-09 停牌
    _ins_daily(mem_db, "600519.SH", "2023-05-09", close=9.0,
               pre_close=9.0, tradestatus=0)
    _ins_xdr(mem_db, "600519.SH", "2023-05-09", dividend=20)
    _real_xdr(mem_db, "600519.SH", "2023-05-08", "2023-05-09")
    n = _count_xdr_uncomputable(
        mem_db, "2023-05-09", "2023-05-09", "", "", []
    )
    assert n == 0


def test_pure_bonus_share_formula(mem_db):
    """纯送股:close_prev=10, bonus_share=10(每10股送10) →
    theory=10/(1+1)=5.0。pre_close=10 → 命中,theory=5.0。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-06-01", "2023-06-02"])
    _ins_daily(mem_db, "600519.SH", "2023-06-01", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-06-02", close=5.0, pre_close=10.0)
    _ins_xdr(mem_db, "600519.SH", "2023-06-02", bonus_share=10)
    _real_xdr(mem_db, "600519.SH", "2023-06-01", "2023-06-02")
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-06-02", "2023-06-02", "", "", []
    )
    assert len(rows) == 1
    assert abs(rows[0][5] - 5.0) < 1e-9


def test_allotment_formula(mem_db):
    """含配股:close_prev=10, allotment_price=5, allotment_share=5(每10股配5) →
    theory=(10 + 5*0.5)/(1+0.5)=12.5/1.5≈8.3333。pre_close=10 → 命中。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-07-03", "2023-07-04"])
    _ins_daily(mem_db, "600519.SH", "2023-07-03", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-07-04", close=8.3, pre_close=10.0)
    _ins_xdr(mem_db, "600519.SH", "2023-07-04",
             allotment_price=5, allotment_share=5)
    _real_xdr(mem_db, "600519.SH", "2023-07-03", "2023-07-04")
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-07-04", "2023-07-04", "", "", []
    )
    assert len(rows) == 1
    assert abs(rows[0][5] - (12.5 / 1.5)) < 1e-9


def test_regression_002763_capital_detail_uses_bare_symbol(mem_db):
    """回归: CAPITAL_DETAIL.code 为裸 symbol(002763),STOCK_INFO/STOCK_DAILY
    为带后缀(002763.SZ)。真实除权(ADJ_FACTOR 变化)但 pre_close 仍等于前收
    (9.87)而非除权理论价,必须被检出,且返回带后缀的规范代码。
    现实案例: 002763 2026-05-15 每10股分红 8.0 → theory=9.87-0.8=9.07。"""
    _seed_stock(mem_db, symbol="002763", exchange="SZ")
    _ins_cal(mem_db, ["2026-05-14", "2026-05-15"])
    _ins_daily(mem_db, "002763.SZ", "2026-05-14", close=9.87, pre_close=9.95)
    _ins_daily(mem_db, "002763.SZ", "2026-05-15", close=8.22, pre_close=9.87)
    _ins_xdr(mem_db, "002763.SZ", "2026-05-15", dividend=8.0)
    _real_xdr(mem_db, "002763.SZ", "2026-05-14", "2026-05-15")

    rows = _query_xdr_preclose_mismatches(
        mem_db, "2026-05-15", "2026-05-15", "", "", []
    )
    assert len(rows) == 1
    xdr_date, code, name, close_prev, pre_close, theory = rows[0]
    assert code == "002763.SZ"          # 输出带后缀的规范代码
    assert close_prev == 9.87
    assert pre_close == 9.87
    assert abs(theory - 9.07) < 1e-9


def test_regression_300174_factor_unchanged_not_flagged(mem_db):
    """回归(误报修复): CAPITAL_DETAIL 有除权除息记录,但 ADJ_FACTOR 因子
    在 prev_date→xdr_date 之间未变化 → 当天并未真实除权,pre_close 等于前收
    是正常的,不得判异常。
    现实案例: 300174 2026-05-15 gbbq 有记录但 adjust_factor 全程 1.0。"""
    _seed_stock(mem_db, symbol="300174", exchange="SZ", board="GEM")
    _ins_cal(mem_db, ["2026-05-14", "2026-05-15"])
    _ins_daily(mem_db, "300174.SZ", "2026-05-14", close=17.90, pre_close=17.89)
    _ins_daily(mem_db, "300174.SZ", "2026-05-15", close=17.89, pre_close=17.90)
    _ins_xdr(mem_db, "300174.SZ", "2026-05-15", dividend=1.0)  # theory=17.8
    # ADJ_FACTOR 未变化(非真实除权)
    _ins_adj(mem_db, "300174.SZ", "2026-05-14", 1.0)
    _ins_adj(mem_db, "300174.SZ", "2026-05-15", 1.0)

    rows = _query_xdr_preclose_mismatches(
        mem_db, "2026-05-15", "2026-05-15", "", "", []
    )
    assert rows == []
    n = _count_xdr_uncomputable(
        mem_db, "2026-05-15", "2026-05-15", "", "", []
    )
    assert n == 0


def test_adj_factor_missing_not_flagged(mem_db):
    """ADJ_FACTOR 缺失 → 无法确认是否真实除权,不判异常(避免误报)。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-10-09", "2023-10-10"])
    _ins_daily(mem_db, "600519.SH", "2023-10-09", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-10-10", close=9.0, pre_close=10.0)
    _ins_xdr(mem_db, "600519.SH", "2023-10-10", dividend=20)  # theory≠10
    # 不写任何 ADJ_FACTOR
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-10-10", "2023-10-10", "", "", []
    )
    assert rows == []


def test_check_xdr_preclose_wrapper_writes_csv_and_returns_count(mem_db, tmp_path, monkeypatch):
    """包装函数:有不一致 → 返回条数并写 CSV;表头与精度正确。"""
    import tools.check_daily as cd

    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-08-01", "2023-08-02"])
    _ins_daily(mem_db, "600519.SH", "2023-08-01", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-08-02", close=5.0, pre_close=10.0)
    _ins_xdr(mem_db, "600519.SH", "2023-08-02", bonus_share=10)
    _real_xdr(mem_db, "600519.SH", "2023-08-01", "2023-08-02")

    # 把 csv 目录重定向到 tmp_path,避免污染项目 csv/
    fake_file = tmp_path / "csv" / "x"
    monkeypatch.setattr(cd, "__file__", str(fake_file))

    n = _check_xdr_preclose(mem_db, "2023-08-02", "2023-08-02", "", "", [])
    assert n == 1

    out = tmp_path / "csv" / "check_preclose_xdr_2023-08-02_2023-08-02.csv"
    assert out.exists()
    content = out.read_text(encoding="utf-8-sig").splitlines()
    assert content[0] == "date,code,name,close_prev,pre_close,theory_preclose,diff"
    assert "600519.SH" in content[1]


def test_check_xdr_preclose_wrapper_clean_returns_zero(mem_db):
    """包装函数:真实除权且 pre_close 恰为理论价 → 返回 0。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-09-01", "2023-09-04"])
    _ins_daily(mem_db, "600519.SH", "2023-09-01", close=10.0, pre_close=10.0)
    # 除权日 pre_close 恰为理论价(纯送股 theory=5.0)
    _ins_daily(mem_db, "600519.SH", "2023-09-04", close=5.0, pre_close=5.0)
    _ins_xdr(mem_db, "600519.SH", "2023-09-04", bonus_share=10)
    _real_xdr(mem_db, "600519.SH", "2023-09-01", "2023-09-04")
    n = _check_xdr_preclose(mem_db, "2023-09-04", "2023-09-04", "", "", [])
    assert n == 0


# ── DAILY_BASIC pb/pe/total_shares/float_shares 缺失检查 ──────────────────
# 缺失口径 = NULL 或 = 0;负值视为有值不报。
# pb/total_shares/float_shares 缺失=异常;pe 缺失仅单独告警不计入。

def test_dbnull_all_present_ok(mem_db):
    """四字段都有正常值 → 返回 0,且不发 pe 告警。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-01"])
    _ins_basic(mem_db, "600519.SH", "2023-11-01")
    n = _check_daily_basic_nulls(mem_db, "2023-11-01", "2023-11-01", "", "", [])
    assert n == 0


def test_dbnull_pe_null_only_warned_not_counted(mem_db, caplog):
    """仅 pe 为 NULL → 不计异常(返回0),但有 pe 缺失告警。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-02"])
    _ins_basic(mem_db, "600519.SH", "2023-11-02", pe=None)
    with caplog.at_level("WARNING"):
        n = _check_daily_basic_nulls(mem_db, "2023-11-02", "2023-11-02", "", "", [])
    assert n == 0
    assert "pe 缺失" in caplog.text


def test_dbnull_pe_zero_only_warned_not_counted(mem_db, caplog):
    """pe = 0 视为缺失 → 不计异常,但有 pe 缺失告警。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-03"])
    _ins_basic(mem_db, "600519.SH", "2023-11-03", pe=0)
    with caplog.at_level("WARNING"):
        n = _check_daily_basic_nulls(mem_db, "2023-11-03", "2023-11-03", "", "", [])
    assert n == 0
    assert "pe 缺失" in caplog.text


def test_dbnull_pe_negative_is_valid_no_warn(mem_db, caplog):
    """pe < 0(亏损股)视为有值 → 不计异常,也不发 pe 缺失告警。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-04"])
    _ins_basic(mem_db, "600519.SH", "2023-11-04", pe=-12.3)
    with caplog.at_level("WARNING"):
        n = _check_daily_basic_nulls(mem_db, "2023-11-04", "2023-11-04", "", "", [])
    assert n == 0
    assert "pe 缺失" not in caplog.text


def test_dbnull_pb_null_counted(mem_db):
    """pb 为 NULL → 异常,返回 1。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-05"])
    _ins_basic(mem_db, "600519.SH", "2023-11-05", pb=None)
    n = _check_daily_basic_nulls(mem_db, "2023-11-05", "2023-11-05", "", "", [])
    assert n == 1


def test_dbnull_pb_zero_counted(mem_db):
    """pb = 0 视为缺失 → 异常,返回 1。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-06"])
    _ins_basic(mem_db, "600519.SH", "2023-11-06", pb=0)
    n = _check_daily_basic_nulls(mem_db, "2023-11-06", "2023-11-06", "", "", [])
    assert n == 1


def test_dbnull_pb_negative_is_valid_not_counted(mem_db):
    """pb < 0(负净资产)视为有值 → 不计异常,返回 0。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-07"])
    _ins_basic(mem_db, "600519.SH", "2023-11-07", pb=-1.5)
    n = _check_daily_basic_nulls(mem_db, "2023-11-07", "2023-11-07", "", "", [])
    assert n == 0


def test_dbnull_total_shares_zero_counted(mem_db):
    """total_shares = 0 视为缺失 → 异常。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-08"])
    _ins_basic(mem_db, "600519.SH", "2023-11-08", total_shares=0)
    n = _check_daily_basic_nulls(mem_db, "2023-11-08", "2023-11-08", "", "", [])
    assert n == 1


def test_dbnull_missing_list_excludes_pe_includes_zero(mem_db):
    """pb=NULL 且 float_shares=0 且 pe=0 → missing='pb,float_shares'(不含 pe),计 1。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-09"])
    _ins_basic(mem_db, "600519.SH", "2023-11-09",
               pb=None, float_shares=0, pe=0)
    missing = mem_db.execute(
        "SELECT concat_ws(','," 
        " CASE WHEN pb IS NULL OR pb=0 THEN 'pb' END,"
        " CASE WHEN total_shares IS NULL OR total_shares=0 THEN 'total_shares' END,"
        " CASE WHEN float_shares IS NULL OR float_shares=0 THEN 'float_shares' END)"
        " FROM DAILY_BASIC WHERE code='600519.SH'"
    ).fetchone()[0]
    assert missing == "pb,float_shares"
    n = _check_daily_basic_nulls(mem_db, "2023-11-09", "2023-11-09", "", "", [])
    assert n == 1


def test_dbnull_suspended_excluded(mem_db):
    """停牌(tradestatus=0)当日即便缺失也不计入。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-11-10"])
    _ins_daily(mem_db, "600519.SH", "2023-11-10", close=10.0,
               pre_close=10.0, tradestatus=0)
    _ins_basic(mem_db, "600519.SH", "2023-11-10", total_shares=0)
    n = _check_daily_basic_nulls(mem_db, "2023-11-10", "2023-11-10", "", "", [])
    assert n == 0
