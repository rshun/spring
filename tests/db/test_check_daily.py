import pytest

from tools.check_daily import _query_xdr_preclose_mismatches, _count_xdr_uncomputable, _check_xdr_preclose
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
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share, updated_at) "
        "VALUES (?, ?, '除权除息', ?, ?, ?, ?, now())",
        [code, date, dividend, allotment_price, bonus_share, allotment_share],
    )


def test_mismatch_detected_with_bonus_and_dividend(mem_db):
    """送股+分红:theory=(close_prev - div/10 + 0)/(1+bonus/10).
    close_prev=11, dividend=5(每10股), bonus_share=10(每10股) →
    theory=(11-0.5)/(1+1)=5.25。pre_close=11 与 theory 差远 → 命中。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-01-04", "2023-01-05"])
    _ins_daily(mem_db, "600519.SH", "2023-01-04", close=11.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-01-05", close=5.2, pre_close=11.0)
    _ins_xdr(mem_db, "600519.SH", "2023-01-05", dividend=5, bonus_share=10)

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
    pre_close=9.92 → diff=0.02 报。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-02-01", "2023-02-02"])
    _ins_daily(mem_db, "600519.SH", "2023-02-01", close=10.0, pre_close=10.0)

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
    """除权日停牌(tradestatus=0)不参与校验。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-03-01", "2023-03-02"])
    _ins_daily(mem_db, "600519.SH", "2023-03-01", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-03-02", close=10.0,
               pre_close=10.0, tradestatus=0)
    _ins_xdr(mem_db, "600519.SH", "2023-03-02", dividend=20)  # theory≠10
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-03-02", "2023-03-02", "", "", []
    )
    assert rows == []


def test_prev_close_missing_not_returned(mem_db):
    """上一交易日无收盘记录 → 无法计算,不在不一致结果中返回。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-04-03", "2023-04-04"])
    # 不写 04-03 的 STOCK_DAILY(close_prev 缺失)
    _ins_daily(mem_db, "600519.SH", "2023-04-04", close=9.0, pre_close=9.0)
    _ins_xdr(mem_db, "600519.SH", "2023-04-04", dividend=20)
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-04-04", "2023-04-04", "", "", []
    )
    assert rows == []


def test_count_uncomputable_counts_missing_prev_close(mem_db):
    """除权且非停牌、但上一交易日收盘缺失 → 计入"无法计算"计数。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-04-03", "2023-04-04"])
    _ins_daily(mem_db, "600519.SH", "2023-04-04", close=9.0, pre_close=9.0)
    _ins_xdr(mem_db, "600519.SH", "2023-04-04", dividend=20)
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
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-07-04", "2023-07-04", "", "", []
    )
    assert len(rows) == 1
    assert abs(rows[0][5] - (12.5 / 1.5)) < 1e-9


def test_check_xdr_preclose_wrapper_writes_csv_and_returns_count(mem_db, tmp_path, monkeypatch):
    """包装函数:有不一致 → 返回条数并写 CSV;表头与精度正确。"""
    import tools.check_daily as cd

    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-08-01", "2023-08-02"])
    _ins_daily(mem_db, "600519.SH", "2023-08-01", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-08-02", close=5.0, pre_close=10.0)
    _ins_xdr(mem_db, "600519.SH", "2023-08-02", bonus_share=10)

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
    """包装函数:无不一致且无无法计算 → 返回 0。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-09-01", "2023-09-04"])
    _ins_daily(mem_db, "600519.SH", "2023-09-01", close=10.0, pre_close=10.0)
    # 除权日 pre_close 恰为理论价(纯送股 theory=5.0)
    _ins_daily(mem_db, "600519.SH", "2023-09-04", close=5.0, pre_close=5.0)
    _ins_xdr(mem_db, "600519.SH", "2023-09-04", bonus_share=10)
    n = _check_xdr_preclose(mem_db, "2023-09-04", "2023-09-04", "", "", [])
    assert n == 0
