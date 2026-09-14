# 修改记录:
#   2026-09-14  Claude  新建 -p 导出 CSV 的正反例(此前 -p 打屏, 无任何测试覆盖)
"""import_daily -p: 按股票导出 CSV

-p 由「打屏」改成「按股票导出 CSV」后有三条契约需要守住：
  1. 不带 -c 必须拒绝运行 —— 否则全市场会一次产出五千多对文件
  2. 文件名用 6 位裸代码, 不带 .SH/.SZ 后缀
  3. 某类数据缺失时不产出对应文件, 也不报错(basic 缺失是部分数据源的常态)
"""
import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import import_daily


def _args(**kw) -> argparse.Namespace:
    base = {"begin": "20260911", "end": "20260914", "exchanges": ["all"],
            "codes": ["600519"], "source": "bstock", "print_only": True,
            "date_range_only": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _daily_df(*codes) -> pd.DataFrame:
    return pd.DataFrame({
        "code": list(codes),
        "date": ["2026-09-11"] * len(codes),
        "open": [10.0] * len(codes), "high": [11.0] * len(codes),
        "low": [9.5] * len(codes), "close": [10.5] * len(codes),
        "pre_close": [10.0] * len(codes),
        "volume": [1000] * len(codes), "amount": [10500.0] * len(codes),
    })


def _basic_df(*codes) -> pd.DataFrame:
    return pd.DataFrame({
        "code": list(codes),
        "trade_date": ["2026-09-11"] * len(codes),
        "turnover_rate": [1.5] * len(codes), "pe": [20.0] * len(codes),
    })


def _run(monkeypatch, tmp_path, daily, basic, **argkw) -> int:
    """跑一次 main(-p), 导出目录重定向到 tmp_path"""
    monkeypatch.setattr(import_daily, "CSV_DIR", tmp_path)
    with patch.object(import_daily, "myutil") as myutil, \
         patch.object(import_daily, "dbutil") as dbutil, \
         patch.object(import_daily, "parse_arguments", return_value=_args(**argkw)), \
         patch.object(import_daily, "check_parameters", return_value=True):
        dbutil.get_candidate_codes.return_value = [("600519", "SH")]
        module = MagicMock(spec=["fetch_batch_data"])
        module.fetch_batch_data.return_value = (daily, basic)
        myutil.import_source_module.return_value = module
        rc = import_daily.main()
        # -p 不得写库
        dbutil.save_daily_to_db.assert_not_called()
        dbutil.save_base_to_db.assert_not_called()
        return rc


# ── 守卫: -p 必须带 -c ────────────────────────────────────────────────────────

def test_print_only_without_codes_is_rejected(monkeypatch):
    """反例: -p 不带 -c 必须拒绝(argparse 用法错, 退出码 2)。

    不带 -c 时候选是全市场, 会一次产出五千多对 CSV。
    """
    monkeypatch.setattr("sys.argv", ["import_daily.py", "-p"])
    with pytest.raises(SystemExit) as e:
        import_daily.parse_arguments()
    assert e.value.code == 2


def test_print_only_with_codes_is_accepted(monkeypatch):
    """正例: -p 带 -c 正常通过"""
    monkeypatch.setattr("sys.argv", ["import_daily.py", "-p", "-c", "600519"])
    args = import_daily.parse_arguments()
    assert args.print_only and args.codes == ["600519"]


def test_guard_lives_outside_build_parser(monkeypatch):
    """反例(契约 C4): 守卫不得放进 build_parser —— 它必须保持无副作用，
    tools/describe_cli.py 靠它自省参数。这里直接构造 -p 而不带 -c 的命名空间，
    build_parser().parse_args() 不应报错。"""
    monkeypatch.setattr("sys.argv", ["import_daily.py", "-p"])
    args = import_daily.build_parser().parse_args()   # 不应抛 SystemExit
    assert args.print_only and not args.codes


# ── 导出行为 ──────────────────────────────────────────────────────────────────

def test_export_uses_bare_symbol_in_filename(monkeypatch, tmp_path):
    """正例: 文件名用 6 位裸代码，不带 .SH/.SZ 后缀"""
    rc = _run(monkeypatch, tmp_path,
              _daily_df("600519.SH"), _basic_df("600519.SH"))
    assert rc == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["600519_basic_20260911_20260914.csv",
                     "600519_daily_20260911_20260914.csv"]


def test_export_one_pair_per_code(monkeypatch, tmp_path):
    """正例: 多只股票各出一对文件"""
    rc = _run(monkeypatch, tmp_path,
              _daily_df("600519.SH", "000001.SZ"),
              _basic_df("600519.SH", "000001.SZ"),
              codes=["600519", "000001"])
    assert rc == 0
    assert len(list(tmp_path.iterdir())) == 4


def test_missing_basic_produces_no_basic_file(monkeypatch, tmp_path):
    """正例: 无 basic 数据时只出 daily 文件，且不报错。

    部分数据源本就不提供 basic，属常态而非故障。
    """
    rc = _run(monkeypatch, tmp_path, _daily_df("600519.SH"), pd.DataFrame())
    assert rc == 0
    names = [p.name for p in tmp_path.iterdir()]
    assert names == ["600519_daily_20260911_20260914.csv"]


def test_empty_but_typed_frame_logs_skip(monkeypatch, tmp_path, caplog):
    """正例: 数据源返回「有列但零行」的空帧时不产出文件，并明确打日志说明。

    这是数据源更常见的空返回形态 —— 列齐全、只是没数据。
    文件行为上 groupby 本就不会产生分组，所以这里断言的重点是那条日志：
    -p 屏幕不输出数据，日志是使用方唯一的反馈，「明确说没数据」与「静默无输出」
    对排查是两回事。
    """
    import logging
    empty_typed = _basic_df("600519.SH").iloc[0:0]
    assert "code" in empty_typed.columns and empty_typed.empty
    with caplog.at_level(logging.INFO, logger="etl.import_daily"):
        rc = _run(monkeypatch, tmp_path, _daily_df("600519.SH"), empty_typed)
    assert rc == 0
    assert [p.name for p in tmp_path.iterdir()] == [
        "600519_daily_20260911_20260914.csv"]
    assert "无 basic 数据，跳过导出" in caplog.text


def test_no_data_at_all_returns_1(monkeypatch, tmp_path):
    """反例: 两类数据都没有 -> 不产出文件并返回 1，不得假装成功"""
    rc = _run(monkeypatch, tmp_path, pd.DataFrame(), pd.DataFrame())
    assert rc == 1
    assert list(tmp_path.iterdir()) == []


def test_exported_content_matches_source(monkeypatch, tmp_path):
    """正例: 导出内容与取到的数据一致(含列名)，且按 code 正确切分"""
    _run(monkeypatch, tmp_path,
         _daily_df("600519.SH", "000001.SZ"), pd.DataFrame(),
         codes=["600519", "000001"])
    got = pd.read_csv(tmp_path / "600519_daily_20260911_20260914.csv")
    assert len(got) == 1
    assert got.iloc[0]["code"] == "600519.SH"
    assert got.iloc[0]["close"] == 10.5


def test_symbol_collision_is_reported_not_silently_overwritten(
        monkeypatch, tmp_path, caplog):
    """反例: 两个标准代码撞到同一裸代码时必须报错并跳过，不得静默覆盖。

    裸代码丢掉了市场后缀，600519.SH 与 600519.SZ 会写到同一文件名。
    A 股 6 位代码按前缀区分市场，正常撞不上；真撞上时静默覆盖会让使用方
    拿到少一半的数据还不自知。
    """
    import logging
    with caplog.at_level(logging.ERROR, logger="etl.import_daily"):
        rc = _run(monkeypatch, tmp_path,
                  _daily_df("600519.SH", "600519.SZ"), pd.DataFrame(),
                  codes=["600519"])
    assert rc == 0
    assert len(list(tmp_path.iterdir())) == 1, "撞名的第二只不得覆盖第一只"
    assert "文件名冲突" in caplog.text
