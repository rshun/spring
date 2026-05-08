import argparse
import duckdb
import logging
from util import dbutil,myutil
from util import validators as pv

logger = logging.getLogger("etl.import_daily")

'''
功能: 获取指定日期范围的所有股票交易数据, 已退市的股票暂不获取
输入参数:
  起始日期 (可选，默认今天)
  结束日期 (可选，默认今天)
  股票代码 (可选，支持多个, 优先级最高，如果指定了代码，忽略交易所参数)
  交易所 (可选，支持多个,sz,sh,bj,all)
  数据源 (可选，默认 bstock)
'''

# 解析命令行参数
def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股历史行情数据入库工具 (支持多源、多代码、指定日期)"
    )

    # 参数: 起始日期 (可选，默认今天)
    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
    )

    # 参数: 结束日期 (可选，默认今天)
    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
    )

    # 参数: 股票代码 (可选，支持多个, 优先级最高，如果指定了代码，忽略交易所参数)
    # nargs='+' 表示可以接受 1 个或多个参数，组成列表
    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量,支持空格分隔或逗号分隔'
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
    
    # 参数: 数据源 (可选，默认 bstock)
    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['lday', 'bstock', 'akstock', 'tdx'], # 限制可选项
        default='bstock',
        help='指定数据源类型: lday (本地day文件)  bstock数据源, akstock数据源, tdx (通达信在线) (默认 bstock数据源)'
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
    validators.append(pv.v_single_day_must_be_trading_day("begin", "end"))
    return pv.run(ctx, validators)

# 主函数
def main():

    myutil.configure_etl_logging()
    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("="*60)
    logger.info("获取股票交易明细数据任务启动")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     指定代码: {args.codes if args.codes else '无 (处理全市场)'}")
    logger.info(f"     数据源:   {args.source}")
    logger.info("="*60)
    
    # 获取股票代码列表
    candidate_codes = dbutil.get_candidate_codes(
        begindate    = begin_date,
        enddate      = end_date,
        exchanges_arg= args.exchanges,
        codes_arg    = args.codes
    )

    if not candidate_codes:
        logger.warning("警告: 数据库中没有找到符合条件的股票....")
        return

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        module = myutil.import_source_module(args.source)
        if not hasattr(module, 'fetch_batch_data'):
            logger.error(f"错误: 模块 '{args.source}' 中没有定义 'fetch_batch_data' 方法。")
            return
        '''
        fetch_batch_data 返回两个 DataFrame, 分别插入不同的表
            df_daily: 包含每日交易明细数据(code, date, open, high, low, close, volume, amount)
            df_basic: 包含每日指标数据(code, trade_date, turnover_rate, pe, pb, is_st)
        '''
        stock_data,basic_df = module.fetch_batch_data(candidate_codes)
        if stock_data is not None and not stock_data.empty:
            dbutil.save_daily_to_db(stock_data, conn)
        else:
            logger.warning("未获取到任何股票数据，跳过数据库写入。")

        if basic_df is not None and not basic_df.empty:
            dbutil.save_base_to_db(basic_df, conn)
        else:
            logger.warning("未获取到股票基本数据，跳过数据库写入。")

    except ImportError:
        logger.error(f"错误: 无法导入模块 {args.source}，请检查文件名是否存在。")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()

if __name__ == "__main__":
    main()
