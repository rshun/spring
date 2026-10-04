# 修改记录:
#   2026-10-04  Claude  新建: pyproject.toml 安装包契约(命令入口 / 随包资源)的正反例
"""pyproject.toml 契约: 命令入口都能解析到可调用对象, 程序不漏注册, 资源文件随包分发"""
import importlib
import re
import tomllib
from pathlib import Path

import pytest

from util.paths import PACKAGE_ROOT

PYPROJECT = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
SCRIPTS: dict[str, str] = PYPROJECT["project"]["scripts"]


@pytest.mark.parametrize("name,target", sorted(SCRIPTS.items()))
def test_script_target_is_callable(name, target):
    """正例: 每个 spring-* 命令都指向一个可导入、可调用的入口"""
    module_path, _, attr = target.partition(":")
    module = importlib.import_module(module_path)
    assert callable(getattr(module, attr)), f"{name} -> {target}"


def _cli_modules() -> set[str]:
    """etl/ 与 tools/ 下带 __main__ 入口的模块"""
    found = set()
    for pkg in ("etl", "tools"):
        for f in (PACKAGE_ROOT / pkg).glob("*.py"):
            if re.search(r'^if __name__ == [\'"]__main__[\'"]', f.read_text(encoding="utf-8"), re.M):
                found.add(f"{pkg}.{f.stem}")
    return found


def test_every_cli_module_has_a_command():
    """反例防线: 新增的命令行程序忘了在 [project.scripts] 注册 -> 安装后没有对应命令"""
    registered = {t.partition(":")[0] for t in SCRIPTS.values()}
    missing = _cli_modules() - registered
    assert not missing, f"未注册为 spring-* 命令: {sorted(missing)}"


def test_describe_cli_programs_have_commands():
    """正例: etl-quant-mcp 通过 describe_cli 调度的程序, 安装后都有对应命令"""
    from tools.describe_cli import PROGRAMS
    registered = {t.partition(":")[0] for t in SCRIPTS.values()}
    assert set(PROGRAMS.values()) <= registered


def test_command_names_follow_convention():
    """反例防线: 命令名必须是 spring- 前缀加连字符, 不得出现下划线(各平台命令风格一致)"""
    bad = [n for n in SCRIPTS if not re.fullmatch(r"spring(-[a-z0-9]+)+", n)]
    assert not bad, bad


def test_package_data_covers_runtime_resources():
    """正例: 运行时从程序目录读取的资源都被 package-data 收入安装包"""
    package_data = PYPROJECT["tool"]["setuptools"]["package-data"]
    required = [Path("sql/schema.sql"), Path("config/config.yaml"), Path("config/pipeline.yaml"),
                Path("data/SwClassCode_2021.csv"), Path("data/reform_resume_days.csv"),
                Path("data/share_listing_days.csv")]
    for rel in required:
        assert (PACKAGE_ROOT / rel).is_file(), rel
        globs = package_data.get(rel.parts[0], [])
        assert any(rel.match(f"{rel.parts[0]}/{g}") for g in globs), f"{rel} 未收入 package-data"
