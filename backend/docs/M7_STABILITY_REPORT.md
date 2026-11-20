# M7 稳定性与可观测报告（单实例口径）

日期：2026-11-17。范围：M7-1 并发与排队、M7-2 会话记忆异步摘要、M7-3 Trace 与管理页、M7-4 增量索引补齐。

按已确认的部署口径执行：**单实例**（一个 Java + 一个 Python）、限流用进程内信号量 + 可选 Redis 计数、管理端**做页面**。

## 0. 结论摘要

| 项 | 结果 |
|---|---|
| 并发闸门 | 默认改为 **2**（压测证据见 M6 报告）；超出排队 2s 后返回 **429 + Retry-After**，不再让请求一起超时 |
| 名额回收 | 名额由工作线程释放，客户端断开不会造成"名额泄漏"；实测 10 并发打满后 `inflight` 回到 0 |
| 会话摘要 | 从"请求线程同步调模型"改为**单线程 + 有界队列（16）+ 同会话合并**；写入用**游标 CAS**，旧结果不会覆盖新摘要 |
| 追踪 | `ai_chat_trace` 表记录 traceId / 意图 / 来源数 / 检索耗时 / 缓存状态 / 总耗时；**不存问题全文与回答正文**（前 20 字 + SHA-256），保留 30 天 |
| 管理端 | `/api/admin/ai/traces`、`/traces/{traceId}`、`/rag-stats` + 前端 `问答追踪` 页；非管理员 403 |
| 增量索引 | 源文档从清单移除后，旧片段与缓存文件同步清理；构建状态 running → succeeded/failed 落盘 |

## 1. M7-1 并发与排队

### 实现

`app/rag/limiter.py`：全局信号量 + 用户滑动窗口 + 有限排队 + 可选 Redis 总量计数。

四个刻意的取舍：

1. **先无阻塞取名额**，取不到才进队列。否则 `max_queue=0` 会把第一个请求也拒掉（写测试时踩到过）。
2. **名额在线程里释放**：`limiter.run(user, work)` 包住工作，`asyncio.to_thread` 被取消时协程的 finally 会早于线程结束，按协程释放会让实际并发超上限。
3. **队列短**（默认 8）+ 等待上限 2s：排队期间用户已经在等，超过预算就 429。
4. **Redis 只做跨进程总量，不做租约续租**：单实例下用不上；多实例时需补租约，报告里标注为已知缺口。

### 验证（本机实测）

```
并发 10（闸门 2 / 队列 8）→ 200: 2, 429: 8
/stats → accepted=3, rejected={"queue_timeout":8}, inflight=0, redis=disabled
```

- 429 带 `Retry-After`；Java 侧把 429 映射成 `RagResult.busy()`，提示"稍后再试"而不是"知识库没有依据"。
- 用户标识由 Java 透传（`user_id`），Python 侧按用户做 60s/10 次窗口限制。

## 2. M7-2 会话记忆与异步摘要

### 改法

| 之前 | 现在 |
|---|---|
| `buildContext()` 里同步调摘要模型，用户在等摘要 | 提交到单线程有界执行器，本轮回答不等它 |
| 全局 `synchronized` 串行所有会话 | 只用于会话与消息写入；摘要并发由线程池 + 每会话合并控制 |
| `updateById` 直接写摘要 | `UPDATE ... WHERE id=? AND summary_covered_sequence=?`，影响 0 行即丢弃 |

### 两条关键测试

- `buildContextDoesNotWaitForSummaryModel`：让摘要模型卡在 latch 上，断言 `buildContext` 在 500ms 内返回——证明请求线程不再被摘要拖住。
- `staleSummaryIsDiscardedWhenCursorAdvanced`：CAS 返回 0 时只记录日志、不抛异常，聊天主流程不受影响。

> 单测里需要手动 `TableInfoHelper.initTableInfo(...)`，否则 `LambdaUpdateWrapper` 报 "can not find lambda cache"——这不是生产问题，是纯单测没有 MyBatis 启动流程。

## 3. M7-3 Trace 与管理端

### 数据

`ai_chat_trace`：`trace_id / user_id / conversation_id / intent / question_excerpt / question_hash / source_count / retrieval_ms / rerank_state / degraded / total_ms / created_at`。

隐私处理（按确认的"脱敏"口径）：

- 问题只存**前 20 字 + SHA-256**，回答正文完全不写库；
- 管理页展示的是意图、来源条数、各段耗时与降级原因，不是对话内容；
- 保留 30 天，每次写入后按 24h 节流清理过期记录。

### 接口

| 接口 | 说明 |
|---|---|
| `GET /api/admin/ai/traces?page&size&traceId&userId` | 列表，按 id 倒序 |
| `GET /api/admin/ai/traces/{traceId}` | 同一 traceId 的全部记录（重试会多条） |
| `GET /api/admin/ai/rag-stats` | 代理 Python 的缓存命中率、熔断与限流状态 |

鉴权：`CurrentUserContext` + `user.role=admin` + `status=active`，否则 **403**（实测普通账号 403，临时提权后 200，验证后已还原）。

### 页面

`AdminAiTraceView.vue`（路由 `/admin/ai-traces`，入口在社区管理页签"问答追踪"）：顶部 6 个运行状态卡（缓存命中率、并发额度、在飞/等待、被拒次数、两个熔断器状态），中间按 traceId / 用户筛选的列表，点击行展开单次详情。`npm run build` 通过。

### 过程中修掉的三个真实缺陷

1. **分页一直是坏的**：项目没有 MyBatis-Plus 分页插件（`mybatis-plus-jsqlparser` 不在依赖里），`selectPage` 返回全量且 `total=0`。离线环境装不了新依赖，改为手工 `count + LIMIT/OFFSET`，实测 `total=1, pages=1`。
2. **`created_at` 纳秒精度被 MySQL 拒绝**：手工塞 `LocalDateTime.now()` 触发 1292，改为交给数据库默认值。
3. **追踪写入曾打到错误的库**：容器 `orangediary-mysql-1` 与宿主 `mysqld` 都叫 `calorie_tracker`，但容器没有发布 3306，Java 连的是宿主实例。迁移脚本已改在宿主执行（`mysql -h127.0.0.1 -uroot`）。

## 4. M7-4 增量索引补齐

- **清理失效文档**：`_run()` 结束时对比 `build_state.documents` 与当前 manifest，差集逐个 `delete_document` + 删 chunk 缓存 + 从状态移除，`documents_removed` 计数。
- **任务状态**：`run()` 包一层，`last_run = {status, index_version, at, error?, documents_*, chunks_written, errors}`；失败时先落 `failed` 再抛出，不再只有一份半成品索引。
- 测试：`test_document_removed_from_manifest_is_pruned`、`test_run_status_is_recorded`（含"单文档解析失败不中断整次运行、错误进 errors"）。

## 5. 未做与已知缺口

1. **流式通道未写追踪**：`chatStream` 目前只发 sources 事件，不落 trace（非流式已覆盖）。补的时候要注意别在 SSE 完成后才写库。
2. **多实例仍需租约**：Redis 计数只有"总量上限 + TTL 兜底"，没有续租与失联任务回收。真正扩容前必须补，否则崩溃后配额会短暂偏紧。
3. **管理页只读**：没有做反馈（赞/踩）列表与筛选，也没有导出。用户反馈表尚未设计。
4. **Trace 不含生成阶段细分**：只有 `retrieval_ms` 与 `total_ms` 两段，DeepSeek 首字延迟未单独记录（需要网关侧埋点）。
5. **压测与追踪未联调**：压测产生的 429 没有落到 trace 表（429 在检索层就返回，Java 不写 trace）。

## 6. 复现命令

```bash
# 建表（宿主 MySQL，不是容器）
mysql -h127.0.0.1 -P3306 -uroot -p calorie_tracker < src/main/resources/ai_trace_migration.sql

# 限流验证：关缓存实例 + 10 并发
RAG_CACHE_ENABLED=false uvicorn main:app --port 8004 &
python scripts/load_test.py --base-url http://127.0.0.1:8004 --concurrency 10 --requests 10 --label busy-check
curl -s http://127.0.0.1:8004/internal/rag/v1/stats -H "Authorization: Bearer $AI_SERVICE_TOKEN"

# 管理端
curl -s "http://127.0.0.1:8080/api/admin/ai/traces?page=1&size=5" -H "Authorization: Bearer $JWT"
```

回滚：`RAG_MAX_CONCURRENCY` 调大即放宽闸门；管理端可直接下线（路由与菜单各一处）；`ai_chat_trace` 表可整表删除，代码有 `record()` 的 try/catch 保护，不会影响聊天。
