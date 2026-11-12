import hmac
import re
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import Settings
from app.services.model_gateway import ModelFailure, ModelGateway

router = APIRouter(prefix="/internal/ai/v1", tags=["内部 AI"])


@router.post("/chat/completions")
async def completions(request: Request):
    settings = Settings.from_env()
    trace_id = request.headers.get("X-Trace-Id", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", trace_id):
        trace_id = uuid.uuid4().hex
    headers = {"X-Trace-Id": trace_id}
    try:
        if not settings.ai_service_token:
            raise ModelFailure("service_not_configured", 503)
        if not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + settings.ai_service_token):
            raise ModelFailure("unauthorized", 401)
        try:
            body = await request.json()
        except ValueError:
            raise ModelFailure("invalid_json", 400) from None
        if not isinstance(body, dict):
            raise ModelFailure("invalid_request", 400)
        result = await ModelGateway(settings).execute(body, trace_id)
        if body.get("stream"):
            return StreamingResponse(result, media_type="text/event-stream", headers=headers)
        return JSONResponse(result, headers=headers)
    except ModelFailure as exc:
        return JSONResponse({"error": {"message": "AI 服务调用失败", "type": exc.code, "traceId": trace_id}},
                            status_code=exc.status, headers=headers)
