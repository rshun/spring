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


def _st_stock(conn, symbol="600009", exchange="SH", name="*ST测试"):
    """插入一只名称带 ST 前缀的股票(is_st 字段留空, 验证名称兜底)"""
    code = f"{symbol}.{exchange}"
    conn.execute(
        "INSERT INTO STOCK_INFO (code, symbol, name, exchange, board, "
        "list_date, list_status, created_at, last_updated_at) "
        "VALUES (?, ?, ?, ?, 'MAIN', '2010-01-01', 'L', now(), now())",
        [code, symbol, name, exchange])
    return code


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


def test_st_extra_flag_not_counted_as_diff(mem_db):
    """正例: ST 股库内已标涨停而外部池未收 -> 不计入差异, 但仍写进 CSV。

    akshare 涨跌停池(东财口径)不收 ST 股, 两侧股票池范围不同, 不是数据错。
    实测 2026-09-14 池内 55 涨/16 跌与库内非 ST 的 55/16 完全相等, 而库内另有
    4 只 ST 池内一只不收 —— 每个交易日都会出现, 若计入差异会天天刷假告警。
    """
    _setup(mem_db)
    st = _st_stock(mem_db)
    _pool(mem_db, "600001.SH", "U")
    _basic(mem_db, "600001.SH", is_up=1)
    _basic(mem_db, st, is_up=1)                 # ST 标了涨停, 池内没有
    result = _run(mem_db)
    assert result.count == 0, "ST 多标不得计入差异"
    assert result.status == checker.STATUS_OK


def test_non_st_extra_flag_still_counted(mem_db):
    """反例: 非 ST 股的「库内多标」照旧计入差异 —— 不能连它一起放过"""
    _setup(mem_db)
    _pool(mem_db, "600001.SH", "U")
    _basic(mem_db, "600001.SH", is_up=1)
    _basic(mem_db, "600002.SH", is_up=1)        # 非 ST 多标
    result = _run(mem_db)
    assert result.count == 1
    assert "库内多标涨停" == result.rows[0]["issue"]


def test_st_missing_flag_still_counted(mem_db):
    """反例(防盲区): 池内有 ST 而库内未标 -> 仍须计入差异。

    只放过「多标」一侧。若池子口径将来变了收了 ST, 漏标必须还能查出来 ——
    这正是不把 ST 整体排除出比对的原因。
    """
    _setup(mem_db)
    st = _st_stock(mem_db)
    _pool(mem_db, st, "U")                      # 池内收了这只 ST
    _basic(mem_db, st, is_up=0)                 # 库内没标
    result = _run(mem_db)
    assert result.count == 1
    assert "库内漏标涨停" == result.rows[0]["issue"]


def test_st_detected_by_is_st_field(mem_db):
    """正例: is_st=1 但名称不含 ST 的也算 ST(字段与名称互为兜底)"""
    _setup(mem_db)
    mem_db.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, is_limit_up, is_st) "
        "VALUES ('600002.SH', ?, 1, 1)", [DATE])
    _pool(mem_db, "600001.SH", "U")
    _basic(mem_db, "600001.SH", is_up=1)
    assert _run(mem_db).count == 0



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
