"""登录足迹（对齐 `DailyCheckinServiceImpl`）。

日期口径固定为 **Asia/Shanghai**：服务器跑在 UTC 容器里时，用 `date.today()` 会让
凌晨 0–8 点的访问被记成前一天。Java 侧显式指定了 ZoneId，这里必须一致。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.checkin import UserDailyCheckin

# 用固定 +08:00 而不是 `ZoneInfo("Asia/Shanghai")`：Windows 没有系统 tzdata，
# 又不想为了一个时区引入 tzdata 依赖（容器里也不总是装）。中国自 1991 年起
# 不再实行夏令时，"今天"的判定用固定偏移与 Java 的 ZoneId 结果完全一致。
APP_ZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
_MAX_RANGE_DAYS = 365


def today_in_app_zone() -> date:
    return datetime.now(APP_ZONE).date()


class CheckinService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record_today(self, user_id: int) -> None:
        now = datetime.now(APP_ZONE).replace(tzinfo=None)
        values = {
            "user_id": user_id,
            "checkin_date": now.date(),
            "checked_at": now,
        }
        # 重复访问不报错也不用先查后插：先查后插在并发下仍会撞唯一键。
        # MySQL 用 INSERT IGNORE，SQLite 用 ON CONFLICT DO NOTHING，语义一致。
        dialect = self._db.get_bind().dialect.name
        if dialect == "mysql":
            statement = mysql_insert(UserDailyCheckin).values(**values).prefix_with("IGNORE")
        elif dialect == "sqlite":
            statement = sqlite_insert(UserDailyCheckin).values(**values).on_conflict_do_nothing()
        else:  # pragma: no cover - 只会用在 mysql/sqlite
            statement = mysql_insert(UserDailyCheckin).values(**values)
        await self._db.execute(statement)

    async def get_footprint(self, user_id: int, start_date: date, end_date: date) -> dict[str, Any]:
        if start_date > end_date:
            raise BusinessError(400, "开始日期不能晚于结束日期")
        if start_date + timedelta(days=_MAX_RANGE_DAYS - 1) < end_date:
            raise BusinessError(400, "足迹查询最多支持365天")

        range_records = await self._dates_between(user_id, start_date, end_date)
        all_records = await self._dates_between(user_id, None, None)
        return {
            "checkedDates": [day.isoformat() for day in range_records],
            "checkedDays": len(range_records),
            "longestStreak": _longest_streak(all_records),
        }

    async def _dates_between(
        self, user_id: int, start_date: date | None, end_date: date | None
    ) -> list[date]:
        query = (
            select(UserDailyCheckin.checkin_date)
            .where(UserDailyCheckin.user_id == user_id)
            .order_by(UserDailyCheckin.checkin_date.asc())
        )
        if start_date is not None and end_date is not None:
            query = query.where(
                UserDailyCheckin.checkin_date >= start_date,
                UserDailyCheckin.checkin_date <= end_date,
            )
        return list((await self._db.scalars(query)).all())


def _longest_streak(days: list[date]) -> int:
    """最长连续天数；输入必须已按日期升序，同一天不会重复（唯一键保证）。"""
    longest = 0
    current = 0
    previous: date | None = None
    for day in days:
        current = current + 1 if previous is not None and day == previous + timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest
