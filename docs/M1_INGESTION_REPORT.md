# M1 知识入库管线报告

日期：2026-11-17
范围：`orangeDiary-ai`（Python）
依据：`AI_RAG_DEVELOPMENT_PLAN.md` 第 4.1–4.2 节

## 1. 结论

第一阶段「离线入库」已跑通：文献转换 → 结构化切块 → Ollama bge-m3 向量化 → Qdrant 版本化集合 → BM25 中文索引。重复导入幂等，未变化文档会跳过解析与 Embedding。

| 指标 | 结果 |
| --- | --- |
| 文献数 | 7（6 docx + 1 pdf） |
| 片段数 | 102 |
| 片段长度 min / 中位 / 平均 / max | 16 / 381 / 385 / 764 字符 |
| Qdrant 集合 | `orange_diary_knowledge_v1`（102 点，1024 维 Cosine） |
| Embedding 实测维度 | 1024（`bge-m3:latest`，本机已有模型） |
| 测试 | Python 47 项通过（含切块 7 项、BM25 6 项新增） |

## 2. 目录结构

```text
orangeDiary-ai/
  knowledge/
    manifest.json         # 人工审核的来源清单（标题/机构/URL/范围/版本）
    sources/*.md          # docx/pdf 转换产物（7 篇）
    index/                # 构建产物，已 gitignore
      build_state.json    # 每篇文档内容哈希与构建时间
      chunks/*.json       # 每篇文档的片段缓存（供 BM25 重建与增量判断）
      bm25/knowledge-v1.json
      manifest.json       # 索引发布清单：模型、维度、切块版本、文档哈希
  app/rag/
    schemas.py            # Chunk / DocumentMeta / 版本常量
    ingestion/loader.py   # 读取与校验（空文件、编码、大小上限）
    ingestion/chunker.py  # 标题感知切块
    ingestion/manifest.py # 来源清单与构建状态
    embedding/ollama.py   # bge-m3 适配器（批量 + 维度实测）
    retrieval/vector.py   # Qdrant REST（集合校验/按文档删除/批量写入）
    retrieval/bm25.py     # jieba 分词 + BM25 + JSON 持久化
    pipeline.py           # 入库编排
  scripts/
    convert_literature.py # docx/pdf → Markdown
    ingest.py             # 受控入库 CLI
```

## 3. 关键实现

**切块**：按标题层级维护 `heading_path`；段落、列表、表格分别成块；表格整块保留不拆；超长段落按句切分，无标点长串按字符硬切；短于 60 字符的片段并入相邻片段。参数为 700/100（目标长度/重叠），均可在 CLI 调整，不视为最优值。

**转换**：docx 原文没有使用 Word 标题样式，标题是「加粗短段落」，因此加了启发式：加粗 + 长度 ≤30 + 不以标点结尾 + 不含分号 → 二级标题；以「：」结尾的引导句、含「；」的列表碎片排除。表格按行转 Markdown（首行作表头）。PDF 按页提取，每页一个二级标题。

**幂等与增量**：`document_id + content_hash + version` 未变化则整篇跳过（实测 7 篇全部跳过、写入 0）；变化时先按 `document_id` 删除旧片段再写入，避免新旧知识同时被召回。

**维度防护**：启动时实测 Embedding 维度，与集合维度不一致直接失败，不允许混用不兼容向量。

## 4. 抽检结果

| 问题 | 向量检索 Top1 | BM25 Top1 |
| --- | --- | --- |
| 成年人每天盐应该吃多少 | `who-sodium-003`（减少钠摄入的建议，0.744） | `who-sodium-003`（9.6） |
| 每天应该吃多少蔬菜 | `cns-pagoda-2022-002`（第二层：蔬菜水果，0.731） | 同左（9.2） |
| 反式脂肪有什么危害 | `who-trans-fat-000`（重要事实，0.754） | `who-trans-fat-002`（8.8） |
| 超重和肥胖怎么判断 | `who-obesity-001`（概述，0.765） | `who-malnutrition-004`（6.8） |
| 营养不良有哪些类型 | `who-malnutrition-001`（概述，0.757） | `who-wpro-nudge-2023-021`（7.0） |

向量通道 5/5 命中正确文档与章节；BM25 有 2 题落在同主题的其他文档，属于关键词通道的正常噪声，M2 的 RRF 融合与精排会处理。

## 5. 已知限制

1. **两篇 `needs_review=true`**：中国营养学会膳食宝塔、WHO 西太区《助推更健康的饮食行为》没有核实到官方链接，`source_url` 留空，不得在回答中作为引用链接展示，需你确认来源后再补。
2. **PDF 噪声**：目录页与参考文献页进入索引（约 3 个片段），会拉低检索精度；M2 加证据过滤后可缓解，必要时可按章节白名单裁剪。
3. **无 Rerank**：本阶段只有向量与 BM25 两路召回，融合与精排在 M2。
4. **未做检索评估**：目前只做了 5 题人工抽检，Recall@K / MRR 需要 M3 的题集。
5. **文献规模小**：7 篇、102 片段只够验证链路，M2 前建议补到 15–20 篇。

## 6. 命令

```bash
cd J:/develope/橘子rag/orangeDiary-ai

# 1) 文献转换（docx/pdf → knowledge/sources/*.md）
./.venv/Scripts/python.exe scripts/convert_literature.py \
  --source "D:/Desktop/桌面/饮食文献" --output knowledge/sources

# 2) 构建索引（增量；未变化文档自动跳过）
./.venv/Scripts/python.exe scripts/ingest.py \
  --index-version knowledge-v1 --collection orange_diary_knowledge_v1

# 3) 全量重建（改切块参数或模型后必须重建）
./.venv/Scripts/python.exe scripts/ingest.py \
  --index-version knowledge-v1 --collection orange_diary_knowledge_v1 --rebuild

# 4) 测试
./.venv/Scripts/python.exe -m unittest discover -s tests
```

前置依赖：Ollama 运行中且已拉取 `bge-m3`；Qdrant 运行在 6333。

## 7. 下一步（M2）

1. `/internal/rag/v1/search`：向量 + BM25 并行召回 → chunk_id 去重 → RRF → 证据过滤 → 返回来源与耗时。
2. Rerank 策略：首期无独立精排模型，按 RRF 分数 + 证据阈值过滤，响应标注 `degraded_components`。
3. Java 接入：新增 RAG 客户端与 DTO，`health_advice` 意图走检索，回答附来源。
4. 前端来源卡片。
