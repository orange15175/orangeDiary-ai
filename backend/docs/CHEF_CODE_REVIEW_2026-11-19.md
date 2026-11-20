# AI 做菜助手代码审查（2026-11-19）

## 1. 审查范围与结论

当前功能有基础，但尚不能稳定满足“说菜名教做菜、说食材推荐菜、连续追问”。本次以当前源码、隔离行为复现、只读业务库统计和依赖扫描为依据，不把已有接口或测试通过等同于产品验收通过。

- 后端：`J:\develope\橘子rag\orangeDiary-ai`。
- 前端：`J:\develope\橘子rag\orange-diary-web`。
- 参考项目：`J:\develope\智能厨师\lc-course-main\lc-course-main`。
- 旧 Java 工作区不是本次实现位置。
- 本轮只审查和设计。此前刚加入但尚未接完的 mode/history 参数和检索、用量缩放修改已准确撤回，保留本轮开始前的业务实现；未回退其他既有工作。
- 未调用真实付费模型，未启动或重启用户服务，未写入业务数据库，未进行完整渗透测试或 Python 全依赖漏洞扫描。

## 2. 已确认的问题

| 编号 / 优先级 | 问题与影响 | 当前证据 | 后续处理 |
|---|---|---|---|
| C01 / P1 | 把整句作为单个 LIKE 关键词，“辣椒炒五花肉怎么做”和多食材输入不命中；无命中又返回库里前三道菜，可能答非所问 | `recipe_service.py:86`、`chef_service.py:74`；隔离探针复现 | 分离菜名/食材/追问检索；无关候选不得兜底 |
| C02 / P1 | 无做菜会话与上下文，“第二道”“没有生抽”无法可靠承接 | `ChefRequest` 只有 message/servings/allergens；`ChefView.vue` 每次覆盖 answer；模型只收到本轮 prompt | 账号隔离的厨师会话、历史和选中菜品 |
| C03 / P1 | 调料没有单列，未明确哪些已有、哪些需补；没有稳定保证火候、时间、熟度判断 | `CHEF_PROMPT`、`_build_prompt` 只列通用食材与步骤 | 结构化输出并校验必需字段 |
| C04 / P1 | allergens 只过滤本地候选，没有传进模型 prompt，RAG 或替换建议仍可能引入禁用食材；前端也未提供明确约束输入 | `chef_service.py:41/85/123`、`ChefView.vue:46` | 将过敏/忌口贯穿检索、生成、输出校验和追问 |
| C05 / P1 | 已登录用户可直接调用厨师模型，绕开普通 AI 问答已有的用户配额；没有本接口的用户并发控制 | `api/recipes.py:59` 直接调用 ChefService；对照 `ai_service.py:279` 的 acquire | 接入共用配额与并发限制；模型熔断不等于用户限流 |
| C06 / P1 | 两项前端依赖高危通告，尚未修复 | 本文第 5 节 npm audit 与依赖树 | 兼容升级、复扫、构建与浏览器回归 |
| C07 / P2 | 人份缩放只改 grams 和营养，quantity/unit 原始数量未缩放；按“个/勺”展示会不一致 | `recipe_service.py:212`；2 人份 2 个改 4 人份后仍为 2 个 | 数量一致缩放；调味料给范围与少量补加提示 |
| C08 / P2 | 降级只输出第一道候选；仅 RAG 有资料但模型不可用时，回退成“暂时没有可用候选”，没有利用资料解释现状 | `chef_service.py:176` | 无模型时只展示匹配且完整的本地原文；其余明确资料不足 |
| C09 / P2 | 来源列表是所有检索命中，不代表模型真正使用；提示词引用规则未做服务端校验 | `chef_service.py:193` | 分开返回已引用来源和检索候选；校验引用 ID |
| C10 / P2 | 厨师仍读取食材记忆/购物线索，入口仍引导计划、采购、复盘，与新范围不符 | `chef_service.py:58`、`DietOverviewView.vue:214`、`AssistantSteps.vue` | 移除厨师对库存记忆的强依赖；主入口收敛两种用法 |
| C11 / P2 | 只有样例食谱，无法保证常见菜覆盖；知识库为空就不能回答，而当前厨师没有联网搜索补充 | 数据统计及 ChefService 全链路；参考项目有 Tavily，当前没有 | 定义无资料策略，补授权食谱；联网属于可选后续能力 |
| C12 / P2 | 食谱模型预留 owner_id/scope，但查询未按用户所有权过滤 | `diet_assistant.py` Recipe；`RecipeService.list_recipes/get_recipe`；路由仅鉴权，不传 owner | 加私有食谱前必须补隔离；不能据此断言当前已有私人数据泄露 |
| C13 / P2 | 测试未覆盖上述真实输入；旧执行清单还把库存预留等历史口径标成当前完成 | 当前 14 项测试通过但探针能复现；`R3_EXECUTION_CHECKLIST.md` | 补真实对话样例与断言；旧计划保留为历史，链接新基线 |

P1 为本轮开发应优先解决的问题，P2 为体验、覆盖和交付完整性问题。未发现或声称存在远程代码执行、数据库已被攻击等情况。

## 3. 隔离行为复现

命令（后端目录，PowerShell）：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts/audit_chef_scope.py
```

探针创建临时内存 SQLite，只有三道测试菜。模型关闭，知识检索替换为空；不访问业务库、不消耗模型额度。结果仅证明对应的确定性行为，不能推出真实模型一定逐字回答相同内容。

| 输入 | 结构化检索 | 实际降级回答 |
|---|---|---|
| 辣椒炒五花肉 | 命中该菜 | 辣椒炒五花肉 |
| 辣椒炒五花肉怎么做 | 空 | 番茄炒蛋 |
| 辣椒、五花肉、鸡腿 | 空 | 番茄炒蛋 |
| 清蒸鲈鱼怎么做（库中无此菜） | 空 | 番茄炒蛋 |

另外：2 人份、食材 2 个 → 请求 4 人份 → 返回 servings=4，但 quantity=2.00，预期为 4。探针保留为后续修复前后对照脚本，不能把其退出码 0 当作新需求验收成功。

## 4. 当前数据和工程检查

2026-11-19 只读 SQL 当前业务库确认：

- 食谱：10 条，全部 source_type=sample；formal=0。
- 食物营养条目：2158；份量换算：6；schema=0009。
- 营养食物库不是菜谱库，不能把 2158 条食物视为 2158 道有做法的菜。

本轮执行：

| 检查 | 结果 | 能证明什么 |
|---|---|---|
| `python -m pytest tests/test_recipes.py tests/test_light_ingredient_memory.py -q --tb=short` | 14 passed；1 条 anyio/pytest 弃用警告 | 既有对应服务回归通过；不是新做菜需求已通过 |
| `python -m pytest tests/test_security.py tests/test_ai_quota.py -q --tb=short` | 16 passed；同类弃用警告 | 所覆盖安全/配额单测通过；不证明厨师已接入配额 |
| 前端 `npm run build` | 通过；482 modules；4.08s | 前端能构建；不证明真实对话效果 |
| `scripts/audit_chef_scope.py` | 复现第 3 节缺口 | 无模型、隔离数据下的问题证据 |

## 5. 依赖扫描

默认 npmmirror 审计端点返回 404 / NOT_IMPLEMENTED。未改 npm 全局配置，改用单次命令重试：

```powershell
npm audit --omit=dev --registry=https://registry.npmjs.org
npm ls follow-redirects source-map-js
```

报告 **2 high severity vulnerabilities**：

- `axios@1.20.0 → follow-redirects@1.16.0`：跨域重定向时敏感头处理相关通告，GHSA-q3jh-wrc6-jgq8、GHSA-8r9p-f939-6h3c、GHSA-mrwp-f3q8-3g9p。follow-redirects 面向 Node HTTP 适配器；当前浏览器 axios 通常走 XHR/fetch，不能直接宣称浏览器凭证已泄露。仍需升级并核对实际运行路径。
- `source-map-js@1.2.1`：indexed source-map section offsets 导致事件循环拒绝服务，GHSA-68fv-2mgg-jv7q。依赖树来自 Vue 编译器及 Vite/PostCSS；实际暴露取决于是否处理攻击者提供的 source map，不能将安装依赖直接等同于线上可利用漏洞。

本轮未执行 `npm audit fix`、未改 lockfile。修复时先查看升级后的具体版本和兼容性，再复扫；不机械运行强制升级。

## 6. 下一步

开发与验收按 `CHEF_ASSISTANT_DEVELOPMENT_PLAN.md` 执行。旧 R3 中的周计划、采购、库存、动态调整与复盘不再作为本轮做菜助手的交付条件；历史代码和数据不自动删除。
