/* 页面三：指令中心。会话、任务与运行分离；提交走真实任务接口，不做模拟成功。 */
(function () {
  "use strict";
  var UI = window.PlatformUI;
  var API = UI.API;

  var state = {
    sessions: [],
    messages: [],
    agents: [],
    tasks: [],
    currentSessionId: null,
    selectedAgentId: "auto",
    priority: 100,
    capability: null,
    capabilityKnown: false,
    authRequired: false,
    pending: null,
    feedback: [],
    polls: {},
    lastSeen: {},
    unread: {},
    search: "",
    submitting: false,
    loadingMessages: false,
    forceScrollOnce: false,
    sessionWarning: ""
  };

  var EXAMPLES = [
    "帮我分析最近一周系统运行情况。",
    "检查所有执行失败的任务并整理原因。",
    "汇总今天所有 Agent 的工作进度。",
    "帮我整理本周项目进展报告。",
    "检查任务执行队列是否存在积压。"
  ];

  var TERMINAL = ["succeeded", "failed", "cancelled", "timed_out"];

  // 给 agent_id 分配稳定的头像属性：名字、首字、背景色。
  // 颜色池预定义 8 组柔和色，agent_id 做 32-bit 哈希取模。
  var AGENT_PALETTE = [
    { bg: "#e3f2fd", fg: "#1565c0" }, // 蓝
    { bg: "#e8f5e9", fg: "#2e7d32" }, // 绿
    { bg: "#fce4ec", fg: "#c2185b" }, // 粉
    { bg: "#fff3e0", fg: "#ef6c00" }, // 橙
    { bg: "#f3e5f5", fg: "#7b1fa2" }, // 紫
    { bg: "#e0f7fa", fg: "#00695c" }, // 青
    { bg: "#fff9c4", fg: "#f57f17" }, // 黄
    { bg: "#efebe9", fg: "#5d4037" }, // 棕
  ];
  function agentAvatar(agentId) {
    if (!agentId) return null;
    // 先从 state.agents 里找名字
    var name = agentId;
    for (var i = 0; i < state.agents.length; i++) {
      if (state.agents[i].item_id === agentId) {
        name = agentDef(state.agents[i]).name || agentId;
        break;
      }
    }
    // 简单字符串哈希 → 调色板索引
    var h = 0;
    for (var j = 0; j < agentId.length; j++) { h = ((h << 5) - h + agentId.charCodeAt(j)) | 0; }
    var palette = AGENT_PALETTE[Math.abs(h) % AGENT_PALETTE.length];
    // avatar 字符：名字首字（中文）或首字母大写
    var firstChar = name.charAt(0);
    return { name: name, char: firstChar, bg: palette.bg, fg: palette.fg };
  }

  function agentDef(item) {
    var definition = (item && item.definition) || {};
    var caps = definition.capabilities;
    var capsText = "";
    if (Array.isArray(caps)) capsText = caps.map(function (v) { return typeof v === "string" ? v : JSON.stringify(v); }).join("、");
    else if (typeof caps === "string") capsText = caps;
    return {
      name: definition.name || definition.display_name || (item && item.item_id) || "未命名智能体",
      desc: definition.description || definition.summary || "",
      caps: capsText,
      status: item ? item.status : null,
      id: item ? item.item_id : ""
    };
  }

  function normalizeAgents(items) {
    var map = {};
    (items || []).forEach(function (item) {
      var current = map[item.item_id];
      if (!current || (current.status !== "published" && item.status === "published") || Number(item.version) > Number(current.version)) {
        map[item.item_id] = item;
      }
    });
    return Object.keys(map).map(function (key) { return map[key]; });
  }

  function publishedAgents() {
    return state.agents.filter(function (item) { return item.status === "published"; });
  }

  function activeTaskCount(agentId) {
    return state.tasks.filter(function (task) {
      return task.agent_id === agentId && (task.status === "running" || task.status === "queued" || task.status === "cancelling");
    }).length;
  }

  /** 意图路由：根据 prompt 里的 @mention / 关键词匹配 / 负载均衡选 agent。
   * 优先级：
   *   1. 显式指定（UI 手动选 + prompt 里的 @mention）
   *   2. 关键词匹配 agent 的 name / item_id / description / capabilities
   *   3. 负载均衡（activeTaskCount 最少 → 字典序）
   */
  function _parseMention(text) {
    if (!text) return null;
    var m = text.match(/@([\u4e00-\u9fa5A-Za-z0-9_\-]{1,20})/);
    return m ? m[1].trim() : null;
  }

  /** 从文本里提取中文关键词集合。
   *  1) 正则抓 2-6 字连续中文 → 保留完整词
   *  2) 再对所有中文字符串按"逐字相邻"切 bigram
   *  两步合并去重 —— 这样"帮我记一笔收入"能得到完整的"收入" bigram
   */
  function _extractTerms(text) {
    if (!text) return [];
    var terms = [];
    // Step 1: 保留所有 2-6 字完整词
    var groups = text.match(/[\u4e00-\u9fa5]{2,6}/g) || [];
    groups.forEach(function (g) { terms.push(g); });
    // Step 2: 对整个文本的中文字符串逐字切相邻 bigram
    var allChinese = text.match(/[\u4e00-\u9fa5]+/g) || [];
    allChinese.forEach(function (seg) {
      for (var i = 0; i < seg.length - 1; i++) terms.push(seg.slice(i, i + 2));
    });
    // 去重
    var seen = {};
    return terms.filter(function (t) {
      if (t.length < 2 || seen[t]) return false;
      seen[t] = true; return true;
    });
  }

  /** 同义词归一化：把常见近义词合并成同一个根词。
   *  只做多字短语替换——单字替换容易误伤（"查"→"检查" 把 "查看支出" 也搞成"检查支出"）。
   */
  var _SYNONYM_MAP = [
    ["招个", "招聘"], ["招新", "招聘"], ["招募", "招聘"],
    ["报帐", "记账"], ["记帐", "记账"],
    ["结帐", "结算"], ["结账", "结算"],
    ["瞅瞅", "检查"],
    ["出问题", "报警"], ["挂了", "报警"], ["坏了", "报警"],
  ];
  function _normalizeSynonyms(text) {
    var out = text;
    _SYNONYM_MAP.forEach(function (pair) {
      out = out.split(pair[0]).join(pair[1]);
    });
    return out;
  }

  function _matchAgentByKeyword(text, candidates) {
    if (!text || !candidates.length) return null;
    var normText = _normalizeSynonyms(text.toLowerCase());
    var pTerms = _extractTerms(normText);
    var scored = candidates.map(function (item) {
      var info = agentDef(item);
      var itemId = (item.item_id || "").toLowerCase();
      var name = (info.name || "").toLowerCase();
      var desc = _normalizeSynonyms((info.desc || "").toLowerCase());
      var caps = _normalizeSynonyms((info.caps || "").toLowerCase());
      var score = 0;
      if (normText.indexOf(itemId) >= 0) score += 100;
      if (name && normText.indexOf(name) >= 0) score += 80;
      var dTerms = _extractTerms(desc);
      var cTerms = _extractTerms(caps);
      var allTerms = dTerms.concat(cTerms);
      pTerms.forEach(function (pt) {
        allTerms.forEach(function (dt) {
          if (dt === pt) score += 25;
          else if (dt.indexOf(pt) >= 0 || pt.indexOf(dt) >= 0) score += 15;
        });
      });
      return { item: item, score: score };
    });
    scored.sort(function (a, b) { return b.score - a.score; });
    return scored[0] && scored[0].score > 0 ? scored[0].item : null;
  }

  function resolveAgent(prompt) {
    var published = publishedAgents();
    if (!published.length) return null;

    // 1. 用户在 UI 手动选了具体 agent → 直接用
    if (state.selectedAgentId !== "auto") {
      var manual = null;
      published.forEach(function (item) { if (item.item_id === state.selectedAgentId) manual = item; });
      if (manual) return manual;
    }

    // 2. prompt 里 @mention 了某个 agent → 优先匹配
    var mention = _parseMention(prompt);
    if (mention) {
      // 先按 item_id 精确匹配
      var byId = published.filter(function (a) { return a.item_id.toLowerCase() === mention.toLowerCase(); });
      if (byId.length) return byId[0];
      // 再按 name 精确匹配
      var byName = published.filter(function (a) {
        var n = (agentDef(a).name || "").toLowerCase();
        return n === mention.toLowerCase();
      });
      if (byName.length) return byName[0];
      // 再按 item_id / name 包含匹配
      var byPartial = published.filter(function (a) {
        var n = (agentDef(a).name || "").toLowerCase();
        return a.item_id.toLowerCase().indexOf(mention.toLowerCase()) >= 0
            || n.indexOf(mention.toLowerCase()) >= 0;
      });
      if (byPartial.length === 1) return byPartial[0];
      if (byPartial.length > 1) published = byPartial; // 多个候选，缩小范围再走关键词
    }

    // 3. 关键词意图匹配
    var keywordMatch = _matchAgentByKeyword(prompt, published);
    if (keywordMatch) return keywordMatch;

    // 4. fallback：选最闲的（原负载均衡逻辑）
    var sorted = published.slice().sort(function (a, b) {
      var diff = activeTaskCount(a.item_id) - activeTaskCount(b.item_id);
      if (diff !== 0) return diff;
      return String(a.item_id).localeCompare(String(b.item_id));
    });
    return sorted[0] || null;
  }

  function findTask(taskId) {
    var found = null;
    state.tasks.forEach(function (task) { if (task.task_id === taskId) found = task; });
    return found;
  }

  function findSession(sessionId) {
    var found = null;
    state.sessions.forEach(function (session) { if (session.session_id === sessionId) found = session; });
    return found;
  }

  function taskName(text) {
    var clean = text.replace(/\s+/g, " ").trim();
    if (!clean) return "新指令";
    return clean.length > 24 ? clean.slice(0, 24) + "…" : clean;
  }

  function nearBottom() {
    var node = document.getElementById("chatScroll");
    if (!node) return true;
    return node.scrollHeight - node.scrollTop - node.clientHeight < 140;
  }

  function scrollBottom() {
    var node = document.getElementById("chatScroll");
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    window.requestAnimationFrame(function () {
      var again = document.getElementById("chatScroll");
      if (again) again.scrollTop = again.scrollHeight;
    });
  }

  function showComposerError(html) {
    var node = document.getElementById("composerError");
    if (!node) return;
    if (!html) {
      node.classList.add("hidden");
      node.innerHTML = "";
      return;
    }
    node.innerHTML = '<span>!</span><div>' + html + '</div>';
    node.classList.remove("hidden");
  }

  function applyCapability() {
    var notice = document.getElementById("capabilityNotice");
    var send = document.getElementById("sendBtn");
    if (state.authRequired) {
      notice.classList.add("hidden");
      send.disabled = true;
      return;
    }
    if (state.capabilityKnown && state.capability === false) {
      notice.classList.remove("hidden");
      send.disabled = true;
    } else {
      notice.classList.add("hidden");
      send.disabled = false;
    }
  }

  function renderSessions() {
    var host = document.getElementById("sessionList");
    var keyword = state.search.trim().toLowerCase();
    var items = state.sessions.filter(function (session) {
      if (!keyword) return true;
      return String(session.title || "").toLowerCase().indexOf(keyword) >= 0;
    });
    document.getElementById("sessionsCount").textContent = state.sessions.length ? "共 " + state.sessions.length + " 个" : "暂无会话";
    if (!state.sessions.length) {
      host.innerHTML = '<div class="empty-box" style="margin:8px;padding:14px;font-size:12px;">还没有历史会话。<br>直接输入指令即可自动新建。</div>';
      return;
    }
    if (!items.length) {
      host.innerHTML = '<div class="empty-box" style="margin:8px;padding:14px;font-size:12px;">没有匹配的会话</div>';
      return;
    }
    host.innerHTML = items.map(function (session) {
      var title = session.title || "未命名会话";
      var active = session.session_id === state.currentSessionId ? " active" : "";
      var unread = state.unread[session.session_id] ? '<span class="unread" title="有新反馈"></span>' : "";
      return '<button class="session-item' + active + '" type="button" data-session="' + UI.esc(session.session_id) + '">' +
        unread +
        '<span class="name">' + UI.esc(title) + '</span>' +
        '<span class="time">' + UI.esc(UI.fmtAgo(session.updated_at)) + '</span>' +
      '</button>';
    }).join("");
  }

  function updateChatHeader() {
    var session = state.currentSessionId ? findSession(state.currentSessionId) : null;
    document.getElementById("chatTitle").textContent = session ? (session.title || "未命名会话") : "新指令";
    var tag = document.getElementById("sessionStateTag");
    if (session) {
      tag.className = "tag ok";
      tag.textContent = "已关联会话";
    } else {
      tag.className = "tag muted";
      tag.textContent = "未关联会话";
    }
    document.getElementById("chatMeta").textContent = session ? "更新于 " + UI.fmtTime(session.updated_at) : "发送后自动新建会话";
    document.getElementById("devSessionId").textContent = state.currentSessionId || "尚未创建";
  }

  function renderMessages() {
    if (!state.messages.length) {
      return '<div class="empty-box"><strong>' + (state.currentSessionId ? "这个会话还没有消息" : "告诉我你要做什么") + '</strong>' +
        (state.currentSessionId
          ? "可以直接在下方输入新的指令，或从左侧选择其他会话。"
          : "平台会整理任务信息、选择执行智能体并反馈结果。你不需要填写 Session、Run 或 Agent ID。") +
        "</div>";
    }
    return state.messages.map(function (message) {
      var role = message.role === "user" ? "user" : "assistant";
      var meta = message.metadata || {};
      var taskId = meta.task_id || message.task_id || null;
      // 头像 + 名字：user / assistant 各有独立逻辑
      var avatar, name, avatarStyle = "";
      if (role === "user") {
        name = "我";
        avatar = "我";
      } else {
        // assistant：优先用 message._agent_id（loadMessages 后处理关联的），
        // 再 fallback 从 task 找，最后 fallback "执行智能体"
        var agentId = message._agent_id || (taskId && findTask(taskId) ? findTask(taskId).agent_id : null);
        var av = agentAvatar(agentId);
        if (av) {
          name = av.name;
          avatar = av.char;
          avatarStyle = ' style="background:' + av.bg + ';color:' + av.fg + ';"';
        } else {
          name = "执行智能体";
          avatar = "智";
        }
      }
      var taskTag = "";
      var task = taskId ? findTask(taskId) : null;
      if (taskId) {
        var label = task ? UI.taskLabel(task.status) : "已关联任务";
        var tone = task ? UI.taskTone(task.status) : "muted";
        taskTag = '<span class="tag ' + tone + '">' + UI.esc(label) + '</span>';
        // 失败 / 超时任务显示重试按钮
        if (task && (task.status === "failed" || task.status === "timed_out")) {
          taskTag += '<button class="btn btn-sm btn-retry" type="button" data-retry="' + UI.esc(taskId) + '" title="重新执行此任务">重试</button>';
        }
      }
      // assistant 消息：content 可能是 Markdown 片段（从 run_result 里提出来的 output），
      // 或者是 extractFirstParagraph 截断的人话片段。都走 renderMarkdown 渲染。
      var bodyHtml;
      if (role === "assistant") {
        var taskInfo = taskId ? findTask(taskId) : null;
        var fullMarkdown = taskInfo ? UI.taskOutputText(taskInfo) : "";
        var brief = message.content || (fullMarkdown ? UI.extractFirstParagraph(fullMarkdown) : "已完成。");
        brief = typeof brief === "string" ? brief : String(brief);
        // 用 renderMarkdown 渲染 brief（处理表格→列表、粗体、代码等），避免原始 Markdown 被直接 esc
        if (fullMarkdown && fullMarkdown !== brief) {
          bodyHtml =
            '<div class="msg-body">' + UI.renderMarkdown(brief) + '</div>' +
            '<details class="msg-details"><summary>查看完整分析</summary>' +
            '<div class="mdl-out" style="margin-top:8px;">' + UI.renderMarkdown(fullMarkdown) + '</div></details>';
        } else {
          bodyHtml = '<div class="msg-body">' + UI.renderMarkdown(brief) + '</div>';
        }
      } else {
        bodyHtml = '<div class="msg-body">' + UI.esc(message.content || "") + '</div>';
      }
      return '<div class="msg ' + role + '">' +
        '<div class="avatar"' + avatarStyle + '>' + avatar + '</div>' +
        '<div class="msg-bubble">' +
          '<div class="msg-name">' + UI.esc(name) + '</div>' +
          bodyHtml +
          '<div class="msg-foot"><span>' + UI.esc(UI.fmtTime(message.created_at)) + '</span>' +
            (taskId ? '<span>任务 ' + UI.esc(UI.shortId(taskId)) + '</span>' + taskTag : "") +
          '</div>' +
        '</div>' +
      '</div>';
    }).join("");
  }

  function priorityLabel(value) {
    return value === 50 ? "低" : value === 500 ? "高" : "普通";
  }

  function renderConfirm() {
    if (!state.pending) return "";
    var agent = resolveAgent(state.pending.text);
    var agentName = agent ? agentDef(agent).name : "暂无已发布智能体";
    var project = (document.getElementById("projectInput").value || "").trim();
    return '<div class="feedback-card">' +
      '<div class="feedback-head"><b>任务确认</b><span class="tag info">待确认下达</span></div>' +
      '<div class="confirm-grid">' +
        '<div><span class="k">任务名称：</span><span class="v">' + UI.esc(taskName(state.pending.text)) + '</span></div>' +
        '<div><span class="k">任务目标：</span><span class="v">' + UI.esc(state.pending.text) + '</span></div>' +
        '<div><span class="k">执行智能体：</span><span class="v">' + UI.esc(agentName) + (state.selectedAgentId === "auto" ? "（自动选择）" : "") + '</span></div>' +
        '<div><span class="k">所属项目：</span><span class="v">' + (project ? UI.esc(project) : "未指定（不关联项目）") + '</span></div>' +
        '<div><span class="k">优先级：</span><span class="v">' + priorityLabel(Number(document.getElementById("prioritySelect").value)) + '</span></div>' +
      '</div>' +
      '<div class="risk">可能涉及的风险：由系统策略判定，页面无法预先确认。L2 外部或不可逆操作需要审批；L3 资金、安全与权限操作由有权限的人直接执行。点击确认只代表提交任务，不能代替正式审批。</div>' +
      '<div class="feedback-actions">' +
        '<button class="btn btn-primary btn-sm" type="button" data-action="confirm">确认下达</button>' +
        '<button class="btn btn-sm" type="button" data-action="cancel">返回修改</button>' +
      '</div>' +
    '</div>';
  }

  function feedbackStatusText(entry) {
    return UI.taskLabel(entry.status);
  }

  function renderFeedback() {
    if (!state.feedback.length) return "";
    // 防御性过滤：只展示属于当前 session 的 entry，
    // 避免 syncFeedbackFromTasks 以外的路径（如 pollTask、directSubmit）
    // 把其他 session 的 entry 意外混进数组时仍然能正确显示。
    var scoped = state.feedback.filter(function (item) {
      if (!state.currentSessionId) return false;
      // entry 自带 sessionId → 直接校验
      if (item.sessionId) return item.sessionId === state.currentSessionId;
      // 兜底：从 state.tasks 里反查 task 的 session_id
      var task = findTask(item.taskId);
      return !!(task && task.session_id === state.currentSessionId);
    });
    if (!scoped.length) return "";
    // 右侧面板只显示最新的一个 entry（按 createdAt 倒序）
    var latest = scoped.reduce(function (a, b) {
      return (!a || (a.createdAt || "") < (b.createdAt || "")) ? b : a;
    }, null);
    if (!latest) return "";
    var entry = latest;
    var tone = UI.taskTone(entry.status);
    var logs = "";
    if (entry.logsOpen) {
      if (entry.logsLoading) {
        logs = '<div class="log-list"><div class="log-row">正在读取执行日志…</div></div>';
      } else if (entry.logsError) {
        logs = '<div class="log-list"><div class="log-row">' + UI.esc(entry.logsError) + '</div></div>';
      } else if (entry.logs && entry.logs.length) {
        logs = '<div class="log-list">' + entry.logs.map(function (line) {
          return '<div class="log-row"><span>' + UI.esc(line.time ? UI.fmtTime(line.time) : "—") + '</span><span>' + UI.esc(line.type) + ' · ' + UI.esc(line.text) + '</span></div>';
        }).join("") + "</div>";
      } else {
        logs = '<div class="log-list"><div class="log-row">暂无可展示的执行事件。</div></div>';
      }
    }
    var resultText = "";
    if (entry.status === "succeeded" && entry.output) {
      // 右侧面板给人话摘要 + 可折叠的完整分析
      var brief = UI.extractFirstParagraph(entry.output) || "已完成。";
      resultText = '<div class="feedback-body">' +
        '<b>执行结果</b><div style="margin-top:5px;">' + UI.esc(brief) + '</div>' +
        '<details class="msg-details" style="margin-top:4px;"><summary>查看完整分析</summary>' +
        '<div class="mdl-out" style="margin-top:6px;">' + UI.renderMarkdown(entry.output) + '</div></details>' +
      '</div>';
    } else if ((entry.status === "failed" || entry.status === "timed_out") && entry.error) {
      resultText = '<div class="feedback-body" style="color:#8b2f28;"><b>失败原因</b><div class="pre" style="margin-top:5px;">' + UI.esc(entry.error) + '</div></div>';
    } else if (entry.status === "paused") {
      resultText = '<div class="feedback-body">任务已暂停，需要人工干预后继续。可到任务全局看板查看运行记录，或到现有工作台处理审批与干预。</div>';
    } else if (entry.status === "queued" || entry.status === "running" || entry.status === "cancelling") {
      resultText = '<div class="feedback-body">任务已接收，平台正在处理。状态会自动刷新，不需要重复提交。</div>';
    }
    return '<div class="feedback-card" data-feedback="' + UI.esc(entry.taskId || "") + '">' +
      '<div class="feedback-head"><b>执行反馈</b><span class="tag ' + tone + '">' + UI.esc(feedbackStatusText(entry)) + '</span>' +
        (entry.taskId ? '<span class="mono">任务 ' + UI.esc(UI.shortId(entry.taskId)) + '</span>' : "") +
      '</div>' +
      resultText +
      (entry.sessionWarning ? '<div class="feedback-body" style="color:#8c5a10;">' + UI.esc(entry.sessionWarning) + '</div>' : "") +
      '<div class="feedback-actions">' +
        ((entry.status === "failed" || entry.status === "timed_out")
          ? '<button class="btn btn-sm btn-primary btn-retry" type="button" data-retry="' + UI.esc(entry.taskId || "") + '">重试任务</button>'
          : "") +
        '<button class="btn btn-sm" type="button" data-logs="' + UI.esc(entry.taskId || "") + '">查看执行日志</button>' +
        '<a class="btn btn-sm" href="/task-board">打开任务全局看板</a>' +
        '<a class="btn btn-sm" href="/app">打开工作台处理审批</a>' +
      '</div>' +
      '<div class="msg-foot" style="margin-top:7px;">' +
        '<span>提交于 ' + UI.esc(UI.fmtTime(entry.createdAt)) + '</span>' +
      '</div>' +
      logs +
    '</div>';
  }

  function renderSidePanel() {
    var slot = document.getElementById("confirmSlot");
    if (!slot) return;
    var html = renderConfirm() + renderFeedback();
    slot.innerHTML = html;
    slot.classList.toggle("empty", !html.trim());
    var hint = document.getElementById("configStatusHint");
    if (hint) {
      var hasConfirm = !!state.pending;
      // 和 renderFeedback 保持同一份 scoped 过滤：只看当前 session 的 feedback
      var scopedFeedback = state.feedback.filter(function (item) {
        if (!state.currentSessionId) return false;
        if (item.sessionId) return item.sessionId === state.currentSessionId;
        var t = findTask(item.taskId);
        return !!(t && t.session_id === state.currentSessionId);
      });
      var hasFeedback = scopedFeedback.length > 0;
      if (hasConfirm) hint.textContent = "待确认下达";
      else if (hasFeedback) {
        var latest = scopedFeedback.reduce(function (a, b) {
          return (!a || (a.createdAt || "") < (b.createdAt || "")) ? b : a;
        }, null);
        hint.textContent = latest ? latest.status : "空闲";
      }
      else hint.textContent = "空闲";
    }
  }

  function renderChat(forceScroll) {
    var host = document.getElementById("chatScroll");
    var stick = forceScroll || nearBottom();
    renderSidePanel();
    host.innerHTML = '<div id="messageList">' + renderMessages() + '</div>';
    if (stick) scrollBottom();
  }

  function renderFeedbackOnly() {
    renderSidePanel();
  }

  function renderAgents() {
    var host = document.getElementById("agentPick");
    if (state.selectedAgentId !== "auto") {
      var stillAvailable = state.agents.some(function (item) {
        return item.item_id === state.selectedAgentId && item.status === "published";
      });
      if (!stillAvailable) state.selectedAgentId = "auto";
    }
    if (state.authRequired) {
      host.innerHTML = '<div class="empty-box" style="padding:14px;font-size:12px;">未登录，无法读取智能体</div>';
      return;
    }
    if (!state.agents.length) {
      host.innerHTML = '<div class="empty-box" style="padding:14px;font-size:12px;"><strong>暂无已注册智能体</strong>请先在现有工作台的智能体管理中创建并发布。</div>';
      return;
    }
    var autoActive = state.selectedAgentId === "auto" ? " active" : "";
    var html = '<button class="agent-card' + autoActive + '" type="button" data-agent="auto">' +
      '<div class="row1"><b>自动选择</b><span class="tag info">本地规则</span></div>' +
      '<div class="desc">按已发布智能体的当前执行任务数最少来选择，不是智能路由。</div>' +
    '</button>';
    html += state.agents.map(function (item) {
      var info = agentDef(item);
      var available = item.status === "published";
      var active = state.selectedAgentId === item.item_id ? " active" : "";
      var tone = item.status === "published" ? "ok" : item.status === "disabled" ? "bad" : "muted";
      var statusText = item.status === "published" ? "已发布" : item.status === "draft" ? "草稿" : item.status === "disabled" ? "已停用" : (item.status || "未知");
      var desc = info.desc || "暂无用途描述";
      var caps = info.caps || "能力描述未提供";
      return '<button class="agent-card' + active + '" type="button" data-agent="' + UI.esc(item.item_id) + '"' +
        (available ? "" : ' disabled style="opacity:.62;cursor:not-allowed;"') + '>' +
        '<div class="row1"><b>' + UI.esc(info.name) + '</b><span class="tag ' + tone + '">' + UI.esc(statusText) + '</span>' +
          (available ? "" : '<span class="tag muted">不可选</span>') + '</div>' +
        '<div class="desc">' + UI.esc(desc) + '</div>' +
        '<div class="desc">能力：' + UI.esc(caps) + ' · 在线状态未接入</div>' +
      '</button>';
    }).join("");
    host.innerHTML = html;
    document.getElementById("devAgentId").textContent = state.selectedAgentId === "auto"
      ? "自动选择"
      : (function () { var a = resolveAgent(); return a ? agentDef(a).name + "（" + state.selectedAgentId + "）" : "未选择"; })();
  }

  function loadMessages(silent) {
    if (!state.currentSessionId || state.loadingMessages) return Promise.resolve();
    state.loadingMessages = true;
    return UI.request(API + "/sessions/" + encodeURIComponent(state.currentSessionId) + "/messages?limit=100").then(function (res) {
      if (res.ok) {
        var raw = (res.data && res.data.items) || [];
        // 去重 1：每条 user message 被后端 run_input 内部快照和前端 command-center 各写一次，
        // assistant 只有 run_result。只保留 command-center / run_result / 其他非 run_input 来源。
        var filtered = raw.filter(function (msg) {
          var meta = msg.metadata || {};
          return meta.source !== "run_input";
        });
        // 去重 2：同 role + 同 content 的连续重复消息只留一条（取时间最新的）。
        // 偶尔 command-center POST 会被重试导致相同 user message 被写两次。
        filtered = filtered.filter(function (msg, idx, arr) {
          if (idx === 0) return true;
          var prev = arr[idx - 1];
          if (msg.role === "user" && prev.role === "user" &&
              msg.content === prev.content) return false;
          return true;
        });
        // 后处理：assistant content 如果是 JSON 字符串（run_result），提取 output；
        // 同时从 metadata.task_id 关联 task → 找到 agent_id/agent_name
        state.messages = filtered.map(function (msg) {
          var m = Object.assign({}, msg);
          m.metadata = m.metadata || {};
          if (m.role === "assistant") {
            var parsed = safeParseContent(m.content);
            if (parsed && typeof parsed === "string") {
              m.content = parsed;
            } else if (parsed && typeof parsed === "object") {
              m._parsed = parsed;
            }
            // 关联 task → agent 信息。task 的 run_id 就是 task_id，
            // state.tasks 里已经有 agent_id 了。
            var agentId = null;
            // 先从 metadata 里找 task_id / run_id
            var lookupId = m.metadata.task_id || m.metadata.run_id || m.run_id || null;
            if (!lookupId && m.session_id) {
              // fallback：从 state.tasks 里找同一个 session 最近的 task
              var sessionTasks = state.tasks.filter(function (t) {
                return t.session_id === m.session_id || t.session_id === state.currentSessionId;
              }).sort(function (a, b) {
                return String(b.created_at || "") > String(a.created_at || "") ? 1 : -1;
              });
              if (sessionTasks.length) lookupId = sessionTasks[0].task_id;
            }
            if (lookupId) {
              var task = findTask(lookupId) || state.tasks.find(function (t) {
                return t.task_id === lookupId;
              });
              if (task && task.agent_id) agentId = task.agent_id;
            }
            if (agentId) m._agent_id = agentId;
          }
          return m;
        });
        var newest = state.messages.reduce(function (acc, item) {
          return !acc || String(item.created_at) > String(acc) ? item.created_at : acc;
        }, null);
        if (newest) state.lastSeen[state.currentSessionId] = newest;
        var force = state.forceScrollOnce;
        state.forceScrollOnce = false;
        renderChat(force || !silent);
      } else if (!silent) {
        state.messages = [];
        renderChat(true);
        showComposerError("会话消息读取失败：" + UI.esc(UI.probeReason(res)));
      }
    }).then(function () {
      state.loadingMessages = false;
    });
  }

  // 尝试把 message content 从 JSON 字符串里提取纯文本。
  // 返回：纯文本（如果提取成功）| null（如果 content 不是 JSON 或提取不到）
  function safeParseContent(content) {
    if (!content || typeof content !== "string") return null;
    var trimmed = content.trim();
    if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return null;
    try {
      var obj = JSON.parse(trimmed);
      if (typeof obj !== "object" || obj === null) return null;
      // 从常见的 LLM run output 结构里找纯文本
      for (var i = 0; i < ["output", "content", "result", "text", "answer", "message"].length; i++) {
        var key = ["output", "content", "result", "text", "answer", "message"][i];
        if (typeof obj[key] === "string" && obj[key].trim()) return obj[key];
      }
      // output 等常规字段为空，但有 tool_calls_history → 调 summarizeToolCalls 生成人话摘要
      var history = Array.isArray(obj.tool_calls_history) ? obj.tool_calls_history : null;
      if (history && history.length) {
        var summary = UI.summarizeToolCalls(history);
        if (summary) return summary;
      }
      // 是 run_output 但没提取到文本
      if (obj.usage || obj.prompt) return "已执行完毕，但没有生成文本回复。";
      return null;
    } catch (e) {
      return null; // 不是 JSON
    }
  }

  function selectSession(sessionId) {
    state.currentSessionId = sessionId;
    state.messages = [];
    state.pending = null;
    state.feedback = [];
    state.unread[sessionId] = false;
    state.lastSeen[sessionId] = new Date().toISOString();
    updateChatHeader();
    renderSessions();
    syncFeedbackFromTasks();
    renderChat(true);
    loadMessages(false);
    loadMessagesAsync();
  }

  function loadMessagesAsync() {
    window.setTimeout(function () { loadMessages(true); }, 400);
  }

  function syncFeedbackFromTasks() {
    if (!state.currentSessionId) {
      // 没有活跃会话 → 不应显示任何 feedback，彻底清空
      if (state.feedback.length) { state.feedback = []; renderFeedbackOnly(); }
      return;
    }
    // 以当前 session 的任务为准，完全重建 feedback 数组。
    // 旧的、不属于当前 session 的 entry 一律丢弃，避免"别的会话的状态残留到右侧面板"。
    var related = state.tasks.filter(function (task) { return task.session_id === state.currentSessionId; });
    related.sort(function (a, b) { return String(b.updated_at).localeCompare(String(a.updated_at)); });
    var rebuilt = [];
    related.slice(0, 6).forEach(function (task) {
      // 先从旧 state.feedback 里找到同一个 task 的已有 entry，保留用户交互状态
      // （如 logsOpen、logsLoading、logsError、logs）
      var prev = null;
      state.feedback.forEach(function (item) { if (item.taskId === task.task_id) prev = item; });
      var entry = {
        taskId: task.task_id,
        sessionId: task.session_id || state.currentSessionId,
        text: UI.taskInputText(task) || UI.taskTitle(task),
        createdAt: task.created_at,
        status: task.status,
        runId: task.run_id,
        output: UI.taskOutputText(task) || "",
        error: task.last_error || "",
        logsOpen: prev ? !!prev.logsOpen : false,
        logsLoading: prev ? !!prev.logsLoading : false,
        logsError: prev ? (prev.logsError || "") : "",
        logs: prev && prev.logs ? prev.logs : null,
        sessionWarning: prev ? (prev.sessionWarning || "") : ""
      };
      rebuilt.push(entry);
    });
    state.feedback = rebuilt;
    state.feedback.sort(function (a, b) { return String(a.createdAt).localeCompare(String(b.createdAt)); });
    renderFeedbackOnly();
  }

  function refreshTasks(silent) {
    return UI.request(API + "/workbench/tasks?limit=200").then(function (res) {
      if (!res.ok) return;
      state.tasks = (res.data && res.data.items) || [];
      var nowIso = new Date().toISOString();
      state.sessions.forEach(function (session) {
        if (!state.lastSeen[session.session_id]) state.lastSeen[session.session_id] = nowIso;
        var latest = null;
        state.tasks.forEach(function (task) {
          if (task.session_id === session.session_id && (!latest || String(task.updated_at) > String(latest))) latest = task.updated_at;
        });
        if (session.session_id !== state.currentSessionId && latest && latest > state.lastSeen[session.session_id]) {
          state.unread[session.session_id] = true;
        }
      });
      renderSessions();
      syncFeedbackFromTasks();
      if (!silent) renderChat(false);
    });
  }

  function loadSessions() {
    return UI.request(API + "/sessions?limit=100").then(function (res) {
      if (res.ok) {
        state.sessions = (res.data && res.data.items) || [];
        var nowIso = new Date().toISOString();
        state.sessions.forEach(function (session) {
          if (!state.lastSeen[session.session_id]) state.lastSeen[session.session_id] = nowIso;
        });
        renderSessions();
        updateChatHeader();
      }
    });
  }

  function pollTask(taskId) {
    if (!taskId || state.polls[taskId]) return;
    function tick() {
      UI.request(API + "/workbench/tasks/" + encodeURIComponent(taskId)).then(function (res) {
        if (!res.ok) return;
        var task = res.data;
        var entry = null;
        state.feedback.forEach(function (item) { if (item.taskId === taskId) entry = item; });
        if (entry) {
          entry.status = task.status;
          entry.runId = task.run_id || entry.runId;
          entry.output = UI.taskOutputText(task) || entry.output || "";
          entry.error = task.last_error || entry.error || "";
          renderFeedbackOnly();
        }
        var index = -1;
        state.tasks.forEach(function (item, i) { if (item.task_id === taskId) index = i; });
        if (index >= 0) state.tasks[index] = task;
        else state.tasks.unshift(task);
        if (TERMINAL.indexOf(task.status) >= 0) {
          window.clearInterval(state.polls[taskId]);
          delete state.polls[taskId];
          loadSessions();
          refreshTasks(true);
        }
      });
    }
    tick();
    state.polls[taskId] = window.setInterval(tick, 5000);
  }

  function ensureSession(title) {
    if (state.currentSessionId) return Promise.resolve(state.currentSessionId);
    return UI.request(API + "/sessions", { method: "POST", body: { title: title } }).then(function (res) {
      if (res.ok && res.data && res.data.session_id) {
        state.currentSessionId = res.data.session_id;
        state.lastSeen[res.data.session_id] = new Date().toISOString();
        state.sessionWarning = "";
        loadSessions();
        updateChatHeader();
        return res.data.session_id;
      }
      state.sessionWarning = "会话创建失败（" + (res.error || "未知原因") + "），任务仍会提交，但不会出现在左侧会话列表。";
      return null;
    });
  }

  function appendUserMessage(sessionId, text, taskId) {
    if (!sessionId) return Promise.resolve();
    return UI.request(API + "/sessions/" + encodeURIComponent(sessionId) + "/messages", {
      method: "POST",
      body: { role: "user", content: text, metadata: { source: "command-center", task_id: taskId || null } }
    }).then(function (res) {
      if (res.ok) loadMessagesAsync();
      else state.sessionWarning = "会话消息写入失败：" + (res.error || "未知原因");
    });
  }

  function submitPending() {
    var pending = state.pending;
    if (!pending || state.submitting) return;
    if (state.authRequired) {
      showComposerError("当前未登录，无法提交任务。请先打开 <a href=\"/app\">现有工作台</a> 登录。");
      return;
    }
    if (state.capabilityKnown && state.capability === false) {
      showComposerError("当前部署未启用任务执行能力，提交入口已关闭，未进行模拟提交。");
      return;
    }
    var agent = resolveAgent(pending.text);
    if (!agent) {
      showComposerError("当前没有已发布的智能体，无法提交任务。请先在现有工作台发布智能体。");
      return;
    }
    var body = {
      agent_id: agent.item_id,
      input: { prompt: pending.text },
      priority: Number(document.getElementById("prioritySelect").value) || 100,
      idempotency_key: crypto.randomUUID(),
    };
    var projectId = (document.getElementById("projectInput").value || "").trim();
    if (projectId) body.project_id = projectId;

    var confirmBtn = document.querySelector('#confirmSlot button[data-action="confirm"]') ||
                     document.querySelector('button[data-action="confirm"]');
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "提交中…";
    }
    showComposerError("");
    state.submitting = true;

    ensureSession(taskName(pending.text)).then(function (sessionId) {
      if (sessionId) body.session_id = sessionId;
      return UI.request(API + "/workbench/tasks", { method: "POST", body: body }).then(function (res) {
        return { res: res, sessionId: sessionId };
      });
    }).then(function (result) {
      state.submitting = false;
      var res = result.res;
      if (!res.ok) {
        if (confirmBtn) {
          confirmBtn.disabled = false;
          confirmBtn.textContent = "确认下达";
        }
        showComposerError("<strong>提交失败：" + UI.esc(res.error) + "</strong><br>输入内容已保留，可以修改后重新提交。");
        return;
      }
      var taskId = (res.data && res.data.task_id) || null;
      var initialStatus = (res.data && res.data.status) || "queued";
      state.pending = null;
      state.feedback.push({
        taskId: taskId,
        sessionId: result.sessionId || state.currentSessionId || null,
        text: pending.text,
        status: initialStatus,
        runId: (res.data && res.data.run_id) || null,
        output: "",
        error: "",
        createdAt: new Date().toISOString(),
        logsOpen: false,
        sessionWarning: state.sessionWarning
      });
      state.sessionWarning = "";
      document.getElementById("commandInput").value = "";
      resizeInput();
      state.forceScrollOnce = true;
      renderChat(true);
      if (result.sessionId) appendUserMessage(result.sessionId, pending.text, taskId);
      else state.sessionWarning = "";
      loadSessions().then(function () { refreshTasks(true); });
      pollTask(taskId);
    }).catch(function (err) {
      state.submitting = false;
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "确认下达";
      }
      showComposerError("<strong>提交失败：网络错误</strong><br>" + UI.esc(err && err.message ? err.message : "未知错误") + "。输入内容已保留。");
    });
  }

  function prepareConfirm() {
    // 旧流程（两步走）：先出确认卡再下达。保留此函数是为了不破坏潜在调用，
    // 但 UI 入口已全部切到 directSubmit。
    if (state.authRequired) {
      showComposerError("当前未登录，无法提交任务。请先打开 <a href=\"/app\">现有工作台</a> 登录。");
      return;
    }
    if (state.capabilityKnown && state.capability === false) {
      showComposerError("当前部署未启用任务执行能力，提交入口已关闭，未进行模拟提交。");
      return;
    }
    var input = document.getElementById("commandInput");
    var text = input.value.trim();
    if (!text) {
      showComposerError("请先输入你要做的事情。");
      input.focus();
      return;
    }
    if (!publishedAgents().length) {
      showComposerError("当前没有已发布的智能体，无法下达任务。请先在工作台发布智能体，或等待注册表恢复。");
      return;
    }
    showComposerError("");
    state.pending = { text: text };
    renderChat(true);
  }

  // 一步到位：点击发送直接下达任务，跳过中间确认卡。
  // 合并了 prepareConfirm 的前置校验 + submitPending 的 POST 逻辑。
  function directSubmit() {
    if (state.submitting) return;
    var input = document.getElementById("commandInput");
    var text = input.value.trim();
    if (!text) {
      showComposerError("请先输入你要做的事情。");
      input.focus();
      return;
    }
    if (state.authRequired) {
      showComposerError("当前未登录，无法提交任务。请先打开 <a href=\"/app\">现有工作台</a> 登录。");
      return;
    }
    if (state.capabilityKnown && state.capability === false) {
      showComposerError("当前部署未启用任务执行能力，提交入口已关闭。");
      return;
    }
    var agent = resolveAgent(text);
    if (!agent) {
      showComposerError("当前没有已发布的智能体，无法下达任务。请先在工作台发布智能体。");
      return;
    }
    var body = {
      agent_id: agent.item_id,
      input: { prompt: text },
      priority: Number(document.getElementById("prioritySelect").value) || 100,
      idempotency_key: crypto.randomUUID(),
    };
    var projectId = (document.getElementById("projectInput").value || "").trim();
    if (projectId) body.project_id = projectId;

    var sendBtn = document.getElementById("sendBtn");
    if (sendBtn) { sendBtn.disabled = true; sendBtn.textContent = "下达中…"; }
    showComposerError("");
    state.submitting = true;

    var taskNameLocal = text.length > 24 ? text.slice(0, 24) + "…" : text;
    ensureSession(taskNameLocal).then(function (sessionId) {
      if (sessionId) body.session_id = sessionId;
      return UI.request(API + "/workbench/tasks", { method: "POST", body: body }).then(function (res) {
        return { res: res, sessionId: sessionId };
      });
    }).then(function (result) {
      state.submitting = false;
      if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "发送"; }
      var res = result.res;
      if (!res.ok) {
        showComposerError("<strong>下达失败：" + UI.esc(res.error) + "</strong><br>输入内容已保留，可以修改后重新下达。");
        return;
      }
      var taskId = (res.data && res.data.task_id) || null;
      var initialStatus = (res.data && res.data.status) || "queued";
      state.feedback.push({
        taskId: taskId,
        sessionId: result.sessionId || state.currentSessionId || null,
        text: text,
        status: initialStatus,
        runId: (res.data && res.data.run_id) || null,
        output: "", error: "",
        createdAt: new Date().toISOString(),
        logsOpen: false,
        sessionWarning: state.sessionWarning
      });
      state.sessionWarning = "";
      input.value = "";
      resizeInput();
      state.forceScrollOnce = true;
      // 不再渲染 confirm 卡，直接 refresh 面板 + 对话
      renderSidePanel();
      var chatHost = document.getElementById("chatScroll");
      if (chatHost) chatHost.innerHTML = '<div id="messageList">' + renderMessages() + '</div>';
      scrollBottom();
      if (result.sessionId) appendUserMessage(result.sessionId, text, taskId);
      loadSessions().then(function () { refreshTasks(true); });
      pollTask(taskId);
    }).catch(function (err) {
      state.submitting = false;
      if (sendBtn) { sendBtn.disabled = false; sendBtn.textContent = "发送"; }
      showComposerError("<strong>下达失败：网络错误</strong><br>" + UI.esc(err && err.message ? err.message : "未知错误") + "。输入内容已保留。");
    });
  }

  function openLogs(taskId) {
    var entry = null;
    state.feedback.forEach(function (item) { if (item.taskId === taskId) entry = item; });
    if (!entry) return;
    entry.logsOpen = !entry.logsOpen;
    if (!entry.logsOpen) {
      renderFeedbackOnly();
      return;
    }
    if (!entry.runId) {
      entry.logsError = "任务尚未产生运行记录，暂时没有执行日志。";
      renderFeedbackOnly();
      return;
    }
    entry.logsLoading = true;
    entry.logsError = "";
    renderFeedbackOnly();
    UI.request(API + "/runs/" + encodeURIComponent(entry.runId) + "/events?limit=200").then(function (res) {
      entry.logsLoading = false;
      if (!res.ok) {
        entry.logsError = "执行日志读取失败：" + UI.probeReason(res);
        renderFeedbackOnly();
        return;
      }
      var items = (res.data && res.data.items) || [];
      entry.logs = items.map(function (event) {
        var type = event.event_type || event.type || event.kind || "事件";
        var payload = event.payload !== undefined ? event.payload : (event.data !== undefined ? event.data : event.detail);
        if (payload === undefined) payload = event;
        var text = typeof payload === "string" ? payload : JSON.stringify(payload);
        if (text.length > 500) text = text.slice(0, 500) + "…";
        return { time: event.occurred_at || event.created_at || event.timestamp || "", type: String(type), text: text };
      });
      renderFeedbackOnly();
    });
  }

  function retryTask(taskId) {
    if (!taskId || state.submitting) return;
    var task = findTask(taskId);
    if (!task) {
      showComposerError("未找到该任务，无法重试。");
      return;
    }
    if (task.status !== "failed" && task.status !== "timed_out") {
      showComposerError("只有失败或超时的任务才能重试。");
      return;
    }
    var entry = null;
    state.feedback.forEach(function (item) { if (item.taskId === taskId) entry = item; });
    if (entry) {
      entry.status = "queued";
      entry.error = "";
      entry.output = "";
      entry.logsOpen = false;
      entry.logs = null;
      entry.logsError = "";
      entry.logsLoading = false;
    }
    var btn = document.querySelector('button[data-retry="' + CSS.escape(taskId) + '"]');
    if (btn) { btn.disabled = true; btn.textContent = "重试中…"; }
    renderChat(false);
    UI.request(API + "/workbench/tasks/" + encodeURIComponent(taskId) + "/retry", {
      method: "POST",
      body: { idempotency_key: crypto.randomUUID() }
    }).then(function (res) {
      if (!res.ok) {
        if (entry) { entry.status = task.status; entry.error = task.last_error || ""; }
        renderChat(false);
        showComposerError("<strong>重试失败：" + UI.esc(res.error) + "</strong>");
        return;
      }
      var newRunId = (res.data && res.data.run_id) || null;
      var newTaskId = (res.data && res.data.task_id) || taskId;
      if (entry) {
        entry.taskId = newTaskId;
        entry.runId = newRunId || entry.runId;
        entry.status = (res.data && res.data.status) || "queued";
        entry.createdAt = new Date().toISOString();
      }
      refreshTasks(true).then(function () {
        renderChat(true);
        pollTask(newTaskId);
      });
    }).catch(function (err) {
      if (entry) { entry.status = task.status; entry.error = task.last_error || ""; }
      renderChat(false);
      showComposerError("<strong>重试失败：网络错误</strong><br>" + UI.esc(err && err.message ? err.message : "未知错误"));
    });
  }

  function resizeInput() {
    var input = document.getElementById("commandInput");
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  function initExamples() {
    document.getElementById("examples").innerHTML = EXAMPLES.map(function (text) {
      return '<button class="chip" type="button" data-example="' + UI.esc(text) + '">' + UI.esc(text) + '</button>';
    }).join("");
  }

  function loadAll() {
    return Promise.all([
      UI.request("/auth/me"),
      UI.request(API + "/sessions?limit=100"),
      UI.request(API + "/registry/agent?limit=200"),
      UI.request(API + "/workbench/capabilities"),
      UI.request(API + "/workbench/tasks?limit=200")
    ]).then(function (results) {
      var me = results[0];
      var sessions = results[1];
      var agents = results[2];
      var caps = results[3];
      var tasks = results[4];

      state.authRequired = me.status === 401;
      if (sessions.ok) state.sessions = (sessions.data && sessions.data.items) || [];
      if (agents.ok) state.agents = normalizeAgents((agents.data && agents.data.items) || []);
      else if (agents.status === 401) state.authRequired = true;
      if (tasks.ok) state.tasks = (tasks.data && tasks.data.items) || [];
      if (caps.ok) {
        state.capabilityKnown = true;
        state.capability = !!(caps.data && caps.data.features && caps.data.features.task_execution);
      }

      var nowIso = new Date().toISOString();
      state.sessions.forEach(function (session) { if (!state.lastSeen[session.session_id]) state.lastSeen[session.session_id] = nowIso; });

      applyCapability();
      renderSessions();
      renderAgents();
      updateChatHeader();
      document.getElementById("refreshStamp").textContent = "最近刷新 " + UI.fmtClock(nowIso);

      var params = new URLSearchParams(window.location.search);
      var requested = params.get("session");
      if (requested && findSession(requested)) selectSession(requested);
      else renderChat(true);

      refreshTasks(true);

      if (state.authRequired) {
        showComposerError("当前未登录，无法读取会话与智能体，也无法提交任务。请先打开 <a href=\"/app\">现有工作台</a> 登录。");
      } else if (agents.status === 403) {
        showComposerError("当前账号无权限读取智能体注册表，暂时无法选择执行智能体。");
      } else if (agents.status === 503) {
        showComposerError("智能体注册表未接入，暂时无法选择执行智能体。");
      }
    }).catch(function (err) {
      console.error(err);
      showComposerError("页面初始化失败：" + UI.esc(err && err.message ? err.message : "未知错误"));
    });
  }

  UI.renderNav("command");
  UI.loadIdentity();
  initExamples();

  document.getElementById("sessionList").addEventListener("click", function (event) {
    var btn = event.target.closest("button[data-session]");
    if (!btn) return;
    selectSession(btn.getAttribute("data-session"));
  });

  document.getElementById("sessionSearch").addEventListener("input", function (event) {
    state.search = event.target.value || "";
    renderSessions();
  });

  document.getElementById("newSessionBtn").addEventListener("click", function () {
    state.currentSessionId = null;
    state.messages = [];
    state.feedback = [];
    state.pending = null;
    state.sessionWarning = "";
    renderSessions();
    updateChatHeader();
    renderChat(true);
    showComposerError("");
    document.getElementById("commandInput").focus();
  });

  document.getElementById("agentPick").addEventListener("click", function (event) {
    var card = event.target.closest("button[data-agent]");
    if (!card || card.disabled) return;
    state.selectedAgentId = card.getAttribute("data-agent");
    renderAgents();
    if (state.pending) renderChat(false);
  });

  document.getElementById("examples").addEventListener("click", function (event) {
    var chip = event.target.closest("button[data-example]");
    if (!chip) return;
    var input = document.getElementById("commandInput");
    input.value = chip.getAttribute("data-example");
    resizeInput();
    input.focus();
    showComposerError("");
  });

  document.getElementById("sendBtn").addEventListener("click", directSubmit);

  document.getElementById("commandInput").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      directSubmit();
    }
  });
  document.getElementById("commandInput").addEventListener("input", resizeInput);

  // click delegation 提到 document 级别——因为 confirm/feedback 卡片在右侧面板，
  // 但 data-action / data-logs 按钮也会出现在 chatScroll 里，全局捕获最稳。
  document.addEventListener("click", function (event) {
    var actionBtn = event.target.closest("button[data-action]");
    if (actionBtn) {
      var action = actionBtn.getAttribute("data-action");
      if (action === "confirm") submitPending();
      if (action === "cancel") {
        var text = state.pending ? state.pending.text : "";
        state.pending = null;
        var input = document.getElementById("commandInput");
        if (!input.value.trim()) input.value = text;
        resizeInput();
        renderChat(false);
      }
      return;
    }
    var logBtn = event.target.closest("button[data-logs]");
    if (logBtn) openLogs(logBtn.getAttribute("data-logs"));
    var retryBtn = event.target.closest("button[data-retry]");
    if (retryBtn) retryTask(retryBtn.getAttribute("data-retry"));
  });

  document.getElementById("configToggle").addEventListener("click", function () {
    document.getElementById("configCol").classList.toggle("open");
  });
  document.getElementById("configClose").addEventListener("click", function () {
    document.getElementById("configCol").classList.remove("open");
  });
  document.getElementById("prioritySelect").addEventListener("change", function (event) {
    state.priority = Number(event.target.value) || 100;
    if (state.pending) renderChat(false);
  });
  document.getElementById("projectInput").addEventListener("input", function () {
    if (state.pending) renderChat(false);
  });

  var lastMessagePoll = 0;
  UI.startPolling(function () {
    var now = Date.now();
    if (state.currentSessionId && document.visibilityState === "visible" && now - lastMessagePoll > 9000) {
      lastMessagePoll = now;
      return loadMessages(true);
    }
    return Promise.resolve();
  }, 10000);

  loadAll();
})();
