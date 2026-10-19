import { createRouter, createWebHashHistory } from "vue-router"
import ChatView from "../views/ChatView.vue"
import BoardView from "../views/BoardView.vue"
import BillsView from "../views/BillsView.vue"
import ReportsView from "../views/ReportsView.vue"
import CalendarView from "../views/CalendarView.vue"
import SettingsView from "../views/SettingsView.vue"

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "chat", component: ChatView },
    { path: "/board", name: "board", component: BoardView },
    { path: "/bills", name: "bills", component: BillsView },
    { path: "/reports", name: "reports", component: ReportsView },
    { path: "/calendar", name: "calendar", component: CalendarView },
    { path: "/settings", name: "settings", component: SettingsView },
  ],
})

export default router
