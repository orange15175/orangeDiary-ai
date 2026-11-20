"""饮食记录接口，路径与 `DietRecordController` 一致。

新增记录走幂等：前端每次提交带一个 `X-Request-Id`，手机端断网重试时不会
在同一天多记一份午餐。头部缺失或不合法直接 400，不做"没有就跳过"的兜底——
跳过等于把重复写入的风险留给了用户。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.diet import CreateDietRecordRequest
from app.services import idempotency
from app.services.diet_service import DietService

router = APIRouter(prefix="/api/diet-records", tags=["饮食记录"])

DIET_CREATE = "DIET_CREATE"


def get_diet_service(db: AsyncSession = Depends(get_session)) -> DietService:
    return DietService(db)


@router.post("", summary="新增饮食记录")
async def create_record(
    payload: CreateDietRecordRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: DietService = Depends(get_diet_service),
):
    result = await idempotency.execute(
        db,
        user_id=user.id,
        operation=DIET_CREATE,
        request_id=x_request_id,
        # 摘要只取业务字段；不含 header，也不含服务端生成的时间。
        payload=payload.model_dump(mode="json"),
        action=lambda: service.create_record(user.id, payload),
    )
    return success(result)


@router.get("", summary="查询当天饮食记录")
async def list_records(
    date: date = Query(..., description="查询日期，格式yyyy-MM-dd"),
    user: User = Depends(get_current_user),
    service: DietService = Depends(get_diet_service),
):
    return success(await service.list_records(user.id, date))


@router.delete("/{record_id}", summary="删除饮食记录")
async def delete_record(
    record_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: DietService = Depends(get_diet_service),
):
    await service.delete_record(user.id, record_id)
    # 删除也要提交：service 内部只 flush，事务边界统一放在接口层。
    await db.commit()
    return success(None)
