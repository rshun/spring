import baostock as bs
import logging
import pandas as pd
import time
from util.myutil import timer
from datetime import datetime

logger = logging.getLogger("etl.datasource.bstock")
MAX_FETCH_ATTEMPTS = 3

def relogin():
    try:
        bs.logout()
    except Exception:
        pass
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"[Baostock] 重新登录失败: {lg.error_msg}")

class BaoNotLoggedInError(RuntimeError):
    pass


class BaoQueryError(RuntimeError):
    pass


def _is_not_logged_in(error_msg: str | None) -> bool:
    msg = (error_msg or "").strip()
    return "用户未登录" in msg


def _is_broken_pipe_error(exc: Exception) -> bool:
    if isinstance(exc, BrokenPipeError):
        return True

    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 32:
        return True

    return "Broken pipe" in str(exc)


def _raise_for_query_error(bs_code: str, error_msg: str | None) -> None:
    if _is_not_logged_in(error_msg):
        raise BaoNotLoggedInError(f"{bs_code}: {error_msg}")
    if _is_broken_pipe_error(RuntimeError(error_msg or "")):
        raise BrokenPipeError(error_msg or "Broken pipe")
    raise BaoQueryError(f"{bs_code}: {error_msg}")

'''
获取交易日信息
输入参数: 
    start_date: 开始日期 (格式: YYYY-MM-DD)
    end_date:   结束日期 (格式: YYYY-MM-DD)
返回参数: 
    pd.DataFrame -- 包含交易日信息的DataFrame, 字段包括:
        cal_date: 交易日期 (YYYY-MM-DD)
        is_open:  是否为交易日 (1: 是, 0: 否
'''
def fetch_sync_calendar(start_date:str, end_date:str):

    bs.login()
    try:
        rs = bs.query_trade_dates(
            start_date= start_date, 
            end_date  = end_date
            )
        if rs.error_code != '0':
            logger.info(f" 查询失败: {rs.error_msg}")
            return
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
            
        df = pd.DataFrame(data_list, columns=rs.fields)
        df = df.rename(columns={
            'calendar_date': 'cal_date',
            'is_trading_day': 'is_open'
        })
        return df

    except Exception as e:
        logger.info(f" 运行中发生错误: {e}")
        return None
    finally:
        bs.logout()

'''
获取股票基本信息
输入参数: 
    exchange: 交易所代码 (SH/SZ)
返回参数: 
    pd.DataFrame -- 包含股票基本信息的DataFrame
    字段包括:
        code	证券代码
        code_name	证券名称
        ipoDate	上市日期
        outDate	退市日期
        type	证券类型 其中1: 股票 2: 指数 3: 其它 4: 可转债 5: ETF
        status	上市状态 其中1: 上市 0: 退市
'''
def fetch_stock_info(exchanges: list) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    target_exs = set(e.lower() for e in exchanges)

    if "all" not in target_exs:
        if "bj" in target_exs and "sh" not in target_exs and "sz" not in target_exs:
            logger.info("[Baostock] 提示: Baostock 接口暂不支持直接查询北交所(BJ)数据，请使用 akstock 数据源。")
            return pd.DataFrame(), None

    lg = bs.login()
    if getattr(lg, "error_code", None) != "0":
        logger.error(f"baostock login failed: {getattr(lg, 'error_msg', '')}")
        return pd.DataFrame(), None

    try:
        rs = bs.query_stock_basic()

        data_list = []
        while rs.error_code == "0" and rs.next():
            data_list.append(rs.get_row_data())

        df = pd.DataFrame(data_list, columns=rs.fields)
        if df.empty:
            return pd.DataFrame(), None

        # 只保留股票 type=1 和指数 type=2
        df = df[df["type"].astype(str).isin(["1", "2"])].copy()

        # 拆分原始代码 (sh.600509 -> ['sh', '600509'])
        split_code = df["code"].astype(str).str.split(".", n=1, expand=True)
        market_part = split_code[0]  # sh/sz
        symbol_part = split_code[1]  # 6位数字

        # 转换 code: 600509.SH
        df["code"] = symbol_part + "." + market_part.str.upper()
        df["symbol"] = symbol_part
        df["exchange"] = market_part.str.upper()

        # board 分类：先默认 MAIN，再把指数设 INDEX；股票再按前缀细分
        type_str = df["type"].astype(str)
        is_stock = type_str == "1"
        is_index = type_str == "2"

        s = df["symbol"].astype(str)

        df["board"] = "MAIN"
        df.loc[is_index, "board"] = "INDEX"
        df.loc[is_stock & s.str.startswith("9"), "board"] = "BJ"              # BJ：9开头
        df.loc[is_stock & s.str.startswith(("300", "301")), "board"] = "GEM"
        df.loc[is_stock & s.str.startswith(("688", "689")), "board"] = "STAR"

        # 字段映射
        df = df.rename(columns={
            "code_name": "name",
            "ipoDate": "list_date",
            "outDate": "delist_date",
        })

        # 状态映射
        status_map = {
            '1': 'L',  # 上市
            '0': 'D',  # 退市
        }
        df["list_status"] = df["status"].astype(str).map(status_map).fillna("L")

        # 日期字段
        df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
        df["delist_date"] = pd.to_datetime(df["delist_date"], errors="coerce")

        final_cols = ["code", "symbol", "name", "exchange", "board", "list_date", "delist_date", "list_status"]
        df_final = df[final_cols].copy()

        return df_final, None

    finally:
        bs.logout()

'''
获取股票当日或历史明细
输入参数: 
    code: 股票代码, sh或sz.+6位数字代码, 或者指数代码, 如: sh.601398. sh: 上海: sz: 深圳. 此参数不可为空: 
    fields: 指示简称, 支持多指标输入, 以半角逗号分隔, 填写内容作为返回类型的列. 详细指标列表见历史行情指标参数章节, 日线与分钟线参数不同. 此参数不可为空: 
    start: 开始日期（包含）, 格式"YYYY-MM-DD", 为空时取2015-01-01
    end: 结束日期（包含）, 格式"YYYY-MM-DD", 为空时取最近一个交易日
    frequency: 数据类型, 默认为d, 日k线: d=日k线 w=周 m=月 5=5分钟 15=15分钟 30=30分钟 60=60分钟k线数据, 不区分大小写: 指数没有分钟线数据: 周线每周最后一个交易日才可以获取, 月线每月最后一个交易日才可以获取. 
    adjustflag: 复权类型, 默认不复权: 3: 1: 后复权: 2: 前复权. 已支持分钟线 日线 周线 月线前后复权. 
返回参数:
    date    交易所行情日期    格式: YYYY-MM-DD
    code    证券代码    格式: sh.600000。sh: 上海, sz: 深圳
    open    今开盘价格    精度: 小数点后4位; 单位: 人民币元
    high    最高价    精度: 小数点后4位; 单位: 人民币元
    low    最低价    精度: 小数点后4位; 单位: 人民币元
    close    今收盘价    精度: 小数点后4位; 单位: 人民币元
    preclose    昨日收盘价    精度: 小数点后4位; 单位: 人民币元
    volume    成交数量    单位: 股
    amount    成交金额    精度: 小数点后4位; 单位: 人民币元
    adjustflag    复权状态    不复权、前复权、后复权
    turn    换手率    精度: 小数点后6位; 单位: %
    tradestatus    交易状态    1: 正常交易 0: 停牌
    pctChg    涨跌幅（百分比）    精度: 小数点后6位     日涨跌幅=[(指定交易日的收盘价-指定交易日前收盘价)/指定交易日前收盘价]*100%
    peTTM    滚动市盈率    精度: 小数点后6位           (指定交易日的股票收盘价/指定交易日的每股盈余TTM)=(指定交易日的股票收盘价*截至当日公司总股本)/归属母公司股东净利润TTM
    psTTM    滚动市销率    精度: 小数点后6位           (指定交易日的股票收盘价/指定交易日的每股销售额)=(指定交易日的股票收盘价*截至当日公司总股本)/营业总收入TTM
    pcfNcfTTM    滚动市现率    精度: 小数点后6位       (指定交易日的股票收盘价/指定交易日的每股现金流TTM)=(指定交易日的股票收盘价*截至当日公司总股本)/现金以及现金等价物净增加额TTM
    pbMRQ    市净率    精度: 小数点后6位
    isST    是否ST    1是, 0否
'''
def fetch_stock_data(begin_date: str, end_date: str, bs_code: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST,peTTM,pbMRQ"

    rs = bs.query_history_k_data_plus(
        bs_code,
        fields,
        start_date=begin_date,
        end_date=end_date,
        frequency="d",
        adjustflag="3"
    )

    if rs.error_code != "0":
        # 关键：把可恢复的 query 错误升级成异常，交给外层统一重试
        _raise_for_query_error(bs_code, rs.error_msg)

    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())

    if not data_list:
        return pd.DataFrame(), pd.DataFrame()

    df_raw = pd.DataFrame(data_list, columns=rs.fields)

    # 更稳：split + 拼接用 str.cat，避免 VSCode 类型提示
    parts = df_raw["code"].astype("string").str.split(".", n=1, expand=True)
    if parts.shape[1] != 2:
        return pd.DataFrame(), pd.DataFrame()

    df_raw["mkt"] = parts[0]
    df_raw["sym"] = parts[1]
    df_raw["std_code"] = df_raw["sym"].str.cat(df_raw["mkt"].str.upper(), sep=".")

    numeric_cols = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "peTTM", "pbMRQ"]
    for col in numeric_cols:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce").fillna(0)
    
    df_raw["isST"] = pd.to_numeric(df_raw["isST"], errors="coerce").fillna(0).astype(int)

    df_daily = pd.DataFrame({
        "code":         df_raw["std_code"],
        "date":         df_raw["date"],
        "open":         df_raw["open"],
        "high":         df_raw["high"],
        "low":          df_raw["low"],
        "close":        df_raw["close"],
        "pre_close":    df_raw["preclose"],
        "volume":       df_raw["volume"].astype(int),
        "amount":       df_raw["amount"],
        "trade_status": df_raw["tradestatus"]
    })

    df_basic = pd.DataFrame({
        "code":          df_raw["std_code"],
        "trade_date":    df_raw["date"],
        "turnover_rate": df_raw["turn"],
        "pe":            df_raw["peTTM"],
        "pb":            df_raw["pbMRQ"],
        "is_st":         df_raw["isST"],
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
    processed = 0
    all_daily_data: list[pd.DataFrame] = []
    all_basic_data: list[pd.DataFrame] = []

    lg = bs.login()
    if lg.error_code != "0":
        logger.info(f"[Baostock] 登录失败: {lg.error_msg}")
        return pd.DataFrame(), pd.DataFrame()

    try:
        logger.info(f"[baostock插件] 开始获取交易明细数据，共计 {total} 只股票...")

        for symbol, market, begindate, enddate,status in stock_list:
            symbol = str(symbol)
            market = str(market)

            # BJ(9开头) 跳过
            if symbol.startswith("9"):
                continue
            # 退市股票跳过
            if status == "D":
                continue

            processed += 1
            bs_code = f"{market.lower()}.{symbol}"

            # 最多尝试 3 次：首次失败后允许重登并重试
            for attempt in range(MAX_FETCH_ATTEMPTS):
                try:
                    df_daily, df_basic = fetch_stock_data(begindate, enddate, bs_code)

                    if not df_daily.empty:
                        all_daily_data.append(df_daily)
                    if not df_basic.empty:
                        all_basic_data.append(df_basic)

                    break  # 成功，跳出重试循环

                except BaoNotLoggedInError:
                    if attempt < MAX_FETCH_ATTEMPTS - 1:
                        logger.info(f"[Baostock] 会话失效({bs_code})，重新登录后重试...")
                        relogin()
                        time.sleep(0.2)
                        continue

                    logger.info(f"[Baostock] 会话恢复失败，跳过 {bs_code}")
                    break

                except Exception as e:
                    if _is_broken_pipe_error(e) and attempt < MAX_FETCH_ATTEMPTS - 1:
                        logger.info(f"[Baostock] 连接中断({bs_code}): {e}，重新登录后重试...")
                        relogin()
                        time.sleep(0.5)
                        continue

                    if isinstance(e, BaoQueryError):
                        logger.info(f"[Baostock] 获取日线失败({bs_code}): {e}")
                        break

                    logger.info(f"\n 获取失败: {symbol}.{market.upper()} | 原因: {e}")
                    break

            if processed % 100 == 0:
                logger.info(f"   已处理: {processed}/{total}")

        final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()
        final_basic = pd.concat(all_basic_data, ignore_index=True) if all_basic_data else pd.DataFrame()

        logger.info(f" 批量采集完成，成功获取 {len(final_daily)} 条记录")
        return final_daily, final_basic

    finally:
        bs.logout()

'''
  获取股票的复权因子
输入参数:
   stock_list: 元组---股票代码(6位数字), 交易所(SH,SZ,BJ),起始和结束日期
返回参数:
    pd--复权因子数据
'''
@timer
def fetch_adjust_factors(stock_list: list[tuple]) -> pd.DataFrame:

    all_records = []
    total_stocks = len(stock_list)
    count = 0

    lg = bs.login()
    if lg.error_code != "0":
        logger.info(f"[Baostock] 登录失败: {lg.error_msg}")
        return pd.DataFrame()

    try:
        for symbol, market, start_date, end_date, status in stock_list:
            symbol = str(symbol)
            market = str(market)
            count += 1
            if symbol.startswith('9'):  # baostock不支持北交所股票
                continue
            if status == "D":
                continue
            bs_code = f"{market.lower()}.{symbol}"

            for attempt in range(MAX_FETCH_ATTEMPTS):
                try:
                    rs_factor = bs.query_adjust_factor(
                        code=bs_code,
                        start_date=start_date,
                        end_date=end_date
                    )

                    if rs_factor.error_code != "0":
                        _raise_for_query_error(bs_code, rs_factor.error_msg)

                    src_market, src_symbol = bs_code.split('.')
                    std_code = f"{src_symbol}.{src_market.upper()}"

                    while rs_factor.next():
                        row = rs_factor.get_row_data()
                        all_records.append({
                            'code': std_code,
                            'date': row[1],
                            'fore_factor': float(row[2]),
                            'back_factor': float(row[3]),
                            'adjust_factor': float(row[4])
                        })

                    break

                except BaoNotLoggedInError:
                    if attempt < MAX_FETCH_ATTEMPTS - 1:
                        logger.info(f"[Baostock] 会话失效({bs_code})，重新登录后重试...")
                        relogin()
                        time.sleep(0.2)
                        continue

                    logger.info(f"[Baostock] 会话恢复失败，跳过 {bs_code}")
                    break

                except Exception as e:
                    if _is_broken_pipe_error(e) and attempt < MAX_FETCH_ATTEMPTS - 1:
                        logger.info(f"[Baostock] 连接中断({bs_code}): {e}，重新登录后重试...")
                        relogin()
                        time.sleep(0.5)
                        continue

                    if isinstance(e, BaoQueryError):
                        logger.info(f"[Baostock] 获取复权因子失败({bs_code}): {e}")
                        break

                    logger.info(f"[Baostock] 获取复权因子失败({bs_code}): {e}")
                    break
            
            if count % 100 == 0:
                logger.info(f"   已处理: {count}/{total_stocks}")

        return pd.DataFrame(all_records)
    finally:
        bs.logout()

'''
  获取单笔指数行情数据
'''
def fetch_index_data(begin_date: str, end_date: str, bs_code: str) -> pd.DataFrame:
    fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
    rs = bs.query_history_k_data_plus(
        bs_code,
        fields,
        start_date=begin_date,
        end_date=end_date,
        frequency="d"
    )

    if rs.error_code != "0":
        # 关键：把可恢复的 query 错误升级成异常，交给外层统一重试
        _raise_for_query_error(bs_code, rs.error_msg)

    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())

    if not data_list:
        return pd.DataFrame()

    df_raw = pd.DataFrame(data_list, columns=rs.fields)
    parts = df_raw["code"].astype("string").str.split(".", n=1, expand=True)
    if parts.shape[1] != 2:
        return pd.DataFrame()

    df_raw["mkt"] = parts[0]
    df_raw["sym"] = parts[1]
    df_raw["std_code"] = df_raw["sym"].str.cat(df_raw["mkt"].str.upper(), sep=".")
    vol = pd.to_numeric(df_raw["volume"], errors="coerce").fillna(0).astype("int64")
    amt = pd.to_numeric(df_raw["amount"], errors="coerce")

    p_close = pd.to_numeric(df_raw["preclose"], errors="coerce")

    df_daily = pd.DataFrame({
        "code":   df_raw["std_code"],
        "date":   df_raw["date"],
        "open":   df_raw["open"],
        "high":   df_raw["high"],
        "low":    df_raw["low"],
        "close":  df_raw["close"],
        "pre_close": p_close,
        "volume": vol,
        "amount": amt,
        "tradestatus": 1
    })

    return df_daily

'''
  获取批量指数行情数据
'''
@timer
def fetch_batch_index(index_list: list[tuple]) -> pd.DataFrame:

    total = len(index_list)
    processed = 0
    all_daily_data: list[pd.DataFrame] = []

    lg = bs.login()
    if lg.error_code != "0":
        logger.info(f"[Baostock] 登录失败: {lg.error_msg}")
        return pd.DataFrame()

    try:
        logger.info(f"[baostock插件] 开始获取交易明细数据，共计 {total} 只指数...")

        for symbol, market, begindate, enddate,status in index_list:
            symbol = str(symbol)
            market = str(market)

            # BJ(9开头) 跳过
            if symbol.startswith("9"):
                continue
            if status == "D":
                continue

            processed += 1
            bs_code = f"{market.lower()}.{symbol}"

            # 最多尝试 3 次：首次失败后允许重登并重试
            for attempt in range(MAX_FETCH_ATTEMPTS):
                try:
                    df_daily = fetch_index_data(begindate, enddate, bs_code)

                    if not df_daily.empty:
                        all_daily_data.append(df_daily)

                    break  # 成功，跳出重试循环

                except BaoNotLoggedInError:
                    if attempt < MAX_FETCH_ATTEMPTS - 1:
                        logger.info(f"[Baostock] 会话失效({bs_code})，重新登录后重试...")
                        relogin()
                        time.sleep(0.2)
                        continue

                    logger.info(f"[Baostock] 会话恢复失败，跳过 {bs_code}")
                    break

                except Exception as e:
                    if _is_broken_pipe_error(e) and attempt < MAX_FETCH_ATTEMPTS - 1:
                        logger.info(f"[Baostock] 连接中断({bs_code}): {e}，重新登录后重试...")
                        relogin()
                        time.sleep(0.5)
                        continue

                    if isinstance(e, BaoQueryError):
                        logger.info(f"[Baostock] 获取指数失败({bs_code}): {e}")
                        break

                    logger.info(f"\n 获取失败: {symbol}.{market.upper()} | 原因: {e}")
                    break

            if processed % 100 == 0:
                logger.info(f"   已处理: {processed}/{total}")

        final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()

        logger.info(f" 批量采集完成，成功获取 {len(final_daily)} 条记录")
        return final_daily
    finally:
        bs.logout()
