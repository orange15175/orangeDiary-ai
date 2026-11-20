import hmac
import re
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.services.intent_classifier import classify_message
from app.services.model_gateway import ModelFailure

router = APIRouter(prefix="/internal/ai/v2", tags=["意图识别"])


def _trace(request: Request) -> str:
    value = request.headers.get("X-Trace-Id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) else uuid.uuid4().hex


def _authorized(request: Request, settings: Settings) -> bool:
    return bool(settings.ai_service_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + settings.ai_service_token
    )


@router.post("/classify")
async def classify(request: Request):
    settings = Settings.from_env()
    trace_id = _trace(request)
    if not _authorized(request, settings):
        return JSONResponse({"error": {"type": "unauthorized", "traceId": trace_id}}, status_code=401)
    try:
        body = await request.json()
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, str) or not message.strip() or len(message) > 8000:
            return JSONResponse({"error": {"type": "invalid_request", "traceId": trace_id}}, status_code=400)
        # 规则与语义两条路径都在 classify_message 里；这里只管鉴权与状态码。
        result = await classify_message(settings, message, trace_id)
        return JSONResponse({"traceId": trace_id, **result})
    except (ValueError, KeyError, IndexError, TypeError, ModelFailure):
        return JSONResponse({"error": {"type": "classification_failed", "traceId": trace_id}}, status_code=502)
