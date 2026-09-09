"""完整 local 快照的迟到、撤销、历史回填与同步回归，仅使用内存库。"""
import pytest
from tests.db.test_adjust_local import _insert_xdr_event, _insert_close, _local_fetch
from etl.adjust import process_and_save_adjust_factors
from tools.export_etl_tables import export_tables, resolve_table_specs
from tools.import_etl_tables import import_tables


def seed(conn):
    for date in ['2025-05-08', '2025-05-09', '2025-05-12', '2025-05-13']:
        conn.execute('INSERT INTO TRADE_CAL(cal_date,is_open) VALUES (?,1)', [date])
    _insert_close(conn, '000001.SZ', '2025-05-08', 10)
    _insert_close(conn, '000001.SZ', '2025-05-09', 10)


def run(conn, begin, end):
    stocks = [('000001', 'SZ', begin, end, 'L')]
    frame = _local_fetch(conn, stocks)
    process_and_save_adjust_factors(frame, stocks, conn, event_table='ADJ_FACTOR_LOCAL')


def values(conn):
    return conn.execute('SELECT fore_factor,back_factor FROM ADJ_FACTOR ORDER BY trade_date').fetchall()


def test_late_event_and_correction_keep_fixed_base(mem_db):
    seed(mem_db)
    run(mem_db, '2025-05-08', '2025-05-09')
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    run(mem_db, '2025-05-12', '2025-05-12')
    assert values(mem_db) == [(0.5, 1), (1, 2), (1, 2)]
    mem_db.execute("UPDATE CAPITAL_DETAIL SET bonus_share=20 WHERE code='000001'")
    run(mem_db, '2025-05-12', '2025-05-12')
    assert [v[1] for v in values(mem_db)] == [1, 3, 3]
    run(mem_db, '2025-05-12', '2025-05-12')
    assert [v[1] for v in values(mem_db)] == [1, 3, 3]
    assert mem_db.execute('SELECT base_factor FROM ADJ_FACTOR_LOCAL_STATE').fetchone()[0] == 1


def test_new_event_rebases_historical_fore(mem_db):
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    run(mem_db, '2025-05-09', '2025-05-09')
    _insert_xdr_event(mem_db, '000001', '2025-05-12', bonus_share=10)
    run(mem_db, '2025-05-12', '2025-05-12')
    assert values(mem_db) == [(0.5, 2), (1, 4)]


def test_withdraw_last_and_all_events(mem_db):
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    _insert_xdr_event(mem_db, '000001', '2025-05-12', bonus_share=10)
    run(mem_db, '2025-05-08', '2025-05-12')
    mem_db.execute("DELETE FROM CAPITAL_DETAIL WHERE date='2025-05-12'")
    run(mem_db, '2025-05-13', '2025-05-13')
    assert mem_db.execute('SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL').fetchone()[0] == 1
    assert [v[1] for v in values(mem_db)] == [1, 2, 2, 2]
    mem_db.execute('DELETE FROM CAPITAL_DETAIL')
    run(mem_db, '2025-05-13', '2025-05-13')
    assert mem_db.execute('SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL').fetchone()[0] == 0
    assert values(mem_db) == [(1, 1)] * 4


def test_gap_and_pre_chain_base_survive_backfill(mem_db):
    seed(mem_db)
    mem_db.execute("INSERT INTO ADJ_FACTOR(code,trade_date,fore_factor,back_factor,adjust_factor) VALUES ('000001.SZ','2025-05-08',1,4.2,4.2)")
    _insert_xdr_event(mem_db, '000001', '2025-05-12', bonus_share=10)
    run(mem_db, '2025-05-09', '2025-05-12')
    assert [v[1] for v in values(mem_db)] == [4.2, 4.2, 8.4]
    run(mem_db, '2025-05-08', '2025-05-12')
    assert values(mem_db) == [(0.5, 4.2), (0.5, 4.2), (1, 8.4)]


def test_incomplete_source_never_removes_old_event(mem_db):
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    run(mem_db, '2025-05-09', '2025-05-09')
    before = values(mem_db)
    mem_db.execute("UPDATE CAPITAL_DETAIL SET allotment_share=2,allotment_price=NULL")
    with pytest.raises(RuntimeError, match='事件链不完整'):
        run(mem_db, '2025-05-12', '2025-05-12')
    assert values(mem_db) == before
    assert mem_db.execute('SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL').fetchone()[0] == 1


def test_missing_legacy_base_is_not_guessed(mem_db):
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    mem_db.execute("INSERT INTO ADJ_FACTOR(code,trade_date,fore_factor,back_factor,adjust_factor) VALUES ('000001.SZ','2025-05-12',1,4,4)")
    with pytest.raises(RuntimeError, match='可信基准'):
        run(mem_db, '2025-05-13', '2025-05-13')
    assert values(mem_db) == [(1, 4)]


def test_snapshot_sync_propagates_full_withdrawal(mem_db, tmp_path):
    from tests.db.test_etl_tables_sync import _fresh_db
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    run(mem_db, '2025-05-08', '2025-05-09')
    specs = resolve_table_specs(['adjust'])
    target = _fresh_db()
    try:
        export_tables(mem_db, specs, '2025-05-09', '2025-05-09', tmp_path / 'first')
        import_tables(target, tmp_path / 'first', list(specs))
        mem_db.execute('DELETE FROM CAPITAL_DETAIL')
        run(mem_db, '2025-05-12', '2025-05-12')
        export_tables(mem_db, specs, '2025-05-12', '2025-05-12', tmp_path / 'second')
        import_tables(target, tmp_path / 'second', list(specs))
        assert target.execute('SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL').fetchone()[0] == 0
        assert values(target) == values(mem_db)
        assert target.execute('SELECT base_factor FROM ADJ_FACTOR_LOCAL_STATE').fetchone()[0] == 1
    finally:
        target.close()



def test_mixed_snapshot_files_rejected_before_mutation(mem_db, tmp_path):
    from tests.db.test_etl_tables_sync import _fresh_db
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    run(mem_db, '2025-05-09', '2025-05-09')
    specs = resolve_table_specs(['adjust'])
    export_tables(mem_db, specs, '2025-05-09', '2025-05-09', tmp_path)
    target = _fresh_db()
    try:
        import_tables(target, tmp_path, list(specs))
        before = values(target)
        with (tmp_path / 'adj_factor_local.parquet').open('ab') as stream:
            stream.write(b'mixed-batch')
        with pytest.raises(ValueError, match='不匹配'):
            import_tables(target, tmp_path, list(specs))
        assert values(target) == before
        assert target.execute('SELECT COUNT(*) FROM ADJ_FACTOR_LOCAL').fetchone()[0] == 1
    finally:
        target.close()


def test_unchanged_snapshot_does_not_rewrite_old_dates(mem_db):
    seed(mem_db)
    _insert_xdr_event(mem_db, '000001', '2025-05-09', bonus_share=10)
    run(mem_db, '2025-05-09', '2025-05-09')
    old = mem_db.execute('SELECT updated_at FROM ADJ_FACTOR').fetchone()[0]
    run(mem_db, '2025-05-12', '2025-05-12')
    assert mem_db.execute("SELECT updated_at FROM ADJ_FACTOR WHERE trade_date='2025-05-09'").fetchone()[0] == old
