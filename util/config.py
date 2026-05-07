"""
项目全局配置加载器

从项目根目录 config.yaml 读取配置，提供带缓存的访问接口。
所有硬编码常量迁移至 config.yaml，程序通过本模块统一获取。

用法:
    from util.config import get_config
    cfg = get_config()
    servers = cfg["tdx"]["servers"]
"""
from __future__ import annotations

import threading
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("util.config")

# 项目根目录 = util/ 的上一级
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"

# 线程安全缓存
_lock = threading.Lock()
_config_cache: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    """获取完整配置字典。

    首次调用从 config.yaml 加载并缓存，后续调用直接返回缓存。
    线程安全。
    """
    global _config_cache

    if _config_cache is not None:
        return _config_cache

    with _lock:
        # 双重检查：持锁后再次判断，避免重复加载
        if _config_cache is not None:
            return _config_cache

        if not _CONFIG_PATH.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {_CONFIG_PATH}"
            )

        logger.debug("加载配置文件: %s", _CONFIG_PATH)
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f)

        if _config_cache is None:
            _config_cache = {}
            logger.warning("配置文件为空: %s", _CONFIG_PATH)

        return _config_cache