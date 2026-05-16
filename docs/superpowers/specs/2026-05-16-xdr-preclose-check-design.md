# 除权日 pre_close 校验 — 设计文档

日期: 2026-05-16
涉及文件: `tools/check_daily.py`

## 背景与目标

正常交易日 `STOCK_DAILY.pre_close`(前收盘价)等于上一交易日收盘价。但在**除权除息日**,前收盘价应为**除权后的参考价**,不再等于上一交易日原始收盘价。

为 `tools/check_daily.py` 增加一项校验:对校验区间内所有发生除权除息的股票,用独立公式算出理论除权参考价,与数据库中实际的 `pre_close` 比对,找出不一致的记录。

## 关键决策(已与用户确认)

| 决策点 | 选择 |
|--------|------|
| 期望值计算基准 | `CAPITAL_DETAIL` 除权公式(独立交叉校验,而非 `ADJ_FACTOR` 内部一致性) |
| 不一致判定容差 | 绝对差 > 0.01 元(A股报价精度为分) |
| 输出与退出码 | 写 CSV 并将异常数计入 `total_missing`,有异常时主程序返回 1 |
| 实现结构 | 单条 SQL CTE 查询(贴合现有 `_check_*` 风格,一次数据库往返) |

## 数据模型依据

- `STOCK_DAILY(code, date, close, pre_close, tradestatus, ...)` — 待校验的 `pre_close`
- `CAPITAL_DETAIL(code, date, category, dividend, allotment_price, bonus_share, allotment_share)`
  - `category='除权除息'` 为除权除息事件(`config/config.yaml` 中 `capital_category` 映射 "1" → "除权除息")
  - `date` = 权息日(即除权日 T)
  - `dividend` = 每10股现金分红
  - `allotment_price` = 配股价(元)
  - `bonus_share` = 每10股送转股
  - `allotment_share` = 每10股配股
- `TRADE_CAL(cal_date, is_open)` — 用于定位上一交易日
- `STOCK_INFO(code, name, board, list_status, exchange, ...)` — 范围过滤

## 除权参考价公式(通达信口径)

每10股字段折算为每股:

```
theory_preclose =
    (close_prev - dividend/10 + allotment_price * allotment_share/10)
    / (1 + bonus_share/10 + allotment_share/10)
```

- `close_prev` = 该股上一交易日 `STOCK_DAILY.close`
- 各权息字段用 `COALESCE(..., 0)` 兜底
- 分母 `1 + bonus_share/10 + allotment_share/10` 恒 ≥ 1,无除零风险

## 实现设计

### 新增函数

在 `tools/check_daily.py` 的 `_check_is_st_null` 之后新增:

```
_check_xdr_preclose(conn, begin_date, end_date,
                    ex_filter, code_filter, code_params) -> int
```

返回不一致记录条数(供 `main()` 累加进 `total_missing`)。

### SQL 逻辑(CTE 分层)

1. **`xdr_events`**:`CAPITAL_DETAIL` 中 `category='除权除息'` 且 `date BETWEEN begin AND end`,
   `INNER JOIN STOCK_INFO i`,过滤 `board NOT IN ('INDEX','BJ')`、`list_status='L'`、`{ex_filter}`、`{code_filter}`
2. **`prev_day`**:对每个 (code, 除权日 T),从 `TRADE_CAL`(`is_open=1`)取**严格小于 T 的最大 cal_date** = 上一交易日
3. 关联:
   - `STOCK_DAILY pd1 ON pd1.code = xdr.code AND pd1.date = prev_day` → `close_prev = pd1.close`
   - `STOCK_DAILY cur ON cur.code = xdr.code AND cur.date = T` → `cur.pre_close`、`cur.tradestatus`
4. SQL 内按公式计算 `theory`
5. 过滤条件:
   - `cur.tradestatus <> 0`(停牌不校验)
   - `close_prev IS NOT NULL`
   - `cur.pre_close IS NOT NULL`
   - `ROUND(ABS(cur.pre_close - theory), 4) > 0.01`
6. 返回列:`(date, code, name, close_prev, pre_close, theory, diff)`,按 `date, code` 排序

### 上一交易日收盘缺失的处理

`close_prev IS NULL`(或除权日 `pre_close IS NULL`)的除权股无法计算理论值:
- 不计入异常数
- 单独一次 `logger.warning` 提示这类记录的条数(可选附样例),便于人工核查

### 输出

- 无异常:`logger.info("[除权前收价  ]    完整 OK")`,返回 0
- 有异常:
  - 写 `csv/check_preclose_xdr_<begin>_<end>.csv`
  - 表头:`date,code,name,close_prev,pre_close,theory_preclose,diff`
  - `logger.warning` 报告异常条数与文件路径
  - 返回异常条数(计入 `total_missing`,主程序退出码 1)
- CSV 写入复用现有约定:`csv_dir = Path(__file__).parent.parent / "csv"`,`encoding="utf-8-sig"`

### main() 集成

- 在 `_check_is_st_null(...)` 调用之后、`if args.include_index:` 之前插入:
  ```
  total_missing += _check_xdr_preclose(conn, begin_date, end_date,
                                       ex_filter, code_filter, code_params)
  ```
- 日志头(`logger.info` 块)增加一行说明本项校验始终执行
- 无需新增命令行参数

## 测试

在 `tests/db/` 下新增 `_check_xdr_preclose` 用例(仿 `tests/db/test_adjust.py` 的 in-memory duckdb fixture 风格):

- 构造一条"送股+现金分红"除权记录,验证理论价计算正确
- 容差边界:`diff = 0.01` 不报、`diff = 0.02` 报
- 停牌(`tradestatus=0`)被跳过
- 上一交易日收盘缺失:不计入异常,走 warning 分支
- 纯现金分红、纯送转、含配股各一条样例验证公式分支

## 范围与非目标

- 仅校验 `category='除权除息'`,不处理其他股本变动类别
- 不修改/回填 `pre_close`,只做检测与报告
- 不引入 pandas 依赖,保持本工具纯 duckdb
