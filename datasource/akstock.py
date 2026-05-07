import akshare as ak
import logging
import pandas as pd
from util.myutil import timer
from datetime import datetime

logger = logging.getLogger("etl.datasource.akstock")

'''
  获取北交所股票基础信息
'''
def fetch_bj_stock_data(trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
                
    try:        
        df_raw = ak.stock_info_bj_name_code()
        if df_raw.empty:
            logger.info("警告: 未获取到北交所数据")
            return pd.DataFrame(), pd.DataFrame()
        df_raw['symbol'] = df_raw['证券代码'].astype(str)
        df_raw['code'] = df_raw['symbol'] + '.BJ'

        df_info = pd.DataFrame()
        df_info['code'] = df_raw['code']
        df_info['symbol'] = df_raw['symbol']
        df_info['name'] = df_raw['证券简称']
        df_info['exchange'] = 'BJ'           # 固定为 BJ
        df_info['board'] = 'BJ'              # 北交所统称为 BJ 板块
        df_info['list_date'] = pd.to_datetime(df_raw['上市日期'], errors='coerce').dt.date
        df_info['delist_date'] = None        # 当前在市，无退市日期
        df_info['list_status'] = 'L'         # 状态默认为 L (Listing)
        
        # 存放 Company Info 所需的字段 (code, date, industry, list_date, total_shares, float_shares)
        df_basic = pd.DataFrame()
        df_basic['code'] = df_raw['code']
        df_basic['date'] = trade_date
        df_basic['total_shares'] = pd.to_numeric(df_raw['总股本'].astype(str).str.replace(',', '', regex=False), errors='coerce')
        df_basic['float_shares'] = pd.to_numeric(df_raw['流通股本'].astype(str).str.replace(',', '', regex=False), errors='coerce')
        
        df_basic = df_basic[['code', 'date', 'total_shares', 'float_shares']]
        
        logger.info(f"[成功] 获取到 {len(df_info)} 条北交所数据")
        
        return df_info, df_basic
    except Exception as e:
        logger.info(f"[失败] 获取北交所数据出错: {e}")
        return pd.DataFrame(), pd.DataFrame()


'''
  获取指定交易所的所有股票基本信息 (akshare接口接口获取股票基本信息根据市场不同而不同)
  京市--ak.stock_info_bj_name_code()
  输入参数
    exchange: str -- 交易所 (sh, sz, bj, all)
'''
def fetch_stock_info(exchanges: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    
    target_exs = set(e.upper() for e in exchanges)
    if 'BJ' in target_exs or 'ALL' in target_exs:
        return fetch_bj_stock_data(datetime.now().strftime("%Y-%m-%d")) 
    else:
        logger.info(f"不支持的交易所代码: {exchanges}")
        return pd.DataFrame(), pd.DataFrame()

'''
  获取股票单笔行情数据
'''
def fetch_stock_data(start_date_str: str, end_date_str: str, symbol: str,market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
 
    df_raw = ak.stock_zh_a_hist(
                symbol=symbol, 
                period="daily", 
                start_date=start_date_str, 
                end_date=end_date_str, 
                adjust="",
                timeout=30
            )

    if df_raw.empty:
        return pd.DataFrame(), pd.DataFrame()
        
    std_code = f"{symbol}.{market.upper()}"
    #目标列: code, date, open, high, low, close, volume, amount
    df_daily = pd.DataFrame({
        'code': std_code,
        'date': df_raw['日期'],
        'open': pd.to_numeric(df_raw['开盘'], errors='coerce'),
        'high': pd.to_numeric(df_raw['最高'], errors='coerce'),
        'low':  pd.to_numeric(df_raw['最低'], errors='coerce'),
        'close': pd.to_numeric(df_raw['收盘'], errors='coerce'),
        'volume': pd.to_numeric(df_raw['成交量'], errors='coerce') * 100,
        'amount': pd.to_numeric(df_raw['成交额'], errors='coerce'),
        "tradestatus": 1
    })
    
    # --- B. 构建 df_basic (每日指标) ---
    # 目标列: code, trade_date, turnover_rate, pe, pb, is_st
    # AkShare 此接口仅提供 换手率
    df_basic = pd.DataFrame({
        'code': std_code,
        'trade_date': df_raw['日期'],
        'turnover_rate': pd.to_numeric(df_raw['换手率'], errors='coerce'),
        'pe': None,     # AkShare此接口无PE
        'pb': None,     # AkShare此接口无PB
        'is_st': None   # AkShare此接口无ST标记
    })

    return df_daily, df_basic

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
    all_daily_data = []
    all_basic_data = []

    logger.info(f"[akshare插件] 开始批量获取个股行情数据，共计 {total} 只股票...")

    fail_count = 0
    MAX_FAIL = 20

    for i, (symbol, market, begindate, enddate,status) in enumerate(stock_list):
        if status == "D":
            continue
        start_date_str = begindate.replace("-", "")
        end_date_str = enddate.replace("-", "")
        try:
            df_daily, df_basic = fetch_stock_data(start_date_str, end_date_str, symbol, market)

            all_daily_data.append(df_daily)
            all_basic_data.append(df_basic)
            if (i + 1) % 100 == 0:
                logger.info(f"   进度: {i + 1}/{total}")
            
            fail_count = 0 # 重置失败计数

        except Exception as e:
            fail_count += 1
            logger.info(f"获取失败: {symbol} | 原因: {e}")
            
            if fail_count >= MAX_FAIL:
                logger.info(f"[警告] 连续失败次数达到 {MAX_FAIL} 次。")
                break
            continue

    final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()
    final_basic = pd.concat(all_basic_data, ignore_index=True) if all_basic_data else pd.DataFrame()
    logger.info(f"[AkShare] 采集完成。")
    logger.info(f"   - 行情记录: {len(final_daily)} 条")
    logger.info(f"   - 指标记录: {len(final_basic)} 条")

    return final_daily, final_basic

'''
获取深证证券交易所股票
该接口返回:
    板块	object
    A股代码 object
    A股简称 object
    A股上市日期 object
    A股总股本   object
    A股流通股本 object
    所属行业    object
'''
'''
DEPRECATED: 旧申万行业定义接口。
  新流程使用 etl.sync_industry --input 读取 tmp/swclasscode.csv，
  并写入新版 SW_INDUSTRY(sw_version, industry_code, ...)。
获取申万一/二/三级行业定义
返回参数:
    pd.DataFrame -- sw_code / sw_name / sw_level / parent_code
'''
def fetch_sw_industries() -> pd.DataFrame:

    # 一级
    l1 = ak.sw_index_first_info()[['行业代码', '行业名称']].copy()
    l1.columns = ['sw_code', 'sw_name']
    l1['sw_level'] = 1
    l1['parent_code'] = None

    # 二级
    l2_raw = ak.sw_index_second_info()[['行业代码', '行业名称', '上级行业']].copy()
    l2_raw.columns = ['sw_code', 'sw_name', 'parent_name']
    name_to_l1_code = l1.set_index('sw_name')['sw_code'].to_dict()
    l2_raw['sw_level'] = 2
    l2_raw['parent_code'] = l2_raw['parent_name'].map(name_to_l1_code)
    l2 = l2_raw[['sw_code', 'sw_name', 'sw_level', 'parent_code']]

    # 三级（接口不一定存在）
    try:
        l3_raw = ak.sw_index_third_info()[['行业代码', '行业名称', '上级行业']].copy()
        l3_raw.columns = ['sw_code', 'sw_name', 'parent_name']
        name_to_l2_code = l2.set_index('sw_name')['sw_code'].to_dict()
        l3_raw['sw_level'] = 3
        l3_raw['parent_code'] = l3_raw['parent_name'].map(name_to_l2_code)
        l3 = l3_raw[['sw_code', 'sw_name', 'sw_level', 'parent_code']]
    except Exception as e:
        logger.info(f"  [警告] 获取申万三级行业定义失败，跳过: {e}")
        l3 = pd.DataFrame(columns=['sw_code', 'sw_name', 'sw_level', 'parent_code'])

    return pd.concat([l1, l2, l3], ignore_index=True)

'''
DEPRECATED: 旧股票-申万行业映射接口。
  新流程使用 fetch_stock_industry_clf_hist_sw() 获取
  ak.stock_industry_clf_hist_sw() 原始历史数据，并通过视图展开一/二/三级。
遍历行业代码，获取所有股票的申万行业归属
输入参数:
    industry_df: fetch_sw_industries() 返回的行业定义 DataFrame
返回参数:
    pd.DataFrame -- code / sw_l1_code / sw_l1_name / sw_l2_code / sw_l2_name /
                    sw_l3_code / sw_l3_name / entry_date
'''
def fetch_stock_sw_mapping(industry_df: pd.DataFrame) -> pd.DataFrame:

    # 用代码直接查父级，避免名称匹配不一致导致 L1/L2 code 为空
    code_to_row = industry_df.set_index('sw_code').to_dict('index')

    def get_hierarchy(sw_code: str, sw_level: int) -> dict:
        """给定行业代码及级别，推导完整 L1/L2/L3 字段。
        - L3：沿 parent_code 向上推导 L2→L1，L3 有值
        - L2：该代码即为 L2，沿 parent_code 推导 L1，L3 为空（申万未细分到三级）
        """
        row = code_to_row.get(sw_code, {})
        if sw_level == 3:
            l3_code = sw_code
            l3_name = row.get('sw_name', '')
            l2_code = row.get('parent_code') or ''
            l2_row  = code_to_row.get(l2_code, {})
            l1_code = l2_row.get('parent_code') or ''
            l1_row  = code_to_row.get(l1_code, {})
        else:  # sw_level == 2，申万仅归属到二级，无三级
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

    # L3 优先遍历；再补查所有 L2，捕捉申万仅归属到二级的股票
    # concat 后 drop_duplicates(keep='first') 保留 L3 记录
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
            logger.info(f"  [警告] 获取行业 {sw_code} 成分股失败: {e}")

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


_SUMMARY_OUT_COLS = [
    'trade_date', 'exchange_code',
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]

_DETAIL_OUT_COLS = [
    'trade_date', 'exchange_code', 'symbol', 'code', 'security_name',
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]


def _fetch_summary_sse(begin_date: str, end_date: str) -> pd.DataFrame:
    """上交所融资融券汇总：单次区间查询。"""
    try:
        df = ak.stock_margin_sse(start_date=begin_date, end_date=end_date)
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
            df = ak.stock_margin_szse(date=d)
        except Exception as e:
            logger.warning(f"  [WARN] 深交所融资融券汇总 {d} 获取失败: {e}")
            continue
        if df is None or df.empty:
            continue

        # 接口返回多行 (融资融券 / 股票融资融券 / 基金融资融券)，取整体汇总行
        if '项目' in df.columns:
            df = df[df['项目'].astype(str).str.strip() == '融资融券'].copy()
            if df.empty:
                continue

        df = df.rename(columns={
            '数据日期':       'trade_date',
            '融资买入额':     'margin_buy_amount',
            '融资余额':       'margin_balance',
            '融券卖出量':     'short_sell_volume',
            '融券余量金额':   'short_balance_amount',
            '融券余量':       'short_balance_volume',
            '融资融券余额':   'margin_short_balance',
        })
        if 'trade_date' not in df.columns:
            df['trade_date'] = d
        df['trade_date']           = pd.to_datetime(df['trade_date'].astype(str), errors='coerce').dt.date
        df['exchange_code']        = 'SZ'
        df['margin_repay_amount']  = None
        df['short_repay_volume']   = None
        rows.append(df.reindex(columns=_SUMMARY_OUT_COLS))

    if not rows:
        return pd.DataFrame(columns=_SUMMARY_OUT_COLS)
    return pd.concat(rows, ignore_index=True)


'''
获取沪深融资融券每日汇总数据 (MARGIN_SUMMARY_DAILY)。

输入:
  begin_date: 开始日期，格式 YYYYMMDD
  end_date:   结束日期，格式 YYYYMMDD
  exchanges:  交易所列表，元素 sh/sz/all
  trade_dates: 区间内交易日列表 YYYYMMDD，供 SZ 逐日抓取使用
输出列对齐 MARGIN_SUMMARY_DAILY。
'''
def fetch_margin_summary(begin_date: str, end_date: str,
                         exchanges: list[str],
                         trade_dates: list[str]) -> pd.DataFrame:

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


'''
获取指定交易日的沪深融资融券明细 (MARGIN_DETAIL_DAILY)。

输入:
  trade_date: 交易日字符串 YYYYMMDD
  exchanges:  交易所列表，元素 sh/sz/all
输出列对齐 MARGIN_DETAIL_DAILY。
'''
def fetch_margin_detail(trade_date: str, exchanges: list[str]) -> pd.DataFrame:

    target = set(e.lower() for e in exchanges)
    want_sh = ('sh' in target) or ('all' in target)
    want_sz = ('sz' in target) or ('all' in target)

    trade_dt = datetime.strptime(trade_date, '%Y%m%d').date()
    dfs: list[pd.DataFrame] = []

    if want_sz:
        try:
            df_sz = ak.stock_margin_detail_szse(date=trade_date)
            if df_sz is not None and not df_sz.empty:
                df_sz = df_sz.rename(columns={
                    '证券代码':     'symbol',
                    '证券简称':     'security_name',
                    '融资买入额':   'margin_buy_amount',
                    '融资余额':     'margin_balance',
                    '融券卖出量':   'short_sell_volume',
                    '融券余量':     'short_balance_volume',
                    '融券余额':     'short_balance_amount',
                    '融资融券余额': 'margin_short_balance',
                })
                # 只保留 A 股 (代码以 0 或 3 开头的 6 位数字)
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
            df_sh = ak.stock_margin_detail_sse(date=trade_date)
            if df_sh is not None and not df_sh.empty:
                df_sh = df_sh.rename(columns={
                    '标的证券代码':   'symbol',
                    '标的证券简称':   'security_name',
                    '融资余额':       'margin_balance',
                    '融资买入额':     'margin_buy_amount',
                    '融资偿还额':     'margin_repay_amount',
                    '融券余量':       'short_balance_volume',
                    '融券卖出量':     'short_sell_volume',
                    '融券偿还量':     'short_repay_volume',
                })
                # 只保留 A 股 (代码以 6 开头的 6 位数字)
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
    return pd.concat(dfs, ignore_index=True).reset_index(drop=True)


'''
获取股票申万行业分类历史原始数据
接口: ak.stock_industry_clf_hist_sw()
返回:
  symbol / start_date / industry_code / update_time
'''
def fetch_stock_industry_clf_hist_sw() -> pd.DataFrame:

    OUT_COLS = ['symbol', 'start_date', 'industry_code', 'update_time']

    try:
        df = ak.stock_industry_clf_hist_sw()
        if df is None or df.empty:
            logger.warning("stock_industry_clf_hist_sw 未返回数据")
            return pd.DataFrame(columns=OUT_COLS)

        missing = [c for c in OUT_COLS if c not in df.columns]
        if missing:
            logger.error(f"stock_industry_clf_hist_sw 缺少字段: {missing}")
            return pd.DataFrame(columns=OUT_COLS)

        result = df[OUT_COLS].copy()
        result = result.dropna(subset=['symbol', 'start_date', 'industry_code'])
        result['symbol'] = result['symbol'].astype(str).str.strip().str.zfill(6)
        result['industry_code'] = result['industry_code'].astype(str).str.strip()
        result['start_date'] = pd.to_datetime(result['start_date'], errors='coerce').dt.date
        result['update_time'] = pd.to_datetime(result['update_time'], errors='coerce')
        result = result.dropna(subset=['start_date'])
        result = result[(result['symbol'] != '') & (result['industry_code'] != '')]
        result = result.drop_duplicates(subset=['symbol', 'start_date', 'industry_code'], keep='last')

        logger.info(f"[成功] 获取股票申万行业历史 {len(result)} 条")
        return result[OUT_COLS].reset_index(drop=True)
    except Exception as e:
        logger.error(f"获取股票申万行业历史失败: {e}")
        return pd.DataFrame(columns=OUT_COLS)
