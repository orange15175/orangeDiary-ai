<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { clearFlowReturn } from '../router/flowReturn'

const props = defineProps({
  // 逻辑父级：返回键永远退到这里，不依赖浏览器历史里"上一页"是谁。
  fallback: { type: String, required: true },
  // 父级需要的上下文（日期、餐次），避免返回时被重置成默认值。
  query: { type: Object, default: () => ({}) },
  label: { type: String, default: '返回上一页' }
})
const router = useRouter()
const target = computed(() => ({ name: props.fallback, query: props.query }))

function goBack() {
  const parent = router.resolve(target.value)
  // 父级就是当前页时不做跳转，避免 no-op 导航。
  if (parent.fullPath === router.currentRoute.value.fullPath) return
  // 离开即视为放弃本次录入流程，连退标记作废。
  clearFlowReturn()
  const back = window.history.state?.back
  // 只有历史里的上一页恰好是父级才真的 pop，省掉一条重复记录。
  // 其它情况一律 replace：上一页可能是刚保存完的子页面（food-weight / exercise-edit），
  // 用 back() 会在"记录页 ⇄ 选择页"之间来回打转，退不出去。
  if (typeof back === 'string' && back === parent.fullPath) {
    router.back()
    return
  }
  router.replace(target.value)
}
</script>

<template>
  <van-button class="back-button" icon="arrow-left" plain hairline size="small" :aria-label="label" @click="goBack" />
</template>

<style scoped>
.back-button { width:38px; height:38px; border-color:var(--line); border-radius:var(--radius-sm); background:var(--surface); color:var(--ink); box-shadow:0 3px 9px rgba(45,111,148,.05); }.back-button:active { background:var(--brand-soft); }
</style>
