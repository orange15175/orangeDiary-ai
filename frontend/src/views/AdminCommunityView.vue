<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getAdminPost, listAdminPosts, listAdminComments, hideAdminPost, restoreAdminPost, removeAdminComment, listCommunityReports, resolveCommunityReport } from '../api/admin'
import { loadPostImage } from '../api/posts'
import ModerationDialog from '../components/ModerationDialog.vue'
import AdminActionLogs from '../components/AdminActionLogs.vue'
import UserAvatar from '../components/UserAvatar.vue'
import '../assets/otter-theme.css'

const route = useRoute(), router = useRouter()
const types = { diet_checkin: '饮食打卡', exercise: '运动记录', weight: '体重记录', daily_share: '日常分享' }
const form = ref({ keyword: '', authorId: '', postType: '', status: '', from: '', until: '' })
const action = ref(null), busy = ref(false), actionError = ref('')
const reportRecords = ref([]), reportLoading = ref(false), reportError = ref(''), reportStatus = ref('pending'), reportPage = ref(1), reportTotal = ref(0)
const reportReasons = { SPAM: '垃圾广告', HARASSMENT: '骚扰辱骂', HEALTH_MISINFORMATION: '不实或危险健康信息', SEXUAL_OR_VIOLENT: '色情或暴力内容', PRIVACY: '侵犯隐私', OTHER: '其他' }
async function submitAction(reason) {
  if(busy.value || !action.value) return
  busy.value = true; actionError.value = ''
  const current = action.value, postId = detail.value?.id
  try {
    if(current.report) { await resolveCommunityReport(current.report.id, current.resolution, reason); await loadCommunityReports() }
    else if(current.comment) await removeAdminComment(postId, current.comment.id, reason)
    else if(current.restore) await restoreAdminPost(postId, reason)
    else await hideAdminPost(postId, reason)
    action.value = null
    if(String(route.query.post) === String(postId)) await openDetail(postId)
  } catch(e) { actionError.value = e.message || '处理失败，请重试' }
  finally { busy.value = false }
}
async function loadCommunityReports() {
  reportLoading.value = true; reportError.value = ''
  try { const data = await listCommunityReports({ page: reportPage.value, size: 20, status: reportStatus.value || undefined }); reportRecords.value = data.records || []; reportTotal.value = data.total || 0 }
  catch (e) { reportError.value = e.message || '举报记录加载失败' }
  finally { reportLoading.value = false }
}
const records = ref([]), total = ref(0), page = ref(1), loading = ref(false), error = ref('')
const detail = ref(null), detailLoading = ref(false), detailError = ref('')
const comments = ref([]), commentPage = ref(0), commentMore = ref(false), commentLoading = ref(false), commentError = ref('')
const images = ref({}), imageErrors = ref({})
let listVersion = 0, detailVersion = 0
const pages = computed(() => Math.max(1, Math.ceil(total.value / 10)))
const date = value => value ? new Date(value).toLocaleString('zh-CN') : '—'
function clearImages() { Object.values(images.value).forEach(URL.revokeObjectURL); images.value = {}; imageErrors.value = {} }
function params() {
  const q = route.query
  return { page: Math.max(1, Number(q.page) || 1), size: 10, keyword: q.keyword || undefined, authorId: q.authorId || undefined, postType: q.postType || undefined, status: q.status || undefined, from: q.from ? `${q.from}T00:00:00` : undefined, until: q.until ? `${q.until}T00:00:00` : undefined }
}
async function load() {
  const version = ++listVersion
  loading.value = true; error.value = ''; records.value = []; total.value = 0
  try {
    const data = await listAdminPosts(params())
    if (version !== listVersion) return
    records.value = data.records || []; total.value = data.total; page.value = data.current
  } catch (e) { if (version === listVersion) error.value = e.message || '帖子加载失败' }
  finally { if (version === listVersion) loading.value = false }
}
function search() {
  if (form.value.from && form.value.until && form.value.from >= form.value.until) { error.value = '结束日期需晚于开始日期'; return }
  const query = Object.fromEntries(Object.entries(form.value).map(([k,v]) => [k, String(v).trim()]).filter(([,v]) => v))
  if (JSON.stringify(route.query) === JSON.stringify(query)) load()
  else router.push({ query })
}
function reset() { form.value = { keyword: '', authorId: '', postType: '', status: '', from: '', until: '' }; search() }
function turn(next) { router.push({ query: { ...route.query, page: next } }) }
async function loadComments() {
  if (!detail.value || commentLoading.value) return
  const version = detailVersion, id = detail.value.id
  commentLoading.value = true; commentError.value = ''
  try {
    const data = await listAdminComments(id, commentPage.value + 1)
    if (version !== detailVersion) return
    comments.value.push(...data.records); commentPage.value = data.current; commentMore.value = data.current * data.size < data.total
  } catch (e) { if (version === detailVersion) commentError.value = e.message || '评论加载失败' }
  finally { if (version === detailVersion) commentLoading.value = false }
}
async function openDetail(id) {
  const version = ++detailVersion
  clearImages(); detail.value = null; detailError.value = ''; detailLoading.value = true
  comments.value = []; commentPage.value = 0; commentMore.value = false; commentLoading.value = false; commentError.value = ''
  try {
    const post = await getAdminPost(id)
    if (version !== detailVersion) return
    detail.value = post
    void loadComments()
    await Promise.all((post.imageUrls || []).map(async url => {
      try { const blob = await loadPostImage(url); if (version === detailVersion) images.value[url] = URL.createObjectURL(blob) }
      catch { if (version === detailVersion) imageErrors.value[url] = true }
    }))
  } catch (e) { if (version === detailVersion) detailError.value = e.message || '帖子已不可查看' }
  finally { if (version === detailVersion) detailLoading.value = false }
}
function closeDetail() { router.push({ query: Object.fromEntries(Object.entries(route.query).filter(([key]) => key !== 'post')) }) }
watch(() => route.query, q => {
  action.value = null; actionError.value = ''
  form.value = { keyword: q.keyword || '', authorId: q.authorId || '', postType: q.postType || '', status: q.status || '', from: q.from || '', until: q.until || '' }
  if(q.logs || q.reports) { ++listVersion; ++detailVersion; clearImages(); if(q.reports) loadCommunityReports(); return }
  if (q.post) { ++listVersion; loading.value = false; openDetail(q.post) }
  else { ++detailVersion; detail.value = null; detailError.value = ''; detailLoading.value = false; clearImages(); load() }
}, { immediate: true })
onUnmounted(() => { ++listVersion; ++detailVersion; clearImages() })
</script>

<template>
  <div class="otter-page admin-shell">
    <main class="admin-page">
      <header class="admin-header"><div><span class="eyebrow">海獭小岛 · 管理员</span><h1>社区管理</h1><p>查看公开动态，了解社区里的每一次分享</p></div><RouterLink to="/community">返回社区</RouterLink></header>
      <nav class="manage-tabs" aria-label="管理功能"><RouterLink to="/admin/community" :class="{selected: !route.query.logs && !route.query.reports}">帖子管理</RouterLink><RouterLink :to="{query: {reports:'1'}}" :class="{selected: route.query.reports}">举报处理</RouterLink><RouterLink :to="{query: {logs:'1'}}" :class="{selected: route.query.logs}">操作日志</RouterLink><RouterLink to="/admin/ai-traces">问答追踪</RouterLink></nav>
      <AdminActionLogs v-if="route.query.logs" />
      <section v-else-if="route.query.reports" class="report-queue">
        <div class="report-toolbar"><h2>社区举报 <small>{{ reportTotal }}</small></h2><select v-model="reportStatus" aria-label="举报状态" @change="reportPage = 1; loadCommunityReports()"><option value="pending">待处理</option><option value="actioned">已处理</option><option value="dismissed">已驳回</option><option value="">全部</option></select></div>
        <p v-if="reportError" class="panel state" role="alert">{{ reportError }} <button type="button" @click="loadCommunityReports">重试</button></p>
        <p v-else-if="reportLoading" class="panel state" role="status">正在加载举报记录…</p>
        <p v-else-if="!reportRecords.length" class="panel state">当前没有举报记录</p>
        <article v-for="report in reportRecords" :key="report.id" class="panel report-row">
          <header><strong>{{ report.targetType === 'POST' ? '帖子' : '评论' }} #{{ report.targetId }} · {{ reportReasons[report.reason] || report.reason }}</strong><span>{{ report.status === 'pending' ? '待处理' : report.status === 'actioned' ? '已处理' : '已驳回' }}</span></header>
          <p class="snapshot">{{ report.targetSnapshot }}</p><p v-if="report.details" class="muted">举报说明：{{ report.details }}</p>
          <p class="muted">举报人：{{ report.reporterName }} · 内容作者：{{ report.targetAuthorName }} · {{ date(report.createdAt) }}</p>
          <p v-if="report.resolutionReason" class="muted">处理说明：{{ report.resolutionReason }}</p>
          <footer v-if="report.status === 'pending'"><button type="button" @click="action = { report, resolution: report.targetType === 'POST' ? 'HIDE_POST' : 'REMOVE_COMMENT' }; actionError = ''">{{ report.targetType === 'POST' ? '下架帖子' : '移除评论' }}</button><button type="button" @click="action = { report, resolution: 'DISMISS' }; actionError = ''">驳回举报</button></footer>
        </article>
        <nav v-if="reportTotal > 20" class="pagination" aria-label="举报分页"><button type="button" :disabled="reportLoading || reportPage <= 1" @click="reportPage--; loadCommunityReports()">上一页</button><span>{{ reportPage }} / {{ Math.ceil(reportTotal / 20) }}</span><button type="button" :disabled="reportLoading || reportPage >= Math.ceil(reportTotal / 20)" @click="reportPage++; loadCommunityReports()">下一页</button></nav>
      </section>
      <template v-else-if="route.query.post">
        <button class="back" type="button" @click="closeDetail">‹ 返回帖子列表</button>
        <p v-if="detailError" class="state" role="alert">{{ detailError }} <button type="button" @click="openDetail(route.query.post)">重试</button></p>
        <p v-if="detailLoading && !detail" class="state" role="status">正在加载帖子…</p>
        <article v-if="detail" class="panel detail">
          <div class="author"><UserAvatar :src="detail.authorAvatar" width="44" height="44" /><div><strong>{{ detail.authorName }}</strong><p>{{ date(detail.publishedAt) }} · {{ types[detail.postType] }}</p></div><span class="badge">{{ detail.status === 'hidden' ? '已下架' : '正常发布' }}</span></div>
          <div class="moderation-toolbar"><p v-if="detail.moderationReason">最近处理原因：{{ detail.moderationReason }}</p><button type="button" :disabled="busy" @click="action = { restore: detail.status === 'hidden' }; actionError = ''">{{ detail.status === 'hidden' ? '恢复帖子' : '下架帖子' }}</button></div>
          <p class="post-content">{{ detail.content }}</p>
          <div v-if="detail.imageUrls?.length" class="images"><div v-for="url in detail.imageUrls" :key="url"><img v-if="images[url]" :src="images[url]" alt="帖子图片" /><span v-else>{{ imageErrors[url] ? '图片暂不可用' : '图片加载中…' }}</span></div></div>
          <p class="muted">帖子 #{{ detail.id }} · 作者 {{ detail.authorId ? `#${detail.authorId}` : '已注销' }} · {{ detail.likeCount }} 赞 · {{ detail.commentCount }} 条评论</p>
          <h2>评论</h2><p v-if="commentError" class="state" role="alert">{{ commentError }} <button type="button" @click="loadComments">重试</button></p>
          <p v-if="!comments.length && !commentLoading && !commentError" class="state">这条动态还没有评论</p>
          <div v-for="comment in comments" :key="comment.id" class="comment"><UserAvatar :src="comment.authorAvatar" width="34" height="34" /><div><strong>{{ comment.authorName }}</strong><time>{{ date(comment.createdAt) }}</time><p>{{ comment.content }}</p><p v-if="comment.status === 'removed'" class="removed-comment">已移除 · {{ comment.moderationReason }}</p><button v-else type="button" :disabled="busy" @click="action = {comment}; actionError = ''">移除违规评论</button></div></div>
          <p v-if="commentLoading" class="state" role="status">正在加载评论…</p>
          <button v-else-if="commentMore" type="button" class="back" @click="loadComments">加载更多评论</button>
        </article>
      </template>
      <template v-else>
        <form class="panel filters" @submit.prevent="search">
          <label class="keyword">帖子关键词<input v-model="form.keyword" maxlength="100" placeholder="搜索正文内容" /></label>
          <label>作者 ID<input v-model="form.authorId" type="number" min="1" step="1" placeholder="精确查找作者" /></label>
          <label>帖子类型<select v-model="form.postType"><option value="">全部类型</option><option v-for="(label, value) in types" :key="value" :value="value">{{ label }}</option></select></label>
          <label>帖子状态<select v-model="form.status"><option value="">全部状态</option><option value="published">正常发布</option><option value="hidden">已下架</option></select></label>
          <label>发布起始日<input v-model="form.from" type="date" /></label><label>发布截止日（不含）<input v-model="form.until" type="date" /></label>
          <div class="filter-actions"><button class="primary" type="submit">查询帖子</button><button type="button" @click="reset">重置</button></div>
        </form>
        <div class="list-heading"><h2>公开帖子 <span>{{ total }}</span></h2><span>公开内容 · 包含管理员下架帖子</span></div>
        <p v-if="error" class="panel state" role="alert">{{ error }} <button type="button" @click="load">重试</button></p>
        <p v-else-if="loading" class="panel state" role="status">正在加载公开帖子…</p>
        <div v-else-if="!records.length" class="panel empty"><h2>没有找到符合条件的帖子</h2><p>试试其他关键词，或放宽筛选范围</p><button type="button" @click="reset">清除筛选</button></div>
        <div v-else class="post-list">
          <article v-for="post in records" :key="post.id" class="panel post-row"><div class="author"><UserAvatar :src="post.authorAvatar" width="40" height="40" /><div><strong>{{ post.authorName }}</strong><p>{{ date(post.publishedAt) }}</p></div><span class="badge">{{ types[post.postType] }} · {{ post.status === 'hidden' ? '已下架' : '正常' }}</span></div><p class="excerpt">{{ post.content }}</p><footer><span>#{{ post.id }} · 作者 {{ post.authorId ? `#${post.authorId}` : '已注销' }} · {{ post.commentCount }} 条评论</span><RouterLink :to="{query: {...route.query, post: post.id}}">查看详情 ›</RouterLink></footer></article>
        </div>
        <nav v-if="total" class="pagination" aria-label="帖子分页"><button type="button" :disabled="loading || page <= 1" @click="turn(page - 1)">上一页</button><span>{{ page }} / {{ pages }}</span><button type="button" :disabled="loading || page >= pages" @click="turn(page + 1)">下一页</button></nav>
      </template>
      <ModerationDialog v-if="action" :title="action.report ? (action.resolution === 'DISMISS' ? '驳回举报' : action.resolution === 'HIDE_POST' ? '下架帖子并结案' : '移除评论并结案') : action.comment ? '移除违规评论' : (action.restore ? '恢复帖子' : '下架帖子')" :busy="busy" :error="actionError" :restore="!!action.restore" :notify="action.resolution !== 'DISMISS'" @cancel="action = null" @submit="submitAction" />
    </main>
  </div>
</template>

<style scoped>
.manage-tabs{display:flex;gap:20px;margin-bottom:22px}.manage-tabs a{color:var(--muted);text-decoration:none;min-height:44px;display:flex;align-items:center;border-bottom:3px solid transparent}.manage-tabs a.selected{color:var(--brand-strong);border-color:var(--brand-strong)}.moderation-toolbar{margin-top:18px;padding:12px;background:var(--brand-soft);border-radius:10px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}.moderation-toolbar p{flex:1;min-width:120px;overflow-wrap:anywhere;font-size:13px}.removed-comment{color:#9b5a34}.comment button{margin-top:10px}
.report-queue{display:grid;gap:12px}.report-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px}.report-toolbar h2{margin:12px 0;font-size:18px}.report-toolbar h2 small{font-size:12px;font-weight:400;color:var(--muted)}.report-toolbar select{width:auto;min-width:130px}.report-row header,.report-row footer{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.report-row header strong{font-size:14px}.report-row header span{font-size:11px;color:var(--brand-strong)}.report-row .snapshot{margin:14px 0;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;font-size:14px}.report-row footer{justify-content:flex-end;margin-top:14px}
.admin-page{max-width:960px;margin:auto;padding:32px 20px 60px;color:var(--ink)}.admin-header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:26px}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--brand-strong)}h1{font-size:28px;margin:8px 0}h2{font-size:18px}.admin-header p,.muted{font-size:12px;color:var(--muted);line-height:1.6}.admin-header a,.post-row a{color:var(--brand-strong);text-decoration:none;min-height:44px;display:inline-flex;align-items:center;flex:none}.panel{border:1px solid var(--line);background:var(--surface);border-radius:16px;box-shadow:var(--shadow-soft);padding:20px}.filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}label{display:grid;gap:8px;font-size:12px;color:var(--muted)}input,select{width:100%;min-width:0;box-sizing:border-box;min-height:44px;border:1px solid var(--line);background:var(--bg,#faf7f0);border-radius:8px;padding:8px 10px;color:var(--ink);font:inherit;font-size:14px}.filter-actions{display:flex;gap:10px;align-items:end}button{min-height:44px;padding:8px 16px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--brand-strong);font:inherit;font-size:13px;cursor:pointer}button.primary{background:var(--brand-strong);color:#fff;border-color:var(--brand-strong)}button:disabled{opacity:.45;cursor:default}.list-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:18px 0 6px}.list-heading>span{font-size:11px;color:var(--muted)}.list-heading h2 span{font-size:12px;font-weight:400;margin-left:8px;color:var(--muted)}.post-list{display:grid;gap:14px}.author{display:flex;gap:12px;align-items:center;min-width:0}.author>div{min-width:0}.author strong{font-size:14px;overflow-wrap:anywhere}.author p{font-size:11px;color:var(--muted);margin:6px 0 0}.badge{margin-left:auto;flex:none;border-radius:5px;background:var(--brand-soft);padding:5px 8px;color:var(--brand-strong);font-size:10px}.excerpt{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.8;margin:18px 0 8px}.post-row footer{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:12px}.post-row footer>span{color:var(--muted);font-size:11px}.pagination{display:flex;align-items:center;justify-content:center;gap:20px;margin-top:24px;font-size:13px}.state,.empty{text-align:center;padding:32px 16px;color:var(--muted);font-size:13px}.empty p{line-height:1.8}.back{margin-bottom:16px}.post-content{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9;font-size:15px;margin:24px 0}.images{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.images>div{aspect-ratio:1;display:grid;place-items:center;border-radius:10px;overflow:hidden;background:var(--bg,#faf7f0);font-size:12px;color:var(--muted)}.images img{width:100%;height:100%;object-fit:contain}.detail h2{border-top:1px solid var(--line);padding-top:22px;margin-top:22px}.comment{display:flex;gap:10px;padding:16px 0;border-bottom:1px solid var(--line)}.comment>div{min-width:0;flex:1}.comment strong{font-size:13px}.comment time{display:block;margin-top:5px;color:var(--muted);font-size:10px}.comment p{font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;margin-bottom:0}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--brand-strong);outline-offset:3px}@media(max-width:600px){.admin-page{padding:22px 14px 40px}.admin-header{align-items:start}h1{font-size:24px}.admin-header p{max-width:200px}.filters{grid-template-columns:repeat(2,minmax(0,1fr));padding:16px;gap:12px}.keyword,.filter-actions{grid-column:1/-1}.filter-actions button{flex:1}.list-heading{align-items:start}.list-heading>span{max-width:110px;text-align:right;line-height:1.6}.post-row,.detail{padding:16px}.author{gap:9px}.post-row footer>span{max-width:180px;line-height:1.6}.images{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
