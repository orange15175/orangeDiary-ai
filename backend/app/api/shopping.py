"""采购清单与购买接口。

价格未配置时，清单会明确返回 `priceStatus=unknown` / `budgetStatus=unverified`，
前端据此显示"暂无可靠参考价"，不显示一个假总价。
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.shopping import BuildListRequest, PurchaseRequest, ReversalRequest
from app.services.shopping_service import ShoppingService

list_router = APIRouter(prefix="/api/shopping-lists", tags=["采购"])
item_router = APIRouter(prefix="/api/shopping-items", tags=["采购"])


@list_router.post("", summary="按计划生成采购清单")
async def build_list(
    payload: BuildListRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(
        await ShoppingService(db).build_list(user.id, payload.planId, payload.versionNumber)
    )


@list_router.get("/{list_id}", summary="采购清单详情")
async def get_list(
    list_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    return success(await ShoppingService(db).get_list(user.id, list_id))


@list_router.post("/{list_id}/price-refresh", summary="刷新参考价格")
async def refresh_price(
    list_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    service = ShoppingService(db)
    listing = await service.get_list(user.id, list_id)
    # 未配置价格来源：不新建任务、不返回伪造报价，直接说明不可用。
    return success(
        {
            "listId": list_id,
            "priceStatus": listing["totals"].get("priceStatus") if listing.get("totals") else "unknown",
            "budgetStatus": "unverified",
            "available": False,
            "message": "未配置价格来源，暂无可靠参考价；可手动填写实付价。",
        }
    )


@item_router.post("/{item_id}/purchase", summary="确认购买并入库")
async def purchase(
    item_id: int,
    payload: PurchaseRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    request_key = x_request_id or f"manual-{item_id}-{payload.actualQuantity}"
    return success(
        await ShoppingService(db).purchase(
            user.id,
            item_id,
            actual_quantity=Decimal(str(payload.actualQuantity)) if payload.actualQuantity is not None else None,
            paid_price=Decimal(str(payload.paidPrice)) if payload.paidPrice is not None else None,
            add_to_ingredients=payload.addToIngredients,
            request_key=request_key,
        )
    )


@item_router.post("/{item_id}/purchase-reversal", summary="撤销购买（补偿）")
async def reverse_purchase(
    item_id: int,
    payload: ReversalRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    request_key = x_request_id or f"reversal-{item_id}"
    return success(
        await ShoppingService(db).reverse_purchase(
            user.id, item_id, reason=payload.reason, request_key=request_key
        )
    )
