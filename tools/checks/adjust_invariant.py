# 修改记录:
#   2026-09-13  Claude  新建复权因子逐日恒等式核对(不依赖事件清单, 不依赖外部网络)
"""复权因子逐日恒等式核对

    back_factor[t] / back_factor[t-1]  ==  prev_close[t] / pre_close[t]

非除权日 pre_close 就是前一交易日收盘, 右边恒为 1, 因子不该动; 除权日 pre_close
是交易所给出的除权参考价, 右边就是交易所**实际使用**的除权比例(未必等于公告的
名义比例, 见 datasource/local_xdr.py 的除权参考价校正)。

与 check_adjust.py 其余对账项的根本差别: 本项**不需要事件清单**。
gbbq / 腾讯 那几项都得先有一份事件表再逐个验算, 因而只能查出「已知事件算错了」,
查不出「算法根本不知道有这个事件」——那是事件驱动核对的结构性盲区。pre_close 是
交易所在每个交易日都给出的量, 于是漏事件、多事件、比例错、日期错(表现为相邻的
漏+多)四类缺陷都会在同一个查询里现形。也因为纯 SQL 不碰外部接口, 它在网络不可用
时照常可跑。

前提: pre_close 必须是交易所口径。只有 datasource/bstock.py 携带真实 preclose,
lday / tdx 两个源是用 LAG(close) 现算的(见各自源码), 被它们覆盖过的行本项核对
恒成立、查不出任何问题。实测 2011 年以后无此类污染。

容差取「两个参考价中较小者的半分」: pre_close 被交易所四舍五入到分, 由它反推的
比例其不确定度就是 0.005/参考价。基准必须取**较小者**——未校正的舍入平局其偏差
是 0.005/名义参考价, 而名义值小于 pre_close, 若用 0.005/pre_close 作容差, 这类
平局会全部被判成超差(实测虚报 525 条, 曾导致把工具自身的缺陷误读成被测对象的
缺陷)。核对工具本身也要核对。

返回 DataFrame 而非 checker.CheckResult: 调用方 tools/check_adjust.py 的
_report_diffs 以 DataFrame 为约定, 与该文件其余对账项保持一致。
"""
import duckdb
import pandas as pd

LABEL = "恒等式核对"
CSV_TAG = "invariant"

ISSUE_EVENT_MISSING = "漏事件(交易所有跳变, 因子没动)"
ISSUE_EVENT_SPURIOUS = "多事件(因子跳了, 交易所没跳)"
ISSUE_RATIO_WRONG = "比例错(因子跳变比例与交易所不符)"

COLUMNS = ["code", "date", "prev_close", "pre_close", "r_exch", "r_fac", "issue"]

# pre_close 的报价精度为分, 半分即其四舍五入误差上界
_PRICE_ROUNDING = 0.005

_SQL = f"""
WITH px AS (
    SELECT d.code, d.date, d.pre_close,
           LAG(d.close) OVER (PARTITION BY d.code ORDER BY d.date) AS prev_close
    FROM STOCK_DAILY d
    WHERE d.tradestatus = 1 AND d.pre_close > 0
      -- 指数不做复权, 其 pre_close 与前收的差异来自指数编制规则, 必须排除
      AND EXISTS (SELECT 1 FROM STOCK_INFO i
                   WHERE i.code = d.code AND i.board <> 'INDEX')
),
fac AS (
    -- 事件表是稀疏的(只在事件日落行), 首行之前的隐含水位为 1.0
    SELECT code, trade_date AS date, back_factor,
           COALESCE(LAG(back_factor) OVER (PARTITION BY code ORDER BY trade_date),
                    1.0) AS prev_bf
    FROM ADJ_FACTOR_LOCAL
),
j AS (
    SELECT px.code, px.date, px.prev_close, px.pre_close,
           px.prev_close / px.pre_close            AS r_exch,
           f.back_factor / NULLIF(f.prev_bf, 0)    AS r_fac
    FROM px
    LEFT JOIN fac f ON f.code = px.code AND f.date = px.date
    WHERE px.prev_close IS NOT NULL
      AND px.date BETWEEN ? AND ?
      {{code_filter}}
),
t AS (
    SELECT j.*,
           {_PRICE_ROUNDING} / LEAST(
               j.pre_close,
               COALESCE(j.prev_close / NULLIF(j.r_fac, 0), j.pre_close)
           ) * (1 + 1e-9) + 1e-12 AS tol
    FROM j
)
SELECT * FROM (
    SELECT code, date, prev_close, pre_close, r_exch, r_fac,
           CASE
             WHEN r_fac IS NULL AND abs(r_exch - 1) > tol
                  THEN '{ISSUE_EVENT_MISSING}'
             WHEN r_fac IS NOT NULL AND abs(r_exch - 1) <= tol
                  AND abs(r_fac - 1) > 1e-9
                  THEN '{ISSUE_EVENT_SPURIOUS}'
             WHEN r_fac IS NOT NULL AND abs(r_fac / r_exch - 1) > tol
                  THEN '{ISSUE_RATIO_WRONG}'
           END AS issue
    FROM t
) x
WHERE x.issue IS NOT NULL
ORDER BY x.date DESC, x.code
"""


def check_invariant(conn: duckdb.DuckDBPyConnection,
                    begin_date: str, end_date: str,
                    symbols: list[str] | None = None) -> pd.DataFrame:
    """逐日恒等式核对, 返回差异帧(无差异则为空帧, 列仍齐全)

    参数:
        begin_date / end_date: YYYY-MM-DD(与 check_adjust.py 其余项一致)
        symbols: 6 位代码列表, 为空表示全市场

    窗口只裁剪输出, 不裁剪 LAG 的取数范围 —— 长期停牌股的前收可能远在窗口之外,
    提前裁剪会把它算成跳空而虚报漏事件。
    """
    code_filter = ""
    params: list = [begin_date, end_date]
    if symbols:
        placeholders = ",".join(["?"] * len(symbols))
        code_filter = f"AND split_part(px.code, '.', 1) IN ({placeholders})"
        params.extend(symbols)

    df = conn.execute(_SQL.format(code_filter=code_filter), params).df()
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    return df[COLUMNS]
