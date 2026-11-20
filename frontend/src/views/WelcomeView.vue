<template>
  <main class="welcome">
    <div class="sea-mark"><span /></div>
    <h1>橙子日记</h1>
  </main>
</template>
<style scoped>
.welcome {
  display: grid;
  min-height: 100vh;
  place-content: center;
  justify-items: center;
  background: #eef8ff;
}
.sea-mark {
  position: relative;
  width: 90px;
  height: 90px;
  overflow: hidden;
  border: 6px solid #39b8ee;
  border-radius: 50%;
  background: #dff5ff;
}
.sea-mark span {
  position: absolute;
  right: -20px;
  bottom: 12px;
  left: -20px;
  height: 31px;
  border-radius: 50%;
  background: #39b8ee;
}
.welcome h1 {
  margin: 24px 0 0;
  color: #254966;
  font-size: 20px;
}
</style>
