# 前端 API Client 与事件总线（UI-G1-743-c）

本模块位于独立 `frontend/src/lib`，不依赖登录页面或本地身份模拟。默认 API 前缀为同源 `/api`，默认 credentials 为 `same-origin`；不要求 token，不创建匿名 principal。实例配置/auth 模式仍由服务端 bootstrap 决定，本模块不提供前端覆盖开关。

## API Client

入口：`@/lib/api`。

- `ApiClient` 统一 fetch、JSON 编码、request id、标准错误解析、超时与 AbortSignal 取消。
- `GET` 支持 ETag：缓存带 ETag 的响应；后续请求发送 `If-None-Match`；304 返回缓存表示。缓存只在当前客户端实例内存中、不持久化且最多保留 256 项；成功写请求会清空 validators。会话登录/退出或身份切换时调用 `apiClient.clearEtagCache()`，避免跨会话复用 304 表示。
- `POST/PATCH` 默认生成 `Idempotency-Key`；调用方可传入稳定 key，以便业务重试复用同一操作标识。不会自动重放非幂等请求。
- 写请求可传 `ifMatch` 发送 `If-Match` revision 前置条件；409/412 转为 `ApiConflictError`，保留 code/details/request_id，供 UI 显示 revision 冲突和刷新策略。
- `list(path, params)` 使用统一分页 query：`page`、`page_size`、`cursor`、`sort`、JSON `filters`。返回 `PageEnvelope<T>`：`data[]` 与 `pagination`（page/page_size/total/total_pages/next_cursor/has_more）。
- 错误 envelope：`code/message/details/request_id/retryable`。HTTP status 与响应 `x-request-id` 一并保留在 `ApiError`。
- 默认 15 秒超时；每次请求可覆盖 `timeoutMs`，并可传标准 AbortSignal。

示例：

```ts
const page = await apiClient.list<Task>('/tasks', {
  page: 1,
  page_size: 25,
  sort: '-created_at',
  filters: { status: 'running' },
})
```

TanStack Query 是服务端状态唯一缓存层，queryFn 应调用统一 `apiClient` 并将 Query 的 signal 传给 client：

```ts
useQuery({
  queryKey: ['tasks', filters],
  queryFn: ({ signal }) => apiClient.list<Task>('/tasks', filters, { signal }),
})
```

## TanStack Query 与 Zustand 边界

- `queryClient` 集中配置 stale/gc/retry；可通过 `QueryClientProvider` 在应用根部访问。
- API/Query 缓存是服务端实体的唯一事实来源；Zustand `useUiStore` 仅保存侧栏折叠、命令面板开关等客户端短生命周期状态。
- 不在 Zustand 镜像任务、Agent、Run、审批等服务端实体；可分享的分页/筛选/实体 ID 应落 URL，而非全局 store。
- 默认不重试 mutation；查询重试最多两次，并尊重 `ApiError.retryable`。

## SSE Event Bus

入口：`@/lib/events/event-bus`。连接由业务功能显式创建/启动，不在 app bootstrap 中自动连接，因此 auth=false 不会因为缺 token 而阻断启动。

事件 envelope：

```ts
interface PlatformEvent {
  event_id: string
  type: string
  entity_id: string
  version: number
  occurred_at: string
  sequence: number
  payload: unknown
}
```

- 采用 Fetch + ReadableStream SSE，支持同源 credentials 和可选请求 headers。
- 心跳：SSE comment（`: heartbeat`）或 `event: heartbeat` 刷新心跳 watchdog；默认 45 秒无心跳则断开并重连。
- 重连：指数退避（1 秒起、30 秒封顶），支持 SSE `retry:` 覆盖；通过 `Last-Event-ID` 发送最后游标。
- 去重：按 event_id 有界去重；同 entity 的 sequence 不递增事件被丢弃。事件游标仅在合法事件派发后推进。
- 断开/stop 会取消当前 fetch/reader 和等待中的重连 timer；订阅返回 unsubscribe。
- Event Bus 只解码、校验、去重、分发，不保存业务实体状态。

Query Cache 校准：`connectQueryEvents(bus, queryClient, routes)` 将事件类型映射到 Query Key。默认触发精准 invalidateQueries；只有事件 payload 足以安全更新当前查询表示时，才注册 `update` 并通过 setQueryData 局部更新。不要把实体复制到 Zustand。

## 自测与边界

Vitest 覆盖分页参数序列化、ETag/304 缓存、幂等键、revision 冲突、SSE 解码、event_id/sequence 去重及 Last-Event-ID 续传。CI 执行 `pnpm test`，再运行 lint、typecheck 和 Vite production build。真实后端契约尚未由本任务虚构；实际分页字段、SSE 鉴权头/端点由后端契约确认后注入配置。
