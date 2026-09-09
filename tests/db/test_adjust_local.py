# 修改记录:
#   2026-09-06  Claude  新增：adjust.py local/bstock 双源事件表路由与 --densify 开关测试
#   2026-09-06  Claude  BUG-001 回归：锚点行端到端（local_xdr 取数→事件入库→稠密化）
#   2026-09-06  Claude  BUG-004：锚点表述改为全历史链；新增 LOCAL 链首不变量端到端断言
#   2026-09-06  Claude  第二轮审查：BUG-006 水位连续端到端、BUG-007 fore 口径、
#                       BUG-012① 未来事件清除、BUG-015 无事件股防重置
#   2026-09-09  Claude  code review 修复回归：LOCAL 空帧不退 legacy、快照 requested
#                       忽略 9 开头/退市股的正反例
"""etl/adjust.py 本地自算（local）源链路测试：事件表路由、稠密化开关、不变量。"""
from unittest.mock import patch

import pandas as pd
import pytest

from datasource import local_xdr
from etl.adjust import process_and_save_adjust_factors
from tests.conftest import insert_trade_cal

TRADE_DATES_JAN = ["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"]


def _insert_trade_cals(conn, dates):
    for d in dates:
        insert_trade_cal(conn, d, 1)


def _stock_list(symbol="000681", exchange="SZ",
                start="2023-01-03", end="2023-01-06"):
    return [(symbol, exchange, start, end, "L")]


def _local_event_df(code="000681.SZ"):
    """模拟 local_xdr 输出：fore ≠ back（历史累计），adjust ≡ back"""
    return pd.DataFrame({
        "code": [code],
        "date": ["2023-01-04"],
        "fore_factor": [0.9998],
        "back_factor": [3.9052],
        "adjust_factor": [3.9052],
    })


def _table_count(conn, table, code="000681.SZ"):
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE code = ?", [code]
    ).fetchone()[0]


# ── 正例：local 源链路 ────────────────────────────────────────────────────────

def test_local_events_written_to_local_table_and_densified(mem_db):
    """正例: local 源事件写 ADJ_FACTOR_LOCAL（不碰 RAW），稠密化读 LOCAL"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(
        _local_event_df(), _stock_list(), mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )

    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL") == 1
    assert _table_count(mem_db, "ADJ_FACTOR_RAW") == 0
    assert _table_count(mem_db, "ADJ_FACTOR") == 4

    # 稠密表 ASOF 前向填充：事件日前 1.0，事件日起取事件值
    rows = mem_db.execute(
        "SELECT trade_date, fore_factor, back_factor FROM ADJ_FACTOR "
        "WHERE code = '000681.SZ' ORDER BY trade_date"
    ).fetchall()
    assert rows[0][1:] == (1.0, 1.0)                       # 2023-01-03
    assert rows[1][1] == pytest.approx(0.9998)             # 2023-01-04
    assert rows[2][2] == pytest.approx(3.9052)             # 2023-01-05 ASOF 沿用


def test_local_adjust_equals_back_invariant(mem_db):
    """正例: LOCAL 事件表与稠密表都满足 adjust_factor ≡ back_factor"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(
        _local_event_df(), _stock_list(), mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    for table in ("ADJ_FACTOR_LOCAL", "ADJ_FACTOR"):
        bad = mem_db.execute(
            f"SELECT COUNT(*) FROM {table} "
            "WHERE ABS(adjust_factor - back_factor) > 1e-12"
        ).fetchone()[0]
        assert bad == 0, f"{table} 存在 adjust_factor ≠ back_factor 的行"


def test_local_densify_idempotent(mem_db):
    """正例: local 源重跑同区间幂等，行数与数值不变"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    args = (_local_event_df(), _stock_list(), mem_db)
    kwargs = dict(event_table="ADJ_FACTOR_LOCAL", densify=True)
    process_and_save_adjust_factors(*args, **kwargs)
    before = mem_db.execute(
        "SELECT code, trade_date, fore_factor, back_factor FROM ADJ_FACTOR ORDER BY 1, 2"
    ).fetchall()
    process_and_save_adjust_factors(*args, **kwargs)
    after = mem_db.execute(
        "SELECT code, trade_date, fore_factor, back_factor FROM ADJ_FACTOR ORDER BY 1, 2"
    ).fetchall()
    assert before == after
    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL") == 1


# ── 正反例：--densify 开关 ────────────────────────────────────────────────────

def test_densify_off_writes_events_only(mem_db, caplog):
    """正例: densify=off 只写事件表，ADJ_FACTOR 不动，日志明确说明跳过"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    with caplog.at_level("INFO", logger="etl.adjust"):
        process_and_save_adjust_factors(
            _local_event_df(), _stock_list(), mem_db,
            event_table="ADJ_FACTOR_LOCAL", densify=False,
        )
    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL") == 1
    assert _table_count(mem_db, "ADJ_FACTOR") == 0
    assert "跳过 ADJ_FACTOR 稠密化" in caplog.text


def test_bstock_path_writes_raw_without_densify(mem_db):
    """正例: bstock 留痕路径——事件写 RAW，densify=off 不稠密化"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(
        _local_event_df(), _stock_list(), mem_db,
        event_table="ADJ_FACTOR_RAW", densify=False,
    )
    assert _table_count(mem_db, "ADJ_FACTOR_RAW") == 1
    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL") == 0
    assert _table_count(mem_db, "ADJ_FACTOR") == 0


def test_densify_on_with_bstock_events_reads_raw(mem_db):
    """正例: 显式 densify=on 时 bstock 源保持旧行为——写 RAW 并从 RAW 稠密化"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(
        _local_event_df(), _stock_list(), mem_db,
        event_table="ADJ_FACTOR_RAW", densify=True,
    )
    assert _table_count(mem_db, "ADJ_FACTOR_RAW") == 1
    rows = mem_db.execute(
        "SELECT back_factor FROM ADJ_FACTOR WHERE code = '000681.SZ' "
        "AND trade_date = '2023-01-05'"
    ).fetchall()
    assert rows[0][0] == pytest.approx(3.9052)


def test_invalid_event_table_raises(mem_db):
    """反例: event_table 不在白名单（防 SQL 注入/手误）必须报错"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    with pytest.raises(ValueError, match="event_table"):
        process_and_save_adjust_factors(
            _local_event_df(), _stock_list(), mem_db,
            event_table="ADJ_FACTOR; DROP TABLE ADJ_FACTOR",
        )


# ── BUG-001/BUG-004：全历史事件链端到端（local_xdr 取数 → 事件入库 → 稠密化）─────

def _insert_xdr_event(conn, symbol, date, dividend=0.0, bonus_share=0.0,
                      allotment_share=0.0, allotment_price=0.0):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share, updated_at) "
        "VALUES (?, ?, '除权除息', ?, ?, ?, ?, now())",
        [symbol, date, dividend, allotment_price, bonus_share, allotment_share],
    )


def _insert_close(conn, code, date, close):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, close) VALUES (?, ?, ?)",
        [code, date, close],
    )


def _local_fetch(mem_db, stock_list):
    """local_xdr 的 DB 读取重定向到 mem_db（cursor 派生连接，关闭不影响 fixture）"""
    with patch.object(local_xdr.dbutil, "get_connection",
                      side_effect=lambda is_read_only=True: mem_db.cursor()):
        return local_xdr.fetch_adjust_factors(stock_list)


def _dense_rows(conn, code="000001.SZ"):
    return conn.execute(
        "SELECT trade_date, back_factor FROM ADJ_FACTOR "
        "WHERE code = ? ORDER BY trade_date", [code]
    ).fetchall()


def test_bug001_first_local_run_window_without_event(mem_db):
    """正例(bug.md 复现): 首次局部运行、窗口内无事件 → 全历史事件随结果返回，
    稠密化后窗口内因子 = 正确累计值 2.0（而非 1.0）"""
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)  # 每10股送10股
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    fetched = _local_fetch(mem_db, stock_list)
    assert fetched["date"].tolist() == ["2025-05-09"]   # 窗口前历史事件(全历史链)
    assert fetched.iloc[0]["back_factor"] == pytest.approx(2.0)

    process_and_save_adjust_factors(
        fetched, stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    rows = _dense_rows(mem_db)
    assert [str(r[0]) for r in rows] == ["2026-08-03", "2026-08-04"]
    assert all(r[1] == pytest.approx(2.0) for r in rows)


def test_bug004_first_run_local_contains_complete_chain(mem_db):
    """正例(BUG-004 不变量): 首次运行后 LOCAL 表含完整历史链——
    表内首行 == 链首事件，其 back_factor == 当次跳变（相对 1.0 的累计起点）"""
    _insert_xdr_event(mem_db, "000001", "2024-05-10", dividend=10.0)  # 每10股派10元, C=11
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)  # 送10股, C=10
    _insert_close(mem_db, "000001.SZ", "2024-05-09", 11.0)
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    fetched = _local_fetch(mem_db, stock_list)
    assert fetched["date"].tolist() == ["2024-05-10", "2025-05-09"]

    process_and_save_adjust_factors(
        fetched, stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    local_rows = mem_db.execute(
        "SELECT trade_date, back_factor FROM ADJ_FACTOR_LOCAL "
        "WHERE code = '000001.SZ' ORDER BY trade_date"
    ).fetchall()
    # 链首 2024-05-10: back == 当次跳变 11/(11-1) = 1.1，对账侧可放心以 1.0 为基准
    assert str(local_rows[0][0]) == "2024-05-10"
    assert local_rows[0][1] == pytest.approx(1.1)
    assert local_rows[1][1] == pytest.approx(1.1 * 2.0)
    # 窗口内逐日因子 = 链尾累计值
    rows = _dense_rows(mem_db)
    assert all(r[1] == pytest.approx(1.1 * 2.0) for r in rows)


def test_bug001_new_event_in_window_built_on_full_history(mem_db):
    """正例: 窗口内有新事件 + 窗口前有历史 → 新事件累计值基于全历史链，
    窗口内新事件前的交易日由历史链续接"""
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)   # back 跳变 2.0
    _insert_xdr_event(mem_db, "000001", "2026-08-04", dividend=1.0)       # 每10股派1元
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    _insert_close(mem_db, "000001.SZ", "2026-08-03", 10.1)                # C=10.1, X=10.0
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    fetched = _local_fetch(mem_db, stock_list)
    assert fetched["date"].tolist() == ["2025-05-09", "2026-08-04"]
    assert fetched.iloc[1]["back_factor"] == pytest.approx(2.0 * 1.01)    # 全历史累计

    process_and_save_adjust_factors(
        fetched, stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    rows = _dense_rows(mem_db)
    assert [str(r[0]) for r in rows] == ["2026-08-03", "2026-08-04"]
    assert rows[0][1] == pytest.approx(2.0)          # 新事件前: 历史链续接
    assert rows[1][1] == pytest.approx(2.0 * 1.01)   # 新事件日: 累计跳变


def test_bug001_incremental_run_with_existing_baseline_no_rescan(mem_db):
    """正例: 已有历史基准的正常增量运行不受影响——全历史链重写入库幂等，
    链首 min(date) 不会触发 ranges CTE 回扫窗口前日期重算"""
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    # 模拟此前已完成全量初始化：LOCAL 有历史事件，稠密表已覆盖到 2026-07-31
    mem_db.execute(
        "INSERT INTO ADJ_FACTOR_LOCAL (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, updated_at) VALUES ('000001.SZ', '2025-05-09', 0.5, 2.0, 2.0, now())"
    )
    mem_db.execute(
        "INSERT INTO ADJ_FACTOR (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, updated_at) VALUES ('000001.SZ', '2026-07-31', 0.5, 2.0, 2.0, now())"
    )
    # TRADE_CAL 故意包含窗口前日期：若发生回扫，2025-05-09 会被写进 ADJ_FACTOR
    _insert_trade_cals(mem_db, ["2025-05-09", "2026-07-31", "2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    mem_db.execute("INSERT INTO ADJ_FACTOR_LOCAL_STATE(code,base_factor) VALUES ('000001.SZ',1)")
    fetched = _local_fetch(mem_db, stock_list)
    process_and_save_adjust_factors(
        fetched, stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )

    rows = _dense_rows(mem_db)
    assert [str(r[0]) for r in rows] == ["2026-07-31", "2026-08-03", "2026-08-04"]
    assert all(r[1] == pytest.approx(2.0) for r in rows)
    # 全历史事件重复入库幂等：LOCAL 仍只有 1 条
    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL", "000001.SZ") == 1


def test_bug001_no_pre_window_event_behaves_as_before(mem_db):
    """反例: 窗口前无任何事件（新股/无除权历史）→ 链上只有窗口内事件，
    行为与现状一致（该事件即链首，正常生效）"""
    _insert_xdr_event(mem_db, "000001", "2026-08-03", dividend=1.0)   # 每10股派1元
    _insert_close(mem_db, "000001.SZ", "2026-07-31", 10.0)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    fetched = _local_fetch(mem_db, stock_list)
    assert fetched["date"].tolist() == ["2026-08-03"]                 # 无窗口前历史

    process_and_save_adjust_factors(
        fetched, stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    rows = _dense_rows(mem_db)
    assert [str(r[0]) for r in rows] == ["2026-08-03", "2026-08-04"]
    assert all(r[1] == pytest.approx(10.0 / 9.9) for r in rows)


# ── 第二轮：BUG-006/007/012①/015 端到端 ───────────────────────────────────────

def _insert_dense_row(conn, code, date, adjust_factor, fore=1.0):
    conn.execute(
        "INSERT INTO ADJ_FACTOR (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, updated_at) VALUES (?, ?, ?, ?, ?, now())",
        [code, date, fore, adjust_factor, adjust_factor],
    )


def test_bug006_daily_run_preserves_aligned_water_level(mem_db):
    """正例(BUG-006 验收): 存量稠密表为 baostock 水位 3.904298，local 未对齐链末
    为 2.0 → 在线校准 k 后新日期写入 3.904298，切换日无断崖"""
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)  # 链 back=2.0
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    for d in ["2026-07-30", "2026-07-31"]:
        insert_trade_cal(mem_db, d, 1)
        _insert_dense_row(mem_db, "000001.SZ", d, 3.904298)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    mem_db.execute("INSERT INTO ADJ_FACTOR_LOCAL_STATE(code,base_factor) VALUES ('000001.SZ',1.952149)")
    fetched = _local_fetch(mem_db, stock_list)
    # 取数出口即已校准到存量水位：k = 3.904298 / 2.0
    assert fetched.iloc[0]["back_factor"] == pytest.approx(3.904298)

    process_and_save_adjust_factors(
        fetched, stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    rows = _dense_rows(mem_db)
    assert [str(r[0]) for r in rows] == ["2026-07-30", "2026-07-31",
                                         "2026-08-03", "2026-08-04"]
    # 同一 code 逐日序列连续，无混合基准断崖
    assert all(r[1] == pytest.approx(3.904298) for r in rows)


def test_bug007_local_fore_anchored_to_latest_event(mem_db):
    """正例(BUG-007): 入库 LOCAL 的 fore 为累计前复权口径——
    末事件 fore=1.0，且每行满足 fore * back_末 == back"""
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)
    _insert_xdr_event(mem_db, "000001", "2026-08-03", dividend=1.0)
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    _insert_close(mem_db, "000001.SZ", "2026-07-31", 10.1)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    process_and_save_adjust_factors(
        _local_fetch(mem_db, stock_list), stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    rows = mem_db.execute(
        "SELECT fore_factor, back_factor FROM ADJ_FACTOR_LOCAL "
        "WHERE code = '000001.SZ' ORDER BY trade_date"
    ).fetchall()
    back_last = rows[-1][1]
    assert rows[-1][0] == pytest.approx(1.0)
    for fore, back in rows:
        assert fore * back_last == pytest.approx(back)


def test_bug012_future_events_purged_on_local_write(mem_db, caplog):
    """反例(BUG-012①): LOCAL 里存量的未来日期预告事件在 local 跑批写事件后
    被顺带清除（局部、幂等），日志记录删除条数"""
    mem_db.execute(
        "INSERT INTO ADJ_FACTOR_LOCAL (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, updated_at) VALUES ('000681.SZ', '2099-01-04', 1, 1, 1, now())"
    )
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    with caplog.at_level("INFO", logger="etl.adjust"):
        process_and_save_adjust_factors(
            _local_event_df(), _stock_list(), mem_db,
            event_table="ADJ_FACTOR_LOCAL", densify=True,
        )
    dates = [str(r[0]) for r in mem_db.execute(
        "SELECT trade_date FROM ADJ_FACTOR_LOCAL ORDER BY trade_date"
    ).fetchall()]
    assert dates == ["2023-01-04"]          # 未来行已删，正常行保留
    assert "未来事件清理：删除 1 条" in caplog.text


def test_bug012_purge_scoped_to_local_table(mem_db):
    """反例: bstock(RAW) 路径不清除 LOCAL 的未来行（清除动作仅属 local 主源）"""
    mem_db.execute(
        "INSERT INTO ADJ_FACTOR_LOCAL (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, updated_at) VALUES ('000681.SZ', '2099-01-04', 1, 1, 1, now())"
    )
    process_and_save_adjust_factors(
        _local_event_df(), _stock_list(), mem_db,
        event_table="ADJ_FACTOR_RAW", densify=False,
    )
    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL") == 1


def test_bug015_no_event_stock_with_non1_history_not_reset(mem_db, caplog):
    """反例(BUG-015 复现 000022.SZ): gbbq 无该股事件、ADJ_FACTOR 已有非 1.0
    因子 → 跳过其稠密化并告警，存量因子不得被重置为 1.0；有事件股票正常跑"""
    _insert_dense_row(mem_db, "000022.SZ", "2018-12-25", 3.529141)
    _insert_xdr_event(mem_db, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(mem_db, "000001.SZ", "2025-05-08", 10.0)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000022", "SZ", "2026-08-03", "2026-08-04", "L"),
                  ("000001", "SZ", "2026-08-03", "2026-08-04", "L")]

    with caplog.at_level("WARNING", logger="etl.adjust"):
        process_and_save_adjust_factors(
            _local_fetch(mem_db, stock_list), stock_list, mem_db,
            event_table="ADJ_FACTOR_LOCAL", densify=True,
        )

    # 000022: 无新行、存量行不动
    rows = _dense_rows(mem_db, "000022.SZ")
    assert [str(r[0]) for r in rows] == ["2018-12-25"]
    assert rows[0][1] == pytest.approx(3.529141)
    assert "000022.SZ" in caplog.text and "不重置为 1.0" in caplog.text
    # 000001: 正常稠密化
    rows = _dense_rows(mem_db, "000001.SZ")
    assert [str(r[0]) for r in rows] == ["2026-08-03", "2026-08-04"]
    assert all(r[1] == pytest.approx(2.0) for r in rows)


def test_bug015_no_event_stock_with_all1_history_still_densified(mem_db):
    """正例: 无事件但存量因子全 1.0（确实没除过权）→ 不在防重置范围，
    照常补齐 1.0"""
    _insert_dense_row(mem_db, "000003.SZ", "2026-07-31", 1.0)
    insert_trade_cal(mem_db, "2026-07-31", 1)
    _insert_trade_cals(mem_db, ["2026-08-03", "2026-08-04"])
    stock_list = [("000003", "SZ", "2026-08-03", "2026-08-04", "L")]

    process_and_save_adjust_factors(
        _local_fetch(mem_db, stock_list), stock_list, mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    rows = _dense_rows(mem_db, "000003.SZ")
    assert [str(r[0]) for r in rows] == ["2026-07-31", "2026-08-03", "2026-08-04"]
    assert all(r[1] == pytest.approx(1.0) for r in rows)


def test_bug015_guard_not_applied_to_bstock_path(mem_db, caplog):
    """反例: 防重置规则仅属 local 主源；bstock 留痕路径保持原行为"""
    _insert_dense_row(mem_db, "000022.SZ", "2018-12-25", 3.529141)
    _insert_trade_cals(mem_db, ["2026-08-03"])
    with caplog.at_level("WARNING", logger="etl.adjust"):
        process_and_save_adjust_factors(
            pd.DataFrame(), [("000022", "SZ", "2026-08-03", "2026-08-03", "L")],
            mem_db, event_table="ADJ_FACTOR_RAW", densify=True,
        )
    assert "不重置为 1.0" not in caplog.text


# ── 2026-09-09 code review 修复：空帧不退 legacy、requested 过滤 ───────────────────

def test_local_empty_frame_without_snapshot_does_not_densify(mem_db, caplog):
    """反例: local_xdr 把候选全过滤掉时返回不带 attrs 的空帧（如候选全为 9 开头），
    不得退到 legacy 稠密化把它们静默写成 1.0"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    empty = pd.DataFrame(columns=local_xdr.RESULT_COLUMNS)      # 无 attrs、无行
    with caplog.at_level("WARNING", logger="etl.adjust"):
        process_and_save_adjust_factors(
            empty, _stock_list("920001", "BJ"), mem_db,
            event_table="ADJ_FACTOR_LOCAL", densify=True,
        )
    assert _table_count(mem_db, "ADJ_FACTOR", "920001.BJ") == 0
    assert _table_count(mem_db, "ADJ_FACTOR_LOCAL_STATE", "920001.BJ") == 0
    assert "本次不写 ADJ_FACTOR" in caplog.text


def test_local_nonempty_frame_without_attrs_still_uses_legacy(mem_db):
    """正例(钉住收窄范围): 非空但无 attrs 的 LOCAL 帧仍走 legacy 稠密化，
    既有 BUG-015/012① 测试依赖此路径"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(
        _local_event_df(), _stock_list(), mem_db,
        event_table="ADJ_FACTOR_LOCAL", densify=True,
    )
    assert _table_count(mem_db, "ADJ_FACTOR") == 4


def _snapshot_frame(bases):
    """模拟 local_xdr 经 _calibrate_to_dense 校验后的空事件快照（只有 bases）"""
    frame = pd.DataFrame(columns=local_xdr.RESULT_COLUMNS)
    frame.attrs["local_snapshot_bases"] = bases
    return frame


def test_snapshot_requested_ignores_bj_and_delisted(mem_db, caplog):
    """反例: stock_list 里的 9 开头/退市股本就被 local_xdr 过滤，不在 bases 里，
    不得触发「无可信完整快照」告警——否则全市场跑 344 只北交所会淹没真告警"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    stocks = [
        ("000681", "SZ", "2023-01-03", "2023-01-06", "L"),
        ("920001", "BJ", "2023-01-03", "2023-01-06", "L"),   # 9 开头
        ("600001", "SH", "2023-01-03", "2023-01-06", "D"),   # 退市
    ]
    with caplog.at_level("WARNING", logger="etl.adjust"):
        process_and_save_adjust_factors(
            _snapshot_frame({"000681.SZ": 1.0}), stocks, mem_db,
            event_table="ADJ_FACTOR_LOCAL", densify=True,
        )
    assert "无可信完整快照" not in caplog.text
    assert _table_count(mem_db, "ADJ_FACTOR", "000681.SZ") == 4
    assert _table_count(mem_db, "ADJ_FACTOR", "920001.BJ") == 0


def test_snapshot_requested_still_warns_for_real_unverified_stock(mem_db, caplog):
    """正例: 正常在市股不在 bases 里（基准无法验证）→ 必须告警并点名，且不写该股"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    stocks = [
        ("000681", "SZ", "2023-01-03", "2023-01-06", "L"),
        ("600000", "SH", "2023-01-03", "2023-01-06", "L"),   # 未经校验
    ]
    with caplog.at_level("WARNING", logger="etl.adjust"):
        process_and_save_adjust_factors(
            _snapshot_frame({"000681.SZ": 1.0}), stocks, mem_db,
            event_table="ADJ_FACTOR_LOCAL", densify=True,
        )
    assert "无可信完整快照" in caplog.text
    assert "600000.SH" in caplog.text
    assert _table_count(mem_db, "ADJ_FACTOR", "600000.SH") == 0
    assert _table_count(mem_db, "ADJ_FACTOR", "000681.SZ") == 4
