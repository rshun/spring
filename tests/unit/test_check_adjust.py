# 修改记录:
#   2026-09-06  Claude  新增 check_adjust 对账逻辑的正反测试(DataFrame 注入, 不触库不触网)
#   2026-09-06  Claude  diff_factor_frames 数值比较改为相邻公共事件区间跳变比,
#                       同步改写用例并新增「绝对水位不同不报/区间跳变不一致报」正反例
#   2026-09-06  Claude  BUG-002/BUG-003 回归: 窗口前基准行参与跳变计算但不进集合差、
#                       tx jump_ratio 缺失输出 jump_unverified
#   2026-09-06  Claude  BUG-004/BUG-005 回归: unit_baseline_ok 区分 LOCAL/RAW 语义、
#                       tx_fetched_codes 判定基准、_build_summary 如实汇总
"""tools.check_adjust 纯对比逻辑单元测试(DataFrame 注入, 不触库不触网)"""
import pandas as pd
import pytest

from tools import check_adjust as ca


def _factor(rows):
    """rows: (code, trade_date, back_factor)"""
    return pd.DataFrame(rows, columns=["code", "trade_date", "back_factor"])


def _factorb(rows):
    """rows: (code, trade_date, back_factor, is_baseline)
    is_baseline=1 表示窗口前最近事件(基准行), 由 _load_factor_events 附加"""
    return pd.DataFrame(rows, columns=["code", "trade_date", "back_factor",
                                       "is_baseline"])


def _tx(rows):
    """rows: (code, xdr_date, div_per10, jump_ratio)"""
    df = pd.DataFrame(rows, columns=["code", "xdr_date", "div_per10", "jump_ratio"])
    df["djr"] = None
    df["content"] = None
    return df[["code", "xdr_date", "div_per10", "djr", "content", "jump_ratio"]]


def _gbbq(rows):
    """rows: (code, date, dividend)"""
    return pd.DataFrame(rows, columns=["code", "date", "dividend"])


# ── diff_factor_frames (LOCAL vs RAW) ────────────────────

def test_diff_factor_identical_passes():
    """正例：两表事件完全一致，无差异"""
    f = _factor([("000681.SZ", "2026-06-01", 3.8),
                 ("000681.SZ", "2026-08-03", 3.904298)])
    assert ca.diff_factor_frames(f, f.copy(), 1e-3).empty


def test_diff_factor_set_difference_both_ways():
    """反例：事件集合互相缺失，双向都能捞出(from_date 为事件日)"""
    local = _factor([("000681.SZ", "2026-08-03", 3.9),
                     ("000681.SZ", "2026-06-01", 3.8)])
    raw = _factor([("000681.SZ", "2026-08-03", 3.9),
                   ("600519.SH", "2026-07-01", 2.0)])
    diff = ca.diff_factor_frames(local, raw, 1e-3)
    issues = {(r["code"], r["from_date"]): r["issue"] for _, r in diff.iterrows()}
    assert issues == {
        ("000681.SZ", "2026-06-01"): "missing_in_raw",
        ("600519.SH", "2026-07-01"): "missing_in_local",
    }


def test_diff_factor_different_baseline_passes():
    """正例：两链绝对水位差 1.55 倍(基准不同)但区间跳变一致，不报差异

    对应真实案例: 000681 2026-08-03 local back=2.518889 vs raw=3.904298,
    绝对水位不同不是数据错误, 复权只看相对比值。
    """
    local = _factor([("000681.SZ", "2026-06-01", 2.5),
                     ("000681.SZ", "2026-08-03", 2.5 * 1.02)])
    raw = _factor([("000681.SZ", "2026-06-01", 2.5 * 1.55),
                   ("000681.SZ", "2026-08-03", 2.5 * 1.55 * 1.02)])
    assert ca.diff_factor_frames(local, raw, 1e-3).empty


def test_diff_factor_interval_jump_beyond_tolerance():
    """反例：相邻公共事件的区间跳变比不一致超容差被捞出"""
    local = _factor([("000681.SZ", "2026-06-01", 2.0),
                     ("000681.SZ", "2026-08-03", 2.0 * 1.05)])
    raw = _factor([("000681.SZ", "2026-06-01", 3.0),
                   ("000681.SZ", "2026-08-03", 3.0 * 1.03)])
    diff = ca.diff_factor_frames(local, raw, 1e-3)
    assert len(diff) == 1
    row = diff.iloc[0]
    assert row["issue"] == "interval_jump_diff"
    assert row["from_date"] == "2026-06-01"
    assert row["to_date"] == "2026-08-03"
    assert row["jump_local"] == pytest.approx(1.05)
    assert row["jump_raw"] == pytest.approx(1.03)
    assert row["jump_diff"] == pytest.approx(abs(1.05 / 1.03 - 1))


def test_diff_factor_interval_robust_to_one_sided_gap():
    """正例：区间中间夹着单边缺失事件, 区间跳变仍一致则只报集合差不报数值差

    local 多一个中间事件 t2(missing_in_raw), 公共事件 t1->t3 的区间跳变
    两边一致(1.1 × 1.1 = 1.21), 不报 interval_jump_diff。
    """
    local = _factor([("000681.SZ", "2026-05-10", 0.8),
                     ("000681.SZ", "2026-06-01", 0.8 * 1.1),
                     ("000681.SZ", "2026-08-03", 0.8 * 1.21)])
    raw = _factor([("000681.SZ", "2026-05-10", 2.0),
                   ("000681.SZ", "2026-08-03", 2.0 * 1.21)])
    diff = ca.diff_factor_frames(local, raw, 1e-3)
    assert len(diff) == 1
    assert diff.iloc[0]["issue"] == "missing_in_raw"
    assert diff.iloc[0]["from_date"] == "2026-06-01"


def test_diff_factor_tolerance_boundary():
    """边界：区间跳变比相对误差 > tol 才报, 恰好等于 tol 不报(取 2^-9 规避浮点尾差)"""
    # jump_raw = 1.0, jump_local = 1 + 2^-9, 相对误差恰为 2^-9
    local = _factor([("000681.SZ", "2026-06-01", 2.0),
                     ("000681.SZ", "2026-08-03", 2.0 * (1.0 + 2 ** -9))])
    raw = _factor([("000681.SZ", "2026-06-01", 3.0),
                   ("000681.SZ", "2026-08-03", 3.0)])
    assert ca.diff_factor_frames(local, raw, 2 ** -9).empty
    assert len(ca.diff_factor_frames(local, raw, 0.001)) == 1


def test_diff_factor_single_common_event_no_interval():
    """边界：仅一个公共事件(无相邻对)且绝对水位不同，不报差异"""
    local = _factor([("000681.SZ", "2026-08-03", 2.518889)])
    raw = _factor([("000681.SZ", "2026-08-03", 3.904298)])
    assert ca.diff_factor_frames(local, raw, 1e-3).empty


def test_diff_factor_empty_inputs():
    """反例：空帧输入返回带列名的空差异帧"""
    diff = ca.diff_factor_frames(pd.DataFrame(), None, 1e-3)
    assert diff.empty
    assert list(diff.columns) == ca.DIFF_FACTOR_COLUMNS


# ── compute_factor_jumps ─────────────────────────────────

def test_compute_factor_jumps_chain():
    """正例：首事件基准 1.0，后续事件为相邻 back_factor 之比"""
    df = _factor([("A.SH", "2025-01-10", 1.01),
                  ("A.SH", "2026-02-10", 1.0302),
                  ("B.SZ", "2026-03-01", 2.0)])
    jumps = ca.compute_factor_jumps(df)
    got = {(r["code"], r["trade_date"]): r["jump_ratio"] for _, r in jumps.iterrows()}
    assert got[("A.SH", "2025-01-10")] == pytest.approx(1.01)
    assert got[("A.SH", "2026-02-10")] == pytest.approx(1.0302 / 1.01)
    assert got[("B.SZ", "2026-03-01")] == pytest.approx(2.0)


def test_compute_factor_jumps_empty():
    """反例：空帧输入"""
    jumps = ca.compute_factor_jumps(pd.DataFrame())
    assert jumps.empty
    assert list(jumps.columns) == ["code", "trade_date", "jump_ratio"]


# ── BUG-002: 窗口前基准行参与跳变计算 ─────────────────────

def test_jumps_use_pre_window_baseline():
    """正例(BUG-002 验收)：历史基准在窗口外, 2.0 → 2.02 应得跳变 1.01"""
    df = _factorb([("000681.SZ", "2026-05-09", 2.0, 1),
                   ("000681.SZ", "2026-08-03", 2.02, 0)])
    jumps = ca.compute_factor_jumps(df)
    assert jumps["trade_date"].tolist() == ["2026-08-03"]  # 基准行不进输出
    assert jumps.iloc[0]["jump_ratio"] == pytest.approx(1.01)


def test_jumps_single_window_event_with_baseline():
    """正例：窗口内只有一个事件且有基准，跳变 = back/基准"""
    df = _factorb([("000681.SZ", "2025-11-20", 3.5, 1),
                   ("000681.SZ", "2026-08-03", 3.57, 0)])
    jumps = ca.compute_factor_jumps(df)
    assert len(jumps) == 1
    assert jumps.iloc[0]["jump_ratio"] == pytest.approx(3.57 / 3.5)


def test_jumps_true_first_event_uses_base_one():
    """边界：真正的历史首事件(无基准无前值)，基准 1.0 是正确语义"""
    df = _factorb([("000681.SZ", "2026-08-03", 1.05, 0)])
    jumps = ca.compute_factor_jumps(df)
    assert jumps.iloc[0]["jump_ratio"] == pytest.approx(1.05)


def test_jumps_baseline_per_code_independent():
    """正例：多股票各自独立取基准, 互不串链"""
    df = _factorb([("A.SH", "2026-01-10", 2.0, 1),
                   ("A.SH", "2026-08-03", 2.2, 0),
                   ("B.SZ", "2026-03-01", 5.0, 1),
                   ("B.SZ", "2026-08-03", 5.5, 0),
                   ("C.SH", "2026-08-03", 1.1, 0)])  # C 无基准=历史首事件
    jumps = ca.compute_factor_jumps(df)
    got = {r["code"]: r["jump_ratio"] for _, r in jumps.iterrows()}
    assert got["A.SH"] == pytest.approx(1.1)
    assert got["B.SZ"] == pytest.approx(1.1)
    assert got["C.SH"] == pytest.approx(1.1)  # /1.0


def test_diff_factor_baseline_rows_not_in_set_diff():
    """反例：两帧基准行日期不同(各自窗口前最近事件), 不得混入集合差"""
    local = _factorb([("000681.SZ", "2026-05-09", 2.0, 1),
                      ("000681.SZ", "2026-08-03", 2.02, 0)])
    raw = _factorb([("000681.SZ", "2026-04-01", 3.0, 1),
                    ("000681.SZ", "2026-08-03", 3.03, 0)])
    diff = ca.diff_factor_frames(local, raw, 1e-3)
    assert diff.empty


def test_diff_tx_factor_with_baseline_no_false_alarm():
    """正例(BUG-002 复现案例)：tx 跳变 1.01 vs 因子 2.0→2.02, 修复后零差异"""
    factor = _factorb([("000681.SZ", "2026-05-09", 2.0, 1),
                       ("000681.SZ", "2026-08-03", 2.02, 0)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.01)])
    assert ca.diff_tx_vs_factor(tx, factor, "local", 1e-3).empty


# ── BUG-004: unit_baseline_ok 区分数据源语义 ─────────────

def test_jumps_raw_no_prev_jump_none():
    """反例(BUG-004)：RAW 语义(unit_baseline_ok=False)无前值 → jump_ratio=None"""
    df = _factor([("000681.SZ", "2025-05-09", 2.02)])
    jumps = ca.compute_factor_jumps(df, unit_baseline_ok=False)
    assert len(jumps) == 1  # 事件行保留, 只是跳变不可算
    assert pd.isna(jumps.iloc[0]["jump_ratio"])
    # 有真实前值时照常计算
    df2 = _factor([("000681.SZ", "2024-05-09", 2.0),
                   ("000681.SZ", "2025-05-09", 2.02)])
    jumps2 = ca.compute_factor_jumps(df2, unit_baseline_ok=False)
    assert jumps2.iloc[1]["jump_ratio"] == pytest.approx(1.01)


def test_jumps_local_first_row_uses_unit_baseline():
    """正例(BUG-004)：LOCAL 语义(unit_baseline_ok=True)表内首行即链首, 基准 1.0"""
    df = _factor([("000681.SZ", "2024-05-09", 2.0)])
    jumps = ca.compute_factor_jumps(df, unit_baseline_ok=True)
    assert jumps.iloc[0]["jump_ratio"] == pytest.approx(2.0)


def test_diff_tx_factor_raw_no_baseline_unverified_not_50pct():
    """反例(BUG-004 复现案例)：RAW 无基准时输出 jump_unverified 而非 50% 误报

    bug.md 场景: LOCAL 残缺表仅存锚点 2025-05-09/2.02(累计值), 腾讯跳变 1.01。
    新契约下 LOCAL 不会再残缺; 同形态对 RAW 验证: 无窗口前基准时不得把
    2.02/1.0=2.02 当跳变去和 1.01 比(相对差 50%), 应标记未完成校验。
    """
    factor = _factor([("000001.SZ", "2025-05-09", 2.02)])
    tx = _tx([("000001.SZ", "2025-05-09", 0.1, 1.01)])
    diff = ca.diff_tx_vs_factor(tx, factor, "raw", 1e-3, unit_baseline_ok=False)
    assert len(diff) == 1
    assert diff.iloc[0]["issue"] == "jump_unverified"
    assert pd.isna(diff.iloc[0]["factor_jump_ratio"])


def test_diff_tx_factor_true_first_event_semantics_differ():
    """边界(BUG-004)：真历史首事件, LOCAL 允许 1.0 基准正常比对, RAW 不允许"""
    factor = _factor([("000001.SZ", "2024-05-09", 2.0)])
    tx = _tx([("000001.SZ", "2024-05-09", 0.0, 2.0)])
    # LOCAL: 首行即链首, jump=2.0/1.0=2.0 与腾讯一致
    assert ca.diff_tx_vs_factor(tx, factor, "local", 1e-3,
                                unit_baseline_ok=True).empty
    # RAW: 无前值不可确认是否历史首事件, 标记未完成校验
    diff = ca.diff_tx_vs_factor(tx, factor, "raw", 1e-3, unit_baseline_ok=False)
    assert diff.iloc[0]["issue"] == "jump_unverified"


# ── BUG-005: 成功取数集合作为 missing_in_tx 判定基准 ──────

def test_diff_tx_factor_missing_in_tx_uses_fetched_codes():
    """正例(BUG-005)：成功取数但无事件的股票, 其因子侧事件判 missing_in_tx"""
    factor = _factor([("000681.SZ", "2026-08-03", 1.0002),
                      ("600519.SH", "2026-08-03", 2.0)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    # 600519 成功取数但无事件 -> 其因子事件应被捞出
    diff = ca.diff_tx_vs_factor(tx, factor, "raw", 1e-3,
                                tx_fetched_codes={"000681.SZ", "600519.SH"})
    assert len(diff) == 1
    assert diff.iloc[0]["code"] == "600519.SH"
    assert diff.iloc[0]["issue"] == "event_missing_in_tx"


def test_diff_tx_factor_failed_code_not_flagged_with_fetched_codes():
    """反例(BUG-005)：取数失败的股票不在成功集合内, 不误报 missing_in_tx"""
    factor = _factor([("000681.SZ", "2026-08-03", 1.0002),
                      ("600519.SH", "2026-08-03", 2.0)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    # 600519 取数失败(不在 fetched 集合) -> 其因子事件不报 missing_in_tx
    diff = ca.diff_tx_vs_factor(tx, factor, "raw", 1e-3,
                                tx_fetched_codes={"000681.SZ"})
    assert diff.empty


def test_diff_tx_gbbq_missing_in_tx_uses_fetched_codes():
    """正例(BUG-005 同族)：gbbq 对比同样以成功取数集合判定 missing_in_tx"""
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    gbbq = _gbbq([("000681.SZ", "2026-08-03", 0.04),
                  ("600519.SH", "2026-08-03", 0.5)])
    diff = ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3,
                              tx_fetched_codes={"000681.SZ", "600519.SH"})
    assert len(diff) == 1
    assert diff.iloc[0]["issue"] == "event_missing_in_tx"
    # 600519 取数失败 -> 不报
    diff2 = ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3, tx_fetched_codes={"000681.SZ"})
    assert diff2.empty


# ── diff_tx_vs_factor ────────────────────────────────────

def test_diff_tx_factor_consistent_passes():
    """正例：腾讯事件与因子表事件齐全且跳变一致，无差异"""
    factor = _factor([("000681.SZ", "2026-08-03", 1.0002)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    assert ca.diff_tx_vs_factor(tx, factor, "local", 1e-3).empty


def test_diff_tx_factor_missing_in_factor():
    """反例：腾讯有事件而因子表缺失"""
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    diff = ca.diff_tx_vs_factor(tx, _factor([]), "raw", 1e-3)
    assert diff.iloc[0]["issue"] == "event_missing_in_raw"


def test_diff_tx_factor_missing_in_tx():
    """反例：因子表有事件而腾讯缺失(仅对腾讯成功覆盖的股票判定)"""
    factor = _factor([("000681.SZ", "2026-08-03", 1.0002)])
    tx = _tx([("000681.SZ", "2026-06-01", 0.5, 1.005)])
    diff = ca.diff_tx_vs_factor(tx, factor, "local", 1e-3)
    issues = {(r["code"], r["xdr_date"]): r["issue"] for _, r in diff.iterrows()}
    assert issues[("000681.SZ", "2026-08-03")] == "event_missing_in_tx"
    assert issues[("000681.SZ", "2026-06-01")] == "event_missing_in_local"


def test_diff_tx_factor_uncovered_code_not_flagged():
    """边界：腾讯拉取失败的股票(不在 tx 帧中)不误报 event_missing_in_tx"""
    factor = _factor([("600519.SH", "2026-08-03", 2.0)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    diff = ca.diff_tx_vs_factor(tx, factor, "raw", 1e-3)
    assert not ((diff["code"] == "600519.SH") &
                (diff["issue"] == "event_missing_in_tx")).any()


def test_diff_tx_factor_jump_ratio_beyond_tolerance():
    """反例：同日事件跳变比例相对误差超容差"""
    factor = _factor([("000681.SZ", "2026-08-03", 1.030)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.010)])
    diff = ca.diff_tx_vs_factor(tx, factor, "local", 1e-3)
    assert len(diff) == 1
    assert diff.iloc[0]["issue"] == "jump_ratio_diff"
    assert diff.iloc[0]["rel_diff"] == pytest.approx(abs(1.010 - 1.030) / 1.030)


def test_diff_tx_factor_jump_tolerance_boundary():
    """边界：跳变相对误差恰好等于容差不报(取 2^-9 规避浮点尾差)"""
    factor = _factor([("000681.SZ", "2026-08-03", 1.0)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0 + 2 ** -9)])
    assert ca.diff_tx_vs_factor(tx, factor, "local", 2 ** -9).empty
    assert len(ca.diff_tx_vs_factor(tx, factor, "local", 0.001)) == 1


def test_diff_tx_factor_both_empty():
    """反例：两边均无事件，无差异"""
    diff = ca.diff_tx_vs_factor(_tx([]), _factor([]), "raw", 1e-3)
    assert diff.empty
    assert list(diff.columns) == ca.DIFF_TX_FACTOR_COLUMNS


def test_diff_tx_factor_jump_unverified_not_silent():
    """反例(BUG-003)：tx jump_ratio 缺失而因子侧有事件, 输出 jump_unverified

    不得静默跳过(呈现为一致), 也不算数值差异; factor 故意设错为 99.0
    时依然只报 jump_unverified(没有比较依据, 不做数值判定)。
    """
    factor = _factor([("000681.SZ", "2026-08-03", 99.0)])
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, None)])
    diff = ca.diff_tx_vs_factor(tx, factor, "local", 1e-3)
    assert len(diff) == 1
    row = diff.iloc[0]
    assert row["issue"] == "jump_unverified"
    assert row["source"] == "local"
    assert pd.isna(row["tx_jump_ratio"])
    assert row["factor_jump_ratio"] == pytest.approx(99.0)


# ── diff_tx_vs_gbbq ──────────────────────────────────────

def test_diff_tx_gbbq_dividend_consistent_passes():
    """正例：分红额一致(每10股口径)，无差异"""
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    gbbq = _gbbq([("000681.SZ", "2026-08-03", 0.04)])
    assert ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3).empty


def test_diff_tx_gbbq_dividend_diff():
    """反例：分红额差异(§5.2 类源间分歧)"""
    tx = _tx([("600157.SH", "2024-11-15", 0.1, 1.006)])
    gbbq = _gbbq([("600157.SH", "2024-11-15", 0.0556)])
    diff = ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3)
    assert diff.iloc[0]["issue"] == "dividend_diff"


def test_diff_tx_gbbq_dividend_one_side_missing():
    """反例：腾讯无分红额而 gbbq 有，记 dividend_diff"""
    tx = _tx([("000681.SZ", "2026-08-03", None, 1.0002)])
    gbbq = _gbbq([("000681.SZ", "2026-08-03", 0.5)])
    diff = ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3)
    assert diff.iloc[0]["issue"] == "dividend_diff"


def test_diff_tx_gbbq_pure_bonus_no_dividend_passes():
    """边界：纯送转事件两边都无分红额，属正常不报"""
    tx = _tx([("000681.SZ", "2026-08-03", None, 1.5)])
    gbbq = _gbbq([("000681.SZ", "2026-08-03", 0.0)])
    assert ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3).empty


def test_diff_tx_gbbq_event_missing_both_ways():
    """反例：事件集合互相缺失"""
    tx = _tx([("000681.SZ", "2026-08-03", 0.04, 1.0002)])
    gbbq = _gbbq([("000681.SZ", "2026-06-01", 0.5)])
    diff = ca.diff_tx_vs_gbbq(tx, gbbq, 1e-3)
    issues = set(diff["issue"])
    assert issues == {"event_missing_in_gbbq", "event_missing_in_tx"}


# ── BUG-005: _build_summary 如实汇总 ─────────────────────

def test_summary_all_consistent():
    """正例：全部校验完成且零差异 → 正常 OK"""
    msg, warn = ca._build_summary(0, [])
    assert "全部一致 OK" in msg
    assert warn is False


def test_summary_tx_all_failed_never_all_consistent():
    """反例(BUG-005 复现案例)：腾讯全部失败, 汇总不得含「全部一致」"""
    msg, warn = ca._build_summary(0, ["腾讯对账未完成(3 只取数失败)"])
    assert "全部一致" not in msg
    assert "腾讯对账未完成(3 只取数失败)" in msg
    assert warn is True


def test_summary_tx_partial_failure_shows_count():
    """反例(BUG-005)：部分取数失败, 汇总体现失败数"""
    msg, warn = ca._build_summary(0, ["腾讯部分股票取数失败(2/5 只)"])
    assert "全部一致" not in msg
    assert "2/5" in msg
    assert warn is True


def test_summary_success_no_events_not_incomplete():
    """正例(BUG-005)：成功取数但无事件不属于未完成项, 与失败区分"""
    # 成功无事件不产生 incomplete 项; 若其他对比也无差异 → 正常 OK
    msg, warn = ca._build_summary(0, [])
    assert "全部一致 OK" in msg
    assert warn is False


def test_summary_diffs_and_incomplete_both_shown():
    """反例：有差异且有未完成项, 两者都要出现在汇总里"""
    msg, warn = ca._build_summary(7, ["腾讯部分股票取数失败(1/9 只)"])
    assert "7 条差异" in msg
    assert "1/9" in msg
    assert warn is True


@pytest.mark.parametrize("local_ok,raw_ok", [(True, True), (False, True), (True, False), (False, False)])
def test_main_skips_unavailable_sources(monkeypatch, caplog, local_ok, raw_ok):
    import logging
    from types import SimpleNamespace
    from datasource import txstock
    frame = _factorb([("000001.SZ", "2026-08-03", 3.9, 0)])
    monkeypatch.setattr(ca, "parse_arguments", lambda: SimpleNamespace(
        begin="20260803", end="20260803", codes=None, tolerance=0.001))
    monkeypatch.setattr(ca, "check_parameters", lambda *a: True)
    monkeypatch.setattr(ca.myutil, "configure_etl_logging", lambda: None)
    monkeypatch.setattr(ca.dbutil, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ca, "_load_factor_events", lambda conn, table, *a:
                        frame.copy() if (local_ok if table == "ADJ_FACTOR_LOCAL" else raw_ok) else None)
    monkeypatch.setattr(ca, "_load_gbbq_events", lambda *a: pd.DataFrame())
    monkeypatch.setattr(txstock, "fetch_xdr_events", lambda *a:
                        (_tx([("000001.SZ", "2026-08-03", 1.0, 1.01)]), []))
    reports = {}
    monkeypatch.setattr(ca, "_report_diffs", lambda title, key, b, e, df:
                        reports.setdefault(key, df) is None and 0 or 0)
    with caplog.at_level(logging.INFO):
        assert ca.main() == 0
    assert ("local_vs_raw" in reports) == (local_ok and raw_ok)
    assert ("tx_vs_raw" in reports) == raw_ok
    assert ("tx_vs_local" in reports) == local_ok
    if not raw_ok:
        assert "ADJ_FACTOR_RAW 不可用" in caplog.text
    if not local_ok:
        assert "ADJ_FACTOR_LOCAL 不可用" in caplog.text
    if not (raw_ok and local_ok):
        assert "全部一致 OK" not in caplog.text
    if local_ok:
        assert "jump_unverified" in reports["tx_vs_local"].to_string()
        assert "jump_ratio_diff" not in reports["tx_vs_local"].to_string()



@pytest.mark.parametrize("explicit", [True, False])
def test_missing_both_factor_sources_still_queries_independent_codes(monkeypatch, explicit):
    from types import SimpleNamespace
    from datasource import txstock
    empty = _factorb([])
    monkeypatch.setattr(ca, "parse_arguments", lambda: SimpleNamespace(
        begin="20260803", end="20260803", codes=["000001"] if explicit else None, tolerance=0.001))
    monkeypatch.setattr(ca, "check_parameters", lambda *a: True)
    monkeypatch.setattr(ca.myutil, "configure_etl_logging", lambda: None)
    monkeypatch.setattr(ca.dbutil, "get_connection", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(ca, "_load_factor_events", lambda *a: empty)
    monkeypatch.setattr(ca, "_load_gbbq_events", lambda *a: pd.DataFrame(
        [] if explicit else [("000001.SZ", "2026-08-03", 1.0)], columns=["code", "date", "dividend"]))
    calls = []
    def fetch(codes, *args):
        calls.append(codes)
        return _tx([("000001.SZ", "2026-08-03", 1.0, 2.0)]), []
    monkeypatch.setattr(txstock, "fetch_xdr_events", fetch)
    issues = []
    def report(title, key, b, e, frame):
        issues.extend(frame["issue"].tolist())
        return len(frame)
    monkeypatch.setattr(ca, "_report_diffs", report)
    assert ca.main() == 0
    assert calls == [["000001.SZ"]]
    assert "event_missing_in_local" in issues
    assert "event_missing_in_raw" in issues
