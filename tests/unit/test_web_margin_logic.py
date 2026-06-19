from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from datasource import web


def test_http_download_writes_content(tmp_path):
    dest = tmp_path / "f.xls"
    resp = MagicMock()
    resp.content = b"hello"
    resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=resp) as mget:
        out = web._http_download("http://x/y", dest, timeout=5, tries=2, delay=0)
    assert out == dest
    assert dest.read_bytes() == b"hello"
    mget.assert_called_once()


def test_http_download_raises_after_retries(tmp_path):
    dest = tmp_path / "f.xls"
    with patch("requests.get", side_effect=RuntimeError("boom")) as mget, \
         patch("time.sleep"):
        with pytest.raises(RuntimeError):
            web._http_download("http://x/y", dest, timeout=5, tries=3, delay=0)
    assert mget.call_count == 3
