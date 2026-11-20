import http from './http'

// M3 食谱与私人厨师
export const getRecipes = (params) => http.get('/recipes', { params })
export const getRecipe = (id) => http.get(`/recipes/${id}`)
export const askChef = (payload) => http.post('/assistant/chef', payload, { timeout: 60_000 })

// M4 周计划与食材核对
export const createPlanDraft = (payload) => http.post('/meal-plans/drafts', payload)
export const getPlans = (params) => http.get('/meal-plans', { params })
export const getPlan = (id, versionNumber) => http.get(`/meal-plans/${id}`, { params: { versionNumber } })
export const getPlanVersions = (id) => http.get(`/meal-plans/${id}/versions`)
export const adoptPlan = (id, versionNumber) => http.post(`/meal-plans/${id}/adopt`, { versionNumber })
export const setMealExecution = (mealId, state) => http.post(`/plan-meals/${mealId}/execution`, { state })
export const getDietPreference = () => http.get('/diet-preferences')
export const saveDietPreference = (payload) => http.put('/diet-preferences', payload)

export const createIngredientCheck = (payload) => http.post('/ingredient-checks', payload)
export const getIngredientCheck = (id) => http.get(`/ingredient-checks/${id}`)
export const answerIngredientCheck = (id, payload) => http.post(`/ingredient-checks/${id}/answers`, payload)
export const getKnownIngredients = () => http.get('/known-ingredients')
export const saveKnownIngredient = (payload) => (payload.id
  ? http.patch(`/known-ingredients/${payload.id}`, payload)
  : http.post('/known-ingredients', payload))

// M5 采购
export const buildShoppingList = (payload) => http.post('/shopping-lists', payload)
export const getShoppingList = (id) => http.get(`/shopping-lists/${id}`)
export const refreshShoppingPrices = (id) => http.post(`/shopping-lists/${id}/price-refresh`)
export const purchaseShoppingItem = (id, payload, requestId) => http.post(
  `/shopping-items/${id}/purchase`,
  payload,
  { headers: requestId ? { 'X-Request-Id': requestId } : undefined }
)
export const reversePurchase = (id, payload, requestId) => http.post(`/shopping-items/${id}/purchase-reversal`, payload, {
  headers: requestId ? { 'X-Request-Id': requestId } : undefined
})

// M6 实际饮食与调整
export const createObservation = (payload) => http.post('/meal-observations', payload)
export const getObservations = (params) => http.get('/meal-observations', { params })
export const resolveObservation = (id, dietRecordIds) => http.post(`/meal-observations/${id}/resolve`, { dietRecordIds })
export const retractObservation = (id) => http.post(`/meal-observations/${id}/retract`)
export const createPlanAdjustment = (planId, payload) => http.post(`/meal-plans/${planId}/adjustments`, payload)

// M7 复盘与记录完整度
export const generateReview = (payload) => http.post('/reviews', payload, { timeout: 30_000 })
export const getReviews = (limit = 20) => http.get('/reviews', { params: { limit } })
export const getReview = (id) => http.get(`/reviews/${id}`)
export const markDayStatus = (day, payload) => http.put(`/diet-day-status/${day}`, payload)
export const getDayStatuses = (startDate, endDate) => http.get('/diet-day-status', { params: { startDate, endDate } })
