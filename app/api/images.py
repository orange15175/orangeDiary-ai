"""图片上传与读取（对齐 `ImageController`）。

读取为什么不是"签名 URL 直接给 MinIO"：图片挂在帖子下，可见性要按帖子判定
（私有帖只有作者能看、下架帖只有管理员能看）。把桶开成公共读，等于让
`posts/12/...` 这种可猜的 key 变成绕过权限的入口。所以这里每次都用后端
代理一次，把 `get` / `getAdmin` 的判定复用在图片上。

响应头两个字段是安全相关，不是随手加的：

- `Cache-Control: private, no-store`：图片带权限判定，不能被中间层缓存后
  发给别人。
- `X-Content-Type-Options: nosniff`：即便存储里的 content-type 被写错，
  浏览器也不许猜。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import BusinessError, success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.community import PostImage
from app.models.user import User
from app.services.community_limit import build_limiter
from app.services.image_storage import get_image_storage, validate_upload
from app.services.post_service import PostService

router = APIRouter(prefix="/api/files/images", tags=["图片"])

_PREFIX = "/api/files/images/"


def get_post_service(
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PostService:
    return PostService(
        db, limiter=build_limiter(settings), storage=get_image_storage(settings)
    )


@router.post("", summary="上传帖子图片")
async def upload_image(
    file: UploadFile | None = File(None),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    if file is None:
        raise BusinessError(400, "请选择图片")
    data = await file.read()
    size = len(data)

    # 先扣额度再校验格式：与 Java 顺序一致。代价是"传了张 20MB 的图被拒"
    # 也会吃掉当天字节额度——这是有意的，否则反复传大文件就成了绕过限流的手段。
    await build_limiter(settings).check_image_upload(user.id, size)
    validate_upload(
        size=size,
        content_type=file.content_type,
        header=data[:12],
        avatar=False,
    )

    storage = get_image_storage(settings)
    image = await storage.upload(
        data=data, content_type=file.content_type or "", user_id=user.id
    )
    return success(
        {
            "key": image.key,
            "url": f"{_PREFIX}{image.key}",
            "contentType": image.content_type,
        }
    )


@router.get("/{key:path}", summary="读取帖子图片")
async def read_image(
    key: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    service: PostService = Depends(get_post_service),
):
    image = await db.scalar(
        select(PostImage).where(PostImage.object_key == key).limit(1)
    )
    if image is None:
        raise BusinessError(404, "图片不存在")

    try:
        await service.get(user.id, image.post_id)
    except BusinessError as exc:
        if exc.code != 404:
            raise
        # 作者看自己被下架的帖子图片：走管理员视角再给一次机会；
        # 仍然不行就抛出原来那个 404，不要泄漏"这条帖子存在但被下架了"。
        try:
            await service.get_admin(user.id, image.post_id)
        except BusinessError as denied:
            if denied.code == 403:
                raise exc from None
            raise

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
