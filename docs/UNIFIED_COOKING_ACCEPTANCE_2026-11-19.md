# 统一聊天做菜能力：当前验收检查

日期：2026-11-19（Asia/Shanghai）。基线：`AI_CHAT_COOKING_IMPROVEMENT_PLAN.md`。

## 结论

**已接入基础做菜分支，但尚未完成开发基线，不宜标为交付完成。** 当前不再只是文档：意图、聊天路由、做菜上下文、流式场景和前端示例已存在；仍缺可用菜谱知识域、可靠追问分类以及完整复合意图处理等关键环节。

本轮只检查、复现和记录结果，没有修复业务代码、导入数据、升级依赖或重启现有后端。未调用真实付费模型、未创建真实测试账号。业务库仅做菜谱数量只读统计。

## 后续状态更新（2026-11-19 22:26）

本文记录的是 22:14 的检查结果。此后已修复其中三条，阅读时以本节为准：

| 本文条目 | 当前状态 | 依据 |
|---|---|---|
| P1-0 全量回归 23 failed | **已修复**：测试替身补 `domain` 参数，与 `QdrantVectorStore` 同签名 | 全量 `pytest tests -q` → 529 passed, 7 skipped |
| P1-2 流式复合消息吞掉做菜 | **已修复**：含 cooking 时先发记录卡再继续生成 | `test_stream_composite_keeps_cooking_after_record_card` |
| P1-4 未完成运动误判 | **已修复**：补未来时排除与完成式识别 | `ExerciseTenseTests`（跑了/跑完了/刚跑完 命中，准备/打算/计划 不命中） |

| P1-1 cooking 知识域无数据 | **已修复（23:20 更新）**：payload 带 `knowledge_domain`/`recipe_key`，`bm25/` 产出 nutrition（144）与 cooking（24）两份分域索引，集合 `orange_diary_knowledge_v4` 共 168 片段；烹饪域导入 18 道项目整理菜谱 | `pytest tests -q` → 542 passed；真实 Qdrant 双向域隔离验证；真实模型「红烧肉怎么做」返回 cooking 来源 |
| 菜名从句吞进槽位（本文未列） | **已修复**：「鸡腿除了红烧还能怎么做」原被提取成「鸡腿除了红烧还能」，检索必然零命中 | `test_clause_is_trimmed_from_dish_name` |

| P1-3 短追问分类未接会话历史 | **已修复（2026-11-20 00:10）**：`classify_message` 增加 `history`，语义路径带最近几轮对话 | `test_semantic_classification_receives_history`；真实链路「不放辣」判为 cooking 追问 |
| 做菜追问检索零命中（本文未列） | **已修复**：追问先用历史补全（`cooking-rewrite`），零命中按"原句 → 已确认菜名"重试 | 「第二道怎么做」命中宫保鸡丁；「没有生抽了怎么办」命中《食材搭配与替换》 |
| 有份量映射却反问克数（本文未列） | **已修复**：`food_serving` 的映射直接换算并出卡 | 「吃了两个鸡蛋」出 `confirm_diet_record`（100g） |

**仍未修复**：P2-1 至 P2-4、正式食谱 60/100。全量回归 562 passed / 7 skipped。

## 1. 已确认接入的部分

- `intent_router.py`、`intent_validator.py`、`intent_prompt.py` 已登记 cooking 及槽位。
- `AiChatService` 复用原账号默认会话，按意图选 `cooking/cooking-stream`，没有为统一聊天创建第二份厨师历史。
- 新 `CookingService` 只提供素材，不扣次、不写历史、不独立调模型；统一聊天做菜分支不再拿前三道无关菜兜底。
- 读路径开始支持 cooking domain：知识客户端、运行时、Qdrant 过滤代码已改；这不等于资料已导入或索引可用。
- 做菜提示词已要求单列调料、参考人数、火候/时间/完成判断和缺料提示；实际输出效果仍需要真实对话验证。
- 前端聊天有“红烧肉怎么做”等示例，旧独立页面不再作为路由入口；既有确认卡保留。

## 2. 阻塞与缺口

### P1-0 全量离线回归未通过

命令：`.\.venv\Scripts\python.exe -m pytest -m 'not live and not integration' -q --tb=short`。

结果：**23 failed，502 passed，7 deselected，16 warnings，3 subtests passed，67.96s**。失败集中在 `test_rag_service.py` 与 `test_rerank.py`；日志中核心错误为 `TypeError: FakeStore.search() got an unexpected keyword argument 'domain'`。

生产 `QdrantStore.search` 已增加 domain，测试 FakeStore 仍用旧签名。这说明接口改动与测试契约没有同步，不能直接推断生产 Qdrant 一定报同样错误。应更新替身并补真正的领域过滤断言，再检查被这个异常提前遮蔽的召回/重排行为；不能只修改预期或跳过这批测试来宣称通过。

### P1-1 菜谱知识域没有落地，现有导入链也未配套

读取当前 Settings 得到索引版本 knowledge-v3，所需文件 `knowledge/index/bm25/cooking.knowledge-v3.json` 不存在。`runtime.py:39` 要求按域命名 BM25 文件；`service.py:193` 文件缺失即抛 RagNotReady。

现有 `pipeline.py:202` 仍写 `chunk.payload()`，`schemas.py:68` 的 payload 没有 knowledge_domain；`pipeline.py:237` 仍只保存普通 `{index_version}.json`。Qdrant cooking 读路径却按 knowledge_domain=cooking 过滤。不能通过原导入脚本直接得到配套 cooking 索引。

真实业务库当前食谱依然只有 **sample 10 条，formal 0 条**。现阶段主要依赖这 10 条结构化样例；超出覆盖范围时不能认为菜谱 RAG 会补上。

### P1-2 记录参数齐全的复合请求，在流式入口吞掉做菜部分

输入：“中午吃了200克米饭，晚上鸡腿怎么做”。规则正确得到 food_record + cooking，但 `ai_service.py:435–450` 在预览卡存在时直接 return，后续做菜上下文和模型调用都不会执行。

隔离探针：用内存历史、假配额和记录预览替身调用真实 `chat_stream`，没有任何数据库/模型写入。输出只有“请核对下方记录卡”及 action/done，做菜上下文调用次数为 0。

现有测试只覆盖“记录缺参不阻断做菜”，没有覆盖“记录参数完整时仍要答做菜”。用户平时使用的是流式入口，因此这是实际主流程阻塞项。

### P1-3 追问分类没有接入会话上下文

规则探针：

| 输入 | 当前规则结果 |
|---|---|
| 就做第二道，没有生抽 | cooking/followup |
| 没有生抽 | 空，进入语义分类 |
| 不吃辣 | 空，进入语义分类 |
| 只有空气炸锅 | 空，进入语义分类 |
| 我有辣椒、五花肉、鸡腿 | 空，进入语义分类 |

`intent_classifier.py:37` 仍只接收 message/trace_id；`ai_service.py:640` 未传历史。分类模型看不到“用户前面正在做哪道菜”，无法稳定将这些短句归为 cooking。不能把规则未命中等同于最终一定失败：后续语义模型仍可能猜对，但这不是可依赖的上下文识别。

此外，菜品选择和原约束主要依赖原始对话文本；`_history_lines` 每条仍截到 200 字，没有新增完整多轮链路测试证明第二道菜、人数和过敏限制能持续保留。

### P1-4 未完成运动仍可能生成已完成运动确认卡

输入“我准备跑步30分钟”，规则实际输出 exercise_record、exercise_name=跑步、duration_min=30。它会进入已完成记录预览，不符合需求中的时态区分。

这不表示系统已经自动写库：确认后才保存的边界仍在。但错误确认卡会诱导用户记录尚未发生的运动，需补时态判断和回归测试。

### P2-1 无资料策略与改进文档不一致

新文档允许对常见菜给明确标识的“通用做法建议”。当前 `CookingService.build` 无候选且无资料时仍要求拒答；COOKING_PROMPT 又规定只用候选和资料。只有样例覆盖时，用户问库外常见菜仍可能被拒绝。

应按已设计策略实现，或明确修订基线；不能一边声称通用建议可用，一边用提示词禁止它。

### P2-2 不可用、排队满与无证据状态被混淆

`CookingService._knowledge` 对异常/None 用空结果加 degraded 说明；`AiChatService._cooking_context` 无 hits 时统一设置 no_evidence，没有将 busy/unavailable 如实传给前端。用户看不到“资料域没准备好”和“确实没有这道菜”的区别。

### P2-3 兼容厨师接口仍留有旧问题

`POST /api/assistant/chef` 仍独立调用 ChefService，没接现有统一聊天配额；其旧候选回退仍取前三道食谱。隐藏前端路由不等于后端入口关闭。

`RecipeService.scale_servings` 仍只缩放 grams 和营养，不缩放 quantity/unit 原始数量。新 CookingService 遇到只有“个/勺”无克重的用料，又渲染成“份量待定”，丢失原本可用的计数信息。

### P2-4 依赖和交付记录尚未收尾

官方 npm audit 再次报告 **2 high**：follow-redirects、source-map-js。风险适用边界与上一份审查一致：分别涉及 Node 重定向处理及 source-map 解析，不等于已证明当前浏览器界面可被直接利用。本轮未自动修复。

`IMPLEMENTATION_STATUS.md` 与开发文档仍标“仅文档、未实施”，已落后于当前新增代码；应在实际修复和验收后更新，不能用旧状态估算完成率。

## 3. 本轮验证结果

| 检查 | 结果与边界 |
|---|---|
| 意图/厨师/聊天/API/改写/网关相关测试 | 104 passed，3 subtests passed；11 条警告（anyio/pytest 弃用及异步连接析构时 event loop closed） |
| 全量离线回归 | 23 failed，502 passed，7 deselected，16 warnings，3 subtests passed；67.96s；失败契约见 P1-0 |
| 前端构建 | 通过，473 modules，2.54s |
| `node --test tests/ai-stream.test.mjs` | 2 passed |
| 390px 浏览器检查 | API 全部桩：示例问题能发送，SSE 内容显示，无饮食确认卡、JS 错误或横向溢出 |
| 后端健康 | 当前端口 8000 的 /ready 和 /health 都为 HTTP 200；不能据此证明该运行进程已加载磁盘上的所有新修改 |
| 数据与索引 | 只读确认 sample 10/formal 0；当前配置所需 cooking BM25 索引不存在 |
| 前端依赖扫描 | 官方 registry 审计返回 2 high，未修复 |
| 真实模型对话 | 本轮未执行，不以桩回复证明菜品、用料与多轮回答效果 |

浏览器检查使用本轮构建的独立 5193 预览，检查后已停止；没有停止用户 8000/5173 服务。

## 4. 建议完成顺序

1. 修复全量回归接口契约，打通 cooking 资料导入/元数据/双通道索引/发布与回滚，并准备覆盖用户示例的完整菜谱。
2. 修复完整记录＋做菜的复合消息，确保流式/非流式都不丢子请求。
3. 给意图分类加入必要的统一会话上下文，补短追问、约束继承和时态判断测试。
4. 对齐无资料策略、知识状态、计数用量和兼容接口配额；处理依赖通告。
5. 回归 U01–U15，再在真实运行环境少量验证菜名、食材、多轮与记录切换。分别记录离线检查、真实模型结果与用户最终验收。

当前适合继续开发和局部试用，不能宣称“统一聊天做菜能力全部完成”。
