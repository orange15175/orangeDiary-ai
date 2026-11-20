<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { listNotifications, readNotification } from '../api/notifications'
import UserAvatar from './UserAvatar.vue'
import emptyOtter from '../assets/otter/community-message-otter.png'
const emit = defineEmits(['open', 'read'])
const items = ref([]), page = ref(0), more = ref(false), loading = ref(false), error = ref(''), opening = ref(null)
const type = ref('')
let version = 0
function filter(value) { type.value = value; items.value = []; page.value = 0; more.value = false; load(true) }
onUnmounted(() => { ++version })
async function load(reset = false) {
  if (loading.value && !reset) return
  const request = ++version; loading.value = true; error.value = ''
  try { const data = await listNotifications(reset ? 1 : page.value + 1, type.value)
    if (request !== version) return
    items.value = reset ? data.records : [...items.value, ...data.records]; page.value = data.current; more.value = data.current * data.size < data.total
  } catch(e) { if(request === version) error.value = e.message || '消息加载失败' }
  finally { if(request === version) loading.value = false }
}
async function open(item) {
  if (opening.value) return
  opening.value = item.id
  try { if(!item.readAt) { await readNotification(item.id); item.readAt = new Date().toISOString(); emit('read') }
    if(!item.unavailableReason && item.postId) emit('open', item)
  } catch(e) { error.value = e.message || '打开消息失败' }
  finally { opening.value = null }
}
const date = value => new Date(value).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit',hour:'2-digit',minute:'2-digit' })
onMounted(() => load(true))
defineExpose({ refresh: () => load(true) })
</script>
<template>
<section class="inbox" aria-label="消息列表">
  <div class="inbox-heading"><div><h1>消息</h1><p>每一次回应，都值得看见</p></div><button type="button" :disabled="loading" @click="load(true)">刷新</button></div>
  <nav class="message-filters" aria-label="消息类型"><button v-for="option in [{value:'',label:'全部'},{value:'COMMENT',label:'评论消息'},{value:'SYSTEM',label:'系统通知'}]" :key="option.value" type="button" :aria-pressed="type === option.value" @click="filter(option.value)">{{ option.label }}</button></nav>
  <p v-if="error" class="inbox-state" role="alert">{{ error }} <button type="button" @click="load(!items.length)">重试</button></p>
  <p v-if="loading && !items.length" class="inbox-state" aria-live="polite">正在加载消息…</p>
  <div v-else-if="!items.length && !error" class="inbox-empty"><img :src="emptyOtter" alt="等待消息的海獭" /><h2>暂时没有消息</h2><p>评论回应和系统通知，都会在这里告诉你</p></div>
  <button v-for="item in items" :key="item.id" type="button" class="notification" :class="{unread:!item.readAt}" :disabled="opening === item.id" @click="open(item)">
    <span v-if="item.type === 'SYSTEM'" class="system-avatar" aria-label="系统通知">通</span>
    <UserAvatar v-else :src="item.actorAvatar" width="42" height="42" alt="评论者头像" />
    <span class="notification-body"><span class="notification-heading"><strong>{{ item.type === 'SYSTEM' ? '系统通知' : item.actorName }}</strong><time>{{ date(item.createdAt) }}</time><i v-if="!item.readAt" aria-label="未读"></i></span><span class="action-copy">{{ item.type === 'SYSTEM' ? item.title : '评论了你的帖子' }}</span><span class="comment-copy">{{ item.type === 'SYSTEM' ? item.content : (item.unavailableReason || item.content) }}</span><span v-if="item.postExcerpt" class="post-excerpt">我的帖子 · {{ item.postExcerpt }}</span><span class="notification-hint">{{ item.unavailableReason || (item.postId ? (item.type === 'SYSTEM' ? '查看帖子 ›' : '查看评论 ›') : '点击标为已读') }}</span></span>
  </button>
  <button v-if="more" class="inbox-more" type="button" :disabled="loading" @click="load()">{{ loading ? '加载中…' : '加载更多消息' }}</button>
</section>
</template>
<style scoped>
.message-filters{display:flex;gap:8px;margin-bottom:18px}.message-filters button{min-height:44px;border:1px solid var(--line);border-radius:22px;padding:0 16px;background:var(--surface);color:var(--muted)}.message-filters button[aria-pressed="true"]{background:var(--brand-soft);color:var(--brand-strong);border-color:var(--brand-strong)}.system-avatar{display:grid;place-items:center;width:42px;height:42px;flex:none;border-radius:50%;background:var(--brand-soft);color:var(--brand-strong);font-weight:650}
.inbox-heading{display:flex;align-items:center;justify-content:space-between;margin:10px 0 22px}.inbox h1{margin:0;font-size:22px}.inbox-heading p{margin:7px 0 0;color:var(--muted);font-size:12px}.inbox-heading button,.inbox-state button,.inbox-more{min-height:44px;border:0;background:transparent;color:var(--brand-strong);font:inherit;font-size:13px}.notification{display:flex;gap:12px;width:100%;padding:18px 14px;margin-bottom:12px;text-align:left;border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--ink);font:inherit}.notification.unread{border-color:#cfbfdc;background:#fcf9fe}.notification-body{display:grid;gap:7px;min-width:0;flex:1}.notification-heading{display:flex;align-items:center;gap:8px}.notification-heading strong{font-size:14px;overflow-wrap:anywhere}.notification-heading time{margin-left:auto;font-size:10px;color:var(--muted);white-space:nowrap}.notification-heading i{width:7px;height:7px;background:var(--brand);border-radius:50%;flex:none}.action-copy,.notification-hint{font-size:11px;color:var(--muted)}.comment-copy{font-size:14px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.post-excerpt{font-size:12px;line-height:1.6;background:var(--bg,#f7f4ed);padding:9px;border-radius:7px;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.notification-hint{color:var(--brand-strong);margin-top:3px}.inbox-state{padding:20px 0;text-align:center;color:var(--muted);font-size:13px}.inbox-empty{text-align:center;padding:40px 0}.inbox-empty img{width:min(230px,70%)}.inbox-empty h2{font-size:20px}.inbox-empty p{font-size:13px;color:var(--muted)}.inbox-more{display:block;margin:auto}button:focus-visible{outline:2px solid var(--brand-strong);outline-offset:3px}
</style>
