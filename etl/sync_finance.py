# 修改记录:
#   2026-05-29  Claude  新增专业财务报表(cw)同步 ETL 编排
"""
同步通达信专业财务报表(cw)数据并入库 FINANCE_REPORT。

数据源逻辑(下载/解析/遍历)在 datasource/tdx_offline.py，位置列到具名字段的
转换在 datasource/cw_fields.py，本文件只保留 ETL 编排：遍历报告期 → 转换 → 入库。

用法示例:
    python -m etl.sync_finance                              # 导入本地全部报告期
    python -m etl.sync_finance --download                   # 先更新本地 cw 文件再导入
    python -m etl.sync_finance --start 20200101 --end 20241231
    python -m etl.sync_finance --codes 000001,600519        # 只导入指定股票
"""
import argparse
import logging

from datasource import cw_fields, tdx_offline
from util import dbutil, myutil

logger = logging.getLogger("etl.sync_finance")


def _parse_codes(codes: list[str] | None) -> set[str] | None:
    """将 --codes 参数(支持逗号/中文逗号/多段)规整为裸代码集合，未传则返回 None"""
    if not codes:
        return None
    out: set[str] = set()
    for item in codes:
        clean = item.replace("，", ",")
        out.update(x.strip() for x in clean.split(",") if x.strip())
    return out or None


def run_sync(start: str | None = None, end: str | None = None,
             codes: list[str] | None = None, download: bool = False) -> None:
    """编排专业财务报表数据的获取与入库"""
    myutil.configure_etl_logging()

    if download:
        logger.info("先更新本地 cw 专业财务文件...")
        tdx_offline.sync_cw_files()

    code_set = _parse_codes(codes)

    conn = None
    total = 0
    periods = 0
    try:
        conn = dbutil.get_connection(is_read_only=False)
        for report_date, raw_df in tdx_offline.iter_cw_reports(start, end):
            df = cw_fields.cw_df_to_finance_report(raw_df, report_date)
            if df.empty:
                continue
            if code_set is not None:
                df = df[df["code"].isin(code_set)]
                if df.empty:
                    continue
            dbutil.save_finance_report_to_db(df, conn)
            total += len(df)
            periods += 1

        if periods == 0:
            logger.warning("未导入任何专业财务数据(无匹配报告期或本地文件缺失)。")
        else:
            logger.info(f"专业财务报表同步完成，共 {periods} 个报告期、{total} 条记录")
    except Exception as e:
        logger.error(f"专业财务报表入库失败: {e}")
    finally:
        if conn is not None:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="同步通达信专业财务报表(cw)数据到 FINANCE_REPORT")
    parser.add_argument("--start", help="起始报告期 YYYYMMDD 或 YYYY-MM-DD(默认不限)")
    parser.add_argument("--end", help="结束报告期 YYYYMMDD 或 YYYY-MM-DD(默认不限)")
    parser.add_argument("--codes", nargs="+", help="指定股票裸代码(逗号或空格分隔)，默认全市场")
    parser.add_argument(
        "--download",
        action="store_true",
        help="导入前先运行 sync_cw_files 更新本地 cw 文件(默认直接读本地)",
    )
    args = parser.parse_args()
    run_sync(start=args.start, end=args.end, codes=args.codes, download=args.download)


if __name__ == "__main__":
    main()
