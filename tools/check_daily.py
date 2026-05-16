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
  -f, --forcerun      强制运行，即使当前日期不是交易日

用法:
  python -m tools.check_daily -b 20260325
  python -m tools.check_daily -b 20260301 -e 20260325
  python -m tools.check_daily -b 20260325 -x sh sz
  python -m tools.check_daily -b 20260325 -i
  python -m tools.check_daily -b 20260328 -f
"""
import argparse
import csv
import logging
from pathlib import Path

import duckdb

from util import dbutil, myutil
from util import validators as pv

logger = logging.getLogger("tools.check_daily")


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

    parser.add_argument(
        '-f', '--forcerun',
        action='store_true',
        help='强制运行, 即使当前日期不是交易日'
    )

    return parser.parse_args()


def check_parameters(begin: str, end: str, forcerun: bool) -> bool:
    ctx = {"begin": begin, "end": end, "forcerun": forcerun}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
    ]
    if not forcerun:
        validators.append(pv.v_single_day_must_be_trading_day("begin", "end"))
    return pv.run(ctx, validators)


def _build_exchange_filter(exchanges: list[str]) -> str:
    """根据交易所参数生成 SQL WHERE 片段（交易所经 argparse choices 校验，无注入风险）"""
    exs = [e.lower() for e in exchanges]
    if "all" in exs:
        return ""
    quoted = ", ".join(f"'{e.upper()}'" for e in exs)
    return f"AND i.exchange IN ({quoted})"


def _build_code_filter(codes: list[str] | None) -> tuple[str, list[str]]:
    """根据代码参数生成 SQL WHERE 片段和对应参数列表，使用占位符避免注入"""
    if not codes:
        return "", []
    real_codes: list[str] = []
    for item in codes:
        clean = item.replace('，', ',')
        real_codes.extend([x.strip() for x in clean.split(',') if x.strip()])
    if not real_codes:
        return "", []
    placeholders = ", ".join(["?"] * len(real_codes))
    return f"AND i.symbol IN ({placeholders})", real_codes


def _find_gap_dates(conn: duckdb.DuckDBPyConnection,
                    table: str, date_col: str,
                    begin_date: str, end_date: str,
                    ex_filter: str, code_filter: str, code_params: list[str],
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
    # params 顺序: trading_days日期, active_stocks代码过滤, suspended_cnt日期, actual_cnt日期
    params = [begin_date, end_date, *code_params,
              begin_date, end_date,
              begin_date, end_date]
    return conn.execute(sql, params).fetchall()


def _find_missing_codes(conn: duckdb.DuckDBPyConnection,
                        table: str, date_col: str,
                        gap_date: str,
                        ex_filter: str, code_filter: str, code_params: list[str],
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
    # params 顺序: active_stocks日期过滤, active_stocks代码过滤, JOIN日期 ×2
    params = [gap_date, gap_date, *code_params, gap_date, gap_date]
    return conn.execute(sql, params).fetchall()


def _check_is_st_null(conn: duckdb.DuckDBPyConnection,
                      begin_date: str, end_date: str,
                      ex_filter: str, code_filter: str, code_params: list[str]) -> int:
    """检查 DAILY_BASIC.is_st 字段为 NULL 的记录，停牌股票不计入"""
    label = "is_st字段   "
    sql = f"""
    WITH trading_days AS (
        SELECT cal_date FROM TRADE_CAL
        WHERE is_open = 1 AND cal_date BETWEEN ? AND ?
    ),
    active_stocks AS (
        SELECT code, name FROM STOCK_INFO i
        WHERE board NOT IN ('INDEX', 'BJ')
          AND list_status = 'L'
          {ex_filter}
          {code_filter}
    ),
    suspended AS (
        SELECT code, date FROM STOCK_DAILY
        WHERE tradestatus = 0
          AND date BETWEEN ? AND ?
    )
    SELECT b.trade_date, s.code, s.name
    FROM DAILY_BASIC b
    INNER JOIN active_stocks s ON b.code = s.code
    INNER JOIN trading_days t ON b.trade_date = t.cal_date
    LEFT JOIN suspended p ON p.code = b.code AND p.date = b.trade_date
    WHERE b.is_st IS NULL
      AND p.code IS NULL
    ORDER BY b.trade_date, s.code
    """
    params = [begin_date, end_date, *code_params, begin_date, end_date]
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        logger.info(f"[{label}]    完整 OK")
        return 0

    csv_dir = Path(__file__).parent.parent / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / f"check_isst_null_{begin_date}_{end_date}.csv"
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "code", "name"])
        for trade_date, code, name in rows:
            writer.writerow([str(trade_date), code, name])

    logger.warning(f"[{label}]    发现 {len(rows)} 条 is_st 为 NULL，明细已写入: {csv_file}")
    return len(rows)


# 除权参考价公式(通达信口径,每10股字段折算为每股)
_XDR_THEORY_SQL = (
    "(close_prev - dividend/10 + allotment_price * allotment_share/10) "
    "/ (1 + bonus_share/10 + allotment_share/10)"
)


def _query_xdr_preclose_mismatches(conn: duckdb.DuckDBPyConnection,
                                   begin_date: str, end_date: str,
                                   ex_filter: str, code_filter: str,
                                   code_params: list[str]) -> list[tuple]:
    """
    查询除权日 pre_close 与理论除权参考价不一致(绝对差 > 0.01 元)的记录。
    仅 category='除权除息'、A股(board NOT IN ('INDEX','BJ'))、list_status='L';
    停牌(tradestatus=0)、上一交易日收盘缺失、当日 pre_close 缺失的记录不在此返回。
    "真实除权日"判据: 该股 ADJ_FACTOR.adjust_factor 相对上一交易日发生变化。
    gbbq 的 除权除息.date 不可靠等于真实除权交易日(可能是股权登记日/未实施),
    仅凭 CAPITAL_DETAIL 会大量误报;因此用独立的 ADJ_FACTOR 因子变化做闸门。
    ADJ_FACTOR 在 xdr_date 或 prev_date 缺失时无法确认,该记录不判异常。
    返回 [(xdr_date, code, name, close_prev, pre_close, theory), ...]
    """
    sql = f"""
    WITH xdr_events AS (
        -- CAPITAL_DETAIL.code 是裸 symbol(无交易所后缀),需用 i.symbol 关联;
        -- 对外/下游统一用带后缀的规范代码 i.code
        SELECT i.code AS code, c.date AS xdr_date, i.name,
               COALESCE(c.dividend, 0)        AS dividend,
               COALESCE(c.allotment_price, 0) AS allotment_price,
               COALESCE(c.bonus_share, 0)     AS bonus_share,
               COALESCE(c.allotment_share, 0) AS allotment_share
        FROM CAPITAL_DETAIL c
        INNER JOIN STOCK_INFO i ON i.symbol = c.code
        WHERE c.category = '除权除息'
          AND c.date BETWEEN ? AND ?
          AND i.board NOT IN ('INDEX', 'BJ')
          AND i.list_status = 'L'
          {ex_filter}
          {code_filter}
    ),
    prev_day AS (
        SELECT e.code, e.xdr_date,
               (SELECT MAX(t.cal_date) FROM TRADE_CAL t
                 WHERE t.is_open = 1 AND t.cal_date < e.xdr_date) AS prev_date
        FROM xdr_events e
    ),
    joined AS (
        SELECT e.code, e.xdr_date, e.name,
               e.dividend, e.allotment_price, e.bonus_share, e.allotment_share,
               pd1.close          AS close_prev,
               cur.pre_close      AS pre_close,
               cur.tradestatus    AS tradestatus,
               afc.adjust_factor  AS af_cur,
               afp.adjust_factor  AS af_prev
        FROM xdr_events e
        JOIN prev_day p ON p.code = e.code AND p.xdr_date = e.xdr_date
        LEFT JOIN STOCK_DAILY pd1 ON pd1.code = e.code AND pd1.date = p.prev_date
        LEFT JOIN STOCK_DAILY cur ON cur.code = e.code AND cur.date = e.xdr_date
        LEFT JOIN ADJ_FACTOR  afc ON afc.code = e.code AND afc.trade_date = e.xdr_date
        LEFT JOIN ADJ_FACTOR  afp ON afp.code = e.code AND afp.trade_date = p.prev_date
    ),
    computed AS (
        SELECT code, xdr_date, name, close_prev, pre_close, tradestatus,
               af_cur, af_prev,
               {_XDR_THEORY_SQL} AS theory
        FROM joined
    )
    SELECT xdr_date, code, name, close_prev, pre_close, theory
    FROM computed
    WHERE COALESCE(tradestatus, 1) <> 0
      AND close_prev IS NOT NULL
      AND pre_close IS NOT NULL
      -- 闸门: ADJ_FACTOR 确认当天确实发生除权(因子相对上一交易日变化)
      AND af_cur IS NOT NULL
      AND af_prev IS NOT NULL
      AND ABS(af_cur - af_prev) > 1e-9
      -- 四舍五入到 4 位后比较,过滤浮点尾差;有效阈值≈0.01 元(A股报价精度为分)
      AND ROUND(ABS(pre_close - theory), 4) > 0.01
    ORDER BY xdr_date, code
    """
    params = [begin_date, end_date, *code_params]
    return conn.execute(sql, params).fetchall()


def _count_xdr_uncomputable(conn: duckdb.DuckDBPyConnection,
                            begin_date: str, end_date: str,
                            ex_filter: str, code_filter: str,
                            code_params: list[str]) -> int:
    """统计已确认真实除权(ADJ_FACTOR 因子变化)、非停牌、但因上一交易日收盘
    或当日 pre_close 缺失而无法计算理论价的记录数"""
    sql = f"""
    -- xdr_events/prev_day CTEs mirror _query_xdr_preclose_mismatches — keep WHERE filters in sync
    WITH xdr_events AS (
        -- CAPITAL_DETAIL.code 是裸 symbol(无交易所后缀),需用 i.symbol 关联
        SELECT i.code AS code, c.date AS xdr_date
        FROM CAPITAL_DETAIL c
        INNER JOIN STOCK_INFO i ON i.symbol = c.code
        WHERE c.category = '除权除息'
          AND c.date BETWEEN ? AND ?
          AND i.board NOT IN ('INDEX', 'BJ')
          AND i.list_status = 'L'
          {ex_filter}
          {code_filter}
    ),
    prev_day AS (
        SELECT e.code, e.xdr_date,
               (SELECT MAX(t.cal_date) FROM TRADE_CAL t
                 WHERE t.is_open = 1 AND t.cal_date < e.xdr_date) AS prev_date
        FROM xdr_events e
    )
    SELECT COUNT(*)
    FROM xdr_events e
    JOIN prev_day p ON p.code = e.code AND p.xdr_date = e.xdr_date
    LEFT JOIN STOCK_DAILY pd1 ON pd1.code = e.code AND pd1.date = p.prev_date
    LEFT JOIN STOCK_DAILY cur ON cur.code = e.code AND cur.date = e.xdr_date
    LEFT JOIN ADJ_FACTOR  afc ON afc.code = e.code AND afc.trade_date = e.xdr_date
    LEFT JOIN ADJ_FACTOR  afp ON afp.code = e.code AND afp.trade_date = p.prev_date
    WHERE COALESCE(cur.tradestatus, 1) <> 0
      -- 闸门: 仅统计 ADJ_FACTOR 确认确实发生除权的记录
      AND afc.adjust_factor IS NOT NULL
      AND afp.adjust_factor IS NOT NULL
      AND ABS(afc.adjust_factor - afp.adjust_factor) > 1e-9
      AND (pd1.close IS NULL OR cur.pre_close IS NULL)
    """
    params = [begin_date, end_date, *code_params]
    return conn.execute(sql, params).fetchone()[0]


def _check_xdr_preclose(conn: duckdb.DuckDBPyConnection,
                        begin_date: str, end_date: str,
                        ex_filter: str, code_filter: str,
                        code_params: list[str]) -> int:
    """除权日 pre_close 校验:不一致写 CSV 并返回异常条数(计入 total_missing)"""
    label = "除权前收价  "
    uncomputable = _count_xdr_uncomputable(conn, begin_date, end_date,
                                           ex_filter, code_filter, code_params)
    if uncomputable:
        logger.warning(
            f"[{label}]    {uncomputable} 条除权记录因上一交易日收盘/当日pre_close缺失无法校验"
        )

    rows = _query_xdr_preclose_mismatches(conn, begin_date, end_date,
                                          ex_filter, code_filter, code_params)
    if not rows:
        if not uncomputable:
            logger.info(f"[{label}]    完整 OK")
        return 0

    csv_dir = Path(__file__).parent.parent / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / f"check_preclose_xdr_{begin_date}_{end_date}.csv"
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "code", "name", "close_prev",
                         "pre_close", "theory_preclose", "diff"])
        for xdr_date, code, name, close_prev, pre_close, theory in rows:
            diff = round(abs(pre_close - theory), 4)
            writer.writerow([str(xdr_date), code, name,
                             round(close_prev, 4), round(pre_close, 4),
                             round(theory, 4), diff])

    logger.warning(f"[{label}]    发现 {len(rows)} 条 pre_close 与除权理论价不一致，"
                    f"明细已写入: {csv_file}")
    return len(rows)


def _check_table(conn: duckdb.DuckDBPyConnection,
                 label: str, table: str, date_col: str,
                 begin_date: str, end_date: str,
                 ex_filter: str, code_filter: str, code_params: list[str],
                 is_self_table: bool = False,
                 board_sql: str = "board NOT IN ('INDEX', 'BJ')") -> int:
    """通用检查: 先按日汇总找缺口日期，再逐日展开明细，全量写 CSV"""
    gap_dates = _find_gap_dates(conn, table, date_col,
                                begin_date, end_date,
                                ex_filter, code_filter, code_params,
                                is_self_table=is_self_table, board_sql=board_sql)
    if not gap_dates:
        logger.info(f"[{label}]    完整 OK")
        return 0

    total_missing = sum(exp - act for _, exp, act in gap_dates)

    csv_rows: list[tuple] = []
    for cal_date, expected, actual in gap_dates:
        dt = str(cal_date)
        rows = _find_missing_codes(conn, table, date_col, dt,
                                   ex_filter, code_filter, code_params,
                                   board_sql=board_sql)
        csv_rows.extend((dt, code, name) for code, name in rows)

    if csv_rows:
        csv_dir = Path(__file__).parent.parent / "csv"
        csv_dir.mkdir(parents=True, exist_ok=True)
        tag = table.lower().replace("_", "")
        csv_file = csv_dir / f"check_{tag}_missing_{begin_date}_{end_date}.csv"
        with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "code", "name"])
            writer.writerows(csv_rows)
        logger.warning(f"[{label}]    发现 {total_missing} 条缺失，明细已写入: {csv_file}")
    else:
        logger.warning(f"[{label}]    发现 {total_missing} 条缺失记录，但未能定位具体代码，请手动核查")

    return total_missing


def main() -> int:
    """返回值: 0=完整, 1=有缺失, 2=检查出错"""
    myutil.configure_etl_logging()
    args = parse_arguments()
    if not check_parameters(args.begin, args.end, args.forcerun):
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
    logger.info(f"     强制运行: {'是' if args.forcerun else '否'}")
    logger.info(f"     除权校验: 是 (除权日 pre_close 与理论除权价比对)")
    logger.info("=" * 60)

    ex_filter = _build_exchange_filter(args.exchanges)
    code_filter, code_params = _build_code_filter(args.codes)

    conn = None
    try:
        conn = dbutil.get_connection()

        total_missing = 0
        total_missing += _check_table(conn, "日线数据    ", "STOCK_DAILY", "date",
                                      begin_date, end_date, ex_filter, code_filter, code_params,
                                      is_self_table=True)
        total_missing += _check_table(conn, "复权因子数据", "ADJ_FACTOR", "trade_date",
                                      begin_date, end_date, ex_filter, code_filter, code_params)
        total_missing += _check_table(conn, "指标数据    ", "DAILY_BASIC", "trade_date",
                                      begin_date, end_date, ex_filter, code_filter, code_params)
        total_missing += _check_is_st_null(conn, begin_date, end_date,
                                           ex_filter, code_filter, code_params)
        total_missing += _check_xdr_preclose(conn, begin_date, end_date,
                                             ex_filter, code_filter, code_params)
        if args.include_index:
            total_missing += _check_table(conn, "指数日线数据", "STOCK_DAILY", "date",
                                          begin_date, end_date, ex_filter, code_filter, code_params,
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
