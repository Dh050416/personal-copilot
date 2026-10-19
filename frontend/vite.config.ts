import { defineConfig, loadEnv } from "vite"
import vue from "@vitejs/plugin-vue"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  // 后端地址。留空则由前端直连 http://127.0.0.1:8000（默认行为）；
  // 设置后 /api 会代理到该地址，配合 VITE_API_BASE="" 即可同源部署。
  const backend = env.VITE_BACKEND_TARGET || "http://127.0.0.1:8000"
  return {
    plugins: [vue()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: backend,
          changeOrigin: true,
          // SSE（/api/chat 与 resume 走 text/event-stream）不能被缓冲，
          // 否则流式 token 会攒到最后一次性到达。
          ws: false,
          configure: (proxy) => {
            proxy.on("proxyRes", (proxyRes) => {
              const type = String(proxyRes.headers["content-type"] || "")
              if (type.includes("text/event-stream")) {
                proxyRes.headers["cache-control"] = "no-cache, no-transform"
                proxyRes.headers["connection"] = "keep-alive"
                proxyRes.headers["x-accel-buffering"] = "no"
              }
            })
          },
        },
      },
    },
  }
})
