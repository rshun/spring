# 修改记录:
#   2026-06-24  Claude  新增沪深主板 ST 涨跌停 2026-07-06 由 5%->10% 的正反测试
#   2026-09-11  Claude  新增新股无涨跌幅窗口「交易日 vs 自然日」口径的正反测试；
#                       新增写库失败必须重抛的反例
#   2026-10-02  Claude  新增上市首日规则正反例: 2014-01-01 前首日不设限、合并上市名单首日不设限
#                       上市日早于日历起点的股票不得把日历首日当上市首日
#   2026-10-02  Claude  新增不设涨跌幅交易日名单(股改复牌首日)的正反例与名单文件契约测试
#   2026-10-03  Claude  名单改为可配多个文件(股改复牌 + 增发/追加对价上市日), 补多文件合并、
#                       跨文件重复报错的正反例与两份名单的契约测试; _run 改回多行 with
#   2026-10-03  Claude  股改名单才限 2005-2008; 增发清单补 2011 新规后不得收录增发类的边界与反例
#   2026-10-04  Claude  名单文件改从 util.paths.PACKAGE_ROOT 读取(dbutil._PROJECT_ROOT 已移除)
"""update_price_limits_by_range 涨跌停比率计算测试 (聚焦沪深主板 ST 规则切换)。"""
from unittest.mock import patch, MagicMock

import pytest

from util.dbutil import update_price_limits_by_range
from tests.conftest import insert_stock_info, insert_trade_cal


def _wrap(mem_db):
    m = MagicMock(wraps=mem_db)
    m.close = MagicMock()  # 避免关闭 fixture 持有的连接
    return m


def _seed_main_st(mem_db, trade_date: str, pre_close: float = 10.0):
    """种入一只沪深主板 ST 股: STOCK_INFO + STOCK_DAILY + DAILY_BASIC(待回填)。"""
    insert_stock_info(mem_db, "600000", "SH", "MAIN", list_date="2010-01-01")
    code = "600000.SH"
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1000, 1.0)",
        [code, trade_date, pre_close, pre_close, pre_close, pre_close, pre_close],
    )
    mem_db.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, is_st) VALUES (?, ?, 1)",
        [code, trade_date],
    )
    return code


def _limit_up(mem_db, code, trade_date):
    return mem_db.execute(
        "SELECT limit_up FROM DAILY_BASIC WHERE code = ? AND trade_date = ?",
        [code, trade_date],
    ).fetchone()[0]


# ── 正例: 2026-07-06 起沪深主板 ST 用 10% ───────────────────────────────────────

def test_main_st_uses_10pct_on_and_after_20260706(mem_db):
    """正例: 切换日当天主板 ST 涨停价 = pre_close * 1.10。"""
    code = _seed_main_st(mem_db, "2026-07-06", pre_close=10.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2026-07-06", "2026-07-06")
    assert _limit_up(mem_db, code, "2026-07-06") == 11.0  # 10.0 * 1.10


# ── 反例(边界): 切换日前一交易日仍用 5% ─────────────────────────────────────────

def test_main_st_still_uses_5pct_before_20260706(mem_db):
    """反例/边界: 2026-07-03 (切换前) 主板 ST 仍为 5%，涨停价 = pre_close * 1.05。"""
    code = _seed_main_st(mem_db, "2026-07-03", pre_close=10.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2026-07-03", "2026-07-03")
    assert _limit_up(mem_db, code, "2026-07-03") == 10.5  # 10.0 * 1.05


# ── 新股无涨跌幅限制窗口: 必须按交易日而非自然日计数 ─────────────────────────────
#
# 创业板/科创板注册制新股「上市后前 5 个交易日不设涨跌幅限制」。此前用
# date_diff('day', list_date, date)+1 按自然日计数，上市日落在周中时会提前越过 5，
# 把仍在无限制期内的新股按 20% 限价。下面用 300784.SZ 的真实日历复现:
#   2024-06-07(五) 上市 = 第 1 个交易日   自然日 1
#   2024-06-08/09 周末, 2024-06-10 端午休市
#   2024-06-11(二) = 第 2 个交易日        自然日 5
#   2024-06-12(三) = 第 3 个交易日        自然日 6  ← 旧算法在这里就误判出窗口
#   2024-06-13(四) = 第 4 个交易日        自然日 7
#   2024-06-14(五) = 第 5 个交易日        自然日 8
#   2024-06-17(一) = 第 6 个交易日        自然日 11 ← 才真正恢复 20% 限制

_GEM_LIST_DATE = "2024-06-07"
_GEM_TRADE_DAYS = ["2024-06-07", "2024-06-11", "2024-06-12",
                   "2024-06-13", "2024-06-14", "2024-06-17"]


def _seed_gem_new_stock(mem_db, trade_date: str, pre_close: float = 100.0):
    """种入一只 2024-06-07 上市的创业板新股，并按真实日历填 TRADE_CAL。"""
    insert_stock_info(mem_db, "300784", "SZ", "GEM", list_date=_GEM_LIST_DATE)
    for d in _GEM_TRADE_DAYS:
        insert_trade_cal(mem_db, d, 1)
    code = "300784.SZ"
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
        "pre_close, tradestatus, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1000, 1.0)",
        [code, trade_date, pre_close, pre_close, pre_close, pre_close, pre_close],
    )
    mem_db.execute(
        "INSERT INTO DAILY_BASIC (code, trade_date, is_st) VALUES (?, ?, 0)",
        [code, trade_date],
    )
    return code


def test_gem_new_stock_third_trading_day_has_no_limit(mem_db):
    """正例(本次修复的回归点): 第 3 个交易日(自然日第 6 天)仍在无涨跌幅限制窗口内，
    limit_up 必须是哨兵值 999999.99 而不是 pre_close * 1.20。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-12", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-12", "2024-06-12")
    assert _limit_up(mem_db, code, "2024-06-12") == 999999.99


def test_gem_new_stock_first_trading_day_has_no_limit(mem_db):
    """正例: 上市首日(第 1 个交易日)无涨跌幅限制。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-07", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-07", "2024-06-07")
    assert _limit_up(mem_db, code, "2024-06-07") == 999999.99


def test_gem_new_stock_fifth_trading_day_still_has_no_limit(mem_db):
    """正例(边界): 第 5 个交易日(自然日第 8 天)是窗口最后一天，仍不设限。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-14", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-14", "2024-06-14")
    assert _limit_up(mem_db, code, "2024-06-14") == 999999.99


def test_gem_new_stock_sixth_trading_day_restores_20pct(mem_db):
    """反例(边界): 第 6 个交易日才真正退出窗口，恢复 20% 限制。
    修复不能矫枉过正地把无限制期一直延下去。"""
    code = _seed_gem_new_stock(mem_db, "2024-06-17", pre_close=100.0)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2024-06-17", "2024-06-17")
    assert _limit_up(mem_db, code, "2024-06-17") == 120.0


def test_old_stock_unaffected_by_trading_day_rewrite(mem_db):
    """反例: 早已上市的老股(上市日远早于日历覆盖范围)不受改写影响，仍按板块常规比率。
    早期交易日不在 TRADE_CAL 里时 days_count 取哨兵 99，不得被误判成新股。"""
    code = _seed_main_st(mem_db, "2026-07-06", pre_close=10.0)
    insert_trade_cal(mem_db, "2026-07-06", 1)
    with patch("util.dbutil.get_connection", return_value=_wrap(mem_db)):
        update_price_limits_by_range("2026-07-06", "2026-07-06")
    assert _limit_up(mem_db, code, "2026-07-06") == 11.0


# ── 写库失败必须重抛(契约 C1) ───────────────────────────────────────────────────

def test_update_failure_reraises(mem_db):
    """反例(本次修复的回归点): 批量更新 SQL 失败时必须重抛，
    否则 etl/update_limit.py 无条件 return 0，写库失败仍退出 0。"""
    broken = MagicMock()
    broken.execute.side_effect = RuntimeError("SQL 执行失败")
    broken.close = MagicMock()
    with patch("util.dbutil.get_connection", return_value=broken):
        with pytest.raises(RuntimeError, match="SQL 执行失败"):
            update_price_limits_by_range("2026-07-06", "2026-07-06")
    broken.close.assert_called_once()


# ── 上市首日: 2014-01-01 前不设限 / 合并上市不设限 ─────────────────────────────
#
# 新股首日 44% 上限 2014-01-01 才生效(沪深同日), 此前首日无涨跌幅限制。
# 合并上市(换股吸收合并 / B 转 A)无发行价, 首日不适用 44%, 名单在 config.yaml。

def _seed_listing(mem_db, symbol, exchange, board, trade_days, pre_close=10.0):
    """种入一只以 trade_days[0] 为上市日的股票, 每个交易日一行日线"""
    insert_stock_info(mem_db, symbol, exchange, board, list_date=trade_days[0])
    code = f"{symbol}.{exchange}"
    for d in trade_days:
        insert_trade_cal(mem_db, d, 1)
        mem_db.execute(
            "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, "
            "pre_close, tradestatus, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1000, 1.0)",
            [code, d, pre_close, pre_close, pre_close, pre_close, pre_close])
        mem_db.execute(
            "INSERT INTO DAILY_BASIC (code, trade_date, is_st) VALUES (?, ?, 0)",
            [code, d])
    return code


def _run(mem_db, begin, end, merger_codes=(), no_limit_days=()):
    with (patch("util.dbutil.get_connection", return_value=_wrap(mem_db)),
          patch("util.dbutil._no_limit_first_day_codes", return_value=list(merger_codes)),
          patch("util.dbutil._load_no_limit_days", return_value=list(no_limit_days))):
        update_price_limits_by_range(begin, end)


def _limits(mem_db, code, trade_date):
    return mem_db.execute(
        "SELECT limit_up, limit_down FROM DAILY_BASIC WHERE code = ? AND trade_date = ?",
        [code, trade_date]).fetchone()


def test_first_day_before_2014_has_no_limit(mem_db):
    """正例: 2010 年上市首日(中航电测原型)不设涨跌幅 -> 哨兵值"""
    code = _seed_listing(mem_db, "300114", "SZ", "GEM", ["2010-08-27", "2010-08-30"], 25.0)
    _run(mem_db, "2010-08-27", "2010-08-30")
    assert _limits(mem_db, code, "2010-08-27") == (999999.99, 0.01)


def test_first_day_on_20131231_still_no_limit(mem_db):
    """正例(边界): 切换日前一天上市的首日仍不设限"""
    code = _seed_listing(mem_db, "600001", "SH", "MAIN", ["2013-12-31"])
    _run(mem_db, "2013-12-31", "2013-12-31")
    assert _limits(mem_db, code, "2013-12-31") == (999999.99, 0.01)


def test_first_day_from_2014_uses_44pct(mem_db):
    """反例(边界): 2014-01-02 起的新股首日恢复 +44% / -36%"""
    code = _seed_listing(mem_db, "600001", "SH", "MAIN", ["2014-01-02"])
    _run(mem_db, "2014-01-02", "2014-01-02")
    assert _limits(mem_db, code, "2014-01-02") == (14.4, 6.4)


def test_second_day_before_2014_keeps_10pct(mem_db):
    """反例: 只放开首日, 2014 年前上市的第 2 个交易日仍按 10%"""
    code = _seed_listing(mem_db, "300114", "SZ", "GEM", ["2010-08-27", "2010-08-30"], 25.0)
    _run(mem_db, "2010-08-27", "2010-08-30")
    assert _limits(mem_db, code, "2010-08-30") == (27.5, 22.5)


def test_merger_listing_first_day_has_no_limit(mem_db):
    """正例: 名单内的合并上市(申万宏源 2015-01-26)首日不设涨跌幅"""
    code = _seed_listing(mem_db, "000166", "SZ", "MAIN", ["2015-01-26", "2015-01-27"], 4.86)
    _run(mem_db, "2015-01-26", "2015-01-27", merger_codes=["000166.SZ"])
    assert _limits(mem_db, code, "2015-01-26") == (999999.99, 0.01)


def test_merger_listing_second_day_keeps_10pct(mem_db):
    """反例: 合并上市只放开首日, 第 2 个交易日按 10%"""
    code = _seed_listing(mem_db, "000166", "SZ", "MAIN", ["2015-01-26", "2015-01-27"], 10.0)
    _run(mem_db, "2015-01-26", "2015-01-27", merger_codes=["000166.SZ"])
    assert _limits(mem_db, code, "2015-01-27") == (11.0, 9.0)


def test_ipo_not_in_merger_list_first_day_uses_44pct(mem_db):
    """反例: 同期上市但不在名单里的普通新股, 首日仍按 44%; 空名单也不得报错"""
    code = _seed_listing(mem_db, "000166", "SZ", "MAIN", ["2015-01-26"], 10.0)
    _run(mem_db, "2015-01-26", "2015-01-26", merger_codes=["300498.SZ"])
    assert _limits(mem_db, code, "2015-01-26") == (14.4, 6.4)
    mem_db.execute("UPDATE DAILY_BASIC SET limit_up = NULL, limit_down = NULL")
    _run(mem_db, "2015-01-26", "2015-01-26", merger_codes=[])
    assert _limits(mem_db, code, "2015-01-26") == (14.4, 6.4)


def test_merger_list_read_from_config():
    """正例: 名单确实从 config.yaml 读取, 且包含已核实的 3 只"""
    from util.dbutil import _no_limit_first_day_codes
    assert {"000166.SZ", "300498.SZ", "601155.SH"} <= set(_no_limit_first_day_codes())


def test_listed_before_calendar_start_not_treated_as_first_day(mem_db):
    """反例(回归): 1999-11-10 上市的浦发银行, 日历从 2000 年才开始;
    2000-01-04 不是它的上市首日, 必须按主板 10%, 不得不设限也不得按 44%"""
    insert_stock_info(mem_db, "600000", "SH", "MAIN", list_date="1999-11-10")
    insert_trade_cal(mem_db, "2000-01-04", 1)
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, pre_close, "
        "tradestatus, volume, amount) VALUES ('600000.SH', '2000-01-04', 10, 10, 10, 10, 10, 1, 1000, 1.0)")
    mem_db.execute("INSERT INTO DAILY_BASIC (code, trade_date, is_st) "
                   "VALUES ('600000.SH', '2000-01-04', 0)")
    _run(mem_db, "2000-01-04", "2000-01-04")
    assert _limits(mem_db, "600000.SH", "2000-01-04") == (11.0, 9.0)


def test_listed_on_calendar_start_still_counts_first_day(mem_db):
    """正例(边界): 上市日恰好等于日历起点时仍能正常识别首日(2000 年 -> 不设限)"""
    code = _seed_listing(mem_db, "600001", "SH", "MAIN", ["2000-01-04"])
    _run(mem_db, "2000-01-04", "2000-01-04")
    assert _limits(mem_db, code, "2000-01-04") == (999999.99, 0.01)


# ── 不设涨跌幅的特定交易日名单(股改复牌首日) ────────────────────────────────────

import datetime as _dt

_REFORM_DAYS = ["2006-02-24", "2006-02-27", "2006-02-28"]   # 停牌前一日 / 复牌首日 / 次日


def test_no_limit_day_in_list_has_no_limit(mem_db):
    """正例: 名单内的 (code, date) 不设涨跌幅 -> 哨兵值"""
    code = _seed_listing(mem_db, "600036", "SH", "MAIN", ["2002-04-09"])
    for d in _REFORM_DAYS:
        _ins_day(mem_db, code, d)
    _run(mem_db, "2006-02-24", "2006-02-28",
         no_limit_days=[(code, _dt.date(2006, 2, 27))])
    assert _limits(mem_db, code, "2006-02-27") == (999999.99, 0.01)


def test_no_limit_day_only_affects_that_day_and_code(mem_db):
    """反例: 名单只放开那一天那一只; 前后交易日、同日其他股票照常 10%"""
    code = _seed_listing(mem_db, "600036", "SH", "MAIN", ["2002-04-09"])
    other = _seed_listing(mem_db, "600000", "SH", "MAIN", ["2002-04-10"])
    for d in _REFORM_DAYS:
        _ins_day(mem_db, code, d)
    _ins_day(mem_db, other, "2006-02-27")
    _run(mem_db, "2006-02-24", "2006-02-28",
         no_limit_days=[(code, _dt.date(2006, 2, 27))])
    assert _limits(mem_db, code, "2006-02-24") == (11.0, 9.0)
    assert _limits(mem_db, code, "2006-02-28") == (11.0, 9.0)
    assert _limits(mem_db, other, "2006-02-27") == (11.0, 9.0)


def test_empty_no_limit_days_keeps_10pct(mem_db):
    """反例: 名单为空(未配置)时照常 10%, 不报错"""
    code = _seed_listing(mem_db, "600036", "SH", "MAIN", ["2002-04-09"])
    _ins_day(mem_db, code, "2006-02-27")
    _run(mem_db, "2006-02-27", "2006-02-27", no_limit_days=[])
    assert _limits(mem_db, code, "2006-02-27") == (11.0, 9.0)


def _ins_day(mem_db, code, d, pre_close=10.0):
    # 多只股票共用同一交易日, 日历只插一次
    mem_db.execute("INSERT OR IGNORE INTO TRADE_CAL (cal_date, is_open) VALUES (?, 1)", [d])
    mem_db.execute(
        "INSERT INTO STOCK_DAILY (code, date, open, high, low, close, pre_close, "
        "tradestatus, volume, amount) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1000, 1.0)",
        [code, d, pre_close, pre_close, pre_close, pre_close, pre_close])
    mem_db.execute("INSERT INTO DAILY_BASIC (code, trade_date, is_st) VALUES (?, ?, 0)",
                   [code, d])


# ── 名单加载 ──────────────────────────────────────────────────────────────────

def _patch_days_files(files):
    return patch("util.dbutil.get_config",
                 return_value={"price_limit": {"no_limit_days_files": files}})


def test_load_no_limit_days_parses_file(tmp_path):
    """正例: 读 code/date 两列, 忽略其他列, 兼容 BOM"""
    from util.dbutil import _load_no_limit_days
    f = tmp_path / "days.csv"
    f.write_text("code,date,tier\n600036.SH,2006-02-27,price_breach\n", encoding="utf-8-sig")
    with _patch_days_files([str(f)]):
        assert _load_no_limit_days() == [("600036.SH", _dt.date(2006, 2, 27))]


def test_load_no_limit_days_merges_multiple_files(tmp_path):
    """正例: 多个名单文件合并使用, 各文件可有不同的附加列"""
    from util.dbutil import _load_no_limit_days
    a = tmp_path / "reform.csv"
    a.write_text("code,date,tier\n600036.SH,2006-02-27,price_breach\n", encoding="utf-8")
    b = tmp_path / "listing.csv"
    b.write_text("code,date,event,note,source\n600481.SH,2008-01-21,public_offering,,\n",
                 encoding="utf-8")
    with _patch_days_files([str(a), str(b)]):
        assert _load_no_limit_days() == [("600036.SH", _dt.date(2006, 2, 27)),
                                         ("600481.SH", _dt.date(2008, 1, 21))]


def test_load_no_limit_days_duplicate_across_files_raises(tmp_path):
    """反例(关键): 同一 (code, date) 出现在两份名单里必须报错, 防止重复维护"""
    from util.dbutil import _load_no_limit_days
    a = tmp_path / "reform.csv"
    a.write_text("code,date\n600481.SH,2008-01-21\n", encoding="utf-8")
    b = tmp_path / "listing.csv"
    b.write_text("code,date\n600481.SH,2008-01-21\n", encoding="utf-8")
    with _patch_days_files([str(a), str(b)]):
        with pytest.raises(ValueError, match="重复"):
            _load_no_limit_days()


def test_load_no_limit_days_unconfigured_is_empty():
    """反例: 未配置 / 空列表 -> 空名单, 不报错"""
    from util.dbutil import _load_no_limit_days
    with _patch_days_files([]):
        assert _load_no_limit_days() == []
    with patch("util.dbutil.get_config", return_value={}):
        assert _load_no_limit_days() == []


def test_load_no_limit_days_missing_file_raises(tmp_path):
    """反例(关键): 已配置但文件不存在必须报错, 不能静默退回 10%"""
    from util.dbutil import _load_no_limit_days
    with _patch_days_files([str(tmp_path / "nope.csv")]):
        with pytest.raises(FileNotFoundError):
            _load_no_limit_days()


def test_load_no_limit_days_missing_columns_raises(tmp_path):
    """反例: 缺 code/date 列必须报错"""
    from util.dbutil import _load_no_limit_days
    f = tmp_path / "days.csv"
    f.write_text("symbol,day\n600036,2006-02-27\n", encoding="utf-8")
    with _patch_days_files([str(f)]):
        with pytest.raises(ValueError):
            _load_no_limit_days()


# ── 仓库内名单文件的契约 ──────────────────────────────────────────────────────

def _read_repo_csv(name):
    import csv
    from util.paths import PACKAGE_ROOT
    with open(PACKAGE_ROOT / "data" / name, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_repo_lists_load_together_without_duplicates():
    """契约: config 配置的名单都能读、合并后无重复、代码带交易所后缀"""
    from util.dbutil import _load_no_limit_days
    days = _load_no_limit_days()          # 跨文件重复时这里就会抛错
    assert len(days) > 1000
    assert all(c.endswith((".SH", ".SZ")) for c, _ in days)


def test_repo_reform_list_tiers_are_known():
    """契约: 股改名单的 tier 只允许 README 中列出的取值, 日期都在股改时期 2005-2008
    (增发清单不限年份: 增发上市首日不设涨跌幅至少延续到 2010 年)"""
    rows = _read_repo_csv("reform_resume_days.csv")
    assert {r["tier"] for r in rows} <= {"price_breach", "no_exright", "manual"}
    assert all("2005-01-01" <= r["date"] <= "2008-12-31" for r in rows)


# 增发上市首日不设涨跌幅的规定 2011 年新版交易规则实施后取消, 此后的增发上市日照常限价,
# 不得收录。确切实施日待核实, 先按「不晚于 2011 年底」卡边界。追加对价不受此限。
_OFFERING_RULE_LAST_DATE = "2011-12-31"


def _offering_after_rule_change(rows):
    """返回日期晚于规则取消边界的增发类记录 [(code, date), ...]"""
    return [(r["code"], r["date"]) for r in rows
            if r["event"] in ("public_offering", "private_placement")
            and r["date"] > _OFFERING_RULE_LAST_DATE]


def test_offering_after_rule_change_is_caught():
    """反例: 2012 年的增发上市日必须被拦下; 2010 年的增发、2012 年的追加对价不受影响"""
    rows = [{"code": "600642.SH", "date": "2010-10-29", "event": "public_offering"},
            {"code": "600000.SH", "date": "2012-03-01", "event": "public_offering"},
            {"code": "600001.SH", "date": "2012-03-01", "event": "private_placement"},
            {"code": "600002.SH", "date": "2012-03-01", "event": "extra_consideration"}]
    assert _offering_after_rule_change(rows) == [("600000.SH", "2012-03-01"),
                                                 ("600001.SH", "2012-03-01")]


def test_repo_share_listing_list_is_well_formed():
    """契约: 增发清单列齐全、event 取值合法、日期格式正确, 早先人工核实的 6 条都在"""
    rows = _read_repo_csv("share_listing_days.csv")
    assert list(rows[0].keys()) == ["code", "date", "event", "note", "source"]
    assert {r["event"] for r in rows} <= {"public_offering", "private_placement",
                                          "extra_consideration"}
    assert all(_dt.date.fromisoformat(r["date"]) for r in rows)
    assert _offering_after_rule_change(rows) == []
    keys = {(r["code"], r["date"]) for r in rows}
    assert {("600753.SH", "2007-05-22"), ("600481.SH", "2008-01-21"),
            ("000921.SZ", "2008-04-11"), ("000722.SZ", "2008-04-17"),
            ("000868.SZ", "2008-05-09"), ("600594.SH", "2008-06-17")} <= keys
