# 修改记录:
#   2026-09-12  Claude  新增 set_diff/num_close 比较器与 CSV 落盘、日志输出
#   2026-09-12  Claude  新建核对框架: 五态状态机 + 外部源可用性三条判定规则
"""核对框架: 结果状态机与外部数据源可用性判定

状态五态:
    ok              一致
    mismatch        有差异(写 CSV)
    source_missing  区间内所有日期外部表均无数据 -> 整项跳过, 绝不当成 ok
    partial         区间内部分日期无数据 -> 有数据的日期照常核对
    error           检查本身出错

三条判定规则(缺一不可, 见设计文档 6.1):
  1. 按日逐天判定, 不按区间整体判 —— 否则缺几天的信息全丢。
  2. LIMIT_POOL_DAILY 按 (trade_date, limit_type) 判 —— 一张表装两个池,
     当日可能有涨停行而无跌停行, 按整表判会把库内每条跌停标记误报成「多标」。
  3. 表内当日 0 行一律视为「未取到数据」, 不做核对 —— 0 行既可能是 ETL 未跑,
     也可能是接口如实返回空, 两者在表里无法区分, 本版统一按前者处理。
"""
import csv
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

logger = logging.getLogger("etl.util.checker")

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_SOURCE_MISSING = "source_missing"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"

# 逐条日志的保护阈值: 超出则截断并指向 CSV。正常量级不会触发,
# 它防的是「某天整批错位」这类失控情形把日志刷爆。
LOG_DETAIL_LIMIT = 200


@dataclass
class CheckResult:
    """单个核对项的结构化结果"""
    label: str
    status: str = STATUS_OK
    count: int = 0
    rows: list[dict] = field(default_factory=list)
    missing_dates: list[str] = field(default_factory=list)
    csv_path: str | None = None
    blocking: bool = False

    def to_json(self) -> dict:
        """JSON 出口用的扁平结构(不含明细行, 明细以 CSV 落盘)"""
        return {
            "label": self.label,
            "count": self.count,
            "status": self.status,
            "missing_dates": list(self.missing_dates),
        }


def split_dates_by_source(conn: duckdb.DuckDBPyConnection,
                          table: str,
                          trade_dates: list[str],
                          extra_where: str | None = None,
                          extra_params: list | None = None
                          ) -> tuple[list[str], list[str]]:
    """把交易日按「外部表当日有无数据」分成两组(规则 1、2、3)

    参数:
        table:        外部事实表名
        trade_dates:  YYYY-MM-DD 列表
        extra_where:  附加条件(如 "limit_type = ?"), 用于按方向分别判定
        extra_params: 附加条件的参数
    返回:
        (有数据的日期, 无数据的日期), 均保持入参顺序
    """
    has: list[str] = []
    missing: list[str] = []
    sql = f"SELECT COUNT(*) FROM {table} WHERE trade_date = ?"
    if extra_where:
        sql += f" AND {extra_where}"

    for dt in trade_dates:
        params = [dt] + list(extra_params or [])
        cnt = conn.execute(sql, params).fetchone()[0]
        (has if cnt > 0 else missing).append(dt)
    return has, missing


def resolve_status(has_dates: list[str], missing_dates: list[str],
                   mismatch_count: int) -> str:
    """由「有数据日期 / 无数据日期 / 差异条数」归并出核对项状态

    一天数据都没有就是 source_missing —— 这是最关键的一条:
    两边都空绝不能判 ok(check_adjust BUG-005 即此类)。
    """
    if not has_dates:
        return STATUS_SOURCE_MISSING
    if missing_dates:
        return STATUS_PARTIAL
    return STATUS_MISMATCH if mismatch_count else STATUS_OK


# CSV 落盘目录。测试通过 monkeypatch 覆盖此模块级变量重定向到 tmp_path。
CSV_DIR = Path(__file__).resolve().parents[1] / "csv"


def set_diff(source_keys: set, db_keys: set) -> tuple[list, list]:
    """双向集合差，返回 (只在外部源, 只在库内)，均升序

    调用方必须先用 split_dates_by_source 判定源可用性：源为空时
    本函数会把全部库内键报成 only_in_db，那是整片误报而非真差异。
    """
    return sorted(source_keys - db_keys), sorted(db_keys - source_keys)


def num_close(a, b, rel_tol: float):
    """相对容差比较。相等 True / 超容差 False / 任一侧缺失 None(无法比较)

    返回 None 而非 False，是为了把「值不对」和「没法比」分开——
    后者不该算成差异，否则会把缺数据伪装成数据错误。
    """
    if a is None or b is None:
        return None
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if math.isnan(fa) or math.isnan(fb):
        return None
    # 基准为 0 时无法做相对比较，退化为绝对比较
    base = max(abs(fa), abs(fb))
    if base == 0:
        return True
    return abs(fa - fb) / base <= rel_tol


def write_diff_csv(tag: str, begin: str, end: str,
                   header: list[str], rows: list[tuple]) -> str | None:
    """把差异明细写入 csv/check_<tag>_<begin>_<end>.csv；无差异返回 None

    不落空文件：空文件会让人误以为「查过且有输出」。
    """
    if not rows:
        return None
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    path = CSV_DIR / f"check_{tag}_{begin}_{end}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return str(path)


def log_rows(label: str, rows: list[str]) -> None:
    """逐条输出异常行。正确的项不调用本函数——正确一律不输出日志。"""
    if not rows:
        return
    for line in rows[:LOG_DETAIL_LIMIT]:
        logger.warning(f"[{label}] {line}")
    if len(rows) > LOG_DETAIL_LIMIT:
        logger.warning(f"[{label}] 共 {len(rows)} 条，已截断前 "
                       f"{LOG_DETAIL_LIMIT} 条，完整明细见 CSV")
