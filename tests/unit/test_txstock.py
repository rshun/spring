# 修改记录:
#   2026-09-06  Claude  新增腾讯 fqkline 数据源 txstock 的正反测试(mock 网络)
#   2026-09-06  Claude  BUG-003 回归: 前值缓冲期拉取/事件裁剪/停牌缺口/无前值
#   2026-09-06  Claude  BUG-005 回归: fetch_xdr_events 新签名 (df, failed_codes),
#                       区分成功无事件/部分失败/全部失败
"""datasource.txstock 单元测试(无网络, monkeypatch requests.get)"""
import types

import pandas as pd
import pytest
import requests

from datasource import txstock


def _fake_resp(payload):
    return types.SimpleNamespace(
        status_code=200,
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


def _make_payload(symbol, klines, key="qfqday"):
    return {"code": 0, "msg": "", "data": {symbol: {key: klines}}}


def _install(monkeypatch, handler):
    """handler(param_str) -> payload dict 或抛异常"""
    calls: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["param"])
        return _fake_resp(handler(params["param"]))

    monkeypatch.setattr(txstock.requests, "get", fake_get)
    monkeypatch.setattr(txstock.time, "sleep", lambda s: None)
    return calls


# ── fetch_kline 正例 ─────────────────────────────────────

def test_fetch_kline_parses_event_metadata(monkeypatch):
    """正例：含事件元数据的 qfq 响应解析"""
    klines = [
        ["2026-07-31", "18.0", "17.956", "18.1", "17.9", "12345"],
        ["2026-08-03", "17.9", "17.96", "18.0", "17.8", "23456",
         {"nd": "2025", "fh_sh": "0.04", "djr": "2026-07-31",
          "cqr": "2026-08-03", "FHcontent": "10派0.04元"}],
    ]
    calls = _install(monkeypatch,
                     lambda p: _make_payload("sz000681", klines))

    df = txstock.fetch_kline("sz000681", "2026-07-31", "2026-08-03", "qfq")

    assert list(df.columns) == txstock.KLINE_COLUMNS
    assert len(df) == 2
    assert df.loc[0, "events"] is None
    event = df.loc[1, "events"]
    assert event["fh_sh"] == "0.04"
    assert event["djr"] == "2026-07-31"
    assert event["FHcontent"] == "10派0.04元"
    assert df.loc[1, "close"] == pytest.approx(17.96)
    assert calls == ["sz000681,day,2026-07-31,2026-08-03,640,qfq"]


def test_fetch_kline_pagination(monkeypatch):
    """正例：单窗口打满 640 根时自动向前翻页拼接"""
    dates = pd.date_range("2020-01-01", periods=645).strftime("%Y-%m-%d").tolist()
    klines = [[d, "1", "1", "1", "1", "100"] for d in dates]

    def handler(param):
        end = param.split(",")[3]
        if end == dates[-1]:
            return _make_payload("sh600519", klines[5:])
        return _make_payload("sh600519", klines[:5])

    calls = _install(monkeypatch, handler)
    df = txstock.fetch_kline("sh600519", dates[0], dates[-1], "qfq")

    assert len(calls) == 2
    # 第二次请求的窗口终点 = 第一批最早日期的前一天
    assert calls[1].split(",")[3] == "2020-01-05"
    assert len(df) == 645
    assert df["date"].tolist() == dates  # 升序、去重、完整拼接


# ── fetch_kline 反例 ─────────────────────────────────────

def test_fetch_kline_error_code_raises(monkeypatch):
    """反例：返回非 0 错误码，抛 TxstockError"""
    _install(monkeypatch, lambda p: {"code": 1, "msg": "invalid param", "data": {}})
    with pytest.raises(txstock.TxstockError, match="错误码"):
        txstock.fetch_kline("sz000681", "2026-07-31", "2026-08-03")


def test_fetch_kline_missing_data_key_raises(monkeypatch):
    """反例：响应缺少 data[symbol] 节点，抛 TxstockError"""
    _install(monkeypatch, lambda p: {"code": 0, "msg": "", "data": {}})
    with pytest.raises(txstock.TxstockError, match="缺少"):
        txstock.fetch_kline("sz000681", "2026-07-31", "2026-08-03")


def test_fetch_kline_empty_klines_returns_empty(monkeypatch):
    """反例：空 K 线数组，返回带列名的空 DataFrame 而非报错"""
    _install(monkeypatch, lambda p: _make_payload("sz000681", []))
    df = txstock.fetch_kline("sz000681", "2026-07-31", "2026-08-03")
    assert df.empty
    assert list(df.columns) == txstock.KLINE_COLUMNS


def test_fetch_kline_network_error_retries_then_raises(monkeypatch):
    """反例：持续网络异常，重试 REQUEST_TRIES 次后抛 TxstockError"""
    def handler(param):
        raise requests.ConnectionError("boom")

    calls = _install(monkeypatch, handler)
    with pytest.raises(txstock.TxstockError, match="重试"):
        txstock.fetch_kline("sz000681", "2026-07-31", "2026-08-03")
    assert len(calls) == txstock.REQUEST_TRIES


# ── fetch_xdr_events ─────────────────────────────────────

def _two_day_payloads(symbol):
    """构造已知除权案例: 事件日 2026-08-03, 前一天 factor=1.0002, 事件日=1.0"""
    event = {"nd": "2025", "fh_sh": "0.04", "djr": "2026-07-31",
             "cqr": "2026-08-03", "FHcontent": "10派0.04元"}
    qfq = [
        ["2026-07-31", "10.0", "10.0", "10.0", "10.0", "100"],
        ["2026-08-03", "10.0", "9.998", "10.0", "9.9", "200", event],
    ]
    raw = [
        ["2026-07-31", "10.002", "10.002", "10.002", "10.002", "100"],
        ["2026-08-03", "9.998", "9.998", "9.998", "9.998", "200"],
    ]
    return {"qfqday": qfq, "day": raw}


def test_fetch_xdr_events_jump_ratio(monkeypatch):
    """正例：jump_ratio = 事件日前一交易日 factor / 事件日 factor ≈ C/X"""
    def handler(param):
        symbol, _, start, end, count, adjust = param.split(",")
        key = "qfqday" if adjust == "qfq" else "day"
        return _make_payload(symbol, _two_day_payloads(symbol)[key], key=key)

    _install(monkeypatch, handler)
    df, failed = txstock.fetch_xdr_events(["000681.SZ"], "2026-07-31", "2026-08-03")

    assert failed == []
    assert len(df) == 1
    row = df.iloc[0]
    assert row["code"] == "000681.SZ"
    assert row["xdr_date"] == "2026-08-03"
    assert row["div_per10"] == pytest.approx(0.04)
    assert row["djr"] == "2026-07-31"
    assert row["content"] == "10派0.04元"
    # factor(07-31)=10.002/10.0=1.0002, factor(08-03)=1.0 -> jump=1.0002
    assert row["jump_ratio"] == pytest.approx(1.0002)


def test_fetch_xdr_events_skips_failed_stock(monkeypatch):
    """反例：单只股票拉取失败记 warning 跳过并进 failed_codes，不影响其他股票"""
    def handler(param):
        symbol, _, start, end, count, adjust = param.split(",")
        if symbol == "sz000681":
            raise requests.ConnectionError("boom")
        key = "qfqday" if adjust == "qfq" else "day"
        return _make_payload(symbol, _two_day_payloads(symbol)[key], key=key)

    _install(monkeypatch, handler)
    df, failed = txstock.fetch_xdr_events(["000681.SZ", "600519.SH"],
                                          "2026-07-31", "2026-08-03")
    assert df["code"].tolist() == ["600519.SH"]
    assert failed == ["000681.SZ"]


def test_fetch_xdr_events_all_failed(monkeypatch):
    """反例(BUG-005)：全部取数失败, 空帧 + failed_codes 覆盖全部请求代码"""
    def handler(param):
        raise requests.ConnectionError("boom")

    _install(monkeypatch, handler)
    df, failed = txstock.fetch_xdr_events(["000681.SZ", "600519.SH"],
                                          "2026-07-31", "2026-08-03")
    assert df.empty
    assert list(df.columns) == txstock.EVENT_COLUMNS
    assert failed == ["000681.SZ", "600519.SH"]


def test_fetch_xdr_events_unsupported_exchange_skipped(monkeypatch):
    """反例：非 SH/SZ 代码(如北交所)跳过不拉取，进 failed_codes"""
    calls = _install(monkeypatch,
                     lambda p: pytest.fail("不应发起请求"))
    df, failed = txstock.fetch_xdr_events(["920096.BJ"], "2026-07-31", "2026-08-03")
    assert df.empty
    assert list(df.columns) == txstock.EVENT_COLUMNS
    assert failed == ["920096.BJ"]
    assert calls == []


def test_fetch_xdr_events_no_events_returns_empty(monkeypatch):
    """边界(BUG-005)：成功取数但无事件, 空帧且 failed_codes 为空(与失败区分)"""
    klines = [["2026-07-31", "10", "10", "10", "10", "100"]]
    _install(monkeypatch,
             lambda p: _make_payload("sz000681", klines,
                                     key="qfqday" if p.endswith("qfq") else "day"))
    df, failed = txstock.fetch_xdr_events(["000681.SZ"], "2026-07-31", "2026-08-03")
    assert df.empty
    assert list(df.columns) == txstock.EVENT_COLUMNS
    assert failed == []


# ── BUG-003: 前值缓冲期 ──────────────────────────────────

def test_fetch_xdr_events_single_day_window_with_lookback(monkeypatch):
    """正例(BUG-003 复现案例)：单日窗口, 缓冲期拿到前一交易日, 跳变可计算"""
    def handler(param):
        symbol, _, start, end, count, adjust = param.split(",")
        key = "qfqday" if adjust == "qfq" else "day"
        return _make_payload(symbol, _two_day_payloads(symbol)[key], key=key)

    calls = _install(monkeypatch, handler)
    df, failed = txstock.fetch_xdr_events(["000681.SZ"], "2026-08-03", "2026-08-03")

    # 拉取起点 = start - FETCH_LOOKBACK_DAYS(30) 自然日 = 2026-07-04
    assert all(p.split(",")[2] == "2026-07-04" for p in calls)
    assert failed == []
    assert len(df) == 1
    # 事件在窗口首日, 但前值来自缓冲期的 07-31, jump 可计算
    assert df.iloc[0]["jump_ratio"] == pytest.approx(1.0002)


def test_fetch_xdr_events_clips_buffer_events(monkeypatch):
    """正例：缓冲期里的事件被裁剪回请求窗口, 不进输出"""
    buf_event = {"fh_sh": "1.0", "djr": "2026-07-09", "cqr": "2026-07-10",
                 "FHcontent": "10派1元"}
    win_event = {"fh_sh": "0.04", "djr": "2026-07-31", "cqr": "2026-08-03",
                 "FHcontent": "10派0.04元"}
    qfq = [
        ["2026-07-09", "10.0", "10.0", "10.0", "10.0", "100"],
        ["2026-07-10", "9.0", "9.0", "9.0", "9.0", "100", buf_event],
        ["2026-07-31", "9.0", "9.0", "9.0", "9.0", "100"],
        ["2026-08-03", "9.0", "8.998", "9.0", "8.9", "200", win_event],
    ]
    raw = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in qfq]
    # raw 事件日价格不同使两个事件都有真实跳变
    raw[1][2] = "9.5"
    raw[3][2] = "9.0"

    def handler(param):
        symbol = param.split(",")[0]
        key = "qfqday" if param.endswith("qfq") else "day"
        return _make_payload(symbol, qfq if key == "qfqday" else raw, key=key)

    _install(monkeypatch, handler)
    df, _ = txstock.fetch_xdr_events(["000681.SZ"], "2026-08-01", "2026-08-31")
    assert df["xdr_date"].tolist() == ["2026-08-03"]


def test_fetch_xdr_events_suspension_gap_uses_last_available_bar(monkeypatch):
    """边界：事件日前停牌(7-21 至 7-31 无 K 线), 前值取最近一根有效 bar"""
    event = {"fh_sh": "0.04", "djr": "2026-07-31", "cqr": "2026-08-03",
             "FHcontent": "10派0.04元"}
    qfq = [
        ["2026-07-20", "10.0", "10.0", "10.0", "10.0", "100"],
        ["2026-08-03", "10.0", "9.998", "10.0", "9.9", "200", event],
    ]
    raw = [
        ["2026-07-20", "10.002", "10.002", "10.002", "10.002", "100"],
        ["2026-08-03", "9.998", "9.998", "9.998", "9.998", "200"],
    ]

    def handler(param):
        symbol = param.split(",")[0]
        key = "qfqday" if param.endswith("qfq") else "day"
        return _make_payload(symbol, qfq if key == "qfqday" else raw, key=key)

    _install(monkeypatch, handler)
    df, _ = txstock.fetch_xdr_events(["000681.SZ"], "2026-08-03", "2026-08-03")
    assert df.iloc[0]["jump_ratio"] == pytest.approx(1.0002)


def test_fetch_xdr_events_listing_day_event_jump_none(monkeypatch):
    """反例：缓冲期后仍无前值(上市首日即除权), jump_ratio 保持 None"""
    event = {"fh_sh": "0.04", "djr": "2026-07-31", "cqr": "2026-08-03",
             "FHcontent": "10派0.04元"}
    klines = [["2026-08-03", "10.0", "9.998", "10.0", "9.9", "200", event]]
    _install(monkeypatch,
             lambda p: _make_payload("sz000681", klines,
                                     key="qfqday" if p.endswith("qfq") else "day"))
    df, _ = txstock.fetch_xdr_events(["000681.SZ"], "2026-08-03", "2026-08-03")
    assert len(df) == 1
    assert pd.isna(df.iloc[0]["jump_ratio"])


def test_repeated_full_page_fails_in_finite_requests(monkeypatch):
    dates = pd.date_range("2020-01-01", periods=640).strftime("%Y-%m-%d")
    rows = [[d, "1", "1", "1", "1", "100"] for d in dates]
    calls = _install(monkeypatch, lambda p: _make_payload("sz000681", rows))
    with pytest.raises(RuntimeError, match="无进度"):
        txstock.fetch_kline("sz000681", "2010-01-01", "2026-08-03")
    assert len(calls) == 2


def test_page_limit_marks_stock_failed(monkeypatch):
    dates = pd.date_range("2020-01-01", periods=640).strftime("%Y-%m-%d")
    rows = [[d, "1", "1", "1", "1", "100"] for d in dates]
    monkeypatch.setattr(txstock, "MAX_PAGES", 1)
    calls = _install(monkeypatch, lambda p: _make_payload("sz000681", rows))
    events, failed = txstock.fetch_xdr_events(["000681.SZ"], "2010-01-01", "2026-08-03")
    assert events.empty
    assert failed == ["000681.SZ"]
    assert len(calls) == 1


@pytest.mark.parametrize("node", [{}, {"qfqday": "invalid"}, {"qfqday": [["invalid"]]}])
def test_malformed_kline_marks_stock_failed(monkeypatch, node):
    _install(monkeypatch, lambda p: {"code": 0, "data": {"sz000681": node}})
    events, failed = txstock.fetch_xdr_events(["000681.SZ"], "2026-08-03", "2026-08-03")
    assert events.empty
    assert failed == ["000681.SZ"]
