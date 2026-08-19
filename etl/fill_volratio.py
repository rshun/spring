# 修改记录:
#   2026-08-19  Claude  main() 返回退出码(0成功/1失败)并由 sys.exit 传出，供外部判定成败
#   2026-08-19  Claude  拆出 build_parser()，供 tools/describe_cli.py 自省参数
"""
补齐量比指标
  前置条件:
  1、先执行import_daily.py, 以确保日线数据已导入
"""
import argparse
import duckdb
import logging
import sys
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.fill_volratio")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A股量比数据补齐工具 (支持多代码、指定日期)"
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

    return parser


def parse_arguments() -> argparse.Namespace:
    args = build_parser().parse_args()

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


def main() -> int:
    myutil.configure_etl_logging()

    args = parse_arguments()

    if not check_parameters(args.begin, args.end, args.forcerun):
        return 1

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("=" * 60)
    logger.info("补全量比数据任务启动")
    logger.info(f"     开始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     股票代码: {args.codes}")
    logger.info("=" * 60)

    if args.codes is not None:
        candidate_codes = dbutil.get_candidate_codes(
            begindate     = begin_date,
            enddate       = end_date,
            exchanges_arg = args.exchanges,
            codes_arg     = args.codes
        )
        if not candidate_codes:
            logger.warning("没有找到符合条件的股票代码")
            return 1
        codes = [f"{t[0]}.{t[1]}" for t in candidate_codes]
    else:
        codes = None

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        dbutil.fill_daily_basic_volume_ratio(begin_date, end_date, codes, conn=conn)
        return 0
    except Exception as e:
        logger.error(f"补全量比数据时发生错误：{e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
