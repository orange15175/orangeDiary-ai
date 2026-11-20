<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import AppBackButton from "../components/AppBackButton.vue";
import { getCurrentUser } from "../api/auth";

const router = useRouter();
const form = ref({
  gender: "male",
  age: 30,
  heightCm: "",
  weightKg: "",
  activityLevel: "medium",
  trainingMinutes: 60,
  trainingDays: 3,
  trainingLevel: "regular",
  goalType: "maintain",
});
const levels = { beginner: 5, regular: 8, advanced: 10 };
const factors = { low: 1.2, medium: 1.325, high: 1.45 };
const adjustments = { lose: -400, maintain: 0, gain: 225 };
const n = (value) => (Number(value) > 0 ? Number(value) : 0);
const goalCopy = {
  lose: "减脂建议在日常消耗基础上减少约 400 千卡。",
  maintain: "维持建议与日常消耗保持基本一致。",
  gain: "增肌建议在日常消耗基础上增加约 225 千卡。",
};
const trainingOptions = [
  { value: "beginner", label: "新手", hint: "适合较少经验的训练，系数为 5" },
  { value: "regular", label: "健身爱好者", hint: "适合有一定经验的训练，系数为 8" },
  { value: "advanced", label: "健身老手", hint: "适合多年训练经验，系数为 10" },
];

const bmr = computed(() => {
  const value = form.value;
  if (!n(value.heightCm) || !n(value.weightKg)) return 0;
  return Math.round(
    10 * n(value.weightKg) +
      6.25 * n(value.heightCm) -
      5 * n(value.age) +
      (value.gender === "female" ? -161 : 5),
  );
});
const training = computed(() =>
  Math.round(
    Math.max(
      0,
      ((((levels[form.value.trainingLevel] - 1) * 3.5 * n(form.value.weightKg)) / 200) *
        Math.min(300, n(form.value.trainingMinutes)) *
        Math.min(7, n(form.value.trainingDays))) /
        7,
    ),
  ),
);
const daily = computed(() => Math.round(bmr.value * (factors[form.value.activityLevel] || 1.325)));
const total = computed(() => daily.value + training.value);
const suggested = computed(() => Math.max(0, total.value + (adjustments[form.value.goalType] || 0)));

function age(birthday) {
  if (!birthday) return 30;
  const date = new Date(birthday);
  const now = new Date();
  return Math.max(
    15,
    now.getFullYear() -
      date.getFullYear() -
      (now < new Date(now.getFullYear(), date.getMonth(), date.getDate()) ? 1 : 0),
  );
}

onMounted(async () => {
  try {
    const user = await getCurrentUser();
    const profile = user.profile || {};
    form.value = {
      ...form.value,
      gender: profile.gender || "male",
      age: age(profile.birthday),
      heightCm: profile.heightCm || "",
      weightKg: profile.currentWeightKg || "",
      activityLevel: profile.activityLevel || "medium",
      goalType: user.activeGoal?.goalType || "maintain",
    };
  } catch {
    // The calculator remains usable with its local defaults when profile data is unavailable.
  }
});
</script>

<template>
  <main class="calculator-page">
    <header class="topbar">
      <AppBackButton fallback="home" label="返回主页" />
      <div class="topbar-copy">
        <h1>热量计算器</h1>
      </div>
      <img class="topbar-mark" src="../assets/otter/floating.png" alt="" />
    </header>

    <section class="energy-hero" aria-labelledby="energy-title">
      <div class="hero-copy">
        <span class="eyebrow">OTTER WELLNESS</span>
        <h2 id="energy-title">把每天的能量，<br /><em>算得刚刚好。</em></h2>
        <p>根据你的身体数据和生活节奏，找到更适合自己的每日热量范围。</p>
      </div>
      <img class="hero-otter" src="../assets/otter/floating.png" alt="漂浮的海獭" />
    </section>

    <section class="recommendation" aria-label="建议每日摄入" aria-live="polite">
      <div class="hero-result">
        <span>建议每日摄入</span>
        <strong>{{ suggested.toLocaleString() }}</strong>
        <small>千卡 / 天</small>
      </div>
      <div class="goal-summary"><span>为你的目标定制</span><strong>{{ { gain: '增肌目标', maintain: '维持体重', lose: '减脂目标' }[form.goalType] }}</strong></div>
    </section>

    <section class="summary-strip" aria-label="热量概览">
      <div><span>基础代谢</span><strong>{{ bmr }}</strong><small>kcal</small></div>
      <div><span>日常消耗</span><strong>{{ daily }}</strong><small>kcal</small></div>
      <div><span>训练消耗</span><strong>{{ training }}</strong><small>kcal</small></div>
      <div class="summary-total"><span>每日总消耗</span><strong>{{ total }}</strong><small>kcal</small></div>
    </section>

    <section class="calculator-section">
      <div class="section-heading"><span>01</span><div><h2>身体基础</h2><p>先告诉我们一些关于你的信息</p></div></div>
      <div class="section-content">
        <div class="gender-field"><span class="field-caption" id="gender-label">生理性别</span><div class="gender-row" role="group" aria-labelledby="gender-label">
          <button v-for="item in [{ value: 'female', label: '女性' }, { value: 'male', label: '男性' }]" :key="item.value" type="button" :aria-pressed="form.gender === item.value" :class="{ active: form.gender === item.value }" @click="form.gender = item.value"><span class="selection-dot" aria-hidden="true"></span><span>{{ item.label }}</span><b>已选择</b></button>
        </div>
        </div>
        <div class="basic-fields">
          <label><span>年龄</span><div class="input-wrap"><input v-model.number="form.age" type="number" min="15" max="120" inputmode="numeric" /><em>岁</em></div></label>
          <label><span>身高</span><div class="input-wrap"><input v-model.number="form.heightCm" type="number" min="1" inputmode="decimal" placeholder="—" /><em>厘米</em></div></label>
          <label><span>体重</span><div class="input-wrap"><input v-model.number="form.weightKg" type="number" min="1" inputmode="decimal" placeholder="—" /><em>公斤</em></div></label>
        </div>
        <div class="inline-result"><span>你的基础代谢</span><strong>{{ bmr }} <small>千卡 / 天</small></strong></div>
      </div>
    </section>

    <section class="calculator-section">
      <div class="section-heading"><span>02</span><div><h2>活动与训练</h2><p>估算你每天真正需要的能量</p></div></div>
      <div class="section-content">
        <label class="field-row"><span>日常活动水平</span><select v-model="form.activityLevel"><option value="low">低活动 · 久坐为主</option><option value="medium">中等活动 · 日常走动</option><option value="high">高活动 · 经常运动</option></select></label>
        <div class="daily-energy"><div><span>日常基础消耗</span><small>不含额外训练</small></div><strong>{{ daily }} <small>千卡</small></strong></div>
        <div class="training-inputs">
          <label><span>每次训练</span><div class="input-wrap"><input v-model.number="form.trainingMinutes" type="number" min="0" max="300" inputmode="numeric" /><em>分钟</em></div></label>
          <label><span>每周训练</span><div class="input-wrap"><input v-model.number="form.trainingDays" type="number" min="0" max="7" inputmode="numeric" /><em>天</em></div></label>
        </div>
        <div class="training-levels"><span class="field-caption" id="training-label">训练经验</span><div class="level-grid" role="group" aria-labelledby="training-label"><button v-for="item in trainingOptions" :key="item.value" type="button" :aria-pressed="form.trainingLevel === item.value" :aria-label="item.label + '，' + item.hint" :class="{ active: form.trainingLevel === item.value }" @click="form.trainingLevel = item.value"><strong>{{ item.label }}</strong><small>系数 {{ levels[item.value] }}</small></button></div></div>
        <div class="inline-result training-result"><span>日均训练消耗（预估）</span><strong>{{ training }} <small>千卡</small></strong></div>
      </div>
    </section>

    <section class="calculator-section goal-section">
      <div class="section-heading"><span>03</span><div><h2>你的目标</h2><p>选择一个你想坚持的方向</p></div></div>
      <div class="section-content">
        <div class="goal-tabs" role="group" aria-label="健康目标"><button v-for="item in [{ value: 'gain', label: '增肌' }, { value: 'maintain', label: '维持' }, { value: 'lose', label: '减脂' }]" :key="item.value" type="button" :aria-pressed="form.goalType === item.value" :class="{ active: form.goalType === item.value }" @click="form.goalType = item.value">{{ item.label }}</button></div>
        <div class="suggestion"><div><span>适合你的建议摄入</span><p>{{ goalCopy[form.goalType] }}</p></div><strong>{{ suggested }} <small>千卡 / 天</small></strong></div>
      </div>
    </section>

    <van-button class="finish-button" block type="primary" @click="router.push({ name: 'diet' })">完成，返回今日饮食 <span aria-hidden="true">→</span></van-button>
  </main>
</template>

<style scoped>
.calculator-page { --warm-white: #fffcf6; --ink: #493526; --brand-strong: #79648f; --brand-soft: #eee7f0; --seafoam: #d9eae3; min-height: 100dvh; color: var(--ink); }
.calculator-page > * { width: min(100%, 760px); margin-inline: auto; }
.topbar { display: grid; grid-template-columns: 44px 1fr 44px; align-items: center; min-height: 78px; }
.topbar-copy { text-align: center; }.topbar-copy h1 { margin: 3px 0 0; font-size: 18px; letter-spacing: .01em; }.eyebrow { color: var(--brand-strong); font-size: 10px; font-weight: 800; letter-spacing: .14em; }.topbar-dot { width: 28px; height: 28px; margin-left: auto; border: 1px solid var(--line); border-radius: 50%; background: var(--brand-soft); }
.energy-hero { position: relative; display: grid; grid-template-columns: 1fr auto; gap: 22px; min-height: 222px; overflow: hidden; padding: 30px 34px; border: 1px solid #dacbdd; border-radius: 18px; background: linear-gradient(120deg, #eee7f0 0%, #f6efe7 100%); }
.hero-copy { position: relative; z-index: 1; max-width: 420px; }.hero-copy h2 { margin: 10px 0 10px; font-size: clamp(26px, 4vw, 38px); line-height: 1.16; letter-spacing: 0; }.hero-copy h2 em { color: var(--brand-strong); font-style: normal; }.hero-copy p { max-width: 350px; margin: 0; color: #796c62; font-size: 13px; line-height: 1.65; }.hero-result { position: relative; z-index: 1; align-self: center; min-width: 180px; padding: 16px 0 16px 24px; border-left: 1px solid rgb(121 100 143 / 24%); }.hero-result span,.hero-result small { display: block; color: var(--muted); font-size: 12px; }.hero-result strong { display: block; margin: 7px 0 2px; color: var(--brand-strong); font-size: 42px; line-height: 1; }.hero-otter { position: absolute; right: 20px; bottom: -12px; width: 120px; opacity: .22; transform: rotate(-7deg); pointer-events: none; }
.summary-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 14px 0 30px; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--line); }.summary-strip > div { min-width: 0; padding: 15px 14px; background: var(--warm-white); }.summary-strip span,.summary-strip small { display: block; color: var(--muted); font-size: 11px; }.summary-strip strong { display: inline-block; margin: 6px 4px 0 0; font-size: 22px; }.summary-total { background: var(--seafoam) !important; }.summary-total strong { color: #527e71; }
.calculator-section { margin-top: 14px; border: 1px solid var(--line); border-radius: 14px; background: var(--warm-white); box-shadow: 0 5px 18px rgb(73 53 38 / 4%); }.section-heading { display: flex; align-items: flex-start; gap: 13px; padding: 23px 24px 19px; border-bottom: 1px solid var(--line); }.section-heading > span { display: grid; place-items: center; width: 32px; height: 32px; flex: none; border-radius: 9px; background: var(--brand-soft); color: var(--brand-strong); font-size: 11px; font-weight: 800; }.section-heading h2 { margin: 1px 0 3px; font-size: 19px; }.section-heading p { margin: 0; color: var(--muted); font-size: 12px; }.section-content { padding: 22px 24px 25px; }
.gender-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }.gender-row button { display: flex; align-items: center; gap: 10px; min-height: 56px; border: 1px solid var(--line); border-radius: 10px; padding: 0 15px; background: #faf7f1; color: var(--ink); text-align: left; cursor: pointer; }.gender-row button.active { border-color: var(--brand); background: var(--brand-soft); color: var(--brand-strong); }.gender-row i { font-size: 22px; font-style: normal; }.gender-row b { margin-left: auto; font-size: 10px; font-weight: 600; }.basic-fields,.training-inputs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 18px; }.basic-fields label,.training-inputs label { display: grid; gap: 7px; color: var(--muted); font-size: 12px; }.input-wrap { display: flex; align-items: center; min-width: 0; height: 46px; border: 1px solid var(--line); border-radius: 9px; background: #fff; }.input-wrap input { min-width: 0; width: 100%; height: 100%; border: 0; padding: 0 8px 0 12px; background: transparent; color: var(--ink); font-size: 18px; outline: 0; }.input-wrap input:focus { box-shadow: none; }.input-wrap em { padding-right: 11px; color: var(--muted); font-size: 11px; font-style: normal; white-space: nowrap; }.inline-result { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-top: 20px; padding-top: 18px; border-top: 1px solid var(--line); }.inline-result > span { color: var(--muted); font-size: 13px; }.inline-result strong { color: var(--brand-strong); font-size: 26px; }.inline-result small,.suggestion small { color: var(--muted); font-size: 11px; font-weight: 500; }
.field-row { display: grid; grid-template-columns: 1fr minmax(230px, 300px); align-items: center; gap: 20px; color: var(--muted); font-size: 13px; }.field-row select { width: 100%; height: 46px; border: 1px solid var(--line); border-radius: 9px; padding: 0 12px; background: #fff; color: var(--ink); outline: 0; }.daily-energy { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 17px; padding: 16px 18px; border-left: 4px solid #8db8a9; border-radius: 0 9px 9px 0; background: var(--seafoam); }.daily-energy span,.daily-energy small { display: block; }.daily-energy span { color: #527e71; font-size: 13px; font-weight: 700; }.daily-energy small { margin-top: 3px; color: #6f978a; font-size: 11px; }.daily-energy strong { color: #527e71; font-size: 25px; white-space: nowrap; }.daily-energy strong small { display: inline; }.training-inputs { grid-template-columns: repeat(2, minmax(0, 1fr)); }.training-levels { margin-top: 21px; }.field-caption { display: block; margin-bottom: 10px; color: var(--muted); font-size: 12px; }.level-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }.level-grid button { min-height: 76px; border: 1px solid var(--line); border-radius: 9px; padding: 10px; background: #faf7f1; color: var(--ink); text-align: left; cursor: pointer; }.level-grid button.active { border-color: var(--brand); background: var(--brand); color: #fff; }.level-grid strong,.level-grid small { display: block; }.level-grid strong { font-size: 14px; }.level-grid small { margin-top: 7px; color: var(--muted); font-size: 10px; line-height: 1.35; }.level-grid button.active small { color: rgb(255 255 255 / 78%); }.training-result { margin-top: 20px; }
.goal-tabs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; padding: 4px; border-radius: 10px; background: #f4efe7; }.goal-tabs button { min-height: 40px; border: 0; border-radius: 7px; background: transparent; color: var(--muted); font-size: 13px; cursor: pointer; }.goal-tabs button.active { background: var(--warm-white); color: var(--brand-strong); box-shadow: 0 2px 8px rgb(73 53 38 / 8%); font-weight: 700; }.suggestion { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 17px; padding: 19px; border-left: 4px solid var(--brand); border-radius: 0 10px 10px 0; background: var(--brand-soft); }.suggestion span { font-size: 14px; font-weight: 700; }.suggestion p { max-width: 380px; margin: 6px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; }.suggestion strong { color: var(--brand-strong); font-size: 28px; white-space: nowrap; }.finish-button { max-width: 760px; height: 50px; margin-top: 18px; border: 1px solid var(--brand) !important; border-radius: 10px !important; background: var(--brand) !important; font-weight: 700; }.finish-button span { margin-left: 10px; font-size: 18px; vertical-align: -1px; }.finish-button:hover { background: var(--brand-strong) !important; }
button:focus-visible, select:focus-visible, input:focus-visible { outline: 3px solid rgb(157 139 183 / 34%); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { .calculator-page * { transition-duration: .01ms !important; } }

.calculator-page { --muted: #75695f; --brand: #88729f; --line: #ddd5ca; padding: 0 24px 48px; background: #f5f5f2; letter-spacing: 0; }
.calculator-page > * { max-width: 860px; width: 100%; box-sizing: border-box; margin-inline: auto; }
.topbar { min-height: 88px; }
.topbar-copy h1 { margin: 0; font-size: 22px; letter-spacing: 0; }
.topbar-mark { width: 38px; height: 38px; object-fit: contain; }
.eyebrow { font-size: 11px; letter-spacing: 0; }
.energy-hero { grid-template-columns: minmax(0, 1fr) 230px; align-items: center; min-height: 238px; padding: 28px 32px; gap: 20px; border-radius: 8px; background: #eee8f1; }
.hero-copy { max-width: none; }
.hero-copy h2 { font-size: 32px; line-height: 1.45; margin: 12px 0; }
.hero-copy p { max-width: 410px; color: var(--muted); line-height: 1.8; }
.hero-otter { position: static; width: 100%; height: 196px; object-fit: contain; opacity: 1; transform: none; }
.recommendation { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 18px; padding: 20px 28px; border: 1px solid var(--line); border-left: 4px solid var(--brand); border-radius: 8px; background: #fff; }
.hero-result { display: grid; grid-template-columns: auto auto; align-items: baseline; justify-content: start; gap: 5px 12px; min-width: 0; padding: 0; border: 0; }
.hero-result > span { grid-column: 1 / -1; font-size: 12px; }
.hero-result strong { font-size: 38px; margin: 0; line-height: 1.25; font-variant-numeric: tabular-nums; }
.hero-result small { font-size: 12px; }
.goal-summary { text-align: right; flex-shrink: 0; }
.goal-summary span { display: block; color: var(--muted); font-size: 12px; }
.goal-summary strong { display: block; margin-top: 10px; font-size: 16px; }
.summary-strip { margin-top: 16px; margin-bottom: 26px; border-radius: 8px; }
.summary-strip > div { padding: 18px 20px; background: #fff; }
.summary-strip small { display: inline; }
.summary-strip strong { margin-top: 9px; font-size: 24px; font-variant-numeric: tabular-nums; }
.summary-total { background: #e2eeea !important; }
.calculator-section { margin-top: 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; box-shadow: none; background: #fff; }
.section-heading { padding: 25px 28px 18px; border-bottom: 0; }
.section-heading > span { border-radius: 8px; }
.section-heading h2 { font-size: 20px; }
.calculator-section:nth-of-type(5) .section-heading > span { background: var(--seafoam); color: #40685c; }
.section-content { padding: 2px 28px 28px; }
.gender-field, .basic-fields label, .field-row, .training-inputs label, .training-levels { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 16px; align-items: center; }
.gender-field > .field-caption, .field-caption { margin: 0; color: var(--ink); font-size: 13px; }
.gender-row { gap: 12px; }
.gender-row button { min-height: 48px; padding: 0 16px; border-radius: 8px; background: #fff; font-size: 14px; }
.gender-row button.active { border-color: var(--brand-strong); background: var(--brand-soft); color: var(--ink); }
.selection-dot { flex: none; width: 16px; height: 16px; border: 1.5px solid #a69aaf; border-radius: 50%; box-sizing: border-box; }
.active .selection-dot { border: 5px solid var(--brand-strong); background: white; }
.gender-row b { visibility: hidden; }
.gender-row .active b { visibility: visible; }
.basic-fields, .training-inputs { grid-template-columns: minmax(0, 1fr); gap: 16px; margin-top: 16px; }
.basic-fields label, .training-inputs label, .field-row { color: var(--ink); font-size: 13px; }
.input-wrap, .field-row select { height: 48px; border-radius: 8px; }
.input-wrap input { font-size: 16px; padding-left: 16px; }
.input-wrap em { padding-right: 16px; font-size: 12px; }
.input-wrap:focus-within { border-color: var(--brand-strong); box-shadow: 0 0 0 3px rgb(121 100 143 / 13%); }
.input-wrap input:focus-visible { outline: 0; }
.inline-result { margin-top: 18px; padding-top: 15px; }
.inline-result strong { font-size: 23px; }
.daily-energy { margin-top: 16px; border-radius: 8px; background: #e2eeea; padding: 15px 18px; }
.daily-energy span, .daily-energy strong { color: #40685c; }
.daily-energy small { color: #536f65; }
.training-levels { margin-top: 16px; }
.level-grid { gap: 10px; }
.level-grid button { min-height: 70px; padding: 10px 5px; text-align: center; border-radius: 8px; background: #fff; }
.level-grid strong { font-size: 13px; }
.level-grid small { font-size: 11px; margin-top: 6px; }
.level-grid button.active { border-color: var(--brand-strong); background: var(--brand-strong); }
.level-grid button.active small { color: #fff; }
.goal-tabs { border-radius: 8px; background: #eeeaf0; }
.goal-tabs button { border-radius: 5px; color: var(--muted); }
.goal-tabs button.active { color: var(--brand-strong); background: #fff; }
.suggestion { border-radius: 8px; padding: 18px; }
.suggestion strong { font-size: 28px; }
.suggestion small { display: block; text-align: right; margin-top: 4px; }
.finish-button { margin-top: 20px; height: 52px; max-width: 860px; border-radius: 8px !important; border-color: var(--brand-strong) !important; background: var(--brand-strong) !important; }
.calculator-page button { transition: background-color 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
.gender-row button:hover, .level-grid button:not(.active):hover { border-color: var(--brand-strong); background: var(--brand-soft); }
.goal-tabs button:not(.active):hover { background: rgb(255 255 255 / 65%); }
.finish-button:hover { background: #665079 !important; }
.calculator-page button:active { filter: brightness(.96); }
@media (max-width: 620px) {
  .calculator-page { padding: 0 16px 32px; }
  .topbar { min-height: 72px; }
  .topbar-copy h1 { font-size: 19px; }
  .energy-hero { grid-template-columns: minmax(0, 1fr) 120px; padding: 22px; gap: 8px; min-height: 210px; }
  .hero-copy h2 { font-size: 26px; }
  .hero-copy p { font-size: 12px; }
  .hero-otter { height: 140px; }
  .recommendation { padding: 17px 20px; }
  .hero-result { padding: 0; border: 0; }
  .hero-result strong { font-size: 32px; }
  .summary-strip { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .summary-strip > div { padding: 14px 9px; }
  .summary-strip strong { font-size: 20px; }
  .summary-strip small { display: block; margin-top: 3px; }
  .section-heading { padding: 22px 18px 18px; }
  .section-content { padding: 0 18px 24px; }
  .gender-field, .basic-fields label, .field-row, .training-inputs label, .training-levels { grid-template-columns: 110px minmax(0, 1fr); gap: 12px; }
  .gender-row button { padding: 0 10px; gap: 7px; justify-content: center; }
  .gender-row b { display: none; }
  .level-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .level-grid button { min-height: 70px; }
  .level-grid strong { font-size: 12px; line-height: 1.5; }
  .suggestion { flex-direction: row; align-items: center; gap: 12px; }
  .suggestion p { font-size: 11px; }
  .suggestion strong { font-size: 25px; }
}
@media (max-width: 420px) {
  .calculator-page { padding-inline: 12px; }
  .energy-hero { grid-template-columns: minmax(0, 1fr) 96px; padding: 20px 16px; }
  .hero-copy h2 { font-size: 23px; }
  .hero-otter { height: 112px; }
  .goal-summary span { font-size: 10px; }
  .goal-summary strong { font-size: 13px; }
  .hero-result { gap: 5px 7px; }
  .hero-result small { font-size: 10px; }
  .gender-field, .basic-fields label, .field-row, .training-inputs label { grid-template-columns: 88px minmax(0, 1fr); gap: 8px; }
  .training-levels { grid-template-columns: minmax(0, 1fr); gap: 10px; }
  .section-heading, .section-content { padding-inline: 14px; }
  .summary-strip > div { padding-inline: 7px; }
  .summary-strip span { font-size: 10px; }
  .summary-strip strong { font-size: 18px; }
  .suggestion { align-items: flex-start; flex-direction: column; }
  .suggestion small { display: inline; margin-left: 5px; }
}
</style>
