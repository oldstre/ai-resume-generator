import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    // 端口统一使用 39xxx 段，避开 Vite 默认的 5173 等常见端口
    host: '127.0.0.1',
    port: 39173,
    strictPort: true,
    // 走代理而非直连，前端代码里所有请求都用同源相对路径，避免 CORS 与环境变量分叉
    proxy: {
      '/api': {
        target: 'http://10.14.3.233:39801',
        changeOrigin: true,
      },
    },
  },
})
