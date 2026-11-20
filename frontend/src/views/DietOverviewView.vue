<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { showFailToast } from "vant";
import AppBottomNav from "../components/AppBottomNav.vue";
import { getDailySummary, getWeightProgress } from "../api/diet";
import { getActiveUserCount } from "../api/auth";
import { getCheckinFootprint, recordTodayCheckin } from "../api/checkin";
import otterWave from "../assets/otter/wave.png";
import otterWeight from "../assets/otter/weight.png";
import otterCelebrate from "../assets/otter/celebrate.png";
import { downloadTodayDietReport } from "../api/reports";
import { shanghaiDate } from "../utils/reportDate";

const router = useRouter();
const today = ref(shanghaiDate());
const generatingReport = ref(false);
const summary = ref({});
const progress = ref({});
const activeUserCount = ref(null);
const monthlyFootprint = ref(null);
const loading = ref(false);
const loadError = ref("");
// 与 PDF 一致：四舍五入至一位小数，整数不补 .0，不加千分位。
const calorieFormatter = new Intl.NumberFormat("en-US", {
  useGrouping: false,
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});
const number = (value) => calorieFormatter.format(Number(value || 0));
const intakeAmount = computed(() => Number(summary.value.totalIntakeCalorie || 0));
const targetAmount = computed(() => Number(summary.value.targetDailyCalorie || 0));
const isOverBudget = computed(
  () => targetAmount.value > 0 && intakeAmount.value > targetAmount.value,
);
const displayedBalance = computed(() =>
  isOverBudget.value
    ? intakeAmount.value - targetAmount.value
    : Math.max(targetAmount.value - intakeAmount.value, 0),
);
const intakePercent = computed(() => {
  return targetAmount.value
    ? Math.min(100, (intakeAmount.value / targetAmount.value) * 100)
    : 0;
});
const progressSegments = computed(() => {
  if (!targetAmount.value) return { budget: 0, overage: 0 };
  if (!isOverBudget.value) return { budget: intakePercent.value, overage: 0 };
  const budget = (targetAmount.value / intakeAmount.value) * 100;
  return { budget, overage: 100 - budget };
});
const progressText = computed(() => {
  const intake = `已摄入 ${number(intakeAmount.value)} kcal`;
  if (!targetAmount.value) return `${intake}，尚未设置每日目标`;
  return isOverBudget.value
    ? `${intake}，多吃了 ${number(displayedBalance.value)} kcal`
    : `${intake}，每日目标 ${number(targetAmount.value)} kcal`;
});
const monthDates = computed(() => {
  const [year, month] = today.value.split("-").map(Number);
  const days = new Date(year, month, 0).getDate();
  return Array.from({ length: days }, (_, index) => `${today.value.slice(0, 8)}${String(index + 1).padStart(2, "0")}`);
});
const checkedDateSet = computed(() => new Set(monthlyFootprint.value?.checkedDates || []));

function monthRange(date) {
  const [year, month] = date.split("-").map(Number);
  const endDay = new Date(year, month, 0).getDate();
  return { startDate: `${date.slice(0, 8)}01`, endDate: `${date.slice(0, 8)}${String(endDay).padStart(2, "0")}` };
}

async function load() {
  today.value = shanghaiDate();
  loading.value = true;
  loadError.value = "";
  try {
    // 首次进入主页时先确保今天已签到，避免网格首次渲染晚一拍点亮。
    await recordTodayCheckin().catch(() => null);
    const range = monthRange(today.value);
    const [daily, weight, userCount, footprint] = await Promise.all([
      getDailySummary(today.value),
      getWeightProgress(),
      // 用户数接口不可用时，不影响今日饮食主页其他模块。
      getActiveUserCount().catch(() => null),
      getCheckinFootprint(range.startDate, range.endDate).catch(() => null),
    ]);
    summary.value = daily || {};
    progress.value = weight || {};
    activeUserCount.value = userCount?.totalUsers ?? null;
    monthlyFootprint.value = footprint;
  } catch (error) {
    loadError.value = error.message || "今日数据加载失败";
    showFailToast(error.message || "今日数据加载失败");
  } finally {
    loading.value = false;
  }
}
async function generateReport() {
  if (generatingReport.value) return;
  generatingReport.value = true;
  try {
    await downloadTodayDietReport();
  } catch (error) {
    showFailToast(error.message || "报告生成失败，请稍后重试");
  } finally {
    generatingReport.value = false;
  }
}
onMounted(load);
</script>

<template>
  <main class="diet-overview-page">
    <header class="page-header">
      <div>
        <h1>今日饮食</h1>
        <p>轻松记录，慢慢靠近更好的自己</p>
      </div>
      <img class="otter-wave" :src="otterWave" alt="" />
      <span v-if="activeUserCount !== null" class="user-counter">已有 <strong>{{ activeUserCount }}</strong> 位用户</span>
    </header>
    <van-loading v-if="loading" class="state" vertical>正在加载</van-loading>
    <section v-else-if="loadError" class="state error-state" role="alert"><p>{{ loadError }}</p><van-button @click="load">重新加载</van-button></section>
    <template v-else>
      <section class="budget-panel">
        <p class="panel-title">今日热量预算</p>
        <div class="budget-number">
          <strong :class="{ 'over-budget': isOverBudget }">{{
            number(displayedBalance)
          }}</strong
          ><span>{{ isOverBudget ? "kcal 多吃了" : "kcal 可摄入" }}</span
          ><small
            >每日目标<br />{{ number(summary.targetDailyCalorie) }} kcal</small
          >
        </div>
        <div
          class="progress-track"
          role="progressbar"
          aria-label="今日热量预算进度"
          :aria-valuenow="intakePercent"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuetext="progressText"
        >
          <i
            class="budget-fill"
            :style="{ width: `${progressSegments.budget}%` }"
          ></i>
          <i
            v-if="progressSegments.overage"
            class="overage-fill"
            :style="{ width: `${progressSegments.overage}%` }"
          ></i>
        </div>
        <div class="budget-meta">
          <span>已摄入 {{ number(summary.totalIntakeCalorie) }} kcal</span
          ><span>运动消耗 {{ number(summary.totalBurnCalorie) }} kcal</span>
        </div>
        <p v-if="!Number(summary.targetDailyCalorie)" class="empty-hint">设置每日目标后即可查看摄入进度</p>
      </section>
      <section class="metrics">
        <div>
          <span>摄入</span
          ><strong class="orange">{{
            number(summary.totalIntakeCalorie)
          }}</strong
          ><small>kcal</small>
        </div>
        <div>
          <span>{{ isOverBudget ? "多吃了" : "还能摄入" }}</span
          ><strong :class="{ 'over-budget': isOverBudget }">{{
            number(displayedBalance)
          }}</strong
          ><small>kcal</small>
        </div>
        <div>
          <span>运动消耗</span
          ><strong>{{ number(summary.totalBurnCalorie) }}</strong
          ><small>kcal</small>
        </div>
      </section>
      <section class="weight-panel">
        <header>
          <div>
            <h2>体重进度</h2>
          </div>
          <span>公斤</span>
        </header>
        <div class="weight-grid">
          <span
            >初始<strong>{{ progress.initialWeightKg || 0 }}</strong></span
          ><span
            >当前<strong>{{ progress.latestWeightKg || 0 }}</strong></span
          ><span
            >已减去<strong>{{ progress.lostWeightKg || 0 }}</strong></span
          ><span
            >目标<strong>{{ progress.targetWeightKg || 0 }}</strong></span
          >
        </div>
        <img class="otter-weight" :src="otterWeight" alt="海獭站在体重秤上" />
        <div class="weight-actions">
          <van-button
            type="primary"
            @click="router.push({ name: 'weight-records' })"
            >记录体重</van-button
          ><van-button
            plain
            @click="router.push({ name: 'calorie-calculator' })"
            >热量计算器</van-button
          >
        </div>
      </section>
      <section class="overview-bottom-grid">
        <button type="button" class="footprint-panel" @click="router.push({ name: 'check-ins' })">
          <header><h2>登录足迹</h2><van-icon name="arrow" /></header>
          <p class="footprint-stats" v-if="monthlyFootprint">本月 {{ monthlyFootprint.checkedDays || 0 }} 天 · 最长连续 {{ monthlyFootprint.longestStreak || 0 }} 天</p>
          <p v-else class="footprint-stats">足迹暂未加载，点击查看</p>
          <div class="month-grid" aria-label="本月登录足迹"><span v-for="day in monthDates" :key="day" :class="{ active: checkedDateSet.has(day) }" :title="day"></span></div>
          <div class="footprint-mascot"><img :src="otterCelebrate" alt="" /></div>
        </button>
        <section class="overview-placeholder report-card"><p>坚持记录<br />遇见更好的自己</p><van-button type="primary" size="small" :loading="generatingReport" :disabled="generatingReport" loading-text="正在生成" @click="generateReport">生成今日报告</van-button><svg class="waves" viewBox="0 0 180 30" preserveAspectRatio="none" aria-hidden="true"><path d="M0 10q22-7 45 0t45 0t45 0t45 0M0 17q22-7 45 0t45 0t45 0t45 0"/></svg></section>
      </section>
    </template>
    <AppBottomNav active="home" theme="otter" />
  </main>
</template>

<style scoped>
.diet-overview-page{--ink:#493526;--muted:#817367;--brand:#9d8bb7;--brand-strong:#79648f;--overage:#c84e53;--line:#e8dfd1;--surface:#fffcf6;--page-bg:#f8f5ed;--soft:#eee7f0;min-height:100dvh;padding:24px 14px calc(100px + env(safe-area-inset-bottom));background:var(--page-bg);color:var(--ink)}
.diet-overview-page>*:not(nav){width:min(100%,620px);margin-left:auto;margin-right:auto}h1,h2,p{margin:0}h2{font-size:16px}
.page-header{position:relative;min-height:84px;margin-bottom:12px;padding-top:15px}.page-header>div{width:60%}.page-header h1{font-size:24px}.page-header p{margin-top:10px;color:var(--muted);font-size:11px}.otter-wave{position:absolute;right:24%;top:-6px;width:84px;height:100px;object-fit:contain}.user-counter{position:absolute;right:0;top:0;max-width:30%;padding:5px 8px;border-radius:14px;background:var(--soft);font-size:10px;color:var(--muted);overflow-wrap:anywhere}.user-counter strong{font-weight:500}
.budget-panel,.weight-panel,.footprint-panel,.overview-placeholder,.metrics>div{border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.budget-panel{padding:14px 13px 18px}.panel-title{font-size:16px;font-weight:700}.budget-number{display:flex;align-items:baseline;gap:9px;margin-top:8px;flex-wrap:wrap}.budget-number strong{font-size:44px;line-height:1.2;overflow-wrap:anywhere}.budget-number strong.over-budget,.metrics strong.over-budget{color:var(--overage)}.budget-number span,.budget-number small,.budget-meta{font-size:11px;color:var(--muted)}.budget-number small{margin-left:auto;padding:6px 10px;border-radius:12px;background:var(--soft);line-height:1.6}.progress-track{display:flex;height:8px;margin-top:16px;overflow:hidden;border-radius:999px;background:var(--soft)}.progress-track i{display:block;flex:none;height:100%;transition:width .2s ease}.progress-track .budget-fill{background:var(--brand)}.progress-track .overage-fill{background:var(--overage)}.budget-meta{display:flex;justify-content:space-between;gap:8px;margin-top:7px;padding-top:6px;border-top:1px solid var(--line)}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-top:12px}.metrics>div{padding:15px 11px 10px;min-width:0}.metrics span,.metrics small{display:block;font-size:11px}.metrics span{font-weight:600}.metrics small{color:var(--muted)}.metrics strong{display:block;margin:9px 0 2px;font-size:27px;line-height:1.2;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}.metrics .orange{color:#ba613b}
.weight-panel{position:relative;padding:13px;margin-top:12px}.weight-panel>header,.footprint-panel header{display:flex;align-items:center;justify-content:space-between;gap:8px}.weight-panel>header>span{font-size:11px;color:var(--muted)}.weight-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:26px;margin-right:86px}.weight-grid span{padding-left:12px;border-left:1px solid var(--line);font-size:10px;color:var(--muted);min-width:0}.weight-grid span:first-child{border:0;padding-left:0}.weight-grid strong{display:block;margin-top:6px;font-size:17px;color:var(--ink);overflow-wrap:anywhere}.otter-weight{position:absolute;right:7px;bottom:18px;width:78px;height:109px;object-fit:contain}.weight-actions{display:flex;gap:8px;margin-top:24px;margin-right:80px;flex-wrap:wrap}.weight-actions :deep(.van-button){min-height:36px;height:auto;padding:8px 11px;border-radius:4px;font-size:11px;font-weight:600}.weight-actions :deep(.van-button--primary){background:var(--brand);border-color:var(--brand);color:#fff}.weight-actions :deep(.van-button--default){background:var(--surface);border-color:var(--line);color:var(--ink)}.weight-actions :deep(.van-button--primary:hover){background:var(--brand-strong)}
.overview-bottom-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:10px;margin-top:12px}.footprint-panel{padding:12px 13px 8px;text-align:left;color:var(--ink);cursor:pointer;min-width:0}.footprint-stats{font-size:10px;color:var(--muted);margin:5px 0 9px;line-height:1.5}.month-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}.month-grid span{aspect-ratio:1;border-radius:2px;background:var(--soft)}.month-grid span.active{background:var(--brand)}.footprint-mascot{display:flex;justify-content:flex-end;height:46px;margin-top:-5px;pointer-events:none}.footprint-mascot img{width:54px;height:59px;object-fit:contain}
.overview-placeholder{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:25px;min-height:180px;overflow:hidden}.overview-placeholder p{font-size:14px;line-height:1.5;transform:rotate(-8deg)}.shell{width:18px;height:18px;fill:none;stroke:var(--brand);stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.waves{position:absolute;bottom:0;width:100%;height:25px;fill:none;stroke:#c6dfd5;stroke-width:1.5}.state{display:flex;min-height:55vh;align-items:center;justify-content:center}.error-state{flex-direction:column;gap:16px;text-align:center}
button:focus-visible{outline:3px solid var(--brand);outline-offset:3px}.footprint-panel:hover{background:var(--soft)}
@media(min-width:600px){.page-header{min-height:100px}.otter-wave{right:23%;width:110px;height:115px}.budget-panel{padding:20px}.metrics>div{padding:18px}.weight-panel{padding:20px}.otter-weight{width:110px;height:135px}.weight-grid,.weight-actions{margin-right:120px}.weight-grid strong{font-size:22px}.overview-bottom-grid{grid-template-columns:1fr 1fr}.footprint-panel{padding:16px}.month-grid span{max-height:28px}.overview-placeholder{min-height:220px}}
@media(max-width:360px){.diet-overview-page{padding-left:10px;padding-right:10px}.page-header h1{font-size:21px}.page-header p{max-width:150px}.otter-wave{right:23%;width:70px}.budget-number strong{font-size:38px}.weight-grid span{padding-left:7px}.weight-grid strong{font-size:15px}.otter-weight{width:62px}.weight-grid,.weight-actions{margin-right:65px}.weight-actions{gap:5px}.weight-actions :deep(.van-button){padding:8px 7px;font-size:10px}.footprint-panel{padding:10px 8px}.footprint-stats{font-size:9px}}
</style>

<style scoped>
.empty-hint { margin-top:10px; color:var(--muted); font-size:11px; }
.report-card { padding: 14px 8px 30px; gap: 18px; }
.report-card :deep(.van-button--primary) { background:var(--brand); border-color:var(--brand); border-radius:6px; }
@media(max-width:360px){.page-header p{max-width:none;font-size:10px;}}
</style>

<style scoped>
</style>
