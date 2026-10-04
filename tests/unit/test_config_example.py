# 修改记录:
#   2026-10-04  Claude  新建: config/config.yaml.example 占位符模板的契约(与 config.yaml 同步、路径为绝对路径占位符)
"""config/config.yaml.example: spring-init 复制给部署机的配置模板

两条约定:
  1. 除 local_paths 外, 各节取值与 config/config.yaml 完全一致 —— 改了 config.yaml 忘了同步模板,
     新部署的机器就会拿到过期的默认值且不报错
  2. local_paths 中的路径项都是绝对路径占位符, 不含 ~、不含开发机路径
"""
import pytest
import yaml

from util.paths import PACKAGE_ROOT

PATH_KEYS = ("tdx_vipdoc", "tdx_gbbq", "db", "db_test")


def _load(name: str) -> dict:
    with open(PACKAGE_ROOT / "config" / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def example() -> dict:
    return _load("config.yaml.example")


@pytest.fixture(scope="module")
def actual() -> dict:
    return _load("config.yaml")


def test_sections_other_than_local_paths_match(example, actual):
    """正例: 除 local_paths 外各节与 config.yaml 逐项一致"""
    rest_example = {k: v for k, v in example.items() if k != "local_paths"}
    rest_actual = {k: v for k, v in actual.items() if k != "local_paths"}
    assert rest_example == rest_actual, "config.yaml 改动后须同步到 config.yaml.example"


def test_local_paths_has_same_keys(example, actual):
    """反例防线: config.yaml 新增 local_paths 配置项而模板漏加 -> 部署机缺该项"""
    assert set(example["local_paths"]) == set(actual["local_paths"])


@pytest.mark.parametrize("key", PATH_KEYS)
def test_path_placeholders_are_absolute(example, key):
    """正例: 路径项是 /absolute/path/to/ 占位符(绝对路径形式, 一眼可辨须替换)"""
    value = example["local_paths"][key]
    assert value.startswith("/absolute/path/to/"), value


@pytest.mark.parametrize("key", PATH_KEYS)
def test_path_placeholders_have_no_home_or_dev_path(example, key):
    """反例: 模板里不得出现 ~ 或开发机路径(会随执行用户变化 / 泄露本机目录结构)"""
    value = example["local_paths"][key]
    assert "~" not in value
    assert ":\\" not in value and ":/" not in value


def test_db_active_default_is_prod(example):
    """正例: 模板默认使用正式库"""
    assert example["local_paths"]["db_active"] == "prod"
