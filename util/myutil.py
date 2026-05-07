import time
import os
import csv
import math
import pkgutil
import importlib
import logging
import sys
import pandas as pd
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from types import ModuleType

logger = logging.getLogger("etl.util.myutil")

"""
配置 ETL 共享日志输出
  日志文件: ~/log/stockdailyYYYYMMDD.log
  每行格式: HH:MM:SS [module_name] [LEVEL] message
"""
def configure_etl_logging() -> None:
    etl_logger = logging.getLogger("etl")
    if getattr(etl_logger, "_configured", False):
        return

    etl_logger.setLevel(logging.INFO)
    etl_logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    try:
        log_dir = Path(__file__).resolve().parents[1] / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"stockdaily{datetime.now().strftime('%Y%m%d')}.log"

        fh = logging.FileHandler(log_path, encoding="utf-8", delay=True)
        fh.setFormatter(fmt)
        etl_logger.addHandler(fh)
    except Exception:
        pass

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    etl_logger.addHandler(ch)
    etl_logger._configured = True


"""
配置 Strategy 共享日志输出
  日志文件: ~/log/reportYYYYMMDD.log
  每行格式: HH:MM:SS [module_name] [LEVEL] message
"""
def configure_strategy_logging() -> None:
    strat_logger = logging.getLogger("strategy")
    if getattr(strat_logger, "_configured", False):
        return

    strat_logger.setLevel(logging.INFO)
    strat_logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )

    try:
        log_dir = Path(__file__).resolve().parents[1] / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"report{datetime.now().strftime('%Y%m%d')}.log"

        fh = logging.FileHandler(log_path, encoding="utf-8", delay=True)
        fh.setFormatter(fmt)
        strat_logger.addHandler(fh)
    except Exception:
        pass

    # Windows 终端默认 GBK 编码，强制 UTF-8 避免中文乱码
    import io
    utf8_stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    ch = logging.StreamHandler(utf8_stream)
    ch.setFormatter(fmt)
    strat_logger.addHandler(ch)
    strat_logger._configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


"""
获取默认的数据库名
  返回:
    数据库的文件名
"""
def get_default_dbfile() -> Path:

    db_path = os.environ.get("DUCKDB_PATH", "").strip()
    if db_path:
        return Path(db_path).expanduser()
    return Path.home() / "data" / "quant.db"


"""
获取lday路径
  返回:
    lday路径
"""
def get_lday_path(market: str | None = None) -> Path:

    if os.name == "nt":
        if not market or not str(market).strip():
            raise ValueError("Windows 环境下必须传入 market（Vipdoc 下的市场目录名，例如 'sh'/'sz'）。")
        market_dir = str(market).strip()
        lday_path = Path(r"C:\new_zszq_cf\Vipdoc") / market_dir / "lday"
    else:
        lday_path = Path.home() / "data" / "lday"

    if not lday_path.is_dir():
        raise FileNotFoundError(f"目录不存在: {lday_path}")
    return lday_path


"""
获取sql文件路径
  返回:
    sql文件路径
"""
def get_sql_file() -> Path:

    root = Path(__file__).resolve().parents[1]
    schema_sql = root / "sql" / "schema.sql"
    if not schema_sql.is_file():
        raise FileNotFoundError(f"错误：找不到数据库架构文件 '{schema_sql}'。请确保该文件存在。")

    return schema_sql


"""
计时
"""
def timer(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger_name = func.__module__ if func.__module__.startswith("etl.") else f"etl.{func.__module__}"
        logging.getLogger(logger_name).info(f"[{func.__name__}] 耗时: {end - start:.2f} 秒")
        return result
    return wrapper


"""
导入取数模块
"""
def import_source_module(source: str, package: str = "datasource") -> ModuleType:

    source = (source or "").strip()
    if not source:
        raise ValueError("source 不能为空")

    module_name = source if "." in source else f"{package}.{source}"

    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        try:
            pkg = importlib.import_module(package)
            avail = sorted([m.name for m in pkgutil.iter_modules(pkg.__path__)])
        except Exception:
            avail = []

        msg = f"无法导入模块: {module_name}"
        if avail:
            msg += f"\n可用模块: {', '.join(avail)}"
        raise ImportError(msg) from e


"""
判断数据库文件是否存在
"""
def dbfile_exists() -> bool:
    return get_default_dbfile().exists()


"""
将内容输出到指定文件
"""
def output_csv_file(csv_path: str, header: str, rows: list[tuple]) -> None:

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


"""
获取当天日期
  返回:
    YYYYMMDD
"""
def get_today() -> str:
    return datetime.now().strftime("%Y%m%d")


"""
获取昨天日期
  返回:
    YYYYMMDD
"""
def get_yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


"""
转换日期格式
  参数:
    yyyymmdd: 日期字符串 (格式: YYYYMMDD)
  返回:
    日期字符串 (格式: YYYY-MM-DD)
"""
def trans_datestr_format(yyyymmdd: str) -> str:
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"日期格式错误: {yyyymmdd}")


def calc_accurate_limit(row, side='up'):

    pre = row['pre_close']
    board = row['board']

    # 获取适用比例
    rate = row['up_rate'] if side == 'up' else (
        row['down_rate'] if pd.notna(row['down_rate']) else row['up_rate']
    )

    if rate == 0:
        return 999999.99 if side == 'up' else 0.01

    # 逻辑分流点
    if board == 'BJ':
        # --- 北交所：截断算法 (Floor/Ceil) ---
        if side == 'up':
            # 涨停：向下取整，严禁越界 30.00...1%
            return math.floor(pre * (1 + rate) * 100 + 0.0001) / 100.0
        else:
            # 跌停：向上取整
            return math.ceil(pre * (1 - rate) * 100 - 0.0001) / 100.0
    else:
        # --- 沪深两市（主板/科创/创业）：四舍五入算法 ---
        # 即使超过 10.000...1% 或 20.000...1% 也是合规的
        if side == 'up':
            return round(pre * (1 + rate) + 0.000001, 2)
        else:
            return round(pre * (1 - rate) + 0.000001, 2)
