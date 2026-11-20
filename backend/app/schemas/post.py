"""社区请求模型。

与 `dto/post/*` 逐字对齐。字段全部先声明成可空、再在 validator 里抛中文，
理由同 `schemas/diet.py`：Pydantic 自己的 "Field required" 不能直出给用户，
而 Java 的 `@NotBlank(message=...)` 是有中文提示的。

`strip()` 之后再判空是关键：Spring 的 `@NotBlank` 对 `"   "` 判不通过，
如果用 Pydantic 的 `min_length=1`，纯空格会一路走到服务层，报错虽然也是 400，
但会先把一次发帖额度扣掉（限流在内容校验之后），行为与 Java 不同。
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator

_POST_TYPES = {"diet_checkin", "exercise", "weight", "daily_share"}
_VISIBILITIES = {"public", "private"}


class PostImageRequest(BaseModel):
    key: str | None = None
    originalName: str | None = None

    @field_validator("key")
    @classmethod
    def _check_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("图片对象不能为空")
        if len(value) > 500:
            raise ValueError("图片对象过长")
        return value

    @field_validator("originalName")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 255:
            raise ValueError("图片名称过长")
        return value


class CreatePostRequest(BaseModel):
    content: str | None = None
    postType: str | None = None
    visibility: str | None = None
    images: list[PostImageRequest] | None = None

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("帖子内容不能为空")
        if len(value) > 2000:
            raise ValueError("帖子内容不能超过2000字")
        return value

    @field_validator("postType")
    @classmethod
    def _check_post_type(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("帖子类型不能为空")
        if value not in _POST_TYPES:
            raise ValueError("帖子类型不合法")
        return value

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("公开范围不能为空")
        if value not in _VISIBILITIES:
            raise ValueError("公开范围不合法")
        return value

    @field_validator("images")
    @classmethod
    def _check_images(
        cls, value: list[PostImageRequest] | None
    ) -> list[PostImageRequest] | None:
        if value is not None and len(value) > 9:
            raise ValueError("最多添加9张图片")
        return value


class CreateCommentRequest(BaseModel):
    content: str | None = None

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("评论内容不能为空")
        if len(value) > 500:
            raise ValueError("评论不能超过500字")
        return value


class CreateCommunityReportRequest(BaseModel):
    reason: str | None = None
    details: str | None = None

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("请选择举报原因")
        return value

    @field_validator("details")
    @classmethod
    def _check_details(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 500:
            raise ValueError("补充说明不能超过500字")
        return value


class ModerationRequest(BaseModel):
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            raise ValueError("必须填写处理原因")
        if len(value) > 500:
            raise ValueError("处理原因不能超过500字")
        return value


class ResolveReportRequest(BaseModel):
    """举报处置入参。

    Java 侧用 `record ResolutionRequest(String resolution, String reason)`
    **没有** 加 `@Valid`，所以空值校验发生在服务层，文案是"处理结果不合法"/
    "处理原因须为1至500字"。这里同样不加 validator，保持这两条文案由服务层给出。
    """

    resolution: str | None = None
    reason: str | None = None
