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
        default=None,
        help='指定起始日期 (格式: YYYYMMDD)，默认为T-1'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=None,
        help='指定结束日期 (格式: YYYYMMDD)，默认: 仅传 -b 时为今天，否则为T-1'
    )

    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量,支持空格分隔或逗号分隔'
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


def resolve_codes(begin_date: str, end_date: str,
                  exchanges: list[str], codes_arg: list[str] | None) -> list[str] | None:
    """根据 -c 参数解析为完整代码列表 (如 600519.SH); 未传则返回 None 表示全量"""
    if codes_arg is None:
        return None
    candidate_codes = dbutil.get_candidate_codes(
        begindate     = begin_date,
        enddate       = end_date,
        exchanges_arg = exchanges,
        codes_arg     = codes_arg
    )
    if not candidate_codes:
        return []
    return [f"{t[0]}.{t[1]}" for t in candidate_codes]


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
    logger.info(f"     股票代码: {args.codes if args.codes else '无 (处理全市场)'}")
    logger.info("=" * 60)

    codes = resolve_codes(begin_date, end_date, args.exchanges, args.codes)
    if codes == []:
        logger.warning("没有找到符合条件的股票代码")
        return

    try:
        exchanges_upper = [x.upper() for x in args.exchanges]
        dbutil.update_price_limits_by_range(begin_date, end_date, exchanges_upper, codes)
    except Exception as e:
        logger.error(f"补全涨跌停数据时发生错误：{e}")


if __name__ == "__main__":
    main()
