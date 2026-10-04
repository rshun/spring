# 修改记录:
#   2026-10-04  Claude  新建: tools.init_home 初始化运行目录的正反例
"""tools.init_home: 建运行目录、复制配置模板、绝不覆盖已有配置"""
import pytest

import tools.init_home as init_home


@pytest.fixture
def layout(monkeypatch, tmp_path):
    """程序目录 pkg(带配置模板) + 运行目录 home(空)"""
    pkg, home = tmp_path / "pkg", tmp_path / "home"
    (pkg / "config").mkdir(parents=True)
    (pkg / "config" / "config.yaml").write_text("template: 1\n", encoding="utf-8")
    monkeypatch.setattr(init_home, "PACKAGE_ROOT", pkg)
    monkeypatch.setenv("SPRING_HOME", str(home))
    return pkg, home


def test_creates_dirs_and_copies_template(layout):
    """正例: 空运行目录 -> 建齐子目录并复制配置模板, 退出码 0"""
    _, home = layout
    assert init_home.init_home() == 0
    for name in init_home.RUNTIME_DIRS:
        assert (home / name).is_dir()
    assert (home / "config" / "config.yaml").read_text(encoding="utf-8") == "template: 1\n"


def test_existing_config_is_not_overwritten(layout):
    """反例: 已有配置文件 -> 原样保留, 重复运行安全"""
    _, home = layout
    (home / "config").mkdir(parents=True)
    (home / "config" / "config.yaml").write_text("mine: 1\n", encoding="utf-8")
    assert init_home.init_home() == 0
    assert (home / "config" / "config.yaml").read_text(encoding="utf-8") == "mine: 1\n"


def test_source_checkout_home_equals_package(monkeypatch, tmp_path):
    """正例: 源码部署(运行目录即程序目录) -> 模板就是配置本身, 不复制不报错"""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("src: 1\n", encoding="utf-8")
    monkeypatch.setattr(init_home, "PACKAGE_ROOT", tmp_path)
    monkeypatch.setenv("SPRING_HOME", str(tmp_path))
    assert init_home.init_home() == 0
    assert (tmp_path / "config" / "config.yaml").read_text(encoding="utf-8") == "src: 1\n"


def test_missing_template_fails(layout):
    """反例: 程序目录里没有配置模板 -> 退出码 1, 不生成空配置"""
    pkg, home = layout
    (pkg / "config" / "config.yaml").unlink()
    assert init_home.init_home() == 1
    assert not (home / "config" / "config.yaml").exists()


def test_unwritable_home_fails(monkeypatch, tmp_path):
    """反例: 运行目录路径被一个普通文件占用 -> 建目录失败, 退出码 1 而非抛异常"""
    blocker = tmp_path / "home"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("SPRING_HOME", str(blocker))
    assert init_home.init_home() == 1
