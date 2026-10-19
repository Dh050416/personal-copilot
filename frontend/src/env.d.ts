/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端地址；空字符串表示与前端同源（走 vite 的 /api 代理） */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
