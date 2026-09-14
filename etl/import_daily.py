# 修改记录:
#   2026-09-14  Claude  -p 由打屏改为按股票导出 CSV 到 csv/ 目录, 屏幕不再输出数据;
#                       并强制要求同时指定 -c(否则全市场会产出五千多对文件)
#   2026-09-13  Claude  取候选传 is_delist=True: 此前用默认 False, 退市股连候选集都进
#                       不去, 本地已有 .day 文件的退市股(如 600387/000594)永远补不进来
#   2026-08-19  Claude  main() 返回退出码(0成功/1失败)并由 sys.exit 传出，供外部判定成败
#   2026-08-19  Claude  拆出 build_parser()，供 tools/describe_cli.py 自省参数
#   2026-09-07  Claude  只传起止日期时改走 bstock 按交易日接口；新增 --by-date 开关，
#                       把该路由暴露到 CLI 自省出口(契约 C4)并支持强制开关
#   2026-09-07  Claude  by-date auto 放宽为「只要不带非日期参数即启用」：不带参数
#                       跑当天全市场、或只给一端日期，都走按日接口
#   2026-09-07  Claude  删除 --by-date 开关：路由完全由参数形态推断，强制开关没有
#                       实际使用场景(带非日期参数时逐股本就更划算)；数据源判断收进
#                       resolve_by_date；模块能力守卫改为检查实际要调用的方法
"""
功能: 获取指定日期范围的所有股票交易数据, 已退市的股票暂不获取
输入参数:
  起始日期 (可选，默认今天)
  结束日期 (可选，默认今天)
  股票代码 (可选，支持多个, 优先级最高，如果指定了代码，忽略交易所参数)
  交易所 (可选，支持多个,sz,sh,bj,all)
  数据源 (可选，默认 bstock)
"""
import argparse
import duckdb
import logging
import sys
from pathlib import Path

from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.import_daily")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A股历史行情数据入库工具 (支持多源、多代码、指定日期)"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
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
        '-s', '--source',
        type=str,
        choices=['lday', 'bstock', 'tdx'],
        default='bstock',
        help='指定数据源类型: lday (本地day文件)  bstock数据源, tdx (通达信在线) (默认 bstock数据源)'
    )

    parser.add_argument(
        '-p', '--print-only',
        action='store_true',
        help='仅把获取结果按股票导出为 CSV 到 csv/ 目录，不写入数据库；'
             '必须同时指定 -c。文件名 <code>_daily_<begin>_<end>.csv 与 '
             '<code>_basic_<begin>_<end>.csv，该股无数据则不产出对应文件'
    )

    return parser


# 导出目录: 与 check_daily / check_adjust 的差异明细同目录, 已在 .gitignore 中
CSV_DIR = Path(__file__).resolve().parents[1] / "csv"


def resolve_by_date(source: str = 'bstock') -> bool:
    """决定是否走 bstock 的按交易日接口。没有开关，完全由参数形态推断。

    只有 bstock 提供按日整市场接口，其余源一律返回 False。

    其次按「命令行没有给出任何非日期参数」判断——日期可以给区间、只给一端，也
    可以完全不给(缺省为当天)，都算「只带日期」，走按日接口拉全市场；一旦出现
    -c/-x/-s/-p 之一就退回逐股接口，因为那些参数意味着只要一部分股票或另一个
    数据源，此时逐股请求本就比拉全市场再筛更划算。

    显式参数用一个 default 全部置 None 的探针 parser 重解析 argv 得到——它与主
    parser 同源，缩写、--begin=X 等写法的解析结果天然一致，不需要另行维护一份
    参数表。
    """
    if source != 'bstock':
        return False

    probe = build_parser()
    for action in probe._actions:
        action.default = None
    given = {dest for dest, value in vars(probe.parse_args()).items() if value is not None}
    return given <= {'begin', 'end'}


def parse_arguments() -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args()
    # -p 强制要求 -c: 不带 -c 时候选是全市场, 会一次生成五千多对文件。
    # 拦在 parse_arguments 而非 build_parser —— 后者必须保持无副作用,
    # tools/describe_cli.py 靠它自省参数(契约 C4)。
    if args.print_only and not args.codes:
        parser.error("-p/--print-only 必须同时指定 -c/--codes，"
                     "否则会对全市场每只股票各产出一对 CSV")
    args.date_range_only = resolve_by_date(args.source)
    return args


def _export_csv(stock_data, basic_df, begin: str, end: str) -> int:
    """按股票导出 CSV, 返回产出的文件数。屏幕不输出数据。

    begin/end 用命令行原样的 YYYYMMDD, 不用转换后的 YYYY-MM-DD —— 文件名里
    不带分隔符更省事, 也与约定的命名一致。
    """
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for tag, df in (("daily", stock_data), ("basic", basic_df)):
        if df is None or df.empty or "code" not in df.columns:
            # basic 缺失是常态(部分数据源不提供), 不产出空文件也不报错
            logger.info(f"无 {tag} 数据，跳过导出")
            continue
        seen: dict[str, str] = {}
        for code, sub in df.groupby("code", sort=True):
            # 文件名取 6 位裸代码, 不带 .SH/.SZ 后缀
            symbol = str(code).split(".")[0]
            if symbol in seen:
                # 同一裸代码撞名会静默覆盖前一只。A 股 6 位代码按前缀区分市场,
                # 正常不会撞; 真撞上必须报出来, 不能让用户拿到少一半的结果。
                logger.error(f"裸代码 {symbol} 同时对应 {seen[symbol]} 与 {code}，"
                             f"文件名冲突，已跳过 {code}")
                continue
            seen[symbol] = str(code)
            path = CSV_DIR / f"{symbol}_{tag}_{begin}_{end}.csv"
            sub.to_csv(path, index=False, encoding="utf-8-sig")
            logger.info(f"已导出 {path}（{len(sub)} 行）")
            written += 1
    return written


def check_parameters(begin: str, end: str) -> bool:
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
        pv.v_single_day_must_be_trading_day("begin", "end"),
    ]
    return pv.run(ctx, validators)


def main() -> int:
    myutil.configure_etl_logging()
    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return 1

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("=" * 60)
    logger.info("获取股票交易明细数据任务启动")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     指定代码: {args.codes if args.codes else '无 (处理全市场)'}")
    logger.info(f"     数据源:   {args.source}")
    logger.info("=" * 60)

    candidate_codes = dbutil.get_candidate_codes(
        begindate     = begin_date,
        enddate       = end_date,
        exchanges_arg = args.exchanges,
        codes_arg     = args.codes,
        # 退市股纳入取价: 与 etl/adjust.py 对齐。两边不对称本身就是缺陷——adjust 已把
        # 退市股纳入因子计算, import_daily 却不给它们取价, 会留下「数据链完整」的错觉。
        # 日常跑批不受影响: get_candidate_data 用 delist_date 作窗口上界, 已退市个股
        # 在 -b/-e 取当天时 eff_begin > eff_end, 自然落选(见 test_dbutil_logic.py
        # ::test_get_candidate_data_delist_excluded_from_daily_run)。
        is_delist     = True
    )

    if not candidate_codes:
        logger.warning("警告: 数据库中没有找到符合条件的股票")
        return 1

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        module = myutil.import_source_module(args.source)

        # 守卫检查的方法必须与下面实际调用的一致，否则模块能力缺失会漏到
        # 最后的 except Exception 里，跟网络故障、数据库锁混在一起
        by_date = getattr(args, 'date_range_only', False)
        required = 'fetch_daily_data_by_date' if by_date else 'fetch_batch_data'
        if not hasattr(module, required):
            logger.error(f"模块 '{args.source}' 中没有定义 '{required}' 方法。")
            return 1

        if by_date:
            stock_data, basic_df = module.fetch_daily_data_by_date(
                candidate_codes, begin_date, end_date
            )
        else:
            stock_data, basic_df = module.fetch_batch_data(candidate_codes)

        if args.print_only:
            written = _export_csv(stock_data, basic_df, args.begin, args.end)
            if not written:
                logger.warning("未获取到任何数据，未产出文件")
                return 1
            logger.info(f"导出完成，共 {written} 个文件，目录: {CSV_DIR}")
            return 0

        conn = dbutil.get_connection(is_read_only=False)
        if stock_data is not None and not stock_data.empty:
            dbutil.save_daily_to_db(stock_data, conn)
        else:
            logger.warning("未获取到任何股票数据，跳过数据库写入。")

        if basic_df is not None and not basic_df.empty:
            dbutil.save_base_to_db(basic_df, conn)
        else:
            logger.warning("未获取到股票基本数据，跳过数据库写入。")

        return 0

    except ImportError:
        logger.error(f"无法导入模块 {args.source}，请检查文件名是否存在。")
        return 1
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
