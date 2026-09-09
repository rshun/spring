# 修改记录:
#   2026-09-06  Claude  新增复权因子对账工具(复权因子自建方案 §6.1/§6.3, 只告警不阻断)
#   2026-09-06  Claude  LOCAL vs RAW 数值比较改为相邻公共事件的区间跳变比:
#                       两链基准不同(LOCAL 首个可计算事件起 1.0 累乘, RAW 为 baostock
#                       绝对水位), 同日绝对值不可直接比, 原口径会全市场误报
#   2026-09-06  Claude  修复 BUG-002: 因子事件加载每股额外带窗口前最近事件作基准,
#                       compute_factor_jumps 用真实基准算跳变, 基准行不进集合差与
#                       腾讯事件有无对比; 修复 BUG-003 对账侧: tx jump_ratio 缺失而
#                       因子侧有事件时输出 jump_unverified, 不再静默跳过
#   2026-09-06  Claude  修复 BUG-004 对账侧: compute_factor_jumps 增加
#                       unit_baseline_ok 区分数据源(LOCAL 表内首行即链首允许 1.0
#                       基准; RAW 无前值时 jump_ratio=None, 下游标记 jump_unverified)
#   2026-09-06  Claude  修复 BUG-005: fetch_xdr_events 新返回 (df, failed_codes);
#                       event_missing_in_tx 判定基准改为成功取数股票集合;
#                       汇总按校验状态如实输出, 腾讯未完成时不再报「全部一致 OK」
"""
功能: 复权因子三方对账(只告警写 CSV, 不阻断流程, 退出码恒为 0)
  1) LOCAL vs RAW: ADJ_FACTOR_LOCAL(自算, 主源) 与 ADJ_FACTOR_RAW(baostock 留痕)
     在窗口内的事件集合差(互相缺失), 以及相邻公共事件的区间跳变比
     (back(t2)/back(t1)) 相对误差超 tolerance 的区间
  2) 腾讯三方对账: 窗口内每只股票的腾讯 fqkline 事件帧(datasource.txstock)
     vs LOCAL / RAW(事件有无、跳变比例) 及 vs gbbq CAPITAL_DETAIL(分红额)

ADJ_FACTOR_LOCAL 可能尚不存在(并行开发中): 读取失败时降级为「仅 RAW vs 腾讯」。

输入参数:
  -b, --begin       起始日期 (格式: YYYYMMDD)，默认为当天
  -e, --end         结束日期 (格式: YYYYMMDD)，默认为当天
  --tolerance       相对误差容忍度 (默认 1e-3)
  -c, --codes       指定股票代码 (可选, 如 600519,000681)

用法:
  python -m tools.check_adjust -b 20260801 -e 20260906
  python -m tools.check_adjust -b 20260801 -e 20260906 -c 000681 --tolerance 0.001
"""
import argparse
import logging
from pathlib import Path

import pandas as pd

from util import dbutil, myutil
from util import validators as pv

# 挂在 "etl" 之下，日志才会进 configure_etl_logging 配置的 stockdailyYYYYMMDD.log
logger = logging.getLogger("etl.tools.check_adjust")

DEFAULT_TOLERANCE = 1e-3

# 对比输出帧的列
# 集合差行: from_date = 事件日, to_date = None;
# 区间跳变行: from_date/to_date = 相邻公共事件日期 t1/t2
DIFF_FACTOR_COLUMNS = ["code", "from_date", "to_date", "issue",
                       "jump_local", "jump_raw", "jump_diff"]
DIFF_TX_FACTOR_COLUMNS = ["code", "xdr_date", "issue", "source",
                          "tx_jump_ratio", "factor_jump_ratio", "rel_diff"]
DIFF_TX_GBBQ_COLUMNS = ["code", "xdr_date", "issue",
                        "tx_div_per10", "gbbq_dividend", "rel_diff"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="复权因子三方对账工具(LOCAL vs RAW vs 腾讯fqkline, 只告警)"
    )
    parser.add_argument('-b', '--begin', type=str, default=myutil.get_today(),
                        help='指定起始日期 (格式: YYYYMMDD)，默认为当天')
    parser.add_argument('-e', '--end', type=str, default=myutil.get_today(),
                        help='指定结束日期 (格式: YYYYMMDD)，默认为当天')
    parser.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE,
                        help=f'相对误差容忍度 (默认 {DEFAULT_TOLERANCE})')
    parser.add_argument('-c', '--codes', nargs='+',
                        help='指定股票代码列表 (例如: 600519,000681)，不传则处理窗口内有事件的全部股票')
    return parser


def parse_arguments() -> argparse.Namespace:
    return build_parser().parse_args()


def check_parameters(begin: str, end: str) -> bool:
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
    ]
    return pv.run(ctx, validators)


# ── 纯对比逻辑(DataFrame 注入, 不触库不触网, 供单测直接调用) ──────────

def _ymd(value) -> str:
    """日期统一为 YYYY-MM-DD 字符串(兼容 date/datetime/YYYYMMDD 字符串)"""
    s = str(value)[:10]
    return s


def _norm_factor_df(df: pd.DataFrame) -> pd.DataFrame:
    """复权因子事件帧归一化: code(str) / trade_date(YYYY-MM-DD) / back_factor(float)

    输入帧可带 is_baseline 列(1=窗口前最近事件, 仅作跳变计算基准, 见
    _load_factor_events); 缺省时全部视为窗口内事件(0)。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "trade_date", "back_factor",
                                     "is_baseline"])
    out = df[["code", "trade_date", "back_factor"]].copy()
    out["code"] = out["code"].astype(str)
    out["trade_date"] = out["trade_date"].map(_ymd)
    out["back_factor"] = pd.to_numeric(out["back_factor"], errors="coerce")
    if "is_baseline" in df.columns:
        out["is_baseline"] = df["is_baseline"].fillna(0).astype(int).to_numpy()
    else:
        out["is_baseline"] = 0
    return out


def _rel_diff(a: float, b: float) -> float:
    """相对误差 |a-b|/|b|; b 为 0 时 a==0 记 0, 否则记 inf"""
    if pd.isna(a) or pd.isna(b):
        return float("nan")
    if b == 0:
        return 0.0 if a == 0 else float("inf")
    return abs(a - b) / abs(b)


def diff_factor_frames(local_df: pd.DataFrame, raw_df: pd.DataFrame,
                       tolerance: float) -> pd.DataFrame:
    """对比一: LOCAL vs RAW

    - 事件集合双向差(missing_in_raw / missing_in_local), 行内 from_date 为事件日
    - 数值比较用「相邻公共事件的区间跳变比」而非同日绝对值: 两链基准不同
      (LOCAL 首个可计算事件起 1.0 累乘, RAW 是 baostock 绝对水位, 如 000681
      2026-08-03 local=2.518889 vs raw=3.904298), 绝对值直接比会全市场误报。
      对每个 code 取两帧事件的交集日期 t1<t2<..., 对相邻公共事件对比较
      jump = back(t2)/back(t1): 该口径 scale-invariant, 且对区间中间夹着的
      单边缺失事件鲁棒(区间内两链都正确则比值一致)。
      |jump_local/jump_raw - 1| > tolerance 才报 interval_jump_diff。

    is_baseline=1 的行(窗口前基准)不进入本对比: 集合语义保持窗口口径,
    区间跳变比本身与基准无关(相邻公共事件的比值已约去基准)。
    """
    local = _norm_factor_df(local_df)
    raw = _norm_factor_df(raw_df)
    local = local[local["is_baseline"] == 0]
    raw = raw[raw["is_baseline"] == 0]

    merged = local.merge(raw, on=["code", "trade_date"], how="outer",
                         suffixes=("_local", "_raw"), indicator=True)
    rows = []
    for _, r in merged.iterrows():
        if r["_merge"] == "left_only":
            rows.append({"code": r["code"], "from_date": r["trade_date"],
                         "to_date": None, "issue": "missing_in_raw",
                         "jump_local": None, "jump_raw": None, "jump_diff": None})
        elif r["_merge"] == "right_only":
            rows.append({"code": r["code"], "from_date": r["trade_date"],
                         "to_date": None, "issue": "missing_in_local",
                         "jump_local": None, "jump_raw": None, "jump_diff": None})

    common = merged[merged["_merge"] == "both"]
    for code, g in common.groupby("code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        jump_local = g["back_factor_local"] / g["back_factor_local"].shift(1)
        jump_raw = g["back_factor_raw"] / g["back_factor_raw"].shift(1)
        for i in range(1, len(g)):
            jd = _rel_diff(jump_local.iloc[i], jump_raw.iloc[i])
            if pd.notna(jd) and jd > tolerance:
                rows.append({"code": code,
                             "from_date": g.loc[i - 1, "trade_date"],
                             "to_date": g.loc[i, "trade_date"],
                             "issue": "interval_jump_diff",
                             "jump_local": jump_local.iloc[i],
                             "jump_raw": jump_raw.iloc[i],
                             "jump_diff": jd})

    out = pd.DataFrame(rows, columns=DIFF_FACTOR_COLUMNS)
    if not out.empty:
        out = out.sort_values(["code", "from_date"]).reset_index(drop=True)
    return out


def compute_factor_jumps(factor_df: pd.DataFrame,
                         unit_baseline_ok: bool = True) -> pd.DataFrame:
    """由稀疏事件帧计算每股每个事件的 back_factor 跳变比例(≈ C/X)

    back_factor 是上市起累计连乘, 相邻事件行之比即当次事件的 C/X。
    前值取该股事件帧内的上一行; is_baseline=1 的行(窗口前最近事件,
    由 _load_factor_events 附加)只参与前值计算、不进入输出, 这样窗口内
    首条事件不会误用 1.0 基准把累计水位当成当次跳变(BUG-002)。

    unit_baseline_ok 区分「无前值时能否用 1.0 当基准」(BUG-004):
    - True (LOCAL 表): 表内首行即因子链首(local_xdr 输出全历史事件链,
      表从链首完整自洽), 基准 1.0 语义正确
    - False (RAW 表): baostock 口径 back 为绝对水位且可能缺历史, 表内
      首行未必是历史首事件, 不得用 1.0; 该事件 jump_ratio=None,
      下游 diff_tx_vs_factor 标记 jump_unverified, 不参与数值比较
    """
    df = _norm_factor_df(factor_df)
    if df.empty:
        return pd.DataFrame(columns=["code", "trade_date", "jump_ratio"])
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    prev = df.groupby("code")["back_factor"].shift(1)
    if unit_baseline_ok:
        prev = prev.fillna(1.0)
    df["jump_ratio"] = df["back_factor"] / prev
    df = df[df["is_baseline"] == 0]
    return df[["code", "trade_date", "jump_ratio"]]


def _norm_tx_df(tx_df: pd.DataFrame) -> pd.DataFrame:
    from datasource import txstock
    if tx_df is None or tx_df.empty:
        return pd.DataFrame(columns=txstock.EVENT_COLUMNS)
    out = tx_df.copy()
    out["code"] = out["code"].astype(str)
    out["xdr_date"] = out["xdr_date"].map(_ymd)
    return out


def diff_tx_vs_factor(tx_df: pd.DataFrame, factor_df: pd.DataFrame,
                      source_name: str, tolerance: float,
                      unit_baseline_ok: bool = True,
                      tx_fetched_codes: set[str] | None = None) -> pd.DataFrame:
    """腾讯事件帧 vs 复权因子事件表(LOCAL 或 RAW): 事件有无 + 跳变比例

    unit_baseline_ok: 透传给 compute_factor_jumps(BUG-004); LOCAL 传 True,
        RAW 传 False(无前值时因子侧跳变置 None, 标记 jump_unverified)。
    tx_fetched_codes: 腾讯成功取数的股票集合(fetch_xdr_events 的
        请求 codes 减去 failed_codes)。event_missing_in_tx 以成功取数
        集合为判定基准(BUG-005): 成功取数但窗口无事件的股票, 其因子侧
        事件应能被判 missing_in_tx; 取数失败的股票不误报。
        缺省 None 时回退旧语义(tx 帧内出现过事件的股票集合)。
    任一侧跳变缺失(腾讯拿不到前值, 或 RAW 无基准)时输出 jump_unverified,
    明确标记「未完成校验」, 不算一致也不算数值差异(BUG-003/004)。
    """
    tx = _norm_tx_df(tx_df)
    jumps = compute_factor_jumps(factor_df, unit_baseline_ok=unit_baseline_ok)

    tx_keys = set(zip(tx["code"], tx["xdr_date"]))
    tx_codes = (set(tx_fetched_codes) if tx_fetched_codes is not None
                else set(tx["code"]))
    f_rows = {(r["code"], r["trade_date"]): r["jump_ratio"]
              for _, r in jumps.iterrows()}

    rows = []
    for _, r in tx.iterrows():
        key = (r["code"], r["xdr_date"])
        if key not in f_rows:
            rows.append({"code": r["code"], "xdr_date": r["xdr_date"],
                         "issue": f"event_missing_in_{source_name}",
                         "source": source_name,
                         "tx_jump_ratio": r.get("jump_ratio"),
                         "factor_jump_ratio": None, "rel_diff": None})
            continue
        f_jump = f_rows[key]
        tx_jump = r.get("jump_ratio")
        f_missing = f_jump is None or pd.isna(f_jump)
        t_missing = tx_jump is None or pd.isna(tx_jump)
        if f_missing or t_missing:
            rows.append({"code": r["code"], "xdr_date": r["xdr_date"],
                         "issue": "jump_unverified", "source": source_name,
                         "tx_jump_ratio": None if t_missing else tx_jump,
                         "factor_jump_ratio": None if f_missing else f_jump,
                         "rel_diff": None})
            continue
        rd = _rel_diff(tx_jump, f_jump)
        if pd.notna(rd) and rd > tolerance:
            rows.append({"code": r["code"], "xdr_date": r["xdr_date"],
                         "issue": "jump_ratio_diff", "source": source_name,
                         "tx_jump_ratio": tx_jump,
                         "factor_jump_ratio": f_jump, "rel_diff": rd})
    for (code, dt), f_jump in f_rows.items():
        if code in tx_codes and (code, dt) not in tx_keys:
            rows.append({"code": code, "xdr_date": dt,
                         "issue": "event_missing_in_tx", "source": source_name,
                         "tx_jump_ratio": None,
                         "factor_jump_ratio": f_jump, "rel_diff": None})
    out = pd.DataFrame(rows, columns=DIFF_TX_FACTOR_COLUMNS)
    if not out.empty:
        out = out.sort_values(["code", "xdr_date"]).reset_index(drop=True)
    return out


def diff_tx_vs_gbbq(tx_df: pd.DataFrame, gbbq_df: pd.DataFrame,
                    tolerance: float,
                    tx_fetched_codes: set[str] | None = None) -> pd.DataFrame:
    """腾讯事件帧 vs gbbq CAPITAL_DETAIL: 事件有无 + 分红额(均为每10股口径)

    gbbq_df 列: code / date / dividend(每10股)
    tx_fetched_codes: 腾讯成功取数的股票集合, 作为 event_missing_in_tx 的
        判定基准(同 diff_tx_vs_factor 的 BUG-005 修正); 缺省 None 时回退
        旧语义(tx 帧内出现过事件的股票集合)。
    """
    tx = _norm_tx_df(tx_df)
    if gbbq_df is None or gbbq_df.empty:
        gbbq = pd.DataFrame(columns=["code", "date", "dividend"])
    else:
        gbbq = gbbq_df[["code", "date", "dividend"]].copy()
        gbbq["code"] = gbbq["code"].astype(str)
        gbbq["date"] = gbbq["date"].map(_ymd)
        gbbq["dividend"] = pd.to_numeric(gbbq["dividend"], errors="coerce")

    g_rows = {(r["code"], r["date"]): r["dividend"] for _, r in gbbq.iterrows()}
    tx_keys = set(zip(tx["code"], tx["xdr_date"]))
    tx_codes = (set(tx_fetched_codes) if tx_fetched_codes is not None
                else set(tx["code"]))

    rows = []
    for _, r in tx.iterrows():
        key = (r["code"], r["xdr_date"])
        if key not in g_rows:
            rows.append({"code": r["code"], "xdr_date": r["xdr_date"],
                         "issue": "event_missing_in_gbbq",
                         "tx_div_per10": r.get("div_per10"),
                         "gbbq_dividend": None, "rel_diff": None})
            continue
        g_div = g_rows[key]
        t_div = r.get("div_per10")
        # 纯送转事件两边都没有分红额, 属正常
        t_empty = t_div is None or pd.isna(t_div) or t_div == 0
        g_empty = g_div is None or pd.isna(g_div) or g_div == 0
        if t_empty and g_empty:
            continue
        rd = _rel_diff(t_div, g_div)
        if pd.isna(rd) or rd > tolerance:
            rows.append({"code": r["code"], "xdr_date": r["xdr_date"],
                         "issue": "dividend_diff",
                         "tx_div_per10": t_div, "gbbq_dividend": g_div,
                         "rel_diff": rd if pd.notna(rd) else None})
    for (code, dt), g_div in g_rows.items():
        if code in tx_codes and (code, dt) not in tx_keys:
            rows.append({"code": code, "xdr_date": dt,
                         "issue": "event_missing_in_tx",
                         "tx_div_per10": None, "gbbq_dividend": g_div,
                         "rel_diff": None})
    out = pd.DataFrame(rows, columns=DIFF_TX_GBBQ_COLUMNS)
    if not out.empty:
        out = out.sort_values(["code", "xdr_date"]).reset_index(drop=True)
    return out


# ── 数据库读取与编排 ─────────────────────────────────────

def _load_factor_events(conn, table: str, begin_date: str, end_date: str) -> pd.DataFrame | None:
    """读取复权因子事件表; 表不存在(如 LOCAL 尚未上线)时返回 None 由调用方降级

    返回帧除窗口内事件(is_baseline=0)外, 每股额外带一条窗口前最近事件
    (is_baseline=1)作为跳变计算基准(BUG-002): 窗口内首条事件的跳变应是
    back(t1)/back(窗口前最近事件), 没有基准会被误算成 back(t1)/1.0。
    基准行不进集合差/事件有无对比(由各 diff 函数按 is_baseline 过滤)。
    """
    try:
        return conn.execute(
            f"SELECT code, CAST(trade_date AS VARCHAR) AS trade_date, "
            f"       back_factor, 0 AS is_baseline "
            f"FROM {table} WHERE trade_date BETWEEN ? AND ? "
            f"UNION ALL "
            f"SELECT code, CAST(trade_date AS VARCHAR), back_factor, 1 "
            f"FROM {table} "
            f"WHERE trade_date < ? "
            f"  AND (code, trade_date) IN ("
            f"      SELECT code, MAX(trade_date) FROM {table} "
            f"      WHERE trade_date < ? GROUP BY code)",
            [begin_date, end_date, begin_date, begin_date],
        ).df()
    except Exception as e:
        logger.warning(f"读取 {table} 失败({e})，本表不参与本次对账")
        return None


def _load_gbbq_events(conn, begin_date: str, end_date: str) -> pd.DataFrame | None:
    """读取 gbbq 除权除息事件(标准 code / date / dividend, dividend 为每10股)"""
    try:
        return conn.execute(
            "SELECT i.code AS code, CAST(c.date AS VARCHAR) AS date, c.dividend "
            "FROM CAPITAL_DETAIL c "
            "INNER JOIN STOCK_INFO i ON i.symbol = c.code "
            "WHERE c.category = '除权除息' AND c.date BETWEEN ? AND ?",
            [begin_date, end_date],
        ).df()
    except Exception as e:
        logger.warning(f"读取 CAPITAL_DETAIL 失败({e})，跳过腾讯 vs gbbq 分红额对账")
        return None


def _filter_by_codes(df: pd.DataFrame, code_col: str, symbols: list[str]) -> pd.DataFrame:
    """按裸 symbol(600519) 过滤标准代码(600519.SH) 帧"""
    if df is None or df.empty or not symbols:
        return df
    sym_set = set(symbols)
    return df[df[code_col].astype(str).str.split(".").str[0].isin(sym_set)]


def _parse_codes_arg(codes: list[str] | None) -> list[str]:
    if not codes:
        return []
    out: list[str] = []
    for item in codes:
        clean = item.replace('，', ',')
        out.extend([x.strip() for x in clean.split(',') if x.strip()])
    return out


def _write_diff_csv(tag: str, begin: str, end: str, df: pd.DataFrame) -> Path | None:
    csv_dir = Path(__file__).parent.parent / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / f"check_adjust_{tag}_{begin}_{end}.csv"
    df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    return csv_file


def _report_diffs(label: str, tag: str, begin: str, end: str,
                  diff: pd.DataFrame) -> int:
    """打印对比结果, 有差异写 CSV, 返回差异条数"""
    if diff is None or diff.empty:
        logger.info(f"[{label}]    一致 OK")
        return 0
    csv_file = _write_diff_csv(tag, begin, end, diff)
    logger.warning(f"[{label}]    发现 {len(diff)} 条差异，明细已写入: {csv_file}")
    return len(diff)


def _build_summary(total: int, incomplete: list[str]) -> tuple[str, bool]:
    """汇总对账结论, 返回 (message, is_warning)

    BUG-005: 只有在所有来源校验都完成(incomplete 为空)且零差异时
    才允许输出「全部一致 OK」; 有校验未完成项时如实列出, 不得以
    零差异掩盖未完成(如腾讯取数全部失败)。
    """
    if incomplete:
        note = "；".join(incomplete)
        if total:
            return (f"对账完成: 共 {total} 条差异(已写 CSV，仅告警)；"
                    f"{note}", True)
        return f"对账完成: 已完成对比项无差异；{note}", True
    if total:
        return f"对账完成: 共 {total} 条差异(已写 CSV，仅告警)", True
    return "对账完成: 全部一致 OK", False


def main() -> int:
    """只告警不阻断: 无论是否有差异均返回 0"""
    args = parse_arguments()
    myutil.configure_etl_logging()

    if not check_parameters(args.begin, args.end):
        return 0

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date = myutil.trans_datestr_format(args.end)
    symbols = _parse_codes_arg(args.codes)

    logger.info("=" * 60)
    logger.info("复权因子三方对账(只告警, 不阻断)")
    logger.info(f"     起始日期: {begin_date}")
    logger.info(f"     结束日期: {end_date}")
    logger.info(f"     容差:     {args.tolerance}")
    logger.info(f"     指定代码: {symbols if symbols else '无 (窗口内全部)'}")
    logger.info("=" * 60)

    conn = None
    try:
        conn = dbutil.get_connection()

        raw_df = _load_factor_events(conn, "ADJ_FACTOR_RAW", begin_date, end_date)
        local_df = _load_factor_events(conn, "ADJ_FACTOR_LOCAL", begin_date, end_date)

        total = 0
        incomplete: list[str] = []  # 校验未完成项(BUG-005: 不得以零差异掩盖)
        if local_df is None:
            logger.warning("ADJ_FACTOR_LOCAL 不可用，降级为「仅 RAW vs 腾讯」对账")
            incomplete.append("ADJ_FACTOR_LOCAL 不可用，已降级为仅 RAW 对账")

        if raw_df is None:
            incomplete.append("ADJ_FACTOR_RAW 不可用，已跳过 RAW 相关对比")

        raw_df = _filter_by_codes(raw_df, "code", symbols)
        local_df = _filter_by_codes(local_df, "code", symbols)

        if local_df is not None and raw_df is not None:
            total += _report_diffs(
                "LOCAL vs RAW", "local_vs_raw", args.begin, args.end,
                diff_factor_frames(local_df, raw_df, args.tolerance))

        # 腾讯对账范围: LOCAL/RAW 窗口内出现过的股票并集(不含基准行,
        # 否则所有有历史事件的股票都会被拉腾讯行情)
        def _window_codes(df):
            if df is None or df.empty:
                return set()
            if "is_baseline" in df.columns:
                df = df[df["is_baseline"].fillna(0).astype(int) == 0]
            return set(df["code"].astype(str))

        gbbq_df = _filter_by_codes(_load_gbbq_events(conn, begin_date, end_date), "code", symbols)
        if gbbq_df is None:
            incomplete.append("gbbq 读取失败，独立事件候选范围未完成")
        independent_codes = set() if gbbq_df is None or gbbq_df.empty else set(gbbq_df["code"])
        # 显式股票不能因两侧漏事件而被候选集合排除。
        for symbol in symbols:
            matches = {code for code in independent_codes | _window_codes(raw_df) | _window_codes(local_df)
                       if code.split(".")[0] == symbol}
            if matches:
                independent_codes.update(matches)
            elif symbol.startswith(("6", "5")):
                independent_codes.add(symbol + ".SH")
            elif symbol.startswith(("0", "3", "1")):
                independent_codes.add(symbol + ".SZ")
            else:
                incomplete.append(f"{symbol} 无可确认的腾讯市场代码，校验未完成")
        codes = sorted(_window_codes(raw_df) | _window_codes(local_df) | independent_codes)
        if not codes:
            logger.info("[腾讯对账  ]    窗口内 LOCAL/RAW 均无事件，跳过腾讯拉取")
        else:
            from datasource import txstock
            logger.info(f"[腾讯对账  ]    开始拉取 {len(codes)} 只股票的腾讯事件帧...")
            tx_df, failed_codes = txstock.fetch_xdr_events(codes, begin_date,
                                                           end_date)
            # 成功取数集合 = 请求集合 - 失败集合(BUG-005: event_missing_in_tx
            # 以成功取数为判定基准, 成功但无事件的股票也参与事件有无对比)
            fetched_codes = set(codes) - set(failed_codes)
            if not fetched_codes:
                logger.warning(f"[腾讯对账  ]    {len(failed_codes)} 只股票取数全部失败，"
                               "腾讯对比未完成")
                incomplete.append(f"腾讯对账未完成({len(failed_codes)} 只取数失败)")
            else:
                if failed_codes:
                    logger.warning(f"[腾讯对账  ]    {len(failed_codes)}/{len(codes)} "
                                   "只取数失败被跳过")
                    incomplete.append(
                        f"腾讯部分股票取数失败({len(failed_codes)}/{len(codes)} 只)")
                if tx_df.empty:
                    logger.info("[腾讯对账  ]    成功取数但窗口内无事件元数据")
                if local_df is not None:
                    total += _report_diffs(
                        "腾讯 vs LOCAL", "tx_vs_local", args.begin, args.end,
                        # LOCAL 经水位缩放，首行无真实前值时不能假定 1.0
                        diff_tx_vs_factor(tx_df, local_df, "local", args.tolerance,
                                          unit_baseline_ok=False,
                                          tx_fetched_codes=fetched_codes))
                if raw_df is not None:
                    total += _report_diffs(
                        "腾讯 vs RAW", "tx_vs_raw", args.begin, args.end,
                        # RAW 为 baostock 绝对水位, 无前值时不得用 1.0 基准
                        diff_tx_vs_factor(tx_df, raw_df, "raw", args.tolerance,
                                          unit_baseline_ok=False,
                                          tx_fetched_codes=fetched_codes))
                if gbbq_df is None:
                    incomplete.append("gbbq 读取失败，分红额对账未完成")
                else:
                    total += _report_diffs(
                        "腾讯 vs gbbq", "tx_vs_gbbq", args.begin, args.end,
                        diff_tx_vs_gbbq(tx_df, gbbq_df, args.tolerance,
                                        tx_fetched_codes=fetched_codes))

        logger.info("-" * 60)
        message, is_warning = _build_summary(total, incomplete)
        (logger.warning if is_warning else logger.info)(message)
        return 0
    except Exception as e:
        logger.error(f"对账过程中发生错误: {e}")
        return 0
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
