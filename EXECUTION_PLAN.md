# OrangeDiary RAG 执行计划（定稿）

编制日期：2026-11-17。本文是 `AI_RAG_DEVELOPMENT_PLAN.md` 的落地执行版，基于当日对三个项目的实际盘点编制。里程碑内容以原计划为准，本文记录已确认的决策、现状基线和执行顺序。

## 0. 已确认的决策（2026-11-17 与用户确认）

| 决策点 | 结论 |
| --- | --- |
| Embedding / Rerank | **Ollama 本地 bge-m3**（1024 维）。Rerank 首期若无本地模型，用 RRF 分数 + 证据过滤顶住并标注 degraded，不假装过阈值 |
| 知识文献来源 | **两者结合**：用户提供已有资料 + 补充收集权威公开资料（WHO、中国居民膳食指南等），统一进来源清单；首期 10–20 篇 |
| 凭据处理 | **只清理文件，暂不轮换**：`.env.example` 与 `application.yml` 改为占位符，本地真实值放 `.env`（不入库）。已知风险：旧值仍存在于 git 历史，轮换由用户另行安排 |
| 执行节奏 | **逐里程碑验收**：每个里程碑完成后提交验收清单（改动、命令、测试结果、未验证项、回滚方式），用户确认后再进下一个 |
| 压测条件（M6/M7 用） | **本机实测**：i7-12700H / 14 核 20 线程 / 15.8 GB；rerank 保持 CPU 版 torch；Ollama、Qdrant、MySQL、Redis 同机运行。结论只对本机有效，报告须注明并发产生方式 |
| 部署实例数 | **单实例**：一个 Java 进程（8080）+ 一个 Python（8000）。限流用进程内信号量 + Redis 计数，**不做跨实例租约**；文档须写明"扩到多实例即失效"的前提 |
| 管理端形态 | **做页面**：按 traceId 查单次问答、反馈列表、运行状态；复用现有管理员角色，个人健康内容默认脱敏 |

## 1. 现状基线（2026-11-17 实测）

- Git：Python `codex/final-version` / `e1eac51`；Java `codex/graduation` / `50774f7`；前端 `codex/graduation` / `0feef09`。
- Qdrant 本地 6333 运行中；旧集合 `orange_diary_knowledge`（72 点 / 1024 维 / Cosine）与 `orange_diary_knowledge_smoke` 为旧 Java 版遗留，**保留不动**，新索引用版本化集合名。
- Ollama 11434 当前未运行，M1 前需启动并拉取 bge-m3。
- MySQL 3306 / Redis 6379 / MinIO 9000 均在监听；8080 与 5173 有服务（Java / 前端可能在跑）；8000 空闲。
- Python `.venv` 存在（Python 3.11.5），依赖完整性待 M0 验证；项目内**无 RAG 代码**，自 `app/` 现有结构扩展。
- 计划所述 `src/main/resources/rag` 在迁移后的 Java 项目中不存在，文献来源按上表决策执行。
- ⚠️ `.env.example` 含真实 AI_SERVICE_TOKEN、MySQL/Redis 密码、JWT_SECRET 并已进 git 历史；Java `application.yml` 的 token 有真实默认值。
- 约束提醒：DeepSeek 仅有对话 API，无 Embedding API；Embedding 走 Ollama。

## 2. 执行顺序

### M0 基线修复（当前里程碑）
1. 安全清理：`.env.example` 占位符化；`application.yml` 删真实默认值（改 `${AI_SERVICE_TOKEN:}` 形式）；创建本地 `.env` 并确认 `.gitignore`。
2. 启动验证：Python venv 依赖安装与 uvicorn 8000 健康检查；Java、前端起停验证；不遗留占用 5173 / 8000 / 8080 的临时服务。
3. 意图协议修复（Python + Java + 前端同步）：
   - 统一 `missing_slots` snake_case 协议，边界兼容旧字段；
   - 单位显式识别："2个鸡蛋"不得解析为 2 克，无可靠份量映射则追问；
   - `nutrition_metric` 与每日汇总指标枚举区分、中文别名归一化；
   - 规则与模型结果走同一校验（数值范围、有限值、类型）；
   - 补齐 Java 运动确认响应字段（项目匹配、时长、候选项）；
   - 缺关键参数先追问，不生成可保存的错误预览；
   - `PythonIntentClient` 超时、安全错误码、traceId、有限降级。
4. 回归：保留全部现有测试，为每个已修复问题新增回归用例；跑 Python unittest、Java mvn test、前端 build。
5. 交付：基线检查报告、配置说明、意图协议文档、错误修复清单。

### M1 知识入库管线
`knowledge/` 目录 + 来源清单（标题、机构、URL、日期、适用范围）→ Markdown/TXT 结构化切块（heading_path、content_hash、稳定 ID、chunker_version）→ Ollama bge-m3 批量 Embedding（启动时实测维度）→ Qdrant 版本化集合 `orange_diary_knowledge_v2`（名称待定）批量 upsert → BM25 中文分词持久化索引 → `scripts/ingest.py` 受控 CLI。重复导入幂等；离线构建与在线检索分离。

### M2 混合检索闭环
`POST /internal/rag/v1/search`（Bearer 鉴权、traceId）：向量 + BM25 并行召回 → chunk_id 去重 → RRF → Rerank（或降级策略）→ Evidence Gate → 上下文预算 → 返回证据、来源、耗时与 degraded 状态。Java 新增 RAG 客户端并映射 DTO（保持现有 reply/conversationId/action 字段兼容，新增可选 sources）；`health_advice` 意图接入检索；前端安全渲染来源卡片。

### M3 第一阶段验收
核心回归题集约 30 题（直接问答、同义、指代、组合、无答案、注入、个人数据隔离）→ 单向量 vs 混合召回对比（同语料同题同预算）→ 故障场景可观察性验证（Embedding/Qdrant/BM25/Rerank）→ 不退化验证（聊天、个人数据、确认链路）→ 第一阶段测试报告。

### M4–M7（第二阶段，M3 验收后细化）
文档版本与增量索引 / 缓存与费用 / 超时重试熔断与备用模型 / Redis 并发与排队 / 异步摘要与会话一致性 / Trace 管理端反馈 / 60–100 题评估集与受控压测。

### M6 / M7 细化（2026-11-17 确认）

**M6 评估体系与受控压测**（对应原计划 2.7 + 2.8）
1. 评估集 48 → 80 题：新增 32 题（食物份量/单位、多轮指代、个人数据+知识组合、跨用户与伪造参数、过时/冲突资料、注入变体），其中 **30 题划为留出集**，只用于最终报告，不参与任何参数调整。
2. 留出集复测 rerank 阈值 0.75（当前阈值在同一批 48 题上扫出，过拟合风险未消除）。若留出集上误拒上升，回退到 0.70 并记录取舍。
3. 四配置消融：单向量 / 混合 RRF / 混合+rerank / 混合+rerank+证据过滤（当前缺第 4 项独立口径）。
4. 受控压测：本机单实例，并发 1/5/10/20 各一轮，冷缓存与热缓存分开记录，输出 P50/P95、吞吐、首字延迟、错误与排队率；注明压测客户端与被测服务同机。
5. 报告：`docs/M6_EVALUATION_LOAD_REPORT.md`，含失败案例清单与硬件/依赖/语料版本。

**M7 稳定性与可观测（单实例版）**
- M7-1 并发与排队（缩水版）：进程内信号量 + Redis 计数（跨进程共享但不做租约续租）、用户级频率限制、容量不足返回 429 `busy`、取消/异常幂等释放。**显式记录"多实例需改租约方案"的风险**。
- M7-2 会话记忆与异步摘要：游标标记摘要覆盖范围、有界执行器异步生成、合并锁 + 版本比对防旧摘要覆盖、上下文硬预算、同会话串行或版本检查。
- M7-3 Trace 与管理端（页面）：traceId 贯穿 Java→Python，分段记录排队/召回/融合/rerank/生成耗时；后端 `/admin/rag/*` 接口 + 前端管理页（按 traceId 查详情、反馈列表、缓存与熔断状态）；赞踩关联 message_id。
- M7-4 增量索引补齐与二期总验收：文档更新/删除时清理旧版失效片段、任务状态机（pending/running/succeeded/failed）、失败片段重试、二期验收报告与实际修复记录。

**未纳入本轮（明确不做）**：跨实例分布式租约、备用模型自动切换（无可用备用模型时只报 unavailable）、RAGAS 等自动打分框架。

## 3. 通用纪律（每个里程碑适用）

- 每个里程碑独立 commit，交付：改了什么、涉及文件、命令、测试结果、未验证部分、回滚方式。
- 不 reset / clean / 批量删除；不动旧的 `java-code\agent` 目录；不动旧 Qdrant 集合。
- 测试用独立可回滚数据；真实模型调用仅在联调环节、需用户授权。
- 不提交密钥；`output/` 下用户产物只读。
- 前端只调 Java；Java 独享业务写入；Python 不建第二套会话历史。

## 4. 里程碑进度（滚动更新）

| 里程碑 | 状态 | 报告 |
|---|---|---|
| M0 基线修复 | 完成 | `docs/M0_BASELINE_REPORT.md` |
| M1 知识入库 | 完成（13 篇 / 144 片段） | `docs/M1_INGESTION_REPORT.md` |
| M2 混合检索 | 完成 | `docs/M2_RETRIEVAL_REPORT.md` |
| M3 一阶段验收 | 完成（48 题评估集） | `docs/M3_EVALUATION_REPORT.md` |
| M4 索引 v3 与门槛 | 完成（误答率 40% → 0%） | `docs/M4_INDEX_AND_THRESHOLD_REPORT.md` |
| M5-1 rerank | 完成（判定准确率 97.92% → 100%，P50 253ms → 1787ms） | `docs/M5_RERANK_REPORT.md` |
| M5-2 缓存与熔断 | 完成（缓存命中 1807ms → 0.02ms；熔断后 2.0s → 0.007s 快速失败） | `docs/M5_CACHE_BREAKER_SSE_REPORT.md` |
| M5-3 SSE 来源事件 | 完成（含修复 SSE 中文被编码成问号） | `docs/M5_CACHE_BREAKER_SSE_REPORT.md` |
| M5-4 端到端生成验收 | 完成（真实登录态返回 4 条来源；修掉意图路由漏检索、Ollama 卸载模型导致首问超时） | `docs/M5_CACHE_BREAKER_SSE_REPORT.md` |
