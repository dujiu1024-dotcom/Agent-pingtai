/* 页面二：任务全局看板。任务与运行分开呈现，缺失数据明确标注待接入。 */
(function () {
  "use strict";
  var UI = window.PlatformUI;
  var API = UI.API;
  var state = { tasks: [], agents: [], jobs: [], approvals: null, filter: "all", loading: false, taskError: "" };

  function statusCount(statuses) {
    return state.tasks.filter(function (task) { return statuses.indexOf(task.status) >= 0; }).length;
  }

  function agentDefinition(agentId) {
    var found = null;
    state.agents.forEach(function (item) {
      if (item.item_id === agentId) found = item;
    });
    var definition = (found && found.definition) || {};
    return {
      found: !!found,
      name: definition.name || definition.display_name || agentId || "未知智能体",
      desc: definition.description || definition.summary || "",
      status: found ? found.status : null,
      item: found
    };
  }

  function agentStatusTag(status) {
    if (status === "published") return '<span class="tag ok">已发布</span>';
    if (status === "draft") return '<span class="tag muted">草稿</span>';
    if (status === "disabled") return '<span class="tag bad">已停用</span>';
    if (status === null) return '<span class="tag warn">注册表未找到</span>';
    return '<span class="tag muted">' + UI.esc(status || "未知") + '</span>';
  }

  function renderKpis() {
    var total = state.tasks.length;
    var running = statusCount(["running", "cancelling"]);
    var queued = statusCount(["queued"]);
    var succeeded = statusCount(["succeeded"]);
    var failed = statusCount(["failed", "timed_out"]);
    document.getElementById("kpiTotal").textContent = String(total);
    document.getElementById("kpiRunning").textContent = String(running);
    document.getElementById("kpiQueued").textContent = String(queued);
    document.getElementById("kpiSucceeded").textContent = String(succeeded);
    document.getElementById("kpiFailed").textContent = String(failed);
    var approvalNode = document.getElementById("kpiApprovals");
    if (state.approvals === null) {
      approvalNode.textContent = "待接入";
      approvalNode.className = "value idle";
      approvalNode.style.fontSize = "18px";
    } else {
      approvalNode.textContent = String(state.approvals.length);
      approvalNode.className = "value " + (state.approvals.length ? "warn" : "idle");
      approvalNode.style.fontSize = "";
    }
  }

  function matchFilter(task) {
    if (state.filter === "all") return true;
    if (state.filter === "active") return task.status === "running" || task.status === "cancelling";
    if (state.filter === "queued") return task.status === "queued";
    if (state.filter === "succeeded") return task.status === "succeeded";
    if (state.filter === "failed") return task.status === "failed" || task.status === "timed_out";
    return ["cancelled", "paused", "interrupted"].indexOf(task.status) >= 0;
  }

  function renderTaskCards(visible) {
    var host = document.getElementById("taskCards");
    if (!host) return;
    if (!visible.length) {
      host.innerHTML = '<div class="empty-box"><strong>' + (state.taskError ? "任务数据未接入" : "当前没有任务记录") + '</strong>' +
        (state.taskError ? UI.esc(state.taskError) : "任务接口返回为空；提交新任务后会出现在这里。") + '</div>';
      return;
    }
    host.innerHTML = visible.map(function (task) {
      var agent = agentDefinition(task.agent_id);
      var isRetryable = task.status === "failed" || task.status === "timed_out";
      return '<div class="task-card">' +
        '<div class="head">' +
          '<div class="title">' + UI.esc(UI.taskTitle(task)) + '</div>' +
          '<span class="tag ' + UI.taskTone(task.status) + '">' + UI.esc(UI.taskLabel(task.status)) + '</span>' +
        '</div>' +
        '<div class="meta">' +
          '<div><span class="k">执行智能体</span>' + UI.esc(agent.name) + '</div>' +
          '<div><span class="k">所属项目</span>' + (task.project_id ? UI.esc(task.project_id) : "未关联") + '</div>' +
          '<div><span class="k">更新时间</span>' + UI.esc(UI.fmtTime(task.updated_at)) + '</div>' +
          '<div><span class="k">任务编号</span><span class="mono">' + UI.esc(task.task_id) + '</span></div>' +
        '</div>' +
        '<div class="actions">' +
          (isRetryable ? '<button class="btn btn-sm btn-primary btn-retry" type="button" data-retry="' + UI.esc(task.task_id) + '">重试</button>' : '') +
          '<button class="btn btn-sm" type="button" data-task="' + UI.esc(task.task_id) + '">查看详情</button>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function renderTasks() {
    var body = document.getElementById("taskTableBody");
    var visible = state.tasks.filter(matchFilter);
    document.getElementById("taskSummary").textContent = state.taskError
      ? "任务接口不可用"
      : "共 " + state.tasks.length + " 条任务，当前显示 " + visible.length + " 条";
    if (state.taskError && !state.tasks.length) {
      body.innerHTML = '<tr><td colspan="8"><div class="notice warn"><span>!</span><div><strong>任务数据未接入</strong><br>' + UI.esc(state.taskError) + '</div></div></td></tr>';
      renderTaskCards([]);
      return;
    }
    if (!state.tasks.length) {
      body.innerHTML = '<tr><td colspan="8"><div class="empty-box" style="margin:8px 0;"><strong>当前没有任务记录</strong>任务接口返回为空；提交新任务后会出现在这里。</div></td></tr>';
      renderTaskCards([]);
      return;
    }
    if (!visible.length) {
      body.innerHTML = '<tr><td colspan="8"><div class="empty-box" style="margin:8px 0;">当前筛选下没有任务</div></td></tr>';
      renderTaskCards([]);
      return;
    }
    body.innerHTML = visible.map(function (task) {
      var agent = agentDefinition(task.agent_id);
      var tone = UI.taskTone(task.status);
      var project = task.project_id ? UI.esc(task.project_id) : '<span style="color:#6b7794;">未关联</span>';
      var isRetryable = task.status === "failed" || task.status === "timed_out";
      return '<tr>' +
        '<td class="mono nowrap">' + UI.esc(task.task_id) + '</td>' +
        '<td><div class="cell-title">' + UI.esc(UI.taskTitle(task)) + '</div><div class="cell-sub">优先级 ' + UI.esc(task.priority) + ' · 尝试 ' + UI.esc(task.attempts) + ' 次</div></td>' +
        '<td><div class="cell-title">' + UI.esc(agent.name) + '</div><div class="cell-sub">' + (agent.found ? "智能体注册项" : "未在注册表找到") + '</div></td>' +
        '<td>' + project + '<div class="cell-sub">负责人与归属待接入</div></td>' +
        '<td><span class="tag ' + tone + '">' + UI.esc(UI.taskLabel(task.status)) + '</span></td>' +
        '<td class="mono">' + (task.run_id ? UI.esc(task.run_id) : '<span style="color:#6b7794;">尚未产生运行</span>') + '</td>' +
        '<td class="nowrap">' + UI.esc(UI.fmtTime(task.updated_at)) + '</td>' +
        '<td>' +
          (isRetryable ? '<button class="btn btn-sm btn-primary btn-retry" type="button" data-retry="' + UI.esc(task.task_id) + '">重试</button> ' : '') +
          '<button class="btn btn-sm" type="button" data-task="' + UI.esc(task.task_id) + '">查看详情</button>' +
        '</td>' +
      '</tr>';
    }).join("");
    renderTaskCards(visible);
  }

  function renderAgents() {
    var host = document.getElementById("agentList");
    var stats = {};
    state.tasks.forEach(function (task) {
      var key = task.agent_id || "未知";
      if (!stats[key]) stats[key] = { running: 0, queued: 0, failed: 0, total: 0, last: null };
      var item = stats[key];
      item.total += 1;
      if (task.status === "running" || task.status === "cancelling") item.running += 1;
      if (task.status === "queued") item.queued += 1;
      if (task.status === "failed" || task.status === "timed_out") item.failed += 1;
      if (!item.last || String(task.updated_at) > String(item.last)) item.last = task.updated_at;
    });
    var ids = [];
    state.agents.forEach(function (agent) { if (ids.indexOf(agent.item_id) < 0) ids.push(agent.item_id); });
    Object.keys(stats).forEach(function (id) { if (ids.indexOf(id) < 0) ids.push(id); });

    if (!ids.length) {
      host.innerHTML = '<div class="empty-box"><strong>暂无智能体数据</strong>注册表为空或接口未接入；任务统计也不会凭空生成智能体。</div>';
      document.getElementById("agentNote").textContent = "注册表为空或未接入";
      return;
    }
    document.getElementById("agentNote").textContent = "注册状态与任务统计；在线状态未接入";
    ids.sort(function (a, b) {
      var sa = stats[a] || { total: 0 };
      var sb = stats[b] || { total: 0 };
      return sb.total - sa.total;
    });
    host.innerHTML = ids.map(function (id) {
      var info = agentDefinition(id);
      var stat = stats[id] || { running: 0, queued: 0, failed: 0, total: 0, last: null };
      return '<div class="agent-row">' +
        '<span class="dot ' + (stat.running ? "info" : "idle") + '"></span>' +
        '<div class="agent-body">' +
          '<div class="agent-name">' + UI.esc(info.name) + agentStatusTag(info.status) + '</div>' +
          '<div class="agent-use">' + UI.esc(info.desc || "暂无用途描述") + '</div>' +
          '<div class="agent-counts">' +
            '<span>执行中 <b>' + stat.running + '</b></span>' +
            '<span>等待 <b>' + stat.queued + '</b></span>' +
            '<span>失败 <b>' + stat.failed + '</b></span>' +
            '<span>任务合计 <b>' + stat.total + '</b></span>' +
            '<span>最近更新 ' + UI.esc(UI.fmtTime(stat.last)) + '</span>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function intervalText(seconds) {
    if (!seconds) return "—";
    if (seconds < 60) return "每 " + seconds + " 秒";
    if (seconds % 3600 === 0) return "每 " + (seconds / 3600) + " 小时";
    return "每 " + Math.round(seconds / 60) + " 分钟";
  }

  function renderScheduler() {
    var host = document.getElementById("schedulerList");
    var note = document.getElementById("schedulerNote");
    if (!state.jobs.length) {
      note.textContent = "接口未返回定时任务";
      host.innerHTML = '<div class="empty-box"><strong>暂无定时任务数据</strong>定时任务接口未接入或当前租户没有配置。</div>';
      return;
    }
    note.textContent = "共 " + state.jobs.length + " 个任务";
    host.innerHTML = state.jobs.map(function (job) {
      var resultTone = job.last_result === "failed" ? "bad" : job.last_result === "succeeded" ? "ok" : "muted";
      var resultText = job.last_result === "failed" ? "上次失败" : job.last_result === "succeeded" ? "上次成功" : job.last_result ? String(job.last_result) : "尚未运行";
      return '<div class="agent-row">' +
        '<span class="dot ' + (job.enabled ? (job.last_result === "failed" ? "bad" : "ok") : "idle") + '"></span>' +
        '<div class="agent-body">' +
          '<div class="agent-name">' + UI.esc(job.name || job.job_id) +
            (job.enabled ? '<span class="tag ok">已启用</span>' : '<span class="tag muted">已停用</span>') +
            '<span class="tag ' + resultTone + '">' + UI.esc(resultText) + '</span>' +
          '</div>' +
          '<div class="agent-use">' + UI.esc(job.description || "暂无说明") + '</div>' +
          '<div class="agent-counts">' +
            '<span>' + UI.esc(intervalText(job.interval_seconds)) + '</span>' +
            '<span>上次运行 ' + UI.esc(UI.fmtTime(job.last_run_at)) + '</span>' +
            '<span>下次运行 ' + UI.esc(UI.fmtTime(job.next_run_at)) + '</span>' +
            '<span>失败次数 <b>' + UI.esc(job.failure_count || 0) + '</b></span>' +
          '</div>' +
          (job.last_result === "failed" && job.last_message ? '<div class="agent-use" style="color:#8c5a10;">上次返回：' + UI.esc(job.last_message) + '</div>' : '') +
        '</div>' +
      '</div>';
    }).join("");
  }

  function riskLabel(level) {
    return { low: "低", medium: "中", high: "高", critical: "严重" }[level] || (level || "未标注");
  }

  function renderApprovals() {
    var host = document.getElementById("approvalList");
    var note = document.getElementById("approvalNote");
    if (state.approvals === null) {
      note.textContent = "审批接口未接入或不可用";
      host.innerHTML = '<div class="empty-box"><strong>审批数据待接入</strong>当前无法读取待审批事项；这不代表没有高风险操作。</div>';
      return;
    }
    if (!state.approvals.length) {
      note.textContent = "当前没有待审批事项";
      host.innerHTML = '<div class="empty-box">当前没有待审批的高风险操作。</div>';
      return;
    }
    note.textContent = "共 " + state.approvals.length + " 项等待处理";
    host.innerHTML = state.approvals.map(function (item) {
      var tone = item.risk_level === "critical" || item.risk_level === "high" ? "bad" : item.risk_level === "medium" ? "warn" : "muted";
      return '<div class="agent-row">' +
        '<span class="dot ' + tone + '"></span>' +
        '<div class="agent-body">' +
          '<div class="agent-name">' + UI.esc(item.action || "未命名操作") +
            '<span class="tag ' + tone + '">风险 ' + UI.esc(riskLabel(item.risk_level)) + '</span>' +
          '</div>' +
          '<div class="agent-use">对象：' + UI.esc(item.resource || "—") + '</div>' +
          '<div class="agent-counts">' +
            '<span>申请人 ' + UI.esc(item.requested_by || "—") + '</span>' +
            '<span>过期时间 ' + UI.esc(UI.fmtTime(item.expires_at)) + '</span>' +
            '<span class="mono">' + UI.esc(UI.shortId(item.approval_id)) + '</span>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function eventLine(event) {
    var type = event.event_type || event.type || event.kind || "事件";
    var time = event.occurred_at || event.created_at || event.timestamp || "";
    var payload = event.payload !== undefined ? event.payload : (event.data !== undefined ? event.data : event.detail);
    if (payload === undefined) payload = event;
    var text = typeof payload === "string" ? payload : JSON.stringify(payload);
    return { type: String(type), time: time, text: text };
  }

  function openTask(taskId) {
    var dialog = document.getElementById("taskDialog");
    var body = document.getElementById("dialogBody");
    document.getElementById("dialogTitle").textContent = "任务详情";
    body.innerHTML = '<div style="color:#6b7794;">正在读取任务详情…</div>';
    if (typeof dialog.showModal === "function") dialog.showModal();

    Promise.all([
      UI.request(API + "/workbench/tasks/" + encodeURIComponent(taskId)),
      UI.request(API + "/workbench/tasks/" + encodeURIComponent(taskId) + "/runs")
    ]).then(function (results) {
      var taskRes = results[0];
      var runsRes = results[1];
      if (!taskRes.ok) {
        body.innerHTML = '<div class="notice bad"><span>!</span><div><strong>任务详情读取失败</strong><br>' + UI.esc(taskRes.error) + '</div></div>';
        return;
      }
      var task = taskRes.data;
      var runs = (runsRes.ok && runsRes.data && runsRes.data.items) || [];
      var agent = agentDefinition(task.agent_id);
      var isRetryable = task.status === "failed" || task.status === "timed_out";
      document.getElementById("dialogTitle").textContent = UI.taskTitle(task);
      var inputText = UI.taskInputText(task) || "（任务输入为空）";
      var outputText = UI.taskOutputText(task) || "暂无输出";
      body.innerHTML =
        '<dl class="kv">' +
          '<dt>任务编号</dt><dd class="mono">' + UI.esc(task.task_id) + '</dd>' +
          '<dt>任务状态</dt><dd><span class="tag ' + UI.taskTone(task.status) + '">' + UI.esc(UI.taskLabel(task.status)) + '</span></dd>' +
          '<dt>执行智能体</dt><dd>' + UI.esc(agent.name) + ' <span class="mono">' + UI.esc(task.agent_id) + '</span></dd>' +
          '<dt>所属项目</dt><dd>' + (task.project_id ? UI.esc(task.project_id) : "未关联（项目归属数据待接入）") + '</dd>' +
          '<dt>关联会话</dt><dd class="mono">' + (task.session_id ? UI.esc(task.session_id) : "未关联") + '</dd>' +
          '<dt>运行编号</dt><dd class="mono">' + (task.run_id ? UI.esc(task.run_id) : "尚未产生运行") + '</dd>' +
          '<dt>优先级</dt><dd>' + UI.esc(task.priority) + '</dd>' +
          '<dt>尝试次数</dt><dd>' + UI.esc(task.attempts) + '</dd>' +
          '<dt>创建时间</dt><dd>' + UI.esc(UI.fmtTime(task.created_at)) + '</dd>' +
          '<dt>更新时间</dt><dd>' + UI.esc(UI.fmtTime(task.updated_at)) + '</dd>' +
          '<dt>完成时间</dt><dd>' + UI.esc(UI.fmtTime(task.finished_at)) + '</dd>' +
        '</dl>' +
        (isRetryable
          ? '<div style="margin:12px 0;display:flex;gap:8px;align-items:center;">' +
              '<button class="btn btn-primary btn-retry" type="button" data-retry="' + UI.esc(task.task_id) + '">重试任务</button>' +
              '<span style="color:#6b7794;font-size:12px;">重试会创建一个新的 Run，保留当前输入与智能体配置</span>' +
            '</div>'
          : '') +
        '<div class="section-label">任务输入</div><div class="pre">' + UI.esc(inputText) + '</div>' +
        '<div class="section-label">执行结果</div><div class="pre">' + UI.esc(outputText) + '</div>' +
        (task.last_error ? '<div class="section-label">错误信息</div><div class="pre" style="color:#8b2f28;">' + UI.esc(task.last_error) + '</div>' : '') +
        '<div class="section-label">运行记录（Run）</div>' +
        (runsRes.ok
          ? (runs.length
            ? '<div class="table-wrap"><table class="data"><thead><tr><th>运行编号</th><th>状态</th><th>更新时间</th><th>尝试</th><th>错误</th><th>操作</th></tr></thead><tbody>' +
              runs.map(function (run) {
                return '<tr>' +
                  '<td class="mono">' + UI.esc(run.run_id) + '</td>' +
                  '<td><span class="tag ' + UI.taskTone(run.status) + '">' + UI.esc(UI.taskLabel(run.status)) + '</span></td>' +
                  '<td class="nowrap">' + UI.esc(UI.fmtTime(run.updated_at || run.started_at)) + '</td>' +
                  '<td>' + UI.esc(run.attempts !== undefined ? run.attempts : "—") + '</td>' +
                  '<td>' + UI.esc(run.error || run.last_error || "—") + '</td>' +
                  '<td><button class="btn btn-sm" type="button" data-run="' + UI.esc(run.run_id) + '" data-target="run-events-' + UI.esc(run.run_id) + '">查看事件</button></td>' +
                '</tr>' +
                '<tr class="hidden" id="run-events-' + UI.esc(run.run_id) + '"><td colspan="6"><div class="pre" style="margin:0;">正在读取运行事件…</div></td></tr>';
              }).join("") +
              '</tbody></table></div>'
            : '<div class="empty-box">该任务还没有运行记录。</div>')
          : '<div class="notice warn"><span>!</span><div>运行记录读取失败：' + UI.esc(runsRes.error) + '</div></div>') +
        '<div class="section-label">开发者信息</div>' +
        '<div class="pre">' + UI.esc(JSON.stringify({ task_id: task.task_id, run_id: task.run_id, session_id: task.session_id, agent_id: task.agent_id, project_id: task.project_id }, null, 2)) + '</div>';
    });
  }

  function loadRunEvents(runId, targetId, button) {
    var row = document.getElementById(targetId);
    if (!row) return;
    row.classList.remove("hidden");
    var cell = row.querySelector(".pre");
    cell.textContent = "正在读取运行事件…";
    button.disabled = true;
    UI.request(API + "/runs/" + encodeURIComponent(runId) + "/events?limit=200").then(function (res) {
      button.disabled = false;
      if (!res.ok) {
        cell.textContent = "运行事件读取失败：" + res.error;
        return;
      }
      var items = (res.data && res.data.items) || [];
      if (!items.length) {
        cell.textContent = "该运行暂无可展示的事件。";
        return;
      }
      cell.textContent = items.map(function (event) {
        var line = eventLine(event);
        return "[" + (line.time ? UI.fmtTime(line.time) : "—") + "] " + line.type + "\n" + line.text;
      }).join("\n\n");
    });
  }

  function retryTask(taskId) {
    if (!taskId) return;
    var task = null;
    state.tasks.forEach(function (item) { if (item.task_id === taskId) task = item; });
    if (!task) { alert("未找到该任务，无法重试。"); return; }
    if (task.status !== "failed" && task.status !== "timed_out") { alert("只有失败或超时的任务才能重试。"); return; }
    var btn = document.querySelector('button[data-retry="' + CSS.escape(taskId) + '"]');
    if (btn) { btn.disabled = true; btn.textContent = "重试中…"; }
    UI.request(API + "/workbench/tasks/" + encodeURIComponent(taskId) + "/retry", {
      method: "POST",
      body: { idempotency_key: crypto.randomUUID() }
    }).then(function (res) {
      if (!res.ok) {
        if (btn) { btn.disabled = false; btn.textContent = "重试"; }
        alert("重试失败：" + res.error);
        return;
      }
      task.status = (res.data && res.data.status) || "queued";
      task.run_id = (res.data && res.data.run_id) || task.run_id;
      task.updated_at = new Date().toISOString();
      task.attempts = (task.attempts || 0) + 1;
      renderTasks();
      renderTaskCards(state.tasks.filter(matchFilter));
    }).catch(function (err) {
      if (btn) { btn.disabled = false; btn.textContent = "重试"; }
      alert("重试失败：" + (err && err.message ? err.message : "网络错误"));
    });
  }

  function load() {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    return Promise.all([
      UI.request(API + "/workbench/tasks?limit=200"),
      UI.request(API + "/registry/agent?limit=200"),
      UI.request(API + "/scheduler/jobs"),
      UI.request(API + "/approvals?limit=100"),
      UI.request("/auth/me")
    ]).then(function (results) {
      var tasksRes = results[0];
      var agentsRes = results[1];
      var jobsRes = results[2];
      var approvalsRes = results[3];
      var meRes = results[4];

      document.getElementById("authNotice").style.display = meRes.status === 401 ? "flex" : "none";

      if (tasksRes.ok) { state.tasks = (tasksRes.data && tasksRes.data.items) || []; state.taskError = ""; }
      else { state.tasks = []; state.taskError = UI.probeReason(tasksRes); }
      if (agentsRes.ok) state.agents = (agentsRes.data && agentsRes.data.items) || [];
      else state.agents = [];
      if (jobsRes.ok) state.jobs = (jobsRes.data && jobsRes.data.items) || [];
      else state.jobs = [];
      state.approvals = approvalsRes.ok ? ((approvalsRes.data && approvalsRes.data.items) || []) : null;

      if (!agentsRes.ok) {
        document.getElementById("agentNote").textContent = "智能体注册表不可用：" + UI.probeReason(agentsRes);
      }
      if (!jobsRes.ok) {
        document.getElementById("schedulerNote").textContent = "定时任务接口不可用：" + UI.probeReason(jobsRes);
      }

      renderKpis();
      renderTasks();
      renderAgents();
      renderScheduler();
      renderApprovals();
      document.getElementById("refreshStamp").textContent = "最近刷新 " + UI.fmtClock(new Date().toISOString());
    }).catch(function (err) {
      console.error(err);
    }).then(function () {
      state.loading = false;
    });
  }

  UI.renderNav("tasks");
  UI.loadIdentity();

  document.getElementById("taskFilters").addEventListener("click", function (event) {
    var btn = event.target.closest(".filter");
    if (!btn) return;
    state.filter = btn.getAttribute("data-filter") || "all";
    Array.prototype.forEach.call(document.querySelectorAll("#taskFilters .filter"), function (item) {
      item.classList.toggle("active", item === btn);
    });
    renderTasks();
  });

  document.getElementById("taskTableBody").addEventListener("click", function (event) {
    var btn = event.target.closest("button[data-task]");
    if (!btn) return;
    openTask(btn.getAttribute("data-task"));
  });

  document.getElementById("taskCards").addEventListener("click", function (event) {
    var btn = event.target.closest("button[data-task]");
    if (!btn) return;
    openTask(btn.getAttribute("data-task"));
  });

  document.getElementById("dialogBody").addEventListener("click", function (event) {
    var btn = event.target.closest("button[data-run]");
    if (!btn) return;
    loadRunEvents(btn.getAttribute("data-run"), btn.getAttribute("data-target"), btn);
  });

  document.getElementById("dialogClose").addEventListener("click", function () {
    document.getElementById("taskDialog").close();
  });

  document.getElementById("refreshBtn").addEventListener("click", load);

  // 全局事件委托：data-retry 按钮（表格、卡片、弹窗里都会出现）
  document.addEventListener("click", function (event) {
    var retryBtn = event.target.closest("button[data-retry]");
    if (retryBtn) retryTask(retryBtn.getAttribute("data-retry"));
  });

  load();
  UI.startPolling(load, 20000);
})();
