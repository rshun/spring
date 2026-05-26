# 修改记录:
#   2026-05-26  Claude  下载/解析/入库三层分离：迁出到 datasource/tdx_offline 与 util/dbutil
"""
同步股本变迁数据 (CAPITAL_DETAIL)

编排层：调用 datasource/tdx_offline 获取 cw 财务文件 + gbbq 股本变迁数据，
再通过 util/dbutil.save_capital_detail_to_db 写入数据库。

用法:
    python -m etl.sync_capital
    python -m etl.sync_capital --download
"""
import argparse
import duckdb
import logging
import time

from datasource import tdx_offline
from util import dbutil
from util.myutil import configure_etl_logging

logger = logging.getLogger("etl.sync_capital")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="股本变迁数据同步工具")
    parser.add_argument(
        '--download',
        action='store_true',
        help='优先从 gbbq.zip 下载 gbbq 文件，默认不下载',
    )
    return parser.parse_args()


def main() -> None:
    configure_etl_logging()
    args = parse_arguments()

    start = time.time()
    logger.info('=' * 60)
    logger.info('股本变迁数据同步任务启动')
    logger.info('=' * 60)

    # 1) 同步通达信专业财务文件
    tdx_offline.sync_cw_files()

    # 2) 获取 gbbq 股本变迁数据
    tick = time.time()
    df_gbbq = tdx_offline.fetch_gbbq(download=args.download)
    if df_gbbq is None:
        logger.info("所有数据源均不可用，跳过 gbbq 同步")
    else:
        logger.info(f"获取 gbbq 数据完成 用时 {time.time() - tick:.2f}s")

        # 3) 写入数据库
        conn: duckdb.DuckDBPyConnection | None = None
        try:
            conn = dbutil.get_connection(is_read_only=False)
            dbutil.save_capital_detail_to_db(df_gbbq, conn)
        finally:
            if conn is not None:
                conn.close()

    # 4) 清理项目内 gbbq 二进制缓存
    tdx_offline.cleanup_gbbq_file()

    logger.info('=' * 60)
    logger.info(f'全部完成 用时 {time.time() - start:.2f}s')
    logger.info('=' * 60)


if __name__ == '__main__':
    main()
