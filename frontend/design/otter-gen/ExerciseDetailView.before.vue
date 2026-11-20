<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { showFailToast, showSuccessToast } from "vant";
import AppBackButton from "../components/AppBackButton.vue";
import { createExercise, previewExercise } from "../api/health";
import { submitIdempotently } from "../api/idempotency";

const router = useRouter();
const draft = JSON.parse(sessionStorage.getItem("exerciseDraft") || "null");
const exercise = ref(draft?.exercise || null);
const durationMin = ref("30");
const recordDate = ref(draft?.date || new Date().toISOString().slice(0, 10));
const recordTime = ref(formatTime(new Date()));
const remark = ref("");
const preview = ref(null);
const previewError = ref("");
const previewing = ref(false);
const submitting = ref(false);
const quickDurations = [15, 30, 45, 60];
let previewTimer;
let previewVersion = 0;

const minutes = computed(() => Number(durationMin.value));
const calorie = computed(() => Number(preview.value?.estimatedCalorieBurned || 0).toFixed(0));
const payload = () => ({ exerciseItemId: exercise.value?.id, durationMin: minutes.value, recordTime: `${recordDate.value}T${recordTime.value}:00`, remark: remark.value || null });

function formatTime(date) { return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`; }

function schedulePreview() {
  // 递增版本号可让已经在路上的旧请求失效，避免覆盖最新时长的结果。
  const requestVersion = ++previewVersion;
  window.clearTimeout(previewTimer);
  preview.value = null;
  previewError.value = "";
  if (!exercise.value || !(minutes.value > 0)) return;
  previewTimer = window.setTimeout(async () => {
    previewing.value = true;
    try {
      const result = await previewExercise(payload());
      if (requestVersion === previewVersion) preview.value = result;
    } catch (error) {
      if (requestVersion === previewVersion) previewError.value = error.message || "消耗计算失败";
    } finally {
      if (requestVersion === previewVersion) previewing.value = false;
    }
  }, 300);
}

async function save() {
  if (!exercise.value) return showFailToast("运动信息已失效，请重新选择");
  if (!(minutes.value > 0) || minutes.value > 1440) return showFailToast("请输入 1 到 1440 分钟的运动时长");
  if (!preview.value) return showFailToast(previewError.value || "正在计算消耗，请稍后确认");
  submitting.value = true;
  try {
    const requestPayload = payload();
    await submitIdempotently("exercise_create", requestPayload, requestPayload, createExercise);
    sessionStorage.removeItem("exerciseDraft");
    showSuccessToast("运动已保存");
    router.replace({ name: "diet-daily", query: { date: recordDate.value } });
  } catch (error) {
    showFailToast(error.message || "保存运动记录失败");
  } finally { submitting.value = false; }
}

watch([durationMin, recordDate, recordTime], schedulePreview, { immediate: true });
onBeforeUnmount(() => window.clearTimeout(previewTimer));
</script>

<template>
  <main class="exercise-detail-page">
    <header class="topbar"><AppBackButton fallback="exercise-add" label="返回选择运动" /><div><p class="eyebrow">运动记录</p><h1>编辑运动</h1></div></header>
    <template v-if="exercise">
      <section class="exercise-card"><span class="exercise-art"><van-icon name="fire-o" /></span><div><h2>{{ exercise.name }}</h2><p>{{ exercise.category || "运动" }} · {{ exercise.metValue ? `MET ${exercise.metValue}` : "按时长计算消耗" }}</p></div></section>
      <section class="burn-card"><p>预计消耗</p><van-loading v-if="previewing" size="20px">正在计算</van-loading><template v-else><div class="calorie"><strong>{{ preview ? calorie : "--" }}</strong><span>kcal</span></div><p v-if="preview">根据当前体重、运动强度和时长计算</p><p v-else class="error-text">{{ previewError || "请输入运动时长" }}</p></template></section>
      <section class="form-card"><label class="duration-label">运动时长（分钟）<div><input v-model="durationMin" inputmode="numeric" type="number" min="1" max="1440" /><span>分钟</span></div></label><div class="quick-grid"><button v-for="duration in quickDurations" :key="duration" type="button" :class="{ active: minutes === duration }" @click="durationMin = String(duration)">{{ duration }} 分钟</button></div></section>
      <section class="form-card details"><label>日期<input v-model="recordDate" type="date" /></label><label>时间<input v-model="recordTime" type="time" /></label><label class="remark">备注<input v-model.trim="remark" maxlength="255" placeholder="可选" /></label></section>
      <van-button class="save-button" type="primary" block :disabled="!preview" :loading="submitting" @click="save">确认保存</van-button>
    </template>
    <section v-else class="missing"><strong>运动信息已失效</strong><van-button plain type="primary" @click="router.replace({ name: 'exercise-add' })">重新选择运动</van-button></section>
  </main>
</template>

<style scoped>
.exercise-detail-page{min-height:100vh;padding:24px 16px 52px;background:#f4faff;color:#254966}.exercise-detail-page>*{width:min(100%,680px);margin-right:auto;margin-left:auto}.topbar{display:flex;align-items:center;gap:12px;margin-bottom:20px}.topbar h1{margin:3px 0 0;font-size:24px}.eyebrow{margin:0;color:#39b8ee;font-family:ui-monospace,monospace;font-size:11px}.exercise-card,.burn-card,.form-card,.missing{border:1px solid #d7eaf5;border-radius:8px;background:#fff;box-shadow:0 8px 18px rgba(84,153,191,.07)}.exercise-card{display:flex;align-items:center;gap:14px;padding:16px}.exercise-art{display:grid;width:62px;height:62px;flex:none;place-items:center;border-radius:8px;background:#dff5ff;color:#1684b1;font-size:29px}.exercise-card h2{margin:0;font-size:18px}.exercise-card p{margin:7px 0 0;color:#7593a8;font-size:12px}.burn-card{min-height:144px;margin-top:12px;padding:18px;background:#f9fdff}.burn-card>p{margin:0;color:#7593a8;font-size:12px}.burn-card :deep(.van-loading){margin-top:24px}.calorie{display:flex;align-items:baseline;gap:7px;margin-top:7px}.calorie strong{color:#ff8b72;font-size:42px;line-height:1}.calorie span{color:#4f7490;font-size:13px}.burn-card .error-text{color:#c86658}.form-card{margin-top:12px;padding:18px}.duration-label{display:grid;gap:10px;color:#4f7490;font-size:13px}.duration-label div{display:flex;align-items:center;border-bottom:1px solid #d7eaf5}.duration-label input{width:100%;min-width:0;border:0;padding:4px 0 7px;color:#254966;font-size:29px;font-weight:700;outline:0}.duration-label span{color:#7593a8}.quick-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:14px}.quick-grid button{min-height:34px;border:1px solid #d7eaf5;border-radius:6px;background:#fff;color:#4f7490;font-size:12px}.quick-grid button.active{border-color:#39b8ee;background:#39b8ee;color:#fff}.details{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.details label{display:grid;gap:6px;color:#7593a8;font-size:12px}.details input{width:100%;min-width:0;min-height:36px;border:1px solid #d7eaf5;border-radius:6px;padding:0 8px;background:#fff;color:#254966}.details .remark{grid-column:1/-1}.save-button{height:46px;margin-top:20px;border-color:#39b8ee;border-radius:7px;background:#39b8ee}.missing{display:grid;min-height:220px;padding:70px 20px;justify-items:center;gap:16px;color:#7593a8}.missing strong{color:#254966}
</style>
