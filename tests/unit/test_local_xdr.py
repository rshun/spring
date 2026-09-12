# 修改记录:
#   2026-09-06  Claude  新增：本地自算复权因子纯函数正反例测试（docs/adj_factor_selfbuild.md §4）
#   2026-09-06  Claude  BUG-001 回归：窗口前锚点行的正反例（含 bug.md 复现场景）
#   2026-09-06  Claude  BUG-004：锚点用例改写为全历史事件链断言（含链首不变量）
#   2026-09-06  Claude  第二轮审查：compute_event_factors 改收带 prev_close 的事件帧
#                       （BUG-008/014 下推 SQL）；新增 BUG-006 水位校准、BUG-007 fore 口径、
#                       BUG-011 跳过统计、BUG-012① 未来事件、BUG-017 配股价缺失用例
#   2026-09-09  Claude  事件日恰为日线首日 → 历史覆盖外(非缺口)的正反例（600018.SH 换股上市复现）
#   2026-09-10  Claude  移除 adjust --by-date 相关用例（bstock 复权因子源废弃，开关已删）
#   2026-09-11  Claude  BUG-015 防重置失效回归：守卫只看首行、基准取最早行而非最近行的
#                       正反例；补 STATE 已存在时走撤销语义(不受守卫影响)的正例
#   2026-09-12  Claude  fixture 的 STOCK_DAILY 补 pre_close 列（真实 schema 有该列，
#                       除权参考价校正依赖它）；新增转增类事件与参考价校正的正反例
"""datasource/local_xdr.py 纯函数与路由开关测试；不连接网络与生产库。"""
import logging
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

from datasource import local_xdr
from etl import adjust
from tools import describe_cli


def _events(rows):
    """rows: (code, date, dividend, bonus_share, allotment_share, allotment_price, prev_close)
    或尾部追加 first_price_date"""
    n = len(rows[0]) if rows else 7
    cols = ["code", "date", "dividend", "bonus_share", "allotment_share",
            "allotment_price", "prev_close", "first_price_date"][:n]
    return pd.DataFrame(rows, columns=cols)


# ── 正例：因子计算（纯函数，prev_close 由 SQL 下推提供）────────────────────────

def test_pure_dividend_000681_case():
    """正例(实测案例): 000681 2026-08-03 每10股派0.04元, C=17.96
    → back = 17.96/17.956；单事件即链尾，fore ≡ 1.0（BUG-007 口径）"""
    events = _events([("000681.SZ", "2026-08-03", 0.04, 0.0, 0.0, 0.0, 17.96)])
    out = local_xdr.compute_event_factors(events)

    assert len(out) == 1
    row = out.iloc[0]
    assert row["code"] == "000681.SZ"
    assert row["date"] == pd.Timestamp("2026-08-03")
    assert row["back_factor"] == pytest.approx(17.96 / 17.956)
    assert row["fore_factor"] == pytest.approx(1.0)
    assert row["adjust_factor"] == row["back_factor"]


def test_bonus_share_event():
    """正例: 每10股送转5股, C=10 → back = 1.5"""
    events = _events([("600519.SH", "2024-06-14", 0.0, 5.0, 0.0, 0.0, 10.0)])
    out = local_xdr.compute_event_factors(events)
    assert out.iloc[0]["back_factor"] == pytest.approx(1.5)
    assert out.iloc[0]["fore_factor"] == pytest.approx(1.0)


def test_allotment_event():
    """正例: 每10股配3股、配股价5元, C=10 → back = 10 / (11.5/1.3)"""
    events = _events([("600000.SH", "2023-11-20", 0.0, 0.0, 3.0, 5.0, 10.0)])
    out = local_xdr.compute_event_factors(events)
    expected_x = 11.5 / 1.3
    assert out.iloc[0]["back_factor"] == pytest.approx(10.0 / expected_x)


def test_multi_event_cumulative_chain_and_fore_anchor():
    """正例(BUG-007): back 为 C/X 累计连乘；fore = back/back_最后一条事件，
    最新事件恒 1.0，历史事件 fore*back_last == back"""
    events = _events([
        ("000001.SZ", "2024-05-10", 2.0, 0.0, 0.0, 0.0, 10.0),  # 每10股派2元
        ("000001.SZ", "2025-05-09", 0.0, 5.0, 0.0, 0.0, 9.0),   # 每10股送5股
    ])
    out = local_xdr.compute_event_factors(events)

    assert len(out) == 2
    x1 = (10.0 - 0.2) / 1.0
    x2 = 9.0 / 1.5
    b1, b2 = 10.0 / x1, (10.0 / x1) * (9.0 / x2)
    assert out.iloc[0]["back_factor"] == pytest.approx(b1)
    assert out.iloc[1]["back_factor"] == pytest.approx(b2)
    assert out.iloc[0]["fore_factor"] == pytest.approx(b1 / b2)
    assert out.iloc[1]["fore_factor"] == pytest.approx(1.0)
    assert (out["adjust_factor"] == out["back_factor"]).all()


# ── 反例：边界与异常输入（纯函数）──────────────────────────────────────────────

def test_empty_events_returns_empty():
    """反例: 空事件帧 → 空结果（带标准列）"""
    out = local_xdr.compute_event_factors(pd.DataFrame(columns=[
        "code", "date", "dividend", "bonus_share", "allotment_share",
        "allotment_price", "prev_close"]))
    assert out.empty
    assert list(out.columns) == local_xdr.RESULT_COLUMNS


def test_zero_value_events_filtered():
    """反例: dividend/bonus/allotment 全为 0 的事件不产生因子行"""
    events = _events([("000001.SZ", "2024-05-10", 0.0, 0.0, 0.0, 0.0, 10.0)])
    assert local_xdr.compute_event_factors(events).empty


def test_missing_prev_close_out_of_coverage_is_silent(caplog):
    """反例(BUG-011): 事件日早于日线覆盖起点 → 不打印逐股明细，但计入跳过统计"""
    events = _events([
        ("000001.SZ", "1997-07-15", 1.0, 0.0, 0.0, 0.0, None, "2005-01-04"),
        ("000001.SZ", "2024-05-10", 2.0, 0.0, 0.0, 0.0, 10.0, "2005-01-04"),
    ])
    stats = local_xdr._new_stats()
    with caplog.at_level(logging.INFO, logger="etl.datasource.local_xdr"):
        out = local_xdr.compute_event_factors(events, stats=stats)

    assert out["date"].tolist() == [pd.Timestamp("2024-05-10")]
    assert stats["skipped"] == 1 and stats["gap"] == 0
    assert stats["out_of_coverage"] == 1
    assert "历史覆盖外" not in caplog.text


def test_missing_prev_close_mid_gap_counted_as_gap(caplog):
    """反例(BUG-011): 事件落在日线覆盖区间内部但前收盘缺失 → warning 且计入缺口 X"""
    events = _events([
        ("000001.SZ", "2024-05-10", 2.0, 0.0, 0.0, 0.0, None, "2005-01-04"),
        ("000001.SZ", "2025-05-09", 0.0, 5.0, 0.0, 0.0, 9.0, "2005-01-04"),
    ])
    stats = local_xdr._new_stats()
    with caplog.at_level(logging.WARNING, logger="etl.datasource.local_xdr"):
        out = local_xdr.compute_event_factors(events, stats=stats)

    assert out["date"].tolist() == [pd.Timestamp("2025-05-09")]
    assert stats["skipped"] == 1 and stats["gap"] == 1
    assert "数据缺口" in caplog.text


def test_missing_prev_close_no_price_at_all_warns(caplog):
    """反例(BUG-011): 该股完全无价格记录(first_price_date 为空) → warning 跳过"""
    events = _events([
        ("600519.SH", "2024-06-14", 1.0, 0.0, 0.0, 0.0, None, None),
        ("000001.SZ", "2024-05-10", 2.0, 0.0, 0.0, 0.0, 10.0, "2005-01-04"),
    ])
    stats = local_xdr._new_stats()
    with caplog.at_level(logging.WARNING, logger="etl.datasource.local_xdr"):
        out = local_xdr.compute_event_factors(events, stats=stats)

    assert out["code"].tolist() == ["000001.SZ"]
    assert "600519.SH" in caplog.text
    assert stats["skipped"] == 1 and stats["gap"] == 0


def test_event_on_first_price_date_is_out_of_coverage_not_gap(caplog):
    """正例(600018.SH 复现): 事件日 == 日线首日，ASOF 取不到首日之前的收盘 →
    归「历史覆盖外」且不打印逐股明细，不计入缺口 X，链从下一条事件起算，整批不中止"""
    events = _events([
        ("600018.SH", "2006-10-26", 0.0, 35.0, 0.0, 0.0, None, "2006-10-26"),  # 换股上市当天
        ("600018.SH", "2007-06-22", 0.76, 0.0, 0.0, 0.0, 6.0, "2006-10-26"),
    ])
    stats = local_xdr._new_stats()
    with caplog.at_level(logging.INFO, logger="etl.datasource.local_xdr"):
        out = local_xdr.compute_event_factors(events, stats=stats)

    assert out["date"].tolist() == [pd.Timestamp("2007-06-22")]
    assert stats["skipped"] == 1 and stats["out_of_coverage"] == 1
    assert stats["gap"] == 0                       # 不会触发「事件链不完整」致命判定
    assert "历史覆盖外" not in caplog.text
    assert "数据缺口" not in caplog.text


def test_event_one_day_after_first_price_date_still_gap(caplog):
    """反例: 事件日晚于日线首日一天但仍取不到前收（首日停牌等）→ 仍是缺口 X，
    边界只放宽到「等于首日」，不能把真实缺口也放过去"""
    events = _events([
        ("600018.SH", "2006-10-27", 0.0, 35.0, 0.0, 0.0, None, "2006-10-26"),
        ("600018.SH", "2007-06-22", 0.76, 0.0, 0.0, 0.0, 6.0, "2006-10-26"),
    ])
    stats = local_xdr._new_stats()
    with caplog.at_level(logging.WARNING, logger="etl.datasource.local_xdr"):
        out = local_xdr.compute_event_factors(events, stats=stats)

    assert out["date"].tolist() == [pd.Timestamp("2007-06-22")]
    assert stats["skipped"] == 1 and stats["gap"] == 1
    assert stats["out_of_coverage"] == 0
    assert "数据缺口" in caplog.text


def test_allotment_without_price_skipped(caplog):
    """反例(BUG-017): allotment_share>0 但配股价缺失 → 跳过并告警，
    不得按 0 元配股计算"""
    events = _events([
        ("000001.SZ", "2024-05-10", 0.0, 0.0, 2.0, None, 10.0),   # 缺配股价 → 跳过
        ("000001.SZ", "2025-05-09", 0.0, 5.0, 0.0, 0.0, 9.0),     # 正常送转
    ])
    stats = local_xdr._new_stats()
    with caplog.at_level(logging.WARNING, logger="etl.datasource.local_xdr"):
        out = local_xdr.compute_event_factors(events, stats=stats)

    assert out["date"].tolist() == [pd.Timestamp("2025-05-09")]
    assert out.iloc[0]["back_factor"] == pytest.approx(1.5)   # 未混入「0元配股」
    assert stats["skipped"] == 1
    assert "配股价" in caplog.text


# ── fetch_adjust_factors：DB 读取层（内存库 mock，不碰生产库）──────────────────

def _seed_mem_conn(with_adj_factor=True):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE ADJ_FACTOR_LOCAL_STATE(code VARCHAR, base_factor DOUBLE)")
    conn.execute("""
        CREATE TABLE CAPITAL_DETAIL (
            code VARCHAR(20), date DATE, category VARCHAR(20),
            dividend DOUBLE, allotment_price DOUBLE,
            bonus_share DOUBLE, allotment_share DOUBLE,
            updated_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (code, date, category))
    """)
    conn.execute("""
        CREATE TABLE STOCK_DAILY (
            code VARCHAR(20), date DATE, close DOUBLE, pre_close DOUBLE,
            tradestatus INTEGER DEFAULT 1,
            PRIMARY KEY (code, date))
    """)
    if with_adj_factor:
        conn.execute("""
            CREATE TABLE ADJ_FACTOR (
                code VARCHAR(20), trade_date DATE,
                fore_factor DOUBLE, back_factor DOUBLE, adjust_factor DOUBLE,
                PRIMARY KEY (code, trade_date))
        """)
    return conn


def _insert_event(conn, symbol, date, dividend=0.0, bonus_share=0.0,
                  allotment_share=0.0, allotment_price=0.0, category="除权除息"):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [symbol, date, category, dividend, allotment_price, bonus_share, allotment_share],
    )


def _insert_close(conn, code, date, close, tradestatus=1, pre_close=None):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, close, pre_close, tradestatus) "
        "VALUES (?, ?, ?, ?, ?)",
        [code, date, close, pre_close, tradestatus],
    )


def _fetch(conn, stock_list):
    with patch.object(local_xdr.dbutil, "get_connection", return_value=conn):
        return local_xdr.fetch_adjust_factors(stock_list)


def test_fetch_full_history_not_window_clipped():
    """正例(BUG-004): 输出为全历史事件链，不按 [start_date,end_date] 窗口裁剪；
    6位无后缀 code 映射为带后缀；非除权除息类别不计入"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000681", "2025-06-10", dividend=5.0)       # 窗口前旧事件
    _insert_event(conn, "000681", "2026-08-03", dividend=0.04)
    _insert_event(conn, "000681", "2026-08-04", dividend=9.9, category="股本变化")
    _insert_close(conn, "000681.SZ", "2025-06-09", 20.0)
    _insert_close(conn, "000681.SZ", "2026-07-31", 17.96)

    out = _fetch(conn, [("000681", "SZ", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    assert out["date"].tolist() == ["2025-06-10", "2026-08-03"]
    x1 = (20.0 - 0.5) / 1.0
    x2 = (17.96 - 0.004) / 1.0
    first = out.iloc[0]
    assert first["code"] == "000681.SZ"
    assert first["back_factor"] == pytest.approx(20.0 / x1)   # 链首: back == 当次跳变
    row = out.iloc[1]
    assert row["back_factor"] == pytest.approx((20.0 / x1) * (17.96 / x2))
    assert row["adjust_factor"] == row["back_factor"]


def test_fetch_fore_anchored_to_latest_event():
    """正例(BUG-007 口径换算): 输出满足 fore * back_last == back，末事件 fore=1.0"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000681", "2025-06-10", dividend=5.0)
    _insert_event(conn, "000681", "2026-08-03", dividend=0.04)
    _insert_close(conn, "000681.SZ", "2025-06-09", 20.0)
    _insert_close(conn, "000681.SZ", "2026-07-31", 17.96)

    out = _fetch(conn, [("000681", "SZ", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    back_last = out["back_factor"].iloc[-1]
    assert out["fore_factor"].iloc[-1] == pytest.approx(1.0)
    assert (out["fore_factor"] * back_last).values == pytest.approx(out["back_factor"].values)


# ── BUG-008/014：取价下推与停牌过滤 ────────────────────────────────────────────

def test_suspended_day_not_used_as_prev_close():
    """反例(BUG-014): 事件日前一日为停牌行(tradestatus=0)且 close 与真实前收不同，
    取价必须落到最近一个 tradestatus=1 的收盘价"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2024-06-14", bonus_share=5.0)
    _insert_close(conn, "000001.SZ", "2024-06-12", 10.0, tradestatus=1)
    # 停牌行 close 是前收结转的正数，本例故意与前收不同以验证不被取到
    _insert_close(conn, "000001.SZ", "2024-06-13", 12.34, tradestatus=0)

    out = _fetch(conn, [("000001", "SZ", "2024-06-01", "2024-06-30", "L")])
    conn.close()

    assert len(out) == 1
    assert out.iloc[0]["back_factor"] == pytest.approx(1.5)   # C=10.0 而非 12.34


def test_zero_close_not_used_as_reference():
    """反例: close=0 的占位行不能作为除权参考价"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2024-05-10", dividend=2.0)
    _insert_close(conn, "000001.SZ", "2024-05-08", 10.0, tradestatus=1)
    _insert_close(conn, "000001.SZ", "2024-05-09", 0.0, tradestatus=1)

    out = _fetch(conn, [("000001", "SZ", "2024-05-01", "2024-05-31", "L")])
    conn.close()

    assert out.iloc[0]["back_factor"] == pytest.approx(10.0 / 9.8)


# ── BUG-001 复现 / BUG-012① 未来事件 ─────────────────────────────────────────

def test_full_history_returned_when_window_has_no_event():
    """正例(BUG-001 复现): 窗口内无事件时, 窗口前的历史事件仍随全历史链返回"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)   # 每10股送10股
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    assert out["date"].tolist() == ["2025-05-09"]
    assert out.iloc[0]["back_factor"] == pytest.approx(2.0)


def test_future_events_filtered():
    """反例(BUG-012①): 未来日期的预告除权事件不进链、不返回"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_event(conn, "000001", "2099-01-04", dividend=1.0)       # 未来预告事件
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    assert out["date"].tolist() == ["2025-05-09"]


# ── BUG-006：水位在线校准 ─────────────────────────────────────────────────────

def test_calibrate_scales_chain_to_dense_anchor():
    """正例(BUG-006): ADJ_FACTOR 末行 adjust=3.9，链在锚点日的 ASOF 值为 2.0
    → k=1.95，back/adjust 全链乘 k，fore 不变（k 约掉）"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)   # back=2.0
    _insert_event(conn, "000001", "2026-08-03", dividend=1.0)       # C=10.1, back=2.02
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)
    _insert_close(conn, "000001.SZ", "2026-07-31", 10.1)
    conn.execute("INSERT INTO ADJ_FACTOR VALUES "
                 "('000001.SZ', '2026-07-31', 0.5, 3.9, 3.9)")      # 存量水位锚

    conn.execute("INSERT INTO ADJ_FACTOR_LOCAL_STATE VALUES ('000001.SZ', 1.95)")
    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    k = 3.9 / 2.0
    assert out["back_factor"].tolist() == pytest.approx([2.0 * k, 2.02 * k])
    assert out["adjust_factor"].tolist() == out["back_factor"].tolist()
    # fore 口径不随水位缩放变化（k 在比值中约掉）
    assert out["fore_factor"].tolist() == pytest.approx([2.0 / 2.02, 1.0])


def test_calibrate_anchor_before_first_event_uses_chain_base_one():
    """正例(BUG-006): 锚点日早于链首事件 → 链前值按 1.0，k = 锚点值本身"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)
    conn.execute("INSERT INTO ADJ_FACTOR VALUES "
                 "('000001.SZ', '2020-01-03', 1.0, 4.2, 4.2)")      # 锚点早于链首

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    assert out.iloc[0]["back_factor"] == pytest.approx(2.0 * 4.2)


def test_calibrate_stops_when_adj_factor_missing():
    conn = _seed_mem_conn(with_adj_factor=False)
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)
    try:
        with pytest.raises(duckdb.CatalogException, match="ADJ_FACTOR"):
            _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    finally:
        conn.close()


def test_calibrate_new_stock_without_anchor_keeps_chain():
    """正例(BUG-006): ADJ_FACTOR 无该股记录（新股）→ k=1 不缩放"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    assert out.iloc[0]["back_factor"] == pytest.approx(2.0)


# ── BUG-011/018：汇总与边界日志 ───────────────────────────────────────────────

def test_fetch_summary_log_counts_skips(caplog):
    """反例(BUG-011): 跑批结束输出跳过汇总；区间内部缺口 X>0 时升级为 error"""
    conn = _seed_mem_conn()
    # 覆盖区间内部缺口：日线起点 2005 年但该行为停牌(tradestatus=0)，
    # 2024 年事件拿不到任何有效前收 → prev_close NULL 且事件日在覆盖区间内
    _insert_event(conn, "000001", "2024-05-10", dividend=2.0)
    _insert_close(conn, "000001.SZ", "2005-01-04", 5.0, tradestatus=0)
    # 历史覆盖外：事件日早于日线起点
    _insert_event(conn, "000002", "1997-07-15", dividend=1.0)
    _insert_close(conn, "000002.SZ", "2005-01-04", 5.0)

    with caplog.at_level(logging.INFO, logger="etl.datasource.local_xdr"):
        with pytest.raises(RuntimeError, match="事件链不完整"):
            _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L"),
                          ("000002", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    assert "本次跳过 2 条事件，涉及 2 只股票（其中区间内部缺口 1 条" in caplog.text
    assert "000002.SZ 事件日不晚于该股日线覆盖起点" not in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def test_fetch_boundary_heartbeat_logs(caplog):
    """正例(BUG-018): 取数前后与计算完成各有边界日志，供调度侧 stalled 判定"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)

    with caplog.at_level(logging.INFO, logger="etl.datasource.local_xdr"):
        _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    conn.close()

    assert "开始读取除权事件" in caplog.text
    assert "事件读取完成" in caplog.text
    assert "本地复权因子计算完成" in caplog.text


# ── 候选过滤 ──────────────────────────────────────────────────────────────────

def test_fetch_adjust_factors_skips_bj_and_delisted():
    """反例: 9开头(北交所)与 status=='D' 的股票被忽略，与 bstock 行为一致"""
    conn = _seed_mem_conn()
    _insert_event(conn, "920096", "2026-08-03", dividend=1.0)
    _insert_event(conn, "600001", "2026-08-03", dividend=1.0)
    _insert_close(conn, "920096.BJ", "2026-07-31", 10.0)
    _insert_close(conn, "600001.SH", "2026-07-31", 10.0)
    stock_list = [
        ("920096", "BJ", "2026-01-01", "2026-12-31", "L"),
        ("600001", "SH", "2026-01-01", "2026-12-31", "D"),
    ]

    out = _fetch(conn, stock_list)
    conn.close()
    assert out.empty


def test_fetch_adjust_factors_empty_stock_list():
    """反例: 空 stock_list → 空结果，且不开库连接"""
    with patch.object(local_xdr.dbutil, "get_connection") as get_conn:
        out = local_xdr.fetch_adjust_factors([])
    assert out.empty
    get_conn.assert_not_called()


# ── etl/adjust.py 路由开关 ────────────────────────────────────────────────────

@pytest.mark.parametrize("mode, source, expected", [
    ("auto", "local", True),
    ("on", "local", True),
    ("off", "local", False),
    # bstock 恒不稠密化：--densify 对该源不生效（2026-09-12）
    ("auto", "bstock", False),
    ("on", "bstock", False),
    ("off", "bstock", False),
])
def test_resolve_densify(mode, source, expected):
    """正反例: local 受 --densify 控制；bstock 无论传什么都只留痕写 RAW"""
    assert adjust.resolve_densify(mode, source) is expected


def test_bstock_never_densifies_regardless_of_flag():
    """反例(回归): --densify on 曾能越过 auto 语义, 拿已知有脏行的 ADJ_FACTOR_RAW
    覆盖主表 ADJ_FACTOR —— 该组合必须失效"""
    assert adjust.resolve_densify("on", "bstock") is False
    assert all(adjust.resolve_densify(m, "bstock") is False
               for m in ("auto", "on", "off"))


def test_densify_is_discoverable():
    """正例: --densify 必须出现在 describe_cli 自省出口(契约 C4)"""
    spec = describe_cli.describe("adjust")["arguments"]["densify"]
    assert spec["choices"] == ["auto", "on", "off"]
    assert spec["default"] == "auto"
    assert spec["help"]


def test_source_choices_include_local():
    """正例: -s/--source 自省出口必须包含 local"""
    spec = describe_cli.describe("adjust")["arguments"]["source"]
    assert spec["choices"] == ["bstock", "local"]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_anchor_stops_calibration(value):
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)
    conn.execute("INSERT INTO ADJ_FACTOR VALUES ('000001.SZ','2025-05-08',1,?,?)", [value, value])
    try:
        with pytest.raises(ValueError, match="锚点水位无效"):
            _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])
    finally:
        conn.close()


@pytest.mark.parametrize("fail", [False, True])
def test_heartbeat_repeats_and_stops(monkeypatch, fail):
    import threading
    pulses = []
    observed = threading.Event()
    monkeypatch.setattr(local_xdr, "get_config", lambda: {"baostock": {"progress_heartbeat_seconds": 0.01}})
    def log(*args):
        pulses.append(args)
        if len(pulses) >= 2:
            observed.set()
    monkeypatch.setattr(local_xdr.logger, "info", log)
    try:
        with local_xdr._progress_heartbeat("测试阻塞阶段"):
            assert observed.wait(2)
            if fail:
                raise RuntimeError("模拟阶段异常")
    except RuntimeError:
        assert fail
    count = len(pulses)
    assert count >= 2
    assert not any(t.name == "local-xdr-heartbeat" for t in threading.enumerate())
    assert len(pulses) == count



def test_stage_timeout_stops_heartbeat_and_rejects_result(monkeypatch):
    import threading
    timeout_seen = threading.Event()
    pulses = []
    monkeypatch.setattr(local_xdr, "get_config", lambda: {
        "baostock": {"progress_heartbeat_seconds": 0.005},
        "local_xdr": {"stage_timeout_seconds": 0.03}})
    monkeypatch.setattr(local_xdr.logger, "info", lambda *a: pulses.append(a))
    monkeypatch.setattr(local_xdr.logger, "error", lambda *a: timeout_seen.set())
    with pytest.raises(TimeoutError):
        with local_xdr._progress_heartbeat("blocked"):
            assert timeout_seen.wait(2)
            count = len(pulses)
            assert not threading.Event().wait(0.03)
            assert len(pulses) == count
    assert not any(t.name == "local-xdr-heartbeat" for t in threading.enumerate())


# ── BUG-015 防重置：守卫必须先于 STATE 判断，且看整表而非首行 ────────────────────

def _insert_dense(conn, code, trade_date, factor):
    conn.execute(
        "INSERT INTO ADJ_FACTOR (code, trade_date, fore_factor, back_factor, adjust_factor)"
        " VALUES (?, ?, ?, ?, ?)",
        [code, trade_date, 1.0 / factor, factor, factor],
    )


def test_reset_guard_looks_at_whole_table_not_first_row(caplog):
    """反例(本次修复的回归点): 稠密表首行是除权前的 1.0、后续才非 1.0 时也要拦住。

    此前守卫用 `ORDER BY trade_date LIMIT 1` 取首行判 != 1.0，而正常稠密表的首行
    本来就是 1.0，守卫因此对绝大多数真实股票失效。
    """
    conn = _seed_mem_conn()
    _insert_dense(conn, "000022.SZ", "2018-12-20", 1.0)        # 除权前
    _insert_dense(conn, "000022.SZ", "2018-12-25", 3.529141)   # 除权后
    # 无 STATE、无事件

    with caplog.at_level(logging.WARNING, logger="etl.datasource.local_xdr"):
        out = _fetch(conn, [("000022", "SZ", "2026-08-03", "2026-08-04", "L")])

    assert "000022.SZ" not in out.attrs["local_snapshot_bases"]


def test_reset_guard_does_not_block_genuinely_unadjusted_stock():
    """正例: 无事件且稠密表确实全 1.0(真的没除过权) → 不在防重置范围，照常给 base=1.0。
    守卫不能矫枉过正地把所有无事件股都拦掉。"""
    conn = _seed_mem_conn()
    _insert_dense(conn, "000003.SZ", "2026-07-31", 1.0)

    out = _fetch(conn, [("000003", "SZ", "2026-08-03", "2026-08-04", "L")])

    assert out.attrs["local_snapshot_bases"] == {"000003.SZ": 1.0}


def test_calibrate_anchor_takes_latest_row_before_first_event():
    """正例(本次修复的回归点): 基准取首事件之前**最近**一行，而非最早一行。

    稠密表在首事件前有 1.0(早) 与 4.2(晚) 两行：升序会取到 1.0 从而抹掉真实水位，
    正确结果是 4.2 → back = 2.0 * 4.2。
    """
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)   # 送10股 → 链内 back=2.0
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)
    _insert_dense(conn, "000001.SZ", "2020-01-03", 1.0)             # 早于首事件, 旧实现会取它
    _insert_dense(conn, "000001.SZ", "2024-12-31", 4.2)             # 首事件前最近一行

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])

    assert out.attrs["local_snapshot_bases"]["000001.SZ"] == pytest.approx(4.2)
    assert out.iloc[0]["back_factor"] == pytest.approx(2.0 * 4.2)


def test_state_base_still_honoured_for_stock_with_events():
    """正例: 有事件的股票仍然只认 STATE 里的固定基准，守卫不改变这条既有语义。"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000001", "2025-05-09", bonus_share=10.0)
    _insert_close(conn, "000001.SZ", "2025-05-08", 10.0)
    _insert_dense(conn, "000001.SZ", "2026-07-31", 3.9)
    conn.execute("INSERT INTO ADJ_FACTOR_LOCAL_STATE VALUES ('000001.SZ', 1.95)")

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])

    assert out.attrs["local_snapshot_bases"]["000001.SZ"] == pytest.approx(1.95)
    assert out.iloc[0]["back_factor"] == pytest.approx(2.0 * 1.95)


def test_state_owned_stock_still_withdraws_when_events_gone():
    """正例(边界): local 已管过的股票(有 STATE)事件被真删除时，走的是既有的「撤销」语义，
    不受 BUG-015 防重置守卫影响——守卫只针对 local 没管过、稠密表因子来源不明的股票。

    与 tests/db/test_adjust_snapshot.py::test_withdraw_last_and_all_events 同一约定，
    在此固化，防止后续加固守卫时误伤撤销路径。
    """
    conn = _seed_mem_conn()
    _insert_dense(conn, "000001.SZ", "2025-05-09", 2.0)
    conn.execute("INSERT INTO ADJ_FACTOR_LOCAL_STATE VALUES ('000001.SZ', 1.0)")
    # CAPITAL_DETAIL 为空：事件已被撤销

    out = _fetch(conn, [("000001", "SZ", "2026-08-03", "2026-08-04", "L")])

    assert out.attrs["local_snapshot_bases"] == {"000001.SZ": 1.0}


# ── 2026-09-12 BUG①：转增类事件漏采 ──────────────────────────────────────────
#
# gbbq 把破产重整等场景的资本公积转增记为「转增股上市」/「未知新类别」而非
# 「除权除息」，旧代码只认后者，导致因子不跳、后复权价出现虚假跳空
# （实测 000908.SZ 石药景峰 2026-03-11 每 10 股转增 10 股，后复权价假跌 31.6%）。

def test_fetch_accepts_capital_reserve_transfer_when_price_gapped():
    """正例: 「转增股上市」类事件在当日价格确实跳空时计入因子链，
    且比例取交易所 pre_close 而非 gbbq 的转增比例。

    复现 000908.SZ 石药景峰 2026-03-11：公告称每 10 股转增 10 股（gbbq 记 10.0），
    但转增股有相当部分用于偿债与引入重整投资人，交易所除权参考价为 4.36 而非
    6.37/2 = 3.185。
    """
    conn = _seed_mem_conn()
    _insert_event(conn, "000908", "2026-03-11", bonus_share=10.0,
                  category="转增股上市")
    _insert_close(conn, "000908.SZ", "2026-03-09", 6.37)
    _insert_close(conn, "000908.SZ", "2026-03-11", 4.58, pre_close=4.36)

    out = _fetch(conn, [("000908", "SZ", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    assert out["date"].tolist() == ["2026-03-11"]
    # 采信交易所: ratio = 6.37 / 4.36 = 1.4610（而非 gbbq 的 2.0）
    assert out.iloc[0]["back_factor"] == pytest.approx(6.37 / 4.36)


def test_fetch_rejects_capital_reserve_transfer_without_price_gap():
    """反例: 转增股登记到账不等于交易所做了除权——转增股若全部用于偿债和引入重整
    投资人，原股东未获配，当天不除权。此时必须丢弃该事件。

    复现 600136.SH ST明诚 2023-12-21：gbbq 记 10 转 25，但当日 pre_close 等于前收，
    交易所未除权。无条件采纳会凭空造出 +250% 的因子跳变。
    """
    conn = _seed_mem_conn()
    _insert_event(conn, "600136", "2023-12-21", bonus_share=25.0,
                  category="转增股上市")
    _insert_close(conn, "600136.SH", "2023-12-20", 2.23)
    _insert_close(conn, "600136.SH", "2023-12-21", 2.15, pre_close=2.23)

    out = _fetch(conn, [("600136", "SH", "2023-01-01", "2023-12-31", "L")])
    conn.close()

    assert out.empty


def test_fetch_rejects_capital_reserve_transfer_when_suspended():
    """反例: 转增事件当日停牌/无行情, 无从判断交易所是否除权, 保守丢弃"""
    conn = _seed_mem_conn()
    _insert_event(conn, "600136", "2023-12-21", bonus_share=25.0,
                  category="转增股上市")
    _insert_close(conn, "600136.SH", "2023-12-20", 2.23)
    # 当日无行情行

    out = _fetch(conn, [("600136", "SH", "2023-01-01", "2023-12-31", "L")])
    conn.close()

    assert out.empty


def test_transfer_skip_does_not_trip_incomplete_chain_guard(caplog):
    """反例(回归): 丢弃未跳空的转增事件是正常结果, 不得计入 skipped 而触发
    「local 事件链不完整，停止写入」守卫——否则该股常规除权事件也会一并写不进去"""
    conn = _seed_mem_conn()
    # 一条正常的除权除息 + 一条未跳空的转增类
    _insert_event(conn, "600136", "2023-06-15", dividend=1.0)
    _insert_close(conn, "600136.SH", "2023-06-14", 10.0)
    _insert_event(conn, "600136", "2023-12-21", bonus_share=25.0,
                  category="转增股上市")
    _insert_close(conn, "600136.SH", "2023-12-20", 2.23)
    _insert_close(conn, "600136.SH", "2023-12-21", 2.15, pre_close=2.23)

    with caplog.at_level(logging.INFO, logger="etl.datasource.local_xdr"):
        out = _fetch(conn, [("600136", "SH", "2023-01-01", "2023-12-31", "L")])
    conn.close()

    # 常规除权事件必须正常写出
    assert out["date"].tolist() == ["2023-06-15"]
    assert out.iloc[0]["back_factor"] == pytest.approx(10.0 / 9.9)
    assert "未见价格跳空" in caplog.text


def test_fetch_dedups_paired_transfer_categories():
    """正例: 「转增股上市」与「未知新类别」同日同值成对出现, 去重后只能算一次"""
    conn = _seed_mem_conn()
    _insert_event(conn, "000908", "2026-03-11", bonus_share=10.0,
                  category="转增股上市")
    _insert_event(conn, "000908", "2026-03-11", bonus_share=10.0,
                  category="未知新类别")
    _insert_close(conn, "000908.SZ", "2026-03-09", 6.37)
    _insert_close(conn, "000908.SZ", "2026-03-11", 4.58, pre_close=4.36)

    out = _fetch(conn, [("000908", "SZ", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    assert len(out) == 1
    # 若未去重会变成 (6.37/4.36)^2
    assert out.iloc[0]["back_factor"] == pytest.approx(6.37 / 4.36)


def test_regular_xdr_wins_when_same_day_has_transfer_label(mem_db=None):
    """正例: 同一 (code,date) 既有「除权除息」又被打上转增标签时, 走常规除权口径
    (不受转增类的跳空闸门约束)——实测 600074 2014-01-06 三个 category 同值"""
    conn = _seed_mem_conn()
    _insert_event(conn, "600074", "2014-01-06", bonus_share=3.55,
                  category="除权除息")
    _insert_event(conn, "600074", "2014-01-06", bonus_share=3.55,
                  category="转增股上市")
    _insert_close(conn, "600074.SH", "2014-01-03", 13.55)
    # 当日无行情 —— 常规除权不要求跳空佐证, 仍应按 gbbq 口径计入
    out = _fetch(conn, [("600074", "SH", "2014-01-01", "2014-12-31", "L")])
    conn.close()

    assert len(out) == 1
    # X = 13.55 / (1 + 3.55/10) = 10.0；ratio = 1.355
    assert out.iloc[0]["back_factor"] == pytest.approx(1.355)


def test_fetch_rejects_share_structure_categories():
    """反例: 「转配股上市」等股本变动类是「万股」口径(前后股本), 字段语义与除权完全不同,
    绝不能计入因子链 —— 混入会算出荒谬的因子"""
    conn = _seed_mem_conn()
    # 实测 000908 的真实记录形态: 前流通 8797.74万 / 前总 8797.74万 / 后流通 8797.74万
    #                              / 后总 17595.49万
    _insert_event(conn, "000908", "2026-03-11",
                  dividend=87977.41, allotment_price=87977.44,
                  bonus_share=87977.44, allotment_share=175954.88,
                  category="转配股上市")
    _insert_close(conn, "000908.SZ", "2026-03-09", 6.37)

    out = _fetch(conn, [("000908", "SZ", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    assert out.empty


# ── 2026-09-12 BUG②：除权参考价与交易所 pre_close 的偏差校正 ──────────────────
#
# 破产重整类转增, gbbq 的 bonus_share 是「总转增比例」, 但转增股并非全部分配给
# 原股东(一部分偿债/引入重整投资人), 交易所按原股东实际获得比例计算除权参考价。

def test_fetch_corrects_reference_price_by_exchange_preclose():
    """正例: 价格确实跳空、且 gbbq 推出的参考价偏差超 2% 时, 改以 pre_close 为准"""
    conn = _seed_mem_conn()
    # gbbq 说每 10 股转增 10 股 -> X = 10.0/2 = 5.0；
    # 但交易所实际 pre_close = 8.0(只有部分转增股给了原股东)
    _insert_event(conn, "600518", "2026-03-11", bonus_share=10.0)
    _insert_close(conn, "600518.SH", "2026-03-09", 10.0)
    _insert_close(conn, "600518.SH", "2026-03-11", 8.2, pre_close=8.0)

    out = _fetch(conn, [("600518", "SH", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    # 采信交易所: ratio = 10.0 / 8.0 = 1.25（而非 gbbq 的 10.0/5.0 = 2.0）
    assert out.iloc[0]["back_factor"] == pytest.approx(1.25)


def test_fetch_keeps_gbbq_when_price_did_not_gap():
    """反例: 当日 pre_close 等于上一交易日收盘(交易所未做除权处理, 如 2005-06 股改
    对价送股), 属于另一套口径, 不得校正 —— 否则会改动历史复权序列"""
    conn = _seed_mem_conn()
    _insert_event(conn, "600000", "2006-05-10", bonus_share=3.0)
    _insert_close(conn, "600000.SH", "2006-05-09", 10.0)
    # pre_close 与上一日收盘相同 = 交易所当天不除权
    _insert_close(conn, "600000.SH", "2006-05-10", 9.1, pre_close=10.0)

    out = _fetch(conn, [("600000", "SH", "2006-01-01", "2006-12-31", "L")])
    conn.close()

    # 保持 gbbq 口径: X = 10.0/(1+0.3) = 7.6923, ratio = 1.3
    assert out.iloc[0]["back_factor"] == pytest.approx(1.3)


def test_fetch_keeps_gbbq_when_deviation_within_tolerance():
    """反例: 偏差在阈值内(舍入级别)时保持 gbbq 口径, 不做校正"""
    conn = _seed_mem_conn()
    _insert_event(conn, "600000", "2026-03-11", dividend=5.0)
    _insert_close(conn, "600000.SH", "2026-03-09", 10.0)
    # gbbq: X = 10.0 - 0.5 = 9.5；交易所 9.51，偏差 0.1% < 2%
    _insert_close(conn, "600000.SH", "2026-03-11", 9.8, pre_close=9.51)

    out = _fetch(conn, [("600000", "SH", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    assert out.iloc[0]["back_factor"] == pytest.approx(10.0 / 9.5)


def test_fetch_keeps_gbbq_when_preclose_missing():
    """反例: 事件当日停牌/无行情导致 pre_close 缺失时, 只能用 gbbq 口径, 不得报错"""
    conn = _seed_mem_conn()
    _insert_event(conn, "600000", "2026-03-11", bonus_share=10.0)
    _insert_close(conn, "600000.SH", "2026-03-09", 10.0)
    # 当日无行情行

    out = _fetch(conn, [("600000", "SH", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    assert out.iloc[0]["back_factor"] == pytest.approx(2.0)


def test_fetch_ignores_suspended_day_preclose():
    """反例: 事件当日停牌(tradestatus=0)的 pre_close 是前收结转, 不是除权参考价,
    不得用于校正"""
    conn = _seed_mem_conn()
    _insert_event(conn, "600000", "2026-03-11", bonus_share=10.0)
    _insert_close(conn, "600000.SH", "2026-03-09", 10.0)
    _insert_close(conn, "600000.SH", "2026-03-11", 10.0, tradestatus=0, pre_close=10.0)

    out = _fetch(conn, [("600000", "SH", "2026-01-01", "2026-12-31", "L")])
    conn.close()

    # 停牌行被忽略 -> 仍走 gbbq 口径 ratio = 2.0
    assert out.iloc[0]["back_factor"] == pytest.approx(2.0)
