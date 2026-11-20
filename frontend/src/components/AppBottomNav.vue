<script setup>
import { useRouter } from 'vue-router'
import { clearFlowReturn } from '../router/flowReturn'
import homeIcon from '../assets/otter/icons/home.svg'
import dietIcon from '../assets/otter/icons/diet.svg'
import aiIcon from '../assets/otter/icons/ai.svg'
import communityIcon from '../assets/otter/icons/community.svg'
import personalIcon from '../assets/otter/icons/personal.svg'

defineProps({ active: { type: String, required: true }, theme: { type: String, default: '' } })
import { aiChatEnabled } from '../config/features'
const router = useRouter()
// 切 tab 等于放弃当前录入流程，清掉连退标记，避免之后保存时退到别的页面。
function go(name) { clearFlowReturn(); router.replace({ name }) }
const navItems = [
  { key: 'home', label: '主页', route: 'diet', icon: homeIcon },
  { key: 'diet', label: '饮食', route: 'diet-daily', icon: dietIcon },
  { key: 'community', label: '社区', route: 'community', icon: communityIcon },
  ...(aiChatEnabled ? [{ key: 'ai', label: 'AI', route: 'ai', icon: aiIcon }] : []),
  { key: 'personal', label: '个人', route: 'home', icon: personalIcon }
]

</script>

<template>
  <nav v-if="theme === 'otter-minimal'" class="minimal-nav" :style="{ gridTemplateColumns: `repeat(${navItems.length}, minmax(0, 1fr))` }" aria-label="主导航">
    <button v-for="item in navItems" :key="item.key" type="button" :class="{ active: active === item.key }" :aria-current="active === item.key ? 'page' : undefined" @click="go(item.route)">
      <img :src="item.icon" alt="" width="22" height="22" /><span>{{ item.label }}</span>
    </button>
  </nav>
  <nav v-else class="bottom-nav" :class="{ 'bottom-nav--otter': theme === 'otter' }" :style="{ gridTemplateColumns: `repeat(${navItems.length}, minmax(0, 1fr))` }" aria-label="主导航">
    <button type="button" :class="{ active: active === 'home' }" @click="go('diet')"><van-icon name="home-o" /><span>主页</span></button>
    <button type="button" :class="{ active: active === 'diet' }" @click="go('diet-daily')"><van-icon name="notes-o" /><span>饮食</span></button>
    <button type="button" :class="{ active: active === 'community' }" @click="go('community')"><van-icon name="friends-o" /><span>社区</span></button>
    <button v-if="aiChatEnabled" type="button" :class="{ active: active === 'ai' }" @click="go('ai')"><van-icon name="chat-o" /><span>AI</span></button>
    <button type="button" :class="{ active: active === 'personal' }" @click="go('home')"><van-icon name="contact" /><span>个人</span></button>
  </nav>
</template>

<style scoped>
.minimal-nav { position: fixed; z-index: 10; right: 0; bottom: 0; left: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); width: min(100%, 648px); margin: auto; min-height: 76px; padding: 8px 8px calc(8px + env(safe-area-inset-bottom)); border-top: 1px solid #e8dfd1; background: #fffcf6; }
.minimal-nav button { display: grid; justify-items: center; align-content: center; gap: 4px; min-height: 60px; border: 0; border-radius: 10px; background: transparent; color: #8b7d70; font-size: 11px; cursor: pointer; }
.minimal-nav img { opacity: .75; }
.minimal-nav button.active { color: #79648f; font-weight: 700; }
.minimal-nav button.active img { opacity: 1; filter: brightness(0) saturate(100%) invert(49%) sepia(12%) saturate(1073%) hue-rotate(228deg) brightness(86%) contrast(83%); }
.minimal-nav button:hover { background: #eee7f0; }
.minimal-nav button:focus-visible { outline: 3px solid #9d8bb7; outline-offset: -3px; }
</style>

<style scoped>
.bottom-nav { position:fixed; z-index:10; right:0; bottom:0; left:0; display:grid; grid-template-columns:repeat(4,1fr); min-height:68px; padding-bottom:env(safe-area-inset-bottom); border-top:1px solid var(--line); background:rgba(255,255,255,.96); box-shadow:0 -7px 18px rgba(46,104,136,.06); backdrop-filter:blur(12px); }.bottom-nav button { position:relative; display:grid; justify-items:center; align-content:center; gap:4px; min-height:64px; border:0; background:transparent; color:var(--muted); font-size:11px; }.bottom-nav :deep(.van-icon) { font-size:20px; }.bottom-nav button.active { color:var(--brand-strong); font-weight:700; }.bottom-nav button.active::before { position:absolute; top:0; width:24px; height:3px; border-radius:0 0 4px 4px; background:var(--brand); content:""; }
</style>

<style scoped>
.bottom-nav--otter { max-width:648px; margin:0 auto; min-height:76px; border:1px solid var(--line); border-radius:14px 14px 0 0; background:var(--surface); box-shadow:none; backdrop-filter:none; }
.bottom-nav--otter button { margin:8px 4px 4px; min-height:60px; border-radius:14px; color:var(--muted); cursor:pointer; }
.bottom-nav--otter button.active { background:var(--brand-soft); color:var(--brand-strong); }
.bottom-nav--otter button.active::before { top:-7px; width:27px; height:4px; border-radius:4px; }
.bottom-nav--otter button:hover { background:var(--brand-soft); }
.bottom-nav--otter button:focus-visible { outline:3px solid var(--brand); outline-offset:2px; }
@media(max-width:360px){.bottom-nav--otter button{margin-left:2px;margin-right:2px;}}
</style>
