# 除权日 pre_close 校验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `tools/check_daily.py` 增加除权日前收盘价(pre_close)校验:用 CAPITAL_DETAIL 除权公式独立算出理论除权参考价,与 STOCK_DAILY.pre_close 比对,差 > 0.01 元的写 CSV 并计入 total_missing。

**Architecture:** 把"查询出不一致记录"与"写 CSV + 日志 + 返回计数"拆成两个函数:纯查询函数 `_query_xdr_preclose_mismatches` 便于单元测试(无文件副作用),包装函数 `_check_xdr_preclose` 复用现有 CSV/日志约定。另有一个统计"无法计算"(上一交易日收盘缺失)的小查询。集成进 `main()`,无新增命令行参数。

**Tech Stack:** Python 3, duckdb(单条 SQL CTE 查询),pytest + in-memory DuckDB fixture(`mem_db`)。

---

## File Structure

- `tools/check_daily.py`(Modify):新增 `_query_xdr_preclose_mismatches`、`_count_xdr_uncomputable`、`_check_xdr_preclose` 三个函数;在 `main()` 中调用并累加 total_missing;补日志头一行。
- `tests/db/test_check_daily.py`(Create):针对纯查询函数的单元测试,使用 conftest 的 `mem_db`、`insert_stock_info`、`insert_trade_cal`。

参数与别名约定(对齐现有代码):`_build_exchange_filter` 生成的片段引用 `i.exchange`,`_build_code_filter` 生成的片段引用 `i.symbol` 且用 `?` 占位;新查询里 STOCK_INFO 别名必须是 `i`。SQL 参数顺序固定为 `[begin_date, end_date, *code_params]`(ex_filter 无参数,code_filter 在日期参数之后)。

---

### Task 1: 纯查询函数 `_query_xdr_preclose_mismatches`

**Files:**
- Modify: `tools/check_daily.py`(在 `_check_is_st_null` 之后、`_check_table` 之前新增函数)
- Test: `tests/db/test_check_daily.py`(Create)

- [ ] **Step 1: 写失败测试**

创建 `tests/db/test_check_daily.py`:

```python
import pytest

from tools.check_daily import _query_xdr_preclose_mismatches
from tests.conftest import insert_stock_info, insert_trade_cal


def _seed_stock(conn, symbol="600519", exchange="SH", board="MAIN"):
    insert_stock_info(conn, symbol, exchange, board, "2020-01-01")


def _ins_cal(conn, dates):
    for d in dates:
        insert_trade_cal(conn, d, 1)


def _ins_daily(conn, code, date, close=None, pre_close=None, tradestatus=1):
    conn.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, 0, 0, 0, ?, ?, ?, 0, 0)",
        [code, date, close, pre_close, tradestatus],
    )


def _ins_xdr(conn, code, date, dividend=0, allotment_price=0,
             bonus_share=0, allotment_share=0):
    conn.execute(
        "INSERT INTO CAPITAL_DETAIL (code, date, category, dividend, "
        "allotment_price, bonus_share, allotment_share, updated_at) "
        "VALUES (?, ?, '除权除息', ?, ?, ?, ?, now())",
        [code, date, dividend, allotment_price, bonus_share, allotment_share],
    )


def test_mismatch_detected_with_bonus_and_dividend(mem_db):
    """送股+分红:theory=(close_prev - div/10 + 0)/(1+bonus/10).
    close_prev=11, dividend=5(每10股), bonus_share=10(每10股) →
    theory=(11-0.5)/(1+1)=5.25。pre_close=11 与 theory 差远 → 命中。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-01-04", "2023-01-05"])
    _ins_daily(mem_db, "600519.SH", "2023-01-04", close=11.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-01-05", close=5.2, pre_close=11.0)
    _ins_xdr(mem_db, "600519.SH", "2023-01-05", dividend=5, bonus_share=10)

    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-01-05", "2023-01-05", "", "", []
    )
    assert len(rows) == 1
    xdr_date, code, name, close_prev, pre_close, theory = rows[0]
    assert code == "600519.SH"
    assert close_prev == 11.0
    assert pre_close == 11.0
    assert abs(theory - 5.25) < 1e-9
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `python -m pytest tests/db/test_check_daily.py::test_mismatch_detected_with_bonus_and_dividend -v`
Expected: FAIL — `ImportError: cannot import name '_query_xdr_preclose_mismatches'`

- [ ] **Step 3: 实现 `_query_xdr_preclose_mismatches`**

在 `tools/check_daily.py` 中,`_check_is_st_null` 函数之后插入:

```python
# 除权参考价公式(通达信口径,每10股字段折算为每股)
_XDR_THEORY_SQL = (
    "(close_prev - dividend/10 + allotment_price * allotment_share/10) "
    "/ (1 + bonus_share/10 + allotment_share/10)"
)


def _query_xdr_preclose_mismatches(conn: duckdb.DuckDBPyConnection,
                                   begin_date: str, end_date: str,
                                   ex_filter: str, code_filter: str,
                                   code_params: list[str]) -> list[tuple]:
    """
    查询除权日 pre_close 与理论除权参考价不一致(绝对差 > 0.01 元)的记录。
    仅 category='除权除息'、A股(board NOT IN ('INDEX','BJ'))、list_status='L';
    停牌(tradestatus=0)、上一交易日收盘缺失、当日 pre_close 缺失的记录不在此返回。
    返回 [(xdr_date, code, name, close_prev, pre_close, theory), ...]
    """
    sql = f"""
    WITH xdr_events AS (
        SELECT c.code, c.date AS xdr_date, i.name,
               COALESCE(c.dividend, 0)        AS dividend,
               COALESCE(c.allotment_price, 0) AS allotment_price,
               COALESCE(c.bonus_share, 0)     AS bonus_share,
               COALESCE(c.allotment_share, 0) AS allotment_share
        FROM CAPITAL_DETAIL c
        INNER JOIN STOCK_INFO i ON i.code = c.code
        WHERE c.category = '除权除息'
          AND c.date BETWEEN ? AND ?
          AND i.board NOT IN ('INDEX', 'BJ')
          AND i.list_status = 'L'
          {ex_filter}
          {code_filter}
    ),
    prev_day AS (
        SELECT e.code, e.xdr_date,
               (SELECT MAX(t.cal_date) FROM TRADE_CAL t
                 WHERE t.is_open = 1 AND t.cal_date < e.xdr_date) AS prev_date
        FROM xdr_events e
    ),
    joined AS (
        SELECT e.code, e.xdr_date, e.name,
               e.dividend, e.allotment_price, e.bonus_share, e.allotment_share,
               pd1.close       AS close_prev,
               cur.pre_close   AS pre_close,
               cur.tradestatus AS tradestatus
        FROM xdr_events e
        JOIN prev_day p ON p.code = e.code AND p.xdr_date = e.xdr_date
        LEFT JOIN STOCK_DAILY pd1 ON pd1.code = e.code AND pd1.date = p.prev_date
        LEFT JOIN STOCK_DAILY cur ON cur.code = e.code AND cur.date = e.xdr_date
    )
    SELECT xdr_date, code, name, close_prev, pre_close,
           {_XDR_THEORY_SQL} AS theory
    FROM joined
    WHERE COALESCE(tradestatus, 1) <> 0
      AND close_prev IS NOT NULL
      AND pre_close IS NOT NULL
      AND ROUND(ABS(pre_close - ({_XDR_THEORY_SQL})), 4) > 0.01
    ORDER BY xdr_date, code
    """
    params = [begin_date, end_date, *code_params]
    return conn.execute(sql, params).fetchall()
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `python -m pytest tests/db/test_check_daily.py::test_mismatch_detected_with_bonus_and_dividend -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tools/check_daily.py tests/db/test_check_daily.py
git commit -m "feat: 新增除权日 pre_close 不一致查询函数"
```

---

### Task 2: 容差边界 / 停牌 / 缺失 的查询行为测试

**Files:**
- Test: `tests/db/test_check_daily.py`(Modify)

- [ ] **Step 1: 追加失败测试**

在 `tests/db/test_check_daily.py` 末尾追加:

```python
def test_tolerance_boundary(mem_db):
    """纯现金分红场景:close_prev=10, dividend=1(每10股=0.1/股) →
    theory=(10-0.1)/1=9.9。pre_close=9.91 → diff=0.01 不报;
    pre_close=9.92 → diff=0.02 报。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-02-01", "2023-02-02"])
    _ins_daily(mem_db, "600519.SH", "2023-02-01", close=10.0, pre_close=10.0)

    # 边界内:diff = 0.01,不报
    _ins_daily(mem_db, "600519.SH", "2023-02-02", close=9.9, pre_close=9.91)
    _ins_xdr(mem_db, "600519.SH", "2023-02-02", dividend=1)
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-02-02", "2023-02-02", "", "", []
    )
    assert rows == []

    # 改成 diff = 0.02,应报
    mem_db.execute(
        "UPDATE STOCK_DAILY SET pre_close = 9.92 "
        "WHERE code = '600519.SH' AND date = '2023-02-02'"
    )
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-02-02", "2023-02-02", "", "", []
    )
    assert len(rows) == 1


def test_suspended_skipped(mem_db):
    """除权日停牌(tradestatus=0)不参与校验。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-03-01", "2023-03-02"])
    _ins_daily(mem_db, "600519.SH", "2023-03-01", close=10.0, pre_close=10.0)
    _ins_daily(mem_db, "600519.SH", "2023-03-02", close=10.0,
               pre_close=10.0, tradestatus=0)
    _ins_xdr(mem_db, "600519.SH", "2023-03-02", dividend=20)  # theory≠10
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-03-02", "2023-03-02", "", "", []
    )
    assert rows == []


def test_prev_close_missing_not_returned(mem_db):
    """上一交易日无收盘记录 → 无法计算,不在不一致结果中返回。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-04-03", "2023-04-04"])
    # 不写 04-03 的 STOCK_DAILY(close_prev 缺失)
    _ins_daily(mem_db, "600519.SH", "2023-04-04", close=9.0, pre_close=9.0)
    _ins_xdr(mem_db, "600519.SH", "2023-04-04", dividend=20)
    rows = _query_xdr_preclose_mismatches(
        mem_db, "2023-04-04", "2023-04-04", "", "", []
    )
    assert rows == []
```

- [ ] **Step 2: 运行测试**

Run: `python -m pytest tests/db/test_check_daily.py -v`
Expected: 全部 PASS(查询逻辑在 Task 1 已实现,这些测试验证既有行为)

- [ ] **Step 3: 若有失败则修正 `_query_xdr_preclose_mismatches`**

仅当某断言失败时,核对 SQL 的 `COALESCE(tradestatus, 1) <> 0`、`close_prev IS NOT NULL`、`ROUND(...,4) > 0.01` 三处条件并修正,使全部测试通过。无失败则跳过。

- [ ] **Step 4: 提交**

```bash
git add tests/db/test_check_daily.py tools/check_daily.py
git commit -m "test: 除权 pre_close 校验的容差/停牌/缺失行为测试"
```

---

### Task 3: `_count_xdr_uncomputable` 与包装函数 `_check_xdr_preclose`

**Files:**
- Modify: `tools/check_daily.py`
- Test: `tests/db/test_check_daily.py`(Modify)

- [ ] **Step 1: 写失败测试**

在 `tests/db/test_check_daily.py` 末尾追加:

```python
from tools.check_daily import _count_xdr_uncomputable


def test_count_uncomputable_counts_missing_prev_close(mem_db):
    """除权且非停牌、但上一交易日收盘缺失 → 计入"无法计算"计数。"""
    _seed_stock(mem_db)
    _ins_cal(mem_db, ["2023-04-03", "2023-04-04"])
    _ins_daily(mem_db, "600519.SH", "2023-04-04", close=9.0, pre_close=9.0)
    _ins_xdr(mem_db, "600519.SH", "2023-04-04", dividend=20)
    n = _count_xdr_uncomputable(
        mem_db, "2023-04-04", "2023-04-04", "", "", []
    )
    assert n == 1
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `python -m pytest tests/db/test_check_daily.py::test_count_uncomputable_counts_missing_prev_close -v`
Expected: FAIL — `ImportError: cannot import name '_count_xdr_uncomputable'`

- [ ] **Step 3: 实现 `_count_xdr_uncomputable` 与 `_check_xdr_preclose`**

在 `_query_xdr_preclose_mismatches` 之后插入:

```python
def _count_xdr_uncomputable(conn: duckdb.DuckDBPyConnection,
                            begin_date: str, end_date: str,
                            ex_filter: str, code_filter: str,
                            code_params: list[str]) -> int:
    """统计除权且非停牌、但因上一交易日收盘或当日 pre_close 缺失而无法计算理论价的记录数"""
    sql = f"""
    WITH xdr_events AS (
        SELECT c.code, c.date AS xdr_date
        FROM CAPITAL_DETAIL c
        INNER JOIN STOCK_INFO i ON i.code = c.code
        WHERE c.category = '除权除息'
          AND c.date BETWEEN ? AND ?
          AND i.board NOT IN ('INDEX', 'BJ')
          AND i.list_status = 'L'
          {ex_filter}
          {code_filter}
    ),
    prev_day AS (
        SELECT e.code, e.xdr_date,
               (SELECT MAX(t.cal_date) FROM TRADE_CAL t
                 WHERE t.is_open = 1 AND t.cal_date < e.xdr_date) AS prev_date
        FROM xdr_events e
    )
    SELECT COUNT(*)
    FROM xdr_events e
    JOIN prev_day p ON p.code = e.code AND p.xdr_date = e.xdr_date
    LEFT JOIN STOCK_DAILY pd1 ON pd1.code = e.code AND pd1.date = p.prev_date
    LEFT JOIN STOCK_DAILY cur ON cur.code = e.code AND cur.date = e.xdr_date
    WHERE COALESCE(cur.tradestatus, 1) <> 0
      AND (pd1.close IS NULL OR cur.pre_close IS NULL)
    """
    params = [begin_date, end_date, *code_params]
    return conn.execute(sql, params).fetchone()[0]


def _check_xdr_preclose(conn: duckdb.DuckDBPyConnection,
                        begin_date: str, end_date: str,
                        ex_filter: str, code_filter: str,
                        code_params: list[str]) -> int:
    """除权日 pre_close 校验:不一致写 CSV 并返回异常条数(计入 total_missing)"""
    label = "除权前收价  "
    uncomputable = _count_xdr_uncomputable(conn, begin_date, end_date,
                                           ex_filter, code_filter, code_params)
    if uncomputable:
        logger.warning(
            f"[{label}]    {uncomputable} 条除权记录因上一交易日收盘/当日pre_close缺失无法校验"
        )

    rows = _query_xdr_preclose_mismatches(conn, begin_date, end_date,
                                          ex_filter, code_filter, code_params)
    if not rows:
        logger.info(f"[{label}]    完整 OK")
        return 0

    csv_dir = Path(__file__).parent.parent / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_file = csv_dir / f"check_preclose_xdr_{begin_date}_{end_date}.csv"
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "code", "name", "close_prev",
                         "pre_close", "theory_preclose", "diff"])
        for xdr_date, code, name, close_prev, pre_close, theory in rows:
            diff = round(abs(pre_close - theory), 4)
            writer.writerow([str(xdr_date), code, name,
                             close_prev, pre_close, round(theory, 4), diff])

    logger.warning(f"[{label}]    发现 {len(rows)} 条 pre_close 与除权理论价不一致，"
                    f"明细已写入: {csv_file}")
    return len(rows)
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `python -m pytest tests/db/test_check_daily.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add tools/check_daily.py tests/db/test_check_daily.py
git commit -m "feat: 除权 pre_close 校验包装函数与无法计算计数"
```

---

### Task 4: 集成进 `main()`

**Files:**
- Modify: `tools/check_daily.py`(`main()` 函数,约 311-341 行附近)

- [ ] **Step 1: 在日志头增加一行说明**

将 `main()` 中这一行:

```python
    logger.info(f"     校验指数: {'是' if args.include_index else '否'}")
    logger.info("=" * 60)
```

改为:

```python
    logger.info(f"     校验指数: {'是' if args.include_index else '否'}")
    logger.info(f"     除权校验: 是 (除权日 pre_close 与理论除权价比对)")
    logger.info("=" * 60)
```

- [ ] **Step 2: 在 is_st 校验之后调用新校验**

将 `main()` 中这一段:

```python
        total_missing += _check_is_st_null(conn, begin_date, end_date,
                                           ex_filter, code_filter, code_params)
        if args.include_index:
```

改为:

```python
        total_missing += _check_is_st_null(conn, begin_date, end_date,
                                           ex_filter, code_filter, code_params)
        total_missing += _check_xdr_preclose(conn, begin_date, end_date,
                                             ex_filter, code_filter, code_params)
        if args.include_index:
```

- [ ] **Step 3: 运行全量测试,确认无回归**

Run: `python -m pytest tests/ -m "not integration" -q`
Expected: 全部 PASS(含新增 `tests/db/test_check_daily.py`)

- [ ] **Step 4: 冒烟运行工具(无除权也应正常结束)**

Run: `python -m tools.check_daily -b 20260515 -c 600519`
Expected: 进程退出,日志含 `[除权前收价  ]` 行(`完整 OK` 或发现条数),无异常堆栈。
(若本地无数据库,此步可跳过,以测试通过为准。)

- [ ] **Step 5: 提交**

```bash
git add tools/check_daily.py
git commit -m "feat: check_daily 集成除权日 pre_close 校验"
```

---

## Self-Review

**Spec coverage:**
- CAPITAL_DETAIL 除权公式作期望值 → Task 1 `_XDR_THEORY_SQL`,通达信每10股折算口径 ✓
- 容差绝对差 > 0.01 元 → Task 1 SQL `ROUND(ABS(...),4) > 0.01`,Task 2 边界测试 ✓
- 写 CSV 并计入 total_missing,退出码 1 → Task 3 `_check_xdr_preclose`、Task 4 `main()` 累加 ✓
- 单条 SQL CTE / 一次往返 → Task 1、Task 3 各一条查询 ✓
- 范围过滤 board/list_status/ex_filter/code_filter,别名 `i` → Task 1/Task 3 SQL ✓
- 上一交易日收盘缺失:不计异常、单独 warning 计数 → Task 3 `_count_xdr_uncomputable` + warning ✓
- 停牌不校验 → Task 1 SQL `COALESCE(tradestatus,1) <> 0`,Task 2 测试 ✓
- 仅 category='除权除息',不改 pre_close,不引入 pandas → 全程仅查询 ✓
- main() 日志头加一行、无新参数 → Task 4 ✓
- 测试仿 test_adjust.py fixture 风格,覆盖公式/边界/停牌/缺失 → Task 1-3 ✓

**Placeholder scan:** 无 TODO/TBD;所有代码步骤含完整代码与确切命令。

**Type consistency:** 三个函数签名一致 `(conn, begin_date, end_date, ex_filter, code_filter, code_params)`;`_query_xdr_preclose_mismatches` 返回 6 元组 `(xdr_date, code, name, close_prev, pre_close, theory)`,Task 3 解包与 Task 1 测试解包一致;`_XDR_THEORY_SQL` 在 SELECT 与 WHERE 复用同一常量,公式一致。
