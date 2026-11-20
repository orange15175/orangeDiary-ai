<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { showFailToast } from "vant";
import AppBackButton from "../components/AppBackButton.vue";
import { browseExercises, getExerciseCategories } from "../api/health";

const route = useRoute();
const router = useRouter();
const categories = ref([]);
const activeCategory = ref("frequent");
const keyword = ref("");
const exercises = ref([]);
const total = ref(0);
const page = ref(1);
const hasMore = ref(true);
const loading = ref(false);
const loadingMore = ref(false);
let searchTimer;

const date = computed(() => route.query.date || new Date().toISOString().slice(0, 10));
const isSearch = computed(() => Boolean(keyword.value.trim()));
const categoryName = computed(() =>
  categories.value.find((item) => item.code === activeCategory.value)?.name || "运动",
);
const emptyText = computed(() => {
  if (isSearch.value) return "没有找到相关运动项目";
  if (["custom", "favorite"].includes(activeCategory.value)) return "这个分类暂时还没有运动项目";
  if (activeCategory.value === "frequent") return "记录过的运动会按添加次数出现在这里";
  return "这个分类暂时没有运动项目";
});

async function loadCategories() {
  try {
    categories.value = await getExerciseCategories();
    activeCategory.value = categories.value.some((item) => item.code === "frequent")
      ? "frequent"
      : categories.value[0]?.code;
    await loadExercises(true);
  } catch (error) {
    showFailToast(error.message || "运动分类加载失败");
  }
}

// 每次只保留当前分类的一页数据，避免运动基础库扩大后拖慢手机页面。
async function loadExercises(reset = false) {
  if (loading.value || loadingMore.value || (!reset && !hasMore.value)) return;
  if (reset) loading.value = true;
  else loadingMore.value = true;
  const nextPage = reset ? 1 : page.value + 1;
  try {
    const result = await browseExercises({
      category: activeCategory.value,
      page: nextPage,
      pageSize: 20,
      ...(isSearch.value ? { keyword: keyword.value.trim() } : {}),
    });
    exercises.value = reset ? result.items : [...exercises.value, ...result.items];
    total.value = result.total;
    page.value = result.page;
    hasMore.value = result.hasMore;
  } catch (error) {
    showFailToast(error.message || "运动项目加载失败");
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

function selectCategory(code) {
  if (code === activeCategory.value && !isSearch.value) return;
  keyword.value = "";
  activeCategory.value = code;
  loadExercises(true);
}

function scheduleSearch() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadExercises(true), 300);
}

// 按运动分类区分图形与底色，列表能快速扫视类型
const CATEGORY_ICONS = {
  "有氧": {
    tone: "aerobic",
    path: "M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67zM11.71 19c-1.78 0-3.22-1.4-3.22-3.14 0-1.62 1.05-2.76 2.81-3.12 1.77-.36 3.6-1.21 4.62-2.58.39 1.29.59 2.65.59 4.04 0 2.65-2.15 4.8-4.8 4.8z",
  },
  "力量": {
    tone: "strength",
    path: "M2 8h4v8H2V8zm5 2h2v4H7v-4zm3 .5h4v3h-4v-3zm5-.5h2v4h-2v-4zm3-2h4v8h-4V8z",
  },
  "球类": {
    tone: "ball",
    path: "M12 3.6a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0 0-16.8Z",
  },
  "柔韧": {
    tone: "flex",
    path: "M6 21c0-9 5.5-14.5 15-16 .5 9.5-5 15.5-15 16z",
  },
};
const FALLBACK_ICON = CATEGORY_ICONS["有氧"];

function categoryIcon(category) {
  return (CATEGORY_ICONS[category] || FALLBACK_ICON).path;
}

function categoryTone(category) {
  return (CATEGORY_ICONS[category] || FALLBACK_ICON).tone;
}

function exerciseMeta(item) {
  if (item.metValue !== null && item.metValue !== undefined) return `MET ${item.metValue}`;
  return item.caloriePerUnit ? `${item.caloriePerUnit} kcal / 分钟` : "消耗待计算";
}

function chooseExercise(exercise) {
  sessionStorage.setItem("exerciseDraft", JSON.stringify({ exercise, date: date.value }));
  router.push({ name: "exercise-edit" });
}

watch(keyword, scheduleSearch);
onMounted(loadCategories);
onBeforeUnmount(() => window.clearTimeout(searchTimer));
</script>

<template>
  <main class="exercise-browser-page">
    <header class="browser-header">
      <AppBackButton fallback="diet-daily" :query="{ date }" label="返回饮食记录" />
      <div class="search-box">
        <van-field
          v-model.trim="keyword"
          clearable
          left-icon="search"
          placeholder="搜索运动项目"
        />
      </div>
    </header>
    <section class="context-line">
      <span>{{ date }}</span>
      <strong>{{ isSearch ? "搜索结果" : categoryName }}</strong>
    </section>
    <div class="browser-shell">
      <nav class="category-nav" aria-label="运动分类">
        <button
          v-for="category in categories"
          :key="category.code"
          type="button"
          :class="{ active: !isSearch && category.code === activeCategory }"
          @click="selectCategory(category.code)"
        >
          {{ category.name }}
        </button>
      </nav>
      <section class="exercise-panel">
        <header>
          <strong>{{ isSearch ? "搜索结果" : categoryName }}</strong>
          <span v-if="total">{{ total }} 项</span>
        </header>
        <van-loading v-if="loading" class="state" vertical
          >正在加载运动项目</van-loading
        >
        <div v-else-if="!exercises.length" class="state empty">
          <span class="empty-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <path fill="currentColor" d="M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67zM11.71 19c-1.78 0-3.22-1.4-3.22-3.14 0-1.62 1.05-2.76 2.81-3.12 1.77-.36 3.6-1.21 4.62-2.58.39 1.29.59 2.65.59 4.04 0 2.65-2.15 4.8-4.8 4.8z" />
            </svg>
          </span>
          <p>{{ emptyText }}</p>
        </div>
        <ul v-else class="exercise-list">
          <li
            v-for="exercise in exercises"
            :key="exercise.id"
            tabindex="0"
            @click="chooseExercise(exercise)"
            @keydown.enter.prevent="chooseExercise(exercise)"
          >
            <span
              class="exercise-icon"
              :class="`icon-${categoryTone(exercise.category)}`"
              aria-hidden="true"
            >
              <svg viewBox="0 0 24 24">
                <path
                  fill="currentColor"
                  :d="categoryIcon(exercise.category)"
                />
              </svg>
            </span>
            <div>
              <strong>{{ exercise.name }}</strong>
              <span>{{ exercise.category || "运动" }}</span>
              <em>{{ exerciseMeta(exercise) }}</em>
            </div>
            <van-icon name="arrow" class="row-arrow" />
          </li>
        </ul>
        <van-button
          v-if="hasMore && exercises.length"
          class="load-more"
          plain
          size="small"
          :loading="loadingMore"
          @click="loadExercises()"
          >加载更多</van-button
        >
      </section>
    </div>
  </main>
</template>

<style scoped>
.exercise-browser-page {
  --ink: #493526;
  --muted: #8b7d70;
  --brand: #9d8bb7;
  --brand-strong: #79648f;
  --brand-soft: #eee7f0;
  --line: #e8dfd1;
  --surface: #fffcf6;
  --page-bg: #f8f5ed;
  --warm: #f7e9e2;
  --warm-ink: #a8613f;
  --van-loading-spinner-color: var(--brand);
  min-height: 100dvh;
  padding: max(18px, env(safe-area-inset-top)) 14px
    max(32px, env(safe-area-inset-bottom));
  background: var(--page-bg);
  color: var(--ink);
}
.browser-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: min(100%, 760px);
  margin: auto;
}
.browser-header :deep(.back-button) {
  width: 42px;
  height: 42px;
  border-color: var(--line);
  border-radius: 10px;
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 3px 10px rgb(73 53 38 / 5%);
}
.search-box {
  flex: 1;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
  transition:
    border-color 150ms ease,
    box-shadow 150ms ease;
}
.search-box:focus-within {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgb(157 139 183 / 14%);
}
.search-box :deep(.van-cell) {
  min-height: 42px;
  padding: 8px 11px;
  background: transparent;
  color: var(--ink);
}
.search-box :deep(.van-field__left-icon) {
  color: var(--muted);
}
.search-box :deep(.van-field__control) {
  color: var(--ink);
  font-size: 14px;
}
.search-box :deep(.van-field__control::placeholder) {
  color: #a2947f;
}
.context-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: min(100%, 760px);
  margin: 16px auto 12px;
  color: var(--muted);
  font-size: 12px;
}
.context-line strong {
  color: var(--brand-strong);
  font-size: 13px;
}
.browser-shell {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  width: min(100%, 760px);
  min-height: calc(100dvh - 132px);
  margin: auto;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface);
  box-shadow: 0 5px 18px rgb(73 53 38 / 4%);
}
.category-nav {
  display: grid;
  align-content: start;
  padding: 8px 0;
  border-right: 1px solid var(--line);
  background: #faf7f0;
}
.category-nav button {
  min-height: 46px;
  border: 0;
  border-left: 3px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  transition:
    background 150ms ease,
    color 150ms ease;
}
.category-nav button.active {
  border-left-color: var(--brand-strong);
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-weight: 700;
}
.category-nav button:focus-visible,
.exercise-list li:focus-visible,
.load-more:focus-visible {
  position: relative;
  z-index: 1;
  outline: 2px solid var(--brand-strong);
  outline-offset: -2px;
}
.exercise-panel {
  min-width: 0;
  padding: 0 14px 18px;
}
.exercise-panel > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 52px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--line);
}
.exercise-panel > header strong {
  font-size: 16px;
  font-weight: 650;
}
.exercise-panel > header span {
  color: var(--muted);
  font-size: 12px;
}
.state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
  color: var(--muted);
  font-size: 13px;
}
.state :deep(.van-loading__text) {
  color: var(--muted);
  font-size: 13px;
}
.empty {
  flex-direction: column;
  gap: 12px;
  padding: 32px 12px;
  text-align: center;
}
.empty-mark {
  display: grid;
  width: 54px;
  height: 54px;
  place-items: center;
  border: 1px solid rgb(73 53 38 / 6%);
  border-radius: 16px;
  background: var(--warm);
  color: var(--warm-ink);
}
.empty-mark svg {
  width: 24px;
  height: 24px;
}
.empty p {
  max-width: 220px;
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}
.exercise-list {
  padding: 0;
  margin: 0;
  list-style: none;
}
.exercise-list li {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 68px;
  border-bottom: 1px solid #eee8de;
  cursor: pointer;
  transition: background 120ms ease;
}
.exercise-list li:last-child {
  border-bottom: 0;
}
.exercise-list li:active {
  background: #faf7f0;
}
.exercise-icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: none;
  place-items: center;
  border: 1px solid rgb(73 53 38 / 6%);
  border-radius: 9px;
  background: var(--warm);
  color: var(--warm-ink);
}
.exercise-icon svg {
  width: 20px;
  height: 20px;
}
.icon-aerobic {
  background: #f7e9e2;
  color: #a8613f;
}
.icon-strength {
  background: #eee7f0;
  color: #79648f;
}
.icon-ball {
  background: #e8eee3;
  color: #5c7859;
}
.icon-flex {
  background: #f5edda;
  color: #8b7449;
}
.exercise-list li > div {
  flex: 1;
  min-width: 0;
}
.exercise-list strong,
.exercise-list span,
.exercise-list em {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.exercise-list strong {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}
.exercise-list span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}
.exercise-list em {
  margin-top: 4px;
  color: var(--warm-ink);
  font-size: 11px;
  font-style: normal;
}
.row-arrow {
  flex: none;
  color: #c9bcae;
  font-size: 14px;
}
.load-more {
  display: block;
  width: 100%;
  margin-top: 14px;
  border-radius: 9px;
  --van-button-default-color: var(--brand-strong);
  --van-button-default-border-color: #d8cde1;
  --van-button-default-background: transparent;
}
@media (max-width: 360px) {
  .exercise-browser-page {
    padding-right: 10px;
    padding-left: 10px;
  }
  .browser-shell {
    grid-template-columns: 78px minmax(0, 1fr);
  }
  .exercise-panel {
    padding-right: 10px;
    padding-left: 10px;
  }
  .exercise-list li {
    gap: 8px;
    min-height: 64px;
  }
  .exercise-icon {
    width: 38px;
    height: 38px;
  }
  .exercise-icon svg {
    width: 18px;
    height: 18px;
  }
}
</style>
