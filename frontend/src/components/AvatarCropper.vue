<script setup>
import { ref, watch, onUnmounted } from 'vue'
const props = defineProps({ file: File })
const emit = defineEmits(['confirm', 'cancel'])
const canvas = ref(null), zoom = ref(1), x = ref(0), y = ref(0), ready = ref(false), error = ref('')
let image, url, disposed = false
function draw() {
  if (!ready.value || !canvas.value) return
  const size = 320, scale = Math.max(size / image.width, size / image.height) * Number(zoom.value)
  const width = image.width * scale, height = image.height * scale
  const ctx = canvas.value.getContext('2d')
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, size, size)
  ctx.drawImage(image, -(width-size)/2 + Number(x.value)*(width-size)/200, -(height-size)/2 + Number(y.value)*(height-size)/200, width, height)
}
watch(() => props.file, (file) => {
  if (url) URL.revokeObjectURL(url)
  ready.value = false; error.value = ''; zoom.value = 1; x.value = 0; y.value = 0
  if (!file) return
  image = new Image(); url = URL.createObjectURL(file)
  image.onload = () => { if (!disposed) { ready.value = true; draw() } }
  image.onerror = () => { error.value = '无法读取这张图片，请重新选择' }
  image.src = url
}, { immediate: true })
watch([zoom, x, y], draw)
function confirm() { canvas.value?.toBlob(blob => { if (blob && !disposed) emit('confirm', blob) }, 'image/jpeg', .9) }
onUnmounted(() => { disposed = true; if(url) URL.revokeObjectURL(url) })
</script>
<template>
  <div class="crop-overlay" @keydown.esc="emit('cancel')">
    <section class="crop-dialog" role="dialog" aria-modal="true" aria-labelledby="crop-title">
      <h2 id="crop-title">调整头像</h2><p>移动和放大图片，保留你喜欢的部分</p>
      <canvas ref="canvas" width="320" height="320" aria-label="方形头像预览"></canvas>
      <p v-if="error" role="alert">{{ error }}</p>
      <label>放大<input v-model="zoom" type="range" min="1" max="3" step=".01" :disabled="!ready" /></label>
      <label>左右移动<input v-model="x" type="range" min="-100" max="100" :disabled="!ready" /></label>
      <label>上下移动<input v-model="y" type="range" min="-100" max="100" :disabled="!ready" /></label>
      <div class="crop-actions"><button type="button" @click="emit('cancel')">取消</button><button type="button" :disabled="!ready" @click="confirm">使用这张头像</button></div>
    </section>
  </div>
</template>
<style scoped>
.crop-overlay{position:fixed;inset:0;z-index:100;background:#32291d80;display:grid;place-items:center;padding:16px;overflow:auto}.crop-dialog{width:min(100%,360px);box-sizing:border-box;padding:20px;border-radius:20px;background:var(--surface,#fffcf6);color:var(--ink,#493526);box-shadow:0 15px 60px #0002}.crop-dialog h2{margin:0 0 8px;font-size:20px}.crop-dialog p{font-size:12px;color:var(--muted,#817565)}canvas{display:block;width:100%;max-height:35vh;object-fit:contain;background:#eee;border-radius:12px}label{display:flex;align-items:center;gap:12px;margin-top:14px;font-size:13px}input{flex:1;min-width:0;accent-color:#9782ac}.crop-actions{display:flex;gap:12px;margin-top:20px}.crop-actions button{flex:1;min-height:44px;border:1px solid #dcd3df;border-radius:10px;background:transparent;color:inherit}.crop-actions button:last-child{background:#9782ac;color:white;border-color:#9782ac}button:disabled{opacity:.5}button:focus-visible,input:focus-visible{outline:2px solid #74558f;outline-offset:3px}
</style>
