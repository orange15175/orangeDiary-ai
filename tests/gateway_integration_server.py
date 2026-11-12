"""Run a real Python gateway with a deterministic local upstream for Java integration tests."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["thinking"]["type"] == "disabled"
        assert body["messages"][0]["role"] == "system"
        assert self.headers["X-Trace-Id"] == "integration-test"
        assert self.headers["Authorization"] == "Bearer fake-model-key"
        has_tool_result = any(m["role"] == "tool" for m in body["messages"])
        tools = body.get("tools") or []
        content = str(next((m for m in reversed(body["messages"]) if m["role"] == "user"), {}))
        if "acceptance-failure" in content:
            self.send_response(503)
            self.end_headers()
            return
        name = tools[0]["function"]["name"] if tools else None
        tool = {"id": "call1", "type": "function", "function": {"name": name, "arguments": "{}"}}
        wants_tool = bool(tools) and not has_tool_result and "acceptance-storage" not in content
        if body.get("stream"):
            delta = {"tool_calls": [{"index": 0, **tool}]} if wants_tool else {"content": "网关流式成功"}
            chunk = {"id": "test", "object": "chat.completion.chunk", "model": body["model"],
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            final = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if wants_tool else "stop"}]}
            data = ("data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(final) + "\n\ndata: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [tool]} if wants_tool else {"role": "assistant", "content": "网关调用成功"}
            data = json.dumps({"id": "test", "object": "chat.completion", "model": body["model"],
                               "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if wants_tool else "stop"}]}).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 18766), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update(AI_SERVICE_TOKEN="integration-service", DEEPSEEK_API_KEY="fake-model-key",
                      DEEPSEEK_BASE_URL="http://127.0.0.1:18766", DEEPSEEK_MODEL="test-model")
    uvicorn.run("app.main:app", host="127.0.0.1", port=18765, log_level="warning")
