# 修改记录:
#   2026-09-13  Claude  新建: 同花顺 dump 读取的时区转换与列对齐测试
#   2026-09-13  Claude  _write_parquet 改用 duckdb COPY 写 parquet, 不再用
#                       pandas.to_parquet(需要未安装且不打算装的 pyarrow, 依赖红线);
#                       与项目其它测试(tests/db/test_etl_tables_sync.py)写法一致
"""同花顺 dump 读取: 时区转换与列对齐。不触网。"""
import datetime

import duckdb
import pytest

from datasource import ths


# ── 毫秒转日期(最关键的一处) ────────────────────────────────────────────────

def test_ms_to_date_normal_utc8():
    """正例: 2026 年事件, UTC+8 午夜 -> 当日

    1788192000000 对应 2026-09-01 00:00:00+08:00。
    若按 UTC 解释会得到 2026-08-31 16:00, 取日期就偏了一天。
    """
    assert ths.ms_to_cn_date(1788192000000) == datetime.date(2026, 9, 1)


@pytest.mark.parametrize("ms, expected", [
    (673110000000, datetime.date(1991, 5, 2)),    # 000001.SZ, dump 中真实值
    (675702000000, datetime.date(1991, 6, 1)),    # 000002.SZ
    (676306800000, datetime.date(1991, 6, 8)),    # 000002.SZ
    (683132400000, datetime.date(1991, 8, 26)),   # 600651.SH
])
def test_ms_to_date_1991_dst(ms, expected):
    """反例(夏令时): 1991 年中国实行夏令时(UTC+9)

    这几条的毫秒值是 UTC+9 午夜。用固定 +08 偏移解释会得到前一天 23:00,
    取日期偏一天。必须用 IANA 'Asia/Shanghai' 才带历史 DST 规则。
    """
    assert ths.ms_to_cn_date(ms) == expected


def test_ms_to_date_rejects_none():
    """反例: 空值必须抛错而不是静默产出一个错误日期"""
    with pytest.raises((TypeError, ValueError)):
        ths.ms_to_cn_date(None)


# ── parquet 读取 ────────────────────────────────────────────────────────────

def _write_parquet(tmp_path, rows):
    """用 duckdb COPY 写 parquet(不用 pandas.to_parquet, 见文件头修改记录)。

    rows: list of dict, 键 thscode/ms/div/bonus/ar/ap。空 rows 也要产出带完整
    schema、0 行的 parquet —— 否则 load_xdr_events 的缺列检查会先抛 ThsError,
    test_load_empty_file_returns_empty_frame_with_columns 就测不到它本来要测的东西。
    """
    p = tmp_path / "dump.parquet"
    con = duckdb.connect()
    try:
        select = ", ".join(
            f"('{r['thscode']}', '{r['thscode'].split('.')[0]}', "
            f"{r['ms']}::BIGINT, {r.get('div', 0.0)}::DOUBLE, "
            f"{r.get('bonus', 0.0)}::DOUBLE, {r.get('ar', 0.0)}::DOUBLE, "
            f"{r.get('ap', 0.0)}::DOUBLE, 'CNY')"
            for r in rows
        ) if rows else (
            "('x', 'x', 0::BIGINT, 0.0::DOUBLE, 0.0::DOUBLE, 0.0::DOUBLE, "
            "0.0::DOUBLE, 'CNY')"
        )
        where = "" if rows else "WHERE false"
        con.execute(f"""
            COPY (
                SELECT * FROM (VALUES {select}) AS t(
                    thscode, ticker, ex_date_ms, dividend_per_share,
                    per_share_bonus, allotment_ratio, allotment_price, currency)
                {where}
            ) TO '{p.as_posix()}' (FORMAT PARQUET)
        """)
    finally:
        con.close()
    return p


def test_load_returns_standard_columns(tmp_path):
    """正例: 列名与顺序标准化, ticker/currency 两个冗余列被丢弃"""
    p = _write_parquet(tmp_path, [{"thscode": "301237.SZ", "ms": 1788192000000, "bonus": 0.4}])
    df = ths.load_xdr_events(p)
    assert list(df.columns) == ths.XDR_COLUMNS
    assert df.loc[0, "code"] == "301237.SZ"
    assert df.loc[0, "ex_date"] == datetime.date(2026, 9, 1)
    assert df.loc[0, "per_share_bonus"] == 0.4


def test_load_drops_all_zero_rows(tmp_path):
    """反例: 四个数值字段全为 0 的无内容行被丢弃

    源数据里有 50 条这样的行, 没有任何除权内容, 留着会跟 gbbq 对不上。
    """
    p = _write_parquet(tmp_path, [
        {"thscode": "000001.SZ", "ms": 1788192000000},                 # 全零
        {"thscode": "600519.SH", "ms": 1788192000000, "div": 0.1},     # 有内容
    ])
    df = ths.load_xdr_events(p)
    assert df["code"].tolist() == ["600519.SH"]


def test_load_empty_file_returns_empty_frame_with_columns(tmp_path):
    """反例: 空 parquet -> 列齐全的空帧, 不抛错"""
    p = _write_parquet(tmp_path, [])
    df = ths.load_xdr_events(p)
    assert df.empty
    assert list(df.columns) == ths.XDR_COLUMNS


def test_load_missing_file_raises(tmp_path):
    """反例: 文件不存在必须抛错, 不能静默返回空帧当成「今天没数据」"""
    with pytest.raises(ths.ThsError):
        ths.load_xdr_events(tmp_path / "nope.parquet")


def test_load_missing_column_raises_clear_error(tmp_path):
    """反例: 上游少列 -> 点名缺哪列, 而不是裸 KeyError"""
    p = tmp_path / "bad.parquet"
    con = duckdb.connect()
    try:
        con.execute(f"""
            COPY (
                SELECT * FROM (VALUES ('600519.SH', 1788192000000::BIGINT))
                    AS t(thscode, ex_date_ms)
            ) TO '{p.as_posix()}' (FORMAT PARQUET)
        """)
    finally:
        con.close()
    with pytest.raises(ths.ThsError, match="per_share_bonus"):
        ths.load_xdr_events(p)
