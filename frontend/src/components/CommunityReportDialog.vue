<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const props = defineProps({ open: Boolean, busy: Boolean, error: String, targetLabel: String })
const emit = defineEmits(['close', 'submit'])
const reason = ref('SPAM')
const details = ref('')
const dialog = ref(null)
let previous
const reasons = [
  ['SPAM', '垃圾广告'], ['HARASSMENT', '骚扰辱骂'], ['HEALTH_MISINFORMATION', '不实或危险健康信息'],
  ['SEXUAL_OR_VIOLENT', '色情或暴力内容'], ['PRIVACY', '侵犯隐私'], ['OTHER', '其他']
]
onMounted(() => { previous = document.activeElement; dialog.value.showModal() })
onUnmounted(() => previous?.focus?.())
</script>

<template>
  <dialog ref="dialog" class="report-dialog" aria-labelledby="report-title" @cancel.prevent="!busy && emit('close')">
    <form @submit.prevent="emit('submit', { reason, details })">
      <h2 id="report-title">举报{{ targetLabel }}</h2>
      <label>举报原因<select v-model="reason" :disabled="busy"><option v-for="item in reasons" :key="item[0]" :value="item[0]">{{ item[1] }}</option></select></label>
      <label>补充说明（选填）<textarea v-model="details" maxlength="500" :disabled="busy" placeholder="提供有助于核实的说明" /></label>
      <p class="count">{{ details.length }} / 500</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <footer><button type="button" :disabled="busy" @click="emit('close')">取消</button><button class="primary" type="submit" :disabled="busy">{{ busy ? '提交中…' : '提交举报' }}</button></footer>
    </form>
  </dialog>
</template>

<style scoped>
.report-dialog{position:fixed;inset:0;margin:auto;width:min(420px,calc(100vw - 36px));max-height:85vh;overflow:auto;border:1px solid var(--line);border-radius:12px;padding:22px;background:var(--surface,#fffdf8);color:var(--ink);box-shadow:0 18px 64px #30253633;z-index:100}.report-dialog::backdrop{background:#24202b66}h2{margin:0 0 20px;font-size:19px}label{display:grid;gap:8px;margin:14px 0;color:var(--muted);font-size:13px}select,textarea{box-sizing:border-box;width:100%;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);font:inherit;padding:10px}select{min-height:42px}textarea{min-height:104px;resize:vertical}.count{text-align:right;color:var(--muted);font-size:11px;margin:5px 0}.error{color:#b44137;font-size:13px}footer{display:flex;justify-content:end;gap:10px;margin-top:18px}button{min-height:40px;border:1px solid var(--line);border-radius:7px;padding:8px 14px;background:var(--surface);color:var(--brand-strong);cursor:pointer}.primary{background:var(--brand-strong);color:#fff}button:disabled{opacity:.55}
</style>
