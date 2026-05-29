# 修改记录:
#   2026-05-29  Claude  修复深交所融资融券汇总(_fetch_summary_szse)字段映射: akshare 实际返回列为「融券余额」, 原映射「融券余量金额」已失效导致 short_balance_amount 入库为空
#   2026-05-29  Claude  4 个融资融券 akshare 调用加异常重试(_retry_call), 重试次数/间隔放 config.yaml akshare.tries/retry_delay, 缓解 szse/sse 官网偶发 SSL 断连
#   2026-05-29  Claude  深交所汇总(_fetch_summary_szse)金额/数量由"亿"显示值统一 ×1e8 转 元/股, 对齐 schema 与其它数据源
import akshare as ak
import logging
import time
import pandas as pd
from util.myutil import timer
from util.config import get_config
from datetime import datetime

logger = logging.getLogger("etl.datasource.akstock")

_SUMMARY_OUT_COLS = [
    'trade_date', 'exchange_code',
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]

_DETAIL_OUT_COLS = [
    'trade_date', 'exchange_code', 'symbol', 'code',
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]

# 深交所汇总接口以"亿"为单位显示的数值列(金额亿元/数量亿股), 入库前 ×1e8 转 元/股
_SZSE_SUMMARY_SCALE_COLS = [
    'margin_buy_amount', 'margin_balance',
    'short_sell_volume', 'short_balance_volume',
    'short_balance_amount', 'margin_short_balance',
]

_DETAIL_NUM_COLS = [
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]

_INDUSTRY_OUT_COLS = ['symbol', 'start_date', 'industry_code', 'update_time']


def _get_max_fail() -> int:
    return get_config()["akshare"]["max_fail"]


def _get_request_timeout() -> int:
    return get_config()["akshare"]["request_timeout"]


def _get_retry_tries() -> int:
    return get_config()["akshare"].get("tries", 3)


def _get_retry_delay() -> float:
    return get_config()["akshare"].get("retry_delay", 3)


def _retry_call(fn, what: str):
    """对 akshare 取数调用做异常重试。

    仅在抛异常时重试，正常返回（含空结果）不重试——空结果是合法结果（如非交易日）。
    重试次数与间隔取自 config.yaml akshare.tries / akshare.retry_delay。
    全部失败后抛出最后一次异常，交由调用方按原有逻辑安全降级。
    """
    tries = _get_retry_tries()
    delay = _get_retry_delay()
    last_exc: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            logger.warning(f"  [WARN] {what} 第 {attempt}/{tries} 次调用失败: {e}")
            if attempt < tries:
                time.sleep(delay)
    raise last_exc


def fetch_bj_stock_data(trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """获取北交所股票基础信息

    Parameters
    ----------
    trade_date : YYYY-MM-DD，用于 df_basic 的 date 字段
    Returns (df_info, df_basic)
    """
    try:
        df_raw = ak.stock_info_bj_name_code()
        if df_raw.empty:
            logger.warning("未获取到北交所数据")
            return pd.DataFrame(), pd.DataFrame()

        df_raw['symbol'] = df_raw['证券代码'].astype(str)
        df_raw['code'] = df_raw['symbol'] + '.BJ'

        df_info = pd.DataFrame()
        df_info['code'] = df_raw['code']
        df_info['symbol'] = df_raw['symbol']
        df_info['name'] = df_raw['证券简称']
        df_info['exchange'] = 'BJ'
        df_info['board'] = 'BJ'
        df_info['list_date'] = pd.to_datetime(df_raw['上市日期'], errors='coerce')
        df_info['delist_date'] = None
        df_info['list_status'] = 'L'

        df_basic = pd.DataFrame()
        df_basic['code'] = df_raw['code']
        df_basic['date'] = trade_date
        df_basic['total_shares'] = pd.to_numeric(
            df_raw['总股本'].astype(str).str.replace(',', '', regex=False), errors='coerce')
        df_basic['float_shares'] = pd.to_numeric(
            df_raw['流通股本'].astype(str).str.replace(',', '', regex=False), errors='coerce')
        df_basic = df_basic[['code', 'date', 'total_shares', 'float_shares']]

        logger.info(f"[成功] 获取到 {len(df_info)} 条北交所数据")
        return df_info, df_basic
    except Exception as e:
        logger.error(f"获取北交所数据出错: {e}")
        return pd.DataFrame(), pd.DataFrame()


def fetch_stock_info(exchanges: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """获取指定交易所的股票基本信息（当前仅支持 BJ）

    Parameters
    ----------
    exchanges : 交易所列表，如 ['BJ'] 或 ['all']
    Returns (df_info, df_basic)
    """
    target_exs = set(e.upper() for e in exchanges)
    if 'BJ' in target_exs or 'ALL' in target_exs:
        return fetch_bj_stock_data(datetime.now().strftime("%Y-%m-%d"))
    else:
        logger.info(f"不支持的交易所代码: {exchanges}")
        return pd.DataFrame(), pd.DataFrame()


def fetch_stock_data(start_date_str: str, end_date_str: str, symbol: str, market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """拉取单只股票历史日K数据

    Parameters
    ----------
    start_date_str / end_date_str : YYYYMMDD 格式
    symbol : 6位股票代码
    market : 交易所 SH/SZ/BJ
    Returns (df_daily, df_basic)
    """
    df_raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date_str,
        end_date=end_date_str,
        adjust="",
        timeout=_get_request_timeout()
    )

    if df_raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    std_code = f"{symbol}.{market.upper()}"
    df_daily = pd.DataFrame({
        'code':         std_code,
        'date':         df_raw['日期'],
        'open':         pd.to_numeric(df_raw['开盘'], errors='coerce'),
        'high':         pd.to_numeric(df_raw['最高'], errors='coerce'),
        'low':          pd.to_numeric(df_raw['最低'], errors='coerce'),
        'close':        pd.to_numeric(df_raw['收盘'], errors='coerce'),
        'volume':       pd.to_numeric(df_raw['成交量'], errors='coerce') * 100,
        'amount':       pd.to_numeric(df_raw['成交额'], errors='coerce'),
        'trade_status': 1
    })

    df_basic = pd.DataFrame({
        'code':          std_code,
        'trade_date':    df_raw['日期'],
        'turnover_rate': pd.to_numeric(df_raw['换手率'], errors='coerce'),
        'pe':            None,
        'pb':            None,
        'is_st':         None
    })

    return df_daily, df_basic


@timer
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """批量获取股票日K数据

    Parameters
    ----------
    stock_list : list of (symbol, market, begin_date, end_date, status) tuples
    Returns (daily_df, basic_df)
    """
    total = len(stock_list)
    all_daily_data = []
    all_basic_data = []
    fail_count = 0
    max_fail = _get_max_fail()

    logger.info(f"[akshare插件] 开始批量获取个股行情数据，共计 {total} 只股票...")

    for i, (symbol, market, begindate, enddate, status) in enumerate(stock_list):
        if status == "D":
            continue
        start_date_str = begindate.replace("-", "")
        end_date_str = enddate.replace("-", "")
        try:
            df_daily, df_basic = fetch_stock_data(start_date_str, end_date_str, symbol, market)

            if not df_daily.empty:
                all_daily_data.append(df_daily)
            if not df_basic.empty:
                all_basic_data.append(df_basic)

            if (i + 1) % 100 == 0:
                logger.info(f"   进度: {i + 1}/{total}")

            fail_count = 0

        except Exception as e:
            fail_count += 1
            logger.warning(f"获取失败: {symbol} | 原因: {e}")

            if fail_count >= max_fail:
                logger.warning(f"连续失败次数达到 {max_fail} 次，终止采集")
                break

    final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()
    final_basic = pd.concat(all_basic_data, ignore_index=True) if all_basic_data else pd.DataFrame()
    logger.info(f"[AkShare] 采集完成，行情记录: {len(final_daily)} 条，指标记录: {len(final_basic)} 条")

    return final_daily, final_basic


def fetch_sw_industries() -> pd.DataFrame:
    """[DEPRECATED] 获取申万一/二/三级行业定义

    新流程使用 etl.sync_industry --input 读取 tmp/swclasscode.csv。
    Returns DataFrame(sw_code, sw_name, sw_level, parent_code)
    """
    l1 = ak.sw_index_first_info()[['行业代码', '行业名称']].copy()
    l1.columns = ['sw_code', 'sw_name']
    l1['sw_level'] = 1
    l1['parent_code'] = None

    l2_raw = ak.sw_index_second_info()[['行业代码', '行业名称', '上级行业']].copy()
    l2_raw.columns = ['sw_code', 'sw_name', 'parent_name']
    name_to_l1_code = l1.set_index('sw_name')['sw_code'].to_dict()
    l2_raw['sw_level'] = 2
    l2_raw['parent_code'] = l2_raw['parent_name'].map(name_to_l1_code)
    l2 = l2_raw[['sw_code', 'sw_name', 'sw_level', 'parent_code']]

    try:
        l3_raw = ak.sw_index_third_info()[['行业代码', '行业名称', '上级行业']].copy()
        l3_raw.columns = ['sw_code', 'sw_name', 'parent_name']
        name_to_l2_code = l2.set_index('sw_name')['sw_code'].to_dict()
        l3_raw['sw_level'] = 3
        l3_raw['parent_code'] = l3_raw['parent_name'].map(name_to_l2_code)
        l3 = l3_raw[['sw_code', 'sw_name', 'sw_level', 'parent_code']]
    except Exception as e:
        logger.warning(f"获取申万三级行业定义失败，跳过: {e}")
        l3 = pd.DataFrame(columns=['sw_code', 'sw_name', 'sw_level', 'parent_code'])

    return pd.concat([l1, l2, l3], ignore_index=True)


def fetch_stock_sw_mapping(industry_df: pd.DataFrame) -> pd.DataFrame:
    """[DEPRECATED] 获取所有股票的申万行业归属

    新流程使用 fetch_stock_industry_clf_hist_sw()。
    Returns DataFrame(code, sw_l1_code, sw_l1_name, sw_l2_code, sw_l2_name,
                       sw_l3_code, sw_l3_name, entry_date)
    """
    code_to_row = industry_df.set_index('sw_code').to_dict('index')

    def get_hierarchy(sw_code: str, sw_level: int) -> dict:
        row = code_to_row.get(sw_code, {})
        if sw_level == 3:
            l3_code = sw_code
            l3_name = row.get('sw_name', '')
            l2_code = row.get('parent_code') or ''
            l2_row  = code_to_row.get(l2_code, {})
            l1_code = l2_row.get('parent_code') or ''
            l1_row  = code_to_row.get(l1_code, {})
        else:
            l3_code = ''
            l3_name = ''
            l2_code = sw_code
            l2_row  = row
            l1_code = row.get('parent_code') or ''
            l1_row  = code_to_row.get(l1_code, {})
        return {
            'sw_l1_code': l1_code,
            'sw_l1_name': l1_row.get('sw_name', ''),
            'sw_l2_code': l2_code,
            'sw_l2_name': l2_row.get('sw_name', ''),
            'sw_l3_code': l3_code,
            'sw_l3_name': l3_name,
        }

    l3_codes = industry_df[industry_df['sw_level'] == 3]['sw_code'].tolist()
    l2_codes = industry_df[industry_df['sw_level'] == 2]['sw_code'].tolist()

    query_pairs = [(c, 3) for c in l3_codes] + [(c, 2) for c in l2_codes]
    total = len(query_pairs)
    logger.info(f"[akshare插件] 开始遍历 {len(l3_codes)} 个L3 + {len(l2_codes)} 个L2 行业获取成分股...")

    all_records = []
    for i, (sw_code, sw_level) in enumerate(query_pairs, 1):
        try:
            df = ak.sw_index_third_cons(symbol=sw_code)
            if df.empty:
                continue

            df = df[['股票代码', '纳入时间']].copy()
            df.columns = ['code', 'entry_date']

            h = get_hierarchy(sw_code, sw_level)
            df['sw_l1_code'] = h['sw_l1_code']
            df['sw_l1_name'] = h['sw_l1_name']
            df['sw_l2_code'] = h['sw_l2_code']
            df['sw_l2_name'] = h['sw_l2_name']
            df['sw_l3_code'] = h['sw_l3_code']
            df['sw_l3_name'] = h['sw_l3_name']

            all_records.append(df)

        except Exception as e:
            logger.warning(f"获取行业 {sw_code} 成分股失败: {e}")

        if i % 20 == 0:
            logger.info(f"   已处理: {i}/{total}")

    if not all_records:
        return pd.DataFrame()

    result = pd.concat(all_records, ignore_index=True)
    result = result.drop_duplicates(subset='code', keep='first')

    cols = ['code', 'sw_l1_code', 'sw_l1_name',
            'sw_l2_code', 'sw_l2_name',
            'sw_l3_code', 'sw_l3_name', 'entry_date']
    return result[cols].reset_index(drop=True)


def _fetch_summary_sse(begin_date: str, end_date: str) -> pd.DataFrame:
    """上交所融资融券汇总：单次区间查询。"""
    try:
        df = _retry_call(
            lambda: ak.stock_margin_sse(start_date=begin_date, end_date=end_date),
            "上交所融资融券汇总",
        )
    except Exception as e:
        logger.warning(f"  [WARN] 上交所融资融券汇总获取失败: {e}")
        return pd.DataFrame(columns=_SUMMARY_OUT_COLS)

    if df is None or df.empty:
        return pd.DataFrame(columns=_SUMMARY_OUT_COLS)

    df = df.rename(columns={
        '信用交易日期':   'trade_date',
        '融资余额':       'margin_balance',
        '融资买入额':     'margin_buy_amount',
        '融券余量':       'short_balance_volume',
        '融券余量金额':   'short_balance_amount',
        '融券卖出量':     'short_sell_volume',
        '融资融券余额':   'margin_short_balance',
    })
    df['trade_date'] = pd.to_datetime(df['trade_date'].astype(str), format='%Y%m%d', errors='coerce').dt.date
    df = df.dropna(subset=['trade_date']).copy()
    df['exchange_code']        = 'SH'
    df['margin_repay_amount']  = None
    df['short_repay_volume']   = None
    return df.reindex(columns=_SUMMARY_OUT_COLS).reset_index(drop=True)


def _fetch_summary_szse(trade_dates: list[str]) -> pd.DataFrame:
    """深交所融资融券汇总：逐日查询，取"融资融券"汇总行。"""
    rows = []
    for d in trade_dates:
        try:
            df = _retry_call(
                lambda d=d: ak.stock_margin_szse(date=d),
                f"深交所融资融券汇总 {d}",
            )
        except Exception as e:
            logger.warning(f"  [WARN] 深交所融资融券汇总 {d} 获取失败: {e}")
            continue
        if df is None or df.empty:
            continue

        if '项目' in df.columns:
            df = df[df['项目'].astype(str).str.strip() == '融资融券'].copy()
            if df.empty:
                continue

        df = df.rename(columns={
            '数据日期':       'trade_date',
            '融资买入额':     'margin_buy_amount',
            '融资余额':       'margin_balance',
            '融券卖出量':     'short_sell_volume',
            '融券余额':       'short_balance_amount',
            '融券余量':       'short_balance_volume',
            '融资融券余额':   'margin_short_balance',
        })
        if 'trade_date' not in df.columns:
            df['trade_date'] = d
        df['trade_date']           = pd.to_datetime(df['trade_date'].astype(str), errors='coerce').dt.date
        df['exchange_code']        = 'SZ'
        df['margin_repay_amount']  = None
        df['short_repay_volume']   = None

        # 深交所汇总数值以"亿"为单位显示(金额=亿元, 数量=亿股)且带千分位逗号,
        # 统一去逗号后 ×1e8 转回 元/股, 对齐 schema 注释及其它数据源(沪市汇总、沪深明细)
        for col in _SZSE_SUMMARY_SCALE_COLS:
            if col in df.columns:
                cleaned = df[col].astype(str).str.replace(',', '', regex=False)
                df[col] = pd.to_numeric(cleaned, errors='coerce') * 1e8

        rows.append(df.reindex(columns=_SUMMARY_OUT_COLS))

    if not rows:
        return pd.DataFrame(columns=_SUMMARY_OUT_COLS)
    return pd.concat(rows, ignore_index=True)


def fetch_margin_summary(begin_date: str, end_date: str,
                         exchanges: list[str],
                         trade_dates: list[str]) -> pd.DataFrame:
    """获取沪深融资融券每日汇总数据 (MARGIN_SUMMARY_DAILY)

    Parameters
    ----------
    begin_date / end_date : YYYYMMDD
    exchanges : 交易所列表，元素 sh/sz/all
    trade_dates : 区间内交易日列表 YYYYMMDD，供 SZ 逐日抓取使用
    """
    target = set(e.lower() for e in exchanges)
    want_sh = ('sh' in target) or ('all' in target)
    want_sz = ('sz' in target) or ('all' in target)

    parts = []
    if want_sh:
        df_sh = _fetch_summary_sse(begin_date, end_date)
        logger.info(f"  上交所汇总: {len(df_sh)} 条")
        parts.append(df_sh)
    if want_sz:
        df_sz = _fetch_summary_szse(trade_dates)
        logger.info(f"  深交所汇总: {len(df_sz)} 条")
        parts.append(df_sz)

    if not parts:
        return pd.DataFrame(columns=_SUMMARY_OUT_COLS)
    return pd.concat(parts, ignore_index=True).reset_index(drop=True)


def fetch_margin_detail(trade_date: str, exchanges: list[str]) -> pd.DataFrame:
    """获取指定交易日的沪深融资融券明细 (MARGIN_DETAIL_DAILY)

    Parameters
    ----------
    trade_date : YYYYMMDD
    exchanges : 交易所列表，元素 sh/sz/all
    """
    target = set(e.lower() for e in exchanges)
    want_sh = ('sh' in target) or ('all' in target)
    want_sz = ('sz' in target) or ('all' in target)

    trade_dt = datetime.strptime(trade_date, '%Y%m%d').date()
    dfs: list[pd.DataFrame] = []

    if want_sz:
        try:
            df_sz = _retry_call(
                lambda: ak.stock_margin_detail_szse(date=trade_date),
                f"深交所融资融券明细 {trade_date}",
            )
            if df_sz is not None and not df_sz.empty:
                df_sz = df_sz.rename(columns={
                    '证券代码':     'symbol',
                    '融资买入额':   'margin_buy_amount',
                    '融资余额':     'margin_balance',
                    '融券卖出量':   'short_sell_volume',
                    '融券余量':     'short_balance_volume',
                    '融券余额':     'short_balance_amount',
                    '融资融券余额': 'margin_short_balance',
                })
                df_sz = df_sz[df_sz['symbol'].astype(str).str.match(r'^[03]\d{5}$')].copy()
                df_sz['symbol']              = df_sz['symbol'].astype(str).str.zfill(6)
                df_sz['code']                = df_sz['symbol'] + '.SZ'
                df_sz['trade_date']          = trade_dt
                df_sz['exchange_code']       = 'SZ'
                df_sz['margin_repay_amount'] = None
                df_sz['short_repay_volume']  = None
                dfs.append(df_sz.reindex(columns=_DETAIL_OUT_COLS))
                logger.info(f"  深交所明细: {len(df_sz)} 条")
        except Exception as e:
            logger.warning(f"  [WARN] 深交所融资融券明细 {trade_date} 获取失败: {e}")

    if want_sh:
        try:
            df_sh = _retry_call(
                lambda: ak.stock_margin_detail_sse(date=trade_date),
                f"上交所融资融券明细 {trade_date}",
            )
            if df_sh is not None and not df_sh.empty:
                df_sh = df_sh.rename(columns={
                    '标的证券代码':   'symbol',
                    '融资余额':       'margin_balance',
                    '融资买入额':     'margin_buy_amount',
                    '融资偿还额':     'margin_repay_amount',
                    '融券余量':       'short_balance_volume',
                    '融券卖出量':     'short_sell_volume',
                    '融券偿还量':     'short_repay_volume',
                })
                df_sh = df_sh[df_sh['symbol'].astype(str).str.match(r'^6\d{5}$')].copy()
                df_sh['symbol']               = df_sh['symbol'].astype(str).str.zfill(6)
                df_sh['code']                 = df_sh['symbol'] + '.SH'
                df_sh['trade_date']           = trade_dt
                df_sh['exchange_code']        = 'SH'
                df_sh['short_balance_amount'] = None
                df_sh['margin_short_balance'] = None
                dfs.append(df_sh.reindex(columns=_DETAIL_OUT_COLS))
                logger.info(f"  上交所明细: {len(df_sh)} 条")
        except Exception as e:
            logger.warning(f"  [WARN] 上交所融资融券明细 {trade_date} 获取失败: {e}")

    if not dfs:
        return pd.DataFrame(columns=_DETAIL_OUT_COLS)
    result = pd.concat(dfs, ignore_index=True).reset_index(drop=True)
    # 数值列统一转 float64，避免 DuckDB 把大额(>21亿)金额列推断成 INT32 溢出
    for col in _DETAIL_NUM_COLS:
        result[col] = pd.to_numeric(result[col], errors='coerce').astype('float64')
    return result


def fetch_stock_industry_clf_hist_sw() -> pd.DataFrame:
    """获取股票申万行业分类历史原始数据

    接口: ak.stock_industry_clf_hist_sw()
    Returns DataFrame(symbol, start_date, industry_code, update_time)
    """
    try:
        df = ak.stock_industry_clf_hist_sw()
        if df is None or df.empty:
            logger.warning("stock_industry_clf_hist_sw 未返回数据")
            return pd.DataFrame(columns=_INDUSTRY_OUT_COLS)

        missing = [c for c in _INDUSTRY_OUT_COLS if c not in df.columns]
        if missing:
            logger.error(f"stock_industry_clf_hist_sw 缺少字段: {missing}")
            return pd.DataFrame(columns=_INDUSTRY_OUT_COLS)

        result = df[_INDUSTRY_OUT_COLS].copy()
        result = result.dropna(subset=['symbol', 'start_date', 'industry_code'])
        result['symbol'] = result['symbol'].astype(str).str.strip().str.zfill(6)
        result['industry_code'] = result['industry_code'].astype(str).str.strip()
        result['start_date'] = pd.to_datetime(result['start_date'], errors='coerce').dt.date
        result['update_time'] = pd.to_datetime(result['update_time'], errors='coerce')
        result = result.dropna(subset=['start_date'])
        result = result[(result['symbol'] != '') & (result['industry_code'] != '')]
        result = result.drop_duplicates(subset=['symbol', 'start_date', 'industry_code'], keep='last')

        logger.info(f"[成功] 获取股票申万行业历史 {len(result)} 条")
        return result[_INDUSTRY_OUT_COLS].reset_index(drop=True)
    except Exception as e:
        logger.error(f"获取股票申万行业历史失败: {e}")
        return pd.DataFrame(columns=_INDUSTRY_OUT_COLS)
