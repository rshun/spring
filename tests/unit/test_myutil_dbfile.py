# 修改记录:
#   2026-09-11  Claude  B045 反例：db_active 非法值必须报错，不得静默回落正式库
#   2026-09-12  Claude  B048 反例：db_active=test 但 db_test 未配置同样必须报错；
#                       删除与之冲突的旧用例(原断言回退内置默认路径=正式库)
from pathlib import Path
from unittest.mock import patch

import pytest

from util import myutil


def _cfg(**local_paths):
    return {"local_paths": local_paths}


# ── 正常测试 ──────────────────────────────────────────────────────────────────

def test_dbfile_prod_profile_uses_db(monkeypatch):
    """db_active=prod 时使用正式库 local_paths.db。"""
    cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db", db_active="prod")
    with patch("util.config.get_config", return_value=cfg):
        assert myutil.get_default_dbfile() == Path.home() / "data" / "quant.db"


def test_dbfile_test_profile_uses_db_test(monkeypatch):
    """db_active=test 时切换到测试库 local_paths.db_test。"""
    cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db", db_active="test")
    with patch("util.config.get_config", return_value=cfg):
        assert myutil.get_default_dbfile() == Path.home() / "data" / "quant_test.db"


def test_dbfile_defaults_to_prod_when_active_missing(monkeypatch):
    """未配置 db_active 时默认走正式库(向后兼容)。"""
    cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db")
    with patch("util.config.get_config", return_value=cfg):
        assert myutil.get_default_dbfile() == Path.home() / "data" / "quant.db"


# ── 反向 / 边界测试 ──────────────────────────────────────────────────────────

# 原 test_dbfile_test_active_but_missing_test_path_falls_back 已于 2026-09-12 删除：
# 它断言「db_active=test 但未配置 db_test 时回退内置默认路径而非报错」，而那个默认路径
# 恰好就是正式库 ~/data/quant.db。该行为与 B045（2026-09-11，实测 db_active 填错把
# 「测试」跑批写进 quant.db）确立的原则直接冲突，改由下方
# test_db_active_test_without_db_test_raises 断言必须报错（B048）。


def test_dbfile_active_case_insensitive(monkeypatch):
    """db_active 大小写不敏感: 'TEST' 同样切测试库。"""
    cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db", db_active="TEST")
    with patch("util.config.get_config", return_value=cfg):
        assert myutil.get_default_dbfile() == Path.home() / "data" / "quant_test.db"


# ── B045：非法 db_active 必须报错 ─────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["db_test", "production", "dev", " Test-DB "])
def test_dbfile_invalid_active_raises(bad):
    """反例(B045): 除 prod/test 外的取值必须抛 ValueError——静默回落正式库会让
    以为在测试库的人把跑批写进生产（2026-09-11 实测 'db_test' 就是这样写进 quant.db 的）"""
    cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db", db_active=bad)
    with patch("util.config.get_config", return_value=cfg):
        with pytest.raises(ValueError, match="db_active 非法"):
            myutil.get_default_dbfile()


def test_dbfile_empty_active_still_defaults_to_prod():
    """正例(B045 边界): 空字符串/None 仍按缺省 prod 处理（向后兼容不变）"""
    for empty in ("", None):
        cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db", db_active=empty)
        with patch("util.config.get_config", return_value=cfg):
            assert myutil.get_default_dbfile() == Path.home() / "data" / "quant.db"


# ── 2026-09-12 B048: db_active=test 但 db_test 未配置不得回落正式库 ───────────

@pytest.mark.parametrize("db_test_value", [None, "", "   "], ids=["缺失", "空串", "空白"])
def test_db_active_test_without_db_test_raises(db_test_value):
    """反例(B048): db_active=test 而 db_test 未配置时必须抛错。

    回落的「内置默认路径」恰好就是正式库 ~/data/quant.db，与 db_active=prod 指向
    同一个文件——后果同 B045：人以为在测试库，实际在写生产。
    """
    local_paths = {"db": "~/data/quant.db", "db_active": "test"}
    if db_test_value is not None:
        local_paths["db_test"] = db_test_value
    with patch("util.config.get_config", return_value={"local_paths": local_paths}):
        with pytest.raises(ValueError, match="db_test"):
            myutil.get_default_dbfile()


def test_db_active_prod_without_db_still_falls_back():
    """正例(边界): db_active=prod 且 db 未配置时仍回落内置默认路径（向后兼容，不受 B048 影响）"""
    with patch("util.config.get_config",
               return_value={"local_paths": {"db_active": "prod"}}):
        assert myutil.get_default_dbfile() == Path("~/data/quant.db").expanduser()


def test_db_active_test_with_db_test_uses_test_db():
    """正例: 正常配置下 test 指向测试库，且与正式库不是同一个文件"""
    local_paths = {"db": "~/data/quant.db", "db_test": "~/data/quant_test.db"}
    with patch("util.config.get_config",
               return_value={"local_paths": {**local_paths, "db_active": "test"}}):
        test_path = myutil.get_default_dbfile()
    with patch("util.config.get_config",
               return_value={"local_paths": {**local_paths, "db_active": "prod"}}):
        prod_path = myutil.get_default_dbfile()
    assert test_path == Path("~/data/quant_test.db").expanduser()
    assert test_path != prod_path
