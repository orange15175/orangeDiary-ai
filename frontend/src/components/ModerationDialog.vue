<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
const props = defineProps({ title: String, busy: Boolean, error: String, restore: Boolean, notify: { type: Boolean, default: true } })
const emit = defineEmits(['cancel', 'submit'])
const reason = ref(''), dialog = ref(null), validation = ref('')
let previous
const options = props.restore ? ['内容复核通过', '误下架，予以恢复'] : ['含有广告或引流内容', '存在侮辱或不友善表达', '内容不符合社区规范']
function submit() { if (!reason.value.trim() || reason.value.trim().length > 500) { validation.value = '请填写1至500字的处理原因'; return }; validation.value = ''; emit('submit', reason.value.trim()) }
function close(event) { event.preventDefault(); if (!props.busy) emit('cancel') }
onMounted(async () => { previous = document.activeElement; dialog.value.showModal(); await nextTick(); dialog.value.querySelector('textarea').focus() })
onUnmounted(() => previous?.focus?.())
</script>
<template>
  <dialog ref="dialog" class="moderation-dialog" aria-labelledby="moderation-title" @cancel="close">
    <form @submit.prevent="submit">
      <h2 id="moderation-title">{{ title }}</h2><p>{{ notify ? '操作会保留记录，并向内容作者发送系统通知。' : '驳回后会保留举报记录，不会修改原内容。' }}</p>
      <div class="presets"><button v-for="option in options" :key="option" type="button" :disabled="busy" @click="reason = option">{{ option }}</button></div>
      <label for="moderation-reason">处理原因（必填）</label><textarea id="moderation-reason" v-model="reason" maxlength="500" :disabled="busy" placeholder="请说明本次处理的具体原因" /><span class="counter">{{ reason.length }} / 500</span>
      <p v-if="error || validation" role="alert" class="error">{{ error || validation }}</p>
      <footer><button type="button" :disabled="busy" @click="emit('cancel')">取消</button><button class="confirm" type="submit" :disabled="busy">{{ busy ? '正在提交…' : '确认处理' }}</button></footer>
    </form>
  </dialog>
</template>
<style scoped>
.moderation-dialog{width:min(440px,calc(100vw - 56px));max-height:85vh;overflow:auto;border:1px solid var(--line);border-radius:18px;padding:24px;background:var(--surface,#fffdf8);color:var(--ink);box-shadow:0 20px 80px #30253633}.moderation-dialog::backdrop{background:#24202b66}h2{margin:0 0 10px;font-size:20px}p{font-size:13px;line-height:1.7;color:var(--muted)}.presets{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.presets button{font-size:11px;padding:6px 10px}button{min-height:44px;border:1px solid var(--line);border-radius:8px;padding:8px 16px;background:var(--surface);color:var(--brand-strong);cursor:pointer}label{font-size:13px}textarea{box-sizing:border-box;width:100%;min-height:120px;margin-top:10px;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);font:inherit;font-size:14px;resize:vertical}.counter{display:block;text-align:right;font-size:11px;color:var(--muted);margin:8px 0}footer{display:flex;justify-content:end;gap:12px;margin-top:20px}.confirm{background:var(--brand-strong);color:white}.error{color:#b44137}button:disabled{opacity:.6;cursor:default}button:focus-visible,textarea:focus-visible{outline:2px solid var(--brand-strong);outline-offset:3px}
</style>
