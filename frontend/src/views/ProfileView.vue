<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import { useRouter } from "vue-router";
import { saveGoal, saveProfile } from "../api/profile";
import { useAuthStore } from "../stores/auth";

import UserAvatar from '../components/UserAvatar.vue';
import AvatarCropper from '../components/AvatarCropper.vue';
import { uploadAvatar } from '../api/avatar';
const avatarFile = ref(null), avatarPreview = ref(''), cropFile = ref(null), avatarInput = ref(null);
const loaded = ref(false), initialBirthday = ref(null), initialAge = ref(30), initialGoal = ref('maintain');
const firstSetup = ref(false);
function chooseAvatar(event) {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file || saving.value) return;
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) return showFailToast('请选择 JPG、PNG 或 WEBP 图片');
  if (file.size > 5 * 1024 * 1024) return showFailToast('头像不能超过5MB');
  cropFile.value = file;
}
function useCrop(blob) {
  if (avatarPreview.value) URL.revokeObjectURL(avatarPreview.value);
  avatarFile.value = blob; avatarPreview.value = URL.createObjectURL(blob); cropFile.value = null;
}
onUnmounted(() => { if (avatarPreview.value) URL.revokeObjectURL(avatarPreview.value) });
const auth = useAuthStore();
const router = useRouter();
const saving = ref(false);
const form = ref({
  nickname: "",
  gender: "male",
  age: 30,
  heightCm: "",
  currentWeightKg: "",
  targetWeightKg: "",
  activityLevel: "medium",
  goalType: "maintain",
});
const goals = [
  { value: "lose", label: "减脂", adjustment: -400 },
  { value: "maintain", label: "维持", adjustment: 0 },
  { value: "gain", label: "增肌", adjustment: 225 },
];
const activities = [
  { value: "low", label: "久坐办公、学生", multiplier: 1.2 },
  { value: "medium", label: "日常走动较多", multiplier: 1.325 },
  { value: "high", label: "体力劳动为主", multiplier: 1.45 },
];

const dailyEnergy = computed(() => {
  const height = Number(form.value.heightCm),
    weight = Number(form.value.currentWeightKg);
  if (!(height > 0 && weight > 0)) return null;
  const bmr =
    10 * weight +
    6.25 * height -
    5 * Number(form.value.age) +
    (form.value.gender === "female" ? -161 : 5);
  return Math.round(
    bmr *
      activities.find((item) => item.value === form.value.activityLevel)
        .multiplier,
  );
});
const dailyIntake = computed(() =>
  dailyEnergy.value == null
    ? null
    : dailyEnergy.value +
      goals.find((item) => item.value === form.value.goalType).adjustment,
);

function birthdayFromAge(age) {
  const now = new Date();
  return `${now.getFullYear() - Number(age)}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
function today() {
  return new Date().toISOString().slice(0, 10);
}

async function submit() {
  if (!Number.isInteger(Number(form.value.age)) || Number(form.value.age) < 1 || Number(form.value.age) > 120) return showFailToast("年龄请填写1到120的整数");
  if (
    !(Number(form.value.heightCm) > 0 && Number(form.value.currentWeightKg) > 0)
  )
    return showFailToast("请填写身高和当前体重");
  if (saving.value || !loaded.value) return;
  saving.value = true;
  try {
    let avatar = auth.user?.avatar || null;
    if (avatarFile.value) { const uploaded = await uploadAvatar(avatarFile.value); avatar = uploaded.url; }
    const updated = await saveProfile({
      avatar,
      nickname: form.value.nickname || null,
      gender: form.value.gender,
      birthday: !firstSetup.value && Number(form.value.age) === initialAge.value ? initialBirthday.value : birthdayFromAge(form.value.age),
      heightCm: Number(form.value.heightCm),
      currentWeightKg: Number(form.value.currentWeightKg),
      targetWeightKg: form.value.targetWeightKg
        ? Number(form.value.targetWeightKg)
        : null,
      activityLevel: form.value.activityLevel,
    });
    auth.user = updated;
    if (firstSetup.value || !updated.activeGoal || form.value.goalType !== initialGoal.value) await saveGoal({
      goalType: form.value.goalType,
      dailyIntakeTarget: null,
      startDate: today(),
    });
    await auth.loadUser();
    showSuccessToast("个人信息和目标已保存");
    router.replace(firstSetup.value ? "/diet" : "/home");
  } catch (error) {
    showFailToast(error.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  try {
    const user = auth.user || (await auth.loadUser());
    const profile = user?.profile || {},
      goal = user?.activeGoal || {};
    firstSetup.value = !user?.profileCompleted;
    initialBirthday.value = profile.birthday || null;
    if (profile.birthday) {
      const birth = new Date(profile.birthday), now = new Date();
      let age = now.getFullYear() - birth.getFullYear();
      if (now.getMonth() < birth.getMonth() || (now.getMonth() === birth.getMonth() && now.getDate() < birth.getDate())) age--;
      form.value.age = age;
    }
    initialAge.value = Number(form.value.age);
    initialGoal.value = goal.goalType || 'maintain';
    form.value.nickname = user?.nickname || "";
    form.value.gender = profile.gender || "male";
    form.value.heightCm = profile.heightCm || "";
    form.value.currentWeightKg = profile.currentWeightKg || "";
    form.value.targetWeightKg = profile.targetWeightKg || "";
    form.value.activityLevel = profile.activityLevel || "medium";
    form.value.goalType = goal.goalType || "maintain";
    loaded.value = true;
  } catch (error) {
    showFailToast(error.message || "个人资料加载失败");
  }
});
</script>

<template>
  <main class="profile-page">
    <header>
      <button v-if="!firstSetup" type="button" class="profile-back" :disabled="saving" @click="router.push('/home')">‹ 返回个人页</button><p class="eyebrow">YOUR BASELINE</p>
      <h1>{{ firstSetup ? "完善个人信息" : "编辑个人信息" }}</h1>
      <p>这些数据用于计算每日消耗与热量建议</p>
    </header>
    <van-form @submit="submit"
      ><section class="form-panel avatar-editor">
        <UserAvatar :src="avatarPreview || auth.user?.avatar" alt="我的头像" width="76" height="76" />
        <div><button type="button" :disabled="saving || !loaded" @click="avatarInput.click()">更换头像</button><p>JPG、PNG、WEBP，最大5MB</p></div>
        <input ref="avatarInput" type="file" accept="image/jpeg,image/png,image/webp" aria-label="选择头像图片" hidden @change="chooseAvatar" />
      </section><section class="form-panel">
        <van-field
          v-model.trim="form.nickname"
          label="昵称"
          placeholder="选填"
          maxlength="50"
        /><van-cell title="性别"
          ><template #value
            ><van-radio-group v-model="form.gender" direction="horizontal"
              ><van-radio name="male">男</van-radio
              ><van-radio name="female">女</van-radio></van-radio-group
            ></template
          ></van-cell
        ><van-field
          v-model.number="form.age"
          label="年龄"
          type="digit"
          inputmode="numeric"
        />
        <div class="two-fields">
          <van-field
            v-model="form.heightCm"
            label="身高"
            type="digit"
            placeholder="cm"
          /><van-field
            v-model="form.currentWeightKg"
            label="当前体重"
            type="digit"
            placeholder="kg"
          />
        </div>
        <van-field
          v-model="form.targetWeightKg"
          label="目标体重"
          type="digit"
          placeholder="选填，kg"
        />
      </section>
      <section class="form-panel">
        <h2>健康目标</h2>
        <div class="goal-grid">
          <button
            v-for="goal in goals"
            :key="goal.value"
            type="button"
            :class="{ active: form.goalType === goal.value }"
            @click="form.goalType = goal.value"
          >
            {{ goal.label }}
          </button>
        </div>
        <van-field
          v-model="form.activityLevel"
          label="日常活动"
          is-link
          readonly
          @click="undefined"
          ><template #input
            ><select v-model="form.activityLevel">
              <option
                v-for="item in activities"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }}
              </option>
            </select></template
          ></van-field
        >
        <div class="advice">
          <span
            >预计每日消耗<strong>{{ dailyEnergy ?? "--" }} kcal</strong></span
          ><span
            >每日摄入建议<strong>{{ dailyIntake ?? "--" }} kcal</strong></span
          >
        </div>
      </section>
      <div class="submit-wrap">
        <van-button
          round
          block
          native-type="submit"
          type="primary"
          :loading="saving"
          :disabled="!loaded"
          >{{ firstSetup ? "保存并进入记录" : "保存个人信息" }}</van-button
        >
      </div>
    </van-form>
    <AvatarCropper v-if="cropFile" :file="cropFile" @confirm="useCrop" @cancel="cropFile = null" />
  </main>
</template>

<style scoped>
.profile-back{min-height:40px;margin-bottom:10px;border:0;background:transparent;color:#684e80;font-size:14px}.avatar-editor{display:flex;align-items:center;gap:18px}.avatar-editor button{min-height:44px;padding:0 16px;border:1px solid #dcd3df;border-radius:10px;background:#f0e9f4;color:#684e80;font:inherit;font-size:14px}.avatar-editor p{margin:8px 0 0;color:#817565;font-size:12px}

.profile-page {
  min-height: 100vh;
  padding: 28px 16px 48px;
  background: #f4faff;
  color: #254966;
}
.profile-page > * {
  width: min(100%, 680px);
  margin-right: auto;
  margin-left: auto;
}
.profile-page header {
  margin-bottom: 20px;
}
.eyebrow {
  margin: 0;
  color: #39b8ee;
  font-family: ui-monospace, monospace;
  font-size: 11px;
}
.profile-page h1 {
  margin: 7px 0;
  font-size: 25px;
}
.profile-page header > p:last-child {
  margin: 0;
  color: #7593a8;
  font-size: 13px;
}
.form-panel {
  margin-top: 12px;
  overflow: hidden;
  border: 1px solid #d7eaf5;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 18px rgba(84, 153, 191, 0.07);
}
.two-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
.two-fields :deep(.van-cell) + :deep(.van-cell) {
  border-left: 1px solid #ebf3f8;
}
.form-panel h2 {
  margin: 18px 16px 12px;
  font-size: 16px;
}
.goal-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding: 0 16px 14px;
}
.goal-grid button {
  min-height: 40px;
  border: 1px solid #cde7f4;
  border-radius: 6px;
  background: #f7fcff;
  color: #62849b;
}
.goal-grid button.active {
  border-color: #39b8ee;
  background: #39b8ee;
  color: #fff;
  font-weight: 700;
}
.form-panel select {
  width: 100%;
  border: 0;
  background: transparent;
  color: #323233;
  text-align: right;
}
.advice {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 12px 16px 18px;
  padding: 12px;
  border-radius: 6px;
  background: #f5fbfe;
}
.advice span {
  color: #7593a8;
  font-size: 12px;
}
.advice strong {
  display: block;
  margin-top: 5px;
  color: #39a9df;
  font-size: 16px;
}
.advice span:last-child strong {
  color: #e2705d;
}
.submit-wrap {
  margin-top: 24px;
}
:deep(.van-button--primary) {
  border-color: #39b8ee;
  background: #39b8ee;
}
</style>
