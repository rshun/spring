import sys
import pytest
from unittest.mock import patch
from pathlib import Path

from util.myutil import (
    trans_datestr_format,
    get_today,
    get_yesterday,
    import_source_module,
    get_lday_path,
)


# ── trans_datestr_format ──────────────────────────────────────────────────────

def test_trans_normal():
    assert trans_datestr_format("20230101") == "2023-01-01"


def test_trans_year_boundary():
    assert trans_datestr_format("20231231") == "2023-12-31"


def test_trans_invalid_text():
    with pytest.raises(ValueError):
        trans_datestr_format("abc")


def test_trans_invalid_month():
    with pytest.raises(ValueError):
        trans_datestr_format("20231301")


def test_trans_invalid_day():
    with pytest.raises(ValueError):
        trans_datestr_format("20230230")  # 2月无30日


def test_trans_wrong_separator_format():
    with pytest.raises(ValueError):
        trans_datestr_format("2023-01-01")  # 期望 YYYYMMDD，不是 YYYY-MM-DD


def test_trans_error_message_contains_input():
    with pytest.raises(ValueError, match="20230230"):
        trans_datestr_format("20230230")


# ── get_today / get_yesterday ─────────────────────────────────────────────────

def test_get_today_is_8_digits():
    today = get_today()
    assert len(today) == 8
    assert today.isdigit()


def test_get_yesterday_is_8_digits():
    yesterday = get_yesterday()
    assert len(yesterday) == 8
    assert yesterday.isdigit()


def test_yesterday_before_today():
    """黑盒：不 mock 时间，直接断言大小关系。"""
    assert get_yesterday() < get_today()


# ── import_source_module ──────────────────────────────────────────────────────

def test_import_short_name():
    mod = import_source_module("tdx")
    assert hasattr(mod, "fetch_batch_data")


def test_import_full_path_same_module():
    """黑盒：两种写法返回同一模块对象。"""
    mod1 = import_source_module("tdx")
    mod2 = import_source_module("datasource.tdx")
    assert mod1 is mod2


def test_import_empty_string_raises_value_error():
    with pytest.raises(ValueError):
        import_source_module("")


def test_import_nonexistent_raises_import_error():
    with pytest.raises(ImportError) as exc_info:
        import_source_module("nonexistent_xyz_module")
    assert "nonexistent_xyz_module" in str(exc_info.value)


def test_import_error_lists_available_modules():
    """错误消息应包含可用模块列表。"""
    with pytest.raises(ImportError) as exc_info:
        import_source_module("nonexistent_xyz_module")
    error_msg = str(exc_info.value)
    assert "tdx" in error_msg or "可用模块" in error_msg


# ── get_lday_path（Windows 专用）────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_lday_path_none_raises_value_error():
    with pytest.raises(ValueError):
        get_lday_path(None)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_lday_path_empty_string_raises_value_error():
    with pytest.raises(ValueError):
        get_lday_path("")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_get_lday_path_nonexistent_dir_raises_file_not_found():
    with patch("util.config.get_config", return_value={"local_paths": {"tdx_vipdoc": "C:\\nonexistent_xyz_dir"}}):
        with pytest.raises(FileNotFoundError):
            get_lday_path("sh")
