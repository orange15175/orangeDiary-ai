# M2 混合检索闭环 · 验收报告

日期：2026-11-17
对应计划：`AI_RAG_DEVELOPMENT_PLAN.md` M2（混合召回 + 检索接口 + 来源展示）

---

## 一、本次范围

1. 补充 6 篇权威文献，与原文献放在同一目录，重建 `knowledge-v2` 索引
2. 实现向量 + BM25 双通道并行检索、RRF 融合、证据过滤
3. 新增 `POST /internal/rag/v1/search`
4. Java 侧接入：健康建议类问题检索知识库，来源随响应返回
5. 前端展示来源卡片

不在本次范围：rerank 模型、Java 与 Python 的真实登录态联调、流式通道的来源回传（见“已知限制”）。

---

## 二、新增文献

采集脚本 `scripts/collect_literature.py`（可复现、默认不覆盖已有文件），输出 docx 到 `D:\Desktop\桌面\饮食文献`，与原有 7 篇放在一起。

| 新增文件 | 发布机构 | 时间 | 来源链接 | needs_review |
|---|---|---|---|---|
| 世界卫生组织-身体活动.docx | 世界卫生组织 | 2024-06-26 | who.int 事实清单 | 否 |
| 中国居民膳食指南（2022）平衡膳食八准则核心推荐.docx | 中国营养学会 | 2022-04-26 | dg.cnsoc.org | 否 |
| 世界卫生组织-游离糖摄入量建议.docx | 世界卫生组织 | 2015-03-04 | who.int 指南发布消息 | 否 |
| 世界卫生组织-非糖甜味剂使用建议.docx | 世界卫生组织 | 2023-05-15 | who.int 官方 PDF | **是** |
| 成人肥胖食养指南（2024年版）要点.docx | 国家卫生健康委 | 2024-02 | nhc.gov.cn PDF | 否 |
| 高血糖症营养和运动指导原则（2024年版）.docx | 国家卫生健康委 | 2024-07 | gov.cn PDF | 否 |

内容只来自抓取到并核对过的官方页面/文件，未补数字、未改写结论。

**未收录**：WHO「减少钠摄入」与已有《控制钠的摄入》为同一页面的不同版本，跳过以免同一来源重复入库。

**非糖甜味剂一篇标 needs_review**：WHO 中文与英文的 2023-05-15 新闻稿页面均返回 404，因此 source_url 指向 WHO 官方 PDF（阿斯巴甜评估摘要，含该建议原文），正文据 WHO 指南与官方发布整理。前端对 needs_review 的来源不渲染成可点击引用。

文献总量：**13 篇**（原 7 + 新 6）。

---

## 三、索引 v2

| 项 | 值 |
|---|---|
| 索引版本 | `knowledge-v2` |
| Qdrant 集合 | `orange_diary_knowledge_v2`（v1 集合保留未动，便于对比） |
| 片段数 | 144（v1 为 102） |
| 向量维度 | 1024（bge-m3 实测） |
| BM25 索引 | `knowledge/index/bm25/knowledge-v2.json` |
| 构建错误 | 0 |

---

## 四、检索接口

`POST /internal/rag/v1/search`（Bearer `AI_SERVICE_TOKEN`）

请求：
```json
{"query": "每天应该吃多少盐", "top_k": 5, "min_score": 0.55}
```

响应：
```json
{
  "traceId": "...",
  "query": "...",
  "answerable": true,
  "degraded": {"rerank": "not_configured", "reason": "未接入 rerank 模型……"},
  "timings": {"vector_ms": 213.8, "bm25_ms": 434.1, "payload_fill_ms": 53.2, "total_ms": 488.7},
  "candidates": {"vector": 20, "bm25": 20, "fused": 34, "dropped": 0},
  "results": [
    {"rank": 1, "chunk_id": "...", "document_id": "who-sodium", "title": "...",
     "heading_path": ["..."], "text": "...", "source_url": "...", "needs_review": false,
     "score": 0.0328, "vector_score": 0.726, "bm25_score": 7.2,
     "vector_rank": 1, "bm25_rank": 2}
  ]
}
```

错误码：401 未授权 / 400 参数非法 / 503 索引未就绪或检索关闭 / 502 上游（Ollama、Qdrant）不可用。

设计要点：
- **并行**：向量通道（Embedding + Qdrant）与 BM25 通道在线程池并行，`timings` 分项记录
- **RRF 融合**：`score = Σ 1/(60+rank)`，不依赖两套分数的尺度；同一通道内重复 ID 只计一次
- **BM25 独有候选补正文**：融合后按 `chunk_id` 从 Qdrant 补取 payload，否则关键词命中的片段拿不到文本
- **证据过滤**：`vector_score >= 0.55` 或 `bm25_score >= 6.0` 才算可用证据；`answerable=false` 时上层拒答
- **来源多样性**：同篇文档最多 2 条，不足时用溢出片段补齐，避免来源卡片被同一段落占满

### 阈值是实测出来的，不是拍的

| 查询类型 | 首位 vector_score | 判定 |
|---|---|---|
| 无关（今天天气 / 股票怎么买） | 0.479 / 0.456 | 拒答 ✅ |
| 相关（吃多少盐 / 蔬菜水果 / 运动时长） | 0.726 / 0.757 / 0.701 | 作答 ✅ |
| 相关但表述短（烤红薯西瓜 GI） | 0.526 | 靠 BM25 17.1 救回 ✅ |

初版阈值 0.35 时“今天天气”“股票怎么买”都会被判可答，因此上调至 0.55。

---

## 五、Java 接入

- 新增 `PythonRagClient`：连接 2s / 读取 6s 超时；401、超时、其他异常分类记录 traceId；失败返回 `null` 由上层降级，**不让检索故障拖垮聊天**
- 地址推导：`/internal/ai/v1` → `/internal/rag/v1`，不新增部署配置项
- `AiServiceImpl`：`health_advice` 意图（及关键词降级命中“膳食指南/推荐摄入/上限”等）触发检索，取 4 条注入上下文
- **三种情况分开表述**，避免模型编造：
  - 检索服务不可用 → “本次未取到知识库资料，不要声称根据资料”
  - 知识库无相关证据 → “未检索到相关内容，请如实说明没有依据”
  - 取到证据 → 注入片段，并要求回答中的数值与片段一致
- `AiChatResponse` 新增 `sources` 字段（rank/title/headingPath/excerpt/sourceUrl/needsReview），仅在真正取到片段时返回

## 六、前端

`AiChatView.vue`：assistant 消息下方渲染来源卡片，显示标题、章节路径、摘要；`sourceUrl` 为空或 `needsReview=true` 时只显示文字并标“链接待核实”，不生成可点击链接。

---

## 七、验证记录

| 项 | 结果 |
|---|---|
| Python 单元测试 | **66 项通过**（新增 fusion 8 + service 10） |
| Java 测试 | **93 项通过**，0 失败 6 跳过（基线 88 + 新增 5） |
| Java 编译 | 通过 |
| 前端构建 | 通过（沙箱拦截清空 `dist/`，输出到临时目录验证后已删除） |
| 接口实测 | 8 个查询（2 无关 + 6 相关）判定全部正确 |
| 错误分支实测 | 401、400（空查询/超长/越界 top_k）均按预期返回 |

检索耗时：本地 bge-m3 + Qdrant，单次 200~400ms（首次含 jieba 词典加载约 500ms）。

---

## 八、已知限制（未验证项）

1. **Java ↔ Python 端到端未联调**：需要登录态，本轮未用真实账号登录。已用本地 HTTP stub 覆盖 `PythonRagClient` 的地址推导、结果解析、401 降级（5 项测试）。
2. **8080 上的 Java 是旧进程**，需重启才加载新代码。
3. **rerank 未接入**：来源排序只由 RRF + 证据过滤决定，接口以 `degraded.rerank=not_configured` 显式标注，不假装做过重排。
4. **流式通道（SSE）未注入知识**：当前没有回传来源的事件协议，注入会导致“有依据但不可追溯”，留到 M4 与前端的 sources 事件一起做。
5. **BM25 有噪声**：如“每天吃多少盐”第 3 条落到“准则四 鱼禽蛋瘦肉”。融合权重与 BM25 阈值需用 M3 评估集调，不在本轮凭感觉改。
6. 文献总量 13 篇仍偏小，覆盖不了慢性病人群、儿童孕妇等细分场景。

---

## 九、回滚方式

```bash
# Python：回滚检索层与配置
cd J:/develope/橘子rag/orangeDiary-ai
git checkout -- app/main.py app/config.py app/rag/retrieval/vector.py .env.example
rm -rf app/rag/retrieval/fusion.py app/rag/service.py app/api/rag.py
rm -rf tests/test_fusion.py tests/test_rag_service.py

# Java：回滚接入
cd J:/develope/橘子rag/orangeDiary
git checkout -- src/main/java/com/example/orangediary/service/impl/AiServiceImpl.java
git checkout -- src/main/java/com/example/orangediary/dto/ai/AiChatResponse.java
rm -f src/main/java/com/example/orangediary/service/PythonRagClient.java
rm -f src/test/java/com/example/orangediary/service/PythonRagClientTest.java

# 前端
cd J:/develope/橘子rag/orange-diary-web
git checkout -- src/views/AiChatView.vue
```

索引回滚：把 `RAG_COLLECTION` 改回 `orange_diary_knowledge_v1`、`RAG_INDEX_VERSION` 改回 `knowledge-v1` 即可切回 v1（v1 集合仍在 Qdrant 中）。新增文献与 `knowledge/sources/*.md` 可直接删除，不影响 v1。

注意：新增的 6 个 docx 在 `D:\Desktop\桌面\饮食文献`，删除它们不影响系统运行，只是下次重建索引时少这 6 篇。
