# 修改记录:
#   2026-08-18  Claude  新增: export_etl_tables / import_etl_tables 的库级正反测试
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
    assert counts == {"ADJ_FACTOR": 2, "ADJ_FACTOR_RAW": 1}


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
