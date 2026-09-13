"""sync_suspension 的参数面与纯函数逻辑(不触网、不连库)。"""
import logging

import pandas as pd

from etl import sync_suspension


def _actions(module):
    return {a.dest: a for a in module.build_parser()._actions if a.dest != "help"}


def test_build_parser_has_common_flags():
    """正例: MCP 通用调用面 -b/-e/-c/-x 齐全"""
    actions = _actions(sync_suspension)
    for dest, short in {"begin": "-b", "end": "-e",
                        "codes": "-c", "exchanges": "-x"}.items():
        assert dest in actions
        assert short in actions[dest].option_strings


def test_exchanges_choices_match_contract():
    """正例: 交易所枚举与其他 ETL 一致"""
    action = _actions(sync_suspension)["exchanges"]
    assert list(action.choices) == ["sh", "sz", "bj", "all"]
    assert action.default == ["all"]


def test_source_defaults_to_akstock():
    """正例: -s 默认 akstock"""
    action = _actions(sync_suspension)["source"]
    assert action.default == "akstock"
    assert "akstock" in action.choices


def test_build_parser_has_no_side_effect():
    """反例: build_parser 不得触发解析, 否则自省会读到 pytest 的 argv"""
    sync_suspension.build_parser()


def test_requested_exchanges_bj_only_is_empty():
    """反例: 只要北交所 -> 沪深集合为空, 调用方据此空跑并告警"""
    assert sync_suspension.requested_exchanges(["bj"]) == set()


def test_requested_exchanges_all_covers_sh_sz():
    """正例: all 覆盖沪深(不含北交所, 本源不支持)"""
    assert sync_suspension.requested_exchanges(["all"]) == {"SH", "SZ"}


def test_filter_by_codes_keeps_only_requested():
    """正例: -c 在本地按裸码过滤(接口不支持按代码查)"""
    df = pd.DataFrame({"symbol": ["600001", "000002", "300003"],
                       "name": ["A", "B", "C"]})
    out = sync_suspension.filter_by_codes(df, ["600001", "300003"])
    assert out["symbol"].tolist() == ["600001", "300003"]


def test_filter_by_codes_none_returns_all():
    """正例: 不传 -c 返回全量"""
    df = pd.DataFrame({"symbol": ["600001", "000002"], "name": ["A", "B"]})
    assert len(sync_suspension.filter_by_codes(df, [])) == 2


def test_filter_by_codes_no_match_returns_empty():
    """反例: 指定代码全不在帧内 -> 空帧而非全量"""
    df = pd.DataFrame({"symbol": ["600001", "000002"], "name": ["A", "B"]})
    assert sync_suspension.filter_by_codes(df, ["999999"]).empty


def _suspension_df(symbols, suspend_times, resume_deadlines):
    """构造停牌帧: 列全用等长数组, 不混标量, 避免整套 pytest 跑时只剩第一列"""
    return pd.DataFrame({
        "symbol": symbols,
        "suspend_time": pd.to_datetime(suspend_times),
        "resume_deadline": pd.to_datetime(resume_deadlines),
    })


def test_filter_suspended_on_keeps_open_ended_suspension():
    """正例: suspend_time <= D 且 resume_deadline 为空(无截止日) -> 保留"""
    df = _suspension_df(["600000"], ["2026-09-10"], [None])
    out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    assert out["symbol"].tolist() == ["600000"]


def test_filter_suspended_on_keeps_deadline_on_or_after_d():
    """正例: suspend_time <= D 且 resume_deadline >= D(含 == D 边界) -> 保留"""
    df = _suspension_df(["600000", "600001"],
                        ["2026-09-10", "2026-09-10"],
                        ["2026-09-11", "2026-09-12"])
    out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    assert out["symbol"].tolist() == ["600000", "600001"]


def test_filter_suspended_on_excludes_future_suspension():
    """反例(未来才停牌): suspend_time > D -> 排除。真实案例: 600301 09-14 停牌, 查询日 09-11"""
    df = _suspension_df(["600301"], ["2026-09-14"], [None])
    out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    assert out.empty


def test_filter_suspended_on_excludes_already_resumed():
    """反例(已复牌): resume_deadline < D -> 排除。真实案例: 002998 截止 09-10, 查询日 09-11"""
    df = _suspension_df(["002998"], ["2026-09-01"], ["2026-09-10"])
    out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    assert out.empty


def test_filter_suspended_on_keeps_intraday_suspend_time():
    """反例防护(盘中时刻): suspend_time 是 D 当天 10:30:00 -> 日期部分等于 D, 必须保留"""
    df = _suspension_df(["600000"], ["2026-09-11 10:30:00"], [None])
    out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    assert out["symbol"].tolist() == ["600000"]


def test_filter_suspended_on_empty_df_stays_empty():
    """边界: 空帧进、空帧出, 不抛错"""
    df = _suspension_df([], [], [])
    out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    assert out.empty


def test_filter_suspended_on_none_passthrough():
    """边界: df 为 None 时原样返回, 不抛错"""
    assert sync_suspension.filter_suspended_on(None, "2026-09-11") is None


def test_filter_suspended_on_excludes_nat_suspend_time_with_warning(caplog):
    """反例(NaT 静默丢弃, BUG-011 同类): suspend_time 缺失/无法解析 -> 排除，
    且必须打 WARNING 说明条数，不能像 BUG-011 那样无声丢弃数据

    直接把 caplog.handler 挂到目标 logger 上而不是靠 caplog.at_level 依赖向 root
    传播: 全量 pytest 跑起来后, etl.sync_finance 的 run_sync() 会真调一次
    myutil.configure_etl_logging()，把祖先 "etl" logger 的 propagate 永久置 False
    (进程内只配置一次)，届时任何依赖传播到 root 的 caplog 用例都会静默失效——这里
    直接挂 handler 可以绕开这个已知的跨用例日志污染。
    """
    target_logger = logging.getLogger("etl.sync_suspension")
    target_logger.addHandler(caplog.handler)
    orig_level = target_logger.level
    target_logger.setLevel(logging.WARNING)
    try:
        df = _suspension_df(["600000", "600001"],
                            [None, "2026-09-01"],
                            [None, None])
        out = sync_suspension.filter_suspended_on(df, "2026-09-11")
    finally:
        target_logger.removeHandler(caplog.handler)
        target_logger.setLevel(orig_level)
    assert out["symbol"].tolist() == ["600001"]
    assert "1 条记录" in caplog.text
    assert "suspend_time" in caplog.text


def test_classify_write_result_written_positive_is_ok():
    """正例: 写入 > 0 -> ok"""
    assert sync_suspension.classify_write_result(api_empty=False, written=5) == "ok"
    assert sync_suspension.classify_write_result(api_empty=True, written=5) == "ok"


def test_classify_write_result_api_empty():
    """反例(接口空帧): 过滤前接口就没数据 -> api_empty, 应计入部分成功"""
    assert sync_suspension.classify_write_result(api_empty=True, written=0) == "api_empty"


def test_classify_write_result_filtered_empty():
    """反例(过滤后为空): 接口有数据、按 filter_suspended_on 过滤后为 0
    -> filtered_empty, 不该计入部分成功"""
    assert sync_suspension.classify_write_result(api_empty=False, written=0) == "filtered_empty"
