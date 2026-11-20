<script setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { showFailToast, showSuccessToast } from "vant";
import { createWeight } from "../api/health";
import { submitIdempotently } from "../api/idempotency";
import AppBackButton from "../components/AppBackButton.vue";
const router = useRouter(),
  weightKg = ref(""),
  saving = ref(false),
  measuredAt = ref(new Date().toISOString().slice(0, 19));
async function save() {
  const value = Number(weightKg.value);
  if (!(value > 0 && value <= 500))
    return showFailToast("请输入 1 到 500 kg 之间的体重");
  saving.value = true;
  try {
    const payload = {
      weightKg: value,
      recordTime: measuredAt.value,
    };
    // Weight is the editable intent; the original measurement timestamp is retained on retry.
    await submitIdempotently("weight_create", { weightKg: value }, payload, createWeight);
    showSuccessToast("体重已记录");
    router.replace({ name: "weight-records" });
  } catch (e) {
    showFailToast(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <main class="page">
    <header>
      <AppBackButton fallback="weight-records" label="返回体重记录" />
      <div>
        <h1>记录体重</h1>
        <p>记录当前体重，帮助你查看体重变化。</p>
      </div>
    </header>
    <section>
      <label
        >当前体重（kg）<input
          v-model.trim="weightKg"
          autofocus
          type="number"
          inputmode="decimal"
          placeholder="例如 68.5"
      /></label>
    </section>
    <van-button block type="primary" :loading="saving" @click="save"
      >确认记录</van-button
    >
  </main>
</template>
<style scoped>
.page {
  min-height: 100vh;
  padding: 28px 16px;
  background: #eef8ff;
  color: #254966;
}
.page > * {
  width: min(100%, 680px);
  margin: auto;
}
.page header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.page h1 {
  font-size: 24px;
}
.page p {
  color: #7593a8;
  font-size: 13px;
}
.page section {
  margin-top: 24px;
  padding: 18px;
  border: 1px solid #d7eaf5;
  border-radius: 8px;
  background: #fff;
}
.page label {
  display: grid;
  gap: 9px;
  color: #4f7490;
  font-size: 13px;
}
.page input {
  height: 44px;
  border: 1px solid #d7eaf5;
  border-radius: 6px;
  padding: 0 10px;
  color: #254966;
  font-size: 19px;
}
.page > .van-button {
  position: fixed;
  right: 16px;
  bottom: calc(18px + env(safe-area-inset-bottom));
  left: 16px;
  width: auto;
  max-width: 648px;
  height: 44px;
  border-color: #39b8ee;
  border-radius: 7px;
  background: #39b8ee;
}
</style>
