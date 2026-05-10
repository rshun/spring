import baostock as bs
import logging
import pandas as pd
import socket
import time
from util.myutil import timer
from util.config import get_config

logger = logging.getLogger("etl.datasource.bstock")


def _get_max_fetch_attempts() -> int:
    return get_config()["baostock"]["max_fetch_attempts"]


def _get_retry_delay_login() -> float:
    return get_config()["baostock"]["retry_delay_login"]


def _get_retry_delay_pipe() -> float:
    return get_config()["baostock"]["retry_delay_pipe"]


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


@timer
def fetch_sync_calendar(start_date: str, end_date: str):
    """获取交易日历

    Parameters
    ----------
    start_date / end_date : YYYY-MM-DD
    Returns DataFrame(cal_date, is_open) or None
    """
    lg = bs.login()
    if getattr(lg, "error_code", None) != "0":
        logger.error(f"baostock login failed: {getattr(lg, 'error_msg', '')}")
        return None
    try:
        rs = bs.query_trade_dates(
            start_date=start_date,
            end_date=end_date
        )
        if rs.error_code != '0':
            logger.warning(f"查询失败: {rs.error_msg}")
            return None
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
        logger.error(f"运行中发生错误: {e}")
        return None
    finally:
        bs.logout()


def fetch_stock_info(exchanges: list) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """获取全市场股票基本信息

    Parameters
    ----------
    exchanges : 交易所列表，如 ['SH', 'SZ'] 或 ['all']
    Returns (df_stock_info, None)
    """
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


def fetch_stock_data(begin_date: str, end_date: str, bs_code: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """拉取单只股票历史日K及基本面数据

    Parameters
    ----------
    begin_date / end_date : YYYY-MM-DD
    bs_code : Baostock 格式代码，如 "sh.600000"
    Returns (df_daily, df_basic)
    """
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
        _raise_for_query_error(bs_code, rs.error_msg)

    data_list = []
    while rs.next():
        data_list.append(rs.get_row_data())

    if not data_list:
        return pd.DataFrame(), pd.DataFrame()

    df_raw = pd.DataFrame(data_list, columns=rs.fields)

    parts = df_raw["code"].astype("string").str.split(".", n=1, expand=True)
    if parts.shape[1] != 2:
        return pd.DataFrame(), pd.DataFrame()

    df_raw["mkt"] = parts[0]
    df_raw["sym"] = parts[1]
    df_raw["std_code"] = df_raw["sym"].str.cat(df_raw["mkt"].str.upper(), sep=".")

    # 价格/量 NaN 填 0；财务指标（PE/PB/换手）停牌时本就无值，保留 NaN
    for col in ["open", "high", "low", "close", "preclose", "volume", "amount"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce").fillna(0)
    for col in ["turn", "peTTM", "pbMRQ"]:
        df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

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


@timer
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """批量获取股票日K及基本面数据

    Parameters
    ----------
    stock_list : list of (symbol, market, begin_date, end_date, status) tuples
    Returns (daily_df, basic_df)
    """
    total = len(stock_list)
    processed = 0
    all_daily_data: list[pd.DataFrame] = []
    all_basic_data: list[pd.DataFrame] = []

    socket.setdefaulttimeout(30)
    lg = bs.login()
    if lg.error_code != "0":
        logger.error(f"[Baostock] 登录失败: {lg.error_msg}")
        return pd.DataFrame(), pd.DataFrame()

    try:
        logger.info(f"[baostock插件] 开始获取交易明细数据，共计 {total} 只股票...")

        for symbol, market, begindate, enddate, status in stock_list:
            symbol = str(symbol)
            market = str(market)

            if symbol.startswith("9"):
                continue
            if status == "D":
                continue

            processed += 1
            bs_code = f"{market.lower()}.{symbol}"

            for attempt in range(_get_max_fetch_attempts()):
                try:
                    df_daily, df_basic = fetch_stock_data(begindate, enddate, bs_code)

                    if not df_daily.empty:
                        all_daily_data.append(df_daily)
                    if not df_basic.empty:
                        all_basic_data.append(df_basic)

                    break

                except BaoNotLoggedInError:
                    if attempt < _get_max_fetch_attempts() - 1:
                        logger.warning(f"[Baostock] 会话失效({bs_code})，重新登录后重试...")
                        relogin()
                        time.sleep(_get_retry_delay_login())
                        continue

                    logger.warning(f"[Baostock] 会话恢复失败，跳过 {bs_code}")
                    break

                except Exception as e:
                    if _is_broken_pipe_error(e) and attempt < _get_max_fetch_attempts() - 1:
                        logger.warning(f"[Baostock] 连接中断({bs_code}): {e}，重新登录后重试...")
                        relogin()
                        time.sleep(_get_retry_delay_pipe())
                        continue

                    if isinstance(e, BaoQueryError):
                        logger.warning(f"[Baostock] 获取日线失败({bs_code}): {e}")
                        break

                    logger.warning(f"获取失败: {symbol}.{market.upper()} | 原因: {e}")
                    break

            if processed % 100 == 0:
                logger.info(f"   已处理: {processed}/{total}")

        final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()
        final_basic = pd.concat(all_basic_data, ignore_index=True) if all_basic_data else pd.DataFrame()

        logger.info(f"批量采集完成，成功获取 {len(final_daily)} 条记录")
        return final_daily, final_basic

    finally:
        bs.logout()


@timer
def fetch_adjust_factors(stock_list: list[tuple]) -> pd.DataFrame:
    """批量获取复权因子

    Parameters
    ----------
    stock_list : list of (symbol, market, start_date, end_date, status) tuples
    Returns DataFrame(code, date, fore_factor, back_factor, adjust_factor)
    """
    all_dfs: list[pd.DataFrame] = []
    total_stocks = len(stock_list)
    count = 0

    socket.setdefaulttimeout(30)
    lg = bs.login()
    if lg.error_code != "0":
        logger.error(f"[Baostock] 登录失败: {lg.error_msg}")
        return pd.DataFrame()

    try:
        for symbol, market, start_date, end_date, status in stock_list:
            symbol = str(symbol)
            market = str(market)
            count += 1
            if symbol.startswith('9'):
                continue
            if status == "D":
                continue
            bs_code = f"{market.lower()}.{symbol}"

            for attempt in range(_get_max_fetch_attempts()):
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

                    rows = []
                    while rs_factor.next():
                        rows.append(rs_factor.get_row_data())
                    if rows:
                        df_f = pd.DataFrame(rows, columns=rs_factor.fields)
                        df_f["code"] = std_code
                        df_f = df_f.rename(columns={
                            "dividOperateDate": "date",
                            "foreAdjustFactor": "fore_factor",
                            "backAdjustFactor": "back_factor",
                            "adjustFactor":     "adjust_factor",
                        })
                        all_dfs.append(df_f[["code", "date", "fore_factor", "back_factor", "adjust_factor"]])

                    break

                except BaoNotLoggedInError:
                    if attempt < _get_max_fetch_attempts() - 1:
                        logger.warning(f"[Baostock] 会话失效({bs_code})，重新登录后重试...")
                        relogin()
                        time.sleep(_get_retry_delay_login())
                        continue

                    logger.warning(f"[Baostock] 会话恢复失败，跳过 {bs_code}")
                    break

                except Exception as e:
                    if _is_broken_pipe_error(e) and attempt < _get_max_fetch_attempts() - 1:
                        logger.warning(f"[Baostock] 连接中断({bs_code}): {e}，重新登录后重试...")
                        relogin()
                        time.sleep(_get_retry_delay_pipe())
                        continue

                    if isinstance(e, BaoQueryError):
                        logger.warning(f"[Baostock] 获取复权因子失败({bs_code}): {e}")
                        break

                    logger.warning(f"[Baostock] 获取复权因子失败({bs_code}): {e}")
                    break

            if count % 100 == 0:
                logger.info(f"   已处理: {count}/{total_stocks}")

        return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    finally:
        bs.logout()


def fetch_index_data(begin_date: str, end_date: str, bs_code: str) -> pd.DataFrame:
    """拉取单只指数历史日K

    Parameters
    ----------
    begin_date / end_date : YYYY-MM-DD
    bs_code : Baostock 格式代码，如 "sh.000001"
    """
    fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
    rs = bs.query_history_k_data_plus(
        bs_code,
        fields,
        start_date=begin_date,
        end_date=end_date,
        frequency="d"
    )

    if rs.error_code != "0":
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
        "code":         df_raw["std_code"],
        "date":         df_raw["date"],
        "open":         df_raw["open"],
        "high":         df_raw["high"],
        "low":          df_raw["low"],
        "close":        df_raw["close"],
        "pre_close":    p_close,
        "volume":       vol,
        "amount":       amt,
        "trade_status": 1
    })

    return df_daily


@timer
def fetch_batch_index(index_list: list[tuple]) -> pd.DataFrame:
    """批量获取指数日K数据

    Parameters
    ----------
    index_list : list of (symbol, market, begin_date, end_date, status) tuples
    Returns daily_df
    """
    total = len(index_list)
    processed = 0
    all_daily_data: list[pd.DataFrame] = []

    socket.setdefaulttimeout(30)
    lg = bs.login()
    if lg.error_code != "0":
        logger.error(f"[Baostock] 登录失败: {lg.error_msg}")
        return pd.DataFrame()

    try:
        logger.info(f"[baostock插件] 开始获取交易明细数据，共计 {total} 只指数...")

        for symbol, market, begindate, enddate, status in index_list:
            symbol = str(symbol)
            market = str(market)

            if symbol.startswith("9"):
                continue
            if status == "D":
                continue

            processed += 1
            bs_code = f"{market.lower()}.{symbol}"

            for attempt in range(_get_max_fetch_attempts()):
                try:
                    df_daily = fetch_index_data(begindate, enddate, bs_code)

                    if not df_daily.empty:
                        all_daily_data.append(df_daily)

                    break

                except BaoNotLoggedInError:
                    if attempt < _get_max_fetch_attempts() - 1:
                        logger.warning(f"[Baostock] 会话失效({bs_code})，重新登录后重试...")
                        relogin()
                        time.sleep(_get_retry_delay_login())
                        continue

                    logger.warning(f"[Baostock] 会话恢复失败，跳过 {bs_code}")
                    break

                except Exception as e:
                    if _is_broken_pipe_error(e) and attempt < _get_max_fetch_attempts() - 1:
                        logger.warning(f"[Baostock] 连接中断({bs_code}): {e}，重新登录后重试...")
                        relogin()
                        time.sleep(_get_retry_delay_pipe())
                        continue

                    if isinstance(e, BaoQueryError):
                        logger.warning(f"[Baostock] 获取指数失败({bs_code}): {e}")
                        break

                    logger.warning(f"获取失败: {symbol}.{market.upper()} | 原因: {e}")
                    break

            if processed % 100 == 0:
                logger.info(f"   已处理: {processed}/{total}")

        final_daily = pd.concat(all_daily_data, ignore_index=True) if all_daily_data else pd.DataFrame()

        logger.info(f"批量采集完成，成功获取 {len(final_daily)} 条记录")
        return final_daily
    finally:
        bs.logout()
