import struct
import logging
import os
import pandas as pd
from functools import wraps
from datetime import date, datetime
from pathlib import Path
from util import myutil, dbutil

logger = logging.getLogger("etl.datasource.lday")


def fetch_stock_data(begin: str, end: str, code_file: str, std_code: str,
                     trade_dates: list[str]) -> pd.DataFrame:
    """读取 .day 文件，返回指定日期区间的日K数据（含停牌占位行）

    Parameters
    ----------
    begin / end  : YYYYMMDD 格式字符串，如 "20251224"
    code_file    : .day 文件路径
    std_code     : 标准代码，如 "600000.SH"
    trade_dates  : 区间内所有交易日（YYYYMMDD 升序），用于生成停牌占位行
    """
    record_struct = struct.Struct("<IIIIIfII")
    raw_records: dict[str, dict] = {}
    begin_int = int(begin)
    end_int = int(end)
    prev_close: float | None = None

    try:
        with open(code_file, 'rb') as f:
            while True:
                chunk = f.read(record_struct.size)
                if len(chunk) < record_struct.size:
                    break

                raw = record_struct.unpack(chunk)
                date_int = raw[0]
                close = raw[4] / 100.0

                if date_int > end_int:
                    break  # .day 文件按日期升序，超出 end 直接停

                if date_int < begin_int:
                    prev_close = close  # 记录 begin 前最后一个收盘，用作首日 pre_close
                    continue

                d_str = str(date_int)
                raw_records[d_str] = {
                    "open":   raw[1] / 100.0,
                    "high":   raw[2] / 100.0,
                    "low":    raw[3] / 100.0,
                    "close":  close,
                    "volume": int(raw[6]),
                    "amount": float(raw[5]),
                }

    except Exception as e:
        logger.error(f"读取文件 {code_file} 出错: {e}")
        return pd.DataFrame()

    target_dates = [d for d in trade_dates if begin <= d <= end]
    if not target_dates:
        return pd.DataFrame()

    all_records = []
    last_close = prev_close  # 动态追踪最近一个真实收盘价

    for td in target_dates:  # trade_dates 已按升序排列
        clean_date = f"{td[:4]}-{td[4:6]}-{td[6:]}"
        if td in raw_records:
            r = raw_records[td]
            all_records.append({
                "code":        std_code,
                "date":        clean_date,
                "open":        r["open"],
                "high":        r["high"],
                "low":         r["low"],
                "close":       r["close"],
                "pre_close":   last_close if last_close is not None else float('nan'),
                "volume":      r["volume"],
                "amount":      r["amount"],
                "tradestatus": 1,
            })
            last_close = r["close"]
        elif last_close is not None:
            # 停牌占位：四价填前收，量额填 0
            all_records.append({
                "code":        std_code,
                "date":        clean_date,
                "open":        last_close,
                "high":        last_close,
                "low":         last_close,
                "close":       last_close,
                "pre_close":   last_close,
                "volume":      0,
                "amount":      0.0,
                "tradestatus": 0,
            })
        # last_close is None 说明该股尚无历史收盘（上市首日即停牌），跳过

    if not all_records:
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    return df[["code", "date", "open", "high", "low", "close", "pre_close", "tradestatus", "volume", "amount"]]

@myutil.timer
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """批量读取本地 .day 文件日K数据

    Parameters
    ----------
    stock_list : list of (symbol, market, begin_date, end_date, status) tuples
    Returns (daily_df, basic_df)
    注意: .day 文件只记录有成交的交易日，停牌日在文件中缺失，无法生成 tradestatus=0 的记录
    """
    all_dfs = []
    total = len(stock_list)
    count = 0

    active = [(b, e) for _, _, b, e, st in stock_list if st != 'D']
    if not active:
        logger.warning("未获取到任何有效数据")
        return pd.DataFrame(), pd.DataFrame()

    min_begin = min(b for b, _ in active)
    max_end   = max(e for _, e in active)
    trade_dates = dbutil.get_trade_dates(min_begin, max_end)  # YYYYMMDD 升序列表

    logger.info(f"[本地] 开始获取数据，共计 {total} 只股票...")
    for symbol, market, begindate, enddate, status in stock_list:
        if status == "D":
            continue
        count += 1
        code_file: Path | None = None
        try:
            code_file = myutil.get_lday_path(market.lower()) / f"{market.lower()}{symbol}.day"
        except Exception as e:
            logger.warning(f"获取失败: {symbol}.{market} | 原因: 无法定位day文件目录: {e}")
            continue

        if not code_file or not code_file.is_file():
            logger.warning(f"获取失败: {symbol}.{market} | 原因: 文件不存在: {code_file}")
            continue
        logger.debug(code_file)
        try:
            df_one = fetch_stock_data(
                begin       = datetime.strptime(begindate, "%Y-%m-%d").strftime("%Y%m%d"),
                end         = datetime.strptime(enddate,   "%Y-%m-%d").strftime("%Y%m%d"),
                code_file   = code_file,
                std_code    = f"{symbol}.{market.upper()}",
                trade_dates = trade_dates,
            )
            if not df_one.empty:
                all_dfs.append(df_one)
            if count % 1000 == 0:
                logger.info(f"   已处理: {count}/{total}")
        except Exception as e:
            logger.warning(f"获取失败: {symbol}.{market} | 原因: {e}")
            continue

    if all_dfs:
        final_df = pd.concat(all_dfs, ignore_index=True)
        basic_df = (
            final_df[['code', 'date']]
            .rename(columns={'date': 'trade_date'})
            .assign(turnover_rate=None, pe=None, pb=None, is_st=None)
        )
        logger.info(f"批量采集完成，成功获取 {len(final_df)} 条记录")
        return final_df, basic_df
    else:
        logger.warning("未获取到任何有效数据")
        return pd.DataFrame(), pd.DataFrame()

def fetch_batch_index(stock_list: list[tuple]) -> pd.DataFrame:
    final_df, _ = fetch_batch_data(stock_list)
    if not final_df.empty:
        final_df["tradestatus"] = 1
    return final_df
    