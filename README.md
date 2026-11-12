# OrangeDiary AI gateway

Python is the internal model gateway. Public clients call Java; Java owns authentication, business data, tools, RAG and persistent conversations. Python owns model credentials, scenario prompts and upstream transport.

Set `AI_SERVICE_TOKEN` to the same independent internal token configured in Java. Set `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` only in Python. Install `requirements.txt`, then run:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Environment variables must be set for the process; `.env.example` is not loaded automatically.

Internal endpoint: `POST /internal/ai/v1/chat/completions`, authenticated with the internal Bearer token. Supported model aliases: `diary-chat`, `diary-chat-stream`, `conversation-summary`, `diet-report`. Actual upstream model and parameters are selected in Python. Prompts are in `app/prompts/scenarios.py`.

The old public `/api/ai/chat` endpoint is removed. No Java-backend URL or MySQL credentials are required. Tool calls are passed back to Java for execution.

Run tests with `python -m unittest discover -s tests -p test_model_gateway.py -v`. For Java cross-service tests, run `python -m tests.gateway_integration_server`; this uses a mock upstream on local ports 18765/18766.

Full Chinese configuration and architecture notes: the Java project's `docs/ai-unification.md`.
