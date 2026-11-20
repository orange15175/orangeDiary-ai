"""只读行为审计：临时 SQLite，不调用模型/搜索，不接触业务数据。

运行：.venv/Scripts/python.exe scripts/audit_chef_scope.py
输出的是当前行为和缺口，不是新功能验收通过声明。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: F401 - 注册全部 ORM 模型
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.config import Settings
from app.db.base import Base
from app.models.diet_assistant import Recipe, RecipeIngredient, RecipeStep
from app.schemas.recipe import ChefRequest
from app.services import knowledge_client
from app.services.chef_service import ChefService
from app.services.recipe_service import RecipeService


async def main():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    original = knowledge_client.search

    async def no_knowledge(*args, **kwargs):
        return knowledge_client.RagResult.empty()

    knowledge_client.search = no_knowledge
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for name, ingredients in [
                ("番茄炒蛋", ["番茄", "鸡蛋", "食用油"]),
                ("辣椒炒五花肉", ["辣椒", "五花肉", "生抽"]),
                ("香煎鸡腿", ["鸡腿", "盐"]),
            ]:
                recipe = Recipe(name=name, scope="system", source_type="sample", servings=2, status="active")
                db.add(recipe)
                await db.flush()
                for index, label in enumerate(ingredients):
                    db.add(RecipeIngredient(recipe_id=recipe.id, original_name=label,
                        quantity=Decimal("2"), unit="个", sort_order=index))
                db.add(RecipeStep(recipe_id=recipe.id, step_index=1, description="仅用于审计的测试步骤"))
            await db.commit()
            recipes = RecipeService(db)
            chef = ChefService(db, replace(Settings.from_env(), deepseek_api_key=""), 1)
            result = {}
            for query in ["辣椒炒五花肉", "辣椒炒五花肉怎么做", "辣椒、五花肉、鸡腿", "清蒸鲈鱼怎么做"]:
                matches = await recipes.search_for_chef(query)
                answer = await chef.respond(query)
                result[query] = {"matched": [r["name"] for r in matches],
                    "returned": [r["name"] for r in answer["recipes"]],
                    "reply_first_line": answer["reply"].splitlines()[0], "degraded": answer["degraded"]}
            scaled = await recipes.scale_servings(1, 4)
            result["servings"] = {"target": scaled["servings"],
                "ingredient_quantity": str(scaled["ingredients"][0]["quantity"]),
                "original_quantity": "2", "expected_scaled_quantity": "4"}
            result["request_fields"] = list(ChefRequest.model_fields)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        knowledge_client.search = original
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
