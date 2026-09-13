"""load_env: 解析 .env 到 os.environ。不引入 python-dotenv(依赖红线)。"""
import os

from util.myutil import load_env


def _write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return p


def test_parses_simple_pairs(tmp_path, monkeypatch):
    """正例: KEY=VALUE 被写进 os.environ"""
    monkeypatch.delenv("THS_API_KEY", raising=False)
    monkeypatch.delenv("OTHER_KEY", raising=False)
    n = load_env(_write(tmp_path, "THS_API_KEY=abc123\nOTHER_KEY=xyz\n"))
    assert n == 2
    assert os.environ["THS_API_KEY"] == "abc123"
    assert os.environ["OTHER_KEY"] == "xyz"


def test_strips_whitespace_and_quotes(tmp_path, monkeypatch):
    """正例: 键值两侧空白与成对引号被剥离"""
    monkeypatch.delenv("THS_API_KEY", raising=False)
    load_env(_write(tmp_path, '  THS_API_KEY = "abc123"  \n'))
    assert os.environ["THS_API_KEY"] == "abc123"


def test_existing_env_wins(tmp_path, monkeypatch):
    """正例(优先级): 已存在的真实环境变量不被 .env 覆盖"""
    monkeypatch.setenv("THS_API_KEY", "from_real_env")
    load_env(_write(tmp_path, "THS_API_KEY=from_file\n"))
    assert os.environ["THS_API_KEY"] == "from_real_env"


def test_missing_file_returns_zero(tmp_path):
    """反例: 文件不存在返回 0, 不抛错(未配置 .env 是常态)"""
    assert load_env(tmp_path / "nope.env") == 0


def test_skips_comments_blanks_and_malformed(tmp_path, monkeypatch):
    """反例: 注释行/空行/无等号的行全部跳过, 不抛错"""
    monkeypatch.delenv("GOOD", raising=False)
    n = load_env(_write(tmp_path,
                        "# 这是注释\n\n   \nNOEQUALSIGN\nGOOD=1\n"))
    assert n == 1
    assert os.environ["GOOD"] == "1"


def test_empty_key_rejected(tmp_path):
    """反例: 等号左边为空的行不得写进环境变量"""
    assert load_env(_write(tmp_path, "=novalue\n")) == 0


def test_value_may_contain_equals(tmp_path, monkeypatch):
    """正例: 只按第一个等号切分, 值里的等号保留(base64 key 常见)"""
    monkeypatch.delenv("K", raising=False)
    load_env(_write(tmp_path, "K=a=b=c\n"))
    assert os.environ["K"] == "a=b=c"
