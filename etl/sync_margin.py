import argparse
import duckdb
import logging
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.sync_margin")

'''
功能: 获取沪深两市融资融券汇总和明细数据
输入参数:
  起始日期 (可选，默认今天)
  结束日期 (可选，默认今天)
  交易所   (可选，支持多个 sh/sz/all，默认 all；akshare 暂无北交所接口)
  同步范围 (可选，summary/detail/all，默认 all)
  数据源   (可选，默认 akstock)
  强制运行 (可选，非交易日跳过的开关)
'''


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股融资融券汇总和明细数据入库工具 (akshare 数据源)"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_today(),
        help='指定起始日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_today(),
        help='指定结束日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-x', '--exchanges', nargs='+',
        default=['all'],
        type=str.lower,
        choices=['sh', 'sz', 'all'],
        help='指定交易所范围: sh (沪), sz (深), all (默认全部)；akshare 暂无北交所接口'
    )

    parser.add_argument(
        '--only',
        type=str.lower,
        choices=['summary', 'detail', 'all'],
        default='all',
        help='同步范围: summary (仅汇总), detail (仅明细), all (默认 全部)'
    )

    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['akstock'],
        default='akstock',
        help='指定数据源类型: akstock数据源 (默认 akstock)'
    )

    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        default=False,
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


def main():

    myutil.configure_etl_logging()
    args = parse_arguments()
    if not check_parameters(args.begin, args.end, args.forcerun):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("=" * 60)
    logger.info("获取融资融券数据任务启动")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     同步范围: {args.only}")
    logger.info(f"     数据源:   {args.source}")
    logger.info("=" * 60)

    # 获取区间内交易日 (YYYYMMDD)，供 SZ summary 与 detail 逐日抓取使用
    trade_dates = dbutil.get_trade_dates(begin_date, end_date)
    if not trade_dates:
        logger.warning("区间内无交易日，任务结束。")
        return
    logger.info(f"区间内交易日 {len(trade_dates)} 个: {trade_dates[0]} ~ {trade_dates[-1]}")

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        module = myutil.import_source_module(args.source)

        # ── 汇总 ──────────────────────────────────────────
        if args.only in ('summary', 'all'):
            if not hasattr(module, 'fetch_margin_summary'):
                logger.error(f"错误: 模块 '{args.source}' 中没有定义 'fetch_margin_summary' 方法。")
            else:
                logger.info("\n[Step] 获取融资融券汇总数据...")
                df_summary = module.fetch_margin_summary(
                    args.begin, args.end, args.exchanges, trade_dates
                )
                if df_summary is not None and not df_summary.empty:
                    dbutil.save_margin_summary_to_db(df_summary, conn)
                else:
                    logger.warning("未获取到融资融券汇总数据，跳过数据库写入。")

        # ── 明细 ──────────────────────────────────────────
        if args.only in ('detail', 'all'):
            if not hasattr(module, 'fetch_margin_detail'):
                logger.error(f"错误: 模块 '{args.source}' 中没有定义 'fetch_margin_detail' 方法。")
            else:
                logger.info("\n[Step] 逐日获取融资融券明细数据...")
                for i, d in enumerate(trade_dates, 1):
                    logger.info(f"  ({i}/{len(trade_dates)}) {d}")
                    df_detail = module.fetch_margin_detail(d, args.exchanges)
                    if df_detail is not None and not df_detail.empty:
                        dbutil.save_margin_detail_to_db(df_detail, conn)
                    else:
                        logger.warning(f"  {d} 未获取到融资融券明细数据，跳过。")

    except ImportError as e:
        logger.error(f"错误: 无法导入模块 {args.source}，请检查文件名是否存在。{e}")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
