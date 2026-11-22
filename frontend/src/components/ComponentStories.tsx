import { useState } from 'react'
import { Check, Plus } from 'lucide-react'
import { Button, Card, Empty, Loading, Modal, Table } from './index'
import './component-stories.css'

const rows = [
  { id: 'AG-1024', name: '知识库同步', status: '运行中', owner: '知识运营组' },
  { id: 'AG-1025', name: '客户意图识别', status: '已完成', owner: 'Agent Studio' },
  { id: 'AG-1026', name: '日报摘要生成', status: '等待中', owner: '运营助手' },
]

export function ComponentStories() {
  const [modalOpen, setModalOpen] = useState(false)
  const [density, setDensity] = useState<'comfortable' | 'standard' | 'compact'>('standard')
  return (
    <section className="ui-stories" aria-labelledby="ui-stories-title">
      <header className="ui-stories__heading"><div><span>COMPONENT LIBRARY / INTERACTIVE STORIES</span><h2 id="ui-stories-title">基础组件</h2><p>统一导出的组件契约与交互状态示例。</p></div><span className="ui-stories__version">UI Kit · 1.0</span></header>
      <div className="ui-stories__grid">
        <Card title="Button" description="语义化变体、尺寸、禁用与加载状态">
          <div className="ui-stories__row"><Button leadingIcon={<Plus size={15} />}>主要操作</Button><Button variant="secondary">次要操作</Button><Button variant="tertiary">轻量操作</Button><Button variant="danger">危险操作</Button><Button disabled>不可用</Button></div>
          <div className="ui-stories__row"><Button loading>提交中</Button><Button size="sm">紧凑按钮</Button><Button size="lg">大尺寸</Button></div>
        </Card>
        <Card title="Card" description="标题、说明、操作区与内边距规范"><div className="ui-stories__nested-card"><strong>Agent 运行概览</strong><span>最近 24 小时</span><b>1,284</b><small>静态组件展示样例</small></div></Card>
        <Card title="Table" description="类型安全列定义、密度切换、加载和空数据状态">
          <div className="ui-stories__density"><span>表格密度</span>{(['comfortable', 'standard', 'compact'] as const).map((value) => <Button key={value} size="sm" variant={density === value ? 'tertiary' : 'ghost'} aria-pressed={density === value} onClick={() => setDensity(value)}>{value === 'comfortable' ? '舒适' : value === 'standard' ? '标准' : '紧凑'}</Button>)}</div>
          <Table columns={[{ key: 'id', title: '标识', width: '100px' }, { key: 'name', title: '任务' }, { key: 'status', title: '状态' }, { key: 'owner', title: '负责方', align: 'end' }]} data={rows} rowKey={(row) => row.id} density={density} />
        </Card>
        <Card title="Modal" description="Escape 关闭、焦点循环、背景滚动锁定与遮罩关闭">
          <div className="ui-stories__row"><Button onClick={() => setModalOpen(true)}>打开对话框</Button><span className="ui-stories__hint">不会提交真实数据</span></div>
          <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="确认示例操作" description="本地交互演示，不会调用后端接口。" footer={<><Button variant="secondary" onClick={() => setModalOpen(false)}>取消</Button><Button onClick={() => setModalOpen(false)} leadingIcon={<Check size={15} />}>确认</Button></>}>此对话框展示 Modal 的语义结构、键盘行为和操作区。</Modal>
        </Card>
        <Card title="Empty" description="空数据说明与可选操作"><Empty compact title="还没有任务" description="创建任务后，执行状态会显示在这里。" action={<Button size="sm" variant="secondary">创建任务</Button>} /></Card>
        <Card title="Loading" description="统一加载反馈与无障碍状态播报"><Loading label="正在同步工作区数据" size="lg" /></Card>
      </div>
    </section>
  )
}
