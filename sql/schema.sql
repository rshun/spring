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
    SELECT symbol, start_date, industry_code, update_time, updated_at,
           CASE WHEN start_date < DATE '2021-07-30' THEN '2014' ELSE '2021' END AS sw_version
    FROM STOCK_INDUSTRY_CLF_HIST_SW_RAW
)
SELECT
    si.code,
    r.symbol,
    r.start_date,
    r.start_date AS effective_date,
    r.sw_version,
    l1.industry_code AS sw_l1_code, l1.industry_name AS sw_l1_name,
    l2.industry_code AS sw_l2_code, l2.industry_name AS sw_l2_name,
    l3.industry_code AS sw_l3_code, l3.industry_name AS sw_l3_name,
    r.industry_code, r.update_time, r.updated_at
FROM raw_with_version r
JOIN STOCK_INFO si
    ON si.symbol = r.symbol
   AND si.board IN ('MAIN','STAR','GEM','BJ')
LEFT JOIN SW_INDUSTRY l3
    ON r.sw_version = l3.sw_version AND r.industry_code = l3.industry_code AND l3.sw_level = 3
LEFT JOIN SW_INDUSTRY l2
    ON r.sw_version = l2.sw_version AND l3.parent_code = l2.industry_code AND l2.sw_level = 2
LEFT JOIN SW_INDUSTRY l1
    ON r.sw_version = l1.sw_version AND l2.parent_code = l1.industry_code AND l1.sw_level = 1;

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

-- 融资融券汇总数据
CREATE TABLE IF NOT EXISTS MARGIN_SUMMARY_DAILY (
    trade_date             DATE NOT NULL,
    exchange_code          VARCHAR(4) NOT NULL CHECK (exchange_code IN ('SH', 'SZ', 'BJ')),
    margin_buy_amount      NUMERIC(20, 4),
    margin_repay_amount    NUMERIC(20, 4),
    margin_balance         NUMERIC(20, 4),
    short_sell_volume      NUMERIC(20, 4),
    short_repay_volume     NUMERIC(20, 4),
    short_balance_volume   NUMERIC(20, 4),
    short_balance_amount   NUMERIC(20, 4),
    margin_short_balance   NUMERIC(20, 4),
    created_at             TIMESTAMP DEFAULT now(),
    updated_at             TIMESTAMP,
    PRIMARY KEY (trade_date, exchange_code)
);
COMMENT ON TABLE  MARGIN_SUMMARY_DAILY                      IS '融资融券每日汇总数据';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.trade_date           IS '交易日期';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.exchange_code        IS '交易所代码，取 STOCK_INFO.exchange：SH、SZ、BJ';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.margin_buy_amount    IS '融资买入额，单位：元';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.margin_repay_amount  IS '融资偿还额，单位：元；若交易所未披露则为空';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.margin_balance       IS '融资余额，单位：元';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.short_sell_volume    IS '融券卖出量，单位：股/份/手，按交易所披露证券类型口径';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.short_repay_volume   IS '融券偿还量，单位：股/份/手；若交易所未披露则为空';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.short_balance_volume IS '融券余量，单位：股/份/手，按交易所披露证券类型口径';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.short_balance_amount IS '融券余额，单位：元';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.margin_short_balance IS '融资融券余额，单位：元';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.created_at           IS '记录创建时间';
COMMENT ON COLUMN MARGIN_SUMMARY_DAILY.updated_at           IS '记录最后更新时间';

-- 融资融券明细数据
CREATE TABLE IF NOT EXISTS MARGIN_DETAIL_DAILY (
    trade_date             DATE NOT NULL,
    exchange_code          VARCHAR(4) NOT NULL CHECK (exchange_code IN ('SH', 'SZ', 'BJ')),
    symbol                 VARCHAR(20) NOT NULL,
    code                   VARCHAR(20) NOT NULL,
    margin_buy_amount      NUMERIC(20, 4),
    margin_repay_amount    NUMERIC(20, 4),
    margin_balance         NUMERIC(20, 4),
    short_sell_volume      NUMERIC(20, 4),
    short_repay_volume     NUMERIC(20, 4),
    short_balance_volume   NUMERIC(20, 4),
    short_balance_amount   NUMERIC(20, 4),
    margin_short_balance   NUMERIC(20, 4),
    created_at             TIMESTAMP DEFAULT now(),
    updated_at             TIMESTAMP,
    PRIMARY KEY (trade_date, exchange_code, symbol),
    UNIQUE (trade_date, code)
);
COMMENT ON TABLE  MARGIN_DETAIL_DAILY                      IS '融资融券每日明细数据';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.trade_date           IS '交易日期';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.exchange_code        IS '交易所代码，取 STOCK_INFO.exchange：SH、SZ、BJ';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.symbol               IS '证券代码，不带交易所后缀';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.code                 IS '带交易所后缀的证券代码，对齐 STOCK_INFO.code，如 600000.SH、000001.SZ、430047.BJ';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.margin_buy_amount    IS '融资买入额，单位：元';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.margin_repay_amount  IS '融资偿还额，单位：元；若交易所未披露则为空';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.margin_balance       IS '融资余额，单位：元';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.short_sell_volume    IS '融券卖出量，单位：股/份/手，按交易所披露证券类型口径';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.short_repay_volume   IS '融券偿还量，单位：股/份/手；若交易所未披露则为空';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.short_balance_volume IS '融券余量，单位：股/份/手，按交易所披露证券类型口径';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.short_balance_amount IS '融券余额，单位：元';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.margin_short_balance IS '融资融券余额，单位：元';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.created_at           IS '记录创建时间';
COMMENT ON COLUMN MARGIN_DETAIL_DAILY.updated_at           IS '记录最后更新时间';

-- 专业财务报表（通达信 cw / gpcw 文件，按报告期一行；三大报表合存一张宽表）
-- 列名为简短 snake_case，COMMENT 标注「cw列号·中文名」，完整580字段见 download/cw_field_map_raw.py
CREATE TABLE IF NOT EXISTS FINANCE_REPORT (
    code          VARCHAR(20),   -- 6位裸代码，对齐 STOCK_INFO.symbol
    report_date   DATE,          -- 报告期(报表所属期末，如 2024-12-31)

    -- ① 每股指标
    eps               DOUBLE,
    eps_deduct        DOUBLE,
    undist_profit_ps  DOUBLE,
    bps               DOUBLE,
    roe               DOUBLE,
    ocf_ps            DOUBLE,

    -- ② 资产负债表
    money_funds         DOUBLE,
    accounts_recv       DOUBLE,
    inventory           DOUBLE,
    total_cur_assets    DOUBLE,
    fixed_assets        DOUBLE,
    intangible_assets   DOUBLE,
    goodwill            DOUBLE,
    total_noncur_assets DOUBLE,
    total_assets        DOUBLE,
    short_term_loan     DOUBLE,
    accounts_pay        DOUBLE,
    total_cur_liab      DOUBLE,
    long_term_loan      DOUBLE,
    bonds_payable       DOUBLE,
    total_noncur_liab   DOUBLE,
    total_liab          DOUBLE,
    share_capital       DOUBLE,
    capital_reserve     DOUBLE,
    surplus_reserve     DOUBLE,
    undist_profits      DOUBLE,
    minority_equity     DOUBLE,
    total_equity        DOUBLE,

    -- ③ 利润表
    op_revenue        DOUBLE,
    op_cost           DOUBLE,
    tax_surcharges    DOUBLE,
    sales_expense     DOUBLE,
    admin_expense     DOUBLE,
    financial_expense DOUBLE,
    asset_impairment  DOUBLE,
    investment_income DOUBLE,
    op_profit         DOUBLE,
    total_profit      DOUBLE,
    income_tax        DOUBLE,
    net_profit        DOUBLE,
    net_profit_parent DOUBLE,
    minority_pl       DOUBLE,
    rd_expense        DOUBLE,

    -- ④ 现金流量表
    ocf_in        DOUBLE,
    ocf_out       DOUBLE,
    ocf_net       DOUBLE,
    icf_net       DOUBLE,
    fcf_net       DOUBLE,
    cash_net_inc  DOUBLE,
    cash_end_bal  DOUBLE,

    -- ⑤ 关键比率
    current_ratio   DOUBLE,
    quick_ratio     DOUBLE,
    debt_ratio      DOUBLE,
    gross_margin    DOUBLE,
    net_margin      DOUBLE,
    revenue_yoy     DOUBLE,
    net_profit_yoy  DOUBLE,

    -- ⑥ 股本股东
    total_shares      DOUBLE,
    float_a_shares    DOUBLE,
    num_shareholders  DOUBLE,

    -- ⑦ 公告日期
    report_anno_date  DOUBLE,

    updated_at    TIMESTAMP DEFAULT now(),
    PRIMARY KEY (code, report_date)
);
COMMENT ON TABLE  FINANCE_REPORT IS '专业财务报表(通达信cw文件,按报告期);列名映射见 datasource/cw_fields.py';
COMMENT ON COLUMN FINANCE_REPORT.code             IS '6位裸代码(对齐 STOCK_INFO.symbol)';
COMMENT ON COLUMN FINANCE_REPORT.report_date      IS '报告期(报表所属期末)';
COMMENT ON COLUMN FINANCE_REPORT.eps              IS 'cw001 基本每股收益';
COMMENT ON COLUMN FINANCE_REPORT.eps_deduct       IS 'cw002 扣非每股收益';
COMMENT ON COLUMN FINANCE_REPORT.undist_profit_ps IS 'cw003 每股未分配利润';
COMMENT ON COLUMN FINANCE_REPORT.bps              IS 'cw004 每股净资产';
COMMENT ON COLUMN FINANCE_REPORT.roe              IS 'cw006 净资产收益率';
COMMENT ON COLUMN FINANCE_REPORT.ocf_ps           IS 'cw007 每股经营现金流';
COMMENT ON COLUMN FINANCE_REPORT.money_funds         IS 'cw008 货币资金(元)';
COMMENT ON COLUMN FINANCE_REPORT.accounts_recv       IS 'cw011 应收账款(元)';
COMMENT ON COLUMN FINANCE_REPORT.inventory           IS 'cw017 存货(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_cur_assets    IS 'cw021 流动资产合计(元)';
COMMENT ON COLUMN FINANCE_REPORT.fixed_assets        IS 'cw027 固定资产(元)';
COMMENT ON COLUMN FINANCE_REPORT.intangible_assets   IS 'cw033 无形资产(元)';
COMMENT ON COLUMN FINANCE_REPORT.goodwill            IS 'cw035 商誉(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_noncur_assets IS 'cw039 非流动资产合计(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_assets        IS 'cw040 资产总计(元)';
COMMENT ON COLUMN FINANCE_REPORT.short_term_loan     IS 'cw041 短期借款(元)';
COMMENT ON COLUMN FINANCE_REPORT.accounts_pay        IS 'cw044 应付账款(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_cur_liab      IS 'cw054 流动负债合计(元)';
COMMENT ON COLUMN FINANCE_REPORT.long_term_loan      IS 'cw055 长期借款(元)';
COMMENT ON COLUMN FINANCE_REPORT.bonds_payable       IS 'cw056 应付债券(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_noncur_liab   IS 'cw062 非流动负债合计(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_liab          IS 'cw063 负债合计(元)';
COMMENT ON COLUMN FINANCE_REPORT.share_capital       IS 'cw064 实收资本/股本(元)';
COMMENT ON COLUMN FINANCE_REPORT.capital_reserve     IS 'cw065 资本公积(元)';
COMMENT ON COLUMN FINANCE_REPORT.surplus_reserve     IS 'cw066 盈余公积(元)';
COMMENT ON COLUMN FINANCE_REPORT.undist_profits      IS 'cw068 未分配利润(元)';
COMMENT ON COLUMN FINANCE_REPORT.minority_equity     IS 'cw069 少数股东权益(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_equity        IS 'cw072 所有者权益合计(元)';
COMMENT ON COLUMN FINANCE_REPORT.op_revenue        IS 'cw074 营业收入(元)';
COMMENT ON COLUMN FINANCE_REPORT.op_cost           IS 'cw075 营业成本(元)';
COMMENT ON COLUMN FINANCE_REPORT.tax_surcharges    IS 'cw076 营业税金及附加(元)';
COMMENT ON COLUMN FINANCE_REPORT.sales_expense     IS 'cw077 销售费用(元)';
COMMENT ON COLUMN FINANCE_REPORT.admin_expense     IS 'cw078 管理费用(元)';
COMMENT ON COLUMN FINANCE_REPORT.financial_expense IS 'cw080 财务费用(元)';
COMMENT ON COLUMN FINANCE_REPORT.asset_impairment  IS 'cw081 资产减值损失(元)';
COMMENT ON COLUMN FINANCE_REPORT.investment_income IS 'cw083 投资收益(元)';
COMMENT ON COLUMN FINANCE_REPORT.op_profit         IS 'cw086 营业利润(元)';
COMMENT ON COLUMN FINANCE_REPORT.total_profit      IS 'cw092 利润总额(元)';
COMMENT ON COLUMN FINANCE_REPORT.income_tax        IS 'cw093 所得税(元)';
COMMENT ON COLUMN FINANCE_REPORT.net_profit        IS 'cw095 净利润(元)';
COMMENT ON COLUMN FINANCE_REPORT.net_profit_parent IS 'cw096 归母净利润(元)';
COMMENT ON COLUMN FINANCE_REPORT.minority_pl       IS 'cw097 少数股东损益(元)';
COMMENT ON COLUMN FINANCE_REPORT.rd_expense        IS 'cw304 研发费用(元)';
COMMENT ON COLUMN FINANCE_REPORT.ocf_in       IS 'cw101 经营活动现金流入小计(元)';
COMMENT ON COLUMN FINANCE_REPORT.ocf_out      IS 'cw106 经营活动现金流出小计(元)';
COMMENT ON COLUMN FINANCE_REPORT.ocf_net      IS 'cw107 经营活动现金流量净额(元)';
COMMENT ON COLUMN FINANCE_REPORT.icf_net      IS 'cw119 投资活动现金流量净额(元)';
COMMENT ON COLUMN FINANCE_REPORT.fcf_net      IS 'cw128 筹资活动现金流量净额(元)';
COMMENT ON COLUMN FINANCE_REPORT.cash_net_inc IS 'cw131 现金及现金等价物净增加额(元)';
COMMENT ON COLUMN FINANCE_REPORT.cash_end_bal IS 'cw133 期末现金及现金等价物余额(元)';
COMMENT ON COLUMN FINANCE_REPORT.current_ratio  IS 'cw159 流动比率';
COMMENT ON COLUMN FINANCE_REPORT.quick_ratio    IS 'cw160 速动比率';
COMMENT ON COLUMN FINANCE_REPORT.debt_ratio     IS 'cw210 资产负债率(%)';
COMMENT ON COLUMN FINANCE_REPORT.gross_margin   IS 'cw202 销售毛利率(%)';
COMMENT ON COLUMN FINANCE_REPORT.net_margin     IS 'cw199 销售净利率(%)';
COMMENT ON COLUMN FINANCE_REPORT.revenue_yoy    IS 'cw183 营业收入增长率(%)';
COMMENT ON COLUMN FINANCE_REPORT.net_profit_yoy IS 'cw184 净利润增长率(%)';
COMMENT ON COLUMN FINANCE_REPORT.total_shares     IS 'cw238 总股本(股)';
COMMENT ON COLUMN FINANCE_REPORT.float_a_shares   IS 'cw239 已上市流通A股(股)';
COMMENT ON COLUMN FINANCE_REPORT.num_shareholders IS 'cw242 股东人数(户)';
COMMENT ON COLUMN FINANCE_REPORT.report_anno_date IS 'cw314 财报公告日期(yyyymmdd数值)';
COMMENT ON COLUMN FINANCE_REPORT.updated_at       IS '记录最后更新时间';

-- 资产负债表视图（物理上同一张宽表，逻辑上分报表查看）
CREATE OR REPLACE VIEW V_BALANCE_SHEET AS
SELECT code, report_date,
       money_funds, accounts_recv, inventory, total_cur_assets,
       fixed_assets, intangible_assets, goodwill, total_noncur_assets, total_assets,
       short_term_loan, accounts_pay, total_cur_liab,
       long_term_loan, bonds_payable, total_noncur_liab, total_liab,
       share_capital, capital_reserve, surplus_reserve, undist_profits,
       minority_equity, total_equity
FROM FINANCE_REPORT;

-- 利润表视图
CREATE OR REPLACE VIEW V_INCOME_STATEMENT AS
SELECT code, report_date,
       op_revenue, op_cost, tax_surcharges, sales_expense, admin_expense,
       financial_expense, asset_impairment, investment_income, op_profit,
       total_profit, income_tax, net_profit, net_profit_parent, minority_pl, rd_expense
FROM FINANCE_REPORT;

-- 现金流量表视图
CREATE OR REPLACE VIEW V_CASH_FLOW AS
SELECT code, report_date,
       ocf_in, ocf_out, ocf_net, icf_net, fcf_net, cash_net_inc, cash_end_bal
FROM FINANCE_REPORT;
