<script setup>
import { ref, watch, onUnmounted } from 'vue'
import { fetchAvatar } from '../api/avatar'
import fallback from '../assets/otter/avatar.png'
const props = defineProps({ src: String, alt: { type: String, default: '用户头像' } })
const image = ref(fallback)
let version = 0
let objectUrl = null
watch(() => props.src, async (src) => {
  const current = ++version
  if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null }
  image.value = fallback
  if (!src) return
  if (!src.startsWith('/api/avatars/')) { image.value = src; return }
  try {
    const blob = await fetchAvatar(src)
    if (current !== version) return
    objectUrl = URL.createObjectURL(blob); image.value = objectUrl
  } catch { /* Default avatar remains visible. */ }
}, { immediate: true })
onUnmounted(() => { ++version; if (objectUrl) URL.revokeObjectURL(objectUrl) })
</script>
<template><img :src="image" :alt="alt" class="user-avatar" @error="image = fallback" /></template>
<style scoped>.user-avatar{object-fit:cover;flex:none;border-radius:50%;background:#f1ece3}</style>
