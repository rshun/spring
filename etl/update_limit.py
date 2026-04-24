import argparse
import logging
from util import dbutil,myutil
from util import validators as pv

logger = logging.getLogger("etl.update_limit")

# 解析命令行参数
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股涨跌停数据补齐工具 (支持指定日期)"
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

    # 参数 交易所 (可选，支持多个,sz,sh,bj,all)
    # 默认值: ['all']
    parser.add_argument(
        '-x', '--exchanges', nargs='+', 
        default=['all'],
        type=str.lower,
        choices=['sh', 'sz', 'bj', 'all'], 
        help='指定交易所范围: sh (沪), sz (深), bj (北), all (默认全部)'
    )

    return parser.parse_args()

'''
检查参数有效性
'''
def check_parameters(begin: str, end: str) -> bool:

    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
    ]
    validators.append(pv.v_single_day_must_be_trading_day("begin", "end", allow_non_trading=False))
    return pv.run(ctx, validators)


def main():
    myutil.configure_etl_logging()

    args = parse_arguments()

    if not check_parameters(args.begin,args.end):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("="*60)
    logger.info("补全涨跌停数据任务启动")
    logger.info(f"     开始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     目标市场: {args.exchanges}")
    logger.info("="*60)

    exchanges_upper = [x.upper() for x in args.exchanges]
    dbutil.update_price_limits_by_range(begin_date, end_date, exchanges_upper)

if __name__ == "__main__":
    main()
