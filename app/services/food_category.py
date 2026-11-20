"""食物分类归一。

源数据里的分类五花八门（"谷物及制品"、"meat"、"蔬果类"），前端只认五个展示分类。
这里逐字复刻 Java `FoodCategoryResolver` 的关键词顺序——顺序变了结果就会变：
"果汁"同时命中"果"和"汁"，Java 先判蔬果，所以归到蔬果；改成先判饮料就全变了。
"""
from __future__ import annotations

# 分类码 → 中文名。顺序即前端 Tab 顺序，不要按字母重排。
CATEGORY_CODES: list[tuple[str, str]] = [
    ("frequent", "常吃"),
    ("custom", "自定义"),
    ("favorite", "收藏"),
    ("staple", "主食"),
    ("meat_dairy", "肉蛋奶"),
    ("fruit_vegetable", "蔬果"),
    ("beverage", "饮料"),
    ("snack", "零食"),
]

_CODE_TO_CATEGORY = {
    "staple": "主食",
    "meat_dairy": "肉蛋奶",
    "fruit_vegetable": "蔬果",
    "beverage": "饮料",
    "snack": "零食",
}

_STAPLE = ("主食", "谷物", "米", "面", "粮", "饭", "粉", "bread", "rice", "grain", "cereal", "noodle", "pasta", "potato")
_MEAT_DAIRY = ("肉", "蛋", "奶", "禽", "畜", "豆", "鱼", "虾", "meat", "poultry", "egg", "dairy", "milk", "cheese", "yogurt", "bean", "legume", "seafood", "fish")
_FRUIT_VEG = ("蔬", "果", "vegetable", "fruit", "produce", "green")
_BEVERAGE = ("饮", "水", "茶", "咖啡", "果汁", "beverage", "drink", "juice", "tea", "coffee")
_SNACK = ("零食", "甜", "点心", "糖", "snack", "dessert", "candy", "chip", "cookie", "sweets")


def normal_category_for_code(code: str | None) -> str | None:
    """分类码转中文；frequent/custom/favorite/未知码返回 None（表示"不按分类过滤"）。"""
    return _CODE_TO_CATEGORY.get(code or "")


def resolve(source_category: str | None) -> str:
    value = (source_category or "").strip().lower()
    # 判定顺序必须与 Java 一致，见模块文档。
    for keywords, label in (
        (_STAPLE, "主食"),
        (_MEAT_DAIRY, "肉蛋奶"),
        (_FRUIT_VEG, "蔬果"),
        (_BEVERAGE, "饮料"),
        (_SNACK, "零食"),
    ):
        if any(keyword in value for keyword in keywords):
            return label
    return "其他"
