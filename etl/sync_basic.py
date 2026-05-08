"""A股基本信息入库工具 (支持多源, 指定交易所)"""
import argparse
import duckdb
import logging
from util import myutil, dbutil
from util import validators as pv

logger = logging.getLogger("etl.sync_basic")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股基本信息入库工具 (支持多源,指定交易所)"
    )

    parser.add_argument(
        '-x', '--exchanges', nargs='+',
        default=['all'],
        type=str.lower,
        choices=['sh', 'sz', 'bj', 'all'],
        help='指定交易所范围: sh (沪), sz (深), bj (北), all (默认全部)'
    )

    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['bstock', 'akstock'],
        default='bstock',
        help='指定数据源类型: bstock数据源, akstock数据源 (默认 bstock数据源)'
    )

    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        help='强制运行, 即使当前日期不是交易日'
    )
    return parser.parse_args()


def check_parameters(forcerun: bool) -> bool:
    ctx = {"forcerun": forcerun}
    validators = [
        pv.v_dbfile_exists(),
    ]
    if not forcerun:
        validators.append(pv.v_single_day_must_be_trading_day())
    return pv.run(ctx, validators)


def main() -> None:
    myutil.configure_etl_logging()

    args = parse_arguments()

    if not check_parameters(args.forcerun):
        return

    logger.info("=" * 60)
    logger.info("获取股票基本信息任务启动")
    logger.info(f"     交易所: {args.exchanges}")
    logger.info(f"     数据源: {args.source}")
    logger.info("=" * 60)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        module = myutil.import_source_module(args.source)
        if not hasattr(module, 'fetch_stock_info'):
            logger.error(f"模块 '{args.source}' 中没有定义 'fetch_stock_info' 方法。")
            return

        stock_info, basic_df = module.fetch_stock_info(args.exchanges)

        if stock_info is not None and not stock_info.empty:
            dbutil.load_stock_info_to_db(stock_info, conn)
        else:
            logger.warning("未获取到任何股票基本信息，跳过数据库写入。")

        if basic_df is not None and not basic_df.empty:
            dbutil.save_shares_to_db(basic_df, conn)
        else:
            logger.warning("未获取到股票股本数据，跳过数据库写入。")

    except ImportError as e:
        logger.error(f"无法导入模块 {args.source}，请检查文件名是否存在。{e}")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
