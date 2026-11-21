import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { EntityDetailPage } from '@/components/EntityDetailPage'
import { apiClient } from '@/lib/api/api-client'

export function ApprovalDetailPage() {
  const { approvalId = '' } = useParams()
  const queryClient = useQueryClient()
  const detailKey = ['entity-detail', `/platform/v1/approvals/${approvalId}`]
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () =>
      apiClient.get<{ status?: string }>(
        `/platform/v1/approvals/${approvalId}`,
      ),
    retry: false,
  })
  const decision = useMutation({
    mutationFn: (approved: boolean) =>
      apiClient.post(`/platform/v1/approvals/${approvalId}/decision`, {
        approved,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: detailKey })
      await queryClient.invalidateQueries({ queryKey: ['generic-list'] })
    },
  })

  const pending = detail.data?.status === 'pending'

  return (
    <EntityDetailPage
      title="审批详情"
      description="审批决定会记录到审计事件，并驱动对应 Run 继续或终止。"
      apiPath={`/platform/v1/approvals/${approvalId}`}
      actions={
        pending ? (
          <div className="detail-actions">
            <button
              className="button-primary"
              type="button"
              disabled={decision.isPending}
              onClick={() => decision.mutate(true)}
            >
              通过
            </button>
            <button
              className="button-secondary"
              type="button"
              disabled={decision.isPending}
              onClick={() => decision.mutate(false)}
            >
              拒绝
            </button>
            {decision.isError && (
              <span className="auth-error" role="alert">
                {decision.error instanceof Error
                  ? decision.error.message
                  : '决定提交失败'}
              </span>
            )}
          </div>
        ) : null
      }
    />
  )
}
