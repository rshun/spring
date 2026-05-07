import logging
import duckdb
import pandas as pd
from . import myutil
from datetime import datetime, date
from typing import List, Tuple,Optional

logger = logging.getLogger("etl.util.dbutil")

"""
检查指定日期是否为交易日
  参数:
    date_str: 输入格式 YYYY-MM-DD
  返回:
    True (交易日), False (非交易日或无记录)
"""
def check_is_trading_day(date_str: str) -> bool:

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = get_connection()
        result = conn.execute(
            "SELECT IS_OPEN FROM TRADE_CAL WHERE CAL_DATE = ?", 
            [date_str]
            ).fetchone()

        if result is None:
            logger.info(f" 警告: 日历表中不存在日期 {date_str} 的记录。")
            return False

        return True if result[0] == 1 else False

    except Exception as e:
        logger.info(f" 检查日历表失败: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()

"""
根据命令行参数，从数据库 stock_info 表中获取符合条件的股票代码列表
  参数:
    begindate: 开始日期
    enddate: 结束日期
    exchanges_arg: 交易所列表
    codes_arg: 股票代码列表
  返回:
    符合条件的股票代码列表
"""
def get_candidate_codes(
        begindate: str,
        enddate: str,
        exchanges_arg: list[str],
        codes_arg: list[str],
        is_delist: bool = False
    ) -> list[tuple]:

    sql = "SELECT SYMBOL,EXCHANGE,LIST_DATE,DELIST_DATE,LIST_STATUS FROM STOCK_INFO WHERE BOARD <> 'INDEX'"
    return get_candidate_data(begindate, enddate, exchanges_arg, codes_arg, is_delist,sql)

"""
根据命令行参数，从数据库 stock_info 表中获取符合条件的指数代码列表
  参数:
    begindate: 开始日期
    enddate: 结束日期
    exchanges_arg: 交易所列表
    index_arg: 指数代码列表
  返回:
    符合条件的指数代码列表
"""
def get_candidate_index(
        begindate: str,
        enddate: str,
        exchanges_arg: list[str],
        index_arg: list[str],
        is_delist: bool = False
    ) -> list[tuple]:
    
    sql = "SELECT SYMBOL,EXCHANGE,LIST_DATE,DELIST_DATE,LIST_STATUS FROM STOCK_INFO WHERE BOARD = 'INDEX'"
    return get_candidate_data(begindate, enddate, exchanges_arg, index_arg, is_delist,sql)

"""
获取指定的股票或指数
  代码优先级最高,如果未传代码,则按交易所过滤, 如果未传交易所,则全市场
  开始日期如果小于上市日期,则以上市日期为准

  参数:
    begindate: 开始日期
    enddate: 结束日期
    exchanges_arg: 交易所列表
    codes_arg: 股票代码列表
    is_delist: 是否包含已退市股票
    sql: 初始查询语句
  返回:
    符合条件的股票代码列表
"""
def get_candidate_data(
        begindate: Optional[str],
        enddate: Optional[str],
        exchanges_arg: list[str],
        codes_arg: list[str],
        is_delist: bool,
        sql: str
    ) -> list[tuple]:

    params = []

    # 如果指定了具体股票代码，直接忽略交易所参数
    if codes_arg:
        real_codes = []
        for item in codes_arg:
            # 兼容中文逗号和英文逗号，并去除空格
            clean_item = item.replace('，', ',')
            real_codes.extend([x.strip() for x in clean_item.split(',') if x.strip()])

        if real_codes:
            placeholders = ','.join(['?'] * len(real_codes))
            sql += f" AND SYMBOL IN ({placeholders})"
            params.extend(real_codes)
    
    # 只有在没传股票代码时，才判断交易所
    elif exchanges_arg and 'all' not in exchanges_arg:
        # 将命令行输入的 sh/sz/bj 转换为数据库存储的大写 SH/SZ/BJ
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
        out: List[Tuple[str, str, str, str,str]] = []

        # 获取当天日期 (用于逻辑3的比较)
        today_date = date.today()

        # 预处理传入的日期字符串为 date 对象，方便后续比较
        req_begin_date = datetime.strptime(begindate, "%Y-%m-%d").date() if begindate else None
        req_end_date = datetime.strptime(enddate, "%Y-%m-%d").date() if enddate else None

        for symbol, exchange, list_date, delist_date,list_status in rows:
            # 数据库中的 list_date 可能是 None，虽然 schema 没强制，但防守式编程
            if not list_date:
                continue 

            eff_begin = None
            eff_end = None

            # 上市大于查询日期，则取上市日期,没有传起始日期，则取上市日期
            if req_begin_date:
                eff_begin = list_date if list_date > req_begin_date else req_begin_date
            else:
                eff_begin = list_date
            
            # 截止日期, 如果有传，则判断退市状态，传入日期和退市日期(当天日期)取最小,如果没传，判断退市状态，取退市日期(当天日期)
            cap_date = today_date if list_status == "L" else delist_date
            eff_end = cap_date if req_end_date is None else min(req_end_date, cap_date)

            # 只有当开始日期 <= 结束日期，且不退市或者要求包含已退市股票
            if eff_begin <= eff_end and (is_delist == True or list_status == "L"):
                out.append((
                    str(symbol), 
                    str(exchange), 
                    eff_begin.strftime("%Y-%m-%d"), 
                    eff_end.strftime("%Y-%m-%d"),
                    str(list_status)
                ))

        return out
    except FileNotFoundError:
        logger.info(f"数据库文件不存在，请先运行init_db.py初始化数据库")
        return []
    except Exception as e:
        logger.info(f"获取数据库连接失败: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()

"""
获取数据库连接
  参数: 
    is_read_only: 是否只读
  返回: 
    数据库连接
"""
def get_connection(is_read_only: bool = True) -> duckdb.DuckDBPyConnection:

    db_path = myutil.get_default_dbfile()
    if is_read_only and not db_path.exists():
        raise FileNotFoundError(f"数据库文件不存在，无法以只读模式连接: {db_path}")

    return duckdb.connect(str(db_path), read_only=is_read_only)

"""
写入每日指标数据 daily_basic 表
  参数:
    df: DataFrame
    conn: duckdb连接
  返回:
    None
"""
def save_base_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    
    try:
        conn.register("temp_daily_basic", df)

        # 使用 ON CONFLICT DO UPDATE 避免覆盖其他来源(如 sync_basic)写入的字段(如 float_mv, total_mv)
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
            SET turnover_rate = EXCLUDED.turnover_rate,
                pe            = EXCLUDED.pe,
                pb            = EXCLUDED.pb,
                is_st         = EXCLUDED.is_st
        """)

        logger.info(f"[入库] 成功合并 {len(df)} 条每日指标数据")
    except Exception as e:
        logger.info(f"[错误] 写入 DAILY_BASIC 表失败: {e}")

def save_shares_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """
    将股本数据(total_shares, float_shares)写入 DAILY_BASIC 表
    df 需包含列: code, date, total_shares, float_shares
    使用 ON CONFLICT 仅更新股本字段，不覆盖其他已有数据
    """
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
        logger.info(f"[错误] 写入股本数据到 DAILY_BASIC 失败: {e}")

"""
写入股票行情明细数据 stock_daily 表
  参数:
    df: DataFrame
    conn: duckdb连接
  返回:
    None
"""
def save_daily_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    
    logger.info(f"正在将 {len(df)} 条行情明细写入数据库...")
    try:
        # 处理字段缺失情况，默认填 -1
        if "pre_close" not in df.columns:
            df["pre_close"] = -1
        else:
            df["pre_close"] = df["pre_close"].fillna(-1)
        
        # 兼容 tradestatus / trade_status
        if "tradestatus" not in df.columns:
            if "trade_status" in df.columns:
                df["tradestatus"] = df["trade_status"]
            else:
                df["tradestatus"] = -1
        
        df["tradestatus"] = df["tradestatus"].fillna(-1)

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
        logger.info(f"数据库写入失败: {e}")

"""
将交易日数据写入 trade_cal 表
  参数:
    df: DataFrame
    conn: duckdb连接
  返回:
    None
"""
def save_calendar_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection):

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

        count = conn.execute("SELECT COUNT(*) FROM trade_cal").fetchone()[0]
        logger.info(f"入库成功！当前 TRADE_CAL 表总记录数: {count}")
    except Exception as e:
        logger.info(f"[数据库错误] 写入 TRADE_CAL 执行失败: {e}")
        raise
    finally:
        try:
            conn.unregister(temp_name)
        except Exception:
            pass

"""
将指数行情明细数据 stock_daily 表
  参数:
    df: DataFrame
    conn: duckdb连接
  返回:
    None
"""
def save_index_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:

    logger.info(f"正在将 {len(df)} 条指数明细写入数据库...")
    try:
        # 处理字段缺失情况，默认填 -1
        if "pre_close" not in df.columns:
            df["pre_close"] = -1
        else:
            df["pre_close"] = df["pre_close"].fillna(-1)

        # 兼容 tradestatus / trade_status
        if "tradestatus" not in df.columns:
            if "trade_status" in df.columns:
                df["tradestatus"] = df["trade_status"]
            else:
                df["tradestatus"] = -1

        df["tradestatus"] = df["tradestatus"].fillna(-1)

        # 将空字符串替换为 NaN，并强制转换数值列，记录有问题的代码
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

"""
将股票基本信息 DataFrame 批量 UPSERT 到 stock_info 表中
  参数:
    df: DataFrame
    conn: duckdb连接
  返回:
    None
"""
def load_stock_info_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:

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
        logger.info(f"  [*] 验证: 'stock_info' 表现在共有 {count_result[0]} 条记录。")

    except duckdb.CatalogException as e:
        logger.info(f"  数据库写入失败: {e}")
        logger.info("  错误提示：很可能是 'stock_info' 表不存在。")
        logger.info("  请确认 'init_db.py' 已经成功运行。")
    except Exception as e:
        logger.info(f"  数据库写入失败: {e}")
    finally:
        try:
            conn.unregister("temp_stock_info")
        except Exception:
            pass

"""
获取缺失行情数据的股票列表
  参数
    start_date: 起始日期
    end_date  : 截止日期
  返回:
    list[tuple]: 缺失行情数据的股票列表
"""
def find_missing_stock_daily(start_date: str,end_date: str)-> list[tuple]:

    sql = """
    WITH trading_days AS (
        SELECT cal_date, ROW_NUMBER() OVER(ORDER BY cal_date) as t_rank
        FROM TRADE_CAL
        WHERE is_open = 1 AND cal_date BETWEEN ? AND ?
    ),
    active_stocks AS (
        SELECT symbol, code, exchange, list_status, list_date, delist_date
        FROM STOCK_INFO WHERE board != 'INDEX'
    ),
    expected AS (
        SELECT s.symbol, s.code, s.exchange, t.cal_date, t.t_rank, s.list_status
        FROM active_stocks s
        CROSS JOIN trading_days t
        WHERE t.cal_date >= s.list_date
          AND (s.delist_date IS NULL OR t.cal_date <= s.delist_date)
    ),
    missing_raw AS (
        SELECT e.*
        FROM expected e
        LEFT JOIN STOCK_DAILY d ON e.code = d.code AND e.cal_date = d.date
        WHERE d.code IS NULL
    ),
    gap_groups AS (
        SELECT *,
               t_rank - ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY cal_date) as grp
        FROM missing_raw
    )
    SELECT 
        symbol, 
        LOWER(exchange), 
        CAST(MIN(cal_date) AS VARCHAR), 
        CAST(MAX(cal_date) AS VARCHAR), 
        list_status
    FROM gap_groups
    GROUP BY symbol, exchange, list_status, grp
    ORDER BY symbol, MIN(cal_date);
    """

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        conn = get_connection()
        rows = conn.execute(sql, [start_date, end_date]).fetchall()
        return rows
    except FileNotFoundError:
        logger.info(f"数据库文件不存在, 请先运行init_db.py初始化数据库")
        return []
    except Exception as e:
        logger.info(f"获取数据库连接失败: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()

"""
补齐京市停牌无数据
  参数
    start_date: 起始日期
    end_date  : 截止日期
    codes     : 股票代码列表
  返回:
    None
"""
def fill_missing_bj_data(start_date: str, end_date: str, codes: list[str]):

    code_filter_sql = ""
    if codes:
        formatted_codes = ", ".join(f"'{c}'" for c in set(codes))
        code_filter_sql = f"AND s.code IN ({formatted_codes})"

    sql = f"""
        INSERT INTO STOCK_DAILY (
            code, date, open, high, low, close, pre_close, 
            tradestatus, volume, amount
        )
        WITH params AS (
            SELECT 
                CAST('{start_date}' AS DATE) as s_date,
                CAST('{end_date}' AS DATE) as e_date
        ),
        target_stocks AS (
            SELECT code, list_date, delist_date
            FROM STOCK_INFO s
            WHERE exchange = 'BJ' 
            {code_filter_sql}  -- 动态插入代码过滤条件
        ),
        valid_cal AS (
            SELECT cal_date
            FROM TRADE_CAL, params
            WHERE is_open = 1 
            AND cal_date BETWEEN params.s_date AND params.e_date
        ),
        expected_spine AS (
            SELECT 
                ts.code, 
                vc.cal_date as date
            FROM target_stocks ts
            CROSS JOIN valid_cal vc
            WHERE vc.cal_date >= ts.list_date 
            AND (ts.delist_date IS NULL OR vc.cal_date <= ts.delist_date)
        ),
        missing_gaps AS (
            SELECT 
                es.code, 
                es.date
            FROM expected_spine es
            ANTI JOIN STOCK_DAILY sd 
                ON es.code = sd.code AND es.date = sd.date
        ),
        filled_values AS (
            SELECT
                m.code,
                m.date,
                -- ASOF JOIN 抓取停牌日期的前一条记录的收盘价
                sd.close as prev_close
            FROM missing_gaps m
            ASOF LEFT JOIN STOCK_DAILY sd
                ON m.code = sd.code 
                AND m.date > sd.date
        )
        SELECT 
            code,
            date,
            prev_close as open,        -- 停牌填前收盘
            prev_close as high,        -- 停牌填前收盘
            prev_close as low,         -- 停牌填前收盘
            prev_close as close,       -- 停牌填前收盘
            prev_close as pre_close,   -- 停牌填前收盘
            0 as tradestatus,          -- 停牌状态为 0
            0 as volume,               -- 成交量 0
            0.0 as amount              -- 成交额 0
        FROM filled_values
        WHERE prev_close IS NOT NULL;  -- 剔除找不到前值的情况(如上市首日即停牌)
    """

    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = get_connection(is_read_only=False)
        con.execute(sql)
        logger.info("执行成功: 停牌数据已补齐。")
    except Exception as e:
        logger.info(f"执行失败: {e}")
    finally:
        if con:
            con.close()


"""
计算并更新指定日期区间内的涨跌停价
    :param start_date: 开始日期 'YYYY-MM-DD'
    :param end_date: 结束日期 'YYYY-MM-DD'
"""
def update_price_limits_by_range(start_date, end_date, markets=['ALL']):
    if not markets:
        markets = ['ALL']

    con: duckdb.DuckDBPyConnection | None = None

    try:
        con = get_connection(is_read_only=False)
        
        logger.info(f"开始批量计算涨跌停价，时间区间: {start_date} 至 {end_date}")

        # 构建市场过滤条件
        market_filter = ""
        if 'ALL' not in markets:
            formatted_markets = ", ".join([f"'{m}'" for m in markets])
            market_filter = f"AND i.exchange IN ({formatted_markets})"

        # 核心计算逻辑：一次性计算所有日期的涨跌停价
        # 注意：这里直接将 Python 中的 rounding 逻辑移植到了 SQL
        # BJ: Floor/Ceil logic
        # Others: Round logic
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
                WHERE d.date BETWEEN '{start_date}' AND '{end_date}'
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
                        WHEN board = 'GEM' AND date < '2020-08-24' AND days_count = 1 THEN 0.36
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
                        -- 无限制
                        WHEN up_rate = 0 THEN 999999.99
                        -- 北交所逻辑: 向下取整 (FLOOR)
                        WHEN board = 'BJ' THEN FLOOR(pre_close * (1 + up_rate) * 100 + 0.0001) / 100.0
                        -- 沪深逻辑: 四舍五入 (ROUND)
                        ELSE ROUND(pre_close * (1 + up_rate) + 0.000001, 2)
                    END as limit_up,
                    CASE 
                        -- 无限制
                        WHEN up_rate = 0 THEN 0.01 -- 这里的0.01是占位，实际无跌幅限制通常意味着跌到底，但业务上通常给个极小值或不做限制
                        -- 修正逻辑：down_rate 优先使用专用的，如果为 NULL 则使用 up_rate (对称)
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
            -- 批量更新
            UPDATE DAILY_BASIC 
            SET limit_up = t.limit_up, 
                limit_down = t.limit_down,
                is_limit_up = t.is_limit_up,
                is_limit_down = t.is_limit_down
            FROM final_marks t
            WHERE DAILY_BASIC.code = t.code 
              AND DAILY_BASIC.trade_date = t.date;
        """

        logger.info("正在执行批量更新 SQL (这可能需要几秒钟)...")
        con.execute(calc_sql)
        
        # 获取受影响行数 (虽然 DuckDB UPDATE 不直接返回行数，但我们可以查下匹配数)
        # 这里为了性能，只打印完成
        logger.info("批量更新完成。")

    except Exception as e:
        logger.info(f"执行失败: {e}")
    finally:
        if con:
            con.close()

"""
补齐量比数据
"""
def fill_daily_basic_volume_ratio(start_date: str, end_date: str,
                                  codes: list[str],
                                  conn: duckdb.DuckDBPyConnection | None = None) -> None:

    code_filter = ""
    update_code_filter = ""

    if codes:
        # 如果传入的是单个字符串，转为列表
        if isinstance(codes, str):
            codes = [codes]
        
        # 给每个代码加上单引号，并用逗号连接 ['300085.SZ', '600519.SH']  -->  "'300085.SZ', '600519.SH'"
        formatted_codes = ", ".join([f"'{c}'" for c in codes])
        
        # 使用 IN 语法
        code_filter = f"AND d.code IN ({formatted_codes})"
        update_code_filter = f"AND DAILY_BASIC.code IN ({formatted_codes})"

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
                -- 计算前5个交易日均量 (不含当日)
                AVG(volume) OVER (
                    PARTITION BY code 
                    ORDER BY date ASC 
                    ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING
                ) as ma5_volume
            FROM valid_daily_data
        ),
        
        ratio_result AS (
            -- 计算量比并过滤出目标区间
            SELECT 
                code,
                date,
                CASE 
                    WHEN ma5_volume IS NULL OR ma5_volume = 0 THEN NULL 
                    ELSE ROUND(volume / ma5_volume, 2)
                END as v_ratio
            FROM calc_ma
            WHERE date BETWEEN CAST('{start_date}' AS DATE) AND CAST('{end_date}' AS DATE)
        )

        UPDATE DAILY_BASIC
        SET volume_ratio = src.v_ratio
        FROM ratio_result src
        WHERE DAILY_BASIC.code = src.code
        AND DAILY_BASIC.trade_date = src.date
        AND DAILY_BASIC.trade_date BETWEEN CAST('{start_date}' AS DATE) AND CAST('{end_date}' AS DATE)
        {update_code_filter};
        """
    
    need_close = conn is None
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = conn if conn is not None else get_connection(is_read_only=False)
        con.execute(sql)
        logger.info("更新成功")
    except Exception as e:
        logger.error(f"量比更新失败: {e}")
    finally:
        if need_close and con is not None:
            con.close()

"""
将融资融券明细数据写入 MARGIN_DATA 表
  参数:
    df  : DataFrame，含 code/trade_date/name/exchange/margin_buy/margin_balance/
          margin_repay/short_sell_vol/short_balance_vol/short_repay_vol/
          short_balance_amt/total_balance
    conn: duckdb 连接
"""
def save_margin_data_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:

    logger.info(f"正在将 {len(df)} 条融资融券数据写入数据库...")
    try:
        conn.register("temp_margin_data", df)
        conn.execute("""
            INSERT OR REPLACE INTO MARGIN_DATA
                (code, trade_date, name, exchange,
                 margin_buy, margin_balance, margin_repay,
                 short_sell_vol, short_balance_vol, short_repay_vol,
                 short_balance_amt, total_balance, updated_at)
            SELECT
                code,
                CAST(trade_date AS DATE),
                name,
                exchange,
                CAST(margin_buy        AS BIGINT),
                CAST(margin_balance    AS BIGINT),
                CAST(margin_repay      AS BIGINT),
                CAST(short_sell_vol    AS BIGINT),
                CAST(short_balance_vol AS BIGINT),
                CAST(short_repay_vol   AS BIGINT),
                CAST(short_balance_amt AS BIGINT),
                CAST(total_balance     AS BIGINT),
                now()
            FROM temp_margin_data
        """)
        logger.info(f"[入库] 成功写入 {len(df)} 条融资融券数据")
    except Exception as e:
        logger.info(f"[错误] 写入 MARGIN_DATA 表失败: {e}")
    finally:
        try:
            conn.unregister("temp_margin_data")
        except Exception:
            pass


"""
获取最新交易日
"""
def get_newest_trade_date() -> str:
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = get_connection(is_read_only=True)
        # 获取当前日期
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 查询 <= 今天的最大交易日 (确保只查开盘日)
        sql = """
        SELECT MAX(cal_date) 
        FROM TRADE_CAL 
        WHERE is_open = 1 AND cal_date <= CAST(? AS DATE)
        """
        result = con.execute(sql, [today]).fetchone()
        
        return result[0].strftime('%Y-%m-%d') if result and result[0] else None
    except Exception as e:
        logger.info(f"获取最新交易日失败: {e}")
        return None
    finally:
        if con:
            con.close()

"""
DEPRECATED: 旧版 SW_INDUSTRY 写入函数。
  旧表结构使用 sw_code/sw_name，已被新版
  SW_INDUSTRY(sw_version, industry_code, industry_name, ...) 替代。
  新流程请使用 save_sw_industry_hierarchy_to_db()。
UPSERT 申万行业定义到 sw_industry 表
  参数:
    df: DataFrame，含 sw_code / sw_name / sw_level / parent_code
    conn: duckdb连接
"""
def save_sw_industry_to_db(df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:

    logger.info(f"正在将 {len(df)} 条申万行业定义写入数据库...")
    try:
        conn.register("temp_sw_industry", df)
        conn.execute("""
            INSERT INTO sw_industry (sw_code, sw_name, sw_level, parent_code, updated_at)
            SELECT sw_code, sw_name, sw_level, parent_code, now()
            FROM temp_sw_industry
            ON CONFLICT (sw_code) DO UPDATE SET
                sw_name     = excluded.sw_name,
                sw_level    = excluded.sw_level,
                parent_code = excluded.parent_code,
                updated_at  = now()
            WHERE
                sw_industry.sw_name     IS DISTINCT FROM excluded.sw_name OR
                sw_industry.parent_code IS DISTINCT FROM excluded.parent_code
        """)
        count = conn.execute("SELECT COUNT(*) FROM sw_industry").fetchone()[0]
        logger.info(f"申万行业定义写入成功，当前共 {count} 条记录。")
    except Exception as e:
        logger.info(f"写入 sw_industry 失败: {e}")
    finally:
        try:
            conn.unregister("temp_sw_industry")
        except Exception:
            pass

"""
DEPRECATED: 旧版股票-申万行业映射写入函数。
  旧流程写入 STOCK_SW_INDUSTRY，已被
  STOCK_INDUSTRY_CLF_HIST_SW_RAW + STOCK_SW_INDUSTRY_VIEW 替代。
  新流程请使用 save_stock_industry_clf_hist_sw_raw_to_db()。
比对现有记录，将行业发生变更的股票插入 stock_sw_industry 表
  参数:
    new_df : 本次从 akshare 拉取的全量映射，含
             code / sw_l1_code / sw_l1_name / sw_l2_code / sw_l2_name /
             sw_l3_code / sw_l3_name / entry_date
    conn   : duckdb连接
    today  : 生效日期字符串 YYYY-MM-DD
"""
def save_stock_sw_industry_to_db(
    new_df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    today: str
) -> None:

    logger.info(f"正在对比申万行业映射，检测变更（基准日期: {today}）...")
    try:
        # 查询数据库中每只股票最新一条行业记录
        latest = conn.execute("""
            SELECT s.code, s.sw_l1_code, s.sw_l2_code, s.sw_l3_code
            FROM stock_sw_industry s
            INNER JOIN (
                SELECT code, MAX(effective_date) AS max_date
                FROM stock_sw_industry
                GROUP BY code
            ) t ON s.code = t.code AND s.effective_date = t.max_date
        """).df()

        if latest.empty:
            # 首次入库：全量插入
            changed = new_df.copy()
            logger.info(f"首次入库，全量写入 {len(changed)} 条记录。")
        else:
            # 合并新旧数据，只保留 l1_code 发生变化或新增的股票
            merged = new_df.merge(latest, on="code", how="left", suffixes=("_new", "_old"))
            mask = (
                merged["sw_l1_code_old"].isna() |
                (merged["sw_l1_code_new"] != merged["sw_l1_code_old"])
            )
            changed = new_df[mask.values].copy()
            logger.info(f"检测到 {len(changed)} 条行业变更（含新增股票）。")

        if changed.empty:
            logger.info("无行业变更，跳过写入。")
            return

        changed["effective_date"] = today
        conn.register("temp_stock_sw", changed)
        conn.execute("""
            INSERT OR REPLACE INTO stock_sw_industry
                (code, effective_date, sw_l1_code, sw_l1_name,
                 sw_l2_code, sw_l2_name, sw_l3_code, sw_l3_name,
                 entry_date, updated_at)
            SELECT
                code,
                CAST(effective_date AS DATE),
                sw_l1_code, sw_l1_name,
                sw_l2_code, sw_l2_name,
                sw_l3_code, sw_l3_name,
                CAST(entry_date AS DATE),
                now()
            FROM temp_stock_sw
        """)
        logger.info(f"[入库] 成功写入 {len(changed)} 条申万行业映射记录。")
    except Exception as e:
        logger.error(f"写入 stock_sw_industry 失败: {e}")
    finally:
        try:
            conn.unregister("temp_stock_sw")
        except (duckdb.Error, RuntimeError) as e:
            logger.warning(f"清理 temp_stock_sw 注册表失败: {e}")


"""
UPSERT 股票申万行业历史原始数据到 STOCK_INDUSTRY_CLF_HIST_SW_RAW 表
  参数:
    df: DataFrame，含 symbol / start_date / industry_code / update_time
    conn: duckdb连接
"""
def save_stock_industry_clf_hist_sw_raw_to_db(
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection
) -> None:

    logger.info(f"正在将 {len(df)} 条股票申万行业历史原始数据写入数据库...")
    if df is None or df.empty:
        logger.info("无股票申万行业历史原始数据，跳过写入。")
        return

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
        count = conn.execute("SELECT COUNT(*) FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW").fetchone()[0]
        logger.info(f"股票申万行业历史原始数据写入成功，当前共 {count} 条记录。")
    except Exception as e:
        logger.error(f"写入 STOCK_INDUSTRY_CLF_HIST_SW_RAW 失败: {e}")
    finally:
        try:
            conn.unregister("temp_stock_industry_clf_hist_sw_raw")
        except (duckdb.Error, RuntimeError) as e:
            logger.warning(f"清理 temp_stock_industry_clf_hist_sw_raw 注册表失败: {e}")


"""
UPSERT 申万行业层级定义到 SW_INDUSTRY 表
  参数:
    df: DataFrame，含 sw_version / industry_code / industry_name / sw_level / parent_code
    conn: duckdb连接
"""
def save_sw_industry_hierarchy_to_db(
    df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection
) -> None:

    logger.info(f"正在将 {len(df)} 条申万行业层级定义写入数据库...")
    if df is None or df.empty:
        logger.info("无申万行业层级定义，跳过写入。")
        return

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
        count = conn.execute("SELECT COUNT(*) FROM SW_INDUSTRY").fetchone()[0]
        logger.info(f"申万行业层级定义写入成功，当前共 {count} 条记录。")
    except Exception as e:
        logger.error(f"写入 SW_INDUSTRY 失败: {e}")
    finally:
        try:
            conn.unregister("temp_sw_industry_hierarchy")
        except (duckdb.Error, RuntimeError) as e:
            logger.warning(f"清理 temp_sw_industry_hierarchy 注册表失败: {e}")


"""
根据 CAPITAL_DETAIL 表的股本变化记录，回填 DAILY_BASIC 表的 total_shares 和 float_shares
  逻辑:
    1. 对每只股票的每个交易日，找 CAPITAL_DETAIL 中该日期当天或之前最近一条'股本变化'记录
    2. 取 allotment_share(后总股本,万股) × 10000 => total_shares(股)
    3. 取 bonus_share(后流通盘,万股) × 10000 => float_shares(股)
    4. 对于交易日早于第一条股本变化记录的情况，使用第一条记录的"前"值回填
  参数:
    start_date: 开始日期 YYYY-MM-DD
    end_date:   结束日期 YYYY-MM-DD
    codes:      股票代码列表(可选)，格式如 ['600519.SH', '000001.SZ']
    exchanges:  交易所列表(可选)，格式如 ['SH', 'SZ']
"""
def fill_daily_basic_shares(start_date: str, end_date: str,
                            codes: list[str] | None = None,
                            exchanges: list[str] | None = None,
                            conn: duckdb.DuckDBPyConnection | None = None) -> None:

    code_filter = ""
    exchange_filter = ""

    if codes:
        if isinstance(codes, str):
            codes = [codes]
        formatted_codes = ", ".join([f"'{c}'" for c in codes])
        code_filter = f"AND db.code IN ({formatted_codes})"

    if exchanges:
        formatted_ex = ", ".join([f"'{x}'" for x in exchanges])
        exchange_filter = f"AND i.exchange IN ({formatted_ex})"

    sql = f"""
        WITH capital_events AS (
            -- 实际股本变化事件：取"后"值
            -- CAPITAL_DETAIL.code 为纯 symbol(如 600519)，通过 STOCK_INFO 转为完整 code(如 600519.SH)
            SELECT i.code, cd.date,
                   cd.bonus_share     AS float_shares_wan,
                   cd.allotment_share AS total_shares_wan
            FROM CAPITAL_DETAIL cd
            JOIN STOCK_INFO i ON cd.code = i.symbol
            WHERE cd.category = '股本变化'

            UNION ALL

            -- 虚拟事件：每只股票第一条记录的"前"值，日期设为极早
            -- 用于回填第一次股本变化之前的交易日
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
            WHERE db.trade_date BETWEEN CAST('{start_date}' AS DATE)
                                    AND CAST('{end_date}'   AS DATE)
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

    need_close = conn is None
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = conn if conn is not None else get_connection(is_read_only=False)

        # 先统计待更新行数
        count_sql = f"""
            SELECT COUNT(*)
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            WHERE db.trade_date BETWEEN CAST('{start_date}' AS DATE)
                                    AND CAST('{end_date}'   AS DATE)
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                {'AND db.code IN (' + ", ".join([f"'{c}'" for c in codes]) + ')' if codes else ''}
                {exchange_filter.replace('i.exchange', 'i.exchange') if exchanges else ''}
        """
        total_rows = con.execute(count_sql).fetchone()[0]

        # 统计 CAPITAL_DETAIL 中有数据的股票数
        cd_count_sql = "SELECT COUNT(DISTINCT code) FROM CAPITAL_DETAIL WHERE category = '股本变化'"
        cd_stocks = con.execute(cd_count_sql).fetchone()[0]
        logger.info(f"  CAPITAL_DETAIL 中共 {cd_stocks} 只股票有股本变化记录")
        logger.info(f"  DAILY_BASIC 目标区间共 {total_rows} 行待处理")

        con.execute(sql)

        # 统计实际更新行数(非NULL)
        verify_sql = f"""
            SELECT COUNT(*)
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            WHERE db.trade_date BETWEEN CAST('{start_date}' AS DATE)
                                    AND CAST('{end_date}'   AS DATE)
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                AND db.total_shares IS NOT NULL
                {'AND db.code IN (' + ", ".join([f"'{c}'" for c in codes]) + ')' if codes else ''}
                {exchange_filter.replace('i.exchange', 'i.exchange') if exchanges else ''}
        """
        updated_rows = con.execute(verify_sql).fetchone()[0]
        skipped = total_rows - updated_rows
        logger.info(f"  成功更新 {updated_rows} 行, 跳过 {skipped} 行(无股本变化数据)")

    except Exception as e:
        logger.error(f"更新股本数据失败: {e}")
    finally:
        if need_close and con is not None:
            con.close()


"""
根据 DAILY_BASIC 的 total_shares/float_shares 和 STOCK_DAILY 的 close 回填市值
  total_mv = total_shares × close
  float_mv = float_shares × close
  前置条件: total_shares 和 float_shares 已回填
  参数:
    start_date: 开始日期 YYYY-MM-DD
    end_date:   结束日期 YYYY-MM-DD
    codes:      股票代码列表(可选)
    exchanges:  交易所列表(可选)
"""
def fill_daily_basic_mv(start_date: str, end_date: str,
                        codes: list[str] | None = None,
                        exchanges: list[str] | None = None,
                        conn: duckdb.DuckDBPyConnection | None = None) -> None:

    code_filter = ""
    exchange_filter = ""

    if codes:
        if isinstance(codes, str):
            codes = [codes]
        formatted_codes = ", ".join([f"'{c}'" for c in codes])
        code_filter = f"AND db.code IN ({formatted_codes})"

    if exchanges:
        formatted_ex = ", ".join([f"'{x}'" for x in exchanges])
        exchange_filter = f"AND i.exchange IN ({formatted_ex})"

    sql = f"""
        UPDATE DAILY_BASIC db
        SET total_mv = db.total_shares * sd.close,
            float_mv = db.float_shares * sd.close
        FROM STOCK_DAILY sd
        JOIN STOCK_INFO i ON sd.code = i.code
        WHERE db.code       = sd.code
          AND db.trade_date  = sd.date
          AND db.trade_date BETWEEN CAST('{start_date}' AS DATE)
                                AND CAST('{end_date}'   AS DATE)
          AND db.total_shares IS NOT NULL
          AND i.board IN ('MAIN', 'STAR', 'GEM')
          {code_filter}
          {exchange_filter};
    """

    need_close = conn is None
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = conn if conn is not None else get_connection(is_read_only=False)

        con.execute(sql)

        verify_sql = f"""
            SELECT COUNT(*)
            FROM DAILY_BASIC db
            JOIN STOCK_INFO i ON db.code = i.code
            WHERE db.trade_date BETWEEN CAST('{start_date}' AS DATE)
                                    AND CAST('{end_date}'   AS DATE)
                AND db.total_mv IS NOT NULL
                AND i.board IN ('MAIN', 'STAR', 'GEM')
                {'AND db.code IN (' + ", ".join([f"'{c}'" for c in codes]) + ')' if codes else ''}
                {exchange_filter if exchanges else ''}
        """
        updated_rows = con.execute(verify_sql).fetchone()[0]
        logger.info(f"  成功更新 {updated_rows} 行市值数据(total_mv, float_mv)")

    except Exception as e:
        logger.error(f"更新市值数据失败: {e}")
    finally:
        if need_close and con is not None:
            con.close()
