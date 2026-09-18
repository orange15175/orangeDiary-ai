"""用一张规则表识别聊天意图，避免为当前少量意图过早拆分策略类。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class IntentMatch:
    name: str
    tool_name: str
    priority: int
    conflict_group: str | None
    params: dict[str, str | float | int]
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentRule:
    name: str
    tool_name: str
    priority: int
    conflict_group: str | None
    patterns: tuple[str, ...]
    extractor: Callable[[str], dict[str, str | float | int]]
    required_fields: tuple[str, ...] = ()


_FOOD_PATTERN = re.compile(
    r"(?:(?P<amount>\d+(?:\.\d+)?)\s*(?:克|g|G)\s*)?"
    r"(?P<food>[\u4e00-\u9fff]{2,12}?)(?:\s*(?P<amount_after>\d+(?:\.\d+)?)\s*(?:克|g|G))?"
    r"(?:的|有|含有)?\s*(?:多少)?(?P<metric>热量|卡路里|大卡|千卡|蛋白质|脂肪|碳水(?:化合物)?)"
)
_EXERCISE_PATTERN = re.compile(
    r"(?P<exercise>[\u4e00-\u9fff]{2,12}?)(?:\s*(?P<duration>\d+)\s*(?:分钟|min))?\s*(?:能)?(?:消耗|燃烧)"
)
_EXERCISE_HINTS = (
    "跑步", "走路", "快走", "游泳", "骑行", "骑车", "跳绳", "健身",
    "力量训练", "瑜伽", "篮球", "足球", "羽毛球", "运动", "训练",
)


def _empty_params(_: str) -> dict[str, str | float | int]:
    return {}


def _food_params(message: str) -> dict[str, str | float | int]:
    matched = _FOOD_PATTERN.search(message)
    if not matched:
        return {}
    food_name = matched.group("food").rstrip("的有含")
    # 这些是“今日汇总/运动”语境，不是可以送往食物库检索的食物名称。
    if any(marker in food_name for marker in ("今天", "今日", "还能吃", "还可以吃", "剩余", "摄入", "消耗", "燃烧", "达标", "超标")):
        return {}
    params: dict[str, str | float | int] = {
        "food_name": food_name,
        "nutrition_metric": matched.group("metric"),
    }
    amount = matched.group("amount") or matched.group("amount_after")
    if amount:
        params["amount_g"] = float(amount)
    return params


def _exercise_params(message: str) -> dict[str, str | float | int]:
    matched = _EXERCISE_PATTERN.search(message)
    if not matched:
        return {}
    exercise_name = matched.group("exercise")
    for prefix in ("今天", "今日", "我的", "我", "请问"):
        if exercise_name.startswith(prefix):
            exercise_name = exercise_name[len(prefix):]
            break
    params: dict[str, str | float | int] = {}
    if exercise_name:
        params["exercise_name"] = exercise_name
    if matched.group("duration"):
        params["duration_min"] = int(matched.group("duration"))
    return params


# 新增简单意图时优先只补这一项；Router 不需要增加新的 if 分支。
INTENT_RULES = (
    IntentRule("food_nutrition", "search_food_nutrition", 100, "nutrition_lookup",
               (r"(?:热量|卡路里|大卡|千卡|蛋白质|脂肪|碳水(?:化合物)?)",), _food_params, ("food_name",)),
    IntentRule("food_record", "prepare_diet_record", 95, "nutrition_lookup",
               (r"吃了(?!什么|啥)", r"喝了(?!什么|啥)", r"进食了"), _empty_params),
    IntentRule("exercise_calorie", "search_exercise_calorie", 90, "exercise_lookup",
               (r"(?:消耗|燃烧)",), _exercise_params, ("exercise_name", "duration_min")),
    IntentRule("daily_summary", "get_today_summary", 80, None,
               (r"还能吃", r"还可以吃", r"剩余(?:热量|卡路里|大卡|千卡)", r"(?:今天|今日)摄入",
                r"(?:热量|摄入).*(?:达标|超标)", r"(?:今天|今日).*(?:总共|一共).*(?:热量|卡路里|大卡|千卡)"), _empty_params),
    IntentRule("diet_detail", "get_today_diet_detail", 70, None,
               (r"吃了什么", r"吃了啥", r"饮食明细", r"几餐", r"(?:早餐|午餐|午饭|晚餐|晚饭).*(?:吃了什么|吃了啥|记录)"), _empty_params),
    IntentRule("weight_progress", "get_weight_progress", 60, None,
               (r"体重", r"减了几公斤", r"减了多少公斤", r"减重", r"体重进度"), _empty_params),
    IntentRule("user_goal", "get_user_goal", 50, None,
               (r"我的目标", r"目标热量", r"每日摄入目标", r"每日运动消耗目标", r"增肌", r"减脂", r"维持"), _empty_params),
)


def normalize_message(message: str) -> str:
    return re.sub(r"\s+", "", message.strip().lower())


def detect_intents(message: str) -> list[IntentMatch]:
    """返回所有互不冲突的意图；真正不同的任务会被保留以便并行查询。"""
    normalized = normalize_message(message)
    candidates: list[IntentMatch] = []
    for rule in INTENT_RULES:
        if not any(re.search(pattern, normalized) for pattern in rule.patterns):
            continue
        if rule.name == "exercise_calorie" and not any(hint in normalized for hint in _EXERCISE_HINTS):
            continue
        params = rule.extractor(normalized)
        if rule.required_fields and not params:
            # “今天还有多少热量”不是食物名称缺失，不应该误触发食物追问。
            continue
        missing_fields = tuple(field for field in rule.required_fields if not params.get(field))
        candidates.append(IntentMatch(rule.name, rule.tool_name, rule.priority, rule.conflict_group, params, missing_fields))
    return _merge_intents(candidates)


def _merge_intents(candidates: list[IntentMatch]) -> list[IntentMatch]:
    """仅在同一冲突组择优；没有冲突的多个用户诉求全部保留。"""
    selected: dict[str, IntentMatch] = {}
    for candidate in candidates:
        key = candidate.conflict_group or candidate.name
        existing = selected.get(key)
        if existing is None or candidate.priority > existing.priority:
            selected[key] = candidate
    return sorted(selected.values(), key=lambda item: item.priority, reverse=True)


def build_clarification(intents: list[IntentMatch]) -> str | None:
    """缺少必要参数时统一追问，避免用猜测的食物或运动数据回答。"""
    missing = {field for intent in intents for field in intent.missing_fields}
    if not missing:
        return None
    if "duration_min" in missing:
        return "你大约进行了多少分钟？"
    if "exercise_name" in missing:
        return "你想查询哪一种运动的热量消耗？"
    if "food_name" in missing:
        return "你想查询哪一种食物的营养信息？"
    return "请补充更具体的信息，我才能准确回答。"
