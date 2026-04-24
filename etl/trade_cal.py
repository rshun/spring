import argparse
import duckdb
import logging
from datetime import datetime
from util import dbutil,myutil
from util import validators as pv

logger = logging.getLogger("etl.trade_cal")

'''
  功能: 获取交易日数据,写入TRADE_CAL 表 (支持多源)
'''
# 解析命令行参数
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="获取交易日 (支持多源)"
    )
    
    # 参数: 数据源 (可选，默认 bstock)
    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['bstock', 'akstock'], # 限制可选项
        default='bstock',
        help='指定数据源类型: bstock数据源, akstock数据源 (默认 bstock数据源)'
    )
    
    # 参数: 起始日期 (可选，默认当前年份减10年)
    parser.add_argument(
        '-b', '--begin',
        type=str,
        default = f"{datetime.now().year - 10}0101",
        help='起始日期 (格式: YYYYMMDD),默认当前年份减10年'
    )

    # 参数: 结束日期 (可选，默认次年最后一天)
    parser.add_argument(
        '-e', '--end',
        type=str,
        default=f"{datetime.now().year + 1}1231",
        help='结束日期 (格式: YYYYMMDD),默认次年最后一天'
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
    return pv.run(ctx, validators)

'''
主函数
'''
def main():
    myutil.configure_etl_logging()

    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return

    start_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("="*60)
    logger.info("获取交易日任务启动")
    logger.info(f"     起始日期: {start_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     数据源:   {args.source}")
    logger.info("="*60)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        module = myutil.import_source_module(args.source) 
        if not hasattr(module, 'fetch_sync_calendar'):
            logger.error(f"错误: 模块 '{args.source}' 中没有定义 'fetch_sync_calendar' 方法。")
            return

        cal = module.fetch_sync_calendar(start_date, end_date)

        if cal is not None and not cal.empty:
            dbutil.save_calendar_to_db(cal,conn)
        else:
            logger.warning("未获取到任何交易交易日的数据，跳过数据库写入。")

    except ImportError as e:
        logger.error(f"错误: 无法导入模块 {args.source}，请检查文件名是否存在。{e}")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()

if __name__ == "__main__":
    main()
