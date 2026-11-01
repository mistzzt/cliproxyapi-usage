# cliproxy-usage frontend

The React SPA for the usage dashboard (`/usage`) and limits page (`/limits`). See the root `README.md` for running and deploying the webapp, and `AGENTS.md` for the frontend architecture and conventions.

```sh
bun install
bun run dev    # Vite on :5173, proxies /api to the backend on :8318
bun test       # bun:test suites colocated with src/utils/usage/*
bun run lint
bun run build  # tsc -b, then the production build into dist/
```
