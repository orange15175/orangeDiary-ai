<script setup>
import { computed, onMounted, ref } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRouter } from "vue-router";
import { getCurrentUser } from "../api/auth";
import { saveGoal } from "../api/profile";
import AppBackButton from "../components/AppBackButton.vue";
const router = useRouter(),
  saving = ref(false),
  form = ref({ goalType: "lose", dailyIntakeTarget: "" });
const options = [
  {
    value: "lose",
    label: "减脂",
    hint: "建议在维持热量基础上减少 300-500 大卡。",
  },
  {
    value: "maintain",
    label: "维持",
    hint: "建议摄入热量与日常消耗大致持平。",
  },
  {
    value: "gain",
    label: "增肌",
    hint: "建议在维持热量基础上增加 150-300 大卡。",
  },
];
const hint = computed(
  () => options.find((x) => x.value === form.value.goalType)?.hint,
);
async function save() {
  const target =
    form.value.dailyIntakeTarget === ""
      ? null
      : Number(form.value.dailyIntakeTarget);
  if (target !== null && !(target > 0))
    return showFailToast("请输入大于 0 的每日摄入热量");
  saving.value = true;
  try {
    await saveGoal({
      goalType: form.value.goalType,
      dailyIntakeTarget: target,
      startDate: new Date().toISOString().slice(0, 10),
    });
    showSuccessToast("目标已保存");
    router.replace({ name: "home" });
  } catch (e) {
    showFailToast(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}
onMounted(async () => {
  try {
    const goal = (await getCurrentUser()).activeGoal || {};
    form.value = {
      goalType: goal.goalType || "lose",
      dailyIntakeTarget:
        goal.dailyIntakeTarget == null ? "" : String(goal.dailyIntakeTarget),
    };
  } catch (e) {
    showFailToast(e.message || "目标加载失败");
  }
});
</script>
<template>
  <main class="page">
    <header>
      <AppBackButton fallback="home" label="返回主页" />
      <div>
        <h1>我的目标</h1>
        <p>设置目标类型和每日摄入热量，用于计算当天还能摄入多少。</p>
      </div>
    </header>
    <section>
      <label
        >健康目标<select v-model="form.goalType">
          <option v-for="item in options" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select></label
      >
      <p>{{ hint }}</p>
      <label
        >每日摄入目标（kcal）<input
          v-model.trim="form.dailyIntakeTarget"
          type="number"
          placeholder="例如 1800；不填写则自动计算"
      /></label>
      <p>填写后，记录页“还能摄入”将按这个数值计算。</p>
    </section>
    <van-button block type="primary" :loading="saving" @click="save"
      >保存目标</van-button
    >
  </main>
</template>
<style scoped>
.page {
  min-height: 100vh;
  padding: 28px 16px;
  background: #eef8ff;
  color: #254966;
}
.page > * {
  width: min(100%, 680px);
  margin: auto;
}
.page header {
  display: flex;
  gap: 12px;
}
.page h1 {
  margin: 3px 0;
  font-size: 23px;
}
.page p {
  color: #7593a8;
  font-size: 13px;
  line-height: 1.5;
}
.page section {
  margin-top: 20px;
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
  margin-top: 20px;
}
.page input,
.page select {
  min-height: 40px;
  border: 1px solid #d7eaf5;
  border-radius: 6px;
  padding: 0 9px;
  background: #fff;
  color: #254966;
}
.page > .van-button {
  margin-top: 22px;
  height: 44px;
  border-color: #39b8ee;
  border-radius: 7px;
  background: #39b8ee;
}
</style>
