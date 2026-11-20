# M0 基线修复报告

日期：2026-11-17
范围：`orangeDiary-ai`（Python）、`orangeDiary`（Java）、`orange-diary-web`（Vue）
依据：`AI_RAG_DEVELOPMENT_PLAN.md` 第 3 章「阶段 0」与用户确认的四项决策

## 1. 结论

阶段 0 的六类问题已处理：凭据清理、启动与配置一致性、意图单位识别、协议字段统一、指标枚举分离、Java 缺参追问与运动确认响应、意图客户端超时与错误记录。

测试结果：

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| Python | `.venv/Scripts/python.exe -m unittest discover -s tests` | 33 项通过（原 17 项 + 新增 16 项回归） |
| Java | `./mvnw.cmd test` | 88 项，0 失败，0 错误，6 跳过（与基线一致） |
| 前端 | `npm run build -- --outDir <临时目录>` | 构建成功，471 modules |

## 2. 改动清单

### 2.1 凭据与配置

| 文件 | 改动 |
| --- | --- |
| `orangeDiary-ai/.env.example` | 删除真实 `AI_SERVICE_TOKEN`、MySQL/Redis 密码、`JWT_SECRET`；改为占位符并附中文说明 |
| `orangeDiary-ai/.env`（新增，已被 gitignore） | 本地真实值：DeepSeek Key、`AI_SERVICE_TOKEN` |
| `orangeDiary-ai/.gitignore`（新增） | 忽略 `.env`、`__pycache__`、`.venv`、IDE 本地文件 |
| `orangeDiary-ai/app/config.py` | 加载项目根 `.env`，`override=False`（进程环境变量优先） |
| `orangeDiary-ai/requirements.txt` | 增加 `python-dotenv` |
| `orangeDiary/src/main/resources/application.yml`（本地文件，已被 gitignore） | `service-token` 去掉硬编码真实值，改为 `${AI_SERVICE_TOKEN:}`；新增 `spring.config.import: optional:file:.env[.properties]`；`features.ai-chat.enabled` 改为 `${FEATURES_AI_CHAT_ENABLED:true}` |
| `orangeDiary/src/main/resources/application.example.yml`（跟踪的模板） | 同步 `spring.config.import`、`ai.gateway` 占位配置、`FEATURES_AI_CHAT_ENABLED` / `FEATURES_RAG_ENABLED` 开关说明 |
| `orangeDiary/.env`（本地，未跟踪） | 补充 `AI_SERVICE_TOKEN` |
| `orangeDiary/docker-compose.yml` | backend 增加 `AI_GATEWAY_BASE_URL`、`AI_SERVICE_TOKEN` 透传 |

### 2.2 意图识别与协议（Python）

| 文件 | 改动 |
| --- | --- |
| `app/services/intent_router.py` | 新增质量/计数/体积单位表与中文数字解析；`food_record` 显式识别「数量 + 单位」；计数与体积单位不换算为克；食物名清洗；`build_clarification` 支持按槽位生成自然追问 |
| `app/services/intent_validator.py` | 拆分「每日汇总指标」与「食物营养指标」两套枚举，中文别名归一化为 `calorie/protein/fat/carbohydrate`；新增 `quantity/unit/metrics` 白名单；布尔、NaN、Infinity 拒绝；`missing_slots` 读 snake_case（旧 `missingSlots` 仅在边界兼容）；新增 `canonicalize_slots` 供规则与语义共用 |
| `app/services/intent_result.py` | 规则与语义结果都走同一套槽位校验，非法值丢弃后自动转为缺参 |
| `app/api/intent.py` | 规则分支改用统一 `IntentResult` 序列化；语义分支在缺参且模型未给 reply 时补追问 |
| `app/prompts/intent_prompt.py` | 全部改为 snake_case；明确计数单位不得换算为克；明确营养指标枚举 |
| `app/services/semantic_parser.py` | 提示词改 snake_case；解析兼容旧 camelCase（仅边界）；`amount_g` 拒绝布尔值 |

### 2.3 Java 承接

| 文件 | 改动 |
| --- | --- |
| `service/PythonIntentClient.java` | 连接 2s / 读取 8s 超时；401/超时/其他异常分别记录安全错误码与 traceId，不再静默吞掉 |
| `service/impl/AiServiceImpl.java` | ① 意图层报告缺参时直接返回追问（普通接口与 SSE 两条路径），不再调用模型；② 新增运动记录预览 `confirm_exercise_record`（项目匹配、时长、候选项，未匹配时明确说明）；③ `food_nutrition` 改为查询食物库并注入每 100 克营养数据，不再用今日饮食冒充；④ 关键词降级范围收窄（裸「热量」不再触发今日汇总；`蛋白/脂肪/碳水` 不再触发今日饮食） |
| `dto/ai/AiChatResponse.java` | 无需改动（`exercisePreview` 字段与前端契约早已存在） |
| 前端 | 无需改动（`AiChatView.vue` 已支持 `confirm_exercise_record`） |

## 3. 实测记录

启动与接口（Python 服务临时启动在 8000，验证后已停止）：

```
GET /health
{"code":200,"message":"Python AI service is running","data":{"deepseekConfigured":true,"model":"deepseek-chat"}}

POST /internal/ai/v2/classify  {"message":"我吃了2个鸡蛋"}
{"intents":[{"intent":"food_record","confidence":1.0,
 "slots":{"food_name":"鸡蛋","quantity":2.0,"unit":"个"},
 "missing_slots":["amount_g"],"source":"rule"}],
 "reply":"你说的「2个鸡蛋」大约有多少克？"}

POST /internal/ai/v2/classify  {"message":"我吃了100克米饭"}
{"intents":[{"intent":"food_record","confidence":1.0,
 "slots":{"food_name":"米饭","quantity":100.0,"unit":"克","amount_g":100.0},
 "missing_slots":[],"source":"rule"}],"reply":null}

无 Authorization → HTTP 401
语义路径（真实模型）：{"message":"帮我看看膳食纤维每天应该吃多少"}
→ food_nutrition + missing_slots ["food_name"] + 追问
```

规则解析样例：

| 输入 | 结果 |
| --- | --- |
| 我吃了2个鸡蛋 | quantity=2, unit=个, 缺 amount_g → 追问克数 |
| 我吃了100克米饭 | amount_g=100 |
| 我吃了0.2公斤米饭 | amount_g=200 |
| 我吃了半碗米饭 | quantity=0.5, unit=碗, 缺 amount_g |
| 我吃了两碗米饭 | quantity=2, unit=碗, 缺 amount_g |
| 我吃了2两米饭 | amount_g=100 |
| 我喝了250毫升牛奶 | quantity=250, unit=毫升, 缺 amount_g（不默认 1ml=1g） |
| 我今天跑步了 | exercise_record, 缺 duration_min → 追问分钟 |
| 鸡蛋的蛋白质 | nutrition_metric=protein |

## 4. 未验证与限制

- **端到端问答未验证**：需要真实账号登录的 Java 聊天接口没有执行，未做登录态下的个人数据问答与确认保存。原因是当前 8080 端口有旧进程在运行，且未使用测试账号登录。
- **Java 运行实例仍是旧代码**：8080 上的进程在我改动前已存在，需要重启（IDE 或 `./mvnw.cmd spring-boot:run`）才能加载新代码。
- **前端构建**：沙箱保护拦截了 vite 清空 `dist/`（75 个文件超过批量删除阈值），因此构建输出到临时目录验证，`dist/` 未更新；正式构建需你在本地执行 `npm run build`。
- **真实模型质量**：只验证了分类协议与追问，没有评测回答质量与幻觉。
- **RAG 未开始**：本阶段没有引入任何检索代码。

## 5. 已知风险

1. **凭据历史暴露**：旧 `.env.example` 中的真实 Token、MySQL/Redis 密码、JWT_SECRET 已进入 git 历史，删除当前文本不能消除历史记录。按用户决定本轮不轮换，建议后续轮换 `JWT_SECRET`、数据库密码和 `AI_SERVICE_TOKEN`。
2. **DeepSeek Key 位置**：真实 Key 存在于 `.idea/workspace.xml`（未被 git 跟踪，但属于本机明文），现同样写入本地 `.env`。不要提交该文件。
3. **Ollama 未运行**：M1 的 bge-m3 Embedding 依赖它，开工前需安装/启动并拉取模型。
4. **旧 Qdrant 集合**：`orange_diary_knowledge` 有 72 个 1024 维旧向量，切块与 embedding 版本不明，不要直接作为新索引使用，M1 将新建版本化集合。
5. **Docker 开关语义**：`FEATURES_AI_CHAT_ENABLED` 现在生效；生产默认关闭，开启时必须同时提供 `AI_GATEWAY_BASE_URL` 与 `AI_SERVICE_TOKEN`。

## 6. 回滚方式

全部改动为工作区未提交修改，可按文件回滚：

```bash
cd J:/develope/橘子rag/orangeDiary-ai && git checkout -- app tests requirements.txt .env.example
cd J:/develope/橘子rag/orangeDiary && git checkout -- src/main/resources/application.example.yml docker-compose.yml src/main/java/com/example/orangediary/service/PythonIntentClient.java src/main/java/com/example/orangediary/service/impl/AiServiceImpl.java
```

注意：`src/main/resources/application.yml` 被项目 `.gitignore` 排除，不在版本控制内，回滚需手工把 `service-token` 默认值加回、删除 `spring.config.import` 与 `FEATURES_AI_CHAT_ENABLED` 引用。

新增文件（`.env`、`.gitignore`、`tests/test_intent_router.py`）直接删除即可；`.env` 不影响仓库，但它保存了本地凭据，删除前确认另存。

## 7. 下一步（M1）

1. 启动 Ollama 并拉取 bge-m3，确认实际输出维度（不要只信配置的 1024）。
2. 建立 `knowledge/` 与来源清单，先收 10–20 篇权威饮食文献。
3. 实现结构化切块 + 内容哈希 + 版本化 Qdrant 集合 + BM25 中文索引 + `scripts/ingest.py`。
4. 需要你提供：旧项目里已有的文献位置（若保留），以及 Ollama 是否已安装。
