<script setup>
import { computed, onMounted, ref } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRoute, useRouter } from "vue-router";
import AssistantSteps from "../components/AssistantSteps.vue";
import AppBackButton from "../components/AppBackButton.vue";
import { buildShoppingList, getShoppingList, purchaseShoppingItem, refreshShoppingPrices, reversePurchase } from "../api/assistant";
import "../assets/diet-assistant.css";

const route = useRoute();
const router = useRouter();
const list = ref(null);
const loading = ref(false);
const buying = ref({});
const paying = ref({});
const requestKeys = new Map();
const error = ref("");

const stateLabels = { needs_check: "待确认", to_buy: "需要买", purchased: "已购买", skipped: "不需要" };

const summary = computed(() => list.value?.totals || {});
const items = computed(() => list.value?.items || []);
const purchased = computed(() => items.value.filter((item) => item.state === "purchased"));
const paidItems = computed(() => purchased.value.filter((item) => item.price?.paidPrice != null));
// 只统计用户真填过的实付；没填就是"待填写"，不按 0 算进合计。
const paidTotal = computed(() => (paidItems.value.length
  ? paidItems.value.reduce((sum, item) => sum + Number(item.price.paidPrice || 0), 0).toFixed(2)
  : null));

async function build() {
  const planId = Number(route.query.planId);
  if (!planId) return showFailToast("请从周计划页进入，或先采纳一份计划");
  loading.value = true;
  try {
    list.value = await buildShoppingList({ planId, versionNumber: Number(route.query.version) || undefined });
    showSuccessToast("采购清单已生成");
  } catch (error) {
    showFailToast(error.message || "生成失败");
  } finally {
    loading.value = false;
  }
}

async function reload() {
  if (!list.value?.id) return;
  list.value = await getShoppingList(list.value.id);
}

async function refreshPrice() {
  if (!list.value?.id) return;
  try {
    const result = await refreshShoppingPrices(list.value.id);
    if (result?.available === false) {
      showFailToast(result.message || "价格来源未配置，暂无参考价");
    } else {
      showSuccessToast("价格已刷新");
    }
    await reload();
  } catch (error) {
    showFailToast(error.message || "刷新失败");
  }
}

async function buy(item) {
  const price = paying.value[item.id];
  buying.value[item.id] = true;
  try {
    list.value = await purchaseShoppingItem(
      item.id,
      { actualQuantity: item.buyQuantity ?? null, paidPrice: price === "" || price === undefined ? null : Number(price) },
      requestKeys.get(item.id) || (requestKeys.set(item.id, crypto.randomUUID()), requestKeys.get(item.id))
    );
    requestKeys.delete(item.id);
    showSuccessToast("已记住买过，不估算剩余克重");
  } catch (error) {
    showFailToast(error.message || "记录失败");
  } finally {
    buying.value[item.id] = false;
  }
}

async function undo(item) {
  buying.value[item.id] = true;
  try { list.value = await reversePurchase(item.id, { reason: "用户撤销" }, crypto.randomUUID()); showSuccessToast("已撤销购买"); }
  catch (e) { showFailToast(e.message); } finally { buying.value[item.id] = false; }
}

onMounted(() => {
  if (route.query.planId) build();
});
</script>

<template>
  <main class="da-page">
    <header class="da-header">
      <AppBackButton fallback="meal-plan" label="返回周计划" />
      <div>
        <h1>采购清单</h1>
        <p>记得之前买过什么，顺便确认还有没有剩余；不需要管理克重库存。</p>
      </div>
    </header>
    <AssistantSteps />

    <section class="da-card">
      <div class="da-row">
        <button class="da-btn" type="button" :disabled="loading" @click="build">
          {{ loading ? "生成中…" : "按计划生成清单" }}
        </button>
        <button v-if="list" class="da-btn ghost" type="button" @click="refreshPrice">刷新参考价</button>
      </div>
      <p v-if="!route.query.planId" style="margin-top: 10px">
        当前没有指定计划，请先在
        <button class="da-btn small ghost" type="button" @click="router.push({ name: 'meal-plan' })">周计划</button>
        里采纳一份。
      </p>
    </section>

    <section v-if="list" class="da-card">
      <header class="da-row" style="justify-content: space-between">
        <h2 style="margin: 0">{{ list.startDate }} ~ {{ list.endDate }}</h2>
        <span class="da-chip" :class="summary.priceStatus === 'unknown' ? 'warn' : 'ok'">
          {{ summary.priceStatus === "unknown" ? "价格来源未配置" : "价格已获取" }}
        </span>
      </header>
      <div class="da-grid-2" style="margin-top: 12px">
        <div class="da-metric"><span>预算状态</span><strong style="font-size: 16px">{{ summary.budgetStatus === "unverified" ? "未核验" : summary.budgetStatus }}</strong></div>
        <div class="da-metric"><span>条目</span><strong>{{ items.length }}</strong></div>
        <div class="da-metric"><span>食材待核对项</span><strong>{{ summary.unknownItemCount ?? "未知" }}</strong></div>
        <div class="da-metric">
          <span>实付合计</span>
          <strong style="font-size: 16px">{{ paidTotal == null ? "待填写" : `¥${paidTotal}` }}</strong>
          <small v-if="purchased.length">已填 {{ paidItems.length }}/{{ purchased.length }} 项</small>
        </div>
      </div>
      <p v-if="summary.note" style="margin-top: 10px">{{ summary.note }}</p>

      <div v-if="!items.length" class="da-empty" style="margin-top: 12px">清单为空</div>
      <div v-else class="da-list">
        <div v-for="item in items" :key="item.id" class="da-item">
          <header>
            <strong>{{ item.label }}</strong>
            <span class="da-chip" :class="item.state === 'purchased' ? 'ok' : item.state === 'to_buy' ? 'warn' : 'muted'">
              {{ stateLabels[item.state] || item.state }}
            </span>
          </header>
          <small>
            菜谱参考用量 {{ item.needQuantity ?? "待定" }} {{ item.unit || "" }}，不是家中准确缺量。
            {{ item.state === "needs_check" ? "先看一下还有没有剩余，再决定是否买。" : item.state === "skipped" ? "今天确认还有一些，可先看看是否够做这道菜。" : "购买后可顺手记一下。" }}
          </small>
          <div v-if="item.state !== 'purchased'" class="da-row" style="margin-top: 8px">
            <input v-model.trim="paying[item.id]" type="number" min="0" placeholder="实付金额（可空）" style="width: 150px" />
            <button class="da-btn small" type="button" :disabled="buying[item.id]" @click="buy(item)">
              {{ buying[item.id] ? "记录中…" : "标记已买" }}
            </button>
          </div>
          <div v-else class="da-row"><small v-if="item.price?.paidPrice != null">实付 ¥{{ item.price?.paidPrice }}</small><button class="da-btn small ghost" :disabled="buying[item.id]" @click="undo(item)">撤销购买</button></div>
        </div>
      </div>
    </section>

    <section v-if="list" class="da-card">
      <h2>下一步</h2>
      <div class="da-row">
        <button class="da-btn ghost" type="button" @click="router.push({ name: 'meal-plan', query: { planId: route.query.planId } })">回去标记吃了没</button>
        <button class="da-btn ghost" type="button" @click="router.push({ name: 'review' })">看本周复盘</button>
      </div>
    </section>
  </main>
</template>
