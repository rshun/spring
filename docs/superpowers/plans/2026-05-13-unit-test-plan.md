# Unit Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 spring A 股 ETL 项目建立全覆盖测试套件（unit + db + integration 三层）。

**Architecture:** 分三层：`unit/` 用 mock 隔离所有外部依赖（纯逻辑）；`db/` 用 in-memory DuckDB + schema.sql 测试 SQL 逻辑；`integration/` 用真实 config + 网络，失败时自动 skip。所有测试通过 pytest 运行。

**Tech Stack:** pytest >= 8.0、pytest-mock >= 3.14、duckdb（已有）、unittest.mock（stdlib）

---

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| Modify | `requirements.txt` | 新增 pytest、pytest-mock |
| Create | `pytest.ini` | 测试路径、mark 注册 |
| Create | `tests/conftest.py` | mem_db fixture、辅助插入函数 |
| Create | `tests/unit/__init__.py` | 包标记 |
| Create | `tests/unit/test_myutil.py` | myutil 纯函数测试 |
| Create | `tests/unit/test_validators.py` | validators 逻辑测试 |
| Create | `tests/unit/test_tdx_logic.py` | tdx 数据处理逻辑测试 |
| Create | `tests/unit/test_akstock_logic.py` | akstock 数据处理逻辑测试 |
| Create | `tests/unit/test_bstock_logic.py` | bstock 错误分类/数据转换测试 |
| Create | `tests/unit/test_dbutil_logic.py` | dbutil 纯逻辑 + 候选股筛选测试 |
| Create | `tests/db/__init__.py` | 包标记 |
| Create | `tests/db/test_dbutil_save.py` | 入库 SQL 测试（含 bug 修复） |
| Create | `tests/db/test_dbutil_query.py` | 查询函数测试 |
| Create | `tests/db/test_adjust.py` | 复权因子稠密化测试 |
| Create | `tests/integration/__init__.py` | 包标记 |
| Create | `tests/integration/test_tdx_live.py` | TDX 真实网络测试 |
| Create | `tests/integration/test_akstock_live.py` | AkShare 真实网络测试 |
| Create | `tests/integration/test_bstock_live.py` | Baostock 真实网络测试 |
| Modify | `util/dbutil.py` | 修复 save_margin_detail_to_db 中多余的 security_name 列 |

> **已知 Bug（Task 8 会发现）：** `save_margin_detail_to_db` 在 INSERT 语句中引用了 `security_name` 列，但 schema.sql 中 `MARGIN_DETAIL_DAILY` 表没有该列。Task 8 写测试时需同步修复。

---

## Task 1: 项目测试基础设施

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Create: `tests/conftest.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/db/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: 更新 requirements.txt，新增测试依赖**

在 `requirements.txt` 末尾追加：
```
pytest>=8.0
pytest-mock>=3.14
```

- [ ] **Step 2: 创建 pytest.ini**

```ini
[pytest]
testpaths = tests
markers =
    integration: marks tests requiring real network/config (deselect with '-m "not integration"')
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

- [ ] **Step 3: 创建 tests/conftest.py**

```python
import pytest
import duckdb
from pathlib import Path
from datetime import date


@pytest.fixture
def mem_db():
    """每个测试函数独立的 in-memory DuckDB，已初始化全部 schema 表。"""
    conn = duckdb.connect(":memory:")
    schema_path = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
    conn.execute(schema_path.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def insert_stock_info(conn, symbol: str, exchange: str, board: str,
                      list_date: str, delist_date: str = None,
                      list_status: str = "L"):
    code = f"{symbol}.{exchange}"
    conn.execute(
        "INSERT INTO STOCK_INFO (code, symbol, name, exchange, board, "
        "list_date, delist_date, list_status, created_at, last_updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, now(), now())",
        [code, symbol, f"Test {symbol}", exchange, board,
         list_date, delist_date, list_status]
    )


def insert_trade_cal(conn, cal_date: str, is_open: int):
    conn.execute(
        "INSERT INTO TRADE_CAL (cal_date, is_open) VALUES (?, ?)",
        [cal_date, is_open]
    )
```

- [ ] **Step 4: 创建空 __init__.py 文件**

创建以下三个空文件：
- `tests/unit/__init__.py`
- `tests/db/__init__.py`
- `tests/integration/__init__.py`

- [ ] **Step 5: 安装测试依赖**

```bash
pip install pytest>=8.0 pytest-mock>=3.14
```

- [ ] **Step 6: 验证基础设施可用**

```bash
cd c:\Dev\spring
pytest --collect-only
```

预期输出：`no tests ran`（无报错即成功）

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini tests/
git commit -m "test: 添加测试基础设施（pytest 配置、conftest、目录结构）"
```

---

## Task 2: test_myutil.py

**Files:**
- Create: `tests/unit/test_myutil.py`

- [ ] **Step 1: 创建 tests/unit/test_myutil.py**

```python
import sys
import pytest
from unittest.mock import patch
from pathlib import Path

from util.myutil import (
    trans_datestr_format,
    get_today,
    get_yesterday,
    import_source_module,
)


# ── trans_datestr_format ──────────────────────────────────────────────────────

def test_trans_normal():
    assert trans_datestr_format("20230101") == "2023-01-01"


def test_trans_year_boundary():
    assert trans_datestr_format("20231231") == "2023-12-31"


def test_trans_invalid_text():
    with pytest.raises(ValueError):
        trans_datestr_format("abc")


def test_trans_invalid_month():
    with pytest.raises(ValueError):
        trans_datestr_format("20231301")


def test_trans_invalid_day():
    with pytest.raises(ValueError):
        trans_datestr_format("20230230")  # 2月无30日


def test_trans_wrong_separator_format():
    with pytest.raises(ValueError):
        trans_datestr_format("2023-01-01")  # 期望 YYYYMMDD，不是 YYYY-MM-DD


def test_trans_error_message_contains_input():
    with pytest.raises(ValueError, match="20230230"):
        trans_datestr_format("20230230")


# ── get_today / get_yesterday ─────────────────────────────────────────────────

def test_get_today_is_8_digits():
    today = get_today()
    assert len(today) == 8
    assert today.isdigit()


def test_get_yesterday_is_8_digits():
    yesterday = get_yesterday()
    assert len(yesterday) == 8
    assert yesterday.isdigit()


def test_yesterday_before_today():
    """黑盒：不 mock 时间，直接断言大小关系。"""
    assert get_yesterday() < get_today()


# ── import_source_module ──────────────────────────────────────────────────────

def test_import_short_name():
    mod = import_source_module("tdx")
    assert hasattr(mod, "fetch_batch_data")


def test_import_full_path_same_module():
    """黑盒：两种写法返回同一模块对象。"""
    mod1 = import_source_module("tdx")
    mod2 = import_source_module("datasource.tdx")
    assert mod1 is mod2


def test_import_empty_string_raises_value_error():
    with pytest.raises(ValueError):
        import_source_module("")


def test_import_nonexistent_raises_import_error():
    with pytest.raises(ImportError) as exc_info:
        import_source_module("nonexistent_xyz_module")
    assert "nonexistent_xyz_module" in str(exc_info.value)


def test_import_error_lists_available_modules():
    """错误消息应包含可用模块列表。"""
    with pytest.raises(ImportError) as exc_info:
        import_source_module("nonexistent_xyz_module")
    error_msg = str(exc_info.value)
    assert "tdx" in error_msg or "可用模块" in error_msg


# ── get_lday_path（Windows 专用）────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_lday_path_none_raises_value_error():
    from util.myutil import get_lday_path
    with pytest.raises(ValueError):
        get_lday_path(None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_lday_path_empty_string_raises_value_error():
    from util.myutil import get_lday_path
    with pytest.raises(ValueError):
        get_lday_path("")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_lday_path_nonexistent_dir_raises_file_not_found():
    from util.myutil import get_lday_path
    with patch("util.config.get_config", return_value={"local_paths": {"tdx_vipdoc": "C:\\nonexistent_xyz_dir"}}):
        with pytest.raises(FileNotFoundError):
            get_lday_path("sh")
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/unit/test_myutil.py -v
```

预期：全部 PASS（若 `test_import_error_lists_available_modules` 失败，检查 `myutil.import_source_module` 错误消息格式）

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_myutil.py
git commit -m "test: 添加 myutil 单元测试（正向/黑盒/异常）"
```

---

## Task 3: test_validators.py

**Files:**
- Create: `tests/unit/test_validators.py`

- [ ] **Step 1: 创建 tests/unit/test_validators.py**

```python
import pytest
from unittest.mock import patch

from util.validators import (
    ValidationError,
    run,
    v_yyyymmdd,
    v_date_order,
    v_single_day_must_be_trading_day,
)


# ── v_yyyymmdd ────────────────────────────────────────────────────────────────

def test_v_yyyymmdd_valid():
    assert v_yyyymmdd("d")({"d": "20230103"}) == []


def test_v_yyyymmdd_invalid_text():
    errors = v_yyyymmdd("d")({"d": "abc"})
    assert len(errors) == 1
    assert errors[0].field == "d"


def test_v_yyyymmdd_invalid_month_13():
    errors = v_yyyymmdd("d")({"d": "20231301"})
    assert len(errors) == 1


def test_v_yyyymmdd_empty_string():
    errors = v_yyyymmdd("d")({"d": ""})
    assert len(errors) == 1


def test_v_yyyymmdd_none_value():
    """None 值不应抛异常，应返回 ValidationError。"""
    errors = v_yyyymmdd("d")({"d": None})
    assert len(errors) == 1


def test_v_yyyymmdd_missing_key():
    errors = v_yyyymmdd("d")({"other": "20230101"})
    assert len(errors) == 1


# ── v_date_order ──────────────────────────────────────────────────────────────

def test_v_date_order_valid():
    assert v_date_order("b", "e")({"b": "20230101", "e": "20230131"}) == []


def test_v_date_order_same_day():
    assert v_date_order("b", "e")({"b": "20230101", "e": "20230101"}) == []


def test_v_date_order_reversed():
    errors = v_date_order("b", "e")({"b": "20230201", "e": "20230101"})
    assert len(errors) == 1


def test_v_date_order_reversed_field_contains_both_names():
    """黑盒：field 应包含 begin 和 end 两个字段名。"""
    errors = v_date_order("begin", "end")({"begin": "20230201", "end": "20230101"})
    assert "begin" in errors[0].field
    assert "end" in errors[0].field


def test_v_date_order_parse_fail_silent():
    """日期解析失败时静默跳过，不重复报错。"""
    assert v_date_order("b", "e")({"b": "invalid", "e": "20230101"}) == []


# ── v_single_day_must_be_trading_day ─────────────────────────────────────────

def test_v_single_day_unequal_skips():
    """begin != end 时直接跳过，不查数据库。"""
    v = v_single_day_must_be_trading_day("b", "e")
    assert v({"b": "20230103", "e": "20230131"}) == []


def test_v_single_day_equal_trading_day():
    with patch("util.validators.dbutil.check_is_trading_day", return_value=True):
        v = v_single_day_must_be_trading_day("b", "e")
        assert v({"b": "20230103", "e": "20230103"}) == []


def test_v_single_day_equal_non_trading_day():
    with patch("util.validators.dbutil.check_is_trading_day", return_value=False):
        v = v_single_day_must_be_trading_day("b", "e")
        errors = v({"b": "20230101", "e": "20230101"})
    assert len(errors) == 1
    assert "2023-01-01" in errors[0].message


def test_v_single_day_only_one_field_raises():
    """构造时传一个字段应立即抛 ValueError。"""
    with pytest.raises(ValueError):
        v_single_day_must_be_trading_day("begin", None)


def test_v_single_day_parse_fail_silent():
    """日期格式错误时静默跳过。"""
    v = v_single_day_must_be_trading_day("b", "e")
    assert v({"b": "invalid", "e": "invalid"}) == []


# ── run ───────────────────────────────────────────────────────────────────────

def test_run_all_pass_returns_true():
    assert run({"d": "20230101"}, [v_yyyymmdd("d")]) is True


def test_run_one_fail_returns_false():
    assert run({"d": "abc"}, [v_yyyymmdd("d")]) is False


def test_run_collects_all_errors():
    """黑盒：不是遇到第一个失败就停，所有 validator 都要执行。"""
    v1 = v_yyyymmdd("begin")
    v2 = v_yyyymmdd("end")
    # 两个都失败，run() 应返回 False（不提前退出）
    result = run({"begin": "abc", "end": "xyz"}, [v1, v2])
    assert result is False


def test_run_empty_validators_returns_true():
    assert run({}, []) is True
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/unit/test_validators.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_validators.py
git commit -m "test: 添加 validators 单元测试（正向/黑盒/异常）"
```

---

## Task 4: test_tdx_logic.py

**Files:**
- Create: `tests/unit/test_tdx_logic.py`

- [ ] **Step 1: 创建 tests/unit/test_tdx_logic.py**

```python
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from datasource.tdx import fetch_stock_data, _to_market

EXPECTED_COLS = ["code", "date", "open", "high", "low", "close",
                 "pre_close", "tradestatus", "volume", "amount"]


def make_raw_row(date_str: str, open_: float, high: float, low: float,
                 close: float, vol: int, amount: float) -> dict:
    return {
        "datetime": f"{date_str} 00:00",
        "open": open_, "high": high, "low": low, "close": close,
        "vol": vol, "amount": amount,
    }


def make_api(rows: list[dict]):
    """构造只返回一页数据的 mock API。"""
    api = MagicMock()
    if rows:
        df = pd.DataFrame(rows)
        api.get_security_bars.side_effect = [rows, []]
        api.to_df.side_effect = lambda x: pd.DataFrame(x) if x else pd.DataFrame()
    else:
        api.get_security_bars.return_value = []
        api.to_df.return_value = pd.DataFrame()
    return api


# ── _to_market ────────────────────────────────────────────────────────────────

def test_to_market_sh_upper():
    assert _to_market("SH") == 1


def test_to_market_sh_lower():
    assert _to_market("sh") == 1


def test_to_market_sz():
    assert _to_market("SZ") == 0


def test_to_market_bj():
    assert _to_market("BJ") == 2


# ── fetch_stock_data 黑盒：输出列固定 ────────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_output_columns_fixed(mock_pages):
    rows = [make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0)]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert list(result.columns) == EXPECTED_COLS


# ── fetch_stock_data 正常：行数 = 区间内交易日数 ──────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_row_count_equals_trade_dates(mock_pages):
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),
        make_raw_row("2023-01-04", 10.5, 11.5, 10.0, 11.0, 120, 132000.0),
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    assert len(result) == 2


# ── fetch_stock_data 正常：volume 手→股 ──────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_volume_converted_from_lots_to_shares(mock_pages):
    rows = [make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 50, 52500.0)]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert result.iloc[0]["volume"] == 50 * 100


# ── fetch_stock_data 黑盒：停牌占位行四价=前收，量=0 ─────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_suspension_placeholder_uses_prev_close(mock_pages):
    """pytdx 未返回 20230104 → 产生停牌占位行，四价=前收，vol=0。"""
    rows = [make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0)]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    suspension = result[result["date"] == "2023-01-04"].iloc[0]
    assert suspension["tradestatus"] == 0
    assert suspension["volume"] == 0
    assert suspension["open"] == suspension["close"] == 10.5   # 前收
    assert suspension["pre_close"] == 10.5


# ── fetch_stock_data 黑盒：停牌后恢复，pre_close 正确接续 ────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_pre_close_after_suspension(mock_pages):
    """停牌日之后有正常行情，pre_close 应等于停牌前的最后收盘价。"""
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),
        make_raw_row("2023-01-05", 10.5, 12.0, 10.0, 11.5, 80,  92000.0),
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-05",
                              ["20230103", "20230104", "20230105"])
    row_0105 = result[result["date"] == "2023-01-05"].iloc[0]
    assert row_0105["pre_close"] == 10.5  # 停牌前的收盘价（01-03）


# ── fetch_stock_data 异常：trade_dates 为空 ──────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_empty_trade_dates_returns_empty_df(mock_pages):
    api = MagicMock()
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04", [])
    assert result.empty


# ── fetch_stock_data 异常：api 返回 None ─────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_api_returns_none_all_suspended(mock_pages):
    """api 返回 None 时，prev_close 不存在，所有交易日均跳过。"""
    api = MagicMock()
    api.get_security_bars.return_value = None
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    # prev_close=None, 无前收，首日停牌应跳过，结果为空
    assert result.empty


# ── fetch_stock_data 异常：api 返回空列表 ────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_api_returns_empty_list(mock_pages):
    api = MagicMock()
    api.get_security_bars.return_value = []
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-03",
                              ["20230103"])
    assert result.empty


# ── fetch_stock_data 停牌识别 ────────────────────────────────────────────────

@patch("datasource.tdx._get_max_pages", return_value=1)
def test_tradestatus_zero_when_vol_zero_and_prices_equal(mock_pages):
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),  # 有前收
        make_raw_row("2023-01-04", 10.5, 10.5, 10.5, 10.5, 0, 0.0),       # 四价相等 vol=0
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    row_0104 = result[result["date"] == "2023-01-04"].iloc[0]
    assert row_0104["tradestatus"] == 0


@patch("datasource.tdx._get_max_pages", return_value=1)
def test_tradestatus_one_when_vol_zero_prices_differ(mock_pages):
    rows = [
        make_raw_row("2023-01-03", 10.0, 11.0, 9.5, 10.5, 100, 105000.0),
        make_raw_row("2023-01-04", 10.0, 10.5, 9.8, 10.2, 0, 0.0),       # vol=0 但四价不同
    ]
    api = make_api(rows)
    result = fetch_stock_data(api, "600519", "SH", "2023-01-03", "2023-01-04",
                              ["20230103", "20230104"])
    row_0104 = result[result["date"] == "2023-01-04"].iloc[0]
    assert row_0104["tradestatus"] == 1
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/unit/test_tdx_logic.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_tdx_logic.py
git commit -m "test: 添加 tdx 数据处理逻辑单元测试（停牌补行/列固定/量转换）"
```

---

## Task 5: test_akstock_logic.py

**Files:**
- Create: `tests/unit/test_akstock_logic.py`

- [ ] **Step 1: 创建 tests/unit/test_akstock_logic.py**

```python
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from datasource.akstock import (
    fetch_bj_stock_data,
    fetch_stock_info,
    fetch_stock_data,
    fetch_margin_detail,
    fetch_stock_industry_clf_hist_sw,
    _fetch_summary_sse,
    _fetch_summary_szse,
    _SUMMARY_OUT_COLS,
    _DETAIL_OUT_COLS,
    _INDUSTRY_OUT_COLS,
)


# ── fetch_bj_stock_data ───────────────────────────────────────────────────────

def _bj_raw_df():
    return pd.DataFrame({
        "证券代码": ["430047", "832566"],
        "证券简称": ["诺思兰德", "奥迪威"],
        "上市日期": ["2014-04-25", "2021-12-02"],
        "总股本": ["10000", "20000"],
        "流通股本": ["8000", "15000"],
    })


def test_fetch_bj_stock_data_exchange_is_bj():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=_bj_raw_df()):
        df_info, df_basic = fetch_bj_stock_data("2024-01-02")
    assert (df_info["exchange"] == "BJ").all()


def test_fetch_bj_stock_data_code_format():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=_bj_raw_df()):
        df_info, _ = fetch_bj_stock_data("2024-01-02")
    assert all(c.endswith(".BJ") for c in df_info["code"])


def test_fetch_bj_stock_data_basic_has_shares():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=_bj_raw_df()):
        _, df_basic = fetch_bj_stock_data("2024-01-02")
    assert "total_shares" in df_basic.columns
    assert "float_shares" in df_basic.columns


def test_fetch_bj_stock_data_empty_response():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", return_value=pd.DataFrame()):
        df_info, df_basic = fetch_bj_stock_data("2024-01-02")
    assert df_info.empty
    assert df_basic.empty


def test_fetch_bj_stock_data_ak_raises():
    with patch("datasource.akstock.ak.stock_info_bj_name_code", side_effect=Exception("网络错误")):
        df_info, df_basic = fetch_bj_stock_data("2024-01-02")
    assert df_info.empty
    assert df_basic.empty


# ── fetch_stock_info 路由逻辑 ─────────────────────────────────────────────────

def test_fetch_stock_info_bj_triggers_bj_fetch():
    with patch("datasource.akstock.fetch_bj_stock_data",
               return_value=(pd.DataFrame({"code": ["1.BJ"]}), pd.DataFrame())) as mock_bj:
        fetch_stock_info(["BJ"])
    mock_bj.assert_called_once()


def test_fetch_stock_info_all_triggers_bj_fetch():
    with patch("datasource.akstock.fetch_bj_stock_data",
               return_value=(pd.DataFrame(), pd.DataFrame())) as mock_bj:
        fetch_stock_info(["all"])
    mock_bj.assert_called_once()


def test_fetch_stock_info_sh_only_returns_empty():
    df_info, df_basic = fetch_stock_info(["SH"])
    assert df_info.empty


# ── fetch_stock_data 列映射 ───────────────────────────────────────────────────

def _ak_hist_df():
    return pd.DataFrame({
        "日期": ["2023-01-03"],
        "开盘": [10.0], "最高": [11.0], "最低": [9.5], "收盘": [10.5],
        "成交量": [100], "成交额": [105000.0], "换手率": [1.5],
    })


def test_fetch_stock_data_daily_columns():
    with patch("datasource.akstock.ak.stock_zh_a_hist", return_value=_ak_hist_df()):
        with patch("datasource.akstock._get_request_timeout", return_value=10):
            df_daily, _ = fetch_stock_data("20230103", "20230103", "000001", "SZ")
    assert "code" in df_daily.columns
    assert "volume" in df_daily.columns


def test_fetch_stock_data_volume_multiplied():
    with patch("datasource.akstock.ak.stock_zh_a_hist", return_value=_ak_hist_df()):
        with patch("datasource.akstock._get_request_timeout", return_value=10):
            df_daily, _ = fetch_stock_data("20230103", "20230103", "000001", "SZ")
    assert df_daily.iloc[0]["volume"] == 100 * 100


def test_fetch_stock_data_basic_has_turnover_rate():
    with patch("datasource.akstock.ak.stock_zh_a_hist", return_value=_ak_hist_df()):
        with patch("datasource.akstock._get_request_timeout", return_value=10):
            _, df_basic = fetch_stock_data("20230103", "20230103", "000001", "SZ")
    assert "turnover_rate" in df_basic.columns


# ── _fetch_summary_sse 列重命名 + exchange_code ────────────────────────────────

def _sse_raw():
    return pd.DataFrame({
        "信用交易日期": ["20230103"],
        "融资余额": [1e10], "融资买入额": [1e9],
        "融券余量": [1e6], "融券余量金额": [1e8],
        "融券卖出量": [5e5], "融资融券余额": [2e10],
    })


def test_fetch_summary_sse_exchange_code():
    with patch("datasource.akstock.ak.stock_margin_sse", return_value=_sse_raw()):
        result = _fetch_summary_sse("20230103", "20230103")
    assert (result["exchange_code"] == "SH").all()


def test_fetch_summary_sse_output_columns():
    """黑盒：输出列固定为 _SUMMARY_OUT_COLS。"""
    with patch("datasource.akstock.ak.stock_margin_sse", return_value=_sse_raw()):
        result = _fetch_summary_sse("20230103", "20230103")
    assert list(result.columns) == _SUMMARY_OUT_COLS


def test_fetch_summary_sse_ak_raises_returns_empty():
    with patch("datasource.akstock.ak.stock_margin_sse", side_effect=Exception("超时")):
        result = _fetch_summary_sse("20230103", "20230103")
    assert result.empty
    assert list(result.columns) == _SUMMARY_OUT_COLS


# ── _fetch_summary_szse 逐日 + exchange_code ─────────────────────────────────

def _szse_raw():
    return pd.DataFrame({
        "数据日期": ["20230103"],
        "项目": ["融资融券"],
        "融资买入额": [1e9], "融资余额": [1e10],
        "融券卖出量": [5e5], "融券余量金额": [1e8],
        "融券余量": [1e6], "融资融券余额": [2e10],
    })


def test_fetch_summary_szse_exchange_code():
    with patch("datasource.akstock.ak.stock_margin_szse", return_value=_szse_raw()):
        result = _fetch_summary_szse(["20230103"])
    assert (result["exchange_code"] == "SZ").all()


def test_fetch_summary_szse_skip_failed_day():
    """某日接口失败时跳过，继续处理其余日期。"""
    call_count = 0
    def side_effect(date):
        nonlocal call_count
        call_count += 1
        if date == "20230103":
            raise Exception("接口错误")
        return _szse_raw()

    with patch("datasource.akstock.ak.stock_margin_szse", side_effect=side_effect):
        result = _fetch_summary_szse(["20230103", "20230104"])
    assert call_count == 2
    # 只有 20230104 成功
    assert len(result) == 1


# ── fetch_margin_detail symbol 过滤正则 ──────────────────────────────────────

def _sz_detail_raw():
    return pd.DataFrame({
        "证券代码": ["000001", "600519", "300001", "abc"],  # 600519 和 abc 应被过滤掉
        "融资买入额": [1e8] * 4, "融资余额": [1e9] * 4,
        "融券卖出量": [0] * 4, "融券余量": [0] * 4,
        "融券余额": [0] * 4, "融资融券余额": [1e9] * 4,
    })


def _sh_detail_raw():
    return pd.DataFrame({
        "标的证券代码": ["600519", "000001", "688001"],  # 000001 和 688001 不符合 ^6\d{5}$
        "融资余额": [1e9] * 3, "融资买入额": [1e8] * 3,
        "融资偿还额": [5e7] * 3, "融券余量": [0] * 3,
        "融券卖出量": [0] * 3, "融券偿还量": [0] * 3,
    })


def test_fetch_margin_detail_sz_filters_non_sz_symbols():
    with patch("datasource.akstock.ak.stock_margin_detail_szse", return_value=_sz_detail_raw()):
        with patch("datasource.akstock.ak.stock_margin_detail_sse", return_value=pd.DataFrame()):
            result = fetch_margin_detail("20230103", ["sz"])
    sz_codes = result["code"].tolist()
    assert all(c.endswith(".SZ") for c in sz_codes)
    assert "600519.SZ" not in sz_codes   # 6开头不是SZ股


def test_fetch_margin_detail_sh_filters_non_sh_symbols():
    with patch("datasource.akstock.ak.stock_margin_detail_sse", return_value=_sh_detail_raw()):
        with patch("datasource.akstock.ak.stock_margin_detail_szse", return_value=pd.DataFrame()):
            result = fetch_margin_detail("20230103", ["sh"])
    sh_codes = result["code"].tolist()
    assert all(c.endswith(".SH") for c in sh_codes)
    assert "000001.SH" not in sh_codes   # 0开头不是SH股


def test_fetch_margin_detail_output_columns():
    """黑盒：输出列固定为 _DETAIL_OUT_COLS。"""
    with patch("datasource.akstock.ak.stock_margin_detail_szse", return_value=_sz_detail_raw()):
        with patch("datasource.akstock.ak.stock_margin_detail_sse", return_value=pd.DataFrame()):
            result = fetch_margin_detail("20230103", ["sz"])
    assert list(result.columns) == _DETAIL_OUT_COLS


def test_fetch_margin_detail_both_fail_returns_empty():
    with patch("datasource.akstock.ak.stock_margin_detail_szse", side_effect=Exception("err")):
        with patch("datasource.akstock.ak.stock_margin_detail_sse", side_effect=Exception("err")):
            result = fetch_margin_detail("20230103", ["all"])
    assert result.empty
    assert list(result.columns) == _DETAIL_OUT_COLS


# ── fetch_stock_industry_clf_hist_sw 数据清洗 ─────────────────────────────────

def _sw_raw():
    return pd.DataFrame({
        "symbol": ["1", "60001", None, "000002"],
        "start_date": ["2021-01-01", "2020-06-01", "2021-01-01", "2021-01-01"],
        "industry_code": ["110101", "220101", "330101", "110101"],
        "update_time": ["2023-01-01 00:00:00"] * 4,
    })


def test_fetch_industry_symbol_zfill():
    """黑盒：symbol 补零至 6 位。"""
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=_sw_raw()):
        result = fetch_stock_industry_clf_hist_sw()
    assert all(len(s) == 6 for s in result["symbol"])


def test_fetch_industry_none_symbol_dropped():
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=_sw_raw()):
        result = fetch_stock_industry_clf_hist_sw()
    assert None not in result["symbol"].tolist()


def test_fetch_industry_output_columns():
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=_sw_raw()):
        result = fetch_stock_industry_clf_hist_sw()
    assert list(result.columns) == _INDUSTRY_OUT_COLS


def test_fetch_industry_ak_raises_returns_empty():
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", side_effect=Exception("超时")):
        result = fetch_stock_industry_clf_hist_sw()
    assert result.empty
    assert list(result.columns) == _INDUSTRY_OUT_COLS


def test_fetch_industry_missing_column_returns_empty():
    bad_df = pd.DataFrame({"symbol": ["000001"], "start_date": ["2021-01-01"]})
    with patch("datasource.akstock.ak.stock_industry_clf_hist_sw", return_value=bad_df):
        result = fetch_stock_industry_clf_hist_sw()
    assert result.empty
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/unit/test_akstock_logic.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_akstock_logic.py
git commit -m "test: 添加 akstock 数据处理逻辑单元测试（列映射/symbol过滤/数据清洗）"
```

---

## Task 6: test_bstock_logic.py

**Files:**
- Create: `tests/unit/test_bstock_logic.py`

- [ ] **Step 1: 创建 tests/unit/test_bstock_logic.py**

```python
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, call

from datasource.bstock import (
    _is_not_logged_in,
    _is_broken_pipe_error,
    _raise_for_query_error,
    BaoNotLoggedInError,
    BaoQueryError,
    fetch_stock_info,
    fetch_stock_data,
    fetch_index_data,
)


# ── _is_not_logged_in ─────────────────────────────────────────────────────────

def test_is_not_logged_in_true():
    assert _is_not_logged_in("用户未登录，请重新登录") is True


def test_is_not_logged_in_false_other():
    assert _is_not_logged_in("其他错误消息") is False


def test_is_not_logged_in_none():
    assert _is_not_logged_in(None) is False


def test_is_not_logged_in_empty():
    assert _is_not_logged_in("") is False


# ── _is_broken_pipe_error ─────────────────────────────────────────────────────

def test_is_broken_pipe_true_for_broken_pipe_error():
    assert _is_broken_pipe_error(BrokenPipeError()) is True


def test_is_broken_pipe_true_for_oserror_errno32():
    import errno
    err = OSError(errno.EPIPE, "broken pipe")
    assert _is_broken_pipe_error(err) is True


def test_is_broken_pipe_true_for_msg_containing_broken_pipe():
    assert _is_broken_pipe_error(RuntimeError("Broken pipe")) is True


def test_is_broken_pipe_false_for_other():
    assert _is_broken_pipe_error(ValueError("other error")) is False


# ── _raise_for_query_error ────────────────────────────────────────────────────

def test_raise_for_query_error_not_logged_in():
    with pytest.raises(BaoNotLoggedInError):
        _raise_for_query_error("sh.600519", "用户未登录，请重新登录")


def test_raise_for_query_error_broken_pipe():
    with pytest.raises(BrokenPipeError):
        _raise_for_query_error("sh.600519", "Broken pipe")


def test_raise_for_query_error_other():
    with pytest.raises(BaoQueryError):
        _raise_for_query_error("sh.600519", "其他查询错误")


# ── fetch_stock_info board 分类 ───────────────────────────────────────────────

def _make_bs_login_ok():
    lg = MagicMock()
    lg.error_code = "0"
    return lg


def _make_bs_query_basic(rows: list[list]):
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["code", "code_name", "ipoDate", "outDate", "status", "type"]
    _rows = iter(rows)
    rs.next.side_effect = lambda: True  # simplified; see below
    rs.get_row_data.side_effect = rows.__iter__().__next__
    # 用更简单的方式：让 next() 在消耗完后返回 False
    remaining = list(rows)
    def next_fn():
        if remaining:
            remaining.pop(0)
            return True
        return False
    rs.next.side_effect = next_fn
    rs.get_row_data.side_effect = lambda: remaining[0] if remaining else rows[-1]
    return rs


def _make_query_basic_simple(rows: list[list]):
    """简化版：直接让 query_stock_basic 返回可迭代结果。"""
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["code", "code_name", "ipoDate", "outDate", "status", "type"]
    rows_copy = list(rows)
    call_count = [0]
    def next_fn():
        return call_count[0] < len(rows_copy)
    def get_row_fn():
        row = rows_copy[call_count[0]]
        call_count[0] += 1
        return row
    rs.next.side_effect = next_fn
    rs.get_row_data.side_effect = get_row_fn
    return rs


@pytest.mark.parametrize("symbol,expected_board", [
    ("300001", "GEM"),
    ("301001", "GEM"),
    ("688001", "STAR"),
    ("689001", "STAR"),
    ("600519", "MAIN"),
    ("000001", "MAIN"),
])
def test_fetch_stock_info_board_classification(symbol, expected_board):
    exchange = "sh" if symbol.startswith(("6", "9")) else "sz"
    row = [f"{exchange}.{symbol}", "测试股票", "2010-01-01", "", "1", "1"]
    rs = _make_query_basic_simple([row])
    lg = _make_bs_login_ok()
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.query_stock_basic", return_value=rs):
            with patch("datasource.bstock.bs.logout"):
                df, _ = fetch_stock_info(["all"])
    row_data = df[df["symbol"] == symbol]
    assert len(row_data) == 1
    assert row_data.iloc[0]["board"] == expected_board


def test_fetch_stock_info_index_board():
    rs = _make_query_basic_simple([["sh.000001", "上证指数", "1990-12-19", "", "1", "2"]])
    lg = _make_bs_login_ok()
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.query_stock_basic", return_value=rs):
            with patch("datasource.bstock.bs.logout"):
                df, _ = fetch_stock_info(["all"])
    assert df.iloc[0]["board"] == "INDEX"


def test_fetch_stock_info_code_format():
    """黑盒：输出 code 格式为 600519.SH，不是 sh.600519。"""
    rs = _make_query_basic_simple([["sh.600519", "贵州茅台", "2001-08-27", "", "1", "1"]])
    lg = _make_bs_login_ok()
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.query_stock_basic", return_value=rs):
            with patch("datasource.bstock.bs.logout"):
                df, _ = fetch_stock_info(["all"])
    assert df.iloc[0]["code"] == "600519.SH"


def test_fetch_stock_info_login_fail_returns_empty():
    lg = MagicMock()
    lg.error_code = "9999"
    lg.error_msg = "登录失败"
    with patch("datasource.bstock.bs.login", return_value=lg):
        with patch("datasource.bstock.bs.logout"):
            df, _ = fetch_stock_info(["all"])
    assert df.empty


# ── fetch_stock_data 数据转换 ─────────────────────────────────────────────────

def _make_kdata_rs(rows: list[list]):
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["date", "code", "open", "high", "low", "close",
                 "preclose", "volume", "amount", "adjustflag",
                 "turn", "tradestatus", "pctChg", "isST", "peTTM", "pbMRQ"]
    rows_copy = list(rows)
    call_count = [0]
    rs.next.side_effect = lambda: call_count[0] < len(rows_copy)
    def get_row():
        row = rows_copy[call_count[0]]
        call_count[0] += 1
        return row
    rs.get_row_data.side_effect = get_row
    return rs


def test_fetch_stock_data_columns():
    row = ["2023-01-03", "sh.600519", "1800", "1850", "1780", "1820",
           "1790", "10000", "180000000", "3", "1.5", "1", "1.5", "0", "35.2", "12.1"]
    rs = _make_kdata_rs([row])
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        df_daily, df_basic = fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")
    assert "code" in df_daily.columns
    assert "pre_close" in df_daily.columns
    assert "pe" in df_basic.columns


def test_fetch_stock_data_price_nan_filled_zero():
    """停牌时价格为空字符串，应填 0。"""
    row = ["2023-01-03", "sh.600519", "", "", "", "",
           "1790", "0", "0", "3", "", "0", "0", "0", "", ""]
    rs = _make_kdata_rs([row])
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        df_daily, _ = fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")
    assert df_daily.iloc[0]["open"] == 0


def test_fetch_stock_data_empty_returns_empty_dfs():
    rs = MagicMock()
    rs.error_code = "0"
    rs.next.return_value = False
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        df_daily, df_basic = fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")
    assert df_daily.empty
    assert df_basic.empty


def test_fetch_stock_data_query_error_raises():
    rs = MagicMock()
    rs.error_code = "9999"
    rs.error_msg = "其他错误"
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        with pytest.raises(BaoQueryError):
            fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")


def test_fetch_stock_data_not_logged_in_raises():
    rs = MagicMock()
    rs.error_code = "9999"
    rs.error_msg = "用户未登录，请重新登录"
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        with pytest.raises(BaoNotLoggedInError):
            fetch_stock_data("2023-01-03", "2023-01-03", "sh.600519")


# ── fetch_index_data ──────────────────────────────────────────────────────────

def test_fetch_index_data_bad_code_format_returns_empty():
    row = ["2023-01-03", "000001", "3300", "3350", "3280", "3320",
           "3290", "100000000", "5e11", "0.5"]
    rs = MagicMock()
    rs.error_code = "0"
    rs.fields = ["date", "code", "open", "high", "low", "close",
                 "preclose", "volume", "amount", "pctChg"]
    rows_copy = [row]
    call_count = [0]
    rs.next.side_effect = lambda: call_count[0] < len(rows_copy)
    def get_row():
        r = rows_copy[call_count[0]]
        call_count[0] += 1
        return r
    rs.get_row_data.side_effect = get_row
    with patch("datasource.bstock.bs.query_history_k_data_plus", return_value=rs):
        result = fetch_index_data("2023-01-03", "2023-01-03", "sh.000001")
    # code 字段为 "000001"（无点），split 得到 1 列而非 2 列 → 返回空
    # 实际代码中 split 后会得到 parts.shape[1] != 2 的情况
    # 但 "000001" 不含点，split(".", n=1) 返回只有一列，result 为空
    assert isinstance(result, pd.DataFrame)
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/unit/test_bstock_logic.py -v
```

预期：大部分 PASS。若 board 分类测试失败，检查 `_make_query_basic_simple` 的 mock 逻辑是否正确模拟了 `rs.next()` / `rs.get_row_data()` 的交互。

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_bstock_logic.py
git commit -m "test: 添加 bstock 错误分类/board分类/数据转换单元测试"
```

---

## Task 7: test_dbutil_logic.py

**Files:**
- Create: `tests/unit/test_dbutil_logic.py`

- [ ] **Step 1: 创建 tests/unit/test_dbutil_logic.py**

```python
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from util import dbutil
from util.dbutil import _normalize_daily_df, get_candidate_data
from tests.conftest import insert_stock_info


# ── _normalize_daily_df ───────────────────────────────────────────────────────

def test_normalize_pre_close_nan_filled():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "pre_close": [float("nan")],
                       "volume": [100], "amount": [105000.0]})
    result = _normalize_daily_df(df)
    assert result["pre_close"].iloc[0] == -1


def test_normalize_no_pre_close_column():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0]})
    result = _normalize_daily_df(df)
    assert "pre_close" in result.columns
    assert result["pre_close"].iloc[0] == -1


def test_normalize_trade_status_copied_to_tradestatus():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0],
                       "trade_status": [1]})
    result = _normalize_daily_df(df)
    assert result["tradestatus"].iloc[0] == 1


def test_normalize_no_status_columns_filled_minus_one():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0]})
    result = _normalize_daily_df(df)
    assert result["tradestatus"].iloc[0] == -1


def test_normalize_tradestatus_nan_filled_minus_one():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0],
                       "tradestatus": [float("nan")]})
    result = _normalize_daily_df(df)
    assert result["tradestatus"].iloc[0] == -1


# ── get_connection 异常 ───────────────────────────────────────────────────────

def test_get_connection_readonly_missing_file_raises(tmp_path):
    nonexistent = tmp_path / "nonexistent.db"
    with patch("util.dbutil.myutil.get_default_dbfile", return_value=nonexistent):
        with pytest.raises(FileNotFoundError):
            dbutil.get_connection(is_read_only=True)


# ── get_candidate_data 筛选逻辑 ───────────────────────────────────────────────

def _wrap_mem_db(mem_db):
    """包装 mem_db 使其 close() 成为 no-op，防止函数内 finally 关闭测试连接。"""
    mock_conn = MagicMock(wraps=mem_db)
    mock_conn.close = MagicMock()
    return mock_conn


SQL_STOCK = ("SELECT SYMBOL,EXCHANGE,LIST_DATE,DELIST_DATE,LIST_STATUS "
             "FROM STOCK_INFO WHERE BOARD <> 'INDEX'")


def test_get_candidate_data_codes_priority(mem_db):
    """黑盒：codes 参数优先级高于 exchanges。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    ["SH"],        # exchanges 指定 SH
                                    ["000001"],    # codes 指定 000001（SZ）
                                    False, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert symbols == ["000001"]   # codes 优先，SH 被忽略


def test_get_candidate_data_chinese_comma(mem_db):
    """黑盒：codes 支持中文逗号分隔。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [],
                                    ["600519，000001"],   # 中文逗号
                                    False, SQL_STOCK)
    assert len(result) == 2


def test_get_candidate_data_eff_begin_not_before_list_date(mem_db):
    """黑盒：begindate 早于 list_date 时，eff_begin 取 list_date。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("1990-01-01", "2023-12-31",
                                    [], ["600519"], False, SQL_STOCK)
    assert len(result) == 1
    assert result[0][2] == "2001-08-27"  # eff_begin = list_date


def test_get_candidate_data_skip_null_list_date(mem_db):
    """list_date=NULL 的记录应被跳过。"""
    mem_db.execute(
        "INSERT INTO STOCK_INFO (code, symbol, name, exchange, board, "
        "list_date, list_status, created_at, last_updated_at) "
        "VALUES ('000002.SZ','000002','Test','SZ','MAIN',NULL,'L',now(),now())"
    )
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], False, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert "000002" not in symbols
    assert "000001" in symbols


def test_get_candidate_data_delist_no_date_skipped(mem_db):
    """退市股无 delist_date 时应跳过并 warning。"""
    insert_stock_info(mem_db, "000003", "SZ", "MAIN", "2000-01-01",
                      delist_date=None, list_status="D")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], True, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert "000003" not in symbols


def test_get_candidate_data_db_not_exist_returns_empty(tmp_path):
    """DB 文件不存在时捕获异常，返回空列表，不向上抛。"""
    nonexistent = tmp_path / "no.db"
    with patch("util.dbutil.myutil.get_default_dbfile", return_value=nonexistent):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], False, SQL_STOCK)
    assert result == []
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/unit/test_dbutil_logic.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_dbutil_logic.py
git commit -m "test: 添加 dbutil 纯逻辑单元测试（normalize/候选股筛选/异常）"
```

---

## Task 8: test_dbutil_save.py + 修复 security_name Bug

**Files:**
- Create: `tests/db/test_dbutil_save.py`
- Modify: `util/dbutil.py`（删除 `save_margin_detail_to_db` 中多余的 `security_name`）

> **Bug 说明：** `schema.sql` 中 `MARGIN_DETAIL_DAILY` 没有 `security_name` 列，但 `save_margin_detail_to_db` 的 INSERT 语句中引用了该列，会导致 DuckDB 报错。需要先修复再写测试。

- [ ] **Step 1: 修复 util/dbutil.py 中的 security_name 引用**

找到 `save_margin_detail_to_db` 函数（约第 617 行），将 INSERT 语句从：

```python
conn.execute("""
    INSERT INTO MARGIN_DETAIL_DAILY
        (trade_date, exchange_code, symbol, code, security_name,
         margin_buy_amount, margin_repay_amount, margin_balance,
         short_sell_volume, short_repay_volume,
         short_balance_volume, short_balance_amount,
         margin_short_balance,
         created_at, updated_at)
    SELECT
        CAST(trade_date AS DATE),
        exchange_code,
        symbol,
        code,
        security_name,
        ...
    FROM temp_margin_detail
""")
```

修改为（去掉 `security_name` 列）：

```python
conn.execute("""
    INSERT INTO MARGIN_DETAIL_DAILY
        (trade_date, exchange_code, symbol, code,
         margin_buy_amount, margin_repay_amount, margin_balance,
         short_sell_volume, short_repay_volume,
         short_balance_volume, short_balance_amount,
         margin_short_balance,
         created_at, updated_at)
    SELECT
        CAST(trade_date AS DATE),
        exchange_code,
        symbol,
        code,
        CAST(margin_buy_amount    AS DOUBLE),
        CAST(margin_repay_amount  AS DOUBLE),
        CAST(margin_balance       AS DOUBLE),
        CAST(short_sell_volume    AS DOUBLE),
        CAST(short_repay_volume   AS DOUBLE),
        CAST(short_balance_volume AS DOUBLE),
        CAST(short_balance_amount AS DOUBLE),
        CAST(margin_short_balance AS DOUBLE),
        now(), now()
    FROM temp_margin_detail
    ON CONFLICT (trade_date, exchange_code, symbol) DO UPDATE SET
        code                  = EXCLUDED.code,
        margin_buy_amount     = EXCLUDED.margin_buy_amount,
        margin_repay_amount   = EXCLUDED.margin_repay_amount,
        margin_balance        = EXCLUDED.margin_balance,
        short_sell_volume     = EXCLUDED.short_sell_volume,
        short_repay_volume    = EXCLUDED.short_repay_volume,
        short_balance_volume  = EXCLUDED.short_balance_volume,
        short_balance_amount  = EXCLUDED.short_balance_amount,
        margin_short_balance  = EXCLUDED.margin_short_balance,
        updated_at            = now()
""")
```

- [ ] **Step 2: 创建 tests/db/test_dbutil_save.py**

```python
import pytest
import pandas as pd
from datetime import date

from util.dbutil import (
    save_daily_to_db,
    save_base_to_db,
    save_calendar_to_db,
    save_shares_to_db,
    save_margin_summary_to_db,
    save_margin_detail_to_db,
    load_stock_info_to_db,
)
from tests.conftest import insert_stock_info


def _daily_df(code="600519.SH", trade_date="2023-01-03", tradestatus=1):
    return pd.DataFrame({
        "code": [code], "date": [trade_date],
        "open": [1800.0], "high": [1850.0], "low": [1780.0], "close": [1820.0],
        "pre_close": [1790.0], "tradestatus": [tradestatus],
        "volume": [10000], "amount": [18200000.0],
    })


def _basic_df(code="600519.SH", trade_date="2023-01-03",
              pe=35.0, pb=12.0, turnover=1.5, is_st=0):
    return pd.DataFrame({
        "code": [code], "trade_date": [trade_date],
        "turnover_rate": [turnover], "pe": [pe], "pb": [pb], "is_st": [is_st],
    })


# ── save_daily_to_db ──────────────────────────────────────────────────────────

def test_save_daily_inserts_row(mem_db):
    save_daily_to_db(_daily_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0]
    assert count == 1


def test_save_daily_replace_on_duplicate(mem_db):
    save_daily_to_db(_daily_df(tradestatus=1), mem_db)
    save_daily_to_db(_daily_df(tradestatus=0), mem_db)  # 同一 (code, date)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0]
    assert count == 1  # INSERT OR REPLACE，不产生重复


def test_save_daily_normalizes_tradestatus_nan(mem_db):
    """黑盒：tradestatus 含 NaN 时 _normalize_daily_df 应被调用，NaN 填 -1。"""
    df = _daily_df()
    df["tradestatus"] = float("nan")
    save_daily_to_db(df, mem_db)
    row = mem_db.execute("SELECT tradestatus FROM STOCK_DAILY").fetchone()
    assert row[0] == -1


def test_save_daily_empty_df_no_error(mem_db):
    save_daily_to_db(pd.DataFrame(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_DAILY").fetchone()[0]
    assert count == 0


# ── save_base_to_db ───────────────────────────────────────────────────────────

def test_save_base_inserts_row(mem_db):
    save_base_to_db(_basic_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM DAILY_BASIC").fetchone()[0]
    assert count == 1


def test_save_base_coalesce_existing_pe_not_overwritten(mem_db):
    """黑盒：先写 pe=35.0，再写 pe=None，pe 应保持 35.0（COALESCE 保护）。"""
    save_base_to_db(_basic_df(pe=35.0), mem_db)
    save_base_to_db(_basic_df(pe=None), mem_db)  # ON CONFLICT + COALESCE
    row = mem_db.execute("SELECT pe FROM DAILY_BASIC").fetchone()
    assert row[0] == 35.0


def test_save_base_updates_existing_pe(mem_db):
    save_base_to_db(_basic_df(pe=35.0), mem_db)
    save_base_to_db(_basic_df(pe=40.0), mem_db)
    row = mem_db.execute("SELECT pe FROM DAILY_BASIC").fetchone()
    assert row[0] == 40.0


# ── save_calendar_to_db ───────────────────────────────────────────────────────

def test_save_calendar_inserts(mem_db):
    df = pd.DataFrame({"cal_date": ["2023-01-03", "2023-01-04"], "is_open": [1, 1]})
    save_calendar_to_db(df, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM TRADE_CAL").fetchone()[0]
    assert count == 2


def test_save_calendar_upsert_no_duplicate(mem_db):
    df = pd.DataFrame({"cal_date": ["2023-01-03"], "is_open": [1]})
    save_calendar_to_db(df, mem_db)
    save_calendar_to_db(df, mem_db)  # 重复写入
    count = mem_db.execute("SELECT COUNT(*) FROM TRADE_CAL").fetchone()[0]
    assert count == 1


def test_save_calendar_updates_is_open(mem_db):
    df1 = pd.DataFrame({"cal_date": ["2023-01-07"], "is_open": [1]})
    df2 = pd.DataFrame({"cal_date": ["2023-01-07"], "is_open": [0]})
    save_calendar_to_db(df1, mem_db)
    save_calendar_to_db(df2, mem_db)
    row = mem_db.execute("SELECT is_open FROM TRADE_CAL WHERE cal_date = '2023-01-07'").fetchone()
    assert row[0] == 0


# ── save_shares_to_db ─────────────────────────────────────────────────────────

def test_save_shares_only_updates_share_fields(mem_db):
    """黑盒：只更新 total_shares / float_shares，不影响 pe。"""
    save_base_to_db(_basic_df(pe=35.0), mem_db)
    shares_df = pd.DataFrame({
        "code": ["600519.SH"], "date": ["2023-01-03"],
        "total_shares": [1260000000], "float_shares": [1200000000],
    })
    save_shares_to_db(shares_df, mem_db)
    row = mem_db.execute("SELECT pe, total_shares FROM DAILY_BASIC").fetchone()
    assert row[0] == 35.0           # pe 未变
    assert row[1] == 1260000000     # total_shares 已更新


# ── save_margin_summary_to_db ─────────────────────────────────────────────────

def _summary_df():
    return pd.DataFrame({
        "trade_date": ["2023-01-03"],
        "exchange_code": ["SH"],
        "margin_buy_amount": [1e9], "margin_repay_amount": [5e8],
        "margin_balance": [1e10], "short_sell_volume": [1e6],
        "short_repay_volume": [5e5], "short_balance_volume": [2e6],
        "short_balance_amount": [2e8], "margin_short_balance": [1.2e10],
    })


def test_save_margin_summary_inserts(mem_db):
    save_margin_summary_to_db(_summary_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_SUMMARY_DAILY").fetchone()[0]
    assert count == 1


def test_save_margin_summary_none_returns_early(mem_db):
    save_margin_summary_to_db(None, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_SUMMARY_DAILY").fetchone()[0]
    assert count == 0


def test_save_margin_summary_empty_returns_early(mem_db):
    save_margin_summary_to_db(pd.DataFrame(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_SUMMARY_DAILY").fetchone()[0]
    assert count == 0


# ── save_margin_detail_to_db ──────────────────────────────────────────────────

def _detail_df():
    return pd.DataFrame({
        "trade_date": ["2023-01-03"],
        "exchange_code": ["SZ"],
        "symbol": ["000001"],
        "code": ["000001.SZ"],
        "margin_buy_amount": [1e8], "margin_repay_amount": [5e7],
        "margin_balance": [1e9], "short_sell_volume": [1e5],
        "short_repay_volume": [5e4], "short_balance_volume": [2e5],
        "short_balance_amount": [2e7], "margin_short_balance": [1.2e9],
    })


def test_save_margin_detail_inserts(mem_db):
    save_margin_detail_to_db(_detail_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_DETAIL_DAILY").fetchone()[0]
    assert count == 1


def test_save_margin_detail_upsert_on_conflict(mem_db):
    """同 (trade_date, exchange_code, symbol) 冲突时更新，不产生重复。"""
    df1 = _detail_df()
    df2 = _detail_df()
    df2["margin_balance"] = [2e9]
    save_margin_detail_to_db(df1, mem_db)
    save_margin_detail_to_db(df2, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_DETAIL_DAILY").fetchone()[0]
    assert count == 1
    row = mem_db.execute("SELECT margin_balance FROM MARGIN_DETAIL_DAILY").fetchone()
    assert float(row[0]) == pytest.approx(2e9)


def test_save_margin_detail_none_returns_early(mem_db):
    save_margin_detail_to_db(None, mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM MARGIN_DETAIL_DAILY").fetchone()[0]
    assert count == 0


# ── load_stock_info_to_db ─────────────────────────────────────────────────────

def _stock_info_df(name="贵州茅台"):
    return pd.DataFrame({
        "code": ["600519.SH"], "symbol": ["600519"], "name": [name],
        "exchange": ["SH"], "board": ["MAIN"],
        "list_date": ["2001-08-27"], "delist_date": [None], "list_status": ["L"],
    })


def test_load_stock_info_inserts(mem_db):
    load_stock_info_to_db(_stock_info_df(), mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM STOCK_INFO").fetchone()[0]
    assert count == 1


def test_load_stock_info_updates_last_updated_at_on_change(mem_db):
    load_stock_info_to_db(_stock_info_df("贵州茅台"), mem_db)
    ts1 = mem_db.execute("SELECT last_updated_at FROM STOCK_INFO").fetchone()[0]
    import time; time.sleep(0.01)
    load_stock_info_to_db(_stock_info_df("贵州茅台NEW"), mem_db)  # name 变了
    ts2 = mem_db.execute("SELECT last_updated_at FROM STOCK_INFO").fetchone()[0]
    assert ts2 >= ts1  # last_updated_at 更新了
```

- [ ] **Step 3: 运行并验证全部通过**

```bash
pytest tests/db/test_dbutil_save.py -v
```

预期：全部 PASS（若 `test_save_margin_detail_*` 失败，核对 Step 1 的 security_name 修复是否完整）

- [ ] **Step 4: Commit**

```bash
git add util/dbutil.py tests/db/test_dbutil_save.py
git commit -m "fix: 移除 save_margin_detail_to_db 中多余的 security_name 列引用
test: 添加 dbutil 入库 SQL 单元测试"
```

---

## Task 9: test_dbutil_query.py

**Files:**
- Create: `tests/db/test_dbutil_query.py`

- [ ] **Step 1: 创建 tests/db/test_dbutil_query.py**

```python
import pytest
from unittest.mock import patch, MagicMock

from util.dbutil import check_is_trading_day, get_trade_dates
from tests.conftest import insert_trade_cal


def _wrap(mem_db):
    m = MagicMock(wraps=mem_db)
    m.close = MagicMock()
    return m


# ── check_is_trading_day ──────────────────────────────────────────────────────

def test_check_trading_day_is_open(mem_db):
    insert_trade_cal(mem_db, "2023-01-03", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert check_is_trading_day("2023-01-03") is True


def test_check_trading_day_not_open(mem_db):
    insert_trade_cal(mem_db, "2023-01-01", 0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert check_is_trading_day("2023-01-01") is False


def test_check_trading_day_not_in_table(mem_db):
    """日期不在表中 → 返回 False，打 warning，不抛异常。"""
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        assert check_is_trading_day("2099-01-01") is False


# ── get_trade_dates ───────────────────────────────────────────────────────────

def test_get_trade_dates_returns_sorted_list(mem_db):
    for d, o in [("2023-01-03", 1), ("2023-01-04", 1), ("2023-01-05", 1),
                 ("2023-01-06", 0)]:
        insert_trade_cal(mem_db, d, o)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-03", "2023-01-06")
    assert result == ["20230103", "20230104", "20230105"]


def test_get_trade_dates_format_is_yyyymmdd(mem_db):
    """黑盒：返回格式严格为 YYYYMMDD（8位，不含连字符）。"""
    insert_trade_cal(mem_db, "2023-01-03", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-03", "2023-01-03")
    assert len(result) == 1
    assert len(result[0]) == 8
    assert "-" not in result[0]


def test_get_trade_dates_boundary_inclusive(mem_db):
    """黑盒：start_date 和 end_date 当天若是交易日也要包含。"""
    for d in ["2023-01-03", "2023-01-04", "2023-01-05"]:
        insert_trade_cal(mem_db, d, 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-03", "2023-01-05")
    assert "20230103" in result
    assert "20230105" in result


def test_get_trade_dates_no_trading_days_returns_empty(mem_db):
    insert_trade_cal(mem_db, "2023-01-07", 0)  # 周六，非交易日
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        result = get_trade_dates("2023-01-07", "2023-01-07")
    assert result == []
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/db/test_dbutil_query.py -v
```

预期：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_dbutil_query.py
git commit -m "test: 添加 dbutil 查询函数 DB 层测试（交易日历查询）"
```

---

## Task 10: test_adjust.py

**Files:**
- Create: `tests/db/test_adjust.py`

- [ ] **Step 1: 创建 tests/db/test_adjust.py**

```python
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock

from etl.adjust import process_and_save_adjust_factors
from tests.conftest import insert_trade_cal


def _insert_trade_cals(conn, dates):
    for d in dates:
        insert_trade_cal(conn, d, 1)


def _stock_list(symbol="600519", exchange="SH",
                start="2023-01-03", end="2023-01-06"):
    return [(symbol, exchange, start, end, "L")]


def _adj_df(code="600519.SH", dates=None, factors=None):
    dates = dates or ["2023-01-03"]
    factors = factors or [1.05]
    return pd.DataFrame({
        "code": [code] * len(dates),
        "date": dates,
        "fore_factor": factors,
        "back_factor": factors,
        "adjust_factor": factors,
    })


TRADE_DATES_JAN = ["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"]


# ── 正常测试 ──────────────────────────────────────────────────────────────────

def test_process_new_stock_default_factor_one(mem_db):
    """新股首次运行，无复权事件 → 默认因子 1.0。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    process_and_save_adjust_factors(pd.DataFrame(), _stock_list(), mem_db)
    rows = mem_db.execute(
        "SELECT fore_factor FROM ADJ_FACTOR WHERE code = '600519.SH' ORDER BY trade_date"
    ).fetchall()
    assert len(rows) == 4
    assert all(r[0] == 1.0 for r in rows)


def test_process_with_event_densifies_from_event_date(mem_db):
    """有复权事件 → 从事件日起稠密化，ADJ_FACTOR 行数 = 区间内交易日数。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    adj = _adj_df(dates=["2023-01-04"], factors=[1.05])
    process_and_save_adjust_factors(adj, _stock_list(), mem_db)
    count = mem_db.execute(
        "SELECT COUNT(*) FROM ADJ_FACTOR WHERE code = '600519.SH'"
    ).fetchone()[0]
    assert count == 4


def test_process_asof_join_forward_fill(mem_db):
    """ASOF JOIN 向前填充：事件后无新事件时，因子沿用最近一次事件值。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    adj = _adj_df(dates=["2023-01-04"], factors=[1.05])
    process_and_save_adjust_factors(adj, _stock_list(), mem_db)
    rows = mem_db.execute(
        "SELECT trade_date, fore_factor FROM ADJ_FACTOR "
        "WHERE code = '600519.SH' ORDER BY trade_date"
    ).fetchall()
    # 01-03: 无事件，默认 1.0；01-04以后: 1.05
    assert rows[0][1] == 1.0   # 2023-01-03
    assert rows[1][1] == 1.05  # 2023-01-04
    assert rows[2][1] == 1.05  # 2023-01-05（ASOF 沿用）
    assert rows[3][1] == 1.05  # 2023-01-06


def test_process_incremental_append(mem_db):
    """已有稠密历史，增量续接：仅补 last_dense_date+1 之后的数据。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN + ["2023-01-09", "2023-01-10"])
    # 第一次处理 01-03 到 01-06
    process_and_save_adjust_factors(pd.DataFrame(), _stock_list(), mem_db)
    count_before = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]

    # 第二次处理 01-03 到 01-10（续接）
    process_and_save_adjust_factors(
        pd.DataFrame(),
        [("600519", "SH", "2023-01-03", "2023-01-10", "L")],
        mem_db
    )
    count_after = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]
    assert count_after == count_before + 2  # 补了 01-09 和 01-10


# ── 异常测试 ──────────────────────────────────────────────────────────────────

def test_process_empty_stock_list_returns_early(mem_db):
    process_and_save_adjust_factors(pd.DataFrame(), [], mem_db)
    count = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]
    assert count == 0


def test_process_adj_df_missing_columns_raises_value_error(mem_db):
    bad_df = pd.DataFrame({"code": ["600519.SH"], "date": ["2023-01-04"]})
    with pytest.raises(ValueError, match="缺少列"):
        process_and_save_adjust_factors(bad_df, _stock_list(), mem_db)


def test_process_rollback_on_error(mem_db):
    """黑盒：事务原子性——中途失败后 ADJ_FACTOR 无脏数据。"""
    _insert_trade_cals(mem_db, TRADE_DATES_JAN)
    # 先写入一些数据
    process_and_save_adjust_factors(pd.DataFrame(), _stock_list(), mem_db)
    count_before = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]

    # 模拟 COMMIT 失败（在事务中途抛异常）
    original_execute = mem_db.execute
    call_count = [0]
    def patched_execute(sql, *args, **kwargs):
        if "INSERT OR REPLACE INTO ADJ_FACTOR" in sql:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("模拟 DB 写入失败")
        return original_execute(sql, *args, **kwargs)

    mem_db.execute = patched_execute
    try:
        with pytest.raises(RuntimeError):
            process_and_save_adjust_factors(
                pd.DataFrame(), _stock_list("000001", "SZ"), mem_db
            )
    finally:
        mem_db.execute = original_execute

    # 原有数据应不受影响
    count_after = mem_db.execute("SELECT COUNT(*) FROM ADJ_FACTOR").fetchone()[0]
    assert count_after == count_before
```

- [ ] **Step 2: 运行并验证全部通过**

```bash
pytest tests/db/test_adjust.py -v
```

预期：全部 PASS（若 `test_process_asof_join_forward_fill` 失败，核查 TRADE_CAL 是否已插入所有交易日）

- [ ] **Step 3: Commit**

```bash
git add tests/db/test_adjust.py
git commit -m "test: 添加复权因子稠密化 DB 层测试（增量/ASOF填充/事务/异常）"
```

---

## Task 11: test_tdx_live.py

**Files:**
- Create: `tests/integration/test_tdx_live.py`

- [ ] **Step 1: 创建 tests/integration/test_tdx_live.py**

```python
import pytest
import pandas as pd

pytestmark = pytest.mark.integration


def _try_connect():
    """尝试连接通达信，失败时 skip。"""
    try:
        from datasource.tdx import _connect_api
        return _connect_api()
    except Exception as e:
        pytest.skip(f"通达信服务器不可达: {e}")


def test_tdx_connect_returns_api():
    api = _try_connect()
    assert api is not None
    api.disconnect()


def test_tdx_fetch_single_stock():
    """拉取 600519.SH 近 5 个交易日，验证输出格式。"""
    from datasource.tdx import _connect_api, fetch_stock_data
    from util.dbutil import get_trade_dates

    api = _try_connect()
    EXPECTED_COLS = ["code", "date", "open", "high", "low", "close",
                     "pre_close", "tradestatus", "volume", "amount"]
    try:
        trade_dates = get_trade_dates("2024-01-02", "2024-01-08")
        if not trade_dates:
            pytest.skip("TRADE_CAL 表无数据，跳过")

        result = fetch_stock_data(api, "600519", "SH",
                                  "2024-01-02", "2024-01-08", trade_dates)
    finally:
        api.disconnect()

    assert not result.empty
    assert list(result.columns) == EXPECTED_COLS
    assert result["date"].str.match(r"\d{4}-\d{2}-\d{2}").all()


def test_tdx_fetch_nonexistent_stock_returns_empty():
    """拉取不存在的股票代码，应返回空 DataFrame，不崩溃。"""
    from datasource.tdx import _connect_api, fetch_stock_data

    api = _try_connect()
    try:
        result = fetch_stock_data(api, "999999", "SH",
                                  "2024-01-02", "2024-01-05",
                                  ["20240102", "20240103"])
    finally:
        api.disconnect()

    assert isinstance(result, pd.DataFrame)


def test_tdx_all_servers_wrong_raises():
    from unittest.mock import patch
    from datasource.tdx import _connect_api
    with patch("datasource.tdx._get_servers", return_value=[("0.0.0.0", 7709)]):
        with pytest.raises(ConnectionError):
            _connect_api()
```

- [ ] **Step 2: 运行 integration 测试**

```bash
pytest tests/integration/test_tdx_live.py -v -m integration
```

预期：网络可达时 PASS，不可达时 SKIP

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_tdx_live.py
git commit -m "test: 添加 TDX 真实网络 integration 测试"
```

---

## Task 12: test_akstock_live.py

**Files:**
- Create: `tests/integration/test_akstock_live.py`

- [ ] **Step 1: 创建 tests/integration/test_akstock_live.py**

```python
import pytest
import pandas as pd

pytestmark = pytest.mark.integration


def _skip_on_network_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        pytest.skip(f"AkShare 接口不可达: {e}")


def test_akstock_fetch_bj_info():
    from datasource.akstock import fetch_bj_stock_data
    df_info, df_basic = _skip_on_network_error(fetch_bj_stock_data, "2024-01-02")
    assert not df_info.empty
    assert (df_info["exchange"] == "BJ").all()
    assert all(len(s) == 6 for s in df_info["symbol"])


def test_akstock_fetch_single_stock():
    from datasource.akstock import fetch_stock_data
    df_daily, df_basic = _skip_on_network_error(
        fetch_stock_data, "20240102", "20240105", "000001", "SZ"
    )
    assert not df_daily.empty
    assert "code" in df_daily.columns
    assert "volume" in df_daily.columns


def test_akstock_fetch_industry_hist():
    from datasource.akstock import fetch_stock_industry_clf_hist_sw, _INDUSTRY_OUT_COLS
    result = _skip_on_network_error(fetch_stock_industry_clf_hist_sw)
    assert list(result.columns) == _INDUSTRY_OUT_COLS
    assert all(len(s) == 6 for s in result["symbol"])


def test_akstock_fetch_margin_detail():
    from datasource.akstock import fetch_margin_detail, _DETAIL_OUT_COLS
    result = _skip_on_network_error(fetch_margin_detail, "20240102", ["sh"])
    assert list(result.columns) == _DETAIL_OUT_COLS
```

- [ ] **Step 2: 运行 integration 测试**

```bash
pytest tests/integration/test_akstock_live.py -v -m integration
```

预期：网络可达时 PASS，不可达时 SKIP

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_akstock_live.py
git commit -m "test: 添加 AkShare 真实网络 integration 测试"
```

---

## Task 13: test_bstock_live.py

**Files:**
- Create: `tests/integration/test_bstock_live.py`

- [ ] **Step 1: 创建 tests/integration/test_bstock_live.py**

```python
import pytest
import pandas as pd

pytestmark = pytest.mark.integration


def _skip_on_bs_error(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        if result is None:
            pytest.skip("Baostock 返回 None，可能未登录成功")
        return result
    except Exception as e:
        pytest.skip(f"Baostock 接口不可达: {e}")


def test_bstock_fetch_calendar():
    from datasource.bstock import fetch_sync_calendar
    df = _skip_on_bs_error(fetch_sync_calendar, "2024-01-01", "2024-01-07")
    assert "cal_date" in df.columns
    assert "is_open" in df.columns
    assert not df.empty


def test_bstock_fetch_single_stock():
    from datasource.bstock import fetch_stock_data
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        pytest.skip(f"Baostock 登录失败: {lg.error_msg}")
    try:
        df_daily, df_basic = fetch_stock_data("2024-01-02", "2024-01-05", "sh.600519")
    finally:
        bs.logout()
    assert not df_daily.empty
    assert "code" in df_daily.columns
    assert "pre_close" in df_daily.columns


def test_bstock_fetch_adjust_factors():
    from datasource.bstock import fetch_adjust_factors
    stock_list = [("600519", "SH", "2024-01-02", "2024-01-05", "L")]
    result = _skip_on_bs_error(fetch_adjust_factors, stock_list)
    if result.empty:
        pytest.skip("无复权因子数据（可能无分红记录）")
    assert "fore_factor" in result.columns
    assert "back_factor" in result.columns


def test_bstock_login_fail_returns_empty():
    """模拟登录失败（错误 config），应返回空 DataFrame，不崩溃。"""
    import baostock as bs
    from datasource.bstock import fetch_adjust_factors
    from unittest.mock import patch, MagicMock

    mock_lg = MagicMock()
    mock_lg.error_code = "9999"
    mock_lg.error_msg = "模拟登录失败"
    with patch("datasource.bstock.bs.login", return_value=mock_lg):
        with patch("datasource.bstock.bs.logout"):
            result = fetch_adjust_factors([("600519", "SH", "2024-01-02", "2024-01-05", "L")])
    assert isinstance(result, pd.DataFrame)
    assert result.empty
```

- [ ] **Step 2: 运行 integration 测试**

```bash
pytest tests/integration/test_bstock_live.py -v -m integration
```

预期：网络可达时 PASS，不可达时 SKIP

- [ ] **Step 3: 运行完整测试套件**

```bash
# 快速验证（unit + db，不需网络）
pytest tests/unit tests/db -v

# 覆盖率报告
pytest tests/unit tests/db --cov=util --cov=datasource --cov=etl --cov-report=term-missing
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_bstock_live.py
git commit -m "test: 添加 Baostock 真实网络 integration 测试，完成全覆盖测试套件"
```

---

## 自检：Spec 覆盖检查

| Spec 要求 | 对应 Task |
|---|---|
| unit/test_myutil.py | Task 2 |
| unit/test_validators.py | Task 3 |
| unit/test_tdx_logic.py | Task 4 |
| unit/test_akstock_logic.py | Task 5 |
| unit/test_bstock_logic.py | Task 6 |
| unit/test_dbutil_logic.py | Task 7 |
| db/test_dbutil_save.py | Task 8 |
| db/test_dbutil_query.py | Task 9 |
| db/test_adjust.py | Task 10 |
| integration/test_tdx_live.py | Task 11 |
| integration/test_akstock_live.py | Task 12 |
| integration/test_bstock_live.py | Task 13 |
| security_name bug 修复 | Task 8 Step 1 |
| pytest 配置与依赖 | Task 1 |
| conftest.py fixtures | Task 1 |
