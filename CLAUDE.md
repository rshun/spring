# Spring 开发规范

本文件由 Claude Code 每次会话自动加载，约束 AI 与开发者的协作方式。**以下规则均为强制要求。**

## 一、分支策略

- **master 是保护分支，任何情况下不得在 master 上直接开发或提交。**
- 所有开发在 `dev` 或 `feature/*` 分支进行，本地测试通过后再合并到 master。
- 开始任何代码改动前，先确认当前分支：`git rev-parse --abbrev-ref HEAD`。
  若发现在 master，必须先切到 `dev` 或新建分支，再开始改动。

## 二、测试规范

测试分三层（详见 README）：

| 层级 | 目录 | 说明 |
|------|------|------|
| Unit | `tests/unit/` | 纯逻辑，无外部依赖，用 mock |
| DB | `tests/db/` | SQL 逻辑，用 `mem_db` fixture（in-memory DuckDB） |
| Integration | `tests/integration/` | 真实网络，`-m "not integration"` 可跳过 |

- **所有新开发（新功能或缺陷修复）必须同时提供正、反测试案例：**
  - **正例**：正常输入下的预期结果（happy path）。
  - **反例**：异常 / 边界场景——空数据、缺字段、网络或 IO 失败、错误输入等，
    验证函数能正确报错或安全降级（如返回空 DataFrame）。
- 提交前必须本地跑通 `pytest`（至少 `pytest -m "not integration"`）。

## 三、代码修改记录

修改或新建源文件时，在文件**最顶部**（模块 docstring 之前）维护一个「修改记录」
注释块。每次改动追加一行：`日期(YYYY-MM-DD)  修改人  修改原因`。

- 修改人固定写 `Claude`。
- 注释块放在 docstring 之前不影响 docstring 生效（Python 中注释不算语句）。

示例：

```python
# 修改记录:
#   2026-05-22  Claude  新增申万官网文件下载回退数据源(akshare 失败时)
#   2026-05-23  Claude  修复日期解析在空值时报错的问题
"""
同步申万行业数据
  ...模块说明...
"""
import argparse
...
```
