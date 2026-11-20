"""R3 行为审计：仅使用临时 SQLite 和测试样例，不调用外部模型，不修改业务库。"""
import asyncio, json, sys
from pathlib import Path
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import app.main
from app.db.base import Base
from app.models.diet_assistant import Recipe, KnownIngredient, IngredientReservation, MealPlan
from app.models.user import User
from app.services.plan_service import PlanService
from app.services.inventory_service import InventoryService
from app.services.shopping_service import ShoppingService
from app.services.review_service import ReviewService
from app.services.chef_service import ChefService
from app.core.errors import BusinessError
from app.config import Settings
from test_plan_and_inventory import _setup, _user
from test_shopping import _plan

async def fresh(fn):
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine,expire_on_commit=False)() as db: await fn(db)
    await engine.dispose()

async def plan_probe(db):
    data=await _setup(db)
    for r in data['recipes'][1:]: r.status='inactive'
    await db.commit()
    svc=PlanService(db)
    result=await svc.generate_draft(_user(), {'startDate': date(2026,11,20),'days':1,'mealTypes':['breakfast','lunch','dinner']})
    print('missing_meals',json.dumps({'requested':3,'actual':len(result['meals']),'checks':result['checks']},ensure_ascii=False))
    for food in [data['egg'],data['oil']]:
        db.add(KnownIngredient(user_id=1, food_item_id=food.id,label=food.name,presence='present',quantity_state='known',quantity=Decimal('1'),unit='g'))
    await db.commit()
    result=await svc.generate_draft(_user(), {'startDate':date(2026,11,20),'days':1,'mealTypes':['lunch'],'ingredientMode':'available_only'})
    await svc.adopt(1,result['planId'],1)
    reserved=await db.scalar(select(func.sum(IngredientReservation.quantity)))
    print('available_only_stock',json.dumps({'stock_total_g':2,'reserved_g':str(reserved),'checks':result['checks']}))
    check=await InventoryService(db).create_check(1,result['planId'],1)
    try:
        await InventoryService(db).answer(1,check['id'],[{'itemId':check['items'][0]['id'],'answer':'enough'}],check['version'])
        print('frontend_enough','accepted')
    except BusinessError as e: print('frontend_enough',str(e))

async def purchase_probe(db):
    data=await _plan(db)
    svc=ShoppingService(db)
    listing=await svc.build_list(1,data['plan'].id,1)
    item=listing['items'][0]
    await svc.purchase(1,item['id'],actual_quantity=Decimal('500'),paid_price=None,add_to_ingredients=True,request_key='audit-buy')
    known=await db.scalar(select(KnownIngredient))
    before=known.quantity
    await svc.reverse_purchase(1,item['id'],reason='audit',request_key='audit-undo')
    await db.refresh(known)
    print('purchase_reversal',json.dumps({'before_g':str(before),'after_g':str(known.quantity)}))

async def nextweek_probe(db):
    await _setup(db)
    db.add(User(id=1, nickname='audit', email='audit@example.invalid', password_hash='unused',role='user',status='active'))
    await db.commit()
    ref=await ReviewService(db)._draft_next_plan(1,date(2026,11,9),date(2026,11,15),{})
    plan=await db.scalar(select(MealPlan).order_by(MealPlan.id.desc()))
    print('next_week_draft',json.dumps({'ref':ref,'days':(plan.end_date-plan.start_date).days+1}))

async def chef_probe(db):
    await _setup(db)
    svc=ChefService(db,Settings.from_env(),1)
    async def no_knowledge(_): return SimpleNamespace(hits=[])
    async def no_model(_): return '', 'offline_audit'
    svc._knowledge=no_knowledge; svc._ask_model=no_model
    a=await svc.respond('番茄炒蛋',servings=1)
    b=await svc.respond('番茄炒蛋',servings=4)
    print('chef_servings',json.dumps({'requested':[1,4],'returned':[a['recipes'][0]['servings'],b['recipes'][0]['servings']],'sameIngredients':a['recipes'][0]['ingredients']==b['recipes'][0]['ingredients']}))

async def main():
    for fn in [plan_probe,purchase_probe,nextweek_probe,chef_probe]: await fresh(fn)
asyncio.run(main())

