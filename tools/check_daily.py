import argparse
import csv
import logging
import os
from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("tools.check_daily")

"""
功能: 检查指定日期范围内 STOCK_DAILY / ADJ_FACTOR / DAILY_BASIC 数据完整性
      对比 STOCK_INFO + TRADE_CAL 的预期记录数，找出缺失的股票
      停牌股票(tradestatus=0)不计入缺失

输入参数:
  -b, --begin         起始日期 (格式: YYYYMMDD)，默认为当天
  -e, --end           结束日期 (格式: YYYYMMDD)，默认为当天
  -x, --exchanges     交易所范围: sh / sz / bj / all (默认 all)
  -c, --codes         指定股票代码 (可选)
  -i, --include-index 同时校验指数日线数据 (默认不校验)

用法:
  python -m tools.check_daily -b 20260325
  python -m tools.check_daily -b 20260301 -e 20260325
  python -m tools.check_daily -b 20260325 -x sh sz
  python -m tools.check_daily -b 20260325 -i
"""


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股每日数据完整性检查工具"
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
        '-i', '--include-index',
        action='store_true',
        help='同时校验指数日线数据 (默认不校验)'
    )

    return parser.parse_args()


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


def _build_exchange_filter(exchanges: list[str]) -> str:
    """根据交易所参数生成 SQL WHERE 片段"""
    exs = [e.lower() for e in exchanges]
    if "all" in exs:
        return ""
    quoted = ", ".join(f"'{e.upper()}'" for e in exs)
    return f"AND i.exchange IN ({quoted})"


def _build_code_filter(codes: list[str] | None) -> str:
    """根据代码参数生成 SQL WHERE 片段"""
    if not codes:
        return ""
    real_codes: list[str] = []
    for item in codes:
        clean = item.replace('，', ',')
        real_codes.extend([x.strip() for x in clean.split(',') if x.strip()])
    if not real_codes:
        return ""
    quoted = ", ".join(f"'{c}'" for c in real_codes)
    return f"AND i.symbol IN ({quoted})"


def _find_gap_dates(conn, table: str, date_col: str,
                    begin_date: str, end_date: str,
                    ex_filter: str, code_filter: str,
                    is_self_table: bool = False,
                    board_sql: str = "board NOT IN ('INDEX', 'BJ')") -> list[tuple]:
    """
    第一阶段: 按日期汇总，比较预期数量 vs 实际数量，找出有缺口的交易日
    返回 [(cal_date, expected_cnt, actual_cnt), ...]

    is_self_table: True 表示检查 STOCK_DAILY 自身，停牌记录已包含在 actual 中，
                   不需要额外减去; False 表示检查其他表，需要从预期中扣除停牌数
    board_sql: active_stocks 的 board 过滤条件，默认排除指数和北交所
    """
    sql = f"""
    WITH trading_days AS (
        SELECT cal_date
        FROM TRADE_CAL
        WHERE is_open = 1 AND cal_date BETWEEN ? AND ?
    ),
    active_stocks AS (
        SELECT code, list_date, delist_date
        FROM STOCK_INFO i
        WHERE {board_sql}
          AND list_status = 'L'
          {ex_filter}
          {code_filter}
    ),
    expected_cnt AS (
        SELECT t.cal_date, COUNT(*) AS cnt
        FROM active_stocks s
        CROSS JOIN trading_days t
        WHERE t.cal_date >= s.list_date
          AND (s.delist_date IS NULL OR t.cal_date <= s.delist_date)
        GROUP BY t.cal_date
    ),
    suspended_cnt AS (
        SELECT d.date, COUNT(*) AS cnt
        FROM STOCK_DAILY d
        INNER JOIN active_stocks s ON d.code = s.code
        WHERE d.tradestatus = 0
          AND d.date BETWEEN ? AND ?
        GROUP BY d.date
    ),
    actual_cnt AS (
        SELECT t.{date_col} AS dt, COUNT(*) AS cnt
        FROM {table} t
        INNER JOIN active_stocks s ON t.code = s.code
        WHERE t.{date_col} BETWEEN ? AND ?
        GROUP BY t.{date_col}
    )
    SELECT e.cal_date,
           e.cnt - {'0' if is_self_table else 'COALESCE(p.cnt, 0)'} AS expected,
           COALESCE(a.cnt, 0) AS actual
    FROM expected_cnt e
    LEFT JOIN actual_cnt     a ON e.cal_date = a.dt
    LEFT JOIN suspended_cnt  p ON e.cal_date = p.date
    WHERE e.cnt - {'0' if is_self_table else 'COALESCE(p.cnt, 0)'} > COALESCE(a.cnt, 0)
    ORDER BY e.cal_date
    """
    return conn.execute(sql, [begin_date, end_date,
                              begin_date, end_date,
                              begin_date, end_date]).fetchall()


def _find_missing_codes(conn, table: str, date_col: str,
                        gap_date: str,
                        ex_filter: str, code_filter: str,
                        board_sql: str = "board NOT IN ('INDEX', 'BJ')") -> list[tuple]:
    """
    第二阶段: 对单个缺口日期，找出具体缺失的股票
    返回 [(code, name), ...]
    """
    sql = f"""
    WITH active_stocks AS (
        SELECT code, name
        FROM STOCK_INFO i
        WHERE {board_sql}
          AND list_status = 'L'
          AND list_date <= ?
          AND (delist_date IS NULL OR delist_date >= ?)
          {ex_filter}
          {code_filter}
    )
    SELECT s.code, s.name
    FROM active_stocks s
    LEFT JOIN {table}     t ON s.code = t.code AND t.{date_col} = ?
    LEFT JOIN STOCK_DAILY p ON s.code = p.code AND p.date = ? AND p.tradestatus = 0
    WHERE t.code IS NULL
      AND p.code IS NULL
    ORDER BY s.code
    """
    return conn.execute(sql, [gap_date, gap_date, gap_date, gap_date]).fetchall()


def _check_table(conn, label: str, table: str, date_col: str,
                 begin_date: str, end_date: str,
                 ex_filter: str, code_filter: str,
                 is_self_table: bool = False,
                 board_sql: str = "board NOT IN ('INDEX', 'BJ')") -> int:
    """通用检查: 先按日汇总找缺口日期，再逐日展开明细，全量写 CSV"""
    gap_dates = _find_gap_dates(conn, table, date_col,
                                begin_date, end_date, ex_filter, code_filter,
                                is_self_table=is_self_table, board_sql=board_sql)
    if not gap_dates:
        logger.info(f"[{label}]    完整 OK")
        return 0

    total_missing = sum(exp - act for _, exp, act in gap_dates)

    csv_rows: list[tuple] = []
    for cal_date, expected, actual in gap_dates:
        dt = str(cal_date)
        rows = _find_missing_codes(conn, table, date_col, dt, ex_filter, code_filter, board_sql=board_sql)
        csv_rows.extend((dt, code, name) for code, name in rows)

    if csv_rows:
        csv_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "csv")
        os.makedirs(csv_dir, exist_ok=True)
        tag = table.lower().replace("_", "")
        csv_file = os.path.join(csv_dir, f"check_{tag}_missing_{begin_date}_{end_date}.csv")
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "code", "name"])
            writer.writerows(csv_rows)
        logger.warning(f"[{label}]    完整明细已写入: {csv_file}")

    return total_missing


def main() -> int:
    """返回值: 0=完整, 1=有缺失, 2=检查出错"""
    myutil.configure_etl_logging()
    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return 2

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date = myutil.trans_datestr_format(args.end)

    logger.info("=" * 60)
    logger.info("每日数据完整性检查")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     交易所:   {args.exchanges}")
    logger.info(f"     指定代码: {args.codes if args.codes else '无 (检查全市场)'}")
    logger.info(f"     校验指数: {'是' if args.include_index else '否'}")
    logger.info("=" * 60)

    ex_filter = _build_exchange_filter(args.exchanges)
    code_filter = _build_code_filter(args.codes)

    conn = None
    try:
        conn = dbutil.get_connection()

        total_missing = 0
        total_missing += _check_table(conn, "日线数据    ", "STOCK_DAILY", "date",
                                      begin_date, end_date, ex_filter, code_filter,
                                      is_self_table=True)
        total_missing += _check_table(conn, "复权因子数据", "ADJ_FACTOR", "trade_date",
                                      begin_date, end_date, ex_filter, code_filter)
        total_missing += _check_table(conn, "指标数据    ", "DAILY_BASIC", "trade_date",
                                      begin_date, end_date, ex_filter, code_filter)
        if args.include_index:
            total_missing += _check_table(conn, "指数日线数据", "STOCK_DAILY", "date",
                                          begin_date, end_date, ex_filter, code_filter,
                                          is_self_table=True, board_sql="board = 'INDEX'")

        logger.info("-" * 60)
        if total_missing == 0:
            logger.info("检查完成: 所有表数据完整 OK")
            return 0
        else:
            logger.warning(f"检查完成: 共发现 {total_missing} 条缺失记录")
            return 1

    except Exception as e:
        logger.error(f"检查过程中发生错误: {e}")
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
