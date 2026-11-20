<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { showFailToast, showSuccessToast } from "vant";
import AppBackButton from "../components/AppBackButton.vue";
import { createExercise, previewExercise } from "../api/health";
import { submitIdempotently } from "../api/idempotency";
import { clearFlowReturn, flowReturnSteps } from "../router/flowReturn";
import "../assets/otter-theme.css";
import dumbbellOtter from "../assets/otter/dumbbell.png";

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
    // 与饮食一致：从记录页进来的话连退两层，历史里不留选择页。
    const steps = flowReturnSteps();
    clearFlowReturn();
    if (steps) router.go(-steps);
    else router.replace({ name: "diet-daily", query: { date: recordDate.value } });
  } catch (error) {
    showFailToast(error.message || "保存运动记录失败");
  } finally { submitting.value = false; }
}

watch([durationMin, recordDate, recordTime], schedulePreview, { immediate: true });
onBeforeUnmount(() => window.clearTimeout(previewTimer));
</script>

<template>
  <main class="exercise-detail-page otter-page">
    <header class="topbar">
      <AppBackButton fallback="exercise-add" :query="{ date: recordDate }" label="返回选择运动" />
      <div>
        <p class="eyebrow">Exercise log</p>
        <h1>编辑运动</h1>
      </div>
    </header>
    <template v-if="exercise">
      <section class="exercise-card">
        <span class="exercise-art" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path
              fill="currentColor"
              d="M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67zM11.71 19c-1.78 0-3.22-1.4-3.22-3.14 0-1.62 1.05-2.76 2.81-3.12 1.77-.36 3.6-1.21 4.62-2.58.39 1.29.59 2.65.59 4.04 0 2.65-2.15 4.8-4.8 4.8z"
            />
          </svg>
        </span>
        <div>
          <h2>{{ exercise.name }}</h2>
          <p>
            {{ exercise.category || "运动" }} ·
            {{
              exercise.metValue
                ? `MET ${exercise.metValue}`
                : "按时长计算消耗"
            }}
          </p>
        </div>
      </section>
      <section class="burn-card">
        <div class="burn-top">
          <div class="burn-figure">
            <p class="burn-label">预计消耗</p>
            <van-loading v-if="previewing" size="20px">正在计算</van-loading>
            <div v-else class="calorie">
              <strong>{{ preview ? calorie : "--" }}</strong><span>kcal</span>
            </div>
          </div>
          <img
            class="burn-otter"
            :src="dumbbellOtter"
            alt=""
            aria-hidden="true"
          />
        </div>
        <p v-if="previewing" class="burn-hint">正在根据体重与时长计算消耗</p>
        <p v-else-if="preview" class="burn-hint">
          根据当前体重、运动强度和时长计算
        </p>
        <p v-else class="burn-hint error-text">
          {{ previewError || "请输入运动时长" }}
        </p>
      </section>
      <section class="form-card">
        <label class="duration-label"
          >运动时长（分钟）
          <div>
            <input
              v-model="durationMin"
              inputmode="numeric"
              type="number"
              min="1"
              max="1440"
            />
            <span>分钟</span>
          </div>
        </label>
        <div class="quick-grid">
          <button
            v-for="duration in quickDurations"
            :key="duration"
            type="button"
            :class="{ active: minutes === duration }"
            @click="durationMin = String(duration)"
          >
            {{ duration }} 分钟
          </button>
        </div>
      </section>
      <section class="form-card details">
        <label>日期<input v-model="recordDate" type="date" /></label>
        <label>时间<input v-model="recordTime" type="time" /></label>
        <label class="remark"
          >备注<input
            v-model.trim="remark"
            maxlength="255"
            placeholder="可选"
        /></label>
      </section>
      <van-button
        class="save-button"
        type="primary"
        block
        :disabled="!preview"
        :loading="submitting"
        @click="save"
      >
        确认保存
      </van-button>
    </template>
    <section v-else class="missing">
      <strong>运动信息已失效</strong>
      <van-button
        plain
        type="primary"
        @click="router.replace({ name: 'exercise-add', query: { date: recordDate } })"
      >
        重新选择运动
      </van-button>
    </section>
  </main>
</template>

<style scoped>
.exercise-detail-page {
  min-height: 100dvh;
  padding: 24px 16px 52px;
}
.exercise-detail-page > * {
  width: min(100%, 680px);
  margin-right: auto;
  margin-left: auto;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
}
.topbar h1 {
  margin: 4px 0 0;
  color: var(--ink);
  font-size: 24px;
  letter-spacing: -0.4px;
}
.exercise-detail-page :deep(.back-button) {
  box-shadow: none;
}
.eyebrow {
  margin: 0;
  color: var(--brand-strong);
  font-family: ui-monospace, monospace;
  font-size: 11px;
  letter-spacing: 1.4px;
  text-transform: uppercase;
}
.exercise-card,
.burn-card,
.form-card,
.missing {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-soft);
}
.exercise-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
}
.exercise-art {
  display: grid;
  width: 62px;
  height: 62px;
  flex: none;
  place-items: center;
  border: 1px solid rgb(73 53 38 / 8%);
  border-radius: 14px;
  background: #f7e9e2;
  color: #a8613f;
}
.exercise-art svg {
  width: 26px;
  height: 26px;
}
.exercise-card h2 {
  margin: 0;
  color: var(--ink);
  font-size: 18px;
}
.exercise-card p {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.burn-card {
  margin-top: 12px;
  padding: 18px;
}
.burn-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 8px;
}
.burn-figure {
  min-width: 0;
}
.burn-label {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.3px;
}
.calorie {
  display: flex;
  align-items: baseline;
  gap: 7px;
  margin-top: 6px;
}
.calorie strong {
  color: #c2704f;
  font-size: 42px;
  font-weight: 650;
  line-height: 1.05;
  letter-spacing: -1.4px;
}
.calorie span {
  color: var(--muted);
  font-size: 13px;
}
.burn-otter {
  width: 72px;
  height: auto;
  flex: none;
  margin: 0 2px -6px 0;
}
.burn-hint {
  margin: 12px 0 0;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.5;
}
.burn-hint.error-text {
  color: var(--danger);
}
.form-card {
  margin-top: 12px;
  padding: 18px;
}
.duration-label {
  display: grid;
  gap: 10px;
  color: var(--muted);
  font-size: 13px;
}
.duration-label div {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--line);
}
.duration-label input {
  width: 100%;
  min-width: 0;
  border: 0;
  padding: 4px 0 7px;
  background: transparent;
  color: var(--ink);
  font-size: 29px;
  font-weight: 650;
  letter-spacing: -0.8px;
  outline: 0;
}
.duration-label span {
  flex: none;
  padding-left: 8px;
  color: var(--muted);
  white-space: nowrap;
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}
.quick-grid button {
  min-height: 36px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  color: var(--muted);
  font-size: 12px;
  transition:
    background 160ms ease,
    border-color 160ms ease,
    color 160ms ease;
}
.quick-grid button.active {
  border-color: var(--brand);
  background: var(--brand);
  color: #fff;
  font-weight: 600;
}
.details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.details label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}
.details input {
  width: 100%;
  min-width: 0;
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0 10px;
  background: var(--surface);
  color: var(--ink);
  font-size: 14px;
}
.details .remark {
  grid-column: 1 / -1;
}
.save-button {
  margin-top: 20px;
  height: 48px;
  border-color: var(--brand);
  border-radius: 12px;
  background: var(--brand);
  font-size: 16px;
  letter-spacing: 0.5px;
}
.save-button:active {
  border-color: var(--brand-strong);
  background: var(--brand-strong);
}
.missing {
  display: grid;
  justify-items: center;
  gap: 16px;
  min-height: 220px;
  padding: 70px 20px;
  color: var(--muted);
  text-align: center;
}
.missing strong {
  color: var(--ink);
}
.missing :deep(.van-button--primary) {
  color: var(--brand-strong);
  border-color: var(--line);
}
@media (max-width: 350px) {
  .burn-otter {
    width: 62px;
  }
  .calorie strong {
    font-size: 37px;
  }
  .quick-grid {
    gap: 6px;
  }
}
</style>
