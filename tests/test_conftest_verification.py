"""Quick verification that conftest fixtures and helpers work correctly."""
from conftest import insert_stock_info, insert_trade_cal


def test_mem_db_fixture_available(mem_db):
    """Verify that mem_db fixture is available and initialized."""
    # Query the schema to verify tables exist
    tables = mem_db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE'"
    ).fetchall()

    table_names = [t[0] for t in tables]

    # Verify key tables exist
    assert "STOCK_INFO" in table_names
    assert "STOCK_DAILY" in table_names
    assert "TRADE_CAL" in table_names
    assert "ADJ_FACTOR" in table_names
    assert "DAILY_BASIC" in table_names


def test_insert_stock_info_helper(mem_db):
    """Verify insert_stock_info helper function works."""
    insert_stock_info(
        mem_db,
        symbol="600000",
        exchange="SH",
        board="MAIN",
        list_date="2000-01-01"
    )

    result = mem_db.execute(
        "SELECT symbol, exchange, board FROM STOCK_INFO WHERE symbol='600000'"
    ).fetchall()

    assert len(result) == 1
    assert result[0] == ("600000", "SH", "MAIN")


def test_insert_trade_cal_helper(mem_db):
    """Verify insert_trade_cal helper function works."""
    from datetime import date

    insert_trade_cal(mem_db, cal_date="2024-05-13", is_open=1)

    result = mem_db.execute(
        "SELECT cal_date, is_open FROM TRADE_CAL WHERE cal_date='2024-05-13'"
    ).fetchall()

    assert len(result) == 1
    assert result[0] == (date(2024, 5, 13), 1)
