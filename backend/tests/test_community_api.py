"""社区 HTTP 契约测试。

不测业务分支（那在服务层测过了），只测"接线"：

- 路由顺序：`/api/posts/mine` 不能被 `/api/posts/{post_id}` 吃掉；
- 幂等：同一个 `X-Request-Id` 发两次只落一条帖子；
- 错误外壳：校验失败是 400 且 message 是中文，不是 FastAPI 默认的 422/英文；
- 429 必须带 `Retry-After`（前端据此显示"再等 N 秒"）。
"""
from __future__ import annotations

import base64
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.auth import get_auth_service
from app.api.community_reports import get_report_service
from app.api.posts import get_post_service
from app.config import Settings
from app.core.errors import BusinessError
from app.db.base import Base
from app.db.session import get_session
from app.dependencies import get_session_store, get_settings
from app.main import create_app
from app.services.auth_service import AuthService
from app.services.auth_session import InMemoryAuthSessionStore
from app.services.community_report_service import CommunityReportService
from app.services.post_service import PostService


def build_settings() -> Settings:
    return Settings(
        deepseek_api_key="test",
        deepseek_base_url="https://example.invalid",
        deepseek_model="test-model",
        deepseek_timeout_seconds=1.0,
        deepseek_input_price_per_million=0.0,
        deepseek_output_price_per_million=0.0,
        jwt_secret=base64.b64encode(b"community-secret-key-32-byte!!").decode("ascii"),
    )


class FakeLimiter:
    def __init__(self) -> None:
        self.deny: str | None = None

    def _check(self, action: str) -> None:
        if self.deny == action:
            raise BusinessError(429, "发帖次数已达上限，请稍后重试", 1800)

    async def check_post(self, user_id: int) -> None:
        self._check("post")

    async def check_comment(self, user_id: int) -> None:
        self._check("comment")

    async def check_image_upload(self, user_id: int, size: int) -> None:
        self._check("image")

    async def check_report(self, user_id: int) -> None:
        self._check("report")


class FakeStorage:
    async def content_type_of(self, key: str) -> str:
        return "image/png"

    async def delete(self, key: str) -> None:
        return None


@pytest_asyncio.fixture
async def context():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    store = InMemoryAuthSessionStore()
    settings = build_settings()
    limiter = FakeLimiter()

    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: AuthService(session, store, settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_post_service] = lambda: PostService(
        session, limiter=limiter, storage=FakeStorage()
    )
    app.dependency_overrides[get_report_service] = lambda: CommunityReportService(
        session, limiter=limiter
    )

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, limiter

    await session.close()
    await engine.dispose()


async def _register(http: AsyncClient, nickname: str = "社区橙子") -> dict[str, str]:
    email = f"community-{uuid.uuid4().hex[:10]}@example.com"
    await http.post(
        "/api/auth/register",
        json={
            "username": nickname,
            "email": email,
            "password": "orange123",
            "confirmPassword": "orange123",
        },
    )
    tokens = (
        await http.post("/api/auth/login", json={"account": email, "password": "orange123"})
    ).json()["data"]
    return {"Authorization": f"Bearer {tokens['token']}"}


def _post_body(**overrides) -> dict:
    body = {"content": "今天吃了一碗热干面", "postType": "daily_share", "visibility": "public"}
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_requires_login(context):
    http, _limiter = context
    response = await http.get("/api/posts")
    assert response.status_code == 401
    assert response.json()["message"] == "请先登录"


@pytest.mark.asyncio
async def test_missing_request_id_returns_400(context):
    http, _limiter = context
    headers = await _register(http)
    response = await http.post("/api/posts", headers=headers, json=_post_body())
    assert response.status_code == 400
    assert response.json()["message"] == "X-Request-Id格式不合法"


@pytest.mark.asyncio
async def test_create_then_replay_with_same_request_id(context):
    http, _limiter = context
    headers = await _register(http)
    request_id = "community-api-0001"
    payload = _post_body()

    first = await http.post(
        "/api/posts", headers={**headers, "X-Request-Id": request_id}, json=payload
    )
    assert first.status_code == 200
    body = first.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "published"
    assert body["data"]["authorName"] == "社区橙子"

    second = await http.post(
        "/api/posts", headers={**headers, "X-Request-Id": request_id}, json=payload
    )
    assert second.status_code == 200
    assert second.json()["data"]["id"] == body["data"]["id"]

    listing = await http.get("/api/posts/mine", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 1


@pytest.mark.asyncio
async def test_blank_content_message_is_chinese_400(context):
    http, _limiter = context
    headers = await _register(http)
    response = await http.post(
        "/api/posts",
        headers={**headers, "X-Request-Id": "community-api-0002"},
        json=_post_body(content="   "),
    )
    assert response.status_code == 400
    assert response.json()["message"] == "帖子内容不能为空"


@pytest.mark.asyncio
async def test_rate_limit_returns_retry_after(context):
    http, limiter = context
    headers = await _register(http)
    limiter.deny = "post"

    response = await http.post(
        "/api/posts",
        headers={**headers, "X-Request-Id": "community-api-0003"},
        json=_post_body(),
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1800"
    assert response.json()["message"] == "发帖次数已达上限，请稍后重试"


@pytest.mark.asyncio
async def test_mine_route_is_not_parsed_as_post_id(context):
    http, _limiter = context
    headers = await _register(http)
    response = await http.get("/api/posts/mine", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    # 被当成 post_id 时这里会是"查询参数格式不正确"。
    assert {"records", "total", "size", "current", "pages"} <= set(data)


@pytest.mark.asyncio
async def test_comment_too_long_returns_400(context):
    http, _limiter = context
    headers = await _register(http)
    created = await http.post(
        "/api/posts",
        headers={**headers, "X-Request-Id": "community-api-0004"},
        json=_post_body(),
    )
    post_id = created.json()["data"]["id"]

    response = await http.post(
        f"/api/posts/{post_id}/comments", headers=headers, json={"content": "字" * 501}
    )
    assert response.status_code == 400
    assert response.json()["message"] == "评论不能超过500字"


@pytest.mark.asyncio
async def test_comment_then_list_and_detail(context):
    http, _limiter = context
    headers = await _register(http)
    created = await http.post(
        "/api/posts",
        headers={**headers, "X-Request-Id": "community-api-0005"},
        json=_post_body(),
    )
    post_id = created.json()["data"]["id"]

    comment = await http.post(
        f"/api/posts/{post_id}/comments", headers=headers, json={"content": "自己补一句"}
    )
    assert comment.status_code == 200
    comment_id = comment.json()["data"]["id"]

    listing = await http.get(f"/api/posts/{post_id}/comments", headers=headers)
    assert listing.json()["data"]["total"] == 1

    detail = await http.get(f"/api/posts/{post_id}/comments/{comment_id}", headers=headers)
    assert detail.json()["data"]["content"] == "自己补一句"


@pytest.mark.asyncio
async def test_like_then_unlike(context):
    http, _limiter = context
    author = await _register(http, "作者")
    visitor = await _register(http, "路人")
    created = await http.post(
        "/api/posts",
        headers={**author, "X-Request-Id": "community-api-0006"},
        json=_post_body(),
    )
    post_id = created.json()["data"]["id"]

    liked = await http.put(f"/api/posts/{post_id}/like", headers=visitor)
    assert liked.json()["data"] == {"liked": True, "likeCount": 1}

    unliked = await http.delete(f"/api/posts/{post_id}/like", headers=visitor)
    assert unliked.json()["data"] == {"liked": False, "likeCount": 0}


@pytest.mark.asyncio
async def test_admin_endpoint_rejects_normal_user(context):
    http, _limiter = context
    headers = await _register(http)
    response = await http.get("/api/admin/posts", headers=headers)
    assert response.status_code == 403
    assert response.json()["message"] == "仅管理员可访问社区管理"


@pytest.mark.asyncio
async def test_delete_post_then_not_found(context):
    http, _limiter = context
    headers = await _register(http)
    created = await http.post(
        "/api/posts",
        headers={**headers, "X-Request-Id": "community-api-0007"},
        json=_post_body(),
    )
    post_id = created.json()["data"]["id"]

    deleted = await http.delete(f"/api/posts/{post_id}", headers=headers)
    assert deleted.status_code == 200

    after = await http.get(f"/api/posts/{post_id}", headers=headers)
    assert after.status_code == 404
    assert after.json()["message"] == "帖子不存在"
