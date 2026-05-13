"""
通达信 pytdx 行情数据源

注意事项:
  1. pytdx 是纯行情接口，不提供 turnover_rate/pe/pb/is_st 等指标，
     fetch_batch_data 返回的 basic_df 中这些字段均为 None（仅用于在 DAILY_BASIC 建行）
  2. pre_close 通过对已拉取数据排序后 shift(1) 推算；该股上市首日无前收，入库时补 -1
  3. tradestatus 判断：vol=0 且四价相同（round(3) 避免浮点误差）则为停牌(0)，否则为正常交易(1)；
     pytdx 未返回的交易日（全天停牌）会按 lday 同样逻辑补占位行（tradestatus=0，四价填前收，量额=0）
  4. 成交量(vol)单位为手，已在代码中 ×100 转换为股
"""
import pandas as pd
import logging
from pytdx.hq import TdxHq_API
from util.myutil import timer
from util.config import get_config
from util import dbutil

logger = logging.getLogger("etl.datasource.tdx")


def _get_servers():
    """从 config.yaml 读取通达信服务器列表"""
    cfg = get_config()
    return [tuple(s) for s in cfg["tdx"]["servers"]]


def _get_max_pages():
    """从 config.yaml 读取最大分页数"""
    return get_config()["tdx"]["max_pages"]


def _get_max_fail():
    """从 config.yaml 读取最大连续失败数"""
    return get_config()["tdx"]["max_fail"]


def _connect_api() -> TdxHq_API:
    """尝试多个服务器，返回已连接的 API 实例"""
    api = TdxHq_API(raise_exception=False)
    for ip, port in _get_servers():
        try:
            if api.connect(ip, port, time_out=3):
                return api
        except Exception as e:
            logger.warning(f"连接通达信服务器 {ip}:{port} 失败: {e}")
    raise ConnectionError("所有通达信行情服务器均无法连接")


def _to_market(exchange: str) -> int:
    """交易所转 pytdx market 参数: SH=1, SZ=0, BJ=2（pytdx 不支持，调用方需跳过）"""
    ex = exchange.upper()
    if ex == 'SH':
        return 1
    if ex == 'BJ':
        return 2
    return 0


def fetch_stock_data(api: TdxHq_API, symbol: str, market: str,
                     begin_date: str, end_date: str,
                     trade_dates: list[str]) -> pd.DataFrame:
    """拉取单只股票的历史日K线，对 pytdx 未返回的交易日补停牌占位行

    Parameters
    ----------
    api         : 已连接的 TdxHq_API
    symbol      : 6位股票代码
    market      : 交易所 SH/SZ/BJ
    begin_date / end_date : YYYY-MM-DD
    trade_dates : 区间内所有交易日（YYYYMMDD 升序），用于生成停牌占位行
    """
    mkt = _to_market(market)

    dfs = []
    for page in range(_get_max_pages()):
        raw = api.get_security_bars(9, mkt, symbol, page * 800, 800)
        if not raw:
            break
        df_page = api.to_df(raw)
        if df_page.empty:
            break
        dfs.append(df_page)
        # pytdx 按时间倒序分页，若当前页最早日期已早于 begin_date，后续页无需再取
        if df_page['datetime'].iloc[0][:10] < begin_date:
            break

    # 将 pytdx 返回的数据索引为 {YYYYMMDD: row}，便于逐交易日查找
    raw_records: dict[str, dict] = {}
    prev_close: float | None = None

    if dfs:
        df = pd.concat(dfs, ignore_index=True)
        df['date'] = pd.to_datetime(df['datetime'], errors='coerce').dt.strftime('%Y-%m-%d')
        df = df.dropna(subset=['date'])
        df = df.sort_values('date').reset_index(drop=True)

        # 取 begin_date 前最后一条作为首日 pre_close 的来源
        before = df[df['date'] < begin_date]
        if not before.empty:
            prev_close = float(before.iloc[-1]['close'])

        in_range = df[(df['date'] >= begin_date) & (df['date'] <= end_date)]
        for _, row in in_range.iterrows():
            key = row['date'].replace('-', '')
            is_suspended = (
                row['vol'] == 0 and
                round(float(row['open']), 3) == round(float(row['close']), 3) and
                round(float(row['high']), 3) == round(float(row['low']),   3) and
                round(float(row['open']), 3) == round(float(row['high']),  3)
            )
            raw_records[key] = {
                'open':        float(row['open']),
                'high':        float(row['high']),
                'low':         float(row['low']),
                'close':       float(row['close']),
                'volume':      int(row['vol']) * 100,  # 手→股
                'amount':      float(row['amount']),
                'tradestatus': 0 if is_suspended else 1,
            }

    target_dates = [d for d in trade_dates
                    if begin_date.replace('-', '') <= d <= end_date.replace('-', '')]
    if not target_dates:
        return pd.DataFrame()

    std_code = f"{symbol}.{market.upper()}"
    all_records = []
    last_close = prev_close

    for td in target_dates:
        clean_date = f"{td[:4]}-{td[4:6]}-{td[6:]}"
        if td in raw_records:
            r = raw_records[td]
            all_records.append({
                'code':        std_code,
                'date':        clean_date,
                'open':        r['open'],
                'high':        r['high'],
                'low':         r['low'],
                'close':       r['close'],
                'pre_close':   last_close if last_close is not None else float('nan'),
                'tradestatus': r['tradestatus'],
                'volume':      r['volume'],
                'amount':      r['amount'],
            })
            last_close = r['close']
        elif last_close is not None:
            # pytdx 未返回该交易日 → 全天停牌占位行
            all_records.append({
                'code':        std_code,
                'date':        clean_date,
                'open':        last_close,
                'high':        last_close,
                'low':         last_close,
                'close':       last_close,
                'pre_close':   last_close,
                'tradestatus': 0,
                'volume':      0,
                'amount':      0.0,
            })
        # last_close is None: 上市首日即停牌，无前收，跳过

    if not all_records:
        return pd.DataFrame()

    return pd.DataFrame(all_records)[
        ['code', 'date', 'open', 'high', 'low', 'close', 'pre_close', 'tradestatus', 'volume', 'amount']
    ]


@timer
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """批量获取个股日K线数据

    Parameters
    ----------
    stock_list : list of (symbol, market, begin_date, end_date, status) tuples
    Returns (daily_df, basic_df)，basic_df 仅含 code/trade_date，指标列均为 None
    """
    total = len(stock_list)
    all_daily_data: list[pd.DataFrame] = []
    fail_count = 0
    max_fail = _get_max_fail()

    active = [(b, e) for _, _, b, e, st in stock_list if st != 'D']
    if not active:
        return pd.DataFrame(), pd.DataFrame()
    min_begin = min(b for b, _ in active)
    max_end   = max(e for _, e in active)
    trade_dates = dbutil.get_trade_dates(min_begin, max_end)  # YYYYMMDD 升序

    api = _connect_api()

    try:
        logger.info(f"[pytdx插件] 开始批量获取个股行情数据，共计 {total} 只股票...")

        for i, (symbol, market, begindate, enddate, status) in enumerate(stock_list):
            if status == "D":
                continue

            try:
                df_daily = fetch_stock_data(api, str(symbol), str(market), begindate, enddate, trade_dates)
                if not df_daily.empty:
                    all_daily_data.append(df_daily)
                fail_count = 0

            except Exception as e:
                fail_count += 1
                logger.warning(f"  获取失败: {symbol}.{market} | 原因: {e}")
                if fail_count >= max_fail:
                    logger.warning(f"  连续失败 {max_fail} 次，尝试重连...")
                    try:
                        api.disconnect()
                        api = _connect_api()
                        fail_count = 0
                    except ConnectionError:
                        logger.error("  重连失败，终止采集")
                        break

            if (i + 1) % 100 == 0:
                logger.info(f"   进度: {i + 1}/{total}")

    finally:
        api.disconnect()

    if all_daily_data:
        final_daily = pd.concat(all_daily_data, ignore_index=True)
        basic_df = (
            final_daily[['code', 'date']]
            .rename(columns={'date': 'trade_date'})
            .assign(turnover_rate=None, pe=None, pb=None, is_st=None)
        )
    else:
        final_daily = pd.DataFrame()
        basic_df = pd.DataFrame()

    logger.info(f"[pytdx] 采集完成，成功获取 {len(final_daily)} 条记录")
    return final_daily, basic_df


def _get_category():
    """从 config.yaml 读取股本变迁类别映射"""
    return get_config()["tdx"]["capital_category"]


def fetch_xdxr_data(stocks: list[tuple]) -> pd.DataFrame | None:
    """从通达信服务器在线拉取全市场 xdxr (除权除息/股本变迁) 数据

    Parameters
    ----------
    stocks : list of (symbol, exchange) tuples
    Returns DataFrame 含 code/date/category/dividend/allotment_price/bonus_share/allotment_share，失败返回 None
    """
    if not stocks:
        logger.warning("[pytdx] 股票列表为空，跳过 xdxr 拉取")
        return None

    logger.info(f"[pytdx] 从服务器在线拉取 xdxr (股本变迁) 数据，共 {len(stocks)} 只...")

    try:
        api = _connect_api()
    except ConnectionError as e:
        logger.error(f"连接通达信服务器失败: {e}")
        return None

    category_map = _get_category()
    max_fail = _get_max_fail()
    fail_count = 0
    all_dfs = []
    try:
        for i, (symbol, exchange) in enumerate(stocks):
            market = _to_market(exchange)
            if market == 2:
                continue
            try:
                raw = api.get_xdxr_info(market, symbol)
                fail_count = 0
                if not raw:
                    continue
                df = api.to_df(raw)
                df = df.assign(
                    date=(df['year'].astype(str).str.zfill(4) + '-'
                          + df['month'].astype(str).str.zfill(2) + '-'
                          + df['day'].astype(str).str.zfill(2)),
                    code=symbol,
                )
                mapped = df['category'].astype(str).map(category_map)
                unknown = df['category'].astype(str)[mapped.isna()].unique()
                if len(unknown):
                    logger.warning(f"  未知 xdxr category 码: {unknown.tolist()}")
                df['category'] = mapped
                df = df.rename(columns={
                    'fenhong':     'dividend',
                    'peigujia':    'allotment_price',
                    'songzhuangu': 'bonus_share',
                    'peigu':       'allotment_share',
                })
                all_dfs.append(df[['code', 'date', 'category',
                                   'dividend', 'allotment_price', 'bonus_share', 'allotment_share']])
            except Exception as e:
                fail_count += 1
                logger.warning(f"  获取失败: {symbol}.{exchange} | 原因: {e}")
                if fail_count >= max_fail:
                    logger.warning(f"  连续失败 {max_fail} 次，尝试重连...")
                    try:
                        api.disconnect()
                        api = _connect_api()
                        fail_count = 0
                    except ConnectionError:
                        logger.error("  重连失败，终止采集")
                        break

            if (i + 1) % 1000 == 0:
                logger.info(f"  已拉取 {i + 1}/{len(stocks)}...")
    finally:
        api.disconnect()

    if not all_dfs:
        logger.warning("[pytdx] 未拉取到任何 xdxr 数据")
        return None

    df_result = pd.concat(all_dfs, ignore_index=True)
    df_result['code'] = df_result['code'].astype(str)
    logger.info(f"[pytdx] xdxr 拉取完成，共 {len(df_result)} 条记录")
    return df_result
