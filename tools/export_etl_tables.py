# 修改记录:
#   2026-08-18  Claude  新增: 按程序(adjust/import_daily/fetch_index)导出其写入的表,
#                       用于把执行成功机器上的增量数据搬运到执行失败的机器
"""
功能: 按 ETL 程序维度，导出该程序写入的表数据(parquet)，供另一台机器导入。

各程序写入的表(只导出"写入表"，STOCK_INFO / TRADE_CAL 属只读依赖，不在此列):
  import_daily  -> STOCK_DAILY(个股)、DAILY_BASIC(仅 turnover_rate/pe/pb/is_st 四列)
  fetch_index   -> STOCK_DAILY(指数)
  adjust        -> ADJ_FACTOR、ADJ_FACTOR_RAW

说明:
  1) STOCK_DAILY 为个股与指数共用表，按 STOCK_INFO.board 区分:
     只选 import_daily 时仅导出个股行，只选 fetch_index 时仅导出指数行，两者都选则不过滤。
  2) DAILY_BASIC 是多程序共用的宽表，涨跌停价/量比/股本/市值等列由其它程序生成，
     故只导出 import_daily 负责的四列，避免导入端覆盖掉这些列。

输入参数:
  -p, --programs      程序范围: adjust / import_daily / fetch_index / all (默认 all，可多选)
  -b, --begin         起始日期 (格式: YYYYMMDD)，默认为当天
  -e, --end           结束日期 (格式: YYYYMMDD)，默认为当天
  -o, --out           输出目录 (默认 tmp/db_sync/out)
      --db            源库路径 (默认取 config.yaml 中当前生效的库)
      --updated-since 额外导出 updated_at >= 该时间的 ADJ_FACTOR/ADJ_FACTOR_RAW 行
                      (格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)

用法:
  python -m tools.export_etl_tables -b 20260817 -e 20260818
  python -m tools.export_etl_tables -p import_daily -b 20260818
  python -m tools.export_etl_tables -p import_daily fetch_index -b 20260818 -o D:/sync/out
  python -m tools.export_etl_tables -p adjust -b 20260818 --updated-since 2026-08-18
"""
import argparse
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from util import myutil
from util import validators as pv

logger = logging.getLogger("etl.tools.export_etl_tables")

PROGRAMS = ("adjust", "import_daily", "fetch_index")

# 表名 -> (导出列, 日期字段)
TABLE_META: dict[str, tuple[str, str]] = {
    "STOCK_DAILY": ("*", "date"),
    "DAILY_BASIC": ("code, trade_date, turnover_rate, pe, pb, is_st", "trade_date"),
    "ADJ_FACTOR": ("*", "trade_date"),
    "ADJ_FACTOR_RAW": ("*", "trade_date"),
}

# 程序 -> {表名: board 范围}；board 范围为 None 表示该表不区分个股/指数
PROGRAM_TABLES: dict[str, dict[str, str | None]] = {
    "import_daily": {"STOCK_DAILY": "stock", "DAILY_BASIC": None},
    "fetch_index": {"STOCK_DAILY": "index"},
    "adjust": {"ADJ_FACTOR": None, "ADJ_FACTOR_RAW": None},
}

_BOARD_FILTER = {
    "stock": "code IN (SELECT code FROM STOCK_INFO WHERE board <> 'INDEX')",
    "index": "code IN (SELECT code FROM STOCK_INFO WHERE board = 'INDEX')",
}


@dataclass(frozen=True)
class TableSpec:
    """一张待导出表的导出规格"""
    table: str
    columns: str
    date_col: str
    board_scope: str | None  # stock | index | None(不按 board 过滤)


def parquet_name(table: str) -> str:
    """表名 -> parquet 文件名"""
    return f"{table.lower()}.parquet"


def resolve_programs(programs: list[str] | None) -> list[str]:
    """展开 all、去重、校验程序名；返回按 PROGRAMS 固定顺序排列的程序列表"""
    names = [str(p).strip().lower() for p in (programs or []) if str(p).strip()]
    if not names:
        raise ValueError("未指定程序，可选: " + " / ".join(PROGRAMS) + " / all")

    if "all" in names:
        return list(PROGRAMS)

    unknown = [n for n in names if n not in PROGRAM_TABLES]
    if unknown:
        raise ValueError(f"未知程序: {unknown}，可选: " + " / ".join(PROGRAMS) + " / all")

    return [p for p in PROGRAMS if p in set(names)]


def resolve_table_specs(programs: list[str] | None) -> dict[str, TableSpec]:
    """根据程序列表解析出待导出的表规格

    同一张表被多个程序命中时(STOCK_DAILY)，board 范围合并为不过滤。
    """
    selected = resolve_programs(programs)

    scopes: dict[str, set[str | None]] = {}
    for prog in selected:
        for table, scope in PROGRAM_TABLES[prog].items():
            scopes.setdefault(table, set()).add(scope)

    specs: dict[str, TableSpec] = {}
    for table in TABLE_META:  # 固定顺序输出，便于日志比对
        if table not in scopes:
            continue
        found = scopes[table]
        board_scope = next(iter(found)) if len(found) == 1 else None
        columns, date_col = TABLE_META[table]
        specs[table] = TableSpec(table, columns, date_col, board_scope)

    return specs


def normalize_updated_since(value: str | None) -> str | None:
    """校验并规范化 --updated-since，避免非法字符串直接拼进 SQL"""
    if value is None:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError(
        f"--updated-since 格式错误: {value} (应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"
    )


def build_where(spec: TableSpec, begin: str, end: str,
                updated_since: str | None = None) -> str:
    """生成单表导出的 WHERE 条件 (begin/end 为 YYYY-MM-DD)"""
    date_cond = f"{spec.date_col} BETWEEN DATE '{begin}' AND DATE '{end}'"
    if updated_since and spec.table.startswith("ADJ_FACTOR"):
        date_cond = f"({date_cond} OR updated_at >= TIMESTAMP '{updated_since}')"

    if spec.board_scope:
        return f"{date_cond} AND {_BOARD_FILTER[spec.board_scope]}"
    return date_cond


def export_tables(conn: duckdb.DuckDBPyConnection,
                  specs: dict[str, TableSpec],
                  begin: str, end: str,
                  out_dir: Path,
                  updated_since: str | None = None) -> dict[str, int]:
    """按规格导出为 parquet，返回 {表名: 行数}"""
    out_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for table, spec in specs.items():
        where = build_where(spec, begin, end, updated_since)
        target = out_dir / parquet_name(table)
        conn.execute(
            f"COPY (SELECT {spec.columns} FROM {table} WHERE {where}) "
            f"TO '{target.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        counts[table] = n
        scope_desc = {"stock": "个股", "index": "指数"}.get(spec.board_scope or "", "全部")
        logger.info(f"[导出] {table:<16} {scope_desc:<4} {n:>9} 行 -> {target}")

    return counts


def warn_stale_files(out_dir: Path, specs: dict[str, TableSpec]) -> list[str]:
    """提示输出目录中本次未覆盖的历史 parquet(避免导入端误用旧数据)，仅告警不删除"""
    if not out_dir.exists():
        return []
    written = {parquet_name(t) for t in specs}
    stale = sorted(p.name for p in out_dir.glob("*.parquet") if p.name not in written)
    for name in stale:
        logger.warning(
            f"[提示] 输出目录存在本次未覆盖的历史文件: {name} (未删除，导入时请注意区分)"
        )
    return stale


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按程序导出其写入的表数据 (parquet)"
    )

    parser.add_argument(
        '-p', '--programs',
        nargs='+',
        default=['all'],
        type=str.lower,
        choices=[*PROGRAMS, 'all'],
        help='指定程序范围: adjust / import_daily / fetch_index / all (默认全部)'
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_today(),
        help='起始日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_today(),
        help='结束日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-o', '--out',
        type=str,
        default='tmp/db_sync/out',
        help='输出目录 (默认 tmp/db_sync/out)'
    )

    parser.add_argument(
        '--db',
        type=str,
        default=None,
        help='源库路径 (默认取 config.yaml 中当前生效的库)'
    )

    parser.add_argument(
        '--updated-since',
        type=str,
        default=None,
        help='额外导出 updated_at >= 该时间的复权因子行 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)'
    )

    return parser.parse_args()


def check_parameters(begin: str, end: str) -> bool:
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
    ]
    return pv.run(ctx, validators)


def main() -> int:
    """返回值: 0=导出成功, 2=参数错误或导出失败"""
    myutil.configure_etl_logging()
    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return 2

    try:
        selected = resolve_programs(args.programs)
        specs = resolve_table_specs(args.programs)
        updated_since = normalize_updated_since(args.updated_since)
    except ValueError as e:
        logger.error(f"参数错误: {e}")
        return 2

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date = myutil.trans_datestr_format(args.end)

    db_path = Path(args.db).expanduser() if args.db else myutil.get_default_dbfile()
    if not db_path.exists():
        logger.error(f"源库不存在: {db_path}")
        return 2

    out_dir = Path(args.out).expanduser()

    logger.info("=" * 60)
    logger.info("ETL 表导出任务启动")
    logger.info(f"     程序范围: {selected}")
    logger.info(f"     导出表:   {list(specs)}")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     源库:     {db_path}")
    logger.info(f"     输出目录: {out_dir}")
    logger.info(f"     复权补捞: {updated_since or '无'}")
    logger.info("=" * 60)

    warn_stale_files(out_dir, specs)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        counts = export_tables(conn, specs, begin_date, end_date, out_dir, updated_since)
        logger.info(f"导出完成，共 {sum(counts.values())} 行。")
        return 0
    except Exception as e:
        logger.error(f"导出失败: {e}")
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
