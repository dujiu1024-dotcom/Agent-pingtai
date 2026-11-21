/* 三个独立看板共用的接口与格式化工具。所有状态都来自真实接口，不做默认绿色填充。 */
(function () {
  "use strict";

  var API = "/api/platform/v1";

  var TASK_LABELS = {
    queued: "等待执行",
    running: "正在执行",
    cancelling: "正在取消",
    paused: "已暂停",
    succeeded: "执行成功",
    failed: "执行失败",
    cancelled: "已取消",
    timed_out: "已超时",
    interrupted: "已中断"
  };

  var TASK_TONES = {
    queued: "muted",
    running: "info",
    cancelling: "warn",
    paused: "warn",
    succeeded: "ok",
    failed: "bad",
    cancelled: "muted",
    timed_out: "bad",
    interrupted: "warn"
  };

  var SERVICE_META = {
    ok: { label: "正常", tone: "ok" },
    bad: { label: "异常", tone: "bad" },
    warn: { label: "警告", tone: "warn" },
    undetected: { label: "未检测", tone: "idle" },
    unconfigured: { label: "未配置", tone: "idle" }
  };

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function pad(n) { return String(n).padStart(2, "0"); }

  function fmtTime(value) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d.getTime())) return "—";
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function fmtClock(value) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d.getTime())) return "—";
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function fmtAgo(value) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d.getTime())) return "—";
    var diff = Date.now() - d.getTime();
    if (diff < 60 * 1000) return "刚刚";
    if (diff < 60 * 60 * 1000) return Math.floor(diff / 60000) + " 分钟前";
    if (diff < 24 * 60 * 60 * 1000) return Math.floor(diff / 3600000) + " 小时前";
    return fmtTime(value);
  }

  function shortId(id) {
    if (!id) return "—";
    var text = String(id);
    return text.length <= 12 ? text : text.slice(0, 8) + "…";
  }

  function readCookie(name) {
    var target = name + "=";
    var parts = String(document.cookie || "").split(";");
    for (var i = 0; i < parts.length; i += 1) {
      var part = parts[i].trim();
      if (part.indexOf(target) === 0) return decodeURIComponent(part.slice(target.length));
    }
    return "";
  }

  function csrfToken() {
    return readCookie("__Host-platform_csrf") || readCookie("platform_csrf");
  }

  function request(path, options) {
    var opts = Object.assign({ credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } }, options || {});
    var method = String(opts.method || "GET").toUpperCase();
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    if (method !== "GET" && method !== "HEAD") {
      var token = csrfToken();
      if (token) opts.headers["X-CSRF-Token"] = token;
    }
    return fetch(path, opts).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (err) { data = text; }
        }
        if (!res.ok) {
          var detail = "";
          if (data && typeof data === "object") {
            detail = data.detail || data.message || (data.error && data.error.message) || "";
            if (detail && typeof detail !== "string") detail = JSON.stringify(detail);
          }
          if (!detail && typeof data === "string") detail = data;
          return {
            ok: false,
            status: res.status,
            data: data,
            error: detail || ("请求失败（HTTP " + res.status + "）")
          };
        }
        return { ok: true, status: res.status, data: data, error: null };
      });
    }).catch(function (err) {
      return { ok: false, status: 0, data: null, error: "无法连接平台接口：" + (err && err.message ? err.message : "网络错误") };
    });
  }

  function probeKind(result) {
    if (!result) return "undetected";
    if (result.ok) return "ok";
    if (result.status === 401 || result.status === 403) return "denied";
    if (result.status === 503 || result.status === 404) return "unconfigured";
    return "undetected";
  }

  function probeReason(result) {
    if (!result) return "接口未调用";
    if (result.status === 401) return "未登录或登录已过期";
    if (result.status === 403) return "当前账号无权限查看";
    if (result.status === 503) return "该能力在当前部署中未启用";
    if (result.status === 404) return "该接口不存在或未接入";
    if (result.status === 0) return "接口不可达";
    return result.error || ("HTTP " + result.status);
  }

  function taskLabel(status) { return TASK_LABELS[status] || (status ? String(status) : "未知状态"); }
  function taskTone(status) { return TASK_TONES[status] || "muted"; }

  function taskTitle(task) {
    var input = (task && task.input) || {};
    var raw = input.title || input.goal || input.prompt || input.instruction || input.message || "";
    if (typeof raw !== "string") raw = JSON.stringify(raw);
    raw = raw.replace(/\s+/g, " ").trim();
    if (!raw) raw = "任务 " + shortId(task && task.task_id);
    return raw.length > 46 ? raw.slice(0, 46) + "…" : raw;
  }

  function taskInputText(task) {
    var input = (task && task.input) || {};
    var raw = input.goal || input.prompt || input.instruction || input.message || input.title || "";
    if (!raw) return "";
    if (typeof raw !== "string") return JSON.stringify(raw, null, 2);
    return raw;
  }

  function taskOutputText(task) {
    if (!task || task.output === null || task.output === undefined) return "";
    if (typeof task.output === "string") return task.output;
    var output = task.output;
    if (typeof output === "object") {
      // "output" 优先——它是 LLM run.output 里的 inner text key（比 summary/text 更具体）
      var keys = ["output", "summary", "text", "content", "message", "result", "answer"];
      for (var i = 0; i < keys.length; i += 1) {
        if (typeof output[keys[i]] === "string" && output[keys[i]].trim()) return output[keys[i]];
      }
      // output 等常规字段为空，但有 tool_calls_history → 调 summarizeToolCalls 生成人话摘要
      var history = Array.isArray(output.tool_calls_history) ? output.tool_calls_history : null;
      if (history && history.length) {
        var summary = summarizeToolCalls(history);
        if (summary) return summary;
      }
      return JSON.stringify(output, null, 2);
    }
    return String(output);
  }

  /** 从 tool_calls_history 生成人话摘要——**纯通用**，不硬编码任何工具。
   *  遍历每个 agentic call → 解析 result_preview JSON → 抽 status + message + 最多 2 个关键 key=value。
   *  跟后端 _fallback_output 逻辑对齐。 */
  function summarizeToolCalls(history) {
    if (!history || !Array.isArray(history) || !history.length) return "";
    var agentic = history.filter(function (h) { return h && h.agentic; });
    if (!agentic.length) return "";

    function parsePreview(p) {
      if (!p) return null;
      if (typeof p === "string") { try { var o = JSON.parse(p); return (o && typeof o === "object" && !Array.isArray(o)) ? o : null; } catch (e) { return null; } }
      if (typeof p === "object" && p !== null && !Array.isArray(p)) return p;
      return null;
    }
    function safeStr(v, def) { return v == null ? (def || "") : String(v); }
    function pickInteresting(obj) {
      var picked = [], used = {};
      var priority = ["summary", "balances", "entry_count", "name", "title", "period", "path"];
      for (var i = 0; i < priority.length; i++) {
        var k = priority[i];
        if (k in obj && !(k in used)) {
          used[k] = 1;
          var v = obj[k];
          if (typeof v === "object" && v !== null) picked.push(k + "=" + JSON.stringify(v).slice(0, 80));
          else picked.push(k + "=" + v);
          if (picked.length >= 2) return picked;
        }
      }
      for (var key in obj) {
        if (!Object.prototype.hasOwnProperty.call(obj, key)) continue;
        if (key in used || key === "status" || key === "message") continue;
        var vv = obj[key];
        if (typeof vv !== "object" && vv != null && String(vv)) { picked.push(key + "=" + vv); if (picked.length >= 2) break; }
      }
      return picked;
    }

    var parts = [];
    for (var j = 0; j < agentic.length; j++) {
      var call = agentic[j];
      var tn = safeStr(call.tool_name, "?");
      var preview = parsePreview(call.result_preview);
      if (!preview) {
        var raw = safeStr(call.result_preview);
        parts.push(tn + "：" + (raw ? raw.slice(0, 100) : "执行完毕。"));
        continue;
      }
      var st = safeStr(preview.status);
      var msg = safeStr(preview.message);
      if (msg) { parts.push(tn + "：" + msg); continue; }
      var interesting = pickInteresting(preview);
      if (interesting.length) { parts.push(tn + "（status=" + (st || "?") + "，" + interesting.join("，") + "）"); }
      else { parts.push(tn + "（status=" + (st || "?") + "）"); }
    }
    return parts.join("；") + "。";
  }

  function startPolling(fn, intervalMs) {
    var stopped = false;
    var timer = null;
    function schedule() {
      if (stopped) return;
      timer = window.setTimeout(run, intervalMs);
    }
    function run() {
      if (stopped) return;
      Promise.resolve()
        .then(fn)
        .catch(function (err) { console.error("刷新失败", err); })
        .then(schedule);
    }
    run();
    return {
      stop: function () {
        stopped = true;
        if (timer) window.clearTimeout(timer);
      }
    };
  }

  function serviceMeta(state) { return SERVICE_META[state] || SERVICE_META.undetected; }

  function setText(id, text) {
    var node = document.getElementById(id);
    if (node) node.textContent = text;
  }

  function setHtml(id, html) {
    var node = document.getElementById(id);
    if (node) node.innerHTML = html;
  }

  function errorBox(target, message, extra) {
    var node = typeof target === "string" ? document.getElementById(target) : target;
    if (!node) return;
    node.innerHTML = '<div class="notice bad"><span>!</span><div><strong>数据获取失败</strong><br>' + esc(message) + (extra ? "<br>" + esc(extra) : "") + "</div></div>";
  }

  function renderNav(active) {
    var links = [
      { id: "ops", href: "/ops-console", label: "运维状态看板" },
      { id: "tasks", href: "/task-board", label: "任务全局看板" },
      { id: "command", href: "/command-center", label: "指令中心" }
    ];
    var nav = document.querySelector(".nav");
    if (nav) {
      nav.innerHTML = links.map(function (item) {
        return '<a href="' + item.href + '"' + (item.id === active ? ' class="active"' : "") + ">" + item.label + "</a>";
      }).join("") + '<a href="/app">工作台</a>';
    }
  }

  function loadIdentity() {
    return request("/auth/me").then(function (res) {
      var node = document.getElementById("identity");
      if (!node) return;
      if (res.ok && res.data) {
        var name = res.data.display_name || res.data.email || res.data.user_id || "已登录用户";
        node.textContent = name;
        node.title = "租户：" + (res.data.tenant_id || "—");
      } else {
        node.textContent = res.status === 401 ? "未登录" : "身份未知";
        node.title = res.error || "";
      }
    });
  }

  /* 极简 Markdown -> HTML 渲染。只处理 agent output 实际出现的语法：
   * ATX 标题、段落、粗体、code、无序列表、有序列表、表格、代码块、引用、分割线。
   * 行内 HTML 统一先 esc() 防注入。 */
  function renderMarkdown(md) {
    if (!md || typeof md !== "string") return "";
    var lines = md.split(/\r?\n/);
    var out = [];
    var i = 0;
    function inline(s) {
      // 先 esc，再在 esc 后的 HTML 上做**粗体**、`code`、[t](url) 替换
      var t = esc(s);
      t = t.replace(/`([^`]+)`/g, '<code class="mdl-code">$1</code>');
      t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
      return t;
    }
    while (i < lines.length) {
      var line = lines[i];
      // 空行
      if (!line.trim()) { i++; continue; }
      // ``` 代码块
      if (/^```/.test(line)) {
        i++;
        var buf = [];
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        if (i < lines.length) i++;
        out.push('<pre class="mdl-pre"><code>' + esc(buf.join('\n')) + '</code></pre>');
        continue;
      }
      // --- 分割线
      if (/^-{3,}\s*$/.test(line)) { out.push('<hr class="mdl-hr">'); i++; continue; }
      // ATX 标题
      var h = /^(#{1,4})\s+(.+?)\s*#*\s*$/.exec(line);
      if (h) {
        var lvl = h[1].length;
        out.push('<h' + lvl + ' class="mdl-h' + lvl + '">' + inline(h[2]) + '</h' + lvl + '>');
        i++; continue;
      }
      // > 引用（允许连续多行）
      if (/^>\s?/.test(line)) {
        var qbuf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { qbuf.push(lines[i].replace(/^>\s?/, '')); i++; }
        out.push('<blockquote class="mdl-blockquote">' + inline(qbuf.join('<br>')) + '</blockquote>');
        continue;
      }
      // 表格 → 转成冒号列表（避免 UI 上出现表格边框）
      if (line.startsWith('|') && i + 1 < lines.length && /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(lines[i + 1])) {
        var header = line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function(c){return c.trim();});
        i += 2;
        var parts = [];
        var isLabelValue = header.length === 2; // 2 列通常是 key|value 格式
        while (i < lines.length && lines[i].startsWith('|')) {
          var cells = lines[i].trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(function(c){return c.trim();});
          if (isLabelValue && cells.length >= 2) {
            // key|value 格式：每行变成一个 "key：value" 的列表项
            // cells[0] 本身可能带 Markdown 粗体（**xxx**），inline() 会转成 <strong>
            parts.push('<li>' + inline(cells[0]) + '：' + inline(cells[1]) + '</li>');
          } else if (cells.length > 1) {
            // 多列表格：每行的每个 cell 前面加对应的 header 名
            var pairs = [];
            for (var ci = 0; ci < cells.length; ci++) {
              var colName = header[ci] || ('列' + (ci + 1));
              if (cells[ci]) pairs.push('**' + colName + '**：' + cells[ci]);
            }
            if (pairs.length) parts.push('<li>' + inline(pairs.join(' · ')) + '</li>');
          } else if (cells.length === 1 && cells[0]) {
            parts.push('<li>' + inline(cells[0]) + '</li>');
          }
          i++;
        }
        if (parts.length) out.push('<ul class="mdl-ul">' + parts.join('') + '</ul>');
        continue;
      }
      // 无序列表
      if (/^[-*+]\s+/.test(line)) {
        var uitems = [];
        while (i < lines.length && /^[-*+]\s+/.test(lines[i])) { uitems.push(lines[i].replace(/^[-*+]\s+/, '')); i++; }
        out.push('<ul class="mdl-ul">' + uitems.map(function(t){return '<li>' + inline(t) + '</li>';}).join('') + '</ul>');
        continue;
      }
      // 有序列表
      if (/^\d+\.\s+/.test(line)) {
        var oitems = [];
        while (i < lines.length && /^\d+\.\s+/.test(lines[i])) { oitems.push(lines[i].replace(/^\d+\.\s+/, '')); i++; }
        out.push('<ol class="mdl-ol">' + oitems.map(function(t){return '<li>' + inline(t) + '</li>';}).join('') + '</ol>');
        continue;
      }
      // 段落：连续非空非装饰行合并
      var pbuf = [line];
      i++;
      while (i < lines.length && lines[i].trim() &&
        !/^#{1,4}\s+/.test(lines[i]) && !/^```/.test(lines[i]) && !/^>\s?/.test(lines[i]) &&
        !/^[-*+]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i]) && !/^\|/.test(lines[i]) && !/^-{3,}\s*$/.test(lines[i])) {
        pbuf.push(lines[i]); i++;
      }
      out.push('<p class="mdl-p">' + inline(pbuf.join('\n')) + '</p>');
    }
    return out.join('\n');
  }

  /* 从 Markdown 文本里提取"人话第一段"。
   * 规则：
   *   - 跳过空行、标题行(# 开头)、分割线(---)、表格分割行(|---|)、列表标记开头(- 1.)
   *   - 取第一个非装饰的段落（遇到下一个空行或下一个装饰行就停）
   *   - 截到第一个句号/问号/感叹号（让它听起来像一句人话，不是半截）
   *   - 最多 120 字兜底
   * 目的：对话场景里不甩文档，只答人话。 */
  function extractFirstParagraph(md) {
    if (!md || typeof md !== "string") return "";
    var lines = md.split(/\r?\n/);
    var para = [];
    var inPara = false;
    for (var i = 0; i < lines.length; i++) {
      var l = lines[i].trim();
      if (!l) {
        if (inPara) break; // 第一段结束
        continue;
      }
      // 跳过装饰行
      if (/^#{1,4}\s/.test(l)) continue;          // 标题
      if (/^-{3,}$/.test(l)) continue;             // 分割线
      if (/^\|/.test(l)) continue;                 // 表格
      if (/^>\s?/.test(l)) continue;               // 引用
      if (/^```/.test(l)) continue;                // 代码块
      if (/^[-*+]\s/.test(l)) continue;            // 无序列表
      if (/^\d+\.\s/.test(l)) continue;            // 有序列表
      // 第一个正文段落
      inPara = true;
      para.push(l);
      if (para.length >= 2) break; // 一段即可，别贪多
    }
    var text = para.join(" ").trim();
    // 截到第一个句末标点，或 120 字
    var stop = text.search(/[。！？!?]/);
    if (stop >= 0) text = text.slice(0, stop + 1);
    if (text.length > 140) text = text.slice(0, 140) + "…";
    return text;
  }

  window.PlatformUI = {
    API: API,
    esc: esc,
    fmtTime: fmtTime,
    fmtClock: fmtClock,
    fmtAgo: fmtAgo,
    shortId: shortId,
    request: request,
    probeKind: probeKind,
    probeReason: probeReason,
    taskLabel: taskLabel,
    taskTone: taskTone,
    taskTitle: taskTitle,
    taskInputText: taskInputText,
    taskOutputText: taskOutputText,
    summarizeToolCalls: summarizeToolCalls,
    renderMarkdown: renderMarkdown,
    extractFirstParagraph: extractFirstParagraph,
    startPolling: startPolling,
    serviceMeta: serviceMeta,
    setText: setText,
    setHtml: setHtml,
    errorBox: errorBox,
    renderNav: renderNav,
    loadIdentity: loadIdentity,
    csrfToken: csrfToken
  };
})();
