<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { showFailToast, showSuccessToast } from "vant";
import AppBottomNav from "../components/AppBottomNav.vue";
import UserAvatar from "../components/UserAvatar.vue";
import { useAuthStore } from "../stores/auth";
import { chatStream, chatWithAi, fetchAiHistory } from "../api/ai";
import { createDietRecord } from "../api/diet";
import { createExercise, previewExercise } from "../api/health";
import { submitIdempotently } from "../api/idempotency";
import "../assets/otter-theme.css";
import otterAvatar from "../assets/otter/avatar.png";
import otterHero from "../assets/otter/chat-hero.png";

const auth = useAuthStore();
const message = ref("");
const sending = ref(false);
const loadingHistory = ref(true);
const conversationEnd = ref(null);
const requestError = ref("");
/** 流式请求的中断句柄：用户点"停止"或离开页面时取消，避免后台继续消耗模型额度。 */
let streamController = null;
const messages = ref([
  {
    role: "assistant",
    content:
      "你好，我是你的饮食助手。你可以问我今天还能吃多少、某一餐怎么搭配、蛋白质够不够。",
  },
]);
watch(() => [messages.value.length, sending.value], async () => {
  await nextTick();
  conversationEnd.value?.scrollIntoView({ block: "end", behavior: "auto" });
});
const dietPreview = ref(null);
const exercisePreview = ref(null);
const foodId = ref(null);
const exerciseId = ref(null);
const amount = ref("");
const duration = ref("");
const saving = ref(false);
const quickQuestions = [
  "我今天还能吃多少？",
  "分析一下今天吃得怎么样",
  "红烧肉怎么做",
  "有辣椒和五花肉，能做什么菜？",
];
const meals = {
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
  snack: "加餐",
};
const selectedFood = computed(() =>
  dietPreview.value?.candidates?.find(
    (item) => String(item.id) === String(foodId.value),
  ),
);
const selectedExercise = computed(() =>
  exercisePreview.value?.candidates?.find(
    (item) => String(item.id) === String(exerciseId.value),
  ),
);
const recordTime = (preview) =>
  preview?.recordTime || `${new Date().toISOString().slice(0, 16)}:00`;

function clearChat() {
  messages.value = [
    {
      role: "assistant",
      content:
        "页面显示已清空。长期记忆仍会保留，你可以继续询问饮食、热量、运动和目标。",
    },
  ];
  requestError.value = "";
  dietPreview.value = null;
  exercisePreview.value = null;
}
function openDiet(preview) {
  dietPreview.value = preview;
  foodId.value = preview.selectedFoodId || preview.candidates?.[0]?.id;
  amount.value = String(preview.quantityG || "");
}
function openExercise(preview) {
  exercisePreview.value = preview;
  exerciseId.value = preview.selectedExerciseId || preview.candidates?.[0]?.id;
  duration.value = String(preview.durationMin || "");
}
function readableError(error) {
  return /timeout/i.test(error?.message || "")
    ? "AI 回复时间较长，请稍后重试。"
    : error?.message || "AI 服务暂时不可用，请检查网络后重试。";
}
// 来源字段做防御性归一化：链接未核实时不渲染成可点击引用。
function normalizeSources(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item) => item && item.title)
    .map((item) => ({
      rank: Number(item.rank) || 0,
      title: String(item.title),
      headingPath: Array.isArray(item.headingPath)
        ? item.headingPath.filter(Boolean).map(String)
        : [],
      excerpt: typeof item.excerpt === "string" ? item.excerpt : "",
      sourceUrl: typeof item.sourceUrl === "string" ? item.sourceUrl : "",
      needsReview: Boolean(item.needsReview),
    }));
}

// 知识库状态文案：让用户看懂"没资料"和"服务不可用"是两件事
const KNOWLEDGE_HINTS = {
  unavailable: "知识库暂时不可用，本回答未引用资料，稍后可再试。",
  busy: "检索排队已满，本次未取到资料，稍后再问会更好。",
};

function knowledgeHint(item) {
  return KNOWLEDGE_HINTS[item?.knowledgeStatus] || "";
}

// 已经出字就不再显示"正在生成"，否则会出现加载动画和正文并存
const hasStreamingText = computed(() => {
  const last = messages.value[messages.value.length - 1];
  return Boolean(last?.role === "assistant" && last?.content);
});

function applyAction(result) {
  if (result?.action === "confirm_diet_record" && result.recordPreview)
    openDiet(result.recordPreview);
  if (result?.action === "confirm_exercise_record" && result.exercisePreview)
    openExercise(result.exercisePreview);
}

function stopStreaming() {
  streamController?.abort();
}

async function send(text = message.value) {
  const question = text.trim();
  if (!question || sending.value || loadingHistory.value) return;
  dietPreview.value = null;
  exercisePreview.value = null;
  messages.value.push({ role: "user", content: question });
  message.value = "";
  requestError.value = "";
  sending.value = true;
  // 先占位再接收：否则用户要等到第一段文字到达才看到"我在回答"
  messages.value.push({ role: "assistant", content: "", sources: [], knowledgeStatus: "", pending: true });
  const target = messages.value[messages.value.length - 1];
  streamController = new AbortController();
  let streamed = false;
  try {
    const result = await chatStream({
      message: question,
      signal: streamController.signal,
      onDelta: (delta) => {
        streamed = true;
        target.content += delta;
      },
      onSources: (list) => {
        target.sources = normalizeSources(list);
      },
      onKnowledge: (status) => {
        target.knowledgeStatus = status || "";
      },
    });
    if (!result?.reply) throw new Error("AI 返回了空回答");
    target.pending = false;
    applyAction(result);
  } catch (error) {
    if (error?.name === "AbortError") {
      target.pending = false;
      target.content = target.content || "（已停止生成）";
      return;
    }
    // 已经出过字就不回落同步接口：重试会让用户看到两遍不同答案
    if (!streamed) {
      try {
        const result = await chatWithAi({ message: question });
        target.content = result.reply;
        target.sources = normalizeSources(result.sources);
        target.knowledgeStatus = result.knowledgeStatus || "";
        target.pending = false;
        applyAction(result);
        return;
      } catch (fallbackError) {
        error = fallbackError;
      }
    }
    target.pending = false;
    requestError.value = readableError(error);
    showFailToast(requestError.value);
  } finally {
    sending.value = false;
    streamController = null;
  }
}
async function saveDiet() {
  const weight = Number(amount.value);
  if (!selectedFood.value || !(weight > 0))
    return showFailToast("请选择食物并填写重量");
  saving.value = true;
  try {
    const payload = {
      foodItemId: selectedFood.value.id,
      mealType: dietPreview.value.mealType || "lunch",
      quantityG: weight,
      recordTime: recordTime(dietPreview.value),
      remark: "AI 自然语言记录",
    };
    await submitIdempotently("diet_create", {
      foodItemId: payload.foodItemId,
      mealType: payload.mealType,
      quantityG: payload.quantityG,
    }, payload, createDietRecord);
    messages.value.push({
      role: "assistant",
      content: `已保存：${meals[dietPreview.value.mealType] || "饮食"} ${selectedFood.value.name} ${weight}克。`,
    });
    dietPreview.value = null;
    showSuccessToast("保存成功");
  } catch (error) {
    showFailToast(error.message || "保存失败");
  } finally {
    saving.value = false;
  }
}
async function saveExercise() {
  const minutes = Number(duration.value);
  if (!selectedExercise.value || !(minutes > 0))
    return showFailToast("请选择运动并填写时长");
  saving.value = true;
  try {
    const payload = {
      exerciseItemId: selectedExercise.value.id,
      durationMin: minutes,
      recordTime: recordTime(exercisePreview.value),
      remark: "AI 自然语言记录",
    };
    const result = await submitIdempotently("exercise_create", {
      exerciseItemId: payload.exerciseItemId,
      durationMin: payload.durationMin,
    }, payload, async (savedPayload, requestId) => {
      const estimate = await previewExercise(savedPayload);
      await createExercise(savedPayload, requestId);
      return estimate;
    });
    messages.value.push({
      role: "assistant",
      content: `已保存：${selectedExercise.value.name} ${minutes}分钟，消耗约 ${Number(result.estimatedCalorieBurned || 0).toFixed(0)} 千卡。`,
    });
    exercisePreview.value = null;
    showSuccessToast("保存成功");
  } catch (error) {
    showFailToast(error.message || "保存失败");
  } finally {
    saving.value = false;
  }
}
onMounted(async () => {
  // 服务端是唯一会话来源，避免旧缓存、跨账号缓存覆盖真实历史。
  try {
    const rows = await fetchAiHistory(40);
    if (Array.isArray(rows) && rows.length) {
      messages.value = rows.map((row) => ({ ...row, sources: normalizeSources(row.sources) }));
    }
  } catch {
    requestError.value = "历史消息加载失败，当前显示可能不完整，请刷新重试。";
  } finally {
    loadingHistory.value = false;
  }
});
</script>

<template>
  <div class="otter-page chat-shell">
  <main class="ai-page">
    <header>
      <div>
        <h1>AI 饮食助手</h1>
        <p>结合你的记录，陪你吃好、动好</p>
      </div>
      <van-button plain hairline size="small" :disabled="sending || loadingHistory" @click="clearChat"
        >清空</van-button
      >
      <img class="chat-hero" :src="otterHero" alt="" width="164" height="85" />
    </header>
    <div class="chat-content">
    <p class="quick-label">不知道问什么？试试这些</p>
    <div class="quick">
      <button
        v-for="question in quickQuestions"
        :key="question"
        type="button"
        :disabled="sending || loadingHistory"
        @click="send(question)"
      >
        {{ question }}
      </button>
    </div>
    <section class="messages" aria-live="polite">
      <div
        v-for="(item, index) in messages"
        :key="index"
        :class="['message-row', item.role]"
      >
        <UserAvatar
          v-if="item.role === 'user'"
          class="avatar user-avatar"
          :src="auth.user?.avatar"
          alt="我的头像"
        />
        <img v-else class="avatar" :src="otterAvatar" alt="" width="32" height="32" />
        <div class="message-content">
          <span class="sender">{{ item.role === 'user' ? '我' : '海獭小助手' }}</span>
          <p>{{ item.content }}<span v-if="item.pending" class="caret" aria-hidden="true">▍</span></p>
          <p v-if="knowledgeHint(item)" class="knowledge-hint">{{ knowledgeHint(item) }}</p>
          <div v-if="item.sources && item.sources.length" class="sources">
            <p class="sources-title">参考来源</p>
            <ul>
              <li v-for="source in item.sources" :key="`${source.rank}-${source.title}`">
                <a
                  v-if="source.sourceUrl"
                  :href="source.sourceUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  >{{ source.title }}</a
                >
                <span v-else>{{ source.title }}</span>
                <span v-if="source.headingPath.length" class="source-path">
                  {{ source.headingPath.join(" / ") }}
                </span>
                <span v-if="source.needsReview" class="source-flag">链接待核实</span>
                <span v-if="source.excerpt" class="source-excerpt">{{ source.excerpt }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
      <div v-if="sending && !hasStreamingText" class="thinking">
        <van-loading size="16px" />正在生成回复
      </div>
    </section>
    <section v-if="dietPreview" class="confirm-card">
      <h2>确认饮食记录</h2>
      <select v-model="foodId">
        <option
          v-for="food in dietPreview.candidates"
          :key="food.id"
          :value="food.id"
        >
          {{ food.name }}
        </option></select
      ><label
        >食用重量（g）<input v-model="amount" type="number" inputmode="decimal"
      /></label>
      <p>餐次：{{ meals[dietPreview.mealType] || "午餐" }}</p>
      <div>
        <van-button plain @click="dietPreview = null">取消</van-button
        ><van-button type="primary" :loading="saving" @click="saveDiet"
          >确认保存</van-button
        >
      </div>
    </section>
    <section v-if="exercisePreview" class="confirm-card">
      <h2>确认运动记录</h2>
      <select v-model="exerciseId">
        <option
          v-for="exercise in exercisePreview.candidates"
          :key="exercise.id"
          :value="exercise.id"
        >
          {{ exercise.name }}
        </option></select
      ><label
        >运动时长（分钟）<input
          v-model="duration"
          type="number"
          inputmode="decimal"
      /></label>
      <div>
        <van-button plain @click="exercisePreview = null">取消</van-button
        ><van-button type="primary" :loading="saving" @click="saveExercise"
          >确认保存</van-button
        >
      </div>
    </section>
    <p v-if="requestError" class="request-error" role="alert">
      {{ requestError }}
    </p>
    <div ref="conversationEnd" class="conversation-end" aria-hidden="true"></div>
    </div>
    <form class="composer" @submit.prevent="send()">
      <textarea
        v-model="message"
        maxlength="4000"
        :disabled="sending || loadingHistory"
        rows="1"
        aria-label="输入你的问题"
        placeholder="输入你的问题…"
      /><van-button v-if="sending" plain @click="stopStreaming">停止</van-button
      ><van-button native-type="submit" type="primary" :loading="sending" :disabled="!message.trim() || sending"
        >发送</van-button
      >
    </form>
  </main>
  <AppBottomNav active="ai" theme="otter-minimal" />
  </div>
</template>

<style scoped>
.ai-page { width: min(100%, 648px); margin: auto; padding: 28px 16px calc(162px + env(safe-area-inset-bottom)); }
.ai-page > header { position: relative; min-height: 153px; }
.ai-page h1 { margin: 0 0 8px; font-size: 25px; line-height: 40px; }
.ai-page header p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
.ai-page header > .van-button { position: absolute; top: 0; right: 0; min-width: 44px; min-height: 40px; padding: 0 8px; border-color: var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); }
.chat-hero { position: absolute; right: 0; bottom: 0; object-fit: contain; }
.chat-content { min-height: calc(100dvh - 390px); margin-top: 20px; padding: 18px 12px; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); }
.quick-label { margin: 0 0 18px; color: var(--muted); font-size: 12px; line-height: 1.5; }
.quick { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 10px; }
.quick button { min-height: 52px; padding: 10px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--ink); font-size: 12px; font-weight: 700; line-height: 1.6; text-align: left; overflow-wrap: anywhere; }
.quick button:nth-child(2), .quick button:nth-child(3) { background: var(--brand-soft); }
.quick button:hover { border-color: var(--brand); }
.messages { margin-top: 18px; }
.message-row { display: flex; align-items: flex-start; gap: 9px; margin-bottom: 18px; min-width: 0; }
.message-row.user { flex-direction: row-reverse; }
.avatar { width: 32px; height: 32px; flex: none; object-fit: contain; border-radius: 9px; }
.avatar.user-avatar { border-radius: 10px; object-fit: cover; background: var(--brand-soft); box-shadow: 0 0 0 1px var(--line); }
.message-content { flex: 1; min-width: 0; }
.sender { display: block; margin: 0 0 5px; color: var(--muted); font-size: 11px; line-height: 1.5; }
.user .sender { text-align: right; }
.message-content > p { margin: 0; padding: 10px 12px; border: 1px solid var(--line); border-left: 3px solid #d2c4df; border-radius: 12px; background: var(--surface); color: var(--ink); font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.sources { margin-top: 8px; padding: 10px 12px; border: 1px dashed var(--line); border-radius: 12px; background: var(--brand-soft); }
.sources-title { margin: 0 0 6px; color: var(--muted); font-size: 11px; font-weight: 700; }
/* 流式输出的光标：没有它用户分不清"还在出字"和"卡住了" */
.caret { display: inline-block; margin-left: 2px; animation: caret-blink 1s steps(1) infinite; }
@keyframes caret-blink { 50% { opacity: 0; } }
/* 知识库状态提示：把"没资料"与"服务不可用"分开说，避免让用户以为知识库是空的 */
.knowledge-hint { margin: 6px 0 0; color: var(--muted); font-size: 11px; line-height: 1.5; }
.sources ul { margin: 0; padding-left: 16px; }
.sources li { color: var(--ink); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.sources li + li { margin-top: 6px; }
.sources a { color: var(--brand); text-decoration: underline; }
.source-path { display: block; color: var(--muted); font-size: 11px; }
.source-flag { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 6px; background: #fff3cd; color: #8a6100; font-size: 10px; }
.source-excerpt { display: block; margin-top: 2px; color: var(--muted); font-size: 11px; }
.user .message-content p { border-color: #d9cde4; background: var(--brand-soft); }
.thinking { display: flex; align-items: center; gap: 8px; width: fit-content; max-width: 100%; margin: 6px 0 0 41px; padding: 8px 11px; border-radius: 10px; background: var(--brand-soft); color: var(--brand-strong); font-size: 12px; }
.confirm-card { margin-top: 16px; padding: 16px; border: 1px solid var(--seafoam); border-radius: 14px; background: var(--page-bg); }
.confirm-card h2 { margin: 0 0 12px; font-size: 16px; }
.confirm-card label { display: grid; gap: 6px; margin-top: 10px; color: var(--muted); font-size: 12px; }
.confirm-card select, .confirm-card input { width: 100%; min-width: 0; min-height: 44px; border: 1px solid var(--line); border-radius: 10px; padding: 8px; background: var(--surface); color: var(--ink); font-size: 14px; }
.confirm-card p { color: var(--muted); font-size: 12px; }
.confirm-card > div { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
.confirm-card :deep(.van-button) { min-height: 44px; border-radius: 10px; }
.confirm-card :deep(.van-button--primary), .composer :deep(.van-button--primary) { border-color: var(--brand); background: var(--brand); }
.request-error { margin: 12px 0 0; padding: 10px 12px; border: 1px solid #e7b6a0; border-radius: 10px; background: #fff4ec; color: var(--danger); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.conversation-end { height: 1px; scroll-margin-bottom: 170px; }
.composer { position: fixed; z-index: 10; right: 16px; bottom: calc(82px + env(safe-area-inset-bottom)); left: 16px; display: flex; align-items: center; gap: 8px; width: min(calc(100% - 32px), 616px); margin: auto; padding: 10px; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); box-shadow: var(--shadow-soft); }
.composer textarea { flex: 1; min-width: 0; height: 42px; min-height: 42px; max-height: 120px; resize: vertical; padding: 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); font-size: 14px; line-height: 20px; }
.composer textarea::placeholder { color: var(--muted); }
.composer .van-button { flex: none; min-width: 60px; height: 42px; padding: 0 13px; border-radius: 10px; }
@media (max-width: 360px) { .quick { gap: 10px 8px; } .quick button { font-size: 11px; } .ai-page h1 { font-size: 23px; } }
</style>
