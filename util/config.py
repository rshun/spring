"""
项目全局配置加载器

从项目根目录 config.yaml 读取配置，提供带缓存的访问接口。
所有硬编码常量迁移至 config.yaml，程序通过本模块统一获取。

用法:
    from util.config import get_config
    cfg = get_config()          # 返回只读视图，不可对顶层键赋值
    servers = cfg["tdx"]["servers"]
"""
import threading
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

logger = logging.getLogger("util.config")

# 项目根目录 = util/ 的上一级
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"

# 线程安全缓存
_lock = threading.Lock()
_config_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    """从磁盘读取并解析配置文件，不使用缓存。"""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件不存在: {_CONFIG_PATH}")

    logger.debug("加载配置文件: %s", _CONFIG_PATH)
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"配置文件解析失败: {_CONFIG_PATH}") from e

    if data is None:
        logger.warning("配置文件为空: %s", _CONFIG_PATH)
        return {}

    if not isinstance(data, dict):
        raise TypeError(
            f"配置文件格式错误，期望 dict，实际为 {type(data).__name__}: {_CONFIG_PATH}"
        )

    return data


def get_config() -> MappingProxyType[str, Any]:
    """获取完整配置（只读视图）。首次调用加载并缓存，后续直接返回缓存。线程安全。"""
    global _config_cache

    if _config_cache is not None:
        return MappingProxyType(_config_cache)

    with _lock:
        # 双重检查：持锁后再次判断，避免重复加载
        if _config_cache is not None:
            return MappingProxyType(_config_cache)
        _config_cache = _load()

    return MappingProxyType(_config_cache)


def reload_config() -> MappingProxyType[str, Any]:
    """强制从磁盘重新加载配置并更新缓存，主要用于测试或配置热更新。"""
    global _config_cache
    with _lock:
        _config_cache = _load()
    return MappingProxyType(_config_cache)
