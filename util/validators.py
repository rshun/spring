# util/param_validators.py
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Any

from util import myutil, dbutil

logger = logging.getLogger("etl.util.validators")

@dataclass
class ValidationError:
    field: str
    message: str


Validator = Callable[[dict[str, Any]], list[ValidationError]]


def run(ctx: dict[str, Any], validators: list[Validator], *, prefix: str = "错误") -> bool:
    """
    执行一组校验：收集所有错误后统一打印，返回 True/False
    """
    errors: list[ValidationError] = []
    for v in validators:
        errors.extend(v(ctx))

    if errors:
        for e in errors:
            logger.error(f"{prefix}: {e.field} - {e.message}")
        return False
    return True


# 基础

def v_dbfile_exists(msg: str = "数据库文件不存在, 请先运行init_db.py初始化数据库") -> Validator:
    def _v(ctx: dict[str, Any]) -> list[ValidationError]:
        return [] if myutil.dbfile_exists() else [ValidationError("dbfile", msg)]
    return _v


def v_yyyymmdd(field: str, msg: str = "日期格式应为 YYYYMMDD") -> Validator:
    def _v(ctx: dict[str, Any]) -> list[ValidationError]:
        s = str(ctx.get(field, "")).strip()
        try:
            datetime.strptime(s, "%Y%m%d")
            return []
        except Exception:
            return [ValidationError(field, msg)]
    return _v


def v_date_order(begin_field: str, end_field: str, msg: str = "起始日期不能晚于结束日期") -> Validator:
    def _v(ctx: dict[str, Any]) -> list[ValidationError]:
        b = str(ctx.get(begin_field, "")).strip()
        e = str(ctx.get(end_field, "")).strip()
        # 依赖 v_yyyymmdd 先通过；这里容错：解析失败就不重复报错
        try:
            bd = datetime.strptime(b, "%Y%m%d")
            ed = datetime.strptime(e, "%Y%m%d")
        except Exception:
            return []
        if bd > ed:
            return [ValidationError(f"{begin_field},{end_field}", msg)]
        return []
    return _v


def v_single_day_must_be_trading_day(
    begin_field: str | None = None,
    end_field: str | None = None,
    *,
    allow_non_trading: bool = False,
    tip_prefix: str = "提示",
) -> Validator:
    """
    - 仅当 begin==end 时检查是否交易日
    - 非交易日：默认返回 False（如果 allow_non_trading=True 则跳过）
    - 如果 begin_field 和 end_field 未传入，默认检查当天
    """
    def _v(ctx: dict[str, Any]) -> list[ValidationError]:
        if allow_non_trading:
            return []

        if begin_field and end_field:
            b = str(ctx.get(begin_field, "")).strip()
            e = str(ctx.get(end_field, "")).strip()
            if not b or not e:
                return []
        elif not begin_field and not end_field:
            # 如果都未指定字段，默认校验“今天”
            today_str = datetime.now().strftime("%Y%m%d")
            b = today_str
            e = today_str
        else:
            # 只有一个字段指定了？这种用法不支持，直接忽略
            return []

        if b != e:
            return []

        try:
            bd = datetime.strptime(b, "%Y%m%d")
        except Exception:
            return []

        day_str = bd.strftime("%Y-%m-%d")
        if not dbutil.check_is_trading_day(day_str):
            return [ValidationError(f"{begin_field}=={end_field}" if begin_field else "today", f"{tip_prefix}: {day_str} 为非交易日（休市）")]
        return []

    return _v
