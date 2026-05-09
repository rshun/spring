import struct
import logging
import os
import pandas as pd
from functools import wraps
from datetime import date, datetime
from pathlib import Path
from util import myutil

logger = logging.getLogger("etl.datasource.lday")


def fetch_stock_data(begin: str, end: str, code_file: str, std_code: str) -> pd.DataFrame:
    """读取 .day 文件，返回指定日期区间的日K数据

    Parameters
    ----------
    begin / end : YYYYMMDD 格式字符串，如 "20251224"
    code_file   : .day 文件路径
    std_code    : 标准代码，如 "600000.SH"
    """

    record_struct = struct.Struct("<IIIIIfII")
    all_records = []
    begin_int = int(begin)
    end_int = int(end)

    try:
        with open(code_file, 'rb') as f:
            while True:
                chunk = f.read(record_struct.size)
                if len(chunk) < record_struct.size:
                    break

                raw = record_struct.unpack(chunk)
                date_int = raw[0] # 获取整数日期，例如 20251224
                if date_int < begin_int or date_int > end_int:
                    continue

                # 将整数日期 (20251224) 转为字符串 "2025-12-24"
                d_str = str(date_int)
                clean_date = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"

                # 构造单条记录
                record = {
                    "code":   std_code,
                    "date":   clean_date,
                    "open":   raw[1] / 100.0,
                    "high":   raw[2] / 100.0,
                    "low":    raw[3] / 100.0,
                    "close":  raw[4] / 100.0,
                    "volume": int(raw[6]),
                    "amount": float(raw[5])
                }
                all_records.append(record)

        if not all_records:
            return pd.DataFrame()
            
        df = pd.DataFrame(all_records)
        
        cols = ["code", "date", "open", "high", "low", "close", "volume", "amount"]
        df = df[cols]
        return df
        
    except Exception as e:
        logger.error(f"读取文件 {code_file} 出错: {e}")
        return pd.DataFrame()

@myutil.timer
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """批量读取本地 .day 文件日K数据

    Parameters
    ----------
    stock_list : list of (symbol, market, begin_date, end_date, status) tuples
    Returns (daily_df, empty_df)
    """
    all_dfs = []
    total = len(stock_list)
    count = 0

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
                begin     = datetime.strptime(begindate, "%Y-%m-%d").strftime("%Y%m%d"),
                end       = datetime.strptime(enddate,   "%Y-%m-%d").strftime("%Y%m%d"), 
                code_file = code_file,
                std_code  = f"{symbol}.{market.upper()}"
                )
            if not df_one.empty:
                all_dfs.append(df_one)
            if count % 100 == 0:
                logger.info(f"   已处理: {count}/{total}")
        except Exception as e:
            logger.warning(f"获取失败: {symbol}.{market} | 原因: {e}")
            continue

    if all_dfs:
        final_df = pd.concat(all_dfs, ignore_index=True)
        logger.info(f"批量采集完成，成功获取 {len(final_df)} 条记录")
        return final_df,pd.DataFrame()
    else:
        logger.warning("未获取到任何有效数据")
        return pd.DataFrame(),pd.DataFrame()

def fetch_batch_index(stock_list: list[tuple]) -> pd.DataFrame:
    final_df, _ = fetch_batch_data(stock_list)
    if not final_df.empty:
        final_df["tradestatus"] = 1
    return final_df
    