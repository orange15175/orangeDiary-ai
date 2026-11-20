<script setup>
import { computed, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { showFailToast, showSuccessToast } from 'vant';
import AppBackButton from '../components/AppBackButton.vue';
import AssistantSteps from '../components/AssistantSteps.vue';
import { createPlanDraft, getPlans, getPlan, getPlanVersions, adoptPlan, setMealExecution, createIngredientCheck, answerIngredientCheck, createPlanAdjustment } from '../api/assistant';
import '../assets/diet-assistant.css';
const route = useRoute(); const router = useRouter();
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
const form = ref({ startDate: today, days: 3, mealTypes: ['breakfast', 'lunch', 'dinner'], ingredientMode: 'prefer_available' });
const plans = ref([]); const draft = ref(null); const versions = ref([]); const check = ref(null);
const busy = ref(false); const error = ref('');
const adjustment = ref({ note: '', maxPrepMinutes: '', excludedFoods: '' });
const labels = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐' };
const states = { planned: '待确认', executed: '按计划吃了', skipped: '没按这餐吃', unknown: '记不清' };
const isDraft = computed(() => draft.value?.versionStatus === 'draft');
const active = computed(() => draft.value?.versionStatus === 'adopted');
async function refresh() { plans.value = await getPlans(); }
async function open(planId, version) {
  busy.value = true; error.value = ''; check.value = null;
  try { draft.value = await getPlan(planId, version); versions.value = await getPlanVersions(planId); await router.replace({ name: 'meal-plan', query: { planId, version: draft.value.versionNumber } }); }
  catch(e) { error.value = e.message; } finally { busy.value = false; }
}
async function generate() {
  if (!form.value.mealTypes.length) return showFailToast('至少选择一餐');
  busy.value = true; error.value = ''; check.value = null;
  try { draft.value = await createPlanDraft({ ...form.value, days: Number(form.value.days) }); await refresh(); await open(draft.value.planId, draft.value.versionNumber); }
  catch(e) { error.value = e.message; } finally { busy.value = false; }
}
async function adopt() {
  busy.value = true;
  try { draft.value = await adoptPlan(draft.value.planId, draft.value.versionNumber); await refresh(); versions.value = await getPlanVersions(draft.value.planId); showSuccessToast('计划已生效'); }
  catch(e) { error.value = e.message; } finally { busy.value = false; }
}
async function mark(meal, state) {
  if (busy.value) return; busy.value = true;
  try { await setMealExecution(meal.id, state); meal.executionState = state; showSuccessToast('已记下，不推断实际摄入或剩余克重'); }
  catch(e) { error.value = e.message; } finally { busy.value = false; }
}
async function startCheck() {
  busy.value = true;
  try { check.value = await createIngredientCheck({ planId: draft.value.planId, versionNumber: draft.value.versionNumber }); }
  catch(e) { error.value = e.message; } finally { busy.value = false; }
}
async function answer(item, answer) {
  if (busy.value) return; busy.value = true;
  try { check.value = await answerIngredientCheck(check.value.id, { version: check.value.version, answers: [{ itemId: item.id, answer }] }); }
  catch(e) { error.value = e.message; } finally { busy.value = false; }
}
async function adjust() {
  if (!adjustment.value.note.trim()) return showFailToast('先说说哪里变了');
  busy.value = true; error.value = ''; check.value = null;
  try {
    draft.value = await createPlanAdjustment(draft.value.planId, { note: adjustment.value.note.trim(), maxPrepMinutes: Number(adjustment.value.maxPrepMinutes) || null, excludedFoods: adjustment.value.excludedFoods.split(/[,，、]/).map(x=>x.trim()).filter(Boolean) });
    versions.value = await getPlanVersions(draft.value.planId); await refresh();
    await router.replace({ name: 'meal-plan', query: { planId: draft.value.planId, version: draft.value.versionNumber } });
    showSuccessToast('先看看变化，采纳后才生效');
  } catch(e) { error.value = e.message; } finally { busy.value = false; }
}
onMounted(async () => { try { await refresh(); if (route.query.planId) await open(Number(route.query.planId), Number(route.query.version) || undefined); } catch(e) { error.value=e.message; } });
</script>
<template>
  <main class="da-page">
    <header class="da-header"><AppBackButton fallback="diet" label="返回饮食" /><div><h1>周计划</h1><p>先看安排，再决定采纳；家里食材只需顺手确认，不用管理克重库存。</p></div></header>
    <AssistantSteps />
    <p v-if="error" class="da-card" role="alert">{{ error }}</p>
    <form class="da-card" @submit.prevent="generate">
      <h2>安排几天吃什么</h2>
      <div class="da-grid-2"><label class="da-label">开始日期<input v-model="form.startDate" type="date" required /></label><label class="da-label">天数<input v-model.number="form.days" type="number" min="1" max="7" required /></label></div>
      <fieldset><legend>安排哪些餐</legend><label v-for="(name, type) in labels" :key="type"><input v-model="form.mealTypes" type="checkbox" :value="type" />{{ name }}　</label></fieldset>
      <label class="da-label">食材安排<select v-model="form.ingredientMode"><option value="prefer_available">优先考虑最近确认还有的食材</option><option value="allow_purchase">缺了可以买</option><option value="available_only">只考虑今天确认还有的食材</option></select></label>
      <p>食材记忆不代表份量足够。只有旧线索或没确认时，会提醒你再看一下。</p>
      <button class="da-btn" :disabled="busy">{{ busy ? '处理中…' : '生成草稿' }}</button>
    </form>
    <section v-if="draft" class="da-card" aria-live="polite">
      <header class="da-row"><h2>{{ draft.startDate }} ~ {{ draft.endDate }}</h2><span class="da-chip">{{ active ? '当前生效' : isDraft ? '待采纳草稿' : draft.versionStatus === 'infeasible' ? '当前条件无法排全' : '历史版本' }} · v{{ draft.versionNumber }}</span></header>
      <p v-if="draft.checks?.code !== 'OK'">当前候选食谱不足以覆盖所有餐次，请调整条件或补充食谱。这份结果不能采纳。</p>
      <p v-if="draft.reason">调整原因：{{ draft.reason }}</p>
      <div v-if="draft.checks?.diff?.length" class="da-list"><h2>调整前后</h2><div v-for="(change, index) in draft.checks.diff" :key="index" class="da-item"><strong>{{ change.date }} {{ labels[change.mealType] }}</strong><p>{{ change.fromName || '原餐' }} → {{ change.toName || '原餐' }}</p></div></div>
      <div v-for="meal in draft.meals" :key="meal.id" class="da-item"><header><strong>{{ meal.date }} {{ labels[meal.mealType] }} · {{ meal.recipeName }}</strong><span class="da-chip">{{ states[meal.executionState] }}</span></header><p>{{ meal.servings }} 份 · 菜谱参考热量 {{ meal.nutrition?.calorie ?? '未知' }} 千卡，实际摄入以记录为准。</p><div v-if="active" class="da-row"><button class="da-btn small ghost" :disabled="busy" @click="mark(meal, 'executed')">按计划吃了</button><button class="da-btn small ghost" :disabled="busy" @click="mark(meal, 'skipped')">没按这餐吃</button><button class="da-btn small ghost" :disabled="busy" @click="mark(meal, 'unknown')">记不清</button></div></div>
      <div class="da-row"><button v-if="isDraft" class="da-btn" :disabled="busy || draft.checks?.code !== 'OK'" @click="adopt">采纳这份计划</button><button class="da-btn ghost" :disabled="busy" @click="startCheck">顺便核对食材</button><button v-if="active" class="da-btn" @click="router.push({ name: 'shopping', query: { planId: draft.planId, version: draft.versionNumber } })">看看需要买什么 →</button></div>
      <div v-if="check" class="da-card"><h2>这些食材还有吗？</h2><p>不问精确数量，不确定的保留待确认。</p><div v-for="item in check.items" :key="item.id" class="da-item"><strong>{{ item.label }}</strong><p>{{ item.status === 'confirmed' ? '今天确认还有一些' : item.status === 'to_buy' ? '确认没有，可加入购买建议' : '之前可能买过或用过，现在有剩吗？' }}</p><div class="da-row"><button class="da-btn small ghost" :disabled="busy" @click="answer(item, 'some')">还有一些</button><button class="da-btn small ghost" :disabled="busy" @click="answer(item, 'none')">用完了</button><button class="da-btn small ghost" :disabled="busy" @click="answer(item, 'unknown')">不确定</button></div></div></div>
      <form v-if="active" class="da-card" @submit.prevent="adjust"><h2>情况变了，调整后面的安排</h2><label class="da-label">发生了什么<textarea v-model="adjustment.note" required maxlength="200" placeholder="例如：今晚加班，想做简单一点" /></label><div class="da-grid-2"><label class="da-label">后续每餐最多几分钟<input v-model="adjustment.maxPrepMinutes" type="number" min="1" max="600" placeholder="不变可留空" /></label><label class="da-label">后面不用哪些食材<input v-model="adjustment.excludedFoods" placeholder="例如：鸡腿、虾" /></label></div><p>已执行或锁定的餐保留，原有过敏和设备限制继续生效。</p><button class="da-btn" :disabled="busy">生成调整草稿</button></form>
      <div v-if="versions.length" class="da-row"><span>版本记录</span><button v-for="version in versions" :key="version.version" class="da-btn small ghost" :disabled="busy" @click="open(draft.planId, version.version)">v{{ version.version }} {{ version.status === 'draft' ? '草稿' : version.status === 'adopted' ? '生效' : '历史' }}</button></div>
      <RouterLink class="da-next" :to="{ name: 'review' }">记下实际吃了什么，或看看复盘 →</RouterLink>
    </section>
    <section class="da-card"><h2>已有计划</h2><p v-if="!plans.length" class="da-empty">还没有计划，先生成一份草稿。</p><div v-for="plan in plans" :key="plan.planId" class="da-item"><header><strong>{{ plan.startDate }} ~ {{ plan.endDate }}</strong><span>{{ plan.meals?.length || 0 }} 餐</span></header><button class="da-btn small ghost" :disabled="busy" @click="open(plan.planId)">打开计划与调整记录</button></div></section>
  </main>
</template>
