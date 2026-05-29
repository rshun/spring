# 修改记录:
#   2026-05-29  Claude  新增 cw 专业财务字段映射与 DataFrame 转换(位置列->具名字段)
"""
通达信 cw 专业财务字段映射

historyfinancialreader(见 datasource/tdx_offline.py) 读出的 DataFrame 列为
整数位置索引:
  列 0      = code (6位裸代码)
  列 N(>=1) = 通达信 cw 字段编号 N (对照 financial_dict)

完整 580 字段对照见 download/cw_field_map_raw.py。本模块只挑选实战常用的
核心字段(约60个)映射为 FINANCE_REPORT 表列名，与 sql/schema.sql 中的
FINANCE_REPORT 定义一一对应。
"""
import pandas as pd

# {cw字段编号: (FINANCE_REPORT列名, 中文名)}
# 顺序按 cw 编号升序，便于与 download/cw_field_map_raw.py 对照
CW_FIELD_MAP: dict[int, tuple[str, str]] = {
    # ① 每股指标
    1:   ("eps",              "基本每股收益"),
    2:   ("eps_deduct",       "扣非每股收益"),
    3:   ("undist_profit_ps", "每股未分配利润"),
    4:   ("bps",              "每股净资产"),
    6:   ("roe",              "净资产收益率"),
    7:   ("ocf_ps",           "每股经营现金流"),
    # ② 资产负债表
    8:   ("money_funds",         "货币资金"),
    11:  ("accounts_recv",       "应收账款"),
    17:  ("inventory",           "存货"),
    21:  ("total_cur_assets",    "流动资产合计"),
    27:  ("fixed_assets",        "固定资产"),
    33:  ("intangible_assets",   "无形资产"),
    35:  ("goodwill",            "商誉"),
    39:  ("total_noncur_assets", "非流动资产合计"),
    40:  ("total_assets",        "资产总计"),
    41:  ("short_term_loan",     "短期借款"),
    44:  ("accounts_pay",        "应付账款"),
    54:  ("total_cur_liab",      "流动负债合计"),
    55:  ("long_term_loan",      "长期借款"),
    56:  ("bonds_payable",       "应付债券"),
    62:  ("total_noncur_liab",   "非流动负债合计"),
    63:  ("total_liab",          "负债合计"),
    64:  ("share_capital",       "实收资本(股本)"),
    65:  ("capital_reserve",     "资本公积"),
    66:  ("surplus_reserve",     "盈余公积"),
    68:  ("undist_profits",      "未分配利润"),
    69:  ("minority_equity",     "少数股东权益"),
    72:  ("total_equity",        "所有者权益合计"),
    # ③ 利润表
    74:  ("op_revenue",        "营业收入"),
    75:  ("op_cost",           "营业成本"),
    76:  ("tax_surcharges",    "营业税金及附加"),
    77:  ("sales_expense",     "销售费用"),
    78:  ("admin_expense",     "管理费用"),
    80:  ("financial_expense", "财务费用"),
    81:  ("asset_impairment",  "资产减值损失"),
    83:  ("investment_income", "投资收益"),
    86:  ("op_profit",         "营业利润"),
    92:  ("total_profit",      "利润总额"),
    93:  ("income_tax",        "所得税"),
    95:  ("net_profit",        "净利润"),
    96:  ("net_profit_parent", "归母净利润"),
    97:  ("minority_pl",       "少数股东损益"),
    304: ("rd_expense",        "研发费用"),
    # ④ 现金流量表
    101: ("ocf_in",       "经营活动现金流入小计"),
    106: ("ocf_out",      "经营活动现金流出小计"),
    107: ("ocf_net",      "经营活动现金流量净额"),
    119: ("icf_net",      "投资活动现金流量净额"),
    128: ("fcf_net",      "筹资活动现金流量净额"),
    131: ("cash_net_inc", "现金及现金等价物净增加额"),
    133: ("cash_end_bal", "期末现金及现金等价物余额"),
    # ⑤ 关键比率
    159: ("current_ratio",  "流动比率"),
    160: ("quick_ratio",    "速动比率"),
    210: ("debt_ratio",     "资产负债率(%)"),
    202: ("gross_margin",   "销售毛利率(%)"),
    199: ("net_margin",     "销售净利率(%)"),
    183: ("revenue_yoy",    "营业收入增长率(%)"),
    184: ("net_profit_yoy", "净利润增长率(%)"),
    # ⑥ 股本股东
    238: ("total_shares",     "总股本"),
    239: ("float_a_shares",   "已上市流通A股"),
    242: ("num_shareholders", "股东人数"),
    # ⑦ 公告日期
    314: ("report_anno_date", "财报公告日期"),
}

# FINANCE_REPORT 的数值列名(不含 code/report_date)，按 cw 编号升序
VALUE_COLUMNS: list[str] = [name for _, (name, _) in sorted(CW_FIELD_MAP.items())]


def normalize_report_date(report_date) -> str:
    """将 YYYYMMDD / YYYY-MM-DD 统一规整为 YYYY-MM-DD 字符串"""
    s = str(report_date).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def cw_df_to_finance_report(df: pd.DataFrame, report_date) -> pd.DataFrame:
    """将 historyfinancialreader 的位置列 DataFrame 转为 FINANCE_REPORT 入库格式

    Parameters
    ----------
    df          : historyfinancialreader 输出，列为整数位置(0=code, N=cw字段N)
    report_date : 报告期，接受 YYYYMMDD 或 YYYY-MM-DD(通常取自 gpcwYYYYMMDD 文件名)

    Returns
    -------
    含 code/report_date + CW_FIELD_MAP 中各具名列的 DataFrame。
    - df 为空或 None 时返回空 DataFrame(安全降级)。
    - 旧版 cw 文件字段较少时，缺失的 cw 编号列自动跳过(不报错)。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    if 0 not in df.columns:
        raise ValueError("cw DataFrame 缺少位置列 0(code)，无法转换")

    # 一次性构造列字典再建 DataFrame。所有值都用等长数组(不混入标量)，
    # 避免在某些 pandas 全局状态下「标量与数组混用」导致除首列外被静默丢弃
    n = len(df)
    data: dict[str, object] = {
        "code": df[0].astype(str).to_numpy(),
        "report_date": [normalize_report_date(report_date)] * n,
    }
    for col_num, (name, _cn) in sorted(CW_FIELD_MAP.items()):
        if col_num in df.columns:
            data[name] = pd.to_numeric(df[col_num], errors="coerce").to_numpy()

    return pd.DataFrame(data)
