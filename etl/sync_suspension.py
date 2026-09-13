# 修改记录:
#   2026-09-12  Claude  新建停牌名单入库 ETL(第三方独立事实源, 供 check_daily 交叉核对)
#   2026-09-13  Claude  -c / -x 缩小范围运行前追加警告: 写库按日期整体删除后重插,
#                       未被本次范围覆盖的股票会从当日快照消失, 导致核对侧整片误报
#   2026-09-13  Claude  修复语义 bug: stock_tfp_em 返回的是围绕查询日的停复牌公告
#                       (含未来才停牌/当日已复牌的行), 不是"当日处于停牌状态"名单;
#                       入库前新增 filter_suspended_on 过滤, 并区分"接口空帧"与
#                       "过滤后为空"两种退出码语义
"""停牌名单入库工具 (支持指定日期区间)

退出码: 0=成功 / 1=失败 / 2=argparse 用法错 / 3=部分成功
  区间内全部日期成功 -> 0；全部失败 -> 1；
  部分日期失败, 或某日接口返回空帧 -> 3。

接口 (stock_tfp_em) 返回的是围绕查询日的停复牌公告，混有未来才停牌、
当日已复牌等与查询日无关的行，入库前会用 filter_suspended_on 过滤成
"D 日确实处于停牌状态"的行，使 trade_date 列名副其实。

接口空帧(取数可能有问题)与过滤后为空(当日确实无停牌股，属正常情况)是两回事:
只有前者才归为「部分成功」计入退出码 3；后者视为正常，退出码按其余日期计算。
"""
import argparse
import logging
import sys

import duckdb
import pandas as pd

from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.sync_suspension")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A股停牌名单入库工具 (支持指定日期区间)"
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
    prefix_sh = df["symbol"].astype(str).str[0].isin(["6", "5"])
    keep = pd.Series(False, index=df.index)
    if "SH" in wanted:
        keep = keep | prefix_sh
    if "SZ" in wanted:
        keep = keep | df["symbol"].astype(str).str[0].isin(["0", "3", "1"])
    return df[keep]


def filter_suspended_on(df: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    """过滤成"trade_date(D)当天确实处于停牌状态"的行

    stock_tfp_em(date=D) 返回的其实是围绕 D 的停复牌公告，混有未来才停牌
    (suspend_time > D)、当日已复牌(resume_deadline < D) 的行，这些行在 D
    当天股票是正常交易的，不该出现在 SUSPENSION_DAILY 里。

    保留条件:
        suspend_time 的日期部分 <= D
        且 (resume_deadline 为空 或 resume_deadline 的日期部分 >= D)

    suspend_time / resume_deadline 是 TIMESTAMP，可能带盘中时刻(如 10:30:00)，
    比较前必须只取日期部分(.dt.normalize())，否则当天盘中停牌的会被误判成
    "还没停"而被排除。resume_deadline 为 NaT 表示没有复牌截止日，视为仍在停牌。
    """
    if df is None or df.empty:
        return df
    target = pd.Timestamp(trade_date).normalize()
    suspend_date = pd.to_datetime(df["suspend_time"], errors="coerce").dt.normalize()
    resume_date = pd.to_datetime(df["resume_deadline"], errors="coerce").dt.normalize()
    keep = (suspend_date <= target) & (resume_date.isna() | (resume_date >= target))
    return df[keep]


def classify_write_result(api_empty: bool, written: int) -> str:
    """区分"写入 0 行"的两种含义，供退出码/日志分支判断使用

    写入 0 行可能来自两种完全不同的情况，语义不能混为一谈:
        - 接口本身就返回空帧: 取数可能有问题，值得作为"空"计入部分成功(退出码 3)
        - 接口有数据，但按 filter_suspended_on 过滤后为 0:
          当日确实没有处于停牌状态的股票，是正常情况，不该污染取数成功信号

    返回值:
        "ok"              written > 0
        "api_empty"       接口返回空帧(过滤前就是空)
        "filtered_empty"  接口有数据，过滤后为空
    """
    if written > 0:
        return "ok"
    return "api_empty" if api_empty else "filtered_empty"


def main() -> int:
    myutil.configure_etl_logging()
    args = parse_arguments()

    if not check_parameters(args.begin, args.end, args.forcerun):
        return 1

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date = myutil.trans_datestr_format(args.end)
    codes = parse_codes_arg(args.codes)
    wanted = requested_exchanges(args.exchanges)

    logger.info("=" * 60)
    logger.info("停牌名单入库任务启动")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
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

    if not hasattr(module, 'fetch_suspension'):
        logger.error(f"模块 '{args.source}' 中没有定义 'fetch_suspension' 方法。")
        return 1

    conn: duckdb.DuckDBPyConnection | None = None
    failed: list[str] = []
    empty: list[str] = []
    try:
        conn = dbutil.get_connection(is_read_only=False)
        for ymd in trade_dates:
            date_str = myutil.trans_datestr_format(ymd)
            try:
                df = module.fetch_suspension(ymd)
            except Exception as e:
                logger.error(f"[{date_str}] 停牌名单取数失败: {e}")
                failed.append(date_str)
                continue

            api_empty = df is None or df.empty
            df = _filter_by_exchange(filter_by_codes(df, codes), wanted)
            df = filter_suspended_on(df, date_str)
            written = dbutil.save_suspension_to_db(df, date_str, conn,
                                                   source=args.source)
            result = classify_write_result(api_empty, written)
            if result == "api_empty":
                logger.warning(f"[{date_str}] 停牌名单接口返回空帧，取数可疑")
                empty.append(date_str)
            elif result == "filtered_empty":
                logger.info(f"[{date_str}] 当日无处于停牌状态的股票(接口有数据，"
                           f"过滤后为空)")

        if failed and len(failed) == len(trade_dates):
            logger.error(f"全部 {len(trade_dates)} 个交易日取数失败，任务失败。")
            return 1
        if failed or empty:
            logger.warning(f"部分成功: 取数失败 {len(failed)} 天 {failed}；"
                           f"无数据 {len(empty)} 天 {empty}")
            return 3
        logger.info(f"任务完成: {len(trade_dates)} 个交易日全部入库成功。")
        return 0
    except Exception as e:
        logger.error(f"停牌名单入库过程中发生错误: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
