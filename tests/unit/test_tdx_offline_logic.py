# 修改记录:
#   2026-06-12  Claude  新增 cw 文件按报告期截断 md5 校验的正反测试
"""tdx_offline cw 文件同步窗口逻辑测试(无网络)

背景: 通达信服务器每天批量重打包大部分历史 gpcw zip, md5 滚动变化,
全量 md5 校验会导致每天重下约百个历史文件。截断策略: 只对最近 N 个
季度(config: tdx.cw_refresh_quarters)做 md5 校验更新, 更早的报告期
"本地存在即跳过"; 缺失文件下载不受窗口限制。
"""
import hashlib
import types

import pytest

from datasource import tdx_offline


# ── filter_recent_cw_filenames 纯逻辑 ────────────────────

def test_filter_recent_keeps_latest_quarters():
    """正例：取最近 N 个报告期，更早的被排除"""
    names = [f"gpcw{rd}.zip" for rd in
             ("20231231", "20240331", "20240630", "20240930", "20241231")]
    got = tdx_offline.filter_recent_cw_filenames(names, quarters=3)
    assert got == {"gpcw20240630.zip", "gpcw20240930.zip", "gpcw20241231.zip"}


def test_filter_recent_list_shorter_than_window():
    """正例：清单不足 N 个季度时全部保留"""
    names = ["gpcw20240331.zip", "gpcw20240630.zip"]
    assert tdx_offline.filter_recent_cw_filenames(names, quarters=12) == set(names)


def test_filter_recent_nonpositive_quarters_means_no_truncation():
    """反例：quarters<=0 表示不截断，返回全部"""
    names = ["gpcw20201231.zip", "gpcw20240331.zip"]
    assert tdx_offline.filter_recent_cw_filenames(names, quarters=0) == set(names)


def test_filter_recent_malformed_names_always_kept():
    """反例：无法解析报告期的文件名安全起见保留(纳入校验)"""
    names = ["gpcw20231231.zip", "gpcw20241231.zip", "gpcwBADDATE.zip", "notes.txt"]
    got = tdx_offline.filter_recent_cw_filenames(names, quarters=1)
    assert got == {"gpcw20241231.zip", "gpcwBADDATE.zip", "notes.txt"}


# ── sync_cw_files 窗口化 md5 校验编排 ─────────────────────

class _RecordingDownloader:
    """打桩 ManyThreadDownload: 记录 run 调用并落一个占位文件"""
    calls: list[str] = []

    def run(self, url, name):
        _RecordingDownloader.calls.append(url.rsplit("/", 1)[-1])
        with open(name, "wb") as f:
            f.write(b"downloaded")


@pytest.fixture
def cw_env(tmp_path, monkeypatch):
    """搭建假服务器清单 + 本地 cw 目录: 新旧两个报告期 zip 的 md5 均与服务器不一致"""
    cw_dir = tmp_path / "cw"
    cw_dir.mkdir()
    (tmp_path / "cw_pkl").mkdir()

    old_zip = cw_dir / "gpcw20201231.zip"
    new_zip = cw_dir / "gpcw20260331.zip"
    old_zip.write_bytes(b"stale-old")
    new_zip.write_bytes(b"stale-new")

    server_lines = "\r\n".join(
        f"{name},{hashlib.md5(b'server-version').hexdigest()},100"
        for name in ("gpcw20201231.zip", "gpcw20260331.zip")
    )

    cfg = {
        "cw_txt_url": "http://fake/gpcw.txt",
        "cw_file_url": "http://fake/",
        "cw_refresh_quarters": 1,
        "download": {"tries": 1, "retry_delay": 0, "request_timeout": 1,
                     "thread_count": 1, "chunk_timeout": 1, "http_retry_total": 0,
                     "http_retry_backoff": 0, "http_retry_status_codes": []},
    }
    monkeypatch.setattr(tdx_offline, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(tdx_offline, "_get_tdx_config", lambda: cfg)
    monkeypatch.setattr(tdx_offline, "download_url",
                        lambda url: types.SimpleNamespace(text=server_lines))
    monkeypatch.setattr(tdx_offline, "ManyThreadDownload", _RecordingDownloader)
    monkeypatch.setattr(tdx_offline, "_extract_and_convert",
                        lambda zip_path, cw_dir, pkl_dir: True)
    _RecordingDownloader.calls = []
    return cfg


def test_sync_cw_files_only_refreshes_recent_window(cw_env):
    """正例：窗口=1 时仅最近报告期被重下，窗口外 md5 不一致也跳过"""
    tdx_offline.sync_cw_files()
    assert _RecordingDownloader.calls == ["gpcw20260331.zip"]


def test_sync_cw_files_full_refreshes_all(cw_env):
    """正例：full=True 全量校验，窗口外的旧报告期也重下"""
    tdx_offline.sync_cw_files(full=True)
    assert sorted(_RecordingDownloader.calls) == [
        "gpcw20201231.zip", "gpcw20260331.zip"]


def test_sync_cw_files_missing_file_downloaded_outside_window(cw_env, tmp_path):
    """反例：窗口外但本地缺失的文件仍要下载(缺失下载不受窗口限制)"""
    (tmp_path / "cw" / "gpcw20201231.zip").unlink()
    tdx_offline.sync_cw_files()
    assert "gpcw20201231.zip" in _RecordingDownloader.calls


def test_sync_cw_files_window_default_from_config(cw_env):
    """反例：config 未配置 cw_refresh_quarters 时回退默认值 12(两个报告期都在窗口内)"""
    cw_env.pop("cw_refresh_quarters")
    tdx_offline.sync_cw_files()
    assert sorted(_RecordingDownloader.calls) == [
        "gpcw20201231.zip", "gpcw20260331.zip"]
