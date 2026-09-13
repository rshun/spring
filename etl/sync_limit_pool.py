# 修改记录:
#   2026-09-12  Claude  新建涨跌停池入库 ETL(第三方独立事实源, 供 check_daily 交叉核对)
#   2026-09-13  Claude  -c / -x 缩小范围运行前追加警告: 写库按日期整体删除后重插,
#                       未被本次范围覆盖的股票会从当日快照消失, 导致核对侧整片误报
"""涨跌停股池入库工具 (支持指定日期区间)

退出码: 0=成功 / 1=失败 / 2=argparse 用法错 / 3=部分成功

两个池分别入库到 LIMIT_POOL_DAILY 的 limit_type='U'/'D'。
某池当日为空同样计入「部分成功」: 跌停池为空在牛市可能属实,
但表内 0 行与「ETL 未跑」无法区分, 故必须把信号传出去。
"""
import argparse
import logging
import sys

import duckdb
import pandas as pd

from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.sync_limit_pool")

# --only 取值 -> (limit_type, 取数函数名)
_POOLS = {
    "up":   ("U", "fetch_limit_pool"),
    "down": ("D", "fetch_limit_down_pool"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A股涨跌停股池入库工具 (支持指定日期区间)"
    )
    parser.add_argument(
        '-b', '--begin', type=str, default=myutil.get_today(),
        help='指定起始日期 (格式: YYYYMMDD)，默认为当天'
    )
    parser.add_argument(
        '-e', '--end', type=str, default=myutil.get_today(),
        help='指定结束日期 (格式: YYYYMMDD)，默认为当天'
    )
    parser.add_argument(
        '-c', '--codes', nargs='+',
        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量,支持空格分隔或逗号分隔'
    )
    parser.add_argument(
        '-x', '--exchanges', nargs='+', default=['all'], type=str.lower,
        choices=['sh', 'sz', 'bj', 'all'],
        help='指定交易所范围: sh (沪), sz (深), bj (北), all (默认全部)'
    )
    parser.add_argument(
        '--only', type=str.lower, choices=['up', 'down', 'all'], default='all',
        help='同步范围: up (仅涨停), down (仅跌停), all (默认 全部)'
    )
    parser.add_argument(
        '-s', '--source', type=str, choices=['akstock'], default='akstock',
        help='指定数据源类型: akstock数据源 (默认 akstock)'
    )
    parser.add_argument(
        '-f', '--forcerun', action='store_true',
        help='强制运行, 即使当前日期不是交易日'
    )
    return parser


def parse_arguments() -> argparse.Namespace:
    return build_parser().parse_args()


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


def requested_exchanges(exchanges: list[str]) -> set[str]:
    """本源只覆盖沪深；返回请求范围内的沪深集合，只要北交所时返回空集。"""
    target = {e.lower() for e in exchanges}
    out: set[str] = set()
    if 'all' in target or 'sh' in target:
        out.add('SH')
    if 'all' in target or 'sz' in target:
        out.add('SZ')
    return out


def parse_codes_arg(codes: list[str] | None) -> list[str]:
    """把 -c 解析成裸码列表，支持空格与逗号混合分隔。"""
    if not codes:
        return []
    out: list[str] = []
    for item in codes:
        out.extend(p.strip() for p in str(item).split(",") if p.strip())
    return out


def filter_by_codes(df: pd.DataFrame, codes: list[str]) -> pd.DataFrame:
    """接口不支持按代码查，故拉全量后在本地按裸码过滤。"""
    if df is None or df.empty or not codes:
        return df
    return df[df["symbol"].astype(str).isin(set(codes))]


def _filter_by_exchange(df: pd.DataFrame, wanted: set[str]) -> pd.DataFrame:
    """按前缀把帧过滤到请求的沪深范围。北交所行在写库时统一丢弃。"""
    if df is None or df.empty:
        return df
    keep = pd.Series(False, index=df.index)
    if "SH" in wanted:
        keep = keep | df["symbol"].astype(str).str[0].isin(["6", "5"])
    if "SZ" in wanted:
        keep = keep | df["symbol"].astype(str).str[0].isin(["0", "3", "1"])
    return df[keep]


def main() -> int:
    myutil.configure_etl_logging()
    args = parse_arguments()

    if not check_parameters(args.begin, args.end, args.forcerun):
        return 1

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date = myutil.trans_datestr_format(args.end)
    codes = parse_codes_arg(args.codes)
    wanted = requested_exchanges(args.exchanges)
    pools = list(_POOLS) if args.only == 'all' else [args.only]

    logger.info("=" * 60)
    logger.info("涨跌停股池入库任务启动")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     同步范围: {args.only}")
    logger.info(f"     指定代码: {codes if codes else '无 (全量)'}")
    logger.info(f"     数据源:   {args.source}")
    logger.info("=" * 60)

    if not wanted:
        logger.warning("akstock 源不覆盖北交所，本次无数据入库。"
                       "若需沪深数据请用 -x sh sz 或 -x all。")
        return 0

    if codes or wanted != {"SH", "SZ"}:
        logger.warning("部分范围运行(-c / -x)会覆盖当日全量快照: 写库按日期整体删除后重插，"
                       "未被本次范围覆盖的股票会从当日快照中消失，导致核对侧产生整片误报。"
                       "该用法仅供人工排查，日常入库请全量运行。")

    try:
        trade_dates = dbutil.get_trade_dates(begin_date, end_date)
    except Exception as e:
        logger.error(f"获取交易日列表失败: {e}")
        return 1
    if not trade_dates:
        logger.warning("区间内无交易日，任务结束。")
        return 1

    try:
        module = myutil.import_source_module(args.source)
    except ImportError as e:
        logger.error(f"无法导入模块 {args.source}，请检查文件名是否存在。{e}")
        return 1

    for pool in pools:
        _, fn_name = _POOLS[pool]
        if not hasattr(module, fn_name):
            logger.error(f"模块 '{args.source}' 中没有定义 '{fn_name}' 方法。")
            return 1

    conn: duckdb.DuckDBPyConnection | None = None
    failed: list[str] = []
    empty: list[str] = []
    total_jobs = len(trade_dates) * len(pools)
    try:
        conn = dbutil.get_connection(is_read_only=False)
        for ymd in trade_dates:
            date_str = myutil.trans_datestr_format(ymd)
            for pool in pools:
                limit_type, fn_name = _POOLS[pool]
                try:
                    df = getattr(module, fn_name)(ymd)
                except Exception as e:
                    logger.error(f"[{date_str} {limit_type}] 取数失败: {e}")
                    failed.append(f"{date_str}/{limit_type}")
                    continue

                df = _filter_by_exchange(filter_by_codes(df, codes), wanted)
                written = dbutil.save_limit_pool_to_db(df, date_str, limit_type,
                                                       conn, source=args.source)
                if written == 0:
                    empty.append(f"{date_str}/{limit_type}")

        if failed and len(failed) == total_jobs:
            logger.error(f"全部 {total_jobs} 个取数任务失败，任务失败。")
            return 1
        if failed or empty:
            logger.warning(f"部分成功: 取数失败 {len(failed)} 项 {failed}；"
                           f"无数据 {len(empty)} 项 {empty}")
            return 3
        logger.info(f"任务完成: {total_jobs} 个取数任务全部入库成功。")
        return 0
    except Exception as e:
        logger.error(f"涨跌停池入库过程中发生错误: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
