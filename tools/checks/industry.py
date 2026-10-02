# 修改记录:
#   2026-10-02  Claude  新建申万行业覆盖核对(在市股票是否都有行业且能映射到一级行业)
"""申万行业覆盖核对

下游按申万一级行业做分组 / 中性化, 股票没有行业或行业代码对不上层级定义时
会被静默丢出分组, 所以单独报出来。

口径:
  - 范围: STOCK_INFO 在市(list_status='L')、非指数、上市日 <= 检查结束日的股票
  - 异常 1「无申万行业记录」: STOCK_INDUSTRY_CLF_HIST_SW_RAW 中该股一行都没有
  - 异常 2「最新行业无法映射到一级」: 该股 start_date 最新的一条, 经
    STOCK_SW_INDUSTRY_VIEW 展开后 sw_l1_code 为空(SW_INDUSTRY 缺该版本层级定义,
    或行业代码已废止; 实测这类全是已退市股, 在市股为 0)
  - sync_industry 无日期参数、每次拉全量历史, 因此只核对当前状态, 不逐交易日展开
  - 申万分类覆盖北交所, 这里不排除 BJ(与 check_daily 其余核对项默认排除 BJ 不同)
  - 原始表整表为空视为 sync_industry 从未运行: 判 source_missing 并只打一行告警
"""
import duckdb

from util import checker

LABEL = "行业覆盖"
CSV_TAG = "industry_missing"

ISSUE_NO_INDUSTRY = "无申万行业记录"
ISSUE_UNMAPPED_L1 = "最新行业无法映射到申万一级"


def check_industry(conn: duckdb.DuckDBPyConnection,
                   begin: str, end: str,
                   ex_filter: str, code_filter: str,
                   code_params: list) -> checker.CheckResult:
    """申万行业覆盖核对; 只告警不阻断

    参数:
        begin / end: YYYY-MM-DD; end 用于排除尚未上市的股票, 二者都用于 CSV 文件名
    """
    result = checker.CheckResult(label=LABEL, blocking=False)

    raw_cnt = conn.execute(
        "SELECT COUNT(*) FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW").fetchone()[0]
    if raw_cnt == 0:
        result.status = checker.STATUS_SOURCE_MISSING
        checker.logger.warning(
            f"[{LABEL}] STOCK_INDUSTRY_CLF_HIST_SW_RAW 为空(sync_industry 未运行过?)，未核对")
        return result

    sql = f"""
    WITH universe AS (
        SELECT i.code, i.symbol, i.name
        FROM STOCK_INFO i
        WHERE i.board <> 'INDEX'
          AND i.list_status = 'L'
          AND i.list_date <= ?
          {ex_filter}
          {code_filter}
    ),
    latest AS (
        SELECT code, sw_l1_code,
               ROW_NUMBER() OVER (PARTITION BY code ORDER BY start_date DESC) AS rn
        FROM STOCK_SW_INDUSTRY_VIEW
    )
    SELECT u.code, u.name,
           CASE WHEN NOT EXISTS (SELECT 1 FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW r
                                 WHERE r.symbol = u.symbol)
                     THEN '{ISSUE_NO_INDUSTRY}'
                WHEN l.sw_l1_code IS NULL
                     THEN '{ISSUE_UNMAPPED_L1}'
           END AS issue
    FROM universe u
    LEFT JOIN latest l ON l.code = u.code AND l.rn = 1
    WHERE NOT EXISTS (SELECT 1 FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW r
                      WHERE r.symbol = u.symbol)
       OR l.sw_l1_code IS NULL
    ORDER BY u.code
    """
    rows = conn.execute(sql, [end, *code_params]).fetchall()

    result.rows = [{"code": code, "name": name, "issue": issue}
                   for code, name, issue in rows]
    result.count = len(rows)
    result.status = checker.STATUS_MISMATCH if rows else checker.STATUS_OK

    checker.log_rows(LABEL, [f"{r['code']}  {r['issue']}" for r in result.rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end, ["code", "name", "issue"],
        [(r["code"], r["name"], r["issue"]) for r in result.rows])
    return result
