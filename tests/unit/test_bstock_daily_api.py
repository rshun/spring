# 修改记录:
#   2026-09-07  Claude  新增：按日期接口的路由、字段转换、范围及失败处理测试
"""按日期接口的路由、字段转换、范围及失败处理；不连接真实网络或数据库。"""
from contextlib import ExitStack
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

from datasource import bstock
from etl import adjust, import_daily
from tools import describe_cli


@pytest.mark.parametrize("module", [import_daily, adjust])
@pytest.mark.parametrize("argv, expected", [
    (["-b", "20260901", "-e", "20260904"], True),
    (["--begin=20260901", "--end=20260904"], True),
    (["-b", "20260901", "-e", "20260904", "-c", "600000"], False),
    (["-b", "20260901", "-e", "20260904", "-x", "all"], False),
    (["-b", "20260901", "-e", "20260904", "-s", "bstock"], False),
])
def test_date_only_route(module, argv, expected):
    """正反例: 给全日期区间走按日接口, 带任何非日期参数一律退回逐股接口"""
    with patch("sys.argv", ["etl"] + argv):
        assert module.parse_arguments().date_range_only is expected


@pytest.mark.parametrize("argv", [[], ["-b", "20260901"], ["-e", "20260904"]])
def test_import_daily_partial_or_absent_date_still_by_date(argv):
    """正例: import_daily 的日期可缺省(当天)或只给一端, 仍算「只带日期」走按日接口"""
    with patch("sys.argv", ["etl"] + argv):
        assert import_daily.parse_arguments().date_range_only is True


@pytest.mark.parametrize("argv", [[], ["-b", "20260901"], ["-e", "20260904"]])
def test_adjust_still_requires_both_dates(argv):
    """反例: adjust 未同步放宽, 仍要求 -b/-e 都显式给出——防止两个程序的语义被静默改掉"""
    with patch("sys.argv", ["etl"] + argv):
        assert adjust.parse_arguments().date_range_only is False


@pytest.mark.parametrize("module, new_method, old_method", [
    (import_daily, "fetch_daily_data_by_date", "fetch_batch_data"),
    (adjust, "fetch_adjust_factors_by_date", "fetch_adjust_factors"),
])
@pytest.mark.parametrize("date_only", [True, False])
def test_main_dispatch(module, new_method, old_method, date_only):
    args = module.build_parser().parse_args(["-b", "20260901", "-e", "20260904"])
    args.date_range_only = date_only
    source = MagicMock()
    result = (pd.DataFrame(), pd.DataFrame()) if module is import_daily else pd.DataFrame()
    getattr(source, new_method).return_value = result
    getattr(source, old_method).return_value = result
    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "parse_arguments", return_value=args))
        stack.enter_context(patch.object(module, "check_parameters", return_value=True))
        util = stack.enter_context(patch.object(module, "myutil"))
        db = stack.enter_context(patch.object(module, "dbutil"))
        util.trans_datestr_format.side_effect = ["2026-09-01", "2026-09-04"]
        util.import_source_module.return_value = source
        db.get_candidate_codes.return_value = TARGETS
        if module is adjust:
            stack.enter_context(patch.object(module, "process_and_save_adjust_factors"))
        assert module.main() == 0
        if date_only:
            getattr(source, new_method).assert_called_once_with(TARGETS, "2026-09-01", "2026-09-04")
            getattr(source, old_method).assert_not_called()
        else:
            getattr(source, old_method).assert_called_once_with(TARGETS)
            getattr(source, new_method).assert_not_called()


TARGETS = [
    ("600000", "SH", "2026-09-01", "2026-09-04", "L"),
    ("000001", "SZ", "2026-09-02", "2026-09-04", "L"),
    ("600001", "SH", "2026-09-01", "2026-09-04", "D"),
    ("920001", "BJ", "2026-09-01", "2026-09-04", "L"),
]
FIELDS = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,isST,peTTM,pbMRQ".split(",")


def result_set(fields, rows, error="0"):
    rs = MagicMock(error_code=error, error_msg="query failed", fields=fields)
    rs.next.side_effect = [True] * len(rows) + [False]
    rs.get_row_data.side_effect = rows
    return rs


def krow(day="2026-09-01", code="sh.600000", turn="1.5", status="1"):
    return [day, code, "10", "11", "9", "10.5", "10", "100", "1050", "3", turn, status, "5", "0", "8", "1"]


@pytest.fixture
def market():
    with ExitStack() as stack:
        stack.enter_context(patch.object(bstock.bs, "login", return_value=MagicMock(error_code="0")))
        logout = stack.enter_context(patch.object(bstock.bs, "logout"))
        stack.enter_context(patch.object(bstock.socket, "setdefaulttimeout"))
        stack.enter_context(patch.object(bstock, "_get_max_fetch_attempts", return_value=2))
        stack.enter_context(patch.object(bstock, "_get_progress_heartbeat_seconds", return_value=30))
        stack.enter_context(patch.object(bstock, "_get_retry_delay_missing", return_value=0))
        stack.enter_context(patch.object(bstock, "_get_retry_delay_login", return_value=0))
        stack.enter_context(patch.object(bstock, "_get_retry_delay_pipe", return_value=0))
        stack.enter_context(patch.object(bstock.time, "sleep"))
        calendar = stack.enter_context(patch.object(bstock.bs, "query_trade_dates", return_value=result_set(
            ["calendar_date", "is_trading_day"],
            [["2026-09-01", "1"], ["2026-09-02", "1"], ["2026-09-03", "0"]])))
        daily = stack.enter_context(patch.object(bstock.bs, "query_daily_history_k_AStock", create=True))
        factor = stack.enter_context(patch.object(bstock.bs, "query_daily_adjust_factor", create=True))
        yield daily, factor, logout, calendar


def test_daily_market_conversion_and_bounds(market):
    daily, _, logout, _ = market
    daily.side_effect = [result_set(FIELDS, [krow(code=code) for code in
        ["sh.600000", "sz.000001", "sh.600001", "bj.920001", "sh.600002"]]),
        result_set(FIELDS, [krow("2026-09-02", "sz.000001", "", "0")])]
    data, basic = bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    assert data["code"].tolist() == ["600000.SH", "000001.SZ"]
    assert data["close"].tolist() == [10.5, 10.5]
    assert basic["turnover_rate"].iloc[0] == 1.5
    assert pd.isna(basic["turnover_rate"].iloc[1])
    assert daily.call_args_list == [call(date="2026-09-01"), call(date="2026-09-02")]
    logout.assert_called_once()


@pytest.mark.parametrize("factor_field", ["adjustFactor", "adjustFacto"])
def test_factor_market_columns_and_empty_day(market, factor_field):
    _, factor, logout, _ = market
    fields = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", factor_field]
    factor.side_effect = [result_set(fields, [
        ["sh.600000", "2026-09-01", "0.5", "2", "1.1"],
        ["sz.000001", "2026-09-01", "0.5", "2", "1.1"]]), result_set(fields, [])]
    data = bstock.fetch_adjust_factors_by_date(TARGETS, "2026-09-01", "2026-09-03")
    assert data.to_dict("records") == [dict(code="600000.SH", date="2026-09-01",
        fore_factor="0.5", back_factor="2", adjust_factor="1.1")]
    assert factor.call_count == 2
    logout.assert_called_once()


def test_missing_turn_retries_whole_day_without_duplicates(market):
    daily, _, _, _ = market
    daily.side_effect = [result_set(FIELDS, [krow(turn="")]),
                         result_set(FIELDS, [krow()]), result_set(FIELDS, [])]
    with patch.object(bstock, "relogin") as relogin:
        data, _ = bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    assert len(data) == 1
    relogin.assert_called_once()


def test_failed_day_does_not_return_partial_data(market):
    daily, _, logout, _ = market
    daily.side_effect = [result_set(FIELDS, [krow()]), result_set(FIELDS, [], error="1")]
    with pytest.raises(bstock.BaoQueryError):
        bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    logout.assert_called_once()


def test_missing_interface_reports_environment_error():
    with patch.object(bstock.bs, "query_daily_adjust_factor", None, create=True):
        with pytest.raises(RuntimeError, match="query_daily_adjust_factor"):
            bstock.fetch_adjust_factors_by_date(TARGETS, "2026-09-01", "2026-09-03")


def test_print_only_retains_old_route():
    with patch("sys.argv", ["etl", "-b", "20260901", "-e", "20260904", "-p"]):
        assert import_daily.parse_arguments().date_range_only is False


def test_result_iteration_error_is_not_silently_accepted():
    rs = result_set(FIELDS, [])

    def fail_next():
        rs.error_code = "1"
        return False

    rs.next.side_effect = fail_next
    with pytest.raises(bstock.BaoQueryError):
        bstock._read_query_frame(rs, "2026-09-01")


def test_english_login_error_retries(market):
    daily, _, _, _ = market
    failed = result_set(FIELDS, [], error="1")
    failed.error_msg = "you don't login."
    daily.side_effect = [failed, result_set(FIELDS, [krow()]), result_set(FIELDS, [])]
    with patch.object(bstock, "relogin") as relogin:
        data, _ = bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    assert len(data) == 1
    relogin.assert_called_once()


def test_missing_turn_exhaustion_aborts(market):
    daily, _, logout, _ = market
    daily.side_effect = [result_set(FIELDS, [krow(turn="")]), result_set(FIELDS, [krow(turn="")])]
    with patch.object(bstock, "relogin"):
        with pytest.raises(bstock.BaoMissingDataError):
            bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    logout.assert_called_once()


# ── #4 复权因子按当日事件过滤 ─────────────────────────────────────────────────

def test_factor_event_not_duplicated_across_days(market):
    """反例: 按日接口若逐日重复带回同一条历史事件, 只应保留事件当日那一条"""
    _, factor, _, _ = market
    fields = ["code", "dividOperateDate", "foreAdjustFactor", "backAdjustFactor", "adjustFactor"]
    event = ["sh.600000", "2026-09-01", "0.5", "2", "1.1"]
    # 09-01 是事件当日; 09-02 的查询把同一条历史事件又带了回来
    factor.side_effect = [result_set(fields, [event]), result_set(fields, [event])]
    data = bstock.fetch_adjust_factors_by_date(TARGETS, "2026-09-01", "2026-09-03")
    assert len(data) == 1
    assert data["date"].tolist() == ["2026-09-01"]


# ── #5 按日路由的推断 ────────────────────────────────────────────────────────

def test_import_daily_has_no_by_date_switch():
    """反例: import_daily 不应有 --by-date 开关, 路由完全由参数形态推断"""
    dests = {a.dest for a in import_daily.build_parser()._actions}
    assert "by_date" not in dests
    assert "by_date" not in describe_cli.describe("import_daily")["arguments"]


@pytest.mark.parametrize("source", ["lday", "tdx"])
def test_non_bstock_source_never_uses_by_date_api(source):
    """反例: 只有 bstock 提供按日接口, 其余源一律走逐股"""
    with patch("sys.argv", ["etl", "-s", source]):
        assert import_daily.parse_arguments().date_range_only is False
    assert import_daily.resolve_by_date(source) is False


@pytest.mark.parametrize("argv, expected", [
    (["-b", "20260901", "-e", "20260904", "--by-date", "on"], True),
    (["-b", "20260901", "-e", "20260904", "-x", "all", "--by-date", "on"], True),
    (["-b", "20260901", "-e", "20260904", "--by-date", "off"], False),
    (["--by-date", "on"], True),
])
def test_adjust_by_date_tristate(argv, expected):
    """正反例: adjust 仍保留 auto/on/off 三态, 未随 import_daily 一起简化"""
    with patch("sys.argv", ["etl"] + argv):
        assert adjust.parse_arguments().date_range_only is expected


def test_adjust_by_date_is_discoverable():
    """正例: adjust 的三态开关同样要能被自省到"""
    spec = describe_cli.describe("adjust")["arguments"]["by_date"]
    assert spec["choices"] == ["auto", "on", "off"]
    assert spec["default"] == "auto"
    assert spec["help"]


def test_missing_by_date_method_reports_clear_error():
    """反例: 走按日分支但模块缺该方法时, 守卫要按方法名报错, 不漏到笼统的兜底 except"""
    args = import_daily.build_parser().parse_args(["-b", "20260901", "-e", "20260904"])
    args.date_range_only = True
    source = MagicMock(spec=["fetch_batch_data"])          # 旧版模块: 只有逐股方法
    with ExitStack() as stack:
        stack.enter_context(patch.object(import_daily, "parse_arguments", return_value=args))
        stack.enter_context(patch.object(import_daily, "check_parameters", return_value=True))
        util = stack.enter_context(patch.object(import_daily, "myutil"))
        db = stack.enter_context(patch.object(import_daily, "dbutil"))
        util.trans_datestr_format.side_effect = ["2026-09-01", "2026-09-04"]
        util.import_source_module.return_value = source
        db.get_candidate_codes.return_value = TARGETS
        assert import_daily.main() == 1


# ── #2 网络类错误码可重试 ────────────────────────────────────────────────────

def test_network_error_code_retries(market):
    """正例: 网络类错误码(10002xxx)属瞬时故障, 重登重试后应拿到数据而非丢弃整段区间"""
    daily, _, _, _ = market
    failed = result_set(FIELDS, [], error="10002007")
    failed.error_msg = "网络接收错误"
    daily.side_effect = [failed, result_set(FIELDS, [krow()]), result_set(FIELDS, [])]
    with patch.object(bstock, "relogin") as relogin:
        data, _ = bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    assert len(data) == 1
    relogin.assert_called_once()


def test_non_network_error_still_aborts(market):
    """反例: 参数错误一类的非瞬时故障不得被当成网络抖动重试, 必须立刻中止"""
    daily, _, _, _ = market
    failed = result_set(FIELDS, [], error="10004006")
    failed.error_msg = "参数错误"
    daily.side_effect = [failed, result_set(FIELDS, [krow()])]
    with patch.object(bstock, "relogin") as relogin:
        with pytest.raises(bstock.BaoQueryError):
            bstock.fetch_daily_data_by_date(TARGETS, "2026-09-01", "2026-09-03")
    relogin.assert_not_called()


def test_adjust_missing_by_date_method_reports_clear_error():
    """反例: adjust 走按日分支但 bstock 模块缺 fetch_adjust_factors_by_date 时,
    守卫按方法名报错返回 1, 不漏到笼统的兜底 except"""
    args = adjust.build_parser().parse_args(["-b", "20260901", "-e", "20260904"])
    args.date_range_only = True
    source = MagicMock(spec=["fetch_adjust_factors"])      # 旧版模块: 只有逐股方法
    with ExitStack() as stack:
        stack.enter_context(patch.object(adjust, "parse_arguments", return_value=args))
        stack.enter_context(patch.object(adjust, "check_parameters", return_value=True))
        util = stack.enter_context(patch.object(adjust, "myutil"))
        db = stack.enter_context(patch.object(adjust, "dbutil"))
        util.trans_datestr_format.side_effect = ["2026-09-01", "2026-09-04"]
        util.import_source_module.return_value = source
        db.get_candidate_codes.return_value = TARGETS
        assert adjust.main() == 1
        source.fetch_adjust_factors.assert_not_called()
