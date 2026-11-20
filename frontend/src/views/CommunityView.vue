<script setup>
import { useRouter } from 'vue-router'
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { showConfirmDialog, showImagePreview, showToast } from 'vant'
import AppBottomNav from '../components/AppBottomNav.vue'
import { useAuthStore } from '../stores/auth'
import { createPost, createPostComment, deletePostComment, deletePost, likePost, listMyPosts, listPostComments, listPosts, loadPostImage, unlikePost, uploadPostImage, reportPost, reportPostComment } from '../api/posts'
import UserAvatar from '../components/UserAvatar.vue'
import CommunityReportDialog from '../components/CommunityReportDialog.vue'
import CommentInbox from '../components/CommentInbox.vue'
import { unreadNotifications } from '../api/notifications'
import { getPost, getPostComment } from '../api/posts'
import { submitIdempotently } from '../api/idempotency'
import { uploadPostImages } from '../utils/uploadPostImages'
import '../assets/otter-theme.css'
import heroOtter from '../assets/otter/bowl.png'
import publishOtter from '../assets/otter/community-publish-otter.png'
import messageOtter from '../assets/otter/community-message-otter.png'

const unreadCount = ref(0), inbox = ref(null), focusedComment = ref(null), notificationPost = ref(false)
let notificationTimer
async function refreshUnread() {
  if (document.visibilityState === 'hidden') return
  try { const data = await unreadNotifications(); if (!disposed) { const changed = unreadCount.value !== (data.count || 0); unreadCount.value = data.count || 0; if (changed && isMessage.value) inbox.value?.refresh() } } catch { /* Retry on next refresh; never interrupt the feed. */ }
}
function onForeground() { if(document.visibilityState === 'visible') { void refreshUnread(); if(isMessage.value) inbox.value?.refresh() } }
async function openNotification(item) {
  try {
    const [post, comment] = await Promise.all([getPost(item.postId), item.commentId ? getPostComment(item.postId, item.commentId) : Promise.resolve(null)])
    ++feedVersion; loading.value = false; clearImageUrls(); commentsByPost.value = {}
    activeTab.value = '我的'; feedMode.value = 'mine'; notificationPost.value = true
    posts.value = [post]; page.value = 0; hasMore.value = false; feedError.value = ''
    focusedComment.value = comment?.id; expandedCommentsPostId.value = post.id
    if (post.status !== 'hidden') await loadComments(post, true)
    const state = commentState(post.id)
    if (comment && !state.records.some(c => c.id === comment.id)) updateCommentState(post.id, { records: [comment, ...state.records] })
    void hydratePostImages([post]); await nextTick()
    document.getElementById(`comment-${comment?.id}`)?.scrollIntoView({block:'center',behavior:'smooth'})
  } catch(e) { showToast(e.message || '内容已不可查看'); inbox.value?.refresh() }
}
const activeTab = ref('推荐')
const screen = ref('feed')
const draft = ref('')
const auth = useAuthStore()
const router = useRouter()
const postTypes = [
  { label: '饮食打卡', value: 'diet_checkin' },
  { label: '运动记录', value: 'exercise' },
  { label: '体重记录', value: 'weight' },
  { label: '日常分享', value: 'daily_share' }
]
const selectedType = ref('daily_share')
const visibility = ref('public')
const posts = ref([])
const page = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const submitting = ref(false)
const publishStage = ref('')
const publishError = ref('')
const publishLabel = computed(() => {
  if (!submitting.value) return '发布'
  if (publishStage.value === 'saving') return '正在保存动态…'
  return `正在上传图片 ${selectedFiles.value.filter(item => item.uploaded).length}/${selectedFiles.value.length}`
})
const deletingId = ref(null)
const pendingLikeIds = ref(new Set())
const expandedCommentsPostId = ref(null)
const commentsByPost = ref({})
const submittingCommentPostId = ref(null)
const selectedFiles = ref([])
const imageInput = ref(null)
const imageUrls = ref(new Map())
const imageObjectUrls = new Set()
const failedImages = ref(new Set())
let feedVersion = 0
let disposed = false
const feedError = ref('')
const reportTarget = ref(null)
const reportBusy = ref(false)
const reportError = ref('')
const feedMode = ref('public')
const tabs = ['推荐', '我的', '消息']
const isMessage = computed(() => activeTab.value === '消息')
const isFollow = computed(() => activeTab.value === '我的')
const headerTitle = computed(() => isMessage.value ? '消息' : isFollow.value ? '我的动态' : '岛上的新鲜事')

function clearImageUrls() {
  imageObjectUrls.forEach((url) => URL.revokeObjectURL(url))
  imageObjectUrls.clear()
  imageUrls.value = new Map()
}

async function hydratePostImages(items, version = feedVersion) {
  await Promise.all(items.flatMap((post) => (post.imageUrls || []).map(async (url) => {
    if (imageUrls.value.has(url)) return
    try {
      const blob = await loadPostImage(url)
      if (disposed || version !== feedVersion) return
      const objectUrl = URL.createObjectURL(blob)
      imageObjectUrls.add(objectUrl)
      imageUrls.value.set(url, objectUrl)
      failedImages.value.delete(url)
    } catch {
      if (!disposed && version === feedVersion) failedImages.value.add(url)
    }
  })))
}

async function loadFeed(reset = false) {
  if (loading.value && !reset) return
  const version = reset ? ++feedVersion : feedVersion
  const requestedPage = reset ? 1 : page.value + 1
  loading.value = true
  feedError.value = ''
  if (reset) {
    posts.value = []
    page.value = 0
    hasMore.value = false
    clearImageUrls()
    failedImages.value.clear()
    commentsByPost.value = {}
    expandedCommentsPostId.value = null
  }
  try {
    const result = feedMode.value === 'mine'
      ? await listMyPosts(requestedPage, 10)
      : await listPosts(requestedPage, 10)
    if (disposed || version !== feedVersion) return
    const records = result.records || []
    posts.value = reset ? records : [...posts.value, ...records]
    page.value = requestedPage
    hasMore.value = page.value * (result.size || 10) < result.total
    void hydratePostImages(records, version)
  } catch (error) {
    if (!disposed && version === feedVersion) feedError.value = error.message || '帖子加载失败'
  } finally {
    if (!disposed && version === feedVersion) loading.value = false
  }
}

function openTab(tab) {
  notificationPost.value = false; focusedComment.value = null
  void refreshUnread()
  screen.value = 'feed'
  activeTab.value = tab
  feedMode.value = tab === '我的' ? 'mine' : 'public'
  if (!isMessage.value) loadFeed(true)
  else { ++feedVersion; loading.value = false; clearImageUrls() }
}
function openPublish() { screen.value = 'publish' }
function chooseImages(event) {
  if (submitting.value) return
  const incoming = Array.from(event.target.files || [])
  const remaining = 9 - selectedFiles.value.length
  if (incoming.length > remaining) showToast('最多添加9张图片')
  const accepted = incoming.filter((file) => ['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type) && file.size > 0 && file.size <= 10 * 1024 * 1024).slice(0, remaining)
  if (accepted.length !== incoming.length) showToast('仅支持10MB以内的图片')
  selectedFiles.value.push(...accepted.map((file) => ({ file, preview: URL.createObjectURL(file), uploaded: null, state: 'pending', progress: 0 })))
  event.target.value = ''
}
function removeImage(index) {
  if (submitting.value) return
  const [removed] = selectedFiles.value.splice(index, 1)
  if (removed) URL.revokeObjectURL(removed.preview)
}
function postPayload() {
  return { content: draft.value.trim(), postType: selectedType.value, visibility: visibility.value }
}
async function publish() {
  if (submitting.value) return
  if (!draft.value.trim()) { showToast('先写下想分享的内容吧'); return }
  if (draft.value.trim().length > 2000) { showToast('内容不能超过2000字'); return }
  submitting.value = true
  publishError.value = ''
  publishStage.value = 'uploading'
  try {
    await uploadPostImages(selectedFiles.value, uploadPostImage)
    publishStage.value = 'saving'
    const payload = {
      ...postPayload(),
      images: selectedFiles.value.map(({ file, uploaded }) => ({ key: uploaded.key, originalName: file.name }))
    }
    await submitIdempotently('post-create', payload, payload, (body, requestId) => createPost(body, requestId))
    selectedFiles.value.forEach((item) => URL.revokeObjectURL(item.preview))
    selectedFiles.value = []
    draft.value = ''
    screen.value = 'feed'
    activeTab.value = visibility.value === 'private' ? '我的' : '推荐'
    feedMode.value = visibility.value === 'private' ? 'mine' : 'public'
    visibility.value = 'public'
    showToast('发布成功')
    void loadFeed(true)
  } catch (error) {
    const timedOut = ['ECONNABORTED', 'ETIMEDOUT'].includes(error.cause?.code)
    publishError.value = timedOut
      ? (publishStage.value === 'uploading' ? '图片上传超时，已上传的图片会保留，点击发布可重试剩余图片。' : '保存响应超时，点击发布可安全重试，不会重复发布。')
      : (error.message || '发布失败，请重试')
    showToast(publishError.value)
  } finally {
    submitting.value = false
  }
}
async function removePost(post) {
  if (deletingId.value) return
  try {
    await showConfirmDialog({ title: '删除帖子', message: '删除后无法恢复，确定删除？', confirmButtonText: '删除' })
  } catch { return }
  if (deletingId.value) return
  deletingId.value = post.id
  try {
    await deletePost(post.id)
    await loadFeed(true)
    showToast('帖子已删除')
  } catch (error) { showToast(error.message || '删除失败') }
  finally { deletingId.value = null }
}
function commentState(postId) {
  return commentsByPost.value[postId] || { records: [], page: 0, hasMore: false, loading: false, loaded: false, error: '', draft: '' }
}
function updateCommentState(postId, changes) {
  commentsByPost.value = { ...commentsByPost.value, [postId]: { ...commentState(postId), ...changes } }
}
function setCommentDraft(postId, value) { updateCommentState(postId, { draft: value }) }
function mayInteract(post) { return post.status !== 'hidden' && (post.visibility !== 'private' || post.authorId === auth.user?.id) }
async function toggleLike(post) {
  if (!mayInteract(post) || pendingLikeIds.value.has(post.id)) return
  const liked = Boolean(post.likedByCurrentUser)
  const oldCount = post.likeCount || 0
  pendingLikeIds.value = new Set(pendingLikeIds.value).add(post.id)
  post.likedByCurrentUser = !liked
  post.likeCount = Math.max(0, oldCount + (liked ? -1 : 1))
  try {
    const result = liked ? await unlikePost(post.id) : await likePost(post.id)
    post.likedByCurrentUser = result.liked
    post.likeCount = result.likeCount
  } catch (error) {
    post.likedByCurrentUser = liked
    post.likeCount = oldCount
    showToast(error.message || '点赞操作失败')
  } finally {
    const pending = new Set(pendingLikeIds.value)
    pending.delete(post.id)
    pendingLikeIds.value = pending
  }
}
async function loadComments(post, reset = false) {
  const state = commentState(post.id)
  if (state.loading || (!reset && !state.hasMore && state.loaded)) return
  const requestedPage = reset ? 1 : state.page + 1
  updateCommentState(post.id, { loading: true, error: '', ...(reset ? { records: [], page: 0, hasMore: false } : {}) })
  try {
    const result = await listPostComments(post.id, requestedPage, 10)
    const current = commentState(post.id)
    const records = result.records || []
    updateCommentState(post.id, {
      records: reset ? records : [...new Map([...current.records, ...records].map((comment) => [comment.id, comment])).values()],
      page: result.current || requestedPage,
      hasMore: (result.current || requestedPage) * (result.size || 10) < result.total,
      loaded: true
    })
    post.commentCount = result.total
  } catch (error) {
    updateCommentState(post.id, { error: error.message || '评论加载失败' })
  } finally {
    updateCommentState(post.id, { loading: false })
  }
}
async function toggleComments(post) {
  if (!mayInteract(post)) return
  if (expandedCommentsPostId.value === post.id) {
    expandedCommentsPostId.value = null
    return
  }
  expandedCommentsPostId.value = post.id
  const state = commentState(post.id)
  if (!state.loaded) await loadComments(post, true)
}
async function submitComment(post) {
  if (submittingCommentPostId.value !== null) return
  const state = commentState(post.id)
  const content = state.draft.trim()
  if (!content) { showToast('先写下评论内容'); return }
  if (content.length > 500) { showToast('评论不能超过500字'); return }
  submittingCommentPostId.value = post.id
  try {
    const comment = await createPostComment(post.id, content)
    const current = commentState(post.id)
    updateCommentState(post.id, { records: [comment, ...current.records], draft: '', loaded: true })
    post.commentCount = (post.commentCount || 0) + 1
    showToast('评论已发布')
  } catch (error) {
    showToast(error.message || '评论发布失败')
  } finally {
    submittingCommentPostId.value = null
  }
}
async function removeComment(post, comment) {
  if (submittingCommentPostId.value !== null) return
  try {
    await showConfirmDialog({ title: '删除评论', message: '确定删除这条评论？', confirmButtonText: '删除' })
  } catch { return }
  submittingCommentPostId.value = post.id
  try {
    await deletePostComment(post.id, comment.id)
    const state = commentState(post.id)
    updateCommentState(post.id, { records: state.records.filter((item) => item.id !== comment.id) })
    post.commentCount = Math.max(0, (post.commentCount || 0) - 1)
    showToast('评论已删除')
  } catch (error) {
    showToast(error.message || '删除评论失败')
  } finally {
    submittingCommentPostId.value = null
  }
}
async function submitReport(payload) {
  if (!reportTarget.value || reportBusy.value) return
  reportBusy.value = true; reportError.value = ''
  try {
    const target = reportTarget.value
    if (target.commentId) await reportPostComment(target.postId, target.commentId, payload)
    else await reportPost(target.postId, payload)
    reportTarget.value = null
    showToast('举报已提交，平台会进行核实')
  } catch (error) { reportError.value = error.message || '举报提交失败' }
  finally { reportBusy.value = false }
}
function formatDate(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function formatCommentDate(value) { return formatDate(value) }
function mayDelete(post) { return post.authorId === auth.user?.id }
function previewImages(post, url) {
  const images = (post.imageUrls || []).map((item) => imageUrls.value.get(item)).filter(Boolean)
  if (images.length) showImagePreview({ images, startPosition: Math.max(0, images.indexOf(imageUrls.value.get(url))) })
}

onMounted(() => {
  void loadFeed(true); void refreshUnread()
  notificationTimer = setInterval(refreshUnread, 30000)
  document.addEventListener('visibilitychange', onForeground)
})
onUnmounted(() => {
  disposed = true
  clearInterval(notificationTimer)
  document.removeEventListener('visibilitychange', onForeground)
  ++feedVersion
  clearImageUrls()
  selectedFiles.value.forEach((item) => URL.revokeObjectURL(item.preview))
})
</script>

<template>
  <div class="otter-page community-shell">
    <main v-if="screen === 'feed'" class="community-page">
      <header class="community-header">
        <nav class="community-tabs" role="tablist" aria-label="社区频道">
          <button v-for="tab in tabs" :id="`community-tab-${tab}`" :key="tab" type="button" role="tab" :aria-selected="activeTab === tab" :class="{ selected: activeTab === tab }" @click="openTab(tab)">{{ tab }}<span v-if="tab === '消息' && unreadCount" class="unread-badge" :aria-label="`${unreadCount}条未读消息`">{{ unreadCount > 99 ? '99+' : unreadCount }}</span></button>
        </nav>
        <button v-if="auth.user?.role === 'admin'" class="admin-entry" type="button" @click="router.push('/admin/community')">社区管理</button>
      </header>


      <template v-if="!isMessage">
        <section v-if="!isFollow" class="community-welcome" aria-label="社区介绍"><div class="welcome-copy"><span class="eyebrow">海獭小岛</span><h1>好好吃饭，好好生活</h1><p>把日常，分享成温暖</p></div><img :src="heroOtter" alt="抱碗的海獭" /><svg class="welcome-wave" viewBox="0 0 400 30" preserveAspectRatio="none" aria-hidden="true"><path d="M0 16Q50 0 100 16T200 16T300 16T400 16M0 24Q50 8 100 24T200 24T300 24T400 24" /></svg></section>
        <section id="community-feed" class="community-feed" role="tabpanel" :aria-labelledby="`community-tab-${activeTab}`">
          <div class="feed-heading"><h2>{{ notificationPost ? '消息关联帖子' : headerTitle }}</h2><button v-if="notificationPost" class="back-inbox" type="button" @click="openTab('消息')">返回消息</button></div>
          <p v-if="feedError" class="feed-state" role="alert">{{ feedError }} <button type="button" @click="loadFeed(!posts.length)">重试</button></p>
          <p v-else-if="loading && !posts.length" class="feed-state" aria-live="polite">正在加载…</p>
          <article v-for="post in posts" :key="post.id" class="post-placeholder">
            <div class="post-heading">
              <UserAvatar class="post-avatar" :src="post.authorHidden ? null : post.authorAvatar" alt="帖子作者头像" />
              <div class="post-author"><strong>{{ post.authorName }}</strong><span>{{ formatDate(post.publishedAt) }} · {{ post.visibility === 'private' ? '仅自己可见' : '公开' }}</span></div>
              <button v-if="mayDelete(post)" class="post-delete" type="button" :disabled="deletingId === post.id" aria-label="删除帖子" @click="removePost(post)"><van-icon name="delete-o" /></button>
            </div>
            <span class="post-type-label">{{ postTypes.find(type => type.value === post.postType)?.label || '日常分享' }}</span>
            <p class="post-copy">{{ post.content }}</p>
            <div v-if="post.imageUrls?.length" class="post-image-grid" :class="`count-${post.imageUrls.length}`">
              <button v-for="url in post.imageUrls" :key="url" class="post-image" type="button" :aria-label="imageUrls.has(url) ? '查看图片' : '重新加载图片'" @click="imageUrls.has(url) ? previewImages(post, url) : hydratePostImages([{ imageUrls: [url] }])">
                <img v-if="imageUrls.has(url)" :src="imageUrls.get(url)" alt="帖子图片" loading="lazy" />
                <span v-else>{{ failedImages.has(url) ? '加载失败，点击重试' : '图片加载中…' }}</span>
              </button>
            </div>
            <p v-if="post.status === 'hidden'" class="moderation-banner" role="status"><strong>已下架 · 暂停互动</strong><br />原因：{{ post.moderationReason }}<br /><small>你仍可查看或删除这条动态</small></p>
            <div class="post-actions">
              <button type="button" :class="{ liked: post.likedByCurrentUser }" :disabled="pendingLikeIds.has(post.id) || !mayInteract(post)" :aria-label="post.likedByCurrentUser ? '取消点赞' : '点赞'" @click="toggleLike(post)"><van-icon :name="post.likedByCurrentUser ? 'like' : 'like-o'" /><span>{{ post.likeCount || 0 }} 赞</span></button>
              <button type="button" :aria-expanded="expandedCommentsPostId === post.id" :disabled="!mayInteract(post)" @click="toggleComments(post)"><van-icon name="chat-o" /><span>{{ post.commentCount || 0 }} 评论</span></button>
              <button v-if="post.authorId !== auth.user?.id && post.visibility === 'public' && post.status !== 'hidden'" type="button" @click="reportTarget = { postId: post.id }; reportError = ''"><van-icon name="warning-o" /><span>举报</span></button>
            </div>
            <section v-if="expandedCommentsPostId === post.id && mayInteract(post)" class="post-comments" :aria-label="`${post.authorName}的帖子评论`">
              <form class="comment-composer" @submit.prevent="submitComment(post)">
                <textarea :value="commentState(post.id).draft" maxlength="500" placeholder="写下友善的评论…" aria-label="评论内容" @input="setCommentDraft(post.id, $event.target.value)"></textarea>
                <div class="comment-composer-footer"><span>{{ commentState(post.id).draft.length }}/500</span><button type="submit" :disabled="submittingCommentPostId === post.id">{{ submittingCommentPostId === post.id ? '发送中…' : '发送' }}</button></div>
              </form>
              <p v-if="commentState(post.id).error" class="comment-state" role="alert">{{ commentState(post.id).error }} <button type="button" @click="loadComments(post, true)">重试</button></p>
              <p v-else-if="commentState(post.id).loading && !commentState(post.id).records.length" class="comment-state">正在加载评论…</p>
              <p v-else-if="commentState(post.id).loaded && !commentState(post.id).records.length" class="comment-state">还没有评论</p>
              <article v-for="comment in commentState(post.id).records" :key="comment.id" :id="`comment-${comment.id}`" class="post-comment" :class="{'focused-comment':focusedComment === comment.id}">
                <UserAvatar class="comment-avatar" :src="comment.authorAvatar" alt="评论者头像" />
                <div class="comment-body"><div class="comment-heading"><strong>{{ comment.authorName }}</strong><time>{{ formatCommentDate(comment.createdAt) }}</time><button v-if="comment.authorId === auth.user?.id" type="button" aria-label="删除评论" :disabled="submittingCommentPostId === post.id" @click="removeComment(post, comment)"><van-icon name="delete-o" /></button><button v-else type="button" aria-label="举报评论" @click="reportTarget = { postId: post.id, commentId: comment.id }; reportError = ''"><van-icon name="warning-o" /></button></div><p>{{ comment.content }}</p></div>
              </article>
              <button v-if="commentState(post.id).hasMore" class="comments-more" type="button" :disabled="commentState(post.id).loading" @click="loadComments(post)">{{ commentState(post.id).loading ? '加载中…' : '加载更多评论' }}</button>
            </section>
          </article>
          <p v-if="!loading && !feedError && !posts.length" class="community-footnote">这里还没有帖子，发布第一份分享吧</p>
          <button v-if="hasMore" class="load-more" type="button" :disabled="loading" @click="loadFeed()">{{ loading ? '加载中…' : '加载更多' }}</button>
        </section>
      </template>
      <CommentInbox v-else ref="inbox" @open="openNotification" @read="refreshUnread" />
    <CommunityReportDialog v-if="reportTarget" :busy="reportBusy" :error="reportError" :target-label="reportTarget?.commentId ? '评论' : '动态'" @close="reportTarget = null" @submit="submitReport" />
    </main>

    <main v-else class="community-page publish-page">
      <header class="publish-header"><button type="button" :disabled="submitting" @click="screen = 'feed'"><van-icon name="arrow-left" /><span>返回</span></button><h1>发布动态</h1><span></span></header>
      <fieldset class="publish-fields" :disabled="submitting">
        <nav class="publish-types" aria-label="动态类型"><button v-for="item in postTypes" :key="item.value" :class="{ active: selectedType === item.value }" type="button" @click="selectedType = item.value">{{ item.label }}</button></nav>
        <section class="editor-card">
          <h2>分享此刻</h2><textarea v-model="draft" maxlength="2000" placeholder="记录今天的饮食、运动或心情…" aria-label="动态内容"></textarea>
          <input ref="imageInput" class="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple @change="chooseImages" />
          <div class="selected-images">
            <div v-for="(item,index) in selectedFiles" :key="item.preview" class="selected-image"><img :src="item.preview" :alt="item.file.name" /><button type="button" aria-label="移除图片" @click="removeImage(index)"><van-icon name="cross" /></button><span v-if="item.uploaded">已上传</span><span v-else-if="item.state === 'uploading'">{{ item.progress >= 99 ? '处理中…' : `上传 ${item.progress}%` }}</span><span v-else-if="item.state === 'failed'">上传失败</span><span v-else-if="submitting">等待上传</span></div>
            <button v-if="selectedFiles.length < 9" class="image-uploader" type="button" aria-label="添加图片" @click="imageInput?.click()"><van-icon name="plus" /><span>{{ selectedFiles.length }}/9</span></button>
          </div>
          <div class="editor-row visibility-row"><span>公开范围</span><div class="visibility-choice"><button type="button" :class="{ active: visibility === 'public' }" @click="visibility = 'public'">公开</button><button type="button" :class="{ active: visibility === 'private' }" @click="visibility = 'private'">仅自己</button></div></div>
          <div class="editor-note"><span>{{ draft.trim().length }}/2000</span><img :src="publishOtter" alt="海獭陪伴插画" /></div>
        </section>
      </fieldset>
      <footer class="publish-footer"><p v-if="publishError" class="publish-error" role="alert">{{ publishError }}</p><p v-if="submitting" class="publish-progress" role="status" aria-live="polite">{{ publishLabel }}</p><button class="primary-publish" type="button" :disabled="submitting" @click="publish">{{ publishLabel }}</button></footer>
    </main>
    <button v-if="screen === 'feed' && !isMessage" class="publish-button" type="button" aria-label="发布帖子" @click="openPublish"><van-icon name="edit" />发布</button>
    <AppBottomNav v-if="screen === 'feed'" active="community" theme="otter-minimal" />
  </div>
</template>

<style scoped>
.publish-error{margin:0 0 12px;color:#a74438;font-size:13px;line-height:1.7;overflow-wrap:anywhere}.publish-progress{margin:0 0 10px;color:var(--muted);font-size:12px;text-align:center}
.moderation-banner{padding:12px;border:1px solid #e5c9ac;border-radius:8px;background:#fff5e8;color:#885c30;font-size:13px;line-height:1.8;overflow-wrap:anywhere}
.admin-entry{flex:none;min-height:44px;padding:0 10px;border:1px solid var(--line);border-radius:12px;background:var(--surface);color:var(--brand-strong);font:inherit;font-size:12px}.community-header{flex-wrap:wrap}
.unread-badge{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 4px;margin-left:6px;border-radius:10px;background:var(--brand);color:white;font-size:10px;vertical-align:middle}.back-inbox{min-height:40px;border:0;background:transparent;color:var(--brand-strong)}.focused-comment{background:#eee6f4;border-radius:9px;scroll-margin:100px;padding-inline:8px!important}

.publish-fields{min-width:0;margin:0;padding:0;border:0}.publish-fields:disabled{opacity:.7}.post-image{display:grid;place-items:center;aspect-ratio:1;min-width:0;padding:0;overflow:hidden;border:0;border-radius:6px;background:#f1eee8;color:var(--muted);font-size:11px}.post-image-grid.count-1 .post-image{aspect-ratio:4/3}.post-image img{height:100%}.image-uploader .van-icon{font-size:28px}.community-welcome{margin-bottom:24px}
.post-actions{display:flex;gap:8px;margin:0 -16px;padding:6px 8px;border-top:1px solid var(--line)}.post-actions button{display:flex;align-items:center;justify-content:center;gap:7px;min-width:0;min-height:40px;flex:1;border:0;border-radius:6px;background:transparent;color:var(--muted);font-size:13px}.post-actions button .van-icon{font-size:17px}.post-actions button.liked{color:#d85e63}.post-actions button:disabled{opacity:.5}.post-comments{margin:0 -16px;padding:12px 14px 14px;border-top:1px solid var(--line);background:color-mix(in srgb,var(--surface) 94%,var(--brand-soft))}.comment-composer{padding-bottom:12px}.comment-composer textarea{display:block;width:100%;min-height:70px;box-sizing:border-box;resize:vertical;padding:9px 10px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);font:inherit;font-size:13px}.comment-composer-footer{display:flex;align-items:center;justify-content:space-between;padding-top:7px;color:var(--muted);font-size:11px}.comment-composer-footer button{min-width:58px;min-height:32px;border:0;border-radius:6px;background:var(--brand);color:#fff;font-size:12px}.comment-composer-footer button:disabled{opacity:.6}.comment-state{margin:7px 0;padding:8px 0;color:var(--muted);font-size:12px}.comment-state button,.comments-more{border:0;background:transparent;color:var(--brand-strong)}.post-comment{display:flex;gap:9px;padding:10px 0}.post-comment+.post-comment{border-top:1px solid var(--line)}.comment-avatar{display:grid;place-items:center;width:30px;height:30px;flex:none;border:1px solid #e3d9cf;border-radius:50%;background:#f2ede7;color:var(--brand-strong);font-size:12px}.comment-body{min-width:0;flex:1}.comment-heading{display:flex;align-items:center;gap:8px;min-height:25px}.comment-heading strong{overflow:hidden;color:var(--ink);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.comment-heading time{color:var(--muted);font-size:10px}.comment-heading button{display:grid;place-items:center;width:28px;height:28px;margin-left:auto;border:0;background:transparent;color:var(--muted);font-size:15px}.comment-body p{margin:3px 0 0;color:var(--ink);font-size:13px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.comments-more{display:block;min-height:34px;margin:0 auto;font-size:12px}
.community-page{max-width:648px;margin:auto;padding:20px 16px 120px}.community-header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:22px}.community-tabs{display:flex;gap:24px}.community-tabs button{position:relative;min-height:48px;padding:0 0 9px;border:0;background:transparent;color:var(--muted);font-size:21px;font-weight:500}.community-tabs button.selected{color:var(--ink);font-size:27px;font-weight:650}.community-tabs button.selected::after{position:absolute;right:0;bottom:0;left:0;height:5px;border-radius:8px;background:var(--brand);content:''}.search-trigger{width:44px;height:44px;padding:0;border:1px solid var(--line);border-radius:50%;background:var(--surface);color:var(--ink);font-size:23px}.community-search{display:flex;align-items:center;gap:8px;margin-bottom:18px;padding:8px 12px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}.community-search input{min-width:0;flex:1;height:36px;border:0;background:transparent;color:var(--ink);font-size:14px}.community-search button{min-height:36px;border:0;background:transparent;color:var(--brand-strong)}
.community-welcome{position:relative;display:flex;align-items:center;justify-content:space-between;min-height:142px;overflow:hidden;padding:18px 18px 24px;border:1px solid #d6dfd3;border-radius:22px 22px 36px 14px;background:#e6eee5}.welcome-copy{position:relative;z-index:1}.eyebrow{color:#697c6e;font-size:11px;letter-spacing:2px}.community-welcome h1{margin:9px 0 7px;font-size:21px;font-weight:600;letter-spacing:-.6px}.community-welcome p{margin:0;color:#7b8375;font-size:12px}.community-welcome img{position:relative;z-index:1;width:100px;height:110px;object-fit:contain;flex:none;margin-right:-5px}.welcome-wave{position:absolute;right:0;bottom:0;left:0;width:100%;height:25px;fill:none;stroke:#b6cdbd;stroke-width:1}.follow-avatars{display:flex;gap:18px;min-height:58px;margin:6px 0 24px;padding-left:3px}.follow-avatars span{width:49px;height:49px;border:2px solid #d9cde0;border-radius:50%;background:var(--surface)}.category-row,.publish-types{display:flex;gap:8px;margin:20px 0 24px;overflow-x:auto;scrollbar-width:none}.category-row button,.publish-types button{flex:none;min-height:36px;padding:0 13px;border:1px solid var(--line);border-radius:10px;background:transparent;color:var(--muted);font-size:12px}.category-row button.active,.publish-types button.active{border-color:#dbd0e3;background:var(--brand-soft);color:var(--brand-strong);font-weight:600}
.feed-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:13px}.feed-heading h2{margin:0;font-size:16px;font-weight:600}.feed-heading span{color:var(--muted);font-size:11px}.post-placeholder{margin-bottom:16px;padding:18px 16px 0;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:var(--shadow-soft)}.post-heading{display:flex;align-items:center;gap:10px}.avatar-space,.post-avatar{width:38px;height:38px;flex:none;border:1px solid #e6dfd4;border-radius:50%;background:#f1ece3;object-fit:cover}.post-author{display:grid;min-width:0;gap:5px}.post-author strong{overflow:hidden;color:var(--ink);font-size:13px;font-weight:600;text-overflow:ellipsis;white-space:nowrap}.post-author span{color:var(--muted);font-size:11px}.post-delete{display:grid;place-items:center;width:36px;height:36px;margin-left:auto;border:0;background:transparent;color:var(--muted);font-size:18px}.post-delete:disabled{opacity:.5}.post-type-label{display:inline-block;margin-top:13px;padding:4px 8px;border-radius:4px;background:#edf2e9;color:#607963;font-size:11px}.post-copy{margin:11px 0 16px;color:var(--ink);font-size:14px;line-height:1.75;white-space:pre-wrap;overflow-wrap:anywhere}.post-image-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin:0 0 16px}.post-image-grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;background:#f1eee8}.post-image-grid.count-1{grid-template-columns:minmax(0,1fr);max-width:420px}.post-image-grid.count-1 img{aspect-ratio:4/3}.feed-state{padding:24px 8px;color:var(--muted);font-size:13px;text-align:center}.feed-state button,.load-more{border:0;background:transparent;color:var(--brand-strong)}.load-more{display:block;min-height:44px;margin:0 auto 20px}.community-footnote{margin:22px 0;color:var(--muted);font-size:11px;text-align:center}.message-empty{display:grid;justify-items:center;align-content:center;min-height:calc(100dvh - 180px);text-align:center}.message-empty img{width:min(240px,70vw);height:auto}.message-empty h1{margin:18px 0 8px;font-size:22px}.message-empty p{margin:0;color:var(--muted);font-size:14px}.publish-button{position:fixed;z-index:12;right:max(20px,calc((100vw - 648px)/2 + 20px));bottom:calc(104px + env(safe-area-inset-bottom));display:flex;align-items:center;justify-content:center;gap:7px;height:47px;padding:0 19px;border:1px solid #8d79a4;border-radius:24px;background:var(--brand);color:#fff;box-shadow:0 5px 15px #79648f25;font-size:14px}.publish-button .van-icon{font-size:20px}
.publish-page{padding-bottom:30px}.publish-header{display:flex;align-items:center;justify-content:space-between;min-height:48px}.publish-header h1{margin:0;font-size:22px}.publish-header button{border:0;background:transparent;color:var(--muted);font-size:14px}.publish-header button:first-child{font-size:16px}.publish-types{margin-top:18px}.publish-types button{min-width:78px}.editor-card{padding:18px 16px 10px;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:var(--shadow-soft)}.editor-card h2{margin:0 0 14px;font-size:20px}.editor-card textarea{display:block;width:100%;min-height:135px;box-sizing:border-box;resize:vertical;padding:12px;border:1px solid var(--line);border-radius:8px;outline:0;background:var(--surface);color:var(--ink);font:inherit;font-size:14px}.editor-card textarea::placeholder{color:#b2a99f}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;clip-path:inset(50%)}.selected-images{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.selected-image{position:relative;width:82px;height:82px}.selected-image img{width:100%;height:100%;border-radius:6px;object-fit:cover}.selected-image>button{position:absolute;top:3px;right:3px;display:grid;place-items:center;width:24px;height:24px;border:0;border-radius:50%;background:#2229;color:white}.selected-image>span{position:absolute;right:2px;bottom:2px;padding:2px 4px;border-radius:3px;background:#2229;color:white;font-size:9px}.image-uploader{display:grid;justify-items:center;align-content:center;gap:2px;width:82px;height:82px;margin:0;padding:8px;border:1px dashed #cfc5b9;border-radius:6px;background:transparent;color:var(--muted)}.image-uploader strong{font-size:28px;font-weight:300;line-height:1}.image-uploader span{font-size:11px}.editor-row{display:flex;align-items:center;justify-content:space-between;width:100%;min-height:46px;padding:0;border:0;border-top:1px solid #eee7db;background:transparent;color:var(--ink);text-align:left;font-size:14px}.visibility-row{margin-top:14px}.visibility-choice{display:flex;gap:6px}.visibility-choice button{min-height:32px;padding:0 10px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--muted);font-size:12px}.visibility-choice button.active{border-color:var(--brand-strong);background:var(--brand-soft);color:var(--brand-strong)}.editor-note{display:flex;align-items:end;justify-content:space-between;min-height:74px;color:var(--muted);font-size:11px}.editor-note img{width:120px;height:76px;object-fit:contain}.publish-footer{display:flex;align-items:center;gap:24px;margin-top:18px;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}.publish-footer button{border:0;background:transparent;color:var(--muted);font-size:13px}.publish-footer .primary-publish{flex:1;min-height:44px;border-radius:8px;background:var(--brand);color:#fff;font-size:16px}.publish-footer .primary-publish:disabled{opacity:.65}button:focus-visible,textarea:focus-visible{outline:2px solid var(--brand-strong);outline-offset:3px}@media(max-width:350px){.community-page{padding-right:12px;padding-left:12px}.community-tabs{gap:16px}.community-tabs button{font-size:18px}.community-tabs button.selected{font-size:23px}.community-welcome h1{font-size:18px}.community-welcome img{width:80px}.post-image-grid{gap:4px}}
</style>
