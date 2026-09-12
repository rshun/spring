"""比较器与落盘: set_diff / num_close / write_diff_csv / log_rows"""
import csv

import pytest

from util import checker


# ── set_diff ────────────────────────────────────────────────────────

def test_set_diff_reports_both_directions():
    """正例: 双向差异各自归位"""
    only_src, only_db = checker.set_diff({"a", "b", "c"}, {"b", "c", "d"})
    assert only_src == ["a"]
    assert only_db == ["d"]


def test_set_diff_identical_sets_empty():
    """正例: 完全一致 -> 两侧都空"""
    assert checker.set_diff({"a"}, {"a"}) == ([], [])


def test_set_diff_results_are_sorted():
    """正例: 输出必须有序, 否则 CSV 与日志每次跑顺序都变, 无法 diff"""
    only_src, _ = checker.set_diff({"c", "a", "b"}, set())
    assert only_src == ["a", "b", "c"]


def test_set_diff_empty_source_reports_all_db_rows():
    """反例: 源为空时全部库内键都进 only_in_db —— 正因如此,
    调用方必须先用 split_dates_by_source 判 source_missing, 否则整片误报。
    """
    only_src, only_db = checker.set_diff(set(), {"a", "b"})
    assert only_src == []
    assert only_db == ["a", "b"]


# ── num_close ───────────────────────────────────────────────────────

def test_num_close_within_tolerance():
    """正例: 容差内判等"""
    assert checker.num_close(45.67, 45.67, 1e-6) is True


def test_num_close_outside_tolerance():
    """正例: 超容差判不等"""
    assert checker.num_close(45.67, 45.66, 1e-6) is False


def test_num_close_relative_not_absolute():
    """正例: 容差是相对的, 大数值的同等绝对误差应判等"""
    assert checker.num_close(5.0e9, 5.0e9 * (1 + 1e-4), 1e-3) is True


@pytest.mark.parametrize("a, b", [(None, 1.0), (1.0, None), (None, None),
                                  (float("nan"), 1.0)])
def test_num_close_missing_side_returns_none(a, b):
    """反例: 任一侧缺失 -> 返回 None(无法比较), 不得当成相等"""
    assert checker.num_close(a, b, 1e-6) is None


def test_num_close_zero_baseline_uses_absolute():
    """反例: 基准为 0 时不能除零, 退化为绝对比较"""
    assert checker.num_close(0.0, 0.0, 1e-6) is True
    assert checker.num_close(0.0, 1.0, 1e-6) is False


# ── write_diff_csv ──────────────────────────────────────────────────

def test_write_diff_csv_creates_file(tmp_path, monkeypatch):
    """正例: 有差异时落盘, 内容含表头与数据行"""
    monkeypatch.setattr(checker, "CSV_DIR", tmp_path)
    path = checker.write_diff_csv("suspension", "20240426", "20240426",
                                  ["date", "code", "issue"],
                                  [("2024-04-26", "600001.SH", "库内标为正常交易")])
    assert path is not None
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["date", "code", "issue"]
    assert rows[1] == ["2024-04-26", "600001.SH", "库内标为正常交易"]


def test_write_diff_csv_no_rows_returns_none(tmp_path, monkeypatch):
    """反例: 无差异不落空文件, 返回 None"""
    monkeypatch.setattr(checker, "CSV_DIR", tmp_path)
    assert checker.write_diff_csv("suspension", "20240426", "20240426",
                                  ["date", "code", "issue"], []) is None
    assert list(tmp_path.iterdir()) == []


# ── log_rows ────────────────────────────────────────────────────────

def test_log_rows_emits_each_row(caplog):
    """正例: 每条异常逐行输出"""
    with caplog.at_level("WARNING"):
        checker.log_rows("停牌核对", ["2024-04-26  600001.SH  库内标为正常交易"])
    assert "600001.SH" in caplog.text


def test_log_rows_truncates_beyond_limit(caplog):
    """反例: 超阈值截断并提示看 CSV, 防某天整批错位刷爆日志"""
    rows = [f"2024-04-26  60{i:04d}.SH  库内标为正常交易" for i in range(300)]
    with caplog.at_level("WARNING"):
        checker.log_rows("停牌核对", rows)
    emitted = [r for r in caplog.text.splitlines() if "库内标为正常交易" in r]
    assert len(emitted) == checker.LOG_DETAIL_LIMIT
    assert "已截断" in caplog.text


def test_log_rows_empty_emits_nothing(caplog):
    """正例: 正确的一律不输出日志"""
    with caplog.at_level("WARNING"):
        checker.log_rows("停牌核对", [])
    assert caplog.text == ""
