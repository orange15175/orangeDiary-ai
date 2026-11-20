import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { recordTodayCheckin } from '../api/checkin'
import { aiChatEnabled } from '../config/features'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/diet' },
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true } },
    { path: '/register', name: 'register', component: () => import('../views/RegisterView.vue'), meta: { public: true } },
    { path: '/profile', name: 'profile', component: () => import('../views/ProfileView.vue') },
    { path: '/home', name: 'home', component: () => import('../views/HomeView.vue') },
    { path: '/diet', name: 'diet', component: () => import('../views/DietOverviewView.vue') },
    { path: '/admin/community', name: 'admin-community', component: () => import('../views/AdminCommunityView.vue'), meta: { admin: true } },
    { path: '/admin/ai-traces', name: 'admin-ai-traces', component: () => import('../views/AdminAiTraceView.vue'), meta: { admin: true } },
    { path: '/community', name: 'community', component: () => import('../views/CommunityView.vue') },
    { path: '/diet/daily', name: 'diet-daily', component: () => import('../views/DietRecordView.vue') },
    { path: '/diet/add', name: 'diet-add', component: () => import('../views/AddDietView.vue') },
    { path: '/foods', name: 'foods', component: () => import('../views/FoodBrowserView.vue') },
    { path: '/diet/food-weight', name: 'food-weight', component: () => import('../views/FoodWeightView.vue') },
    // 做菜能力已统一到 AI 聊天窗口（/ai）：厨师/周计划/采购/复盘的独立页面与
    // 偏好设置页一并下线，避免出现指向已删路由的死链。页面文件暂留，便于恢复。
    { path: '/exercise/add', name: 'exercise-add', component: () => import('../views/ExerciseBrowserView.vue') },
    { path: '/exercise/edit', name: 'exercise-edit', component: () => import('../views/ExerciseDetailView.vue') },
    { path: '/weight/records', name: 'weight-records', component: () => import('../views/WeightView.vue') },
    ...(aiChatEnabled
      ? [{ path: '/ai', name: 'ai', component: () => import('../views/AiChatView.vue') }]
      : [{ path: '/ai', name: 'ai', redirect: '/diet' }]),
    { path: '/goal', name: 'goal', component: () => import('../views/GoalView.vue') },
    { path: '/nutrition/custom-budget', name: 'budget', component: () => import('../views/BudgetView.vue') },
    { path: '/weight/add', name: 'weight-add', component: () => import('../views/WeightAddView.vue') },
    { path: '/calorie/calculator', name: 'calorie-calculator', component: () => import('../views/CalorieCalculatorView.vue') },
    { path: '/welcome', name: 'welcome', component: () => import('../views/WelcomeView.vue'), meta: { public: true } },
    { path: '/check-ins', name: 'check-ins', component: () => import('../views/CheckinView.vue') },
    { path: '/blank', name: 'blank', component: () => import('../views/BlankView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/diet' }
  ]
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // Login and registration must stay renderable even when an older local token is invalid.
  if (to.meta.public) return true
  if (!to.meta.public && !auth.token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (!auth.user) {
    try { await auth.loadUser() } catch { auth.signOut(); return { name: 'login' } }
  }
  if (to.meta.admin) {
    try { await auth.loadUser() } catch { return { name: 'login' } }
    if (auth.user?.role !== 'admin') return { name: 'community' }
  }
  if (!auth.user?.profileCompleted && to.name !== 'profile') return { name: 'profile' }
})

router.afterEach((to) => {
  const auth = useAuthStore()
  if (to.meta.public || !auth.user) return
  // 路由守卫已验证 JWT、Redis 会话和账号状态，再静默记录当天足迹。
  recordTodayCheckin().catch(() => {})
})

export default router
