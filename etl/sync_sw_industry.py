'''
同步申万行业分类数据
  1、获取申万行业定义，写入 SW_INDUSTRY 表
  2、获取股票-行业映射，写入 STOCK_SW_INDUSTRY 表
'''
import argparse
import logging
from datetime import date

from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.sync_sw_industry")


def parse_arguments():
    parser = argparse.ArgumentParser(description="申万行业数据同步工具")
    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['akstock'],
        default='akstock',
        help='指定数据源类型 (默认 akstock)'
    )
    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        default=False,
        help='强制运行，即使当前日期不是交易日'
    )
    return parser.parse_args()


def check_parameters(forcerun: bool) -> bool:
    ctx = {"forcerun": forcerun}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_single_day_must_be_trading_day(allow_non_trading=bool(forcerun)),
    ]
    return pv.run(ctx, validators)


def main():
    myutil.configure_etl_logging()
    args = parse_arguments()

    if not check_parameters(args.forcerun):
        return

    today = date.today().strftime('%Y-%m-%d')

    logger.info("=" * 60)
    logger.info("申万行业数据同步任务启动")
    logger.info(f"  数据源: {args.source}")
    logger.info(f"  基准日期: {today}")
    logger.info("=" * 60)

    conn = None
    try:
        conn = dbutil.get_connection(is_read_only=False)
        module = myutil.import_source_module(args.source)

        # 1. 同步行业定义
        if not hasattr(module, 'fetch_sw_industries'):
            logger.error(f"错误: 模块 '{args.source}' 中没有定义 'fetch_sw_industries' 方法。")
            return

        logger.info("\n[Step 1] 获取申万行业定义...")
        industry_df = module.fetch_sw_industries()
        if industry_df is None or industry_df.empty:
            logger.warning("未获取到行业定义数据，终止。")
            return
        logger.info(f"  共获取 {len(industry_df)} 条行业定义。")
        dbutil.save_sw_industry_to_db(industry_df, conn)

        # 2. 同步股票-行业映射
        if not hasattr(module, 'fetch_stock_sw_mapping'):
            logger.error(f"错误: 模块 '{args.source}' 中没有定义 'fetch_stock_sw_mapping' 方法。")
            return

        logger.info("\n[Step 2] 获取股票申万行业映射...")
        mapping_df = module.fetch_stock_sw_mapping(industry_df)
        if mapping_df is None or mapping_df.empty:
            logger.warning("未获取到成分股映射数据，跳过写入。")
        else:
            logger.info(f"  共获取 {len(mapping_df)} 条股票映射。")
            dbutil.save_stock_sw_industry_to_db(mapping_df, conn, today)

    except ImportError as e:
        logger.error(f"错误: 无法导入模块 {args.source}，请检查文件是否存在。{e}")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()

    logger.info("\n" + "=" * 60)
    logger.info("申万行业数据同步完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
