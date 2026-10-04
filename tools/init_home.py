# 修改记录:
#   2026-10-04  Claude  新建: 安装包部署时初始化运行目录 SPRING_HOME(建目录 + 复制配置模板)
"""
初始化运行目录 SPRING_HOME

安装包部署后首次使用前运行一次: 建好 config/ log/ csv/ download/ 目录,
并把随包分发的 config.yaml 复制为运行目录下的 config/config.yaml 供修改。
已存在的配置文件一律不覆盖, 重复运行安全。
源码部署时运行目录就是项目根目录, 本工具只会补建缺失的目录。

用法:
    spring-init                       # 安装包部署
    python -m tools.init_home         # 源码部署
    SPRING_HOME=D:/spring spring-init # 指定运行目录

退出码: 0 成功; 1 失败(配置模板缺失 / 目录或文件无法创建)。
"""
import argparse
import shutil
import sys

from util.paths import PACKAGE_ROOT, spring_home

RUNTIME_DIRS = ("config", "log", "csv", "download")
CONFIG_RELATIVE = ("config", "config.yaml")


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="初始化运行目录 SPRING_HOME: 建目录并复制配置模板(不覆盖已有配置)"
    )


def init_home() -> int:
    home = spring_home()
    template = PACKAGE_ROOT.joinpath(*CONFIG_RELATIVE)
    target = home.joinpath(*CONFIG_RELATIVE)
    try:
        for name in RUNTIME_DIRS:
            (home / name).mkdir(parents=True, exist_ok=True)

        if target.exists():
            print(f"配置文件已存在, 未覆盖: {target}")
        elif not template.is_file():
            print(f"配置模板不存在: {template}", file=sys.stderr)
            return 1
        else:
            shutil.copyfile(template, target)
            print(f"已生成配置文件, 请按本机情况修改: {target}")
    except OSError as e:
        print(f"初始化运行目录失败: {e}", file=sys.stderr)
        return 1

    print(f"运行目录: {home}")
    if not (home / ".env").exists():
        print(f"如需同花顺除权事件接口, 在 {home / '.env'} 中写入一行: "
              f"THS_API_KEY=your_api_key_here")
    return 0


def main() -> int:
    build_parser().parse_args()
    return init_home()


if __name__ == "__main__":
    sys.exit(main())
