<script setup>
import { onMounted, ref } from "vue";
import { showFailToast } from "vant";
import AssistantSteps from "../components/AssistantSteps.vue";
import AppBackButton from "../components/AppBackButton.vue";
import { askChef, getRecipe, getRecipes } from "../api/assistant";
import "../assets/diet-assistant.css";

const keyword = ref("");
const recipes = ref([]);
const loading = ref(false);
const detail = ref(null);
const asking = ref(false);
const message = ref("");
const servings = ref(2);
const answer = ref(null);

const sourceLabels = { sample: "测试样例", formal: "正式数据" };

async function load() {
  loading.value = true;
  try {
    const data = await getRecipes({ keyword: keyword.value || undefined, pageSize: 30 });
    recipes.value = data?.records || [];
  } catch (error) {
    showFailToast(error.message || "食谱加载失败");
  } finally {
    loading.value = false;
  }
}

async function openDetail(id) {
  try {
    detail.value = await getRecipe(id);
  } catch (error) {
    showFailToast(error.message || "食谱详情加载失败");
  }
}

async function submit() {
  const text = message.value.trim();
  if (!text) return showFailToast("先说说想吃什么或想用什么食材");
  asking.value = true;
  answer.value = null;
  try {
    answer.value = await askChef({ message: text, servings: Number(servings.value) || 2 });
  } catch (error) {
    showFailToast(error.message || "厨师暂时没回应");
  } finally {
    asking.value = false;
  }
}

/** 营养缺失按 null 展示"未知"，不显示成 0。 */
function nutrient(value, unit = "") {
  return value === null || value === undefined ? "未知" : `${value}${unit}`;
}

function percent(value) {
  return value === null || value === undefined ? "未知" : `${Math.round(Number(value) * 100)}%`;
}

onMounted(load);
</script>

<template>
  <main class="da-page">
    <header class="da-header">
      <AppBackButton fallback="diet" label="返回饮食" />
      <div>
        <h1>私人厨师</h1>
        <p>按菜名或食材找菜，也可以直接问做法。菜谱里没给的克重不会编，营养算不全就标"未知"。</p>
      </div>
    </header>
    <AssistantSteps />

    <section class="da-card">
      <h2>问厨师</h2>
      <label class="da-label">
        想吃什么
        <textarea v-model="message" placeholder="例如：红烧肉怎么做 / 冰箱有鸡蛋和番茄，能做什么" />
      </label>
      <div class="da-row">
        <label class="da-label" style="max-width: 120px">
          份数
          <input v-model.number="servings" type="number" min="1" max="12" />
        </label>
        <button class="da-btn" type="button" :disabled="asking" @click="submit">
          {{ asking ? "厨师正在看菜谱…" : "问一下" }}
        </button>
      </div>
      <div v-if="answer" class="da-list">
        <div class="da-item">
          <header>
            <strong>回答</strong>
            <span class="da-chip" :class="answer.degraded ? 'warn' : 'ok'">
              {{ answer.degraded ? "模型不可用，以下为菜谱原文" : "模型已回答" }}
            </span>
          </header>
          <p class="da-reply">{{ answer.reply }}</p>
          <p v-if="answer.degraded" class="da-reply" style="font-size: 12px">原因：{{ answer.degraded }}</p>
        </div>
        <div v-if="answer.sources?.length" class="da-item">
          <header><strong>引用来源</strong><small>{{ answer.sources.length }} 条</small></header>
          <p v-for="(source, index) in answer.sources" :key="index" class="da-reply" style="font-size: 12px">
            [{{ index + 1 }}] {{ source.title || source.url || "本地知识片段" }}
          </p>
        </div>
      </div>
    </section>

    <section class="da-card">
      <h2>食谱库</h2>
      <div class="da-row">
        <input v-model.trim="keyword" placeholder="菜名或食材，例如 番茄" style="flex: 1; min-width: 150px" />
        <button class="da-btn ghost" type="button" :disabled="loading" @click="load">搜索</button>
      </div>
      <div v-if="loading" class="da-empty">加载中…</div>
      <div v-else-if="!recipes.length" class="da-empty">没有匹配的食谱</div>
      <div v-else class="da-list">
        <div v-for="item in recipes" :key="item.id" class="da-item">
          <header>
            <strong>{{ item.name }}</strong>
            <span class="da-chip">{{ sourceLabels[item.sourceType] || item.sourceType }}</span>
          </header>
          <small>
            {{ item.servings }} 人份 · {{ (item.prepMinutes || 0) + (item.cookMinutes || 0) }} 分钟 ·
            营养覆盖 {{ percent(item.nutrition?.coverage) }}
          </small>
          <div class="da-row" style="margin-top: 8px">
            <button class="da-btn small ghost" type="button" @click="openDetail(item.id)">看做法</button>
          </div>
        </div>
      </div>
    </section>

    <section v-if="detail" class="da-card">
      <header class="da-row" style="justify-content: space-between">
        <h2 style="margin: 0">{{ detail.name }}</h2>
        <button class="da-btn small ghost" type="button" @click="detail = null">收起</button>
      </header>
      <p>食材</p>
      <div class="da-list">
        <div v-for="ing in detail.ingredients || []" :key="ing.id" class="da-item">
          <header>
            <strong>{{ ing.name }}</strong>
            <small>{{ ing.quantity ?? "适量" }} {{ ing.unit || "" }}</small>
          </header>
          <small>
            折合 {{ ing.grams === null || ing.grams === undefined ? "未知克重" : `${ing.grams} g` }} ·
            来源 {{ ing.gramStatus === "serving" ? "按份量换算" : ing.gramStatus === "direct" ? "菜谱直接给出" : "未确定" }}
          </small>
        </div>
      </div>
      <p style="margin-top: 12px">做法</p>
      <div class="da-list">
        <div v-for="step in detail.steps || []" :key="step.id" class="da-item">
          <p class="da-reply">{{ step.index }}. {{ step.description }}</p>
        </div>
      </div>
      <p style="margin-top: 12px">
        全菜营养合计：热量 {{ nutrient(detail.nutrition?.total?.calorie, " kcal") }} ·
        蛋白质 {{ nutrient(detail.nutrition?.total?.protein, " g") }} ·
        脂肪 {{ nutrient(detail.nutrition?.total?.fat, " g") }} ·
        碳水 {{ nutrient(detail.nutrition?.total?.carb, " g") }}
      </p>
      <p v-if="detail.nutrition?.missing?.length">
        未计入（食材营养数据缺失）：{{ detail.nutrition.missing.join("、") }}
      </p>
    </section>
  </main>
</template>
