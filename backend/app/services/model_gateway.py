import asyncio
import json
import logging
import random
import threading
import time

import httpx

from app.config import Settings
from app.prompts.scenarios import SCENARIOS
from app.rag.circuit import CircuitBreaker

logger = logging.getLogger("orange-diary-ai")

# 可重试的瞬时故障：连接失败 / 超时 / 429 / 5xx。4xx（除 429）是参数或鉴权问题，
# 重试不会变好；空回答与截断是上游"正常返回了无效内容"，重试大概率还是这样。
_RETRYABLE_CODES = {
    "upstream_timeout",
    "upstream_http_429",
    "upstream_http_500",
    "upstream_http_502",
    "upstream_http_503",
    "upstream_http_504",
    "invalid_upstream_response",
}

# 熔断器按场景隔离：聊天挂了不该连带拖垮摘要或意图分类。进程级共享，
# 因为调用方每次都 `ModelGateway(settings)` 新建实例，状态必须活在外面。
_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def _breaker_for(scene_id: str, settings: Settings) -> CircuitBreaker:
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(scene_id)
        if breaker is None:
            breaker = CircuitBreaker(
                f"model:{scene_id}",
                failure_threshold=settings.model_breaker_failure_threshold,
                reset_timeout_seconds=settings.model_breaker_reset_seconds,
            )
            _BREAKERS[scene_id] = breaker
        return breaker


def reset_model_breakers() -> None:
    """清空熔断器状态（测试与运维重载用）。"""
    with _BREAKERS_LOCK:
        _BREAKERS.clear()


def _is_retryable(code: str) -> bool:
    return code in _RETRYABLE_CODES


def _retry_delay(attempt: int, settings: Settings) -> float:
    """指数退避 + 全抖动（0.5x~1.5x），并封顶，避免重试把请求拖出总预算。"""
    raw = settings.model_retry_base_delay_seconds * (settings.model_retry_backoff_factor ** attempt)
    jittered = raw * (0.5 + random.random())
    return min(jittered, settings.model_retry_max_delay_seconds)


class ModelFailure(Exception):
    def __init__(self, code: str, status: int = 502):
        self.code, self.status = code, status
        super().__init__(code)


class ModelGateway:
    """The only upstream text-model transport; tools are executed by the caller (Python side)."""
    def __init__(self, settings: Settings):
        self.settings = settings

    def prepare(self, body: dict) -> tuple[dict, object]:
        if not isinstance(body.get("stream", False), bool):
            raise ModelFailure("invalid_stream", 400)
        scenario_id = body.get("model")
        scene = SCENARIOS.get(scenario_id) if isinstance(scenario_id, str) else None
        if scene is None:
            raise ModelFailure("unknown_scenario", 400)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant", "tool"}
            for m in messages
        ):
            raise ModelFailure("invalid_messages", 400)
        if not scene.allow_tools and (body.get("tools") or any(m["role"] == "tool" for m in messages)):
            raise ModelFailure("tools_not_allowed", 400)
        # Never trust callers to override model, prompt, thinking or output limits.
        payload = {
            "model": self.settings.deepseek_model,
            "messages": [{"role": "system", "content": scene.prompt}] + [m for m in messages if m["role"] != "system"],
            "temperature": 0.3, "max_tokens": scene.max_tokens,
            "thinking": {"type": "disabled"}, "stream": bool(body.get("stream", False)),
        }
        if payload["stream"] and not scene.allow_tools:
            raise ModelFailure("stream_not_allowed", 400)
        if scene.allow_tools:
            for key in ("tools", "tool_choice", "parallel_tool_calls"):
                if key in body:
                    payload[key] = body[key]
        if "response_format" in body and not scene.allow_tools:
            payload["response_format"] = body["response_format"]
        return payload, scene

    def log_result(self, scene_id, scene, trace_id, start, status, usage=None, error=None):
        # No JWT, prompts, model replies or health records in logs.
        logger.info("AI_CALL %s", json.dumps({
            "traceId": trace_id, "scene": scene_id, "promptVersion": scene.version,
            "model": self.settings.deepseek_model, "durationMs": round((time.monotonic() - start) * 1000),
            "status": status, "usage": usage or {}, "error": error,
        }, ensure_ascii=False))

    async def execute(self, body: dict, trace_id: str):
        payload, scene = self.prepare(body)
        if not self.settings.deepseek_api_key:
            raise ModelFailure("model_not_configured", 503)

        scene_id = body["model"]
        breaker = _breaker_for(scene_id, self.settings)
        if not breaker.allow():
            self.log_result(scene_id, scene, trace_id, time.monotonic(), "failed", error="circuit_open")
            raise ModelFailure("circuit_open", 503)

        attempts = max(1, self.settings.model_retry_max_attempts)
        for attempt in range(attempts):
            try:
                result = await self._transact(payload, scene, scene_id, trace_id)
            except ModelFailure as error:
                # 只有"瞬时且还有名额"才重试；不可重试或最后一次失败都记入熔断并抛出。
                if not (_is_retryable(error.code) and attempt < attempts - 1):
                    breaker.record_failure(error.code)
                    raise
                delay = _retry_delay(attempt, self.settings)
                logger.info(
                    "AI_RETRY scene=%s attempt=%d/%d error=%s delayMs=%d",
                    scene_id, attempt + 1, attempts, error.code, int(delay * 1000),
                )
                await asyncio.sleep(delay)
                continue
            breaker.record_success()
            return result
        raise AssertionError("unreachable")  # 循环要么 return 要么 raise，兜底防静默

    async def _transact(self, payload: dict, scene, scene_id: str, trace_id: str):
        """单次 HTTP 事务，不含重试。

        流式请求一旦成功建立连接就返回生成器、退出重试层：正文已经（或即将）产出，
        不能再无痕重播；生成器内部的错误仍由 `stream` 自行降级。
        """
        start = time.monotonic()
        client = httpx.AsyncClient(timeout=httpx.Timeout(scene.timeout_seconds, connect=4))
        response = None
        try:
            request = client.build_request("POST", self.settings.deepseek_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.deepseek_api_key, "X-Trace-Id": trace_id}, json=payload)
            response = await client.send(request, stream=payload["stream"])
            response.raise_for_status()
            if payload["stream"]:
                return self.stream(response, client, scene_id, scene, trace_id, start)
            result = response.json()
            choice = result["choices"][0]
            message = choice.get("message") or {}
            # Tool requests are valid replies: Java executes them and sends the results back.
            if not (message.get("content") or "").strip() and not (scene.allow_tools and message.get("tool_calls")):
                raise ModelFailure("empty_response")
            if choice.get("finish_reason") == "length":
                raise ModelFailure("output_truncated")
            self.log_result(scene_id, scene, trace_id, start, "success", result.get("usage"))
            return result
        except httpx.TimeoutException:
            self.log_result(scene_id, scene, trace_id, start, "failed", error="upstream_timeout")
            raise ModelFailure("upstream_timeout", 504) from None
        except httpx.HTTPStatusError as exc:
            self.log_result(scene_id, scene, trace_id, start, "failed", error="upstream_http_" + str(exc.response.status_code))
            raise ModelFailure("upstream_http_" + str(exc.response.status_code)) from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            self.log_result(scene_id, scene, trace_id, start, "failed", error="invalid_upstream_response")
            raise ModelFailure("invalid_upstream_response") from None
        except ModelFailure as exc:
            self.log_result(scene_id, scene, trace_id, start, "failed", error=exc.code)
            raise
        finally:
            if response is None or not payload["stream"] or response.status_code != 200:
                if response is not None:
                    await response.aclose()
                await client.aclose()

    async def stream(self, response, client, scene_id, scene, trace_id, start):
        seen = False
        done = False
        usage = {}
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    if not seen:
                        raise ModelFailure("empty_response")
                    done = True
                    yield "data: [DONE]\n\n"
                    break
                chunk = json.loads(value)
                if chunk.get("error"):
                    raise ModelFailure("upstream_stream_error")
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    seen = seen or bool(delta.get("content") or delta.get("tool_calls"))
                    if choice.get("finish_reason") == "length":
                        raise ModelFailure("output_truncated")
                yield "data: " + value + "\n\n"
            if not done:
                raise ModelFailure("incomplete_stream")
            self.log_result(scene_id, scene, trace_id, start, "success", usage)
        except asyncio.CancelledError:
            self.log_result(scene_id, scene, trace_id, start, "cancelled", usage)
            raise
        except (httpx.HTTPError, ValueError, ModelFailure) as exc:
            code = exc.code if isinstance(exc, ModelFailure) else "stream_transport_error"
            self.log_result(scene_id, scene, trace_id, start, "failed", usage, code)
            yield "data: " + json.dumps({"error": {"message": "AI 服务调用失败", "type": code, "traceId": trace_id}}) + "\n\n"
        finally:
            await response.aclose()
            await client.aclose()
