import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 单进程开发环境由 Python 8000 承载业务；生产使用 Nginx 同源 /api 路由。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || process.env.VITE_JAVA_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})
