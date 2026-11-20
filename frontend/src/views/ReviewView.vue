<script setup>
import { computed, onMounted, ref } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRouter } from "vue-router";
import AssistantSteps from "../components/AssistantSteps.vue";
import AppBackButton from "../components/AppBackButton.vue";
import { generateReview, getReviews, markDayStatus, getPlans, createObservation, getObservations, retractObservation, resolveObservation } from "../api/assistant";
import { getDietRecords } from "../api/diet";
import "../assets/diet-assistant.css";

const router = useRouter();
const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" });
const reports = ref([]);
const report = ref(null);
const busy = ref(false);
// 不填就走默认周期（上一个完整自然周）；填了按用户选的区间算。
const periodStart = ref("");
const periodEnd = ref("");
const dayState = ref("complete");
const plans = ref([]);
const selectedPlan = ref("");
const observations = ref([]);
const observation = ref({ description: "", occurredAt: `${today}T18:00` });
const recording = ref(false);
const withDraftPlan = ref(false);
const linkChoices = ref({});
const dietRecords = ref([]);

const metrics = computed(() => report.value?.metrics || {});
const execution = computed(() => metrics.value.execution || {});
const nutrition = computed(() => metrics.value.nutrition || {});
const weight = computed(() => metrics.value.weight || {});
const cost = computed(() => metrics.value.cost || {});
const feedback = computed(() => metrics.value.feedback || {});

function percent(value) {
  if (value === null || value === undefined) return "未知";
  return `${Math.round(Number(value) * 100)}%`;
}

function num(value, unit = "") {
  return value === null || value === undefined ? "未知" : `${value}${unit}`;
}

const gapLabels = {
  NO_ADOPTED_PLAN: "没有已采纳计划",
  MEALS_UNRECORDED: "有餐次没反馈",
  NO_DIET_RECORD: "没有饮食记录",
  DAY_COMPLETENESS_UNKNOWN: "没标记记录完整性",
  MACRO_MISSING: "营养素数据缺失",
  WEIGHT_SAMPLES_INSUFFICIENT: "体重样本不足",
  PRICE_UNAVAILABLE: "价格来源未配置",
  NO_FEEDBACK: "没有餐次评价",
};

async function load() {
  try {
    reports.value = (await getReviews(10)) || [];
    plans.value = (await getPlans()) || [];
    observations.value = (await getObservations()) || [];
    dietRecords.value = (await getDietRecords(today)) || [];
  } catch (error) {
    showFailToast(error.message || "复盘列表加载失败");
  }
}

async function generate() {
  busy.value = true;
  try {
    report.value = await generateReview({
      withDraftPlan: withDraftPlan.value,
      periodStart: periodStart.value || undefined,
      periodEnd: periodEnd.value || undefined,
    });
    const pending = report.value?.dataGaps?.length || 0;
    showSuccessToast(pending ? `报告已生成，另有 ${pending} 处数据缺口` : "报告已生成");
    await load();
  } catch (error) {
    showFailToast(error.message || "生成失败");
  } finally {
    busy.value = false;
  }
}

async function openReport(id) {
  report.value = reports.value.find((item) => item.id === id) || null;
}

async function submitDay() {
  try {
    await markDayStatus(today, { completeness: dayState.value });
    showSuccessToast("已记录今天的完整度");
  } catch (error) {
    showFailToast(error.message || "保存失败");
  }
}

async function recordObservation() {
  if (!observation.value.description.trim()) return showFailToast("先说说吃了什么");
  recording.value = true;
  try {
    await createObservation({ description: observation.value.description.trim(), occurredAt: observation.value.occurredAt + ":00", source: "text", completeness: "partial" });
    observation.value.description = "";
    observations.value = await getObservations();
    showSuccessToast("已记下这次饮食，未推断精确营养");
  } catch(e) { showFailToast(e.message); } finally { recording.value = false; }
}
async function retract(item) {
  if (recording.value) return; recording.value = true;
  try { await retractObservation(item.id); observations.value = await getObservations(); }
  catch(e) { showFailToast(e.message); } finally { recording.value = false; }
}
async function linkRecord(item) {
  if (!linkChoices.value[item.id]) return;
  recording.value = true;
  try { await resolveObservation(item.id, [Number(linkChoices.value[item.id])]); observations.value = await getObservations(); showSuccessToast("已关联，不会重复算作两次饮食"); }
  catch(e) { showFailToast(e.message); } finally { recording.value = false; }
}
function nextPlan(ref) {
  const match = /^meal_plan:(\d+):v(\d+)$/.exec(ref || "");
  if (match) router.push({ name: 'meal-plan', query: { planId: match[1], version: match[2] } });
}

onMounted(load);
</script>

<template>
  <main class="da-page">
    <header class="da-header">
      <AppBackButton fallback="diet" label="返回饮食" />
      <div>
        <h1>周期复盘</h1>
        <p>先算事实再解释：没反馈的餐次算"待确认"，不当成没执行；没记录的天不补 0。</p>
      </div>
    </header>
    <AssistantSteps />

    <section class="da-card">
      <h2>顺手记下实际吃了什么</h2>
      <form @submit.prevent="recordObservation"><label class="da-label">什么时候<input v-model="observation.occurredAt" type="datetime-local" required /></label><label class="da-label">吃了什么<textarea v-model="observation.description" maxlength="2000" required placeholder="例如：晚上和同事吃火锅，具体份量记不清" /></label><button class="da-btn" :disabled="recording">记下来</button></form>
      <p>简单记录只作为饮食线索，不编造热量；补了正式饮食记录后可以关联。</p>
      <div v-for="item in observations" :key="item.id" class="da-item"><strong>{{ item.description }}</strong><p>{{ item.occurredAt?.replace('T', ' ') }} · {{ item.status === 'retracted' ? '已撤回' : item.status === 'resolved' ? '已关联正式记录' : '营养待确认' }}</p><div v-if="item.status !== 'retracted'" class="da-row"><button class="da-btn small ghost" :disabled="recording" @click="retract(item)">撤回</button><template v-if="item.status !== 'resolved' && dietRecords.length"><label class="da-label">关联今天已记的饮食<select v-model="linkChoices[item.id]"><option value="">请选择</option><option v-for="record in dietRecords" :key="record.id" :value="record.id">{{ record.foodNameSnapshot || record.foodName || record.name }} · {{ record.recordTime }}</option></select></label><button class="da-btn small ghost" :disabled="recording || !linkChoices[item.id]" @click="linkRecord(item)">关联记录</button></template></div></div>
    </section>
    <section class="da-card"><h2>今天记全了吗</h2>
      <p>日均值要能说清分母是"已记录天"还是"完整记录天"，所以这一步只能你自己勾。</p>
      <div class="da-row" style="margin-top: 10px">
        <select v-model="dayState" style="min-width: 160px">
          <option value="complete">记录完整</option>
          <option value="partial">记了一部分</option>
          <option value="unknown">不确定</option>
        </select>
        <button class="da-btn" type="button" @click="submitDay">保存（{{ today }}）</button>
      </div>
    </section>

    <section class="da-card">
      <div class="da-row" style="justify-content: space-between">
        <h2 style="margin: 0">生成复盘</h2>
        <button class="da-btn" type="button" :disabled="busy" @click="generate">{{ busy ? "统计中…" : "生成报告" }}</button>
      </div>
      <div class="da-grid-2" style="margin-top: 10px">
        <label class="da-label">开始日期<input v-model="periodStart" type="date" /></label>
        <label class="da-label">结束日期<input v-model="periodEnd" type="date" /></label>
      </div>
      <label class="da-row"><input v-model="withDraftPlan" type="checkbox" />同时生成下周草稿（不会自动采纳）</label>
      <p style="margin-top: 8px">不填按上一个完整自然周算（周一至周日）。记录被补记后重新生成，旧报告会标为过期但不会被改写。</p>
    </section>

    <section v-if="report" class="da-card">
      <header class="da-row" style="justify-content: space-between">
        <h2 style="margin: 0">{{ report.periodStart }} ~ {{ report.periodEnd }}</h2>
        <span class="da-chip" :class="report.status === 'current' ? 'ok' : 'muted'">
          {{ report.status === "current" ? "当前有效" : "已过期" }}
        </span>
      </header>

      <div class="da-item" style="margin-top: 12px">
        <strong>{{ execution.primaryText }}</strong>
        <p v-if="execution.secondaryText" style="margin-top: 6px">{{ execution.secondaryText }}</p>
        <p style="margin-top: 6px">{{ execution.note }}</p>
      </div>

      <div class="da-grid-2" style="margin-top: 12px">
        <div class="da-metric"><span>确认完成率</span><strong>{{ percent(execution.confirmedExecutionRate) }}</strong></div>
        <div class="da-metric"><span>执行覆盖率</span><strong>{{ percent(execution.executionCoverage) }}</strong></div>
        <div class="da-metric"><span>完成 / 跳过</span><strong style="font-size: 16px">{{ execution.executed ?? 0 }} / {{ execution.explicitlySkipped ?? 0 }}</strong></div>
        <div class="da-metric"><span>待确认餐次</span><strong>{{ execution.unrecordedMeals ?? 0 }}</strong></div>
        <div class="da-metric"><span>已记录天</span><strong>{{ nutrition.recordedDays ?? 0 }}</strong></div>
        <div class="da-metric"><span>完整记录天</span><strong>{{ nutrition.completeDays ?? 0 }}</strong></div>
        <div class="da-metric"><span>日均摄入</span><strong style="font-size: 16px">{{ num(nutrition.averageIntakeCalorie, " kcal") }}</strong><small>分母：已记录天</small></div>
        <div class="da-metric"><span>体重变化</span><strong style="font-size: 16px">{{ num(weight.changeKg, " kg") }}</strong><small>样本 {{ weight.sampleCount ?? 0 }} 条</small></div>
      </div>

      <p style="margin-top: 10px">{{ nutrition.averageBasis === "recorded_days" ? "营养日均值按已记录天计算" : "" }}{{ weight.note }}</p>

      <div v-if="report.dataGaps?.length" style="margin-top: 12px">
        <h2 style="font-size: 14px">数据缺口</h2>
        <div class="da-row" style="margin-top: 6px">
          <span v-for="gap in report.dataGaps" :key="gap.code" class="da-chip warn" :title="gap.message">
            {{ gapLabels[gap.code] || gap.code }}
          </span>
        </div>
      </div>

      <div v-if="report.suggestions?.length" style="margin-top: 12px">
        <h2 style="font-size: 14px">改进建议（最多 3 条）</h2>
        <div class="da-list">
          <div v-for="item in report.suggestions" :key="item.code" class="da-item">
            <header>
              <strong style="font-size: 14px">{{ item.message }}</strong>
              <span class="da-chip" :class="item.kind === 'hypothesis' ? 'muted' : 'ok'">
                {{ item.kind === "hypothesis" ? "推测" : "数据" }}
              </span>
            </header>
            <small>依据：{{ item.evidence }}</small>
          </div>
        </div>
      </div>

      <button v-if="report.draftPlanRef" class="da-btn ghost" @click="nextPlan(report.draftPlanRef)">查看下周草稿，再决定采纳 →</button>
      <p style="margin-top: 6px">满意度反馈 {{ feedback.count ?? 0 }} 条 · 实付 {{ cost.actualPaid == null ? "待填写" : `¥${cost.actualPaid}` }}</p>
      <div class="da-row" style="margin-top: 12px">
        <button class="da-btn ghost" type="button" @click="router.push({ name: 'meal-plan' })">去调整下周计划</button>
        <button class="da-btn ghost" type="button" @click="router.push({ name: 'shopping' })">看采购</button>
      </div>
    </section>

    <section class="da-card">
      <h2>情况有变化，调整后面的安排</h2>
      <label class="da-label">选择计划<select v-model="selectedPlan"><option value="">请选择要调整的计划</option><option v-for="plan in plans" :key="plan.planId" :value="plan.planId">{{ plan.startDate }} ~ {{ plan.endDate }} · {{ plan.status === 'active' ? '生效中' : '草稿' }}</option></select></label>
      <button class="da-btn" :disabled="!selectedPlan" @click="router.push({ name: 'meal-plan', query: { planId: selectedPlan } })">打开计划，查看与调整</button>
    </section>

    <section class="da-card">
      <h2>历史报告</h2>
      <div v-if="!reports.length" class="da-empty">还没有复盘报告</div>
      <div v-else class="da-list">
        <div v-for="item in reports" :key="item.id" class="da-item">
          <header>
            <strong style="font-size: 14px">{{ item.periodStart }} ~ {{ item.periodEnd }}</strong>
            <span class="da-chip" :class="item.status === 'current' ? 'ok' : 'muted'">{{ item.status === "current" ? "有效" : "过期" }}</span>
          </header>
          <div class="da-row" style="margin-top: 8px">
            <button class="da-btn small ghost" type="button" @click="openReport(item.id)">查看</button>
          </div>
        </div>
      </div>
    </section>
  </main>
</template>
