import argparse
import logging
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.fill_shares")

'''
根据 CAPITAL_DETAIL 表回填 DAILY_BASIC 的 total_shares 和 float_shares
  前置条件:
  1、先执行 import_daily.py, 以确保日线数据已导入
  2、CAPITAL_DETAIL 表已有股本变化数据

  注意:
  - 京市(BJ)暂时不更新
  - 新股如果 CAPITAL_DETAIL 无数据则跳过
'''

# 解析命令行参数
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="根据CAPITAL_DETAIL回填DAILY_BASIC的总股本和流通股本"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_yesterday(),
        help='起始日期 (格式: YYYYMMDD)，默认为T-1'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_yesterday(),
        help='结束日期 (格式: YYYYMMDD)，默认为T-1'
    )

    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519 000001)，不传则默认处理全量'
    )

    parser.add_argument(
        '-x', '--exchanges', nargs='+',
        default=['sh', 'sz'],
        type=str.lower,
        choices=['sh', 'sz', 'bj', 'all'],
        help='指定交易所范围: sh (沪), sz (深), bj (北), all (全部)，默认 sh sz (排除北交所)'
    )

    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        default=False,
        help='强制运行, 即使当前日期不是交易日'
    )

    return parser.parse_args()


def check_parameters(begin: str, end: str, forcerun: bool) -> bool:
    ctx = {"begin": begin, "end": end, "forcerun": forcerun}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
        pv.v_single_day_must_be_trading_day("begin", "end", allow_non_trading=bool(forcerun)),
    ]
    return pv.run(ctx, validators)


def main():
    myutil.configure_etl_logging()

    args = parse_arguments()

    if not check_parameters(args.begin, args.end, args.forcerun):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    # 处理交易所参数
    if 'all' in args.exchanges:
        exchanges = None
    else:
        exchanges = [x.upper() for x in args.exchanges]

    # 处理股票代码参数
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
    logger.info(f"     交易所:   {exchanges if exchanges else '全部(排除北交所由SQL控制)'}")
    logger.info(f"     股票代码: {codes if codes else '全量'}")
    logger.info("=" * 60)

    from util.dbutil import get_connection
    con = None
    try:
        con = get_connection(is_read_only=False)
        dbutil.fill_daily_basic_shares(begin_date, end_date, codes, exchanges, conn=con)
        dbutil.fill_daily_basic_mv(begin_date, end_date, codes, exchanges, conn=con)
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    main()
