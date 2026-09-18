# 修改记录:
#   2026-09-18  Claude  新建: 锁住 _load_gbbq_events 必须排除指数(裸代码撞名)
"""check_adjust._load_gbbq_events: 裸代码撞名不得把股票事件挂到指数上

CAPITAL_DETAIL 存 6 位裸代码, 而 STOCK_INFO.symbol 并不唯一 —— 沪市指数与深市
股票会撞同一裸代码(全库实测 134 组, 如 000001.SH 上证指数 vs 000001.SZ 平安银行)。
`INNER JOIN STOCK_INFO i ON i.symbol = c.code` 不加 board 过滤时, 一条深市股票的
除权事件会同时匹配到指数, 使该指数被报成「腾讯缺事件」。

实测 2026-09-18: 华东医药(000963.SZ)分红 3.5/10 的事件被挂到
中证下游消费与服务产业指数(000963.SH)上并报成差异, 而真正的 000963.SZ
反倒没进比对。全历史共 2,414 条事件会这样落到指数上。
"""
import pytest

from tests.conftest import insert_stock_info
from tools.check_adjust import _load_gbbq_events

DATE = "2026-09-18"


def _gbbq(conn, symbol, date, dividend, category="除权除息"):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share) "
        "VALUES (?, ?, ?, ?, 0, 0, 0)", [symbol, date, category, dividend])


def _load(conn):
    return _load_gbbq_events(conn, DATE, DATE)


def test_colliding_symbol_maps_only_to_the_stock(mem_db):
    """反例(本次修复的回归点): 裸代码同时对应指数与股票时, 事件只能落到股票上。

    不加 board 过滤会返回两行, 其中指数那行是凭空捏造的事件。
    """
    insert_stock_info(mem_db, "000963", "SH", "INDEX", "2010-01-01")
    insert_stock_info(mem_db, "000963", "SZ", "MAIN", "2000-01-01")
    _gbbq(mem_db, "000963", DATE, 3.5)

    df = _load(mem_db)
    assert list(df["code"]) == ["000963.SZ"], \
        f"指数不该出现在 gbbq 事件里, 实际: {list(df['code'])}"


def test_normal_symbol_unaffected(mem_db):
    """正例: 不撞名的裸代码照常返回, 字段完整"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    _gbbq(mem_db, "600519", DATE, 27.6)

    df = _load(mem_db)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["code"] == "600519.SH"
    assert str(row["date"]).startswith(DATE)
    assert row["dividend"] == pytest.approx(27.6)


def test_index_only_symbol_yields_nothing(mem_db):
    """反例(边界): 裸代码只对应指数时返回空, 不得把指数当股票放行"""
    insert_stock_info(mem_db, "000300", "SH", "INDEX", "2005-04-08")
    _gbbq(mem_db, "000300", DATE, 1.0)

    df = _load(mem_db)
    assert df.empty


def test_non_xdr_category_excluded(mem_db):
    """反例: 只取「除权除息」类别, 股本变化等不得混入"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    _gbbq(mem_db, "600519", DATE, 1.0, category="股本变化")

    df = _load(mem_db)
    assert df.empty


def test_symbol_absent_from_stock_info_is_dropped(mem_db):
    """正例(预期行为, 非缺陷): symbol 不在 STOCK_INFO 时丢弃。

    B股(200/900)、基金(16x)、REITs(508x) 都会落到这一支 —— 它们本就不在
    A 股复权对账范围内。全历史约 9,440 条, 属预期而非 bug。
    """
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    _gbbq(mem_db, "200011", DATE, 1.0)   # B股, STOCK_INFO 无此记录

    df = _load(mem_db)
    assert df.empty
