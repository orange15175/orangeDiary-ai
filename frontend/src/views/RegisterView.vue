<script setup>
import { ref } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const form = ref({
  username: "",
  email: "",
  password: "",
  confirmPassword: "",
});
const loading = ref(false);
const auth = useAuthStore();
const router = useRouter();
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

async function submit() {
  const value = form.value;
  if (
    !value.username ||
    !value.email ||
    !value.password ||
    !value.confirmPassword
  )
    return showFailToast("请完整填写注册信息");
  if (!emailPattern.test(value.email))
    return showFailToast("请输入正确的邮箱地址");
  if (value.password.length < 8) return showFailToast("密码至少需要 8 位");
  if (value.password !== value.confirmPassword)
    return showFailToast("两次输入的密码不一致");
  loading.value = true;
  try {
    await auth.signUp(value);
    showSuccessToast("注册成功");
    router.replace("/profile");
  } catch (error) {
    showFailToast(error.message || "注册失败");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="register-page">
    <section class="register-card">
      <div class="brand-mark">O</div>
      <p class="eyebrow">ORANGE DIARY</p>
      <h1>创建账号</h1>
      <p>开始记录你的饮食与健康变化</p>
      <van-form @submit="submit"
        ><van-cell-group inset>
          <van-field
            v-model.trim="form.username"
            label="用户名"
            maxlength="50"
            placeholder="请输入展示名称"
            :rules="[{ required: true, message: '请输入用户名' }]"
          />
          <van-field
            v-model.trim="form.email"
            label="邮箱"
            type="email"
            placeholder="请输入常用邮箱"
            :rules="[{ required: true, message: '请输入邮箱' }]"
          />
          <van-field
            v-model="form.password"
            label="密码"
            type="password"
            placeholder="至少 8 位"
            :rules="[{ required: true, message: '请输入密码' }]"
          />
          <van-field
            v-model="form.confirmPassword"
            label="确认密码"
            type="password"
            placeholder="请再次输入密码"
            :rules="[{ required: true, message: '请确认密码' }]"
          />
        </van-cell-group>
        <div class="submit-wrap">
          <van-button
            round
            block
            native-type="submit"
            type="primary"
            :loading="loading"
            >注册并继续</van-button
          >
        </div></van-form
      >
      <router-link to="/login">已有账号？去登录</router-link>
    </section>
  </main>
</template>

<style scoped>
.register-page {
  display: grid;
  min-height: 100vh;
  padding: 24px;
  place-items: center;
  background: #f4faff;
}
.register-card {
  width: min(100%, 440px);
  padding: 32px 20px 26px;
  border: 1px solid #d7eaf5;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 18px 42px rgba(65, 133, 170, 0.12);
  text-align: center;
}
.brand-mark {
  display: grid;
  width: 52px;
  height: 52px;
  margin: 0 auto 14px;
  place-items: center;
  border-radius: 16px;
  background: #ff8b72;
  color: #fff;
  font-size: 28px;
  font-weight: 800;
}
.eyebrow {
  margin: 0;
  color: #2ea9dd;
  font-family: ui-monospace, monospace;
  font-size: 11px;
}
.register-card h1 {
  margin: 6px 0;
  color: #254966;
  font-size: 25px;
}
.register-card > p:not(.eyebrow) {
  margin: 0 0 20px;
  color: #7593a8;
  font-size: 13px;
}
.submit-wrap {
  margin: 22px 16px 0;
}
.register-card a {
  display: block;
  margin-top: 18px;
  color: #4f7490;
  font-size: 14px;
  text-decoration: none;
}
:deep(.van-button--primary) {
  border-color: #39b8ee;
  background: #39b8ee;
}
</style>
