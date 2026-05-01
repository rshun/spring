-- 股票基本信息
CREATE TABLE IF NOT EXISTS STOCK_INFO (
  code            VARCHAR(20) PRIMARY KEY,
  symbol          VARCHAR(20),
  name            VARCHAR(50),
  exchange        VARCHAR(4),
  board           VARCHAR(30) CHECK (board IN ('MAIN','STAR','GEM','BJ','ETF','BOND','INDEX')),
  list_date       DATE,
  delist_date     DATE,
  list_status     VARCHAR(1) CHECK (list_status IN ('L','D')) DEFAULT 'L',
  created_at       TIMESTAMP DEFAULT now(),
  last_updated_at  TIMESTAMP
);
COMMENT ON COLUMN STOCK_INFO.code IS '股票代码';
COMMENT ON COLUMN STOCK_INFO.symbol IS '股票代码无点';
COMMENT ON COLUMN STOCK_INFO.name IS '股票简称';
COMMENT ON COLUMN STOCK_INFO.exchange IS '交易所(SH, SZ, BJ)';
COMMENT ON COLUMN STOCK_INFO.board IS '板块 (主板, 创业板, 科创板, 北交所, ETF, 债券, 指数)';
COMMENT ON COLUMN STOCK_INFO.list_date IS '上市日期';
COMMENT ON COLUMN STOCK_INFO.delist_date IS '退市日期';
COMMENT ON COLUMN STOCK_INFO.list_status IS '上市状态 (L=上市, D=退市)';
COMMENT ON COLUMN STOCK_INFO.created_at IS '记录创建时间';
COMMENT ON COLUMN STOCK_INFO.last_updated_at IS '记录最后更新时间';

-- 日线数据（原始）
CREATE TABLE IF NOT EXISTS STOCK_DAILY (
  code        VARCHAR(20),
  date        DATE,
  open        DOUBLE,
  high        DOUBLE,
  low         DOUBLE,
  close       DOUBLE,
  pre_close   DOUBLE,
  tradestatus INTEGER  CHECK (tradestatus IN (-1,0, 1)) DEFAULT 1,
  volume      BIGINT,
  amount      DOUBLE,
  PRIMARY KEY (code, date)
);
COMMENT ON COLUMN STOCK_DAILY.code IS '股票代码';
COMMENT ON COLUMN STOCK_DAILY.date IS '交易日期';
COMMENT ON COLUMN STOCK_DAILY.open IS '开盘价';
COMMENT ON COLUMN STOCK_DAILY.high IS '最高价';
COMMENT ON COLUMN STOCK_DAILY.low IS '最低价';
COMMENT ON COLUMN STOCK_DAILY.close IS '收盘价';
COMMENT ON COLUMN STOCK_DAILY.pre_close IS '前收盘价';
COMMENT ON COLUMN STOCK_DAILY.tradestatus IS '交易状态(1: 正常交易 0: 停牌,-1--暂时没有值)';
COMMENT ON COLUMN STOCK_DAILY.volume IS '成交量 (单位：股)';
COMMENT ON COLUMN STOCK_DAILY.amount IS '成交额 (单位：元)';

-- 交易日历（全日历+是否开市）
CREATE TABLE IF NOT EXISTS TRADE_CAL (
  cal_date  DATE PRIMARY KEY,
  is_open   INTEGER CHECK (is_open IN (0, 1))
);
COMMENT ON COLUMN TRADE_CAL.cal_date IS '日历日期';
COMMENT ON COLUMN TRADE_CAL.is_open IS '是否交易日 (1:是, 0:否)';

-- 复权因子（逐日）
CREATE TABLE IF NOT EXISTS ADJ_FACTOR (
  code           VARCHAR(20),
  trade_date     DATE,
  fore_factor    DOUBLE,
  back_factor    DOUBLE,
  adjust_factor  DOUBLE,
  created_at     TIMESTAMP DEFAULT now(),
  updated_at     TIMESTAMP,
  PRIMARY KEY (code, trade_date)
);
COMMENT ON COLUMN ADJ_FACTOR.code IS '股票代码';
COMMENT ON COLUMN ADJ_FACTOR.trade_date IS '交易日期';
COMMENT ON COLUMN ADJ_FACTOR.fore_factor IS '前复权因子';
COMMENT ON COLUMN ADJ_FACTOR.back_factor IS '后复权因子';
COMMENT ON COLUMN ADJ_FACTOR.adjust_factor IS '本次复权因子';
COMMENT ON COLUMN ADJ_FACTOR.created_at IS '记录创建时间';
COMMENT ON COLUMN ADJ_FACTOR.updated_at IS '记录最后更新时间';

-- 复权因子原始
CREATE TABLE IF NOT EXISTS ADJ_FACTOR_RAW (
    code           VARCHAR(32) NOT NULL,
    trade_date     DATE NOT NULL,
    fore_factor    DOUBLE,
    back_factor    DOUBLE,
    adjust_factor  DOUBLE,
    created_at     TIMESTAMP DEFAULT now(),
    updated_at     TIMESTAMP,
    PRIMARY KEY (code, trade_date)
);
COMMENT ON COLUMN ADJ_FACTOR_RAW.code IS '股票代码';
COMMENT ON COLUMN ADJ_FACTOR_RAW.trade_date IS '交易日期';
COMMENT ON COLUMN ADJ_FACTOR_RAW.fore_factor IS '前复权因子';
COMMENT ON COLUMN ADJ_FACTOR_RAW.back_factor IS '后复权因子';
COMMENT ON COLUMN ADJ_FACTOR_RAW.adjust_factor IS '本次复权因子';
COMMENT ON COLUMN ADJ_FACTOR_RAW.created_at IS '记录创建时间';
COMMENT ON COLUMN ADJ_FACTOR_RAW.updated_at IS '记录最后更新时间';

-- 每日指标表 (每日收盘后更新)
CREATE TABLE IF NOT EXISTS DAILY_BASIC (
    code          VARCHAR(20),
    trade_date    DATE,
    turnover_rate DOUBLE,
    float_mv      DOUBLE,
    total_mv      DOUBLE,
    pe            DOUBLE,
    pb            DOUBLE,
    is_st         INTEGER CHECK (is_st IN (0, 1)),
    limit_up      DOUBLE,
    limit_down    DOUBLE,
    is_limit_up   INTEGER DEFAULT 0 CHECK (is_limit_up IN (0, 1)),
    is_limit_down INTEGER DEFAULT 0 CHECK (is_limit_down IN (0, 1)),
    volume_ratio  DOUBLE,
    total_shares   BIGINT,
    float_shares   BIGINT,
    PRIMARY KEY (code, trade_date)
);
COMMENT ON COLUMN DAILY_BASIC.code IS '股票代码';
COMMENT ON COLUMN DAILY_BASIC.trade_date IS '交易日';
COMMENT ON COLUMN DAILY_BASIC.turnover_rate IS '换手率';
COMMENT ON COLUMN DAILY_BASIC.float_mv IS '流通市值 (单位: 元)';
COMMENT ON COLUMN DAILY_BASIC.total_mv IS '总市值 (单位: 元) ';
COMMENT ON COLUMN DAILY_BASIC.pe IS '滚动市盈率';
COMMENT ON COLUMN DAILY_BASIC.pb IS '市净率';
COMMENT ON COLUMN DAILY_BASIC.is_st IS '是否ST股 (1:是, 0:否)';
COMMENT ON COLUMN DAILY_BASIC.limit_up IS '涨停价';
COMMENT ON COLUMN DAILY_BASIC.limit_down IS '跌停价';
COMMENT ON COLUMN DAILY_BASIC.is_limit_up IS '收盘价是否涨停 (1:是, 0:否)';
COMMENT ON COLUMN DAILY_BASIC.is_limit_down IS '收盘价是否跌停 (1:是, 0:否)';
COMMENT ON COLUMN DAILY_BASIC.volume_ratio IS '量比';
COMMENT ON COLUMN DAILY_BASIC.total_shares IS '总股本(股)';
COMMENT ON COLUMN DAILY_BASIC.float_shares IS '流通股本(股)';

-- 申万行业定义（一/二/三级，按版本区分）
CREATE TABLE IF NOT EXISTS SW_INDUSTRY (
    sw_version    VARCHAR(10),
    industry_code VARCHAR(20),
    industry_name VARCHAR(50) NOT NULL,
    sw_level      INTEGER NOT NULL CHECK (sw_level IN (1, 2, 3)),
    parent_code   VARCHAR(20),
    updated_at    TIMESTAMP DEFAULT now(),
    PRIMARY KEY (sw_version, industry_code)
);
COMMENT ON COLUMN SW_INDUSTRY.sw_version    IS '申万行业分类版本 (e.g. 2014, 2021)';
COMMENT ON COLUMN SW_INDUSTRY.industry_code IS '申万行业代码 (e.g. 370304)';
COMMENT ON COLUMN SW_INDUSTRY.industry_name IS '申万行业名称';
COMMENT ON COLUMN SW_INDUSTRY.sw_level      IS '行业级别 (1/2/3)';
COMMENT ON COLUMN SW_INDUSTRY.parent_code   IS '上级行业代码 (一级为 NULL)';
COMMENT ON COLUMN SW_INDUSTRY.updated_at    IS '最近更新时间';


-- 股票申万行业历史原始数据
CREATE TABLE IF NOT EXISTS STOCK_INDUSTRY_CLF_HIST_SW_RAW (
    symbol        VARCHAR(20),
    start_date    DATE,
    industry_code VARCHAR(20),
    update_time   TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT now(),
    PRIMARY KEY (symbol, start_date, industry_code)
);
COMMENT ON COLUMN STOCK_INDUSTRY_CLF_HIST_SW_RAW.symbol        IS '股票代码';
COMMENT ON COLUMN STOCK_INDUSTRY_CLF_HIST_SW_RAW.start_date    IS '计入日期';
COMMENT ON COLUMN STOCK_INDUSTRY_CLF_HIST_SW_RAW.industry_code IS '申万三级行业代码';
COMMENT ON COLUMN STOCK_INDUSTRY_CLF_HIST_SW_RAW.update_time   IS '接口返回的更新日期';
COMMENT ON COLUMN STOCK_INDUSTRY_CLF_HIST_SW_RAW.updated_at    IS '记录写入时间';

-- 股票申万行业历史视图（按 start_date 判断申万版本，并展开一/二/三级行业）
CREATE OR REPLACE VIEW STOCK_SW_INDUSTRY_VIEW AS
WITH raw_with_version AS (
    SELECT
        symbol,
        start_date,
        industry_code,
        update_time,
        updated_at,
        CASE
            WHEN start_date < DATE '2021-07-30' THEN '2014'
            ELSE '2021'
        END AS sw_version
    FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW
)
SELECT
    r.symbol,
    r.start_date,
    r.sw_version,
    l1.industry_code AS sw_l1_code,
    l1.industry_name AS sw_l1_name,
    l2.industry_code AS sw_l2_code,
    l2.industry_name AS sw_l2_name,
    l3.industry_code AS sw_l3_code,
    l3.industry_name AS sw_l3_name,
    r.industry_code,
    r.update_time,
    r.updated_at
FROM raw_with_version r
LEFT JOIN SW_INDUSTRY l3
    ON r.sw_version = l3.sw_version
   AND r.industry_code = l3.industry_code
   AND l3.sw_level = 3
LEFT JOIN SW_INDUSTRY l2
    ON r.sw_version = l2.sw_version
   AND l3.parent_code = l2.industry_code
   AND l2.sw_level = 2
LEFT JOIN SW_INDUSTRY l1
    ON r.sw_version = l1.sw_version
   AND l2.parent_code = l1.industry_code
   AND l1.sw_level = 1;

-- 股本变动/权息资料
CREATE TABLE IF NOT EXISTS CAPITAL_DETAIL (
    code             VARCHAR(20),
    date             DATE,
    category         VARCHAR(20),
    dividend         DOUBLE,
    allotment_price  DOUBLE,
    bonus_share      DOUBLE,
    allotment_share  DOUBLE,
    updated_at        TIMESTAMP DEFAULT now(),
    PRIMARY KEY (code, date, category)
);
COMMENT ON COLUMN CAPITAL_DETAIL.code            IS '股票代码';
COMMENT ON COLUMN CAPITAL_DETAIL.date            IS '权息日';
COMMENT ON COLUMN CAPITAL_DETAIL.category        IS '类别 (除权除息/股本变化/送配股上市)';
COMMENT ON COLUMN CAPITAL_DETAIL.dividend        IS '分红(每10股)/前流通盘(万股)';
COMMENT ON COLUMN CAPITAL_DETAIL.allotment_price IS '配股价(元)/前总股本(万股)';
COMMENT ON COLUMN CAPITAL_DETAIL.bonus_share     IS '送转股(每10股)/后流通盘(万股)';
COMMENT ON COLUMN CAPITAL_DETAIL.allotment_share IS '配股(每10股)/后总股本(万股)';
COMMENT ON COLUMN CAPITAL_DETAIL.updated_at      IS '记录写入时间';
