"""周期复盘：先算事实，再给解释。

设计 §11 的核心不是"生成一段总结"，而是**别把未知算成 0**：

- `confirmedExecutionRate = executed / (executed + explicitlySkipped)`，分母 0 则 null；
- `executionCoverage = (executed + explicitlySkipped) / dueAdoptedMeals`，
  没反馈的餐次算"待确认"，不能算"未执行"；
- 营养日均值必须说明分母是"已记录天"还是"完整记录天"，
  "完整天"只取用户自己标记过的（`diet_day_status`），不由系统推断；
- 体重样本 < 2 不推断趋势，更不推断脂肪变化；
- 复盘的假设用"可能"表述，不断言因果。

报告带 `input_hash`：同一周期输入没变就返回原报告；输入变了（补记、改执行状态）
把旧报告标 `stale` 并生成新报告——历史结论不被静默改写。
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.core.jsonable import to_jsonable
from app.models.diet import DietRecord
from app.models.diet_assistant import (
    DietDayStatus,
    MealPlan,
    MealPlanVersion,
    PlanMeal,
    ReviewReport,
    ShoppingItem,
    ShoppingList,
    UserFeedback,
)
from app.models.health import DailySummary, WeightRecord

RULE_VERSION = "review-rules-v1"

# 设计 §11 的展示策略（不是健康科学阈值）：反馈 ≥3 餐且覆盖 ≥60% 才在
# 主卡显示百分比，否则显示"反馈不足"并把完成/跳过/待确认计数摊开。
_MIN_FEEDBACK_FOR_PERCENT = 3
_MIN_COVERAGE_FOR_PERCENT = Decimal("0.60")

_DAY_COMPLETENESS = {"unknown", "partial", "complete"}


def _q(value: Any, places: str = "0.01") -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """分母未知或为 0 时返回 None，绝不返回 0 冒充"完全没有"。"""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return _q(numerator / denominator, "0.0001")


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{(value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)}%"


class ReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------------- 周期 ----------------

    @staticmethod
    def default_period(today: date | None = None) -> tuple[date, date]:
        """默认上一个完整的自然周（周一至周日）。"""
        anchor = today or date.today()
        this_monday = anchor - timedelta(days=anchor.weekday())
        start = this_monday - timedelta(days=7)
        return start, start + timedelta(days=6)

    # ---------------- 当日记录完整度 ----------------

    async def mark_day(
        self, user_id: int, day: date, completeness: str, note: str | None = None
    ) -> dict[str, Any]:
        if completeness not in _DAY_COMPLETENESS:
            raise BusinessError(400, "完整度取值不合法")
        row = await self._db.scalar(
            select(DietDayStatus).where(
                DietDayStatus.user_id == user_id, DietDayStatus.status_date == day
            )
        )
        if row is None:
            row = DietDayStatus(user_id=user_id, status_date=day)
            self._db.add(row)
        row.completeness = completeness
        row.note = note
        row.confirmed_at = None if completeness == "unknown" else datetime.now()
        await self._db.commit()
        await self._db.refresh(row)
        return self._serialize_day(row)

    async def list_days(self, user_id: int, start: date, end: date) -> list[dict[str, Any]]:
        rows = await self._db.scalars(
            select(DietDayStatus)
            .where(
                DietDayStatus.user_id == user_id,
                DietDayStatus.status_date >= start,
                DietDayStatus.status_date <= end,
            )
            .order_by(DietDayStatus.status_date)
        )
        return [self._serialize_day(row) for row in rows]

    # ---------------- 生成报告 ----------------

    async def generate(
        self,
        user_id: int,
        *,
        period_start: date | None = None,
        period_end: date | None = None,
        with_draft_plan: bool = False,
    ) -> dict[str, Any]:
        default_start, default_end = self.default_period()
        start = period_start or default_start
        end = period_end or default_end
        if end < start:
            raise BusinessError(400, "周期结束日期早于开始日期")
        if (end - start).days > 62:
            raise BusinessError(400, "复盘周期最长 62 天")

        metrics, gaps, suggestions, raw = await self._collect(user_id, start, end)

        input_hash = hashlib.sha256(
            json.dumps(to_jsonable(raw), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        existing = await self._db.scalar(
            select(ReviewReport)
            .where(
                ReviewReport.user_id == user_id,
                ReviewReport.period_start == start,
                ReviewReport.period_end == end,
                ReviewReport.status == "current",
            )
            .order_by(ReviewReport.id.desc())
        )
        if existing is not None and existing.input_hash == input_hash:
            # 输入没变仍允许用户补生成下周草稿，不重复覆盖已有草稿。
            if with_draft_plan and not existing.draft_plan_ref:
                existing.draft_plan_ref = await self._draft_next_plan(user_id, start, end, metrics)
                await self._db.commit()
            return self._serialize(existing)

        if existing is not None:
            # 记录被补记/修正过：旧报告标过期，历史结论保留可查。
            existing.status = "stale"

        draft_ref = None
        if with_draft_plan:
            draft_ref = await self._draft_next_plan(user_id, start, end, metrics)

        report = ReviewReport(
            user_id=user_id,
            period_start=start,
            period_end=end,
            input_hash=input_hash,
            metrics=to_jsonable(metrics),
            data_gaps=to_jsonable(gaps),
            suggestions=to_jsonable(suggestions[:3]),
            draft_plan_ref=draft_ref,
            status="current",
            model_version=None,  # 规则生成，未调用模型：这里如实留空
            prompt_version=None,
            rule_version=RULE_VERSION,
        )
        self._db.add(report)
        await self._db.commit()
        # created_at 是 server_default，commit 后仍是"未加载"，同步访问会触发
        # 懒加载并在 async 下报 MissingGreenlet；显式 refresh 一次。
        await self._db.refresh(report)
        return self._serialize(report)

    async def get(self, user_id: int, report_id: int) -> dict[str, Any]:
        row = await self._db.get(ReviewReport, report_id)
        if row is None or row.user_id != user_id:
            raise BusinessError(404, "复盘报告不存在")
        return self._serialize(row)

    async def list_reports(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.scalars(
            select(ReviewReport)
            .where(ReviewReport.user_id == user_id)
            .order_by(ReviewReport.id.desc())
            .limit(max(1, min(limit, 50)))
        )
        return [self._serialize(row) for row in rows]

    # ---------------- 事实采集 ----------------

    async def _collect(
        self, user_id: int, start: date, end: date
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        execution = await self._execution(user_id, start, end)
        nutrition = await self._nutrition(user_id, start, end)
        weight = await self._weight(user_id, start, end)
        cost = await self._cost(user_id, start, end)
        feedback = await self._feedback(user_id, start, end)

        gaps: list[dict[str, Any]] = []
        if execution["dueAdoptedMeals"] == 0:
            gaps.append({"code": "NO_ADOPTED_PLAN", "message": "本周没有已采纳的饮食计划"})
        elif execution["unrecordedMeals"] > 0:
            gaps.append(
                {
                    "code": "MEALS_UNRECORDED",
                    "message": f"{execution['unrecordedMeals']} 餐没有反馈执行情况，未计入完成率",
                    "count": execution["unrecordedMeals"],
                }
            )
        if nutrition["recordedDays"] == 0:
            gaps.append({"code": "NO_DIET_RECORD", "message": "本周没有饮食记录，无法评估摄入"})
        elif nutrition["completeDays"] == 0:
            gaps.append(
                {
                    "code": "DAY_COMPLETENESS_UNKNOWN",
                    "message": "没有标记过完整记录天，日均值只能按已记录天计算",
                }
            )
        if nutrition["macroMissingRate"] not in (None, Decimal("0")):
            gaps.append(
                {
                    "code": "MACRO_MISSING",
                    "message": f"{_pct(nutrition['macroMissingRate'])} 的记录缺少蛋白质/脂肪/碳水数据",
                }
            )
        if weight["sampleCount"] < 2:
            gaps.append(
                {
                    "code": "WEIGHT_SAMPLES_INSUFFICIENT",
                    "message": f"体重仅 {weight['sampleCount']} 条记录，不足以判断趋势",
                    "count": weight["sampleCount"],
                }
            )
        if cost["priceStatus"] == "unknown":
            gaps.append(
                {
                    "code": "PRICE_UNAVAILABLE",
                    "message": "价格来源未配置，费用只有实付部分，不能给总花费",
                }
            )
        if feedback["count"] == 0:
            gaps.append({"code": "NO_FEEDBACK", "message": "本周没有餐次评价，口味偏好无从判断"})

        metrics = {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "execution": execution,
            "nutrition": nutrition,
            "weight": weight,
            "cost": cost,
            "feedback": feedback,
            "narrativeSource": "rules",
        }
        raw = {
            "execution": execution,
            "nutrition": nutrition,
            "weight": weight,
            "cost": cost,
            "feedback": feedback,
            "dayCompleteness": nutrition["dayCompleteness"],
        }
        return metrics, gaps, self._suggestions(execution, nutrition, weight, cost, feedback), raw

    async def _execution(self, user_id: int, start: date, end: date) -> dict[str, Any]:
        """统计已采纳计划中"已到期"的餐次执行情况。

        同一 (日期, 餐次) 可能因计划改版出现多个版本，这里按采纳时间取最新一条，
        避免改版后把同一餐算两次。
        """
        due_end = min(end, date.today())
        rows = (
            await self._db.execute(
                select(PlanMeal, MealPlanVersion)
                .join(MealPlanVersion, PlanMeal.plan_version_id == MealPlanVersion.id)
                .join(MealPlan, MealPlanVersion.plan_id == MealPlan.id)
                .where(
                    MealPlan.user_id == user_id,
                    MealPlanVersion.status == "adopted",
                    PlanMeal.meal_date >= start,
                    PlanMeal.meal_date <= due_end,
                )
            )
        ).all()

        latest: dict[tuple[date, str], tuple[datetime, str]] = {}
        for meal, version in rows:
            adopted_on = version.adopted_at.date() if version.adopted_at else version.created_at.date()
            if meal.meal_date < adopted_on:
                continue  # 采纳前已经过去的餐次不算"计划内到期"
            key = (meal.meal_date, meal.meal_type)
            stamp = version.adopted_at or version.created_at
            if key not in latest or stamp >= latest[key][0]:
                latest[key] = (stamp, meal.execution_state or "planned")

        executed = skipped = unknown = planned = 0
        for _, state in latest.values():
            if state == "executed":
                executed += 1
            elif state == "skipped":
                skipped += 1
            elif state == "unknown":
                unknown += 1
            else:
                planned += 1

        due = len(latest)
        feedback = executed + skipped + unknown
        unrecorded = planned
        rate = _ratio(Decimal(executed), Decimal(executed + skipped))
        coverage = _ratio(Decimal(executed + skipped), Decimal(due))
        show_percent = (
            due > 0
            and feedback >= _MIN_FEEDBACK_FOR_PERCENT
            and (coverage or Decimal("0")) >= _MIN_COVERAGE_FOR_PERCENT
            and rate is not None
        )

        if due == 0:
            primary = "本周没有可统计的饮食计划"
            secondary = None
        elif show_percent:
            primary = f"已反馈的 {feedback} 餐中，完成 {executed} 餐（{_pct(rate)}）"
            secondary = (
                f"本周计划 {due} 餐，已反馈 {feedback} 餐，"
                f"另 {unrecorded} 餐待确认（覆盖 {_pct(coverage)}）"
            )
        else:
            primary = "反馈不足，暂不评价执行情况"
            secondary = (
                f"本周计划 {due} 餐，完成 {executed} 餐、跳过 {skipped} 餐、"
                f"待确认 {unrecorded} 餐"
            )

        return {
            "dueAdoptedMeals": due,
            "executed": executed,
            "explicitlySkipped": skipped,
            "explicitlyUnknown": unknown,
            "unrecordedMeals": unrecorded,
            "confirmedFeedback": feedback,
            "confirmedExecutionRate": rate,
            "executionCoverage": coverage,
            "showPercent": show_percent,
            "primaryText": primary,
            "secondaryText": secondary,
            # 明确写出不能用"整体执行率"掩盖未知餐次。
            "note": "未反馈的餐次计入待确认，不按未执行计算",
        }

    async def _nutrition(self, user_id: int, start: date, end: date) -> dict[str, Any]:
        summaries = list(
            await self._db.scalars(
                select(DailySummary)
                .where(
                    DailySummary.user_id == user_id,
                    DailySummary.summary_date >= start,
                    DailySummary.summary_date <= end,
                )
                .order_by(DailySummary.summary_date)
            )
        )
        recorded = [row for row in summaries if row.total_intake_calorie is not None]
        intake_sum = sum((Decimal(str(row.total_intake_calorie)) for row in recorded), Decimal("0"))
        target_rows = [row for row in summaries if row.goal_intake_target is not None]
        target_sum = sum(
            (Decimal(str(row.goal_intake_target)) for row in target_rows), Decimal("0")
        )

        days = list(
            await self._db.scalars(
                select(DietDayStatus).where(
                    DietDayStatus.user_id == user_id,
                    DietDayStatus.status_date >= start,
                    DietDayStatus.status_date <= end,
                )
            )
        )
        buckets = {"complete": 0, "partial": 0, "unknown": 0}
        for row in days:
            buckets[row.completeness if row.completeness in buckets else "unknown"] += 1

        records = list(
            await self._db.scalars(
                select(DietRecord).where(
                    DietRecord.user_id == user_id,
                    DietRecord.record_date >= start,
                    DietRecord.record_date <= end,
                )
            )
        )
        macro_missing = sum(
            1
            for row in records
            if row.protein_snapshot is None or row.fat_snapshot is None or row.carb_snapshot is None
        )

        avg_intake = _q(intake_sum / len(recorded)) if recorded else None
        avg_target = _q(target_sum / len(target_rows)) if target_rows else None
        return {
            "recordedDays": len(recorded),
            "recordDaysWithRecord": len({row.record_date for row in records}),
            "completeDays": buckets["complete"],
            "partialDays": buckets["partial"],
            "unknownCompletenessDays": buckets["unknown"],
            "dayCompleteness": buckets,
            "averageIntakeCalorie": avg_intake,
            "averageTargetCalorie": avg_target,
            # 分母写清楚，别让人以为是"整周平均"。
            "averageBasis": "recorded_days",
            "intakeRatio": _ratio(avg_intake, avg_target),
            "dietRecordCount": len(records),
            "macroMissingCount": macro_missing,
            "macroMissingRate": _ratio(Decimal(macro_missing), Decimal(len(records))),
        }

    async def _weight(self, user_id: int, start: date, end: date) -> dict[str, Any]:
        rows = list(
            await self._db.scalars(
                select(WeightRecord)
                .where(
                    WeightRecord.user_id == user_id,
                    WeightRecord.record_date >= start,
                    WeightRecord.record_date <= end,
                )
                .order_by(WeightRecord.record_date, WeightRecord.record_time)
            )
        )
        if not rows:
            return {
                "sampleCount": 0,
                "firstWeight": None,
                "lastWeight": None,
                "changeKg": None,
                "trend": "insufficient",
                "note": "本周期没有体重记录，不作趋势判断",
            }
        first, last = rows[0], rows[-1]
        change = None
        if len(rows) >= 2:
            change = _q(Decimal(str(last.weight_kg)) - Decimal(str(first.weight_kg)))
        return {
            "sampleCount": len(rows),
            "firstDate": first.record_date.isoformat(),
            "lastDate": last.record_date.isoformat(),
            "firstWeight": _q(first.weight_kg),
            "lastWeight": _q(last.weight_kg),
            "changeKg": change,
            "trend": "insufficient" if len(rows) < 2 else ("flat" if change == 0 else ("down" if change < 0 else "up")),
            "note": (
                "样本不足，不判断趋势"
                if len(rows) < 2
                else "仅为体重变化，短期波动不能推断脂肪变化或减重效果"
            ),
        }

    async def _cost(self, user_id: int, start: date, end: date) -> dict[str, Any]:
        listings = list(
            await self._db.scalars(
                select(ShoppingList).where(
                    ShoppingList.user_id == user_id,
                    ShoppingList.date_start <= end,
                    ShoppingList.date_end >= start,
                )
            )
        )
        if not listings:
            return {
                "listCount": 0,
                "priceStatus": "unknown",
                "purchasedItemCount": 0,
                "paidItemCount": 0,
                "actualPaid": None,
                "costComplete": False,
                "note": "本周期没有采购清单",
            }

        items = list(
            await self._db.scalars(
                select(ShoppingItem).where(ShoppingItem.list_id.in_([row.id for row in listings]))
            )
        )
        purchased = [row for row in items if row.state == "purchased"]
        paid = [
            row
            for row in purchased
            if isinstance(row.price_snapshot, dict) and row.price_snapshot.get("paidPrice") is not None
        ]
        paid_sum = sum(
            (Decimal(str(row.price_snapshot["paidPrice"])) for row in paid), Decimal("0")
        )
        price_status = "unknown"
        for row in listings:
            if isinstance(row.totals, dict) and row.totals.get("priceStatus"):
                price_status = row.totals["priceStatus"]
                break
        complete = bool(purchased) and len(paid) == len(purchased)
        return {
            "listCount": len(listings),
            "priceStatus": price_status,
            "purchasedItemCount": len(purchased),
            "paidItemCount": len(paid),
            "actualPaid": _q(paid_sum) if complete else None,
            "actualPaidPartial": _q(paid_sum) if paid and not complete else None,
            "paidCoverage": _ratio(Decimal(len(paid)), Decimal(len(purchased))),
            "costComplete": complete,
            "note": (
                "价格来源未配置，报价不可用；实付仅来自用户填写"
                if price_status == "unknown"
                else "费用基于已记录报价与实付"
            ),
        }

    async def _feedback(self, user_id: int, start: date, end: date) -> dict[str, Any]:
        rows = list(
            await self._db.scalars(
                select(UserFeedback).where(
                    UserFeedback.user_id == user_id,
                    UserFeedback.created_at >= datetime.combine(start, datetime.min.time()),
                    UserFeedback.created_at <= datetime.combine(end, datetime.max.time()),
                )
            )
        )
        by_rating: dict[str, int] = defaultdict(int)
        for row in rows:
            by_rating[row.rating] += 1
        return {
            "count": len(rows),
            "byRating": dict(by_rating),
            "hunger": self._tally(rows, "hunger"),
            "satiety": self._tally(rows, "satiety"),
            "difficulty": self._tally(rows, "difficulty"),
        }

    @staticmethod
    def _tally(rows: list[UserFeedback], field: str) -> dict[str, int]:
        counter: dict[str, int] = defaultdict(int)
        for row in rows:
            value = getattr(row, field)
            if value:
                counter[value] += 1
        return dict(counter)

    # ---------------- 建议 ----------------

    def _suggestions(
        self,
        execution: dict[str, Any],
        nutrition: dict[str, Any],
        weight: dict[str, Any],
        cost: dict[str, Any],
        feedback: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """最多 3 条，每条都要能指回一个事实；不写没有依据的建议。"""
        pool: list[dict[str, Any]] = []

        if execution["unrecordedMeals"] > 0:
            pool.append(
                {
                    "code": "RECORD_MEAL_STATE",
                    "message": f"有 {execution['unrecordedMeals']} 餐没标执行状态，补上后完成率才准",
                    "evidence": "execution.unrecordedMeals",
                    "kind": "data",
                }
            )
        if nutrition["recordedDays"] > 0 and nutrition["completeDays"] == 0:
            pool.append(
                {
                    "code": "MARK_DAY_COMPLETE",
                    "message": "每天勾一下\"记录是否完整\"，日均值才能按完整天算",
                    "evidence": "nutrition.dayCompleteness",
                    "kind": "data",
                }
            )
        if nutrition["macroMissingRate"] and nutrition["macroMissingRate"] > Decimal("0.3"):
            pool.append(
                {
                    "code": "FILL_NUTRITION",
                    "message": "较多记录缺少营养素数据，建议补全或替换为营养数据完整的食物",
                    "evidence": "nutrition.macroMissingRate",
                    "kind": "data",
                }
            )
        if (
            nutrition["intakeRatio"] is not None
            and nutrition["averageTargetCalorie"]
            and nutrition["intakeRatio"] < Decimal("0.8")
            and nutrition["recordedDays"] >= 3
        ):
            pool.append(
                {
                    "code": "INTAKE_BELOW_TARGET",
                    "message": "已记录天的平均摄入低于目标较多，可能漏记或份量偏小",
                    "evidence": "nutrition.intakeRatio",
                    "kind": "hypothesis",
                }
            )
        if weight["sampleCount"] < 2:
            pool.append(
                {
                    "code": "MORE_WEIGHT_SAMPLES",
                    "message": "每周至少称 2 次体重，才能看出趋势",
                    "evidence": "weight.sampleCount",
                    "kind": "data",
                }
            )
        if execution["explicitlySkipped"] >= 2:
            pool.append(
                {
                    "code": "SIMPLIFY_MEALS",
                    "message": "有餐次被跳过，可能是当天时间不够，可以准备 15 分钟内的快速餐",
                    "evidence": "execution.explicitlySkipped",
                    "kind": "hypothesis",
                }
            )
        if feedback["count"] == 0 and execution["executed"] > 0:
            pool.append(
                {
                    "code": "RATE_MEALS",
                    "message": "给吃过的餐打个分，下周选餐才能避开不喜欢的",
                    "evidence": "feedback.count",
                    "kind": "data",
                }
            )
        if cost["priceStatus"] == "unknown" and cost["purchasedItemCount"] > 0:
            pool.append(
                {
                    "code": "RECORD_PAID_PRICE",
                    "message": "采购时填实付金额，才能核对预算",
                    "evidence": "cost.priceStatus",
                    "kind": "data",
                }
            )
        return pool

    # ---------------- 下周草稿 ----------------

    async def _draft_next_plan(
        self, user_id: int, start: date, end: date, metrics: dict[str, Any]
    ) -> str | None:
        """按本周期事实生成"下周变更草稿"，只建 draft，不采纳、不改偏好。"""
        from app.models.user import User
        from app.services.plan_service import PlanService

        user = await self._db.get(User, user_id)
        if user is None:
            return None
        next_start = end + timedelta(days=1)
        next_end = next_start + timedelta(days=6)
        try:
            plan = await PlanService(self._db).generate_draft(
                user,
                {
                    "startDate": next_start,
                    "days": 7,
                    "mealTypes": ["breakfast", "lunch", "dinner"],
                    "reason": f"复盘 {start.isoformat()}~{end.isoformat()} 生成的草稿",
                },
            )
        except BusinessError:
            # 候选食谱不足等业务原因：不生成草稿，如实留空。
            return None
        version = plan.get("versionNumber")
        return f"meal_plan:{plan['planId']}:v{version or 1}"

    # ---------------- 序列化 ----------------

    @staticmethod
    def _serialize_day(row: DietDayStatus) -> dict[str, Any]:
        return {
            "date": row.status_date.isoformat(),
            "completeness": row.completeness,
            "note": row.note,
            "confirmedAt": row.confirmed_at.isoformat() if row.confirmed_at else None,
        }

    @staticmethod
    def _serialize(row: ReviewReport) -> dict[str, Any]:
        return {
            "id": row.id,
            "periodStart": row.period_start.isoformat(),
            "periodEnd": row.period_end.isoformat(),
            "metrics": row.metrics,
            "dataGaps": row.data_gaps or [],
            "suggestions": row.suggestions or [],
            "draftPlanRef": row.draft_plan_ref,
            "status": row.status,
            "ruleVersion": row.rule_version,
            "modelVersion": row.model_version,
            "promptVersion": row.prompt_version,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
