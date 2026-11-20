<script setup>
import { computed, onMounted, ref } from "vue";
import { showFailToast } from "vant";
import { useRouter } from "vue-router";
import AppBottomNav from "../components/AppBottomNav.vue";
import { useAuthStore } from "../stores/auth";
import "../assets/otter-theme.css";
import UserAvatar from "../components/UserAvatar.vue";
import chevron from "../assets/otter/icons/chevron.svg";
import chevronWhite from "../assets/otter/icons/chevron-white.svg";
import chevronDanger from "../assets/otter/icons/chevron-danger.svg";

const auth = useAuthStore();
const router = useRouter();
const loading = ref(false);
const user = computed(() => auth.user || {});
const goalLabels = { lose: "减脂", maintain: "维持", gain: "增肌" };
const displayName = computed(() => user.value.nickname || user.value.username || "橙子用户");
const goal = computed(() => goalLabels[user.value.activeGoal?.goalType] || "暂未设置");
async function load() {
  loading.value = true;
  try { await auth.loadUser(); }
  catch (error) { showFailToast(error.message || "个人资料加载失败"); }
  finally { loading.value = false; }
}
async function signOut() {
  await auth.signOut();
  router.replace({ name: "login" });
}
onMounted(load);
</script>

<template>
  <div class="otter-page profile-shell">
    <main class="home-page">
      <header class="profile-header">
        <h1>个人</h1>
        <button type="button" class="settings" aria-label="设置个人信息" @click="router.push({ name: 'profile' })">设置</button>
      </header>
      <section class="profile-card" :aria-busy="loading" aria-label="个人资料">
        <div class="identity">
          <UserAvatar class="avatar" :src="user.avatar" alt="我的头像" width="82" height="82" />
          <div class="identity-copy">
            <h2>{{ displayName }}</h2>
            <p>{{ user.email || "未绑定邮箱" }}</p>
          </div>
        </div>
        <div class="goal-panel"><span>当前目标</span><strong>{{ goal }}</strong></div>
        <button class="profile-action primary" type="button" @click="router.push({ name: 'profile' })">
          <span>编辑个人信息</span><img :src="chevronWhite" alt="" width="18" height="18" />
        </button>
        <button class="profile-action" type="button" @click="router.push({ name: 'goal' })">
          <span>我的目标</span><img :src="chevron" alt="" width="18" height="18" />
        </button>
        <button class="profile-action danger" type="button" @click="signOut">
          <span>退出登录</span><img :src="chevronDanger" alt="" width="18" height="18" />
        </button>
      </section>
    </main>
    <AppBottomNav active="personal" theme="otter-minimal" />
  </div>
</template>

<style scoped>
.home-page { width: min(100%, 648px); margin: auto; padding: 28px 16px calc(100px + env(safe-area-inset-bottom)); }
.profile-header { position: relative; display: flex; height: 40px; align-items: center; justify-content: center; margin-bottom: 20px; }
.profile-header h1 { margin: 0; font-size: 23px; line-height: 1.5; }
.settings { position: absolute; right: 0; min-width: 44px; min-height: 44px; padding: 0; border: 0; background: transparent; color: var(--ink); font-size: 12px; }
.profile-card { display: grid; gap: 18px; padding: 22px 18px; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: var(--shadow-soft); }
.identity { display: flex; align-items: center; gap: 16px; min-width: 0; }
.avatar { flex: none; object-fit: contain; border-radius: 18px; }
.identity-copy { min-width: 0; }
.identity h2 { margin: 0; font-size: 22px; line-height: 1.5; overflow-wrap: anywhere; }
.identity p { margin: 6px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.goal-panel { display: grid; justify-items: center; gap: 4px; padding: 16px; border-radius: 14px; background: var(--seafoam); }
.goal-panel span { color: var(--muted); font-size: 13px; line-height: 1.5; }
.goal-panel strong { color: #527e71; font-size: 27px; line-height: 1.5; }
.profile-action { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 50px; padding: 12px 18px; border: 1px solid var(--seafoam); border-radius: 12px; background: var(--surface); color: var(--ink); font-size: 16px; font-weight: 700; text-align: left; }
.profile-action.primary { border-color: var(--brand); background: var(--brand); color: white; }
.profile-action.danger { border-color: var(--danger); color: var(--danger); }
.profile-action:hover { box-shadow: 0 3px 10px rgb(73 53 38 / 8%); }
@media (max-width: 360px) { .profile-card { padding-inline: 14px; } .identity { gap: 12px; } .avatar { width: 68px; height: 68px; } }
</style>
