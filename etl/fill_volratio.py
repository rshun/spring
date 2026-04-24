import argparse
import logging
from util import dbutil,myutil
from util import validators as pv

logger = logging.getLogger("etl.fill_volratio")

'''
补齐量比指标
  前置条件:
  1、先执行import_daily.py, 以确保日线数据已导入
'''

# 解析命令行参数
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股量比数据补齐工具 (支持多代码、指定日期)"
    )

    # 参数: 起始日期 (可选，默认T-1)
    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_yesterday(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为T-1'
    )

    # 参数: 结束日期 (可选，默认T-1)
    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_yesterday(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为T-1'
    )

    # 参数: 股票代码 (可选，支持多个, 优先级最高，如果指定了代码，忽略交易所参数)
    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量,支持空格分隔或逗号分隔'
    )

    # 参数 交易所 (可选，支持多个,sz,sh,bj,all)
    # 默认值: ['all']
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
        default=False,
        help='强制运行, 即使当前日期不是交易日'
    )

    return parser.parse_args()

'''
检查参数有效性
'''
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

    if not check_parameters(args.begin,args.end,args.forcerun):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("="*60)
    logger.info("补全量比数据任务启动")
    logger.info(f"     开始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     股票代码: {args.codes}")
    logger.info("="*60)

    # 获取股票代码列表
    if args.codes is not None:
        candidate_codes = dbutil.get_candidate_codes(
            begindate    = begin_date,
            enddate      = end_date,
            exchanges_arg= args.exchanges,
            codes_arg    = args.codes
           )
        if not candidate_codes:
            logger.warning("没有找到符合条件的股票代码")
            return
        codes = [f"{t[0]}.{t[1]}" for t in candidate_codes]
    else:
        codes = None

    dbutil.fill_daily_basic_volume_ratio(begin_date,end_date,codes)

if __name__ == "__main__":
    main()
