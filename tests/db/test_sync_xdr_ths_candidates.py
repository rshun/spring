# 修改记录:
#   2026-09-18  Claude  新建: 锁住 _candidate_codes 必须排除指数(裸代码撞名)
"""sync_xdr_ths._candidate_codes: 候选集不得混进指数

CAPITAL_DETAIL 存 6 位裸代码, 而 STOCK_INFO.symbol 并不唯一 —— 沪市指数与深市
股票撞同一裸代码(全库实测 134 组, 如 000001.SH 上证指数 vs 000001.SZ 平安银行)。
候选集若混进指数, 会对它们发注定失败的同花顺接口请求。

目前 XDR_EVENT_THS 里没有指数行, 靠的是同花顺接口对指数返回不了除权数据 ——
那是外部行为, 不该当成我们的保障, 故在 SQL 侧显式排除。
"""
from tests.conftest import insert_stock_info
from etl.sync_xdr_ths import _candidate_codes

BEGIN = "2026-09-18"
END = "2026-09-18"


def _gbbq(conn, symbol, date, category="除权除息"):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share) "
        "VALUES (?, ?, ?, 1.0, 0, 0, 0)", [symbol, date, category])


def test_colliding_symbol_yields_only_the_stock(mem_db):
    """反例(本次加固的回归点): 裸代码撞名时只返回股票, 指数必须排除"""
    insert_stock_info(mem_db, "000963", "SH", "INDEX", "2010-01-01")
    insert_stock_info(mem_db, "000963", "SZ", "MAIN", "2000-01-01")
    _gbbq(mem_db, "000963", BEGIN)

    assert _candidate_codes(mem_db, BEGIN, END) == ["000963.SZ"]


def test_normal_symbol_returned(mem_db):
    """正例: 不撞名的代码照常进入候选集"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    _gbbq(mem_db, "600519", BEGIN)

    assert _candidate_codes(mem_db, BEGIN, END) == ["600519.SH"]


def test_index_only_symbol_yields_empty(mem_db):
    """反例(边界): 裸代码只对应指数时候选集为空, 不得把指数当股票请求"""
    insert_stock_info(mem_db, "000300", "SH", "INDEX", "2005-04-08")
    _gbbq(mem_db, "000300", BEGIN)

    assert _candidate_codes(mem_db, BEGIN, END) == []


def test_out_of_range_date_excluded(mem_db):
    """反例: 区间外的事件不进候选集(增量语义, 见函数 docstring)"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    _gbbq(mem_db, "600519", "2026-09-01")

    assert _candidate_codes(mem_db, BEGIN, END) == []
