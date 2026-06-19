# 融资融券 akstock → 官网文件回退 设计文档

- 日期: 2026-06-19
- 作者: Claude
- 涉及表: `MARGIN_SUMMARY_DAILY`、`MARGIN_DETAIL_DAILY`（**结构不变**）

## 1. 背景与目标

`etl/sync_margin.py` 当前仅从 akshare（`datasource/akstock.py`）获取沪深融资融券汇总/明细。
当 akshare 取数失败时缺少回退手段。

目标：**先用 akstock 取数；某交易所取数失败时，自动改从交易所官网下载 Excel 文件，清洗后入库**。
无数据（如节假日深市无数据）时仅日志告警，不视为错误。

沿用现有 `sync_industry` 的回退范式：`datasource/web.py` 提供与 akstock 同名同签名、同输出列的
HTTP 下载函数，互为回退。

## 2. 数据源（官网下载地址，配置在 config.yaml）

| 数据 | 地址模板 | sheet / 参数 |
|------|----------|--------------|
| 上海 汇总+明细 | `https://www.sse.com.cn/market/dealingdata/overview/margin/a/rzrqjygk{YYYYMMDD}.xls` | 单文件含「汇总信息」「明细信息」两 sheet |
| 深圳 汇总 | `https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1837_xxpl&txtDate={YYYY-MM-DD}&TABKEY=tab1` | sheet「融资融券交易总量」 |
| 深圳 明细 | 同上，`TABKEY=tab2` | sheet「融资融券交易明细」 |

- 日期固定取 **T-1 日**（`sync_margin` 的 begin/end 默认即昨天，保留参数以支持回补）。
- 节假日规则：T 为节假日、T-1 为交易日时深市可能无数据、沪市有 → 按交易所独立处理，深市空仅告警。

### config.yaml 新增 `margin_web` 块（示意）

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

## 3. 架构

### 3.1 `datasource/web.py`（新增函数，复用现有下载基建）

将现有 `_download`（sws 专用）抽成通用 `_http_download(url, dest, *, timeout, tries, delay, verify, headers)`，
sws 路径改为调用它。新增：

- `fetch_margin_summary(begin_date, end_date, exchanges, trade_dates) -> pd.DataFrame`
  - 签名/输出列与 `akstock.fetch_margin_summary` 一致（`_SUMMARY_OUT_COLS`）。
  - 内部按 `trade_dates` 逐日、按 `exchanges` 逐市下载并清洗，concat 返回。
- `fetch_margin_detail(trade_date, exchanges) -> pd.DataFrame`
  - 签名/输出列与 `akstock.fetch_margin_detail` 一致（`_DETAIL_OUT_COLS`）。

下载的 SSE `.xls` 缓存到 `download/rzrqjygk{date}.xls`，summary 与 detail 复用同一文件（已存在则不重下）。

**纯解析/清洗函数（不触网，供单测直接调用）：**
- `_clean_sse_summary(raw_df, trade_date) -> df`
- `_clean_sse_detail(raw_df, trade_date) -> df`
- `_clean_szse_summary(raw_df, trade_date) -> df`
- `_clean_szse_detail(raw_df, trade_date) -> df`

### 3.2 `etl/sync_margin.py`（按交易所独立回退）

汇总：
1. `df_ak = akstock.fetch_margin_summary(begin, end, exchanges, trade_dates)`
2. `missing = 请求交易所集合 - set(df_ak['exchange_code'])`
3. `missing` 非空 → `df_web = web.fetch_margin_summary(begin, end, missing, trade_dates)`
4. concat 后 `save_margin_summary_to_db`；最终仍为空 → `logger.warning`，不报错。

明细（逐交易日 d 同理）：
1. `df_ak = akstock.fetch_margin_detail(d, exchanges)`
2. `missing = 请求交易所 - 已返回交易所`
3. 缺失 → `web.fetch_margin_detail(d, missing)`，concat 入库。

新增 `--download` 开关（与 sync_industry 一致）：强制直接走官网，不经 akstock。

## 4. 清洗规则

| 项 | 处理 |
|---|---|
| 列名 | 去 `本日` 前缀、`(元)`/`(股/份)` 后缀后映射到标准英文列 |
| trade_date / exchange_code | 由下载参数注入（SH/SZ），文件内不带日期 |
| code（明细） | `symbol + '.SH'/'.SZ'` |
| 证券代码 | `dtype=str` 读取防丢前导零；**只保留个股**：SH `^6\d{5}$`、SZ `^[03]\d{5}$`，过滤 ETF/基金/债券 |
| 数值 | 去千分位逗号转 float；**深圳官网值已是 元/股，不做 ×1e8**（与 akshare 接口口径相反） |
| SH 汇总 sheet | 丢弃全空行与末尾「注：…」说明行（仅取首个有效汇总行） |
| 不披露字段 | SH 偿还额、SZ 偿还额 → None（schema 可空，与 akstock 一致） |

字段映射（官网列 → 标准列）：

- SSE 汇总：融资余额→margin_balance、融资买入额→margin_buy_amount、融券余量→short_balance_volume、融券余量金额→short_balance_amount、融券卖出量→short_sell_volume、融资融券余额→margin_short_balance；margin_repay_amount/short_repay_volume=None。
- SSE 明细：标的证券代码→symbol、融资余额→margin_balance、融资买入额→margin_buy_amount、融资偿还额→margin_repay_amount、融券余量→short_balance_volume、融券卖出量→short_sell_volume、融券偿还量→short_repay_volume；short_balance_amount/margin_short_balance=None。
- SZSE 汇总：融资买入额→margin_buy_amount、融资余额→margin_balance、融券卖出量→short_sell_volume、融券余量→short_balance_volume、融券余额→short_balance_amount、融资融券余额→margin_short_balance；repay 两项=None。
- SZSE 明细：证券代码→symbol，其余同 SZSE 汇总口径；repay 两项=None。

## 5. 入库

复用现有 `dbutil.save_margin_summary_to_db` / `save_margin_detail_to_db`，**不改表结构**。
回退数据与 akstock 数据列完全一致，可直接 concat 后统一写入。

## 6. 错误处理

- 下载失败（重试耗尽 / HTTP 错误）→ 清洗函数前抛异常被捕获 → 该交易所返回空 DataFrame，记 `logger.warning`。
- 缺关键列 → 抛 `ValueError`（列名变更需人工介入），上层捕获记 warning，不影响其它交易所。
- 某交易所空：不影响另一交易所入库；全空仅 warning。

## 7. 测试（CLAUDE.md：正反例，全程 mock 网络）

单元测试 `tests/unit/test_web_margin_logic.py`：

**正例**
- 用 `tmp/` 真实样本（或内联构造同结构 DataFrame）喂 4 个 `_clean_*` 函数：
  - 输出列等于 `_SUMMARY_OUT_COLS` / `_DETAIL_OUT_COLS`；
  - 深圳 `证券代码` 前导零保留（`000001`），`code` 后缀正确；
  - 深圳数值未被 ×1e8（与原始 元 值一致）；
  - exchange_code、不披露字段为 None 正确。

**反例**
- 缺列 → `_clean_*` 抛 `ValueError`；
- `_http_download` mock 抛异常 → `fetch_margin_*` 返回空 DataFrame（列齐全）；
- SH 汇总含「注：…」说明行/空行 → 被丢弃，仅留有效行；
- ETF（510050 / 159xxx）被过滤；
- `sync_margin` 中某交易所空 → 仅 warning，另一交易所正常入库（mock 两数据源）。

提交前本地跑通 `pytest -m "not integration"`。

## 8. 影响面 / 不做的事

- 改动文件：`datasource/web.py`、`etl/sync_margin.py`、`config/config.yaml`、新增测试。
- 表结构、`dbutil` 写库函数、akstock 现有逻辑**不动**。
- 不做无关重构；`_download` 抽取为通用函数仅为支撑本需求。
