# 修改记录:
#   2026-08-18  Claude  新增: 导入 export_etl_tables.py 导出的 parquet(按程序选择, 幂等 upsert)
#   2026-09-13  Claude  接入 sync_suspension(SUSPENSION_DAILY) / sync_limit_pool
#                       (LIMIT_POOL_DAILY)。这两张表是"每日全量快照，成员会变"，
#                       不能沿用 upsert(会在目标库留下幽灵行)，改为按 parquet 的
#                       trade_date 区间"先删后插"；新增 SNAPSHOT_TABLES 标记这类表，
#                       现有 6 张 upsert 表行为不变
#   2026-09-13  Claude  复核意见修复: 快照表 parquet 为空时 d_min/d_max 为 NULL，
#                       DELETE 恒不删任何行，此前只在注释里提"不会误删"、未提"也不会
#                       清理"，且日志无感知；本项目刚在同一模块(见 5273f4a)把静默
#                       排除/静默空帧改成显式 WARNING/INFO，这里不能再留新的静默点。
#                       现改为该场景下打一条 WARNING，并补全注释的另一面
"""
功能: 把 export_etl_tables.py 导出的 parquet 合并进本机 DuckDB。

写入策略与各 ETL 程序保持一致(幂等，只覆盖各自负责的列):
  STOCK_DAILY     整行覆盖(该表所有列都由 import_daily / fetch_index 负责)
  DAILY_BASIC     仅覆盖 turnover_rate/pe/pb/is_st，且来源为 NULL 时保留原值；
                  涨跌停价/量比/股本/市值等由其它程序生成的列不受影响
  ADJ_FACTOR      覆盖 fore/back/adjust_factor + updated_at，保留原 created_at
  ADJ_FACTOR_RAW / ADJ_FACTOR_LOCAL  同上

  SUSPENSION_DAILY / LIMIT_POOL_DAILY 不走 upsert，走"按 trade_date 区间先删后插"
  (区间取 parquet 内 MIN/MAX(trade_date))：这两张表是每日全量快照，成员会变，
  upsert 只覆盖同主键行、不删多余旧行，会在目标库留下幽灵行(同一 bug 在写库侧
  已被 util/dbutil.py 的 save_suspension_to_db / save_limit_pool_to_db 规避过，
  见其 docstring)。LIMIT_POOL_DAILY 的删除只按 trade_date，不按 limit_type，
  确保涨停/跌停两个方向在同一批导入里一起清干净。

输入参数:
  -p, --programs  程序范围: adjust / import_daily / fetch_index / sync_suspension /
                  sync_limit_pool / all (默认 all，可多选)
  -i, --input     parquet 目录 (默认 tmp/db_sync/out)
      --db        目标库路径 (默认取 config.yaml 中当前生效的库)
      --dry-run   只读打开目标库，统计待导入行数，不写库

用法:
  python -m tools.import_etl_tables -i tmp/db_sync/out --dry-run
  python -m tools.import_etl_tables -i tmp/db_sync/out
  python -m tools.import_etl_tables -p import_daily -i D:/sync/out
"""
import argparse
import hashlib
import json
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
    "ADJ_FACTOR_LOCAL": """
        INSERT INTO ADJ_FACTOR_LOCAL
            (code, trade_date, fore_factor, back_factor, adjust_factor, created_at, updated_at)
        SELECT code, trade_date, fore_factor, back_factor, adjust_factor, created_at, updated_at
        FROM read_parquet('{src}')
        ON CONFLICT (code, trade_date) DO UPDATE SET
            fore_factor   = EXCLUDED.fore_factor,
            back_factor   = EXCLUDED.back_factor,
            adjust_factor = EXCLUDED.adjust_factor,
            updated_at    = EXCLUDED.updated_at
    """,
    "ADJ_FACTOR_LOCAL_STATE": """
        INSERT INTO ADJ_FACTOR_LOCAL_STATE SELECT * FROM read_parquet('{src}')
        ON CONFLICT(code) DO UPDATE SET base_factor=EXCLUDED.base_factor,updated_at=EXCLUDED.updated_at
    """,

}

# 每日全量快照表：成员会变，不能 upsert，导入走"按 trade_date 区间先删后插"。
# 与 UPSERT_SQL 互斥，一张表只能出现在其中一个字典里。
SNAPSHOT_TABLES: frozenset[str] = frozenset({"SUSPENSION_DAILY", "LIMIT_POOL_DAILY"})

# 快照表的插入语句：整行覆盖(导出列即为 "*")，删除逻辑见 import_tables 里的
# "DELETE ... WHERE {date_col} BETWEEN ? AND ?"。
SNAPSHOT_INSERT_SQL: dict[str, str] = {
    "SUSPENSION_DAILY": "INSERT INTO SUSPENSION_DAILY SELECT * FROM read_parquet('{src}')",
    "LIMIT_POOL_DAILY": "INSERT INTO LIMIT_POOL_DAILY SELECT * FROM read_parquet('{src}')",
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
        if "ADJ_FACTOR_LOCAL_STATE" in tables:
            if not {"ADJ_FACTOR", "ADJ_FACTOR_LOCAL"} <= set(tables):
                raise ValueError("local 状态必须与事件和稠密表一起导入")
            state_file = in_dir / parquet_name("ADJ_FACTOR_LOCAL_STATE")
            local_file = in_dir / parquet_name("ADJ_FACTOR_LOCAL")
            if state_file.exists() and not local_file.exists():
                raise ValueError("local 状态快照缺少对应事件文件，拒绝导入")
            if state_file.exists() and local_file.exists():
                manifest_file = in_dir / "local_snapshot_manifest.json"
                if not manifest_file.exists():
                    raise ValueError("local 完整快照缺少校验清单，拒绝导入")
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                hashes = manifest.get("sha256", {})
                if manifest.get("version") != 1 or not {"ADJ_FACTOR_LOCAL", "ADJ_FACTOR_LOCAL_STATE", "ADJ_FACTOR"} <= set(hashes):
                    raise ValueError("local 快照清单不完整，拒绝导入")
                for name in ("ADJ_FACTOR_LOCAL", "ADJ_FACTOR_LOCAL_STATE", "ADJ_FACTOR"):
                    with (in_dir / parquet_name(name)).open("rb") as stream:
                        digest = hashlib.sha256()
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    if digest.hexdigest() != hashes[name]:
                        raise ValueError("local 快照文件不匹配，拒绝混合批次导入")
            if state_file.exists() and local_file.exists() and not dry_run:
                # 文件由同一事务全量导出；空事件文件也能传播全部撤销。
                conn.execute(f"""
                    DELETE FROM ADJ_FACTOR_LOCAL WHERE code IN (
                        SELECT code FROM read_parquet('{state_file.as_posix()}'))
                    AND NOT EXISTS (SELECT 1 FROM read_parquet('{local_file.as_posix()}') e
                        WHERE e.code=ADJ_FACTOR_LOCAL.code AND e.trade_date=ADJ_FACTOR_LOCAL.trade_date)
                """)
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

            if table in SNAPSHOT_TABLES and d_min is None:
                # parquet 0 行时 d_min/d_max 都是 NULL，BETWEEN 恒为 UNKNOWN，不会误删——
                # 但代价是也不会清理：若目标库该区间(未知，无法界定)恰好留有旧行/幽灵行，
                # 这次导入不会把它们删掉。不能静默过去，必须让用户知道这次没清理。
                logger.warning(
                    f"[{table}] parquet 为空，无法确定日期区间，"
                    f"目标库该区间(若有)的旧行不会被清理"
                )

            before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if table in SNAPSHOT_TABLES:
                # 快照表：按 parquet 的 trade_date 区间先删后插，避免 upsert 留幽灵行。
                conn.execute(
                    f"DELETE FROM {table} WHERE {date_col} BETWEEN ? AND ?",
                    [d_min, d_max]
                )
                conn.execute(SNAPSHOT_INSERT_SQL[table].format(src=src_posix))
            else:
                conn.execute(UPSERT_SQL[table].format(src=src_posix))
            after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = {"src": n_src, "before": before, "after": after}
            verb = "先删后插" if table in SNAPSHOT_TABLES else "更新"
            logger.info(
                f"[导入] {table:<16} parquet {n_src:>9} 行 ({d_min}~{d_max}) | "
                f"表 {before} -> {after} (新增 {after - before}，其余为{verb})"
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
        help='指定程序范围: adjust / import_daily / fetch_index / sync_suspension / sync_limit_pool / all (默认全部)'
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
