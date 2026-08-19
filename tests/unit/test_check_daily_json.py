# 修改记录:
#   2026-08-19  Claude  新建：--json 出口的截断逻辑与 CLI 契约的正反例
"""check_daily --json 出口的正反例。

这个出口是 etl-quant-mcp 的 check_data_gaps 的唯一数据来源，两条要害：

  * **stdout 只能有 JSON**——日志默认走 stdout，`--json` 必须把它改到 stderr，
    否则调用方拿到的是日志与 JSON 混在一起的东西，根本解析不了；
  * **明细必须可截断**——全市场缺一天就是 5000+ 条，不设限会灌爆调用方的上下文。
"""
import inspect

import pytest

from tools import check_daily
from tools.check_daily import (
    DEFAULT_JSON_MAX_DETAIL,
    STATUS_COMPLETE,
    STATUS_ERROR,
    STATUS_GAPS_FOUND,
    _truncate_detail,
    build_parser,
)


def _check(n: int, label: str = "日线数据") -> dict:
    return {
        "label": label,
        "table": "STOCK_DAILY",
        "missing": n,
        "gap_dates": [{"date": "2026-08-17", "expected": n, "actual": 0, "missing": n}],
        "missing_codes": [{"date": "2026-08-17", "code": f"{600000+i}.SH", "name": f"股票{i}"}
                          for i in range(n)],
        "csv_path": "/tmp/x.csv",
    }


# ── 截断 ──────────────────────────────────────────────────────────────────────

def test_truncate_not_needed_when_under_limit():
    """正例: 未超限时不截断，并如实报出总数"""
    checks = [_check(5)]
    assert _truncate_detail(checks, 100) is False
    assert len(checks[0]["missing_codes"]) == 5
    assert checks[0]["missing_codes_truncated"] is False
    assert checks[0]["missing_codes_total"] == 5


def test_truncate_applies_when_over_limit():
    """反例(关键): 超限时必须截断并标记，否则会灌爆调用方上下文"""
    checks = [_check(500)]
    assert _truncate_detail(checks, 10) is True
    assert len(checks[0]["missing_codes"]) == 10
    assert checks[0]["missing_codes_truncated"] is True
    assert checks[0]["missing_codes_total"] == 500, "截断后仍须如实报出原始总数"


def test_truncate_budget_is_shared_across_checks():
    """反例(关键): 预算是跨检查项共享的，不能每项各给一份上限"""
    checks = [_check(8, "日线"), _check(8, "复权因子"), _check(8, "指标")]
    assert _truncate_detail(checks, 10) is True
    total = sum(len(c["missing_codes"]) for c in checks)
    assert total == 10, f"三项合计应受同一预算约束，实得 {total}"


def test_truncate_marks_only_the_overflowing_check():
    """正例: 预算耗尽的那一项之后的检查项也应被清空并标记"""
    checks = [_check(10, "日线"), _check(5, "复权因子")]
    _truncate_detail(checks, 10)
    assert checks[0]["missing_codes_truncated"] is False   # 恰好用完，未被切
    assert checks[1]["missing_codes"] == []
    assert checks[1]["missing_codes_truncated"] is True


def test_truncate_never_touches_gap_dates():
    """正例(关键): gap_dates 不截断——它每项只是几个数字，且正是「哪几天要补」的答案"""
    checks = [_check(500)]
    before = list(checks[0]["gap_dates"])
    _truncate_detail(checks, 1)
    assert checks[0]["gap_dates"] == before


def test_truncate_with_empty_checks_is_noop():
    """反例(边界): 空输入不该崩"""
    assert _truncate_detail([], 10) is False


def test_truncate_negative_limit_disables_truncation():
    """反例(边界): 负数上限视为不限制"""
    checks = [_check(500)]
    assert _truncate_detail(checks, -1) is False
    assert len(checks[0]["missing_codes"]) == 500


# ── CLI 契约 ──────────────────────────────────────────────────────────────────

def _actions() -> dict:
    return {a.dest: a for a in build_parser()._actions if a.dest != "help"}


def test_json_flag_exists_and_defaults_off():
    """正例: --json 默认关闭，不改变既有调用方的行为"""
    action = _actions()["json"]
    assert action.default is False
    assert "-j" in action.option_strings


def test_json_max_detail_has_sane_default():
    """正例: 截断上限有默认值，调用方不传也安全"""
    assert _actions()["json_max_detail"].default == DEFAULT_JSON_MAX_DETAIL
    assert DEFAULT_JSON_MAX_DETAIL > 0


def test_common_flags_preserved():
    """正例: 原有参数面不得因新增 --json 而改变"""
    actions = _actions()
    for dest, short in (("begin", "-b"), ("end", "-e"), ("codes", "-c"),
                        ("exchanges", "-x"), ("include_index", "-i"),
                        ("forcerun", "-f")):
        assert dest in actions, f"缺少参数 {dest}"
        assert short in actions[dest].option_strings


def test_build_parser_does_not_parse():
    """反例: build_parser() 只构造不解析，否则会读到 pytest 的 argv"""
    build_parser()   # 不抛 SystemExit 即通过


# ── 日志流：stdout 必须留给 JSON ──────────────────────────────────────────────

def test_main_sends_logs_to_stderr_when_json(monkeypatch):
    """正例(要害): --json 时日志必须改走 stderr，否则 stdout 不是合法 JSON"""
    import sys
    captured = {}

    def fake_configure(console_stream=None):
        captured["stream"] = console_stream

    monkeypatch.setattr(check_daily.myutil, "configure_etl_logging", fake_configure)
    monkeypatch.setattr(check_daily, "parse_arguments",
                        lambda: _fake_args(json=True))
    monkeypatch.setattr(check_daily, "check_parameters", lambda *a: False)

    check_daily.main()
    assert captured["stream"] is sys.stderr


def test_main_keeps_stdout_logging_without_json(monkeypatch):
    """正例: 不带 --json 时日志仍走默认流，既有用法完全不受影响"""
    captured = {}

    def fake_configure(console_stream=None):
        captured["stream"] = console_stream

    monkeypatch.setattr(check_daily.myutil, "configure_etl_logging", fake_configure)
    monkeypatch.setattr(check_daily, "parse_arguments",
                        lambda: _fake_args(json=False))
    monkeypatch.setattr(check_daily, "check_parameters", lambda *a: False)

    check_daily.main()
    assert captured["stream"] is None


def test_param_failure_still_emits_json(monkeypatch, capsys):
    """反例(关键): 参数校验失败时也必须吐 JSON，否则调用方拿到空 stdout 无从判断"""
    import json as json_mod
    monkeypatch.setattr(check_daily.myutil, "configure_etl_logging", lambda **k: None)
    monkeypatch.setattr(check_daily, "parse_arguments", lambda: _fake_args(json=True))
    monkeypatch.setattr(check_daily, "check_parameters", lambda *a: False)

    code = check_daily.main()
    payload = json_mod.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["status"] == STATUS_ERROR
    assert payload["exit_code"] == 2


def test_status_constants_are_distinct():
    """正例: 三个状态互不相同，调用方据此分支"""
    assert len({STATUS_COMPLETE, STATUS_GAPS_FOUND, STATUS_ERROR}) == 3


def _fake_args(json: bool):
    import argparse
    return argparse.Namespace(
        begin="20260817", end="20260817", codes=None, exchanges=["all"],
        include_index=False, forcerun=False, json=json,
        json_max_detail=DEFAULT_JSON_MAX_DETAIL,
    )
