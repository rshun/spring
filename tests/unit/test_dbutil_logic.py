import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

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


def test_normalize_corrects_tradestatus_when_really_traded():
    """正例: 有成交量且有成交额却被标停牌 -> 纠正为 1。

    实测全库 4 行(603133/600647/600766 的 2024-06-13、688065 的 2023-06-15), 它们的
    close 正是次一交易日的 pre_close, 佐证当天确实在交易。标错会让一切「按
    tradestatus=1 取前收」的逻辑跳过该日 —— 复权因子的逐日恒等式核对因此把一条
    完全正确的因子链报成漏事件。
    """
    df = pd.DataFrame({"code": ["A"], "date": ["2023-06-15"],
                       "open": [55.04], "high": [55.80], "low": [54.76], "close": [55.53],
                       "pre_close": [55.02], "volume": [353426], "amount": [19589785.99],
                       "tradestatus": [0]})
    assert _normalize_daily_df(df)["tradestatus"].iloc[0] == 1


def test_normalize_keeps_suspended_row_with_carried_volume():
    """反例: 停牌占位行会结转前一交易日的 volume 却不结转 amount, 不得误判为交易日。

    实例 002500.SZ 2020-06-17: volume 与 06-16 完全相同(38945309)、amount=0、
    四价全等于前收, 同花顺该日无行 —— 确为停牌。判据必须同时要求 amount>0。
    """
    df = pd.DataFrame({"code": ["A"], "date": ["2020-06-17"],
                       "open": [6.39], "high": [6.39], "low": [6.39], "close": [6.39],
                       "pre_close": [6.39], "volume": [38945309], "amount": [0.0],
                       "tradestatus": [0]})
    assert _normalize_daily_df(df)["tradestatus"].iloc[0] == 0


def test_normalize_does_not_promote_unknown_status():
    """反例: -1(未知)是有意保留的状态, 不得据成交量悄悄推断成 1。

    tools/checks/suspension.py 专为 tradestatus=-1 设了一类告警;
    在此推断掉会让那类异常永远查不出来。
    """
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "pre_close": [10.0], "volume": [100], "amount": [105000.0],
                       "tradestatus": [-1]})
    assert _normalize_daily_df(df)["tradestatus"].iloc[0] == -1


def _anomaly_df(close=16.71, code="600822.SH", date="2016-12-01"):
    """600822.SH 2016-12-01 的真实错值行: 只有 close 错, OHL 与独立源完全一致"""
    return pd.DataFrame({"code": [code], "date": [date],
                         "open": [16.66], "high": [16.95], "low": [16.50],
                         "close": [close], "pre_close": [16.68],
                         "volume": [7540113], "amount": [126000000.0],
                         "tradestatus": [1]})


def test_normalize_fixes_known_close_anomaly():
    """正例: 已核实的单点收盘价错值被修正。

    600822.SH 2016-12-01: baostock 记 16.71, 而同花顺 dump、通达信本地 .day 文件
    与 baostock 自己次日的 pre_close 三方一致为 16.70 —— 三比一, 且 open/high/low
    完全正确, 只有 close 一个字段错。
    """
    assert _normalize_daily_df(_anomaly_df())["close"].iloc[0] == 16.70


@pytest.mark.parametrize("field,value", [
    ("close", 16.72),   # 上游改了收盘价本身 —— 最要紧的一项:
                        # 不校验它就会把任意收盘价都覆盖成 16.70
    ("low",   16.40),
    ("high",  17.00),
    ("open",  16.60),
])
def test_normalize_close_anomaly_requires_full_signature(field, value):
    """反例: 整条记录签名匹配, 任一字段与已核实的错误记录不符即不得覆盖。

    与 gbbq 定点修正同一原则 —— 上游若修复或改值, 规则自动不再命中,
    绝不拿一条陈旧的硬编码去盖真实数据。
    """
    changed = _anomaly_df()
    changed.loc[0, field] = value
    got = _normalize_daily_df(changed)["close"].iloc[0]
    assert got == (value if field == "close" else 16.71)


def test_normalize_close_anomaly_does_not_touch_other_dates():
    """反例: 同一股票的其它交易日不受影响"""
    other = _anomaly_df(date="2016-12-02")
    assert _normalize_daily_df(other)["close"].iloc[0] == 16.71


def _amt_df(amount=6256618071.1, code="300999.SZ", date="2020-11-09", volume=110605581):
    """300999.SZ 金龙鱼 2020-11-09 的真实错值行。

    baostock 记成交额 62.57 亿, 而 62.57e8 / 110,605,581 股 = 均价 56.57 元,
    低于当日最低价 58.58 —— 按定义不可能(所有成交都在 [low, high] 内)。
    通达信 .day 记 67.65 亿, 均价 61.16 落在区间中间。2026-09-14 由用户用两个
    独立数据源复核确认为 67.65 亿。
    注: 该错值与前一交易日 11-06 的 amount 完全相同, 是源侧串行而非随机误差。
    """
    return pd.DataFrame({"code": [code], "date": [date],
                         "open": [61.00], "high": [64.64], "low": [58.58],
                         "close": [61.66], "pre_close": [60.90],
                         "volume": [volume], "amount": [amount],
                         "tradestatus": [1]})


def test_normalize_fixes_known_amount_anomaly():
    """正例: 已核实的成交额错值被修正, 修正后均价落回 [low, high]"""
    got = _normalize_daily_df(_amt_df())
    assert got["amount"].iloc[0] == 6764569088.0
    vwap = got["amount"].iloc[0] / got["volume"].iloc[0]
    assert 58.58 <= vwap <= 64.64


@pytest.mark.parametrize("field,value", [
    ("amount", 6.3e9),        # 上游改了成交额本身 —— 最要紧的一项
    ("volume", 110605580),
])
def test_normalize_amount_anomaly_requires_full_signature(field, value):
    """反例: 签名任一字段不符即不得覆盖(上游修复或改值时规则自动失效)"""
    changed = _amt_df()
    changed.loc[0, field] = value
    got = _normalize_daily_df(changed)["amount"].iloc[0]
    assert got == (value if field == "amount" else 6256618071.1)


def test_normalize_amount_anomaly_does_not_touch_other_dates():
    """反例: 同一股票的其它交易日不受影响"""
    assert _normalize_daily_df(_amt_df(date="2020-11-10"))["amount"].iloc[0] == 6256618071.1


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


def test_get_candidate_data_delist_excluded_from_daily_run(mem_db):
    """正例(运行安全): adjust.py 自 2026-09-13 起固定传 is_delist=True，日常跑批
    (-b/-e 都是当天)必须仍然不带上退市股——靠的是窗口裁剪：eff_begin 取当天、
    eff_end 取 delist_date，已退市个股 eff_begin > eff_end 自然落选。
    这条断言若失效，每天的增量跑会平白多算几百只早已摘牌的股票。"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2000-01-01",
                      delist_date="2023-06-30", list_status="D")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "2000-01-01")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-12-01", "2023-12-01",
                                    [], [], True, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert symbols == ["000001"]


def test_get_candidate_data_delist_included_in_history_run(mem_db):
    """正例: 历史区间跑批要带上退市股，且窗口上界截到 delist_date——
    这是把 232 只退市股补进复权因子表的入口"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2000-01-01",
                      delist_date="2023-06-30", list_status="D")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], True, SQL_STOCK)
    assert len(result) == 1
    assert result[0][0] == "600001"
    assert result[0][3] == "2023-06-30"     # eff_end 截到退市日


def test_get_candidate_data_delist_excluded_when_flag_off(mem_db):
    """反例: is_delist=False 时退市股仍被排除(其它调用方的行为不受本次改动影响)"""
    insert_stock_info(mem_db, "600001", "SH", "MAIN", "2000-01-01",
                      delist_date="2023-06-30", list_status="D")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], False, SQL_STOCK)
    assert result == []


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
