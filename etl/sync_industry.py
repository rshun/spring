"""
同步申万行业数据
  1、默认通过 ak.stock_industry_clf_hist_sw() 获取股票申万三级行业历史原始数据
  2、指定 --input 时读取 data/SwClassCode_2021.csv，写入申万行业一/二/三级层级定义
"""
import argparse
import duckdb
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("etl.sync_industry")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data"
DEFAULT_INPUT_FILE = "SwClassCode_2021.csv"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="申万行业数据同步工具")
    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['akstock'],
        default='akstock',
        help='指定数据源类型 (默认 akstock)'
    )
    parser.add_argument(
        '--input',
        nargs='?',
        const=DEFAULT_INPUT_FILE,
        default=None,
        help='读取 CSV 文件同步申万行业一/二/三级层级定义；不指定文件名时默认 data/SwClassCode_2021.csv'
    )
    parser.add_argument(
        '--version',
        type=str,
        default='2021',
        help='--input 模式下写入 SW_INDUSTRY 的申万行业版本号 (默认 2021)'
    )
    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        help='强制运行，即使当前日期不是交易日'
    )
    return parser.parse_args()


def check_parameters(forcerun: bool) -> bool:
    ctx = {"forcerun": forcerun}
    validators = [
        pv.v_dbfile_exists(),
    ]
    if not forcerun:
        validators.append(pv.v_single_day_must_be_trading_day())
    return pv.run(ctx, validators)


def resolve_input_file(input_arg: str) -> Path:
    input_path = Path(input_arg).expanduser()
    if input_path.is_absolute():
        return input_path

    # 优先读 config.yaml 中 local_paths.data_dir; 未配置则回退到项目 data/
    from util.config import get_config
    configured = (get_config().get("local_paths") or {}).get("data_dir", "").strip()
    base_dir = Path(configured).expanduser() if configured else DEFAULT_INPUT_DIR
    return base_dir / input_path


def _cell_text(value) -> str:
    if pd.isna(value):
        return ''
    text = str(value).strip()
    return '' if text.lower() == 'nan' else text


def _industry_code(value) -> str:
    text = _cell_text(value)
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    return text


def read_swclasscode_csv(input_file: Path, sw_version: str) -> pd.DataFrame:
    if not input_file.is_file():
        raise FileNotFoundError(f"找不到申万行业分类文件: {input_file}")

    raw = pd.read_csv(input_file, dtype=str, encoding='utf-8')

    required_cols = ['行业代码', '一级行业名称', '二级行业名称', '三级行业名称']
    missing = [c for c in required_cols if c not in raw.columns]
    if missing:
        raise ValueError(f"{input_file.name} 缺少字段: {missing}")

    df = raw[required_cols].copy()
    for col in required_cols:
        df[col] = df[col].map(_cell_text)
    df['行业代码'] = df['行业代码'].map(_industry_code)
    df = df[df['行业代码'] != ''].copy()

    l1_by_name: dict[str, str] = {}
    l2_by_name: dict[tuple[str, str], str] = {}

    for _, row in df.iterrows():
        code = row['行业代码']
        l1_name = row['一级行业名称']
        l2_name = row['二级行业名称']
        l3_name = row['三级行业名称']
        if not l2_name:
            l1_by_name[l1_name] = code
        elif not l3_name:
            l2_by_name[(l1_name, l2_name)] = code

    records = []
    for _, row in df.iterrows():
        code = row['行业代码']
        l1_name = row['一级行业名称']
        l2_name = row['二级行业名称']
        l3_name = row['三级行业名称']

        if not l2_name:
            sw_level = 1
            industry_name = l1_name
            parent_code = None
        elif not l3_name:
            sw_level = 2
            industry_name = l2_name
            parent_code = l1_by_name.get(l1_name)
        else:
            sw_level = 3
            industry_name = l3_name
            parent_code = l2_by_name.get((l1_name, l2_name))

        records.append({
            'sw_version': sw_version,
            'industry_code': code,
            'industry_name': industry_name,
            'sw_level': sw_level,
            'parent_code': parent_code,
        })

    result = pd.DataFrame(records)
    result = result.drop_duplicates(subset=['sw_version', 'industry_code'], keep='last')
    return result[['sw_version', 'industry_code', 'industry_name', 'sw_level', 'parent_code']]


def main() -> None:
    myutil.configure_etl_logging()
    args = parse_arguments()

    if not check_parameters(args.forcerun):
        return

    today = date.today().strftime('%Y-%m-%d')

    logger.info("=" * 60)
    logger.info("申万行业数据同步任务启动")
    logger.info(f"  数据源: {args.source}")
    logger.info(f"  基准日期: {today}")
    is_input_mode = args.input is not None
    logger.info(f"  同步模式: {'行业层级定义' if is_input_mode else '股票行业历史原始数据'}")
    logger.info("=" * 60)

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        if is_input_mode:
            input_file = resolve_input_file(args.input)
            logger.info(f"\n[Step 1] 读取申万行业层级文件: {input_file}")
            industry_df = read_swclasscode_csv(input_file, args.version)
            if industry_df.empty:
                logger.warning("未读取到申万行业层级定义，跳过写入。")
            else:
                logger.info(f"  共读取 {len(industry_df)} 条申万行业层级定义。")
                dbutil.save_sw_industry_hierarchy_to_db(industry_df, conn)
        else:
            module = myutil.import_source_module(args.source)
            if not hasattr(module, 'fetch_stock_industry_clf_hist_sw'):
                logger.error(f"模块 '{args.source}' 中没有定义 'fetch_stock_industry_clf_hist_sw' 方法。")
                return

            logger.info("\n[Step 1] 获取股票申万行业历史原始数据...")
            raw_df = module.fetch_stock_industry_clf_hist_sw()
            if raw_df is None or raw_df.empty:
                logger.warning("未获取到股票申万行业历史原始数据，跳过写入。")
            else:
                logger.info(f"  共获取 {len(raw_df)} 条股票申万行业历史原始数据。")
                dbutil.save_stock_industry_clf_hist_sw_raw_to_db(raw_df, conn)

        logger.info("\n" + "=" * 60)
        logger.info("申万行业数据同步完成")
        logger.info("=" * 60)

    except ImportError as e:
        logger.error(f"依赖或模块导入失败。{e}")
    except Exception as e:
        logger.error(f"执行过程中发生未预期的错误: {e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
