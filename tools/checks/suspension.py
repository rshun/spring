# 修改记录:
#   2026-09-17  Claude  盘中停牌不再按全天停牌判: 该类当天照常成交, tradestatus=1
#                       才是对的(实测 601091.SH 新股首日临停成交 1.68 亿股被误报);
#                       改为按 suspend_period 分类施加预期, 并对未识别类别单独告警
#   2026-09-12  Claude  新建停牌一致性核对项(SUSPENSION_DAILY vs STOCK_DAILY.tradestatus)
#   2026-09-13  Claude  修正 check_suspension docstring: begin/end 实际约定是
#                       YYYY-MM-DD(调用方 check_daily.run_warn_checks 如此传入)，
#                       不是此前写的 YYYYMMDD
"""停牌一致性核对

把 SUSPENSION_DAILY(第三方独立事实) 与 STOCK_DAILY.tradestatus 做双向比对。
四种情形中只有「外部停牌 + 库内 tradestatus=0」是一致的，其余三种都要输出。

「外部停牌但库内无该行」必须与「库内标为正常交易」分开：
baostock 对长期停牌股本来就可能不给行，属数据源特性而非 bug。

「外部停牌但库内 tradestatus=-1(暂时没有值)」必须单独报出，不能落不到任何
CASE 分支而被静默当成一致——把「未知」当「一致」正是本项目核对设计要防的
那类 bug。

停牌类别决定预期方向，不能一刀切(2026-09-17)：
  - 全天停牌类(连续停牌 / 停牌一天 / 未识别类别)：当天无成交，预期 tradestatus=0
  - 盘中停牌(临停)：当天照常成交，只是盘中暂停几分钟，**预期 tradestatus=1**
实测 601091.SH 沈鼓集团 2026-09-17 新股首日涨幅触发临停，当天成交 1.68 亿股，
库内 tradestatus=1 完全正确，旧逻辑却报成「外部停牌但库内标为正常交易」。
新股首日与 ST 异动都会触发临停，不区分则必然持续误报。

未识别的 suspend_period 一律按全天停牌处理并额外告警：宁可误报也不放过新类别，
理由同上面那条「未知不能当一致」。
"""
import duckdb

from util import checker

LABEL = "停牌核对"
CSV_TAG = "suspension"

ISSUE_DB_SAYS_TRADING = "外部停牌但库内标为正常交易(tradestatus=1)"
ISSUE_DB_ROW_ABSENT = "外部停牌但库内无该行"
ISSUE_DB_STATUS_UNKNOWN = "外部停牌但库内 tradestatus 未知(-1)"
ISSUE_DB_ONLY = "库内标停牌但外部名单无此股"
ISSUE_INTRADAY_NOT_TRADED = "盘中临停但库内标为全天停牌(tradestatus=0)"

# 盘中临停：当天正常成交，只是盘中暂停。预期 tradestatus=1。
_INTRADAY_PERIODS = ("盘中停牌",)
# 全天停牌：预期 tradestatus=0。未识别类别也走这一支(见 docstring)。
_FULLDAY_PERIODS = ("连续停牌", "停牌一天")


def _rows_for_date(conn: duckdb.DuckDBPyConnection, dt: str,
                   ex_filter: str, code_filter: str,
                   code_params: list) -> list[dict]:
    """单个交易日的四类异常，一次 FULL OUTER JOIN 查出

    DuckDB 的 QUALIFY 要求查询里含窗口函数，本查询没有，所以把带 CASE 的
    SELECT 包成子查询，外层用 WHERE 过滤 issue IS NOT NULL。
    """
    intraday_ph = ", ".join(["?"] * len(_INTRADAY_PERIODS))
    sql = f"""
    WITH universe AS (
        SELECT code, name FROM STOCK_INFO i
        WHERE 1=1 {ex_filter} {code_filter}
    ),
    ext AS (
        SELECT s.code, s.name,
               -- 盘中临停当天照常成交, 预期 tradestatus=1; 其余(含未识别类别)
               -- 一律按全天停牌处理, 预期 tradestatus=0
               CASE WHEN s.suspend_period IN ({intraday_ph}) THEN 1 ELSE 0 END
                 AS is_intraday
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
                 WHEN ext.code IS NOT NULL AND db.code IS NOT NULL
                      AND (db.tradestatus IS NULL OR db.tradestatus NOT IN (0, 1))
                      THEN '{ISSUE_DB_STATUS_UNKNOWN}'
                 -- 全天停牌: 预期 tradestatus=0, 标成 1 才是异常
                 WHEN ext.code IS NOT NULL AND ext.is_intraday = 0
                      AND db.tradestatus = 1
                      THEN '{ISSUE_DB_SAYS_TRADING}'
                 -- 盘中临停: 预期 tradestatus=1, 标成 0 才是异常
                 WHEN ext.code IS NOT NULL AND ext.is_intraday = 1
                      AND db.tradestatus = 0
                      THEN '{ISSUE_INTRADAY_NOT_TRADED}'
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
    # 顺序: universe 的 code 过滤 -> ext 的 intraday 类别 -> ext 的 dt -> db 的 dt
    params = list(code_params) + list(_INTRADAY_PERIODS) + [dt, dt]
    rows = conn.execute(sql, params).fetchall()
    return [{"date": dt, "code": r[0], "name": r[1], "issue": r[2]} for r in rows]


def _warn_unknown_periods(conn: duckdb.DuckDBPyConnection,
                          dates: list[str]) -> None:
    """出现未识别的 suspend_period 时告警。

    未识别类别按全天停牌处理(预期 tradestatus=0)。若上游新增的其实是某种临停，
    会持续误报；若是某种全天停牌，判据正好。两种都需要人来判断一次,
    所以必须报出来, 不能静默归类。
    """
    if not dates:
        return
    known = list(_INTRADAY_PERIODS) + list(_FULLDAY_PERIODS)
    ph_d = ", ".join(["?"] * len(dates))
    ph_k = ", ".join(["?"] * len(known))
    rows = conn.execute(
        f"""SELECT DISTINCT suspend_period FROM SUSPENSION_DAILY
            WHERE trade_date IN ({ph_d})
              AND suspend_period IS NOT NULL
              AND suspend_period NOT IN ({ph_k})""",
        list(dates) + known).fetchall()
    if rows:
        checker.logger.warning(
            f"[{LABEL}] 出现未识别的停牌类别 {[r[0] for r in rows]}，"
            f"已按「全天停牌」处理(预期 tradestatus=0)，请确认判据是否适用")


def check_suspension(conn: duckdb.DuckDBPyConnection,
                     trade_dates: list[str],
                     begin: str, end: str,
                     ex_filter: str, code_filter: str,
                     code_params: list) -> checker.CheckResult:
    """停牌一致性核对；只告警不阻断

    参数:
        trade_dates: 待核对的交易日(YYYY-MM-DD)
        begin / end: YYYY-MM-DD, 仅用于 CSV 文件名(调用方 tools/check_daily.py 的
                     run_warn_checks 传入的是 begin_date/end_date，故意与其他核对项
                     一致，以匹配既有 CSV 产物 check_isst_null_2023-11-05_2023-11-05.csv
                     的命名，不是 YYYYMMDD)
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
    _warn_unknown_periods(conn, has_dates)
    checker.log_rows(LABEL, [f"{r['date']}  {r['code']}  {r['issue']}" for r in rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end, ["date", "code", "name", "issue"],
        [(r["date"], r["code"], r["name"], r["issue"]) for r in rows])
    return result
