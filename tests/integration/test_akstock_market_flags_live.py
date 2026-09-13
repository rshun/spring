# 修改记录:
#   2026-09-13  Claude  新增停牌/涨跌停 akshare 接口字段形状集成测试(Task 15)
"""真实网络: 只验证 akshare 上游字段名与形状未变, 不断言具体数值。

akshare 上游改列名是真实风险, 这层是唯一能提前发现的地方。
用 -m "not integration" 可跳过。
"""
import pytest

from datasource import akstock
from tests.unit.test_akstock_suspension import SUSPENSION_COLUMNS
from tests.unit.test_akstock_limit_pool import LIMIT_POOL_COLUMNS

# 选一个确定的历史交易日，避免依赖「今天是不是交易日」
TRADE_DATE = "20260911"


@pytest.mark.integration
def test_fetch_suspension_live_shape():
    """正例: 真实接口返回帧列名与标准列集一致"""
    df = akstock.fetch_suspension(TRADE_DATE)
    assert list(df.columns) == SUSPENSION_COLUMNS


@pytest.mark.integration
def test_fetch_limit_pool_live_shape():
    """正例: 涨停池真实接口列对齐"""
    df = akstock.fetch_limit_pool(TRADE_DATE)
    assert list(df.columns) == LIMIT_POOL_COLUMNS


@pytest.mark.integration
def test_fetch_limit_down_pool_live_shape():
    """正例: 跌停池真实接口列对齐"""
    df = akstock.fetch_limit_down_pool(TRADE_DATE)
    assert list(df.columns) == LIMIT_POOL_COLUMNS


@pytest.mark.integration
def test_limit_pool_symbols_are_six_digit_strings():
    """反例守卫: 代码列被推断成 int 会丢前导零, 导致 000001 变成 1"""
    df = akstock.fetch_limit_pool(TRADE_DATE)
    if df.empty:
        pytest.skip("该日涨停池为空，无可校验样本")
    assert df["symbol"].map(lambda s: isinstance(s, str) and len(s) == 6).all()
