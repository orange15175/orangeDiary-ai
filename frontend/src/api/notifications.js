import http from './http'
export const listNotifications = (page = 1, type) => http.get('/notifications', { params: { page, size: 10, type: type || undefined } })
export const unreadNotifications = () => http.get('/notifications/unread-count')
export const readNotification = (id) => http.put(`/notifications/${id}/read`)
