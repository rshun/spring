# 修改记录:
#   2026-09-12  Claude  新建停牌一致性核对项(SUSPENSION_DAILY vs STOCK_DAILY.tradestatus)
"""停牌一致性核对

把 SUSPENSION_DAILY(第三方独立事实) 与 STOCK_DAILY.tradestatus 做双向比对。
四种情形中只有「外部停牌 + 库内 tradestatus=0」是一致的，其余三种都要输出。

「外部停牌但库内无该行」必须与「库内标为正常交易」分开：
baostock 对长期停牌股本来就可能不给行，属数据源特性而非 bug。

「外部停牌但库内 tradestatus=-1(暂时没有值)」必须单独报出，不能落不到任何
CASE 分支而被静默当成一致——把「未知」当「一致」正是本项目核对设计要防的
那类 bug。
"""
import duckdb

from util import checker

LABEL = "停牌核对"
CSV_TAG = "suspension"

ISSUE_DB_SAYS_TRADING = "外部停牌但库内标为正常交易(tradestatus=1)"
ISSUE_DB_ROW_ABSENT = "外部停牌但库内无该行"
ISSUE_DB_STATUS_UNKNOWN = "外部停牌但库内 tradestatus 未知(-1)"
ISSUE_DB_ONLY = "库内标停牌但外部名单无此股"


def _rows_for_date(conn: duckdb.DuckDBPyConnection, dt: str,
                   ex_filter: str, code_filter: str,
                   code_params: list) -> list[dict]:
    """单个交易日的四类异常，一次 FULL OUTER JOIN 查出

    DuckDB 的 QUALIFY 要求查询里含窗口函数，本查询没有，所以把带 CASE 的
    SELECT 包成子查询，外层用 WHERE 过滤 issue IS NOT NULL。
    """
    sql = f"""
    WITH universe AS (
        SELECT code, name FROM STOCK_INFO i
        WHERE 1=1 {ex_filter} {code_filter}
    ),
    ext AS (
        SELECT s.code, s.name
        FROM SUSPENSION_DAILY s
        INNER JOIN universe u ON u.code = s.code
        WHERE s.trade_date = ?
    ),
    db AS (
        SELECT d.code, d.tradestatus
        FROM STOCK_DAILY d
        INNER JOIN universe u ON u.code = d.code
        WHERE d.date = ?
    )
    SELECT * FROM (
        SELECT COALESCE(ext.code, db.code) AS code,
               COALESCE(ext.name, u2.name) AS name,
               CASE
                 WHEN ext.code IS NOT NULL AND db.code IS NULL
                      THEN '{ISSUE_DB_ROW_ABSENT}'
                 WHEN ext.code IS NOT NULL AND db.tradestatus = 1
                      THEN '{ISSUE_DB_SAYS_TRADING}'
                 WHEN ext.code IS NOT NULL AND db.code IS NOT NULL
                      AND (db.tradestatus IS NULL OR db.tradestatus NOT IN (0, 1))
                      THEN '{ISSUE_DB_STATUS_UNKNOWN}'
                 WHEN ext.code IS NULL AND db.tradestatus = 0
                      THEN '{ISSUE_DB_ONLY}'
               END AS issue
        FROM ext
        FULL OUTER JOIN db ON db.code = ext.code
        LEFT JOIN universe u2 ON u2.code = COALESCE(ext.code, db.code)
    ) t
    WHERE t.issue IS NOT NULL
    ORDER BY t.code
    """
    params = list(code_params) + [dt, dt]
    rows = conn.execute(sql, params).fetchall()
    return [{"date": dt, "code": r[0], "name": r[1], "issue": r[2]} for r in rows]


def check_suspension(conn: duckdb.DuckDBPyConnection,
                     trade_dates: list[str],
                     begin: str, end: str,
                     ex_filter: str, code_filter: str,
                     code_params: list) -> checker.CheckResult:
    """停牌一致性核对；只告警不阻断

    参数:
        trade_dates: 待核对的交易日(YYYY-MM-DD)
        begin / end: 原始 YYYYMMDD, 仅用于 CSV 文件名
    """
    result = checker.CheckResult(label=LABEL, blocking=False)
    has_dates, missing_dates = checker.split_dates_by_source(
        conn, "SUSPENSION_DAILY", trade_dates)
    result.missing_dates = missing_dates

    rows: list[dict] = []
    for dt in has_dates:
        rows.extend(_rows_for_date(conn, dt, ex_filter, code_filter, code_params))

    result.rows = rows
    result.count = len(rows)
    result.status = checker.resolve_status(has_dates, missing_dates, len(rows))

    if missing_dates:
        checker.logger.warning(
            f"[{LABEL}] SUSPENSION_DAILY 以下日期无数据，未核对: {missing_dates}")
    checker.log_rows(LABEL, [f"{r['date']}  {r['code']}  {r['issue']}" for r in rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end, ["date", "code", "name", "issue"],
        [(r["date"], r["code"], r["name"], r["issue"]) for r in rows])
    return result
