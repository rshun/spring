"""停牌一致性核对: 四种情形 + source_missing"""
from tests.conftest import insert_stock_info, insert_trade_cal
from tools.checks.suspension import check_suspension
from util import checker

DATE = "2024-04-26"
NO_EX_FILTER = ""
NO_CODE_FILTER = ""


def _setup(conn):
    insert_trade_cal(conn, DATE, 1)
    for sym, ex in [("600001", "SH"), ("600002", "SH"), ("000003", "SZ"),
                    ("000004", "SZ")]:
        insert_stock_info(conn, sym, ex, "MAIN", "2010-01-01")


def _daily(conn, code, tradestatus):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, close, tradestatus) "
        "VALUES (?, ?, 10.0, ?)", [code, DATE, tradestatus])


def _susp(conn, code):
    conn.execute(
        "INSERT INTO SUSPENSION_DAILY (code, trade_date, name, source) "
        "VALUES (?, ?, '测试', 'akstock')", [code, DATE])


def _run(conn):
    return check_suspension(conn, [DATE], "20240426", "20240426",
                            NO_EX_FILTER, NO_CODE_FILTER, [])


def test_consistent_rows_produce_no_output(mem_db):
    """正例: 外部停牌 + 库内 tradestatus=0 -> 一致, 不输出任何行"""
    _setup(mem_db)
    _susp(mem_db, "600001.SH")
    _daily(mem_db, "600001.SH", 0)
    result = _run(mem_db)
    assert result.status == checker.STATUS_OK
    assert result.count == 0
    assert result.rows == []


def test_external_suspended_but_db_says_trading(mem_db):
    """反例: 外部停牌但库内 tradestatus=1 -> 高严重度差异"""
    _setup(mem_db)
    _susp(mem_db, "600001.SH")
    _daily(mem_db, "600001.SH", 1)
    result = _run(mem_db)
    assert result.status == checker.STATUS_MISMATCH
    issues = {r["code"]: r["issue"] for r in result.rows}
    assert issues["600001.SH"] == "外部停牌但库内标为正常交易(tradestatus=1)"


def test_external_suspended_but_row_absent(mem_db):
    """反例: 外部停牌但库内无该行 -> 必须单列, 不与上一类混淆

    baostock 对长期停牌股本来就可能不给行, 属数据源特性而非 bug,
    混进「不一致」会天天误报。
    """
    _setup(mem_db)
    _susp(mem_db, "000003.SZ")
    result = _run(mem_db)
    issues = {r["code"]: r["issue"] for r in result.rows}
    assert issues["000003.SZ"] == "外部停牌但库内无该行"


def test_db_suspended_but_not_in_external_list(mem_db):
    """反例: 库内标停牌但外部名单无此股 -> 库内多标"""
    _setup(mem_db)
    _susp(mem_db, "600001.SH")
    _daily(mem_db, "600001.SH", 0)
    _daily(mem_db, "600002.SH", 0)
    result = _run(mem_db)
    issues = {r["code"]: r["issue"] for r in result.rows}
    assert issues["600002.SH"] == "库内标停牌但外部名单无此股"


def test_normal_trading_stock_not_reported(mem_db):
    """正例: 两边都认为正常交易的股票不应出现在结果里"""
    _setup(mem_db)
    _daily(mem_db, "600002.SH", 1)
    result = _run(mem_db)
    assert result.count == 0


def test_source_missing_when_table_empty(mem_db):
    """反例(最关键): SUSPENSION_DAILY 当日 0 行 -> source_missing

    此时库内即使有 tradestatus=0 的行也绝不能报「库内多标停牌」,
    那是整片误报。
    """
    _setup(mem_db)
    _daily(mem_db, "600001.SH", 0)
    result = _run(mem_db)
    assert result.status == checker.STATUS_SOURCE_MISSING
    assert result.count == 0
    assert result.rows == []
    assert result.missing_dates == [DATE]


def test_partial_when_one_of_two_dates_missing(mem_db):
    """反例: 两天里缺一天 -> partial, 有数据那天照常核对"""
    prev = "2024-04-25"
    _setup(mem_db)
    insert_trade_cal(mem_db, prev, 1)
    _susp(mem_db, "600001.SH")
    _daily(mem_db, "600001.SH", 1)
    result = check_suspension(mem_db, [prev, DATE], "20240425", "20240426",
                              NO_EX_FILTER, NO_CODE_FILTER, [])
    assert result.status == checker.STATUS_PARTIAL
    assert result.missing_dates == [prev]
    assert result.count == 1


def test_external_suspended_but_status_unknown(mem_db):
    """反例: 外部停牌但库内 tradestatus=-1(暂时没有值) -> 必须报出

    绝不能因为落不到任何 CASE 分支而被静默当成一致——
    把「未知」当「一致」正是本项目核对设计要防的那类 bug。
    """
    _setup(mem_db)
    _susp(mem_db, "600001.SH")
    _daily(mem_db, "600001.SH", -1)
    result = _run(mem_db)
    assert result.status == checker.STATUS_MISMATCH
    issues = {r["code"]: r["issue"] for r in result.rows}
    assert issues["600001.SH"] == "外部停牌但库内 tradestatus 未知(-1)"


def test_code_filter_binds_params_correctly(mem_db):
    """正例(守参数绑定): 带 -c 过滤时占位符与参数个数必须匹配

    code_filter 的占位符只在 universe CTE 定义处出现一次, CTE 被引用多次
    不会重复绑定参数。若参数多传一份, 这里会抛「参数个数不匹配」。
    """
    _setup(mem_db)
    _susp(mem_db, "600001.SH")
    _daily(mem_db, "600001.SH", 1)
    _susp(mem_db, "600002.SH")
    _daily(mem_db, "600002.SH", 1)
    result = check_suspension(mem_db, [DATE], "20240426", "20240426",
                              "", "AND i.symbol IN (?)", ["600001"])
    codes = {r["code"] for r in result.rows}
    assert codes == {"600001.SH"}


def test_code_filter_with_multiple_placeholders(mem_db):
    """正例: 多个占位符同样要能正确绑定(两个 ? 对应两个参数)"""
    _setup(mem_db)
    for code in ("600001.SH", "600002.SH", "000003.SZ"):
        _susp(mem_db, code)
        _daily(mem_db, code, 1)
    result = check_suspension(mem_db, [DATE], "20240426", "20240426",
                              "", "AND i.symbol IN (?, ?)", ["600001", "000003"])
    codes = {r["code"] for r in result.rows}
    assert codes == {"600001.SH", "000003.SZ"}
