# 修改记录:
#   2026-09-06  Claude  新增：本地自算复权因子主源（方案A，CAPITAL_DETAIL 除权事件
#                       + STOCK_DAILY 收盘价，见 docs/adj_factor_selfbuild.md §4）
#   2026-09-06  Claude  修复 BUG-001：输出附带每股窗口前最近一条事件作为锚点行，
#                       避免首次/窄窗口运行时 ASOF 稠密化缺历史基准错填 1.0
#   2026-09-06  Claude  修复 BUG-004 根源：锚点行方案改为返回每股全历史事件链，
#                       ADJ_FACTOR_LOCAL 从链首起完整自洽，「表内首行==链首」成为
#                       对账侧可依赖的不变量
#   2026-09-06  Claude  修复第二轮审查 BUG-006/007/008/011/012①/014/017/018：
#                       取价下推为 SQL ASOF LEFT JOIN（tradestatus=1 过滤停牌行，
#                       不再全量读日线）；fore 改为锚定最新事件的累计前复权口径；
#                       back/adjust 按 ADJ_FACTOR 末行水位在线校准（k 倍缩放）；
#                       未来事件不入链；缺价/缺配股价事件分级统计并汇总告警；
#                       补取数与计算阶段的边界日志（心跳）
#   2026-09-09  Claude  事件日恰为该股日线首日时归「历史覆盖外」而非「区间内部缺口」：
#                       首日之前无 K 线可取前收，不是数据缺口；此前 600018.SH
#                       2006-10-26 换股上市当天的除权事件被判为缺口，整批 668 只中止
#   2026-09-11  Codex   关闭「历史覆盖外」逐股明细日志，保留跳过统计与批次汇总
"""
本地自算复权因子数据源

用库内已有的 gbbq 除权事件（CAPITAL_DETAIL）和日线收盘价（STOCK_DAILY）
自行计算复权因子，替代 baostock 取数层（ADJ_FACTOR_RAW 降级为留痕）。

计算公式（docs/adj_factor_selfbuild.md §4）：
    除权参考价 X = (C - D + P * N) / (1 + S + N)
      C = 事件日前最近一个 tradestatus=1 且 close>0 的交易日收盘价
          （SQL ASOF 下推；停牌行 close 为前收结转的正数，必须按 tradestatus 过滤）
      D = dividend / 10        （gbbq 单位：每 10 股）
      P = allotment_price      （配股价，不除；有配股份额但配股价缺失的事件跳过）
      S = bonus_share / 10     （送转比例，每股）
      N = allotment_share / 10 （配股比例，每股）
    back_factor   = 该股首个可计算事件起 C/X 的累计连乘，再乘以水位系数 k
      k = ADJ_FACTOR_LOCAL_STATE 保存的固定基准，不随每日事件重新反推
      （首次无历史为 1；有历史时只接受首事件之前的可信水位，否则拒绝自动迁移）
    fore_factor   = back_t / back_该股最后一条事件（锚定最新事件的累计前复权口径，
      最新事件恒 1.0，与 baostock foreAdjustFactor 一致；k 在比值中约掉）
    adjust_factor = back_factor （恒等，沿用 baostock 口径）

对外接口 fetch_adjust_factors(stock_list) 与 datasource/bstock.py 同名函数一致：
stock_list 为 (symbol, market, start_date, end_date, status) 元组列表，
返回 DataFrame(code, date, fore_factor, back_factor, adjust_factor)。
返回每股**全部历史事件**（不按窗口裁剪，未来事件除外）：ADJ_FACTOR_LOCAL 从链首起
完整保存；水位缩放后，表内首行不能再以 1.0 作为跳变基准
（见 docs/bug.md BUG-001/BUG-004）。stock_list 中的窗口参数仅为签名兼容保留。
"""
import logging
import math
import threading
import time
from contextlib import contextmanager
from util.config import get_config

import pandas as pd

from util import dbutil

logger = logging.getLogger("etl.datasource.local_xdr")

# 只有「除权除息」类事件影响价格连续性；股本变化/送配股上市等不产生因子事件
_EVENT_CATEGORY = "除权除息"
_EVENT_VALUE_COLS = ["dividend", "bonus_share", "allotment_share"]
RESULT_COLUMNS = ["code", "date", "fore_factor", "back_factor", "adjust_factor"]

# 事件级取数（BUG-008/012①/014）：取价下推为 ASOF LEFT JOIN，
# 只取 tradestatus=1 且 close>0 的有效收盘价（停牌行 close 是前收结转的正数，
# close>0 无过滤作用）；prev_close 为 NULL 的事件保留在行内，由
# compute_event_factors 分级统计（BUG-011）。候选经 temp_candidates 关联，
# 不再拼 code IN (...) 占位符。
_EVENT_SQL = """
SELECT t.code AS code, c.date AS date,
       c.dividend, c.bonus_share, c.allotment_share, c.allotment_price,
       d.close AS prev_close,
       (SELECT MIN(dd.date) FROM STOCK_DAILY dd WHERE dd.code = t.code) AS first_price_date
FROM temp_candidates t
JOIN CAPITAL_DETAIL c ON c.code = t.symbol
ASOF LEFT JOIN (
    SELECT code, date, close FROM STOCK_DAILY
    WHERE tradestatus = 1 AND close > 0
) d ON d.code = t.code AND d.date < c.date
WHERE c.category = ?
  AND c.date <= CURRENT_DATE
  AND (COALESCE(c.dividend, 0) > 0
       OR COALESCE(c.bonus_share, 0) > 0
       OR COALESCE(c.allotment_share, 0) > 0)
ORDER BY t.code, c.date
"""

@contextmanager
def _progress_heartbeat(stage):
    """有阶段期限的存活日志；超时停止心跳且不接受计算结果，不跨线程访问数据库。"""
    interval = float(get_config()["baostock"]["progress_heartbeat_seconds"])
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("progress_heartbeat_seconds 必须为有限正数")
    stopped = threading.Event()
    expired = threading.Event()
    timeout = float(get_config().get("local_xdr", {}).get("stage_timeout_seconds", 300))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("stage_timeout_seconds 必须为有限正数")
    deadline = time.monotonic() + timeout

    def report():
        while not stopped.wait(min(interval, max(0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                expired.set()
                logger.error("local 阶段超时：%s；停止存活心跳，禁止本次结果写入", stage)
                return
            logger.info("local 心跳：%s 仍在执行（存活信号，不代表已完成）", stage)

    worker = threading.Thread(target=report, name="local-xdr-heartbeat", daemon=True)
    worker.start()
    try:
        yield
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError(f"local 阶段超时：{stage}")
    finally:
        stopped.set()
        worker.join()


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def _new_stats() -> dict:
    """跳过事件的可观测统计（BUG-011）：N=skipped 总数, M=stocks, X=gap 区间内部缺口"""
    return {"skipped": 0, "stocks": set(), "gap": 0, "out_of_coverage": 0}


def _count_skip(stats: dict | None, df: pd.DataFrame, *, gap: bool = False,
                out_of_coverage: bool = False) -> None:
    if stats is None or df.empty:
        return
    stats["skipped"] += len(df)
    stats["stocks"].update(df["code"].unique())
    if gap:
        stats["gap"] += len(df)
    if out_of_coverage:
        stats["out_of_coverage"] += len(df)


def compute_event_factors(events_df: pd.DataFrame,
                          stats: dict | None = None) -> pd.DataFrame:
    """纯函数：由带 prev_close 的事件帧计算未校准的因子链（全历史累计）。

    Parameters
    ----------
    events_df : DataFrame(code, date, dividend, bonus_share, allotment_share,
                allotment_price, prev_close[, first_price_date])。
                code 为带后缀标准代码（如 000681.SZ）；
                dividend/bonus_share/allotment_share 为每 10 股口径；
                prev_close 为事件日前最近有效收盘价（SQL ASOF 已下推），
                缺失（NULL）的事件跳过并按 first_price_date 分级统计；
                first_price_date 为该股 STOCK_DAILY 最早日期，可无。
    stats : 可选 dict（_new_stats()），传入时累加跳过事件的统计。

    Returns
    -------
    DataFrame(code, date, fore_factor, back_factor, adjust_factor)；
    date 为 datetime64；back/adjust 为未做水位校准的原始累计链；
    fore = back / back_该股最后一条事件（锚定最新事件口径）。
    """
    if events_df is None or events_df.empty:
        return _empty_result()

    events = events_df.copy()
    events["date"] = pd.to_datetime(events["date"])
    for col in _EVENT_VALUE_COLS:
        events[col] = pd.to_numeric(events[col], errors="coerce").fillna(0.0)
    # allotment_price 不 fillna：缺失配股价是有配股事件的数据缺陷（BUG-017），需可识别
    events["allotment_price"] = pd.to_numeric(events["allotment_price"], errors="coerce")
    events["prev_close"] = pd.to_numeric(events["prev_close"], errors="coerce")

    # 只保留 dividend/bonus_share/allotment_share 至少一项 > 0 的事件
    events = events.loc[(events[_EVENT_VALUE_COLS] > 0).any(axis=1)]
    if events.empty:
        return _empty_result()

    events = (events
              .sort_values(["code", "date"])
              .drop_duplicates(["code", "date"], keep="last"))

    # ── 缺前收盘价（BUG-011）：区分「历史覆盖外」与「区间内部缺口」──────────────
    missing = events["prev_close"].isna() | (events["prev_close"] <= 0)
    if missing.any():
        if "first_price_date" in events.columns:
            first_date = pd.to_datetime(events["first_price_date"])
            no_price_at_all = missing & first_date.isna()
            # 事件日 == 日线首日 也归「历史覆盖外」：ASOF 取的是事件日之前的收盘，
            # 首日之前根本没有 K 线，不是覆盖区间内部的缺口（如 600018.SH 2006-10-26
            # 换股吸收合并当天上市，gbbq 把换股记成了当天的除权事件）
            out_cov = missing & first_date.notna() & (events["date"] <= first_date)
            mid_gap = missing & first_date.notna() & (events["date"] > first_date)
        else:
            no_price_at_all = missing
            out_cov = mid_gap = missing & False

        for mask, level, msg, kwargs, log_details in [
            (out_cov, "info", "事件日不晚于该股日线覆盖起点（历史覆盖外），跳过",
             dict(out_of_coverage=True), False),
            (no_price_at_all, "warning", "该股无任何价格记录，跳过其除权事件", {}, True),
            (mid_gap, "warning", "事件落在日线覆盖区间内部但前收盘缺失（数据缺口），跳过",
             dict(gap=True), True),
        ]:
            sub = events.loc[mask]
            if sub.empty:
                continue
            if log_details:
                for code, grp in sub.groupby("code"):
                    dates = grp["date"].dt.strftime("%Y-%m-%d").tolist()
                    getattr(logger, level)(f"{code} {msg} {len(grp)} 条: {dates}")
            _count_skip(stats, sub, **kwargs)
        events = events.loc[~missing]
    if events.empty:
        return _empty_result()

    # ── 有配股份额但配股价缺失（BUG-017）：按 0 元配股会算低 X、抬高因子 ──────
    bad_allot = (events["allotment_share"] > 0) & (
        events["allotment_price"].isna() | (events["allotment_price"] <= 0))
    if bad_allot.any():
        sub = events.loc[bad_allot]
        for code, grp in sub.groupby("code"):
            dates = grp["date"].dt.strftime("%Y-%m-%d").tolist()
            logger.warning(f"{code} 有配股份额但配股价缺失/非正，跳过 {len(grp)} 条事件: {dates}")
        _count_skip(stats, sub)
        events = events.loc[~bad_allot]
    if events.empty:
        return _empty_result()

    c = events["prev_close"]
    d = events["dividend"] / 10.0
    s = events["bonus_share"] / 10.0
    n = events["allotment_share"] / 10.0
    p = events["allotment_price"].fillna(0.0)

    x = (c - d + p * n) / (1.0 + s + n)
    if (x <= 0).any():
        sub = events.loc[x <= 0]
        for code, grp in sub.groupby("code"):
            dates = grp["date"].dt.strftime("%Y-%m-%d").tolist()
            logger.warning(f"{code} 除权参考价非正数，跳过 {len(grp)} 条事件: {dates}")
        _count_skip(stats, sub)
        events = events.loc[x > 0]
        x = x.loc[x > 0]
        c = c.loc[x.index]
    if events.empty:
        return _empty_result()

    # 累计后复权链（未校准）+ 锚定最新事件的累计前复权（BUG-007）
    ratio = c / x
    back = ratio.groupby(events["code"]).cumprod()
    back_last = back.groupby(events["code"]).transform("last")
    fore = back / back_last

    return pd.DataFrame({
        "code": events["code"].to_numpy(),
        "date": events["date"].to_numpy(),
        "fore_factor": fore.to_numpy(),
        "back_factor": back.to_numpy(),
        "adjust_factor": back.to_numpy(),
    })


def _calibrate_to_dense(conn, factors: pd.DataFrame) -> pd.DataFrame:
    """固定基准只初始化一次；缺少可验证基准时拒绝推测迁移水位。"""
    # 已初始化的股票只使用固定基准。首次初始化优先选首事件之前的真实历史值，
    # 若已有历史但没有可证明未受事件影响的基准，拒绝自动迁移。
    saved = dict(conn.execute(
        "SELECT s.code, s.base_factor FROM ADJ_FACTOR_LOCAL_STATE s JOIN temp_candidates t ON t.code=s.code"
    ).fetchall())
    candidates = [r[0] for r in conn.execute("SELECT code FROM temp_candidates").fetchall()]
    bases = {}
    for code in candidates:
        if code in saved:
            base = float(saved[code])
        else:
            chain = factors[factors["code"] == code].sort_values("date")
            first = chain.iloc[0]["date"] if not chain.empty else None
            if first is not None:
                anchor = conn.execute(
                    "SELECT adjust_factor FROM ADJ_FACTOR WHERE code=? AND trade_date < ? ORDER BY trade_date LIMIT 1",
                    [code, first],
                ).fetchone()
                if anchor is None:
                    has_history = conn.execute("SELECT COUNT(*) FROM ADJ_FACTOR WHERE code=?", [code]).fetchone()[0]
                    if has_history:
                        raise RuntimeError(f"{code} 缺少首事件之前的可信基准，拒绝自动初始化；需审核迁移水位")
            else:
                anchor = conn.execute("SELECT adjust_factor FROM ADJ_FACTOR WHERE code=? ORDER BY trade_date LIMIT 1", [code]).fetchone()
                # 没有事件但已有非单位水位，不能声明源完整，保留 BUG-015 防护。
                if anchor and float(anchor[0]) != 1.0:
                    continue
            base = float(anchor[0]) if anchor else 1.0
        if not math.isfinite(base) or base <= 0:
            raise ValueError("ADJ_FACTOR 锚点水位无效，停止本次 local 计算")
        bases[code] = base
    out = factors[factors["code"].isin(bases)].copy()
    out["back_factor"] *= out["code"].map(bases)
    out["adjust_factor"] = out["back_factor"]
    out.attrs["local_snapshot_bases"] = bases
    return out


def fetch_adjust_factors(stock_list: list[tuple]) -> pd.DataFrame:
    """本地自算复权因子（与 bstock.fetch_adjust_factors 同签名）。

    Parameters
    ----------
    stock_list : list of (symbol, market, start_date, end_date, status) tuples，
        日期格式 YYYY-MM-DD（窗口参数仅为签名兼容保留，不参与事件裁剪）。

    Returns
    -------
    DataFrame(code, date, fore_factor, back_factor, adjust_factor)；
    code 为 '000681.SZ' 格式，date 为 'YYYY-MM-DD' 字符串。
    行 = 每股全历史事件链（已按 ADJ_FACTOR 水位校准），未来事件除外。
    """
    targets: dict[str, tuple[str, str, str]] = {}
    for symbol, market, start_date, end_date, status in stock_list:
        symbol = str(symbol).strip()
        market = str(market).strip().upper()
        if symbol.startswith("9") or status == "D":
            continue
        targets[f"{symbol}.{market}"] = (symbol, str(start_date), str(end_date))

    if not targets:
        return _empty_result()

    candidates = pd.DataFrame(
        [(v[0], code) for code, v in sorted(targets.items())],
        columns=["symbol", "code"],
    )

    stats = _new_stats()
    conn = None
    try:
        conn = dbutil.get_connection(is_read_only=True)
        conn.register("temp_candidates", candidates)
        # 累计链必须从该股首个事件起算，事件取全历史、不按窗口裁剪
        logger.info(f"开始读取除权事件及事件日前收盘价（ASOF 下推，候选 {len(candidates)} 只）...")
        with _progress_heartbeat("读取事件及前收盘价"):
            events_df = conn.execute(_EVENT_SQL, [_EVENT_CATEGORY]).fetchdf()
        logger.info(f"事件读取完成：{len(events_df)} 行，涉及 "
                    f"{events_df['code'].nunique() if not events_df.empty else 0} 只股票")

        with _progress_heartbeat("计算因子链"):
            factors = compute_event_factors(events_df, stats=stats)

        # BUG-011：跑批汇总——缺价/缺配股价等被跳过事件的可观测出口
        if stats["skipped"] > 0:
            summary = (f"本次跳过 {stats['skipped']} 条事件，涉及 {len(stats['stocks'])} 只股票"
                       f"（其中区间内部缺口 {stats['gap']} 条，"
                       f"历史覆盖外 {stats['out_of_coverage']} 条）")
            if stats["gap"] > 0:
                logger.error(summary)
            else:
                logger.warning(summary)

        # 不完整事件链不得作为撤销快照写入；历史覆盖外的事件除外。
        if stats["skipped"] > stats["out_of_coverage"]:
            raise RuntimeError("local 事件链不完整，停止写入（缺价/非法配股等）")

        # BUG-006：按 ADJ_FACTOR 末行水位在线校准，保证每日跑批不写回未对齐链
        with _progress_heartbeat("校准水位"):
            factors = _calibrate_to_dense(conn, factors)
    finally:
        if conn is not None:
            try:
                conn.unregister("temp_candidates")
            except Exception:
                pass
            conn.close()

    out = factors.copy()
    if out.empty:
        return out
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    logger.info(f"本地复权因子计算完成：{len(out)} 条事件 / {out['code'].nunique()} 只股票")
    return out
