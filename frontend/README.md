# Frontend workspace

独立的 React 18 + TypeScript + Vite 应用。此目录不覆盖或复用旧版 `platform-ui/`，旧入口保持原样。

## Toolchain

- Node.js: `20.19.0` baseline（`.nvmrc`）；package engines 要求 Node.js 20.19+
- pnpm: `9.15.9`（`packageManager` 与 CI action 锁定）
- React / React DOM: `18.3.1`
- TypeScript: strict mode；`@/*` 指向 `src/*`

## Commands

```sh
corepack enable
pnpm install
pnpm dev
pnpm lint
pnpm typecheck
pnpm build
```

CI workflow `.github/workflows/frontend-ci.yml` 对本目录变更执行 install、lint/format、typecheck 和 production build。
