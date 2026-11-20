# 实施状态

## 最新检查：统一聊天做菜 + 菜谱知识域 + 多轮承接（2026-11-20 00:10 更新）

当前源码已有 cooking 意图、CookingService、统一聊天场景、前端示例，以及**独立的烹饪知识域**
（Qdrant payload `knowledge_domain` + 分域 BM25 + 18 道菜谱语料）。最近一次全量回归：
**562 passed, 7 skipped**（`.venv\Scripts\python.exe -m pytest tests -q`）。

已完成修复（相对 22:14 那份验收记录）：

- **P1-0 全量回归失败**：测试替身 `FakeStore.search` 补上 `domain` 参数，与 `QdrantVectorStore` 同签名。23 failed → 0 failed。
- **P1-2 流式复合消息吞掉做菜**：`ai_service.chat_stream` 在预览卡存在时不再直接收尾；含 cooking 时先发记录卡再继续生成做菜回答。回归用例 `test_stream_composite_keeps_cooking_after_record_card`。
- **P1-4 运动时态误判**：补未来时排除（准备/打算/计划/明天…）与完成式识别（跑了/跑完了/刚跑完），单字动词 `跑/走` 还原为 `跑步/走路`。回归用例 `ExerciseTenseTests`。
- **P1-1 cooking 知识域没有数据**（链路部分已修）：payload 增 `knowledge_domain` / `recipe_key`；
  `bm25/` 产出 `nutrition.knowledge-v4.json`（144 片段）与 `cooking.knowledge-v4.json`（24 片段）；
  集合 `orange_diary_knowledge_v4` 共 168 片段；烹饪域语料 18 道项目整理菜谱 + 1 篇搭配经验。
- **P1-3 短追问分类接上会话历史**：`classify_message(settings, message, trace_id, history=...)`。
  语义模型能看到最近几轮对话，"不放辣""没有生抽了"这类省略句不再被当成闲聊；规则命中时行为不变。
- **做菜追问的检索补全**：追问句本身不含菜名时先用历史补成独立问句再检索（`cooking-rewrite` 场景）；
  补全零命中时按"原句 → 本句已确认的菜名"依次重试；菜名来自历史时不做同菜过滤
  （否则会把《食材搭配与替换》当成"别的菜"删光）。
- **份量映射直出记录卡**：`food_serving` 已有「1 个鸡蛋 = 50g」「1 碗米饭 = 150g」等 6 条映射，
  现在"吃了两个鸡蛋/一碗米饭"直接出 `confirm_diet_record` 卡，不再反问克数；没有映射仍追问，绝不猜。
- **中文时长换算**："跑了半小时""游泳一个半小时""走路一小时"按 30/90/60 分钟入卡，不再反问分钟。


仍然未完成（不要当作已交付）：

- **正式食谱只有 18 道**：结构化食谱仍是 10 条 `sample`，距 60/100 的验收门槛未达；吃到"没有收录"是真实覆盖不足。
- **P2 系列**：无资料策略与提示词不一致；busy/unavailable/no_evidence 三种状态未区分；`/api/assistant/chef` 未接配额；`scale_servings` 不缩放 `quantity/unit`；npm audit 2 项 high 未处理。

- 详细问题清单：`UNIFIED_COOKING_ACCEPTANCE_2026-11-19.md`（该文 P1-0/P1-2/P1-4 已过期，见上面三条）。
- 未做：正式食谱 60/100、图片与价格 Provider、`edible_ratio` 换算、`ChefService` 收敛、I5 全量用例、未获用户验收。
- 已知局限：改写模型偶尔会把上文语境拼进检索句（如"宫保鸡丁不放辣版怎么做"），靠"原句 → 菜名"两档重试兜底，
  不是每次都能命中；命不中时按"资料里没有"如实回答。

> 最新设计口径（2026-11-19）：做菜能力接入现有统一聊天，新增 cooking 意图和菜谱知识域，复用同一会话来源。以 `AI_CHAT_COOKING_IMPROVEMENT_PLAN.md` 为实施基线；下面提及的旧独立厨师方案已被更新。

> 历史记录说明：下面“最新范围：AI 做菜助手”一节及更早段落写于实施之前（当时只交付设计文档），不代表当前代码状态。

## 历史范围：AI 做菜助手（2026-11-19，实施前记录）

用户已取消本轮的计划/库存/采购/调整/复盘目标，收敛为“说菜名教做菜、说食材推荐菜，并支持连续追问”。

- 当前审查：`CHEF_CODE_REVIEW_2026-11-19.md`；开发基线：`CHEF_ASSISTANT_DEVELOPMENT_PLAN.md`。下文旧迁移及 R3 状态是历史模块记录，不能直接当作新做菜需求完成证据。
- 隔离复现确认整句检索失配、无关候选回退、原始数量未缩放；多轮会话等缺口详见审查。
- 本轮相关服务测试 14 passed；安全/配额测试 16 passed；前端构建通过。未调用真实模型、未做用户验收。
- 官方 npm audit 报告 2 项 high 通告，尚未修复；依赖运行路径与适用边界见审查第 5 节。

> 每个开发会话结束前更新本文件。只写"已完成/未完成 + 验证命令 + 实测输出"，
> 不写"预计完成"。用户验收与工程自测分开记录，未经用户回复不得标为已验收。

## 完成范围澄清（2026-11-19）

- **70 / 70 仅表示旧 Java 业务端点已迁移到 Python，不表示整个智能饮食助手完成或已通过用户验收。**
- R3 / M3–M7 当前已有数据模型、迁移、服务、API和页面；完整交付尚未完成。2026-11-19 代码审计发现规划、库存、确认交互等错误，详见 `R3_CODE_AUDIT_2026-11-19.md`。此前“当前无对应模块”的描述已过时。
- 下文沿用的“切流 M3”和新版设计“厨师 M3”属于不同阶段命名，不能互相当作完成证据。

## 当前阶段

- 阶段：**M0 完成、M1 完成、M2 完成（70 / 70 个端点全部迁移）**
- 端点覆盖率：**70 / 70（100%）**，由 `scripts/migration_matrix.py` 实测生成
- 本地首启验证：**已通过（2026-11-18，真实 MySQL 8 + Redis 7 + Qdrant + Ollama + DeepSeek）**，见下节
- 下一阶段：**M3 切流差分**（Java 仍在线，双写对拍；未开始）
- 迁移台账：`docs/MIGRATION_MATRIX.md`（随代码重跑，不手改）
- schema 版本：`REQUIRED_SCHEMA_REVISION = "0010"`（`/ready` 用它判定；0010 给来源快照与追踪表补 `knowledge_domain`）
- 索引版本：`knowledge-v4` / 集合 `orange_diary_knowledge_v4`（168 片段：nutrition 144 + cooking 24）

## M0 基线（已完成）

| 项目 | 产出 | 状态 |
|---|---|---|
| 接口矩阵 | `docs/MIGRATION_MATRIX.md`，70 个端点 / 25 张表（含 24 个 entity） | 已生成，可重跑 |
| 表清单 | SQL 25 张、entity 标注 24 张，差异已在矩阵中列出 | 已核对 |
| 业务数据 | 未读取、未修改、未清空 | 符合要求 |

## M1 认证与地基（已完成）

| 模块 | 文件 | 说明 |
|---|---|---|
| 配置 | `app/config.py` | `database_url` / `redis_url` / `jwt_*` / `auth_session_namespace` |
| 错误外壳 | `app/core/errors.py` | `code=0` 成功；业务失败 HTTP 状态码与 code 同值 |
| trace | `app/core/trace.py` | contextvar + `X-Request-Id` 透传/回写 |
| 密码与令牌 | `app/core/security.py` | BCrypt 兼容 `$2a$`；JWT 复刻 jjwt 的 Base64→Base64URL 解码 |
| ORM | `app/models/user.py`、`avatar.py` | `user`/`user_profile`/`user_goal`/`avatar_upload` |
| 会话 | `app/db/session.py` | async engine + `/ready` 双条件判定 |
| 迁移 | `alembic/versions/0001_legacy_baseline.py`、`0002_user_domain.py` | 手写基线；存量库 `alembic stamp 0001` 后 `upgrade head` |
| 认证 | `app/services/auth_service.py`、`auth_session.py`、`app/api/auth.py` | 注册/登录/刷新/登出；refresh 一次性轮换 + 复用即销毁会话 |
| 鉴权依赖 | `app/dependencies.py` | 复刻 `JwtAuthenticationInterceptor` 的四道判定；`require_admin` |
| 用户中心 | `app/services/user_service.py`、`user_view.py`、`app/api/users.py` | `/me`、`/validate`、`/count`、`PUT /profile`、`POST /goals` |

## M2 旧业务迁移（已完成）

| 模块 | 端点 | 文件 | 状态 |
|---|---|---|---|
| 鉴权中间件 | `/api/**`（除 `/api/auth/**`） | `app/dependencies.py` | 已迁移 |
| 用户中心 | 5/5 | `app/api/users.py` | 已迁移 |
| 食物库 | 4/4 | `app/api/foods.py`、`services/food_service.py`、`food_category.py` | 已迁移 |
| 饮食记录 | 3/3 | `app/api/diet.py`、`services/diet_service.py`、`idempotency.py`、`health_summary.py` | 已迁移 |
| 运动库与记录 | 7/7 | `app/api/exercise.py`、`services/exercise_service.py` | 已迁移 |
| 体重记录 | 5/5 | `app/api/weight.py`、`services/weight_service.py` | 已迁移 |
| 每日汇总 | 1/1 | `app/api/summary.py`、`services/summary_service.py` | 已迁移 |
| 登录足迹 | 2/2 | `app/api/checkin.py`、`services/checkin_service.py` | 已迁移 |
| 站内消息 | 3/3 | `app/api/notifications.py`、`services/notification_service.py` | 已迁移 |
| 社区帖子 | 11/11 | `app/api/posts.py`、`services/post_service.py` | 已迁移 |
| 社区举报 | 2/2 | `app/api/community_reports.py`、`services/community_report_service.py` | 已迁移 |
| 社区管理 | 8/8 | `app/api/posts.py`（`/api/admin/posts`）、`community_reports.py`、`services/moderation_service.py` | 已迁移 |
| 管理流水 | 2/2 | `app/api/admin_logs.py`、`services/moderation_service.py` | 已迁移 |
| 文件与头像 | 4/4 | `app/api/images.py`、`avatars.py`、`services/image_storage.py`、`avatar_service.py` | 已迁移（MinIO） |
| 限流基础设施 | — | `app/core/limiter.py`、`core/redis_client.py`、`services/community_limit.py` | 已迁移 |
| 报告 PDF | 1/1 | `app/api/reports.py`、`app/reports/*`、`app/reports/assets/*` | 已迁移 |
| AI 管理端 | 3/3 | `app/api/admin_ai.py`、`services/ai_trace_service.py` | 已迁移 |
| AI 会话 | 3/3 | `app/api/ai.py`、`services/ai_service.py`、`ai_conversation.py`、`ai_tools.py`、`ai_quota.py`、`knowledge_client.py`、`intent_classifier.py` | 已迁移 |
| AI 工具 | — | `app/services/ai_tools.py`（今日汇总 / 今日饮食 / 个人档案） | 已迁移 |
| 检索运行时 | — | `app/rag/runtime.py`（检索实例从 `api/rag.py` 提出来，聊天与管理页共用一份） | 已重构 |

### 迁移 revision 一览

| revision | 内容 |
|---|---|
| `0001` | `user` / `user_profile` / `user_goal` |
| `0002` | `avatar_upload` |
| `0003` | `food_item` / `food_alias` / `food_serving` / `diet_record` |
| `0004` | `daily_summary` / `weight_record` / `exercise_item` / `exercise_record` / `idempotency_record` |
| `0005` | `user_daily_checkin` / `post` / `post_image` / `post_like` / `post_comment` / `community_report` / `notification` / `admin_action_log` |
| `0006` | `ai_conversation` / `ai_conversation_message` / `ai_chat_trace` |

全部 `CREATE TABLE IF NOT EXISTS`，对存量库只做版本记账。`0005` 额外检查
`post`/`post_comment` 的审核三列是否存在并补齐——`CREATE TABLE IF NOT EXISTS`
对已存在的表整句跳过，不会补列。

### 验证命令与结果

```bash
# 离线单测（SQLite + 内存会话 + 内存 Redis 替身）
pytest tests -m "not integration and not live" -q        # 476 passed（3 subtests）

# 集成（真实 MySQL 8.0.46 + Redis 7）
docker compose -f deploy/compose.test.yml up -d --wait
APP_ENV=test \
TEST_DATABASE_URL='mysql+asyncmy://orange_test:orange_test@127.0.0.1:3307/orange_diary_test_local' \
TEST_REDIS_URL='redis://127.0.0.1:6380/0' \
pytest tests/integration -m integration -q                # 7 passed
docker compose -f deploy/compose.test.yml down -v
```

`451` 里本轮新增 AI 会话相关用例：`test_ai_conversation.py`（15）、`test_ai_service.py`（28）、
`test_ai_api.py`（14）、`test_ai_quota.py`（8）。此前各轮累计：`test_post_service.py`（34）、
`test_community_moderation.py`（24）、`test_community_limit.py`（22）、`test_community_api.py`（11）、
`test_diet_report.py`（21）、`test_report_api.py`（2）、`test_admin_ai.py`（8）。

集成用例覆盖：`0001→0006` 全链迁移在 MySQL 8 上可执行、BCrypt 落库回读、
refresh 轮换的复用检测、饮食写入 + 汇总重算 + 删除回滚、**并发同 requestId 只落一条**、
`INSERT IGNORE` 签到去重、**三条并发消息拿到互不相同的序号**、会话 ID 跨进程稳定。
这些是 SQLite 跑不出来的（`FOR UPDATE` 会被方言忽略、唯一键竞争行为不同）。

## 迁移中做出的显式取舍

| 项 | 决定 | 理由 |
|---|---|---|
| 校验失败状态码 | **400**（FastAPI 默认 422） | Java `ApiExceptionResolver` 对 `@Valid` 与绑定失败都返回 400；`docs` 535 行也要求"旧 code 语义按前端和 Java 实际对照保持" |
| 幂等实现 | MySQL 唯一键占位 + 两段提交（不用 Redis 抢锁、不用 SAVEPOINT） | pysqlite 的隐式事务会让 SAVEPOINT 直接 COMMIT 外层事务，"失败回滚"在单测里根本不成立；两段式在 MySQL/SQLite 行为一致 |
| 幂等 PROCESSING 残留 | 超过 60s 判为崩溃残留，释放后可重试 | 两段式会留下"占位已提交、业务未完成"的窗口；用时间判定兜住，与 Java 的 Redis 锁 TTL 同量级 |
| 签到"今天" | 固定 `+08:00`（不用 `ZoneInfo`） | Windows 无系统 tzdata，`ZoneInfo("Asia/Shanghai")` 直接抛 `ZoneInfoNotFoundError`；中国 1991 年后无夏令时，固定偏移与 Java `ZoneId` 结果一致 |
| 运动"常练" total | 只统计 active 项目 | Java 的 `countFrequentItems` 不过滤 `status`，停用一个项目会让 total 比列表多 1，前端"还有更多"永远为真 |
| 食物浏览全站使用次数 | SQL `GROUP BY`（Java 是全表拉内存分组） | 结果一致，避免一次浏览扫描整张 `diet_record` |
| 头像校验 | 数据库侧四道判定 + 对象存在性检查 | MinIO 客户端已接入，`PUT /profile` 会 stat 一次对象；登记行在但对象被清掉的情况现在返回 400「头像图片无效」而不是放行坏头像 |
| 社区列表排序 | 追加 `id desc` 兜底 | `published_at` 只有秒级精度，同一秒发的两条帖在 Java 里顺序不确定；追加主键排序只影响"本来就没定义"的并列项 |
| 营养占比"数据缺失"提示 | 保留为**不可达分支** | Java 的 `nutrientSum` 先把 null 映射成 0，`missingCount` 恒为 0，页面那行"部分营养数据缺失"永远不出现。保持现状以免两个实现的占比口径分叉；要改须连 `nutrientNullPolicy` 一起改 |
| `synchronize_session` | 显式 `False` + 手工回写实例 | Core `UPDATE` 的 `auto` 启发式只同步了部分字段（实测 `status` 同步、`moderation_reason` 不同步），会留下脏的会话态 |
| 报告生成并发闸门 | 进程内 per-user 锁 | Java 也是进程内 `ConcurrentHashMap`；多副本各限一次，多生成一份只浪费一次模型调用，不写错数据 |
| 会话记忆 | 只用库里的「摘要 + 最近 24 条」，不做进程内 `MessageWindowChatMemory` | 那份记忆是 langchain4j 的实现细节，进程本地、多副本下不可用，且与库内历史重复。行为差异仅限"上一轮的工具调用/结果不再进上下文"，这两类内容对下一轮回答没有作用 |
| 非流式「缺参追问」不退还配额 | **照 Java 原样保留** | 流式通道退了、非流式没退，这是 Java 自身的不一致（流式注释写明"追问没走生成模型"）。切流对拍时两边剩余次数必须一致，所以要改得两个通道一起改 |
| 工具轮次上限 | 3 轮（Java langchain4j 默认无上限） | 三个工具都只读、结果稳定，正常一轮收敛；加工上限是为了不让"模型反复调同一工具"把一次请求拖到超时 |
| 意图识别 | 进程内调用 `intent_classifier`，不再走内部 HTTP | 单进程后自己调自己只会多一次序列化；内部接口仍保留（运维脚本与灰度对比在用），两边共用同一份实现 |
| 知识检索 | 进程内 `app/rag/runtime.py` 共享实例 | 另建实例会重复加载索引与 rerank 模型（几百 MB），且两份缓存计数分开，管理页命中率凭空减半 |
| AI 上下文常量 | 提到 `Settings`（`ai_summary_trigger_messages` 等） | Java 是 `static final` 常量，改一次要重新编译；这几个值是"按数据量调"的运维参数 |

## 首次本机启动（2026-11-18 实测）

用已有的 Java 开发库（`calorie_tracker`，26 张表）直接起 Python 服务，第一次跑通全链路。
这一节记录"启动过程中撞到的问题"，因为**离线单测全绿也不能保证能起来**——三个 bug 都在
"真连库 + 真调模型"时才暴露。

| 步骤 | 结果 |
|---|---|
| `alembic upgrade head`（用 root） | 0001→0006 全部 `CREATE TABLE IF NOT EXISTS`，已有 26 张表全部跳过，仅新增 `alembic_version`；`alembic current` = `0006 (head)` |
| 启动 | rerank 模型 7.1s、索引 1.4s、Embedding 预热 4.6s，共约 13s |
| `GET /ready` | `{"business":"ready"}` |
| 注册 / 登录 | 注册 200（`id=60`）；登录返回 `code=0`（成功壳的 code 是 0，不是 200） |
| 业务读接口 | `/api/users/me`、`/api/daily-summaries?date=`、`/api/diet-records?date=`、`/api/foods/browse` 全 200 |
| AI 一次性问答 | `knowledgeStatus=ok`、3 条 sources、回答引用《成人肥胖食养指南（2024年版）》 |
| AI SSE | `knowledge`→`sources`→`message`×199→`done`(conversationId)，`content-type: text/event-stream` |
| 记录类追问 | "两个鸡蛋"→反问克数（无卡）；"200克米饭"→`action=confirm_diet_record` + `recordPreview`；"跑步30分钟"→`confirm_exercise_record` + `exercisePreview` |
| 内部检索 | `POST /internal/rag/v1/search` 200，`answerable=true`，`rerank=local:bge-reranker-base`，总耗时 3.2s（rerank 2.5s） |
| 鉴权 | 无 token 打 `/api/ai/chat` → 401「请先登录」；内部检索无 Bearer → 401 `unauthorized` |

### 启动时修复的三个缺陷

1. **`pool_pre_ping` 让所有业务接口 500**（新增 `app/db/asyncmy_dialect.py`）
   `MySQLDialect_asyncmy` 继承 pymysql 方言的 `do_ping`，后者按**环境里装没装 pymysql**
   决定要不要给 `ping()` 传参。本机有 PyMySQL 1.2.3（其 `ping` 已去掉 `reconnect` 形参），
   探测结论是"不用传参"，而 asyncmy 适配器 `ping(reconnect: bool)` 没有默认值 →
   每次从连接池取连接都 `TypeError: missing 1 required positional argument: 'reconnect'`。
   离线单测用 SQLite，走不到这条路径，所以全绿。修法：子类把 `_send_false_to_ping`
   固定为 True（并补 `supports_statement_cache = True`，否则语句缓存被关且每次启动告警）。

2. **`/internal/rag/v1/search` 500：`NameError: _trace`**（`app/api/rag.py`）
   把 `service_for`/`limiter_for` 抽到 `app/rag/runtime.py` 时，连带删掉了 `_trace` 和
   `_authorized` 两个私有函数。AI 问答走的是进程内检索，不经过这个接口，所以聊天正常、
   只有内部检索 500——这种"整块功能不在主路径上"的缺失最难靠单测发现。已按原文恢复。

3. **AI 摘要与流式断线补写全部静默失败**（`app/services/ai_conversation.py`）
   `self._session_factory = session_factory or get_session_factory` 少调一层：存进去的是
   "返回 sessionmaker 的函数"，`async with self._session_factory()` 得到的是
   `async_sessionmaker` 本身，抛 `TypeError: does not support the asynchronous context
   manager protocol`。异常被 `except Exception` 吞成一条 warning（"回答正常但摘要永远不推进"）。
   测试一直注入现成 sessionmaker，所以没覆盖默认分支。已归一为存 sessionmaker，并补用例
   `test_default_session_factory_opens_sessions`。

修复后：离线 **452 passed / 7 skipped**；服务日志无 WARNING / Traceback。

## 可靠性增强（2026-11-19，P1）

| 项 | 文件 | 说明 |
|---|---|---|
| 模型重试 + 熔断 | `app/services/model_gateway.py`、`app/config.py` | 瞬时故障（连接失败 / 超时 / 429 / 5xx）指数退避重试（默认 3 次）；4xx 与空回答不重试；按场景隔离的熔断器（closed/open/half_open），冷却期直接 `circuit_open`，复用 `app/rag/circuit.py` 的 `CircuitBreaker` |
| 多轮指代改写 | `app/services/query_rewriter.py`、`app/prompts/scenarios.py`、`app/services/ai_service.py` | 检索前用最近 8 轮对话把「那一天 / 这个 / 它」消解成独立查询（`query-rewrite` 场景）；仅命中指代词且需检索才触发，改写失败退回原问题不阻断 |

配置项 `MODEL_RETRY_*` / `MODEL_BREAKER_*` 已补进 `.env.example`。离线 **465 passed / 7 skipped**。

## 可解释性与反馈（2026-11-19，P2）

| 项 | 文件 | 说明 |
|---|---|---|
| 来源快照 | `alembic/versions/0007_*.py`、`app/models/ai.py`（`ai_answer_source`）、`app/services/ai_source_snapshot.py` | 回答落库时把本次引用的片段（chunk_id / 标题 / 标题路径 / 正文摘录 / 分数 / `index_version`）存副本；按 `(message_id, rank)` 去重；写入失败不影响回答。索引更新后仍可解释旧回答引用（计划书 §1.6） |
| 赞 / 踩反馈 | `ai_answer_feedback` 表、`app/services/ai_feedback.py`、`app/api/ai.py`、`app/api/admin_ai.py` | `POST /api/ai/messages/{id}/feedback`（登录态定身份，只能评自己的 assistant 消息，重复提交改主意、null 取消）；历史接口带 `rating`/`upCount`/`downCount`/`sources`；管理端 `GET /api/admin/ai/feedbacks`、`/sources`（计划书 §2.6） |

schema 版本升到 `0007`，真实 MySQL 上已 `upgrade head`。离线 **476 passed / 7 skipped**；
独立测试库的集成 **7 passed**（含 0001→0007 全链迁移）。

## 未完成 / 风险

- **M3 切流差分未开始**：70 个端点都在 Python 侧可跑，但还没有"同一请求打两个实现比响应"的对拍。
  这是发现"我以为对齐了"的唯一手段，尤其 AI 回答（模型输出本身有随机性，只能对拍结构字段：
  `action` / `preview` 字段集 / `knowledgeStatus` / `sources` 数量）。
- **AI 链路的真实调用未自动化**：`/api/ai/**` 的单测全部把模型换成预定回复。
  2026-11-18 手工跑通了真实 DeepSeek（含模型自发工具调用、SSE 时序、知识库 3 条来源，见上节），
  但这些没有进用例——要固化成回归需 `live` 标记 + 付费 key，跑一次约几十次调用。
  已覆盖的是我们这侧的全部编排分支（含工具轮次、断线部分落库、三态检索降级）。
- **`live` 标记用例未跑**：`tests -m live` 需要 `DEEPSEEK_API_KEY`，本轮没跑。
- **MinIO 未进测试环境**：图片上传/读取只有单元级（魔数、限流、key 规则）覆盖，
  真实 `put_object`/`get_object` 未跑过。集成测试要加一个 MinIO 服务容器。
- **报告字体随包分发**：`app/reports/assets/NotoSansSC-Regular.ttf` 10MB（OFL）。
  `Dockerfile` 用 `COPY app ./app` 整包带入、`.dockerignore` 也没排除 `app/`，
  镜像里会有；代价是镜像增大约 10MB，换来的是不依赖阅读器自带 CJK 字体。
- **`/api/capabilities` 未实现**：文档 18 节要求逐能力返回 `ready|degraded|unavailable`。
- **开发代理已切至 Python**：`orange-diary-web/vite.config.js` 默认指向 `127.0.0.1:8000`，支持环境变量覆盖；生产切流尚未验收。
- **`docker-compose.yml` 的 `ai` 服务还只是检索进程**：它只配了 DeepSeek / Qdrant / Ollama 相关变量。
  切流时 Python 要承载业务端点，必须补 `DATABASE_URL` / `REDIS_URL` / `JWT_SECRET` / `MINIO_*`，
  并把 `backend`（Java）从栈里摘掉。这是 M3 的部署动作，改的是 `orangeDiary/docker-compose.yml`。
- **集成用例未进 CI**：CI 只跑离线单测，集成需要 MySQL/Redis service 容器。
- **来源快照与赞踩反馈前端未接**：后端接口与表已就绪，但 `orange-diary-web` 还未渲染来源卡片的历史回溯与赞踩按钮；属于 M3 切流前端改造的一部分。


## 2026-11-19 聊天体验修复（工程自测，未用户验收）

- 已完成：流式记录请求返回 action 确认卡；确认前不写饮食/运动业务记录；前端接收并显示既有确认表单。
- 已完成：上一问从默认会话原文查询；近期上下文优先保留最新消息，不因摘要游标丢失近期原文；历史接口与聊天使用同一默认会话。
- 已完成：页面进入时读取服务端历史，取消全账号共享 sessionStorage 历史优先级；历史加载期间禁止发送，防止覆盖新消息。
- 已完成：普通离线聊天测试隔离真实 Redis 配额，避免重复自测消耗本机账号配额。
- 后端验证：`.\.venv\Scripts\python.exe -m pytest -m "not live and not integration" -q --tb=native` → **481 passed, 7 deselected, 12 warnings, 3 subtests passed**（39.94s）。告警含 Windows 异步资源关闭和 pytest 插件弃用。
- 前端验证（orange-diary-web）：`node --test tests/ai-stream.test.mjs` → **2 passed**；`npm run build` → **473 modules transformed，构建成功**。
- 未完成：新版 R3 私人厨师、周计划、采购、动态调整、复盘；本轮修复不能作为这些能力的完成证据。
- 未验证：本轮真实付费模型调用、浏览器完整交互验收；现有 8000 进程未以 reload 启动，需重启当前后端后验证新代码，未替用户重启 IDE 管理的进程。


## 2026-11-19 R3代码核对（未修改业务实现）

- 审计报告：`docs/R3_CODE_AUDIT_2026-11-19.md`；可复现探针：`scripts/audit_r3_probe.py`（内存SQLite、测试样例、模型禁用）。
- 本轮新模块单测23 passed（1个插件弃用告警）；前端构建成功；真实库只读确认0009、10条sample食谱/0条formal、2158条食物、6条份量换算；当前/ready=200，OpenAPI 97 paths。
- 探针复现6项业务缺口：缺餐仍OK、available_only超库存预留、“够用”枚举不匹配、撤销采购不退库存、下周草稿仅1天、厨师人数未影响用量。
- 未重跑真实付费模型/图片/价格服务或浏览器完整验收；未修复本次发现的业务问题，不能标为已完成。
