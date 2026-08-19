# 修改记录:
#   2026-08-19  Claude  新增：把 ETL 的 argparse 定义导出为 JSON，作为跨仓自省出口
"""
ETL 命令行参数自省出口

用途: 外部调度方(如 etl-quant-mcp)据此构造命令行，参数增删改自动跟随，调用方零改动。
对 spring 自身也可用来生成 README 的参数表。

用法:
    python -m tools.describe_cli import_daily     # 单个程序
    python -m tools.describe_cli --all            # 全部程序
    python -m tools.describe_cli --list           # 只列程序名

退出码: 0 成功; 1 失败(程序名未注册 / 模块导入失败 / 模块未提供 build_parser)。

两点必须知道的默认值语义:
  1. adjust / import_daily / fetch_index 的 -b/-e 默认值是 **build_parser() 调用时的当天**,
     因此本工具的输出**逐日变化**。调用方若要做快照比对, 需先把日期类默认值归一化。
  2. fill_volratio / update_limit / fill_shares 的 -b/-e 在 argparse 层默认值为 null,
     真实默认(T-1 / 今天)是在 parse_arguments() 里 parse 之后才套用的, 本工具看不到。
     该语义由 help 文本承载(契约 C4), 调用方应把 help 原样透传给模型。
"""
import argparse
import importlib
import json
import sys

# 注册表: 程序名 → 模块路径。新增 ETL 时在此加一行即可。
PROGRAMS: dict[str, str] = {
    "adjust":        "etl.adjust",
    "import_daily":  "etl.import_daily",
    "fetch_index":   "etl.fetch_index",
    "fill_volratio": "etl.fill_volratio",
    "update_limit":  "etl.update_limit",
    "fill_shares":   "etl.fill_shares",
}

_ACTION_NAMES = {
    "_StoreAction": "store",
    "_StoreTrueAction": "store_true",
    "_StoreFalseAction": "store_false",
    "_AppendAction": "append",
    "_CountAction": "count",
}


def _type_name(t) -> str | None:
    """argparse 的 type 可能是 str、str.lower 等，取限定名以保留 'str.lower' 这类信息"""
    if t is None:
        return None
    return getattr(t, "__qualname__", None) or getattr(t, "__name__", None) or str(t)


def _jsonable(value):
    """默认值可能是任意对象，非 JSON 原生类型一律降级为字符串"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def describe(name: str) -> dict:
    """把单个 ETL 的 argparse 定义导出为 dict

    Raises
    ------
    KeyError     程序名未注册
    ImportError  模块导入失败
    AttributeError  模块没有 build_parser()
    """
    module_path = PROGRAMS[name]
    module = importlib.import_module(module_path)

    if not hasattr(module, "build_parser"):
        raise AttributeError(f"模块 '{module_path}' 没有定义 build_parser()")

    parser = module.build_parser()

    arguments: dict[str, dict] = {}
    for action in parser._actions:
        if action.dest in ("help", argparse.SUPPRESS):
            continue
        arguments[action.dest] = {
            "flags":    list(action.option_strings),
            "type":     _type_name(action.type),
            "action":   _ACTION_NAMES.get(action.__class__.__name__, action.__class__.__name__),
            "nargs":    _jsonable(action.nargs),
            "default":  _jsonable(action.default),
            "choices":  _jsonable(list(action.choices)) if action.choices is not None else None,
            "required": bool(action.required),
            "help":     action.help,
        }

    return {
        "program":     name,
        "module":      module_path,
        "description": parser.description,
        "arguments":   arguments,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="导出 ETL 的 argparse 参数定义为 JSON"
    )
    parser.add_argument(
        'program',
        nargs='?',
        choices=sorted(PROGRAMS),
        help='要自省的 ETL 程序名; 与 --all / --list 三选一'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='导出全部已注册程序'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='只输出已注册的程序名列表'
    )
    return parser


def parse_arguments() -> argparse.Namespace:
    return build_parser().parse_args()


def main() -> int:
    args = parse_arguments()

    if args.list:
        print(json.dumps(sorted(PROGRAMS), ensure_ascii=False, indent=2))
        return 0

    if args.all:
        names = sorted(PROGRAMS)
    elif args.program:
        names = [args.program]
    else:
        print("错误: 需指定程序名, 或使用 --all / --list", file=sys.stderr)
        return 1

    result: dict[str, dict] = {}
    for name in names:
        try:
            result[name] = describe(name)
        except KeyError:
            print(f"错误: 程序 '{name}' 未在 PROGRAMS 中注册", file=sys.stderr)
            return 1
        except ImportError as e:
            print(f"错误: 导入 '{PROGRAMS.get(name, name)}' 失败: {e}", file=sys.stderr)
            return 1
        except AttributeError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1

    payload = result if (args.all or len(names) > 1) else result[names[0]]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
