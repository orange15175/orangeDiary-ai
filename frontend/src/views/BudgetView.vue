<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRouter } from "vue-router";
import { getCurrentUser } from "../api/auth";
import { getDailySummary } from "../api/diet";
import { saveGoal } from "../api/profile";
import AppBackButton from "../components/AppBackButton.vue";
const router = useRouter(),
  calories = ref(""),
  goalType = ref("maintain"),
  saving = ref(false),
  ratios = ref(
    JSON.parse(
      localStorage.getItem("macroRatios") ||
        '{"carb":44,"protein":30,"fat":26}',
    ),
  );
const macros = computed(() =>
  [
    { key: "carb", name: "碳水化合物", unit: 4 },
    { key: "protein", name: "蛋白质", unit: 4 },
    { key: "fat", name: "脂肪", unit: 9 },
  ].map((x) => ({
    ...x,
    grams: Math.round(
      ((Number(calories.value) || 0) * ratios.value[x.key]) / 100 / x.unit,
    ),
  })),
);
watch(
  () => ratios.value.carb,
  (v) => {
    ratios.value.protein = Math.max(5, 100 - v - ratios.value.fat);
  },
);
watch(
  () => ratios.value.protein,
  (v) => {
    ratios.value.fat = Math.max(5, 100 - ratios.value.carb - v);
  },
);
async function save() {
  const value = Number(calories.value);
  if (!(value >= 1000 && value <= 4000))
    return showFailToast("请输入 1000 - 4000 之间的热量");
  saving.value = true;
  try {
    await saveGoal({
      goalType: goalType.value,
      dailyIntakeTarget: Math.round(value),
      startDate: new Date().toISOString().slice(0, 10),
    });
    localStorage.setItem("macroRatios", JSON.stringify(ratios.value));
    showSuccessToast("预算已保存");
    router.replace({ name: "diet" });
  } catch (e) {
    showFailToast(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}
onMounted(async () => {
  try {
    const [user, summary] = await Promise.all([
      getCurrentUser(),
      getDailySummary(new Date().toISOString().slice(0, 10)),
    ]);
    goalType.value = user.activeGoal?.goalType || "maintain";
    calories.value = String(
      summary.targetDailyCalorie || user.activeGoal?.dailyIntakeTarget || "",
    );
  } catch (e) {
    showFailToast(e.message || "预算加载失败");
  }
});
</script>
<template>
  <main class="page">
    <header>
      <AppBackButton fallback="diet" label="返回饮食记录" />
      <h1>自定义预算</h1>
    </header>
    <p class="intro">设置每天的营养预算，总热量决定三大营养素的克数。</p>
    <section>
      <label
        >每日总热量<input
          v-model="calories"
          type="number"
          placeholder="例如 1720"
        /><small>kcal，建议设置 1000 - 4000</small></label
      >
    </section>
    <header class="section-head">
      <h2>三大营养素分配</h2>
      <span>合计 {{ ratios.carb + ratios.protein + ratios.fat }}%</span>
    </header>
    <section>
      <label
        >碳水 {{ ratios.carb }}%<input
          v-model.number="ratios.carb"
          min="5"
          max="90"
          type="range" /></label
      ><label
        >蛋白 {{ ratios.protein }}%<input
          v-model.number="ratios.protein"
          min="5"
          max="90"
          type="range"
      /></label>
      <div v-for="item in macros" :key="item.key" class="macro">
        <strong>{{ item.name }}</strong
        ><span
          >{{ ratios[item.key] }}% · {{ item.grams }}g ·
          {{ item.unit }} kcal/g</span
        >
      </div>
    </section>
    <van-button block type="primary" :loading="saving" @click="save"
      >保存预算</van-button
    >
  </main>
</template>
<style scoped>
.page {
  min-height: 100vh;
  padding: 24px 16px 48px;
  background: #eef8ff;
  color: #254966;
}
.page > * {
  width: min(100%, 680px);
  margin-right: auto;
  margin-left: auto;
}
.page header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page h1 {
  font-size: 22px;
}
.intro {
  color: #7593a8;
  font-size: 13px;
}
.page section {
  margin-top: 16px;
  padding: 18px;
  border: 1px solid #d7eaf5;
  border-radius: 8px;
  background: #fff;
}
.page label {
  display: grid;
  gap: 8px;
  color: #4f7490;
  font-size: 13px;
}
.page label + label {
  margin-top: 17px;
}
.page input[type="number"] {
  min-height: 44px;
  border: 0;
  border-bottom: 1px solid #8bd7f4;
  color: #254966;
  font-size: 26px;
  font-weight: 700;
}
.page small {
  color: #7593a8;
}
.section-head {
  justify-content: space-between;
  margin-top: 24px;
}
.section-head h2 {
  font-size: 16px;
}
.section-head span {
  color: #208dbc;
  font-size: 12px;
}
.macro {
  display: flex;
  justify-content: space-between;
  padding: 14px 0;
  border-bottom: 1px solid #e6f2f8;
}
.macro span {
  color: #7593a8;
  font-size: 12px;
}
.page > .van-button {
  margin-top: 22px;
  height: 44px;
  border-color: #39b8ee;
  border-radius: 7px;
  background: #39b8ee;
}
</style>
