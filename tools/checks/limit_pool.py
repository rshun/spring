# 修改记录:
#   2026-09-14  Claude  ST 股的「库内多标」单列不计差异: akshare 涨跌停池不收 ST,
#                       两侧股票池范围不同而非数据错(实测非 ST 部分 55/16 完全匹配)
#   2026-09-12  Claude  新建涨跌停一致性核对项(LIMIT_POOL_DAILY vs DAILY_BASIC)
#   2026-09-13  Claude  num_close 返回 None(一侧缺失/无法比较)此前被静默丢弃, 与
#                       「比过了且一致」无法区分; 现累计计数, 结尾打一条聚合 WARNING
"""涨跌停一致性核对

三组比较，都只用库内已有字段，不推算任何涨跌幅比例
（比例口径随日期、板块、ST 状态漂移，拿它当核对基准等于引入新的易错点）：

  组 1 标志一致性: 池内集合 vs DAILY_BASIC.is_limit_up/down = 1 集合(双向)
  组 2 涨跌停价:   池内最新价 应等于 DAILY_BASIC.limit_up/limit_down
  组 3 抽样数值:   换手率 / 流通市值 / 总市值

组 2 是对 etl/update_limit.py 所算涨跌停价的直接第三方验证：
东财称该股今日涨停、收在 X 元，则 X 就是当日涨停价。

源可用性必须按 (trade_date, limit_type) 判定：一张表装两个池，
当日可能有涨停行而无跌停行，按整表判会把库内每条跌停标记误报成「多标」。

ST 股的「库内多标」单列不计差异：实测 akshare 涨跌停池(东财口径)不收 ST 股 ——
2026-09-14 池内 55 涨/16 跌，与库内**非 ST** 的 55/16 完全相等，而库内另有 4 只
ST(2 涨 2 跌)池内一只不收。这是两侧股票池范围不同，不是数据错。
不直接把 ST 排除出比对，是为了不制造盲区：万一池子口径变了收了 ST，
「库内漏标」那一侧仍须能查出来。故只把 ST 的「多标」改标签并移出差异计数，
仍写入 CSV 留痕。
注意样本仅两个交易日(LIMIT_POOL_DAILY 2026-09-12 才建表)，结论待更长历史验证。
"""
import duckdb

from util import checker

# 价格本质是两位小数的确定值，1e-6 只留浮点余量
PRICE_REL_TOL = 1e-6
# 抽样字段来自不同口径的计算链，留 1e-3
SAMPLE_REL_TOL = 1e-3

_SAMPLE_FIELDS = ("turnover_rate", "float_mv", "total_mv")

_SPEC = {
    "U": {"label": "涨停核对", "tag": "limit_up",
          "flag": "is_limit_up", "price": "limit_up",
          "missing": "库内漏标涨停", "extra": "库内多标涨停",
          "extra_st": "库内多标涨停(ST，外部池不收，非差异)",
          "price_issue": "涨停价不符"},
    "D": {"label": "跌停核对", "tag": "limit_down",
          "flag": "is_limit_down", "price": "limit_down",
          "missing": "库内漏标跌停", "extra": "库内多标跌停",
          "extra_st": "库内多标跌停(ST，外部池不收，非差异)",
          "price_issue": "跌停价不符"},
}


def _load_pool(conn, dt, limit_type, ex_filter, code_filter, code_params):
    sql = f"""
    SELECT p.code, p.close, p.turnover_rate, p.float_mv, p.total_mv
    FROM LIMIT_POOL_DAILY p
    INNER JOIN STOCK_INFO i ON i.code = p.code
    WHERE p.trade_date = ? AND p.limit_type = ? {ex_filter} {code_filter}
    """
    rows = conn.execute(sql, [dt, limit_type] + list(code_params)).fetchall()
    return {r[0]: {"close": r[1], "turnover_rate": r[2],
                   "float_mv": r[3], "total_mv": r[4]} for r in rows}


def _load_basic(conn, dt, spec, ex_filter, code_filter, code_params):
    # is_st 单独用不可靠(实测全库有大量 NULL), 叠加名称前缀兜底
    sql = f"""
    SELECT b.code, b.{spec['flag']}, b.{spec['price']},
           b.turnover_rate, b.float_mv, b.total_mv,
           CASE WHEN COALESCE(b.is_st, 0) = 1
                  OR i.name LIKE '%ST%' THEN 1 ELSE 0 END AS is_st
    FROM DAILY_BASIC b
    INNER JOIN STOCK_INFO i ON i.code = b.code
    WHERE b.trade_date = ? {ex_filter} {code_filter}
    """
    rows = conn.execute(sql, [dt] + list(code_params)).fetchall()
    return {r[0]: {"flag": r[1], "price": r[2], "turnover_rate": r[3],
                   "float_mv": r[4], "total_mv": r[5], "is_st": r[6]}
            for r in rows}


def _compare_one_date(conn, dt, limit_type, spec, ex_filter, code_filter,
                      code_params) -> tuple[list[dict], list[dict], int]:
    """返回 (差异行, ST 多标行, 因一侧缺失/无法比较而跳过的数值比较次数)

    ST 多标行不是差异——外部池本就不收 ST(见模块 docstring)，它只进 CSV 留痕，
    不计入 result.count，也不影响 status。
    末项也不是差异——它是「没法比」，同样不进 rows，
    单独统计后由调用方聚合成一条 WARNING(不逐条输出)。
    """
    pool = _load_pool(conn, dt, limit_type, ex_filter, code_filter, code_params)
    basic = _load_basic(conn, dt, spec, ex_filter, code_filter, code_params)
    flagged = {c for c, v in basic.items() if v["flag"] == 1}

    rows: list[dict] = []
    st_rows: list[dict] = []
    uncomputable = 0

    # 组 1: 标志集合差(双向)
    only_pool, only_db = checker.set_diff(set(pool), flagged)
    rows.extend({"date": dt, "code": c, "issue": spec["missing"]} for c in only_pool)
    # ST 的「多标」是两侧股票池范围不同所致(见模块 docstring), 改标签且不计入差异,
    # 但仍进 CSV —— 一旦池子口径变化, 这批的消失或非 ST 的出现都还看得见。
    for code in only_db:
        is_st = basic.get(code, {}).get("is_st", 0) == 1
        (st_rows if is_st else rows).append(
            {"date": dt, "code": code,
             "issue": spec["extra_st"] if is_st else spec["extra"]})

    # 组 2 + 组 3: 只对两边都有记录的股票比数值
    for code in sorted(set(pool) & set(basic)):
        p, b = pool[code], basic[code]

        same_price = checker.num_close(p["close"], b["price"], PRICE_REL_TOL)
        if same_price is False:
            rows.append({"date": dt, "code": code,
                         "issue": f"{spec['price_issue']}: 池 {p['close']} / 库 {b['price']}"})
        elif same_price is None:
            uncomputable += 1

        for field in _SAMPLE_FIELDS:
            same = checker.num_close(p[field], b[field], SAMPLE_REL_TOL)
            if same is False:
                rows.append({"date": dt, "code": code,
                             "issue": f"{field} 不符: 池 {p[field]} / 库 {b[field]}"})
            elif same is None:
                uncomputable += 1
    return rows, st_rows, uncomputable


def check_limit_pool(conn: duckdb.DuckDBPyConnection,
                     trade_dates: list[str],
                     begin: str, end: str,
                     limit_type: str,
                     ex_filter: str, code_filter: str,
                     code_params: list) -> checker.CheckResult:
    """涨停(U) 或 跌停(D) 一致性核对；只告警不阻断"""
    if limit_type not in _SPEC:
        raise ValueError(f"limit_type 只能是 U 或 D，收到: {limit_type!r}")
    spec = _SPEC[limit_type]

    result = checker.CheckResult(label=spec["label"], blocking=False)
    # 规则 2: 必须带 limit_type 判源
    has_dates, missing_dates = checker.split_dates_by_source(
        conn, "LIMIT_POOL_DAILY", trade_dates,
        extra_where="limit_type = ?", extra_params=[limit_type])
    result.missing_dates = missing_dates

    rows: list[dict] = []
    st_rows: list[dict] = []
    uncomputable_total = 0
    for dt in has_dates:
        dt_rows, dt_st_rows, dt_uncomputable = _compare_one_date(
            conn, dt, limit_type, spec, ex_filter, code_filter, code_params)
        rows.extend(dt_rows)
        st_rows.extend(dt_st_rows)
        uncomputable_total += dt_uncomputable

    result.rows = rows
    result.count = len(rows)
    result.status = checker.resolve_status(has_dates, missing_dates, len(rows))

    if missing_dates:
        checker.logger.warning(
            f"[{spec['label']}] LIMIT_POOL_DAILY({limit_type}) "
            f"以下日期无数据，未核对: {missing_dates}")
    if uncomputable_total:
        checker.logger.warning(
            f"[{spec['label']}] 有 {uncomputable_total} 个数值因一侧缺失(NULL/NaN)"
            f"无法比较，未计入差异")
    if st_rows:
        # 聚合成一条, 不逐条刷屏——每个交易日都会有几只, 逐条输出会淹没真告警
        checker.logger.info(
            f"[{spec['label']}] {len(st_rows)} 只 ST 股库内已标而外部池未收"
            f"(池不含 ST, 非差异)，明细见 CSV")
    checker.log_rows(spec["label"],
                     [f"{r['date']}  {r['code']}  {r['issue']}" for r in rows])
    # ST 行一并写 CSV 留痕: 池子口径一旦变化, 这批的消失或非 ST 的出现都还看得见
    result.csv_path = checker.write_diff_csv(
        spec["tag"], begin, end, ["date", "code", "issue"],
        [(r["date"], r["code"], r["issue"]) for r in rows + st_rows])
    return result
