# 修改记录:
#   2026-08-19  Claude  main() 返回退出码(0成功/1失败)并由 sys.exit 传出，供外部判定成败
#   2026-08-19  Claude  拆出 build_parser()，供 tools/describe_cli.py 自省参数
#   2026-08-19  Claude  写连接延后到下载完成之后，下载期间不持写锁，便于卡死时安全 kill
#   2026-09-07  Claude  只传起止日期时改走 bstock 按交易日接口；新增 --by-date 开关，
#                       把该路由暴露到 CLI 自省出口(契约 C4)并支持强制开关
#   2026-09-07  Claude  新增 -s local（本地自算复权因子，事件写 ADJ_FACTOR_LOCAL）；
#                       新增 --densify 开关(auto: local 稠密化 / bstock 只留痕写 RAW)；
#                       local 源强制不走 by-date 按日接口
#   2026-09-06  Claude  第二轮审查：BUG-012① local 路径写事件后顺带清除 LOCAL 未来日期
#                       事件；BUG-015 gbbq 无事件但稠密表已有非1.0因子的股票跳过稠密化
#                       防重置为 1.0
#   2026-09-09  Claude  code review 修复：local 源空帧(候选全为9开头/退市)不再退到 legacy
#                       稠密化写 1.0；快照路径 requested 套用与 local_xdr 相同的过滤，消除
#                       每次全市场跑 344 只北交所的假告警；ROLLBACK/unregister 加异常保护
#                       防吞原异常；by-date 分支的模块方法守卫改为检查实际调用的方法
import argparse
import duckdb
import logging
import pandas as pd
import sys
from util import myutil, dbutil
from util import validators as pv

logger = logging.getLogger("etl.adjust")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A股复权因子入库 (支持多源、多代码)"
    )

    parser.add_argument(
        '-b', '--begin',
        type=str,
        default=myutil.get_today(),
        help='指定交易日期 (格式: YYYYMMDD)，默认为当天'
    )

    parser.add_argument(
        '-e', '--end',
        type=str,
        default=myutil.get_today(),
        help='截止日期 (格式: YYYYMMDD),默认当天'
    )

    parser.add_argument(
        '-c', '--codes',
        nargs='+',
        help='指定股票代码列表 (例如: 600519,000001)，不传则默认处理全量'
    )

    parser.add_argument(
        '-x', '--exchanges', nargs='+',
        default=['all'],
        type=str.lower,
        choices=['sh', 'sz', 'bj', 'all'],
        help='指定交易所范围: sh (沪), sz (深), bj (北), all (默认全部)'
    )

    parser.add_argument(
        '-s', '--source',
        type=str,
        choices=['bstock', 'local'],
        default='bstock',
        help='指定数据源类型: bstock=baostock下载(留痕写RAW), '
             'local=本地自算(CAPITAL_DETAIL+STOCK_DAILY, 写ADJ_FACTOR_LOCAL), (默认 bstock)'
    )

    parser.add_argument(
        '--by-date',
        type=str.lower,
        choices=['auto', 'on', 'off'],
        default='auto',
        help='bstock 按交易日整市场下载(query_daily_adjust_factor): '
             'auto=仅当命令行只给出 -b/-e 时启用(默认), on=强制启用, off=强制走逐股接口；'
             '仅对 bstock 源有效, local 源强制不走按日接口'
    )

    parser.add_argument(
        '--densify',
        type=str.lower,
        choices=['auto', 'on', 'off'],
        default='auto',
        help='是否稠密化写入 ADJ_FACTOR 逐日表: '
             'auto=local 源开 / bstock 源关(默认, bstock 只留痕写RAW), '
             'on=强制稠密化, off=只写事件表'
    )

    return parser


def resolve_by_date(mode: str, source: str = 'bstock') -> bool:
    """决定是否走 bstock 的按交易日接口。

    local 源是本地整链计算，没有按日整市场接口，一律强制 off。
    auto 的判定是「命令行上除 -b/-e 外没有显式给出任何参数」。显式参数用一个
    default 全部置 None 的探针 parser 重解析 argv 得到——它与主 parser 同源，
    缩写、--begin=X 等写法的解析结果天然一致，不需要另行维护一份参数表。
    """
    if source != 'bstock':
        return False
    if mode != 'auto':
        return mode == 'on'

    probe = build_parser()
    for action in probe._actions:
        action.default = None
    given = {dest for dest, value in vars(probe.parse_args()).items() if value is not None}
    given.discard('by_date')          # 显式写 --by-date auto 不应否定 auto 自身
    given.discard('densify')          # 稠密化开关与按日路由正交，同理剔除
    return given == {'begin', 'end'}


def resolve_densify(mode: str, source: str) -> bool:
    """决定是否稠密化写入 ADJ_FACTOR 逐日表。

    auto 语义: local 源自算整链是主源，必须稠密化(on)；bstock 降级为留痕层，
    只写 ADJ_FACTOR_RAW 不再喂稠密表(off)。on/off 显式强制。
    """
    if mode != 'auto':
        return mode == 'on'
    return source == 'local'


def parse_arguments() -> argparse.Namespace:
    args = build_parser().parse_args()
    args.date_range_only = resolve_by_date(args.by_date, args.source)
    return args


# 事件表路由白名单：表名需拼进 SQL（参数绑定不支持表名），必须限定合法取值
_EVENT_TABLES = ("ADJ_FACTOR_RAW", "ADJ_FACTOR_LOCAL")



def _save_local_snapshot(frame, stock_list, conn, densify):
    """只接受 local_xdr 已校验的完整快照，固定基准、撤销及历史重算原子提交。"""
    bases = frame.attrs["local_snapshot_bases"]
    targets = pd.DataFrame([
        (f"{str(s).strip()}.{str(m).strip().upper()}", b, e, bases[f"{str(s).strip()}.{str(m).strip().upper()}"])
        for s, m, b, e, *_ in stock_list
        if f"{str(s).strip()}.{str(m).strip().upper()}" in bases
    ], columns=["code", "start_date", "end_date", "base_factor"]).drop_duplicates("code")
    # 与 local_xdr.fetch_adjust_factors 同一套过滤：9 开头/退市股本就不在计算范围，
    # 不能把它们当「基准无法验证」告警出来——全市场跑会列出 344 只北交所，淹没真告警
    requested = {
        f"{str(s).strip()}.{str(m).strip().upper()}"
        for s, m, _b, _e, status, *_ in stock_list
        if not str(s).strip().startswith("9") and status != "D"
    }
    if requested - set(bases):
        logger.warning("%s 无可信完整快照，跳过稠密化（不重置为 1.0）", sorted(requested - set(bases)))
    if targets.empty:
        logger.warning("无已验证完整的 local 快照，本次不写入")
        return
    conn.register("local_snapshot_targets", targets)
    conn.register("local_snapshot_events", frame)
    try:
        conn.execute("BEGIN")
        changed = conn.execute("""
            SELECT DISTINCT t.code FROM local_snapshot_targets t
            LEFT JOIN ADJ_FACTOR_LOCAL_STATE s USING(code)
            WHERE s.code IS NULL
            UNION
            SELECT COALESCE(e.code,a.code) AS code
            FROM local_snapshot_events e FULL OUTER JOIN
                (SELECT * FROM ADJ_FACTOR_LOCAL WHERE code IN (SELECT code FROM local_snapshot_targets)) a
            ON e.code=a.code AND CAST(e.date AS DATE)=a.trade_date
            WHERE e.code IS NULL OR a.code IS NULL
                OR e.fore_factor IS DISTINCT FROM a.fore_factor
                OR e.back_factor IS DISTINCT FROM a.back_factor
        """).fetchdf()
        conn.register("local_snapshot_changed", changed)
        # 防止读取与写入之间另一跑批改变固定基准。
        conflicts = conn.execute("""
            SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL_STATE s JOIN local_snapshot_targets t USING(code)
            WHERE s.base_factor != t.base_factor
        """).fetchone()[0]
        if conflicts:
            raise RuntimeError("local 基准在取数后发生变化，拒绝覆盖")
        conn.execute("""
            INSERT INTO ADJ_FACTOR_LOCAL_STATE(code,base_factor,updated_at)
            SELECT code,base_factor,now() FROM local_snapshot_targets
            ON CONFLICT(code) DO UPDATE SET updated_at=EXCLUDED.updated_at
        """)
        conn.execute("""
            DELETE FROM ADJ_FACTOR_LOCAL WHERE code IN (SELECT code FROM local_snapshot_targets)
            AND NOT EXISTS (SELECT 1 FROM local_snapshot_events e
                            WHERE e.code=ADJ_FACTOR_LOCAL.code AND CAST(e.date AS DATE)=trade_date)
        """)
        conn.execute("""
            INSERT INTO ADJ_FACTOR_LOCAL(code,trade_date,fore_factor,back_factor,adjust_factor,updated_at)
            SELECT code,CAST(date AS DATE),fore_factor,back_factor,adjust_factor,now() FROM local_snapshot_events
            WHERE code IN (SELECT code FROM local_snapshot_targets)
            ON CONFLICT(code,trade_date) DO UPDATE SET
                fore_factor=EXCLUDED.fore_factor,back_factor=EXCLUDED.back_factor,
                adjust_factor=EXCLUDED.adjust_factor,updated_at=EXCLUDED.updated_at
        """)
        if densify:
            conn.execute("""
                INSERT OR REPLACE INTO ADJ_FACTOR
                    (code,trade_date,fore_factor,back_factor,adjust_factor,updated_at)
                WITH dates AS (
                    SELECT a.code,a.trade_date FROM ADJ_FACTOR a JOIN local_snapshot_changed t USING(code)
                    UNION
                    SELECT t.code,c.cal_date FROM local_snapshot_targets t JOIN TRADE_CAL c
                    ON c.is_open=1 AND c.cal_date BETWEEN CAST(t.start_date AS DATE)
                        AND LEAST(CAST(t.end_date AS DATE),CURRENT_DATE)
                ), tails AS (
                    SELECT code,arg_max(back_factor,trade_date) AS last_back
                    FROM ADJ_FACTOR_LOCAL GROUP BY code
                ), values_at_date AS (
                    SELECT d.code,d.trade_date,e.back_factor
                    FROM dates d ASOF LEFT JOIN ADJ_FACTOR_LOCAL e
                    ON d.code=e.code AND d.trade_date>=e.trade_date
                )
                SELECT v.code,v.trade_date,
                    COALESCE(v.back_factor,t.base_factor)/COALESCE(l.last_back,t.base_factor),
                    COALESCE(v.back_factor,t.base_factor),COALESCE(v.back_factor,t.base_factor),now()
                FROM values_at_date v JOIN local_snapshot_targets t USING(code)
                LEFT JOIN tails l USING(code)
            """)
        conn.execute("COMMIT")
    except Exception:
        # ROLLBACK / unregister 自身失败不得吞掉原异常，与 legacy 路径保持一致
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        for name in ("local_snapshot_changed", "local_snapshot_events", "local_snapshot_targets"):
            try:
                conn.unregister(name)
            except Exception:
                pass


def process_and_save_adjust_factors(
    adjust_df: pd.DataFrame,
    stock_list: list[tuple],
    conn: duckdb.DuckDBPyConnection,
    event_table: str = "ADJ_FACTOR_RAW",
    densify: bool = True,
) -> None:
    """
    处理已有的复权因子数据：事件入库并按股票稠密化。
    规则：
      - 源数据 adjust_df 只提供"复权因子发生变化的日期及复权因子"（稀疏事件）。
      - 事件写入 event_table 指定的事件表：local 源 → ADJ_FACTOR_LOCAL，
        bstock 源 → ADJ_FACTOR_RAW（留痕）。
      - densify=True 时逐日表 ADJ_FACTOR 必须覆盖交易日；当日无事件值则取 T-1
        （向前填充），且 ASOF 事件源与 event_table 同表。
      - densify=False 时只写事件表，跳过稠密化。
      - 新股：若历史从未出现过事件值，则默认因子为 1.0，并从 start_date 起补齐到 end/today。
      - local 源附加规则：写事件后顺带清除 ADJ_FACTOR_LOCAL 中未来日期的预告事件
        （BUG-012①）；「gbbq 无除权事件但 ADJ_FACTOR 已有非 1.0 因子」的候选股跳过
        稠密化，防止被静默重置为 1.0（BUG-015，疑似 CAPITAL_DETAIL 未覆盖）。
    """
    if event_table not in _EVENT_TABLES:
        raise ValueError(f"event_table 必须是 {_EVENT_TABLES} 之一，收到: {event_table!r}")

    if event_table == "ADJ_FACTOR_LOCAL" and adjust_df is not None and "local_snapshot_bases" in adjust_df.attrs:
        return _save_local_snapshot(adjust_df, stock_list, conn, densify)

    # local_xdr 把候选全部过滤掉（9 开头/退市）时返回不带 attrs 的空帧：既无事件也无
    # 已校验基准，不得退到 legacy 稠密化把这些股票静默写成 1.0（绕过快照路径的
    # 「无可信基准即跳过」原则）。非空的无 attrs 帧仍走 legacy（BUG-015 守卫在那里）。
    if event_table == "ADJ_FACTOR_LOCAL" and (adjust_df is None or adjust_df.empty):
        logger.warning("local 源未返回任何事件且无已校验快照（候选可能全为 9 开头/退市股），"
                       "本次不写 ADJ_FACTOR。")
        return

    targets: list[dict[str, str]] = []
    for symbol, exchange, start_d, end_d, *_ in stock_list:
        sym = str(symbol).strip()
        ex = str(exchange).strip().upper()
        code = f"{sym}.{ex}"

        s = str(start_d).strip()
        e = str(end_d).strip()
        if s and e:
            targets.append({"code": code, "start_date": s, "end_date": e})

    if not targets:
        logger.warning("stock_list 为空或无有效区间，跳过复权因子处理。")
        return

    targets_df = pd.DataFrame(targets)

    need_cols = ["code", "date", "fore_factor", "back_factor", "adjust_factor"]
    if adjust_df is None or adjust_df.empty:
        adj_raw = pd.DataFrame(columns=need_cols)
    else:
        missing = [c for c in need_cols if c not in adjust_df.columns]
        if missing:
            raise ValueError(f"adjust_df 缺少列: {missing}")

        adj_raw = adjust_df[need_cols].copy()
        adj_raw["code"] = adj_raw["code"].astype(str)
        adj_raw["date"] = adj_raw["date"].astype(str)

        # 同 code+date 重复：保留最后一条
        adj_raw = (adj_raw
                   .sort_values(["code", "date"])
                   .drop_duplicates(["code", "date"], keep="last"))

    # BUG-015：local 源对「本次无事件、但 ADJ_FACTOR 已有非 1.0 因子」的候选股
    # 跳过稠密化——gbbq 缺该股事件可能是 sync_capital 未覆盖而非真的没除过权，
    # 直接稠密化会把存量因子静默重置为 1.0
    if densify and event_table == "ADJ_FACTOR_LOCAL":
        codes_with_events = set(adj_raw["code"]) if not adj_raw.empty else set()
        no_event_codes = sorted({t["code"] for t in targets} - codes_with_events)
        if no_event_codes:
            placeholders = ", ".join(["?"] * len(no_event_codes))
            risky_codes = [r[0] for r in conn.execute(
                f"""
                SELECT DISTINCT code FROM ADJ_FACTOR
                WHERE code IN ({placeholders})
                  AND ABS(adjust_factor - 1.0) > 1e-9
                ORDER BY code
                """,
                no_event_codes,
            ).fetchall()]
            if risky_codes:
                logger.warning(
                    f"以下 {len(risky_codes)} 只股票 gbbq 无除权事件但 ADJ_FACTOR 已有"
                    f"非 1.0 因子，疑似 CAPITAL_DETAIL 未覆盖，跳过其稠密化"
                    f"（不重置为 1.0）: {risky_codes}"
                )
                targets = [t for t in targets if t["code"] not in set(risky_codes)]
        if not targets:
            logger.warning("候选股票全部被 BUG-015 防重置规则跳过，本次不写 ADJ_FACTOR。")
            return
        targets_df = pd.DataFrame(targets)

    try:
        conn.register("temp_targets", targets_df)
        conn.register("temp_adj_raw", adj_raw)

        conn.execute("BEGIN")
        if not adj_raw.empty:
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {event_table}
                    (code, trade_date, fore_factor, back_factor, adjust_factor, updated_at)
                SELECT
                    code,
                    CAST(date AS DATE),
                    CAST(fore_factor AS DOUBLE),
                    CAST(back_factor AS DOUBLE),
                    CAST(adjust_factor AS DOUBLE),
                    now()
                FROM temp_adj_raw;
                """
            )

        # BUG-012①：预告的未来除权事件不提前入 LOCAL（局部、幂等，随每次跑批自清）
        if event_table == "ADJ_FACTOR_LOCAL":
            future_cnt = conn.execute(
                "SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL WHERE trade_date > CURRENT_DATE"
            ).fetchone()[0]
            if future_cnt:
                conn.execute(
                    "DELETE FROM ADJ_FACTOR_LOCAL WHERE trade_date > CURRENT_DATE"
                )
            logger.info(f"ADJ_FACTOR_LOCAL 未来事件清理：删除 {future_cnt} 条")

        if not densify:
            conn.execute("COMMIT")
            logger.info(f"事件已写入 {event_table}；densify=off，跳过 ADJ_FACTOR 稠密化。")
            return

        # 稠密化写入 ADJ_FACTOR（关键：全部 DATE 化，避免类型绑定错误）
        # 说明：
        # - affected：本次发生变更的 code，从 min_change_date 起重算
        # - last_dense：已稠密化表的最后日期，无变更时从 last_dense_date+1 补到 today
        # - 新股首次跑：既无变更也无历史 last_dense -> 兜底用 start_date（修复只补最后一天的 bug）
        # - ev：事件源与上面的写入表同表（local→ADJ_FACTOR_LOCAL / bstock→ADJ_FACTOR_RAW）
        conn.execute(
            f"""
            WITH
            affected AS (
                SELECT
                    code,
                    MIN(CAST(date AS DATE)) AS min_change_date
                FROM temp_adj_raw
                GROUP BY code
            ),
            last_dense AS (
                SELECT
                    code,
                    MAX(trade_date) AS last_dense_date
                FROM ADJ_FACTOR
                GROUP BY code
            ),
            ranges AS (
                SELECT
                    t.code,
                    CASE
                        -- 新股/重置（无稠密历史），或显式回填（start_date 早于已有数据）：
                        -- 直接从 start_date 开始，不受 min_change_date 干扰
                        WHEN ld.last_dense_date IS NULL
                          OR CAST(t.start_date AS DATE) < ld.last_dense_date
                            THEN CAST(t.start_date AS DATE)
                        -- 正常增量续接：从 min(事件日, last_dense+1) 开始
                        ELSE GREATEST(
                            CAST(t.start_date AS DATE),
                            COALESCE(
                                LEAST(a.min_change_date, CAST(date_add(ld.last_dense_date, INTERVAL 1 DAY) AS DATE)),
                                CAST(date_add(ld.last_dense_date, INTERVAL 1 DAY) AS DATE)
                            )
                        )
                    END AS from_date,
                    LEAST(CAST(t.end_date AS DATE), CURRENT_DATE) AS to_date
                FROM temp_targets t
                LEFT JOIN affected a ON a.code = t.code
                LEFT JOIN last_dense ld ON ld.code = t.code
                WHERE CAST(t.start_date AS DATE) <= CAST(t.end_date AS DATE)
            ),
            cal AS (
                SELECT
                    r.code,
                    CAST(c.cal_date AS DATE) AS trade_date
                FROM ranges r
                JOIN TRADE_CAL c
                ON c.is_open = 1
                AND CAST(c.cal_date AS DATE) BETWEEN r.from_date AND r.to_date
                WHERE r.from_date <= r.to_date
            ),
            ev AS (
                SELECT
                    code,
                    trade_date,
                    fore_factor,
                    back_factor,
                    adjust_factor
                FROM {event_table}
                WHERE code IN (SELECT code FROM ranges)
            )
            INSERT OR REPLACE INTO ADJ_FACTOR
                (code, trade_date, fore_factor, back_factor, adjust_factor, updated_at)
            SELECT
                cal.code,
                cal.trade_date,
                COALESCE(ev.fore_factor, 1.0) AS fore_factor,
                COALESCE(ev.back_factor, 1.0) AS back_factor,
                COALESCE(ev.adjust_factor, 1.0) AS adjust_factor,
                now() AS updated_at
            FROM cal
            ASOF LEFT JOIN ev
            ON cal.code = ev.code
            AND cal.trade_date >= ev.trade_date;
            """
        )

        conn.execute("COMMIT")
        logger.info(f"复权因子稠密化完成：ADJ_FACTOR 已更新（事件源 {event_table}）。")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.error(f"复权因子稠密化失败：{e}")
        raise
    finally:
        for name in ("temp_targets", "temp_adj_raw"):
            try:
                conn.unregister(name)
            except Exception:
                pass


def check_parameters(begin: str, end: str) -> bool:
    """校验命令行参数有效性"""
    ctx = {"begin": begin, "end": end}
    validators = [
        pv.v_dbfile_exists(),
        pv.v_yyyymmdd("begin"),
        pv.v_yyyymmdd("end"),
        pv.v_date_order("begin", "end"),
        pv.v_single_day_must_be_trading_day("begin", "end"),
    ]
    return pv.run(ctx, validators)


def main() -> int:
    myutil.configure_etl_logging()

    args = parse_arguments()
    if not check_parameters(args.begin, args.end):
        return 1

    begin_date = myutil.trans_datestr_format(args.begin)
    end_date   = myutil.trans_datestr_format(args.end)

    densify = resolve_densify(getattr(args, 'densify', 'auto'), args.source)
    event_table = "ADJ_FACTOR_LOCAL" if args.source == "local" else "ADJ_FACTOR_RAW"

    logger.info("=" * 60)
    logger.info("获取股票复权因子任务启动")
    logger.info(f"     起始日期:  {begin_date}")
    logger.info(f"     截止日期:  {end_date}")
    logger.info(f"     交易所:    {args.exchanges}")
    logger.info(f"     指定代码:  {args.codes if args.codes else '无 (处理全市场)'}")
    logger.info(f"     数据源:    {args.source}")
    logger.info(f"     事件表:    {event_table}，稠密化: {'开' if densify else '关(仅写事件表留痕)'}")
    logger.info("=" * 60)

    candidate_codes = dbutil.get_candidate_codes(
        begindate     = begin_date,
        enddate       = end_date,
        exchanges_arg = args.exchanges,
        codes_arg     = args.codes
    )

    if not candidate_codes:
        logger.warning("警告: 数据库中没有找到符合条件的股票")
        return 1

    conn: duckdb.DuckDBPyConnection | None = None
    try:
        # -s local 对应 datasource/local_xdr.py（文件名带 _xdr 后缀，与 CLI 名做个映射）
        module_name = "local_xdr" if args.source == "local" else args.source
        module = myutil.import_source_module(module_name)
        # 守卫检查的方法必须与实际调用的一致，否则模块能力缺失会漏到兜底 except 里
        by_date = args.source == 'bstock' and getattr(args, 'date_range_only', False)
        required = 'fetch_adjust_factors_by_date' if by_date else 'fetch_adjust_factors'
        if not hasattr(module, required):
            logger.error(f"模块 '{module_name}' 中没有定义 '{required}' 方法。")
            return 1

        if by_date:
            adjust = module.fetch_adjust_factors_by_date(
                candidate_codes, begin_date, end_date
            )
        else:
            adjust = module.fetch_adjust_factors(candidate_codes)

        if adjust is None:
            adjust = pd.DataFrame()

        # 写连接在下载完成之后才获取：下载阶段耗时最长也最容易卡死，
        # 此时不持写锁，外部可安全 kill 而不会中断持锁进程。
        conn = dbutil.get_connection(is_read_only=False)

        # 即便没有新的复权因子事件，也需要调用处理函数，
        # 以便将 ADJ_FACTOR 表的数据延续（Forward Fill）到 end_date；
        # densify=False 时仅写事件表留痕，不触碰 ADJ_FACTOR。
        process_and_save_adjust_factors(adjust, candidate_codes, conn,
                                        event_table=event_table, densify=densify)

        if not densify:
            logger.info(f"densify=off：复权因子事件已写入 {event_table} 留痕，跳过 ADJ_FACTOR 稠密化。")
        elif not adjust.empty:
            logger.info("复权因子已成功写入数据库（含新变更）。")
        else:
            logger.info("未获取到新复权因子，已执行每日数据补齐。")

        return 0

    except ImportError:
        logger.error(f"模块 '{args.source}' 不存在，请检查数据源配置。")
        return 1
    except Exception as e:
        logger.error(f"处理复权因子时发生错误：{e}")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
