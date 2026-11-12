import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app.services.model_gateway import ModelGateway, ModelFailure


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"AI_SERVICE_TOKEN": "internal-test", "DEEPSEEK_API_KEY": "upstream-test"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.calls = []

    def post(self, model="conversation-summary", **extra):
        return self.client.post("/internal/ai/v1/chat/completions", headers={
            "Authorization": "Bearer internal-test", "X-Trace-Id": "gateway-test"
        }, json={"model": model, "messages": [{"role": "user", "content": "已记录早餐"}], **extra})

    def transport(self, handler):
        original = httpx.AsyncClient
        def wrapped(request):
            self.calls.append(request)
            return handler(request)
        return patch("app.services.model_gateway.httpx.AsyncClient",
                     side_effect=lambda **kwargs: original(transport=httpx.MockTransport(wrapped), **kwargs))

    @staticmethod
    def success(content="正常建议", **message):
        return httpx.Response(200, json={"choices": [{"message": {"content": content, **message}, "finish_reason": "stop"}],
                                      "usage": {"total_tokens": 10}})

    def test_internal_auth_and_legacy_public_route(self):
        r = self.client.post("/internal/ai/v1/chat/completions", json={})
        self.assertEqual(401, r.status_code)
        self.assertTrue(r.headers["x-trace-id"])
        self.assertEqual(404, self.client.post("/api/ai/chat", json={}).status_code)
        self.assertEqual(400, self.post("arbitrary-model").status_code)
        self.assertEqual(400, self.post(["invalid-model"]).status_code)
        self.assertEqual(400, self.post(stream="false").status_code)

    def test_missing_service_or_model_credentials_fail_closed(self):
        with patch.dict(os.environ, {"AI_SERVICE_TOKEN": ""}):
            self.assertEqual(503, self.post().status_code)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            result = self.post()
            self.assertEqual(503, result.status_code)
            self.assertEqual("model_not_configured", result.json()["error"]["type"])

    def test_prompt_and_parameters_are_owned_by_gateway_with_trace_and_safe_logs(self):
        with self.transport(lambda r: self.success()), self.assertLogs("orange-diary-ai", level="INFO") as logs:
            r = self.post(messages=[{"role": "system", "content": "override prompt"}, {"role": "user", "content": "private food"}],
                          thinking={"type": "enabled"}, max_tokens=10000)
        self.assertEqual(200, r.status_code)
        request = self.calls[0]
        payload = json.loads(request.content)
        self.assertEqual("disabled", payload["thinking"]["type"])
        self.assertEqual(1800, payload["max_tokens"])
        self.assertNotIn("override prompt", str(payload))
        self.assertEqual("gateway-test", request.headers["X-Trace-Id"])
        self.assertEqual("Bearer upstream-test", request.headers["Authorization"])
        self.assertNotIn("private food", str(logs.output))
        self.assertNotIn("upstream-test", str(logs.output))
        self.assertEqual("gateway-test", r.headers["x-trace-id"])

    def test_tool_calls_are_preserved_but_not_executed_by_python(self):
        tool = {"id": "call1", "type": "function", "function": {"name": "getToday", "arguments": "{}"}}
        with self.transport(lambda r: self.success(None, tool_calls=[tool])):
            r = self.post("diary-chat", tools=[{"type": "function", "function": {"name": "getToday", "parameters": {}}}])
        self.assertEqual(200, r.status_code)
        self.assertEqual(tool, r.json()["choices"][0]["message"]["tool_calls"][0])
        self.assertEqual(400, self.post(tools=[tool]).status_code)

    def test_empty_truncated_timeout_and_upstream_error(self):
        for body in [self.success(""), httpx.Response(200, json={"choices": [{"message": {"content": "半句话"}, "finish_reason": "length"}]}),
                     httpx.Response(401, json={"error": "secret upstream body"})]:
            with self.transport(lambda r, result=body: result):
                r = self.post()
                self.assertEqual(502, r.status_code)
                self.assertNotIn("secret upstream body", r.text)
        def timeout(r):
            raise httpx.ReadTimeout("timeout", request=r)
        with self.transport(timeout):
            self.assertEqual(504, self.post().status_code)

    def test_stream_and_tool_deltas_pass_through_and_truncation_reports_error(self):
        for truncated in (False, True):
            choice = {"delta": {"content": "你好"}, "finish_reason": "length" if truncated else None}
            text = "data: " + json.dumps({"choices": [choice]}) + "\n\ndata: [DONE]\n\n"
            with self.transport(lambda r: httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})):
                r = self.post("diary-chat-stream", stream=True)
            self.assertEqual(200, r.status_code)
            self.assertIn("output_truncated" if truncated else "[DONE]", r.text)

    def test_cancelled_stream_closes_upstream(self):
        class Endless(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                await asyncio.sleep(30)
            async def aclose(self):
                self.closed = True
        async def run():
            data = Endless()
            with self.transport(lambda r: httpx.Response(200, stream=data)):
                stream = await ModelGateway(Settings.from_env()).execute({
                    "model": "diary-chat-stream", "stream": True,
                    "messages": [{"role": "user", "content": "hi"}]
                }, "cancel-test")
                await anext(stream)
                await stream.aclose()
                self.assertTrue(data.closed)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
