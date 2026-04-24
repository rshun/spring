"""
通达信 pytdx 行情数据源

注意事项:
  1. pytdx 是纯行情接口，不提供 turnover_rate/pe/pb/is_st 等指标，
     fetch_batch_data 返回的第二个 DataFrame 始终为空
  2. 停牌股票不会返回任何数据（该日期行直接缺失），
     如需停牌记录(tradestatus=0)，需用 baostock 等其他数据源补充
  3. 成交量(vol)单位为手，已在代码中 ×100 转换为股
"""
import pandas as pd
import logging
from pytdx.hq import TdxHq_API
from util.myutil import timer

logger = logging.getLogger("etl.datasource.tdx")


# 备选服务器列表，按经验排序
TDX_SERVERS = [
    ('218.6.170.55', 7709),
    ('123.125.108.24', 7709),
    ('180.153.39.51', 7709),
    ('221.194.181.176', 7709),
    ('59.173.18.140', 7709),
    ('115.238.90.165', 7709),
    ('124.160.88.183', 7709),
    ('60.28.23.80', 7709),
]

# 每次最多拉800条，分页次数（10页 ≈ 8000个交易日）
MAX_PAGES = 10


def _connect_api() -> TdxHq_API:
    """尝试多个服务器，返回已连接的 API 实例"""
    api = TdxHq_API(raise_exception=False)
    for ip, port in TDX_SERVERS:
        try:
            if api.connect(ip, port, time_out=3):
                return api
        except Exception:
            continue
    raise ConnectionError("所有通达信行情服务器均无法连接")


def _to_market(exchange: str) -> int:
    """交易所转 pytdx market 参数: SH=1, SZ/BJ=0"""
    return 1 if exchange.upper() == 'SH' else 0


def fetch_stock_data(api: TdxHq_API, symbol: str, market: str,
                     begin_date: str, end_date: str) -> pd.DataFrame:
    """拉取单只股票的历史日K线

    Parameters
    ----------
    api : 已连接的 TdxHq_API
    symbol : 6位股票代码
    market : 交易所 SH/SZ/BJ
    begin_date / end_date : YYYY-MM-DD
    """
    mkt = _to_market(market)
    dfs = []
    for page in range(MAX_PAGES):
        raw = api.get_security_bars(9, mkt, symbol, page * 800, 800)
        if not raw:
            break
        dfs.append(api.to_df(raw))

    if not dfs:
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)

    # datetime 列格式: "2026-03-12" 或 "2026-03-12 15:00"
    df['date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
    df = df[(df['date'] >= begin_date) & (df['date'] <= end_date)]

    if df.empty:
        return pd.DataFrame()

    std_code = f"{symbol}.{market.upper()}"
    return pd.DataFrame({
        'code':   std_code,
        'date':   df['date'].values,
        'open':   df['open'].values,
        'high':   df['high'].values,
        'low':    df['low'].values,
        'close':  df['close'].values,
        'volume': (df['vol'] * 100).astype(int).values,  # pytdx vol单位是手，转为股
        'amount': df['amount'].values,
    })


'''
批量获取股票数据
输入参数:
   stock_list: 元组---股票代码(6位数字), 交易所(SH,SZ,BJ),起始和结束日期
返回参数:
   pd--明细
'''
@timer
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:

    total = len(stock_list)
    all_daily_data: list[pd.DataFrame] = []
    fail_count = 0
    MAX_FAIL = 20

    api = _connect_api()

    try:
        logger.info(f"[pytdx插件] 开始批量获取个股行情数据，共计 {total} 只股票...")

        for i, (symbol, market, begindate, enddate, status) in enumerate(stock_list):
            if status == "D":
                continue

            try:
                df_daily = fetch_stock_data(api, str(symbol), str(market), begindate, enddate)
                if not df_daily.empty:
                    all_daily_data.append(df_daily)
                fail_count = 0

            except Exception as e:
                fail_count += 1
                logger.info(f"  获取失败: {symbol}.{market} | 原因: {e}")
                if fail_count >= MAX_FAIL:
                    logger.info(f"  [警告] 连续失败 {MAX_FAIL} 次，尝试重连...")
                    try:
                        api.disconnect()
                        api = _connect_api()
                        fail_count = 0
                    except ConnectionError:
                        logger.info("  [错误] 重连失败，终止采集")
                        break

            if (i + 1) % 100 == 0:
                logger.info(f"   进度: {i + 1}/{total}")

    finally:
        api.disconnect()

    final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()
    logger.info(f"[pytdx] 采集完成，成功获取 {len(final_daily)} 条记录")
    return final_daily, pd.DataFrame()


# ── CATEGORY 映射 (股本变迁类别) ─────────────────────────

CATEGORY = {
    '1': '除权除息', '2': '送配股上市', '3': '非流通股上市', '4': '未知股本变动', '5': '股本变化',
    '6': '增发新股', '7': '股份回购', '8': '增发新股上市', '9': '转配股上市', '10': '可转债上市',
    '11': '扩缩股', '12': '非流通股缩股', '13': '送认购权证', '14': '送认沽权证',
    '15': '未知新类别',
}


'''
从通达信服务器在线拉取全市场 xdxr (除权除息/股本变迁) 数据
输入参数:
    stocks: list[tuple] -- (symbol, exchange) 列表
返回参数:
    pd.DataFrame -- 包含 code/date/category/dividend/allotment_price/bonus_share/allotment_share
    如果拉取失败返回 None
'''
def fetch_xdxr_data(stocks: list[tuple]) -> pd.DataFrame | None:

    if not stocks:
        logger.info("[pytdx] 股票列表为空")
        return None

    logger.info(f"[pytdx] 从服务器在线拉取 xdxr (股本变迁) 数据，共 {len(stocks)} 只...")

    try:
        api = _connect_api()
    except ConnectionError as e:
        logger.info(f"[错误] {e}")
        return None

    all_dfs = []
    try:
        for i, (symbol, exchange) in enumerate(stocks):
            market = _to_market(exchange)
            raw = api.get_xdxr_info(market, symbol)
            if not raw:
                continue
            df = api.to_df(raw)
            df = df.assign(
                date=(df['year'].astype(int) * 10000
                      + df['month'].astype(int) * 100
                      + df['day'].astype(int)),
                code=symbol,
            )
            df['category'] = df['category'].astype(str).map(CATEGORY)
            df = df.rename(columns={
                'fenhong':     'dividend',
                'peigujia':    'allotment_price',
                'songzhuangu': 'bonus_share',
                'peigu':       'allotment_share',
            })
            all_dfs.append(df[['code', 'date', 'category',
                               'dividend', 'allotment_price', 'bonus_share', 'allotment_share']])

            if (i + 1) % 500 == 0:
                logger.info(f"  已拉取 {i + 1}/{len(stocks)}...")
    finally:
        api.disconnect()

    if not all_dfs:
        logger.info("[pytdx] 未拉取到任何 xdxr 数据")
        return None

    df_result = pd.concat(all_dfs, ignore_index=True)
    df_result['code'] = df_result['code'].astype(str)
    logger.info(f"[pytdx] xdxr 拉取完成，共 {len(df_result)} 条记录")
    return df_result
