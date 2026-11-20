import http, { authStorage } from './http'

export async function login(account, password) {
  const tokens = await http.post('/auth/login', { account, password })
  authStorage.save(tokens)
  return http.get('/users/me')
}

export const getCurrentUser = () => http.get('/users/me')
export const getActiveUserCount = () => http.get('/users/count')

export const register = (payload) => http.post('/auth/register', payload)

export async function logout() {
  const refreshToken = localStorage.getItem('refreshToken')
  try {
    if (refreshToken) await http.post('/auth/logout', { refreshToken })
  } finally {
    // Clearing local credentials is still required when the server is unavailable.
    authStorage.clear()
  }
}
