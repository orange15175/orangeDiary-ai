"""用户确认的轻量食材口径，以及审计发现的问题回归。全部使用内存数据库。"""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from test_plan_and_inventory import factory, db, _setup, _user
from app.config import Settings
from app.core.errors import BusinessError
from app.core.limiter import APP_ZONE
from app.models.diet import DietRecord
from app.models.diet_assistant import KnownIngredient, IngredientReservation, MealPlan, MealPlanVersion, IngredientLedger
from app.models.user import User
from app.services.inventory_service import InventoryService
from app.services.plan_service import PlanService
from app.services.shopping_service import ShoppingService
from app.services.chef_service import ChefService
from app.services.adjustment_service import AdjustmentService
from app.services.review_service import ReviewService
from app.services import knowledge_client


@pytest.mark.asyncio
async def test_missing_meals_cannot_pass_or_be_adopted(db):
    data = await _setup(db)
    for recipe in data['recipes'][1:]:
        recipe.status = 'inactive'
    await db.commit()
    svc = PlanService(db)
    result = await svc.generate_draft(_user(), {'startDate': date(2026, 11, 20), 'days': 1})
    assert result['checks']['requestedMealCount'] == 3
    assert result['checks']['code'] == 'CANDIDATE_POOL_INSUFFICIENT'
    with pytest.raises(BusinessError):
        await svc.adopt(1, result['planId'], 1)


@pytest.mark.asyncio
async def test_today_presence_is_not_a_quantity_guarantee_and_expires_next_day(db):
    data = await _setup(db)
    memory = InventoryService(db)
    for food in [data['egg'], data['oil']]:
        await memory.upsert(1, {'label': food.name, 'foodItemId': food.id, 'presence': 'present'})
    svc = PlanService(db)
    draft = await svc.generate_draft(_user(), {'startDate': date(2026,11,20), 'mealTypes': ['lunch'], 'ingredientMode': 'available_only'})
    assert draft['checks']['ingredientPolicy'] == 'light_memory'
    await svc.adopt(1, draft['planId'], 1)
    assert list(await db.scalars(select(IngredientReservation))) == []
    row = await db.scalar(select(KnownIngredient).where(KnownIngredient.food_item_id == data['egg'].id))
    row.last_confirmed_at = datetime.now(APP_ZONE).replace(tzinfo=None) - timedelta(days=1)
    await db.commit()
    listed = await memory.list_known(1)
    egg = next(item for item in listed if item['foodItemId'] == data['egg'].id)
    assert egg['presence'] == 'unknown'
    assert 'quantity' not in egg
    blocked = await svc.generate_draft(_user(), {'startDate': date(2026,11,20), 'mealTypes': ['lunch'], 'ingredientMode': 'available_only'})
    assert blocked['status'] == 'infeasible'


@pytest.mark.asyncio
async def test_purchase_suggestions_are_not_purchase_facts_and_undo_is_idempotent(db):
    data = await _setup(db)
    draft = await PlanService(db).generate_draft(_user(), {'startDate': date(2026,11,20), 'mealTypes':['lunch']})
    svc = ShoppingService(db)
    listing = await svc.build_list(1, draft['planId'], 1)
    hints = await InventoryService(db).list_known(1)
    assert hints and all(item['presence'] == 'unknown' for item in hints)
    assert all(item['source'] == 'suggestion' for item in hints)
    assert not await InventoryService(db).list_known(2)
    item_id = listing['items'][0]['id']
    for key in ['buy-once', 'buy-once', 'double-click-new-key']:
        await svc.purchase(1, item_id, actual_quantity=None, paid_price=Decimal('12'), add_to_ingredients=True, request_key=key)
    assert len(list(await db.scalars(select(IngredientLedger)))) == 1
    memory = await db.scalar(select(KnownIngredient))
    assert memory.quantity is None and memory.source == 'purchase'
    for _ in range(2):
        await svc.reverse_purchase(1, item_id, reason='点错了', request_key='undo-once')
    await db.refresh(memory)
    assert memory.presence == 'unknown' and memory.quantity is None
    listing = await svc.get_list(1, listing['id'])
    assert listing['items'][0]['state'] == 'to_buy'
    assert 'paidPrice' not in listing['items'][0]['price']


@pytest.mark.asyncio
async def test_undo_purchase_does_not_overwrite_later_manual_confirmation(db):
    await _setup(db)
    draft = await PlanService(db).generate_draft(_user(), {'startDate': date(2026,11,20), 'mealTypes':['lunch']})
    svc = ShoppingService(db)
    listing = await svc.build_list(1, draft['planId'], 1)
    item = listing['items'][0]
    await svc.purchase(1,item['id'],actual_quantity=None,paid_price=None,add_to_ingredients=True,request_key='buy')
    memory = await db.scalar(select(KnownIngredient))
    await InventoryService(db).upsert(1, {'id': memory.id, 'label': memory.label, 'version': memory.version, 'presence': 'absent'})
    await svc.reverse_purchase(1,item['id'],reason='撤销',request_key='undo')
    await db.refresh(memory)
    assert memory.presence == 'absent'


@pytest.mark.asyncio
async def test_leftover_answer_and_execution_never_deduct_grams(db):
    await _setup(db)
    svc = PlanService(db)
    draft = await svc.generate_draft(_user(), {'startDate': date(2026,11,20), 'mealTypes':['lunch']})
    memory = InventoryService(db)
    check = await memory.create_check(1, draft['planId'], 1)
    answered = await memory.answer(1,check['id'],[{'itemId': item['id'], 'answer':'some'} for item in check['items']],check['version'])
    assert answered['status'] == 'confirmed'
    assert all(item['confirmedQuantity'] is None for item in answered['items'])
    await svc.adopt(1,draft['planId'],1)
    await svc.set_execution(1,draft['meals'][0]['id'],'executed')
    hints = await memory.list_known(1)
    assert all(item['source'] == 'used' and item['presence'] == 'unknown' for item in hints)
    assert all('剩余' in item['hint'] for item in hints)


@pytest.mark.asyncio
async def test_adjustment_inherits_constraints_and_rejects_stale_draft(db):
    await _setup(db)
    svc = PlanService(db)
    original = await svc.generate_draft(_user(), {'startDate':date(2026,11,20),'mealTypes':['lunch'], 'allergens':['虾'], 'maxPrepMinutes':30})
    await svc.adopt(1,original['planId'],1)
    first = await svc.revise(1,original['planId'],'加班',max_minutes=15)
    second = await svc.revise(1,original['planId'],'另一个草稿')
    assert first['constraints']['allergens'] == ['虾']
    assert first['constraints']['maxPrepMinutes'] == 15
    await svc.adopt(1,original['planId'],first['versionNumber'])
    with pytest.raises(BusinessError) as exc:
        await svc.adopt(1,original['planId'],second['versionNumber'])
    assert exc.value.code == 409
    with pytest.raises(BusinessError):
        await svc.set_execution(1,original['meals'][0]['id'],'executed')


@pytest.mark.asyncio
async def test_next_week_has_seven_days_and_chef_scales(db, monkeypatch):
    await _setup(db)
    db.add(User(id=1,nickname='test',email='test@example.invalid',role='user',status='active'))
    await db.commit()
    ref = await ReviewService(db)._draft_next_plan(1,date(2026,11,9),date(2026,11,15),{})
    plan = await db.scalar(select(MealPlan))
    assert ref and (plan.end_date-plan.start_date).days == 6
    async def empty(*args,**kwargs): return knowledge_client.RagResult.empty()
    monkeypatch.setattr(knowledge_client,'search',empty)
    svc = ChefService(db,replace(Settings.from_env(),deepseek_api_key=''),1)
    one = await svc.respond('番茄炒蛋',servings=1)
    four = await svc.respond('番茄炒蛋',servings=4)
    assert four['recipes'][0]['servings'] == 4
    assert four['recipes'][0]['ingredients'][0]['grams'] == one['recipes'][0]['ingredients'][0]['grams'] * 4


@pytest.mark.asyncio
async def test_observation_cannot_link_missing_or_another_accounts_record(db):
    data = await _setup(db)
    record = DietRecord(user_id=2, food_item_id=data['egg'].id, meal_type='lunch',food_name_snapshot='鸡蛋',quantity_g=Decimal('100'),calorie_snapshot=Decimal('144'),record_time=datetime(2026,11,20,12),record_date=date(2026,11,20))
    db.add(record); await db.commit()
    svc=AdjustmentService(db)
    observation = await svc.create_observation(1,occurred_at=datetime(2026,11,20,12),description='聚餐',source='text',estimated_nutrition=None,completeness='partial')
    for ids in [[record.id],[99999]]:
        with pytest.raises(BusinessError) as exc:
            await svc.resolve(1,observation['id'],ids)
        assert exc.value.code == 404


@pytest.mark.asyncio
async def test_http_text_flow_with_version_and_memory_answers(db):
    import httpx
    from app.main import app
    from app.dependencies import get_current_user
    from app.db.session import get_session
    await _setup(db)
    async def session_override():
        yield db
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user] = lambda: _user()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            result = await client.post('/api/meal-plans/drafts', json={'startDate':'2026-11-20','days':1,'mealTypes':['lunch']})
            assert result.status_code == 200, result.text
            draft=result.json()['data']
            result = await client.post(f"/api/meal-plans/{draft['planId']}/adopt",json={'versionNumber':1})
            assert result.status_code == 200, result.text
            check=(await client.post('/api/ingredient-checks',json={'planId':draft['planId']})).json()['data']
            result=await client.post(f"/api/ingredient-checks/{check['id']}/answers",json={'version':check['version'],'answers':[{'itemId':item['id'],'answer':'some'} for item in check['items']]})
            assert result.status_code == 200, result.text
            assert result.json()['data']['status'] == 'confirmed'
            result=await client.post(f"/api/meal-plans/{draft['planId']}/adjustments",json={'note':'今晚加班','maxPrepMinutes':15})
            assert result.status_code == 200, result.text
            version=result.json()['data']['versionNumber']
            result=await client.get(f"/api/meal-plans/{draft['planId']}",params={'versionNumber':version})
            assert result.json()['data']['constraints']['maxPrepMinutes']==15
            assert result.json()['data']['activeVersion']==1
            assert result.json()['data']['versionStatus']=='draft'
            result=await client.post(f"/api/meal-plans/{draft['planId']}/adopt",json={'versionNumber':version})
            assert result.status_code==200
    finally:
        app.dependency_overrides.pop(get_session,None)
        app.dependency_overrides.pop(get_current_user,None)
