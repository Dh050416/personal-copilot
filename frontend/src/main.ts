import { createApp } from "vue"
import { createPinia } from "pinia"
import App from "./App.vue"
import router from "./router"
import { setupTools } from "./tools/registry"
import { setOnUnauthorized } from "./services/agentClient"
import "./styles/theme.css"

setupTools()

// 401 未登录：跳登录页（登录页是 hash 路由 /#/login）
setOnUnauthorized(() => {
  if (router.currentRoute.value.name !== "login") {
    router.replace({ name: "login" })
  }
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount("#app")
