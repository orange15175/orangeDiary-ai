"""头像上传登记表。

存在的意义只有两条：防止用户把任意 URL 填进 `user.avatar`，以及让清理任务
能判定"这个对象已经一天没人引用，可以删"。字段与存量 DDL 一致。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

from app.models.user import BigIntPK


class AvatarUpload(Base):
    __tablename__ = "avatar_upload"

    # 主键就是对象存储的 key（形如 `avatars/12/xxx.png`），没有自增 id。
    object_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
