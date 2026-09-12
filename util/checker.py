# 修改记录:
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
import logging
from dataclasses import dataclass, field

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
