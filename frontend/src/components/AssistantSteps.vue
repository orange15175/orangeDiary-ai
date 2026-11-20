<template>
  <nav class="da-row da-steps" aria-label="饮食助手导航">
    <RouterLink :to="{ name: 'diet-preferences' }">偏好与食材</RouterLink>
    <RouterLink :to="{ name: 'chef' }">找菜做饭</RouterLink>
    <RouterLink :to="{ name: 'meal-plan' }">我的计划</RouterLink>
    <RouterLink :to="{ name: 'review' }">记录与复盘</RouterLink>
  </nav>
</template>
