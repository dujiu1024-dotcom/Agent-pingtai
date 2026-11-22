# Agent 平台功能演示 Demo

本目录提供两种演示入口，按用途选择：

## 入口

- `index.html`：轻量独立演示页。无需安装依赖，直接用浏览器打开；使用自带 mock 数据和演示交互。
- `production.html`：生产前端组件演示入口。通过 Vite 直接加载 `frontend/src` 中的生产 React 页面、路由、组件和 CSS，并在浏览器端注入本地 mock API。
- `prototype-original.html`：用户提交的原始交互原型备份，不作为默认演示入口。

## 启动生产组件演示

要求 Node.js 20.19+。

```bash
cd 演示demo
npm install
npm run dev
```

Vite 启动后打开终端输出的本地地址及 `/production.html`。生产组件源码通过别名直接引用仓库中的 `frontend/src`，不复制或修改生产源码。

## 模拟数据与安全边界

- `production-main.tsx` 在生产模块加载前替换浏览器 fetch，将 API 请求路由到本地 mock transport。
- 模拟传输提供启动配置、任务、Agent、Workflow、审批、Tool/Skill 和 Run 的样例数据；未覆盖的 GET 接口返回空列表，写请求只返回本地模拟成功结果。
- 不需要后端服务，不连接生产 API，不写入数据库，也不会执行真实发布、审批或任务操作。
- 页面布局、路由和已接入的业务组件来自生产前端；但数据、权限模式和写操作均为演示模拟。
- 生产路由中仍存在由生产代码明确标记为“业务页面待模块接入”的页面。演示入口复用这些真实占位状态，不将它们包装成已交付的生产功能。
- 3D 办公室复用生产 React Three Fiber 场景；运行效果取决于浏览器 WebGL 支持。

## 原型独立演示入口覆盖

`index.html` 保留七个一级模块和主要业务页面的独立交互演示，包括菜单切换、列表筛选、详情弹窗、创建表单、Workflow 节点操作、办公室工位查看及模拟导出/刷新/发布反馈。该入口的数据与交互逻辑独立于生产 React 代码。

## 当前验证状态

生产组件演示入口已接入源码与 mock transport 配置；尚需在具备依赖安装和浏览器运行环境的机器执行 `npm install`、`npm run dev` 和逐路由浏览器验收。未完成该验收前，不宣称构建或端到端测试通过。
