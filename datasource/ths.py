# 修改记录:
#   2026-09-13  Claude  新建同花顺除权事件取数源(复权因子外部校验)
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
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

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


def ms_to_cn_date(ms) -> datetime.date:
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


def load_xdr_events(parquet_path) -> pd.DataFrame:
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
