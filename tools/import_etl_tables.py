# 修改记录:
#   2026-08-18  Claude  新增: 导入 export_etl_tables.py 导出的 parquet(按程序选择, 幂等 upsert)
"""
功能: 把 export_etl_tables.py 导出的 parquet 合并进本机 DuckDB。

写入策略与各 ETL 程序保持一致(幂等，只覆盖各自负责的列):
  STOCK_DAILY     整行覆盖(该表所有列都由 import_daily / fetch_index 负责)
  DAILY_BASIC     仅覆盖 turnover_rate/pe/pb/is_st，且来源为 NULL 时保留原值；
                  涨跌停价/量比/股本/市值等由其它程序生成的列不受影响
  ADJ_FACTOR      覆盖 fore/back/adjust_factor + updated_at，保留原 created_at
  ADJ_FACTOR_RAW  同上

输入参数:
  -p, --programs  程序范围: adjust / import_daily / fetch_index / all (默认 all，可多选)
  -i, --input     parquet 目录 (默认 tmp/db_sync/out)
      --db        目标库路径 (默认取 config.yaml 中当前生效的库)
      --dry-run   只读打开目标库，统计待导入行数，不写库

用法:
  python -m tools.import_etl_tables -i tmp/db_sync/out --dry-run
  python -m tools.import_etl_tables -i tmp/db_sync/out
  python -m tools.import_etl_tables -p import_daily -i D:/sync/out
"""
import argparse
import logging
from pathlib import Path

import duckdb

from util import myutil
from tools.export_etl_tables import (
    PROGRAMS,
    TABLE_META,
    parquet_name,
    resolve_programs,
    resolve_table_specs,
)

logger = logging.getLogger("etl.tools.import_etl_tables")

UPSERT_SQL: dict[str, str] = {
    "STOCK_DAILY": """
        INSERT INTO STOCK_DAILY
            (code, date, open, high, low, close, pre_close, tradestatus, volume, amount)
        SELECT code, date, open, high, low, close, pre_close, tradestatus, volume, amount
        FROM read_parquet('{src}')
        ON CONFLICT (code, date) DO UPDATE SET
            open        = EXCLUDED.open,
            high        = EXCLUDED.high,
            low         = EXCLUDED.low,
            close       = EXCLUDED.close,
            pre_close   = EXCLUDED.pre_close,
            tradestatus = EXCLUDED.tradestatus,
            volume      = EXCLUDED.volume,
            amount      = EXCLUDED.amount
    """,
    "DAILY_BASIC": """
        INSERT INTO DAILY_BASIC
            (code, trade_date, turnover_rate, pe, pb, is_st)
        SELECT code, trade_date, turnover_rate, pe, pb, is_st
        FROM read_parquet('{src}')
        ON CONFLICT (code, trade_date) DO UPDATE SET
            turnover_rate = COALESCE(EXCLUDED.turnover_rate, DAILY_BASIC.turnover_rate),
            pe            = COALESCE(EXCLUDED.pe,            DAILY_BASIC.pe),
            pb            = COALESCE(EXCLUDED.pb,            DAILY_BASIC.pb),
            is_st         = COALESCE(EXCLUDED.is_st,         DAILY_BASIC.is_st)
    """,
    "ADJ_FACTOR": """
        INSERT INTO ADJ_FACTOR
            (code, trade_date, fore_factor, back_factor, adjust_factor, created_at, updated_at)
        SELECT code, trade_date, fore_factor, back_factor, adjust_factor, created_at, updated_at
        FROM read_parquet('{src}')
        ON CONFLICT (code, trade_date) DO UPDATE SET
            fore_factor   = EXCLUDED.fore_factor,
            back_factor   = EXCLUDED.back_factor,
            adjust_factor = EXCLUDED.adjust_factor,
            updated_at    = EXCLUDED.updated_at
    """,
    "ADJ_FACTOR_RAW": """
        INSERT INTO ADJ_FACTOR_RAW
            (code, trade_date, fore_factor, back_factor, adjust_factor, created_at, updated_at)
        SELECT code, trade_date, fore_factor, back_factor, adjust_factor, created_at, updated_at
        FROM read_parquet('{src}')
        ON CONFLICT (code, trade_date) DO UPDATE SET
            fore_factor   = EXCLUDED.fore_factor,
            back_factor   = EXCLUDED.back_factor,
            adjust_factor = EXCLUDED.adjust_factor,
            updated_at    = EXCLUDED.updated_at
    """,
}


def import_tables(conn: duckdb.DuckDBPyConnection,
                  in_dir: Path,
                  tables: list[str],
                  dry_run: bool = False) -> dict[str, dict[str, int]]:
    """把目录下的 parquet 合并进库，返回 {表名: {src, before, after}}

    单事务提交，任一表出错整体回滚；缺失的 parquet 只告警跳过。
    """
    stats: dict[str, dict[str, int]] = {}

    if not dry_run:
        conn.execute("BEGIN")
    try:
        for table in tables:
            src = in_dir / parquet_name(table)
            if not src.exists():
                logger.warning(f"[跳过] {table:<16} 缺少文件 {src}")
                continue

            date_col = TABLE_META[table][1]
            src_posix = src.as_posix()
            n_src, d_min, d_max = conn.execute(
                f"SELECT COUNT(*), MIN({date_col}), MAX({date_col}) "
                f"FROM read_parquet('{src_posix}')"
            ).fetchone()

            if dry_run:
                logger.info(f"[待导入] {table:<16} {n_src:>9} 行  区间 {d_min} ~ {d_max}")
                stats[table] = {"src": n_src, "before": 0, "after": 0}
                continue

            before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(UPSERT_SQL[table].format(src=src_posix))
            after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = {"src": n_src, "before": before, "after": after}
            logger.info(
                f"[导入] {table:<16} parquet {n_src:>9} 行 ({d_min}~{d_max}) | "
                f"表 {before} -> {after} (新增 {after - before}，其余为更新)"
            )

        if not dry_run:
            conn.execute("COMMIT")
    except Exception:
        if not dry_run:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise

    return stats


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导入 export_etl_tables.py 导出的 parquet"
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
        '-i', '--input',
        type=str,
        default='tmp/db_sync/out',
        help='parquet 目录 (默认 tmp/db_sync/out)'
    )

    parser.add_argument(
        '--db',
        type=str,
        default=None,
        help='目标库路径 (默认取 config.yaml 中当前生效的库)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只统计待导入行数，不写库'
    )

    return parser.parse_args()


def main() -> int:
    """返回值: 0=导入成功, 2=参数错误或导入失败"""
    myutil.configure_etl_logging()
    args = parse_arguments()

    try:
        selected = resolve_programs(args.programs)
        tables = list(resolve_table_specs(args.programs))
    except ValueError as e:
        logger.error(f"参数错误: {e}")
        return 2

    in_dir = Path(args.input).expanduser()
    if not in_dir.exists():
        logger.error(f"输入目录不存在: {in_dir}")
        return 2

    db_path = Path(args.db).expanduser() if args.db else myutil.get_default_dbfile()
    if not db_path.exists():
        logger.error(f"目标库不存在: {db_path}")
        return 2

    logger.info("=" * 60)
    logger.info("ETL 表导入任务启动")
    logger.info(f"     程序范围: {selected}")
    logger.info(f"     导入表:   {tables}")
    logger.info(f"     输入目录: {in_dir}")
    logger.info(f"     目标库:   {db_path}")
    logger.info(f"     运行模式: {'干跑(不写库)' if args.dry_run else '写库'}")
    logger.info("=" * 60)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = duckdb.connect(str(db_path), read_only=args.dry_run)
        stats = import_tables(conn, in_dir, tables, dry_run=args.dry_run)
        if not stats:
            logger.warning("没有可导入的文件，未做任何变更。")
        elif args.dry_run:
            logger.info(f"干跑完成，待导入合计 {sum(s['src'] for s in stats.values())} 行。")
        else:
            logger.info(f"导入完成，处理 {sum(s['src'] for s in stats.values())} 行。")
        return 0
    except Exception as e:
        logger.error(f"导入失败: {e}")
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
