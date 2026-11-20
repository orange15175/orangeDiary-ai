<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { showConfirmDialog, showFailToast, showSuccessToast } from "vant";
import { deleteWeight, getWeightRecords, getWeightTrend } from "../api/health";
import AppBackButton from "../components/AppBackButton.vue";
import weightArt from "../assets/otter/weight.png";
import emptyArt from "../assets/otter/wave.png";

const router = useRouter();
const date = ref(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()));
const dateLabel = computed(() => date.value.replaceAll("-", "/"));
const recordsError = ref("");
const trendError = ref("");
let recordsRequest = 0;
let trendRequest = 0;
const rangeDays = ref(30);
const rows = ref([]);
const trendRows = ref([]);
const loading = ref(false);
const trendLoading = ref(false);
const ranges = [7, 30, 90];
const chartElement = ref(null);
const chartWidth = ref(340);
const chartHeight = computed(() => chartWidth.value > 600 ? 300 : 205);
const chartArea = computed(() => ({ left: 44, top: 20, width: Math.max(100, chartWidth.value - 66), height: chartHeight.value - 58 }));
const chartObserver = new ResizeObserver(([entry]) => { chartWidth.value = entry.contentRect.width; });
watch(chartElement, (element) => { chartObserver.disconnect(); if (element) chartObserver.observe(element); });
onUnmounted(() => chartObserver.disconnect());

// 根据顶部选中的结束日期计算趋势查询的起始日期。
function dateBefore(baseDate, days) {
  const result = new Date(`${baseDate}T00:00:00Z`);
  result.setUTCDate(result.getUTCDate() - days);
  return result.toISOString().slice(0, 10);
}

function displayWeight(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : "--";
}

function displayDate(value) {
  return String(value || "")
    .slice(5)
    .replace("-", "/");
}

// 纵轴上下预留边距，避免最高或最低点贴住图表边缘。
const chartMetrics = computed(() => {
  const weights = trendRows.value
    .map((item) => Number(item.weightKg))
    .filter(Number.isFinite);
  if (!weights.length) return null;

  const lowest = Math.min(...weights);
  const highest = Math.max(...weights);
  const padding = Math.max(0.5, (highest - lowest) * 0.25);
  return {
    min: Math.floor((lowest - padding) * 2) / 2,
    max: Math.ceil((highest + padding) * 2) / 2,
  };
});

// Match the SVG coordinates to its rendered size so labels and dots stay crisp.
const chartPoints = computed(() => {
  if (!chartMetrics.value) return [];

  const { min, max } = chartMetrics.value;
  const chart = chartArea.value;
  return trendRows.value.map((item, index, items) => ({
    ...item,
    x:
      chart.left +
      (items.length === 1
        ? chart.width / 2
        : (index * chart.width) / (items.length - 1)),
    y:
      chart.top +
      ((max - Number(item.weightKg)) * chart.height) / (max - min || 1),
  }));
});

const chartPath = computed(() =>
  chartPoints.value
    .map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`)
    .join(" "),
);
const latestTrend = computed(() => trendRows.value.at(-1) || null);
const trendDelta = computed(() =>
  trendRows.value.length < 2
    ? null
    : Number(trendRows.value.at(-1).weightKg) -
      Number(trendRows.value[0].weightKg),
);

// 只显示起点、中点、终点日期，保证 90 天时手机端仍可读。
const axisLabels = computed(() => {
  const points = chartPoints.value;
  if (points.length < 2) return points;

  const candidates = [
    points[0],
    points[Math.floor((points.length - 1) / 2)],
    points.at(-1),
  ];
  return Array.from(
    new Map(candidates.map((point) => [point.recordDate, point])).values(),
  );
});

async function loadRecords() {
  const request = ++recordsRequest;
  recordsError.value = "";
  rows.value = [];
  loading.value = true;
  try {
    const result = await getWeightRecords(date.value);
    if (request === recordsRequest) rows.value = result || [];
  } catch (error) {
    if (request === recordsRequest) recordsError.value = error.message || "体重记录加载失败";
  } finally {
    if (request === recordsRequest) loading.value = false;
  }
}

async function loadTrend() {
  const request = ++trendRequest;
  trendError.value = "";
  trendRows.value = [];
  trendLoading.value = true;
  try {
    // 后端会将同一天的多次测量折叠为最后一次。
    const result =
      (await getWeightTrend(
        dateBefore(date.value, rangeDays.value - 1),
        date.value,
      )) || [];
    if (request === trendRequest) trendRows.value = result;
  } catch (error) {
    if (request === trendRequest) trendError.value = error.message || "体重趋势加载失败";
  } finally {
    if (request === trendRequest) trendLoading.value = false;
  }
}

function goToAddWeight() {
  router.push({ name: "weight-add", query: { date: date.value } });
}

async function remove(row) {
  try {
    await showConfirmDialog({
      title: "删除体重记录",
      message: `确定删除 ${row.weightKg} kg 记录吗？`,
    });
    await deleteWeight(row.id);
    await Promise.all([loadRecords(), loadTrend()]);
    showSuccessToast("已删除");
  } catch (error) {
    if (error?.message !== "cancel" && error !== "cancel") showFailToast(error.message || "删除失败");
  }
}

// 切换顶部日期时，同时刷新当天列表和以该日为终点的趋势图。
watch(date, (value, previous) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) { date.value = previous; return; }
  Promise.all([loadRecords(), loadTrend()]);
});
watch(rangeDays, loadTrend);
onMounted(() => Promise.all([loadRecords(), loadTrend()]));
</script>

<template>
  <main class="weight-page otter-page">
    <header class="page-header">
      <AppBackButton fallback="home" label="返回主页" />
      <div class="heading-copy">
        <h1>体重记录</h1>
        <p>每一点变化，都值得被温柔记录</p>
      </div>
      <img class="header-art" :src="weightArt" alt="" />
      <label class="date-picker">
        <span>{{ dateLabel }}</span><van-icon name="calendar-o" aria-hidden="true" />
        <input v-model="date" type="date" required aria-label="选择记录日期" />
      </label>
    </header>

    <section class="card trend-card" aria-label="体重趋势">
      <header class="trend-header">
        <div>
          <h2>体重趋势</h2>
          <p>按每天最后一次记录计算</p>
        </div>
        <van-button class="record-button" type="primary" size="small" @click="goToAddWeight"
          >记录体重</van-button
        >
      </header>

      <div class="range-tabs" role="group" aria-label="选择趋势时间范围">
        <button
          v-for="days in ranges"
          :key="days"
          type="button"
          :class="{ active: rangeDays === days }"
          :aria-pressed="rangeDays === days"
          @click="rangeDays = days"
        >
          {{ days }}天
        </button>
      </div>

      <van-loading v-if="trendLoading" class="trend-state" vertical
        >趋势加载中</van-loading
      >
      <div v-else-if="trendError" class="trend-empty" role="alert">
        <strong>{{ trendError }}</strong>
        <button type="button" @click="loadTrend">重新加载</button>
      </div>
      <div v-else-if="!trendRows.length" class="trend-empty">
        <strong>还没有体重趋势</strong>
        <span>记录第一条体重数据后显示趋势</span>
        <button type="button" @click="goToAddWeight">记录体重</button>
      </div>

      <template v-else>
        <div class="trend-summary">
          <div>
            <span>最新体重</span>
            <strong
              >{{ displayWeight(latestTrend?.weightKg) }}
              <small>kg</small></strong
            >
          </div>
          <div v-if="trendDelta !== null">
            <span>区间变化</span>
            <strong :class="{ down: trendDelta < 0, up: trendDelta > 0 }">
              {{ trendDelta > 0 ? "+" : "" }}{{ displayWeight(trendDelta) }}
              <small>kg</small>
            </strong>
          </div>
        </div>

        <svg
          class="trend-chart"
          ref="chartElement"
          :viewBox="`0 0 ${chartWidth} ${chartHeight}`"
          :style="{ height: `${chartHeight}px` }"
          role="img"
          :aria-label="`近 ${rangeDays} 天体重趋势，共 ${trendRows.length} 条记录`"
        >
          <g class="grid">
            <line class="vertical-axis" :x1="chartArea.left" :x2="chartArea.left" y1="16" :y2="chartArea.top + chartArea.height" />
            <line v-for="fraction in [0, .5, 1]" :key="fraction" :x1="chartArea.left" :x2="chartArea.left + chartArea.width" :y1="chartArea.top + chartArea.height * fraction" :y2="chartArea.top + chartArea.height * fraction" />
          </g>
          <g class="axis-text">
            <text x="2" y="24">{{ chartMetrics?.max.toFixed(1) }}</text>
            <text x="2" :y="chartArea.top + chartArea.height / 2 + 4">
              {{ ((chartMetrics?.max + chartMetrics?.min) / 2).toFixed(1) }}
            </text>
            <text x="2" :y="chartArea.top + chartArea.height + 4">{{ chartMetrics?.min.toFixed(1) }}</text>
          </g>
          <path class="trend-line" :d="chartPath" />
          <circle
            v-for="point in chartPoints"
            :key="point.recordDate"
            class="trend-dot"
            :cx="point.x"
            :cy="point.y"
            r="3.6"
          >
            <title>
              {{ point.recordDate }}：{{ displayWeight(point.weightKg) }} kg
            </title>
          </circle>
          <g class="date-labels">
            <text
              v-for="point in axisLabels"
              :key="point.recordDate"
              :x="point.x"
              :y="chartHeight - 12"
              text-anchor="middle"
            >
              {{ displayDate(point.recordDate) }}
            </text>
          </g>
        </svg>
      </template>
      <p class="trend-note"><van-icon name="flower-o" aria-hidden="true" />记录更多天数，慢慢看见自己的变化</p>
    </section>

    <section class="card history">
      <header>
        <h2>当天记录</h2>
        <span>{{ rows.length }} 条</span>
      </header>
      <van-loading v-if="loading" class="state" vertical>加载中</van-loading>
      <div v-else-if="recordsError" class="history-empty" role="alert">
        <strong>{{ recordsError }}</strong>
        <button class="empty-action" type="button" @click="loadRecords">重新加载</button>
      </div>
      <div v-else-if="!rows.length" class="history-empty">
        <img :src="emptyArt" alt="抱着本子的水獭" />
        <h3>{{ date === new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date()) ? '今天' : '当天' }}还没有体重记录</h3>
        <p>记录一下，陪你积累每一天的变化</p>
        <button class="empty-action" type="button" @click="goToAddWeight">记录体重</button>
      </div>
      <ul v-else>
        <li v-for="row in rows" :key="row.id">
          <div>
            <strong>{{ row.weightKg }} kg</strong>
            <span>
              {{
                String(row.recordTime || "")
                  .replace("T", " ")
                  .slice(0, 16)
              }}
              <template v-if="row.bodyFatRate">
                · 体脂 {{ row.bodyFatRate }}%</template
              >
            </span>
          </div>
          <div>
            <em>BMI {{ row.bmi }}</em>
            <button type="button" @click="remove(row)">删除</button>
          </div>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.weight-page {
  min-height: 100vh;
  padding: 26px 16px 48px;
  background: #eef8ff;
  color: #254966;
}
.weight-page > * {
  width: min(100%, 680px);
  margin-right: auto;
  margin-left: auto;
}
.page-header {
  display: grid;
  grid-template-columns: 40px 1fr auto;
  align-items: start;
  gap: 10px;
}
.history header,
.trend-header {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 12px;
}
.eyebrow {
  margin: 0;
  color: #39b8ee;
  font:
    11px ui-monospace,
    monospace;
}
.weight-page h1 {
  margin: 7px 0;
  font-size: 24px;
}
.page-header p:last-child,
.state,
.trend-header p,
.trend-empty span {
  color: #7593a8;
  font-size: 13px;
}
.page-header > input {
  min-height: 34px;
  border: 1px solid #d7eaf5;
  border-radius: 6px;
  background: #fff;
  color: #254966;
}
.card {
  margin-top: 16px;
  padding: 18px;
  border: 1px solid #d7eaf5;
  border-radius: 8px;
  background: #fff;
}
.trend-header h2,
.history h2 {
  margin: 0;
  font-size: 16px;
}
.trend-header p {
  margin: 5px 0 0;
}
.trend-header :deep(.van-button) {
  border-radius: 6px;
}
.range-tabs {
  display: flex;
  gap: 7px;
  margin-top: 18px;
}
.range-tabs button {
  min-width: 54px;
  height: 30px;
  border: 1px solid #d7eaf5;
  border-radius: 15px;
  background: #f8fcff;
  color: #7593a8;
  font-size: 12px;
}
.range-tabs button.active {
  border-color: #39b8ee;
  background: #e6f7fe;
  color: #208dbc;
  font-weight: 700;
}
.trend-summary {
  display: flex;
  gap: 28px;
  margin: 18px 0 5px;
}
.trend-summary div {
  display: grid;
  gap: 3px;
}
.trend-summary span {
  color: #7593a8;
  font-size: 11px;
}
.trend-summary strong {
  font-size: 21px;
  color: #254966;
}
.trend-summary strong.down {
  color: #28a484;
}
.trend-summary strong.up {
  color: #df7966;
}
.trend-summary small {
  font-size: 11px;
  font-weight: 500;
}
.trend-chart {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}
.grid line {
  stroke: #e6f2f8;
  stroke-dasharray: 3 3;
}
.axis-text,
.date-labels {
  fill: #86a2b5;
  font-size: 9px;
}
.trend-line {
  fill: none;
  stroke: #39b8ee;
  stroke-width: 2.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.trend-dot {
  fill: #fff;
  stroke: #39b8ee;
  stroke-width: 2;
}
.trend-state,
.trend-empty {
  min-height: 180px;
  padding-top: 60px;
  text-align: center;
}
.trend-empty {
  display: grid;
  justify-items: center;
  gap: 8px;
}
.trend-empty strong {
  font-size: 14px;
}
.trend-empty button {
  margin-top: 5px;
  border: 0;
  background: transparent;
  color: #208dbc;
  font-size: 13px;
}
.history header span {
  color: #7593a8;
  font-size: 12px;
}
.state {
  min-height: 120px;
  padding-top: 45px;
  text-align: center;
}
.history ul {
  padding: 0;
  margin: 12px 0 0;
  list-style: none;
}
.history li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 67px;
  border-bottom: 1px solid #e6f2f8;
}
.history strong,
.history span {
  display: block;
}
.history strong {
  font-size: 16px;
}
.history span {
  margin-top: 4px;
  color: #7593a8;
  font-size: 11px;
}
.history em {
  color: #208dbc;
  font-size: 12px;
  font-style: normal;
}
.history button {
  display: block;
  margin: 5px 0 0 auto;
  border: 0;
  background: transparent;
  color: #c86658;
  font-size: 11px;
}

.weight-page { --ink: #493526; --muted: #8b7d70; --brand: #9d8bb7; --brand-strong: #79648f; --line: #e8dfd1; --surface: #fffdf8; min-height: 100vh; padding: 38px clamp(18px, 4vw, 48px) 72px; background: #f8f5ed; color: var(--ink); }
.weight-page > * { width: min(100%, 1064px); margin-inline: auto; }
.page-header { position: relative; min-height: 180px; display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: start; gap: 18px; }
.heading-copy { padding-top: 4px; }
.weight-page h1 { margin: 0; font-size: clamp(30px, 3vw, 42px); line-height: 1.15; }
.heading-copy p { margin: 10px 0 0; color: var(--muted); font-size: 15px; }
.header-art { position: absolute; top: -24px; right: 120px; width: clamp(150px, 23vw, 250px); height: 170px; object-fit: contain; pointer-events: none; }
.date-picker { position: relative; z-index: 1; display: inline-flex; align-items: center; gap: 8px; min-height: 42px; padding: 0 13px; border: 1px solid var(--line); border-radius: 22px; background: rgba(255,253,248,.88); color: var(--muted); font-size: 13px; white-space: nowrap; }
.date-picker input { position: absolute; inset: 0; width: 100%; opacity: 0; cursor: pointer; }
.date-picker :deep(.van-icon) { color: var(--brand-strong); font-size: 17px; }
.card { margin-top: 20px; padding: clamp(20px, 3vw, 32px); border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: 0 10px 30px rgba(104,81,54,.045); }
.history header, .trend-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.trend-header h2, .history h2 { margin: 0; font-size: 22px; line-height: 1.25; }
.trend-header p { margin: 7px 0 0; color: var(--muted); font-size: 13px; }
.record-button { --van-button-primary-background: var(--brand); --van-button-primary-border-color: var(--brand); --van-button-radius: 999px; min-width: 106px; box-shadow: none; }
.range-tabs { display: inline-flex; gap: 4px; margin-top: 24px; padding: 4px; border-radius: 999px; background: #f4f0e9; }
.range-tabs button { min-width: 58px; height: 31px; border: 0; border-radius: 999px; background: transparent; color: var(--muted); font-size: 12px; }
.range-tabs button.active { background: var(--surface); color: var(--brand-strong); font-weight: 700; box-shadow: 0 2px 8px rgba(83,62,45,.08); }
.trend-summary { display: flex; gap: 40px; margin: 22px 0 4px; }
.trend-summary div { display: grid; gap: 5px; }
.trend-summary span { color: var(--muted); font-size: 12px; }
.trend-summary strong { color: var(--ink); font-size: 28px; }
.trend-summary strong.down { color: #6d9a7f; } .trend-summary strong.up { color: #bd796b; } .trend-summary small { font-size: 13px; font-weight: 500; }
.trend-chart { display: block; width: 100%; height: 270px; margin-top: 8px; overflow: visible; }
.grid line { stroke: #eee8de; stroke-dasharray: 3 4; } .grid .vertical-axis { stroke: #e5dbce; stroke-dasharray: none; } .axis-text, .date-labels { fill: #a99a8b; font-size: 9px; } .trend-line { stroke: var(--brand); } .trend-dot { fill: var(--surface); stroke: var(--brand); }
.trend-state, .trend-empty { min-height: 220px; padding-top: 76px; text-align: center; color: var(--muted); } .trend-empty { display: grid; justify-items: center; gap: 8px; } .trend-empty strong { color: var(--ink); font-size: 15px; }
.trend-empty button, .empty-action { border: 1px solid var(--brand); border-radius: 999px; padding: 8px 16px; background: transparent; color: var(--brand-strong); font-size: 13px; }
.trend-note { display: flex; align-items: center; justify-content: center; gap: 7px; margin: 4px 0 0; color: var(--muted); font-size: 12px; } .trend-note :deep(.van-icon) { color: #91ad95; font-size: 16px; }
.history { min-height: 360px; } .history header span { color: var(--muted); font-size: 13px; } .state { min-height: 180px; padding-top: 72px; color: var(--muted); text-align: center; }
.history-empty { min-height: 290px; display: grid; justify-items: center; align-content: center; gap: 9px; text-align: center; } .history-empty img { width: min(210px, 56vw); height: 135px; object-fit: contain; } .history-empty h3 { margin: 0; font-size: 18px; } .history-empty p { margin: 0 0 9px; color: var(--muted); font-size: 13px; }
.history ul { padding: 0; margin: 18px 0 0; list-style: none; } .history li { display: flex; align-items: center; justify-content: space-between; min-height: 72px; border-bottom: 1px solid #f0e9df; } .history li:last-child { border-bottom: 0; } .history strong, .history span { display: block; } .history strong { font-size: 18px; } .history span { margin-top: 5px; color: var(--muted); font-size: 12px; } .history em { color: var(--brand-strong); font-size: 12px; font-style: normal; } .history li button { display: block; margin: 6px 0 0 auto; border: 0; background: transparent; color: #b97b6d; font-size: 11px; }
@media (max-width: 680px) { .weight-page { padding: 24px 16px 48px; } .page-header { min-height: 154px; grid-template-columns: 38px minmax(0, 1fr); gap: 12px; } .heading-copy p { margin-top: 7px; font-size: 13px; } .header-art { top: 4px; right: 8px; width: 118px; height: 105px; opacity: .92; } .date-picker { position: absolute; top: 112px; left: 50px; min-height: 36px; padding-inline: 11px; } .card { margin-top: 16px; padding: 20px 16px; border-radius: 15px; } .trend-header h2, .history h2 { font-size: 19px; } .record-button { min-width: 92px; } .trend-summary strong { font-size: 24px; } .trend-chart { height: 205px; } .trend-note { font-size: 11px; } }
@media (max-width: 360px) { .header-art { right: -4px; width: 100px; } .date-picker { left: 48px; } .trend-header { align-items: stretch; flex-direction: column; } .record-button { align-self: flex-start; } }
</style>

