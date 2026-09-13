"""写库: 先删当日再插入(幂等), 北交所丢弃。"""
import pandas as pd

from util import dbutil

SUSP_COLS = ["symbol", "name", "suspend_time", "resume_deadline",
             "suspend_period", "suspend_reason", "market", "expect_resume"]


def _susp_df(symbols):
    n = len(symbols)
    return pd.DataFrame({
        "symbol": list(symbols),
        "name": [f"股票{s}" for s in symbols],
        "suspend_time": pd.to_datetime(["2024-04-26 10:30:00"] * n),
        "resume_deadline": pd.to_datetime(["2024-04-26 15:00:00"] * n),
        "suspend_period": ["盘中停牌"] * n,
        "suspend_reason": ["异常波动"] * n,
        "market": ["上交所"] * n,
        "expect_resume": pd.to_datetime(["2024-04-26 13:00:00"] * n),
    })


def _pool_df(symbols):
    n = len(symbols)
    return pd.DataFrame({
        "symbol": list(symbols),
        "name": [f"股票{s}" for s in symbols],
        "pct_change": [10.0] * n,
        "close": [11.0] * n,
        "amount": [1.0e8] * n,
        "float_mv": [5.0e9] * n,
        "total_mv": [8.0e9] * n,
        "turnover_rate": [3.5] * n,
        "seal_amount": [1.2e8] * n,
        "last_seal_time": ["09:35:00"] * n,
        "industry": ["电子"] * n,
        "first_seal_time": ["09:31:00"] * n,
        "broken_times": [0] * n,
        "limit_stat": ["1/1"] * n,
        "boards": [1] * n,
        "pe_dynamic": [None] * n,
        "board_amount": [None] * n,
        "down_days": [None] * n,
        "open_times": [None] * n,
    })


def test_save_suspension_writes_standard_codes(mem_db):
    """正例: 裸码转标准代码入库"""
    n = dbutil.save_suspension_to_db(_susp_df(["600001", "000002"]),
                                     "2024-04-26", mem_db)
    assert n == 2
    codes = [r[0] for r in mem_db.execute(
        "SELECT code FROM SUSPENSION_DAILY ORDER BY code").fetchall()]
    assert codes == ["000002.SZ", "600001.SH"]


def test_save_suspension_drops_bj_codes(mem_db):
    """反例: 北交所代码被丢弃, 不入库"""
    n = dbutil.save_suspension_to_db(_susp_df(["600001", "430047"]),
                                     "2024-04-26", mem_db)
    assert n == 1
    codes = [r[0] for r in mem_db.execute(
        "SELECT code FROM SUSPENSION_DAILY").fetchall()]
    assert codes == ["600001.SH"]


def test_save_suspension_is_idempotent(mem_db):
    """正例: 同日跑两次, 结果一致"""
    for _ in range(2):
        dbutil.save_suspension_to_db(_susp_df(["600001", "000002"]),
                                     "2024-04-26", mem_db)
    assert mem_db.execute("SELECT COUNT(*) FROM SUSPENSION_DAILY").fetchone()[0] == 2


def test_save_suspension_removes_stale_rows(mem_db):
    """反例(幽灵行): 第二次该日成员缩减, 旧成员必须被删除

    这是不用 INSERT OR REPLACE 的理由: 它只覆盖同主键行、不删多余旧行。
    """
    dbutil.save_suspension_to_db(_susp_df(["600001", "000002", "600003"]),
                                 "2024-04-26", mem_db)
    dbutil.save_suspension_to_db(_susp_df(["600001", "000002"]),
                                 "2024-04-26", mem_db)
    codes = {r[0] for r in mem_db.execute(
        "SELECT code FROM SUSPENSION_DAILY").fetchall()}
    assert codes == {"600001.SH", "000002.SZ"}


def test_save_suspension_other_dates_untouched(mem_db):
    """反例: 删除必须限定当日, 不得波及其他日期"""
    dbutil.save_suspension_to_db(_susp_df(["600001"]), "2024-04-25", mem_db)
    dbutil.save_suspension_to_db(_susp_df(["000002"]), "2024-04-26", mem_db)
    assert mem_db.execute("SELECT COUNT(*) FROM SUSPENSION_DAILY").fetchone()[0] == 2


def test_save_limit_pool_deletes_only_same_limit_type(mem_db):
    """反例(关键): 重写涨停池不得删掉当日跌停池的行"""
    dbutil.save_limit_pool_to_db(_pool_df(["000003"]), "2026-09-11", "D", mem_db)
    dbutil.save_limit_pool_to_db(_pool_df(["600001"]), "2026-09-11", "U", mem_db)
    dbutil.save_limit_pool_to_db(_pool_df(["600002"]), "2026-09-11", "U", mem_db)
    rows = {(r[0], r[1]) for r in mem_db.execute(
        "SELECT code, limit_type FROM LIMIT_POOL_DAILY").fetchall()}
    assert rows == {("000003.SZ", "D"), ("600002.SH", "U")}


def test_save_limit_pool_empty_frame_still_clears_the_day(mem_db):
    """正例: 传空帧 -> 当日该方向被清空并返回 0(调用方据此判定接口返回空)"""
    dbutil.save_limit_pool_to_db(_pool_df(["600001"]), "2026-09-11", "U", mem_db)
    n = dbutil.save_limit_pool_to_db(_pool_df([]), "2026-09-11", "U", mem_db)
    assert n == 0
    assert mem_db.execute(
        "SELECT COUNT(*) FROM LIMIT_POOL_DAILY WHERE limit_type = 'U'"
    ).fetchone()[0] == 0


def test_save_suspension_empty_frame_defaults_to_warning(mem_db, caplog):
    """正例: 不传 filtered_empty(默认 False) -> 空帧仍打 WARNING, 维持原有行为"""
    with caplog.at_level("WARNING", logger="etl.util.dbutil"):
        n = dbutil.save_suspension_to_db(_susp_df([]), "2026-09-11", mem_db)
    assert n == 0
    assert "无停牌数据，已清空当日" in caplog.text
    assert not any(r.levelname == "INFO" and "过滤后" in r.message
                  for r in caplog.records)


def test_save_suspension_filtered_empty_logs_info_not_warning(mem_db, caplog):
    """反例(打架修复): filtered_empty=True 时空帧打 INFO, 不再产生自相矛盾的 WARNING"""
    with caplog.at_level("INFO", logger="etl.util.dbutil"):
        n = dbutil.save_suspension_to_db(_susp_df([]), "2026-09-11", mem_db,
                                         filtered_empty=True)
    assert n == 0
    assert "过滤后无处于停牌状态的股票" in caplog.text
    assert not any(r.levelname == "WARNING" for r in caplog.records)
