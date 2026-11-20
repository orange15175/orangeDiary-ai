import { defineStore } from 'pinia'
import { getCurrentUser, login, logout, register } from '../api/auth'
import { authStorage } from '../api/http'

export const useAuthStore = defineStore('auth', {
  state: () => ({ token: authStorage.token(), user: null }),
  actions: {
    async signIn(account, password) {
      this.user = await login(account, password)
      this.token = authStorage.token()
      return this.user
    },
    async signUp(payload) {
      await register(payload)
      return this.signIn(payload.email, payload.password)
    },
    async loadUser() {
      if (!this.token) return null
      this.user = await getCurrentUser()
      return this.user
    },
    async signOut() {
      await logout()
      this.token = null
      this.user = null
    }
  }
})
