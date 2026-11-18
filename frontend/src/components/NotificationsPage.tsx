import { EmptyState } from '@/components/States'

export function NotificationsPage() {
  return (
    <section className="global-tool-page">
      <div className="page-heading">
        <p className="eyebrow">GLOBAL CAPABILITY</p>
        <h1>通知中心</h1>
        <p>通知列表、已读状态与 Task/Run/Approval 穿透依赖服务端通知契约。</p>
      </div>
      <EmptyState
        title="通知 API 尚未接入"
        description="当前仓库尚未证实通知列表与投递端点；此处不展示伪造通知，也不会建立未经契约确认的 SSE 连接。"
      />
    </section>
  )
}
