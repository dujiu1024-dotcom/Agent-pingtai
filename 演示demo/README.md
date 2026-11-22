# Agent 平台独立演示 Demo

## 唯一默认入口

打开 `index.html` 会自动进入 `production.html`。实际演示页面直接复用仓库 `frontend/src` 中的生产 React 页面、菜单、路由、业务组件和 CSS，不再使用旧版独立 HTML 模板作为默认展示。

`prototype-original.html` 仅作为用户提供的原始视觉原型存档，不是第二套产品界面。旧版独立模拟页面不再作为对外演示入口。

## 本地启动与构建

要求 Node.js 20.19+。

```bash
cd 演示demo
npm install
npm run dev
```

开发服务器根地址会自动跳转至生产组件演示页。构建独立静态演示包：

```bash
npm run build
```

构建产物位于 `dist-production/`。将该目录内容部署到静态站点根目录即可；无需部署后端、数据库或 Redis。由于 React Router 使用 history 路由，静态托管服务需要将未知路径回退到 `production.html`。

## 模拟数据与安全边界

- `production-main.tsx` 在动态加载生产页面之前安装本地 mock transport；演示 API 不访问生产服务。
- 已接入的任务、Agent、Workflow、审批、Tool/Skill 和 Run 集合支持本地列表、详情及基本增删改；变更保存在浏览器 localStorage，刷新后保留。
- 未接入的接口明确返回 HTTP 501，不再以空列表或成功响应伪装成已支持功能。演示数据仅保存在当前浏览器，不会写入后端。
- 页面结构、菜单、路由、业务控件和样式直接来自生产前端源码；数据、权限配置和写操作是演示模拟，不会写入真实业务系统。
- 模拟启动配置启用权限模式，以便展示组织与 RBAC 页面。
- 生产源码中仍明确标记为待模块接入的页面，演示时保留真实占位状态，不伪装成已交付功能。
- 3D 办公室复用生产 React Three Fiber 场景，效果依赖浏览器 WebGL 支持。
- 页面右下角显示“生产前端组件 · 本地模拟数据”标识。

## 原型资料

- `prototype-original.html`：原始 HTML 原型备份，仅供视觉对照。
- `demo-runtime.js`：旧独立 HTML 演示运行时，不由默认入口加载。

## 验收边界

代码复用关系和默认入口已调整；仍需在具备 Node.js 依赖与浏览器的环境运行 `npm install`、`npm run build`，并逐路由验收菜单、页面、页签、分页、弹窗、筛选和 3D 场景。未完成构建及浏览器验收前，不宣称端到端验收通过。
