import axios from 'axios'

const KEYS = ['token', 'tokenExpiresIn', 'tokenExpiresAt', 'refreshToken', 'refreshTokenExpiresIn', 'refreshTokenExpiresAt']
let refreshPromise = null

export const authStorage = {
  token: () => localStorage.getItem('token'),
  save(tokens) {
    if (!tokens?.token) return
    localStorage.setItem('token', tokens.token)
    localStorage.setItem('tokenExpiresIn', String(tokens.expiresIn || ''))
    localStorage.setItem('tokenExpiresAt', String(Date.now() + Number(tokens.expiresIn || 0) * 1000))
    if (tokens.refreshToken) {
      localStorage.setItem('refreshToken', tokens.refreshToken)
      localStorage.setItem('refreshTokenExpiresIn', String(tokens.refreshExpiresIn || ''))
      localStorage.setItem('refreshTokenExpiresAt', String(Date.now() + Number(tokens.refreshExpiresIn || 0) * 1000))
    }
  },
  clear() { KEYS.forEach((key) => localStorage.removeItem(key)) }
}

const http = axios.create({ baseURL: '/api', timeout: 12_000 })

// 保留诊断字段给页面使用；页面仍只展示后端提供的安全提示，不展示堆栈或 SQL。
function createRequestError({ message, status, code, traceId, cause }) {
  // 用户反馈时只需提供短编号，完整编号仍保留在 error.traceId 便于排查。
  const shortTraceId = traceId?.slice(0, 8)
  const displayMessage = shortTraceId
    ? `${message || '请求失败'}（追踪编号：${shortTraceId}）`
    : (message || '请求失败')
  const error = new Error(displayMessage)
  error.status = status
  error.code = code
  error.traceId = traceId
  error.cause = cause
  return error
}

function responseTraceId(response) {
  // 优先读统一 JSON 字段；非 JSON 响应可退回读取 HTTP 响应头。
  return response?.data?.traceId || response?.headers?.['x-trace-id']
}

async function errorBody(response) {
  if (response?.data instanceof Blob) {
    try { return JSON.parse(await response.data.text()) } catch { return {} }
  }
  return response?.data || {}
}

function binaryError(response, body) {
  return createRequestError({
    message: '操作失败，请稍后重试',
    status: response?.status,
    code: body?.code,
    traceId: body?.traceId || response?.headers?.['x-trace-id']
  })
}

function expireSession() {
  authStorage.clear()
  if (location.hash !== '#/login') location.hash = '#/login'
}

async function refreshAccessToken() {
  if (refreshPromise) return refreshPromise
  const refreshToken = localStorage.getItem('refreshToken')
  if (!refreshToken) throw new Error('登录已过期，请重新登录')
  refreshPromise = axios.post('/api/auth/refresh', { refreshToken })
    .then(({ data }) => {
      if (data?.code !== 0 || !data?.data?.token) throw new Error(data?.message || '登录刷新失败')
      authStorage.save(data.data)
      return data.data.token
    })
    .finally(() => { refreshPromise = null })
  return refreshPromise
}

/**
 * 给 fetch 用的令牌：流式接口走原生 fetch（要拿增量流），无法复用 axios 拦截器，
 * 这里补一份同样的"快过期就先刷新"逻辑，避免流式请求固定 401。
 */
export async function ensureAccessToken() {
  const expiresAt = Number(localStorage.getItem('tokenExpiresAt') || 0)
  let token = authStorage.token()
  if (token && expiresAt > 0 && Date.now() >= expiresAt - 30_000) {
    try {
      token = await refreshAccessToken()
    } catch {
      // 刷新失败就带旧令牌，让后端返回 401，由页面统一跳登录
    }
  }
  return token
}

http.interceptors.request.use(async (config) => {
  if (!config.url?.startsWith('/auth/')) {
    const expiresAt = Number(localStorage.getItem('tokenExpiresAt') || 0)
    let token = authStorage.token()
    if (token && expiresAt > 0 && Date.now() >= expiresAt - 30_000) token = await refreshAccessToken()
    if (token) config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  async (response) => {
    if (response.config.responseType === 'blob') {
      const type = response.headers?.['content-type'] || ''
      const signature = response.data instanceof Blob ? await response.data.slice(0, 5).text() : ''
      if (type.split(';')[0].trim().toLowerCase() === 'application/pdf' && signature === '%PDF-') return response
      if (type.toLowerCase().startsWith('image/') && response.data instanceof Blob) return response.data
      return Promise.reject(binaryError(response, await errorBody(response)))
    }
    if (response.data?.code === 0) return response.data.data
    return Promise.reject(createRequestError({
      message: response.data?.message,
      status: response.status,
      code: response.data?.code,
      traceId: responseTraceId(response)
    }))
  },
  async (error) => {
    const request = error.config
    if (error.response?.status === 401 && !request?._retried && !request?.url?.startsWith('/auth/')) {
      // Token 失效时只刷新并重放一次原请求，避免无限重试。
      request._retried = true
      try {
        request.headers.Authorization = `Bearer ${await refreshAccessToken()}`
        return http(request)
      } catch {
        expireSession()
      }
    }
    if (error.response?.status === 401) expireSession()
    if (request?.responseType === 'blob') {
      return Promise.reject(binaryError(error.response, await errorBody(error.response)))
    }
    return Promise.reject(createRequestError({
      message: error.response?.data?.message || error.message || '网络连接失败',
      status: error.response?.status,
      code: error.response?.data?.code,
      traceId: responseTraceId(error.response),
      cause: error
    }))
  }
)

export default http
