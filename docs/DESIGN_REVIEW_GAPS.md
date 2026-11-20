# 设计文档问题清单（审阅记录）

- 审阅对象：`C:\Users\15175\Documents\Codex\2026-11-18\new-chat\outputs\OrangeDiary_Python重构与智能饮食助手设计文档.md`（v1.0，937 行）
- 审阅日期：2026-11-18
- 审阅方式：全文逐节阅读 + 对文档声明的事实做代码核对
- 用途：逐条回填。每条给出原文位置、问题、需要补的决定。**本文件不修改原设计文档。**

## 回填说明（2026-11-18）

已对36条问题逐条在原“需要”后补充解答，保留原审阅文字。解答中的具体规则作为v1.0设计的补充/修正，本次不改主设计文件、业务代码或其他历史文档。用户最新要求优先于本文；人周、限额、样本规模、展示阈值、回滚时间等属于明确标注的设计默认，不冒充用户已确认或已经实测。

当前完成的是“设计决定已回填”，不是“功能/依赖已实现验证”。供应商精确模型ID、真实数据库结构、价格费用、目标公式与人工质量验收等仍按各条列明阶段核实，不能因有答复就标记运行阻塞已消失。

**已合并**：2026-11-18 起，36 条解答已并入主设计 v1.1（`docs/PYTHON_FULL_BACKEND_AND_DIET_ASSISTANT_DESIGN.md`），本文只保留审阅过程与回填原文，作为追溯记录。此后以 v1.1 为准；本文与 v1.1 冲突时以 v1.1 为准，并回头修 v1.1，不再“用回填覆盖主设计”。

八项优先问题的结论：最终全量替代但分阶段验收；LangGraph只编排、DB负责恢复；普通问答旧SSE/复杂任务job+events；分类限频与token硬预算并用；前端UUID幂等键；私人厨师为烹饪能力集合；Docker Compose真实MySQL/Redis集成测试；开发者自测、用户最终验收，开放新写入即结束直接回切窗口。

复核（同日第二次）：36 条正文均已有回填解答，不再是未决问题。回填之后仍需确认的数值、与现状的三处不一致、合并 v1.1 需同步的 5 处主设计条文，统一见文末“回填状态复核”。

## 事实核对结论（先说清哪些不是问题）

下列文档声明经代码核对**准确**，不列入问题：

| 声明 | 核对结果 |
|---|---|
| 基线 SHA：Python `6179487` / Java `52b55d1` / 前端 `bb41979` | 三仓 HEAD 完全一致 |
| 附录 A 旧接口索引 | 70 行，与 Java 侧 `@Get/Post/Put/Delete/PatchMapping` 总数 70 完全一致 |
| `idempotency_record` 可扩展复用 | 表存在（`idempotency_migration.sql`） |
| 复用 `HealthSummaryWriter` 并发保护语义 | 类存在 |
| `app/services/model_gateway.py`、`clients/deepseek_client.py`、`intent_validator.py`、`api/gateway.py`、`api/intent.py` | 全部存在 |
| requirements 缺 ORM/迁移/LangGraph | 确认：sqlalchemy / alembic / celery / asyncmy / langgraph 出现次数均为 0 |
| `calorie_tracker.sql` 含 DROP TABLE | 10 处 |
| 知识资料 13 个文件 | 一致 |
| `VITE_JAVA_API_TARGET` 变量名 | 一致 |
| 现有前端原生 fetch 解析 SSE | 一致 |

结论：**文档没有编造代码事实**。问题集中在"决策未关死、验收不可判定、范围无上限"三类。

---

## P0 阻塞性：不解决无法开工，或必然返工（8 条）

### P0-1 范围没有上限，也没有人力/时间假设
**原文**：1.2、1.3、20 节 M0–M8
**问题**：范围 = 完整替代 Java（认证、档案、食物、饮食/运动/体重、签到、PDF、上传头像、社区、通知、举报审核、管理日志）+ 8 个新模块（食谱库、多约束求解、库存流水与预留、采购与联网价格、图片理解、周期复盘、LangGraph、Celery）。文档只有"不能只做聊天演示就声称完成"这句约束，没有写人力、周期或最小可交付集。
**需要**：确定是"全量替代"还是"AI 闭环 + 核心记录先行，社区/管理端/PDF 延后"。仅 M1–M2 的等价替换接近现有 Java 项目全部工作量。

**回填解答（2026-11-18）**

**结论：采纳范围分层建议，最终范围不缩减。** 用户已明确完全替代Java，因此最终交付仍覆盖旧业务；首个开发增量可以先完成核心记录，但不得将该增量称为完整替代，也不得提前关闭尚承载社区/报告等业务的Java。

执行顺序固定为R0（M0基线）→R1（M1核心业务，可本地独立演示）→R2（M2旧功能全部兼容，Java可停机）→R3（M3–M7智能饮食闭环）→R4（M8整体交付）。R1为内部增量，不做生产混合写入切流；R2之后的新增功能不回调Java。

规划假设：1名熟悉Python/Vue的开发者等效投入，模型辅助编码，用户负责产品验收；全量按10–16人周量级预留，其中旧业务迁移约3–5人周、智能能力约5–8人周、基线与综合验收约2–3人周，区间仅用于排期，不是工期承诺。真实数据质量、供应商接入和现有接口差异会改变估算。M0输出任务分解和实际估算后替换此范围；没有固定交付日期，不通过偷偷删功能压缩工期。

<!-- review-answer-end -->

### P0-2 LangGraph 被 12.2 自己架空了
**原文**：121（3 节定 LangGraph 做工作流）、363、369
**问题**：12.1 要求"状态不能只放在 LangGraph checkpoint"，12.2 说"checkpoint 初版用 MySQL 业务快照，不引入 saver"，最后落到"否则明确只支持从已持久化节点重新执行"。不用 checkpointer、不用状态图恢复，LangGraph 剩下的价值只有"把分支写成图"，代价是多一层依赖和一套新调试方式。文档没有为这个取舍给理由。
**需要**：明确二选一——(a) 接受"LangGraph 仅作编排语法，不承担状态恢复"，并删掉 checkpoint 相关表述；(b) 允许引入一个维护中的 MySQL/Postgres saver，接受多一个存储。

**回填解答（2026-11-18）**

**结论：明确选择(a)：LangGraph只负责单次执行的状态图编排，不承担跨进程checkpoint恢复。** 保留它是为显式表达路由、澄清出口、有限修复和求解分支；不把引入LangGraph包装为已经实现durable execution。

跨重启恢复由MySQL的job、workflow_step和input_snapshot实现。补充workflow_step字段：job_id、node_name、attempt、input_hash、state、output_ref、started_at、completed_at、lease_token；完成节点在短事务中保存输出与事件。worker重新领取租约后复用已完成且输入版本匹配的结果，从未完成节点重新调用图；输入变化则创建新attempt。waiting_input结束本次图执行，答案提交后校验版本并启动下一次执行。

因此第12.2节不再保留“可能引入saver”的开放选项：首版不配置任何LangGraph saver，不承诺token级续传或模型恰好调用一次。崩溃可能重复只读模型调用，计入实际费用；所有写操作仍通过确定性API/DB幂等控制。验收包括worker在节点前后崩溃、等待澄清时重启、旧输入恢复被拒绝三类，不以MemorySaver通过冒充恢复通过。

<!-- review-answer-end -->

### P0-3 两套对话通道并存，新流程走哪条没定
**原文**：453–495（14.2 job + events）、496–518（14.3 旧 SSE 必须保留）、355（`waiting_input`）
**问题**：新接口定义了 `POST /assistant/requests` + `GET /requests/{id}/events`，同时又要求完整保留 `/api/ai/chat/stream` 的四种事件（knowledge / sources / message / done）。但规划流程的 `waiting_input`（暂停等用户回答澄清）在这四种事件里**没有表达方式**。规划走旧 SSE 就无处承载暂停-恢复；走新 job 模式则旧聊天接口只是兼容壳。
**需要**：明确分工（建议：聊天走旧 SSE，规划/核对/采购走 job + events），或统一到一套并给出旧协议迁移期。

**回填解答（2026-11-18）**

**结论：采用双入口、单业务实现的明确分工。** 普通问答、菜谱解释、食物查询继续走旧 `/api/ai/chat[/stream]`；规划、图片、食材核对、采购生成、价格刷新和复盘走 `POST /api/assistant/requests` 或对应领域任务API，随后用job+events读取状态。

旧聊天识别到复杂任务时不在旧SSE里执行等待恢复，而是通过同一assistant service创建一次任务，发送现有协议允许扩展的 `card` 事件（requestId/jobId/resourceRef），再发送原格式done并结束；前端升级先于此功能启用。旧客户端不支持card时只显示任务摘要/跳转提示，不把复杂任务误报成完成。

新事件协议使用单调eventId和JSON envelope，至少包括progress、text_delta、card、waiting_input、completed、failed；waiting_input携带draftId、draftVersion、questions及resumeUrl（服务端白名单生成），并关闭本次流。用户答案走草稿answers命令，不往SSE连接写入。重连从持久事件游标补读或查询资源快照；事件保留默认7天，到期返回快照并说明不可回放。旧done仅表示该次旧响应结束，不表示子任务成功。

<!-- review-answer-end -->

### P0-4 配额计量单位变了，文档没提
**原文**：无（缺失项）。现状是"每人每天 50 次提问"
**问题**：规划任务要跑 collect_requirements → load_context → retrieve_candidates → compute → solve → validate → prepare_draft 七个节点，其中至少 3 个可能调用模型。若仍按"任务"计 50 次，实际模型调用变成 150–350 次/天/人，与现有配额差一个量级。文档 18 节只要求"记录 tokens 与成本"，没有给限额口径。
**需要**：确定配额按"任务"还是按"token 预算"；规划类与闲聊类是否分账。

**回填解答（2026-11-18）**

**结论：采用“按任务分类限频＋输入/输出token硬预算＋外部调用数限制”，不再仅按提问次数衡量费用。** 以下为可配置的开发默认值，不是已确认费用预算：每用户每天聊天50个新请求、规划/调整共5个新任务、复盘2个、图片20张、价格联网20次；文本token总额每日输入200,000、输出20,000，管理员也受成本硬上限约束。

同一逻辑任务的澄清恢复和网络幂等重放不重复占新任务名额，但实际新模型调用消耗token。默认每任务最多3次文本调用（含修复），每次预留max_output_tokens；原文“最多两次修复”服从总调用上限。7个工作流节点不等于7次调用：资料加载、计算、求解、验证均为程序。图片按供应商usage另记账，不假设可与文本token等价换算；价格请求有独立次数限额。

调用前以Redis原子预留本次输入估算与最大输出额度，DB保存reservation及逐次usage；已有账本和活跃预留用于重建计数，计数不可确认时付费调用fail closed。完成后结算差额；失败仍记录供应商实际usage，只有确认未发出的预留可释放。usage缺失记estimated/unknown并保守占用预留，不记零。再增加全站token/外部请求硬上限，M0按成本基线设置；正式外部演示前必须设置，未设置时付费功能关闭。429返回分类errorKey及resetAt。取消/重试不得绕过已发生费用。

<!-- review-answer-end -->

### P0-5 Alembic 基线怎么建没说清
**原文**：390
**问题**：原文只有"用 Alembic 建基线并验证后 stamp，再执行增量迁移；空库初始化和存量升级分别测试"。对已有库，`autogenerate` 会产出巨大且危险的 diff；常规做法是手写 baseline 再 `stamp`。两种路线对 A27（空库安装 + 存量升级都要过）的影响完全不同。
**需要**：指定路线（建议手写 baseline + `stamp head`），并说明如何验证 baseline 与 `information_schema` 一致。

**回填解答（2026-11-18）**

**结论：选择手工审查的legacy baseline＋指定revision stamp，禁止直接对存量库autogenerate或stamp head。** baseline编号约定 `0001_legacy_baseline`，后续新表/扩展为0002起。

流程：①从授权数据库副本只读导出information_schema及SHOW CREATE TABLE，与Java SQL/entity对照，去除敏感数据；②编写baseline upgrade，仅创建旧架构表/索引/约束，不含DROP，不包含新功能表；③在空MySQL执行 `alembic upgrade 0001_legacy_baseline`；④将空库结构与副本结构做规范化diff，比较字段类型/长度、nullable/default、字符集/排序规则、索引列顺序/唯一性、外键及ON DELETE/UPDATE；忽略自增当前值和统计信息，其他差异必须说明；⑤只有通过的存量副本执行 `alembic stamp 0001_legacy_baseline`，stamp不执行建表；⑥空库与存量副本分别upgrade head并验证。

若存量库存在多个历史变体，先做明确的legacy bridge迁移/修复及备份，不能强行stamp假装一致。审阅建议中的 `stamp head` 在已经存在新增revision时会跳过真实DDL，故不采纳。新功能迁移允许autogenerate产生草稿，但必须逐句review，不能当作对生产库的自动修复器。

<!-- review-answer-end -->

### P0-6 `requestKey` 由谁生成没说
**原文**：460、469、473、480、482、483、486，配合 13.2 的 `command_idempotency`
**问题**：十几个写接口都要求 `requestKey`，但没写生成方。前端不改，所有幂等保护等于零；如果服务端按 body hash 生成，"用户确实想再买一次"会被误判为重复。
**需要**：选定生成方（建议前端生成 UUID 并随重试复用），并规定 409 的前端提示文案。

**回填解答（2026-11-18）**

**结论：前端生成UUID，同一未完成用户意图重试复用；新的主动操作生成新键。** 代码核对补充：前端已存在 `src/api/idempotency.js` 的 `submitIdempotently()`，使用 `crypto.randomUUID()` 并在HTTP前写sessionStorage；不是完全缺少基础设施，新增命令应扩展复用它。

统一新命令以 `X-Request-Id` 传输UUID；正文requestKey/clientRequestId若过渡保留，必须与头一致，不一致400。服务端不按body hash创建身份键，body hash只用于检测同键异请求。后台派生命令使用jobId+step+业务对象构造稳定键；前端成功后清pending，主动“再买一次”生成新键。结果未知时保留键并先查状态，不自动创建新键。

409分errorKey：IDEMPOTENCY_PAYLOAD_MISMATCH：“这次提交与原请求不一致，请检查后重新提交”；VERSION_CONFLICT：“方案已更新，请刷新核对后再操作”；REQUEST_IN_PROGRESS：“请求正在处理中，请稍后查看”，保留原键。已存在的基于中文message判断逻辑应迁移到errorKey。DB结果默认保留7天；客户端最长重放6天，过期pending提示核对业务事实，不静默重提。永久业务事件还用cause唯一约束，防止过期重放重复扣库存。

<!-- review-answer-end -->

### P0-7 时区与日界规则不完整
**原文**：169（默认北京时间）、413（`diet_day_status` 用 user_id + date 唯一）、404（`known_ingredient` 无时区字段）
**问题**：5 节说"记录用户时区字段供后续扩展"，但 13.2 的全部新表都没有 timezone 字段。`diet_day_status` 的 `date` 是哪个时区的 date 没说；凌晨 1 点的夜宵归前一天还是当天也没说。这直接影响 A21（周报缺口）和 A26（快照）的一致性。
**需要**：明确 (a) 存放时区的字段位置；(b) 夜宵/跨日的归属规则；(c) `daily_summary` 按 date 还是 datetime 分区。

**回填解答（2026-11-18）**

**结论：首版唯一业务时区为Asia/Shanghai，按自然日零点切分，不实现夜宵自动归前一天。** 补充 `user_profile.timezone`，默认且首版只允许Asia/Shanghai；新事件存occurred_at_utc（DATETIME(6)，约定UTC）与event_timezone快照。known_ingredient的opened/confirmed时间属于时间点按UTC存，到期日期若只有包装日期则保留本地DATE语义，无需每表重复用户时区。

凌晨01:00的夜宵归该日；meal_type=snack不改变日界。用户需要补记前一天时选择实际发生日期/时间，不另设可任意覆盖的record_date。旧无offset的recordTime按Asia/Shanghai解释，新接口带offset，服务端据业务时区计算record_date并校验，禁止由客户端随意拼写。

daily_summary按 `(user_id, summary_date DATE)` 唯一键汇总，不做物理分区；diet_day_status同一DATE规则，周报为当地周一00:00至下周一00:00的左闭右开时间段。旧MySQL本地datetime保留口径，在DTO边界转换；不能给所有旧datetime直接加UTC标签。未来更换时区需单独版本化迁移，不追溯重写旧日期。

<!-- review-answer-end -->

### P0-8 现有 Python 测试没有数据库集成基础设施
**原文**：778（21.3 只说"新增 pytest/httpx 集成测试"）
**问题**：21.1 中 A05、A06、A07、A14、A15、A16、A24 都是并发/幂等/事务用例，需要真实 MySQL。现有 138 个测试是纯 unittest（11 秒跑完，不连库），没有 fixture 体系、没有隔离 schema、没有 testcontainers。这决定 M1 能否开工。
**需要**：指定测试库方案（testcontainers-mysql / 独立 schema / 每次重建），以及 CI 中如何避免连生产库。

**回填解答（2026-11-18）**

**结论：M1第一项交付固定Docker Compose集成测试环境，使用真实MySQL8与Redis，不用SQLite模拟事务。** 不再将testcontainers作为另一并列路线，减少Windows/Docker差异；本地和CI复用 `deploy/compose.test.yml`。

测试容器使用临时卷、隔离账号；pytest按runId/xdist worker分配 `orange_diary_test_<runId>_<worker>` schema，由fixture创建并跑Alembic。事务/并发用例每连接独立Session并显式commit，不能用包住全用例的外层rollback掩盖真实行为；测试后按已登记schema清理。

安全双门槛：APP_ENV=test且DB名符合test前缀，否则拒绝初始化和任何清理；TEST_DATABASE_URL独立提供，缺失不能回退DATABASE_URL。CI服务容器仅绑定作业内网络，账号不访问业务库。没有Docker时纯单测可跑、集成测试标skip且报告“未完成集成验收”，不能显示M1完成。A05/06/07/14/15/16/24必须在真实MySQL重复并发验证。

<!-- review-answer-end -->

---

## P1 定义缺失：写了但没定义，会导致实现不一致（9 条）

### P1-1 "私人厨师"全文未定义
**原文**：24、172–195（第 6 节标题即"多约束饮食规划与私人厨师"）
**问题**：出现 20 余次，1.1 说是"交互入口"，6.2 又说它支持做法步骤、设备替换、份量缩放、菜品替换。到底是 (a) 人格化对话角色、(b) 菜谱生成与烹饪指导能力、(c) 整个规划模块的对外名称？三者工作量差数倍，且决定前端是"一个对话框"还是"一套页面"。
**需要**：给出定义并说明它与普通聊天的边界。

**回填解答（2026-11-18）**

**结论：私人厨师是“菜谱选择与烹饪指导能力集合”的对外名称，采用选项(b)。** 由同一助手调用食谱检索、份量缩放、做法/设备适配和替换建议，不是独立人格智能体，也不是整个规划模块的别名。

输入可以是需求文字、已确认食材或食材图片草稿；输出为食谱候选、步骤卡、替代与来源。营养目标和多天组合交给规划服务；价格/购买交给采购服务。前端复用AiChatView和食谱详情卡，不另建聊天系统或单独“厨师后台”；普通问答不自动创建计划/采购事实。

<!-- review-answer-end -->

### P1-2 CP-SAT 的必要性未论证
**原文**：120、199
**问题**：6.3 说可以先做"确定性贪心 + 回溯"作 baseline，发布前必须完成独立验证；但没给候选食谱库的规模假设。如果只有几十道菜，贪心足够；CP-SAT 的整数缩放、离散份量建模是为几千道菜准备的专门工程。
**需要**：给出首版候选库规模（食谱数 × 份量档位数），据此决定是否首版就上 CP-SAT。

**回填解答（2026-11-18）**

**结论：首版保留CP-SAT，但不是因为只有“大库”才需要它。** 约束冲突、跨天多样性和包装预算组合在小库也会产生组合搜索；库大小本身不能证明贪心足够或必须上求解器。

首版数据边界：30道审查食谱可做内部联调，正式M4验收目标100道有效主菜/搭配模板（最低60道且按约束评测有足够候选）；每食谱最多3个经合理份量审查的档位。每餐先确定性筛到最多20个候选，共≤60个份量选项，7天×3餐最多21个餐位，约1260个选择变量量级，而非对全库任意连续克重求解。

优化器封装SolverProvider，贪心/回溯只作基线与对照；CP-SAT设5秒求解时间预算并限制候选/变量规模，返回FEASIBLE_NOT_OPTIMAL/INFEASIBLE/TIMEOUT。5秒是设计超时，不是性能实测；M4在标注机器上记录P50/P95。候选不足时报告“当前食谱库不足”，不自动生成未审食谱填满。最终可行性由独立校验器验证，不由算法品牌保证。

<!-- review-answer-end -->

### P1-3 核对会话没有提问上限
**原文**：237
**问题**：7.2 只设计"只询问相关未知、过时或数量不明的食材"，没设上限。用户选一道四菜一汤，可能要回答 8–12 个"有没有/够不够"。演示能扛，真实使用会直接劝退。
**需要**：设单次核对上限（建议 5 个），并规定超限的降级方式（按类别批量确认 / 直接建议购买 / 标"待核对"跳过）。

**回填解答（2026-11-18）**

**结论：单次屏幕最多展开5个具体食材问题，主动追问最多2轮，其余保留待核对。** 5个是交互上限，不是需求项被截断；条目仍完整列在折叠清单，用户可主动继续编辑。

先按可执行性与影响排序（主食/蛋白质、关键缺口，再配料），油盐等常备品可按组确认；批量“这些都有”必须是用户明确动作。两轮后给“先用当前信息继续”“稍后核对”，未知仍unknown，采购量只能条件展示。available_only下未知关键项不满足硬约束，允许选别的候选或解释暂不能确认，不以超限为由直接断言缺少并建议全买。

<!-- review-answer-end -->

### P1-4 未知项怎么进预算上界，两处规则冲突
**原文**：205（strict 预算用价格上界校验）、277（未知项给已知小计与未知项数量）
**问题**：食材数量 unknown 时，价格上界从哪来？若按"买满包装"推算上界，可能直接把可行方案判成超预算；若不给上界，strict 预算就无法认证。两节规则没有接上。
**需要**：明确 unknown 一律 `budgetStatus=unverified`（推荐），还是给出显式的保守上界公式。

**回填解答（2026-11-18）**

**结论：统一为任一影响采购金额上界的数量、规格、可分配库存、费用或价格未知，strict预算即unverified。** unverified不是over_budget，也不是infeasible；不能把信息不足误判成不可行。

只有采购缺口、包装规格和适用价格上界均已确定时，才计算 `Σ(包装数×对应包装价格上界)+已纳入的配送等费用` 并判断within_budget/over_budget。配送未计入时必须把预算范围写成“食材商品金额，不含配送”；若用户预算含配送而该值未知，则仍unverified。

用户可明确选择“假设家中没有，全部购买”重新建立场景，但这只是带假设的scenario结果，不更新真实库存；卡片标明scenario_assumed，不与confirmed_budget_verified混用。

<!-- review-answer-end -->

### P1-5 `meal_observation` 迟到确认要重算哪些天，没定
**原文**：290–292
**问题**：9.1 要求汇总查询防止 observation 与已关联记录重复计入，也说要"对修改/删除重算影响日期"。但 `daily_summary` 是物化汇总，如果 observation 三天后才确认匹配到正式记录，要重算哪几天、如何避免与已计入的部分重复，没有规则。
**需要**：给出 observation 状态迁移时的重算范围规则（含 `observation_record_link` 建立/撤销两个方向）。

**回填解答（2026-11-18）**

**结论：建立统一有效摄入视图和按受影响日期全集重算的事务规则。** observation从draft→confirmed_unmatched→resolved/retracted。draft不计入；confirmed_unmatched仅在已确认估算存在时进入estimated小计；没有数值只降低完整度。resolved由正式diet_record计入，不再加原估算。

将observation分解成多个正式记录时，必须一次性建立完整link集合并将observation改resolved，不允许“部分正式记录＋完整原估算”同时有效；不能完整拆解时先留在unmatched。重算日期集合为变更前后observation的record_date，加所有新增/修改/删除/解除关联diet_record的旧、新record_date。确认时已是三天后也重算进食当天，而非确认当天；跨日修改则两天都算。

锁定同一用户健康写入行后，在短事务内完成状态/link/记录修改，按唯一日期集合排序重建daily_summary。保留旧confirmed小计，新增estimated小计及coverage字段，total若返回两者合计必须标估算口径，不能把估算混入旧精确字段。解除link必须走命令：若恢复原observation则同时撤销该解析产生且尚有效的正式记录；若保留正式记录，则observation保持不计入/标retracted，不能恢复双计。事务提交后outbox将相交期间的周报标stale、相关未执行计划标needs_review，不自动覆盖已采纳方案。

<!-- review-answer-end -->

### P1-6 工具清单内部矛盾
**原文**：380
**问题**：表里把 `adopt_plan / confirm_record / confirm_inventory / confirm_purchase` 列为"用户确认命令"，权限栏写"经认证的命令 API，**不交给模型自主执行**"。既然不交给模型，为何注册进工具清单？如果注册了但被策略拦截，拦截发生在哪一层（工具注册表？节点？）没有说明。
**需要**：要么从工具清单移除、只在 API 层暴露；要么说明"注册但运行时拒绝"的机制与测试用例。

**回填解答（2026-11-18）**

**结论：从模型工具注册表移除全部用户确认命令。** 第12.3节最后一行应归类为“业务命令API”，不是LLM可见tool；schema和运行时allowlist都不含adopt/confirm/purchase等命令。仅注册只读、计算和草稿工具。

前端用户确认→认证API→归属/版本/幂等校验→service事务。自然语言“就按这个买吧”首版只返回确认卡，不由模型直接执行写入。验收检查发给供应商的tools列表、注册表单测和恶意tool_name请求，均不能发现/执行确认命令；直接访问API仍须正常鉴权。

<!-- review-answer-end -->

### P1-7 分页命名混搭，理由未给
**原文**：449
**问题**：新接口要求"page / pageSize 入参"但"records / total / current / size 出参"——入参 camelCase 现代风格，出参是 Java 分页对象字段名。两者并列会让前端需要两套映射，且 `current` 与 `page` 语义重复。
**需要**：确认是否刻意沿用（为了前端复用），还是笔误。

**回填解答（2026-11-18）**

**结论：原文是兼容倾向但不值得让新接口混搭，现统一新接口命名。** 旧接口入参page/size、出参records/total/current/size保持不变；新接口入参page/pageSize，出参 `{records,total,page,pageSize}`，page从1开始、pageSize默认20/最大100。

前端新增统一normalizePageResponse，旧DTO在API适配层转成page/pageSize给页面；新页面只认标准内部结构。旧与新端点各有OpenAPI schema，不给同一字段加current/page两个同义出口；不把此变化反向应用到旧接口造成破坏。

<!-- review-answer-end -->

### P1-8 复盘两个执行率并存，对外口径未定
**原文**：342
**问题**：`confirmedExecutionRate = executed / (executed + explicitlySkipped)` 与 `executionCoverage = (executed + skipped) / dueAdoptedMeals` 同时展示。前者反映"有反馈的餐次里执行了多少"，后者反映"有多少餐次有反馈"。用户看周报时看哪个？没有主指标。
**需要**：指定主指标与副指标，并给出文案。

**回填解答（2026-11-18）**

**结论：主指标是“已确认餐次的执行率”，副指标是“执行反馈覆盖率”，样本不足时不突出百分比。** 主指标文案：“已反馈的5餐中，完成4餐（80%）”；副指标：“本周计划9餐，已反馈5餐，另4餐待确认（覆盖56%）”。

只有dueAdoptedMeals>0、反馈≥3餐且coverage≥60%时主卡展示百分比；否则主卡显示“反馈不足，暂不评价执行情况”，保留完成/跳过/待确认计数。该60%/3餐是首版展示策略默认，不是健康科学阈值。无采纳计划显示“本周没有可统计的饮食计划”。绝不写“本周整体执行率80%”掩盖未知4餐。

<!-- review-answer-end -->

### P1-9 首版不做的工程边界缺失
**原文**：41–48（1.3 只写了功能边界）
**问题**：1.3 列的都是功能边界（不做疾病饮食、不做语音、不做多人目标），没有工程边界：不做多实例、不做 i18n、不做移动端原生、不做离线模式、不做多租户。缺少这些，接手模型会自行扩展。
**需要**：补一节"工程范围外"，明确不做项。

**回填解答（2026-11-18）**

**结论：补充首版工程范围外。** 不做多地区/多机高可用、Kubernetes/服务网格、微服务拆分、多租户组织权限、原生移动App、i18n、多时区开放设置、离线同步、可穿戴硬件/智能冰箱接入、电商下单支付、持续学习自动训练、任意插件市场或多智能体自治。

首版部署为单主机、1个Web实例（1个进程）、1个worker、1个调度器；仍须正确处理不同连接并发、重复投递和重启，不能以单机为由省略事务/幂等。多账号隔离与管理员权限必须保留，它们不同于组织多租户。桌面/移动浏览器响应式在范围内。突破这些边界需新需求，不由接手模型自行扩展。

<!-- review-answer-end -->

---

## P2 验收不可判定：写了标准但无法执行（6 条）

### P2-1 A01 与 M3 的完成定义含主观词
**原文**：739（"可推荐其他合理菜"）、724（"生成可信做法和确认卡"）
**问题**："合理""可信"没有判定主体和判定方式，无法写进自动化测试，也无法验收。
**需要**：替换为可检查的条件（如"返回 ≥2 个候选，且至少 1 个包含未记录食材，且过敏过滤通过"）。

**回填解答（2026-11-18）**

**结论：替换主观词为固定fixture和可检查输出，模型效果与规则正确性分开验收。** A01固定fixture中提供≥3道满足其余硬约束的菜，其中1道只需已知鸡蛋/番茄，2道包含未记录食材；prefer_available必须返回≥2候选，至少1候选含未记录食材，状态为needs_check，所有硬约束通过。若数据本身不具备该前提，不强求凑两个候选，返回candidate_pool_insufficient。

M3做法卡要求：recipeId/version与来源可查；ingredient列表与结构化食谱一致；步骤顺序完整、关键设备/可见用量不丢；缩放后的用量由程序计算；图片结果待确认；预览不写库存。自动测试用固定食谱/供应商fixture核对，另按P2-2进行人工评价；不能只看文字流畅就叫可信。

<!-- review-answer-end -->

### P2-2 "引用支持率"由谁判定没说
**原文**：772
**问题**：度量里列了"引用有效/支持率"。有效可以程序校验（evidenceId 是否存在）；支持率是"结论是否被原文支持"，只能人工或模型判定，两种方式的成本和可信度完全不同。
**需要**：指定判定方式（建议人工标注 50 条子集 + 记录 κ 值），并明确不做全量自动判定。

**回填解答（2026-11-18）**

**结论：引用“存在有效”由程序全量检查，“结论获支持”采用人工留出集判定，不由生成模型自评分作为最终结论。** M3准备50条有来源的代表性回答，拆成可核对的事实主张；标签为supported/partial/unsupported/unclear，明确适用条件是否保留。主张级支持率为supported/全部需证据主张，另报告partial和unclear，不只挑有利样本。

理想由用户指定的第二名评审者与开发者独立标注，记录原始标签、一致率和Cohen κ，再讨论分歧。用户没有提供第二评审者时先单人标注，明确single_rater、κ不适用，不能虚构第二人或自动造κ；此时仅作内部候选，正式健康问答质量验收需用户抽查确认。模型评审可辅助定位问题，不能替代最终人工证据。

<!-- review-answer-end -->

### P2-3 "候选多样性"无指标定义
**原文**：772
**问题**：列为度量项，但没有公式。是菜系熵、食材重合度倒数，还是"连续两天不重复主菜"？
**需要**：给出公式与阈值。

**回填解答（2026-11-18）**

**结论：首版用可复算的主菜重复指标，不引入菜系熵等复杂指标。** 对一个计划内已采用的主菜recipe_family_id序列，`unique_main_ratio=不同family数量/主菜餐次数`；family合并同菜不同份量。另统计同一meal_type相邻自然日的主菜family重复次数 `adjacent_repeat_count`。

7天计划默认软目标unique_main_ratio≥0.70、adjacent_repeat_count=0；只作目标，不凌驾过敏、预算或候选不足。指定“重复做这道菜”或允许批量备餐的餐次标exempt，并同时显示原始值和非豁免值。1天/单餐多候选推荐要求family不重复；主菜为零则指标null。验收固定充足候选数据能达到目标，受限数据必须标diversity_degraded及原因，不因偏好重复而判硬失败。

<!-- review-answer-end -->

### P2-4 效果阈值指向一个不存在的基线
**原文**：772（"其他效果阈值需根据真实基线设定并记录"）
**问题**：基线尚未测量，这条等于暂时没有标准。M3–M7 的完成定义里却已经写了"失败可降级"这类要求，两者不匹配。
**需要**：约定 M0 阶段先测基线，再回填阈值；或明确"首版只跑通不设达标线"。

**回填解答（2026-11-18）**

**结论：分为M0工程基线、M3前领域基线和发布门槛，不能在M0假装测到尚未存在的规划器。** M0跑现有接口/测试/检索基线和小规模模型能力验证；M3建立开发集与冻结留出集，M4为规划补确定性基线。每阶段实测耗时/成本，才回填可实现的性能SLO；未测则写未测，不默认通过。

先确定与基线无关的发布门槛：A类硬约束/归属/事务/幂等fixture必须全通过；模型输出schema最终校验通过率100%（失败任务可显式失败，不得用拒绝所有请求刷通过率）；50条回答的引用ID有效率100%、人工主张支持率目标≥90%，严重过敏/权限/伪造来源案例为0。任务完成率另外报告，不与格式率合并。

90%是拟定工程发布目标，不是已验证准确率或临床指标；不足不开放相应健康建议功能，只保留明确失败/定性说明。性能和成本上限需在M0/M3报告中填写实际机器/模型和数据规模，未填不可宣布M8性能验收完成。用户可调整目标，但调整要记录理由，不在看到留出结果后偷偷调低。

<!-- review-answer-end -->

### P2-5 没有验收责任人与回滚窗口
**原文**：715–731（M0–M8）、705–713（19.3 回滚）
**问题**：完成定义都是技术描述，谁判定通过（自测 / 用户验收 / 第三方）未定。19.3 说"回滚前先判断 Python 是否写入旧 Java 不认识的数据"，但没给回滚窗口的时间边界（切换后多久内允许回滚、之后冻结）。
**需要**：指定验收人与回滚截止条件。

**回填解答（2026-11-18）**

**结论：开发模型负责提供自动化证据，用户是产品最终验收人；生产发布由用户另行决定。** 每阶段“工程自测通过”和“用户验收通过”分别记状态。没有用户回复不能冒充用户已验收，但可继续不依赖该验收的可逆开发。

首版默认生产切换方案：T0开始只读维护/停止旧写入，2小时技术验证窗口内新后端也不开用户写入；验证失败可回旧服务。用户确认后开放Python写入，该时点Twrite即结束无损直接回切窗口，而不是“到第24小时仍可随意恢复旧备份”。Twrite后故障先关闭新增写入、保留快照并按兼容矩阵前向修复；回切需要逐条确认新增数据、DDL兼容和任务事实，另作方案。24小时为重点观察期，不是备份覆盖许可。上述2小时为排程默认，不保证切换一定能在2小时完成；只读阶段可延长并明确告知。

<!-- review-answer-end -->

### P2-6 人工演示脚本未标注失败分支的预期表现
**原文**：827–831
**问题**：演示要求同场展示 Java 停机、图片不可用、价格缺失、检索无证据、约束无解、版本冲突。这些是很好的设计，但没写每种情况下**用户应该看到什么**，无法判断"表现正确"。
**需要**：为 6 个失败场景各写一句期望文案/状态。

**回填解答（2026-11-18）**

**结论：六个分支的用户表现固定如下，可用错误码/状态断言，文案允许等义优化。**

| 场景 | 后端/页面预期 |
|---|---|
| Java停机 | 没有故障文案；登录、记录、社区、PDF和助手使用Python完成，监测无Java回调 |
| 图片服务不可用 | VISION_UNAVAILABLE；“图片暂时无法分析，你可以输入食材名称”，显示重试/文字录入，不入库存 |
| 价格缺失 | priceStatus=unknown、budgetStatus=unverified；“部分食材暂无可靠价格，已知小计…，完整预算待核实”，不显示完整合计 |
| 检索无证据 | knowledgeStatus=no_evidence；“暂未找到支持这项建议的资料”，不展示来源，允许换问法；与unavailable不同 |
| 约束无解 | INFEASIBLE或CANDIDATE_POOL_INSUFFICIENT；“当前条件下暂未找到可用方案”，列冲突/缺库，提供用户可选放宽条件，不擅自放宽 |
| 旧版本提交 | HTTP409、VERSION_CONFLICT；“方案已更新，请刷新核对后再操作”，不覆盖新计划，不重复预留/扣减 |

<!-- review-answer-end -->

---

## P3 事实与表述风险（7 条）

### P3-1 `VISION_MODEL` 写了具体值，与"待验证"矛盾
**原文**：669（`.env` 样例 `VISION_MODEL=qwen3.8-omni-flash`）、143（"精确 model ID、endpoint、鉴权和收费尚待验证"）
**问题**：同一份文档里，配置样例给了确定值，正文说未验证。其他未定项都写 `<to-verify>`，这里没有。容易被当成可直接使用。
**需要**：改成 `<to-verify>`，并在 4.2 增加"验证通过后回填"的明确步骤。

**回填解答（2026-11-18）**

**结论：保留用户指定模型为需求值，运行配置改占位值并默认关闭。** 本答复更正示例为 `VISION_REQUESTED_MODEL=qwen3.8-omni-flash`、`VISION_MODEL=<verified-provider-model-id>`、`VISION_ENABLED=false`；requested字段只记录意向，不传供应商。

M0查对应供应商官方文档/控制台确认精确标识、区域endpoint、鉴权和图片格式，再在已具备凭证与调用授权时做一张测试图验证，记录requested/response模型、时间、usage和限制。验证完成才回填VISION_MODEL并启用；失败标blocked，不自动换近似名称，也不在本次回填中执行付费调用。

<!-- review-answer-end -->

### P3-2 `.env` 样例用的文本模型名与正文推荐名不一致
**原文**：665（`TEXT_MODEL=deepseek-v4-flash`）、135（"推荐调用名为 `deepseek-flash`"）
**问题**：正文说推荐用新名，配置样例给的是旧名。虽然有"旧名仍接受"的说明，但样例是实施者最先复制的东西。
**需要**：统一到推荐名，或在样例行尾注明"旧名兼容"。

**回填解答（2026-11-18）**

**结论：官方DeepSeek直连场景的运行配置统一推荐 `TEXT_MODEL=deepseek-flash`，并分开记录 `TEXT_REQUESTED_MODEL=deepseek-v4-flash`。** 这表示用户选择Flash系列，不能声称锁定旧模型权重。

第三方代理若只接受旧别名，由provider映射表配置实际ID并通过能力验证，不能强行套官方别名。示例复制后仍需合法密钥、验证状态和能力开关；不在未配置环境自动发请求。

<!-- review-answer-end -->

### P3-3 DeepSeek 模型名三处口径不一
**原文**：33（1.2 列为"已确认的产品决定"）、135（"实施时核实供应商/代理是否相同"）、801（23 节"用户代理和权限未知"）
**问题**：一处"已确认"，两处"待核实"。另外 135 关于"请求由 DeepSeek-V4.1-Flash-1017 承接"这句没有标注来源与抓取日期。
**需要**：统一降级为待核实，并记录一次真实调用的 requestedModel / responseModel 作为证据。

**回填解答（2026-11-18）**

**结论：区分“用户选择已确认”和“供应商运行事实待验证”，不把整个模型选择降成未定，也不把文档说明当真实调用证据。** 用户指定DeepSeek Flash已确认；精确路由、responseModel、权限和账户价格需M0验证。

原设计第4.1节已经标2026-11-18并在紧接段落给出官方首页和pricing链接，因此“完全没来源/日期”不成立；但应补成更清晰的证据记录：`checkedAt=2026-11-18, source=https://api-docs.deepseek.com/, evidenceType=public_documentation, liveCallPerformed=false`。此前查看页面说明旧名由更新Flash承接；该信息可变，实施时重查，不能虚构冻结版本保证。

实际联调另记requestedModel、configuredModel、responseModel（供应商未返回则null）、providerHost、testedAt、traceId和usage，不存密钥。本次没有live调用，不回填假responseModel。模型名不一致不一定错误，但必须通过映射解释。

<!-- review-answer-end -->

### P3-4 23 节把阻塞项归入"不阻塞"
**原文**：806（身体目标公式"定量规划上线前完成"）、805（食谱来源"待补"）、813（"不要求用户在开发前一次决定所有供应商细节"）
**问题**：目标公式是 M4 的硬前置，食谱库是 M3 的硬前置。这两项被放进"不阻塞"框架里，会让人误以为可以先做规划再补数据。
**需要**：把阻塞项与非阻塞项分开列，阻塞项标注"卡在哪个阶段"。

**回填解答（2026-11-18）**

**结论：重新按阻塞阶段分类，“不阻塞全部开发”不等于“不阻塞对应功能上线”。**

| 依赖 | 卡住的阶段/能力 | 仍可继续 |
|---|---|---|
| DB结构/测试环境 | M1事务与迁移验收 | schema/纯领域逻辑草稿 |
| 食谱来源授权/规范数据 | M3正式厨师与M4有效规划验收 | 30道fixture内部联调、旧业务迁移 |
| 目标公式/适用边界/营养策略 | M4定量个性化规划发布 | 非量化搭配、候选查询、采购UI |
| 文本模型兼容/凭证 | M3真实AI联调 | mock契约与确定性业务 |
| 图片供应商验证 | 图片能力发布 | 文字录入和其他模块 |
| 搜索来源与费用配置 | 真实价格查询、strict完整预算验证 | 价格未知状态、用户手填实购价 |
| 人工留出评测/保留政策 | M8对外健康建议及图片上线 | 本地功能与自动化测试 |

每阶段报告要列blockedBy，不能用“后面补数据”把功能标已验收。

<!-- review-answer-end -->

### P3-5 要求旧文档降级，但没指定文件与标记方式
**原文**：16（0 节第 8 条）
**问题**：规定 `AI_RAG_DEVELOPMENT_PLAN.md`、`EXECUTION_PLAN.md`、README 及旧报告中"Java 拥有业务"的描述属于旧架构，但没说哪些文件要改、改成什么、是否归档。接手模型读旧文档仍会混乱。
**需要**：给出待处理文件清单与处理方式（顶部加"已废弃，见 XXX"横幅 / 移到 docs/archive/）。

**回填解答（2026-11-18）**

**结论：实施M0时在明确文件顶部加架构状态横幅，不搬动/删除历史证据。** 本次用户只要求回填问题文件，因此不擅自修改以下文件；列出后续动作即可。

| 文件 | M0处理方式 |
|---|---|
| 根README.md | 更新当前实现/目标架构、唯一入口、配置加载事实；顶部链接主设计及本回填 |
| AI_RAG_DEVELOPMENT_PLAN.md | 顶部加“历史方案：Java职责分配已被完整Python目标替代；检索技术和历史结果供参考” |
| EXECUTION_PLAN.md | 同样标历史任务计划；新任务状态只写docs/IMPLEMENTATION_STATUS.md |
| docs/M0_BASELINE_REPORT.md～M7_STABILITY_REPORT.md及相关M5报告 | 保留原测试日期/环境/结果，顶部标“历史测量，不证明新后端通过” |
| docs/IMPROVEMENTS_2026-11-18.md | 保留修复事实，标该报告针对旧架构时期 |
| docs/PYTHON_FULL_BACKEND_AND_DIET_ASSISTANT_DESIGN.md | 后续合并本回填为v1.1，更新变更记录；本次不改 |

横幅统一链接相对路径正确的主设计和DESIGN_REVIEW_GAPS.md；现阶段尚未合并的条款以本文件“回填解答”的具体规则覆盖v1.0对应条款。用户最新指令优先。

<!-- review-answer-end -->

### P3-6 两个 `main.py` 的表述会误导
**原文**：67（"`app/main.py`，`main.py` 为现有入口之一"）
**问题**：根目录 `main.py` 实际只有 3 行 `from app.main import app`，是 uvicorn 转发壳。"入口之一"的写法会让接手模型以为要合并两个入口。
**需要**：改为"根 `main.py` 为转发壳，唯一真实入口是 `app/main.py`"。

**回填解答（2026-11-18）**

**结论：采纳措辞修正。** 代码核对：根 `main.py` 只有说明与 `from app.main import app`，是兼容 `uvicorn main:app` 的转发壳；应用工厂、路由与lifespan唯一真实实现为 `app/main.py`。

后续启动命令统一推荐 `python -m uvicorn app.main:app ...`，保留根壳以兼容现有IDE/脚本；无需“合并两个应用”，不能在根文件再次建FastAPI实例或注册路由。测试两种导入返回同一app对象。

<!-- review-answer-end -->

### P3-7 AGPL 项目只能"学设计"，意味着核心模块要从零写
**原文**：789–793（Mealie、wger、Tandoor 均 AGPL，`HowToCook` 需逐文件审查第三方素材）
**问题**：三个最相关的参考项目都不能复制代码，等于求解器、采购合并、单位换算全部自研。第 6 节的 CP-SAT 建模和 8.1 的单位/包装合并是领域内最容易踩坑的部分，文档没有为"不能借鉴代码"带来的人力成本留出说明。
**需要**：明确这几个模块为"纯自研"，并调整阶段预期。

**回填解答（2026-11-18）**

**结论：部分采纳工程成本提醒，但纠正“AGPL都不能复制”的判断。** AGPL允许在条款下使用、修改和分发，并非绝对禁止；是否符合项目发布/开源方式尚未确认。Tandoor还带Commons Clause，需独立处理，不能与普通AGPL等同。

首版设计默认不复制/改编这些项目代码到本项目，避免无意引入未评估义务；使用公开业务概念、独立编写领域算法，并保留来源记录。食谱数据授权另审，不以代码许可证代替数据许可。OR-Tools等正常库按各自许可证引入，不等于求解器底层从零实现。

需要独立实现的是业务约束建模、食材匹配/单位包装、采购分摊和状态一致性；M0拆任务时单独计工作量，M4/M5分别预留集成与边界验证，已纳入P0-1量级，不因看过参考源码就宣称“现成接入”。用户未来选择满足AGPL的发布方式或取得授权后可重新评估复制，不能擅自认定商业项目一律不可用。

<!-- review-answer-end -->

---

## P4 工程细节遗漏：会踩坑但好补（6 条）

### P4-1 `asyncmy` 验证失败的退路未定
**原文**：118（"MySQL async 驱动优先验证 asyncmy"）
**问题**：只写了"优先验证"，没写验证不通过怎么办（回退 aiomysql？还是 sync SQLAlchemy + 线程池？）。这决定 SQLAlchemy 的 session 类型，影响全部 repository 签名，必须开工前定。
**需要**：给出 fallback 决策点与判定时间（建议 M1 第一周内定）。

**回填解答（2026-11-18）**

**结论：在M1开始的驱动spike中锁定唯一业务DB模式，最多1个工程日完成初选；不允许每个repository自行选。** 优先SQLAlchemy AsyncSession+asyncmy，验证Windows Python3.13安装、Linux镜像、MySQL8连接、事务隔离、FOR UPDATE、并发、Decimal/datetime和连接取消。

若asyncmy无法通过，验证同API模式的aiomysql；若仍有阻塞，在新增业务repository展开前统一采用同步SQLAlchemy+PyMySQL，数据库段通过受控线程执行。不能在async def里直接跑阻塞SQL；同步Session在同一worker线程内创建/使用/关闭，不能跨线程传递。

失败原因、所选驱动和完整spike结果写M1 ADR并锁依赖。回退后更新第3/16节和测试fixture，保持一个主业务repository接口，不为两种模式长期各维护一套。1日是时间盒，不通过则M1数据库阶段blocked，不能带着未定方案全面编码。

<!-- review-answer-end -->

### P4-2 Celery 与 async session 的桥接方式未提
**原文**：124
**问题**：Celery worker 是同步进程，SQLAlchemy async session 需要 `asyncio.run` 包装或独立同步 engine。文档没说用哪种，这会造成两套 session 生命周期和两套事务写法。
**需要**：指定方案（建议 worker 内独立同步 engine，或统一 asyncio.run 包一层并禁用异步 client 复用）。

**回填解答（2026-11-18）**

**结论：为保留现有异步模型网关，首版选择“Celery task外层同步＋每次task一个asyncio.run”，不引入另一套同步业务repository。** 主DB模式按P4-1确定；默认AsyncSession情况下，任务async入口内创建本任务engine/sessionmaker、AsyncClient和Redis async client，finally关闭/dispose，禁止跨任务/跨event loop复用。

Web使用自己的lifespan连接池；Celery进程不继承Web已初始化连接，使用prefork/solo，不用eventlet/gevent。业务service函数相同，事务范围不跨模型等待。每个任务用一个event loop，CPU求解在受控执行器，循环结束前await所有子任务。单机首版接受创建连接资源开销，测量后再优化为维护单一loop的专用worker方案。

若P4-1统一回退同步DB，则worker同样使用该同步repository，不再另开异步DB；模型调用仍可在task异步壳内，阻塞DB通过受控线程段执行。验收连续任务、超时/取消、worker重启，不出现“attached to a different loop”或连接泄漏。

<!-- review-answer-end -->

### P4-3 `/ready` 与"AI 能力独立展示"缺接口
**原文**：651
**问题**：`/ready` 表示 DB/Redis 就绪，"AI 能力状态独立展示"——但没定义 qdrant / ollama / 图片 / 搜索未就绪时 `/ready` 是否失败，也没给展示状态的接口。
**需要**：给出 `/ready` 的判定集合与一个独立的 `/capabilities` 响应结构。

**回填解答（2026-11-18）**

**结论：就绪判定与可选能力分离，并新增明确接口。** `/health`只检查进程可响应，不泄露配置；`/ready`要求数据库可查询且schema达到当前required revision、Redis认证会话可用，否则503。MinIO/Qdrant/Ollama/文本/图片/搜索/worker不是基础就绪硬条件，它们各自影响对应能力，不能让图片故障阻止登录。

`GET /api/capabilities`需登录，code=0返回 `{"business":{"status":"ready"},"assistant":{"status":"ready|degraded|unavailable","reasonKey":null},"knowledge":{"status":"..."},"vision":{"status":"..."},"priceSearch":{"status":"..."},"uploads":{"status":"..."},"backgroundJobs":{"status":"..."}}`。每项可带lastCheckedAt，不暴露密钥、内网URL或模型供应商细节。单项status取ready/degraded/unavailable/disabled/unknown。

检查只用廉价健康探测、最近成功状态和worker心跳；“已配置”不等于ready，未验证为unknown，不能每次轮询发付费模型请求。详细依赖/版本仅管理员诊断接口可见。worker不可用时新长任务返回503/JOB_WORKER_UNAVAILABLE，已有任务可查，不无限堆积显示正在处理。

<!-- review-answer-end -->

### P4-4 unittest 与 pytest 并存的运行方式未定
**原文**：127、778
**问题**：要求"保留现有 unittest discover 兼容"同时"新增 pytest"。两套并存时 CI 跑哪条、pytest 是否也收集 unittest 用例、覆盖率怎么合并，都没写。
**需要**：指定 CI 命令（建议 pytest 同时收集两边，保留 unittest discover 作为回归验证）。

**回填解答（2026-11-18）**

**结论：pytest为CI唯一覆盖率汇总入口，保留unittest作为轻量兼容命令，不将重复运行结果相加。** 新增锁定的requirements-dev或等效依赖，包含pytest、pytest-asyncio、pytest-cov和所需驱动。

单测CI：`python -m pytest tests -m "not integration and not live" --cov=app --cov-report=xml`，pytest同时收集现有TestCase；集成CI启动隔离compose后执行 `python -m pytest tests -m integration --cov=app --cov-append --cov-report=xml`，仅同一job工作区允许append；分job则上传独立coverage数据再combine。live默认不在普通CI执行。

`python -m unittest discover -s tests -p "test_*.py" -v`继续用于兼容回归，可在迁移阶段单独跑，但不再次累计覆盖率。pytest使用strict markers，未标记网络测试不得混进离线组；需要数据库的用例放integration目录并显式marker。未具备Docker的CI不能把integrations全skip后宣称整体绿色验收。

<!-- review-answer-end -->

### P4-5 资源删除接口缺失
**原文**：474–483
**问题**：`known-ingredients`、`shopping-items` 只有 POST/PATCH，没有 DELETE。如果设计意图是用状态迁移代替删除（合理），需要说明，否则实现者会自行加接口。
**需要**：确认"不提供物理删除"为设计决定。

**回填解答（2026-11-18）**

**结论：业务食材/采购记录默认不提供物理删除，补齐显式归档命令；隐私删除另有专门路径。** 新增 `POST /api/known-ingredients/{id}/archive`（expectedVersion、reason、requestKey），有活跃预留则409并列影响餐次，先释放/调整后归档；用完/丢弃是ledger事件，不等于归档。

采购条目使用PATCH state=skipped/archived处理未购项；已购只允许purchase-reversal补偿，不能删除来抹掉已发生库存变化。查询默认排除归档项，includeArchived只对本人可用。全部命令有归属/版本/幂等。

图片原件、个人资料或账号的用户隐私删除不能被“流水不可删”永久阻止：需独立、经认证的删除/匿名化任务，按保留政策清理资产和可识别内容，必要审计保留最小去标识信息。首版可先提供本人删除图片接口 `DELETE /api/assistant-media/{id}`，同步撤销访问、后台清理对象与缓存，不把业务归档当作已经满足数据删除。

<!-- review-answer-end -->

### P4-6 语料扩容的评估预算未给
**原文**：322–326、701
**问题**：10.2 承认"新菜谱和新领域必须重新做开发集/独立留出集评测，不能照搬 0.60、0.75"，但语料要从 13 篇扩到菜谱库（HowToCook 数百道菜），chunk 数会从 144 涨到数万。重建索引、重扫阈值、重跑消融的工作量没说。
**需要**：给出扩容后的评测计划与时间预算，或明确"首版沿用 13 篇语料 + 独立菜谱检索通道"。

**回填解答（2026-11-18）**

**结论：首版保留现有13份营养资料作为独立索引，菜谱主要进入结构化DB与单独烹饪检索通道，不一次性导入HowToCook全库。** 13份资料也需来源质量审核，数量不是合格证明。首批30道内部联调、正式目标100道的边界见P1-2；是否需要切片由步骤和篇幅决定，不假设必然数万chunk。

营养和烹饪各自维护document/collection或强制scope过滤、阈值、版本与评测集；不拿原nutrition阈值直接过滤新菜谱。首批扩容工程预算为4–5工程日（数据整理约2日、索引与开发集调参1日、冻结留出/消融/报告1–2日），人工健康引用评审按P2-2另排用户时间；估算需用实际文件规模修正，不承诺自动导入即完成。

评测规模默认：营养开发40问＋留出40问，烹饪开发40问＋留出40问，另30条跨域/无关/注入/证据缺失案例；50条人工引用评测覆盖两类可引用回答。固定开发集对vector/BM25/RRF/可选rerank做消融，在开发集确定阈值后只跑一次留出验证，失败必须登记后续版本，不能反复调留出。只统计实际发生的embedding/重排时间和调用费用；模型评测次数/token预先预算，达到P0-4上限停止。后续批量扩容每次生成规模、耗时和效果差异报告，不静默覆盖正在使用的索引。

<!-- review-answer-end -->

---

## 汇总

| 分组 | 数量 | 性质 |
|---|---|---|
| P0 阻塞性 | 8 | 不解决无法开工或必然返工 |
| P1 定义缺失 | 9 | 会导致不同人实现出不同系统 |
| P2 验收不可判定 | 6 | 写了标准但无法执行 |
| P3 事实与表述风险 | 7 | 会误导实施者 |
| P4 工程细节 | 6 | 会踩坑但好补 |
| 合计 | 36 | — |

## 回填状态复核（2026-11-18 第二次核对）

本节用于修正本文件早期版本：正文 36 条均已在原位置附「回填解答」，**不应再把它们当作未决问题重复提问**。以下只列回填之后仍未关死的部分。

### 1. 回填已关死的部分（不再问）

八项优先结论已在文件开头定稿：全量替代但分阶段、LangGraph 只编排不承担恢复、旧 SSE 与 job+events 双入口、分类限频＋token 硬预算、前端 UUID 幂等键、私人厨师＝烹饪能力集合、真实 MySQL/Redis 集成测试、开发者自测＋用户最终验收。其余 28 条也都给出了可执行规则，无需二次确认。

### 2. 仍需用户点头的数值（回填中自述为“设计默认”，不是已确认事实）

| 编号 | 数值 | 影响 |
|---|---|---|
| P0-1 | 10–16 人周、R0–R4 五段 | 排期基线，决定何时能看到可演示版本 |
| P0-4 | 聊天 50/日、规划 5/日、复盘 2/日、图片 20/日、联网 20/日；token 20 万入/2 万出；每任务≤3 次文本调用 | 直接决定成本上限与演示可用性 |
| P1-2 | 食谱库目标 100 道（最低 60）、每菜≤3 档、求解 5 秒 | 决定 CP-SAT 是否必要与 M4 数据准备量 |
| P1-3 | 单屏最多 5 问、主动追问最多 2 轮 | 影响核对体验与放弃率 |
| P1-8 | 主指标展示门槛：反馈≥3 餐且覆盖≥60% | 决定周报是否显示百分比 |
| P2-4 | 发布门槛：引用 ID 有效率 100%、人工主张支持率≥90% | 不达标即不开放该健康建议功能 |
| P2-5 | 只读窗口 2 小时、观察期 24 小时 | 切换当天的操作预案 |
| P4-6 | 扩容预算 4–5 工程日；评测集 40+40+40+40+30 | 语料扩容的工期与评测工作量 |

这些不需要重新讨论方向，只需要确认“按这个数走”，或给出替代值。

### 3. 回填与现状的三处不一致（实施时会踩到）

| 项 | 回填说法 | 代码/仓库现状 | 需处理的点 |
|---|---|---|---|
| 管理员配额 | P0-4：“管理员也受成本硬上限约束” | `ai.quota.admin-exempt=true`，管理员当前不受次数限制 | 明确“免的是次数、不免 token”，还是两者都免；否则实现会与回填相反 |
| 测试入口 | P4-4：pytest 作为 CI 唯一覆盖率入口，新增 requirements-dev | venv 无 pytest、无 `requirements-dev.txt`、`.github/workflows/ci.yml` 跑的是 `unittest discover` | 该条落地前需先加依赖并改 CI，回填完成≠可执行 |
| 集成测试环境 | P0-8：本地与 CI 复用 `deploy/compose.test.yml` | 项目无 `deploy/` 目录，文件不存在 | 属 M1 首项交付，需新建，不能当既有设施引用 |

另：P0-6 引用的 `src/api/idempotency.js`、`submitIdempotently()`、`crypto.randomUUID()` 已核实存在（`FoodWeightView`、`WeightAddView`、`AiChatView` 均在用），这条回填是准确的。

**上述三项已于 2026-11-18 处理完毕**（上表保留为当时记录）：

| 项 | 处理结果 |
|---|---|
| 管理员配额 | 新增 `ai.quota.admin-exempt-tokens=false` 与 `AiQuotaService.isTokenCapExempt()`，语义固定为“免次数、不免 token 成本”，并补了一个测试断言默认值 |
| 测试入口 | 新增 `requirements-dev.txt`（pytest 9.2.0 / pytest-cov 7.1.0 / pytest-asyncio 1.5.0）与 `pytest.ini`（strict markers）；CI 改为 pytest + 覆盖率门槛 75（全仓实测 79%），unittest 只作兼容回归 |
| 集成环境 | 新建 `deploy/compose.test.yml` + `deploy/test-db-init/` + `deploy/README.md`；实测 MySQL 8.0.46 与 Redis 可用，端口 3307/6380，`down -v` 后端口释放 |

### 4. 合并进主设计 v1.1 时必须同步的 5 处

回填在以下位置改了主设计的具体条文，若只合并正文、不改这些段落，文档内部会自相矛盾：

| 主设计位置 | 现文 | 回填后 |
|---|---|---|
| 4.1（第 135 行） | `TEXT_MODEL=deepseek-v4-flash` | `TEXT_MODEL=deepseek-flash`，另记 `TEXT_REQUESTED_MODEL` |
| 4.2（第 143、669 行） | `VISION_MODEL=qwen3.8-omni-flash` | `VISION_REQUESTED_MODEL=qwen3.8-omni-flash`、`VISION_MODEL=<verified-provider-model-id>`、`VISION_ENABLED=false` |
| 12.2（第 369 行） | “若要 checkpoint saver…否则…” | 删除 saver 开放选项，状态由 MySQL job/workflow_step 承担 |
| 12.3（第 380 行） | 确认命令列为工具，权限栏写“不交给模型” | 从模型工具表移除，归入业务命令 API |
| 14.1（第 449 行） | 新接口出参 `records/total/current/size` | 新接口出参 `{records,total,page,pageSize}` |

### 5. 仍按阶段核实、不属于“已回答即可用”的项

供应商精确模型 ID 与鉴权（P3-1/P3-2）、真实数据库结构（P0-5）、价格与费用（P0-4/P4-6）、人工质量验收（P2-1/P2-2）、目标公式（P3-4）。这些在回填中原样保留为 M0/M3/M8 的验证动作，不能用“问题已回填”当作“能力已验证”。

## 文档做得好的地方（保留，不要改）

- `null ≠ 0`、`unknown ≠ absent` 贯穿全文，且落到具体字段（`quantityState`、`shortageG=null`）；
- 600 / 200 / 400 / "有一点" 四个样例在 A03、A04、A05、14.4 反复复用，是可测试的具体化；
- "出处存在不代表支持结论"、`knowledgeStatus` 区分 `no_evidence` 与 `unavailable`；
- 禁止用旧备份覆盖切换后新增记录、失败不回落成未校验的模型计划；
- 附录 A 接口索引与 Java 侧 70 个映射完全一致，附录 B 给出了可核对的源码路径。
