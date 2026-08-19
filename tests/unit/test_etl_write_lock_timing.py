# 修改记录:
#   2026-08-19  Claude  新增：断言三个下载型 ETL 在下载完成之后才获取写连接
"""
写锁获取时机契约

DuckDB 是单写者。下载阶段耗时最长、也是唯一会卡死的阶段，若此时已持有写连接：
  1. 外部 kill 会中断一个持锁进程；
  2. 该进程存活期间，其他写入者全被挡在门外。

因此三个下载型 ETL 必须遵守同一顺序: **先下载，后取写连接**。
本文件用调用顺序断言这条契约，防止有人把 get_connection() 挪回去。

对应文档 2.3 结论 7/8/8b 与 5.5 kill 策略表。
"""
import argparse
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import adjust, fetch_index, import_daily


def _args(**kwargs) -> argparse.Namespace:
    base = {"begin": "20260817", "end": "20260817",
            "exchanges": ["all"], "codes": None, "source": "bstock"}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _df() -> pd.DataFrame:
    return pd.DataFrame({"code": ["600519.SH"]})


def _trace(order: list, label: str, result):
    """构造一个把调用顺序记进 order 的 side_effect"""
    def _inner(*args, **kwargs):
        order.append(label)
        return result
    return _inner


# ── import_daily：原本就正确，加测试防止退化 ──────────────────────────────────

def test_import_daily_fetches_before_acquiring_write_connection():
    """正例: 下载在取写连接之前"""
    order: list[str] = []
    source = MagicMock(spec=["fetch_batch_data"])
    source.fetch_batch_data.side_effect = _trace(order, "fetch", (_df(), _df()))

    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        dbutil.get_connection.side_effect = _trace(order, "connect", MagicMock())
        myutil.import_source_module.return_value = source

        assert import_daily.main() == 0

    assert order == ["fetch", "connect"], f"实际顺序: {order}"


def test_import_daily_print_only_never_opens_write_connection():
    """正例: 干跑全程不取写连接"""
    order: list[str] = []
    source = MagicMock(spec=["fetch_batch_data"])
    source.fetch_batch_data.side_effect = _trace(order, "fetch", (_df(), _df()))

    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(print_only=True)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        dbutil.get_connection.side_effect = _trace(order, "connect", MagicMock())
        myutil.import_source_module.return_value = source

        assert import_daily.main() == 0

    assert order == ["fetch"], f"干跑不应取写连接, 实际顺序: {order}"


# ── adjust：S3 修正的对象 ──────────────────────────────────────────────────────

def test_adjust_fetches_before_acquiring_write_connection():
    """正例(S3 修正): 复权因子下载完成后才取写连接"""
    order: list[str] = []
    source = MagicMock(spec=["fetch_adjust_factors"])
    source.fetch_adjust_factors.side_effect = _trace(order, "fetch", _df())

    with patch.object(adjust, "myutil") as myutil, \
         patch.object(adjust, "dbutil") as dbutil, \
         patch.object(adjust, "process_and_save_adjust_factors"), \
         patch.object(adjust, "parse_arguments", return_value=_args()), \
         patch.object(adjust, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        dbutil.get_connection.side_effect = _trace(order, "connect", MagicMock())
        myutil.import_source_module.return_value = source

        assert adjust.main() == 0

    assert order == ["fetch", "connect"], f"实际顺序: {order}"


def test_adjust_download_failure_leaves_no_write_connection():
    """反例: 下载阶段就失败时, 根本不该开过写连接"""
    order: list[str] = []
    source = MagicMock(spec=["fetch_adjust_factors"])
    source.fetch_adjust_factors.side_effect = ConnectionError("数据源连接中断")

    with patch.object(adjust, "myutil") as myutil, \
         patch.object(adjust, "dbutil") as dbutil, \
         patch.object(adjust, "process_and_save_adjust_factors"), \
         patch.object(adjust, "parse_arguments", return_value=_args()), \
         patch.object(adjust, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        dbutil.get_connection.side_effect = _trace(order, "connect", MagicMock())
        myutil.import_source_module.return_value = source

        assert adjust.main() == 1

    assert order == [], f"下载失败不应取过写连接, 实际: {order}"


# ── fetch_index：S3 修正的对象 ────────────────────────────────────────────────

def test_fetch_index_fetches_before_acquiring_write_connection():
    """正例(S3 修正): 指数下载完成后才取写连接"""
    order: list[str] = []
    source = MagicMock(spec=["fetch_batch_index"])
    source.fetch_batch_index.side_effect = _trace(order, "fetch", _df())

    with patch.object(fetch_index, "myutil") as myutil, \
         patch.object(fetch_index, "dbutil") as dbutil, \
         patch.object(fetch_index, "parse_arguments", return_value=_args()), \
         patch.object(fetch_index, "check_parameters", return_value=True):
        dbutil.get_candidate_index.return_value = [("000001", "SH")]
        dbutil.get_connection.side_effect = _trace(order, "connect", MagicMock())
        myutil.import_source_module.return_value = source

        assert fetch_index.main() == 0

    assert order == ["fetch", "connect"], f"实际顺序: {order}"


def test_fetch_index_download_failure_leaves_no_write_connection():
    """反例: 下载阶段就失败时, 根本不该开过写连接"""
    order: list[str] = []
    source = MagicMock(spec=["fetch_batch_index"])
    source.fetch_batch_index.side_effect = ConnectionError("数据源连接中断")

    with patch.object(fetch_index, "myutil") as myutil, \
         patch.object(fetch_index, "dbutil") as dbutil, \
         patch.object(fetch_index, "parse_arguments", return_value=_args()), \
         patch.object(fetch_index, "check_parameters", return_value=True):
        dbutil.get_candidate_index.return_value = [("000001", "SH")]
        dbutil.get_connection.side_effect = _trace(order, "connect", MagicMock())
        myutil.import_source_module.return_value = source

        assert fetch_index.main() == 1

    assert order == [], f"下载失败不应取过写连接, 实际: {order}"


# ── 三者一致性 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module_name", ["adjust", "fetch_index", "import_daily"])
def test_write_connection_is_not_first_statement_in_try(module_name):
    """正例: 源码层面确认 get_connection 不再是 try 块的第一句"""
    import inspect
    module = {"adjust": adjust, "fetch_index": fetch_index, "import_daily": import_daily}[module_name]
    source = inspect.getsource(module.main)
    fetch_pos = min((source.index(f"module.{m}(")
                     for m in ("fetch_adjust_factors", "fetch_batch_index", "fetch_batch_data")
                     if f"module.{m}(" in source), default=-1)
    conn_pos = source.index("dbutil.get_connection(is_read_only=False)")
    assert fetch_pos != -1, f"{module_name} 未找到下载调用"
    assert fetch_pos < conn_pos, f"{module_name} 仍在下载前获取写连接"
