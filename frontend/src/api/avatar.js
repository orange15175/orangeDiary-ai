import http from './http'
export function uploadAvatar(file) { const data = new FormData(); data.append('file', file, 'avatar.jpg'); return http.post('/avatars', data) }
export const fetchAvatar = (url) => http.get(url.replace(/^\/api\//, '/'), { responseType: 'blob' })
