import { createRouter, createWebHashHistory } from "vue-router"
import ChatView from "../views/ChatView.vue"
import BoardView from "../views/BoardView.vue"
import BillsView from "../views/BillsView.vue"
import ReportsView from "../views/ReportsView.vue"
import CalendarView from "../views/CalendarView.vue"
import SettingsView from "../views/SettingsView.vue"
import KnowledgeView from "../views/KnowledgeView.vue"
import LoginView from "../views/LoginView.vue"
import { isLoggedIn } from "../services/agentClient"

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/login", name: "login", component: LoginView, meta: { public: true } },
    { path: "/", name: "chat", component: ChatView },
    { path: "/board", name: "board", component: BoardView },
    {
      path: "/personal", component: () => import("../views/PersonalView.vue"),
      children: [
        { path: "", redirect: { name: "personal-tasks" } },
        { path: "tasks", name: "personal-tasks", component: () => import("../views/PersonalTasks.vue") },
        { path: "goals", name: "personal-goals", component: () => import("../views/PersonalGoals.vue") },
        { path: "reviews", name: "personal-reviews", component: () => import("../views/PersonalReviews.vue") },
      ],
    },
    { path: "/bills", name: "bills", component: BillsView },
    { path: "/reports", name: "reports", component: ReportsView },
    { path: "/calendar", name: "calendar", component: CalendarView },
    { path: "/knowledge", name: "knowledge", component: KnowledgeView },
    { path: "/settings", name: "settings", component: SettingsView },
  ],
})

// 全局守卫：未登录一律跳登录页（登录页本身放行）。
router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!isLoggedIn()) return { name: "login" }
  return true
})

export default router
