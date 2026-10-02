# 修改记录:
#   2026-10-02  Claude  新建量比空值核对(DAILY_BASIC.volume_ratio, etl.fill_volratio 的产出)
"""量比空值核对(etl.fill_volratio 的产出)

fill_volratio 口径: 量比 = 当日成交量 / 此前最多 5 个正常交易日(tradestatus=1)的均量,
均量为空或为 0 时写 NULL。因此「该股此前没有任何正常交易日」(新股上市首日)
的 NULL 是正常的, 不报; 其余正常交易日为 NULL 即说明 fill_volratio 漏跑或漏算。

口径:
  - 只看正常交易日(tradestatus=1)且 DAILY_BASIC 有行的记录; 缺行由核心检查负责
  - 异常: volume_ratio 为 NULL(且此前有过正常交易日), 或 volume_ratio < 0
  - volume_ratio = 0 不报: 成交极小时 ROUND(·, 2) 会合法地得到 0.00
    (实测 2023–2025 有 34 条)
  - 范围与 check_daily 其余 DAILY_BASIC 字段核对一致: 排除指数与北交所
"""
import duckdb

from util import checker

LABEL = "量比空值"
CSV_TAG = "volume_ratio_nulls"

ISSUE_NULL = "量比为空"
ISSUE_NEGATIVE = "量比为负"


def check_volume_ratio(conn: duckdb.DuckDBPyConnection,
                       begin: str, end: str,
                       ex_filter: str, code_filter: str,
                       code_params: list) -> checker.CheckResult:
    """量比空值核对; 只告警不阻断

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
        SELECT d.date AS trade_date, s.code, s.name, b.volume_ratio
        FROM STOCK_DAILY d
        INNER JOIN active_stocks s ON d.code = s.code
        INNER JOIN trading_days t ON d.date = t.cal_date
        INNER JOIN DAILY_BASIC b ON b.code = d.code AND b.trade_date = d.date
        WHERE d.tradestatus = 1
    )
    SELECT x.trade_date, x.code, x.name,
           CASE WHEN x.volume_ratio IS NULL THEN '{ISSUE_NULL}'
                ELSE '{ISSUE_NEGATIVE}' END AS issue
    FROM traded x
    WHERE x.volume_ratio < 0
       OR (x.volume_ratio IS NULL
           -- 此前没有任何正常交易日(新股首日)时量比本就无法计算, 不报
           AND EXISTS (SELECT 1 FROM STOCK_DAILY p
                       WHERE p.code = x.code AND p.date < x.trade_date
                         AND p.tradestatus = 1))
    ORDER BY x.trade_date, x.code
    """
    rows = conn.execute(sql, [begin, end, *code_params]).fetchall()

    result.rows = [{"date": str(d), "code": code, "name": name, "issue": issue}
                   for d, code, name, issue in rows]
    result.count = len(rows)
    result.status = checker.STATUS_MISMATCH if rows else checker.STATUS_OK

    checker.log_rows(LABEL, [f"{r['date']}  {r['code']}  {r['issue']}" for r in result.rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end, ["date", "code", "name", "issue"],
        [(r["date"], r["code"], r["name"], r["issue"]) for r in result.rows])
    return result
