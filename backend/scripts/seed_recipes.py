"""导入 R3 食谱样例（内部联调数据）。

**这些是 sample 数据，不是正式验收食谱**：
- 数量为内部联调用的草稿样量，不用于对外营养结论；
- 营养不写死，运行时按 `food_item` 每 100g 数据计算，缺失保持 null；
- 参考了 lc-course 的食材/步骤结构与私人厨师思路，但**未复制其文本**
  （该仓库未见明确许可证，设计文档 §22 要求不能默认复制）。

幂等：按 `name + scope=system` 查找，存在则替换其食材与步骤，不重复插入。
运行：`.venv/Scripts/python.exe scripts/seed_recipes.py`
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.diet_assistant import Recipe, RecipeIngredient, RecipeStep  # noqa: E402
from app.models.food import FoodItem, FoodServing  # noqa: E402

# 食材显示名 -> 食物库规范名（《中国食物成分表》命名）。
# 找不到的（如八角）留空，营养计算时计入 missing，不硬塞数值。
_FOOD_MAP = {
    "鸡蛋": "鸡蛋",
    "番茄": "番茄（西红柿）",
    "小葱": "葱（小葱，鲜）",
    "盐": "精盐",
    "糖": "白砂糖",
    "食用油": "豆油",
    "五花肉": "猪肉(肋条肉)",
    "冰糖": "冰糖",
    "生抽": "生抽",
    "老抽": "老抽",
    "料酒": "料酒",
    "姜": "姜（鲜）（黄姜）",
    "葱": "大葱",
    "西兰花": "西兰花（绿菜花）",
    "蒜": "大蒜（白皮，鲜）（蒜头）",
    "牛肉": "牛肉",
    "香菇": "香菇（鲜，香蕈，冬菇）",
    "红枣": "枣（干）",
    "茄子": "茄子（代表值）",
    "白菜": "大白菜（代表值）",
    "豆腐": "豆腐",
    "韭菜": "韭菜",
    "土豆": "马铃薯（土豆、洋芋）",
    "鸡胸肉": "鸡胸肉",
    "猪肉": "猪肉(后臀尖)",
    "米饭": "米饭",
}

# (名称, 分类, 份数, 预备分钟, 烹饪分钟, 设备, 食材[(显示名, 数量, 单位, 可选)],
#  步骤[描述])
_RECIPES = [
    (
        "番茄炒蛋", "家常菜", 2, 10, 10, "炒锅",
        [("鸡蛋", 3, "个", False), ("番茄", 2, "个", False), ("小葱", 1, "根", True),
         ("盐", None, None, False), ("糖", None, None, True), ("食用油", 15, "g", False)],
        ["鸡蛋打散加少许盐搅匀；番茄切小块。",
         "热锅倒油，倒入蛋液快速划散，凝固后盛出。",
         "锅中留底油下番茄，炒出汁水后倒回鸡蛋翻匀，撒葱花出锅。"],
    ),
    (
        "红烧肉", "家常菜", 4, 15, 60, "炒锅",
        [("五花肉", 500, "g", False), ("冰糖", 20, "g", False), ("生抽", 30, "g", False),
         ("老抽", 15, "g", False), ("料酒", 15, "g", False), ("姜", 10, "g", True),
         ("葱", 20, "g", True)],
        ["五花肉切块，冷水下锅焯水，撇沫后捞出洗净。",
         "小火炒冰糖至焦糖色，下肉块翻炒上色。",
         "加生抽、老抽、料酒、姜葱和开水，小火炖 50 分钟。",
         "大火收汁，汤汁浓稠裹住肉块即可。"],
    ),
    (
        "清炒西兰花", "素菜", 2, 8, 8, "炒锅",
        [("西兰花", 300, "g", False), ("蒜", 10, "g", False), ("食用油", 10, "g", False),
         ("盐", None, None, False)],
        ["西兰花切小朵，焯水 1 分钟后沥干。",
         "热锅下油爆香蒜末，倒入西兰花快炒。",
         "加盐调味，翻匀出锅。"],
    ),
    (
        "番茄炖牛肉", "炖菜", 3, 15, 50, "炖锅",
        [("牛肉", 300, "g", False), ("番茄", 200, "g", False), ("姜", 10, "g", True),
         ("盐", None, None, False)],
        ["牛肉切块焯水，番茄切块。",
         "锅中下牛肉、姜和番茄翻炒，加开水没过。",
         "小火炖 45 分钟，加盐调味。"],
    ),
    (
        "香菇鸡肉汤", "汤", 3, 10, 40, "汤锅",
        [("鸡胸肉", 200, "g", False), ("香菇", 100, "g", False), ("红枣", 20, "g", True),
         ("盐", None, None, False)],
        ["鸡胸肉切块焯水，香菇洗净切片。",
         "所有食材入锅加清水，大火烧开转小火煮 35 分钟。",
         "加盐调味即可。"],
    ),
    (
        "蒜蓉茄子", "素菜", 2, 10, 15, "炒锅",
        [("茄子", 300, "g", False), ("蒜", 15, "g", False), ("生抽", 15, "g", False),
         ("食用油", 10, "g", False)],
        ["茄子切条，蒸 8 分钟至软。",
         "热油爆香蒜末，加生抽调汁。",
         "浇在茄子上拌匀即可。"],
    ),
    (
        "白菜炖豆腐", "素菜", 3, 8, 20, "炖锅",
        [("白菜", 300, "g", False), ("豆腐", 200, "g", False), ("盐", None, None, False)],
        ["白菜切段，豆腐切块。",
         "锅中加清水烧开，下白菜和豆腐煮 15 分钟。",
         "加盐调味出锅。"],
    ),
    (
        "韭菜炒鸡蛋", "家常菜", 2, 8, 8, "炒锅",
        [("韭菜", 200, "g", False), ("鸡蛋", 2, "个", False), ("食用油", 10, "g", False)],
        ["韭菜切段，鸡蛋打散。",
         "热油炒蛋盛出，再下韭菜快炒。",
         "倒回鸡蛋翻匀，加盐出锅。"],
    ),
    (
        "土豆炖猪肉", "炖菜", 3, 12, 40, "炖锅",
        [("猪肉", 200, "g", False), ("土豆", 300, "g", False), ("生抽", 15, "g", False),
         ("盐", None, None, False)],
        ["猪肉切块焯水，土豆去皮切块。",
         "锅中下猪肉煸炒，加生抽和开水炖 20 分钟。",
         "加入土豆再炖 15 分钟，加盐收汁。"],
    ),
    (
        "西红柿鸡蛋面", "主食", 1, 8, 12, "炒锅",
        [("面条", 100, "g", False), ("鸡蛋", 2, "个", False), ("番茄", 150, "g", False),
         ("食用油", 8, "g", False), ("盐", None, None, False)],
        ["番茄切块，鸡蛋打散炒熟盛出。",
         "锅中炒番茄出汁，加开水烧开。",
         "下面条煮熟，倒回鸡蛋，加盐调味。"],
    ),
]


async def _food_ids(db) -> dict[str, int]:
    wanted = set(_FOOD_MAP.values())
    rows = await db.scalars(select(FoodItem).where(FoodItem.name.in_(wanted)))
    return {food.name: food.id for food in rows}


# 常见份量参考（个/根 -> 克）。这是换算口径，不是营养值：
# 鸡蛋约 50g/个、番茄约 150g/个、小葱约 5g/根，均为家庭常用估值，
# 用于把"3 个鸡蛋"换算成可计算克重；标为 approximate，不宣称精确。
_SERVINGS = {
    "鸡蛋": [("个", 50)],
    "番茄（西红柿）": [("个", 150)],
    "葱（小葱，鲜）": [("根", 5)],
    "马铃薯（土豆、洋芋）": [("个", 150)],
}


async def _seed_servings(db, food_ids: dict[str, int]) -> None:
    for food_name, servings in _SERVINGS.items():
        food_id = food_ids.get(food_name)
        if food_id is None:
            continue
        for serving_name, grams in servings:
            exists = await db.scalar(
                select(FoodServing).where(
                    FoodServing.food_item_id == food_id,
                    FoodServing.serving_name == serving_name,
                )
            )
            if exists is None:
                db.add(
                    FoodServing(
                        food_item_id=food_id,
                        serving_name=serving_name,
                        gram_weight=grams,
                    )
                )


async def seed() -> None:
    factory = get_session_factory()
    async with factory() as db:
        food_ids = await _food_ids(db)
        await _seed_servings(db, food_ids)
        created = updated = 0
        unresolved: set[str] = set()
        for name, category, servings, prep, cook, equipment, ingredients, steps in _RECIPES:
            recipe = await db.scalar(
                select(Recipe).where(Recipe.name == name, Recipe.scope == "system")
            )
            if recipe is None:
                recipe = Recipe(
                    name=name,
                    scope="system",
                    source_type="sample",
                    license="internal-sample",
                    status="active",
                )
                db.add(recipe)
                await db.flush()
                created += 1
            else:
                updated += 1
            recipe.category = category
            recipe.servings = servings
            recipe.prep_minutes = prep
            recipe.cook_minutes = cook
            recipe.equipment = equipment
            recipe.version = (recipe.version or 1)

            await db.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id))
            await db.execute(delete(RecipeStep).where(RecipeStep.recipe_id == recipe.id))
            for index, (display, quantity, unit, optional) in enumerate(ingredients):
                food_item_id = None
                mapped = _FOOD_MAP.get(display)
                if mapped and mapped in food_ids:
                    food_item_id = food_ids[mapped]
                elif mapped:
                    unresolved.add(mapped)
                db.add(
                    RecipeIngredient(
                        recipe_id=recipe.id,
                        food_item_id=food_item_id,
                        original_name=display,
                        quantity=quantity,
                        unit=unit,
                        basis="raw",
                        optional=optional,
                        sort_order=index,
                    )
                )
            for index, description in enumerate(steps, start=1):
                db.add(
                    RecipeStep(
                        recipe_id=recipe.id,
                        step_index=index,
                        description=description,
                    )
                )
        await db.commit()
        print(f"样例食谱导入完成：新增 {created}，更新 {updated}")
        if unresolved:
            print("以下食物在库中未找到，营养将标为缺失：", "、".join(sorted(unresolved)))
    # 同一事件循环内释放连接池，避免退出时在已关闭的循环上清理 transport。
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())
