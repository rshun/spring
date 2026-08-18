# 修改记录:
#   2026-08-18  Claude  新增: export_etl_tables 程序->表解析 / WHERE 拼装 的正反测试
"""tools.export_etl_tables 纯逻辑单元测试(不依赖数据库)"""
import pytest

from tools.export_etl_tables import (
    PROGRAMS,
    build_where,
    normalize_updated_since,
    parquet_name,
    resolve_programs,
    resolve_table_specs,
)


# ---------- 正例: 程序解析 ----------

def test_resolve_programs_all():
    assert resolve_programs(["all"]) == list(PROGRAMS)


def test_resolve_programs_keeps_fixed_order():
    """无论传入顺序如何，都按 PROGRAMS 固定顺序返回"""
    assert resolve_programs(["fetch_index", "adjust"]) == ["adjust", "fetch_index"]


def test_resolve_programs_dedup_and_normalize():
    assert resolve_programs([" Import_Daily ", "import_daily"]) == ["import_daily"]


# ---------- 反例: 程序解析 ----------

def test_resolve_programs_empty_raises():
    with pytest.raises(ValueError):
        resolve_programs([])


def test_resolve_programs_blank_only_raises():
    with pytest.raises(ValueError):
        resolve_programs(["  "])


def test_resolve_programs_unknown_raises():
    with pytest.raises(ValueError, match="未知程序"):
        resolve_programs(["import_daily", "no_such_prog"])


def test_resolve_programs_none_raises():
    with pytest.raises(ValueError):
        resolve_programs(None)


# ---------- 正例: 表规格解析 ----------

def test_specs_import_daily_only():
    specs = resolve_table_specs(["import_daily"])
    assert set(specs) == {"STOCK_DAILY", "DAILY_BASIC"}
    assert specs["STOCK_DAILY"].board_scope == "stock"
    assert specs["DAILY_BASIC"].board_scope is None
    # DAILY_BASIC 只导出 import_daily 负责的列
    assert "limit_up" not in specs["DAILY_BASIC"].columns
    assert "turnover_rate" in specs["DAILY_BASIC"].columns


def test_specs_fetch_index_only():
    specs = resolve_table_specs(["fetch_index"])
    assert set(specs) == {"STOCK_DAILY"}
    assert specs["STOCK_DAILY"].board_scope == "index"


def test_specs_adjust_only():
    specs = resolve_table_specs(["adjust"])
    assert set(specs) == {"ADJ_FACTOR", "ADJ_FACTOR_RAW"}
    assert all(s.date_col == "trade_date" for s in specs.values())


def test_specs_two_programs_merge_stock_daily_scope():
    """同时选个股和指数时，STOCK_DAILY 不再按 board 过滤"""
    specs = resolve_table_specs(["import_daily", "fetch_index"])
    assert set(specs) == {"STOCK_DAILY", "DAILY_BASIC"}
    assert specs["STOCK_DAILY"].board_scope is None


def test_specs_all_covers_four_tables():
    assert set(resolve_table_specs(["all"])) == {
        "STOCK_DAILY", "DAILY_BASIC", "ADJ_FACTOR", "ADJ_FACTOR_RAW"
    }


# ---------- 正反例: WHERE 拼装 ----------

def test_build_where_stock_scope_has_board_filter():
    spec = resolve_table_specs(["import_daily"])["STOCK_DAILY"]
    where = build_where(spec, "2026-08-17", "2026-08-18")
    assert "date BETWEEN DATE '2026-08-17' AND DATE '2026-08-18'" in where
    assert "board <> 'INDEX'" in where


def test_build_where_index_scope_has_index_filter():
    spec = resolve_table_specs(["fetch_index"])["STOCK_DAILY"]
    assert "board = 'INDEX'" in build_where(spec, "2026-08-18", "2026-08-18")


def test_build_where_merged_scope_has_no_board_filter():
    spec = resolve_table_specs(["import_daily", "fetch_index"])["STOCK_DAILY"]
    assert "board" not in build_where(spec, "2026-08-18", "2026-08-18")


def test_build_where_updated_since_only_for_adj_tables():
    adj = resolve_table_specs(["adjust"])["ADJ_FACTOR"]
    basic = resolve_table_specs(["import_daily"])["DAILY_BASIC"]
    since = "2026-08-18 00:00:00"
    assert "updated_at >=" in build_where(adj, "2026-08-18", "2026-08-18", since)
    # 反例: 非复权表不应被 updated_at 条件放大范围
    assert "updated_at" not in build_where(basic, "2026-08-18", "2026-08-18", since)


# ---------- 正反例: --updated-since 规范化 ----------

def test_normalize_updated_since_date_only():
    assert normalize_updated_since("2026-08-18") == "2026-08-18 00:00:00"


def test_normalize_updated_since_full_timestamp():
    assert normalize_updated_since(" 2026-08-18 09:30:00 ") == "2026-08-18 09:30:00"


def test_normalize_updated_since_none():
    assert normalize_updated_since(None) is None


def test_normalize_updated_since_bad_format_raises():
    with pytest.raises(ValueError, match="格式错误"):
        normalize_updated_since("18/08/2026")


def test_normalize_updated_since_injection_text_raises():
    """反例: 非法内容不能被拼进 SQL"""
    with pytest.raises(ValueError):
        normalize_updated_since("2026-08-18'; DROP TABLE ADJ_FACTOR; --")


def test_parquet_name():
    assert parquet_name("STOCK_DAILY") == "stock_daily.parquet"
