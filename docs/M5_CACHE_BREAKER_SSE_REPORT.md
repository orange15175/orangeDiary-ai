# M5-2/3/4 报告：缓存与熔断、SSE 来源事件、端到端生成验收

日期：2026-11-17。承接 `M5_RERANK_REPORT.md`（rerank 接入），完成 M5 剩余三项。
所有结论都来自本机实测，命令与观察值一并记录。

---

## 一、M5-2 缓存与熔断

### 做了什么

**1. 检索结果缓存**（`app/rag/cache.py`）

- 键 = 规范化查询 + `top_k` + `min_score`；规范化只做去首尾空白、压缩连续空白、统一小写，**不做语义近似**——近似命中会把 A 问题的来源挂到 B 问题上。
- 容量 256 条、TTL 300s，LRU 淘汰；只缓存"规范化后完全相同"的查询。
- **同请求合并（single-flight）**：同一查询并发进来，只有一个真正检索，其余等待并复用结果。rerank 单次约 1.5s，用户连点两次发送是最常见的重复来源。
- 命中必须显式暴露：`RetrievalOutcome.cache` 取 `miss / hit / shared / bypass`，并写进响应的 `cache` 字段与 `degraded.cache`。命中时 `timings` 换成本次真实耗时（`total_ms` ≈ 0.02ms），原始耗时放在 `origin_total_ms`，**不拿上次的耗时冒充本次性能**。
- 评估与调参脚本强制 `cache_enabled=False`：否则 P50 会被算成 0.2ms。

**2. 熔断器**（`app/rag/circuit.py`）

- 状态机 closed → open（连续失败达阈值）→ half_open（冷却结束只放行 1 个探针）→ 成功回 closed / 失败立即重熔。
- 两个熔断器：`vector`（Embedding + Qdrant 合为一个，对调用方都是"检索不到"）与 `rerank`。
- 向量通道熔断期内抛 `CircuitOpenError` → 接口返回 **503 `circuit_open`**，而不是等 8s 超时再报 502。
- rerank 熔断期内跳过重排、退回 M4 的两级证据门槛，`degraded.rerank = "circuit_open"`（与 `failed` 区分，免得运维以为模型加载失败）。
- 刻意不做单次重试：检索是回答的前置步骤，一次重试就要再等一个超时，总预算必然超过 Java 的 6s 上限。

**3. 观测**：新增 `GET /internal/rag/v1/stats`（Bearer 鉴权），返回缓存命中率、条目数、淘汰数、两个熔断器的状态与最近错误。

配置项（`.env.example` 已补）：`RAG_CACHE_ENABLED`、`RAG_CACHE_TTL_SECONDS`、`RAG_CACHE_MAX_ENTRIES`、`RAG_BREAKER_FAILURE_THRESHOLD`、`RAG_BREAKER_RESET_SECONDS`。

### 实测

缓存（同一问题连问三次，`/internal/rag/v1/search`）：

| 次序 | cache | 检索耗时 |
|---|---|---|
| 第 1 次 | miss | 6951ms（含 Ollama 冷加载） |
| 第 2 次 | hit | 0.02ms |
| 第 3 次 | hit | 0.02ms |

稳态下 rerank 走满的问题：1807ms（miss）→ 0.02ms（hit）。

熔断（把 `OLLAMA_BASE_URL` 指向必然拒绝连接的端口，阈值 2、冷却 30s）：

| 次序 | 结果 | 耗时 |
|---|---|---|
| 第 1 次 | 502 `upstream_unavailable` | 2.08s |
| 第 2 次 | 502 `upstream_unavailable` | 2.04s |
| 第 3 次 | **503 `circuit_open`** | **0.007s** |
| 冷却 31s 后 | `/stats` 显示 half_open，探针请求 2.05s 再失败 | 熔断重新打开（trips=2） |

半开恢复路径也验证过：冷却结束后放行一个探针，探针失败立即重熔，不会让并发流量同时打回下游。

### 踩到的坑

`CircuitBreaker.snapshot()` 持锁后又调用 `state` 属性，而 `state` 会再取同一把 `threading.Lock` —— **非重入锁直接死锁**。单测跑起来整进程挂住、连测试名都不打印，排查时先怀疑了测试框架。修法是把"有效状态"抽成 `_effective_state_locked()`，`state` 与 `snapshot` 各自决定是否取锁。

---

## 二、M5-3 SSE 来源事件

流式通道此前明确"不注入知识"（`KnowledgeHolder` 传 null），理由是没有回传来源的协议——有依据但不可追溯。现在补齐：

- `chatStream` 在**开流前**完成检索，命中来源时先发一条 `sources` 事件（JSON 字符串），再逐字输出 `message`，最后 `done`。
- 没有来源就不发事件（检索失败、知识库无证据都算），前端据此区分"没有引用"与"引用为空"，不渲染空卡片。
- 检索在开流前完成，最坏多等一次检索时间（稳态 2.0s），换来源可追溯。

### 附带修掉的一个真 Bug：SSE 中文全是问号

写测试时断言中文标题，输出是 `"title":"??????"`。逐层排查确认：

- `SseEmitter.send(event().data(text))` 交给消息转换器时 **media type 为 null**，`StringHttpMessageConverter` 取响应头里的 `text/event-stream`（不带 charset），回落到默认字符集 **ISO-8859-1**；
- 在 `produces` 上写 `charset=UTF-8` 无效（编码看的是响应头，实测 CT 仍是 `text/event-stream`）；
- 给单条事件 `data(x, MediaType)` 也无效（`addDefaultHeaders` 不会覆盖已有 Content-Type）；
- 唯一有效的层是转换器本身：`WebConfig.extendMessageConverters` 把 `StringHttpMessageConverter` 换成 UTF-8 实例。

这条路径此前从未被真实使用（前端只调非流式的 `/api/ai/chat`），所以中文 token 被写成问号的缺陷一直没暴露。修复后实测流式响应：

```
event:sources
data:[{"rank":1,"documentId":"who-wpro-nudge-2023","title":"助推更健康的饮食行为：减少中国居民盐摄入的创新案例研究",...}]
...
event:message
data:还要
event:message
data:更
event:done
data:2e6cda21-...
```

中文完好，`grep '????'` 计数为 0。

---

## 三、M5-4 端到端生成验收（此前最大空白）

环境：MySQL/Redis/MinIO 本地、Qdrant 6333、Ollama 11434、Python 8000（rerank=local）、Java 8080、前端 5173。
用注册的验收账号走真实登录态，`POST /api/ai/chat` 与 `/api/ai/chat/stream`。

### 验收过程中暴露的两个真问题（都已修）

**1. 意图路由把"要查指南"的问题挡在知识库外**

`减重期间主食应该吃多少克` 被意图层判成 `weight_progress`，而 `resolveNeeds` 的逻辑是"意图命中就返回、不再走关键词兜底"，于是只查了用户档案，**完全没触发检索**。模型只能凭记忆给出"每公斤体重 2~3 克"，无法追溯到指南原文——这正是 M5-1 花力气把 q19 的排序修好的那道题，却在路由层被绕过去了。

修法：意图命中后仍做一次循证诉求检查（`上限 / 推荐量 / 应该吃多少 / 膳食指南 …`），命中就补一个 `knowledge`。仅对带明确循证词的问题生效，"今天吃了什么"这类不会多查一次。

**2. Ollama 空闲 5 分钟卸载模型，首问超过 Java 的 6s 超时**

验收第一次请求 `sources: 0`，Java 日志是 `[AI知识检索] 调用失败, error=RestClientException`。查 Python 侧同一条查询：`origin_total_ms = 7624.6`——Ollama 把 bge-m3 卸载了，重载 5~6s，加上 rerank 1.7s 直接顶破 Java 6s 读超时，来源被整条丢掉。

修法两层：
- `OllamaEmbedder` 每次请求带 `keep_alive`（默认 `30m`，`OLLAMA_KEEP_ALIVE` 可配，空值则不发送）；
- 启动预热从"只加载索引"扩展到"真发一次向量请求"（`RetrievalService.prime()`），把模型加载挪到启动阶段。

### 验收结果

| 问题 | 意图 | 来源数 | 回答依据 |
|---|---|---|---|
| 减重期间主食应该吃多少克 | weight_progress + knowledge | **4** | 引《成人肥胖食养指南（2024年版）》：谷薯类 150~300 g/天、全谷物杂豆 50~150 g、薯类 50~100 g |
| 用代糖能帮助减肥吗 | health_advice | 1 | 引《高血糖症营养和运动指导原则（2024年版）》"少喝代糖饮料" |
| 今天天气怎么样 | 无 | 0 | 明确说无法查天气，未编造 |
| 今天吃了什么 | diet_detail | 0 | 说明没有饮食记录，未触发检索 |

延迟（同一条问题，K=4）：

| 场景 | 端到端 | 其中检索 |
|---|---|---|
| 首次（cache miss） | 4829ms | 2033ms |
| 再问一次（cache hit） | 2741ms | 0ms（日志 `cache=hit, tookMs=0`） |

流式通道：`event:sources` → 多条 `event:message`（中文完好）→ `event:done`，HTTP 200、`Content-Type: text/event-stream`。

---

## 四、测试

| 套件 | 结果 |
|---|---|
| Python unittest | **126 项通过**（M5-1 是 93；新增 cache 9、circuit 9、embedding 5、rag_service 缓存/熔断 10） |
| Java mvn test | **99 项通过 / 跳过 6**（M5-1 是 93；新增 AiStreamSourcesTest 5、PythonRagClient 1） |

新增测试覆盖：TTL 过期、LRU 淘汰、单飞（并发同查询只跑一次）、空结果也缓存、`top_k` 进键、熔断阈值/冷却/半开/探针失败重熔、rerank 熔断降级、向量熔断快速失败、`keep_alive` 载荷、sources 事件的发送与"无证据不发"、意图命中后仍补检索。

---

## 五、改动文件

Python（`orangeDiary-ai`）：

- 新增 `app/rag/cache.py`、`app/rag/circuit.py`、`tests/test_cache.py`、`tests/test_circuit.py`、`tests/test_embedding.py`
- 改 `app/rag/service.py`（缓存包装、两个熔断器、`prime()`、`stats()`）、`app/rag/embedding/ollama.py`（keep_alive）、`app/api/rag.py`（503 circuit_open、`/stats`、预热带 prime）、`app/config.py`（新配置项）、`.env.example`、`scripts/evaluate.py`、`scripts/tune_rerank.py`（关缓存）、`tests/test_rag_service.py`

Java（`orangeDiary`）：

- 改 `AiServiceImpl`（流式注入知识 + `sources` 事件、意图命中后补知识检索）、`AiController`（`produces` 带 charset，附注说明它并非编码生效点）、`WebConfig`（String 转换器改 UTF-8，**真正的修复**）、`PythonRagClient`（解析 `cache`）、`PythonRagClientTest`
- 新增 `AiStreamSourcesTest`

---

## 六、回滚方式

| 改动 | 回滚 |
|---|---|
| 缓存 | `RAG_CACHE_ENABLED=false`（无需改代码） |
| 熔断 | `RAG_BREAKER_FAILURE_THRESHOLD` 调大 或 `RAG_BREAKER_RESET_SECONDS=0` |
| 模型保活 | `OLLAMA_KEEP_ALIVE=`（空值即不发送该参数） |
| SSE 来源事件 | 把 `chatStream` 里的 `KnowledgeHolder` 改回 `null` 即恢复"流式不注入知识" |
| UTF-8 转换器 | 移除 `WebConfig.extendMessageConverters`（但流式中文会重新变成问号） |

---

## 七、未解决 / 需要你知道的

1. **我把 8080 的旧进程换掉了**：本地 `orangeDiary/.env` 里 `FEATURES_AI_CHAT_ENABLED=false`，AI 接口会整体不注册（表现为 500 `No static resource api/ai/chat`）。我以 `--features.ai-chat.enabled=true` 启动新包，现在 8080 跑的是这个实例（日志在 `.tmp-acceptance/app.log`）。若你希望恢复关闭状态，改回该开关即可。
2. **验收账号**：为走登录态注册了 `rag-acceptance@example.com`（用户 id 58，昵称「RAG验收」），在本地 MySQL 里。需要的话可自行删除。
3. rerank 阈值 0.75 仍是在同一批 48 题上扫出来的，过拟合风险未消除，等 M6 扩题后用留出集复测。
4. 前端仍只调非流式 `/api/ai/chat`，SSE 端点目前没有客户端消费；`sources` 事件协议已就绪，接前端时直接用。
5. 本轮改动尚未提交 git（M0–M5 都还在工作区），需要我按里程碑拆 commit 的话告诉我。
