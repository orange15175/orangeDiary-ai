<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
const route = useRoute();
const title = computed(() => route.query.title || "空白模块");
</script>
<template>
  <main>
    <h1>{{ title }}</h1>
    <p>暂时还没有内容，下一次记录会出现在这里</p>
  </main>
</template>
<style scoped>
main {
  display: grid;
  min-height: 100vh;
  place-content: center;
  justify-items: center;
  padding: 40px;
  background: #eef8ff;
  color: #7593a8;
  text-align: center;
}
h1 {
  margin: 0;
  color: #254966;
  font-size: 24px;
}
p {
  font-size: 13px;
}
</style>
