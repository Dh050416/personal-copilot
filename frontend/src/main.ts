import { createApp } from "vue"
import { createPinia } from "pinia"
import App from "./App.vue"
import router from "./router"
import { setupTools } from "./tools/registry"
import "./styles/theme.css"

setupTools()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount("#app")
