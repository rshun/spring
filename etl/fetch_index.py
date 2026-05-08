"""A股指数历史行情数据入库工具 (支持多源、多代码、指定日期)"""
import argparse
import duckdb
import logging
from util import myutil, dbutil
from util import validators as pv

logger = logging.getLogger("etl.fetch_index")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股指数历史行情数据入库工具 (支持多源、多代码、指定日期)"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定指数代码列表 (例如: 000001,399001)，不传则默认处理全量'
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
        choices=['lday', 'bstock', 'akstock'],
        default='bstock',
        help='指定数据源类型: lday (本地day文件)  bstock数据源, akstock数据源 (默认 bstock数据源)'
    )

    return parser.parse_args()


def check_parameters(begin: str, end: str) -> bool:
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
        pv.v_single_day_must_be_trading_day("begin", "end"),
    ]
    return pv.run(ctx, validators)


def main() -> None:
    myutil.configure_etl_logging()

    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("="*60)
    logger.info("获取指数交易明细数据任务启动")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     指定代码: {args.codes if args.codes else '无 (处理全市场)'}")
    logger.info(f"     数据源:   {args.source}")
    logger.info("="*60)

    candidate_index_codes = dbutil.get_candidate_index(
        begindate    = begin_date,
        enddate      = end_date,
        exchanges_arg= args.exchanges,
        index_arg    = args.codes
    )

    if not candidate_index_codes:
        logger.warning("警告: 数据库中没有找到符合条件的指数。")
        return

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        module = myutil.import_source_module(args.source)
        if not hasattr(module, 'fetch_batch_index'):
            logger.error(f"模块 '{args.source}' 中没有定义 'fetch_batch_index' 方法。")
            return

        index_data = module.fetch_batch_index(candidate_index_codes)

        if index_data is not None and not index_data.empty:
            dbutil.save_index_to_db(index_data, conn)
        else:
            logger.warning("未获取到任何指数数据，跳过数据库写入。")

    except ImportError:
        logger.error(f"无法导入模块 {args.source}，请检查文件名是否存在。")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
