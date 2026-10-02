# 修改记录:
#   2026-10-02  Claude  新建融资融券逐日完整性核对(MARGIN_SUMMARY_DAILY / MARGIN_DETAIL_DAILY)
"""融资融券逐日完整性核对

口径:
  - 按交易日 × 交易所逐格判定: 汇总表当日该所无行 -> 「汇总缺失」;
    明细表当日该所 0 行 -> 「明细缺失」
  - 交易所只核对沪深: sync_margin 的 akshare 源没有北交所接口, BJ 本来就不入库。
    -x 只选了 bj 时没有可核对的交易所, 整项判 source_missing
  - 两融按交易所口径披露, 与具体股票无关, 因此 -c 指定的代码不参与本项过滤
  - 两融数据 T+1 才能取全(深市次交易日才发布, 见 sync_margin 默认取上一交易日),
    检查日当天及以后的交易日不核对, 记入 missing_dates(计为「未核对」), 不当成缺失

与停牌 / 涨跌停核对不同, 这里当日 0 行就是要找的缺口本身(两张表是本项目 ETL
的产物而非外部对照源), 所以记为差异, 不套用 split_dates_by_source 的「无数据即跳过」。
"""
import duckdb

from util import checker, myutil

LABEL = "融资融券"
CSV_TAG = "margin_missing"

# sync_margin 能入库的交易所
MARGIN_EXCHANGES = ("SH", "SZ")

ISSUE_SUMMARY_ABSENT = "汇总缺失"
ISSUE_DETAIL_ABSENT = "明细缺失"


def _target_exchanges(exchanges: list[str] | None) -> list[str]:
    """-x 参数 -> 需要核对的交易所(只保留两融有数据的沪深, 顺序固定)"""
    exs = {e.upper() for e in (exchanges or ["all"])}
    if "ALL" in exs:
        return list(MARGIN_EXCHANGES)
    return [e for e in MARGIN_EXCHANGES if e in exs]


def check_margin(conn: duckdb.DuckDBPyConnection,
                 trade_dates: list[str],
                 begin: str, end: str,
                 exchanges: list[str] | None = None,
                 today: str | None = None) -> checker.CheckResult:
    """融资融券逐日完整性核对; 只告警不阻断

    参数:
        trade_dates: 待核对的交易日(YYYY-MM-DD)
        begin / end: YYYY-MM-DD, 仅用于 CSV 文件名
        exchanges:   -x 参数原样传入(sh/sz/bj/all), None 视为 all
        today:       YYYY-MM-DD, 当天及以后的交易日不核对; None 取系统当天(测试可注入)
    """
    result = checker.CheckResult(label=LABEL, blocking=False)
    today = today or myutil.trans_datestr_format(myutil.get_today())
    target_exs = _target_exchanges(exchanges)

    if not target_exs:
        result.status = checker.STATUS_SOURCE_MISSING
        result.missing_dates = list(trade_dates)
        checker.logger.warning(f"[{LABEL}] 北交所无两融数据源，未核对")
        return result

    checked = [d for d in trade_dates if d < today]
    unchecked = [d for d in trade_dates if d >= today]
    result.missing_dates = unchecked

    rows: list[dict] = []
    for dt in checked:
        summary_exs = {r[0] for r in conn.execute(
            "SELECT DISTINCT exchange_code FROM MARGIN_SUMMARY_DAILY WHERE trade_date = ?",
            [dt]).fetchall()}
        detail_exs = {r[0] for r in conn.execute(
            "SELECT DISTINCT exchange_code FROM MARGIN_DETAIL_DAILY WHERE trade_date = ?",
            [dt]).fetchall()}
        for ex in target_exs:
            if ex not in summary_exs:
                rows.append({"date": dt, "exchange": ex, "issue": ISSUE_SUMMARY_ABSENT})
            if ex not in detail_exs:
                rows.append({"date": dt, "exchange": ex, "issue": ISSUE_DETAIL_ABSENT})

    result.rows = rows
    result.count = len(rows)
    result.status = checker.resolve_status(checked, unchecked, len(rows))

    if unchecked:
        checker.logger.warning(
            f"[{LABEL}] 两融数据 T+1 披露，以下日期尚未到可取时间，未核对: {unchecked}")
    checker.log_rows(LABEL, [f"{r['date']}  {r['exchange']}  {r['issue']}" for r in rows])
    result.csv_path = checker.write_diff_csv(
        CSV_TAG, begin, end, ["date", "exchange", "issue"],
        [(r["date"], r["exchange"], r["issue"]) for r in rows])
    return result
