import logging
import duckdb
import pandas as pd
from . import myutil
from datetime import datetime, date
from typing import List, Tuple, Optional

logger = logging.getLogger("etl.util.dbutil")


def check_is_trading_day(date_str: str) -> bool:
    """检查指定日期是否为交易日，参数格式 YYYY-MM-DD，返回 True/False"""
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = get_connection()
        result = conn.execute(
            "SELECT IS_OPEN FROM TRADE_CAL WHERE CAL_DATE = ?",
            [date_str]
        ).fetchone()

        if result is None:
            logger.warning(f"日历表中不存在日期 {date_str} 的记录。")
            return False

        return True if result[0] == 1 else False

    except Exception as e:
        logger.error(f"检查日历表失败: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def get_candidate_codes(
        begindate: str,
        enddate: str,
        exchanges_arg: list[str],
        codes_arg: list[str],
        is_delist: bool = False
    ) -> list[tuple]:
    """从 stock_info 表获取符合条件的股票代码列表（不含指数）"""
    sql = "SELECT SYMBOL,EXCHANGE,LIST_DATE,DELIST_DATE,LIST_STATUS FROM STOCK_INFO WHERE BOARD <> 'INDEX'"
    return get_candidate_data(begindate, enddate, exchanges_arg, codes_arg, is_delist, sql)


def get_candidate_index(
        begindate: str,
        enddate: str,
        exchanges_arg: list[str],
        index_arg: list[str],
        is_delist: bool = False
    ) -> list[tuple]:
    """从 stock_info 表获取符合条件的指数代码列表"""
    sql = "SELECT SYMBOL,EXCHANGE,LIST_DATE,DELIST_DATE,LIST_STATUS FROM STOCK_INFO WHERE BOARD = 'INDEX'"
    return get_candidate_data(begindate, enddate, exchanges_arg, index_arg, is_delist, sql)


def get_candidate_data(
        begindate: Optional[str],
        enddate: Optional[str],
        exchanges_arg: list[str],
        codes_arg: list[str],
        is_delist: bool,
        sql: str
    ) -> list[tuple]:
    """
    获取指定的股票或指数。
    代码优先级最高；未传代码时按交易所过滤；未传交易所则全市场。
    开始日期小于上市日期时以上市日期为准。
    """
    params = []

    if codes_arg:
        real_codes = []
        for item in codes_arg:
            clean_item = item.replace('，', ',')
            real_codes.extend([x.strip() for x in clean_item.split(',') if x.strip()])

        if real_codes:
            placeholders = ','.join(['?'] * len(real_codes))
            sql += f" AND SYMBOL IN ({placeholders})"
            params.extend(real_codes)

    elif exchanges_arg and 'all' not in exchanges_arg:
        target_markets = [e.upper() for e in exchanges_arg]
        placeholders = ','.join(['?'] * len(target_markets))
        sql += f" AND EXCHANGE IN ({placeholders})"
        params.extend(target_markets)
        logger.info(f"依据交易所: {target_markets}")
    else:
        logger.info("全市场模式 (所有在市股票或指数)")

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = get_connection()
        sql = sql + " ORDER BY SYMBOL"
        rows = conn.execute(sql, params).fetchall()
        out: List[Tuple[str, str, str, str, str]] = []

        today_date = date.today()
        req_begin_date = datetime.strptime(begindate, "%Y-%m-%d").date() if begindate else None
        req_end_date = datetime.strptime(enddate, "%Y-%m-%d").date() if enddate else None

        for symbol, exchange, list_date, delist_date, list_status in rows:
            if not list_date:
                continue

            if req_begin_date:
                eff_begin = list_date if list_date > req_begin_date else req_begin_date
            else:
                eff_begin = list_date

            cap_date = today_date if list_status == "L" else delist_date
            if cap_date is None:
                logger.warning(f"{symbol}.{exchange} 已退市但无退市日，跳过")
                continue
            eff_end = cap_date if req_end_date is None else min(req_end_date, cap_date)

            if eff_begin <= eff_end and (is_delist or list_status == "L"):
                out.append((
                    str(symbol),
                    str(exchange),
                    eff_begin.strftime("%Y-%m-%d"),
                    eff_end.strftime("%Y-%m-%d"),
                    str(list_status)
                ))

        return out
    except FileNotFoundError:
        logger.error("数据库文件不存在，请先运行init_db.py初始化数据库")
        return []
    except Exception as e:
        logger.error(f"获取候选股票失败: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


def get_connection(is_read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """获取数据库连接"""
    db_path = myutil.get_default_dbfile()
    if is_read_only and not db_path.exists():
        raise FileNotFoundError(f"数据库文件不存在，无法以只读模式连接: {db_path}")

    return duckdb.connect(str(db_path), read_only=is_read_only)


def _normalize_daily_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一补齐 pre_close / tradestatus 缺失字段，原地修改并返回"""
    if "pre_close" not in df.columns:
        df["pre_close"] = -1
    else:
        df["pre_close"] = df["pre_close"].fillna(-1)

    if "tradestatus" not in df.columns:
        if "trade_status" in df.columns:
            df["tradestatus"] = df["trade_status"]
        else:
            df["tradestatus"] = -1

    df["tradestatus"] = df["tradestatus"].fillna(-1)
    return df


def save_base_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """写入每日指标数据到 DAILY_BASIC 表，使用 ON CONFLICT 避免覆盖其他来源写入的字段"""
    try:
        conn.register("temp_daily_basic", df)
        conn.execute("""
            INSERT INTO DAILY_BASIC
                (code, trade_date, turnover_rate, pe, pb, is_st)
            SELECT
                code,
                CAST(trade_date AS DATE),
                turnover_rate,
                pe,
                pb,
                is_st
            FROM temp_daily_basic
            ON CONFLICT (code, trade_date) DO UPDATE
            SET turnover_rate = COALESCE(EXCLUDED.turnover_rate, DAILY_BASIC.turnover_rate),
                pe            = COALESCE(EXCLUDED.pe,            DAILY_BASIC.pe),
                pb            = COALESCE(EXCLUDED.pb,            DAILY_BASIC.pb),
                is_st         = COALESCE(EXCLUDED.is_st,         DAILY_BASIC.is_st)
        """)
        logger.info(f"[入库] 成功合并 {len(df)} 条每日指标数据")
    except Exception as e:
        logger.error(f"写入 DAILY_BASIC 表失败: {e}")
    finally:
        try:
            conn.unregister("temp_daily_basic")
        except Exception:
            pass


def save_shares_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """将股本数据(total_shares, float_shares)写入 DAILY_BASIC 表，仅更新股本字段"""
    try:
        conn.register("temp_shares", df)
        conn.execute("""
            INSERT INTO DAILY_BASIC (code, trade_date, total_shares, float_shares)
            SELECT code, CAST(date AS DATE), total_shares, float_shares
            FROM temp_shares
            ON CONFLICT (code, trade_date) DO UPDATE
            SET total_shares = EXCLUDED.total_shares,
                float_shares = EXCLUDED.float_shares
        """)
        logger.info(f"[入库] 成功合并 {len(df)} 条股本数据到 DAILY_BASIC")
    except Exception as e:
        logger.error(f"写入股本数据到 DAILY_BASIC 失败: {e}")
    finally:
        try:
            conn.unregister("temp_shares")
        except Exception:
            pass


def save_daily_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """写入股票行情明细数据到 STOCK_DAILY 表"""
    logger.info(f"正在将 {len(df)} 条行情明细写入数据库...")
    try:
        df = _normalize_daily_df(df)
        conn.register("temp_stock_daily", df)
        conn.execute("""
            INSERT OR REPLACE INTO STOCK_DAILY
                (code, date, open, high, low, close, pre_close, tradestatus, volume, amount)
            SELECT
                code,
                CAST(date AS DATE),
                open, high, low, close,
                pre_close, tradestatus,
                volume, amount
            FROM temp_stock_daily
        """)
        logger.info("行情数据入库成功。")
    except Exception as e:
        logger.error(f"写入 STOCK_DAILY 表失败: {e}")
    finally:
        try:
            conn.unregister("temp_stock_daily")
        except Exception:
            pass


def save_calendar_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection):
    """将交易日数据写入 trade_cal 表"""
    logger.info(f"正在将 {len(df)} 条日历记录写入数据库...")

    temp_name = "temp_trade_cal"
    conn.register(temp_name, df)

    try:
        upsert_sql = f"""
            INSERT INTO trade_cal AS t (cal_date, is_open)
            SELECT
                CAST(cal_date AS DATE) AS cal_date,
                is_open
            FROM {temp_name}
            ON CONFLICT (cal_date) DO UPDATE SET
                is_open = excluded.is_open;
        """
        conn.execute(upsert_sql)

        result = conn.execute("SELECT COUNT(*) FROM trade_cal").fetchone()
        count = result[0] if result else 0
        logger.info(f"入库成功！当前 TRADE_CAL 表总记录数: {count}")
    except Exception as e:
        logger.error(f"写入 TRADE_CAL 执行失败: {e}")
        raise
    finally:
        try:
            conn.unregister(temp_name)
        except Exception:
            pass


def save_index_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """将指数行情明细数据写入 STOCK_DAILY 表"""
    logger.info(f"正在将 {len(df)} 条指数明细写入数据库...")
    try:
        df = _normalize_daily_df(df)

        numeric_cols = ["open", "high", "low", "close", "pre_close", "volume", "amount"]
        for col in numeric_cols:
            if col not in df.columns:
                continue
            mask = df[col].astype(str).str.strip() == ""
            if mask.any():
                bad_codes = df.loc[mask, "code"].unique().tolist()
                logger.warning(
                    f"列 [{col}] 存在空字符串，涉及 {mask.sum()} 条记录，"
                    f"代码: {bad_codes[:20]}{'...' if len(bad_codes) > 20 else ''}"
                )
                df[col] = df[col].replace("", pd.NA)
            df[col] = pd.to_numeric(df[col], errors="coerce")

        conn.register("temp_index_daily", df)
        conn.execute("""
            INSERT OR REPLACE INTO STOCK_DAILY (code, date, open, high, low, close, pre_close, tradestatus, volume, amount)
            SELECT code, CAST(date AS DATE), open, high, low, close, pre_close, tradestatus, volume, amount
            FROM temp_index_daily
        """)
        logger.info("指数数据入库成功。")
    except Exception as e:
        logger.error(f"数据库写入失败: {e}")
    finally:
        try:
            conn.unregister("temp_index_daily")
        except Exception:
            pass


def load_stock_info_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """将股票基本信息 DataFrame 批量 UPSERT 到 stock_info 表"""
    logger.info("\n--- 数据库加载(L)开始 ---")
    logger.info(f"准备将 {len(df)} 条记录 'UPSERT' 到 'stock_info' 表中...")

    try:
        conn.register("temp_stock_info", df)

        sql_upsert = """
            INSERT INTO stock_info AS t
            (code, symbol, name, exchange, board, list_date, delist_date, list_status, created_at, last_updated_at)
            SELECT
                code,
                symbol,
                name,
                exchange,
                board,
                list_date,
                delist_date,
                list_status,
                now(),
                now()
            FROM temp_stock_info
            ON CONFLICT (code) DO UPDATE SET
                symbol         = excluded.symbol,
                name           = excluded.name,
                exchange       = excluded.exchange,
                board          = excluded.board,
                list_date      = excluded.list_date,
                delist_date    = excluded.delist_date,
                list_status    = excluded.list_status,
                last_updated_at = now()
            WHERE
                t.symbol      IS DISTINCT FROM excluded.symbol OR
                t.name        IS DISTINCT FROM excluded.name OR
                t.exchange    IS DISTINCT FROM excluded.exchange OR
                t.board       IS DISTINCT FROM excluded.board OR
                t.list_date   IS DISTINCT FROM excluded.list_date OR
                t.delist_date IS DISTINCT FROM excluded.delist_date OR
                t.list_status IS DISTINCT FROM excluded.list_status;
        """

        conn.execute(sql_upsert)
        logger.info("  [+] 成功！数据已 'UPSERT' 到 'stock_info' 表。")

        count_result = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()
        logger.info(f"  [*] 验证: 'stock_info' 表现在共有 {count_result[0] if count_result else 0} 条记录。")

    except duckdb.CatalogException as e:
        logger.error(f"写入 stock_info 失败: {e}")
        logger.error("  错误提示：很可能是 'stock_info' 表不存在。")
        logger.error("  请确认 'init_db.py' 已经成功运行。")
    except Exception as e:
        logger.error(f"写入 stock_info 失败: {e}")
    finally:
        try:
            conn.unregister("temp_stock_info")
        except Exception:
            pass


def update_price_limits_by_range(start_date: str, end_date: str, markets: list[str] | None = None):
    """计算并批量更新指定日期区间内的涨跌停价"""
    if not markets:
        markets = ["ALL"]

    con: duckdb.DuckDBPyConnection | None = None

    try:
        con = get_connection(is_read_only=False)
        logger.info(f"开始批量计算涨跌停价，时间区间: {start_date} 至 {end_date}")

        market_filter = ""
        market_params: list = []
        if 'ALL' not in markets:
            placeholders = ", ".join(["?"] * len(markets))
            market_filter = f"AND i.exchange IN ({placeholders})"
            market_params = list(markets)

        # 0.000001 是浮点加法补偿，确保恰好在临界值时 ROUND 向上进位
        calc_sql = f"""
            WITH base_data AS (
                SELECT
                    d.code,
                    d.date,
                    d.close,
                    d.pre_close,
                    i.board,
                    COALESCE(b.is_st, 0) as is_st,
                    date_diff('day', i.list_date, d.date) + 1 as days_count
                FROM STOCK_DAILY d
                JOIN STOCK_INFO i ON d.code = i.code
                LEFT JOIN DAILY_BASIC b ON d.code = b.code AND d.date = b.trade_date
                WHERE d.date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND d.tradestatus = 1
                  AND d.pre_close != -1
                  AND i.board IN ('MAIN', 'STAR', 'GEM', 'BJ')
                  {market_filter}
            ),
            calc_rate AS (
                SELECT
                    *,
                    CASE
                        WHEN board = 'STAR' THEN (CASE WHEN days_count <= 5 THEN 0.0 ELSE 0.2 END)
                        WHEN board = 'GEM' THEN (
                            CASE WHEN date >= '2020-08-24' THEN (CASE WHEN days_count <= 5 THEN 0.0 ELSE 0.2 END)
                                 ELSE (CASE WHEN days_count = 1 THEN 0.44 ELSE 0.1 END) END
                        )
                        WHEN board = 'BJ' THEN (CASE WHEN days_count = 1 THEN 0.0 ELSE 0.3 END)
                        WHEN board = 'MAIN' THEN (
                            CASE WHEN date >= '2023-04-10' THEN (CASE WHEN days_count <= 5 THEN 0.0 WHEN is_st = 1 THEN 0.05 ELSE 0.1 END)
                                 ELSE (CASE WHEN days_count = 1 THEN 0.44 WHEN is_st = 1 THEN 0.05 ELSE 0.1 END) END
                        )
                        ELSE 0.1
                    END as up_rate,
                    CASE
                        WHEN board = 'MAIN' AND date < '2023-04-10' AND days_count = 1 THEN 0.36
                        WHEN board = 'GEM'  AND date < '2020-08-24' AND days_count = 1 THEN 0.36
                        ELSE NULL
                    END as down_rate
                FROM base_data
            ),
            final_limits AS (
                SELECT
                    code,
                    date,
                    close,
                    up_rate,
                    CASE
                        WHEN up_rate = 0 THEN 999999.99
                        WHEN board = 'BJ' THEN FLOOR(pre_close * (1 + up_rate) * 100 + 0.0001) / 100.0
                        ELSE ROUND(pre_close * (1 + up_rate) + 0.000001, 2)
                    END as limit_up,
                    CASE
                        WHEN up_rate = 0 THEN 0.01
                        WHEN board = 'BJ' THEN CEIL(pre_close * (1 - COALESCE(down_rate, up_rate)) * 100 - 0.0001) / 100.0
                        ELSE ROUND(pre_close * (1 - COALESCE(down_rate, up_rate)) + 0.000001, 2)
                    END as limit_down
                FROM calc_rate
            ),
            final_marks AS (
                SELECT
                    code,
                    date,
                    limit_up,
                    limit_down,
                    CASE
                        WHEN up_rate = 0 THEN 0
                        WHEN ROUND(close + 0.000001, 2) = limit_up THEN 1
                        ELSE 0
                    END as is_limit_up,
                    CASE
                        WHEN up_rate = 0 THEN 0
                        WHEN ROUND(close + 0.000001, 2) = limit_down THEN 1
                        ELSE 0
                    END as is_limit_down
                FROM final_limits
            )
            UPDATE DAILY_BASIC
            SET limit_up      = t.limit_up,
                limit_down    = t.limit_down,
                is_limit_up   = t.is_limit_up,
                is_limit_down = t.is_limit_down
            FROM final_marks t
            WHERE DAILY_BASIC.code       = t.code
              AND DAILY_BASIC.trade_date = t.date;
        """

        params: list = [start_date, end_date, *market_params]
        logger.info("正在执行批量更新 SQL (这可能需要几秒钟)...")
        con.execute(calc_sql, params)
        logger.info("批量更新完成。")

    except Exception as e:
        logger.error(f"涨跌停价批量更新失败: {e}")
    finally:
        if con:
            con.close()


def fill_daily_basic_volume_ratio(start_date: str, end_date: str,
                                  codes: list[str],
                                  conn: duckdb.DuckDBPyConnection | None = None) -> None:
    """补齐量比数据（前5交易日均量之比）"""
    code_filter = ""
    update_code_filter = ""
    code_params: list[str] = []

    if codes:
        if isinstance(codes, str):
            codes = [codes]

        placeholders = ", ".join(["?"] * len(codes))
        code_filter = f"AND d.code IN ({placeholders})"
        update_code_filter = f"AND DAILY_BASIC.code IN ({placeholders})"
        code_params = list(codes)

    sql = f"""
        WITH valid_daily_data AS (
            SELECT
                d.code,
                d.date,
                d.volume
            FROM STOCK_DAILY d
            JOIN STOCK_INFO i ON d.code = i.code
            WHERE
                i.board NOT IN ('INDEX','BOND','ETF')
                AND d.tradestatus = 1
                {code_filter}
        ),

        calc_ma AS (
            SELECT
                code,
                date,
                volume,
                AVG(volume) OVER (
                    PARTITION BY code
                    ORDER BY date ASC
                    ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                ) as ma5_volume
            FROM valid_daily_data
        ),

        ratio_result AS (
            SELECT
                code,
                date,
                CASE
                    WHEN ma5_volume IS NULL OR ma5_volume = 0 THEN NULL
                    ELSE ROUND(volume / ma5_volume, 2)
                END as v_ratio
            FROM calc_ma
            WHERE date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
        )

        UPDATE DAILY_BASIC
        SET volume_ratio = src.v_ratio
        FROM ratio_result src
        WHERE DAILY_BASIC.code       = src.code
          AND DAILY_BASIC.trade_date = src.date
          AND DAILY_BASIC.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
          {update_code_filter};
    """

    # params 顺序: ratio_result 的 date 过滤 × 1组, UPDATE 的 date 过滤 × 1组, 各自带 code_params
    params: list = [*code_params, start_date, end_date, start_date, end_date, *code_params]

    need_close = conn is None
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = conn if conn is not None else get_connection(is_read_only=False)
        con.execute(sql, params)
        logger.info("更新成功")
    except Exception as e:
        logger.error(f"量比更新失败: {e}")
    finally:
        if need_close and con is not None:
            con.close()


def save_margin_summary_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """将融资融券每日汇总数据写入 MARGIN_SUMMARY_DAILY 表"""
    if df is None or df.empty:
        logger.info("无融资融券汇总数据，跳过写入。")
        return

    logger.info(f"正在将 {len(df)} 条融资融券汇总数据写入数据库...")
    try:
        conn.register("temp_margin_summary", df)
        conn.execute("""
            INSERT OR REPLACE INTO MARGIN_SUMMARY_DAILY
                (trade_date, exchange_code,
                 margin_buy_amount, margin_repay_amount, margin_balance,
                 short_sell_volume, short_repay_volume,
                 short_balance_volume, short_balance_amount,
                 margin_short_balance,
                 created_at, updated_at)
            SELECT
                CAST(trade_date AS DATE),
                exchange_code,
                CAST(margin_buy_amount    AS DOUBLE),
                CAST(margin_repay_amount  AS DOUBLE),
                CAST(margin_balance       AS DOUBLE),
                CAST(short_sell_volume    AS DOUBLE),
                CAST(short_repay_volume   AS DOUBLE),
                CAST(short_balance_volume AS DOUBLE),
                CAST(short_balance_amount AS DOUBLE),
                CAST(margin_short_balance AS DOUBLE),
                now(), now()
            FROM temp_margin_summary
        """)
        logger.info(f"[入库] 成功写入 {len(df)} 条融资融券汇总数据")
    except Exception as e:
        logger.error(f"写入 MARGIN_SUMMARY_DAILY 表失败: {e}")
    finally:
        try:
            conn.unregister("temp_margin_summary")
        except Exception:
            pass


def save_margin_detail_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """将融资融券每日明细数据写入 MARGIN_DETAIL_DAILY 表"""
    if df is None or df.empty:
        logger.info("无融资融券明细数据，跳过写入。")
        return

    logger.info(f"正在将 {len(df)} 条融资融券明细数据写入数据库...")
    try:
        conn.register("temp_margin_detail", df)
        conn.execute("""
            INSERT INTO MARGIN_DETAIL_DAILY
                (trade_date, exchange_code, symbol, code, security_name,
                 margin_buy_amount, margin_repay_amount, margin_balance,
                 short_sell_volume, short_repay_volume,
                 short_balance_volume, short_balance_amount,
                 margin_short_balance,
                 created_at, updated_at)
            SELECT
                CAST(trade_date AS DATE),
                exchange_code,
                symbol,
                code,
                security_name,
                CAST(margin_buy_amount    AS DOUBLE),
                CAST(margin_repay_amount  AS DOUBLE),
                CAST(margin_balance       AS DOUBLE),
                CAST(short_sell_volume    AS DOUBLE),
                CAST(short_repay_volume   AS DOUBLE),
                CAST(short_balance_volume AS DOUBLE),
                CAST(short_balance_amount AS DOUBLE),
                CAST(margin_short_balance AS DOUBLE),
                now(), now()
            FROM temp_margin_detail
            ON CONFLICT (trade_date, exchange_code, symbol) DO UPDATE SET
                code                  = EXCLUDED.code,
                security_name         = EXCLUDED.security_name,
                margin_buy_amount     = EXCLUDED.margin_buy_amount,
                margin_repay_amount   = EXCLUDED.margin_repay_amount,
                margin_balance        = EXCLUDED.margin_balance,
                short_sell_volume     = EXCLUDED.short_sell_volume,
                short_repay_volume    = EXCLUDED.short_repay_volume,
                short_balance_volume  = EXCLUDED.short_balance_volume,
                short_balance_amount  = EXCLUDED.short_balance_amount,
                margin_short_balance  = EXCLUDED.margin_short_balance,
                updated_at            = now()
        """)
        logger.info(f"[入库] 成功写入 {len(df)} 条融资融券明细数据")
    except Exception as e:
        logger.error(f"写入 MARGIN_DETAIL_DAILY 表失败: {e}")
    finally:
        try:
            conn.unregister("temp_margin_detail")
        except Exception:
            pass


def get_trade_dates(start_date: str, end_date: str) -> list[str]:
    """查询 [start_date, end_date] 内的交易日列表，返回 YYYYMMDD 格式"""
    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = get_connection(is_read_only=True)
        rows = conn.execute(
            "SELECT cal_date FROM TRADE_CAL WHERE is_open = 1 "
            "AND cal_date BETWEEN ? AND ? ORDER BY cal_date",
            [start_date, end_date],
        ).fetchall()
        return [r[0].strftime('%Y%m%d') for r in rows]
    except Exception as e:
        logger.error(f"获取交易日列表失败: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


def save_stock_industry_clf_hist_sw_raw_to_db(
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection
) -> None:
    """UPSERT 股票申万行业历史原始数据到 STOCK_INDUSTRY_CLF_HIST_SW_RAW 表"""
    if df is None or df.empty:
        logger.info("无股票申万行业历史原始数据，跳过写入。")
        return
    logger.info(f"正在将 {len(df)} 条股票申万行业历史原始数据写入数据库...")

    required_cols = ['symbol', 'start_date', 'industry_code', 'update_time']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"写入 STOCK_INDUSTRY_CLF_HIST_SW_RAW 失败，缺少字段: {missing}")
        return

    try:
        data = df[required_cols].copy()
        conn.register("temp_stock_industry_clf_hist_sw_raw", data)
        conn.execute("""
            INSERT INTO STOCK_INDUSTRY_CLF_HIST_SW_RAW
                (symbol, start_date, industry_code, update_time, updated_at)
            SELECT
                symbol,
                CAST(start_date AS DATE),
                industry_code,
                CAST(update_time AS TIMESTAMP),
                now()
            FROM temp_stock_industry_clf_hist_sw_raw
            ON CONFLICT (symbol, start_date, industry_code) DO UPDATE SET
                update_time = excluded.update_time,
                updated_at = now()
        """)
        result = conn.execute("SELECT COUNT(*) FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW").fetchone()
        count = result[0] if result else 0
        logger.info(f"股票申万行业历史原始数据写入成功，当前共 {count} 条记录。")
    except Exception as e:
        logger.error(f"写入 STOCK_INDUSTRY_CLF_HIST_SW_RAW 失败: {e}")
    finally:
        try:
            conn.unregister("temp_stock_industry_clf_hist_sw_raw")
        except (duckdb.Error, RuntimeError) as e:
            logger.warning(f"清理 temp_stock_industry_clf_hist_sw_raw 注册表失败: {e}")


def save_sw_industry_hierarchy_to_db(
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection
) -> None:
    """UPSERT 申万行业层级定义到 SW_INDUSTRY 表"""
    if df is None or df.empty:
        logger.info("无申万行业层级定义，跳过写入。")
        return
    logger.info(f"正在将 {len(df)} 条申万行业层级定义写入数据库...")

    required_cols = ['sw_version', 'industry_code', 'industry_name', 'sw_level', 'parent_code']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"写入 SW_INDUSTRY 失败，缺少字段: {missing}")
        return

    try:
        data = df[required_cols].copy()
        conn.register("temp_sw_industry_hierarchy", data)
        conn.execute("""
            INSERT INTO SW_INDUSTRY
                (sw_version, industry_code, industry_name, sw_level, parent_code, updated_at)
            SELECT
                sw_version,
                industry_code,
                industry_name,
                CAST(sw_level AS INTEGER),
                parent_code,
                now()
            FROM temp_sw_industry_hierarchy
            ON CONFLICT (sw_version, industry_code) DO UPDATE SET
                industry_name = excluded.industry_name,
                sw_level      = excluded.sw_level,
                parent_code   = excluded.parent_code,
                updated_at    = now()
            WHERE
                SW_INDUSTRY.industry_name IS DISTINCT FROM excluded.industry_name OR
                SW_INDUSTRY.sw_level      IS DISTINCT FROM excluded.sw_level OR
                SW_INDUSTRY.parent_code   IS DISTINCT FROM excluded.parent_code
        """)
        result = conn.execute("SELECT COUNT(*) FROM SW_INDUSTRY").fetchone()
        count = result[0] if result else 0
        logger.info(f"申万行业层级定义写入成功，当前共 {count} 条记录。")
    except Exception as e:
        logger.error(f"写入 SW_INDUSTRY 失败: {e}")
    finally:
        try:
            conn.unregister("temp_sw_industry_hierarchy")
        except (duckdb.Error, RuntimeError) as e:
            logger.warning(f"清理 temp_sw_industry_hierarchy 注册表失败: {e}")


def fill_daily_basic_shares(start_date: str, end_date: str,
                            codes: list[str] | None = None,
                            exchanges: list[str] | None = None,
                            conn: duckdb.DuckDBPyConnection | None = None) -> None:
    """
    根据 CAPITAL_DETAIL 股本变化记录回填 DAILY_BASIC 的 total_shares / float_shares。
    params 顺序: [start_date, end_date, *code_params, *exchange_params]，三处 SQL 均相同。
    """
    if isinstance(codes, str):
        codes = [codes]

    code_filter = ""
    exchange_filter = ""
    code_params: list[str] = []
    exchange_params: list[str] = []

    if codes:
        placeholders = ", ".join(["?"] * len(codes))
        code_filter = f"AND db.code IN ({placeholders})"
        code_params = list(codes)

    if exchanges:
        placeholders = ", ".join(["?"] * len(exchanges))
        exchange_filter = f"AND i.exchange IN ({placeholders})"
        exchange_params = list(exchanges)

    sql = f"""
        WITH capital_events AS (
            SELECT i.code, cd.date,
                   cd.bonus_share     AS float_shares_wan,
                   cd.allotment_share AS total_shares_wan
            FROM CAPITAL_DETAIL cd
            JOIN STOCK_INFO i ON cd.code = i.symbol
            WHERE cd.category = '股本变化'

            UNION ALL

            SELECT i.code, DATE '1990-01-01' AS date,
                   t.dividend         AS float_shares_wan,
                   t.allotment_price  AS total_shares_wan
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY date) AS rn
                FROM CAPITAL_DETAIL
                WHERE category = '股本变化'
            ) t
            JOIN STOCK_INFO i ON t.code = i.symbol
            WHERE t.rn = 1
        ),

        matched AS (
            SELECT
                db.code,
                db.trade_date,
                CAST(ROUND(ce.total_shares_wan * 10000) AS BIGINT) AS total_shares,
                CAST(ROUND(ce.float_shares_wan * 10000) AS BIGINT) AS float_shares
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            ASOF JOIN capital_events ce
                ON db.code = ce.code
                AND db.trade_date >= ce.date
            WHERE db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                {code_filter}
                {exchange_filter}
        )

        UPDATE DAILY_BASIC
        SET total_shares = m.total_shares,
            float_shares = m.float_shares
        FROM matched m
        WHERE DAILY_BASIC.code       = m.code
          AND DAILY_BASIC.trade_date = m.trade_date;
    """

    range_params: list = [start_date, end_date, *code_params, *exchange_params]

    need_close = conn is None
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = conn if conn is not None else get_connection(is_read_only=False)

        count_sql = f"""
            SELECT COUNT(*)
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            WHERE db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                {code_filter}
                {exchange_filter}
        """
        total_result = con.execute(count_sql, range_params).fetchone()
        total_rows = total_result[0] if total_result else 0

        cd_count_sql = "SELECT COUNT(DISTINCT code) FROM CAPITAL_DETAIL WHERE category = '股本变化'"
        cd_result = con.execute(cd_count_sql).fetchone()
        cd_stocks = cd_result[0] if cd_result else 0
        logger.info(f"  CAPITAL_DETAIL 中共 {cd_stocks} 只股票有股本变化记录")
        logger.info(f"  DAILY_BASIC 目标区间共 {total_rows} 行待处理")

        con.execute(sql, range_params)

        verify_sql = f"""
            SELECT COUNT(*)
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            WHERE db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                AND db.total_shares IS NOT NULL
                {code_filter}
                {exchange_filter}
        """
        updated_result = con.execute(verify_sql, range_params).fetchone()
        updated_rows = updated_result[0] if updated_result else 0
        skipped = total_rows - updated_rows
        logger.info(f"  成功更新 {updated_rows} 行, 跳过 {skipped} 行(无股本变化数据)")

    except Exception as e:
        logger.error(f"更新股本数据失败: {e}")
    finally:
        if need_close and con is not None:
            con.close()


def fill_daily_basic_mv(start_date: str, end_date: str,
                        codes: list[str] | None = None,
                        exchanges: list[str] | None = None,
                        conn: duckdb.DuckDBPyConnection | None = None) -> None:
    """
    根据 DAILY_BASIC 的 total_shares/float_shares 和 STOCK_DAILY 的 close 回填市值。
    前置条件: total_shares 和 float_shares 已回填。
    """
    if isinstance(codes, str):
        codes = [codes]

    code_filter = ""
    exchange_filter = ""
    code_params: list[str] = []
    exchange_params: list[str] = []

    if codes:
        placeholders = ", ".join(["?"] * len(codes))
        code_filter = f"AND db.code IN ({placeholders})"
        code_params = list(codes)

    if exchanges:
        placeholders = ", ".join(["?"] * len(exchanges))
        exchange_filter = f"AND i.exchange IN ({placeholders})"
        exchange_params = list(exchanges)

    sql = f"""
        UPDATE DAILY_BASIC db
        SET total_mv = db.total_shares * sd.close,
            float_mv = db.float_shares * sd.close
        FROM STOCK_DAILY sd
        JOIN STOCK_INFO i ON sd.code = i.code
        WHERE db.code       = sd.code
          AND db.trade_date  = sd.date
          AND db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
          AND db.total_shares IS NOT NULL
          AND i.board IN ('MAIN', 'STAR', 'GEM')
          {code_filter}
          {exchange_filter};
    """

    range_params: list = [start_date, end_date, *code_params, *exchange_params]

    need_close = conn is None
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = conn if conn is not None else get_connection(is_read_only=False)
        con.execute(sql, range_params)

        verify_sql = f"""
            SELECT COUNT(*)
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            WHERE db.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                AND db.total_mv IS NOT NULL
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                {code_filter}
                {exchange_filter}
        """
        updated_result = con.execute(verify_sql, range_params).fetchone()
        updated_rows = updated_result[0] if updated_result else 0
        logger.info(f"  成功更新 {updated_rows} 行市值数据(total_mv, float_mv)")

    except Exception as e:
        logger.error(f"更新市值数据失败: {e}")
    finally:
        if need_close and con is not None:
            con.close()
