<script setup>
import { ref } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import OtterWaterScene from "../components/OtterWaterScene.vue";

const account = ref("");
const password = ref("");
const loading = ref(false);
const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

async function submit() {
  if (loading.value) return;
  if (!account.value || !password.value)
    return showFailToast("请输入邮箱和密码");
  loading.value = true;
  try {
    const user = await auth.signIn(account.value.trim(), password.value);
    showSuccessToast("登录成功");
    const next = user.profileCompleted
      ? route.query.redirect || "/diet"
      : "/profile";
    router.replace(String(next));
  } catch (error) {
    showFailToast(error.message || "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <div class="login-scene">
    <svg class="sea-waves" viewBox="0 0 390 844" preserveAspectRatio="none" aria-hidden="true"><g fill="none" stroke="#9bbfb3" stroke-width="2" stroke-linecap="round"><path d="M24 84q16 8 34 0M316 51q9 9 22 4M48 188q-15 8-17 23M295 165q22 4 28 18M35 290q17-5 30 0M309 282q19-5 34 0M25 596q12 5 22 0M340 720q-14-2-22 7M35 789q18 4 29-5M269 816q18 6 33-2"/></g></svg>
    <div class="otter-hero" aria-hidden="true"><OtterWaterScene /></div>
    <section class="login-card" aria-labelledby="login-title">
      <h1 id="login-title">橙子日记</h1>
      <p class="subtitle">记录每一餐的热量</p>

      <van-form class="login-form" @submit="submit">
        <van-cell-group inset>
          <van-field
            id="login-email"
            v-model="account"
            name="email"
            label="邮箱"
            type="email"
            autocomplete="email"
            placeholder="请输入邮箱"
            :rules="[{ required: true, message: '请输入邮箱' }]"
          />
          <van-field
            id="login-password"
            v-model="password"
            name="password"
            label="密码"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
            :rules="[{ required: true, message: '请输入密码' }]"
          />
        </van-cell-group>
        <div class="submit-wrap">
          <van-button
            block
            native-type="submit"
            type="primary"
            :loading="loading"
            :disabled="loading"
            >登录</van-button
          >
        </div>
      </van-form>
      <p class="register-prompt">还没有账号？ <router-link class="register-link" to="/register">去注册</router-link></p>
    </section>
    </div>
  </main>
</template>

<style scoped>
.login-page { --ink:#493526;--muted:#8b7d70;--brand:#9d8bb7;--brand-strong:#79648f;min-height:100dvh;background:#d9eae3;color:var(--ink);padding:0 20px max(40px,env(safe-area-inset-bottom)); }
.login-scene {position:relative;isolation:isolate;width:min(100%,430px);margin:auto;padding-top:30px;}
.sea-waves {position:absolute;inset:0;width:100%;height:100%;min-height:844px;z-index:-1;pointer-events:none;animation:water-drift 4.2s ease-in-out infinite alternate;}
.otter-hero {position:relative;height:290px;display:flex;align-items:center;justify-content:center;margin-bottom:18px;pointer-events:none;}
.floating-otter {width:270px;height:240px;object-fit:contain;animation:otter-float 4.2s ease-in-out infinite;filter:drop-shadow(0 6px 5px #9bbfb31a);}
.otter-ripples {position:absolute;width:330px;max-width:100%;height:210px;bottom:0;fill:none;stroke:#b5cfc4;stroke-width:2;animation:water-drift 4.2s ease-in-out infinite alternate;}
.otter-ripples path {stroke:#fffcf6;}
.login-card {position:relative;width:100%;padding:24px 22px 25px;border:1.2px solid #bcaf9c;border-radius:18px 16px 19px 15px;background:#fffcf6;box-shadow:0 5px 18px #4935260c;text-align:center;}
h1 {margin:0;color:var(--ink);font-size:28px;line-height:1.3;}
.subtitle {margin:8px 0 24px;color:var(--muted);font-size:13px;}
.login-form {text-align:left;}
.login-form :deep(.van-cell-group--inset){margin:0;overflow:visible;background:transparent;border-radius:0;}
.login-form :deep(.van-cell){display:flex;flex-direction:column;align-items:stretch;padding:0;margin-bottom:17px;background:transparent;overflow:visible;}
.login-form :deep(.van-cell:after){display:none;}
.login-form :deep(.van-field__label){width:auto;margin:0 0 8px;color:var(--ink);font-size:13px;font-weight:600;}
.login-form :deep(.van-field__body){min-height:44px;padding:9px 12px;border:1px solid #d4c9bc;border-radius:9px;background:#fffcf6;transition:border-color .16s,box-shadow .16s;}
.login-form :deep(.van-field__body:focus-within){border-color:var(--brand);box-shadow:0 0 0 3px #9d8bb726;}
.login-form :deep(.van-field__control){color:var(--ink);font-size:13px;box-shadow:none!important;outline:none;min-width:0;}
.login-form :deep(.van-field__control::placeholder){color:#9c8d80;opacity:1;}
.login-form :deep(.van-field__error-message){padding-top:4px;font-size:11px;}
.submit-wrap {margin-top:6px;}
.login-form :deep(.van-button--primary){height:44px;border:1px solid var(--brand);border-radius:8px;background:var(--brand);font-size:16px;font-weight:600;color:#fff;}
.login-form :deep(.van-button--primary:not(:disabled):hover){background:var(--brand-strong);border-color:var(--brand-strong);}
.register-prompt {margin:22px 0 0;color:var(--muted);font-size:12px;}
.register-link {margin-left:4px;color:var(--brand-strong);font-weight:600;text-decoration:none;padding:8px 0;}
.register-link:hover {text-decoration:underline;}
.register-link:focus-visible,.login-form :deep(.van-button:focus-visible){outline:3px solid var(--brand);outline-offset:3px;}
@keyframes otter-float {0%,100%{transform:translateY(0) rotate(0deg);}50%{transform:translateY(-6px) rotate(2deg);}}
@keyframes water-drift {from{transform:translateX(0);opacity:1;}to{transform:translateX(4px);opacity:.75;}}
@media(min-width:768px){.login-scene{padding-top:35px;}.otter-hero{height:290px;}.login-card{padding:28px 26px;}}
@media(max-width:360px){.login-page{padding-left:16px;padding-right:16px;}.otter-hero{height:240px;}.floating-otter{width:235px;height:210px;}.login-card{padding:22px 18px;}}
@media(max-height:650px){.login-scene{padding-top:10px;}.otter-hero{height:160px;margin-bottom:10px;}.floating-otter{height:160px;width:210px;}.otter-ripples{height:130px;}.subtitle{margin-bottom:18px;}}
@media(prefers-reduced-motion:reduce){.floating-otter,.otter-ripples,.sea-waves{animation:none;}}
</style>
