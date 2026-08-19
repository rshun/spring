# 修改记录:
#   2026-08-19  Claude  新增：验证 6 个 ETL 的 main() 退出码契约(0成功/1失败)
"""
ETL 退出码契约测试

约定: main() 返回 0 表示成功, 1 表示失败; __main__ 块用 sys.exit(main()) 传出。
外部调度方(cron / MCP)据此判定成败, 因此「失败必须返回非 0」是硬要求——
返回 0 掩盖失败, 是本测试要防的主要回归。

本文件为纯逻辑测试: 数据源、数据库访问全部 mock, 不走网络也不碰真实库。
"""
import argparse
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import adjust, fetch_index, fill_shares, fill_volratio, import_daily, update_limit

ETL_MODULES = [adjust, fetch_index, fill_shares, fill_volratio, import_daily, update_limit]


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _args(**kwargs) -> argparse.Namespace:
    base = {"begin": "20260817", "end": "20260817", "exchanges": ["all"], "codes": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _df() -> pd.DataFrame:
    """非空 DataFrame, 用于走通「有数据 → 写库」分支"""
    return pd.DataFrame({"code": ["600519.SH"]})


def _source(method_name: str, return_value):
    """构造只带指定方法的假数据源模块"""
    module = MagicMock(spec=[method_name])
    getattr(module, method_name).return_value = return_value
    return module


# ── 契约自检: 签名与 __main__ 块 ───────────────────────────────────────────────

@pytest.mark.parametrize("module", ETL_MODULES, ids=lambda m: m.__name__)
def test_main_annotated_as_int(module):
    """正例: 6 个 main() 的返回标注必须是 int, 防止退化回 None"""
    assert inspect.signature(module.main).return_annotation is int


@pytest.mark.parametrize("module", ETL_MODULES, ids=lambda m: m.__name__)
def test_main_exit_code_propagated(module):
    """正例: __main__ 块必须用 sys.exit(main()) 把退出码传给调用方"""
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "sys.exit(main())" in source
    assert "\n    main()\n" not in source


# ── import_daily ──────────────────────────────────────────────────────────────

def test_import_daily_success_returns_0():
    """正例: 正常下载并写库 → 0"""
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.return_value = _source("fetch_batch_data", (_df(), _df()))

        assert import_daily.main() == 0
        dbutil.save_daily_to_db.assert_called_once()


def test_import_daily_print_only_returns_0():
    """正例: 干跑不写库, 但干跑本身是成功的 → 0"""
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=True)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.return_value = _source("fetch_batch_data", (_df(), _df()))

        assert import_daily.main() == 0
        dbutil.get_connection.assert_not_called()


def test_import_daily_invalid_params_returns_1():
    """反例: 参数校验不通过 → 1"""
    with patch.object(import_daily, "myutil"), \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=False):
        assert import_daily.main() == 1


def test_import_daily_no_candidates_returns_1():
    """反例: 没有候选股票, 等于活没干成 → 1, 不得报成功"""
    with patch.object(import_daily, "myutil"), \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = []
        assert import_daily.main() == 1


def test_import_daily_source_missing_method_returns_1():
    """反例: 数据源模块没有 fetch_batch_data → 1"""
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.return_value = MagicMock(spec=[])
        assert import_daily.main() == 1


def test_import_daily_fetch_raises_returns_1():
    """反例: 下载阶段抛异常 → 1"""
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        module = _source("fetch_batch_data", None)
        module.fetch_batch_data.side_effect = ConnectionError("数据源连接中断")
        myutil.import_source_module.return_value = module

        assert import_daily.main() == 1


def test_import_daily_save_raises_returns_1():
    """反例: 写库阶段抛异常 → 1"""
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="bstock", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.return_value = _source("fetch_batch_data", (_df(), _df()))
        dbutil.save_daily_to_db.side_effect = RuntimeError("写库失败")

        assert import_daily.main() == 1


def test_import_daily_import_error_returns_1():
    """反例: 数据源模块不存在 → 1"""
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments",
                      return_value=_args(source="nosuch", print_only=False)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.side_effect = ImportError("no module")

        assert import_daily.main() == 1


# ── adjust ────────────────────────────────────────────────────────────────────

def test_adjust_success_returns_0():
    """正例: 正常获取复权因子 → 0"""
    with patch.object(adjust, "myutil") as myutil, \
         patch.object(adjust, "dbutil") as dbutil, \
         patch.object(adjust, "process_and_save_adjust_factors") as save, \
         patch.object(adjust, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(adjust, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.return_value = _source("fetch_adjust_factors", _df())

        assert adjust.main() == 0
        save.assert_called_once()


def test_adjust_invalid_params_returns_1():
    """反例: 参数校验不通过 → 1"""
    with patch.object(adjust, "myutil"), \
         patch.object(adjust, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(adjust, "check_parameters", return_value=False):
        assert adjust.main() == 1


def test_adjust_no_candidates_returns_1():
    """反例: 没有候选股票 → 1"""
    with patch.object(adjust, "myutil"), \
         patch.object(adjust, "dbutil") as dbutil, \
         patch.object(adjust, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(adjust, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = []
        assert adjust.main() == 1


def test_adjust_source_missing_method_returns_1():
    """反例: 数据源模块没有 fetch_adjust_factors → 1"""
    with patch.object(adjust, "myutil") as myutil, \
         patch.object(adjust, "dbutil") as dbutil, \
         patch.object(adjust, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(adjust, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        myutil.import_source_module.return_value = MagicMock(spec=[])
        assert adjust.main() == 1


def test_adjust_fetch_raises_returns_1():
    """反例: 下载阶段抛异常 → 1"""
    with patch.object(adjust, "myutil") as myutil, \
         patch.object(adjust, "dbutil") as dbutil, \
         patch.object(adjust, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(adjust, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        module = _source("fetch_adjust_factors", None)
        module.fetch_adjust_factors.side_effect = ConnectionError("数据源连接中断")
        myutil.import_source_module.return_value = module

        assert adjust.main() == 1


# ── fetch_index ───────────────────────────────────────────────────────────────

def test_fetch_index_success_returns_0():
    """正例: 正常获取指数行情 → 0"""
    with patch.object(fetch_index, "myutil") as myutil, \
         patch.object(fetch_index, "dbutil") as dbutil, \
         patch.object(fetch_index, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(fetch_index, "check_parameters", return_value=True):
        dbutil.get_candidate_index.return_value = [("000001", "SH")]
        myutil.import_source_module.return_value = _source("fetch_batch_index", _df())

        assert fetch_index.main() == 0
        dbutil.save_index_to_db.assert_called_once()


def test_fetch_index_no_candidates_returns_1():
    """反例: 没有候选指数 → 1"""
    with patch.object(fetch_index, "myutil"), \
         patch.object(fetch_index, "dbutil") as dbutil, \
         patch.object(fetch_index, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(fetch_index, "check_parameters", return_value=True):
        dbutil.get_candidate_index.return_value = []
        assert fetch_index.main() == 1


def test_fetch_index_fetch_raises_returns_1():
    """反例: 下载阶段抛异常 → 1"""
    with patch.object(fetch_index, "myutil") as myutil, \
         patch.object(fetch_index, "dbutil") as dbutil, \
         patch.object(fetch_index, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(fetch_index, "check_parameters", return_value=True):
        dbutil.get_candidate_index.return_value = [("000001", "SH")]
        module = _source("fetch_batch_index", None)
        module.fetch_batch_index.side_effect = ConnectionError("数据源连接中断")
        myutil.import_source_module.return_value = module

        assert fetch_index.main() == 1


# ── fill_volratio ─────────────────────────────────────────────────────────────

def test_fill_volratio_success_returns_0():
    """正例: 全市场补齐量比 → 0"""
    with patch.object(fill_volratio, "myutil"), \
         patch.object(fill_volratio, "dbutil") as dbutil, \
         patch.object(fill_volratio, "parse_arguments", return_value=_args(forcerun=False)), \
         patch.object(fill_volratio, "check_parameters", return_value=True):
        assert fill_volratio.main() == 0
        dbutil.fill_daily_basic_volume_ratio.assert_called_once()


def test_fill_volratio_no_candidates_returns_1():
    """反例: 显式指定的代码一个都解析不出来 → 1"""
    with patch.object(fill_volratio, "myutil"), \
         patch.object(fill_volratio, "dbutil") as dbutil, \
         patch.object(fill_volratio, "parse_arguments",
                      return_value=_args(codes=["999999"], forcerun=False)), \
         patch.object(fill_volratio, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = []
        assert fill_volratio.main() == 1


def test_fill_volratio_fill_raises_returns_1():
    """反例: 补齐过程抛异常 → 1"""
    with patch.object(fill_volratio, "myutil"), \
         patch.object(fill_volratio, "dbutil") as dbutil, \
         patch.object(fill_volratio, "parse_arguments", return_value=_args(forcerun=False)), \
         patch.object(fill_volratio, "check_parameters", return_value=True):
        dbutil.fill_daily_basic_volume_ratio.side_effect = RuntimeError("SQL 执行失败")
        assert fill_volratio.main() == 1


# ── update_limit ──────────────────────────────────────────────────────────────

def test_update_limit_success_returns_0():
    """正例: 全市场补齐涨跌停 → 0"""
    with patch.object(update_limit, "myutil"), \
         patch.object(update_limit, "dbutil") as dbutil, \
         patch.object(update_limit, "parse_arguments", return_value=_args(forcerun=False)), \
         patch.object(update_limit, "check_parameters", return_value=True):
        assert update_limit.main() == 0
        dbutil.update_price_limits_by_range.assert_called_once()


def test_update_limit_no_candidates_returns_1():
    """反例: 显式指定的代码一个都解析不出来 → 1"""
    with patch.object(update_limit, "myutil"), \
         patch.object(update_limit, "dbutil") as dbutil, \
         patch.object(update_limit, "parse_arguments",
                      return_value=_args(codes=["999999"], forcerun=False)), \
         patch.object(update_limit, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = []
        assert update_limit.main() == 1


def test_update_limit_update_raises_returns_1():
    """反例: 更新过程抛异常 → 1"""
    with patch.object(update_limit, "myutil"), \
         patch.object(update_limit, "dbutil") as dbutil, \
         patch.object(update_limit, "parse_arguments", return_value=_args(forcerun=False)), \
         patch.object(update_limit, "check_parameters", return_value=True):
        dbutil.update_price_limits_by_range.side_effect = RuntimeError("SQL 执行失败")
        assert update_limit.main() == 1


# ── fill_shares ───────────────────────────────────────────────────────────────

def test_fill_shares_success_returns_0():
    """正例: 全市场回填股本 → 0"""
    with patch.object(fill_shares, "myutil"), \
         patch.object(fill_shares, "dbutil") as dbutil, \
         patch.object(fill_shares, "parse_arguments", return_value=_args(forcerun=False)), \
         patch.object(fill_shares, "check_parameters", return_value=True):
        assert fill_shares.main() == 0
        dbutil.fill_daily_basic_shares.assert_called_once()
        dbutil.fill_daily_basic_mv.assert_called_once()


def test_fill_shares_no_candidates_returns_1():
    """反例: 显式指定的代码一个都解析不出来 → 1"""
    with patch.object(fill_shares, "myutil"), \
         patch.object(fill_shares, "dbutil") as dbutil, \
         patch.object(fill_shares, "parse_arguments",
                      return_value=_args(codes=["999999"], forcerun=False)), \
         patch.object(fill_shares, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = []
        assert fill_shares.main() == 1


def test_fill_shares_fill_raises_returns_1():
    """反例: 回填过程抛异常 → 1"""
    with patch.object(fill_shares, "myutil"), \
         patch.object(fill_shares, "dbutil") as dbutil, \
         patch.object(fill_shares, "parse_arguments", return_value=_args(forcerun=False)), \
         patch.object(fill_shares, "check_parameters", return_value=True):
        dbutil.fill_daily_basic_shares.side_effect = RuntimeError("SQL 执行失败")
        assert fill_shares.main() == 1
