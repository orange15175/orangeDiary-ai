"""用一张规则表识别聊天意图，避免为当前少量意图过早拆分策略类。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


ParamValue = str | float | int | tuple[str, ...]


@dataclass(frozen=True)
class IntentMatch:
    name: str
    tool_name: str
    priority: int
    conflict_group: str | None
    params: dict[str, ParamValue]
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentRule:
    name: str
    tool_name: str
    priority: int
    conflict_group: str | None
    patterns: tuple[str, ...]
    extractor: Callable[[str], dict[str, ParamValue]]
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
_EXERCISE_RECORD_PATTERN = re.compile(
    r"(?:我|刚刚|刚才|晚上|今天)?(?:进行了|做了|完成了|跑了|练了|运动了)"
    r"(?P<exercise>[\u4e00-\u9fff]{0,12}?)(?:\s*(?P<duration>\d+)\s*(?:分钟|min))?"
)


def _empty_params(_: str) -> dict[str, ParamValue]:
    """供不需要参数的规则复用。"""
    return {}


def _food_params(message: str) -> dict[str, ParamValue]:
    """提取食物查询需要的名称、重量和营养指标。"""
    matched = _FOOD_PATTERN.search(message)
    if not matched:
        return {}
    food_name = matched.group("food").rstrip("的有含")
    # 单字通常是正则回溯产生的残片，不应作为食物名查询。
    if len(food_name) < 2:
        return {}
    # 这些是“个人汇总/运动”语境，不是可以送往食物库检索的食物名称。
    if any(marker in food_name for marker in ("我的", "今天", "今日", "额度", "还能吃", "还可以吃", "剩余", "摄入", "消耗", "燃烧", "达标", "超标")):
        return {}
    params: dict[str, ParamValue] = {
        "food_name": food_name,
        "nutrition_metric": matched.group("metric"),
    }
    amount = matched.group("amount") or matched.group("amount_after")
    if amount:
        params["amount_g"] = float(amount)
    return params


def _daily_summary_params(message: str) -> dict[str, ParamValue]:
    """提取今日汇总问题关心的热量指标。"""
    # 同一次汇总查询可返回多个指标，避免为“吃了多少/还剩多少”重复请求 Java。
    metrics: list[str] = []
    if re.search(r"(?:吃了|摄入|已经吃|已摄入).*(?:多少|多少热量|多少卡|多少大卡|多少千卡)", message):
        metrics.append("intake_calorie")
    if re.search(r"(?:能吃|可以吃|还能吃|还可以吃|剩余).*(?:多少|多少热量|多少卡|多少大卡|多少千卡)", message):
        metrics.append("remaining_calorie")
    if not metrics:
        metrics.append("summary")
    return {"metrics": tuple(dict.fromkeys(metrics))}


def _exercise_params(message: str) -> dict[str, ParamValue]:
    """提取运动消耗查询的项目和时长。"""
    matched = _EXERCISE_PATTERN.search(message)
    if not matched:
        return {}
    exercise_name = matched.group("exercise")
    for prefix in ("今天", "今日", "我的", "我", "请问"):
        if exercise_name.startswith(prefix):
            exercise_name = exercise_name[len(prefix):]
            break
    params: dict[str, ParamValue] = {}
    if exercise_name:
        params["exercise_name"] = exercise_name
    if matched.group("duration"):
        params["duration_min"] = int(matched.group("duration"))
    return params


def _exercise_record_params(message: str) -> dict[str, ParamValue]:
    """提取运动记录预览需要的项目和时长。"""
    matched = _EXERCISE_RECORD_PATTERN.search(message)
    exercise_name = matched.group("exercise").strip("了的") if matched else ""
    # "我刚刚跑步了" 的动作本身就是项目；避免把时间词当成运动名。
    for name in _EXERCISE_HINTS:
        if name in message:
            exercise_name = name
            break
    params: dict[str, ParamValue] = {}
    if exercise_name:
        params["exercise_name"] = exercise_name
    duration = matched.group("duration") if matched else None
    if not duration:
        duration_match = re.search(r"(\d+)\s*(?:分钟|min)", message)
        duration = duration_match.group(1) if duration_match else None
    if duration:
        params["duration_min"] = int(duration)
    return params


# 新增简单意图时优先只补这一项；Router 不需要增加新的 if 分支。
INTENT_RULES = (
    IntentRule("food_nutrition", "search_food_nutrition", 100, "nutrition_lookup",
               (r"(?:热量|卡路里|大卡|千卡|蛋白质|脂肪|碳水(?:化合物)?)",), _food_params, ("food_name",)),
    IntentRule("food_record", "prepare_diet_record", 95, "nutrition_lookup",
               (r"吃了(?!什么|啥|多少|几)", r"喝了(?!什么|啥|多少|几)", r"进食了"), _empty_params),
    IntentRule("exercise_calorie", "search_exercise_calorie", 90, "exercise_lookup",
               (r"(?:消耗|燃烧)",), _exercise_params, ("exercise_name", "duration_min")),
    IntentRule("exercise_record", "prepare_exercise_record", 94, "exercise_lookup",
               (r"(?:刚刚|刚才|今天|晚上)?.*(?:跑步了|走路了|游泳了|骑车了|骑行了|跳绳了|健身了|训练了|瑜伽了|运动了)",
                r"^(?!.*(?:消耗|燃烧|多少)).*(?:跑步|走路|游泳|骑车|骑行|跳绳|健身|训练|瑜伽)\s*\d+\s*(?:分钟|min)"), _exercise_record_params, ("exercise_name", "duration_min")),
    IntentRule("daily_summary", "get_today_summary", 80, None,
               (r"(?:今天|今日)?(?:能吃|可以吃|还能吃|还可以吃)多少(?:热量|卡路里|大卡|千卡)?",
                r"(?:今天|今日)?(?:吃了|摄入|已经吃|已摄入)多少(?:热量|卡路里|大卡|千卡)?",
                r"还能吃", r"还可以吃", r"剩余(?:热量|卡路里|大卡|千卡)", r"(?:今天|今日)摄入",
                r"(?:热量|摄入).*(?:达标|超标)", r"(?:今天|今日).*(?:总共|一共).*(?:热量|卡路里|大卡|千卡)"), _daily_summary_params),
    IntentRule("diet_detail", "get_today_diet_detail", 70, None,
               (r"吃了什么", r"吃了啥", r"饮食明细", r"几餐", r"(?:早餐|午餐|午饭|晚餐|晚饭).*(?:吃了什么|吃了啥|记录)"), _empty_params),
    IntentRule("weight_progress", "get_weight_progress", 60, None,
               (r"体重", r"减了几公斤", r"减了多少公斤", r"减重", r"体重进度"), _empty_params),
    IntentRule("user_goal", "get_user_goal", 50, None,
               (r"我的目标", r"目标热量", r"每日摄入目标", r"每日运动消耗目标",
                r"(?:我的|当前|选择|设置).*(?:增肌|减脂|维持)"), _empty_params),
)


def normalize_message(message: str) -> str:
    """统一空白和大小写，减少规则表达式的重复分支。"""
    return re.sub(r"\s+", "", message.strip().lower())


def detect_intents(message: str) -> list[IntentMatch]:
    """返回所有互不冲突的意图；真正不同的任务会被保留以便并行查询。"""
    # 规则层先给出低成本、确定性的候选意图。
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


def normalize_intents(message: str) -> tuple:
    """Return rule results in the canonical intent layer format."""
    from app.services.intent_result import IntentResult

    # 为调用方提供规则路径的统一结果，语义路径也会产出相同结构。
    return tuple(IntentResult.from_rule(intent) for intent in detect_intents(message))


def _merge_intents(candidates: list[IntentMatch]) -> list[IntentMatch]:
    """仅在同一冲突组择优；没有冲突的多个用户诉求全部保留。"""
    selected: dict[str, IntentMatch] = {}
    for candidate in candidates:
        # 只在互斥组内取优先级最高项；不同业务诉求会被保留给后续并行处理。
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
