# 修改记录:
#   2026-09-13  Claude  新建同花顺除权事件入库 ETL(CAPITAL_DETAIL 的独立外部对照)
#   2026-09-13  Claude  修复: -s parquet(全表替换)与 -c/-x 子集过滤同用会先清空整表
#                       再只插入过滤后的行, 导致其余股票历史事件被永久删除且退出码仍是 0；
#                       parse_arguments() 里检测到这种组合直接 parser.error() 拦截(退出码 2)
"""同花顺除权事件入库工具

两种模式:
  -s parquet (默认)  整份本地 dump 灌库(全表替换), 忽略 -b/-e, 跳过交易日校验。
                     **不能与 -c / -x 子集同用**: parquet 模式下写库前会先清空整表
                     再插入过滤后的数据, 加了 -c/-x 就等于「清空全表 -> 只插入过滤后
                     的这一小部分」, 其余股票/交易所的历史除权事件会被永久删除,
                     而且退出码仍是 0(看起来是"任务完成")。这种组合没有合理用例——
                     想查 dump 里某只股票的数据应直接调 ths.load_xdr_events()，
                     想按代码增量应该用 -s api -c。因此 parse_arguments() 检测到
                     该组合会直接 parser.error() 拒绝(退出码 2), 不会跑到写库那一步。
  -s api             只查 [begin,end] 内 gbbq 有事件的股票, 逐只请求, -c/-x 可以正常
                     配合使用(增量按区间删/插, 不会波及区间外的数据)。

退出码: 0=成功 / 1=失败 / 2=argparse 用法错(含 parquet+子集过滤的拦截) / 3=部分成功

本 ETL **不注册进 MCP**: 其对账本期不进 check_daily, 没有核对项就没有补数闭环,
与 sync_margin 同一位置(详见 spec §8)。只需加入
tests/unit/test_etl_exit_codes.py 的 ETL_MODULES(通用退出码契约)。
"""
import argparse
import logging
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd

from datasource import ths
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.sync_xdr_ths")

DOWNLOAD_DIR = Path(__file__).resolve().parents[1] / "download"
PARQUET_GLOB = "a_share_adjustment_factors_event_*.parquet"
_DATE_SUFFIX_RE = re.compile(r"(\d{8})\.parquet$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="同花顺除权事件入库工具(CAPITAL_DETAIL 的独立外部对照)"
    )
    parser.add_argument('-b', '--begin', type=str, default=myutil.get_today(),
                        help='指定起始日期 (格式: YYYYMMDD)，默认为当天')
    parser.add_argument('-e', '--end', type=str, default=myutil.get_today(),
                        help='指定结束日期 (格式: YYYYMMDD)，默认为当天')
    parser.add_argument('-c', '--codes', nargs='+',
                        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量,支持空格分隔或逗号分隔。'
                             '注意: 不能与 -s parquet 同用(会先清空整表再只插入过滤后的行，'
                             '导致其余股票历史事件被永久删除)，按代码过滤请配合 -s api')
    parser.add_argument('-x', '--exchanges', nargs='+', default=['all'], type=str.lower,
                        choices=['sh', 'sz', 'bj', 'all'],
                        help='指定交易所范围: sh (沪), sz (深), bj (北), all (默认全部)。'
                             '注意: 子集不能与 -s parquet 同用(同 -c，会先清空整表)，'
                             '全量灌库请用 all 或不传')
    parser.add_argument('-s', '--source', type=str,
                        choices=['parquet', 'api'], default='parquet',
                        help='数据来源: parquet=本地dump全量替换(默认，不能与 -c/-x 子集同用) '
                             '/ api=在线接口区间增量(-c/-x 可正常同用)')
    parser.add_argument('--parquet', type=str, default=None,
                        help='dump 文件路径; 默认取 download/ 下文件名日期后缀最大的那个。'
                             '仅用于 -s parquet 全表替换模式')
    parser.add_argument('-f', '--forcerun', action='store_true',
                        help='强制运行, 即使当前日期不是交易日')
    return parser


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数, 并拦截 parquet 全表替换与 -c/-x 子集过滤的危险组合

    parquet 模式写库前会先清空整表再插入过滤后的数据, 一旦叠加 -c/-x 子集,
    实际效果就是「清空全表 -> 只插入这一小部分」, 其余股票/交易所的历史除权事件
    会被永久删除, 且退出码仍是 0。这种组合没有合理用例(按代码/交易所查询应该用
    -s api, 或直接调 ths.load_xdr_events() 看 dump), 因此检测到就 parser.error()
    拒绝(argparse 用法错, 退出码 2)。

    校验逻辑放在这里而不是 build_parser(): build_parser() 必须保持零副作用
    (纯构造 parser、不触发解析), 自省场景会直接调用它。
    """
    parser = build_parser()
    args = parser.parse_args()
    if args.source == "parquet":
        if args.codes:
            parser.error(
                "-s parquet 是全表替换模式，不能与 -c 同用："
                "它会先清空整表再只插入过滤后的行，导致其余股票的历史事件被永久删除。"
                "想按代码取数请用 -s api -c，或直接调 ths.load_xdr_events() 查看 dump。")
        if requested_exchanges(args.exchanges) != {"SH", "SZ", "BJ"}:
            parser.error(
                "-s parquet 是全表替换模式，不能与 -x 子集同用（同上原因）。"
                "全量灌库请用 -x all 或不传 -x。")
    return args


def check_parameters(begin: str, end: str, forcerun: bool, source: str) -> bool:
    """parquet 模式跳过交易日校验: 灌的是全历史静态文件, 与运行当天无关"""
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
    ]
    if source == "api" and not forcerun:
        validators.append(pv.v_single_day_must_be_trading_day("begin", "end"))
    return pv.run(ctx, validators)


def resolve_parquet_path(explicit: str | None = None,
                         search_dir: Path | None = None) -> Path:
    """定位 dump 文件: 显式路径优先, 否则取文件名日期后缀最大的

    按文件名而非 mtime: 重新下载会刷新 mtime, 但文件名里的 YYYYMMDD 才是数据截止日。
    """
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"指定的 dump 文件不存在: {p}")
        return p

    base = Path(search_dir) if search_dir else DOWNLOAD_DIR
    candidates = sorted(base.glob(PARQUET_GLOB),
                        key=lambda f: (_DATE_SUFFIX_RE.search(f.name).group(1)
                                       if _DATE_SUFFIX_RE.search(f.name) else ""))
    if not candidates:
        raise FileNotFoundError(
            f"{base} 下找不到匹配 {PARQUET_GLOB} 的文件；请用 --parquet 显式指定")
    return candidates[-1]


def requested_exchanges(exchanges: list[str]) -> set[str]:
    """本源覆盖沪深北三个交易所(实测 SZ 30195 / SH 25933 / BJ 1102)"""
    target = {e.lower() for e in exchanges}
    if 'all' in target:
        return {"SH", "SZ", "BJ"}
    return {e.upper() for e in target}


def parse_codes_arg(codes: list[str] | None) -> list[str]:
    """把 -c 解析成裸码列表，支持空格与逗号混合分隔。"""
    if not codes:
        return []
    out: list[str] = []
    for item in codes:
        out.extend(p.strip() for p in str(item).split(",") if p.strip())
    return out


def filter_by_codes(df: pd.DataFrame, codes: list[str]) -> pd.DataFrame:
    """按裸码过滤标准代码帧"""
    if df is None or df.empty or not codes:
        return df
    bare = df["code"].astype(str).str.split(".").str[0]
    return df[bare.isin(set(codes))]


def filter_by_exchange(df: pd.DataFrame, wanted: set[str]) -> pd.DataFrame:
    """按标准代码后缀过滤交易所"""
    if df is None or df.empty:
        return df
    sfx = df["code"].astype(str).str.split(".").str[-1].str.upper()
    return df[sfx.isin(wanted)]


def _candidate_codes(conn, begin_date: str, end_date: str) -> list[str]:
    """api 模式的候选集: gbbq 在区间内有除权事件的股票

    接口 thscode 不支持批量, 全市场逐只请求不可行(5900 次/天);
    只查 gbbq 说有事件的那几十只, 增量才成立。
    """
    rows = conn.execute("""
        SELECT DISTINCT i.code
        FROM CAPITAL_DETAIL c
        INNER JOIN STOCK_INFO i ON i.symbol = c.code
        WHERE c.category = '除权除息' AND c.date BETWEEN ? AND ?
        ORDER BY i.code
    """, [begin_date, end_date]).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    myutil.configure_etl_logging()
    args = parse_arguments()

    if not check_parameters(args.begin, args.end, args.forcerun, args.source):
        return 1

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date = myutil.trans_datestr_format(args.end)
    codes = parse_codes_arg(args.codes)
    wanted = requested_exchanges(args.exchanges)

    logger.info("=" * 60)
    logger.info("同花顺除权事件入库任务启动")
    logger.info(f"     数据来源: {args.source}")
    if args.source == "api":
        logger.info(f"     起始日期: {begin_date}")
        logger.info(f"     结束日期: {end_date}")
    else:
        logger.info("     日期范围: 全量(parquet 模式忽略 -b/-e)")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     指定代码: {codes if codes else '无 (全量)'}")
    logger.info("=" * 60)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        if args.source == "parquet":
            try:
                path = resolve_parquet_path(args.parquet)
            except FileNotFoundError as e:
                logger.error(str(e))
                return 1
            logger.info(f"读取 dump: {path}")
            df = ths.load_xdr_events(path)
            df = filter_by_exchange(filter_by_codes(df, codes), wanted)
            written = dbutil.save_xdr_event_ths_to_db(df, conn, source="parquet")
            logger.info(f"任务完成: 全量替换，入库 {written} 条。")
            return 0 if written else 3

        # api 模式
        candidates = _candidate_codes(conn, begin_date, end_date)
        if codes:
            candidates = [c for c in candidates if c.split(".")[0] in set(codes)]
        candidates = [c for c in candidates if c.split(".")[-1].upper() in wanted]
        if not candidates:
            logger.warning(f"{begin_date} ~ {end_date} 区间内 gbbq 无除权事件，无候选股票。")
            return 3

        logger.info(f"候选股票 {len(candidates)} 只，开始逐只请求...")
        sleep_s = float(ths._cfg().get("sleep_between_stocks", 0.2))
        frames: list[pd.DataFrame] = []
        failed: list[str] = []
        import time
        for idx, code in enumerate(candidates):
            try:
                frames.append(ths.fetch_xdr_events(code, begin_date, end_date))
            except Exception as e:
                logger.error(f"[{code}] 取数失败: {e}")
                failed.append(code)
            if idx < len(candidates) - 1:
                time.sleep(sleep_s)

        if failed and len(failed) == len(candidates):
            logger.error(f"全部 {len(candidates)} 只取数失败，任务失败。")
            return 1

        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=ths.XDR_COLUMNS)
        df = filter_by_exchange(df, wanted)
        written = dbutil.save_xdr_event_ths_to_db(
            df, conn, source="api", begin=begin_date, end=end_date)

        if failed:
            logger.warning(f"部分成功: {len(failed)}/{len(candidates)} 只取数失败 {failed}")
            return 3
        logger.info(f"任务完成: 入库 {written} 条。")
        return 0
    except Exception as e:
        logger.error(f"同花顺除权事件入库过程中发生错误: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
