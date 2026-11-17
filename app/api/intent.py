import hmac
import re
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import Settings
from app.services.intent_router import build_clarification, clarification_for_slots, normalize_intents
from app.services.intent_validator import normalize_semantic_result, parse_semantic_json
from app.services.model_gateway import ModelFailure, ModelGateway

router = APIRouter(prefix="/internal/ai/v2", tags=["意图识别"])


def _trace(request: Request) -> str:
    value = request.headers.get("X-Trace-Id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) else uuid.uuid4().hex


def _authorized(request: Request, settings: Settings) -> bool:
    return bool(settings.ai_service_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), "Bearer " + settings.ai_service_token
    )


def _intent_payload(result) -> dict:
    """规则与语义路径共用同一份下发协议（snake_case）。"""
    return {
        "intent": result.intent,
        "confidence": result.confidence,
        "slots": dict(result.slots),
        "missing_slots": list(result.missing_slots),
        "source": result.source,
    }


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
        results = normalize_intents(message)
        if results:
            return JSONResponse({
                "traceId": trace_id,
                "intents": [_intent_payload(result) for result in results],
                "reply": build_clarification(results),
            })
        result = await ModelGateway(settings).execute(
            {
                "model": "intent-classification",
                "messages": [{"role": "user", "content": message}],
                "response_format": {"type": "json_object"},
            },
            trace_id,
        )
        content = (result.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
        parsed = parse_semantic_json(content)
        if parsed is None:
            # 模型没有给出可解析结果时退回追问，不静默透传空意图。
            return JSONResponse({
                "traceId": trace_id,
                "intents": [{
                    "intent": "clarification",
                    "confidence": 0.0,
                    "slots": {},
                    "missing_slots": [],
                    "source": "semantic_fallback",
                }],
                "reply": None,
            })
        normalized = normalize_semantic_result(parsed)
        # 缺参时即使模型没写 reply 也要追问，避免 Java 端拿空参数继续生成答案。
        reply = normalized["reply"] or clarification_for_slots(
            normalized["slots"], normalized["missing_slots"]
        )
        return JSONResponse({
            "traceId": trace_id,
            "intents": [{
                "intent": normalized["intent"],
                "confidence": normalized["confidence"],
                "slots": normalized["slots"],
                "missing_slots": normalized["missing_slots"],
                "source": "semantic",
            }],
            "reply": reply,
        })
    except (ValueError, KeyError, IndexError, TypeError, ModelFailure):
        return JSONResponse({"error": {"type": "classification_failed", "traceId": trace_id}}, status_code=502)
