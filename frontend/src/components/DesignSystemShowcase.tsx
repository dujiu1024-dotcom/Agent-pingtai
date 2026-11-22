import { useState } from 'react'

import './design-system.css'
import { ComponentStories } from './ComponentStories'

const brandColors = [
  ['Brand 50', '#EFF4FF'],
  ['Brand 100', '#DCE7FF'],
  ['Brand 200', '#B9CEFF'],
  ['Brand 500', '#4778F5'],
  ['Brand 600', '#356DF3'],
  ['Brand 700', '#2456D8'],
]

const semanticColors = [
  { name: 'Success', value: '#16845B', className: 'success' },
  { name: 'Warning', value: '#A96508', className: 'warning' },
  { name: 'Danger', value: '#C43D4B', className: 'danger' },
  { name: 'Info', value: '#2864C8', className: 'info' },
]

const chartColors = [
  '#4778F5',
  '#1C9A91',
  '#8666D5',
  '#D9942B',
  '#D96878',
  '#4B9A68',
  '#399AC4',
  '#77849A',
]

const sampleRows = [
  { name: '知识库同步', status: '运行中', owner: '知识运营组' },
  { name: '客户意图识别', status: '已完成', owner: 'Agent Studio' },
  { name: '日报摘要生成', status: '等待中', owner: '运营助手' },
]

export default function DesignSystemShowcase() {
  const [density, setDensity] = useState<
    'comfortable' | 'standard' | 'compact'
  >('standard')

  return (
    <section className="ds-showcase" aria-labelledby="design-system-title">
      <div className="ds-heading">
        <div>
          <p className="ds-kicker">FOUNDATION / UI KIT</p>
          <h2 id="design-system-title">设计系统样例</h2>
          <p>品牌、状态、数据密度与 Office 材质的可运行组件参考。</p>
        </div>
        <span className="ds-version">Design tokens · v1</span>
      </div>

      <div className="ds-grid">
        <article className="ds-panel">
          <div className="ds-panel-heading">
            <h3>品牌色</h3>
            <span>Primary</span>
          </div>
          <div className="ds-swatches">
            {brandColors.map(([name, value]) => (
              <div className="ds-swatch" key={name}>
                <span
                  className="ds-swatch-color"
                  style={{ backgroundColor: value }}
                />
                <strong>{name}</strong>
                <code>{value}</code>
              </div>
            ))}
          </div>
        </article>

        <article className="ds-panel">
          <div className="ds-panel-heading">
            <h3>语义状态</h3>
            <span>Status</span>
          </div>
          <div className="ds-status-list">
            {semanticColors.map((color) => (
              <span
                className={`ds-status ds-status--${color.className}`}
                key={color.name}
              >
                <i aria-hidden="true" />
                {color.name}
                <code>{color.value}</code>
              </span>
            ))}
          </div>
        </article>

        <article className="ds-panel ds-panel--wide">
          <div className="ds-panel-heading">
            <h3>交互状态</h3>
            <span>Buttons</span>
          </div>
          <div className="ds-button-row">
            <button className="ds-button ds-button--primary" type="button">
              主要操作
            </button>
            <button className="ds-button ds-button--secondary" type="button">
              次要操作
            </button>
            <button className="ds-button ds-button--danger" type="button">
              危险操作
            </button>
            <button
              className="ds-button ds-button--primary"
              disabled
              type="button"
            >
              禁用状态
            </button>
          </div>
          <p className="ds-helper">
            使用 Tab 键聚焦按钮可查看可见焦点环；禁用按钮不可触发操作。
          </p>
        </article>

        <article className="ds-panel ds-panel--wide">
          <div className="ds-panel-heading ds-density-heading">
            <div>
              <h3>表格密度</h3>
              <span>Density</span>
            </div>
            <div className="ds-density-switch" aria-label="选择表格密度">
              {(['comfortable', 'standard', 'compact'] as const).map(
                (value) => (
                  <button
                    aria-pressed={density === value}
                    className={density === value ? 'is-selected' : ''}
                    key={value}
                    onClick={() => setDensity(value)}
                    type="button"
                  >
                    {value === 'comfortable'
                      ? '舒适'
                      : value === 'standard'
                        ? '标准'
                        : '紧凑'}
                  </button>
                ),
              )}
            </div>
          </div>
          <div className={`ds-table-wrap ds-table-wrap--${density}`}>
            <table className="ds-table">
              <thead>
                <tr>
                  <th scope="col">任务</th>
                  <th scope="col">状态</th>
                  <th scope="col">负责方</th>
                </tr>
              </thead>
              <tbody>
                {sampleRows.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{row.status}</td>
                    <td>{row.owner}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="ds-panel">
          <div className="ds-panel-heading">
            <h3>图表色板</h3>
            <span>Series 1–8</span>
          </div>
          <div className="ds-chart-bars" aria-label="八组图表分类颜色示例">
            {chartColors.map((color, index) => (
              <span
                aria-label={`系列 ${index + 1}`}
                key={color}
                style={{ backgroundColor: color, height: `${35 + index * 7}%` }}
              />
            ))}
          </div>
          <div className="ds-chart-legend">
            {chartColors.map((color, index) => (
              <span key={color}>
                <i style={{ backgroundColor: color }} />
                {index + 1}
              </span>
            ))}
          </div>
        </article>

        <article className="ds-panel ds-panel--office">
          <div className="ds-panel-heading">
            <h3>Office 材质基调</h3>
            <span>Low-poly direction</span>
          </div>
          <div
            className="ds-office-preview"
            aria-label="暖白墙面、木质办公桌与植物的材质示意"
          >
            <div className="ds-office-window" />
            <div className="ds-office-plant">
              <i />
              <i />
              <i />
            </div>
            <div className="ds-office-desk" />
            <div className="ds-office-monitor" />
            <div className="ds-office-person">
              <i />
            </div>
          </div>
          <p className="ds-helper">
            暖白建筑、自然木色、墨绿植物、柔和漫射光；实际 3D 场景须提供等价 2D
            操作视图。
          </p>
        </article>
      </div>
      <ComponentStories />
    </section>
  )
}
