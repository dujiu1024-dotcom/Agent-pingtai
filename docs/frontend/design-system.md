# 前端设计系统（UI-G1-743-b）

> 状态：实现基线（可执行 Token）；适用范围：独立 `frontend/` React 应用。  
> 视觉参考：`../../示意图/首页示意图.png`、`../../示意图/3D办公室示意图.jpeg`。参考图中的文字、数字仅作构图参考，不构成业务字段或指标定义。

## 1. 设计原则与主题

- **清晰优先**：企业 SaaS 后台以浅色、低噪声、明确层级为默认；信息密度高但不拥挤。
- **蓝色作为品牌锚点**：主操作、当前导航、链接和交互反馈统一使用品牌蓝；语义状态色只表达状态，不替代品牌色。
- **克制的表面语言**：白色卡片、浅灰画布、细边框；圆角以 6–12px 为主，避免大面积胶囊和重阴影。
- **两套视觉主题、一套语义**：Dashboard 是浅色企业后台；Office 主题采用暖白建筑、自然木色、墨绿植物和低多边形半写实角色。Office 可以深色沉浸，但成功/警告/错误等状态含义与 Dashboard 完全一致。
- **可访问性优先**：所有状态同时提供文字/图标线索；焦点可见；禁用态不只依赖透明度；支持键盘和减少动态效果。

## 2. 色彩 Token

### 2.1 品牌色

| Token | HEX | 用途 |
|---|---|---|
| `--color-brand-50` | `#EFF4FF` | 当前导航/轻量品牌底 |
| `--color-brand-100` | `#DCE7FF` | hover 底色 |
| `--color-brand-200` | `#B9CEFF` | 柔和描边 |
| `--color-brand-500` | `#4778F5` | 次级品牌强调 |
| `--color-brand-600` | `#356DF3` | 主按钮、链接、焦点 |
| `--color-brand-700` | `#2456D8` | pressed / 深色文字强调 |
| `--color-brand-800` | `#1D43A8` | 深色主题品牌文字 |

主操作使用 brand-600；白字按钮文字对比度须在最终组件上复核。浅色品牌背景上的文字使用 brand-700 或更深。

### 2.2 中性色

| Token | HEX | 用途 |
|---|---|---|
| `--color-neutral-0` | `#FFFFFF` | 卡片/输入表面 |
| `--color-neutral-25` | `#FAFBFD` | 次级表面 |
| `--color-neutral-50` | `#F5F7FB` | 页面画布 |
| `--color-neutral-100` | `#EEF1F6` | hover/分隔背景 |
| `--color-neutral-200` | `#E2E7EF` | 边框 |
| `--color-neutral-300` | `#CBD3E0` | 强边框/禁用边框 |
| `--color-neutral-500` | `#7B879B` | 次级文字 |
| `--color-neutral-600` | `#5F6B80` | 正文辅助 |
| `--color-neutral-700` | `#465268` | 正文 |
| `--color-neutral-900` | `#182338` | 标题/主文字 |

页面画布 neutral-50；内容表面 neutral-0；主要文字 neutral-900。正文不使用低于 neutral-600 的灰色作为唯一信息载体。

### 2.3 语义色

| 语义 | 前景 | 背景 | 描边 | 用途 |
|---|---|---|---|---|
| Success | `#16845B` | `#EAF7F0` | `#B9E5CE` | 完成、在线、通过 |
| Warning | `#A96508` | `#FFF5E5` | `#F2D49F` | 等待、风险、需关注 |
| Danger | `#C43D4B` | `#FFF0F1` | `#F2C1C6` | 失败、删除、阻断 |
| Info | `#2864C8` | `#EDF4FF` | `#C5D9FF` | 提示、运行中、说明 |
| Neutral | `#5F6B80` | `#F1F3F7` | `#DDE2EA` | 未知、未开始、归档 |

语义 Token：`--color-success`、`--color-warning`、`--color-danger`、`--color-info`，并分别提供 `-bg`、`-border` 变体。状态必须带文字标签或可访问名称，不可只靠色相区分。

### 2.4 Office 主题专用材质色

| Token | HEX | 用途 |
|---|---|---|
| `--office-wall` | `#F1EEE5` | 米白墙面 |
| `--office-surface` | `#FFFCF5` | 暖白桌面/卡片 |
| `--office-wood` | `#A8794F` | 自然木家具 |
| `--office-wood-dark` | `#76553B` | 木质结构 |
| `--office-leaf` | `#426B52` | 植物/局部点缀 |
| `--office-ink` | `#29352F` | 角色/轮廓/深色文字 |

3D 场景的环境材质不改变语义色。角色比例统一（头身约 1:4），low-poly semi-realistic；避免全息、控制舱、照片级写实、塑料高光和戏剧化轮廓光。场景需保留桌、椅、显示器、工位、隔断、人物；同时提供等价 2D 列表和操作路径。

## 3. 字体与字号

字体栈：`Inter, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`。中文优先系统无衬线；数字可使用 `font-variant-numeric: tabular-nums`，避免指标跳动。

| Token | 字号 / 行高 | 字重 | 用途 |
|---|---:|---:|---|
| `--text-xs` | 11 / 16px | 400–500 | 辅助标签、图表刻度 |
| `--text-sm` | 12 / 18px | 400–500 | 表格辅助、元信息 |
| `--text-md` | 14 / 22px | 400–500 | 默认正文、导航 |
| `--text-lg` | 16 / 24px | 500–600 | 卡片标题、强调正文 |
| `--text-xl` | 20 / 28px | 600 | 区块标题 |
| `--text-2xl` | 24 / 32px | 600 | 页面标题 |
| `--text-3xl` | 30 / 38px | 650–700 | 首页主标题/关键指标 |
| `--text-display` | 36 / 44px | 700 | 极少量展示型标题 |

正文默认 14px；长文本行宽建议 60–80 个汉字；标题采用紧凑字距但不压缩正文。小于 12px 的文本仅用于非关键元信息。

## 4. 间距、尺寸与布局

采用 4px 基础网格，Token 单位均为 CSS px：

| Token | 值 | 用途 |
|---|---:|---|
| `--space-1` | 4px | 图标与文字 |
| `--space-2` | 8px | 紧凑控件内部 |
| `--space-3` | 12px | 标签/字段间距 |
| `--space-4` | 16px | 卡片内基础间距 |
| `--space-5` | 20px | 表单组/卡片内容 |
| `--space-6` | 24px | 卡片 padding |
| `--space-8` | 32px | 区块间距 |
| `--space-10` | 40px | 页面分组 |
| `--space-12` | 48px | 首屏/大区块 |

- 侧栏宽度：展开 248px、收起 72px；顶部栏 64–72px。
- 主内容最大宽度 1440px；桌面左右留白 32–40px，平板 24px，手机 16px。
- 页面使用 12 列逻辑网格；卡片间距 16–24px；首屏 KPI 通常 3–4 列，窄屏自然降列。
- 表单字段高度：默认 40px，紧凑 32px，大尺寸 48px；点击热区至少 40×40px（WCAG 目标）。

## 5. 圆角、边框与阴影

| Token | 值 | 用途 |
|---|---|---|
| `--radius-xs` | 4px | 小标签、微控件 |
| `--radius-sm` | 6px | 输入框、按钮 |
| `--radius-md` | 8px | 菜单、卡片 |
| `--radius-lg` | 12px | 大卡片/弹层 |
| `--radius-xl` | 16px | 特殊展示容器 |
| `--radius-pill` | 999px | 状态徽标/头像，不用于大面积容器 |
| `--border-default` | 1px solid neutral-200 | 默认分隔 |
| `--border-strong` | 1px solid neutral-300 | 强调边界 |
| `--shadow-sm` | `0 1px 2px rgb(24 35 56 / 6%)` | 卡片轻微抬升 |
| `--shadow-md` | `0 8px 24px rgb(24 35 56 / 10%)` | 浮层/菜单 |
| `--shadow-lg` | `0 16px 40px rgb(24 35 56 / 14%)` | 模态层 |

默认卡片优先用边框而非阴影。阴影只用于有层级关系的浮层，不用于所有区块。

## 6. 层级（Z-index）

| Token | 值 | 组件 |
|---|---:|---|
| `--z-base` | 0 | 普通内容 |
| `--z-sticky` | 100 | 粘性表头/顶部栏 |
| `--z-dropdown` | 200 | 下拉菜单、自动补全 |
| `--z-overlay` | 300 | 页面遮罩 |
| `--z-modal` | 400 | 对话框/抽屉 |
| `--z-toast` | 500 | 全局通知 |
| `--z-tooltip` | 600 | 提示气泡 |

局部组件不得自行引入高于 600 的层级；弹层由统一 Overlay 层管理，避免菜单被模态框或侧栏裁切。

## 7. 响应式断点

| Token | 范围 | 布局行为 |
|---|---|---|
| `--bp-xs` | 0–479px | 单列；导航转为抽屉/横向滚动 |
| `--bp-sm` | 480–767px | 手机横屏/大手机；卡片 1–2 列 |
| `--bp-md` | 768–1023px | 平板；侧栏可收起，主内容 2 列 |
| `--bp-lg` | 1024–1279px | 笔记本；默认展开导航，内容 2–3 列 |
| `--bp-xl` | 1280–1535px | 桌面；3–4 列 KPI |
| `--bp-2xl` | ≥1536px | 宽屏；内容限宽 1440px 并居中 |

断点建议：480 / 768 / 1024 / 1280 / 1536px。响应式改变布局而非隐藏关键操作；表格在窄屏采用横向滚动或卡片化行，不截断唯一可用操作。

## 8. 表格密度

| 模式 | 行高 | 单元格水平 padding | 用途 |
|---|---:|---:|---|
| Comfortable | 52px | 16px | 默认业务表、低频阅读 |
| Standard | 44px | 12px | 默认后台列表 |
| Compact | 36px | 10px | 运营监控/高密度数据 |

表头 36–40px，背景 neutral-25，文字 neutral-600，表头可 sticky；行 hover 使用 neutral-50；选中行使用 brand-50 并保留左侧/复选框等非颜色标识。数字列右对齐并启用 tabular nums。排序状态需暴露 `aria-sort`；分页/筛选状态可由 URL 恢复。

## 9. 图表色板

离散分类色（顺序固定，确保跨图一致）：

1. Blue `#4778F5`
2. Teal `#1C9A91`
3. Violet `#8666D5`
4. Amber `#D9942B`
5. Rose `#D96878`
6. Green `#4B9A68`
7. Cyan `#399AC4`
8. Slate `#77849A`

语义图表：成功 `#16845B`、警告 `#C98724`、失败 `#C43D4B`、信息 `#356DF3`。同一图表避免超过 8 个分类系列；超过时使用筛选/分组，不继续生成相近色。线图需配合线型/点型，不能只靠颜色。网格线 neutral-100，轴标签 neutral-600，图表背景透明。暗色 Office 图表应使用同一色板的高亮变体并重新验证对比度。

## 10. 动效

| Token | 时长 | 用途 |
|---|---:|---|
| `--motion-fast` | 120ms | hover、pressed |
| `--motion-normal` | 180ms | 菜单、轻量展开 |
| `--motion-slow` | 240ms | 抽屉、模态过渡 |

缓动：`cubic-bezier(0.2, 0, 0, 1)`。只动画化 opacity/transform；避免布局抖动、循环闪烁和无意义装饰动效。3D Office 动效只表达真实 Agent 状态（空闲、执行、等待、成功、失败），不做持续漂浮/呼吸灯。必须实现 `prefers-reduced-motion: reduce`，关闭非必要过渡与自动运动。

## 11. 焦点、悬停、按下与禁用态

- **Focus-visible**：键盘焦点使用 2px brand-600 外环，offset 2px；不得移除浏览器 outline 而不提供替代。
- **Hover**：仅对可交互元素提供轻量背景/边框变化；触屏不依赖 hover 才能看到操作。
- **Pressed**：主按钮使用 brand-700；不通过缩放造成布局变化。
- **Disabled**：背景 neutral-100、文字 neutral-500、边框 neutral-200；禁止状态使用 `disabled`/`aria-disabled` 语义，且不响应点击；必要时提供原因说明。
- **Loading**：按钮保留原宽度与名称，提供 `aria-busy`；进度状态可被辅助技术识别。
- **Error**：字段错误文字与边框同时出现，关联 `aria-describedby`；不能仅用红色。
- **对比度**：正文目标 ≥4.5:1，大字号 ≥3:1，控件边界/焦点 ≥3:1（WCAG 2.2 AA 目标）；最终以真实组合测量为准。

## 12. 可运行组件样例

入口：`frontend/src/components/DesignSystemShowcase.tsx`，样式：`frontend/src/components/design-system.css`。它在新前端首页直接挂载，展示品牌/语义色、按钮交互与禁用态、键盘焦点、三档表格密度、图表色板及 Office 材质预览。该样例不依赖 Storybook 或外部资源，可由现有 Vite dev/build 直接运行。

## 13. Token 落地与变更规则

- CSS Custom Properties 是视觉基础事实来源；组件样式只引用 Token，不复制散落色值。
- 新增 Token 必须同时补充本文件表格和可运行样例；语义色含义不得随页面改变。
- 组件新增变体需说明默认态、hover、focus-visible、pressed、disabled、loading、错误态。
- 本规范是第一版工程基线；截图评审后允许调数值，但必须以同一 Token 更新文档、CSS 和样例，不能单页私改。


## 12. 基础组件 API（L1-T13）

组件统一从 `@/components` 导入，避免页面直接依赖组件内部文件：

```tsx
import { Button, Card, Empty, Loading, Modal, Table } from '@/components'
import type { TableColumn } from '@/components'
```

| 组件 | 统一职责 | 关键约束 |
|---|---|---|
| Button | primary / secondary / tertiary / danger / ghost 操作按钮 | 原生 button 属性透传；默认 type=button；loading/disabled 阻止重复提交；支持 sm/md/lg 和图标槽位 |
| Card | section/article/div 内容容器 | 标题、说明、操作区与 none/sm/md/lg 内边距；不隐式承担数据请求 |
| Table | 类型安全的记录表格 | 显式 columns、data、rowKey；支持列宽、对齐、render、密度、loading 与 empty；表头使用 scope=col |
| Modal | 模态对话框 | 受控 open/onClose；Escape、遮罩关闭可配置；焦点初始定位与 Tab 循环；锁定背景滚动并恢复原焦点 |
| Empty | 空数据状态 | 有标题/说明/可选操作；role=status；不得将空数据伪装成请求成功的业务记录 |
| Loading | 加载反馈 | role=status、aria-live；支持尺寸与整页布局；尊重 prefers-reduced-motion |

### 12.1 Story 演示

`frontend/src/components/ComponentStories.tsx` 是可运行的组件 Story 集合，由 `DesignSystemShowcase` 挂载。演示覆盖按钮变体/禁用/加载、Card 结构、Table 密度和数据、Modal 键盘/遮罩交互、Empty 操作槽及 Loading 状态。演示数据仅为本地静态样例，不调用业务 API、不写入后端。

### 12.2 样式与可访问性

组件样式只消费本文件定义的 CSS Token（品牌/中性/语义色、间距、圆角、层级、动效）；交互控件提供可见 focus-visible 状态。Loading 动效在系统启用 reduced motion 时关闭。Modal 通过 aria-modal/aria-labelledby 暴露语义，关闭后恢复触发前焦点。Table 使用原生 table/thead/th/tbody 结构，窄屏由外层容器横向滚动。
