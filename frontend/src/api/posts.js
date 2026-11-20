import http from './http'

export const listPosts = (page = 1, size = 10) => http.get('/posts', { params: { page, size } })
export const listMyPosts = (page = 1, size = 10) => http.get('/posts/mine', { params: { page, size } })
export const createPost = (payload, requestId) => http.post('/posts', payload, { timeout: 30_000, headers: { 'X-Request-Id': requestId } })
export const deletePost = (id) => http.delete(`/posts/${id}`)
export const likePost = (id) => http.put(`/posts/${id}/like`)
export const unlikePost = (id) => http.delete(`/posts/${id}/like`)
export const listPostComments = (id, page = 1, size = 10) => http.get(`/posts/${id}/comments`, { params: { page, size } })
export const createPostComment = (id, content) => http.post(`/posts/${id}/comments`, { content })
export const deletePostComment = (postId, commentId) => http.delete(`/posts/${postId}/comments/${commentId}`)

export function uploadPostImage(file, onUploadProgress) {
  const formData = new FormData()
  formData.append('file', file)
  return http.post('/files/images', formData, { timeout: 90_000, onUploadProgress })
}

export const loadPostImage = (url) => http.get(url.replace(/^\/api\//, '/'), { responseType: 'blob' })

export const getPost = (id) => http.get(`/posts/${id}`)
export const getPostComment = (id, commentId) => http.get(`/posts/${id}/comments/${commentId}`)
export const reportPost = (id, payload) => http.post(`/posts/${id}/reports`, payload)
export const reportPostComment = (postId, commentId, payload) => http.post(`/posts/${postId}/comments/${commentId}/reports`, payload)
