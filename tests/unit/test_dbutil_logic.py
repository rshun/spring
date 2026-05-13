import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

from util import dbutil
from util.dbutil import _normalize_daily_df, get_candidate_data
from tests.conftest import insert_stock_info


# ── _normalize_daily_df ───────────────────────────────────────────────────────

def test_normalize_pre_close_nan_filled():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "pre_close": [float("nan")],
                       "volume": [100], "amount": [105000.0]})
    result = _normalize_daily_df(df)
    assert result["pre_close"].iloc[0] == -1


def test_normalize_no_pre_close_column():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0]})
    result = _normalize_daily_df(df)
    assert "pre_close" in result.columns
    assert result["pre_close"].iloc[0] == -1


def test_normalize_trade_status_copied_to_tradestatus():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0],
                       "trade_status": [1]})
    result = _normalize_daily_df(df)
    assert result["tradestatus"].iloc[0] == 1


def test_normalize_no_status_columns_filled_minus_one():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0]})
    result = _normalize_daily_df(df)
    assert result["tradestatus"].iloc[0] == -1


def test_normalize_tradestatus_nan_filled_minus_one():
    df = pd.DataFrame({"code": ["A"], "date": ["2023-01-03"],
                       "open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
                       "volume": [100], "amount": [105000.0],
                       "tradestatus": [float("nan")]})
    result = _normalize_daily_df(df)
    assert result["tradestatus"].iloc[0] == -1


# ── get_connection 异常 ───────────────────────────────────────────────────────

def test_get_connection_readonly_missing_file_raises(tmp_path):
    nonexistent = tmp_path / "nonexistent.db"
    with patch("util.dbutil.myutil.get_default_dbfile", return_value=nonexistent):
        with pytest.raises(FileNotFoundError):
            dbutil.get_connection(is_read_only=True)


# ── get_candidate_data 筛选逻辑 ───────────────────────────────────────────────

def _wrap_mem_db(mem_db):
    """包装 mem_db 使其 close() 成为 no-op，防止函数内 finally 关闭测试连接。"""
    mock_conn = MagicMock(wraps=mem_db)
    mock_conn.close = MagicMock()
    return mock_conn


SQL_STOCK = ("SELECT SYMBOL,EXCHANGE,LIST_DATE,DELIST_DATE,LIST_STATUS "
             "FROM STOCK_INFO WHERE BOARD <> 'INDEX'")


def test_get_candidate_data_codes_priority(mem_db):
    """黑盒：codes 参数优先级高于 exchanges。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    ["SH"],        # exchanges 指定 SH
                                    ["000001"],    # codes 指定 000001（SZ）
                                    False, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert symbols == ["000001"]   # codes 优先，SH 被忽略


def test_get_candidate_data_chinese_comma(mem_db):
    """黑盒：codes 支持中文逗号分隔。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [],
                                    ["600519，000001"],   # 中文逗号
                                    False, SQL_STOCK)
    assert len(result) == 2


def test_get_candidate_data_eff_begin_not_before_list_date(mem_db):
    """黑盒：begindate 早于 list_date 时，eff_begin 取 list_date。"""
    insert_stock_info(mem_db, "600519", "SH", "MAIN", "2001-08-27")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("1990-01-01", "2023-12-31",
                                    [], ["600519"], False, SQL_STOCK)
    assert len(result) == 1
    assert result[0][2] == "2001-08-27"  # eff_begin = list_date


def test_get_candidate_data_skip_null_list_date(mem_db):
    """list_date=NULL 的记录应被跳过。"""
    mem_db.execute(
        "INSERT INTO STOCK_INFO (code, symbol, name, exchange, board, "
        "list_date, list_status, created_at, last_updated_at) "
        "VALUES ('000002.SZ','000002','Test','SZ','MAIN',NULL,'L',now(),now())"
    )
    insert_stock_info(mem_db, "000001", "SZ", "MAIN", "1991-04-03")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], False, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert "000002" not in symbols
    assert "000001" in symbols


def test_get_candidate_data_delist_no_date_skipped(mem_db):
    """退市股无 delist_date 时应跳过并 warning。"""
    insert_stock_info(mem_db, "000003", "SZ", "MAIN", "2000-01-01",
                      delist_date=None, list_status="D")

    with patch("util.dbutil.get_connection", return_value=_wrap_mem_db(mem_db)):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], True, SQL_STOCK)
    symbols = [r[0] for r in result]
    assert "000003" not in symbols


def test_get_candidate_data_db_not_exist_returns_empty(tmp_path):
    """DB 文件不存在时捕获异常，返回空列表，不向上抛。"""
    nonexistent = tmp_path / "no.db"
    with patch("util.dbutil.myutil.get_default_dbfile", return_value=nonexistent):
        result = get_candidate_data("2023-01-01", "2023-12-31",
                                    [], [], False, SQL_STOCK)
    assert result == []
