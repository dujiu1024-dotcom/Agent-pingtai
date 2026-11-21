import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { EntityDetailPage } from '@/components/EntityDetailPage'
import { apiClient } from '@/lib/api/api-client'
import { displayValue, fieldLabel, localizedJson } from '@/lib/i18n/labels'

interface AuditEvent {
  audit_id?: string
  event_id?: string
  id?: string
  [key: string]: unknown
}

export function AuditEventDetailPage() {
  const { eventId = '' } = useParams()
  const list = useQuery({
    queryKey: ['audit-events-for-detail'],
    queryFn: () =>
      apiClient.get<{ items?: AuditEvent[] }>(
        '/platform/v1/audit/identity-events?limit=500',
      ),
    retry: false,
  })
  const record = (list.data?.items ?? []).find(
    (item) =>
      String(item.audit_id ?? item.event_id ?? item.id ?? '') === eventId,
  )

  if (record) {
    return (
      <section className="page-content">
        <div className="page-heading">
          <p className="eyebrow">审计</p>
          <h1>审计事件</h1>
          <p>事件 ID：{eventId}</p>
        </div>
        <dl className="detail-fields">
          {Object.entries(record).map(([key, value]) => (
            <div key={key} className="detail-field">
              <dt>{fieldLabel(key)}</dt>
              <dd>
                {typeof value === 'object' && value !== null ? (
                  <pre className="detail-json">{localizedJson(value)}</pre>
                ) : (
                  displayValue(key, value)
                )}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    )
  }

  return (
    <EntityDetailPage
      title="审计事件"
      description="按事件 ID 在最近 500 条身份审计事件中定位。"
      apiPath="/platform/v1/audit/identity-events?limit=500"
    />
  )
}
