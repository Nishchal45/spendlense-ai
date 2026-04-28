/// <reference types="vite/client" />

// Vite injects ``import.meta.env`` at build time. The default
// ``ImportMetaEnv`` knows about ``MODE``/``BASE_URL``; we extend it
// with the SpendLens-specific keys so misspellings ("VITE_API_URL")
// fail at type-check, not in production.
interface ImportMetaEnv {
  /** Override for the API base URL. Defaults to ``/api/v1`` (proxied). */
  readonly VITE_API_BASE_URL?: string;
  /** Dev-only: where the vite proxy forwards ``/api/*``. */
  readonly VITE_API_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
