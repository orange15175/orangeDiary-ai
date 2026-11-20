"""头像上传与读取（对齐 `AvatarController`）。

读取只要求登录、不要求"是不是本人"：帖子评论里到处都是别人的头像，
按本人判定会让列表里全是裂图。可见性靠 `require_visible`——必须有一个
活跃用户正在用它。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError, success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.services.avatar_service import AvatarService
from app.services.image_storage import get_image_storage, validate_upload

router = APIRouter(prefix="/api/avatars", tags=["头像"])


def get_avatar_service(
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AvatarService:
    return AvatarService(db, storage=get_image_storage(settings))


@router.post("", summary="上传头像")
async def upload_avatar(
    file: UploadFile | None = File(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: AvatarService = Depends(get_avatar_service),
):
    if file is None:
        raise BusinessError(400, "请选择图片")
    data = await file.read()
    # 头像不走社区的图片配额：它是"一人一个"的低频操作，按字节限流只会
    # 让用户改两次头像就撞墙，而滥用面比发帖小得多。
    validate_upload(
        size=len(data), content_type=file.content_type, header=data[:12], avatar=True
    )
    result = await service.upload(
        data=data, content_type=file.content_type or "", user_id=user.id
    )
    await db.commit()
    return success(result)


@router.get("/{key:path}", summary="读取头像")
async def read_avatar(
    key: str,
    _user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    service: AvatarService = Depends(get_avatar_service),
):
    await service.require_visible(key)
    storage = get_image_storage(settings)
    content = await storage.open_bytes(key)
    return Response(
        content=content,
        media_type=await storage.content_type_of(key),
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
