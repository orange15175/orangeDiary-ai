import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.adjustments import plan_router as plan_adjustments_router
from app.api.adjustments import router as observations_router
from app.api.admin_ai import router as admin_ai_router
from app.api.ai import router as ai_router
from app.api.admin_logs import router as admin_logs_router
from app.api.auth import router as auth_router
from app.api.avatars import router as avatars_router
from app.api.checkin import router as checkin_router
from app.api.community_reports import admin_report_router
from app.api.community_reports import report_router as community_reports_router
from app.api.diet import router as diet_router
from app.api.exercise import items_router as exercise_items_router
from app.api.exercise import records_router as exercise_records_router
from app.api.foods import router as foods_router
from app.api.gateway import router as gateway_router
from app.api.images import router as images_router
from app.api.intent import router as intent_router
from app.api.inventory import check_router as ingredient_check_router
from app.api.inventory import router as known_ingredients_router
from app.api.notifications import router as notifications_router
from app.api.posts import admin_router as admin_posts_router
from app.api.posts import router as posts_router
from app.api.plans import meal_router as plan_meals_router
from app.api.plans import preference_router as diet_preference_router
from app.api.plans import router as plans_router
from app.api.rag import router as rag_router
from app.api.reports import router as reports_router
from app.api.recipes import assistant_router as chef_router
from app.api.recipes import router as recipes_router
from app.api.reviews import day_router as diet_day_status_router
from app.api.reviews import router as reviews_router
from app.api.shopping import item_router as shopping_items_router
from app.api.shopping import list_router as shopping_lists_router
from app.api.rag import warmup as rag_warmup
from app.api.summary import router as summary_router
from app.api.users import router as users_router
from app.api.weight import router as weight_router
from app.config import Settings
from app.core.errors import BusinessError, business_error_handler, failure, success
from app.core.trace import get_trace_id, normalize_trace_id, set_trace_id
from app.db.session import database_is_ready, dispose_engine
from app.services.auth_session import get_redis_session_store


# Pydantic 的类型解析失败码：中文文案统一成 Java 的"查询参数格式不正确"。
_TYPE_ERROR_CODES = {
    "int_parsing",
    "int_type",
    "float_parsing",
    "float_type",
    "decimal_parsing",
    "decimal_type",
    "bool_parsing",
    "bool_type",
    "date_from_datetime_parsing",
    "date_parsing",
    "date_type",
    "datetime_from_date_parsing",
    "datetime_parsing",
    "datetime_type",
}


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """把 Pydantic 的英文校验文案换成 DTO 里写好的中文。

    FastAPI 默认 422 的响应体是 `{"detail": [...]}`，前端 `http.js` 读不到 message，
    会退化成一个笼统的"请求失败"。这里统一成 ApiResponse 外壳。

    状态码用 400 而不是 422：Java 的 `ApiExceptionResolver` 对 `@Valid` 失败和
    参数绑定失败都返回 400，前端对"参数错"的既有处理是按 400 写的。
    422 虽然更"语义正确"，但切流时会让同一份前端代码走上不同分支。
    """
    message = "请求参数不合法"
    errors = exc.errors()
    if errors:
        first = errors[0]
        raw = str(first.get("msg", "")).strip().removeprefix("Value error, ").strip()
        # 只放行中文文案（DTO validator 抛的）。Pydantic 自带的英文
        # "Field required" / "Input should be ..." 不能直出给用户。
        if raw and any("\u4e00" <= ch <= "\u9fff" for ch in raw):
            message = raw
        elif first.get("type", "") in _TYPE_ERROR_CODES:
            # 与 Java 的 MethodArgumentTypeMismatchException 文案对齐。
            message = "查询参数格式不正确"
    return JSONResponse(status_code=400, content=failure(400, message))


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动时在后台线程预热检索资源（本地 rerank 模型加载约 10s）。

    放到线程里是为了不阻塞事件循环，同时让第一个真实请求不必承担加载成本。
    """
    task = asyncio.create_task(asyncio.to_thread(rag_warmup))
    try:
        yield
    finally:
        if not task.done():
            task.cancel()
        await dispose_engine()


def configure_logging() -> None:
    """为业务日志配置独立的控制台输出。"""
    logger = logging.getLogger("orange-diary-ai")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # --reload 重载时模块可能重复初始化，避免重复添加输出器。
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
        )
        logger.addHandler(handler)


def create_app() -> FastAPI:
    configure_logging()
    logging.getLogger("orange-diary-ai").info("AI 业务日志已启用")
    app = FastAPI(title="Orange Diary AI Service", version="0.1.0", lifespan=_lifespan)
    # 本地 H5 与 Swagger 的白名单；生产环境改成实际 HTTPS 前端域名。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        # 沿用上游网关传入的 X-Request-Id；没有就新生成，并回写到响应头，
        # 让用户报障时能直接把 id 给出来。
        raw = request.headers.get("X-Request-Id") or request.headers.get("X-Trace-Id")
        set_trace_id(normalize_trace_id(raw))
        response = await call_next(request)
        response.headers["X-Request-Id"] = get_trace_id()
        return response

    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    # 业务接口（M1 起由 Python 自己承载）；旧的 AI 网关接口继续保留给 Java 调用。
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(foods_router)
    app.include_router(diet_router)
    app.include_router(exercise_items_router)
    app.include_router(exercise_records_router)
    app.include_router(weight_router)
    app.include_router(summary_router)
    app.include_router(checkin_router)
    app.include_router(notifications_router)
    app.include_router(posts_router)
    app.include_router(images_router)
    app.include_router(avatars_router)
    app.include_router(community_reports_router)
    app.include_router(admin_posts_router)
    app.include_router(admin_report_router)
    app.include_router(admin_logs_router)
    app.include_router(reports_router)
    app.include_router(ai_router)
    app.include_router(admin_ai_router)
    app.include_router(gateway_router)
    app.include_router(intent_router)
    app.include_router(rag_router)
    app.include_router(recipes_router)
    app.include_router(chef_router)
    app.include_router(plans_router)
    app.include_router(plan_meals_router)
    app.include_router(diet_preference_router)
    app.include_router(known_ingredients_router)
    app.include_router(ingredient_check_router)
    app.include_router(shopping_lists_router)
    app.include_router(shopping_items_router)
    app.include_router(observations_router)
    app.include_router(plan_adjustments_router)
    app.include_router(reviews_router)
    app.include_router(diet_day_status_router)

    @app.get("/health", tags=["系统"])
    def health():
        settings = Settings.from_env()
        return {"code": 200, "message": "Python AI service is running", "data": {"deepseekConfigured": bool(settings.deepseek_api_key), "model": settings.deepseek_model, "ragEnabled": settings.rag_enabled, "ragCollection": settings.rag_collection, "rerankProvider": settings.rerank_provider}}

    @app.get("/ready", tags=["系统"])
    async def ready():
        """就绪判定只有两项：库可查且 schema 到位、Redis 会话可用。

        Qdrant / Ollama / 模型 / 图片 / 搜索都不进这个判定——图片服务挂了不该
        阻止用户登录，各自能力由 `/api/capabilities` 单独汇报。
        """
        settings = Settings.from_env()
        db_ready = await database_is_ready()
        try:
            redis_ready = await get_redis_session_store(settings).ping()
        except RuntimeError:
            redis_ready = False
        if not (db_ready and redis_ready):
            return JSONResponse(status_code=503, content=failure(503, "服务未就绪"))
        return success({"business": "ready"})

    return app


app = create_app()
