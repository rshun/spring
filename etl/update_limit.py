"""A股涨跌停数据补齐工具 (支持指定日期)"""
import argparse
import logging
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.update_limit")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股涨跌停数据补齐工具 (支持指定日期)"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_yesterday(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为T-1'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_yesterday(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为T-1'
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

    return parser.parse_args()


def check_parameters(begin: str, end: str, forcerun: bool) -> bool:
    ctx = {"begin": begin, "end": end}
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

    logger.info("=" * 60)
    logger.info("补全涨跌停数据任务启动")
    logger.info(f"     开始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     目标市场: {args.exchanges}")
    logger.info("=" * 60)

    try:
        exchanges_upper = [x.upper() for x in args.exchanges]
        dbutil.update_price_limits_by_range(begin_date, end_date, exchanges_upper)
    except Exception as e:
        logger.error(f"补全涨跌停数据时发生错误：{e}")


if __name__ == "__main__":
    main()
