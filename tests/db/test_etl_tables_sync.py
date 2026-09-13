# 修改记录:
#   2026-08-18  Claude  新增: export_etl_tables / import_etl_tables 的库级正反测试
#   2026-09-13  Claude  接入 sync_suspension(SUSPENSION_DAILY) / sync_limit_pool
#                       (LIMIT_POOL_DAILY) 后补测: 往返一致 + 幽灵行必须被删掉(核心反例,
#                       用来守住"不能用 upsert"这个决策) + 不误伤其它日期/另一方向 + dry-run
"""导出 + 导入的库级测试：按程序切分、日期区间、幂等、宽表列保护"""
from pathlib import Path

import duckdb
import pytest

from tests.conftest import insert_stock_info
from tools.export_etl_tables import export_tables, resolve_table_specs
from tools.import_etl_tables import import_tables

STOCK = "000001.SZ"
INDEX = "000300.SH"
D1 = "2026-08-17"
D2 = "2026-08-18"


def _fresh_db() -> duckdb.DuckDBPyConnection:
    """再建一个独立的 in-memory 库，充当"另一台机器"的目标库"""
    conn = duckdb.connect(":memory:")
    schema = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
    conn.execute(schema.read_text(encoding="utf-8"))
    return conn


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    insert_stock_info(conn, "000001", "SZ", "MAIN", "2000-01-01")
    insert_stock_info(conn, "000300", "SH", "INDEX", "2005-04-08")

    for code in (STOCK, INDEX):
        for d in (D1, D2):
            conn.execute(
                "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
                "pre_close, tradestatus, volume, amount) "
                "VALUES (?, ?, 10, 11, 9, 10.5, 10, 1, 1000, 10500)",
                [code, d]
            )

    for d in (D1, D2):
        conn.execute(
            "INSERT INTO DAILY_BASIC (code, trade_date, turnover_rate, pe, pb, is_st, "
            "limit_up, volume_ratio) VALUES (?, ?, 1.5, 12.3, 1.1, 0, 11.55, 0.98)",
            [STOCK, d]
        )
        conn.execute(
            "INSERT INTO ADJ_FACTOR (code, trade_date, fore_factor, back_factor, "
            "adjust_factor, created_at, updated_at) "
            "VALUES (?, ?, 0.9, 1.1, 1.0, now(), now())",
            [STOCK, d]
        )

    conn.execute(
        "INSERT INTO ADJ_FACTOR_RAW (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, created_at, updated_at) "
        "VALUES (?, ?, 0.9, 1.1, 1.0, now(), now())",
        [STOCK, D2]
    )


@pytest.fixture
def seeded(mem_db):
    _seed(mem_db)
    return mem_db


# ---------- 正例: 按程序导出 ----------

def test_export_import_daily_only_stock_rows(seeded, tmp_path):
    specs = resolve_table_specs(["import_daily"])
    counts = export_tables(seeded, specs, D1, D2, tmp_path)

    assert counts == {"STOCK_DAILY": 2, "DAILY_BASIC": 2}
    assert (tmp_path / "stock_daily.parquet").exists()
    assert (tmp_path / "daily_basic.parquet").exists()
    # 未选中的程序不产出文件
    assert not (tmp_path / "adj_factor.parquet").exists()

    codes = seeded.execute(
        f"SELECT DISTINCT code FROM read_parquet('{(tmp_path / 'stock_daily.parquet').as_posix()}')"
    ).fetchall()
    assert codes == [(STOCK,)]


def test_export_fetch_index_only_index_rows(seeded, tmp_path):
    specs = resolve_table_specs(["fetch_index"])
    counts = export_tables(seeded, specs, D1, D2, tmp_path)

    assert counts == {"STOCK_DAILY": 2}
    assert not (tmp_path / "daily_basic.parquet").exists()

    codes = seeded.execute(
        f"SELECT DISTINCT code FROM read_parquet('{(tmp_path / 'stock_daily.parquet').as_posix()}')"
    ).fetchall()
    assert codes == [(INDEX,)]


def test_export_two_programs_covers_both_boards(seeded, tmp_path):
    specs = resolve_table_specs(["import_daily", "fetch_index"])
    counts = export_tables(seeded, specs, D1, D2, tmp_path)

    assert counts["STOCK_DAILY"] == 4  # 个股 2 + 指数 2
    assert counts["DAILY_BASIC"] == 2


def test_export_adjust_only(seeded, tmp_path):
    counts = export_tables(seeded, resolve_table_specs(["adjust"]), D1, D2, tmp_path)
    assert counts == {"ADJ_FACTOR": 2, "ADJ_FACTOR_RAW": 1, "ADJ_FACTOR_LOCAL": 0, "ADJ_FACTOR_LOCAL_STATE": 0}


# ---------- 正例: 日期区间 ----------

def test_export_respects_date_range(seeded, tmp_path):
    counts = export_tables(seeded, resolve_table_specs(["import_daily"]), D2, D2, tmp_path)
    assert counts == {"STOCK_DAILY": 1, "DAILY_BASIC": 1}


def test_export_updated_since_pulls_old_adj_event(seeded, tmp_path):
    """事件日早于导出区间时，可用 --updated-since 补捞"""
    seeded.execute(
        "INSERT INTO ADJ_FACTOR_RAW (code, trade_date, fore_factor, back_factor, "
        "adjust_factor, created_at, updated_at) "
        "VALUES (?, DATE '2020-01-02', 0.8, 1.2, 1.0, now(), now())",
        [STOCK]
    )
    specs = resolve_table_specs(["adjust"])

    assert export_tables(seeded, specs, D2, D2, tmp_path)["ADJ_FACTOR_RAW"] == 1
    counts = export_tables(seeded, specs, D2, D2, tmp_path,
                           updated_since="2020-01-01 00:00:00")
    assert counts["ADJ_FACTOR_RAW"] == 2


# ---------- 正例: 导入 ----------

def test_import_into_empty_db(seeded, tmp_path):
    specs = resolve_table_specs(["all"])
    export_tables(seeded, specs, D1, D2, tmp_path)

    target = _fresh_db()
    try:
        stats = import_tables(target, tmp_path, list(specs))
        assert stats["STOCK_DAILY"]["after"] == 4
        assert stats["DAILY_BASIC"]["after"] == 2
        assert stats["ADJ_FACTOR"]["after"] == 2
        assert stats["ADJ_FACTOR_RAW"]["after"] == 1
    finally:
        target.close()


def test_import_is_idempotent(seeded, tmp_path):
    specs = resolve_table_specs(["all"])
    export_tables(seeded, specs, D1, D2, tmp_path)

    target = _fresh_db()
    try:
        import_tables(target, tmp_path, list(specs))
        stats = import_tables(target, tmp_path, list(specs))
        assert stats["STOCK_DAILY"]["before"] == stats["STOCK_DAILY"]["after"] == 4
        assert stats["ADJ_FACTOR"]["before"] == stats["ADJ_FACTOR"]["after"] == 2
    finally:
        target.close()


def test_import_preserves_other_daily_basic_columns(seeded, tmp_path):
    """DAILY_BASIC 宽表中由其它程序生成的列不能被导入覆盖"""
    specs = resolve_table_specs(["import_daily"])
    export_tables(seeded, specs, D1, D2, tmp_path)

    target = _fresh_db()
    try:
        # 目标库已有别的程序算好的涨跌停价/量比，但缺 turnover_rate
        target.execute(
            "INSERT INTO DAILY_BASIC (code, trade_date, limit_up, limit_down, volume_ratio) "
            "VALUES (?, ?, 99.9, 88.8, 1.23)", [STOCK, D2]
        )
        import_tables(target, tmp_path, list(specs))

        row = target.execute(
            "SELECT turnover_rate, pe, limit_up, limit_down, volume_ratio "
            "FROM DAILY_BASIC WHERE code = ? AND trade_date = ?", [STOCK, D2]
        ).fetchone()
        assert row[0] == 1.5      # 导入补上
        assert row[1] == 12.3     # 导入补上
        assert row[2] == 99.9     # 原值保留
        assert row[3] == 88.8     # 原值保留
        assert row[4] == 1.23     # 原值保留
    finally:
        target.close()


def test_import_updates_changed_rows(seeded, tmp_path):
    specs = resolve_table_specs(["import_daily"])
    export_tables(seeded, specs, D2, D2, tmp_path)

    target = _fresh_db()
    try:
        target.execute(
            "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, pre_close, "
            "tradestatus, volume, amount) VALUES (?, ?, 1, 1, 1, 1, 1, 0, 0, 0)",
            [STOCK, D2]
        )
        import_tables(target, tmp_path, list(specs))
        close, volume = target.execute(
            "SELECT close, volume FROM STOCK_DAILY WHERE code = ? AND date = ?",
            [STOCK, D2]
        ).fetchone()
        assert close == 10.5
        assert volume == 1000
    finally:
        target.close()


# ---------- 反例: 边界与异常 ----------

def test_export_empty_range_writes_zero_row_file(seeded, tmp_path):
    counts = export_tables(seeded, resolve_table_specs(["import_daily"]),
                           "2019-01-01", "2019-01-02", tmp_path)
    assert counts == {"STOCK_DAILY": 0, "DAILY_BASIC": 0}
    assert (tmp_path / "stock_daily.parquet").exists()


def test_import_zero_row_file_changes_nothing(seeded, tmp_path):
    specs = resolve_table_specs(["import_daily"])
    export_tables(seeded, specs, "2019-01-01", "2019-01-02", tmp_path)

    target = _fresh_db()
    try:
        stats = import_tables(target, tmp_path, list(specs))
        assert stats["STOCK_DAILY"]["before"] == stats["STOCK_DAILY"]["after"] == 0
    finally:
        target.close()


def test_import_missing_file_is_skipped(seeded, tmp_path):
    """反例: 目录里缺少某表的 parquet 时跳过，不抛异常"""
    export_tables(seeded, resolve_table_specs(["adjust"]), D1, D2, tmp_path)

    target = _fresh_db()
    try:
        stats = import_tables(target, tmp_path, ["STOCK_DAILY", "ADJ_FACTOR", "ADJ_FACTOR_RAW"])
        assert "STOCK_DAILY" not in stats
        assert stats["ADJ_FACTOR"]["after"] == 2
    finally:
        target.close()


def test_import_empty_dir_returns_empty_stats(tmp_path):
    target = _fresh_db()
    try:
        assert import_tables(target, tmp_path, ["STOCK_DAILY"]) == {}
    finally:
        target.close()


def test_import_dry_run_does_not_write(seeded, tmp_path):
    specs = resolve_table_specs(["import_daily"])
    export_tables(seeded, specs, D1, D2, tmp_path)

    target = _fresh_db()
    try:
        stats = import_tables(target, tmp_path, list(specs), dry_run=True)
        assert stats["STOCK_DAILY"]["src"] == 2
        assert target.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0] == 0
        assert target.execute("SELECT COUNT(*) FROM DAILY_BASIC").fetchone()[0] == 0
    finally:
        target.close()


def test_import_rolls_back_on_error(seeded, tmp_path, monkeypatch):
    """反例: 中途出错时整体回滚，不留半批数据"""
    import tools.import_etl_tables as mod

    specs = resolve_table_specs(["all"])
    export_tables(seeded, specs, D1, D2, tmp_path)

    broken = dict(mod.UPSERT_SQL)
    broken["ADJ_FACTOR"] = "INSERT INTO NO_SUCH_TABLE SELECT * FROM read_parquet('{src}')"
    monkeypatch.setattr(mod, "UPSERT_SQL", broken)

    target = _fresh_db()
    try:
        with pytest.raises(Exception):
            mod.import_tables(target, tmp_path, list(specs))
        # STOCK_DAILY 在出错表之前已执行，回滚后应为 0
        assert target.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0] == 0
    finally:
        target.close()


def test_all_factor_tables_have_sync_mapping(mem_db):
    from tools.import_etl_tables import UPSERT_SQL
    tables = {r[0] for r in mem_db.execute("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'ADJ_FACTOR%'").fetchall()}
    assert tables <= set(resolve_table_specs(["adjust"]))
    assert tables <= set(UPSERT_SQL)


# ---------- 正反例: 快照表(SUSPENSION_DAILY / LIMIT_POOL_DAILY) 先删后插 ----------

SD0 = "2026-09-10"
SD1 = "2026-09-11"
SD2 = "2026-09-12"


def _insert_suspension(conn: duckdb.DuckDBPyConnection, code: str, trade_date: str,
                       name: str = "测试停牌") -> None:
    conn.execute(
        "INSERT INTO SUSPENSION_DAILY (code, trade_date, name, source) "
        "VALUES (?, ?, ?, 'akstock')",
        [code, trade_date, name]
    )


def _insert_limit_pool(conn: duckdb.DuckDBPyConnection, code: str, trade_date: str,
                       limit_type: str, name: str = "测试涨跌停") -> None:
    conn.execute(
        "INSERT INTO LIMIT_POOL_DAILY (code, trade_date, limit_type, name, source) "
        "VALUES (?, ?, ?, ?, 'akstock')",
        [code, trade_date, limit_type, name]
    )


def test_snapshot_tables_disjoint_from_upsert_sql():
    """SNAPSHOT_TABLES / UPSERT_SQL 是互斥的两类标记，不应有表同时出现在两边"""
    from tools.import_etl_tables import SNAPSHOT_TABLES, UPSERT_SQL
    assert SNAPSHOT_TABLES == {"SUSPENSION_DAILY", "LIMIT_POOL_DAILY"}
    assert SNAPSHOT_TABLES.isdisjoint(UPSERT_SQL)


def test_export_import_suspension_roundtrip(mem_db, tmp_path):
    """正例: 往返一致"""
    _insert_suspension(mem_db, "000001.SZ", SD1, "A")
    _insert_suspension(mem_db, "000002.SZ", SD1, "B")

    specs = resolve_table_specs(["sync_suspension"])
    export_tables(mem_db, specs, SD1, SD1, tmp_path)

    target = _fresh_db()
    try:
        stats = import_tables(target, tmp_path, list(specs))
        assert stats["SUSPENSION_DAILY"]["after"] == 2
        codes = {r[0] for r in target.execute("SELECT code FROM SUSPENSION_DAILY").fetchall()}
        assert codes == {"000001.SZ", "000002.SZ"}
    finally:
        target.close()


def test_export_import_limit_pool_roundtrip(mem_db, tmp_path):
    """正例: 往返一致，涨跌停两个方向都要如实到达"""
    _insert_limit_pool(mem_db, "000001.SZ", SD1, "U")
    _insert_limit_pool(mem_db, "000002.SZ", SD1, "D")

    specs = resolve_table_specs(["sync_limit_pool"])
    export_tables(mem_db, specs, SD1, SD1, tmp_path)

    target = _fresh_db()
    try:
        stats = import_tables(target, tmp_path, list(specs))
        assert stats["LIMIT_POOL_DAILY"]["after"] == 2
        rows = {(r[0], r[1]) for r in
                target.execute("SELECT code, limit_type FROM LIMIT_POOL_DAILY").fetchall()}
        assert rows == {("000001.SZ", "U"), ("000002.SZ", "D")}
    finally:
        target.close()


def test_import_suspension_removes_ghost_row(mem_db, tmp_path):
    """反例(最关键): 源库当日只有 A/B，目标库当日原有 A/B/C -> 导入后 C(幽灵行)必须被删掉。

    这条直接守住"这两张表不能用 upsert"的决策：如果实现被改回 upsert，
    C 不会被删，本测试必须变红。
    """
    _insert_suspension(mem_db, "000001.SZ", SD1, "A")
    _insert_suspension(mem_db, "000002.SZ", SD1, "B")

    specs = resolve_table_specs(["sync_suspension"])
    export_tables(mem_db, specs, SD1, SD1, tmp_path)

    target = _fresh_db()
    try:
        _insert_suspension(target, "000001.SZ", SD1, "A")
        _insert_suspension(target, "000002.SZ", SD1, "B")
        _insert_suspension(target, "000003.SZ", SD1, "C-幽灵行")

        import_tables(target, tmp_path, list(specs))

        codes = {r[0] for r in
                 target.execute("SELECT code FROM SUSPENSION_DAILY WHERE trade_date = ?",
                                [SD1]).fetchall()}
        assert codes == {"000001.SZ", "000002.SZ"}
        assert "000003.SZ" not in codes
    finally:
        target.close()


def test_import_suspension_does_not_affect_other_dates(mem_db, tmp_path):
    """反例: 只导入 SD1 当日的数据，不得影响 SD0 / SD2 的行"""
    _insert_suspension(mem_db, "000001.SZ", SD1, "本日")

    specs = resolve_table_specs(["sync_suspension"])
    export_tables(mem_db, specs, SD1, SD1, tmp_path)

    target = _fresh_db()
    try:
        _insert_suspension(target, "000009.SZ", SD0, "前一天")
        _insert_suspension(target, "000008.SZ", SD2, "后一天")

        import_tables(target, tmp_path, list(specs))

        before_day = {r[0] for r in
                      target.execute("SELECT code FROM SUSPENSION_DAILY WHERE trade_date = ?",
                                     [SD0]).fetchall()}
        after_day = {r[0] for r in
                     target.execute("SELECT code FROM SUSPENSION_DAILY WHERE trade_date = ?",
                                    [SD2]).fetchall()}
        assert before_day == {"000009.SZ"}
        assert after_day == {"000008.SZ"}
    finally:
        target.close()


def test_import_limit_pool_cleans_both_directions_same_day(mem_db, tmp_path):
    """反例: 涨停/跌停同一天在同一个 parquet 里，先删后插必须把两个方向的幽灵行一起清掉"""
    _insert_limit_pool(mem_db, "000001.SZ", SD1, "U")
    _insert_limit_pool(mem_db, "000002.SZ", SD1, "D")

    specs = resolve_table_specs(["sync_limit_pool"])
    export_tables(mem_db, specs, SD1, SD1, tmp_path)

    target = _fresh_db()
    try:
        _insert_limit_pool(target, "000001.SZ", SD1, "U")
        _insert_limit_pool(target, "000002.SZ", SD1, "D")
        _insert_limit_pool(target, "000099.SZ", SD1, "U", "涨停幽灵行")
        _insert_limit_pool(target, "000098.SZ", SD1, "D", "跌停幽灵行")

        import_tables(target, tmp_path, list(specs))

        rows = {(r[0], r[1]) for r in
                target.execute("SELECT code, limit_type FROM LIMIT_POOL_DAILY WHERE trade_date = ?",
                               [SD1]).fetchall()}
        assert rows == {("000001.SZ", "U"), ("000002.SZ", "D")}
    finally:
        target.close()


def test_import_suspension_dry_run_does_not_write(mem_db, tmp_path):
    """反例: --dry-run 不得写库，目标库原有行(哪怕是幽灵行)也不能被删或改"""
    _insert_suspension(mem_db, "000001.SZ", SD1, "A")

    specs = resolve_table_specs(["sync_suspension"])
    export_tables(mem_db, specs, SD1, SD1, tmp_path)

    target = _fresh_db()
    try:
        _insert_suspension(target, "000099.SZ", SD1, "应保留-幽灵行")
        stats = import_tables(target, tmp_path, list(specs), dry_run=True)
        assert stats["SUSPENSION_DAILY"]["src"] == 1

        codes = {r[0] for r in target.execute("SELECT code FROM SUSPENSION_DAILY").fetchall()}
        assert codes == {"000099.SZ"}
    finally:
        target.close()


def test_local_factor_roundtrip_updated_since_and_idempotency(mem_db, tmp_path):
    mem_db.execute("INSERT INTO ADJ_FACTOR_LOCAL VALUES ('000001.SZ', '2020-01-02', 0.5, 3.9, 3.9, '2020-01-01', '2026-08-18')")
    specs = resolve_table_specs(["adjust"])
    counts = export_tables(mem_db, specs, D2, D2, tmp_path, updated_since=D2)
    assert counts["ADJ_FACTOR_LOCAL"] == 1
    target = _fresh_db()
    try:
        target.execute("INSERT INTO ADJ_FACTOR_LOCAL VALUES ('000001.SZ', '2020-01-02', 1, 1, 1, '2019-01-01', '2020-01-01')")
        for _ in range(2):
            import_tables(target, tmp_path, list(specs))
        rows = target.execute("SELECT fore_factor, back_factor, adjust_factor, CAST(created_at AS DATE), CAST(updated_at AS DATE) FROM ADJ_FACTOR_LOCAL").fetchall()
        from datetime import date
        assert rows == [(0.5, 3.9, 3.9, date(2019, 1, 1), date(2026, 8, 18))]
    finally:
        target.close()
