# 修改记录:
#   2026-05-22  Claude  从 dlhttp.py 重命名为 web.py，定位为通用 http/https 文件下载数据源
"""通用 http/https 文件下载数据源

通过 http/https 下载外部数据文件并解析，作为 akshare/baostock 等接口取数失败时的回退数据源。
目前提供申万行业分类（个股）历史明细，从申万宏源研究 (swsresearch.com) 下载
StockClassifyUse_stock.xls 并解析为与 akstock.fetch_stock_industry_clf_hist_sw()
完全一致的 DataFrame，便于两者互为回退。
"""
import logging
import time
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


def _get_sws_config() -> dict:
    return get_config()["sws"]


def _download(url: str, dest: Path) -> Path:
    """下载 url 到 dest，带简单重试。失败抛异常。"""
    import requests

    cfg = _get_sws_config()
    tries = cfg.get("tries", 3)
    delay = cfg.get("retry_delay", 3)
    timeout = cfg.get("request_timeout", 30)
    verify = cfg.get("verify_ssl", False)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/87.0.4280.141',
    }
    if not verify:
        # 申万官网证书链不完整，关闭校验时抑制 InsecureRequestWarning
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, verify=verify)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return dest
        except Exception as e:
            logger.warning(f"  下载 {url} 第 {attempt}/{tries} 次失败: {e}")
            if attempt == tries:
                raise
            time.sleep(delay)


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
