# 融资融券 akstock→官网文件回退 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `sync_margin` 在 akstock 取数失败时按交易所自动回退到沪深交易所官网 Excel 文件，清洗后写入既有 `MARGIN_*_DAILY` 表。

**Architecture:** 沿用现有 `sync_industry` + `datasource/web.py` 回退范式：在 `web.py` 新增与 akstock 同名同签名、同输出列的下载/清洗函数；`sync_margin.py` 先调 akstock，按 `exchange_code` 检测缺失交易所并仅对其回退合并入库。表结构与 `dbutil` 写库函数不动。

**Tech Stack:** Python, pandas, requests, openpyxl(.xlsx)/xlrd(.xls), DuckDB, pytest。

## Global Constraints

- 分支：在 `dev` 上开发，禁止在 master 提交。
- 每个修改/新建源文件顶部维护「修改记录」注释块，新增一行 `2026-06-19  Claude  <原因>`。
- 每个功能必须含正例 + 反例单测；提交前跑通 `pytest -m "not integration"`。
- 虚拟环境：`C:\dev\venv_quant`（命令示例用 `python`/`pytest` 默认即该 venv）。
- 输出列严格等于 akstock 现有常量（逐字复制）：
  - `_SUMMARY_OUT_COLS = ['trade_date','exchange_code','margin_buy_amount','margin_repay_amount','margin_balance','short_sell_volume','short_repay_volume','short_balance_volume','short_balance_amount','margin_short_balance']`
  - `_DETAIL_OUT_COLS = ['trade_date','exchange_code','symbol','code','margin_buy_amount','margin_repay_amount','margin_balance','short_sell_volume','short_repay_volume','short_balance_volume','short_balance_amount','margin_short_balance']`
- 深圳官网数值已是 元/股，**不做 ×1e8**。
- 个股过滤：SH `^6\d{5}$`、SZ `^[03]\d{5}$`（丢弃 ETF/基金/债券）。

---

### Task 1: config 增加 margin_web 块 + web.py 抽出通用下载器

**Files:**
- Modify: `config/config.yaml`（在 `sws:` 块之后新增 `margin_web:` 块）
- Modify: `datasource/web.py`（顶部修改记录；抽出 `_http_download`，`_download` 改为调用它）
- Test: `tests/unit/test_web_margin_logic.py`（新建）

**Interfaces:**
- Produces: `_http_download(url: str, dest: Path, *, timeout: int = 30, tries: int = 3, delay: int = 3, verify: bool = True, headers: dict | None = None) -> Path`（成功返回 dest，重试耗尽抛异常）
- Consumes: `get_config()["margin_web"]`

- [ ] **Step 1: 在 config.yaml 的 `sws:` 块后新增 margin_web 配置**

在 `config/config.yaml` 中 `sws:` 块（`verify_ssl: false` 行）之后插入：

```yaml

# 沪深交易所官网融资融券文件下载源（akstock 取数失败时的回退）
margin_web:
  # {date} = YYYYMMDD
  sse_url_tpl: "https://www.sse.com.cn/market/dealingdata/overview/margin/a/rzrqjygk{date}.xls"
  # {date} = YYYY-MM-DD, {tabkey} = tab1(汇总)/tab2(明细)
  szse_url_tpl: "https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1837_xxpl&txtDate={date}&TABKEY={tabkey}"
  request_timeout: 30
  tries: 3
  retry_delay: 3
  verify_ssl: true        # sse/szse 证书正常；如所在网络异常可改 false
```

- [ ] **Step 2: 写失败测试 `_http_download`（成功 + 重试耗尽）**

新建 `tests/unit/test_web_margin_logic.py`：

```python
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from datasource import web


def test_http_download_writes_content(tmp_path):
    dest = tmp_path / "f.xls"
    resp = MagicMock()
    resp.content = b"hello"
    resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=resp) as mget:
        out = web._http_download("http://x/y", dest, timeout=5, tries=2, delay=0)
    assert out == dest
    assert dest.read_bytes() == b"hello"
    mget.assert_called_once()


def test_http_download_raises_after_retries(tmp_path):
    dest = tmp_path / "f.xls"
    with patch("requests.get", side_effect=RuntimeError("boom")) as mget, \
         patch("time.sleep"):
        with pytest.raises(RuntimeError):
            web._http_download("http://x/y", dest, timeout=5, tries=3, delay=0)
    assert mget.call_count == 3
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: FAIL（`_http_download` 不存在 / AttributeError）

- [ ] **Step 4: 在 web.py 实现 `_http_download` 并让 `_download` 复用**

在 `datasource/web.py` 顶部修改记录追加一行：

```python
#   2026-06-19  Claude  抽出通用 _http_download；新增沪深融资融券官网文件下载/清洗(fetch_margin_summary/detail)作为 akstock 回退
```

在 `_download` 之上新增通用下载器：

```python
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
```

将现有 `_download` 函数体替换为对它的调用（保持 sws 行为不变）：

```python
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
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: PASS（2 passed）

- [ ] **Step 6: 确认 sws 既有测试未回归**

Run: `pytest tests/unit -k "industry or web or sws" -q`
Expected: PASS（无回归）

- [ ] **Step 7: 提交**

```bash
git add config/config.yaml datasource/web.py tests/unit/test_web_margin_logic.py
git commit -m "feat: web.py 抽出通用 _http_download + margin_web 官网下载配置

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 上交所汇总/明细清洗纯函数

**Files:**
- Modify: `datasource/web.py`（新增常量与 `_clean_sse_summary` / `_clean_sse_detail`）
- Test: `tests/unit/test_web_margin_logic.py`

**Interfaces:**
- Produces:
  - `_clean_sse_summary(raw_df: pd.DataFrame, trade_date: datetime.date) -> pd.DataFrame`（列= `_SUMMARY_OUT_COLS`）
  - `_clean_sse_detail(raw_df: pd.DataFrame, trade_date: datetime.date) -> pd.DataFrame`（列= `_DETAIL_OUT_COLS`）
  - 常量 `_SUMMARY_OUT_COLS`, `_DETAIL_OUT_COLS`, `_to_num`

- [ ] **Step 1: 写失败测试（正例 2 + 反例 1）**

在 `tests/unit/test_web_margin_logic.py` 追加：

```python
import datetime
import pandas as pd

TD = datetime.date(2026, 6, 18)


def _sse_summary_raw():
    # 第 1 行为数据；末行为官网说明文字；中间为空行
    return pd.DataFrame({
        '本日融资余额(元)':     ['1495185799372', None, '注：本表格同时包含融资融券汇总信息及明细信息'],
        '本日融资买入额(元)':   ['179607982680', None, None],
        '本日融券余量':         ['2444063656', None, None],
        '本日融券余量金额(元)': ['14089378242', None, None],
        '本日融券卖出量':       ['41813060', None, None],
        '本日融资融券余额(元)': ['1509275177614', None, None],
    })


def test_clean_sse_summary_drops_note_and_maps():
    out = web._clean_sse_summary(_sse_summary_raw(), TD)
    assert list(out.columns) == web._SUMMARY_OUT_COLS
    assert len(out) == 1
    row = out.iloc[0]
    assert row['exchange_code'] == 'SH'
    assert row['trade_date'] == TD
    assert row['margin_balance'] == 1495185799372.0
    assert row['short_balance_amount'] == 14089378242.0
    assert row['margin_repay_amount'] is None or pd.isna(row['margin_repay_amount'])


def test_clean_sse_detail_filters_etf_and_sets_code():
    raw = pd.DataFrame({
        '标的证券代码':     ['510050', '600000', '688981'],   # 510050=ETF 应被过滤
        '标的证券简称':     ['50ETF', '浦发银行', '中芯国际'],
        '本日融资余额(元)': ['1', '2', '3'],
        '本日融资买入额(元)': ['4', '5', '6'],
        '本日融资偿还额(元)': ['7', '8', '9'],
        '本日融券余量':     ['0', '0', '0'],
        '本日融券卖出量':   ['0', '0', '0'],
        '本日融券偿还量':   ['0', '0', '0'],
    })
    out = web._clean_sse_detail(raw, TD)
    assert list(out.columns) == web._DETAIL_OUT_COLS
    assert set(out['symbol']) == {'600000', '688981'}   # ETF 过滤
    assert out.loc[out['symbol'] == '600000', 'code'].iloc[0] == '600000.SH'
    assert out['exchange_code'].unique().tolist() == ['SH']
    assert out['short_balance_amount'].isna().all()      # 沪市明细不披露


def test_clean_sse_summary_missing_column_raises():
    bad = _sse_summary_raw().drop(columns=['本日融资余额(元)'])
    with pytest.raises(ValueError):
        web._clean_sse_summary(bad, TD)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: FAIL（`_clean_sse_summary` 未定义）

- [ ] **Step 3: 在 web.py 实现常量与 SSE 清洗函数**

在 import 区确保有 `from datetime import datetime`（已用于别处则复用）。在 `_INDUSTRY_OUT_COLS` 附近新增：

```python
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


def _to_num(series):
    """去千分位逗号后转 float64（非数值→NaN）。"""
    cleaned = series.astype(str).str.replace(',', '', regex=False).str.strip()
    return pd.to_numeric(cleaned, errors='coerce').astype('float64')


def _require_columns(df, mapping, name):
    missing = [c for c in mapping if c not in df.columns]
    if missing:
        raise ValueError(f"{name} 缺少字段: {missing}，实际列: {list(df.columns)}")


def _clean_sse_summary(raw_df, trade_date):
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: PASS（含 Task1 共 6 passed）

- [ ] **Step 5: 提交**

```bash
git add datasource/web.py tests/unit/test_web_margin_logic.py
git commit -m "feat: web.py 上交所融资融券汇总/明细清洗函数

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 深交所汇总/明细清洗纯函数

**Files:**
- Modify: `datasource/web.py`（新增 `_SZSE_*_MAP`、`_clean_szse_summary` / `_clean_szse_detail`）
- Test: `tests/unit/test_web_margin_logic.py`

**Interfaces:**
- Produces:
  - `_clean_szse_summary(raw_df, trade_date) -> pd.DataFrame`（列= `_SUMMARY_OUT_COLS`）
  - `_clean_szse_detail(raw_df, trade_date) -> pd.DataFrame`（列= `_DETAIL_OUT_COLS`）

- [ ] **Step 1: 写失败测试（正例 2 + 反例 1）**

在 `tests/unit/test_web_margin_logic.py` 追加：

```python
def test_clean_szse_summary_strips_commas_no_scaling():
    raw = pd.DataFrame({
        '融资买入额(元)':   ['166,476,286,758'],
        '融资余额(元)':     ['1,433,639,152,915'],
        '融券卖出量(股/份)': ['23,358,661'],
        '融券余量(股/份)':  ['880,313,065'],
        '融券余额(元)':     ['7,631,187,003'],
        '融资融券余额(元)': ['1,441,270,339,918'],
    })
    out = web._clean_szse_summary(raw, TD)
    assert list(out.columns) == web._SUMMARY_OUT_COLS
    assert out.iloc[0]['margin_balance'] == 1433639152915.0   # 原值，未 ×1e8
    assert out.iloc[0]['exchange_code'] == 'SZ'
    assert out.iloc[0]['short_balance_amount'] == 7631187003.0


def test_clean_szse_detail_preserves_leading_zeros():
    raw = pd.DataFrame({
        '证券代码':         ['000001', '000002', '159915', '301687'],  # 159915=ETF 过滤
        '证券简称':         ['平安银行', '万科A', '创业板ETF', '新广益'],
        '融资买入额(元)':   ['96,987,355', '55,065,272', '1', '43,520,546'],
        '融资余额(元)':     ['5,239,155,555', '2,488,937,846', '1', '155,840,011'],
        '融券卖出量(股/份)': ['58,200', '49,800', '0', '0'],
        '融券余量(股/份)':  ['1,744,100', '1,954,800', '0', '0'],
        '融券余额(元)':     ['18,801,398', '6,040,332', '0', '0'],
        '融资融券余额(元)': ['5,257,956,953', '2,494,978,178', '1', '155,840,011'],
    })
    out = web._clean_szse_detail(raw, TD)
    assert list(out.columns) == web._DETAIL_OUT_COLS
    assert set(out['symbol']) == {'000001', '000002', '301687'}  # ETF 过滤、前导零保留
    assert out.loc[out['symbol'] == '000001', 'code'].iloc[0] == '000001.SZ'
    assert out['margin_repay_amount'].isna().all()


def test_clean_szse_detail_missing_column_raises():
    with pytest.raises(ValueError):
        web._clean_szse_detail(pd.DataFrame({'证券代码': ['000001']}), TD)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: FAIL（`_clean_szse_summary` 未定义）

- [ ] **Step 3: 在 web.py 实现 SZSE 清洗函数**

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: PASS（共 9 passed）

- [ ] **Step 5: 提交**

```bash
git add datasource/web.py tests/unit/test_web_margin_logic.py
git commit -m "feat: web.py 深交所融资融券汇总/明细清洗函数

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: web.fetch_margin_summary / fetch_margin_detail（下载编排）

**Files:**
- Modify: `datasource/web.py`（新增下载助手与两个 public fetch 函数）
- Test: `tests/unit/test_web_margin_logic.py`

**Interfaces:**
- Consumes: `_http_download`, `_clean_sse_summary/_clean_sse_detail/_clean_szse_summary/_clean_szse_detail`, `get_config()["margin_web"]`
- Produces:
  - `fetch_margin_summary(begin_date: str, end_date: str, exchanges: list[str], trade_dates: list[str]) -> pd.DataFrame`（列= `_SUMMARY_OUT_COLS`；失败/无数据返回空表）
  - `fetch_margin_detail(trade_date: str, exchanges: list[str]) -> pd.DataFrame`（列= `_DETAIL_OUT_COLS`）

- [ ] **Step 1: 写失败测试（正例：成功路径 mock 下载+读 Excel；反例：下载失败返回空表）**

在 `tests/unit/test_web_margin_logic.py` 追加：

```python
def test_fetch_margin_summary_sh_success(monkeypatch, tmp_path):
    # mock 下载：返回一个假路径；mock read_excel 返回 SSE 汇总原始表
    monkeypatch.setattr(web, "_ensure_sse_file", lambda d: tmp_path / f"rzrqjygk{d}.xls")
    monkeypatch.setattr(web.pd, "read_excel", lambda *a, **k: _sse_summary_raw())
    out = web.fetch_margin_summary("20260618", "20260618", ["sh"], ["20260618"])
    assert list(out.columns) == web._SUMMARY_OUT_COLS
    assert len(out) == 1
    assert out.iloc[0]['exchange_code'] == 'SH'


def test_fetch_margin_summary_download_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(web, "_ensure_sse_file", boom)
    monkeypatch.setattr(web, "_download_szse_file", boom)
    out = web.fetch_margin_summary("20260618", "20260618", ["all"], ["20260618"])
    assert list(out.columns) == web._SUMMARY_OUT_COLS
    assert out.empty


def test_fetch_margin_detail_sz_success(monkeypatch, tmp_path):
    raw = pd.DataFrame({
        '证券代码': ['000001'], '证券简称': ['平安银行'],
        '融资买入额(元)': ['1'], '融资余额(元)': ['2'],
        '融券卖出量(股/份)': ['0'], '融券余量(股/份)': ['0'],
        '融券余额(元)': ['0'], '融资融券余额(元)': ['2'],
    })
    monkeypatch.setattr(web, "_download_szse_file", lambda d, tab: tmp_path / "x.xlsx")
    monkeypatch.setattr(web.pd, "read_excel", lambda *a, **k: raw)
    out = web.fetch_margin_detail("20260618", ["sz"])
    assert list(out.columns) == web._DETAIL_OUT_COLS
    assert out.iloc[0]['code'] == '000001.SZ'
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: FAIL（`_ensure_sse_file` / `fetch_margin_summary` 未定义）

- [ ] **Step 3: 在 web.py 实现下载助手与 fetch 函数**

确保顶部已 `from datetime import datetime`。新增：

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/unit/test_web_margin_logic.py -q`
Expected: PASS（共 12 passed）

- [ ] **Step 5: 提交**

```bash
git add datasource/web.py tests/unit/test_web_margin_logic.py
git commit -m "feat: web.py 沪深融资融券官网下载编排 fetch_margin_summary/detail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: sync_margin 按交易所回退接入 + --download

**Files:**
- Modify: `etl/sync_margin.py`（顶部修改记录；新增 `_requested_codes` / `_fill_missing_exchanges`；main 接入回退 + `--download` 参数）
- Test: `tests/unit/test_sync_margin_logic.py`（新建）

**Interfaces:**
- Consumes: `datasource.web.fetch_margin_summary/fetch_margin_detail`, akstock 同名函数
- Produces:
  - `_requested_codes(exchanges: list[str]) -> set[str]`（返回 {'SH','SZ'} 子集）
  - `_fill_missing_exchanges(df: pd.DataFrame | None, requested: set[str], fallback_fn) -> pd.DataFrame`

- [ ] **Step 1: 写失败测试（正例：缺失交易所触发回退合并；反例：无缺失不调回退）**

新建 `tests/unit/test_sync_margin_logic.py`：

```python
import pandas as pd
import pytest

from etl import sync_margin


def test_requested_codes():
    assert sync_margin._requested_codes(['all']) == {'SH', 'SZ'}
    assert sync_margin._requested_codes(['sh']) == {'SH'}
    assert sync_margin._requested_codes(['sz']) == {'SZ'}


def test_fill_missing_calls_fallback_for_missing_exchange():
    ak_df = pd.DataFrame({'exchange_code': ['SH'], 'margin_balance': [1.0]})
    calls = {}

    def fallback(ex):
        calls['ex'] = ex
        return pd.DataFrame({'exchange_code': ['SZ'], 'margin_balance': [2.0]})

    out = sync_margin._fill_missing_exchanges(ak_df, {'SH', 'SZ'}, fallback)
    assert calls['ex'] == ['sz']                      # 仅缺失的 SZ
    assert set(out['exchange_code']) == {'SH', 'SZ'}  # 合并


def test_fill_missing_skips_fallback_when_complete():
    ak_df = pd.DataFrame({'exchange_code': ['SH', 'SZ'], 'margin_balance': [1.0, 2.0]})

    def fallback(ex):
        raise AssertionError("不应调用回退")

    out = sync_margin._fill_missing_exchanges(ak_df, {'SH', 'SZ'}, fallback)
    assert len(out) == 2


def test_fill_missing_empty_akstock_falls_back_all():
    calls = {}

    def fallback(ex):
        calls['ex'] = sorted(ex)
        return pd.DataFrame({'exchange_code': ['SH', 'SZ'], 'margin_balance': [1.0, 2.0]})

    out = sync_margin._fill_missing_exchanges(None, {'SH', 'SZ'}, fallback)
    assert calls['ex'] == ['sh', 'sz']
    assert len(out) == 2
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/unit/test_sync_margin_logic.py -q`
Expected: FAIL（`_requested_codes` 未定义）

- [ ] **Step 3: 在 sync_margin.py 顶部加修改记录 + import**

文件第 1 行（docstring `"""` 之前）插入：

```python
# 修改记录:
#   2026-06-19  Claude  akstock 取数失败时按交易所回退到交易所官网文件下载(datasource.web)；新增 --download 强制官网
```

在 `from util import dbutil, myutil` 处补充：

```python
import pandas as pd
from datasource import web
```

- [ ] **Step 4: 新增 `_requested_codes` 与 `_fill_missing_exchanges`**

在 `check_parameters` 之后新增：

```python
def _requested_codes(exchanges: list[str]) -> set[str]:
    target = {e.lower() for e in exchanges}
    codes: set[str] = set()
    if 'all' in target or 'sh' in target:
        codes.add('SH')
    if 'all' in target or 'sz' in target:
        codes.add('SZ')
    return codes


def _fill_missing_exchanges(df, requested: set[str], fallback_fn):
    """对 akstock 未返回的交易所调用 fallback_fn(缺失交易所小写列表)，合并结果。"""
    present = set(df['exchange_code'].unique()) if df is not None and not df.empty else set()
    missing = requested - present
    if not missing:
        return df if df is not None else pd.DataFrame()
    logger.warning(f"akstock 未取到 {sorted(missing)} 数据，回退官网下载...")
    df_web = fallback_fn([c.lower() for c in sorted(missing)])
    frames = [d for d in (df, df_web) if d is not None and not d.empty]
    if not frames:
        return df if df is not None else pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 5: 新增 `--download` 参数**

在 `parse_arguments` 的 `-f/--forcerun` 之后插入：

```python
    parser.add_argument(
        '--download',
        action='store_true',
        help='强制直接从交易所官网下载文件取数，不经 akstock'
    )
```

- [ ] **Step 6: main() 接入回退（汇总与明细）**

将 main() 中「汇总」分支替换为：

```python
        # ── 汇总 ──────────────────────────────────────────
        if args.only in ('summary', 'all'):
            logger.info("\n[Step] 获取融资融券汇总数据...")
            requested = _requested_codes(args.exchanges)
            if args.download:
                df_summary = web.fetch_margin_summary(args.begin, args.end, args.exchanges, trade_dates)
            elif not hasattr(module, 'fetch_margin_summary'):
                logger.error(f"模块 '{args.source}' 中没有定义 'fetch_margin_summary' 方法。")
                df_summary = pd.DataFrame()
            else:
                df_ak = module.fetch_margin_summary(args.begin, args.end, args.exchanges, trade_dates)
                df_summary = _fill_missing_exchanges(
                    df_ak, requested,
                    lambda ex: web.fetch_margin_summary(args.begin, args.end, ex, trade_dates),
                )
            if df_summary is not None and not df_summary.empty:
                dbutil.save_margin_summary_to_db(df_summary, conn)
            else:
                logger.warning("未获取到融资融券汇总数据，跳过数据库写入。")
```

将 main() 中「明细」分支替换为：

```python
        # ── 明细 ──────────────────────────────────────────
        if args.only in ('detail', 'all'):
            logger.info("\n[Step] 逐日获取融资融券明细数据...")
            requested = _requested_codes(args.exchanges)
            has_ak_detail = hasattr(module, 'fetch_margin_detail')
            if not args.download and not has_ak_detail:
                logger.error(f"模块 '{args.source}' 中没有定义 'fetch_margin_detail' 方法。")
            else:
                for i, d in enumerate(trade_dates, 1):
                    logger.info(f"  ({i}/{len(trade_dates)}) {d}")
                    if args.download:
                        df_detail = web.fetch_margin_detail(d, args.exchanges)
                    else:
                        df_ak = module.fetch_margin_detail(d, args.exchanges)
                        df_detail = _fill_missing_exchanges(
                            df_ak, requested,
                            lambda ex, d=d: web.fetch_margin_detail(d, ex),
                        )
                    if df_detail is not None and not df_detail.empty:
                        dbutil.save_margin_detail_to_db(df_detail, conn)
                    else:
                        logger.warning(f"  {d} 未获取到融资融券明细数据，跳过。")
```

- [ ] **Step 7: 运行测试，确认通过**

Run: `pytest tests/unit/test_sync_margin_logic.py -q`
Expected: PASS（4 passed）

- [ ] **Step 8: 全量单测回归**

Run: `pytest -m "not integration" -q`
Expected: PASS（无回归）

- [ ] **Step 9: 提交**

```bash
git add etl/sync_margin.py tests/unit/test_sync_margin_logic.py
git commit -m "feat: sync_margin akstock 失败按交易所回退官网下载 + --download

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 验收（人工，可选）

- 联网真实跑：`python -m etl.sync_margin --only all -x all -f`（取昨天，akstock 正常路径）。
- 强制官网：`python -m etl.sync_margin --only all -x all --download -f`，核对沪深汇总/明细落库且深圳金额未被 ×1e8、代码前导零正确。
