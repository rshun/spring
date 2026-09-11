# 修改记录:
#   2026-09-11  Claude  新增：lday / tdx 批量取数的「全批失败必须抛错」契约，
#                       tdx 跳过北交所，tdx_offline 的 HEAD 加固与 md5 原子更新
"""datasource 批量取数与下载的失败路径契约。全部 mock，不联网、不碰真实库。

核心约定（契约 C1）：逐只标的失败只跳过、不中止整批；但**所有**候选都失败时必须抛错。
返回空表会让 etl/import_daily.py 只打一句「未获取到任何股票数据」就退出 0，
把整天的采集失败报成成功。
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from datasource import lday, tdx, tdx_offline

TRADE_DATES = ["20260908"]


# ── lday: 全批失败 ────────────────────────────────────────────────────────────

def _lday_stocks(n: int) -> list[tuple]:
    return [(f"60000{i}", "SH", "2026-09-08", "2026-09-08", "L") for i in range(n)]


def test_lday_all_files_missing_raises():
    """反例: Vipdoc 路径失效导致所有 .day 文件都读不到 → 抛错，不得返回空表"""
    with patch.object(lday.dbutil, "get_trade_dates", return_value=TRADE_DATES), \
         patch.object(lday.myutil, "get_lday_path",
                      side_effect=FileNotFoundError("目录不存在: X:/Vipdoc/sh/lday")):
        with pytest.raises(RuntimeError, match="全部 2 只"):
            lday.fetch_batch_data(_lday_stocks(2))


def test_lday_partial_failure_does_not_abort(tmp_path):
    """正例(边界): 只有部分股票缺文件 → 不抛错，成功那只照常返回。
    个别退市股缺 .day 文件是常态，不能因此中止整批。"""
    good = pd.DataFrame({"code": ["600000.SH"], "date": ["2026-09-08"], "open": [1.0],
                         "high": [1.0], "low": [1.0], "close": [1.0], "pre_close": [1.0],
                         "tradestatus": [1], "volume": [1], "amount": [1.0]})
    exists = {"sh600000.day"}
    fake_dir = MagicMock()
    fake_dir.__truediv__ = lambda self, name: _fake_file(name, exists)

    with patch.object(lday.dbutil, "get_trade_dates", return_value=TRADE_DATES), \
         patch.object(lday.myutil, "get_lday_path", return_value=fake_dir), \
         patch.object(lday, "fetch_stock_data", return_value=good):
        daily, basic = lday.fetch_batch_data(_lday_stocks(2))

    assert daily["code"].tolist() == ["600000.SH"]
    assert not basic.empty


def _fake_file(name, exists):
    f = MagicMock()
    f.is_file.return_value = Path(name).name in exists
    f.__str__ = lambda self: name
    return f


def test_lday_no_failure_empty_result_does_not_raise(tmp_path):
    """正例(关键边界): 文件都在、只是区间内没有数据(空结果) → 不算失败，不得抛错。
    判定必须看「失败只数」，不能看「结果是否为空」。"""
    fake_dir = MagicMock()
    fake_dir.__truediv__ = lambda self, name: _fake_file(name, {"sh600000.day", "sh600001.day"})

    with patch.object(lday.dbutil, "get_trade_dates", return_value=TRADE_DATES), \
         patch.object(lday.myutil, "get_lday_path", return_value=fake_dir), \
         patch.object(lday, "fetch_stock_data", return_value=pd.DataFrame()):
        daily, basic = lday.fetch_batch_data(_lday_stocks(2))

    assert daily.empty and basic.empty


# ── tdx: 全批失败 + 跳过北交所 ────────────────────────────────────────────────

@pytest.fixture
def tdx_env():
    """已连接的假 pytdx 会话"""
    with patch.object(tdx, "_connect_api", return_value=MagicMock()) as api, \
         patch.object(tdx, "_get_max_fail", return_value=100), \
         patch.object(tdx.dbutil, "get_trade_dates", return_value=TRADE_DATES):
        yield api


def test_tdx_all_stocks_fail_raises(tdx_env):
    """反例: 所有股票都取数失败 → 抛错，不得返回空表让 import_daily 退出 0"""
    with patch.object(tdx, "fetch_stock_data", side_effect=RuntimeError("连接被拒绝")):
        with pytest.raises(RuntimeError, match="全部 2 只"):
            tdx.fetch_batch_data(_lday_stocks(2))


def test_tdx_partial_failure_does_not_abort(tdx_env):
    """正例(边界): 部分失败 → 不抛错，成功那只照常返回"""
    good = pd.DataFrame({"code": ["600000.SH"], "date": ["2026-09-08"]})
    with patch.object(tdx, "fetch_stock_data",
                      side_effect=[RuntimeError("取数失败"), good]):
        daily, _ = tdx.fetch_batch_data(_lday_stocks(2))
    assert daily["code"].tolist() == ["600000.SH"]


def test_tdx_skips_bj_stocks(tdx_env):
    """正例(本次修复的回归点): 北交所(market=2) pytdx 不支持，必须跳过而不是发无效请求。
    与同文件 fetch_xdxr_data 的处理一致；此前不跳过会对 344 只 920xxx 逐一请求，
    失败计数还会被推到 max_fail 触发无谓重连。"""
    good = pd.DataFrame({"code": ["600000.SH"], "date": ["2026-09-08"]})
    stocks = [("920001", "BJ", "2026-09-08", "2026-09-08", "L"),
              ("600000", "SH", "2026-09-08", "2026-09-08", "L")]
    with patch.object(tdx, "fetch_stock_data", return_value=good) as fetch:
        tdx.fetch_batch_data(stocks)
    assert fetch.call_count == 1                       # 只请求了沪市那只
    assert fetch.call_args.args[2] == "SH"


def test_tdx_all_bj_does_not_raise(tdx_env):
    """正例(边界): 候选全是北交所 → 全被跳过，attempted=0，不能误判成「全批失败」"""
    stocks = [("920001", "BJ", "2026-09-08", "2026-09-08", "L")]
    with patch.object(tdx, "fetch_stock_data") as fetch:
        daily, _ = tdx.fetch_batch_data(stocks)
    fetch.assert_not_called()
    assert daily.empty


# ── tdx_offline: HEAD 探测加固 ────────────────────────────────────────────────

@pytest.fixture
def dl_cfg():
    cfg = {"download": {"tries": 2, "retry_delay": 0, "request_timeout": 30,
                        "thread_count": 2}}
    with patch.object(tdx_offline, "_get_tdx_config", return_value=cfg), \
         patch.object(tdx_offline.time, "sleep"):
        yield


def test_head_without_content_length_raises_runtimeerror(dl_cfg):
    """反例(本次修复的回归点): 服务器不返回 Content-Length 时必须抛 RuntimeError。

    此前是裸 KeyError，而 sync_cw_files 只 except RuntimeError，
    整个 sync_capital 会带栈中止，后面的 gbbq 同步全都不执行。
    """
    resp = MagicMock(headers={})
    resp.raise_for_status = MagicMock()
    with patch("requests.head", return_value=resp):
        with pytest.raises(RuntimeError, match="Content-Length"):
            tdx_offline.ManyThreadDownload()._probe_total("http://x/gpcw.zip")


def test_head_failure_raises_runtimeerror_after_retries(dl_cfg):
    """反例: HEAD 连续失败 → 重试耗尽后抛 RuntimeError（而非原始网络异常）"""
    with patch("requests.head", side_effect=OSError("连接超时")) as head:
        with pytest.raises(RuntimeError, match="HEAD"):
            tdx_offline.ManyThreadDownload()._probe_total("http://x/gpcw.zip")
    assert head.call_count == 2


def test_head_passes_timeout(dl_cfg):
    """正例(本次修复的回归点): HEAD 必须带超时，否则服务器不响应时进程永久挂起"""
    resp = MagicMock(headers={"Content-Length": "1024"})
    resp.raise_for_status = MagicMock()
    with patch("requests.head", return_value=resp) as head:
        assert tdx_offline.ManyThreadDownload()._probe_total("http://x/gpcw.zip") == 1024
    assert head.call_args.kwargs["timeout"] == 30
