"""社区帖子接口，路径与 `PostController` + `AdminPostController` 一致。

两处细节与前端行为绑定，不要"顺手优化"：

- `GET /mine` 必须声明在 `GET /{post_id}` 之前。FastAPI 按声明顺序匹配，
  否则 `/api/posts/mine` 会被解析成 `post_id="mine"`，变成参数格式错误。
- 发帖走幂等（`POST_CREATE`）。断网重试时若不带 `X-Request-Id`，宁可 400
  也不"没有就放行"——放行等于让同一个帖子发两遍。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import success
from app.db.session import get_session
from app.dependencies import get_current_user, get_settings
from app.models.user import User
from app.schemas.post import CreateCommentRequest, CreatePostRequest, ModerationRequest
from app.services import idempotency
from app.services.community_limit import build_limiter
from app.services.image_storage import get_image_storage
from app.services.moderation_service import ModerationService
from app.services.post_service import PostService

router = APIRouter(prefix="/api/posts", tags=["社区帖子"])
admin_router = APIRouter(prefix="/api/admin/posts", tags=["社区管理"])

POST_CREATE = "POST_CREATE"


def get_post_service(
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PostService:
    return PostService(
        db, limiter=build_limiter(settings), storage=get_image_storage(settings)
    )


# ==================== 帖子 ====================


@router.post("", summary="发布帖子")
async def create_post(
    payload: CreatePostRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: PostService = Depends(get_post_service),
):
    result = await idempotency.execute(
        db,
        user_id=user.id,
        operation=POST_CREATE,
        request_id=x_request_id,
        payload=payload.model_dump(mode="json"),
        action=lambda: service.create(user.id, payload),
    )
    return success(result)


@router.get("", summary="公开帖子列表")
async def list_posts(
    page: int = Query(1),
    size: int = Query(10),
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    # 匿名不能看：Java 在方法首行就 requireUser，索引页也要求登录。
    return success(await service.list_public(user.id, page, size))


@router.get("/mine", summary="我的帖子")
async def list_my_posts(
    page: int = Query(1),
    size: int = Query(10),
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(await service.list_mine(user.id, page, size))


@router.get("/{post_id}", summary="帖子详情")
async def get_post(
    post_id: int,
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(await service.get(user.id, post_id))


@router.delete("/{post_id}", summary="删除帖子")
async def delete_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    service: PostService = Depends(get_post_service),
):
    keys = await service.delete(user.id, post_id)
    await db.commit()
    # 提交之后再删对象：回滚时这些图还在被引用，先删就成了"帖子还在图没了"。
    storage = get_image_storage(settings)
    for key in keys:
        await storage.delete(key)
    return success(None)


# ==================== 点赞 ====================


@router.put("/{post_id}/like", summary="点赞")
async def like_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: PostService = Depends(get_post_service),
):
    result = await service.like(user.id, post_id)
    await db.commit()
    return success(result)


@router.delete("/{post_id}/like", summary="取消点赞")
async def unlike_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: PostService = Depends(get_post_service),
):
    result = await service.unlike(user.id, post_id)
    await db.commit()
    return success(result)


# ==================== 评论 ====================


@router.get("/{post_id}/comments", summary="评论列表")
async def list_comments(
    post_id: int,
    page: int = Query(1),
    size: int = Query(10),
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(await service.list_comments(user.id, post_id, page, size))


@router.post("/{post_id}/comments", summary="发表评论")
async def create_comment(
    post_id: int,
    payload: CreateCommentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: PostService = Depends(get_post_service),
):
    result = await service.create_comment(user.id, post_id, payload.content)
    # 评论与通知在同一个事务里提交：通知落库失败就不该留下一条没人知道的评论。
    await db.commit()
    return success(result)


@router.get("/{post_id}/comments/{comment_id}", summary="评论详情")
async def get_comment(
    post_id: int,
    comment_id: int,
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(await service.get_comment(user.id, post_id, comment_id))


@router.delete("/{post_id}/comments/{comment_id}", summary="删除评论")
async def delete_comment(
    post_id: int,
    comment_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: PostService = Depends(get_post_service),
):
    await service.delete_comment(user.id, post_id, comment_id)
    await db.commit()
    return success(None)


# ==================== 管理端 ====================


def get_moderation_service(db: AsyncSession = Depends(get_session)) -> ModerationService:
    return ModerationService(db)


@admin_router.get("", summary="管理端帖子列表")
async def admin_list_posts(
    page: int = Query(1),
    size: int = Query(10),
    keyword: str | None = Query(None),
    authorId: int | None = Query(None),
    postType: str | None = Query(None),
    status: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None),
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(
        await service.list_admin(
            user.id,
            page=page,
            size=size,
            keyword=keyword,
            author_id=authorId,
            post_type=postType,
            status=status,
            from_time=from_,
            until_time=until,
        )
    )


@admin_router.get("/{post_id}", summary="管理端帖子详情")
async def admin_get_post(
    post_id: int,
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(await service.get_admin(user.id, post_id))


@admin_router.get("/{post_id}/comments", summary="管理端评论列表")
async def admin_list_comments(
    post_id: int,
    page: int = Query(1),
    size: int = Query(10),
    user: User = Depends(get_current_user),
    service: PostService = Depends(get_post_service),
):
    return success(await service.comments_admin(user.id, post_id, page, size))


@admin_router.put("/{post_id}/hide", summary="下架帖子")
async def admin_hide_post(
    post_id: int,
    payload: ModerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    moderation: ModerationService = Depends(get_moderation_service),
):
    await moderation.change_post(user.id, post_id, restore=False, reason=payload.reason)
    await db.commit()
    return success(None)


@admin_router.put("/{post_id}/restore", summary="恢复帖子")
async def admin_restore_post(
    post_id: int,
    payload: ModerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    moderation: ModerationService = Depends(get_moderation_service),
):
    await moderation.change_post(user.id, post_id, restore=True, reason=payload.reason)
    await db.commit()
    return success(None)


@admin_router.put("/{post_id}/comments/{comment_id}/remove", summary="移除评论")
async def admin_remove_comment(
    post_id: int,
    comment_id: int,
    payload: ModerationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    moderation: ModerationService = Depends(get_moderation_service),
):
    await moderation.remove_comment(user.id, post_id, comment_id, reason=payload.reason)
    await db.commit()
    return success(None)
