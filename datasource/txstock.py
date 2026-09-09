# 修改记录:
#   2026-09-06  Claude  新增腾讯 fqkline 复权对账数据源(复权因子自建方案 §6.3)
#   2026-09-06  Claude  修复 BUG-003: fetch_xdr_events 拉取起点向前扩
#                       FETCH_LOOKBACK_DAYS 缓冲期(覆盖周末/节假日/停牌),
#                       窗口首日事件也能拿到前一交易日计算 jump_ratio,
#                       事件提取后裁剪回请求窗口
#   2026-09-06  Claude  修复 BUG-005: fetch_xdr_events 改返回
#                       (events_df, failed_codes) 二元组, 调用方可区分
#                       「成功取数但无事件」与「取数失败被跳过」
#   2026-09-06  Claude  修复 BUG-016: fetch_kline 翻页加页数上限 MAX_PAGES 与
#                       进度断言(新 window_end 必须严格前移), 防服务端异常时死循环
"""
腾讯 fqkline 复权对账数据源(只取数, 不入库)

端点(腾讯自选股网页端半公开接口, 无官方文档/SLA):
    GET https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
        ?param={market}{symbol},day,{start},{end},{count},qfq

注意(2026-09-06 实测, 详见 docs/adj_factor_selfbuild.md §6.3):
  - 必须钉死用 fqkline 端点; akshare 的 stock_zh_a_hist_tx 走的是
    newfqkline 端点, 实测漏近期除权事件, 不满足对账时效要求
  - 单次请求上限 640 根 K 线, 更长区间需翻页(本模块按窗口向前翻页拼接)
  - qfq 价格仅 3 位小数, 逐日相除反推因子会累积漂移, 只取事件日跳变比例
  - qfq 以最新交易日为锚, 序列每天整体平移, 不能当稳定因子表用
  - K 线行形如 [date, open, close, high, low, volume, 可选事件dict, ...],
    事件 dict 只在除权日那行出现, 形如
    {"nd":"2025","fh_sh":"0.04","djr":"2026-07-31","cqr":"2026-08-03",
     "FHcontent":"10派0.04元"}, fh_sh 单位是每 10 股
  - adjust 传空串拿不复权价(响应键为 "day")

对外接口:
  - fetch_kline(symbol, start_date, end_date, adjust='qfq')
        symbol 形如 'sz000681'; 返回 date/open/close/high/low/volume/events
  - fetch_xdr_events(codes, start_date, end_date)
        codes 为 '000681.SZ' 标准格式列表; 返回 (事件帧, failed_codes),
        事件帧列: code/xdr_date/div_per10/djr/content/jump_ratio
"""
import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

logger = logging.getLogger("etl.datasource.txstock")

FQKLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

MAX_COUNT_PER_REQUEST = 640   # 端点单次请求 K 线上限
REQUEST_TIMEOUT = 10          # 单次 HTTP 超时(秒)
REQUEST_TRIES = 3             # 单窗口最大尝试次数
RETRY_DELAY_SECONDS = 1.0     # 重试间隔(秒)
SLEEP_BETWEEN_STOCKS = 0.3    # 逐只限频间隔(秒), 限频未知须保守
FETCH_LOOKBACK_DAYS = 30      # 事件日前值缓冲(自然日): 窗口首日事件需前一交易日
                              # 行情才能算 jump_ratio, 30 天覆盖周末/长假/一般停牌
MAX_PAGES = 40                # 翻页上限: 640×40≈25,600 根, 覆盖 A 股最长历史(BUG-016)

KLINE_COLUMNS = ["date", "open", "close", "high", "low", "volume", "events"]
EVENT_COLUMNS = ["code", "xdr_date", "div_per10", "djr", "content", "jump_ratio"]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/87.0.4280.141",
}


class TxstockError(RuntimeError):
    """腾讯 fqkline 调用失败(网络重试耗尽 / 返回错误码 / 报文结构异常)"""


def _code_to_tx_symbol(code: str) -> str:
    """标准代码 '000681.SZ' -> 腾讯代码 'sz000681'; 仅支持 SH/SZ"""
    parts = str(code).strip().upper().split(".")
    if len(parts) != 2 or parts[1] not in ("SH", "SZ") or not parts[0].isdigit():
        raise ValueError(f"无法转换为腾讯代码(仅支持 SH/SZ): {code}")
    return parts[1].lower() + parts[0]


def _fetch_kline_node(symbol: str, start_date: str, end_date: str,
                      count: int, adjust: str) -> dict:
    """请求单个窗口并返回 data[symbol] 节点, 失败重试, 耗尽后抛 TxstockError"""
    param = f"{symbol},day,{start_date},{end_date},{count},{adjust}"
    last_err: Exception | None = None
    for attempt in range(1, REQUEST_TRIES + 1):
        try:
            resp = requests.get(FQKLINE_URL, params={"param": param},
                                headers=_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            last_err = e
            if attempt < REQUEST_TRIES:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        ret = payload.get("code", 0)
        if str(ret) != "0":
            raise TxstockError(
                f"fqkline 返回错误码 {ret} ({payload.get('msg', '')}): {param}")
        data = payload.get("data")
        if not isinstance(data, dict) or symbol not in data:
            raise TxstockError(f"fqkline 响应缺少 data[{symbol}] 节点: {param}")
        return data[symbol]

    raise TxstockError(f"fqkline 请求失败(重试 {REQUEST_TRIES} 次): {param}: {last_err}")


def _parse_klines(klines: list) -> pd.DataFrame:
    """解析 K 线数组为 DataFrame(KLINE_COLUMNS), 无法解析的行跳过"""
    rows = []
    for item in klines:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        try:
            rows.append({
                "date":    str(item[0]),
                "open":    float(item[1]),
                "close":   float(item[2]),
                "high":    float(item[3]),
                "low":     float(item[4]),
                "volume":  float(item[5]),
                "events":  item[6] if len(item) > 6 and isinstance(item[6], dict) else None,
            })
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows, columns=KLINE_COLUMNS)


def fetch_kline(symbol: str, start_date: str, end_date: str,
                adjust: str = "qfq") -> pd.DataFrame:
    """获取腾讯日 K 线, 超 640 根自动按窗口向前翻页拼接

    翻页有 MAX_PAGES 页数上限与进度断言(新窗口终点必须严格前移)两道保险,
    触发时抛出异常，由调用方计入 failed_codes，避免部分数据被当成完整结果。

    Parameters
    ----------
    symbol     : 腾讯代码, 形如 'sz000681' / 'sh600519'
    start_date / end_date : 'YYYY-MM-DD'
    adjust     : 'qfq'(前复权) 或 ''(不复权)

    Returns
    -------
    DataFrame(KLINE_COLUMNS), date 升序去重; 区间内无 K 线时返回空帧
    """
    key = "qfqday" if adjust == "qfq" else "day"
    frames: list[pd.DataFrame] = []
    window_end = end_date

    # BUG-016 两道保险: 页数上限 MAX_PAGES + 进度断言(新 window_end 必须严格
    # 前移)。该端点是无官方文档/SLA 的半公开接口, 若服务端忽略窗口参数
    # 恒定返回同一批数据, 无保险会死循环持续打对方接口。
    for _ in range(MAX_PAGES):
        node = _fetch_kline_node(symbol, start_date, window_end,
                                 MAX_COUNT_PER_REQUEST, adjust)
        klines = node.get(key)
        if klines is None and key != "day":
            # 无复权历史的股票请求 qfq 时服务端只回 "day" 节点
            klines = node.get("day")
        if not isinstance(klines, list):
            raise ValueError("腾讯响应缺少有效 K 线节点")
        if not klines:
            break

        parsed = _parse_klines(klines)
        if len(parsed) != len(klines):
            raise ValueError("腾讯 K 线包含无法解析的记录")
        frames.append(parsed)
        earliest = str(klines[0][0])
        if len(klines) < MAX_COUNT_PER_REQUEST or earliest <= start_date:
            break
        # 本窗口打满 640 根, 说明前面还有数据, 以最早日期的前一天为新窗口终点
        new_end = (datetime.strptime(earliest, "%Y-%m-%d")
                   - timedelta(days=1)).strftime("%Y-%m-%d")
        if new_end >= window_end:
            raise RuntimeError(f"[txstock] {symbol} 翻页无进度，数据不完整")
        window_end = new_end
    else:
        raise RuntimeError(f"[txstock] {symbol} 翻页达到上限 {MAX_PAGES} 页，数据不完整")

    if not frames:
        return pd.DataFrame(columns=KLINE_COLUMNS)

    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates("date", keep="first")
            .sort_values("date")
            .reset_index(drop=True))
    return df[(df["date"] >= start_date) & (df["date"] <= end_date)].reset_index(drop=True)


def _extract_events(code: str, qfq: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """由同一只股票的 qfq / 不复权 K 线提取除权事件帧(EVENT_COLUMNS)

    jump_ratio: factor_t = 不复权close / qfq收盘, 事件日跳变 =
        factor(前一交易日) / factor(事件日), qfq 以最新为锚时该比值 ≈ C/X
    """
    empty = pd.DataFrame(columns=EVENT_COLUMNS)
    if qfq.empty:
        return empty

    merged = (qfq[["date", "close", "events"]]
              .merge(raw[["date", "close"]], on="date",
                     how="left", suffixes=("_qfq", "_raw"))
              .sort_values("date")
              .reset_index(drop=True))
    if merged.empty:
        return empty

    factor = merged["close_raw"] / merged["close_qfq"]
    factor = factor.replace([float("inf"), float("-inf")], pd.NA)
    prev_factor = factor.shift(1)

    rows = []
    for idx, item in merged.iterrows():
        event = item["events"]
        if not isinstance(event, dict):
            continue
        fh_sh = event.get("fh_sh")
        try:
            div_per10 = float(fh_sh) if fh_sh not in (None, "") else None
        except (TypeError, ValueError):
            div_per10 = None
        f_prev, f_cur = prev_factor.iloc[idx], factor.iloc[idx]
        jump = (f_prev / f_cur
                if pd.notna(f_prev) and pd.notna(f_cur) and f_cur != 0 else None)
        rows.append({
            "code":       code,
            "xdr_date":   item["date"],
            "div_per10":  div_per10,
            "djr":        event.get("djr"),
            "content":    event.get("FHcontent"),
            "jump_ratio": jump,
        })
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def fetch_xdr_events(codes: list[str],
                     start_date: str, end_date: str) -> tuple[pd.DataFrame, list[str]]:
    """批量提取腾讯除权事件帧, 逐只失败记 warning 跳过, 不中断整体

    行情拉取起点比 start_date 向前扩 FETCH_LOOKBACK_DAYS 个自然日作为前值
    缓冲(覆盖周末/节假日/停牌), 保证窗口首日的事件也能拿到前一交易日
    计算 jump_ratio; 事件提取后裁剪回 [start_date, end_date]。
    缓冲期后仍无前值(如上市首日即除权)时 jump_ratio 保持 None。

    Parameters
    ----------
    codes      : 标准代码列表, 形如 ['000681.SZ', '600519.SH'](仅支持 SH/SZ)
    start_date / end_date : 'YYYY-MM-DD'

    Returns
    -------
    (events_df, failed_codes)
        events_df    : DataFrame(EVENT_COLUMNS), 成功取数股票的事件合集
                       (成功但窗口内无事件时为空帧)
        failed_codes : 取数失败/不支持被跳过的标准代码列表;
                       调用方据此区分「成功取数但无事件」与「取数失败」
    """
    fetch_start = (datetime.strptime(start_date, "%Y-%m-%d")
                   - timedelta(days=FETCH_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    total = len(codes)
    for i, code in enumerate(codes, 1):
        try:
            symbol = _code_to_tx_symbol(code)
            qfq = fetch_kline(symbol, fetch_start, end_date, adjust="qfq")
            raw = fetch_kline(symbol, fetch_start, end_date, adjust="")
            events = _extract_events(code, qfq, raw)
            events = events[(events["xdr_date"] >= start_date)
                            & (events["xdr_date"] <= end_date)]
            if not events.empty:
                frames.append(events)
        except Exception as e:
            failed.append(code)
            logger.warning(f"[txstock] {code} 事件提取失败，跳过: {e}")
        if i < total:
            time.sleep(SLEEP_BETWEEN_STOCKS)

    if not frames:
        return pd.DataFrame(columns=EVENT_COLUMNS), failed
    return (pd.concat(frames, ignore_index=True)
              .sort_values(["code", "xdr_date"])
              .reset_index(drop=True)), failed
