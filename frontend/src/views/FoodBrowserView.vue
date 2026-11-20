<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppBackButton from "../components/AppBackButton.vue";
import { showFailToast } from "vant";
import { browseFoods, getFoodCategories, matchFoodByName } from "../api/foods";

const route = useRoute();
const router = useRouter();
const categories = ref([]);
const activeCategory = ref("frequent");
const keyword = ref("");
const foods = ref([]);
const loading = ref(false);
const loadingMore = ref(false);
const hasMore = ref(true);
const page = ref(1);
const total = ref(0);
const failedImages = ref({});
let searchTimer;

const isSearch = computed(() => Boolean(keyword.value.trim()));
const categoryName = computed(
  () =>
    categories.value.find((item) => item.code === activeCategory.value)?.name ||
    "食物",
);
const date = computed(
  () => route.query.date || new Date().toISOString().slice(0, 10),
);
const mealType = computed(() => route.query.mealType || "breakfast");
const emptyText = computed(() => {
  if (isSearch.value) return "没有找到相关食物，换个名称试试";
  if (["custom", "favorite"].includes(activeCategory.value))
    return "这个分类暂时还没有食物";
  if (activeCategory.value === "frequent")
    return "记录过的食物会按使用频率出现在这里";
  return "这个分类暂时没有食物";
});

async function loadCategories() {
  try {
    categories.value = await getFoodCategories();
    activeCategory.value = categories.value.some(
      (item) => item.code === "frequent",
    )
      ? "frequent"
      : categories.value[0]?.code;
    await loadFoods(true);
  } catch (error) {
    showFailToast(error.message || "分类加载失败");
  }
}

// One page at a time keeps the category browser responsive on mobile.
async function loadFoods(reset = false) {
  if (loading.value || loadingMore.value || (!reset && !hasMore.value)) return;
  if (reset) loading.value = true;
  else loadingMore.value = true;
  const nextPage = reset ? 1 : page.value + 1;
  try {
    const result = await browseFoods({
      page: nextPage,
      pageSize: 20,
      ...(isSearch.value
        ? { keyword: keyword.value.trim() }
        : { category: activeCategory.value }),
    });
    foods.value = reset ? result.items : [...foods.value, ...result.items];
    page.value = result.page;
    total.value = result.total;
    hasMore.value = result.hasMore;
  } catch (error) {
    showFailToast(error.message || "食物加载失败");
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

function selectCategory(code) {
  if (code === activeCategory.value && !isSearch.value) return;
  keyword.value = "";
  activeCategory.value = code;
  loadFoods(true);
}

function scheduleSearch() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadFoods(true), 300);
}

function hideBrokenImage(id) {
  failedImages.value = { ...failedImages.value, [id]: true };
}

async function chooseFood(food) {
  try {
    // Browse data has nutrition; match enriches only this selected item with optional serving sizes.
    const result = await matchFoodByName(food.name);
    const detailedFood =
      result.candidates?.find((item) => String(item.id) === String(food.id)) ||
      food;
    sessionStorage.setItem(
      "dietFoodDraft",
      JSON.stringify({
        food: detailedFood,
        date: date.value,
        mealType: mealType.value,
      }),
    );
    router.push({ name: "food-weight" });
  } catch (error) {
    showFailToast(error.message || "食物信息获取失败");
  }
}

watch(keyword, scheduleSearch);
onMounted(loadCategories);
onBeforeUnmount(() => window.clearTimeout(searchTimer));
</script>

<template>
  <main class="food-page">
    <header class="browser-header">
      <AppBackButton fallback="diet-daily" :query="{ date }" label="返回饮食记录" />
      <div class="search-box">
        <van-field
          v-model.trim="keyword"
          clearable
          left-icon="search"
          placeholder="搜索食物名称或品牌"
        />
      </div>
    </header>
    <section class="context-line">
      <span
        >{{ date }} ·
        {{
          { breakfast: "早餐", lunch: "午餐", dinner: "晚餐", snack: "加餐" }[
            mealType
          ]
        }}</span
      ><strong>{{ isSearch ? "搜索结果" : categoryName }}</strong>
    </section>
    <div class="browser-shell">
      <nav class="category-nav" aria-label="食物分类">
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
      <section class="food-panel">
        <header>
          <strong>{{ isSearch ? "搜索结果" : categoryName }}</strong
          ><span v-if="total">{{ total }} 种</span>
        </header>
        <van-loading v-if="loading" class="state" vertical
          >正在加载食物</van-loading
        >
        <div v-else-if="!foods.length" class="state empty">{{ emptyText }}</div>
        <ul v-else class="food-list">
          <li v-for="food in foods" :key="food.id" @click="chooseFood(food)">
            <img
              v-if="food.coverImageUrl && !failedImages[food.id]"
              class="food-avatar food-image"
              :src="food.coverImageUrl"
              :alt="food.name"
              @error="hideBrokenImage(food.id)"
            />
            <div
              v-else
              class="food-avatar"
              :class="`category-${food.category}`"
            >
              {{ food.name?.slice(0, 1) || "食" }}
            </div>
            <div>
              <strong>{{ food.name }}</strong
              ><span v-if="food.brand">{{ food.brand }}</span
              ><em
                >{{ Number(food.caloriePer100g || 0).toFixed(0) }} kcal /
                100g</em
              >
            </div>
            <van-icon name="arrow" />
          </li>
        </ul>
        <van-button
          v-if="hasMore && foods.length"
          class="load-more"
          plain
          size="small"
          :loading="loadingMore"
          @click="loadFoods()"
          >加载更多</van-button
        >
      </section>
    </div>
  </main>
</template>

<style scoped>
.food-page {
  --ink: #493526;
  --muted: #8b7d70;
  --brand: #9d8bb7;
  --brand-strong: #79648f;
  --brand-soft: #eee7f0;
  --line: #e8dfd1;
  --surface: #fffcf6;
  --page-bg: #f8f5ed;
  min-height: 100dvh;
  padding: max(18px, env(safe-area-inset-top)) 14px 32px;
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
  transition: border-color 150ms ease, box-shadow 150ms ease;
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
  color: #aa9e92;
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
  min-height: 50px;
  border: 0;
  border-left: 3px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  transition: background 150ms ease, color 150ms ease;
}
.category-nav button.active {
  border-left-color: var(--brand);
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-weight: 700;
}
.category-nav button:focus-visible,
.food-list li:focus-visible,
.load-more:focus-visible {
  position: relative;
  z-index: 1;
  outline: 2px solid var(--brand-strong);
  outline-offset: -2px;
}
.food-panel {
  min-width: 0;
  padding: 0 14px 18px;
}
.food-panel > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 52px;
  padding: 10px 2px;
  border-bottom: 1px solid var(--line);
}
.food-panel > header strong {
  font-size: 16px;
  font-weight: 650;
}
.food-panel > header span {
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
.empty {
  text-align: center;
}
.food-list {
  padding: 0;
  margin: 0;
  list-style: none;
}
.food-list li {
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 82px;
  border-bottom: 1px solid #eee8de;
  cursor: pointer;
  transition: background 120ms ease;
}
.food-list li:active {
  background: #faf7f0;
}
.food-list li > div:last-of-type {
  flex: 1;
  min-width: 0;
}
.food-list strong,
.food-list span,
.food-list em {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.food-list strong {
  font-size: 14px;
  font-weight: 600;
}
.food-list span {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}
.food-list em {
  margin-top: 4px;
  color: var(--brand-strong);
  font-size: 11px;
  font-style: normal;
}
.food-avatar {
  display: grid;
  width: 48px;
  height: 48px;
  flex: none;
  place-items: center;
  overflow: hidden;
  border: 1px solid rgb(73 53 38 / 6%);
  border-radius: 9px;
  background: #eee7f0;
  color: var(--brand-strong);
  font-weight: 700;
}
.food-image {
  object-fit: cover;
}
.category-meat_dairy {
  background: #f7e9e2;
  color: #a96752;
}
.category-fruit_vegetable {
  background: #e8eee3;
  color: #5c7859;
}
.category-beverage {
  background: #eee7f0;
  color: #79648f;
}
.category-snack {
  background: #f5edda;
  color: #8b7449;
}
.load-more {
  display: block;
  width: 100%;
  margin-top: 14px;
  border-color: #d8cde1;
  border-radius: 9px;
  color: var(--brand-strong);
}
@media (max-width: 360px) {
  .food-page {
    padding-right: 10px;
    padding-left: 10px;
  }
  .browser-shell {
    grid-template-columns: 78px minmax(0, 1fr);
  }
  .food-panel {
    padding-right: 10px;
    padding-left: 10px;
  }
  .food-list li {
    gap: 8px;
    min-height: 76px;
  }
  .food-avatar {
    width: 42px;
    height: 42px;
  }
}
</style>
