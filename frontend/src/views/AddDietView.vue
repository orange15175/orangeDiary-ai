<script setup>
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppBackButton from "../components/AppBackButton.vue";

const router = useRouter();
const route = useRoute();
const now = new Date();
const recordDate = ref(route.query.date || now.toISOString().slice(0, 10));
const mealType = ref(route.query.mealType || defaultMeal(now.getHours()));

const meals = [
  { value: "breakfast", label: "早餐", hint: "开启今天的能量" },
  { value: "lunch", label: "午餐", hint: "补充午间所需" },
  { value: "dinner", label: "晚餐", hint: "平衡一天的摄入" },
  { value: "snack", label: "加餐", hint: "灵活记录每一口" },
];
const selectedMeal = computed(() =>
  meals.find((meal) => meal.value === mealType.value),
);

// Keep the first suggestion natural, while always leaving the final choice to the user.
function defaultMeal(hour) {
  if (hour < 10) return "breakfast";
  if (hour < 15) return "lunch";
  if (hour < 21) return "dinner";
  return "snack";
}

function chooseFood() {
  router.push({
    name: "foods",
    query: { date: recordDate.value, mealType: mealType.value },
  });
}
</script>

<template>
  <main class="add-page">
    <header class="topbar">
      <AppBackButton fallback="diet" label="返回饮食记录" />
      <div>
        <p class="eyebrow">NEW ENTRY</p>
        <h1>新增饮食</h1>
      </div>
    </header>

    <section class="setting-card">
      <label>记录日期<input v-model="recordDate" type="date" /></label>
      <p class="section-title">选择餐次</p>
      <div class="meal-grid">
        <button
          v-for="meal in meals"
          :key="meal.value"
          type="button"
          :class="{ selected: mealType === meal.value }"
          @click="mealType = meal.value"
        >
          <strong>{{ meal.label }}</strong
          ><span>{{ meal.hint }}</span>
        </button>
      </div>
    </section>

    <section class="next-card">
      <span>本次记录</span
      ><strong>{{ recordDate }} · {{ selectedMeal?.label }}</strong>
    </section>
    <van-button class="primary-action" block type="primary" @click="chooseFood"
      >选择食物</van-button
    >
  </main>
</template>

<style scoped>
.add-page {
  min-height: 100vh;
  padding: 24px 16px 48px;
  background: #f4faff;
  color: #254966;
}
.add-page > * {
  width: min(100%, 680px);
  margin-right: auto;
  margin-left: auto;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
}
.topbar h1 {
  margin: 3px 0 0;
  font-size: 24px;
}
.eyebrow {
  margin: 0;
  color: #39b8ee;
  font-family: ui-monospace, monospace;
  font-size: 11px;
}
.setting-card,
.next-card {
  border: 1px solid #d7eaf5;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 18px rgba(84, 153, 191, 0.07);
}
.setting-card {
  padding: 20px;
}
.setting-card label {
  display: grid;
  gap: 7px;
  color: #7593a8;
  font-size: 13px;
}
.setting-card input {
  min-height: 40px;
  border: 1px solid #d7eaf5;
  border-radius: 6px;
  padding: 0 10px;
  color: #254966;
}
.section-title {
  margin: 24px 0 10px;
  color: #254966;
  font-size: 15px;
  font-weight: 700;
}
.meal-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.meal-grid button {
  min-height: 82px;
  border: 1px solid #d7eaf5;
  border-radius: 7px;
  background: #fff;
  color: #254966;
  text-align: left;
}
.meal-grid button.selected {
  border-color: #39b8ee;
  background: #effaff;
  box-shadow: inset 3px 0 #39b8ee;
}
.meal-grid strong,
.meal-grid span {
  display: block;
}
.meal-grid strong {
  font-size: 15px;
}
.meal-grid span {
  margin-top: 6px;
  color: #7593a8;
  font-size: 11px;
}
.next-card {
  display: grid;
  gap: 5px;
  margin-top: 14px;
  padding: 16px 18px;
}
.next-card span {
  color: #7593a8;
  font-size: 12px;
}
.next-card strong {
  font-size: 14px;
}
.primary-action {
  margin-top: 20px;
  height: 46px;
  border-radius: 7px;
  background: #39b8ee;
  border-color: #39b8ee;
}
</style>
