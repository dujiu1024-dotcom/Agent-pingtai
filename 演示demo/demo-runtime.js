/* Standalone demo runtime: browser-only mock data, no production API. */
(() => {
  const STORAGE_KEY = "agent-platform-demo-v1";
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"\x27`]/g, "");
  const persist = () => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); }
    catch (error) { notify("浏览器存储不可用，本次修改仅在当前页面有效"); }
  };
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    Object.keys(saved).forEach((key) => {
      if (Array.isArray(saved[key]) && Array.isArray(data[key])) {
        data[key] = saved[key].map((row) => Array.isArray(row) ? row : row);
      }
    });
  } catch (error) {
    localStorage.removeItem(STORAGE_KEY);
  }

  window.createItem = function () {
    openModal("创建记录", `
      <div class="field"><label>名称</label><input id="demo-name" maxlength="80" placeholder="输入名称" required></div>
      <div class="field"><label>描述</label><textarea id="demo-description" maxlength="240" placeholder="填写用途、目标或配置说明"></textarea></div>
      <div class="field"><label>状态</label><select id="demo-status"><option>运行中</option><option>待处理</option><option>已完成</option><option>草稿</option></select></div>
      <div class="field"><label>分类 / 版本</label><input id="demo-extra" maxlength="60" placeholder="例如：标准任务 / v1.0"></div>
      <p class="desc">数据仅保存在当前浏览器，不会发送到服务器。</p>
    `);
    document.getElementById("demo-name")?.focus();
  };

  window.submitModal = function () {
    if (modalKind !== "创建记录") {
      closeModal();
      notify(modalKind + "成功（演示环境，本地模拟）");
      return;
    }
    const name = document.getElementById("demo-name")?.value.trim();
    if (!name) { notify("请填写名称"); return; }
    const description = escapeHtml(document.getElementById("demo-description")?.value.trim() || "演示数据");
    const status = escapeHtml(document.getElementById("demo-status")?.value || "待处理");
    const extra = escapeHtml(document.getElementById("demo-extra")?.value.trim() || "本地演示");
    const safeName = escapeHtml(name);
    const title = pageFor(current)[0];
    const keyByPage = {
      "任务指挥中心":"任务指挥中心", "任务详情":"任务指挥中心",
      "Agent 运行态":"Agent 运行态", "Agent 运行详情":"Agent 运行态",
      "工具与 MCP":"工具与 MCP", "Workflow 设计":"Workflow 设计", "Workflow 设计器":"Workflow 设计",
      "知识库工作台":"知识库工作台", "评测数据集":"评测数据集", "评测运行":"评测数据集",
      "模型供应商":"模型供应商", "Skills":"Skills", "审计中心":"审计中心"
    };
    const key = keyByPage[title] || "任务指挥中心";
    const rows = data[key] || (data[key] = []);
    const id = "DEMO-" + Date.now().toString().slice(-7);
    let row;
    if (key === "任务指挥中心") row = [id, safeName, extra, status, "普通", "刚刚"];
    else if (key === "Agent 运行态") row = [id, safeName, extra || "v1.0.0", status, "演示环境"];
    else if (key === "工具与 MCP") row = [id, safeName, extra || "本地模拟工具", status, "刚刚"];
    else if (key === "Workflow 设计") row = [id, safeName, extra || "v1", status, "0 次运行"];
    else row = [id, safeName, description, status, "刚刚"];
    rows.unshift(row);
    persist();
    closeModal();
    render();
    notify("已创建并保存在本地演示数据中");
  };

  const tools = document.querySelector(".sidebarfoot");
  if (tools) {
    tools.insertAdjacentHTML("beforeend", `
      <div class="demo-tools">
        <button type="button" class="demo-tool" id="demo-export">导出演示数据</button>
        <button type="button" class="demo-tool" id="demo-reset">重置演示数据</button>
      </div>`);
    document.getElementById("demo-export")?.addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url; link.download = "agent-platform-demo-data.json"; link.click();
      URL.revokeObjectURL(url);
      notify("演示数据已导出");
    });
    document.getElementById("demo-reset")?.addEventListener("click", () => {
      if (!confirm("重置本浏览器中的演示数据？此操作不可撤销。")) return;
      localStorage.removeItem(STORAGE_KEY);
      location.reload();
    });
  }

  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY) notify("其他标签页的演示数据已更新，刷新页面后生效");
  });
  const style = document.createElement("style");
  style.textContent = `
    .demo-tools{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}
    .demo-tool{border:1px solid var(--border);background:#f8f9fc;border-radius:6px;padding:7px 5px;color:#65738a;font-size:10px}
    .demo-tool:hover{border-color:#b9caff;color:var(--brand);background:#f1f5ff}
  `;
  document.head.appendChild(style);
  render();
})();
