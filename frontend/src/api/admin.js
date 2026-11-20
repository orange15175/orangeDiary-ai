import http from './http'
export const listAdminPosts = (params) => http.get('/admin/posts', { params })
export const getAdminPost = (id) => http.get(`/admin/posts/${id}`)
export const listAdminComments = (id, page = 1) => http.get(`/admin/posts/${id}/comments`, { params: { page, size: 10 } })

export const hideAdminPost = (id, reason) => http.put(`/admin/posts/${id}/hide`, { reason })
export const restoreAdminPost = (id, reason) => http.put(`/admin/posts/${id}/restore`, { reason })
export const removeAdminComment = (id, commentId, reason) => http.put(`/admin/posts/${id}/comments/${commentId}/remove`, { reason })
export const listActionLogs = (params) => http.get('/admin/action-logs', { params })
export const listCommunityReports = (params) => http.get('/admin/community-reports', { params })
export const resolveCommunityReport = (id, resolution, reason) => http.put(`/admin/community-reports/${id}`, { resolution, reason })

export const listAiTraces = (params) => http.get('/admin/ai/traces', { params })
export const getAiTrace = (traceId) => http.get(`/admin/ai/traces/${traceId}`)
export const getRagStats = () => http.get('/admin/ai/rag-stats')
