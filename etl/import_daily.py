# 修改记录:
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
        help='仅将获取结果（含列名）输出到屏幕，不写入数据库'
    )

    return parser


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
    args = build_parser().parse_args()
    args.date_range_only = resolve_by_date(args.source)
    return args


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
        codes_arg     = args.codes
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
            print("stock_data:")
            print(stock_data.to_string(index=False) if stock_data is not None else "None")
            print("\nbasic_df:")
            print(basic_df.to_string(index=False) if basic_df is not None else "None")
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
