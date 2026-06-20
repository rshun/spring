# 修改记录:
#   2026-05-22  Claude  从 dlhttp.py 重命名为 web.py，定位为通用 http/https 文件下载数据源
#   2026-06-19  Claude  抽出通用 _http_download；新增沪深融资融券官网文件下载/清洗(fetch_margin_summary/detail)作为 akstock 回退
#   2026-06-19  Claude  新增上交所融资融券汇总/明细清洗函数(_clean_sse_summary/_clean_sse_detail)
#   2026-06-19  Claude  新增深交所融资融券汇总/明细清洗函数(_clean_szse_summary/_clean_szse_detail)
#   2026-06-19  Claude  新增官网下载编排 fetch_margin_summary/fetch_margin_detail
#   2026-06-20  Claude  兼容历史(约 2015 前)上交所文件融券数量列带 (股) 后缀的列名(_normalize_sse_cols)
#   2026-06-20  Claude  _normalize_sse_cols 增加列名首尾空白清理(2014 等年份的 ' 标的证券代码' 带前导空格)
"""通用 http/https 文件下载数据源

通过 http/https 下载外部数据文件并解析，作为 akshare/baostock 等接口取数失败时的回退数据源。
目前提供申万行业分类（个股）历史明细，从申万宏源研究 (swsresearch.com) 下载
StockClassifyUse_stock.xls 并解析为与 akstock.fetch_stock_industry_clf_hist_sw()
完全一致的 DataFrame，便于两者互为回退。
"""
import logging
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from util.config import get_config

logger = logging.getLogger("etl.datasource.web")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = PROJECT_ROOT / "download"

CLASSIFY_XLS_NAME = "StockClassifyUse_stock.xls"

_INDUSTRY_OUT_COLS = ['symbol', 'start_date', 'industry_code', 'update_time']

# 申万 xls 中文列名 → 标准英文列名
_CLASSIFY_COL_MAP = {
    '股票代码': 'symbol',
    '计入日期': 'start_date',
    '行业代码': 'industry_code',
    '更新日期': 'update_time',
}

_SUMMARY_OUT_COLS = [
    'trade_date', 'exchange_code',
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]
_DETAIL_OUT_COLS = [
    'trade_date', 'exchange_code', 'symbol', 'code',
    'margin_buy_amount', 'margin_repay_amount', 'margin_balance',
    'short_sell_volume', 'short_repay_volume',
    'short_balance_volume', 'short_balance_amount',
    'margin_short_balance',
]

# 官网中文列 → 标准英文列
_SSE_SUMMARY_MAP = {
    '本日融资余额(元)':     'margin_balance',
    '本日融资买入额(元)':   'margin_buy_amount',
    '本日融券余量':         'short_balance_volume',
    '本日融券余量金额(元)': 'short_balance_amount',
    '本日融券卖出量':       'short_sell_volume',
    '本日融资融券余额(元)': 'margin_short_balance',
}
_SSE_DETAIL_MAP = {
    '标的证券代码':     'symbol',
    '本日融资余额(元)': 'margin_balance',
    '本日融资买入额(元)': 'margin_buy_amount',
    '本日融资偿还额(元)': 'margin_repay_amount',
    '本日融券余量':     'short_balance_volume',
    '本日融券卖出量':   'short_sell_volume',
    '本日融券偿还量':   'short_repay_volume',
}


def _get_sws_config() -> dict:
    return get_config()["sws"]


def _http_download(url, dest, *, timeout=30, tries=3, delay=3, verify=True, headers=None):
    """通用 http(s) 文件下载，带简单重试。成功返回 dest，重试耗尽抛异常。"""
    import requests

    base_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/87.0.4280.141',
    }
    if headers:
        base_headers.update(headers)
    if not verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, headers=base_headers, timeout=timeout, verify=verify)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return dest
        except Exception as e:
            logger.warning(f"  下载 {url} 第 {attempt}/{tries} 次失败: {e}")
            if attempt == tries:
                raise
            time.sleep(delay)


def _download(url: str, dest: Path) -> Path:
    """下载 url 到 dest（sws 配置），带简单重试。失败抛异常。"""
    cfg = _get_sws_config()
    return _http_download(
        url, dest,
        timeout=cfg.get("request_timeout", 30),
        tries=cfg.get("tries", 3),
        delay=cfg.get("retry_delay", 3),
        verify=cfg.get("verify_ssl", False),
    )


def read_classify_xls(path: Path) -> pd.DataFrame:
    """读取申万行业分类 xls，重命名为标准英文列。

    xls 列: [股票代码, 计入日期, 行业代码, 更新日期]
    """
    path = Path(path)
    df = pd.read_excel(str(path), dtype=str, engine='xlrd')

    missing = [c for c in _CLASSIFY_COL_MAP if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} 缺少字段: {missing}，实际列: {list(df.columns)}")

    return df.rename(columns=_CLASSIFY_COL_MAP)[_INDUSTRY_OUT_COLS].copy()


def _clean_industry_df(df: pd.DataFrame) -> pd.DataFrame:
    """清洗为与 akstock.fetch_stock_industry_clf_hist_sw() 一致的输出"""
    result = df[_INDUSTRY_OUT_COLS].copy()
    result = result.dropna(subset=['symbol', 'start_date', 'industry_code'])
    result['symbol'] = result['symbol'].astype(str).str.strip().str.zfill(6)
    result['industry_code'] = result['industry_code'].astype(str).str.strip()
    result['start_date'] = pd.to_datetime(result['start_date'], errors='coerce').dt.date
    result['update_time'] = pd.to_datetime(result['update_time'], errors='coerce')
    result = result.dropna(subset=['start_date'])
    result = result[(result['symbol'] != '') & (result['industry_code'] != '')]
    result = result.drop_duplicates(subset=['symbol', 'start_date', 'industry_code'], keep='last')
    return result[_INDUSTRY_OUT_COLS].reset_index(drop=True)


def fetch_stock_industry_clf_hist_sw() -> pd.DataFrame:
    """下载并解析申万行业分类（个股）历史明细。

    与 akstock 同名同签名，便于互为回退。
    Returns DataFrame(symbol, start_date, industry_code, update_time)；失败返回空表。
    """
    try:
        url = _get_sws_config()["classify_xls_url"]
        dest = DOWNLOAD_DIR / CLASSIFY_XLS_NAME
        logger.info(f"下载申万行业分类文件: {url}")
        _download(url, dest)
        logger.info(f"  已下载到 {dest}")

        raw = read_classify_xls(dest)
        result = _clean_industry_df(raw)
        logger.info(f"[成功] 解析申万行业分类历史 {len(result)} 条")
        return result
    except Exception as e:
        logger.error(f"下载/解析申万行业分类文件失败: {e}")
        return pd.DataFrame(columns=_INDUSTRY_OUT_COLS)


def _to_num(series):
    """去千分位逗号后转 float64（非数值→NaN）。"""
    cleaned = series.astype(str).str.replace(',', '', regex=False).str.strip()
    return pd.to_numeric(cleaned, errors='coerce').astype('float64')


def _require_columns(df, mapping, name):
    missing = [c for c in mapping if c not in df.columns]
    if missing:
        raise ValueError(f"{name} 缺少字段: {missing}，实际列: {list(df.columns)}")


# 历史(约 2015 前)上交所文件的融券数量列带 (股) 后缀(如 本日融券余量(股)),
# 新版已去掉; 统一剥掉该后缀以匹配标准映射。注意金额列是 (元), 不受影响。
_SSE_VOL_SUFFIX = '(股)'


def _normalize_sse_cols(raw_df):
    """规整上交所列名以兼容历年文件: 去掉列名首尾空白(部分年份如 2014 的
    ' 标的证券代码' 带前导空格); 剥掉融券数量列末尾的 (股) 后缀。"""
    def _norm(c):
        c = str(c).strip()
        if c.endswith(_SSE_VOL_SUFFIX):
            c = c[:-len(_SSE_VOL_SUFFIX)]
        return c
    rename = {c: _norm(c) for c in raw_df.columns if _norm(c) != c}
    return raw_df.rename(columns=rename) if rename else raw_df


def _clean_sse_summary(raw_df, trade_date):
    raw_df = _normalize_sse_cols(raw_df)
    _require_columns(raw_df, _SSE_SUMMARY_MAP, "SSE 汇总")
    df = raw_df.rename(columns=_SSE_SUMMARY_MAP)
    for col in _SSE_SUMMARY_MAP.values():
        df[col] = _to_num(df[col])
    df = df.dropna(subset=['margin_balance']).copy()   # 丢弃空行/说明行
    df['trade_date'] = trade_date
    df['exchange_code'] = 'SH'
    df['margin_repay_amount'] = None
    df['short_repay_volume'] = None
    return df.reindex(columns=_SUMMARY_OUT_COLS).reset_index(drop=True)


def _clean_sse_detail(raw_df, trade_date):
    raw_df = _normalize_sse_cols(raw_df)
    _require_columns(raw_df, _SSE_DETAIL_MAP, "SSE 明细")
    df = raw_df.rename(columns=_SSE_DETAIL_MAP)
    df['symbol'] = df['symbol'].astype(str).str.strip().str.zfill(6)
    df = df[df['symbol'].str.match(r'^6\d{5}$')].copy()
    for col in _SSE_DETAIL_MAP.values():
        if col != 'symbol':
            df[col] = _to_num(df[col])
    df['code'] = df['symbol'] + '.SH'
    df['trade_date'] = trade_date
    df['exchange_code'] = 'SH'
    df['short_balance_amount'] = None
    df['margin_short_balance'] = None
    return df.reindex(columns=_DETAIL_OUT_COLS).reset_index(drop=True)


_SZSE_SUMMARY_MAP = {
    '融资买入额(元)':   'margin_buy_amount',
    '融资余额(元)':     'margin_balance',
    '融券卖出量(股/份)': 'short_sell_volume',
    '融券余量(股/份)':  'short_balance_volume',
    '融券余额(元)':     'short_balance_amount',
    '融资融券余额(元)': 'margin_short_balance',
}
_SZSE_DETAIL_MAP = {
    '证券代码':         'symbol',
    '融资买入额(元)':   'margin_buy_amount',
    '融资余额(元)':     'margin_balance',
    '融券卖出量(股/份)': 'short_sell_volume',
    '融券余量(股/份)':  'short_balance_volume',
    '融券余额(元)':     'short_balance_amount',
    '融资融券余额(元)': 'margin_short_balance',
}


def _clean_szse_summary(raw_df, trade_date):
    _require_columns(raw_df, _SZSE_SUMMARY_MAP, "SZSE 汇总")
    df = raw_df.rename(columns=_SZSE_SUMMARY_MAP)
    for col in _SZSE_SUMMARY_MAP.values():
        df[col] = _to_num(df[col])             # 仅去逗号，深圳官网已是 元/股，不 ×1e8
    df = df.dropna(subset=['margin_balance']).copy()
    df['trade_date'] = trade_date
    df['exchange_code'] = 'SZ'
    df['margin_repay_amount'] = None
    df['short_repay_volume'] = None
    return df.reindex(columns=_SUMMARY_OUT_COLS).reset_index(drop=True)


def _clean_szse_detail(raw_df, trade_date):
    _require_columns(raw_df, _SZSE_DETAIL_MAP, "SZSE 明细")
    df = raw_df.rename(columns=_SZSE_DETAIL_MAP)
    df['symbol'] = df['symbol'].astype(str).str.strip().str.zfill(6)
    df = df[df['symbol'].str.match(r'^[03]\d{5}$')].copy()
    for col in _SZSE_DETAIL_MAP.values():
        if col != 'symbol':
            df[col] = _to_num(df[col])
    df['code'] = df['symbol'] + '.SZ'
    df['trade_date'] = trade_date
    df['exchange_code'] = 'SZ'
    df['margin_repay_amount'] = None
    df['short_repay_volume'] = None
    return df.reindex(columns=_DETAIL_OUT_COLS).reset_index(drop=True)


def _get_margin_web_config() -> dict:
    return get_config()["margin_web"]


def _ensure_sse_file(yyyymmdd: str) -> Path:
    """下载上交所 rzrqjygk{date}.xls 到 download/（已存在则不重下），返回路径。"""
    cfg = _get_margin_web_config()
    url = cfg["sse_url_tpl"].format(date=yyyymmdd)
    dest = DOWNLOAD_DIR / f"rzrqjygk{yyyymmdd}.xls"
    if not dest.exists():
        logger.info(f"下载上交所融资融券文件: {url}")
        _http_download(
            url, dest,
            timeout=cfg.get("request_timeout", 30),
            tries=cfg.get("tries", 3),
            delay=cfg.get("retry_delay", 3),
            verify=cfg.get("verify_ssl", True),
            headers={'Referer': 'https://www.sse.com.cn/'},
        )
    return dest


def _download_szse_file(yyyy_mm_dd: str, tabkey: str) -> Path:
    """下载深交所融资融券 xlsx（tab1 汇总 / tab2 明细），返回路径。"""
    cfg = _get_margin_web_config()
    url = cfg["szse_url_tpl"].format(date=yyyy_mm_dd, tabkey=tabkey)
    dest = DOWNLOAD_DIR / f"szse_margin_{tabkey}_{yyyy_mm_dd}.xlsx"
    logger.info(f"下载深交所融资融券文件: {url}")
    _http_download(
        url, dest,
        timeout=cfg.get("request_timeout", 30),
        tries=cfg.get("tries", 3),
        delay=cfg.get("retry_delay", 3),
        verify=cfg.get("verify_ssl", True),
    )
    return dest


def _wants(exchanges):
    target = {e.lower() for e in exchanges}
    return ('sh' in target or 'all' in target), ('sz' in target or 'all' in target)


def fetch_margin_summary(begin_date, end_date, exchanges, trade_dates):
    """官网回退：沪深融资融券汇总。签名/输出列与 akstock.fetch_margin_summary 一致。"""
    want_sh, want_sz = _wants(exchanges)
    parts = []
    for d in trade_dates:
        td = datetime.strptime(d, '%Y%m%d').date()
        if want_sh:
            try:
                path = _ensure_sse_file(d)
                raw = pd.read_excel(path, sheet_name='汇总信息', dtype=str, engine='xlrd')
                parts.append(_clean_sse_summary(raw, td))
                logger.info(f"  上交所汇总(官网) {d}: 1 条")
            except Exception as e:
                logger.warning(f"  [WARN] 上交所汇总(官网) {d} 获取失败: {e}")
        if want_sz:
            try:
                path = _download_szse_file(td.isoformat(), 'tab1')
                raw = pd.read_excel(path, sheet_name=0, dtype=str)
                parts.append(_clean_szse_summary(raw, td))
                logger.info(f"  深交所汇总(官网) {d}: 1 条")
            except Exception as e:
                logger.warning(f"  [WARN] 深交所汇总(官网) {d} 获取失败: {e}")
    if not parts:
        return pd.DataFrame(columns=_SUMMARY_OUT_COLS)
    return pd.concat(parts, ignore_index=True).reset_index(drop=True)


def fetch_margin_detail(trade_date, exchanges):
    """官网回退：指定交易日沪深融资融券明细。签名/输出列与 akstock.fetch_margin_detail 一致。"""
    want_sh, want_sz = _wants(exchanges)
    td = datetime.strptime(trade_date, '%Y%m%d').date()
    dfs = []
    if want_sz:
        try:
            path = _download_szse_file(td.isoformat(), 'tab2')
            raw = pd.read_excel(path, sheet_name=0, dtype=str)
            df = _clean_szse_detail(raw, td)
            dfs.append(df)
            logger.info(f"  深交所明细(官网) {trade_date}: {len(df)} 条")
        except Exception as e:
            logger.warning(f"  [WARN] 深交所明细(官网) {trade_date} 获取失败: {e}")
    if want_sh:
        try:
            path = _ensure_sse_file(trade_date)
            raw = pd.read_excel(path, sheet_name='明细信息', dtype=str, engine='xlrd')
            df = _clean_sse_detail(raw, td)
            dfs.append(df)
            logger.info(f"  上交所明细(官网) {trade_date}: {len(df)} 条")
        except Exception as e:
            logger.warning(f"  [WARN] 上交所明细(官网) {trade_date} 获取失败: {e}")
    if not dfs:
        return pd.DataFrame(columns=_DETAIL_OUT_COLS)
    return pd.concat(dfs, ignore_index=True).reset_index(drop=True)
