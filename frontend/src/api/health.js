import http from './http'

export const getExercises = () => http.get('/exercises')
export const getExerciseCategories = () => http.get('/exercises/categories')
export const browseExercises = (params) => http.get('/exercises/browse', { params })
export const getExerciseRecords = (date) => http.get('/exercise-records', { params: { date } })
export const previewExercise = (payload) => http.post('/exercise-records/preview', payload)
export const createExercise = (payload, requestId) => http.post('/exercise-records', payload, {
  headers: { 'X-Request-Id': requestId }
})
export const deleteExercise = (id) => http.delete(`/exercise-records/${id}`)
export const getWeightRecords = (date) => http.get('/weight-records', { params: { date } })
// 曲线图需要多天数据，和原有的单日记录接口分开。
export const getWeightTrend = (startDate, endDate) => http.get('/weight-records/trend', { params: { startDate, endDate } })
export const createWeight = (payload, requestId) => http.post('/weight-records', payload, {
  headers: { 'X-Request-Id': requestId }
})
export const deleteWeight = (id) => http.delete(`/weight-records/${id}`)
