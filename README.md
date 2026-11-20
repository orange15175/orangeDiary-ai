# OrangeDiary 后端（Python）

Java 版本（`orangeDiary`）的全部业务端点已迁移到这里：认证、饮食/运动/体重记录、日报、
社区、通知、管理后台，加上知识库检索（Qdrant + BM25 + rerank）与 AI 问答。
终态是单个 Python 进程承载业务，模型凭据只在本进程里。

## 本机启动

先确认这几个依赖在跑（`docker ps` 看容器状态。Docker Desktop 会把闲置容器置为 Paused，
暂停状态下端口仍在监听但连接会读超时，容易误判成"服务挂了"）：

| 依赖 | 默认地址 | 说明 |
| --- | --- | --- |
| MySQL 8 | `127.0.0.1:3306` | 库名 `calorie_tracker`，与 Java 版同一个库 |
| Redis 7 | `127.0.0.1:6379` | 登录会话；需要密码 |
| Qdrant | `127.0.0.1:6333` | 集合 `orange_diary_knowledge_v4` |
| MinIO | `127.0.0.1:9000` | 社区/头像图片 |
| Ollama | `127.0.0.1:11434` | `bge-m3`（1024 维）做 Embedding；没起也能启动，检索会降级 |

`.env` 需要这些键（`app/config.py` 会自动加载项目根的 `.env`，进程环境变量优先）：

```
DATABASE_URL=mysql+asyncmy://user:pass@127.0.0.1:3306/calorie_tracker
REDIS_URL=redis://:password@127.0.0.1:6379/0
JWT_SECRET=<与 Java 版一致，切流时前端 token 不用重登>
DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / MINIO_BUCKET
```

迁移只跑一次。业务账号通常没有 DDL 权限，建 `alembic_version` 要 root：

```sh
DATABASE_URL="mysql+asyncmy://root:pass@127.0.0.1:3306/calorie_tracker" python -m alembic upgrade head
python -m alembic current      # 应为 0010 (head)
```

起服务：

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 健康检查 `GET /health`：只报配置有没有到位。
- 就绪检查 `GET /ready`：真的连库并校验 `alembic_version == 0010`，返回 `{"business":"ready"}` 才算能接流。
- 接口文档 `GET /docs`。

启动会在后台线程预热 rerank 模型与索引（约 8s）。日志出现
`检索资源加载完成：168 片段，32 篇文档` 后再打第一个问题，否则首次请求必然超时。

## 测试

```sh
python -m pytest -q                    # 离线全量（542 项：SQLite / 假模型 / mock 上游）
python -m pytest -m live               # 打真实 DeepSeek，需要付费 key，默认跳过
```

集成用例连 `deploy/compose.test.yml` 起的 MySQL/Redis（3307/6380），不碰开发库。

## AI 问答要点

- 端点：`POST /api/ai/chat`（一次性返回）、`POST /api/ai/chat/stream`（SSE）。
- SSE 事件顺序固定：`knowledge` → `sources` → `message`×N → `done`（带 `conversationId`）；
  任何一步失败走 `error`，不会静默断流。
- 响应字段对齐 Java `AiChatResponse`：`reply` / `conversationId` / `action` /
  `recordPreview` / `exercisePreview` / `sources` / `knowledgeStatus`。未触发的可选字段
  Python 直接省略键，Java 序列化为 `null`——前端都是 falsy，行为一致。
- `knowledgeStatus` 五态：`ok`（有来源）、`no_evidence`（确实没资料）、`busy`（检索排队超时）、
  `unavailable`（检索服务挂了）、`skipped`（本轮没查）。前端按状态给不同文案，不可合并。
- 记录类提问要带克数/分钟数才出确认卡（`confirm_diet_record` / `confirm_exercise_record`），
  只说"两个鸡蛋"会反问克数——这条规则是照 Java 的正则口径复刻的。
- 配额：每日 50 次 + 每分钟 10 次，管理员免次数；Redis 不可用时放行（配额不是安全边界，
  社区写操作的限流才是，那部分 Redis 故障时 fail-closed）。
- 内部接口 `POST /internal/rag/v1/search` 用 `Authorization: Bearer <AI_SERVICE_TOKEN>`。

## 知识域与索引

索引里有两个逻辑域，靠 Qdrant payload 的 `knowledge_domain` 与各自的 BM25 文件隔离：

| 域 | 内容 | BM25 文件 | 调用方 |
| --- | --- | --- | --- |
| `nutrition` | WHO / 中国营养学会 / 国家卫健委文献 | `bm25/nutrition.<版本>.json` | AI 问答、内部检索接口 |
| `cooking` | 项目整理的家常菜谱与搭配经验 | `bm25/cooking.<版本>.json` | 聊天做菜分支、`/api/assistant/chef` |

重建索引（会同时写出合并索引与两个分域索引）：

```sh
python scripts/ingest.py --index-version knowledge-v4 \
  --collection orange_diary_knowledge_v4 --rebuild
```

菜谱在 `knowledge/sources/cooking/`，来源清单在 `knowledge/manifest.json`（`domain` 写错会被
清单校验直接拒绝，避免"两个域都查不到"这种静默故障）。菜谱是**项目整理的通用做法**，
`source_url` 为空、`needs_review=true`，不得在回答里说成权威来源。

## 数据库迁移

`alembic/versions/0001`–`0006` 全是 `CREATE TABLE IF NOT EXISTS`，对已有 Java 库执行不会改结构、
不会丢数据。改基线时同步改 `app/db/session.py` 的 `REQUIRED_SCHEMA_REVISION`，
否则新副本会带着"就绪"状态跑在不兼容的表结构上。
