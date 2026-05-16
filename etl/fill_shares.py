"""
根据 CAPITAL_DETAIL 表回填 DAILY_BASIC 的 total_shares 和 float_shares
  前置条件:
  1、先执行 import_daily.py, 以确保日线数据已导入
  2、CAPITAL_DETAIL 表已有股本变化数据

  注意:
  - 新股如果 CAPITAL_DETAIL 无数据则跳过
"""
import argparse
import duckdb
import logging
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.fill_shares")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="根据CAPITAL_DETAIL回填DAILY_BASIC的总股本和流通股本"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=None,
        help='起始日期 (格式: YYYYMMDD)，默认为T-1'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=None,
        help='结束日期 (格式: YYYYMMDD)，默认: 仅传 -b 时为今天，否则为T-1'
    )

    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519 000001)，不传则默认处理全量'
    )

    parser.add_argument(
        '-x', '--exchanges', nargs='+',
        default=['all'],
        type=str.lower,
        choices=['sh', 'sz', 'bj', 'all'],
        help='指定交易所范围: sh (沪), sz (深), bj (北), all (默认全部)'
    )

    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        help='强制运行, 即使当前日期不是交易日'
    )

    args = parser.parse_args()

    # 默认日期: 仅指定 -b 时, -e 取今天; 否则两端都默认 T-1
    if args.begin is None:
        args.begin = myutil.get_yesterday()
        if args.end is None:
            args.end = myutil.get_yesterday()
    elif args.end is None:
        args.end = myutil.get_today()

    return args


def check_parameters(begin: str, end: str, forcerun: bool) -> bool:
    ctx = {"begin": begin, "end": end, "forcerun": forcerun}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
    ]
    if not forcerun:
        validators.append(pv.v_single_day_must_be_trading_day("begin", "end"))
    return pv.run(ctx, validators)


def main() -> None:
    myutil.configure_etl_logging()

    args = parse_arguments()

    if not check_parameters(args.begin, args.end, args.forcerun):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    if 'all' in args.exchanges:
        exchanges = None
    else:
        exchanges = [x.upper() for x in args.exchanges]

    codes = None
    if args.codes is not None:
        candidate_codes = dbutil.get_candidate_codes(
            begindate     = begin_date,
            enddate       = end_date,
            exchanges_arg = args.exchanges,
            codes_arg     = args.codes
        )
        if not candidate_codes:
            logger.warning("没有找到符合条件的股票代码")
            return
        codes = [f"{t[0]}.{t[1]}" for t in candidate_codes]

    logger.info("=" * 60)
    logger.info("回填股本数据任务启动")
    logger.info(f"     开始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {exchanges if exchanges else '全部'}")
    logger.info(f"     股票代码: {codes if codes else '全量'}")
    logger.info("=" * 60)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        dbutil.fill_daily_basic_shares(begin_date, end_date, codes, exchanges, conn=conn)
        dbutil.fill_daily_basic_mv(begin_date, end_date, codes, exchanges, conn=conn)
    except Exception as e:
        logger.error(f"回填股本数据时发生错误：{e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
