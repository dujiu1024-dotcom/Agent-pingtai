import { Link } from 'react-router-dom'

const areas = [
  {
    title: '知识库与 RAG',
    description: '知识集合、文档、Chunk、索引状态、检索测试与引用证据。',
    path: '/operations/knowledge-bases',
    related: [{ label: '知识图谱', path: '/operations/knowledge-graph' }, { label: 'RAG 实验台', path: '/operations/rag-playground' }],
    owner: '#760',
  },
  {
    title: '记忆治理',
    description: '候选与已确认记忆、版本、来源和归档操作。',
    path: '/operations/memories',
    related: [],
    owner: '#761',
  },
  {
    title: '会话与消息',
    description: '会话列表、消息记录、项目归属及会话详情。',
    path: '/operations/conversations',
    related: [],
    owner: '#762',
  },
  {
    title: '文件与产物',
    description: '文件元数据、版本、会话/项目关联及受控下载。',
    path: '/operations/files',
    related: [],
    owner: '#762',
  },
  {
    title: '审批中心',
    description: '审批请求、受控决策、撤销与审批详情。',
    path: '/operations/approvals',
    related: [],
    owner: '#763',
  },
  {
    title: '评测数据与运行',
    description: '数据集版本、样本、评测运行、结果与评测洞察。',
    path: '/operations/evaluation/datasets',
    related: [{ label: '评测运行', path: '/operations/evaluation/runs' }, { label: '评测洞察', path: '/operations/evaluation/insights' }],
    owner: '#763',
  },
]

const journeys = [
  {
    title: '知识证据链',
    steps: ['知识集合', '文档详情', 'Chunk / 检索命中', '引用来源'],
    note: '从知识库入口进入集合与文档详情；检索命中与引用定位由 Knowledge 页面提供。',
    path: '/operations/knowledge-bases',
  },
  {
    title: '会话与附件链',
    steps: ['会话列表', '会话详情 / 消息', '关联文件', '文件详情 / 版本'],
    note: '会话与文件页面分别读取服务端资源；跨实体权限和下载授权仍以服务端响应为准。',
    path: '/operations/conversations',
  },
  {
    title: '审批与评测链',
    steps: ['审批请求', '审批详情 / 决策', '数据集版本', '评测运行 / 结果'],
    note: '审批决策调用受控 API；评测详情保留在评测数据集与运行页面，不复制质量聚合指标。',
    path: '/operations/approvals',
  },
]

export function BusinessManagementHub() {
  return (
    <section className="page-content">
      <div className="page-heading">
        <p className="eyebrow">业务管理 / 统一入口</p>
        <h1>业务管理中心</h1>
        <p>统一进入知识、记忆、会话、文件、审批与评测业务域。实体 CRUD 和详情由各自唯一归属页面负责，本页只提供导航与跨域路径，不复制数据或权限逻辑。</p>
      </div>

      <div className="generic-table-wrap">
        <table className="generic-table">
          <thead><tr><th>业务域</th><th>管理范围</th><th>实现归属</th><th>入口</th></tr></thead>
          <tbody>
            {areas.map((area) => (
              <tr key={area.path}>
                <td><strong>{area.title}</strong></td>
                <td>{area.description}</td>
                <td>{area.owner}</td>
                <td><Link className="text-link" to={area.path}>打开管理页面</Link>{area.related.map((item) => <span key={item.path}> · <Link className="text-link" to={item.path}>{item.label}</Link></span>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="detail-section-title">跨域引用路径</h2>
      <div className="generic-table-wrap">
        <table className="generic-table">
          <thead><tr><th>业务旅程</th><th>实体穿透顺序</th><th>说明</th><th>起点</th></tr></thead>
          <tbody>
            {journeys.map((journey) => (
              <tr key={journey.title}>
                <td><strong>{journey.title}</strong></td>
                <td>{journey.steps.join(' → ')}</td>
                <td>{journey.note}</td>
                <td><Link className="text-link" to={journey.path}>进入</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="detail-empty">安全边界：内容 ACL、记忆敏感字段、会话消息及文件下载均由后端鉴权；本入口不预取业务全文、不持久化敏感内容，也不生成跨域授权结论。缺失的详情或 API 能力由对应子 Issue 跟踪。</p>
    </section>
  )
}
