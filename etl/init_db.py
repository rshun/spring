"""初始化duckdb, 并且创建sql/schema.sql中定义的表结构"""
import logging
import duckdb
from util import dbutil, myutil

logger = logging.getLogger("etl.init_db")


def create_database_schema() -> None:
    myutil.configure_etl_logging()

    try:
        sql_file = myutil.get_sql_file()
    except FileNotFoundError as e:
        logger.error(str(e))
        return

    sql = sql_file.read_text(encoding="utf-8")
    myutil.ensure_dbfile_dir()
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        logger.info("--- 正在创建表结构 ---")
        conn.execute(sql)
        logger.info("--- 表结构创建成功 ---")

    except Exception as e:
        logger.error(f"创建数据库时发生严重错误: {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    create_database_schema()
