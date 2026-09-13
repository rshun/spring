# 修改记录:
#   2026-09-13  Claude  新建同花顺 API 取数测试(全 mock, 不触网、不使用真实 key)
#   2026-09-13  Claude  补充网络异常路径的密钥不泄漏断言(异常文本+日志两条出口)
"""同花顺 API 取数。全部 mock, 不触网、不使用真实 key。"""
import datetime

import pytest

from datasource import ths


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _ok_payload(items):
    return {"thscode": "600519.SH", "ticker": "600519", "item": items}


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    """所有用例都注入假 key; 绝不使用真实密钥"""
    monkeypatch.setenv("THS_API_KEY", "test-key-not-real")


def test_parses_full_payload(monkeypatch):
    """正例: 字段齐全时正确解析, 日期按 Asia/Shanghai 转换"""
    monkeypatch.setattr(ths.requests, "get", lambda *a, **k: _Resp(_ok_payload([
        {"ex_date_ms": 1788192000000, "dividend_per_share": 0.0,
         "per_share_bonus": 0.4, "allotment_ratio": 0.0, "allotment_price": 0.0},
    ])))
    df = ths.fetch_xdr_events("301237.SZ")
    assert list(df.columns) == ths.XDR_COLUMNS
    assert df.loc[0, "ex_date"] == datetime.date(2026, 9, 1)
    assert df.loc[0, "per_share_bonus"] == 0.4


def test_missing_allotment_fields_filled_with_none(monkeypatch, caplog):
    """反例(关键): 文档未承诺返回配股字段, 缺失时补 None 且告警, 不抛错"""
    monkeypatch.setattr(ths.requests, "get", lambda *a, **k: _Resp(_ok_payload([
        {"ex_date_ms": 1788192000000, "dividend_per_share": 0.1, "per_share_bonus": 0.0},
    ])))
    with caplog.at_level("WARNING", logger="etl.datasource.ths"):
        df = ths.fetch_xdr_events("600519.SH")
    assert df.loc[0, "allotment_ratio"] is None
    assert df.loc[0, "allotment_price"] is None
    assert "allotment" in caplog.text


def test_empty_item_returns_empty_frame(monkeypatch):
    """正例: 该股无事件 -> 列齐全的空帧, 不抛错"""
    monkeypatch.setattr(ths.requests, "get", lambda *a, **k: _Resp(_ok_payload([])))
    df = ths.fetch_xdr_events("600519.SH")
    assert df.empty
    assert list(df.columns) == ths.XDR_COLUMNS


def test_api_key_error_raises_without_leaking_key(monkeypatch):
    """反例: code=2001 报明确错误, 且错误信息不得包含 key 的值"""
    monkeypatch.setattr(ths.requests, "get",
                        lambda *a, **k: _Resp({"code": 2001, "msg": "invalid api key"}))
    with pytest.raises(ths.ThsError) as ei:
        ths.fetch_xdr_events("600519.SH")
    assert "2001" in str(ei.value)
    assert "test-key-not-real" not in str(ei.value)


def test_missing_key_raises_clear_error(monkeypatch):
    """反例: 未设置 THS_API_KEY 时给出可操作的提示"""
    monkeypatch.delenv("THS_API_KEY", raising=False)
    with pytest.raises(ths.ThsError, match="THS_API_KEY"):
        ths.fetch_xdr_events("600519.SH")


def test_date_range_passed_through(monkeypatch):
    """正例: begin/end 以 from/to 传给接口"""
    seen = {}

    def _capture(url, params=None, headers=None, timeout=None):
        seen.update(params or {})
        return _Resp(_ok_payload([]))

    monkeypatch.setattr(ths.requests, "get", _capture)
    ths.fetch_xdr_events("600519.SH", "2026-01-01", "2026-09-13")
    assert seen["thscode"] == "600519.SH"
    assert seen["from"] == "2026-01-01"
    assert seen["to"] == "2026-09-13"


def test_network_failure_retries_then_raises(monkeypatch):
    """反例: 网络失败重试耗尽后抛 ThsError, 不返回空帧

    并锁住密钥安全: 异常链里转述了 str(e), 而 headers 含 key ——
    依赖「requests 不把 headers 塞进异常文本」这个隐式假设,
    用断言把它固化, 将来换 HTTP 库或加自定义异常时会红。
    """
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise OSError("connection reset")

    monkeypatch.setattr(ths.requests, "get", _boom)
    monkeypatch.setattr(ths.time, "sleep", lambda s: None)
    with pytest.raises(ths.ThsError) as ei:
        ths.fetch_xdr_events("600519.SH")
    assert calls["n"] >= 2
    assert "test-key-not-real" not in str(ei.value)


def test_network_failure_log_does_not_leak_key(monkeypatch, caplog):
    """反例: 重试告警日志里也不得出现 key

    日志与异常文本是两条独立出口, 分别断言。
    """
    def _boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(ths.requests, "get", _boom)
    monkeypatch.setattr(ths.time, "sleep", lambda s: None)
    with caplog.at_level("WARNING", logger="etl.datasource.ths"):
        with pytest.raises(ths.ThsError):
            ths.fetch_xdr_events("600519.SH")
    assert "test-key-not-real" not in caplog.text
