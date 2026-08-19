# 修改记录:
#   2026-08-19  Claude  新增：CLI 接口契约自测(build_parser / describe_cli / pipeline.yaml / C1b 心跳)
"""
CLI 契约自测——「spring 的对外接口是稳定的」

跨仓协作方(etl-quant-mcp)只认三个出口: 命令行 + 退出码、describe_cli 的 JSON、日志文件。
本文件守住其中前两个, 外加 pipeline.yaml 与 C1b 心跳。
spring 改了 CLI 而没同步 → 本文件红; MCP 用错参数 → 由 MCP 仓库自己的测试红。
两边都不需要对方在场。

退出码本身由 tests/unit/test_etl_exit_codes.py 覆盖, 此处不重复。
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yaml

from etl import adjust, fetch_index, fill_shares, fill_volratio, import_daily, update_limit
from tools import describe_cli

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 程序名 → 模块，与 describe_cli.PROGRAMS 应当一致
MODULES = {
    "adjust":        adjust,
    "import_daily":  import_daily,
    "fetch_index":   fetch_index,
    "fill_volratio": fill_volratio,
    "update_limit":  update_limit,
    "fill_shares":   fill_shares,
}

# 6 个程序共有的参数, MCP 侧的通用调用面
COMMON_FLAGS = {
    "begin":     "-b",
    "end":       "-e",
    "codes":     "-c",
    "exchanges": "-x",
}

EXCHANGE_CHOICES = ["sh", "sz", "bj", "all"]


def _actions(module) -> dict:
    return {a.dest: a for a in module.build_parser()._actions if a.dest != "help"}


# ── 一、argparse 接口稳定性 ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(MODULES), ids=sorted(MODULES))
def test_build_parser_exposed(name):
    """正例: 6 个程序都必须提供 build_parser(), 这是自省出口的前提"""
    module = MODULES[name]
    assert hasattr(module, "build_parser"), f"{name} 缺少 build_parser()"
    parser = module.build_parser()
    assert parser.description, f"{name} 的 parser 缺少 description"


@pytest.mark.parametrize("name", sorted(MODULES), ids=sorted(MODULES))
def test_common_flags_present(name):
    """正例: -b/-e/-c/-x 是 6 个程序的公共参数面, 少一个 MCP 的通用调用就会断"""
    actions = _actions(MODULES[name])
    for dest, short in COMMON_FLAGS.items():
        assert dest in actions, f"{name} 缺少参数 {dest}"
        assert short in actions[dest].option_strings, f"{name} 的 {dest} 缺少短选项 {short}"
        assert "--" + dest in actions[dest].option_strings, f"{name} 的 {dest} 缺少长选项"


@pytest.mark.parametrize("name", sorted(MODULES), ids=sorted(MODULES))
def test_exchanges_choices_consistent(name):
    """正例: 交易所枚举在 6 个程序间必须一致, MCP 侧按同一份白名单校验"""
    action = _actions(MODULES[name])["exchanges"]
    assert list(action.choices) == EXCHANGE_CHOICES
    assert action.default == ["all"]


@pytest.mark.parametrize("name", ["adjust", "import_daily", "fetch_index"])
def test_source_choices_include_bstock(name):
    """正例: 三个下载型程序都必须支持 bstock(macOS 上唯一可用的数据源)"""
    action = _actions(MODULES[name])["source"]
    assert "bstock" in action.choices
    assert action.default == "bstock"


@pytest.mark.parametrize("name", ["fill_volratio", "update_limit", "fill_shares"])
def test_fill_programs_have_forcerun(name):
    """正例: 三个补齐型程序都必须有 -f/--forcerun"""
    action = _actions(MODULES[name])["forcerun"]
    assert "-f" in action.option_strings
    assert action.default is False


def test_import_daily_has_print_only():
    """正例: -p 干跑开关是 MCP 侧安全冒烟的依据, 不得消失"""
    action = _actions(import_daily)["print_only"]
    assert "-p" in action.option_strings
    assert action.default is False


def test_build_parser_has_no_side_effect_on_argv():
    """反例: build_parser() 只能构造 parser, 不得触发解析(否则自省会读到 pytest 的 argv)"""
    for module in MODULES.values():
        module.build_parser()  # 不抛 SystemExit 即通过


# ── 二、describe_cli 自省出口 ─────────────────────────────────────────────────

def test_registry_matches_modules():
    """正例: describe_cli 的注册表与实际程序集合一致, 防止新增 ETL 漏注册"""
    assert set(describe_cli.PROGRAMS) == set(MODULES)


@pytest.mark.parametrize("name", sorted(MODULES), ids=sorted(MODULES))
def test_describe_structure(name):
    """正例: 自省输出的顶层结构固定, MCP 按此解析"""
    d = describe_cli.describe(name)
    assert set(d) == {"program", "module", "description", "arguments"}
    assert d["program"] == name
    assert d["module"] == f"etl.{name}"
    assert d["description"]

    for dest, spec in d["arguments"].items():
        assert set(spec) == {"flags", "type", "action", "nargs",
                             "default", "choices", "required", "help"}, dest
        assert spec["flags"], f"{name}.{dest} 没有 flags"
        assert spec["help"], f"{name}.{dest} 缺 help——help 是语义传给模型的唯一通道(契约 C4)"


@pytest.mark.parametrize("name", sorted(MODULES), ids=sorted(MODULES))
def test_describe_json_serializable(name):
    """正例: 自省结果必须能 JSON 序列化, 否则跨进程传不过去"""
    json.dumps(describe_cli.describe(name), ensure_ascii=False)


def test_describe_reflects_argparse():
    """正例: 自省结果与 argparse 定义一致(抽查 import_daily)"""
    args = describe_cli.describe("import_daily")["arguments"]
    assert args["source"]["choices"] == ["lday", "bstock", "tdx"]
    assert args["exchanges"]["choices"] == EXCHANGE_CHOICES
    assert args["exchanges"]["nargs"] == "+"
    assert args["print_only"]["action"] == "store_true"
    assert args["begin"]["flags"] == ["-b", "--begin"]


def test_describe_covers_all_common_flags():
    """正例: 自省结果里 6 个程序都能看到公共参数"""
    for name in MODULES:
        args = describe_cli.describe(name)["arguments"]
        for dest in COMMON_FLAGS:
            assert dest in args, f"{name} 的自省结果缺 {dest}"


def test_describe_unknown_program_raises():
    """反例: 未注册的程序名必须报 KeyError, 不得静默返回空"""
    with pytest.raises(KeyError):
        describe_cli.describe("nosuch_program")


def test_describe_module_without_build_parser_raises(monkeypatch):
    """反例: 模块没有 build_parser() 时必须报 AttributeError, 不得返回残缺结果"""
    monkeypatch.setitem(describe_cli.PROGRAMS, "fake", "json")
    with pytest.raises(AttributeError):
        describe_cli.describe("fake")


def test_describe_cli_main_requires_target():
    """反例: 不给程序名也不给 --all/--list 时退出码为 1"""
    with patch.object(describe_cli, "parse_arguments",
                      return_value=MagicMock(list=False, all=False, program=None)):
        assert describe_cli.main() == 1


def test_describe_cli_main_all_returns_0():
    """正例: --all 正常导出退出码为 0"""
    with patch.object(describe_cli, "parse_arguments",
                      return_value=MagicMock(list=False, all=True, program=None)):
        assert describe_cli.main() == 0


# ── 三、pipeline.yaml 依赖声明（契约 C3）────────────────────────────────────────

@pytest.fixture(scope="module")
def pipeline() -> dict:
    path = PROJECT_ROOT / "etl" / "pipeline.yaml"
    assert path.exists(), "etl/pipeline.yaml 不存在"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_pipeline_covers_all_programs(pipeline):
    """正例: 6 个程序都必须在依赖表里有条目"""
    assert set(pipeline) == set(MODULES)


def test_pipeline_requires_is_list(pipeline):
    """正例: requires 必须是列表, MCP 直接拿来做拓扑排序"""
    for name, spec in pipeline.items():
        assert isinstance(spec.get("requires"), list), f"{name}.requires 不是列表"


def test_pipeline_requires_are_known(pipeline):
    """正例: 依赖项要么是已注册程序, 要么是明确记录的外部程序"""
    known = set(pipeline) | {"sync_capital"}   # sync_capital 无日期参数, 暂不纳入 MCP
    for name, spec in pipeline.items():
        for dep in spec["requires"]:
            assert dep in known, f"{name} 依赖了未知程序 {dep}"


def test_pipeline_is_acyclic(pipeline):
    """反例守卫: 依赖图不得成环, 否则拓扑排序会死循环"""
    graph = {k: list(v["requires"]) for k, v in pipeline.items()}
    resolved: set[str] = set()
    # 反复剥离入度为 0 的节点; 剥不动还有剩余即成环
    while True:
        ready = [n for n, deps in graph.items()
                 if n not in resolved and all(d in resolved or d not in graph for d in deps)]
        if not ready:
            break
        resolved.update(ready)
    assert resolved == set(graph), f"依赖成环, 无法解析: {set(graph) - resolved}"


def test_pipeline_fill_programs_depend_on_import_daily(pipeline):
    """正例: 三个补齐程序都依赖日线, 这是补数顺序的关键事实"""
    for name in ("fill_volratio", "update_limit", "fill_shares"):
        assert "import_daily" in pipeline[name]["requires"]


# ── 四、C1b 心跳（进度日志「条数或时间」双条件）────────────────────────────────

def _stock_list(n: int) -> list[tuple]:
    return [(f"60{i:04d}", "SH", "2026-08-17", "2026-08-17", "L") for i in range(n)]


def _run_batch_with_clock(monotonic_values, stock_count, heartbeat=30):
    """跑 fetch_batch_data，用假时钟驱动，返回进度日志的输出次数"""
    from datasource import bstock

    login_ok = MagicMock(error_code="0")
    fake_logger = MagicMock()

    with patch.object(bstock, "bs") as fake_bs, \
         patch.object(bstock, "fetch_stock_data",
                      return_value=(pd.DataFrame(), pd.DataFrame())), \
         patch.object(bstock, "_get_progress_heartbeat_seconds", return_value=heartbeat), \
         patch.object(bstock, "logger", fake_logger), \
         patch.object(bstock.time, "monotonic", side_effect=list(monotonic_values)):
        fake_bs.login.return_value = login_ok
        bstock.fetch_batch_data(_stock_list(stock_count))

    return sum(1 for call in fake_logger.info.call_args_list
               if "已处理" in str(call.args[0]))


def test_heartbeat_config_present():
    """正例: 心跳间隔必须可配, 且为正数"""
    from datasource.bstock import _get_progress_heartbeat_seconds
    value = _get_progress_heartbeat_seconds()
    assert isinstance(value, (int, float)) and value > 0


def test_progress_logged_on_time_trigger():
    """正例(C1b 核心): 条数不满 100, 但每轮都超过心跳间隔 → 每轮都应输出进度"""
    # 时钟: 初始 0, 三轮分别 31/62/93, 每轮距上次都 >= 30
    assert _run_batch_with_clock([0, 31, 62, 93], stock_count=3) == 3


def test_progress_timer_resets_after_log():
    """关键回归: 打完一行后必须重置计时器, 否则会退化成每轮都打"""
    # 第 1 轮 31-0=31 触发并把基准重置到 31; 后两轮 32/33 距 31 不足 30, 不应再打
    assert _run_batch_with_clock([0, 31, 32, 33], stock_count=3) == 1


def test_progress_not_logged_when_neither_condition():
    """反例: 条数不满 100 且未到心跳间隔 → 一行都不该打, 不能改成无脑每轮输出"""
    assert _run_batch_with_clock([0, 1, 2, 3], stock_count=3) == 0


def test_progress_logged_on_count_trigger():
    """正例: 时间完全不走, 满 100 只仍应按条数触发(保留原有行为)"""
    assert _run_batch_with_clock([0] * 101, stock_count=100) == 1
