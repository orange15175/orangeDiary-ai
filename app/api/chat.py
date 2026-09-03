from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService

router = APIRouter(prefix="/api/ai", tags=["AI 助手"])
# 使用 OpenAPI 标准 Bearer 方案，Swagger 会提供统一的 Authorize 按钮。
bearer_scheme = HTTPBearer(auto_error=False)


@router.post("/chat")
async def chat(
    request: ChatRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    """Controller 层只接收 HTTP 请求，业务逻辑交给 ChatService。"""
    try:
        # Swagger 会在用户填入 Token 后自动补上 Bearer 前缀。
        authorization = None
        if credentials:
            authorization = f"{credentials.scheme} {credentials.credentials}"
        data = await ChatService(Settings.from_env()).chat(request, authorization)
        return {"code": 200, "message": "success", "data": data.model_dump()}
    except HTTPException:
        raise
    except RuntimeError as exc:
        status_code = 503 if str(exc) == "未配置 DEEPSEEK_API_KEY" else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
