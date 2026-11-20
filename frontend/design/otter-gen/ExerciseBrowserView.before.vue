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
      <AppBackButton fallback="diet-daily" label="返回饮食记录" />
      <div class="search-box">
        <van-field v-model.trim="keyword" clearable left-icon="search" placeholder="搜索运动项目" />
      </div>
    </header>
    <section class="context-line"><span>{{ date }}</span><strong>{{ isSearch ? "搜索结果" : categoryName }}</strong></section>
    <div class="browser-shell">
      <nav class="category-nav" aria-label="运动分类">
        <button v-for="category in categories" :key="category.code" type="button" :class="{ active: !isSearch && category.code === activeCategory }" @click="selectCategory(category.code)">{{ category.name }}</button>
      </nav>
      <section class="exercise-panel">
        <header><strong>{{ isSearch ? "搜索结果" : categoryName }}</strong><span v-if="total">{{ total }} 项</span></header>
        <van-loading v-if="loading" class="state" vertical>正在加载运动项目</van-loading>
        <div v-else-if="!exercises.length" class="state empty">{{ emptyText }}</div>
        <ul v-else class="exercise-list">
          <li v-for="exercise in exercises" :key="exercise.id" @click="chooseExercise(exercise)">
            <span class="exercise-icon" aria-hidden="true"><van-icon name="fire-o" /></span>
            <div><strong>{{ exercise.name }}</strong><span>{{ exercise.category || "运动" }}</span><em>{{ exerciseMeta(exercise) }}</em></div>
            <van-icon name="arrow" />
          </li>
        </ul>
        <van-button v-if="hasMore && exercises.length" class="load-more" plain size="small" :loading="loadingMore" @click="loadExercises()">加载更多</van-button>
      </section>
    </div>
  </main>
</template>

<style scoped>
.exercise-browser-page{min-height:100vh;padding:18px 12px 34px;background:#f4faff;color:#254966}.browser-header{display:flex;align-items:center;gap:8px;width:min(100%,760px);margin:auto}.search-box{flex:1;overflow:hidden;border:1px solid #cbeaf8;border-radius:7px;background:#fff}.search-box :deep(.van-cell){padding:7px 10px}.context-line{display:flex;justify-content:space-between;width:min(100%,760px);margin:14px auto 10px;color:#7593a8;font-size:12px}.context-line strong{color:#39a9df}.browser-shell{display:grid;grid-template-columns:92px minmax(0,1fr);width:min(100%,760px);min-height:calc(100vh - 126px);margin:auto;overflow:hidden;border:1px solid #d7eaf5;border-radius:8px;background:#fff}.category-nav{display:grid;align-content:start;padding:6px 0;border-right:1px solid #e3f2f8;background:#f8fcff}.category-nav button{min-height:46px;border:0;border-left:3px solid transparent;background:transparent;color:#7593a8;font-size:12px}.category-nav button.active{border-left-color:#39b8ee;background:#fff;color:#1684b1;font-weight:700}.exercise-panel{min-width:0;padding:0 12px 18px}.exercise-panel>header{display:flex;justify-content:space-between;padding:16px 2px 12px;border-bottom:1px solid #e8f4f9}.exercise-panel>header span{color:#7593a8;font-size:11px}.state{display:flex;align-items:center;justify-content:center;min-height:240px;color:#7593a8;font-size:13px}.empty{text-align:center}.exercise-list{padding:0;margin:0;list-style:none}.exercise-list li{display:flex;align-items:center;gap:10px;min-height:73px;border-bottom:1px solid #eef7fb;cursor:pointer}.exercise-icon{display:grid;width:42px;height:42px;flex:none;place-items:center;border-radius:7px;background:#dff5ff;color:#1684b1;font-size:20px}.exercise-list li>div{flex:1;min-width:0}.exercise-list strong,.exercise-list span,.exercise-list em{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.exercise-list strong{font-size:14px}.exercise-list span{margin-top:3px;color:#7593a8;font-size:11px}.exercise-list em{margin-top:4px;color:#39a9df;font-size:11px;font-style:normal}.load-more{display:block;width:100%;margin-top:14px;border-color:#b8e3f5;color:#218fc0}
</style>
