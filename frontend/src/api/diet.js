import http from './http'

export const getDietRecords = (date) => http.get('/diet-records', { params: { date } })
export const getDailySummary = (date) => http.get('/daily-summaries', { params: { date } })
export const getWeightProgress = () => http.get('/weight-records/progress')
export const deleteDietRecord = (id) => http.delete(`/diet-records/${id}`)
export const createDietRecord = (payload, requestId) => http.post('/diet-records', payload, {
  headers: { 'X-Request-Id': requestId }
})
