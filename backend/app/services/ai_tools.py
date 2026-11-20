"""聊天工具（对齐 Java `ai/DailySummaryTool`、`TodayDietTool`、`UserProfileTool`）。

工具描述会原样发给模型，所以**逐字照抄 Java 的 `@Tool` 文案**：模型选不选这个工具，
取决于这几句描述，改写会让工具调用率明显变化（实测"热量"没写进描述就很少被调用）。

三个工具都是只读查询，不接受参数——要查谁由服务端的 `user_id` 决定，
绝不能让模型传用户 ID（那是越权入口）。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserProfile
from app.services.summary_service import SummaryService
from app.services.diet_service import DietService

# 工具名取 Java 方法名（langchain4j 默认用它作为 function name）。
TOOL_DAILY_SUMMARY = "getTodaySummary"
TOOL_TODAY_DIET = "getTodayDiet"
TOOL_USER_PROFILE = "getCurrentProfile"

_DAILY_SUMMARY_DESCRIPTION = (
    "查询用户今天的热量汇总，包括总摄入、运动消耗、目标热量和剩余或超出预算的热量"
)
_TODAY_DIET_DESCRIPTION = (
    "查询当前登录用户今天的饮食明细，包括食物名称、餐次、食用重量、热量、蛋白质、脂肪、碳水和记录时间。"
    "当用户询问今天吃了什么、哪一餐吃了什么、某个食物的热量或营养数据时必须调用此工具。"
)
_USER_PROFILE_DESCRIPTION = (
    "查询当前登录用户的个人资料和健康档案，包括性别、生日、身高、当前体重、目标体重和日常活动水平。"
    "当用户询问我的个人资料、我的档案、我的身体数据、自己的身高体重、基础情况，"
    "或需要根据个人身体情况给出饮食建议时，必须调用此工具。"
)


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI function-calling 形态的工具声明。"""
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_DAILY_SUMMARY,
                "description": _DAILY_SUMMARY_DESCRIPTION,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_TODAY_DIET,
                "description": _TODAY_DIET_DESCRIPTION,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_USER_PROFILE,
                "description": _USER_PROFILE_DESCRIPTION,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]


def _value(value: Any) -> str:
    """Java 侧 `value()`：null 输出 "0"。

    注意 BigDecimal 的 toString 会保留标度（`1600.00` 不会变成 `1600`），
    Python 的 Decimal 行为一致，所以这里直接 str。
    """
    return "0" if value is None else str(value)


def _meal_name(meal_type: str | None) -> str:
    if meal_type is None:
        return "未分类"
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "snack": "加餐",
    }.get(meal_type, meal_type)


def _gender_name(gender: str | None) -> str:
    if gender == "female":
        return "女"
    if gender == "male":
        return "男"
    return "未填写" if gender is None else gender


def _activity_name(level: str | None) -> str:
    if level is None:
        return "未填写"
    return {"low": "低", "medium": "中等", "high": "高"}.get(level, level)


def _profile_value(value: Any) -> str:
    return "未填写" if value is None else str(value)


async def _today_summary(db: AsyncSession, user_id: int) -> str:
    summary = await SummaryService(db).get_summary(user_id, date.today())
    target = summary.get("targetDailyCalorie")
    over = summary.get("overBudgetCalorie")
    over_budget = target is not None and target > 0 and over is not None and over > 0
    label = "多吃了" if over_budget else "还能摄入"
    balance = over if over_budget else summary.get("availableIntakeCalorie")
    return (
        "今日热量汇总：\n"
        f"总摄入：{_value(summary.get('totalIntakeCalorie'))} kcal\n"
        f"运动消耗：{_value(summary.get('totalBurnCalorie'))} kcal\n"
        f"目标摄入：{_value(target)} kcal\n"
        f"{label}：{_value(balance)} kcal"
    )


async def _today_diet(db: AsyncSession, user_id: int) -> str:
    records = await DietService(db).list_records(user_id, date.today())
    if not records:
        return "今天还没有饮食记录。"

    lines = [f"今日饮食明细（共 {len(records)} 条）："]
    for record in records:
        record_time = record.get("recordTime")
        # recordTime 是 "2026-11-18T08:30:00" 形态，取 HH:mm 与 Java 的 HH:mm 对齐。
        clock = record_time[11:16] if record_time else "未记录时间"
        lines.append(
            f"- {clock}，{_meal_name(record.get('mealType'))}："
            f"{record.get('foodNameSnapshot')}，"
            f"{_value(record.get('quantityG'))}g，"
            f"{_value(record.get('calorieSnapshot'))} kcal；"
            f"蛋白质 {_value(record.get('proteinSnapshot'))}g，"
            f"脂肪 {_value(record.get('fatSnapshot'))}g，"
            f"碳水 {_value(record.get('carbSnapshot'))}g"
        )
    return "\n".join(lines) + "\n"


async def _user_profile(db: AsyncSession, user_id: int) -> str:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        return "用户尚未填写健康档案。"
    return (
        "用户健康档案：\n"
        f"性别：{_gender_name(profile.gender)}\n"
        f"生日：{_profile_value(profile.birthday)}\n"
        f"身高：{_profile_value(profile.height_cm)} cm\n"
        f"当前体重：{_profile_value(profile.current_weight_kg)} kg\n"
        f"目标体重：{_profile_value(profile.target_weight_kg)} kg\n"
        f"日常活动水平：{_activity_name(profile.activity_level)}"
    )


_TOOL_IMPLS = {
    TOOL_DAILY_SUMMARY: _today_summary,
    TOOL_TODAY_DIET: _today_diet,
    TOOL_USER_PROFILE: _user_profile,
}


async def call_tool(name: str, db: AsyncSession, user_id: int) -> str:
    """执行一个工具；未知工具回一句可读的错误，而不是抛异常打断整轮回答。

    模型偶尔会编一个工具名。抛异常会让用户看到 502，而它本来只需要重新组织回答。
    """
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        return f"未知工具：{name}，请直接依据已有信息回答。"
    try:
        return await impl(db, user_id)
    except Exception as error:  # noqa: BLE001 - 单个工具失败不该毁掉整轮
        return f"查询失败（{error.__class__.__name__}），请告知用户稍后重试，不要编造数据。"
