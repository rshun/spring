# 修改记录:
#   2026-05-29  Claude  新增 sync_finance 编排与 iter_cw_reports 的正反测试
"""sync_finance 编排逻辑 + tdx_offline.iter_cw_reports 纯逻辑测试(无数据库/网络)"""
import types

import pandas as pd
import pytest

from datasource import tdx_offline
from etl import sync_finance
from util import dbutil


# ── _parse_codes ─────────────────────────────────────────

def test_parse_codes_splits_mixed_separators():
    """正例：逗号/中文逗号/多段混合都能拆成裸代码集合"""
    assert sync_finance._parse_codes(["000001，600519", "000002"]) == {
        "000001", "600519", "000002"
    }


def test_parse_codes_none_returns_none():
    """反例：未传 codes 返回 None(表示全市场)"""
    assert sync_finance._parse_codes(None) is None
    assert sync_finance._parse_codes([]) is None


# ── iter_cw_reports ──────────────────────────────────────

def _write_pkl(pkl_dir, rd):
    df = pd.DataFrame([{0: "000001", 1: 1.23, 95: 2.0e9}])
    df.to_pickle(str(pkl_dir / f"gpcw{rd}.pkl"), compression=None)


def test_iter_cw_reports_filters_by_date(tmp_path, monkeypatch):
    """正例：按报告期闭区间过滤，升序产出"""
    pkl_dir = tmp_path / "cw_pkl"
    pkl_dir.mkdir()
    for rd in ("20230331", "20231231", "20241231"):
        _write_pkl(pkl_dir, rd)
    (tmp_path / "cw").mkdir()
    monkeypatch.setattr(tdx_offline, "DOWNLOAD_DIR", tmp_path)

    got = [rd for rd, _ in tdx_offline.iter_cw_reports("20231001", "2024-12-31")]
    assert got == ["20231231", "20241231"]


def test_iter_cw_reports_empty_dir(tmp_path, monkeypatch):
    """反例：本地无任何 cw 文件时不产出，安全降级"""
    (tmp_path / "cw_pkl").mkdir()
    (tmp_path / "cw").mkdir()
    monkeypatch.setattr(tdx_offline, "DOWNLOAD_DIR", tmp_path)
    assert list(tdx_offline.iter_cw_reports()) == []


# ── run_sync 编排 ────────────────────────────────────────

def _patch_conn(monkeypatch):
    """get_connection 返回带 close() 的哑对象(save 已被打桩，连接不会真正使用)"""
    monkeypatch.setattr(
        dbutil, "get_connection",
        lambda is_read_only=False: types.SimpleNamespace(close=lambda: None),
    )


def test_run_sync_transforms_and_saves(monkeypatch):
    """正例：每个报告期经转换后调用 save，写入的 df 含具名列"""
    raw = pd.DataFrame([{0: "000001", 1: 1.23, 95: 2.0e9}])
    monkeypatch.setattr(tdx_offline, "iter_cw_reports",
                        lambda start=None, end=None: iter([("20241231", raw)]))
    saved = []
    monkeypatch.setattr(dbutil, "save_finance_report_to_db",
                        lambda df, conn: saved.append(df))
    _patch_conn(monkeypatch)

    sync_finance.run_sync()

    assert len(saved) == 1
    assert saved[0].loc[0, "code"] == "000001"
    assert saved[0].loc[0, "eps"] == pytest.approx(1.23)
    assert saved[0].loc[0, "report_date"] == "2024-12-31"


def test_run_sync_filters_codes(monkeypatch):
    """正例：--codes 过滤后只写入匹配的股票"""
    raw = pd.DataFrame([
        {0: "000001", 1: 1.0, 95: 1.0},
        {0: "600519", 1: 2.0, 95: 2.0},
    ])
    monkeypatch.setattr(tdx_offline, "iter_cw_reports",
                        lambda start=None, end=None: iter([("20241231", raw)]))
    saved = []
    monkeypatch.setattr(dbutil, "save_finance_report_to_db",
                        lambda df, conn: saved.append(df))
    _patch_conn(monkeypatch)

    sync_finance.run_sync(codes=["600519"])

    assert len(saved) == 1
    assert saved[0]["code"].tolist() == ["600519"]


def test_run_sync_no_reports_does_not_save(monkeypatch):
    """反例：无报告期产出时不调用 save，且不抛异常"""
    monkeypatch.setattr(tdx_offline, "iter_cw_reports",
                        lambda start=None, end=None: iter([]))
    saved = []
    monkeypatch.setattr(dbutil, "save_finance_report_to_db",
                        lambda df, conn: saved.append(df))
    _patch_conn(monkeypatch)

    sync_finance.run_sync()
    assert saved == []


def test_run_sync_codes_no_match_skips_save(monkeypatch):
    """反例：--codes 无任何匹配时不写入"""
    raw = pd.DataFrame([{0: "000001", 1: 1.0, 95: 1.0}])
    monkeypatch.setattr(tdx_offline, "iter_cw_reports",
                        lambda start=None, end=None: iter([("20241231", raw)]))
    saved = []
    monkeypatch.setattr(dbutil, "save_finance_report_to_db",
                        lambda df, conn: saved.append(df))
    _patch_conn(monkeypatch)

    sync_finance.run_sync(codes=["999999"])
    assert saved == []
