# 修改记录:
#   2026-10-04  Claude  新建: 区分「程序目录」与「运行目录 SPRING_HOME」, 支持安装包部署
"""路径约定: 程序目录(随包分发的只读资源) 与 运行目录(用户配置与运行产出)

源码部署与安装包部署共用同一套代码, 区别只在这两个目录是否重合:

  PACKAGE_ROOT  etl/ util/ 等包所在目录。源码部署时是项目根目录, 安装包部署时是
                site-packages。随包分发的只读资源从这里读:
                sql/schema.sql、data/*.csv、config/pipeline.yaml、
                config/config.yaml(安装包部署时仅作 spring-init 的模板)
  spring_home() 用户配置与运行产出: config/config.yaml、.env、log/、csv/、download/

spring_home() 的取值, 按优先级:
  1. 环境变量 SPRING_HOME(非空)
  2. 源码部署(PACKAGE_ROOT 下有 pyproject.toml) -> PACKAGE_ROOT, 与改造前的行为完全一致
  3. 安装包部署 -> ~/.spring
"""
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INSTALLED_HOME = "~/.spring"


def spring_home() -> Path:
    """运行目录: 用户配置(config.yaml / .env)与运行产出(log/ csv/ download/)所在目录"""
    value = os.environ.get("SPRING_HOME", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    if (PACKAGE_ROOT / "pyproject.toml").is_file():
        return PACKAGE_ROOT
    return Path(DEFAULT_INSTALLED_HOME).expanduser()


def resolve_data_file(relative: str | Path) -> Path:
    """解析配置里写的相对路径(如 data/reform_resume_days.csv)

    先找运行目录(用户自己放的文件), 再找程序目录(随包分发的文件)。
    两处都没有时返回程序目录下的路径, 由调用方按「文件不存在」报错。
    """
    for root in (spring_home(), PACKAGE_ROOT):
        candidate = root / relative
        if candidate.exists():
            return candidate
    return PACKAGE_ROOT / relative
