# 修改记录:
#   2026-10-02  Claude  新建股本资料覆盖核对(CAPITAL_DETAIL 是否覆盖全部在市股票)
"""股本资料覆盖核对

CAPITAL_DETAIL(gbbq) 是 fill_shares 回填股本/市值、adjust 自算复权因子的数据前置。
某只在市股票在表里一行都没有时, fill_shares 会静默跳过它(新股常见, 见 pipeline.yaml),
下游市值 / 换手率随之缺失且不报错, 所以要单独报出来。

口径:
  - 范围: STOCK_INFO 在市(list_status='L')、非指数、上市日 <= 检查结束日的股票
  - 异常: CAPITAL_DETAIL 中按裸代码一行都没有
  - CAPITAL_DETAIL.code 是裸 symbol, 必须经 STOCK_INFO.symbol 关联; 这里从股票侧
    出发关联, 不会把同号指数(如 000001)的行误算进来
  - sync_capital 无日期参数、每次全量刷新, 因此只核对当前状态, 不逐交易日展开
  - gbbq 覆盖北交所, 这里不排除 BJ(与 check_daily 其余核对项默认排除 BJ 不同)
  - 整表为空视为 sync_capital 从未运行: 判 source_missing 并只打一行告警,
    不把全市场 5000+ 只股票逐条报成异常
"""
import duckdb

from util import checker

LABEL = "股本资料覆盖"
CSV_TAG = "capital_missing"

ISSUE_NO_CAPITAL = "CAPITAL_DETAIL 无该股任何记录"


def check_capital(conn: duckdb.DuckDBPyConnection,
                  begin: str, end: str,
                  ex_filter: str, code_filter: str,
                  code_params: list) -> checker.CheckResult:
    """股本资料覆盖核对; 只告警不阻断

    参数:
        begin / end: YYYY-MM-DD; end 用于排除尚未上市的股票, 二者都用于 CSV 文件名
    """
    result = checker.CheckResult(label=LABEL, blocking=False)

    if conn.execute("SELECT COUNT(*) FROM CAPITAL_DETAIL").fetchone()[0] == 0:
        result.status = checker.STATUS_SOURCE_MISSING
        checker.logger.warning(f"[{LABEL}] CAPITAL_DETAIL 为空(sync_capital 未运行过?)，未核对")
        return result

    sql = f"""
    SELECT i.code, i.name
    FROM STOCK_INFO i
    WHERE i.board <> 'INDEX'
      AND i.list_status = 'L'
      AND i.list_date <= ?
      {ex_filter}
      {code_filter}
      AND NOT EXISTS (SELECT 1 FROM CAPITAL_DETAIL c WHERE c.code = i.symbol)
    ORDER BY i.code
    """
    rows = conn.execute(sql, [end, *code_params]).fetchall()

    result.rows = [{"code": code, "name": name, "issue": ISSUE_NO_CAPITAL}
                   for code, name in rows]
    result.count = len(rows)
    result.status = checker.STATUS_MISMATCH if rows else checker.STATUS_OK

    checker.log_rows(LABEL, [f"{r['code']}  {r['issue']}" for r in result.rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end, ["code", "name", "issue"],
        [(r["code"], r["name"], r["issue"]) for r in result.rows])
    return result
