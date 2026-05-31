from pathlib import Path
from unittest.mock import patch

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

def test_dbfile_test_active_but_missing_test_path_falls_back(monkeypatch):
    """db_active=test 但未配置 db_test 时, 回退内置默认路径而非报错。"""
    cfg = _cfg(db="~/data/quant.db", db_active="test")
    with patch("util.config.get_config", return_value=cfg):
        assert myutil.get_default_dbfile() == Path.home() / "data" / "quant.db"


def test_dbfile_active_case_insensitive(monkeypatch):
    """db_active 大小写不敏感: 'TEST' 同样切测试库。"""
    cfg = _cfg(db="~/data/quant.db", db_test="~/data/quant_test.db", db_active="TEST")
    with patch("util.config.get_config", return_value=cfg):
        assert myutil.get_default_dbfile() == Path.home() / "data" / "quant_test.db"
