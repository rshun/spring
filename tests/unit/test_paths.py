# 修改记录:
#   2026-10-04  Claude  新建: util.paths 运行目录 / 程序目录解析的正反例
"""util.paths: SPRING_HOME 取值优先级与相对数据文件解析"""
import pytest

from util import paths


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("SPRING_HOME", raising=False)


def test_env_spring_home_wins(monkeypatch, tmp_path):
    """正例: 设置了 SPRING_HOME 就用它"""
    monkeypatch.setenv("SPRING_HOME", str(tmp_path))
    assert paths.spring_home() == tmp_path.resolve()


def test_source_checkout_uses_package_root(no_env):
    """正例: 源码部署(程序目录下有 pyproject.toml) -> 运行目录就是项目根, 与改造前一致"""
    assert (paths.PACKAGE_ROOT / "pyproject.toml").is_file()
    assert paths.spring_home() == paths.PACKAGE_ROOT


def test_blank_env_is_ignored(monkeypatch):
    """反例: SPRING_HOME 为空白串视同未设置, 不得解析成当前目录"""
    monkeypatch.setenv("SPRING_HOME", "   ")
    assert paths.spring_home() == paths.PACKAGE_ROOT


def test_installed_package_falls_back_to_user_dir(no_env, monkeypatch, tmp_path):
    """正例: 安装包部署(程序目录是 site-packages, 无 pyproject.toml) -> ~/.spring"""
    site = tmp_path / "site-packages"
    site.mkdir()
    fake_home = tmp_path / "user"
    monkeypatch.setattr(paths, "PACKAGE_ROOT", site)
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    assert paths.spring_home() == fake_home / ".spring"


def _layout(monkeypatch, tmp_path):
    home, pkg = tmp_path / "home", tmp_path / "pkg"
    (home / "data").mkdir(parents=True)
    (pkg / "data").mkdir(parents=True)
    monkeypatch.setenv("SPRING_HOME", str(home))
    monkeypatch.setattr(paths, "PACKAGE_ROOT", pkg)
    return home.resolve(), pkg


def test_resolve_data_file_prefers_runtime_dir(monkeypatch, tmp_path):
    """正例: 运行目录与程序目录都有同名文件时取运行目录(用户自己的版本)"""
    home, pkg = _layout(monkeypatch, tmp_path)
    (home / "data" / "a.csv").write_text("x", encoding="utf-8")
    (pkg / "data" / "a.csv").write_text("y", encoding="utf-8")
    assert paths.resolve_data_file("data/a.csv") == home / "data" / "a.csv"


def test_resolve_data_file_falls_back_to_package(monkeypatch, tmp_path):
    """正例: 运行目录没有时取程序目录(随包分发的版本)"""
    _, pkg = _layout(monkeypatch, tmp_path)
    (pkg / "data" / "a.csv").write_text("y", encoding="utf-8")
    assert paths.resolve_data_file("data/a.csv") == pkg / "data" / "a.csv"


def test_resolve_data_file_missing_returns_package_path(monkeypatch, tmp_path):
    """反例: 两处都没有 -> 返回程序目录下的路径(不存在), 由调用方报「文件不存在」"""
    _, pkg = _layout(monkeypatch, tmp_path)
    result = paths.resolve_data_file("data/none.csv")
    assert result == pkg / "data" / "none.csv"
    assert not result.exists()


def test_missing_config_error_mentions_init(monkeypatch, tmp_path):
    """反例: 配置文件不存在时, 报错要指明 spring-init / SPRING_HOME 两条出路"""
    from util import config
    monkeypatch.setattr(config, "_CONFIG_PATH", tmp_path / "config" / "config.yaml")
    with pytest.raises(FileNotFoundError, match="spring-init.*SPRING_HOME"):
        config._load()
