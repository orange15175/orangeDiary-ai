# R3（智能饮食闭环）执行清单

> 最新执行入口：`AI_CHAT_COOKING_IMPROVEMENT_PLAN.md`。做菜接入现有聊天，按 cooking 意图路由并复用统一历史；不再按独立厨师会话方案实施。本历史清单中的其他计划链接不覆盖这一最新决定。

> 范围变更（2026-11-19）：用户将本轮目标收敛为 AI 做菜助手。本文保留历史 R3 清单，下面的“完成”不能视为新需求通过验收；库存预留等描述也不得覆盖当前代码与最新用户决定。本轮请执行 `CHEF_ASSISTANT_DEVELOPMENT_PLAN.md`，当前问题见 `CHEF_CODE_REVIEW_2026-11-19.md`。

依据 `PYTHON_FULL_BACKEND_AND_DIET_ASSISTANT_DESIGN.md` v1.1，按依赖顺序
「食谱与私人厨师 → 周计划与食材核对 → 采购 → 动态调整 → 复盘」实施。
本清单记录**设计条目 → 实现位置 → 状态**，每阶段完成后回填，不把"写了文档"当成"已完成"。

状态口径：`完成` = 数据模型 + 迁移 + 服务 + API + 测试 + 前端入口齐全；`部分` = 主链路可用但有明确缺口；
`待做` = 未实现。**降级与未做项单独列在第 5 节**，不在表格里模糊成"完成"。

## 0. 可复用点（已核对）

| 设计需要 | 现有实现 | 复用方式 |
|---|---|---|
| 营养目标（BMR/TDEE/目标修正） | `app/services/summary_service.py`：Mifflin–St Jeor、活动系数（1.2/1.325/1.45）、`lose -400` / `gain +225` | 已抽成模块函数 `daily_target(db, user_id, day)`，规划与复盘共用，未重复实现 |
| 食物营养（每 100g） | `food_item`（2158 条，热量/蛋白/脂肪/碳水）+ `food_serving` | 食谱营养按 `Σ(每100g × 可食克重/100)` 计算；缺失保持 null |
| 份量换算 | `food_serving`（原本仅 2 条） | 种子脚本补了 4 条换算（鸡蛋 50g/个 等），标注为换算口径而非营养值；无换算则标未知 |
| 检索链路 | `app/rag/*`（向量 + BM25 + RRF + rerank + 证据门槛） | 烹饪/营养问答复用；食谱库按结构化查询，不重复造向量库 |
| 私人厨师提示词 | lc-course `personal_chef.py` 系统提示词与 `[来源: ...]` 标注 | 改写为 `chef` 场景提示词（`app/prompts/scenarios.py`），保留防注入与来源区分 |
| 示例食谱 | lc-course `examples/recipes` | 仅借鉴"结构/做法组织方式"，正文自写、标 `source_type=sample`、`license=internal-sample`（设计 §22 禁止默认照抄） |

## 1. 设计与实现映射

### M3 食谱与私人厨师

| 设计节 | 条目 | 实现位置 | 状态 |
|---|---|---|---|
| §6.2 | 私人厨师=菜谱选择/烹饪指导能力集合 | `app/services/chef_service.py` | 完成 |
| §6.2 | 输入：文字；输出：候选、步骤、替代、来源 | `POST /api/assistant/chef`（返回 `reply/recipes/sources/degraded`） | 完成 |
| §6.4 | 营养 = Σ(每100g × 可食克重/100)，缺失 null + coverage | `recipe_service.compute_nutrition`（返回 `total/coverage/missing`） | 完成 |
| §6.4 | 生熟/可食部/份量区分 | `recipe_ingredient.basis`；`gramStatus` 标 direct/serving/unknown | 部分（`edible_ratio` 未落表，见 §5） |
| §10.3 | 引用契约 | `chef_service` 复用 RAG `_to_sources` 结构 | 完成 |
| §13.2 | recipe / recipe_ingredient / recipe_step / recipe_nutrition / diet_preference | `app/models/diet_assistant.py` + 迁移 0008 | 完成 |
| — | 前端入口 | `views/ChefView.vue`（问厨师 + 食谱库 + 做法详情） | 完成 |

### M4 周计划与食材核对

| 设计节 | 条目 | 实现位置 | 状态 |
|---|---|---|---|
| §6.1 | PlanRequest 1–7 天、餐次、三食材模式 | `app/schemas/plan.py` / `plan_service` | 完成 |
| §6.3 | 确定性筛选 + 校验；候选不足不凑数 | `plan_service.generate_draft`（`CANDIDATE_POOL_INSUFFICIENT`） | 完成（贪心 baseline，非 CP-SAT） |
| §6.3 | 硬约束（过敏/设备/时间/锁定）；软目标 | `plan_service._candidates/_pick` | 完成（预算软约束未参与排序） |
| §7.1 | presence/quantityState/foodState/source | `known_ingredient` + `inventory_service.list_known/upsert` | 完成 |
| §7.2 | 按需核对、未知不当 absent | `inventory_service.create_check/_assess`（未记录→needs_check/unknown） | 完成 |
| §7.3 | 预留/扣减/补偿流水 | `ingredient_reservation`（采纳时）+ `ingredient_ledger` | 完成 |
| §13.2 | meal_plan / version / plan_meal / known_ingredient / ledger / reservation / check_* | 迁移 0008 | 完成 |
| — | 前端入口 | `views/MealPlanView.vue`（草稿→采纳→标记执行→食材核对） | 完成 |

### M5 采购闭环

| 设计节 | 条目 | 实现位置 | 状态 |
|---|---|---|---|
| §8.1 | 缺口 = max(需求 - 可分配, 0)，未知不当 0 | `shopping_service._shortage` | 完成 |
| §8.1 | strict 预算未确定 = unverified | `shopping_service.build_list` totals | 完成 |
| §8.2 | 价格 Provider；未配置显示不可用，不编造 | `app/services/price_provider.py`（`UnavailablePriceProvider`） | 部分（无真实价格源，见 §5） |
| §8.3 | 购买入库幂等、撤销补偿 | `shopping_service.purchase/reverse_purchase`（`X-Request-Id`） | 完成 |
| §13.2 | shopping_list / shopping_item / shopping_item_source / price_observation | 迁移 0008 | 完成 |
| — | 前端入口 | `views/ShoppingView.vue`（清单、缺量、实付、幂等购买） | 完成 |

### M6 动态调整

| 设计节 | 条目 | 实现位置 | 状态 |
|---|---|---|---|
| §9.1 | meal_observation、与正式记录不重复计入 | `adjustment_service` / `observation_record_link` | 完成 |
| §9.2 | 差异草稿、不覆盖生效、锁定已吃项 | `PlanService.revise`（保留 executed/skipped/locked）+ `POST /api/meal-plans/{id}/adjustments` | 完成 |
| §9.3 | 外卖代理（文字/截图） | 仅文字入口 `POST /api/meal-observations` | 部分（截图识别依赖图片模型，见 §5） |
| — | 前端入口 | `views/ReviewView.vue` 内"按实际情况调整计划"卡片 | 完成 |

### M7 复盘

| 设计节 | 条目 | 实现位置 | 状态 |
|---|---|---|---|
| §11 | confirmedExecutionRate / executionCoverage | `app/services/review_service.py` | 完成 |
| §11 | 缺记录不当 0、数据缺口、建议、下周草稿 | `review_service._nutrition/_suggestions/_draft_next_plan` | 完成（建议走规则，未接模型解释） |
| §11 | 报告版本化：输入变则旧报告标 stale | `review_service.generate`（`input_hash` + `status=stale`） | 完成 |
| §13.2 | review_report / user_feedback | 迁移 0008 | 完成 |
| §13.2 | diet_day_status（当日记录完整度） | 迁移 **0009**（0008 漏建，单开 revision 补齐） | 完成 |
| — | 前端入口 | `views/ReviewView.vue`（完整度标记、周期选择、指标、缺口、建议、历史） | 完成 |

## 2. 前置与阻塞（按你的处理方式）

| 项 | 处理 |
|---|---|
| 食谱数量（60/100 才验收） | **不阻塞启动**。已导入 10 道样例并标 `sample`；60/100 道仍是正式验收门槛，需另行整理并标 `formal` |
| 营养目标参数 | 复用 `summary_service`；无法确定的保留未知或用户自定（`diet_preference`），不阻断 |
| 图片模型 / 价格搜索 | Provider 接口已落地；未配置返回明确 unavailable，模拟数据只用于测试（`tests/` 内），不进生产路径 |

## 3. 每阶段完成定义

每阶段必须同时具备：真实数据模型 + Alembic 迁移、服务层、API、必要测试、前端入口（能点到的页面/卡片）。
上一阶段的输出必须能被下一阶段直接引用（计划的餐次→采购条目→调整草稿→复盘统计）。

## 4. 验证证据（2026-11-19）

| 类型 | 内容 | 结果 |
|---|---|---|
| 迁移 | `0008`（21 表）、`0009`（diet_day_status）在真实 MySQL 8.0.26 执行 | `alembic current = 0009 (head)` |
| 单元测试 | `tests/test_recipes.py` 5 + `test_plan_and_inventory.py` 6 + `test_shopping.py` 4 + `test_review_and_adjustment.py` 8 | 23 passed |
| 端到端冒烟 | `scripts/smoke_r3.py`：注册→资料→食谱→厨师→草稿→核对→采纳→采购→幂等购买→观察→调整→复盘 | 32/32 通过（真实 MySQL + 真实 DeepSeek） |
| 前端 | `npx vite build` + `scripts/ui_check.mjs`（Playwright 走 5 个入口并真实点击） | 构建通过；8/8 渲染与交互通过 |
| 截图 1 回归 | 先发"跑步30分钟"，再问"我上一个问题是啥" | 回答 `你上一条消息是：跑步30分钟`（确定性召回生效） |

`/ready` 由 503 恢复 200，注册路由从 69 条增至 97 条。

## 5. 未做与降级项（不要当成已完成）

| 项 | 现状 | 影响 | 下一步 |
|---|---|---|---|
| CP-SAT 排程 | 用"筛选 + 贪心 + 同/连日不重复 + 份量上限"baseline | 多硬约束组合下可能报候选不足，而非求解最优 | 需要时再引入 OR-Tools，接口不变 |
| 食谱 `edible_ratio` / 生熟换算 | 只存 `basis`(raw/cooked)，未存可食部比例 | 带骨/带皮食材营养可能偏高 | 补 `food_nutrition_meta` 表与换算 |
| 价格源 | `UnavailablePriceProvider`，报价恒为 null | 不能给总花费，只统计用户实付 | 接真实价格 Provider 后写 `price_observation` |
| 预算软约束 | `budgetMode/budgetValue` 已存，未参与选餐排序 | 严格预算下不会主动压成本 | 在 `_pick` 加成本排序 |
| 图片识别 / 外卖截图 | 未接入，`estimatedNutrition` 恒 null | 只能文字记录，界面标"未知" | 走 Provider 接口，未配置保持不可用 |
| 复盘解释文案 | 规则模板生成，`modelVersion` 留空 | 结论准确但措辞固定 | 可接模型"事实→解释"，需保留规则兜底 |
| 正式食谱 60/100 | 仅 10 道 `sample` | 正式验收门槛未达 | 组织正式食谱数据并标 `formal` |
