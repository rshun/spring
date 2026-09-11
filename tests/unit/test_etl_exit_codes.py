# 修改记录:
#   2026-08-19  Claude  新增：验证 6 个 ETL 的 main() 退出码契约(0成功/1失败)
#   2026-09-10  Claude  纳入 fill_turnover（第 7 个，此前 main() 返回 None 且无 sys.exit）；
#                       新增三个 fill_* 的「库层抛错 → 1」用例
#   2026-09-11  Claude  纳入 trade_cal / sync_basic / sync_margin / sync_industry /
#                       sync_finance / init_db（此前 main() 吞异常后 return None，把库层的
#                       重抛重新吞掉，写库失败仍退出 0）
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

from etl import (adjust, fetch_index, fill_shares, fill_turnover, fill_volratio,
                 import_daily, init_db, sync_basic, sync_capital, sync_finance,
                 sync_industry, sync_margin, trade_cal, update_limit)

ETL_MODULES = [adjust, fetch_index, fill_shares, fill_turnover, fill_volratio,
               import_daily, update_limit,
               trade_cal, sync_basic, sync_margin, sync_industry, sync_finance,
               sync_capital]


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


def test_fill_volratio_passes_exchanges_through():
    """正例(本次修复): -x sh 必须传到 dbutil（此前只接受不生效, 静默全市场重算）"""
    with patch.object(fill_volratio, "myutil"), \
         patch.object(fill_volratio, "dbutil") as dbutil, \
         patch.object(fill_volratio, "parse_arguments",
                      return_value=_args(forcerun=False, exchanges=["sh"])), \
         patch.object(fill_volratio, "check_parameters", return_value=True):
        assert fill_volratio.main() == 0
        args, kwargs = dbutil.fill_daily_basic_volume_ratio.call_args
        assert args[3] == ["SH"]


# ── fill_turnover ─────────────────────────────────────────────────────────────

def _turnover_args(**kw):
    return _args(forcerun=False, overwrite=False, **kw)


def test_fill_turnover_success_returns_0():
    """正例: 正常回填 → 0（08-19 之前 main() 返回 None, 调用方无法判定）"""
    with patch.object(fill_turnover, "myutil"), \
         patch.object(fill_turnover, "dbutil") as dbutil, \
         patch.object(fill_turnover, "parse_arguments", return_value=_turnover_args()), \
         patch.object(fill_turnover, "check_parameters", return_value=True):
        dbutil.fill_daily_basic_turnover.return_value = 3
        assert fill_turnover.main() == 0
        dbutil.fill_daily_basic_turnover.assert_called_once()


def test_fill_turnover_invalid_params_returns_1():
    """反例: 参数校验失败 → 1"""
    with patch.object(fill_turnover, "myutil"), \
         patch.object(fill_turnover, "parse_arguments", return_value=_turnover_args()), \
         patch.object(fill_turnover, "check_parameters", return_value=False):
        assert fill_turnover.main() == 1


def test_fill_turnover_no_candidates_returns_1():
    """反例: -c 指定的代码不存在 → 1"""
    with patch.object(fill_turnover, "myutil"), \
         patch.object(fill_turnover, "dbutil") as dbutil, \
         patch.object(fill_turnover, "parse_arguments",
                      return_value=_turnover_args(codes=["NOSUCH"])), \
         patch.object(fill_turnover, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = []
        assert fill_turnover.main() == 1


def test_fill_turnover_fill_raises_returns_1():
    """反例: 库层抛错 → 1（dbutil 层现已重抛, 不再被吞成「更新 0 行」+ 退出 0）"""
    with patch.object(fill_turnover, "myutil"), \
         patch.object(fill_turnover, "dbutil") as dbutil, \
         patch.object(fill_turnover, "parse_arguments", return_value=_turnover_args()), \
         patch.object(fill_turnover, "check_parameters", return_value=True):
        dbutil.fill_daily_basic_turnover.side_effect = RuntimeError("SQL 执行失败")
        assert fill_turnover.main() == 1


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


# ── trade_cal ─────────────────────────────────────────────────────────────────

def test_trade_cal_success_returns_0():
    """正例: 取到日历并写库 → 0"""
    with patch.object(trade_cal, "myutil") as myutil, \
         patch.object(trade_cal, "dbutil") as dbutil, \
         patch.object(trade_cal, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(trade_cal, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = _source("fetch_sync_calendar", _df())
        assert trade_cal.main() == 0
        dbutil.save_calendar_to_db.assert_called_once()


def test_trade_cal_save_raises_returns_1():
    """反例(本次修复的回归点): save_calendar_to_db 重抛写库异常时，不得再被 main() 吞成 0"""
    with patch.object(trade_cal, "myutil") as myutil, \
         patch.object(trade_cal, "dbutil") as dbutil, \
         patch.object(trade_cal, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(trade_cal, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = _source("fetch_sync_calendar", _df())
        dbutil.save_calendar_to_db.side_effect = RuntimeError("写 TRADE_CAL 失败")
        assert trade_cal.main() == 1


def test_trade_cal_invalid_params_returns_1():
    """反例: 参数校验不过 → 1"""
    with patch.object(trade_cal, "myutil"), \
         patch.object(trade_cal, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(trade_cal, "check_parameters", return_value=False):
        assert trade_cal.main() == 1


def test_trade_cal_source_missing_method_returns_1():
    """反例: 数据源模块缺 fetch_sync_calendar → 1"""
    with patch.object(trade_cal, "myutil") as myutil, \
         patch.object(trade_cal, "dbutil"), \
         patch.object(trade_cal, "parse_arguments", return_value=_args(source="bstock")), \
         patch.object(trade_cal, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = MagicMock(spec=[])
        assert trade_cal.main() == 1


# ── sync_basic ────────────────────────────────────────────────────────────────

def test_sync_basic_success_returns_0():
    """正例: 取到基本信息并写库 → 0"""
    with patch.object(sync_basic, "myutil") as myutil, \
         patch.object(sync_basic, "dbutil") as dbutil, \
         patch.object(sync_basic, "parse_arguments",
                      return_value=_args(source="bstock", forcerun=False)), \
         patch.object(sync_basic, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = _source("fetch_stock_info", (_df(), _df()))
        assert sync_basic.main() == 0
        dbutil.load_stock_info_to_db.assert_called_once()


def test_sync_basic_save_raises_returns_1():
    """反例(本次修复的回归点): 写 STOCK_INFO 抛异常 → 1"""
    with patch.object(sync_basic, "myutil") as myutil, \
         patch.object(sync_basic, "dbutil") as dbutil, \
         patch.object(sync_basic, "parse_arguments",
                      return_value=_args(source="bstock", forcerun=False)), \
         patch.object(sync_basic, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = _source("fetch_stock_info", (_df(), _df()))
        dbutil.load_stock_info_to_db.side_effect = RuntimeError("写 STOCK_INFO 失败")
        assert sync_basic.main() == 1


def test_sync_basic_invalid_params_returns_1():
    """反例: 非交易日且未加 -f → 1"""
    with patch.object(sync_basic, "myutil"), \
         patch.object(sync_basic, "parse_arguments",
                      return_value=_args(source="bstock", forcerun=False)), \
         patch.object(sync_basic, "check_parameters", return_value=False):
        assert sync_basic.main() == 1


# ── sync_margin ───────────────────────────────────────────────────────────────

def _margin_args(**kwargs) -> argparse.Namespace:
    base = {"begin": "20260817", "end": "20260817", "exchanges": ["all"],
            "only": "summary", "source": "akstock", "forcerun": False, "download": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_sync_margin_success_returns_0():
    """正例: 取到汇总并写库 → 0"""
    with patch.object(sync_margin, "myutil") as myutil, \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args()), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = ["20260817"]
        myutil.import_source_module.return_value = _source(
            "fetch_margin_summary", pd.DataFrame({"exchange_code": ["SH", "SZ"]}))
        assert sync_margin.main() == 0
        dbutil.save_margin_summary_to_db.assert_called_once()


def test_sync_margin_save_raises_returns_1():
    """反例(本次修复的回归点): 写 MARGIN_SUMMARY_DAILY 抛异常 → 1"""
    with patch.object(sync_margin, "myutil") as myutil, \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args()), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = ["20260817"]
        myutil.import_source_module.return_value = _source(
            "fetch_margin_summary", pd.DataFrame({"exchange_code": ["SH", "SZ"]}))
        dbutil.save_margin_summary_to_db.side_effect = RuntimeError("写融资融券失败")
        assert sync_margin.main() == 1


def test_sync_margin_no_trade_dates_returns_1():
    """反例: 区间内无交易日 → 1"""
    with patch.object(sync_margin, "myutil"), \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args()), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = []
        assert sync_margin.main() == 1


def test_sync_margin_no_last_trade_date_returns_1():
    """反例: 未显式给日期且交易日历为空 → 1"""
    with patch.object(sync_margin, "myutil"), \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "parse_arguments",
                      return_value=_margin_args(begin=None, end=None)):
        dbutil.get_last_trade_date.return_value = None
        assert sync_margin.main() == 1


# ── sync_industry ─────────────────────────────────────────────────────────────

def _industry_args(**kwargs) -> argparse.Namespace:
    base = {"source": "akstock", "input": None, "version": "2021",
            "download": False, "forcerun": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_sync_industry_success_returns_0():
    """正例: 取到行业历史并写库 → 0"""
    with patch.object(sync_industry, "myutil") as myutil, \
         patch.object(sync_industry, "dbutil") as dbutil, \
         patch.object(sync_industry, "parse_arguments", return_value=_industry_args()), \
         patch.object(sync_industry, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = _source(
            "fetch_stock_industry_clf_hist_sw", _df())
        assert sync_industry.main() == 0
        dbutil.save_stock_industry_clf_hist_sw_raw_to_db.assert_called_once()


def test_sync_industry_save_raises_returns_1():
    """反例(本次修复的回归点): 写 STOCK_INDUSTRY_CLF_HIST_SW_RAW 抛异常 → 1"""
    with patch.object(sync_industry, "myutil") as myutil, \
         patch.object(sync_industry, "dbutil") as dbutil, \
         patch.object(sync_industry, "parse_arguments", return_value=_industry_args()), \
         patch.object(sync_industry, "check_parameters", return_value=True):
        myutil.import_source_module.return_value = _source(
            "fetch_stock_industry_clf_hist_sw", _df())
        dbutil.save_stock_industry_clf_hist_sw_raw_to_db.side_effect = RuntimeError("写库失败")
        assert sync_industry.main() == 1


def test_sync_industry_invalid_params_returns_1():
    """反例: 非交易日且未加 -f → 1"""
    with patch.object(sync_industry, "myutil"), \
         patch.object(sync_industry, "parse_arguments", return_value=_industry_args()), \
         patch.object(sync_industry, "check_parameters", return_value=False):
        assert sync_industry.main() == 1


# ── sync_finance ──────────────────────────────────────────────────────────────

def test_sync_finance_success_returns_0():
    """正例: 遍历报告期并写库 → 0"""
    with patch.object(sync_finance, "myutil"), \
         patch.object(sync_finance, "dbutil") as dbutil, \
         patch.object(sync_finance, "tdx_offline") as tdx_offline, \
         patch.object(sync_finance, "cw_fields") as cw_fields:
        tdx_offline.iter_cw_reports.return_value = [("20240331", _df())]
        cw_fields.cw_df_to_finance_report.return_value = _df()
        assert sync_finance.run_sync() == 0
        dbutil.save_finance_report_to_db.assert_called_once()


def test_sync_finance_save_raises_returns_1():
    """反例(本次修复的回归点): 写 FINANCE_REPORT 抛异常 → 1"""
    with patch.object(sync_finance, "myutil"), \
         patch.object(sync_finance, "dbutil") as dbutil, \
         patch.object(sync_finance, "tdx_offline") as tdx_offline, \
         patch.object(sync_finance, "cw_fields") as cw_fields:
        tdx_offline.iter_cw_reports.return_value = [("20240331", _df())]
        cw_fields.cw_df_to_finance_report.return_value = _df()
        dbutil.save_finance_report_to_db.side_effect = RuntimeError("写 FINANCE_REPORT 失败")
        assert sync_finance.run_sync() == 1


def test_sync_finance_no_reports_returns_0():
    """正例: 本地无任何报告期是合法结果(安全降级), 不算失败 → 0"""
    with patch.object(sync_finance, "myutil"), \
         patch.object(sync_finance, "dbutil") as dbutil, \
         patch.object(sync_finance, "tdx_offline") as tdx_offline:
        tdx_offline.iter_cw_reports.return_value = []
        assert sync_finance.run_sync() == 0
        dbutil.save_finance_report_to_db.assert_not_called()


# ── init_db ───────────────────────────────────────────────────────────────────

def test_init_db_success_returns_0():
    """正例: 建表成功 → 0"""
    with patch.object(init_db, "myutil") as myutil, \
         patch.object(init_db, "dbutil"):
        myutil.get_sql_file.return_value = MagicMock(
            read_text=MagicMock(return_value="CREATE TABLE t(i INT);"))
        assert init_db.create_database_schema() == 0


def test_init_db_execute_raises_returns_1():
    """反例(本次修复的回归点): 建表 SQL 执行失败 → 1"""
    with patch.object(init_db, "myutil") as myutil, \
         patch.object(init_db, "dbutil") as dbutil:
        myutil.get_sql_file.return_value = MagicMock(
            read_text=MagicMock(return_value="CREATE TABLE t(i INT);"))
        dbutil.get_connection.return_value.execute.side_effect = RuntimeError("建表失败")
        assert init_db.create_database_schema() == 1


def test_init_db_schema_file_missing_returns_1():
    """反例: 找不到 sql/schema.sql → 1"""
    with patch.object(init_db, "myutil") as myutil, \
         patch.object(init_db, "dbutil"):
        myutil.get_sql_file.side_effect = FileNotFoundError("找不到 schema.sql")
        assert init_db.create_database_schema() == 1


def test_init_db_exit_code_propagated():
    """正例: __main__ 块必须用 sys.exit(...) 传出退出码(init_db 入口名不是 main)"""
    source = Path(init_db.__file__).read_text(encoding="utf-8")
    assert "sys.exit(create_database_schema())" in source
    assert "\n    create_database_schema()\n" not in source


# ── sync_capital（B040：08-19 与 09-11 两轮契约改造都漏掉的第 7 个入口）────────────

def _capital_args(**kw) -> argparse.Namespace:
    base = {"download": False, "full": False}
    base.update(kw)
    return argparse.Namespace(**base)


def test_sync_capital_success_returns_0():
    """正例: 取到 gbbq 并写库 → 0"""
    with patch.object(sync_capital, "configure_etl_logging"), \
         patch.object(sync_capital, "parse_arguments", return_value=_capital_args()), \
         patch.object(sync_capital, "tdx_offline") as tdx, \
         patch.object(sync_capital, "dbutil") as dbutil:
        tdx.fetch_gbbq.return_value = _df()
        assert sync_capital.main() == 0
        dbutil.save_capital_detail_to_db.assert_called_once()
        tdx.cleanup_gbbq_file.assert_called_once()


def test_sync_capital_all_sources_unavailable_returns_1():
    """反例(本次修复的回归点): fetch_gbbq 四级回退全部失败返回 None → 1（此前打一句 info 退出 0）"""
    with patch.object(sync_capital, "configure_etl_logging"), \
         patch.object(sync_capital, "parse_arguments", return_value=_capital_args()), \
         patch.object(sync_capital, "tdx_offline") as tdx, \
         patch.object(sync_capital, "dbutil") as dbutil:
        tdx.fetch_gbbq.return_value = None
        assert sync_capital.main() == 1
        dbutil.save_capital_detail_to_db.assert_not_called()
        tdx.cleanup_gbbq_file.assert_called_once()      # 失败也要清缓存


def test_sync_capital_save_raises_returns_1():
    """反例: 写 CAPITAL_DETAIL 抛异常 → 1"""
    with patch.object(sync_capital, "configure_etl_logging"), \
         patch.object(sync_capital, "parse_arguments", return_value=_capital_args()), \
         patch.object(sync_capital, "tdx_offline") as tdx, \
         patch.object(sync_capital, "dbutil") as dbutil:
        tdx.fetch_gbbq.return_value = _df()
        dbutil.save_capital_detail_to_db.side_effect = RuntimeError("写库失败")
        assert sync_capital.main() == 1


def test_sync_capital_cw_sync_raises_returns_1():
    """反例: cw 文件同步阶段抛错（如 md5 清单拿不到）→ 1，不进入 gbbq 阶段"""
    with patch.object(sync_capital, "configure_etl_logging"), \
         patch.object(sync_capital, "parse_arguments", return_value=_capital_args()), \
         patch.object(sync_capital, "tdx_offline") as tdx, \
         patch.object(sync_capital, "dbutil"):
        tdx.sync_cw_files.side_effect = RuntimeError("cw 清单下载失败")
        assert sync_capital.main() == 1
        tdx.fetch_gbbq.assert_not_called()


# ── sync_industry（B043：两个源都拿不到 → 1）──────────────────────────────────

def test_sync_industry_both_sources_empty_returns_1():
    """反例(本次修复的回归点): akstock 空 → 回退申万官网也空 → 1（此前 warning 后退出 0）"""
    with patch.object(sync_industry, "myutil") as myutil, \
         patch.object(sync_industry, "dbutil") as dbutil, \
         patch.object(sync_industry, "parse_arguments", return_value=_industry_args()), \
         patch.object(sync_industry, "check_parameters", return_value=True), \
         patch("datasource.web.fetch_stock_industry_clf_hist_sw", return_value=pd.DataFrame()) as web:
        myutil.import_source_module.return_value = _source(
            "fetch_stock_industry_clf_hist_sw", pd.DataFrame())
        assert sync_industry.main() == 1
        web.assert_called_once()                                     # 确实走了回退
        dbutil.save_stock_industry_clf_hist_sw_raw_to_db.assert_not_called()


def test_sync_industry_fallback_succeeds_returns_0():
    """正例: akstock 空但申万官网回退拿到数据 → 0（回退语义保持）"""
    with patch.object(sync_industry, "myutil") as myutil, \
         patch.object(sync_industry, "dbutil") as dbutil, \
         patch.object(sync_industry, "parse_arguments", return_value=_industry_args()), \
         patch.object(sync_industry, "check_parameters", return_value=True), \
         patch("datasource.web.fetch_stock_industry_clf_hist_sw", return_value=_df()):
        myutil.import_source_module.return_value = _source(
            "fetch_stock_industry_clf_hist_sw", pd.DataFrame())
        assert sync_industry.main() == 0
        dbutil.save_stock_industry_clf_hist_sw_raw_to_db.assert_called_once()


# ── sync_margin（B044：全批失败判定）──────────────────────────────────────────

def test_sync_margin_summary_empty_returns_1():
    """反例(本次修复的回归点): 汇总 akstock 与官网回退都空 → 1（此前 warning 后退出 0）"""
    with patch.object(sync_margin, "myutil") as myutil, \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "web") as web, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args()), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = ["20260817"]
        myutil.import_source_module.return_value = _source("fetch_margin_summary", pd.DataFrame())
        web.fetch_margin_summary.return_value = pd.DataFrame()
        assert sync_margin.main() == 1
        dbutil.save_margin_summary_to_db.assert_not_called()


def test_sync_margin_detail_all_days_missing_returns_1():
    """反例: 明细区间内每个交易日两源都空 → 1"""
    with patch.object(sync_margin, "myutil") as myutil, \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "web") as web, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args(only="detail")), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = ["20260815", "20260817"]
        myutil.import_source_module.return_value = _source("fetch_margin_detail", pd.DataFrame())
        web.fetch_margin_detail.return_value = pd.DataFrame()
        assert sync_margin.main() == 1
        dbutil.save_margin_detail_to_db.assert_not_called()


def test_sync_margin_detail_partial_days_missing_returns_0():
    """正例(关键边界): 明细只缺一部分交易日（深市次日才可得是常态）→ 仍为 0，只告警"""
    with patch.object(sync_margin, "myutil") as myutil, \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "web") as web, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args(only="detail")), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = ["20260815", "20260817"]
        module = _source("fetch_margin_detail", None)
        module.fetch_margin_detail.side_effect = [
            pd.DataFrame({"exchange_code": ["SH", "SZ"]}),   # 第一天有
            pd.DataFrame(),                                   # 第二天空
        ]
        myutil.import_source_module.return_value = module
        web.fetch_margin_detail.return_value = pd.DataFrame()
        assert sync_margin.main() == 0
        assert dbutil.save_margin_detail_to_db.call_count == 1


def test_sync_margin_detail_source_missing_method_returns_1():
    """反例: 请求明细但数据源模块没有 fetch_margin_detail → 1（此前 logger.error 后退出 0）"""
    with patch.object(sync_margin, "myutil") as myutil, \
         patch.object(sync_margin, "dbutil") as dbutil, \
         patch.object(sync_margin, "parse_arguments", return_value=_margin_args(only="detail")), \
         patch.object(sync_margin, "check_parameters", return_value=True):
        dbutil.get_trade_dates.return_value = ["20260817"]
        myutil.import_source_module.return_value = _source("fetch_margin_summary", pd.DataFrame())
        assert sync_margin.main() == 1
