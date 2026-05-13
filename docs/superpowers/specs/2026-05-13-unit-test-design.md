# 单元测试设计文档

**日期**: 2026-05-13  
**项目**: spring（A 股 ETL 数据管道）  
**范围**: 第一步 — 全覆盖测试（unit + db + integration）

---

## 背景与目标

项目当前无任何测试。目标是为 `util/`、`datasource/`、`etl/` 三个包建立完整测试覆盖，具体包括：

- 纯逻辑的正向测试、黑盒测试、异常测试
- 数据库 SQL 逻辑的 in-memory DuckDB 测试
- 外部数据源的 integration 测试（网络不通自动 skip）

**测试框架**：pytest  
**DB 策略**：`db/` 层使用 `duckdb.connect(":memory:")` + `sql/schema.sql` 初始化，不依赖真实文件  
**网络策略**：使用真实 `config.yaml`，网络失败时自动 skip，不 mock 外部 API

---

## 目录结构

```
tests/
  conftest.py              # 公共 fixtures：in-memory DuckDB、测试数据工厂
  unit/
    test_myutil.py
    test_validators.py
    test_tdx_logic.py
    test_dbutil_logic.py
  db/
    test_dbutil_save.py
    test_dbutil_query.py
    test_adjust.py
  integration/
    test_tdx_live.py
    test_akstock_live.py
```

**分层原则**：
- `unit/`：无 DuckDB 文件、无网络，毫秒级运行
- `db/`：in-memory DuckDB，无真实文件依赖
- `integration/`：需真实 config + 网络，用 `pytest -m integration` 单独触发

---

## conftest.py

- `mem_db` fixture：创建 in-memory DuckDB 连接，执行 `schema.sql` 初始化所有表，每个测试函数独立一个连接（function scope）
- `stock_info_factory`：快速插入 STOCK_INFO 测试行的辅助函数
- `trade_cal_factory`：快速插入 TRADE_CAL 测试行的辅助函数

---

## unit/ 层

### test_myutil.py

**正常测试**
- `trans_datestr_format("20230101")` → `"2023-01-01"`
- `get_today()` 返回 8 位数字字符串
- `get_yesterday()` 永远早于 `get_today()`
- `import_source_module("tdx")` 与 `import_source_module("datasource.tdx")` 返回同一模块

**黑盒测试**
- `get_yesterday()` < `get_today()`（不 mock 时间，断言大小关系）

**异常测试**
- `trans_datestr_format("abc")` → `ValueError`
- `trans_datestr_format("20230230")` → `ValueError`（2月30日不存在）
- `trans_datestr_format("2023-01-01")` → `ValueError`（格式不符合 YYYYMMDD）
- `get_lday_path(None)` on Windows → `ValueError`
- `get_lday_path("sh")` 目录不存在 → `FileNotFoundError`
- `import_source_module("")` → `ValueError`
- `import_source_module("nonexistent_module")` → `ImportError`，错误消息含可用模块列表

---

### test_validators.py

**正常测试**
- `v_yyyymmdd` 合法日期返回 `[]`
- `v_date_order` begin <= end 返回 `[]`
- `v_single_day_must_be_trading_day` begin != end 直接返回 `[]`
- `run()` 全部通过返回 `True`

**黑盒测试**
- `run()` 多个 validator 全部失败时收集所有错误再输出，不是遇到第一个就停
- `run()` 返回值只有 True / False
- `v_date_order` 失败时 `ValidationError.field` 包含两个字段名

**异常测试**
- `v_yyyymmdd("date")` ctx 里 date=None → 不抛异常，返回 `ValidationError`
- `v_yyyymmdd` 传入 `"abc"` / `"20231301"` / 空字符串 → 返回 `[ValidationError]`
- `v_date_order` ctx 里日期解析失败 → 静默跳过返回 `[]`
- `v_single_day_must_be_trading_day("begin", None)` → 构造时抛 `ValueError`
- `v_single_day_must_be_trading_day` begin==end 且非交易日（mock `check_is_trading_day` 返回 False）→ 返回 `ValidationError`

---

### test_tdx_logic.py

mock 对象：`api`（mock `get_security_bars` / `to_df`）、`_get_max_pages`、`_get_max_fail`

**正常测试**
- 正常行情数据返回 DataFrame，含正确 10 列
- volume 已 ×100（手→股）
- `_to_market("SH")` → 1，`_to_market("SZ")` → 0，`_to_market("BJ")` → 2，大小写不敏感

**黑盒测试**
- 输出列名和顺序固定为 `[code, date, open, high, low, close, pre_close, tradestatus, volume, amount]`
- 输出行数 = trade_dates 落在区间内的数量（每个交易日恰好一行）
- 停牌占位行：四价相等且等于前收，vol=0，tradestatus=0
- 停牌后恢复交易，pre_close 正确接续前收

**异常测试**
- `trade_dates=[]` → 返回空 DataFrame，不报错
- api 返回 `None` → 所有交易日产生停牌占位行（prev_close 已知时）
- api 返回 `[]` → 同上
- begin_date > end_date → target_dates 为空，返回空 DataFrame
- 首日即停牌（prev_close=None，api 无该日数据）→ 跳过该日，不报错
- vol=0 且四价相同 → tradestatus=0
- vol=0 但四价不同 → tradestatus=1

---

### test_dbutil_logic.py

**正常测试**
- `_normalize_daily_df` 有 pre_close 列，NaN 填 -1
- 无 pre_close 列 → 补列填 -1
- 有 trade_status 无 tradestatus → 复制列
- 无任何状态列 → tradestatus 填 -1

**黑盒测试**
- codes 参数优先级高于 exchanges（同时传时结果只按 codes 过滤）
- codes 支持中文逗号分隔 `"600519，000001"`，等价于英文逗号
- eff_begin 不会早于 list_date（即使 begindate 传入更早日期）
- eff_end 不会晚于今天（list_status=L 时 cap_date = today）

**异常测试**
- `get_connection(is_read_only=True)` 文件不存在 → 抛 `FileNotFoundError`
- `get_candidate_data` DB 文件不存在 → 捕获异常返回 `[]`，不向上抛
- stock_info 含 `list_date=None` 的记录 → 该条被跳过，不影响其余结果
- `delist_date=None` 且 `list_status=D` → 跳过并输出 warning

---

## db/ 层

所有测试使用 `mem_db` fixture（in-memory DuckDB + schema.sql）。

### test_dbutil_save.py

**正常测试**
- `save_daily_to_db`：写入正常行情，行数一致，字段类型正确
- `save_daily_to_db`：重复写入同一条，`INSERT OR REPLACE` 不产生重复行
- `save_base_to_db`：COALESCE 逻辑——先写 pe=10.0 再写 pe=None，pe 仍为 10.0
- `save_base_to_db`：新行插入，旧行按 ON CONFLICT 更新
- `save_calendar_to_db`：cal_date 去重，is_open 正确；重复写入不报错取新值
- `save_shares_to_db`：仅更新 total_shares / float_shares，不影响其他字段
- `save_margin_summary_to_db`：行数一致，数值字段 CAST 正确
- `save_margin_detail_to_db`：同 trade_date+exchange+symbol 冲突时更新字段
- `load_stock_info_to_db`：字段有变化时更新 last_updated_at；无变化时不更新

**黑盒测试**
- `save_daily_to_db` 调用后 tradestatus 列不含 NaN（验证 `_normalize_daily_df` 被调用）

**异常测试**
- `save_margin_summary_to_db` 传入 None → 提前返回，打 log，不抛异常
- `save_margin_summary_to_db` 传入空 DataFrame → 提前返回
- `save_daily_to_db` 传入缺少必要列的 DataFrame → 捕获异常打 log，不向上抛

---

### test_dbutil_query.py

**正常测试**
- `check_is_trading_day` 交易日（is_open=1）→ True
- `check_is_trading_day` 非交易日（is_open=0）→ False
- `get_trade_dates` 区间内有 3 个交易日 → 返回 3 个 YYYYMMDD 字符串，升序

**黑盒测试**
- `get_trade_dates` 返回格式严格为 YYYYMMDD（不含连字符，长度=8）
- 边界日期包含在内（start_date 和 end_date 当天若是交易日也要返回）

**异常测试**
- `check_is_trading_day` 日期不在 TRADE_CAL 表 → 返回 False，打 warning
- `get_trade_dates` 区间内无交易日 → 返回 `[]`

---

### test_adjust.py

使用 `mem_db` fixture，需同时初始化 TRADE_CAL、ADJ_FACTOR_RAW、ADJ_FACTOR 表。

**正常测试**
- 有复权事件，从事件日起稠密化：ADJ_FACTOR 行数 = 区间内交易日数
- 无复权事件，新股首次运行：默认因子 1.0，从 start_date 补到 end_date
- 已有稠密历史，增量续接：仅补 last_dense_date+1 之后的数据
- 显式回填（start_date 早于已有数据）：从 start_date 重算，覆盖旧值

**黑盒测试**
- 稠密化后每个交易日恰好一行（不多不少，无缺日）
- ASOF JOIN 向前填充：事件日之后无新事件时，因子沿用最近一次事件值
- 事务原子性：中途失败后 ADJ_FACTOR 无脏数据

**异常测试**
- `stock_list` 为空 → 提前返回，打 warning，不抛异常
- `adjust_df` 缺少必要列 → 抛 `ValueError`（含缺失列名）
- DB 操作中途失败 → 触发 ROLLBACK，不留半写状态

---

## integration/ 层

用 `@pytest.mark.integration` 标记，通过 `pytest -m integration` 单独触发。网络失败时用 `pytest.skip()` 自动跳过。

### test_tdx_live.py

**正常测试**
- 连接通达信服务器：至少一台可达，返回 API 对象
- 拉取单只股票（如 600519.SH）近 5 个交易日：DataFrame 非空，列齐全，date 格式正确
- 拉取 xdxr 数据（少量股票）：返回 DataFrame 或 None，不报错

**异常测试**
- 所有服务器 IP 填写错误 → 抛 `ConnectionError`
- 拉取不存在的股票代码 → 返回空 DataFrame，不报错

---

### test_akstock_live.py

**正常测试**
- 拉取单只股票日线数据：DataFrame 非空，必要列存在
- 拉取交易日历：返回包含 cal_date / is_open 的 DataFrame

**异常测试**
- 网络超时 / AkShare 接口报错 → 捕获异常返回 None 或空 DataFrame，不崩溃

---

## 依赖项

`requirements.txt` 新增：
```
pytest>=8.0
pytest-mock>=3.14
```

---

## 运行方式

```bash
# 只跑 unit + db（快速，无网络依赖）
pytest tests/unit tests/db

# 跑所有包括 integration
pytest -m integration tests/integration

# 生成覆盖率报告
pytest --cov=util --cov=datasource --cov=etl --cov-report=term-missing
```
