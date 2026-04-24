import struct
import logging
import os
import pandas as pd
from functools import wraps
from datetime import date, datetime
from util import myutil

logger = logging.getLogger("etl.datasource.lday")

'''
批量读取data/lday/目录下的day文件
需要注意的是, 如果文件非常多(超过1800个), 内存就会被撑满
'''

'''
  读取文件获取指定区间明细数据
输入参数:
   begin: 起始日期 "2025-01-01"
   end:   结束日期 "2025-12-31"
   code_file: day文件路径
   std_code: 标准代码 "600000.SH"
返回参数:
    pd--明细
'''
def fetch_stock_data(begin: str, end: str, code_file: str, std_code: str) -> pd.DataFrame:

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
        logger.info(f"读取文件 {code_file} 出错: {e}")
        return pd.DataFrame()

'''
批量获取股票数据
输入参数: 
   stock_list: 元组---代码代码(6位数字), 交易所(SH,SZ,BJ),起始和结束日期
返回参数:
   pd--明细
'''
def fetch_batch_data(stock_list: list[tuple]) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_dfs = []
    total = len(stock_list)
    count = 0

    logger.info(f"[本地] 开始获取数据，共计 {total} 只股票...")
    for symbol, market,begindate,enddate,status in stock_list:
        count += 1
        code_file: Path | None = None
        try:
            code_file = myutil.get_lday_path(market.lower()) / f"{market.lower()}{symbol}.day"
        except Exception as e:
            logger.info(f" 获取失败: {symbol}.{market} | 原因: 无法定位day文件目录: {e}")
            continue

        if not code_file or not code_file.is_file():
            logger.info(f" 获取失败: {symbol}.{market} | 原因: 文件不存在: {code_file}")
            continue
        logger.info(code_file)
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
            logger.info(f"\n 获取失败: {symbol}.{market} | 原因: {e}")
            continue

    if all_dfs:
        final_df = pd.concat(all_dfs, ignore_index=True)
        logger.info(f" 批量采集完成，成功获取 {len(final_df)} 条记录")
        return final_df,pd.DataFrame()
    else:
        logger.info("未获取到任何有效数据")
        return pd.DataFrame(),pd.DataFrame()

def fetch_batch_index(stock_list: list[tuple]) -> tuple[pd.DataFrame]:
    final_df, _ = fetch_batch_data(stock_list)
    if not final_df.empty:
        final_df["tradestatus"] = 1
    return final_df
    