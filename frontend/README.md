# Frontend workspace

独立的 React 18 + TypeScript + Vite 应用。此目录不覆盖或复用旧版 `platform-ui/`，旧入口保持原样。

## Toolchain

- Node.js: `20.19.0` baseline（`.nvmrc`）；package engines 要求 Node.js 20.19+
- pnpm: `9.15.9`（`packageManager` 与 CI action 锁定）
- React / React DOM: `18.3.1`
- TanStack Query: `5.83.0`（唯一服务端状态缓存）
- Zustand: `5.0.6`（客户端 UI 状态）
- TypeScript: strict mode；`@/*` 指向 `src/*`

## Commands

```sh
corepack enable
pnpm install
pnpm dev
pnpm test
pnpm lint
pnpm typecheck
pnpm build
```

## API and events

- API Client: `src/lib/api`，统一分页、错误 envelope、request_id、超时/取消、ETag、If-Match、幂等键。
- SSE Event Bus: `src/lib/events`，心跳 watchdog、Last-Event-ID 续传、指数退避、event_id 与 entity sequence 去重。
- Query Client: `src/lib/query/query-client.ts`。
- Zustand UI store: `src/stores/ui-store.ts`；禁止在其中复制服务端业务实体。
- Contract and usage: `../docs/frontend/api-client-event-bus.md`。

CI workflow `.github/workflows/frontend-ci.yml` 对本目录变更执行 install、unit tests、lint/format、typecheck 和 production build。
