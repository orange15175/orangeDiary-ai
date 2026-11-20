<script setup>
import { onMounted, ref } from 'vue';
import { showFailToast, showSuccessToast } from 'vant';
import AppBackButton from '../components/AppBackButton.vue';
import AssistantSteps from '../components/AssistantSteps.vue';
import { getDietPreference, saveDietPreference, getKnownIngredients, saveKnownIngredient } from '../api/assistant';
import '../assets/diet-assistant.css';

const form = ref({ allergens: '', dislikes: '', equipment: '', prepTimeLimit: '', budgetValue: '', city: '', version: 0 });
const memories = ref([]);
const label = ref('');
const busy = ref(false);
const loading = ref(true);
const error = ref('');
const split = (text) => String(text || '').split(/[,，、]/).map(x => x.trim()).filter(Boolean);
async function load() {
  loading.value = true;
  try {
    const preference = await getDietPreference();
    form.value = { ...preference, allergens: (preference.allergens || []).join('、'), dislikes: (preference.dislikes || []).join('、'), equipment: (preference.equipment || []).join('、') };
    memories.value = await getKnownIngredients();
    error.value = '';
  } catch (e) { error.value = e.message || '加载失败，请重试'; }
  finally { loading.value = false; }
}
async function save() {
  busy.value = true;
  try {
    const result = await saveDietPreference({ ...form.value, allergens: split(form.value.allergens), dislikes: split(form.value.dislikes), equipment: split(form.value.equipment), prepTimeLimit: Number(form.value.prepTimeLimit) || null, budgetValue: form.value.budgetValue === '' ? null : Number(form.value.budgetValue) });
    form.value.version = result.version;
    showSuccessToast('偏好已保存，下次安排会继续使用');
  } catch (e) { showFailToast(e.message); }
  finally { busy.value = false; }
}
async function remember(item, presence) {
  if (busy.value) return;
  busy.value = true;
  try {
    await saveKnownIngredient({ id: item.id || undefined, label: item.label, foodItemId: item.foodItemId, version: item.version, presence });
    memories.value = await getKnownIngredients();
    label.value = '';
    showSuccessToast('记住了');
  } catch (e) { showFailToast(e.message); }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template>
  <main class="da-page">
    <header class="da-header"><AppBackButton fallback="diet" label="返回饮食" /><div><h1>偏好与食材</h1><p>记住你的习惯，也顺带记一下家里可能还有什么。</p></div></header>
    <AssistantSteps />
    <p v-if="loading" role="status">正在加载…</p>
    <section v-else-if="error" class="da-card" role="alert"><p>{{ error }}</p><button class="da-btn" @click="load">重新加载</button></section>
    <template v-else>
      <form class="da-card" @submit.prevent="save">
        <h2>我的饮食偏好</h2>
        <label class="da-label">过敏食材（多项用顿号分开）<input v-model="form.allergens" placeholder="例如：花生、虾" /></label>
        <label class="da-label">不喜欢吃<input v-model="form.dislikes" placeholder="例如：香菜" /></label>
        <label class="da-label">可用厨具<input v-model="form.equipment" placeholder="例如：炒锅、电饭煲" /></label>
        <div class="da-grid-2"><label class="da-label">每餐做饭时间（分钟）<input v-model="form.prepTimeLimit" type="number" min="1" max="600" placeholder="不限制可留空" /></label><label class="da-label">参考预算（元）<input v-model="form.budgetValue" type="number" min="0" placeholder="可留空" /></label></div>
        <label class="da-label">城市<input v-model="form.city" placeholder="可留空" /></label>
        <p>预算目前作为偏好保存；没有可靠报价时，不会承诺已经满足预算。</p>
        <button class="da-btn" type="submit" :disabled="busy">{{ busy ? '保存中…' : '保存偏好' }}</button>
      </form>
      <section class="da-card">
        <h2>家里可能还有</h2><p>昨天买过或用过，不代表今天一定有剩余。回答还有一些、用完了或不确定就好。</p>
        <form class="da-row" @submit.prevent="label.trim() && remember({ label: label.trim() }, 'present')"><label class="da-label">顺手记一种食材<input v-model="label" maxlength="120" placeholder="例如：鸡腿" /></label><button class="da-btn" :disabled="busy || !label.trim()">记一下</button></form>
        <p v-if="!memories.length" class="da-empty">还没有食材线索，之后的采购建议和购买记录也会显示在这里。</p>
        <div v-for="item in memories" :key="item.id || `hint-${item.label}`" class="da-item">
          <header><strong>{{ item.label }}</strong><span class="da-chip">{{ item.presence === 'present' ? '今天确认还有' : item.presence === 'absent' ? '已用完' : '余量待确认' }}</span></header>
          <p>{{ item.hint }}</p><div class="da-row"><button class="da-btn small ghost" :disabled="busy" @click="remember(item, 'present')">还有一些</button><button class="da-btn small ghost" :disabled="busy" @click="remember(item, 'absent')">用完了</button><button class="da-btn small ghost" :disabled="busy" @click="remember(item, 'unknown')">不确定</button></div>
        </div>
        <RouterLink class="da-next" :to="{ name: 'meal-plan' }">接下来，安排几天吃什么 →</RouterLink>
      </section>
    </template>
  </main>
</template>
