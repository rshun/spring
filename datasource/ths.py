# 修改记录:
#   2026-09-13  Claude  新建同花顺除权事件取数源(复权因子外部校验)
#   2026-09-13  Claude  补全 ms_to_cn_date/load_xdr_events 的类型注解(brief 里有,
#                       实现时漏了)
#   2026-09-13  Claude  新增 fetch_xdr_events 在线 API 取数(密钥走环境变量, 不入库)
"""同花顺(fuyao.aicubes.cn)除权事件取数(只取数, 不入库)

两个入口返回**同一套列**, 调用方不关心数据来自本地文件还是接口:
  - load_xdr_events(parquet_path)        本地 dump 全量
  - fetch_xdr_events(code, begin, end)   在线接口, 单只

注意(2026-09-13 实测, 详见 docs/superpowers/specs/2026-09-13-ths-xdr-event-design.md):
  - ex_date_ms 是**本地午夜**的毫秒时间戳。57,226 条是 UTC+8 午夜, 但 4 条 1991 年的
    是 UTC+9(中国 1986-91 实行夏令时)。按 UTC 解释全表偏一天, 按固定 +08 解释那 4 条
    偏一天。必须用 IANA 'Asia/Shanghai'。
  - 数值单位是**每股**, 与 gbbq/CAPITAL_DETAIL 的**每10股**差 10 倍。本模块**不换算**,
    存原始口径, 换算放在对账查询里(表忠实于源, 换算在比对处才看得见)。
  - (thscode, ex_date) **不唯一**, 源数据有 3 组重复。本模块不去重、不合并,
    seq 由写库层分配。
  - ticker 与 currency 两列冗余(实测 ticker 与 thscode 前缀 0 条不一致,
    currency 全为 CNY), 不进输出。
"""
import datetime
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import requests

from util.config import get_config
from util import myutil

logger = logging.getLogger("etl.datasource.ths")

CN_TZ = ZoneInfo("Asia/Shanghai")

XDR_COLUMNS = ["code", "ex_date", "dividend_per_share",
               "per_share_bonus", "allotment_ratio", "allotment_price"]

# parquet 里必须存在的源列
_REQUIRED_PARQUET_COLS = ["thscode", "ex_date_ms", "dividend_per_share",
                          "per_share_bonus", "allotment_ratio", "allotment_price"]

# 判定「无内容行」的四个数值列
_VALUE_COLS = ["dividend_per_share", "per_share_bonus",
               "allotment_ratio", "allotment_price"]


class ThsError(RuntimeError):
    """同花顺取数失败(文件缺失/结构异常/接口错误)"""


def ms_to_cn_date(ms: int | float) -> datetime.date:
    """毫秒时间戳 -> 北京时间日期。

    必须用 IANA 'Asia/Shanghai' 而非固定 +08: 中国 1986-1991 实行过夏令时,
    那几年的本地午夜是 UTC+9, 用固定偏移会让日期偏一天。
    """
    if ms is None:
        raise ValueError("ex_date_ms 不能为空")
    return datetime.datetime.fromtimestamp(float(ms) / 1000.0, CN_TZ).date()


def _drop_empty_rows(df: pd.DataFrame) -> pd.DataFrame:
    """丢弃四个数值列全为 0 的无内容行, 并记数(不静默丢弃)"""
    if df.empty:
        return df
    keep = (df[_VALUE_COLS].fillna(0).abs().sum(axis=1) > 0)
    dropped = int((~keep).sum())
    if dropped:
        logger.info(f"[ths] 丢弃 {dropped} 条无内容行(四个数值字段全为 0)")
    return df[keep].reset_index(drop=True)


def load_xdr_events(parquet_path: str | Path) -> pd.DataFrame:
    """读取本地 dump, 返回列为 XDR_COLUMNS 的帧

    文件不存在抛 ThsError 而非返回空帧: 把「路径配错」伪装成「今天没数据」
    会让调用方以退出码 0 结束, 是本项目反复踩过的坑。
    """
    path = Path(parquet_path)
    if not path.is_file():
        raise ThsError(f"同花顺 dump 文件不存在: {path}")

    con = duckdb.connect()
    try:
        # DESCRIBE 结果第 0 列是 column_name(已实测确认)
        cols = {r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path.as_posix()}')").fetchall()}
        missing = [c for c in _REQUIRED_PARQUET_COLS if c not in cols]
        if missing:
            raise ThsError(f"同花顺 dump 缺少预期列: {missing}; 实际列: {sorted(cols)}")

        df = con.execute(f"""
            SELECT thscode AS code,
                   CAST(to_timestamp(ex_date_ms/1000) AT TIME ZONE 'Asia/Shanghai' AS DATE) AS ex_date,
                   dividend_per_share, per_share_bonus,
                   allotment_ratio, allotment_price
            FROM read_parquet('{path.as_posix()}')
        """).df()
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame(columns=XDR_COLUMNS)

    df["code"] = df["code"].astype(str).str.strip()
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    return _drop_empty_rows(df[XDR_COLUMNS])


def _cfg() -> dict:
    return get_config().get("ths", {})


def _api_key() -> str:
    """从环境变量取 key; .env 由 load_env 载入。绝不回显 key 的值。"""
    myutil.load_env()
    key = os.environ.get("THS_API_KEY")
    if not key:
        raise ThsError("未设置 THS_API_KEY，请在项目根目录 .env 中配置"
                       "（参考 .env.example）")
    return key


def fetch_xdr_events(code: str, begin: str | None = None,
                     end: str | None = None) -> pd.DataFrame:
    """在线拉取单只股票的除权事件, 返回列为 XDR_COLUMNS 的帧

    参数:
        code : 标准代码 '600519.SH'
        begin/end : YYYY-MM-DD, 可选

    接口 thscode 必需且**不支持批量**, 一次只能查一只;
    调用方须自行控制候选集大小(见 etl/sync_xdr_ths.py 只查 gbbq 有事件的股票)。
    """
    cfg = _cfg()
    url = cfg.get("base_url")
    if not url:
        raise ThsError("config.yaml 缺少 ths.base_url")

    params = {"thscode": code}
    if begin:
        params["from"] = begin
    if end:
        params["to"] = end
    headers = {"X-api-key": _api_key()}

    tries = int(cfg.get("tries", 3))
    delay = float(cfg.get("retry_delay", 3))
    timeout = int(cfg.get("request_timeout", 30))

    last_err: Exception | None = None
    payload = None
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception as e:
            last_err = e
            # 不打印 headers: 里面有 key
            logger.warning(f"  [WARN] ths {code} 第 {attempt}/{tries} 次请求失败: {e}")
            if attempt < tries:
                time.sleep(delay)
    if payload is None:
        raise ThsError(f"同花顺接口请求失败({code}): {last_err}")

    ret = payload.get("code")
    if ret is not None and str(ret) != "0":
        # 只回显错误码与 msg, 绝不回显 key
        raise ThsError(f"同花顺接口返回错误码 {ret} ({payload.get('msg', '')})")

    items = payload.get("item") or []
    if not items:
        return pd.DataFrame(columns=XDR_COLUMNS)

    if any("allotment_ratio" not in it or "allotment_price" not in it for it in items):
        logger.warning(f"[ths] {code} 响应缺少 allotment_ratio/allotment_price 字段，"
                       "已补 None；官方文档未承诺返回这两列")

    df = pd.DataFrame({
        "code":               [code for _ in items],
        "ex_date":            [ms_to_cn_date(it["ex_date_ms"]) for it in items],
        "dividend_per_share": [it.get("dividend_per_share") for it in items],
        "per_share_bonus":    [it.get("per_share_bonus") for it in items],
        "allotment_ratio":    [it.get("allotment_ratio") for it in items],
        "allotment_price":    [it.get("allotment_price") for it in items],
    })
    return _drop_empty_rows(df[XDR_COLUMNS])
