<script setup>
import { onMounted, ref } from 'vue'
import { getAiTrace, getRagStats, listAiTraces } from '../api/admin'
import '../assets/otter-theme.css'

const rows = ref([]), total = ref(0), page = ref(1), size = 20
const traceId = ref(''), userId = ref('')
const loading = ref(false), error = ref('')
const stats = ref(null), detail = ref(null), detailError = ref('')

// rerankState 现在存"排序方式"（local:bge-reranker-base / not_configured / failed）。
// 早期版本把缓存状态写进了这一列，历史行仍会是 hit/miss，这里显式标注成旧数据。
const LEGACY_CACHE_VALUES = new Set(['hit', 'shared', 'miss', 'bypass'])
function rerankLabel(value) {
  if (!value) return '—'
  if (LEGACY_CACHE_VALUES.has(value)) return `缓存状态 ${value}（旧数据）`
  return value
}

async function load() {
  loading.value = true; error.value = ''
  try {
    const params = { page: page.value, size }
    if (traceId.value.trim()) params.traceId = traceId.value.trim()
    if (userId.value.trim()) params.userId = userId.value.trim()
    const data = await listAiTraces(params)
    rows.value = data.records || []
    total.value = Number(data.total || 0)
  } catch (e) {
    error.value = e?.response?.data?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try { stats.value = await getRagStats() } catch (e) { stats.value = null }
}

async function openDetail(row) {
  detailError.value = ''
  try {
    const list = await getAiTrace(row.traceId)
    detail.value = (list && list[0]) || row
  } catch (e) {
    detail.value = row
    detailError.value = e?.response?.data?.message || '详情加载失败，已显示列表数据'
  }
}

function pct(value) {
  return typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
}

function cacheHitRate() {
  return stats.value?.cache?.hit_rate ?? null
}

onMounted(() => { load(); loadStats() })
</script>

<template>
  <section class="trace-page">
    <header class="head">
      <h1>问答追踪</h1>
      <p class="hint">按 traceId 定位一次问答慢在哪一段。只存问题前 20 字与哈希，不存回答正文。</p>
    </header>

    <div v-if="stats" class="stats">
      <div class="stat"><span>缓存命中率</span><strong>{{ pct(cacheHitRate()) }}</strong></div>
      <div class="stat"><span>并发额度</span><strong>{{ stats.limiter?.limits?.max_concurrency ?? '—' }}</strong></div>
      <div class="stat"><span>在飞 / 等待</span><strong>{{ stats.limiter?.inflight ?? 0 }} / {{ stats.limiter?.waiting ?? 0 }}</strong></div>
      <div class="stat"><span>被拒次数</span><strong>{{ Object.values(stats.limiter?.rejected || {}).reduce((a, b) => a + b, 0) }}</strong></div>
      <div class="stat"><span>向量熔断</span><strong>{{ stats.breakers?.vector?.state ?? '—' }}</strong></div>
      <div class="stat"><span>rerank</span><strong>{{ stats.breakers?.rerank?.state ?? '—' }}</strong></div>
    </div>

    <form class="filters" @submit.prevent="page = 1; load()">
      <input v-model="traceId" placeholder="traceId（精确）" />
      <input v-model="userId" placeholder="用户 ID" />
      <button type="submit" :disabled="loading">查询</button>
      <button type="button" class="ghost" @click="traceId = ''; userId = ''; page = 1; load()">重置</button>
    </form>

    <p v-if="error" class="error">{{ error }}</p>
    <table class="grid">
      <thead>
        <tr><th>时间</th><th>用户</th><th>意图</th><th>来源</th><th>检索</th><th>总耗时</th><th>traceId</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id" @click="openDetail(row)">
          <td>{{ (row.createdAt || '').replace('T', ' ').slice(0, 19) }}</td>
          <td>{{ row.userId ?? '-' }}</td>
          <td>{{ row.intent || '-' }}</td>
          <td>{{ row.sourceCount }}</td>
          <td>{{ row.retrievalMs ?? '-' }} ms</td>
          <td>{{ row.totalMs ?? '-' }} ms</td>
          <td class="mono">{{ (row.traceId || '').slice(0, 12) }}</td>
        </tr>
        <tr v-if="!rows.length && !loading"><td colspan="7" class="empty">暂无记录</td></tr>
      </tbody>
    </table>

    <div class="pager">
      <button :disabled="page <= 1" @click="page--; load()">上一页</button>
      <span>第 {{ page }} 页 / 共 {{ total }} 条</span>
      <button :disabled="page * size >= total" @click="page++; load()">下一页</button>
    </div>

    <aside v-if="detail" class="detail">
      <header>
        <h2>本次问答</h2>
        <button class="ghost" @click="detail = null">关闭</button>
      </header>
      <p v-if="detailError" class="error">{{ detailError }}</p>
      <dl>
        <dt>问题摘要</dt><dd>{{ detail.questionExcerpt || '—' }}</dd>
        <dt>意图</dt><dd>{{ detail.intent || '—' }}</dd>
        <dt>来源条数</dt><dd>{{ detail.sourceCount }}</dd>
        <dt>检索耗时</dt><dd>{{ detail.retrievalMs ?? '-' }} ms</dd>
        <dt>排序方式</dt><dd>{{ rerankLabel(detail.rerankState) }}</dd>
        <dt>总耗时</dt><dd>{{ detail.totalMs ?? '-' }} ms</dd>
        <dt>降级</dt><dd>{{ detail.degraded || '无' }}</dd>
        <dt>traceId</dt><dd class="mono">{{ detail.traceId }}</dd>
      </dl>
    </aside>
  </section>
</template>

<style scoped>
.trace-page { padding: 24px; max-width: 1080px; margin: 0 auto; }
.head h1 { font-size: 20px; margin: 0 0 4px; }
.hint { color: #666; font-size: 13px; margin: 0 0 16px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 16px; }
.stat { border: 1px solid #e5e5e5; border-radius: 8px; padding: 10px 12px; }
.stat span { display: block; font-size: 12px; color: #666; }
.stat strong { font-size: 16px; }
.filters { display: flex; gap: 8px; margin-bottom: 12px; }
.filters input { flex: 1; padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; }
button { padding: 8px 14px; border: 1px solid #d85a30; background: #d85a30; color: #fff; border-radius: 6px; cursor: pointer; }
button.ghost { background: #fff; color: #333; border-color: #ddd; }
button:disabled { opacity: .5; cursor: not-allowed; }
.grid { width: 100%; border-collapse: collapse; font-size: 13px; }
.grid th, .grid td { border-bottom: 1px solid #eee; padding: 8px 6px; text-align: left; }
.grid tbody tr { cursor: pointer; }
.grid tbody tr:hover { background: #faf7f5; }
.mono { font-family: ui-monospace, monospace; }
.empty { text-align: center; color: #888; }
.pager { display: flex; align-items: center; gap: 12px; margin: 12px 0 20px; font-size: 13px; }
.detail { border: 1px solid #e5e5e5; border-radius: 10px; padding: 16px; }
.detail header { display: flex; justify-content: space-between; align-items: center; }
.detail h2 { font-size: 16px; margin: 0; }
.detail dl { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; font-size: 13px; margin: 12px 0 0; }
.detail dt { color: #666; }
.detail dd { margin: 0; word-break: break-all; }
.error { color: #a32d2d; font-size: 13px; }
</style>
