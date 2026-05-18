# Spring

Spring 是一个专为 AI 驱动的量化交易和金融分析设计的 A 股数据基础设施平台。该项目利用本地化高性能的 [DuckDB](https://duckdb.org/) 进行金融时序数据的存储与处理，并通过 **MCP (Model Context Protocol)** 协议将数据能力直接暴露给大语言模型（如 Claude），赋能 AI 智能体进行深度的量化研究与策略开发。

## 🌟 核心特性

- **自动化数据 ETL**：集成 `AKShare`、`Baostock`、`pytdx` 及本地通达信日线（`lday`），支持自动化拉取和更新 A 股日线数据、交易日历、复权因子、申万行业分类、融资融券及股本变动信息。
- **高性能本地存储**：以 DuckDB 为底层数据库，提供极速的列式数据查询与统计能力，轻松处理海量历史金融数据。
- **AI 智能体无缝集成**：内置基于 FastMCP 实现的 `duckdb-quant-readonly` 服务端，大模型可通过标准化 Tool 直接调用金融数据接口、计算技术指标或执行探索性 SQL 检索。

## 📂 项目结构

```text
spring/
├── etl/                 # 数据获取与清洗脚本
│   ├── init_db.py       # 数据库初始化
│   ├── trade_cal.py     # 交易日历同步
│   ├── import_daily.py  # 导入日线行情数据
│   ├── fetch_index.py   # 获取指数数据
│   ├── adjust.py        # 复权因子入库
│   ├── sync_basic.py    # 同步每日基础指标（市值、换手率、市盈率等）
│   ├── sync_capital.py  # 同步股本变动与权息资料 (gbbq)
│   ├── sync_industry.py # 同步申万行业分类
│   ├── sync_margin.py   # 同步融资融券汇总和明细数据
│   ├── fill_volratio.py # 补齐量比
│   ├── update_limit.py  # 补齐涨跌停
│   └── fill_shares.py   # 回填总股本/流通股本(及市值)
├── datasource/          # 数据源适配器
│   ├── akstock.py       # AKShare 数据源
│   ├── bstock.py        # Baostock 数据源
│   ├── tdx.py           # 通达信 pytdx 在线接口
│   └── lday.py          # 本地通达信日线文件
├── mcp_server/          # MCP 服务端代码
│   └── server.py        # DuckDB 供大模型调用的 FastMCP 核心服务
├── sql/                 # 数据库定义与管理
│   └── schema.sql       # DuckDB 核心表结构定义（如 STOCK_INFO, STOCK_DAILY 等）
├── tools/               # 工具类
│   └── check_daily.py   # 校验数据是否完整
├── util/                # 核心工具包
│   ├── dbutil.py        # 数据库连接与执行工具
│   ├── myutil.py        # 通用辅助函数
│   ├── config.py        # 读取 config/config.yaml 配置
│   └── validators.py    # 数据校验逻辑
├── config/              # 配置文件 (config.yaml: 数据库路径、数据源等)
├── tests/               # 测试 (unit / db / integration 三层)
└── requirements.txt     # Python 依赖清单
```

## 🛠️ 安装与配置

### **环境准备**
   确保已安装 Python 3.10+（代码使用 `X | None` 等 PEP 604 写法，3.9 无法运行），并安装所需依赖：
```bash
   pip install -r requirements.txt
```

### **数据库初始化**
   在 `config/config.yaml` 中配置 `local_paths.db` 指向你的本地数据库文件路径（默认 `~/data/quant.db`），然后执行初始化脚本建表：
```yaml
   # config/config.yaml
   local_paths:
     db: "~/data/quant.db"
```
```bash
   python -m etl.init_db
```

**校验数据是否完整**
```bash
python -m tools.check_daily
```

**启动 MCP 服务**
用于对接 Claude 或其他支持 MCP 协议的客户端：
```bash
  python mcp_server/server.py
```

### **ETL**  
#### 初始化表  
```bash
# 第一次或者创建新表的时候运行
python -m etl.init_db
```

#### 同步交易日  
```bash
# 每年年底公布次年节假日之后运行
python -m etl.trade_cal -b 20000101
```

#### 同步股票基本信息  
```bash
# 每天运行
python -m etl.sync_basic

# 从akstock数据源中获取京市股本信息(若当日不是交易日也强制执行)
python -m etl.sync_basic -x bj -s akstock -f

```

#### 同步股本股息资料gbbq (默认优先级: 从本地目录读取,csv/gbbq,通达信服务器上下载)  
```bash
# 每天运行
python -m etl.sync_capital 

# 优先从通达信服务器下载gbbq
python -m etl.sync_capital --download
```

#### 同步股票日线数据  
```bash
# 每天运行(获取当天)
python -m etl.import_daily

# 从lday数据源中获取从2000-01-01到2025-12-31的京市的日线数据
python -m etl.import_daily -b 20000101 -e 20251231 -x bj -s lday 

```

#### 同步指数日线数据  
```bash
# 每天运行(获取当天)
python -m etl.fetch_index 

# 从lday数据源中获取从2000-01-01的指数数据
python -m etl.fetch_index -b 20000101 -s lday
```

#### 同步复权因子  
```bash
# 每天运行(获取当天)
python -m etl.adjust  

# 从bstock数据源中获取从2000-01-01开始所有股票的复权因子
python -m etl.adjust -b 20000101
```

#### 补齐指标量比,涨停,流通市值(流通市值等数据前置条件是需要股本资料)  
```bash
# 补齐深沪市量比数据(补齐T-1日,每天运行)
python -m etl.fill_volratio

# 补齐深沪市涨停数据(补齐T-1日,每天运行)
python -m etl.update_limit 

# 根据CAPITAL_DETAIL回填DAILY_BASIC的总股本和流通股本(默认补齐T-1日，每天运行)  
# 由于新股上市会有数据上窗口空缺, 所以回填数据以15天为限来保证股本数据有值  
python  -m etl.fill_shares -b 20260501 -e 20260515  
```

#### 同步申万行业数据  
```bash
# 每天运行
python -m etl.sync_industry

# 从文件中同步申万一、二级行业数据(执行一次,默认2021版本)  
python -m etl.sync_industry --input 

# 从文件中同步申万一、二级行业数据(如果未来有更新)  
python -m etl.sync_industry --input SwClassCode_2014.csv --version 2014
```

#### 同步融资融券数据 (akshare 数据源, 暂无北交所接口)
```bash
# 每天运行(获取T-1日的汇总和明细)
python -m etl.sync_margin

# 获取从 2025-01-01 到 2025-05-07 的沪市融资融券数据
python -m etl.sync_margin -b 20250101 -e 20250507 -x sh

# 仅同步汇总数据 (--only summary|detail|all, 默认 all)
python -m etl.sync_margin --only summary
```

## 🤖 MCP 工具能力 (Tools)

通过 `mcp_server/server.py`，项目向 AI 模型提供了丰富的量化工具，大模型可以直接调用以下功能：

- `search_stock` / `get_stock_info`: 股票检索及基本面信息获取
- `get_stock_daily` / `get_daily_basic`: 获取指定股票的历史 K 线及每日核心指标（换手率、PE、PB、量比等）
- `calc_indicators`: 动态计算技术指标（如各种周期的均线 MA、成交量均线 VOL_MA、收益率等）
- `get_adj_factor` / `get_capital_detail`: 获取复权因子与除权除息/送配股明细
- `get_margin_summary` / `get_margin_detail`: 获取交易所级融资融券每日汇总及个股明细
- `get_stock_industry` / `get_stock_industry_history`: 查询股票的申万行业（一/二/三级）归属及历史变动
- `query`: 提供安全的只读 SQL 查询接口，方便 AI 进行复杂的交叉分析

## 📊 数据表核心概览

- `STOCK_INFO`: 股票基础信息（代码、名称、板块、上市状态等）
- `STOCK_DAILY`: 股票日线行情（开高低收、前收、成交量、成交额、交易状态）
- `DAILY_BASIC`: 每日基本面衍生指标（PE、PB、换手率、量比、总/流通市值、总/流通股本、涨跌停、是否 ST）
- `TRADE_CAL`: 交易日历
- `ADJ_FACTOR`: 逐日复权因子（前/后复权因子）
- `ADJ_FACTOR_RAW`: 复权因子原始事件数据
- `SW_INDUSTRY`: 申万行业定义（一/二/三级，按版本）
- `STOCK_INDUSTRY_CLF_HIST_SW_RAW`: 股票申万行业历史原始数据
- `STOCK_SW_INDUSTRY_VIEW`: 申万行业分类历史查询视图（一/二/三级展开）
- `CAPITAL_DETAIL`: 股本变动及除权除息资料
- `MARGIN_SUMMARY_DAILY`: 融资融券每日汇总数据（按交易所）
- `MARGIN_DETAIL_DAILY`: 融资融券每日明细数据（按个股）

## 🧪 测试

```bash
# 运行所有测试
pytest

# 仅运行某一层
pytest tests/unit/
pytest tests/db/
pytest tests/integration/
```

测试分三层：

| 层级 | 目录 | 说明 |
|------|------|------|
| Unit | `tests/unit/` | 纯逻辑测试，无外部依赖 |
| DB | `tests/db/` | SQL 逻辑测试，使用 in-memory DuckDB |
| Integration | `tests/integration/` | 真实网络请求，可用 `pytest -m "not integration"` 跳过 |

## 📝 开发协议

1. **只读保护**：MCP 服务默认处于只读模式 (`duckdb-quant-readonly`)，拦截所有的 DDL/DML 操作以保障本地数据安全。
2. **轻量连接**：数据库在 MCP 请求中采用 Connect-Per-Request（短连接）的策略，避免了多线程死锁或长期锁表的问题。


## ❓ 已知问题  
- tdx  
   - **返回的成交量和金额不精确，是返回的\*100**
   - **没有IS_ST这个值，在计算涨跌停会有问题**
   - **若当日除权, 会使用未复权的前收盘价, 这样会导致涨跌停价格有问题**

- lday  
   - **没有IS_ST这个值，在计算涨跌停会有问题**
   - **务必要保证通达信的APP中，日线数据已经成功下载，否则表中的数据会出现异常**
   - **若当日除权, 会使用未复权的前收盘价, 这样会导致涨跌停价格有问题**

- akstock
   - **京市返回的成交量和金额不精确，是返回的\*100**
   - **除了获取基本信息这个接口之外，其它接口都会存在网络不稳定**
   - **获取的京市基本数据暂无退市的股票**

- bstock
   - **有时候日线有数据，且下载未报错, 但pb,pe不一定有值, 如果后续有用到需要先校验再使用**
   - **此数据源以下日期和股票缺失**
      - 2012-09-10,001872.SZ,招商港口
      - 2012-09-10,001914.SZ,招商积余
      - 2012-09-10,302132.SZ,中航成飞

## ⚠ 注意  
   - **若当日停牌,tdx,lday,bstock均会插入数据到STOCK_DAILY和DAILY_BASIC表**
   - **若股票在STOCK_INFO表中上市状态标识为已经退市，则不会获取历史数据**
   - **若使用不同数据源下载数据，原STOCK_DAILY和DAILY_BASIC表的数据会被清空，新数据会填入进去**
   - **import_daily和fetch_index都存在上述3个共性问题**
   - **建议优先使用bstock这个数据源, lday作为补充**

## 📋️ TODO  
- 补齐换手率
- 下载财务数据
- 根据除权除息资料校验pre_close是否准确
- 补齐pb,pe
