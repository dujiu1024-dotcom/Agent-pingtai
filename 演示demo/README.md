# 演示 Demo

本目录是**基于当前生产前端实现对齐的独立交互演示**，不是将历史原型直接当作生产系统。

## 对齐依据（main）

- 应用入口与导航：`frontend/src/App.tsx`、`frontend/src/routes/route-config.ts`
- 工作台：`frontend/src/features/workspace/Dashboard.tsx`、`frontend/src/features/workspace/dashboard.css`
- 3D 办公室：`frontend/src/components/OfficeWorkspace.tsx`、`frontend/src/components/office-workspace.css`
- 全局样式：`frontend/src/styles.css`
- 任务数据契约参考：`frontend/src/features/tasks/TaskCommandCenter.tsx`、`frontend/src/lib/api/api-client.ts`

演示页复用了生产前端的全局样式、工作台样式和办公室样式，并按生产应用的七个一级入口及实际页面结构组织交互。

## 本地预览

直接用浏览器打开 `index.html`，无需安装依赖或启动构建工具。

## 演示范围

- 工作台：任务指标、趋势、Agent 列表、任务筛选与最近动态
- 3D 办公室：楼层切换、空间/俯视视角、工位选择与搜索
- 任务中心：任务检索、状态筛选、创建本地演示任务
- Agent 协作：团队、运行时间线、协作黑板分区
- Workflow：节点展示、添加节点、草稿/编译/发布的本地交互
- 成本与质量、系统管理：遵循生产页面的信息结构与未接入状态表达

## 重要边界

- 本演示不连接生产 API；样例任务与 Agent 数据均为本地数据。
- 生产前端的 3D 场景使用 React Three Fiber；此独立 HTML 用 CSS 空间模型模拟可交互布局，不能宣称与生产 3D 渲染逐像素相同。
- 成本聚合、Office 实时快照等生产端尚未接入的数据保持为“—”或“待接入”，不虚构生产指标。
- 页面仅用于交互和视觉评审，不会修改生产数据。
