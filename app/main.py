import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.config import Settings


def configure_logging() -> None:
    """为业务日志配置独立的控制台输出。"""
    # 日志配置集中在应用启动阶段，避免每次请求重复添加 Handler。
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
    """创建 FastAPI 应用并注册跨域、聊天和健康检查路由。"""
    configure_logging()
    logging.getLogger("orange-diary-ai").info("AI 业务日志已启用")
    app = FastAPI(title="Orange Diary AI Service", version="0.1.0")
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
    app.include_router(chat_router)

    @app.get("/health", tags=["系统"])
    def health():
        """返回进程状态和模型配置状态，不执行模型调用。"""
        settings = Settings.from_env()
        return {"code": 200, "message": "Python AI service is running", "data": {"deepseekConfigured": bool(settings.deepseek_api_key), "model": settings.deepseek_model}}

    return app


app = create_app()
