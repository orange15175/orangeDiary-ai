"""头像上传与可见性（对齐 `AvatarService`）。

`avatar_upload` 这张登记表存在只有一个理由：**防止用户把任意 URL 填进
`user.avatar`**。没有它的话，把 `avatar` 设成外部图片地址就能做流量统计、
甚至挂一张违法图片，而审核只看得到自己域名下的内容。

`require_visible` 再做一层：只有"有活跃用户正在用"的头像才能被读。
否则删号后头像对象仍可被访问，等于留了一堆没有归属的公开图片。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.models.avatar import AvatarUpload
from app.models.user import User
from app.services.image_storage import ImageStorage

AVATAR_PREFIX = "/api/avatars/"

# 上传后一天内没被保存进 profile 的，视为放弃，允许清理任务删掉。
AVATAR_TTL = timedelta(days=1)


class AvatarService:
    def __init__(self, db: AsyncSession, *, storage: ImageStorage) -> None:
        self._db = db
        self._storage = storage

    async def upload(self, *, data: bytes, content_type: str, user_id: int) -> dict[str, Any]:
        image = await self._storage.upload(
            data=data, content_type=content_type, user_id=user_id, avatar=True
        )
        self._db.add(
            AvatarUpload(object_key=image.key, user_id=user_id, created_at=datetime.now())
        )
        try:
            await self._db.flush()
        except IntegrityError:
            # 登记失败就把刚传上去的对象删掉，否则它会永远留在桶里没人认领。
            await self._db.rollback()
            await self._storage.delete(image.key)
            raise
        return {
            "key": image.key,
            "url": AVATAR_PREFIX + image.key,
            "contentType": image.content_type,
        }

    async def require_visible(self, key: str) -> None:
        if ".." in key or not key.startswith("avatars/"):
            raise BusinessError(404, "头像不存在")
        count = await self._db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.avatar == AVATAR_PREFIX + key, User.status == "active")
        )
        if not count:
            raise BusinessError(404, "头像不存在")

    async def cleanup(self) -> int:
        """删掉"上传超过一天且没有任何用户引用"的对象，返回删除条数。

        引用检查要在删除前重做一次：登记表里的行可能刚好在这一天里被
        `PUT /profile` 用上，只看登记时间就删会把正在用的头像抹掉。
        """
        cutoff = datetime.now() - AVATAR_TTL
        assets = (
            await self._db.scalars(
                select(AvatarUpload).where(AvatarUpload.created_at < cutoff)
            )
        ).all()
        removed = 0
        for asset in assets:
            used = await self._db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.avatar == AVATAR_PREFIX + asset.object_key)
            )
            if used:
                continue
            await self._storage.delete(asset.object_key)
            await self._db.delete(asset)
            removed += 1
        if removed:
            await self._db.flush()
        return removed
