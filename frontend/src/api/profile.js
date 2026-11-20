import http from './http'

export const saveProfile = (payload) => http.put('/users/profile', payload)
export const saveGoal = (payload) => http.post('/users/goals', payload)
