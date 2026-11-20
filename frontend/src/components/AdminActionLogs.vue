<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { listActionLogs } from '../api/admin'
const actions = { POST_HIDE: '帖子下架', POST_RESTORE: '帖子恢复', COMMENT_REMOVE: '评论移除' }
const filters = ref({ action: '', adminId: '', targetId: '', from: '', until: '' }), records = ref([]), page = ref(1), total = ref(0), loading = ref(false), error = ref('')
let version = 0
async function load(next = 1) {
  const request = ++version; loading.value = true; error.value = ''
  try {
    const f = filters.value
    const data = await listActionLogs({ page: next, size: 10, action: f.action || undefined, adminId: f.adminId || undefined, targetId: f.targetId || undefined, from: f.from ? `${f.from}T00:00:00` : undefined, until: f.until ? `${f.until}T00:00:00` : undefined })
    if (version !== request) return
    records.value = data.records; total.value = data.total; page.value = data.current
  } catch (e) { if (request === version) error.value = e.message || '日志加载失败' }
  finally { if (request === version) loading.value = false }
}
onMounted(() => load())
onUnmounted(() => ++version)
</script>
<template>
  <section aria-label="管理员操作日志">
    <form class="log-filters" @submit.prevent="load()"><label>操作类型<select v-model="filters.action"><option value="">全部操作</option><option v-for="(name, key) in actions" :key="key" :value="key">{{ name }}</option></select></label><label>管理员 ID<input v-model="filters.adminId" type="number" min="1" /></label><label>目标 ID<input v-model="filters.targetId" type="number" min="1" /></label><label>起始日期<input v-model="filters.from" type="date" /></label><label>截止日期（不含）<input v-model="filters.until" type="date" /></label><button type="submit" :disabled="loading">查询日志</button></form>
    <p class="hint">共 {{ total }} 条记录 · 仅管理员可见，记录不可修改或删除</p>
    <p v-if="error" role="alert">{{ error }} <button type="button" @click="load(page)">重试</button></p>
    <p v-else-if="loading" role="status">正在加载日志…</p>
    <p v-else-if="!records.length" class="empty">暂无符合条件的操作记录</p>
    <article v-for="log in records" v-else :key="log.id" class="log"><header><strong>{{ actions[log.actionType] }}</strong><time>{{ new Date(log.createdAt).toLocaleString('zh-CN') }}</time></header><p>管理员 {{ log.adminName || '管理员' }} #{{ log.adminId }} · {{ log.targetType === 'POST' ? '帖子' : '评论' }} #{{ log.targetId }}</p><p class="reason">{{ log.reason }}</p><details><summary>查看操作详情</summary><dl><dt>记录编号</dt><dd>#{{ log.id }}</dd><dt>关联帖子 / 作者</dt><dd>#{{ log.postId }} / #{{ log.authorId }}</dd><dt>状态变化</dt><dd>{{ log.beforeStatus }} → {{ log.afterStatus }}</dd></dl><h3>处理时内容</h3><p class="snapshot">{{ log.snapshot }}</p></details></article>
    <nav v-if="total" aria-label="日志分页"><button type="button" :disabled="loading || page <= 1" @click="load(page - 1)">上一页</button><span>{{ page }} / {{ Math.ceil(total / 10) }}</span><button type="button" :disabled="loading || page * 10 >= total" @click="load(page + 1)">下一页</button></nav>
  </section>
</template>
<style scoped>
.log-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;background:var(--surface);padding:20px;border:1px solid var(--line);border-radius:14px}.log-filters label{display:grid;gap:8px;font-size:12px;color:var(--muted)}input,select{box-sizing:border-box;min-width:0;width:100%;min-height:44px;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit}button{min-height:44px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--brand-strong);font:inherit;padding:8px 14px;cursor:pointer}.log-filters button{align-self:end;background:var(--brand-strong);color:white}.hint{font-size:12px;color:var(--muted);margin:22px 0}.log{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;margin:12px 0}.log header{display:flex;justify-content:space-between;gap:12px;align-items:center}.log strong{font-size:15px}.log time{font-size:11px;color:var(--muted)}.log p{font-size:12px;line-height:1.8;color:var(--muted)}.log .reason{color:var(--ink);font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}.log summary{min-height:44px;align-content:center;font-size:12px;color:var(--brand-strong);cursor:pointer}.log dl{display:grid;grid-template-columns:auto 1fr;gap:10px;font-size:12px}.log dd{margin:0;overflow-wrap:anywhere}.log dt{color:var(--muted)}h3{font-size:13px}.log .snapshot{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--bg);padding:12px;border-radius:8px}nav{display:flex;justify-content:center;align-items:center;gap:20px;margin-top:20px;font-size:13px}.empty{text-align:center;padding:40px;color:var(--muted)}button:disabled{opacity:.5}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--brand-strong);outline-offset:3px}@media(max-width:600px){.log-filters{grid-template-columns:repeat(2,minmax(0,1fr));padding:14px}.log header{align-items:start;flex-direction:column}}
</style>
