import argparse
import duckdb
import logging
import pandas as pd
from util import myutil, dbutil
from util import validators as pv

logger = logging.getLogger("etl.adjust")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="A股复权因子入库 (支持多源、多代码)"
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
        help='截止日期 (格式: YYYYMMDD),默认当天'
    )

    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量'
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
        choices=['bstock'],
        default='bstock',
        help='指定数据源类型: bstock数据源, (默认 bstock数据源)'
    )

    return parser.parse_args()


def process_and_save_adjust_factors(
    adjust_df: pd.DataFrame,
    stock_list: list[tuple],
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """
    处理已有的复权因子数据：按股票稠密化并逐个入库。
    规则：
      - 源数据 adjust_df 只提供"复权因子发生变化的日期及复权因子"（稀疏事件）。
      - 逐日表 ADJ_FACTOR 必须覆盖交易日；当日无事件值则取 T-1（向前填充）。
      - 新股：若历史从未出现过事件值，则默认因子为 1.0，并从 start_date 起补齐到 end/today。
    """
    targets: list[dict[str, str]] = []
    for symbol, exchange, start_d, end_d, *_ in stock_list:
        sym = str(symbol).strip()
        ex = str(exchange).strip().upper()
        code = f"{sym}.{ex}"

        s = str(start_d).strip()
        e = str(end_d).strip()
        if s and e:
            targets.append({"code": code, "start_date": s, "end_date": e})

    if not targets:
        logger.warning("stock_list 为空或无有效区间，跳过复权因子处理。")
        return

    targets_df = pd.DataFrame(targets)

    need_cols = ["code", "date", "fore_factor", "back_factor", "adjust_factor"]
    if adjust_df is None or adjust_df.empty:
        adj_raw = pd.DataFrame(columns=need_cols)
    else:
        missing = [c for c in need_cols if c not in adjust_df.columns]
        if missing:
            raise ValueError(f"adjust_df 缺少列: {missing}")

        adj_raw = adjust_df[need_cols].copy()
        adj_raw["code"] = adj_raw["code"].astype(str)
        adj_raw["date"] = adj_raw["date"].astype(str)

        # 同 code+date 重复：保留最后一条
        adj_raw = (adj_raw
                   .sort_values(["code", "date"])
                   .drop_duplicates(["code", "date"], keep="last"))

    try:
        conn.register("temp_targets", targets_df)
        conn.register("temp_adj_raw", adj_raw)

        conn.execute("BEGIN")
        if not adj_raw.empty:
            conn.execute(
                """
                INSERT OR REPLACE INTO ADJ_FACTOR_RAW
                    (code, trade_date, fore_factor, back_factor, adjust_factor, updated_at)
                SELECT
                    code,
                    CAST(date AS DATE),
                    CAST(fore_factor AS DOUBLE),
                    CAST(back_factor AS DOUBLE),
                    CAST(adjust_factor AS DOUBLE),
                    now()
                FROM temp_adj_raw;
                """
            )

        # 稠密化写入 ADJ_FACTOR（关键：全部 DATE 化，避免类型绑定错误）
        # 说明：
        # - affected：本次发生变更的 code，从 min_change_date 起重算
        # - last_dense：已稠密化表的最后日期，无变更时从 last_dense_date+1 补到 today
        # - 新股首次跑：既无变更也无历史 last_dense -> 兜底用 start_date（修复只补最后一天的 bug）
        conn.execute(
            """
            WITH
            affected AS (
                SELECT
                    code,
                    MIN(CAST(date AS DATE)) AS min_change_date
                FROM temp_adj_raw
                GROUP BY code
            ),
            last_dense AS (
                SELECT
                    code,
                    MAX(trade_date) AS last_dense_date
                FROM ADJ_FACTOR
                GROUP BY code
            ),
            ranges AS (
                SELECT
                    t.code,
                    CASE
                        -- 新股/重置（无稠密历史），或显式回填（start_date 早于已有数据）：
                        -- 直接从 start_date 开始，不受 min_change_date 干扰
                        WHEN ld.last_dense_date IS NULL
                          OR CAST(t.start_date AS DATE) < ld.last_dense_date
                            THEN CAST(t.start_date AS DATE)
                        -- 正常增量续接：从 min(事件日, last_dense+1) 开始
                        ELSE GREATEST(
                            CAST(t.start_date AS DATE),
                            COALESCE(
                                a.min_change_date,
                                CAST(date_add(ld.last_dense_date, INTERVAL 1 DAY) AS DATE)
                            )
                        )
                    END AS from_date,
                    LEAST(CAST(t.end_date AS DATE), CURRENT_DATE) AS to_date
                FROM temp_targets t
                LEFT JOIN affected a ON a.code = t.code
                LEFT JOIN last_dense ld ON ld.code = t.code
                WHERE CAST(t.start_date AS DATE) <= CAST(t.end_date AS DATE)
            ),
            cal AS (
                SELECT
                    r.code,
                    CAST(c.cal_date AS DATE) AS trade_date
                FROM ranges r
                JOIN TRADE_CAL c
                ON c.is_open = 1
                AND CAST(c.cal_date AS DATE) BETWEEN r.from_date AND r.to_date
                WHERE r.from_date <= r.to_date
            ),
            ev AS (
                SELECT
                    code,
                    trade_date,
                    fore_factor,
                    back_factor,
                    adjust_factor
                FROM ADJ_FACTOR_RAW
                WHERE code IN (SELECT code FROM ranges)
            )
            INSERT OR REPLACE INTO ADJ_FACTOR
                (code, trade_date, fore_factor, back_factor, adjust_factor, updated_at)
            SELECT
                cal.code,
                cal.trade_date,
                COALESCE(ev.fore_factor, 1.0) AS fore_factor,
                COALESCE(ev.back_factor, 1.0) AS back_factor,
                COALESCE(ev.adjust_factor, 1.0) AS adjust_factor,
                now() AS updated_at
            FROM cal
            ASOF LEFT JOIN ev
            ON cal.code = ev.code
            AND cal.trade_date >= ev.trade_date;
            """
        )

        conn.execute("COMMIT")
        logger.info("复权因子稠密化完成：ADJ_FACTOR 已更新。")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error(f"复权因子稠密化失败：{e}")
        raise
    finally:
        for name in ("temp_targets", "temp_adj_raw"):
            try:
                conn.unregister(name)
            except Exception:
                pass


def check_parameters(begin: str, end: str) -> bool:
    """校验命令行参数有效性"""
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
        pv.v_single_day_must_be_trading_day("begin", "end"),
    ]
    return pv.run(ctx, validators)


def main() -> None:
    myutil.configure_etl_logging()

    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    logger.info("=" * 60)
    logger.info("获取股票复权因子任务启动")
    logger.info(f"     起始日期:  {begin_date}")
    logger.info(f"     截止日期:  {end_date}")
    logger.info(f"     交易所:    {args.exchanges}")
    logger.info(f"     指定代码:  {args.codes if args.codes else '无 (处理全市场)'}")
    logger.info(f"     数据源:    {args.source}")
    logger.info("=" * 60)

    candidate_codes = dbutil.get_candidate_codes(
        begindate     = begin_date,
        enddate       = end_date,
        exchanges_arg = args.exchanges,
        codes_arg     = args.codes
    )

    if not candidate_codes:
        logger.warning("警告: 数据库中没有找到符合条件的股票")
        return

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = dbutil.get_connection(is_read_only=False)

        module = myutil.import_source_module(args.source)
        if not hasattr(module, 'fetch_adjust_factors'):
            logger.error(f"模块 '{args.source}' 中没有定义 'fetch_adjust_factors' 方法。")
            return

        adjust = module.fetch_adjust_factors(candidate_codes)

        if adjust is None:
            adjust = pd.DataFrame()

        # 即便没有新的复权因子事件，也需要调用处理函数，
        # 以便将 ADJ_FACTOR 表的数据延续（Forward Fill）到 end_date
        process_and_save_adjust_factors(adjust, candidate_codes, conn)

        if not adjust.empty:
            logger.info("复权因子已成功写入数据库（含新变更）。")
        else:
            logger.info("未获取到新复权因子，已执行每日数据补齐。")

    except ImportError:
        logger.error(f"模块 '{args.source}' 不存在，请检查数据源配置。")
    except Exception as e:
        logger.error(f"处理复权因子时发生错误：{e}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
