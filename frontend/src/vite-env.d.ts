/// <reference types="vite/client" />
interface ImportMetaEnv {
  readonly VITE_GLOBAL_SEARCH_URL?: string
  readonly VITE_NOTIFICATION_SSE_URL?: string
}
interface ImportMeta { readonly env: ImportMetaEnv }
