# 修改记录:
#   2026-09-13  Claude  新增 num_close 返回 None(一侧缺失)的正反例: 不产生差异行、
#                       不改变 status，但计入聚合 WARNING 的「无法比较」计数
#   2026-09-13  Claude  _run 传给 begin/end 的日期改成 "YYYY-MM-DD"(此前用
#                       "20260911" 紧凑格式), 与实际调用约定(check_daily.py
#                       run_warn_checks 传 YYYY-MM-DD)对齐，防止测试守着错误约定
"""涨跌停一致性核对: 标志集合差 + 涨跌停价 + 抽样数值 + 按 limit_type 判源"""
from tests.conftest import insert_stock_info, insert_trade_cal
from tools.checks.limit_pool import check_limit_pool
from util import checker

DATE = "2026-09-11"


def _setup(conn):
    insert_trade_cal(conn, DATE, 1)
    for sym, ex in [("600001", "SH"), ("600002", "SH"), ("000003", "SZ")]:
        insert_stock_info(conn, sym, ex, "MAIN", "2010-01-01")


def _pool(conn, code, limit_type, close=11.0, turnover_rate=3.5,
          float_mv=5.0e9, total_mv=8.0e9):
    conn.execute(
        "INSERT INTO LIMIT_POOL_DAILY (code, trade_date, limit_type, name, "
        "close, turnover_rate, float_mv, total_mv, source) "
        "VALUES (?, ?, ?, '测试', ?, ?, ?, ?, 'akstock')",
        [code, DATE, limit_type, close, turnover_rate, float_mv, total_mv])


def _basic(conn, code, is_up=0, is_down=0, limit_up=11.0, limit_down=9.0,
           turnover_rate=3.5, float_mv=5.0e9, total_mv=8.0e9):
    conn.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, is_limit_up, is_limit_down, "
        "limit_up, limit_down, turnover_rate, float_mv, total_mv) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [code, DATE, is_up, is_down, limit_up, limit_down,
         turnover_rate, float_mv, total_mv])


def _run(conn, limit_type="U"):
    return check_limit_pool(conn, [DATE], "2026-09-11", "2026-09-11",
                            limit_type, "", "", [])


def test_consistent_produces_no_output(mem_db):
    """正例: 池内涨停、库内 is_limit_up=1、价格一致 -> 无输出"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U", close=11.0)
    _basic(mem_db, "600001.SH", is_up=1, limit_up=11.0)
    result = _run(mem_db)
    assert result.status == checker.STATUS_OK
    assert result.rows == []


def test_db_missing_limit_up_flag(mem_db):
    """反例: 池内涨停但库内 is_limit_up=0 -> 库内漏标"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U")
    _basic(mem_db, "600001.SH", is_up=0)
    result = _run(mem_db)
    issues = [r["issue"] for r in result.rows if r["code"] == "600001.SH"]
    assert "库内漏标涨停" in issues


def test_db_extra_limit_up_flag(mem_db):
    """反例: 库内 is_limit_up=1 但池内无 -> 库内多标"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U")
    _basic(mem_db, "600001.SH", is_up=1, limit_up=11.0)
    _basic(mem_db, "600002.SH", is_up=1, limit_up=22.0)
    result = _run(mem_db)
    issues = [r["issue"] for r in result.rows if r["code"] == "600002.SH"]
    assert "库内多标涨停" in issues


def test_limit_up_price_mismatch(mem_db):
    """反例(高价值): 池内最新价与库内 limit_up 不等 -> update_limit 算错了

    这一组不依赖任何涨跌幅比例假设: 东财称该股今日涨停、收在 11.00,
    则 11.00 就是当日涨停价。
    """
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U", close=11.00)
    _basic(mem_db, "600001.SH", is_up=1, limit_up=10.99)
    result = _run(mem_db)
    issues = [r["issue"] for r in result.rows if r["code"] == "600001.SH"]
    assert any("涨停价不符" in i for i in issues)


def test_limit_down_price_mismatch(mem_db):
    """反例: 跌停方向比 limit_down"""
    _setup(mem_db)
    _pool(mem_db, "000003.SZ", "D", close=9.00)
    _basic(mem_db, "000003.SZ", is_down=1, limit_down=9.01)
    result = _run(mem_db, limit_type="D")
    issues = [r["issue"] for r in result.rows if r["code"] == "000003.SZ"]
    assert any("跌停价不符" in i for i in issues)


def test_sampled_turnover_rate_mismatch(mem_db):
    """反例: 换手率超 1e-3 相对容差 -> 抽样核对报差异"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U", turnover_rate=3.5)
    _basic(mem_db, "600001.SH", is_up=1, turnover_rate=3.9)
    result = _run(mem_db)
    issues = [r["issue"] for r in result.rows if r["code"] == "600001.SH"]
    assert any("turnover_rate" in i for i in issues)


def test_sampled_values_within_tolerance_not_reported(mem_db):
    """正例: 市值在 1e-3 相对容差内 -> 不报"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U", float_mv=5.0e9)
    _basic(mem_db, "600001.SH", is_up=1, float_mv=5.0e9 * (1 + 1e-5))
    result = _run(mem_db)
    assert not any("float_mv" in r["issue"] for r in result.rows)


def test_source_missing_for_down_when_only_up_present(mem_db):
    """反例(最关键): 当日只有涨停行、无跌停行

    跌停方向必须判 source_missing, 绝不能把库内每条 is_limit_down=1
    都报成「库内多标跌停」。这正是按整表判源会造成的整片误报。
    """
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U")
    _basic(mem_db, "000003.SZ", is_down=1, limit_down=9.0)
    result = _run(mem_db, limit_type="D")
    assert result.status == checker.STATUS_SOURCE_MISSING
    assert result.rows == []
    assert result.missing_dates == [DATE]


def test_up_direction_still_checked_when_down_missing(mem_db):
    """正例: 跌停缺数据不影响涨停方向照常核对"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U", close=11.0)
    _basic(mem_db, "600001.SH", is_up=1, limit_up=11.0)
    result = _run(mem_db, limit_type="U")
    assert result.status == checker.STATUS_OK


def test_null_field_uncomputable_not_reported_but_counted(mem_db, caplog):
    """反例: 池内 turnover_rate 为 NULL(一侧缺失, 无法比较) -> 不产生差异行、
    不影响 status, 但会被计入「无法比较」的聚合计数(一条 WARNING, 不逐条输出)"""
    _setup(mem_db)
    mem_db.execute(
        "INSERT INTO LIMIT_POOL_DAILY (code, trade_date, limit_type, name, "
        "close, turnover_rate, float_mv, total_mv, source) "
        "VALUES (?, ?, 'U', '测试', ?, NULL, ?, ?, 'akstock')",
        ["600001.SH", DATE, 11.0, 5.0e9, 8.0e9])
    _basic(mem_db, "600001.SH", is_up=1, limit_up=11.0)
    with caplog.at_level("WARNING"):
        result = _run(mem_db)
    assert result.rows == []
    assert result.count == 0
    assert result.status == checker.STATUS_OK
    assert "1 个数值" in caplog.text
    assert "无法比较" in caplog.text
