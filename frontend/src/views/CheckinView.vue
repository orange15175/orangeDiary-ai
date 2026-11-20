<script setup>
import { computed, onMounted, ref } from 'vue'
import { showFailToast } from 'vant'
import AppBackButton from '../components/AppBackButton.vue'
import { getCheckinFootprint, recordTodayCheckin } from '../api/checkin'

const footprint = ref({ checkedDates: [], checkedDays: 0, longestStreak: 0 })
const loading = ref(false)

function today() {
  return new Date().toISOString().slice(0, 10)
}

function dateBefore(days) {
  const result = new Date(`${today()}T00:00:00`)
  result.setDate(result.getDate() - days)
  return result.toISOString().slice(0, 10)
}

// 365 个格子按日期顺序排列，每个格子只对应一天。
const dates = computed(() => Array.from({ length: 365 }, (_, index) => dateBefore(364 - index)))
const checkedDateSet = computed(() => new Set(footprint.value.checkedDates || []))

async function load() {
  loading.value = true
  try {
    // 直接打开详情页时同样先记录当天，再读取 365 天网格。
    await recordTodayCheckin().catch(() => null)
    footprint.value = await getCheckinFootprint(dates.value[0], dates.value.at(-1)) || footprint.value
  } catch (error) {
    showFailToast(error.message || '登录足迹加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="checkin-page">
    <header class="page-header"><AppBackButton fallback="diet" label="返回主页" /><div><p class="eyebrow">LOGIN FOOTPRINT</p><h1>登录足迹</h1><p>最近 365 天足迹</p></div></header>
    <section class="card"><div class="summary"><div><span>近 365 天已签到</span><strong>{{ footprint.checkedDays }} <small>天</small></strong></div><div><span>最长连续</span><strong>{{ footprint.longestStreak }} <small>天</small></strong></div></div><van-loading v-if="loading" class="state" vertical>足迹加载中</van-loading><div v-else class="year-grid" aria-label="最近365天登录足迹"><span v-for="date in dates" :key="date" :class="{ active: checkedDateSet.has(date) }" :title="date"></span></div></section>
  </main>
</template>

<style scoped>
.checkin-page{min-height:100vh;padding:26px 16px 48px;background:#eef8ff;color:#254966}.checkin-page>*{width:min(100%,680px);margin-right:auto;margin-left:auto}.page-header{display:flex;align-items:flex-start;gap:12px}.eyebrow{margin:0;color:#39b8ee;font:11px ui-monospace,monospace}.page-header h1{margin:7px 0;font-size:24px}.page-header p:last-child{margin:6px 0;color:#7593a8;font-size:13px}.card{margin-top:22px;padding:18px;border:1px solid #d7eaf5;border-radius:8px;background:#fff}.summary{display:flex;gap:34px}.summary div{display:grid;gap:4px}.summary span{color:#7593a8;font-size:12px}.summary strong{font-size:24px}.summary small{font-size:12px;font-weight:500}.year-grid{display:grid;grid-template-columns:repeat(20,1fr);gap:4px;margin-top:22px}.year-grid span{aspect-ratio:1;border-radius:3px;background:#e2f0f7}.year-grid span.active{background:#39b8ee}.state{min-height:280px;padding-top:110px;text-align:center}
</style>
