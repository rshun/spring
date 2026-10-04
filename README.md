# Spring

Spring 是一个专为 AI 驱动的量化交易和金融分析设计的 A 股数据基础设施平台。该项目利用本地化高性能的 [DuckDB](https://duckdb.org/) 进行金融时序数据的存储与处理，并通过 **MCP (Model Context Protocol)** 协议将数据能力直接暴露给大语言模型（如 Claude），赋能 AI 智能体进行深度的量化研究与策略开发。

## 🌟 核心特性

- **自动化数据 ETL**：集成 `AKShare`、`Baostock`、`pytdx` 及本地通达信日线（`lday`），支持自动化拉取和更新 A 股日线数据、交易日历、复权因子、申万行业分类、融资融券及股本变动信息。
- **高性能本地存储**：以 DuckDB 为底层数据库，提供极速的列式数据查询与统计能力，轻松处理海量历史金融数据。
- **AI 智能体无缝集成**：配套独立项目 [quant-mcp](https://github.com/rshun/quant-mcp) 提供基于 FastMCP 的 `duckdb-quant-readonly` 只读服务端，大模型可通过标准化 Tool 直接调用金融数据接口、计算技术指标或执行探索性 SQL 检索。
- **AI 驱动的管道运维**：配套独立项目 [etl-quant-mcp](https://github.com/rshun/etl-quant-mcp) 提供 `quant-etl` 服务端，让大模型能触发本项目的 ETL、判定夜跑是否卡死、并按数据缺口定向补数。与只读侧对称：那边读数据，这边跑管道。

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
│   ├── sync_finance.py  # 同步专业财务报表 (通达信 cw, 按报告期)
│   ├── sync_suspension.py  # 同步停牌名单 (第三方独立事实源, 供 check_daily 交叉核对)
│   ├── sync_limit_pool.py  # 同步涨跌停股池 (第三方独立事实源, 供 check_daily 交叉核对)
│   ├── fill_volratio.py # 补齐量比
│   ├── update_limit.py  # 补齐涨跌停
│   └── fill_shares.py   # 回填总股本/流通股本(及市值)
├── datasource/          # 数据源适配器
│   ├── akstock.py       # AKShare 数据源
│   ├── bstock.py        # Baostock 数据源
│   ├── tdx.py           # 通达信 pytdx 在线接口
│   └── lday.py          # 本地通达信日线文件
├── sql/                 # 数据库定义与管理
│   └── schema.sql       # DuckDB 核心表结构定义（如 STOCK_INFO, STOCK_DAILY 等）
├── tools/               # 工具类
│   ├── check_daily.py          # 校验数据是否完整(含停牌/涨跌停一致性核对)
│   ├── checks/                 # check_daily 的告警类核对项子包
│   │   ├── suspension.py       # 停牌一致性核对(SUSPENSION_DAILY vs STOCK_DAILY)
│   │   └── limit_pool.py       # 涨跌停一致性核对(LIMIT_POOL_DAILY vs DAILY_BASIC)
│   ├── export_etl_tables.py    # 按程序导出其写入的表 (跨机器搬运数据)
│   ├── import_etl_tables.py    # 导入上面导出的 parquet (幂等 upsert)
│   └── init_home.py            # 安装包部署: 初始化运行目录 SPRING_HOME (spring-init)
├── util/                # 核心工具包
│   ├── dbutil.py        # 数据库连接与执行工具
│   ├── myutil.py        # 通用辅助函数
│   ├── config.py        # 读取 config/config.yaml 配置
│   ├── validators.py    # 数据校验逻辑
│   ├── checker.py       # 核对框架: 结果状态机与外部数据源可用性判定
│   └── paths.py         # 程序目录 / 运行目录(SPRING_HOME) 路径约定
├── config/              # 配置文件 (config.yaml: 数据库路径、数据源等; config.yaml.example: 占位符模板)
├── data/                # 基础输入数据 (随仓库分发)
│   ├── SwClassCode_*.csv        # 申万行业层级定义 (sync_industry --input)
│   ├── reform_resume_days.csv   # 不设涨跌幅名单: 股改复牌首日 (update_limit, 见下文)
│   └── share_listing_days.csv   # 不设涨跌幅名单: 增发 / 追加对价股份上市日 (update_limit, 见下文)
├── tests/               # 测试 (unit / db / integration 三层)
├── pyproject.toml       # 安装包(wheel)构建配置与 spring-* 命令入口
└── requirements.txt     # Python 依赖清单
```

## 🛠️ 安装与配置

### Baostock 按日期批量下载

只带日期参数时，日线和复权因子分别使用
`query_daily_history_k_AStock(date=...)` 和 `query_daily_adjust_factor(date=...)`，
按区间内交易日逐日请求，并保留原候选股票、上市日期和退市过滤规则。

```bash
python -m etl.import_daily                              # 当天全市场，走按日接口
python -m etl.import_daily -b 20260901 -e 20260904      # 区间，走按日接口
python -m etl.adjust -s bstock                          # 当天全市场，走按日接口
python -m etl.adjust -s bstock -b 20260907 -e 20260911  # 区间，按交易日逐日循环
```

以上命令会按原有流程写入数据库。
指数下载、其他数据源、字段转换及复权因子补齐规则保持不变。

两个程序都没有路由开关，走哪条完全由参数形态决定：

| 程序 | 按日接口 | 逐股接口 |
|------|----------|----------|
| `import_daily` | 不带参数 / 只带 `-b` / 只带 `-e` / `-b` `-e` 都带 | 带了 `-c`、`-x`、`-s`、`-p` 中任意一个 |
| `adjust`（须显式 `-s bstock`） | 不限定范围：默认当天、`-b`/`-e` 区间、`-x all` | 带了 `-c`，或 `-x` 指定了具体交易所（`sh`/`sz`/`bj`） |

带这些参数意味着只要一部分股票或换了数据源，此时逐股请求本就比拉全市场再筛
更划算，所以不提供强制走按日接口的开关。按日接口也只有 bstock 源提供：
`import_daily` 的 `-s lday`、`-s tdx` 一律走逐股；`adjust` 的默认源 `-s local`
是纯库内自算、完全不联网，任何参数形态都不走按日接口。

两者在 `-x all` 上的判定不同：`import_daily` 只要出现 `-x` 就退回逐股，
`adjust` 把显式 `-x all` 视为与默认相同的全市场，仍走按日。

baostock 的网络类错误码（`10002xxx`，连接/收发失败或超时）按瞬时故障处理，
重登重试；重试耗尽或遇到非瞬时错误仍然中止且不写入部分数据。

运行环境需包含上述两个接口（已核对官方 PyPI 的 baostock 0.9.3 发布包）。
可先检查当前解释器，以下命令不访问网络、不写数据库：

```bash
python -c "import baostock as bs; print(bs.__file__); print(hasattr(bs, 'query_daily_history_k_AStock'), hasattr(bs, 'query_daily_adjust_factor'))"
```

若结果不是两个 `True`，需在目标虚拟环境中手动升级；升级会替换该环境中的
baostock，执行前应记录原版本，以便必要时恢复。仅 `pip install baostock`
可能保留已经安装的旧版，升级命令为：

```bash
python -m pip install --upgrade baostock -i https://pypi.org/simple
```

### **环境准备**
   确保已安装 Python 3.11+（依赖的 numpy 2.4 要求 3.11 起），并安装所需依赖：
```bash
   pip install -r requirements.txt
```

### **安装包部署（Windows / Linux / macOS 通用）**
除上面的源码部署外，也可以打成 wheel 安装包部署。两种方式共用同一套代码，区别只在两个目录：

| 目录 | 内容 | 源码部署 | 安装包部署 |
|------|------|----------|------------|
| 程序目录 | 代码与随包资源 `sql/`、`data/`、`config/pipeline.yaml` | 项目根目录 | Python 环境的 site-packages |
| 运行目录 `SPRING_HOME` | `config/config.yaml`、`.env`、`log/`、`csv/`、`download/` | 项目根目录 | 默认 `~/.spring`，可用环境变量 `SPRING_HOME` 指定 |

判定规则见 `util/paths.py`：设置了环境变量 `SPRING_HOME` 就用它；否则程序目录下有
`pyproject.toml`（源码检出）就用项目根目录，与改造前完全一致；否则用 `~/.spring`。

```bash
# 1. 构建(在源码检出里执行; 需要 build 工具)
python -m build

# 2. 目标机安装(推荐 pipx, 每个应用一个独立虚拟环境; 依赖由 pip 自动安装)
pipx install dist/spring_quant-0.1.0-py3-none-any.whl

# 3. 首次使用: 初始化运行目录(建目录, 把占位符模板 config.yaml.example 复制为 config.yaml; 已有配置不覆盖)
spring-init

# 4. 把 ~/.spring/config/config.yaml 中 local_paths 的 /absolute/path/to/... 占位符改为本机绝对路径
#    (规则见下方「配置文件路径规则」); 需要同花顺接口时在 ~/.spring/.env 写入 THS_API_KEY
spring-init-db
```

**配置文件路径规则**（源码部署与安装包部署通用）

- `config.yaml` 中 `local_paths` 下的路径（`db`、`db_test`、`tdx_vipdoc`、`tdx_gbbq`、自定义的 `data_dir`）
  **一律写绝对路径，不要用 `~` 或相对路径**。`~` 会随「执行命令的用户」变化，相对路径会随启动目录变化，
  cron、systemd、手工执行可能因此各自读写一个不同的库，且不会报错。
- 写在双引号里的 Windows 路径，反斜杠要写两个，例如 `"D:\\data\\quant.db"`；也可以写成 `"D:/data/quant.db"`。
- `price_limit.no_limit_days_files` 是随包分发的名单文件，**保持相对路径**（先找运行目录，再找程序目录），
  不受上面这条约束。
- 配置模板是 [`config/config.yaml.example`](config/config.yaml.example)：除 `local_paths` 为占位符外，
  其余各节与 `config/config.yaml` 完全一致（`tests/unit/test_config_example.py` 校验）。修改 `config.yaml`
  的通用配置时须同步修改模板；仓库里的 `config/config.yaml` 是开发机自用配置，部署时请以模板为准。

每个程序安装后都有一个对应命令，命令名为 `spring-` 加程序名（下划线换成连字符），参数与
`python -m` 写法完全相同，例如：

| 源码部署 | 安装包部署（两种写法等价） |
|----------|----------------------------|
| `python -m etl.import_daily -b 20261009` | `spring-import-daily -b 20261009` 或 `python -m etl.import_daily -b 20261009` |
| `python -m etl.adjust -b 20261009` | `spring-adjust -b 20261009` 或 `python -m etl.adjust -b 20261009` |
| `python -m tools.check_daily` | `spring-check-daily` 或 `python -m tools.check_daily` |

**GitHub 自动发布**：推送 `vX.Y.Z` 标签（须与 `pyproject.toml` 的 `version` 一致）后，
`.github/workflows/release.yml` 会在 Windows / Linux / macOS 跑测试、构建 wheel 并做安装冒烟测试，
通过后创建同名 Release 并上传安装包；目标机从 Release 页面下载 `.whl` 后按上面第 2 步安装即可。
普通推送只跑测试与构建，不发布。

完整命令列表见 `pyproject.toml` 的 `[project.scripts]`。安装包部署时 `python -m` 写法需使用安装该包的
解释器（pipx 安装时在 `pipx` 的 venvs 目录下），外部调度方（如 etl-quant-mcp）应通过环境变量把
`SPRING_HOME` 传给子进程。

#### Linux 服务器部署示例

以「程序装在 `/opt/spring`、运行目录 `/srv/spring`、数据库放 `/srv/data`」为例。要求 Python 3.11+
（`python3 --version` 确认；Debian/Ubuntu 建 venv 报 `ensurepip is not available` 时需先装系统包 `python3-venv`）。

```bash
# 1. 建目录并交给运行用户(/opt、/srv 默认只有 root 可写)
sudo mkdir -p /opt/spring /srv/spring /srv/data
sudo chown your_user:your_group /opt/spring /srv/spring /srv/data

# 2. 用自建 venv 安装(目录完全自定; 程序与依赖都在 /opt/spring 下)
python3 -m venv /opt/spring
/opt/spring/bin/pip install spring_quant-<版本>-py3-none-any.whl

# 3. 指定运行目录(只对当前用户的交互 shell 生效)并初始化
echo 'export SPRING_HOME=/srv/spring' >> ~/.bashrc
echo 'export PATH=/opt/spring/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
spring-init
```

`spring-init` 生成的 `/srv/spring/config/config.yaml` 来自占位符模板，把 `local_paths` 改为绝对路径
（Linux 上只需改这一节，规则见上方「配置文件路径规则」）：

```yaml
local_paths:
  db: "/srv/data/quant.db"            # 替换占位符 /absolute/path/to/quant.db
  db_test: "/srv/data/quant_test.db"  # 替换占位符 /absolute/path/to/quant_test.db
  db_active: "prod"
  # tdx_vipdoc / tdx_gbbq 是 Windows 通达信路径, Linux 上不读取, 保留占位符即可
```

需要同花顺接口时，在 `/srv/spring/.env` 写入 `THS_API_KEY=your_api_key_here` 并 `chmod 600`
（只有 `sync_xdr_ths` 读取该文件，须由文件属主执行）。

**新库首次初始化顺序**（缺了后两步，取数程序会报「数据库中没有找到符合条件的股票」）：

```bash
spring-init-db          # 建表(幂等, 对已有库只补缺失的表)
spring-trade-cal        # 交易日历, 其他程序判断交易日都依赖它
spring-sync-basic -f    # 股票基本信息; 非交易日执行要加 -f
```

验证：`/opt/spring/bin/python -c "from util.paths import spring_home; from util import myutil; print(spring_home(), myutil.get_default_dbfile())"`
应输出 `/srv/spring /srv/data/quant.db`。注意 `import_daily -p` 虽然不写库，但同样要求库已存在且
`STOCK_INFO` 中有该股票。

**注意事项**

- **cron 与 systemd 不读 `~/.bashrc`**：定时脚本里必须显式 `export SPRING_HOME=/srv/spring`，并用绝对路径
  调用命令；systemd 服务在单元文件里用 `Environment=SPRING_HOME=/srv/spring` 设置。
- **多个用户共用运行目录 / 数据库目录时用组权限，不要用 777**（777 会让任何账号读到 `.env` 中的密钥、改写数据库）。
  DuckDB 写库时会在库文件同目录生成 `.wal` 文件，因此目录本身也要组可写；建议加默认 ACL，
  让任一方新建的文件对组自动可写：
  ```bash
  sudo chgrp your_group /srv/spring /srv/data
  sudo chmod 2770 /srv/spring /srv/data
  sudo setfacl -d -m g:your_group:rwX /srv/spring /srv/data
  ```
- **同一时间只能有一个进程写库**：手工补数不要与定时任务、etl-quant-mcp 的任务同时运行。
- **源码部署与安装包部署并存时**，两边 `config.yaml` 的 `db` 要指向同一个库文件，否则数据会分别写进两个库。

定时脚本示例（只列关键写法，取数顺序同下文「ETL」一节）：

```bash
SPRING_BIN=/opt/spring/bin
export SPRING_HOME=/srv/spring
TODAY=$(date '+%Y%m%d')

$SPRING_BIN/spring-import-daily -b $TODAY -e $TODAY
$SPRING_BIN/spring-adjust       -b $TODAY -e $TODAY
# 完整性核对通过(退出码 0)后再补齐指标
if $SPRING_BIN/spring-check-daily -b $TODAY -e $TODAY; then
    $SPRING_BIN/spring-fill-volratio -b $TODAY -e $TODAY
fi
```

#### 升级与卸载

程序目录与运行目录分开存放，**升级、卸载只动程序目录**（venv），运行目录（配置、`.env`、日志）和数据库都不受影响。
下面以自建 venv `/opt/spring` 为例；用 pipx 安装的，把 `/opt/spring/bin/pip install` 换成
`pipx install --force`，`/opt/spring/bin/pip uninstall` 换成 `pipx uninstall spring-quant`。
Windows 自建 venv 时，把 `/opt/spring/bin/` 换成 `D:\apps\spring\Scripts\`。

**查看当前版本**

```bash
/opt/spring/bin/pip show spring-quant
```

**升级**（在定时任务和 etl-quant-mcp 都没有任务运行时进行）

```bash
# 1. 备份数据库(新版本可能新增表或修改数据处理逻辑, 先留一份可回退的副本)
cp -p /srv/data/quant.db /srv/data/quant.db.bak

# 2. 安装新版本(覆盖旧版本; 依赖有变化时 pip 会一并处理)
/opt/spring/bin/pip install spring_quant-<新版本>-py3-none-any.whl

# 3. 补建新版本新增的表(幂等, 已有表和数据不受影响)
spring-init-db

# 4. 对比配置模板: 新版本若新增了配置项, 手工补进自己的 config.yaml
#    (spring-init 不会覆盖已有配置, 新配置项不会自动出现)
diff /srv/spring/config/config.yaml \
  "$(/opt/spring/bin/python -c 'from util.paths import PACKAGE_ROOT; print(PACKAGE_ROOT)')/config/config.yaml.example"

# 5. 验证
/opt/spring/bin/pip show spring-quant
spring-check-daily -b <最近交易日> -e <最近交易日>
```

第 4 步的 `diff` 中，`local_paths` 的差异是正常的（你的配置是真实路径，模板是占位符），只需关注**新增的配置项**。

**回退到旧版本**：用旧版本的 `.whl` 再执行一次第 2 步即可。若新版本已对数据库做过改动，还需要用第 1 步的备份恢复数据库。

**单独升级某个依赖包（以 akshare 为例）**

`datasource/akstock.py` 底层调用的是第三方包 akshare。它接口变动频繁，经常需要单独升级，直接在 spring 所在 venv 里
`pip install --upgrade` 即可，**不需要重新打包或重装 spring**。但要注意：

- akshare 在 `pyproject.toml` 中**未锁版本**，可以随时升级；`pandas` / `numpy` / `duckdb` 是**锁定版本**的。
  若新版 akshare 要求更高的 pandas，pip 会把它一并升级，事后只打印一条警告——`duckdb` 关系到库文件格式，**绝不能被连带升级**。
- 受 akshare 影响的程序：`sync_basic`（`-s akstock`）、`sync_industry`、`sync_margin`、`sync_limit_pool`、`sync_suspension`。
- 之后升级 spring 本身不会把 akshare 降回去（未锁版本时 pip 保留已安装的版本）。

建议先在开发机的虚拟环境里升级同一版本并跑一遍 `pytest`。单元测试全部是 mock，验证不了真实接口，
**下面第 5 步的真实取数检查才是关键**。在定时任务没有运行时操作：

```bash
# 1. 记录当前版本, 留作回退依据
/opt/spring/bin/pip show akshare | grep Version
/opt/spring/bin/pip freeze > /srv/spring/pip-freeze-$(date +%Y%m%d).txt

# 2. 预演: 只显示将要变动的包, 不实际安装
/opt/spring/bin/pip install --upgrade akshare --dry-run

# 3. 正式升级(镜像未同步到最新版时, 加 -i https://pypi.org/simple 改从官方源安装)
/opt/spring/bin/pip install --upgrade akshare

# 4. 检查依赖一致性, 预期输出 No broken requirements found.
/opt/spring/bin/pip check

# 5. 真实取数检查(只联网取数, 不写库; 日期换成最近的交易日)
/opt/spring/bin/python -c "from datasource import akstock; print(akstock.fetch_suspension('20260930').shape); print(akstock.fetch_limit_pool('20260930').shape)"
```

- 第 2 步的 `Would install ...` 里**出现 `pandas`、`numpy` 或 `duckdb` 时，停止升级**：新版 akshare 与 spring 锁定的版本冲突，
  需要先评估、调整 spring 的依赖版本并发布新版本。
- 第 4 步提示 `spring-quant requires ...`，或第 5 步报错（例如接口返回缺少列），按下面的方法回退。
- 升级后的第一个交易日，检查定时任务日志：`grep -E "ERROR|WARNING" /srv/spring/log/stockdaily<日期>.log`。

回退：

```bash
# 只回退 akshare
/opt/spring/bin/pip install akshare==<第 1 步记录的版本>

# pandas / numpy / duckdb 也被连带改动时, 按第 1 步的快照整体恢复
/opt/spring/bin/pip install -r /srv/spring/pip-freeze-<日期>.txt
```

其他依赖（如 baostock）的升级流程相同，把包名换掉即可。

**卸载**

```bash
# 1. 卸载程序(只删除 venv 里的 spring 代码与命令, 第三方依赖仍留在 venv 中)
/opt/spring/bin/pip uninstall spring-quant
```

如需彻底清理，以下步骤会**永久删除**文件，执行前请确认路径和备份：

- 整个 venv 目录 `/opt/spring`（程序与全部依赖；删除后无法再运行任何 `spring-*` 命令）
- 运行目录 `/srv/spring`（含配置、`.env` 中的密钥、日志）——不打算重装时再删
- 数据库 `/srv/data/quant.db`——**删除前务必确认已有可用备份**，库中历史数据无法从安装包恢复
- 同时删除 `~/.bashrc` 中 `SPRING_HOME` / `PATH` 两行、crontab 中的定时任务，以及 systemd 服务里的
  `SPRING_HOME` 等设置，否则定时任务会持续报「命令不存在」

### **数据库初始化**
   在 `config/config.yaml` 中配置 `local_paths.db` 指向你的数据库文件，**写绝对路径**（见上文「配置文件路径规则」；
   新机器可先 `cp config/config.yaml.example config/config.yaml` 再替换占位符），然后执行初始化脚本建表：
```yaml
   # config/config.yaml
   local_paths:
     db: "/srv/data/quant.db"        # Windows 例: "D:/data/quant.db"
```
```bash
   python -m etl.init_db
```
新增 `SUSPENSION_DAILY`（停牌名单）/ `LIMIT_POOL_DAILY`（涨跌停股池）两张表，
供 `check_daily` 与它们做交叉核对；老库升级到本版本只需重新执行一遍上面的
`python -m etl.init_db`（`CREATE TABLE IF NOT EXISTS`，幂等，不影响已有表）。

**校验数据是否完整**
```bash
python -m tools.check_daily
```
用 `-t` 只核对指定类别，可多选（`daily` 日线 / `adj` 复权因子 / `index` 指数 /
`basic` 基础数据 / `limit` 涨跌停 / `volratio` 量比 / `capital` 股本资料 / `industry` 行业 / `margin` 融资融券）；
不传则核对除 `index` 外的全部类别：
```bash
python -m tools.check_daily -t daily adj
```

**跨机器搬运某个程序的产出数据**

某台机器的 ETL 执行失败、而另一台执行成功时，可按程序把成功机器的数据导出再导入。
涉及的表: `import_daily` -> STOCK_DAILY(个股) + DAILY_BASIC(四列)；`fetch_index` -> STOCK_DAILY(指数)；
`adjust` -> ADJ_FACTOR + ADJ_FACTOR_RAW + ADJ_FACTOR_LOCAL + ADJ_FACTOR_LOCAL_STATE。导入为幂等 upsert，只覆盖各程序负责的列。
```bash
# 成功的机器上导出 (可指定一个或多个程序)
python -m tools.export_etl_tables -p import_daily adjust -b 20260817 -e 20260818 -o D:/sync/out

# 失败的机器上先干跑, 再导入
python -m tools.import_etl_tables -p import_daily adjust -i D:/sync/out --dry-run
python -m tools.import_etl_tables -p import_daily adjust -i D:/sync/out
```

**启动 MCP 服务**
MCP 只读服务已拆分为独立项目 [quant-mcp](https://github.com/rshun/quant-mcp)，用于对接 Claude 或其他支持 MCP 协议的客户端。安装与客户端配置详见该项目的 README。

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

# gbbq 是全历史快照，不受 cw 最近 12 季度刷新窗口限制。
# 入库前会对已核实的源数据异常做定点处理(均按整条记录签名匹配，
# 上游若修复或改值则规则自动失效，见 datasource/tdx_offline.py)：
#   1) 改字段值: 000863/20000919、600602/20000623、600657/20011022
#      通达信把送转股误记为零价配股
#   2) 整行剔除: 301097/20260602 幻象除权事件——交易所当日未除权，
#      独立源(同花顺)亦无记录，真实事件在 20260608；保留会对同一次
#      分配除权两次(该股复权价曾因此错约 41%)
# 注意: CAPITAL_DETAIL 写库是 INSERT OR REPLACE，上述「整行剔除」只能
# 防止重新写入，删不掉已入库的旧行；老库需另行定点 DELETE。
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
# 每天运行(获取当天; 默认 -s local: 库内自算并维护 ADJ_FACTOR 稠密表)
# 运行前预检 ADJ_FACTOR 缺口: 漏跑 / 上市日起未稠密化 / 区间内部空洞 → 退出码 1 并给出 -b 回填命令
python -m etl.adjust

# 漏跑后按日志提示回填(示例: 从首个缺失交易日起; 可加 -c 只补个别股票)
python -m etl.adjust -b 20260908

# 【已废弃】baostock 复权因子源: 只留痕写 ADJ_FACTOR_RAW, 不维护 ADJ_FACTOR 稠密表
# 不限定范围 → 按交易日循环调 query_daily_adjust_factor; 加 -c 或 -x sh → 逐股 query_adjust_factor
python -m etl.adjust -s bstock -b 20260907 -e 20260911
```

稠密化没有开关（2026-09-12 移除 `--densify`），完全由 `-s` 决定：
`local` 写事件表 `ADJ_FACTOR_LOCAL` 并稠密化 `ADJ_FACTOR`；
`bstock` 只写留痕事件表 `ADJ_FACTOR_RAW`，不触碰 `ADJ_FACTOR`。

#### 补齐指标量比,涨停,流通市值(流通市值等数据前置条件是需要股本资料)  
```bash
# 补齐深沪市量比数据(补齐T-1日,每天运行)
python -m etl.fill_volratio

# 补齐深沪市涨停数据(补齐T-1日,每天运行)
python -m etl.update_limit 

# 根据CAPITAL_DETAIL回填DAILY_BASIC的总股本和流通股本(默认补齐T-1日，每天运行)  
# 由于新股上市会有数据上窗口空缺, 所以回填数据以15天为限来保证股本数据有值  
python  -m etl.fill_shares -b 20260501 -e 20260515  

# 回填换手率(成交量/流通股本; 默认只补 baostock turn 为空的行, -o 覆盖重算; 须在 fill_shares 之后)
python -m etl.fill_turnover
```

**涨跌停的特殊交易日：`data/` 下的不设涨跌幅名单**

`update_limit` 按板块、ST、上市天数计算涨跌停价，但历史上有些交易日按规定**不设涨跌幅**，
无法从行情数据推算出来，只能靠名单。名单内的 (code, date) 一律写入「无涨跌幅」哨兵值
`limit_up = 999999.99` / `limit_down = 0.01`，两个涨跌停标志都为 0。

名单文件由 `config/config.yaml` 的 `price_limit.no_limit_days_files` 列出，可配多个，程序合并使用：

| 文件 | 收录内容 | 来源 |
|------|------|------|
| `data/reform_resume_days.csv` | 2005–2008 年**股权分置改革复牌首日**（含股改与重组同时进行的复牌） | 按规则从行情数据筛出，另手工补入第一批股改试点 |
| `data/share_listing_days.csv` | **新增股份上市日**：公开增发、定向增发的新股上市首日，股改追加（追送）对价股份上市日 | 逐条查公告人工核实 |

两份文件都**只有 `code`、`date` 两列被程序读取**，其余列供人工审阅。
配置了但文件缺失、缺 `code`/`date` 列，或同一 (code, date) 在名单中出现两次时，`update_limit` 直接报错，
防止名单静默失效（退回按 10% 计算）或两处各改各的。

**`data/reform_resume_days.csv`（股改复牌首日）**

| 列 | 含义 |
|------|------|
| `code` | 股票代码，带交易所后缀，对齐 `STOCK_INFO.code`（如 `600031.SH`） |
| `date` | 复牌首日（`YYYY-MM-DD`），当天不设涨跌幅 |
| `tier` | 收录依据：<br>`price_breach` — 收盘价超出按 10%（ST 为 5%）计算的涨跌停区间，在限价下不可能成交，确定为不设限日；<br>`no_exright` — 未越界，但当日复权因子发生变化、且前收价未除权（等于停牌前收盘），符合「非流通股东送股对价、交易所不除权」的股改特征；<br>`manual` — 预留给人工查公告核实、规则没筛出来的股改复牌日（目前没有） |
| `gap_days` | 复牌前连续停牌的交易日数（股改第一批试点如三一重工只停 1 天） |
| `last_close` | 停牌前最后一个交易日的收盘价 |
| `pre_close` | 复牌首日的前收价（`STOCK_DAILY.pre_close`）；等于 `last_close` 表示交易所未做除权 |
| `close` | 复牌首日的收盘价 |

这份是一次性生成的静态文件：2005–2008 年停牌 ≥5 个交易日后的复牌首日，按上面两种依据筛选，
另手工补入第一批股改试点（三一重工、金牛能源、紫江企业，只停牌 1 天）。

**`data/share_listing_days.csv`（增发 / 追加对价股份上市日）**

| 列 | 含义 |
|------|------|
| `code` | 股票代码，带交易所后缀，对齐 `STOCK_INFO.code` |
| `date` | 新增股份上市日（`YYYY-MM-DD`），当天整只股票不设涨跌幅 |
| `event` | 事件类型：`public_offering` 公开增发新股上市首日；`private_placement` 定向增发新增股份上市；`extra_consideration` 股改追加（追送）对价股份上市 |
| `note` | 说明，如当时的简称、对价数量等 |
| `source` | 出处（公告标题或链接），便于复查；可为空 |

**适用时期**：增发股份上市首日不设涨跌幅的规定，**在 2011 年新版交易规则实施后取消**，
此后增发股上市首日恢复涨跌幅限制。因此 `public_offering` / `private_placement` 只收 2011 年新规实施之前的日子，
之后的增发上市日照常限价，**不要**加进本清单（契约测试会拦截 2012 年及以后的增发类记录）。

这类日子当天股价往往没越界，核对报不出来；gbbq 只记「股本变化」，又和普通的限售股解禁
（照常限价）分不开，所以**只按人工核实逐条加入**，不用规则批量推断。
`check_daily -t limit` 报出「收盘价超出涨跌停区间」、查公告确认是这类事件时，在这里补一行。

核实依据（任一即可）：上市公告书写明「上市首日本公司股票不设涨跌幅限制」，或当天龙虎榜的上榜原因为
「无价格涨跌幅限制的证券」。**不要只凭价格判断**：不设限的日子里，最高价也可能恰好停在 10% 位置
（如 000159 国际实业 2008-03-11 最高价正好等于按 10% 算的涨停价，但公告与龙虎榜都确认当天不设限）。

**维护**：两份文件直接增删行即可；改动后对受影响日期重跑涨跌停，例如：

```bash
python -m etl.update_limit -b 20050101 -e 20081231
```

上市首日不设涨跌幅的「合并上市」（换股吸收合并、B 转 A）另有名单，在 `config/config.yaml`
的 `price_limit.no_limit_first_day_codes`；2014-01-01 之前的新股上市首日由程序按规则统一处理，无需列入名单。

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

#### 同步专业财务报表数据 (通达信 cw 文件, 按报告期)
```bash
# 同步周期: 财报为季度数据, 无需每天跑。建议每周 1 次(不带 --download);
#   披露季(4月一季报 / 8月半年报 / 10月三季报 / 次年3-4月年报)可加到每周 2 次。
#   入库为 UPSERT(按 code+report_date), 重复跑安全幂等。
#   注: sync_capital 每天已调用 sync_cw_files 更新本地 cw 文件,
#       故 sync_finance 日常可不带 --download, 直接重读本地即可。

# 导入本地全部报告期 (读 download/cw_pkl, 缺失回退 download/cw)
python -m etl.sync_finance

# 导入前先运行 sync_cw_files 更新本地 cw 文件
python -m etl.sync_finance --download

# 仅导入指定报告期区间 (YYYYMMDD 或 YYYY-MM-DD)
python -m etl.sync_finance --start 20200101 --end 20241231

# 仅导入指定股票 (逗号或空格分隔的裸代码)
python -m etl.sync_finance --codes 000001,600519
```

#### 同步停牌名单 / 涨跌停股池 (akstock 数据源, 第三方独立事实源, 供 check_daily 交叉核对)
```bash
# 每天运行(获取当天全量停牌名单)
python -m etl.sync_suspension

# 每天运行(获取当天全量涨跌停股池, 涨停+跌停)
python -m etl.sync_limit_pool

# 仅同步涨停池 (--only up|down|all, 默认 all)
python -m etl.sync_limit_pool --only up
```
`-c` / `-x` 仅供人工排查个别股票，**日常入库必须全量运行**（不带 `-c`，
`-x` 用默认的 `all`）：这两个 ETL 写库时按日期(及方向)整体删除当日旧数据、
再插入本次范围取到的行；缩小范围会让未覆盖到的股票从当日快照里消失，
被 `check_daily` 的停牌/涨跌停核对项误判成「库内多标」，产生整片误报。

## 🤖 MCP 工具能力 (Tools)

通过配套独立项目 [quant-mcp](https://github.com/rshun/quant-mcp)（只读 DuckDB 数据），项目向 AI 模型提供了丰富的量化工具，大模型可以直接调用以下功能：

- `search_stock` / `get_stock_info`: 股票检索及基本面信息获取
- `get_stock_daily` / `get_daily_basic`: 获取指定股票的历史 K 线及每日核心指标（换手率、PE、PB、量比等）
- `calc_indicators`: 动态计算技术指标（如各种周期的均线 MA、成交量均线 VOL_MA、收益率等）
- `get_adj_factor` / `get_capital_detail`: 获取复权因子与除权除息/送配股明细
- `get_margin_summary` / `get_margin_detail`: 获取交易所级融资融券每日汇总及个股明细
- `get_stock_industry` / `get_stock_industry_history`: 查询股票的申万行业（一/二/三级）归属及历史变动
- `query`: 提供安全的只读 SQL 查询接口，方便 AI 进行复杂的交叉分析

### 写入侧：ETL 调度

另有独立项目 [etl-quant-mcp](https://github.com/rshun/etl-quant-mcp)（服务名 `quant-etl`），
把本项目的 ETL 程序以子进程方式暴露给模型，解决的是**夜跑卡死**这个具体问题——
故障形态不是崩溃而是停在下载处，既不报错也不退出，退出码对此完全失效。

它提供 13 个 Tool，分四类：

- **执行**：`etl_import_daily` / `etl_adjust` / `etl_fetch_index` / `etl_fill_indicators`
- **任务管理**：`list_jobs` / `get_job` / `get_job_output` / `cancel_job`——靠心跳判定 `stalled`，可中途取消
- **日志**：`list_etl_logs` / `read_etl_log` / `summarize_etl_log`——复盘 cron 昨晚那一次
- **校验**：`check_data_gaps`（转发 `tools.check_daily --json`）/ `describe_etl_program`

典型闭环：`summarize_etl_log` → `check_data_gaps` → 按缺口定向补 → 再 check。

本项目为此提供三个稳定出口，改动 ETL 时需一并维护：

| 出口 | 用途 | 由谁保证 |
|------|------|---------|
| `python -m etl.<prog>` + 退出码 | 执行 | `tests/unit/test_etl_exit_codes.py` |
| `python -m tools.describe_cli <prog>` | 自省参数 | `tests/unit/test_cli_contract.py` |
| `log/stockdailyYYYYMMDD.log` | 观测 | `util/myutil.py` 单点定义 |

退出码约定：`0` 成功 / `1` 失败 / `2` argparse 用法错误（标准库写死）/ `3` 部分成功（暂未产出）。
`config/pipeline.yaml` 声明程序间的依赖顺序，改 ETL 的人顺手维护。

完整设计说明见该仓库的 `docs/mcp_etl_plan.md`。

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
- `FINANCE_REPORT`: 专业财务报表（通达信 cw 文件，按报告期；三大报表合存宽表）
- `V_BALANCE_SHEET` / `V_INCOME_STATEMENT` / `V_CASH_FLOW`: 资产负债表 / 利润表 / 现金流量表视图（基于 `FINANCE_REPORT`）

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
   - **000850.SZ这只股票在2001-03-26~04-10股本资料有问题，总股本返回是1850(1850万股)，但实际应该是18500(1.85亿股)**——导致 DAILY_BASIC 该 12 行 `float_shares > total_shares`、`float_mv > total_mv`。（日期原记为 2021-03-26，2026-09-14 核对数据后更正为 2001）

- lday  
   - **没有IS_ST这个值，在计算涨跌停会有问题**
   - **务必要保证通达信的APP中，日线数据已经成功下载，否则表中的数据会出现异常**
   - **若当日除权, 会使用未复权的前收盘价, 这样会导致涨跌停价格有问题**
   - **pre_close 是用 LAG(close) 现算的，不是交易所的除权参考价**（bstock 才带真实
     preclose）。由 lday 写入的行，复权因子的「除权参考价校正」永远不会触发，
     `tools/checks/adjust_invariant.py` 的逐日恒等式核对对其判别能力也是不对称的：
     比例错与漏事件完全不可见，真实除权事件反而被误报成「多事件」。
   - **以下两只退市股的日线由 lday 补入（bstock 不覆盖），整段 pre_close 均为合成值**：

     | 代码 | 名称 | 受影响区间 | 说明 |
     |---|---|---|---|
     | 600387.SH | 退市海越 | 2007-01-11 ~ 2025-07-11 | 2025-07-11 退市，最后成交日 2025-07-03 |
     | 000594.SZ | 国恒退 | 2007-01-11 ~ 2015-07-13 | 2015-07-13 退市，最后成交日 2015-07-03 |

     这两只的复权因子只能按 gbbq 名义比例计算且无从校验；实测 600387.SH 因此在
     恒等式核对里产生 8 条「多事件」误报，不应视为缺陷。
     判定某只股票是否属于此类：该股 `pre_close` 与前一交易日收盘不相等的行数为 0。

- akstock
   - **京市返回的成交量和金额不精确，是返回的\*100**
   - **除了获取基本信息这个接口之外，其它接口都会存在网络不稳定**
   - **获取的京市基本数据暂无退市的股票**

- bstock
   - **有时候日线有数据，且下载未报错, 但pb,pe不一定有值, 如果后续有用到需要先校验再使用**
   - **成交额(amount)有单点错值**：已核实 58 处，均表现为 `amount/volume`
     算出的均价越出当日 `[low, high]`——按定义不可能。集中在创业板注册制新股
     上市首日（2020-07-21 / 2020-11-09 / 2020-12-09 / 2022-07-20 四天占绝大多数）。
     实例 `300999.SZ` 金龙鱼 2020-11-09：baostock 记 62.57 亿，而该值与前一交易日
     11-06 的成交额**完全相同**（源侧串行），算出均价 56.57 元低于当日最低价 58.58；
     通达信及另两个独立数据源均为 **67.65 亿**（均价 61.16，落在区间内）。
     已由 `util/dbutil.py` 的 `_KNOWN_DAILY_AMOUNT_ANOMALIES` 定点修正 + 一次性 UPDATE。
   - **另有 200 处同类成交额错值无法修复**：集中在 11 只股票的 2000–2001 年
     （`600837`/`600633`/`600833`/`600083` 四只占 184 处），均价高出收盘价约 5%。
     通达信 `.day` 对该区间无覆盖，取不到正确值。这四只同时也是复权因子核对里
     缺陷最集中的股票，其 2000–2001 年数据在多个维度上都不可靠。
   - **此数据源以下日期和股票缺失**
      - 2012-09-10,001872.SZ,招商港口
      - 2012-09-10,001914.SZ,招商积余
      - 2012-09-10,302132.SZ,中航成飞

## ⚠ 注意  
   - **若当日停牌,tdx,lday,bstock均会插入数据到STOCK_DAILY和DAILY_BASIC表**
   - **退市股（STOCK_INFO.list_status='D'）取数范围因源而异**：lday 会获取
     （2026-09-13 起，本地 .day 文件里有历史的就补得进来）；bstock / tdx / akstock
     仍不获取——这三个源走网络，退市股本就取不到。日常跑批不受影响：候选集按
     delist_date 裁剪窗口，已退市个股在 `-b/-e` 取当天时自然落选。
   - **若使用不同数据源下载数据，原STOCK_DAILY和DAILY_BASIC表的数据会被清空，新数据会填入进去**
   - **import_daily和fetch_index都存在上述3个共性问题**
   - **建议优先使用bstock这个数据源, lday作为补充**
   - **换手率的值可能会有差异，比如002728.SZ在2026-05-29，东方财富网和bstock是3.72%，通达信和雪球是2.41%**

## 📋️ TODO  
- 根据除权除息资料校验pre_close是否准确
- 补齐pb,pe
- **[新] 行情自洽核对：`close[t]` 与 `pre_close[t+1]` 对不上**

  2026-09-13 在复权因子恒等式核对（`tools/checks/adjust_invariant.py`）中作为副产品
  发现。全库（2011 年起、排除除权日）共 23 处；用同花顺收盘价逐条仲裁后，**成因有
  三种，不是单一问题**：

  | 成因 | 处置 |
  |---|---|
  | `tradestatus` 把交易日误标成停牌 | **已修复**：`_normalize_daily_df` 新增规则（`volume>0` 且 `amount>0` 则纠正为 1），并回补了库内 4 行 |
  | `close` 源数据错值 | **未修复**，见下 |
  | 长期停牌 / 缩股 / 重组复牌 | 属复权算法覆盖缺口，非行情问题 |

  **`close` 源数据错值 —— 已用通达信本地 `.day` 对全历史逐条仲裁**

  通达信 vipdoc 的 `.day` 文件是完全独立的第三方，多数股票覆盖到 2007-01-11
  （少数更晚），据此把全历史 604 处候选逐条定性：

  | 判定 | 行数 | 处置 |
  |---|---|---|
  | 通达信支持 `pre_close` → `close` 错 | 27 | 其中 19 行已修，见下 |
  | 通达信支持 `close` → 疑漏除权事件 | 38 | 属复权算法覆盖缺口 |
  | 通达信无该日记录（多为 2007 前） | 539 | **无法仲裁** |

  **已修 19 行**（`util/dbutil.py` 的 `_KNOWN_DAILY_CLOSE_ANOMALIES` + 一次性
  UPDATE）：2016-12-01 四只沪市（三方独立源一致），加通达信仲裁出的 15 行
  （2003-11-17 两只、2008-10-24 七只、2013-05~08 五只、2017-10-24 一只）。
  只收录**单字段错**——通达信的 `open`/`high`/`low` 与库内完全一致、仅 `close`
  不同、且修正值落在库内 `[low, high]` 内。

  **未修 8 行**，因为它们不是单字段错：

  - **整行分歧 6 行**（`600015`/`600320`/`600600`/`600537`/`600303` 的
    2003-11-17、`002016` 的 2008-10-24）：通达信的 `low` 也与库内不同，只改
    `close` 会造出 `close < low` 的非法行，须整行替换才对。
  - **价格整段滞后 2 行起**（`600845.SH` 2001-05~06）：库内某日 OHLC 等于通达信
    **前一条记录**的值，而**成交量两边完全一致**（如 06-01 都是 402,700）。
    是连续多行错位，须重排该段而非改单点。

  **待办：把这个判据做成独立的行情核对项。** 判据 `close[t] == pre_close[t+1]`
  （非除权日），命中后用同花顺 dump 仲裁。注意只有 `bstock` 带真实 `preclose`，
  `lday`/`tdx` 是 `LAG(close)` 现算的，对这类股票判据恒成立、查不出问题
  （见「已知问题 > lday」）。


### 本地复权源：固定基准与完整快照（2026-09-09）

`local` 使用 `ADJ_FACTOR_LOCAL_STATE.base_factor` 保存一次初始化的固定基准，
不会每天按稠密表末行重估。新库无历史时基准为 1；有历史时只接受首个可计算事件之前的
存量水位。若旧库只有事件后的数据且没有状态行，程序会拒绝自动初始化，需要先审核迁移水位，
不能用最后一行直接反推后强行续跑。

升级前应先停止该库写入、备份完整 DuckDB 文件并验证备份可读，再在测试副本执行现有
`python -m etl.init_db` 建立新增的 `ADJ_FACTOR_LOCAL_STATE` 表。
确认表存在、迁移基准正确及回归通过后，再安排业务库升级；本次代码修复没有执行业务库升级。
可用只读 SQL 检查状态表及初始化覆盖：

```sql
SELECT table_name FROM information_schema.tables
WHERE table_name = 'ADJ_FACTOR_LOCAL_STATE';
SELECT code, base_factor, updated_at FROM ADJ_FACTOR_LOCAL_STATE ORDER BY code;
SELECT DISTINCT a.code FROM ADJ_FACTOR a
LEFT JOIN ADJ_FACTOR_LOCAL_STATE s ON s.code = a.code
WHERE s.code IS NULL;
```

完整源快照会在单事务内同步事件撤销、基准状态和稠密因子。有缺价或非法配股等不完整数据时，
整批拒绝写入，避免把取数缺口当作撤销。事件链发生变化时重算该股已有稠密历史；未变化时仅补
请求窗口，避免每晚重写全市场历史。完整撤销保留状态行，事件前因子使用固定基准。

`-p adjust` 同步现在包含完整 LOCAL 事件、完整状态表，以及已初始化股票的完整稠密历史；
这部分不受日期窗口裁剪，因此导出量可能增加。RAW 和未初始化股票仍按原日期条件导出。
必须整体搬运同一次导出的 parquet 与 `local_snapshot_manifest.json`，不要混用不同批次文件。
导入时校验 SHA-256 清单；完整快照可以传播撤销，事务失败整体回滚。升级后的源端和目标端都需要新状态表。
运行前可先使用现有 `tools.import_etl_tables --dry-run` 检查文件；该选项不写库。

本地各阶段默认最多 300 秒，沿用 `baostock.progress_heartbeat_seconds` 输出存活日志。
可选配置 `local_xdr.stage_timeout_seconds` 调整阶段期限（有限正数，单位秒）；本次未改配置文件。
阶段超时会输出错误并停止心跳，阶段返回后抛出 TimeoutError，禁止写入结果。
超时机制不能强制终止底层阻塞调用；外部调度应在心跳停止后按 stalled 规则取消任务。
不要把存活日志解释成已完成进度；全市场较慢时应先在测试副本测时，再设置合理期限。

代码回滚可从 `tmp/bugfix-round3-backup/` 恢复本轮文件，需先确认没有后续编辑。
业务库一旦执行了重算或快照撤销，代码回滚不会恢复数据，应恢复升级前经验证的数据库备份。
