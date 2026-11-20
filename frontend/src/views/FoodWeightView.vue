<script setup>
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import AppBackButton from "../components/AppBackButton.vue";
import "../assets/otter-theme.css";
import portionOtter from "../assets/otter/portion.png";
import { showFailToast, showSuccessToast } from "vant";
import { createDietRecord } from "../api/diet";
import { submitIdempotently } from "../api/idempotency";
import { clearFlowReturn, flowReturnSteps } from "../router/flowReturn";

const router = useRouter();
const draft = JSON.parse(sessionStorage.getItem("dietFoodDraft") || "null");
const food = ref(draft?.food || null);
const quantityG = ref("100");
const recordDate = ref(draft?.date || new Date().toISOString().slice(0, 10));
const recordTime = ref(formatTime(new Date()));
const mealType = ref(draft?.mealType || "breakfast");
const remark = ref("");
const submitting = ref(false);
const imageVisible = ref(Boolean(draft?.food?.coverImageUrl));

const meals = [
  { value: "breakfast", label: "早餐" },
  { value: "lunch", label: "午餐" },
  { value: "dinner", label: "晚餐" },
  { value: "snack", label: "加餐" },
];
const quickWeights = [50, 100, 150, 200];
const amount = computed(() => Math.max(0, Number(quantityG.value) || 0));
const nutrition = computed(() => scaleNutrition(food.value, amount.value));

function scaleNutrition(item, grams) {
  const ratio = grams / 100;
  const scale = (value) =>
    Number.isFinite(Number(value)) ? Number(value) * ratio : null;
  return {
    calorie: scale(item?.caloriePer100g),
    protein: scale(item?.proteinPer100g),
    fat: scale(item?.fatPer100g),
    carb: scale(item?.carbPer100g),
  };
}

function display(value) {
  return Number.isFinite(value) ? value.toFixed(1) : "--";
}
function setServing(serving) {
  quantityG.value = String(serving.gramWeight);
}
function formatTime(date) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

// Persist only the backend contract: the server snapshots nutrition from foodItemId.
async function save() {
  if (!food.value) return showFailToast("食物信息已失效，请重新选择");
  if (!Number.isFinite(amount.value) || amount.value <= 0)
    return showFailToast("请输入大于 0g 的食用重量");
  if (amount.value > 99999.99)
    return showFailToast("食用重量不能超过 99999.99g");
  submitting.value = true;
  try {
    const payload = {
      foodItemId: food.value.id,
      mealType: mealType.value,
      quantityG: amount.value,
      recordTime: `${recordDate.value}T${recordTime.value}:00`,
      remark: remark.value || null,
    };
    await submitIdempotently("diet_create", payload, payload, createDietRecord);
    sessionStorage.removeItem("dietFoodDraft");
    showSuccessToast("已添加到今日饮食");
    // 从记录页进来的话连退两层回原页面，历史里不留选择页；否则退回今日饮食。
    const steps = flowReturnSteps();
    clearFlowReturn();
    if (steps) router.go(-steps);
    else
      router.replace({
        name: "diet-daily",
        query: { date: recordDate.value },
      });
  } catch (error) {
    showFailToast(error.message || "保存饮食记录失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="weight-page otter-page">
    <header class="topbar">
      <AppBackButton fallback="foods" :query="{ date: recordDate, mealType }" label="返回选择食物" />
      <div>
        <p class="eyebrow">FOOD PORTION</p>
        <h1>编辑重量</h1>
      </div>
    </header>
    <template v-if="food">
      <section class="food-card">
        <img
          v-if="imageVisible"
          class="food-art food-image"
          :src="food.coverImageUrl"
          :alt="food.name"
          @error="imageVisible = false"
        />
        <div v-else class="food-art" :class="`category-${food.category}`">
          {{ food.name?.slice(0, 1) || "食" }}
        </div>
        <div>
          <h2>{{ food.name }}</h2>
          <p>
            {{ food.brand || food.category || "食物" }} · 每 100g
            {{ display(food.caloriePer100g) }} kcal
          </p>
        </div>
      </section>
      <section class="nutrition-card">
        <div class="nutrition-top">
          <div class="nutrition-figure">
            <p class="nutrition-label">本次食用</p>
            <div class="calorie">
              <strong>{{ display(nutrition.calorie) }}</strong
              ><span>kcal</span>
            </div>
          </div>
          <img
            class="portion-otter"
            :src="portionOtter"
            alt=""
            aria-hidden="true"
          />
        </div>
        <div class="macro-grid">
          <div>
            <span>蛋白质</span
            ><strong>{{ display(nutrition.protein) }}g</strong>
          </div>
          <div>
            <span>脂肪</span><strong>{{ display(nutrition.fat) }}g</strong>
          </div>
          <div>
            <span>碳水</span><strong>{{ display(nutrition.carb) }}g</strong>
          </div>
        </div>
      </section>
      <section class="form-card">
        <label class="weight-label"
          >食用重量（g）
          <div>
            <input
              v-model="quantityG"
              inputmode="decimal"
              type="number"
              min="0.01"
              max="99999.99"
            /><span>g</span>
          </div></label
        >
        <div class="quick-grid">
          <button
            v-for="weight in quickWeights"
            :key="weight"
            type="button"
            :class="{ active: amount === weight }"
            @click="quantityG = String(weight)"
          >
            {{ weight }}g
          </button>
        </div>
        <div v-if="food.servings?.length" class="servings">
          <p>常用份量</p>
          <button
            v-for="serving in food.servings"
            :key="serving.id"
            type="button"
            @click="setServing(serving)"
          >
            {{ serving.servingName }} · {{ serving.gramWeight }}g
          </button>
        </div>
      </section>
      <section class="form-card details">
        <label
          >餐次<select v-model="mealType">
            <option v-for="meal in meals" :key="meal.value" :value="meal.value">
              {{ meal.label }}
            </option>
          </select></label
        ><label>日期<input v-model="recordDate" type="date" /></label
        ><label>时间<input v-model="recordTime" type="time" /></label
        ><label class="remark"
          >备注<input v-model.trim="remark" maxlength="255" placeholder="可选"
        /></label>
      </section>
      <van-button
        class="save-button"
        type="primary"
        block
        :loading="submitting"
        @click="save"
        >确认添加</van-button
      >
    </template>
    <section v-else class="missing">
      <strong>食物信息已失效</strong
      ><van-button
        plain
        type="primary"
        @click="router.replace({ name: 'foods', query: { date: recordDate, mealType } })"
        >重新选择食物</van-button
      >
    </section>
  </main>
</template>

<style scoped>
.weight-page {
  min-height: 100dvh;
  padding: 24px 16px 52px;
}
.weight-page > * {
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
.weight-page :deep(.back-button) {
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
.food-card,
.nutrition-card,
.form-card,
.missing {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-soft);
}
.food-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px;
}
.food-art {
  display: grid;
  width: 62px;
  height: 62px;
  flex: none;
  place-items: center;
  border: 1px solid rgb(73 53 38 / 8%);
  border-radius: 14px;
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-size: 24px;
  font-weight: 600;
}
.food-image {
  object-fit: cover;
}
.category-meat_dairy {
  background: #f7e9e2;
  color: #a8613f;
}
.category-fruit_vegetable {
  background: #e8eee3;
  color: #5f7d63;
}
.category-beverage {
  background: #eee7f0;
  color: #6f5a86;
}
.category-snack {
  background: #f5edda;
  color: #8a6b3d;
}
.food-card h2 {
  margin: 0;
  color: var(--ink);
  font-size: 18px;
}
.food-card p {
  margin: 7px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.nutrition-card {
  margin-top: 12px;
  padding: 18px;
}
.nutrition-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 8px;
}
.nutrition-figure {
  min-width: 0;
}
.nutrition-label {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.3px;
}
.portion-otter {
  width: 72px;
  height: auto;
  flex: none;
  margin: 0 2px -6px 0;
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
  overflow-wrap: anywhere;
}
.calorie span {
  color: var(--muted);
  font-size: 13px;
}
.macro-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}
.macro-grid div {
  padding-left: 10px;
  border-left: 1px solid var(--line);
}
.macro-grid div:first-child {
  padding-left: 0;
  border-left: 0;
}
.macro-grid span,
.macro-grid strong {
  display: block;
}
.macro-grid span {
  color: var(--muted);
  font-size: 11px;
}
.macro-grid strong {
  margin-top: 5px;
  color: var(--ink);
  font-size: 14px;
}
.form-card {
  margin-top: 12px;
  padding: 18px;
}
.weight-label {
  display: grid;
  gap: 10px;
  color: var(--muted);
  font-size: 13px;
}
.weight-label div {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--line);
}
.weight-label input {
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
.weight-label span {
  color: var(--muted);
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}
.quick-grid button,
.servings button {
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
.servings {
  margin-top: 16px;
}
.servings p {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 12px;
}
.servings button {
  margin: 0 7px 7px 0;
  padding: 0 10px;
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
.details input,
.details select {
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
  --van-button-primary-background: var(--brand);
  --van-button-primary-border-color: var(--brand);
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
  .portion-otter {
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
