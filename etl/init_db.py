# 修改记录:
#   2026-09-11  Claude  create_database_schema() 返回退出码(0成功/1失败)并由 sys.exit 传出：
#                       此前建表失败只打日志后 return，进程仍退出 0(契约 C1)
"""初始化duckdb, 并且创建sql/schema.sql中定义的表结构"""
import logging
import sys
import duckdb
from util import dbutil, myutil

logger = logging.getLogger("etl.init_db")


def create_database_schema() -> int:
    myutil.configure_etl_logging()

    try:
        sql_file = myutil.get_sql_file()
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    sql = sql_file.read_text(encoding="utf-8")
    db_path = myutil.ensure_dbfile_dir()
    logger.info(f"数据库路径: {db_path.parent}, 文件名: {db_path.name}")
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        logger.info("--- 正在创建表结构 ---")
        conn.execute(sql)
        logger.info("--- 表结构创建成功 ---")
        return 0

    except Exception as e:
        logger.error(f"创建数据库时发生严重错误: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(create_database_schema())
