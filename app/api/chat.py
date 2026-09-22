from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.dependencies import get_chat_service
from app.schemas.chat import ChatRequest
from app.services.chat_service import ChatService

router = APIRouter(prefix="/api/ai", tags=["AI 助手"])
# 使用 OpenAPI 标准 Bearer 方案，Swagger 会提供统一的 Authorize 按钮。
bearer_scheme = HTTPBearer(auto_error=False)


@router.post("/chat")
async def chat(
    request: ChatRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    chat_service: ChatService = Depends(get_chat_service),
):
    """Controller 层只接收 HTTP 请求，业务逻辑交给 ChatService。"""
    # 入口只负责协议转换，业务分支从这里进入 ChatService。
    try:
        # Swagger 会在用户填入 Token 后自动补上 Bearer 前缀。
        authorization = None
        if credentials:
            authorization = f"{credentials.scheme} {credentials.credentials}"
        data = await chat_service.chat(request, authorization)
        # 与现有 Uni-app 前端的 ApiResponse 协议保持一致：成功必须是 code: 0。
        return {"code": 0, "message": "success", "data": data.model_dump()}
    except HTTPException:
        raise
    except RuntimeError as exc:
        status_code = 503 if str(exc) == "未配置 DEEPSEEK_API_KEY" else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
