<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  showConfirmDialog,
  showFailToast,
  showSuccessToast,
} from "vant";
import { deleteDietRecord, getDailySummary, getDietRecords } from "../api/diet";
import AppBackButton from "../components/AppBackButton.vue";
import { markFlowReturn } from "../router/flowReturn";
import "../assets/otter-theme.css";
import CalorieRiver from "../components/CalorieRiver.vue";
import editIcon from "../assets/otter/icons/record-edit.svg";
import spoonIcon from "../assets/otter/icons/record-spoon.svg";
import dropdownIcon from "../assets/otter/icons/record-dropdown.svg";

const router = useRouter();
const route = useRoute();
// 运动保存后带回原日期，避免用户被重置到今天。
const selectedDate = ref(route.query.date || formatDate(new Date()));
const records = ref([]);
const summary = ref({});
const loading = ref(false);
const failedFoodImages = ref(new Set());
const macroRatios = ref({ carb: 44, protein: 30, fat: 26 });
const mealGroups = [
  { value: "breakfast", label: "早餐" },
  { value: "lunch", label: "午餐" },
  { value: "dinner", label: "晚餐" },
  { value: "snack", label: "加餐" },
];

function formatDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function number(value) {
  return Number(value || 0).toFixed(0);
}

function loadMacroRatios() {
  try {
    const saved = JSON.parse(localStorage.getItem("macroRatios") || "");
    const total =
      Number(saved?.carb) + Number(saved?.protein) + Number(saved?.fat);
    if (total === 100) macroRatios.value = saved;
  } catch {
    // An invalid local preference must not block the daily record page.
  }
}

const isToday = computed(() => selectedDate.value === formatDate(new Date()));
const targetCalories = computed(() => Number(summary.value.targetDailyCalorie || 0));
const intakeCalories = computed(() => Number(summary.value.totalIntakeCalorie || 0));
const isOverBudget = computed(() => targetCalories.value > 0 && intakeCalories.value > targetCalories.value);
const calorieBalance = computed(() => targetCalories.value > 0
  ? Math.abs(targetCalories.value - intakeCalories.value)
  : 0);
const weekDays = computed(() => {
  const current = new Date(`${selectedDate.value}T00:00:00`);
  const weekDay = current.getDay() || 7;
  return Array.from({ length: 7 }, (_, index) => {
    const day = new Date(current);
    day.setDate(current.getDate() - weekDay + index + 1);
    return {
      date: formatDate(day),
      label: ["一", "二", "三", "四", "五", "六", "日"][index],
    };
  });
});
const visibleMealGroups = computed(() =>
  mealGroups.filter((group) =>
    records.value.some((record) => record.mealType === group.value),
  ),
);

function mealRecords(type) {
  return records.value.filter((record) => record.mealType === type);
}
function mealTotal(type) {
  return mealRecords(type).reduce(
    (total, record) => total + Number(record.calorieSnapshot || 0),
    0,
  );
}
function macroTotal(type) {
  const field = {
    carb: "carbSnapshot",
    protein: "proteinSnapshot",
    fat: "fatSnapshot",
  }[type];
  return records.value.reduce(
    (total, record) => total + Number(record[field] || 0),
    0,
  );
}
function macroTarget(type) {
  const caloriesPerGram = type === "fat" ? 9 : 4;
  return Math.round(
    (Number(summary.value.targetDailyCalorie || 0) *
      Number(macroRatios.value[type] || 0)) /
      100 /
      caloriesPerGram,
  );
}
function macroPercent(type) {
  const target = macroTarget(type);
  return target ? Math.min(100, (macroTotal(type) / target) * 100) : 0;
}

async function load() {
  loading.value = true;
  try {
    const [diet, daily] = await Promise.all([
      getDietRecords(selectedDate.value),
      getDailySummary(selectedDate.value),
    ]);
    records.value = diet || [];
    failedFoodImages.value = new Set();
    summary.value = daily || {};
  } catch (error) {
    showFailToast(error.message || "饮食数据加载失败");
  } finally {
    loading.value = false;
  }
}
function selectDate(date) {
  if (date !== selectedDate.value) {
    selectedDate.value = date;
    syncDate();
    load();
  }
}
// 让 URL 跟所选日期保持一致：返回键会带着 date 回到本页，URL 滞后会让两者错位。
function syncDate() {
  if ((route.query.date || "") !== selectedDate.value)
    router.replace({ query: { ...route.query, date: selectedDate.value } });
}
function onDateInput() {
  syncDate();
  load();
}
// The meal shortcut already has its date and meal type, so it can enter food selection directly.
function goToForm(mealType) {
  markFlowReturn();
  router.push({ name: "foods", query: { date: selectedDate.value, mealType } });
}
function goToExercise() {
  markFlowReturn();
  router.push({ name: "exercise-add", query: { date: selectedDate.value } });
}
function hideBrokenFoodImage(id) {
  failedFoodImages.value = new Set([...failedFoodImages.value, id]);
}
async function remove(record) {
  try {
    await showConfirmDialog({
      title: "确认删除",
      message: `确定删除“${record.foodNameSnapshot}”吗？`,
    });
    await deleteDietRecord(record.id);
    showSuccessToast("已删除");
    await load();
  } catch (error) {
    if (error?.message !== "cancel") showFailToast(error.message || "删除失败");
  }
}
onMounted(() => {
  loadMacroRatios();
  load();
});
</script>

<template>
  <div class="diet-view otter-page">
    <main class="diet-page">
      <header class="page-header">
        <AppBackButton fallback="diet" label="返回今日饮食" />
        <label class="date-pill"
          ><span>{{ isToday ? "今天" : selectedDate }}</span>
          <img :src="dropdownIcon" alt="" /><input
            v-model="selectedDate"
            type="date"
            aria-label="选择日期"
            @change="onDateInput"
        /></label>
        <span class="header-placeholder" aria-hidden="true"></span>
      </header>
      <nav class="week-row" aria-label="本周日期">
        <button
          v-for="day in weekDays"
          :key="day.date"
          type="button"
          :class="{ active: day.date === selectedDate }"
          :aria-pressed="day.date === selectedDate"
          :aria-label="`${day.date} 星期${day.label}`"
          @click="selectDate(day.date)"
        >
          {{ day.label }}
        </button>
      </nav>

      <section class="summary-card" aria-label="今日营养摘要">
        <button
          class="summary-title"
          type="button"
          aria-label="编辑自定义预算"
          @click="router.push({ name: 'budget' })"
        >
          <span><i aria-hidden="true"></i>自定义</span><img :src="editIcon" alt="" />
        </button>
        <div class="calorie-overview">
          <div class="side-stat">
            <span>饮食摄入</span
            ><strong class="orange">{{
              number(summary.totalIntakeCalorie)
            }}<small>kcal</small></strong>
          </div>
          <div class="calorie-budget">
            <span>{{ isOverBudget ? "超出预算" : "还可以吃" }}</span>
            <div class="remaining" :class="{ 'over-budget': isOverBudget }"><strong>{{ number(calorieBalance) }}</strong><small>kcal</small></div>
            <p>自定义预算 {{ number(summary.targetDailyCalorie) }} kcal</p>
          </div>
          <div class="side-stat">
            <span>运动消耗</span
            ><strong>{{ number(summary.totalBurnCalorie) }}<small>kcal</small></strong>
          </div>
        </div>
        <CalorieRiver :intake="intakeCalories" :target="targetCalories" />
        <div class="macro-row">
          <div
            v-for="macro in [
              { key: 'carb', label: '碳水化合物' },
              { key: 'protein', label: '蛋白质' },
              { key: 'fat', label: '脂肪' },
            ]"
            :key="macro.key"
            class="macro"
          >
            <span>{{ macro.label }}</span>
            <div class="track">
              <i
                :class="macro.key"
                :style="{ width: `${macroPercent(macro.key)}%` }"
              ></i>
            </div>
            <small
              >{{ number(macroTotal(macro.key)) }} /
              {{ macroTarget(macro.key) }}克</small
            >
          </div>
        </div>
      </section>

      <van-loading v-if="loading" class="loading" vertical
        >正在加载饮食记录</van-loading
      >
      <section v-else-if="!visibleMealGroups.length" class="daily-empty-card">
        <img :src="spoonIcon" alt="" />
        <strong>{{ isToday ? '今天' : '这一天' }}还没有饮食记录</strong>
        <p>点击下方餐次，记录你的第一口美味吧</p>
      </section>
      <section
        v-for="group in visibleMealGroups"
        v-else
        :key="group.value"
        class="meal-card"
      >
        <header class="meal-head">
          <h2>{{ group.label }}</h2>
          <span
            >{{ number(mealTotal(group.value)) }} 千卡 <van-icon name="arrow"
          /></span>
        </header>
        <div
          v-for="record in mealRecords(group.value)"
          :key="record.id"
          class="food-row"
        >
          <img
            v-if="record.foodImageUrl && !failedFoodImages.has(record.id)"
            class="food-thumb"
            :src="record.foodImageUrl"
            :alt="record.foodNameSnapshot"
            loading="lazy"
            @error="hideBrokenFoodImage(record.id)"
          />
          <div v-else class="food-thumb-fallback" aria-hidden="true">
            {{ record.foodNameSnapshot?.slice(0, 1) || "食" }}
          </div>
          <div class="food-info">
            <strong>{{ record.foodNameSnapshot }}</strong
            ><span>{{ record.quantityG }}克</span>
          </div>
          <div class="food-actions">
            <span>{{ number(record.calorieSnapshot) }} 千卡</span
            ><button type="button" @click="remove(record)">删除</button>
          </div>
        </div>
        <button class="add-meal" type="button" @click="goToForm(group.value)">
          + 添加{{ group.label }}
        </button>
      </section>
    </main>
    <footer class="meal-nav" aria-label="快捷添加记录">
      <button
        v-for="group in mealGroups"
        :key="group.value"
        type="button"
        @click="goToForm(group.value)"
      >
        +{{ group.label }}</button
      ><button type="button" @click="goToExercise">+运动</button>
    </footer>
  </div>
</template>
