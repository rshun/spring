# Spring

Spring 是一个专为 AI 驱动的量化交易和金融分析设计的 A 股数据基础设施平台。该项目利用本地化高性能的 [DuckDB](https://duckdb.org/) 进行金融时序数据的存储与处理，并通过 **MCP (Model Context Protocol)** 协议将数据能力直接暴露给大语言模型（如 Claude），赋能 AI 智能体进行深度的量化研究与策略开发。

## 🌟 核心特性

- **自动化数据 ETL**：集成 `AKShare`、`Baostock` 和 `pytdx`，支持自动化拉取和更新 A 股日线数据、交易日历、复权因子、申万行业分类、融资融券及股本变动信息。
- **高性能本地存储**：以 DuckDB 为底层数据库，提供极速的列式数据查询与统计能力，轻松处理海量历史金融数据。
- **AI 智能体无缝集成**：内置基于 FastMCP 实现的 `duckdb-quant-readonly` 服务端，大模型可通过标准化 Tool 直接调用金融数据接口、计算技术指标或执行探索性 SQL 检索。

## 📂 项目结构

```text
spring/
├── etl/                # 数据获取与清洗脚本
│   ├── init_db.py      # 数据库初始化
│   ├── fetch_index.py  # 获取指数数据
│   ├── import_daily.py # 导入日线行情数据
│   ├── sync_basic.py   # 同步每日基础指标（市值、换手率、市盈率等）
│   ├── sync_capital.py # 同步股本变动与权息资料
│   ├── sync_sw_industry.py # 同步申万行业分类
│   ├── trade_cal.py    # 交易日历同步
│   └── adjust.py, ...  # 其他复权因子及数据处理脚本
├── mcp_server/         # MCP 服务端代码
│   └── server.py       # DuckDB 供大模型调用的 FastMCP 核心服务
├── sql/                # 数据库定义与管理
│   └── schema.sql      # DuckDB 核心表结构定义（如 STOCK_INFO, STOCK_DAILY 等）
├── util/               # 核心工具包
│   ├── dbutil.py       # 数据库连接与执行工具
│   ├── myutil.py       # 通用辅助函数
│   └── validators.py   # 数据校验逻辑
└── requirements.txt    # Python 依赖清单
```

## 🛠️ 安装与配置

1. **环境准备**
   确保已安装 Python 3.9+，并安装所需依赖：
   ```bash
   pip install -r requirements.txt
   ```

2. **数据库初始化**
   配置环境变量 `DUCKDB_PATH` 指向你的本地数据库文件路径，然后执行初始化脚本建表：
   ```bash
   # Windows (PowerShell)
   $env:DUCKDB_PATH="C:\path\to\your\quant.duckdb"
   python etl/init_db.py
   ```

3. **数据同步**
   按照需求运行 `etl/` 目录下的脚本，同步基础信息与日线数据（建议配置定时任务进行盘后更新）。

4. **启动 MCP 服务**
   用于对接 Claude 或其他支持 MCP 协议的客户端：
   ```bash
   python mcp_server/server.py
   ```

## 🤖 MCP 工具能力 (Tools)

通过 `mcp_server/server.py`，项目向 AI 模型提供了丰富的量化工具，大模型可以直接调用以下功能：

- `search_stock` / `get_stock_info`: 股票检索及基本面信息获取
- `get_stock_daily` / `get_daily_basic`: 获取指定股票的历史 K 线及每日核心指标（换手率、PE、PB、量比等）
- `calc_indicators`: 动态计算技术指标（如各种周期的均线 MA、成交量均线 VOL_MA、收益率等）
- `get_adj_factor` / `get_capital_detail`: 获取复权因子与除权除息/送配股明细
- `get_stock_industry`: 查询股票的申万行业（一/二/三级）归属及历史变动
- `get_margin_data`: 获取融资融券明细
- `get_model_pool`: 检索并跟踪量化策略模型输出的股票池（观察、关注、触发名单）
- `query`: 提供安全的只读 SQL 查询接口，方便 AI 进行复杂的交叉分析

## 📊 数据表核心概览

- `STOCK_INFO`: 股票基础信息（代码、名称、板块、上市状态等）
- `STOCK_DAILY`: 股票日线行情（开高低收、成交量、成交额）
- `DAILY_BASIC`: 每日基本面衍生指标（市值、涨跌停状态等）
- `TRADE_CAL`: 交易日历
- `STOCK_SW_INDUSTRY`: 申万行业分类及历史映射
- `CAPITAL_DETAIL`: 股本变动及除权除息资料

## 📝 开发协议

1. **只读保护**：MCP 服务默认处于只读模式 (`duckdb-quant-readonly`)，拦截所有的 DDL/DML 操作以保障本地数据安全。
2. **轻量连接**：数据库在 MCP 请求中采用 Connect-Per-Request（短连接）的策略，避免了多线程死锁或长期锁表的问题。
