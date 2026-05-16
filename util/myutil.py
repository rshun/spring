import time
import os
import pkgutil
import importlib
import logging
import sys
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from types import ModuleType

logger = logging.getLogger("etl.util.myutil")

def configure_etl_logging() -> None:
    """配置 ETL 共享日志输出

    日志文件: ~/log/stockdailyYYYYMMDD.log
    每行格式: HH:MM:SS [module_name] [LEVEL] message
    """
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
    except Exception as e:
        print(f"[etl.util.myutil] 配置文件日志失败: {e}", file=sys.stderr)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    etl_logger.addHandler(ch)
    etl_logger._configured = True


def get_default_dbfile() -> Path:
    """获取默认的数据库名

    返回:
        数据库的文件名
    """
    from util.config import get_config
    db_path = (get_config().get("local_paths") or {}).get("db", "").strip()
    if not db_path:
        db_path = str(Path.home() / "data" / "quant.db")
    return Path(db_path).expanduser()


def ensure_dbfile_dir() -> Path:
    """确保数据库文件所在目录存在，返回数据库文件路径"""
    db_path = get_default_dbfile()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_lday_path(market: str | None = None) -> Path:
    """获取lday路径

    返回:
        lday路径
    """
    if os.name == "nt":
        if not market or not str(market).strip():
            raise ValueError("Windows 环境下必须传入 market（Vipdoc 下的市场目录名，例如 'sh'/'sz'）。")
        market_dir = str(market).strip()
        # 延迟导入避免循环依赖（util.config 不依赖 myutil）
        from util.config import get_config
        vipdoc_root = (get_config().get("local_paths") or {}).get("tdx_vipdoc")
        if not vipdoc_root:
            raise ValueError("config.yaml 缺少 local_paths.tdx_vipdoc 配置")
        lday_path = Path(vipdoc_root) / market_dir / "lday"
    else:
        lday_path = Path.home() / "data" / "lday"

    if not lday_path.is_dir():
        raise FileNotFoundError(f"目录不存在: {lday_path}")
    return lday_path


def get_sql_file() -> Path:
    """获取sql文件路径

    返回:
        sql文件路径
    """
    root = Path(__file__).resolve().parents[1]
    schema_sql = root / "sql" / "schema.sql"
    if not schema_sql.is_file():
        raise FileNotFoundError(f"错误：找不到数据库架构文件 '{schema_sql}'。请确保该文件存在。")

    return schema_sql


def timer(func):
    """计时装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        logger_name = func.__module__ if func.__module__.startswith("etl.") else f"etl.{func.__module__}"
        logging.getLogger(logger_name).info(f"[{func.__name__}] 耗时: {end - start:.2f} 秒")
        return result
    return wrapper


def import_source_module(source: str, package: str = "datasource") -> ModuleType:
    """导入取数模块"""
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


def dbfile_exists() -> bool:
    """判断数据库文件是否存在"""
    return get_default_dbfile().exists()


def get_today() -> str:
    """获取当天日期

    返回:
        YYYYMMDD
    """
    return datetime.now().strftime("%Y%m%d")


def get_yesterday() -> str:
    """获取昨天日期

    返回:
        YYYYMMDD
    """
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


def trans_datestr_format(yyyymmdd: str) -> str:
    """转换日期格式

    参数:
        yyyymmdd: 日期字符串 (格式: YYYYMMDD)
    返回:
        日期字符串 (格式: YYYY-MM-DD)
    """
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"日期格式错误: {yyyymmdd}") from e
