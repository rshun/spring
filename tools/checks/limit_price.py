# 修改记录:
#   2026-10-02  Claude  新建涨跌停价核对(DAILY_BASIC.limit_up/limit_down/is_limit_*, 由
#                       check_daily 指标空值中拆出涨跌停价空值, 并新增区间与标志一致性)
"""涨跌停价核对(etl.update_limit 的产出)

只看正常交易日(STOCK_DAILY.tradestatus=1)且 DAILY_BASIC 有行的记录; 缺行由核心
检查「指标数据」负责, 这里不重复报。每行只报优先级最高的一种问题:

  1. 涨跌停价缺失: limit_up / limit_down 为 NULL 或 ≤0
     (update_limit 跳过 pre_close=-1 的行, 这类会同时被「日线价量空值」报出)
  2. 收盘价超出涨跌停区间: 收盘价(按分取整)高于涨停价或低于跌停价——真实成交
     不可能越过涨跌停, 出现即说明涨跌停价算错(实测抓到过板块被误标成 MAIN 的
     创业板股, 被按 10% 算了涨跌停价)
  3. 涨跌停标志与收盘价不一致: is_limit_up/is_limit_down 应等于「收盘价是否等于
     涨/跌停价」。不一致多为日线修订后没有重跑 update_limit
     无涨跌幅限制的日子(新股前几日)update_limit 写入哨兵 999999.99 / 0.01,
     此时两个标志都应为 0

范围与 check_daily 其余 DAILY_BASIC 字段核对一致: 排除指数与北交所。
"""
import duckdb

from util import checker

LABEL = "涨跌停价"
CSV_TAG = "limit_price"

ISSUE_ABSENT = "涨跌停价缺失(NULL 或 ≤0)"
ISSUE_OUT_OF_RANGE = "收盘价超出涨跌停区间"
ISSUE_FLAG_MISMATCH = "涨跌停标志与收盘价不一致"

# update_limit 对无涨跌幅限制日写入的涨停价哨兵值
_NO_LIMIT_SENTINEL = 999999

# 「同一个价」的判定容差: A 股价格精度为分, 比 0.01 小一个量级即可吸收浮点尾差
_PRICE_EPS = 0.001


def check_limit_price(conn: duckdb.DuckDBPyConnection,
                      begin: str, end: str,
                      ex_filter: str, code_filter: str,
                      code_params: list) -> checker.CheckResult:
    """涨跌停价核对; 只告警不阻断

    参数:
        begin / end: YYYY-MM-DD, 既是查询区间也用于 CSV 文件名
    """
    result = checker.CheckResult(label=LABEL, blocking=False)
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
    traded AS (
        SELECT d.date AS trade_date, s.code, s.name,
               ROUND(d.close + 0.000001, 2) AS close2,
               b.limit_up, b.limit_down, b.is_limit_up, b.is_limit_down
        FROM STOCK_DAILY d
        INNER JOIN active_stocks s ON d.code = s.code
        INNER JOIN trading_days t ON d.date = t.cal_date
        INNER JOIN DAILY_BASIC b ON b.code = d.code AND b.trade_date = d.date
        WHERE d.tradestatus = 1
          AND d.close > 0
    ),
    judged AS (
        SELECT trade_date, code, name, close2, limit_up, limit_down,
               is_limit_up, is_limit_down,
               CASE
                 WHEN limit_up IS NULL OR limit_up <= 0
                   OR limit_down IS NULL OR limit_down <= 0
                      THEN '{ISSUE_ABSENT}'
                 WHEN close2 > limit_up + {_PRICE_EPS}
                   OR close2 < limit_down - {_PRICE_EPS}
                      THEN '{ISSUE_OUT_OF_RANGE}'
                 WHEN COALESCE(is_limit_up, -1) <> CASE
                        WHEN limit_up >= {_NO_LIMIT_SENTINEL} THEN 0
                        WHEN ABS(close2 - limit_up) < {_PRICE_EPS} THEN 1 ELSE 0 END
                   OR COALESCE(is_limit_down, -1) <> CASE
                        WHEN limit_up >= {_NO_LIMIT_SENTINEL} THEN 0
                        WHEN ABS(close2 - limit_down) < {_PRICE_EPS} THEN 1 ELSE 0 END
                      THEN '{ISSUE_FLAG_MISMATCH}'
               END AS issue
        FROM traded
    )
    SELECT trade_date, code, name, close2, limit_up, limit_down,
           is_limit_up, is_limit_down, issue
    FROM judged
    WHERE issue IS NOT NULL
    ORDER BY trade_date, code
    """
    rows = conn.execute(sql, [begin, end, *code_params]).fetchall()

    result.rows = [
        {"date": str(r[0]), "code": r[1], "name": r[2], "close": r[3],
         "limit_up": r[4], "limit_down": r[5],
         "is_limit_up": r[6], "is_limit_down": r[7], "issue": r[8]}
        for r in rows
    ]
    result.count = len(rows)
    result.status = checker.STATUS_MISMATCH if rows else checker.STATUS_OK

    checker.log_rows(LABEL, [f"{r['date']}  {r['code']}  {r['issue']}" for r in result.rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end,
        ["date", "code", "name", "close", "limit_up", "limit_down",
         "is_limit_up", "is_limit_down", "issue"],
        [(r["date"], r["code"], r["name"], r["close"], r["limit_up"],
          r["limit_down"], r["is_limit_up"], r["is_limit_down"], r["issue"])
         for r in result.rows])
    return result
