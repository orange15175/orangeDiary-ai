# 2026-11-18 缺陷修复与加固记录

本轮针对项目评审时发现的 20 条问题做集中修复，覆盖交付闭环、功能缺陷、运行期风险与工程卫生四类。
所有改动都跑过回归（Python 138 个用例、Java 112 个用例、前端生产构建），没有引入新的失败用例。

## 0. 结论先行

| 类别 | 修了什么 | 验证方式 |
|---|---|---|
| 交付闭环 | AI 链路进 compose（`--profile ai`）、补齐 13 号迁移、依赖锁版本 | `docker compose config` 通过；镜像未实际构建（Docker Desktop 当时未启动） |
| 功能缺陷 | trace 字段错位、长度上限三处不一致、历史读不回来、前端未接流式、RAG 状态未进响应 | Java 单测 + 前端构建 + 真实联调 |
| 运行期风险 | AI 配额（50/天 + 10/分）、trace 异步化、上下文封顶、会话记忆有界 | 新增 9 个单测 |
| 工程卫生 | 移除 87 个已跟踪的 pyc/IDE 配置、去 BOM、删死代码、修 8 行乱码注释、三仓 CI | 编译 + 全量测试 |

## 1. 交付级问题

### 1.1 AI 链路可一键启动（profile ai）

`docker-compose.yml` 原来只有 mysql/redis/minio/backend/nginx，AI 完全靠手工起 5 个进程。现在新增两个服务，挂在 `profile: ["ai"]` 下，默认不启动：

```bash
docker compose up -d                     # 业务底座，和以前一样
docker compose --profile ai up -d        # 加上 qdrant + ai（复用宿主机 Ollama）
```

要点：

- `ai` 服务通过 `host.docker.internal:11434` 用宿主机的 Ollama，避免把 1.2GB 的 bge-m3 塞进镜像；Linux 上由 `extra_hosts: host-gateway` 兜底。
- 索引与模型用只读卷挂载，改知识库后不用重建镜像。
- **没有给 backend 加 `depends_on: ai`**：Compose 不允许默认启动的服务依赖 profile 服务，加了会让不带 profile 的 `up` 直接报错。

### 1.2 迁移补位

`ai_trace_migration.sql` 之前不在 initdb 序列里，也没有 Flyway。新环境起来后 `ai_chat_trace` 不存在，`recordTrace` 的 try/catch 会把异常吞掉——管理端"问答追踪"永远是空表，且不报错，极难排查。已补为 `13-ai-trace.sql`。

### 1.3 仓库卫生

- Python：`git rm --cached` 移除 87 个已跟踪的 `.pyc` 与 `.idea/*`、`*.iml`；`.gitignore` 改为整个 `.idea/`。
- Java：删除仓库根目录分叉的 `dto/ai/`、`service/AiService.java`、`service/impl/AiServiceImpl.java`（88 行版本，与 `src` 里的 692 行版本不同）；`.gitignore` 补 `output/`（356MB，含 8 个数据库备份）、`outputs/`、`tmp/`、`__pycache__/`、`.test-evidence/`。

## 2. 功能缺陷

### 2.1 trace 的 rerankState 存的是缓存状态

原代码 `knowledge.rerankState = result.cache()`，把 `hit/miss/shared/bypass` 写进了"重排状态"列；同时 Java 只读 `degraded.reason`，而 Python 在 rerank **成功**时返回 `{"rerank":"local:bge-reranker-base","rerank_threshold":0.75}`，没有 `reason` 字段。

净效果：排序正常工作时 trace 的 `degraded` 恒为空、`rerankState` 显示缓存命中，"这题到底有没有走重排"完全看不出来。

修法（三处）：

1. `PythonRagClient.RagResult` 拆成 `rerank`（排序方式）与 `degraded`（完整降级说明，键值对拼平）；
2. `AiServiceImpl` 分别写进 `rerankState` / `degraded`，并截断到列宽（`varchar(60)` / `varchar(128)`）；
3. 管理页列名从"缓存状态"改为"排序方式"，对历史行（值仍是 hit/miss）显式标注"旧数据"。

### 2.2 消息长度上限三处不一致

| 位置 | 原值 |
|---|---|
| `AiChatRequest`（Java） | 4000 |
| `AiChatView.vue` | 8000 |
| `schemas/chat.py` | 8000 |

输入 4001–8000 字符时前端放行、Java 直接 400。已统一到 **4000**（8000 字符的 prompt 会显著推高 token 成本，没有理由放宽）。

### 2.3 历史只存在浏览器里

历史一直在 `ai_conversation_message` 表里，但 `AiConversationService` 没有读接口，`AiController` 也没有 GET —— 前端只能靠 `sessionStorage`，关掉标签页就白屏。另外 `AiChatView` 里 `const history = messages.slice(-16,-1)` 计算后并未发送（`chatWithAi` 只发 `{message}`），是一段有误导注释的死代码。

修法：

- 新增 `GET /api/ai/conversation/messages?limit=40`（`AiChatMessageView` 只回 role/content/createdAt，不回主键）；
- 前端 `onMounted` 在本地缓存为空时拉取服务端历史；删除那段死代码。

### 2.4 前端接入流式

SSE 通道（`/api/ai/chat/stream`）此前没有任何调用方。新增 `chatStream()`：用 `fetch` + `ReadableStream` 手工解析 SSE 帧（不能用 `EventSource`——它不能带 Authorization 头，JWT 只能进 query，等于写进访问日志）。

前端行为：

- 先插入占位气泡，收到第一段 token 就开始渲染（带闪烁光标）；
- `knowledge` 事件先于正文到达，用于提示"知识库不可用/排队已满"；
- `sources` 事件到达即渲染引用，不等回答结束；
- 提供"停止"按钮（AbortController）；
- **只有一字未出时才回落同步接口**，已经出字再重试会让用户看到两遍不同答案。

### 2.5 RAG 状态没有进 API
三种情况（服务不可用 / 排队满 / 无证据）原先只写进了给模型的 prompt，`AiChatResponse` 没有对应字段，界面无法区分。现在：

- 响应体新增 `knowledgeStatus`：`ok | no_evidence | busy | unavailable | skipped`；
- 流式通道在出字前发一条 `knowledge` 事件；
- 前端对 `unavailable` / `busy` 显示一行说明，而不是让用户以为知识库是空的。

### 2.6 中断时已生成的部分不落库（联调时发现）

第一次真实联调用 `head -c` 提前断开连接，随后查历史发现只有一条提问、没有回答——`onCompleteResponse` 没被触发，`appendMessage` 也就没执行。用户点"停止"或断网时都会这样，刷新后看起来像丢消息。

修法：抽出 `persistReply`（`AtomicBoolean` 保证只写一次），在 `cancel`（onCompletion/onTimeout/onError）里也调用一次，同时与写 token 的线程用 `synchronized (reply)` 互斥，避免读到半个字符。复测：断开后历史里出现了 27 字的半截回答，符合预期。

## 3. 运行期加固

### 3.1 AI 调用配额

`/api/ai/chat` 原来没有任何用户级限制，而 Python 侧的 10 次/60s 只管检索，off-topic 问题根本不走检索——等于可以无限刷 DeepSeek。

实现（复用社区限流已有的 Lua 脚本，抽成 `SlidingWindowLimiter`，避免两份脚本漂移）：

| 项 | 值 | 依据 |
|---|---|---|
| 日上限 | 50 | 正常用户 8~15 次/天；答辩日"彩排+演示+老师试"约 50 次 |
| 分钟窗口 | 10 | 正常对话不会一分钟连问 10 句，脚本一定会触发 |
| 管理员 | 免限 | 演示账号被自己的配额拦住是最难看的故障 |
| 失败 | 退还 | 模型超时返回 502 时用户没拿到回答，不该扣次数 |
| Redis 不可用 | **放行 + 告警** | 配额是防滥用不是安全边界；社区限流仍保持 fail-closed |

### 3.2 追踪写库移出请求线程

`recordTrace` 原来是同步 INSERT，挂在用户响应链路上。现在投递到单线程 + 有界队列（64）+ DiscardPolicy；TraceId 是 ThreadLocal，切线程时显式传递，否则落库的 traceId 会变成 `-`。

### 3.3 上下文封顶

`buildContext` 依赖摘要游标推进；一旦摘要连续失败（模型不可用），游标不动、消息越积越多，token 成本线性膨胀。新增双重上限：最多 24 条、合计 12000 字符，超出时优先保留最近消息。

### 3.4 会话记忆有界

`ChatMemoryProvider` 默认的 `InMemoryChatMemoryStore` 只增不删，每个用户一份、永不回收。改为 `LruChatMemoryStore`（容量 200，按访问顺序淘汰）——淘汰只丢短期窗口，持久历史仍在库里。

## 4. 工程卫生

- `requirements.txt`：8 个依赖全部 `==` 钉死（与实测通过的 3.13 环境一致），redis 标为可选。
- `Dockerfile`：3.11 → 3.13、非 root（uid 10001）、`HEALTHCHECK`、`COPY knowledge`、不写 pyc；新增 `.dockerignore` 排除 `.venv` 与 `knowledge/models`。
- 去掉 5 个 Python 文件的 UTF-8 BOM（`ast.parse` 会直接抛 `SyntaxError`）。
- 删除 217 行零引用死代码：`app/services/request_plan.py`、`semantic_parser.py`。
- 修正 `application.example.yml` 里 8 行被写坏的乱码注释（`# ????`）。
- 修正 `scripts/evaluate.py` 的默认索引版本：原来是 `knowledge-v2`，与服务端默认 `knowledge-v3` 不一致，直接跑脚本会拿旧索引评估。
- 三仓各加一个 GitHub Actions 工作流（Python 单测 + 语法检查、Maven 单测、前端构建）。Java 工作流跳过需要真实 MySQL 的上下文测试。

## 5. 实验补充：置信区间

见 `docs/M6_EVALUATION_LOAD_REPORT.md` 新增的 2.3 节。核心结论：

- **rerank 相对 RRF 的提升在留出集上不显著**（MRR 0.880 [0.759, 0.972] vs 0.861 [0.694, 1.000]，区间重叠）。
- **证据门槛的效应是决定性的**（误答率 0% → 100%，14/14 全收），不依赖样本量。
- 负例仅 14 题，误答率分辨率约 7 个百分点，小于这个量级的差异不应写成"改善"。

复现：`python scripts/evaluate.py --split holdout --rerank --index-version knowledge-v3 --collection orange_diary_knowledge_v3`，然后 `python scripts/bootstrap_ci.py --input knowledge/eval/holdout_ablation_v3.json --split holdout`。

## 6. 验证清单

```bash
# Python：138 个用例
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"

# Java：112 个用例（6 个跳过，均为需要外部依赖的验收测试）
./mvnw -o test

# 前端：生产构建
npm run build

# 编排：配置合法
docker compose config
```

### 6.1 真实联调（Python 8000 + Java 8081 + Qdrant + Ollama，同一台机器）

| 验证项 | 结果 |
|---|---|
| 流式事件顺序 | `knowledge` → `sources` → 多个 `message` → `done`，中文无乱码 |
| 知识状态 | "钠摄入" 返回 `ok` + 4 条来源；"晚饭怎么搭配" 返回 `no_evidence`；"我今天还能吃多少" 返回 `skipped` |
| 历史接口 | 新账号 `[]`；对话后返回 user/assistant 两条；**未登录访问 401** |
| 中断落库 | 提前断开后，历史里保留了 27 字的半截回答 |
| 配额计数 | Redis `ai:limit:chat:user:59:day:2026-11-18=5`（ttl≈8.3h）、`minute:...=2`（ttl≈41s） |
| 追踪字段 | `rerank=local:bge-reranker-base`、`degraded=rerank=local:bge-reranker-base; rerank_threshold=0.75`、`retrieval_ms=3482` |

未验证：`docker compose --profile ai build ai`。本机 Docker 的 registry 代理（127.0.0.1:7890）未运行，拉不到 `python:3.13-slim`，镜像构建失败在第一步，与 Dockerfile 内容无关。需要在能访问镜像仓库的环境复验一次。

## 7. 仍未做（明确边界）

1. **多实例配额仍需租约**：Redis 计数只有窗口 TTL，没有续租与失联回收；单实例够用，扩容前必须补。
2. **流式通道不写 trace**：非流式已覆盖，流式仍缺（补的时候要注意别在 SSE 完成后才写库）。
3. **trace 没有生成阶段细分**：只有 `retrievalMs` 与 `totalMs`，DeepSeek 首字延迟未单独埋点。
4. **429 不落 trace**：配额与容量拒绝发生在业务之前，目前只有日志。
5. **语料规模**：13 篇 / 144 片段，MRR 数字不可外推到上百篇语料；阈值需要重扫。
6. **前端无单测**：目前只有构建作为门禁。
