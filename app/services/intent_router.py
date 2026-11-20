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


# 单位只做“同类换算”。计数单位（个/碗/份）没有可靠份量表时不能变成克。
_MASS_UNITS: dict[str, float] = {
    "克": 1.0, "g": 1.0, "公克": 1.0,
    "千克": 1000.0, "公斤": 1000.0, "kg": 1000.0,
    "斤": 500.0, "两": 50.0,
}
_COUNT_UNITS = ("个", "只", "碗", "份", "杯", "瓶", "盒", "袋", "片", "根", "块", "勺", "盘", "条", "颗", "串", "听", "罐", "张", "把")
# 体积换算成质量需要密度，不能默认 1ml = 1g。
_VOLUME_UNITS = ("毫升", "ml", "升", "l")
_UNIT_PATTERN = "公斤|千克|公克|毫升|克|斤|两|kg|g|ml|个|只|碗|份|杯|瓶|盒|袋|片|根|块|勺|盘|条|颗|串|听|罐|张|把"

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 食物名前面常带的问句前缀；只在这些固定词出现时剥离，避免误伤“我的自制沙拉”。
_FOOD_PREFIXES = ("我想知道", "我想问一下", "我想问", "请问", "帮我查一下", "帮我看看", "查一下", "了解一下", "想问一下")
_FOOD_STOPWORDS = ("什么", "啥", "多少", "几", "哪些", "东西", "食物", "早餐", "午餐", "晚餐", "加餐", "记录")

_FOOD_PATTERN = re.compile(
    r"(?:(?P<amount>\d+(?:\.\d+)?)\s*(?:公斤|千克|斤|克|g|G)\s*)?"
    r"(?P<food>[\u4e00-\u9fff]{2,12}?)(?:\s*(?P<amount_after>\d+(?:\.\d+)?)\s*(?:公斤|千克|斤|克|g|G))?"
    r"(?:的|有|含有)?\s*(?:多少)?(?P<metric>热量|卡路里|大卡|千卡|能量|蛋白质|脂肪|碳水(?:化合物)?)"
)
# 时长不能只认阿拉伯数字：用户说"跑了半小时""一个半小时""两小时"同样有确定值，
# 反问"请问多少分钟"等于让用户把自己刚说的话再翻译一遍。
_DURATION_PATTERN = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]|半)(?:\s*个)?\s*(?:半\s*)?(?:小时|钟头|分钟|min)"
_EXERCISE_PATTERN = re.compile(
    rf"(?P<exercise>[\u4e00-\u9fff]{{2,12}}?)(?:\s*(?P<duration>{_DURATION_PATTERN}))?\s*(?:能)?(?:消耗|燃烧)"
)
_EXERCISE_HINTS = (
    "跑步", "走路", "快走", "游泳", "骑行", "骑车", "跳绳", "健身",
    "力量训练", "瑜伽", "篮球", "足球", "羽毛球", "运动", "训练",
    # 单字动词放最后："我跑完了30分钟"只出现"跑"，不补这一项就取不到项目名。
    # "走"只在带时长时算运动（见下面规则），单说"我走了"不当作运动记录。
    "跑", "走",
)
# 单字动词还原成登记项目名，避免把"跑"直接写进记录卡。
_EXERCISE_ALIASES = {"跑": "跑步", "游": "游泳", "骑": "骑行", "走": "走路"}
# 未来时标记：准备/打算/计划做的运动尚未发生，不能生成"已完成"记录卡。
_FUTURE_MARKERS = r"(?:准备|打算|计划|将要|马上|一会儿|待会儿|等下|明天|后天|下周|下次|要去|想去)"
_EXERCISE_RECORD_PATTERN = re.compile(
    r"(?:我|刚刚|刚才|晚上|今天)?(?:进行了|做了|完成了|跑了|练了|运动了)"
    rf"(?P<exercise>[\u4e00-\u9fff]{{0,12}}?)(?:\s*(?P<duration>{_DURATION_PATTERN}))?"
)
# 记录语句优先匹配“数量 + 单位”；单位可选但必须显式识别，不能默认当克。
_FOOD_RECORD_PATTERN = re.compile(
    r"(?:吃了|喝了|进食了|记录了)\s*"
    r"(?P<quantity>\d+(?:\.\d+)?|[零一二两三四五六七八九十半]+)\s*"
    rf"(?P<unit>{_UNIT_PATTERN})?\s*"
    r"(?P<food>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]{0,15})"
)
# 没有数量时仍要能识别“我吃了鸡蛋”，交给缺参追问而不是忽略。
_FOOD_RECORD_NO_AMOUNT = re.compile(
    r"(?:吃了|喝了|进食了)\s*(?P<food>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9]{0,15})"
)

# 做菜意图的显式标记。只认"问做法/找菜"的说法，避免和"吃/热量"混在一起：
# 「红烧肉热量多少」必须仍是 food_nutrition，「我吃了红烧肉」必须仍是 food_record。
_COOKING_DISH_QUERY = re.compile(
    r"(?P<dish>[\u4e00-\u9fffA-Za-z]{2,12}?)(?:怎么做|怎么烧|怎么炒|怎么炖|怎么煮|怎么煎|怎么蒸|怎么拌|如何做|的做法|做法)"
)
_COOKING_DISH_WANT = re.compile(
    r"(?:我想吃|想吃|我想做|想做|我要做|帮我做|教我做)(?:一)?(?:道|个|份)?(?P<dish>[\u4e00-\u9fff]{2,10})"
)
_COOKING_INGREDIENT_QUERY = re.compile(r"能做什么|可以做什么|做什么菜|做什么好|推荐(?:个|一道|一个)菜|来一道")
# "我有辣椒、五花肉和鸡腿"：只取人称之后到句末的片段，再按分隔符拆。
_COOKING_STOCK = re.compile(
    r"(?:我有|家里有|冰箱里有|还剩|剩下|(?<!没)有)(?P<items>[^，。？！?,]{2,40})"
)
_COOKING_STOCK_STOPWORDS = ("什么", "啥", "哪些", "没有", "多少", "办法", "做法", "推荐")
_COOKING_FOLLOWUP = re.compile(r"第[一二三四五1-5]道|就做|换成|换一道|换一个|不放|不要放|不用放")


def _clean_dish(raw: str | None) -> str | None:
    """剥掉菜名前的口头语、时间和句尾语气词，保留真正的菜名。"""
    if not raw:
        return None
    dish = raw.strip()
    # "晚上鸡腿怎么做"里的"晚上"是时间，不是菜名的一部分。
    for prefix in (
        "我想吃", "想吃", "我想做", "想做", "我要做", "帮我做", "教我做", "做一道", "来一道",
        "晚上", "中午", "早上", "今天", "明天", "今晚", "周末", "我家", "家里",
    ):
        if dish.startswith(prefix):
            dish = dish[len(prefix):]
            break
    dish = dish.strip("的了呢吧啊")
    # "鸡腿除了红烧还能怎么做" 会把从句一起吞进菜名（正则只能靠"怎么做"定位）。
    # 从句标记一律截断，否则菜名去检索必然零命中。
    for marker in ("除了", "还能", "还可以", "可以", "如何", "怎么", "要", "用"):
        cut = dish.find(marker)
        if cut >= 2:
            dish = dish[:cut]
            break
    dish = dish.strip("的了呢吧啊")
    if len(dish) < 2:
        return None
    return dish


def _cooking_params(message: str) -> dict[str, ParamValue]:
    """提取做菜槽位。这里不产出 amount_g：做菜不是记录，缺克重也不追问。"""
    params: dict[str, ParamValue] = {}

    dish = _COOKING_DISH_QUERY.search(message) or _COOKING_DISH_WANT.search(message)
    if dish:
        name = _clean_dish(dish.group("dish"))
        if name:
            params["dish_name"] = name

    stock = _COOKING_STOCK.search(message)
    if stock:
        raw_items = stock.group("items")
        if not any(word in raw_items for word in _COOKING_STOCK_STOPWORDS):
            items = [
                item.strip()
                for item in re.split(r"[、,，和及\s]+", raw_items)
                if item.strip() and len(item.strip()) <= 12
            ]
            items = [item for item in items if not any(word in item for word in _COOKING_STOCK_STOPWORDS)]
            if items:
                params["ingredients"] = tuple(dict.fromkeys(items))

    # 「辣椒、五花肉、鸡腿能做什么」没有"有"字，食材直接列在问句前面。
    if "ingredients" not in params and _COOKING_INGREDIENT_QUERY.search(message):
        head = re.split(r"能做什么|可以做什么|做什么菜|做什么好|推荐|来一道", message)[0]
        head = re.sub(r"^(?:晚上|中午|早上|今天|明天|今晚|我|家里|冰箱里|还剩|剩下)+", "", head).strip("，,、 的")
        if 2 <= len(head) <= 40:
            items = [
                item.strip()
                for item in re.split(r"[、,，和及\s]+", head)
                if 1 < len(item.strip()) <= 12
            ]
            items = [item for item in items if not any(word in item for word in _COOKING_STOCK_STOPWORDS)]
            if items:
                params["ingredients"] = tuple(dict.fromkeys(items))

    allergens = [
        value.strip()
        for value in re.findall(r"(?:对)?([\u4e00-\u9fff]{1,6}?)过敏", message)
        if value.strip()
    ]
    if allergens:
        params["allergens"] = tuple(dict.fromkeys(allergens))

    excluded = [
        value.strip()
        for value in re.findall(r"(?:不吃|不要放|不放|忌口有?|不能吃)([\u4e00-\u9fff]{1,6})", message)
        if value.strip() and not any(word in value for word in _COOKING_STOCK_STOPWORDS)
    ]
    if excluded:
        params["excluded_ingredients"] = tuple(dict.fromkeys(excluded))

    servings = re.search(r"(\d+)\s*(?:人份|个人吃|人吃)", message)
    if servings and 0 < int(servings.group(1)) <= 12:
        params["servings"] = int(servings.group(1))
    else:
        cn_servings = re.search(r"([一二两三四五六])\s*个?人吃", message)
        if cn_servings:
            value = _CN_DIGITS.get(cn_servings.group(1))
            if value:
                params["servings"] = int(value)

    if _COOKING_FOLLOWUP.search(message):
        params["subtype"] = "followup"
    elif _COOKING_INGREDIENT_QUERY.search(message) or (
        "ingredients" in params and not params.get("dish_name")
    ):
        params["subtype"] = "ingredients"
    else:
        params["subtype"] = "dish"
    return params



def parse_quantity(text: str | None) -> float | None:
    """把“2 / 半 / 两 / 十二”等写法解析为数值，无法解析时返回 None。"""
    if not text:
        return None
    value = text.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    if value == "半":
        return 0.5
    if value in _CN_DIGITS:
        return float(_CN_DIGITS[value])
    if "十" in value:
        head, _, tail = value.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        if not tail:
            return float(tens * 10)
        if tail in _CN_DIGITS:
            return float(tens * 10 + _CN_DIGITS[tail])
    return None


def normalize_food_name(raw: str | None) -> str | None:
    """清理食物名中的问句前缀和语气词，过滤纯疑问词。"""
    if not raw:
        return None
    name = raw.strip()
    for prefix in _FOOD_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.strip("的了")
    if len(name) < 1:
        return None
    if any(stop in name for stop in _FOOD_STOPWORDS):
        return None
    return name


def _empty_params(_: str) -> dict[str, ParamValue]:
    """供不需要参数的规则复用。"""
    return {}


def _mass_to_grams(quantity: float, unit: str | None) -> float | None:
    """只有质量单位才给出克数；计数、体积单位返回 None 以便追问。"""
    if unit is None:
        return None
    factor = _MASS_UNITS.get(unit.lower())
    if factor is None:
        return None
    return round(quantity * factor, 2)


def _food_record_params(message: str) -> dict[str, ParamValue]:
    """显式识别“数量 + 单位 + 食物”，不把计数单位当成克。"""
    matched = _FOOD_RECORD_PATTERN.search(message)
    if matched:
        food_name = normalize_food_name(matched.group("food"))
        quantity = parse_quantity(matched.group("quantity"))
        unit = matched.group("unit")
        if food_name and quantity is not None:
            params: dict[str, ParamValue] = {"food_name": food_name, "quantity": quantity}
            if unit:
                params["unit"] = unit
            grams = _mass_to_grams(quantity, unit)
            if grams is not None:
                params["amount_g"] = grams
            return params
        if food_name:
            return {"food_name": food_name}
        return {}
    fallback = _FOOD_RECORD_NO_AMOUNT.search(message)
    if fallback:
        food_name = normalize_food_name(fallback.group("food"))
        return {"food_name": food_name} if food_name else {}
    return {}


def _food_params(message: str) -> dict[str, ParamValue]:
    """提取食物查询需要的名称、重量和营养指标。"""
    matched = _FOOD_PATTERN.search(message)
    if not matched:
        return {}
    food_name = normalize_food_name(matched.group("food").rstrip("的有含"))
    # 单字通常是正则回溯产生的残片，不应作为食物名查询。
    if not food_name:
        return {}
    # 这些是“个人汇总/运动”语境，不是可以送往食物库检索的食物名称。
    if any(marker in food_name for marker in ("我的", "今天", "今日", "额度", "还能吃", "还可以吃", "剩余", "摄入", "消耗", "燃烧", "达标", "超标")):
        return {}
    params: dict[str, ParamValue] = {
        "food_name": food_name,
        "nutrition_metric": matched.group("metric"),
    }
    amount_text = matched.group(0)
    amount = matched.group("amount") or matched.group("amount_after")
    if amount:
        quantity = parse_quantity(amount)
        if quantity is not None:
            unit = next((u for u in ("公斤", "千克", "斤", "克", "g", "G") if u in amount_text), "克")
            grams = _mass_to_grams(quantity, unit.lower())
            if grams is not None:
                params["amount_g"] = grams
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


_CN_MINUTES = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_duration_minutes(text: str) -> int | None:
    """把"30分钟/半小时/一个半小时/两小时"统一换算成分钟。"""
    matched = re.search(rf"(?P<num>\d+(?:\.\d+)?|[一二两三四五六七八九十]|半)"
                        rf"(?:\s*个)?\s*(?P<half>半\s*)?(?P<unit>小时|钟头|分钟|min)", text or "")
    if not matched:
        return None
    raw = matched.group("num")
    value = float(raw) if raw[0].isdigit() else _CN_MINUTES.get(raw)
    if value is None:
        return None
    if matched.group("half"):
        value += 0.5
    minutes = value * 60 if matched.group("unit") in ("小时", "钟头") else value
    return int(round(minutes)) if minutes > 0 else None


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
        minutes = _parse_duration_minutes(matched.group("duration"))
        if minutes:
            params["duration_min"] = minutes
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
    exercise_name = _EXERCISE_ALIASES.get(exercise_name, exercise_name)
    params: dict[str, ParamValue] = {}
    if exercise_name:
        params["exercise_name"] = exercise_name
    duration = matched.group("duration") if matched else None
    if not duration:
        duration_match = re.search(r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十]|半)\s*(?:个)?\s*(?:半)?\s*(?:小时|钟头|分钟|min)", message)
        duration = duration_match.group(0) if duration_match else None
    if duration:
        minutes = _parse_duration_minutes(duration)
        if minutes:
            params["duration_min"] = minutes
    return params


# 新增简单意图时优先只补这一项；Router 不需要增加新的 if 分支。
INTENT_RULES = (
    IntentRule("food_nutrition", "search_food_nutrition", 100, "nutrition_lookup",
               # "今天消耗了多少大卡"里的"大卡"是运动消耗口径，不能被食物营养抢走。
               (r"^(?!.*(?:消耗|燃烧)).*(?:热量|卡路里|大卡|千卡|能量|蛋白质|脂肪|碳水(?:化合物)?)",), _food_params, ("food_name",)),
    IntentRule("food_record", "prepare_diet_record", 95, "nutrition_lookup",
               (r"吃了(?!什么|啥|多少|几)", r"喝了(?!什么|啥|多少|几)", r"进食了"), _food_record_params,
               ("food_name", "amount_g")),
    IntentRule("exercise_calorie", "search_exercise_calorie", 90, "exercise_lookup",
               (r"(?:消耗|燃烧)",), _exercise_params, ("exercise_name", "duration_min")),
    IntentRule("exercise_record", "prepare_exercise_record", 94, "exercise_lookup",
               # 已发生：全名+"了"（跑步了/健身了），或单字动词+"完了/过了"（跑完了/刚跑完）。
               (rf"^(?!.*{_FUTURE_MARKERS}).*(?:跑步|走路|游泳|骑车|骑行|跳绳|健身|训练|瑜伽|运动|跑)(?:完了|完|过了|了)",
                # 已发生：动词直接带时长（跑步30分钟/跑了半小时/走路一小时）。未来时与消耗查询排除在外。
                rf"^(?!.*(?:消耗|燃烧|多少|{_FUTURE_MARKERS})).*(?:跑步|走路|游泳|骑车|骑行|跳绳|健身|训练|瑜伽|跑|走)\s*(?:了)?\s*{_DURATION_PATTERN}"), _exercise_record_params, ("exercise_name", "duration_min")),
    # 做菜排在看营养/记饮食之后：同一句话里"红烧肉热量多少"仍归营养查询。
    # 不给 conflict_group，才能和记录意图并存，供编排层拆分处理。
    IntentRule("cooking", "answer_cooking", 85, None,
               (r"怎么做|怎么烧|怎么炒|怎么炖|怎么煮|怎么煎|怎么蒸|怎么拌|如何做|做法|教程",
                r"能做什么|可以做什么|做什么菜|做什么好|推荐个菜|推荐一道|来一道",
                r"我想吃|想吃|我想做|想做",
                r"第[一二三四五1-5]道|就做第"), _cooking_params),
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


def is_future_plan(message: str) -> bool:
    """「准备/打算/计划…」+ 运动项目：这件事还没发生，不能生成"已完成"记录卡。

    规则层已经排除了未来时，但语义模型仍可能把这类句子判成 exercise_record。
    出卡前统一用这个判断兜底，避免诱导用户保存一条没做过的运动。
    """
    text = (message or "").strip()
    if not text:
        return False
    future = _FUTURE_MARKERS.replace("计划", "计划(?!外|内)")
    return re.search(
        future + r".{0,8}(?:跑步|走路|游泳|骑车|骑行|跳绳|健身|训练|瑜伽|运动|跑|走)",
        text,
    ) is not None


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


def _format_quantity(value: object) -> str:
    """把 2.0 显示成 2，把 0.5 显示成 0.5。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


def clarification_for_slots(slots: dict, missing: tuple[str, ...] | list[str]) -> str | None:
    """按缺参类型给出自然追问；有数量+单位时只问克数，不重复问食物。"""
    missing_set = set(missing or ())
    if not missing_set:
        return None
    food_name = slots.get("food_name")
    quantity = slots.get("quantity")
    unit = slots.get("unit")
    exercise_name = slots.get("exercise_name")

    if "amount_g" in missing_set and food_name:
        if quantity is not None and unit:
            amount_text = slots.get("quantity_text") or _format_quantity(quantity)
            return f"你说的「{amount_text}{unit}{food_name}」大约有多少克？"
        return f"「{food_name}」大约吃了多少克？"
    if "duration_min" in missing_set:
        if exercise_name:
            return f"你这次「{exercise_name}」大约进行了多少分钟？"
        return "你大约进行了多少分钟？"
    if "exercise_name" in missing_set:
        return "你想查询哪一种运动的热量消耗？"
    if "food_name" in missing_set:
        return "你想查询哪一种食物的营养信息？"
    return "请补充更具体的信息，我才能准确回答。"


def build_clarification(intents) -> str | None:
    """缺少必要参数时统一追问，避免用猜测的食物或运动数据回答。"""
    missing = {field for intent in intents for field in intent.missing_slots}
    if not missing:
        return None
    for intent in intents:
        reply = clarification_for_slots(dict(intent.slots), intent.missing_slots)
        if reply:
            return reply
    return "请补充更具体的信息，我才能准确回答。"
