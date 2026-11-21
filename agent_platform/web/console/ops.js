/* 页面一：运维状态看板。所有判断来自真实接口，未接入项显式标注。 */
(function () {
  "use strict";
  var UI = window.PlatformUI;
  var API = UI.API;
  var state = { services: [], filter: "all", monitorConnected: false, fetching: false };

  var GROUPS = [
    { id: "basic", name: "基础服务", hint: "平台运行依赖的底层服务" },
    { id: "execution", name: "任务执行服务", hint: "任务排队、执行与失败处理" },
    { id: "monitor", name: "监控与告警", hint: "容量、指标与告警链路" }
  ];

  var CAPACITY_LABELS = {
    db_connections: "数据库连接数",
    database_connections: "数据库连接数",
    object_storage: "对象存储容量",
    storage: "存储容量",
    vector_store: "向量库容量",
    redis_memory: "Redis 内存",
    run_concurrency: "并发执行数"
  };

  function svc(config) {
    if (config.state === "denied") config.state = "undetected";
    return Object.assign({
      key: "",
      group: "basic",
      name: "",
      check: "",
      state: "undetected",
      result: "尚未取得检查结果",
      reason: "接口未返回数据",
      action: "确认接口是否已接通",
      href: null,
      hrefLabel: "查看详情",
      checkedAt: new Date().toISOString()
    }, config);
  }

  function readyState(result, key) {
    if (!result) return "undetected";
    if (result.status === 401) return "undetected";
    if (result.status === 403) return "undetected";
    var data = result.data || {};
    var checks = data.checks || {};
    if (!Object.keys(checks).length) return result.status === 503 ? "unconfigured" : "undetected";
    if (!(key in checks)) return "unconfigured";
    return checks[key] === "ok" ? "ok" : "bad";
  }

  function readyReason(result, key) {
    if (!result) return "检查接口未调用";
    if (result.status === 401) return "未登录，无法读取健康检查";
    if (result.status === 403) return "当前账号无权限读取健康检查";
    if (result.status === 0) return "健康检查接口不可达";
    var checks = (result.data && result.data.checks) || {};
    if (!Object.keys(checks).length) return result.status === 503 ? "当前部署未配置就绪检查" : "就绪检查未返回依赖项";
    if (!(key in checks)) return "该依赖未纳入就绪检查";
    return checks[key] === "ok" ? "—" : "依赖检查返回不可用";
  }

  function buildServices(ctx) {
    var now = ctx.fetchedAt;
    var services = [];
    var live = ctx.live, ready = ctx.ready, bootstrap = ctx.bootstrap;
    var caps = ctx.caps, tasks = ctx.tasks, jobs = ctx.jobs, dlq = ctx.dlq;
    var capacity = ctx.capacity, metrics = ctx.metrics, alerts = ctx.alerts;
    var workerInfo = ctx.worker && ctx.worker.ok ? ctx.worker.data : null;
    var outboxInfo = ctx.outbox && ctx.outbox.ok ? ctx.outbox.data : null;
    var leaseInfo = ctx.lease && ctx.lease.ok ? ctx.lease.data : null;
    var streamsInfo = ctx.streams && ctx.streams.ok ? ctx.streams.data : null;

    /* ---------- 基础服务 ---------- */
    var apiState = "bad";
    var apiResult = "存活检查失败";
    var apiReason = live && live.status ? UI.probeReason(live) : "存活检查未返回";
    if (live.ok && live.data && live.data.status === "live") {
      if (bootstrap.status === 401) {
        apiState = "warn";
        apiResult = "服务存活，业务接口要求登录";
        apiReason = "当前浏览器会话未登录，无法读取平台业务数据";
      } else if (bootstrap.status === 403) {
        apiState = "warn";
        apiResult = "服务存活，当前账号权限不足";
        apiReason = "业务接口返回 403";
      } else if (bootstrap.ok) {
        apiState = "ok";
        apiResult = "存活检查通过，业务接口正常响应";
        apiReason = "—";
      } else {
        apiState = "warn";
        apiResult = "服务存活，业务接口响应异常";
        apiReason = UI.probeReason(bootstrap);
      }
    } else if (live.status === 401) {
      apiState = "warn";
      apiResult = "接口可达，但需要登录后检查";
      apiReason = "健康检查返回 401";
    }
    services.push(svc({
      key: "api", group: "basic", name: "API 服务", check: "服务存活、接口响应",
      state: apiState, result: apiResult, reason: apiReason,
      action: apiState === "ok" ? "无需处理" : "检查服务进程、反向代理与登录状态",
      href: "/health/live", hrefLabel: "查看存活检查", checkedAt: now
    }));

    var pgState = readyState(ready, "postgres");
    var pgChecks = (ready.data && ready.data.checks) || {};
    services.push(svc({
      key: "postgres", group: "basic", name: "数据库 PostgreSQL", check: "数据库连接、依赖检查",
      state: pgState,
      result: pgState === "ok"
        ? "数据库连接正常" + (("quota_schema" in pgChecks) ? "，配额表结构检查正常" : "")
        : pgState === "unconfigured" ? "当前部署未提供数据库就绪检查" : (ready.ok ? "数据库检查未通过" : "未取得数据库检查结果"),
      reason: readyReason(ready, "postgres"),
      action: pgState === "ok" ? "无需处理" : "检查数据库连接串、连接池与迁移状态，然后在运维看板复核",
      href: "/board", hrefLabel: "打开运营看板", checkedAt: now
    }));

    var redisState = readyState(ready, "redis");
    services.push(svc({
      key: "redis", group: "basic", name: "Redis", check: "Redis 连接状态",
      state: redisState,
      result: redisState === "ok" ? "Redis 连接正常" : redisState === "unconfigured" ? "当前部署未提供 Redis 就绪检查" : "未取得 Redis 检查结果",
      reason: readyReason(ready, "redis"),
      action: redisState === "ok" ? "无需处理" : "检查 Redis 进程、地址与密码配置，确认网络可达",
      href: "/board", hrefLabel: "打开运营看板", checkedAt: now
    }));

    // ---- Streams ----
    var streamsState, streamsResult, streamsReason, streamsAction;
    if (!ctx.streams.ok) {
      streamsState = ctx.streams.status === 0 ? "undetected" : UI.probeKind(ctx.streams);
      streamsResult = "未取得 Redis Streams 检查结果";
      streamsReason = UI.probeReason(ctx.streams);
      streamsAction = "确认 Streams 监控接口是否已挂载";
    } else if (!streamsInfo || streamsInfo.redis_ok === false) {
      // Redis 不可达
      streamsState = "warn";
      streamsResult = "Redis 不可达，无法探测 Streams";
      streamsReason = (streamsInfo && streamsInfo.reason) || "Redis 连接失败";
      streamsAction = "检查 Redis 进程是否在线，确认 REDIS_URL 配置";
    } else if (streamsInfo.streams_active && streamsInfo.consumers && streamsInfo.consumers.length > 0) {
      // 有 consumer groups — 常规模式
      var cTotal = streamsInfo.consumers.length;
      var pTotal = streamsInfo.pending_total || 0;
      streamsState = pTotal > 1000 ? "warn" : "ok";
      streamsResult = "已发现 " + cTotal + " 个消费者组，Pending " + pTotal + " 条";
      streamsReason = pTotal > 1000 ? "Pending 积压超过 1000，可能存在消费延迟" : "—";
      streamsAction = pTotal > 1000 ? "检查消费者进程是否在线，确认消费速率" : "无需处理";
    } else if (streamsInfo.streams_count > 0) {
      // Redis 活着、有 Stream key 活动、但没有 consumer group — producer-only 模式
      streamsState = "ok";
      streamsResult = "Redis 可用，存在 " + streamsInfo.streams_count + " 个 Stream key，未启用消费者组";
      streamsReason = streamsInfo.deployment_note || "当前部署只发布不消费 Redis Streams";
      streamsAction = "本地部署由 Outbox Relay XADD 发布，消费端使用独立 Worker — 无需处理";
    } else {
      // Redis 活着但无任何 Stream 活动
      streamsState = "ok";
      streamsResult = "Redis 可用，无 Stream 活动";
      streamsReason = streamsInfo.deployment_note || "当前未使用 Redis Streams 能力";
      streamsAction = "无需处理";
    }
    services.push(svc({
      key: "streams", group: "basic", name: "消息队列（Redis Streams）", check: "Streams 消费情况",
      state: streamsState, result: streamsResult, reason: streamsReason, action: streamsAction,
      href: ctx.streams.ok ? (API + "/workbench/ops/streams-stats") : null, hrefLabel: "查看 Streams 详情", checkedAt: now
    }));

    // ---- Worker ----
    var workerState, workerResult, workerReason, workerAction;
    if (!ctx.worker.ok) {
      workerState = ctx.worker.status === 0 ? "undetected" : UI.probeKind(ctx.worker);
      workerResult = "未取得 Worker 心跳检查结果";
      workerReason = UI.probeReason(ctx.worker);
      workerAction = "确认 Worker 心跳接口是否已挂载";
    } else if (!workerInfo || workerInfo.enabled === false) {
      workerState = "unconfigured";
      workerResult = "Worker 心跳未启用";
      workerReason = (workerInfo && workerInfo.reason) || "心跳检查接口未返回有效数据";
      workerAction = "检查 Worker 监控端点是否配置";
    } else {
      var total = workerInfo.total || 0;
      var online = workerInfo.active_online || 0;
      if (total === 0) {
        workerState = "warn";
        workerResult = "平台从未观察到 Worker 进程";
        workerReason = "platform_runs 表中没有 worker_id 记录";
        workerAction = "确认 Worker 进程是否启动、claim_workbench_tasks 是否被调用";
      } else if (online === 0) {
        workerState = "bad";
        workerResult = "共有 " + total + " 个历史 Worker，但当前无在线进程";
        workerReason = "所有 Worker 的租约已过期且最近 2 分钟无活动";
        workerAction = "立即检查 Worker 进程日志，重新启动执行器";
      } else {
        workerState = "ok";
        workerResult = "在线 Worker " + online + " / 历史 " + total + " 个";
        workerReason = "—";
        workerAction = "无需处理";
      }
    }
    services.push(svc({
      key: "worker", group: "basic", name: "Worker 执行器", check: "Worker 心跳、执行能力",
      state: workerState, result: workerResult, reason: workerReason, action: workerAction,
      href: ctx.worker.ok ? (API + "/workbench/ops/worker-health") : null, hrefLabel: "查看 Worker 详情", checkedAt: now
    }));

    /* ---------- 任务执行服务 ---------- */
    var jobItems = (jobs.ok && jobs.data && jobs.data.items) || [];
    var failedJobs = jobItems.filter(function (job) { return job.last_result === "failed"; });
    var schedulerState = jobs.ok ? (failedJobs.length ? "warn" : "ok") : (jobs.status === 0 ? "undetected" : UI.probeKind(jobs));
    services.push(svc({
      key: "scheduler", group: "execution", name: "调度器", check: "定时任务调度状态",
      state: schedulerState,
      result: jobs.ok
        ? "已配置 " + jobItems.length + " 个定时任务，其中 " + jobItems.filter(function (j) { return j.enabled; }).length + " 个启用" + (failedJobs.length ? "；" + failedJobs.length + " 个上次执行失败" : "")
        : "未取得定时任务数据",
      reason: jobs.ok ? (failedJobs.length ? "定时任务上次执行失败：" + failedJobs.map(function (j) { return j.name || j.job_id; }).join("、") : "—") : UI.probeReason(jobs),
      action: schedulerState === "ok" ? "无需处理" : "在任务全局看板查看定时任务的上次结果与下次执行时间",
      href: "/task-board#scheduler", hrefLabel: "查看定时任务", checkedAt: now
    }));

    var taskCount = (tasks.ok && tasks.data && tasks.data.items) ? tasks.data.items.length : 0;
    var taskExecutionEnabled = !!(caps.ok && caps.data && caps.data.features && caps.data.features.task_execution);
    var taskState;
    var taskResult;
    var taskReason;
    if (!caps.ok) {
      taskState = UI.probeKind(caps);
      taskResult = "未取得任务执行能力信息";
      taskReason = UI.probeReason(caps);
    } else if (!taskExecutionEnabled) {
      taskState = "unconfigured";
      taskResult = "当前部署未启用任务执行能力";
      taskReason = "能力接口返回 task_execution=false";
    } else if (!tasks.ok) {
      taskState = UI.probeKind(tasks);
      taskResult = "任务清单接口不可用";
      taskReason = UI.probeReason(tasks);
    } else {
      taskState = "ok";
      taskResult = "任务清单可读取，最近 " + taskCount + " 条任务记录";
      taskReason = "—";
    }
    services.push(svc({
      key: "taskcenter", group: "execution", name: "任务执行中心", check: "任务执行状态",
      state: taskState, result: taskResult, reason: taskReason,
      action: taskState === "ok" ? "无需处理" : "确认任务执行能力开关与任务服务配置",
      href: "/task-board", hrefLabel: "打开任务看板", checkedAt: now
    }));

    var taskItems = (tasks.ok && tasks.data && tasks.data.items) || [];
    var statusCount = function (status) {
      return taskItems.filter(function (item) { return item.status === status; }).length;
    };
    var queuedCount = statusCount("queued");
    services.push(svc({
      key: "queue", group: "execution", name: "执行队列", check: "等待任务数量",
      state: tasks.ok ? (queuedCount > 20 ? "warn" : "ok") : (tasks.status === 0 ? "undetected" : UI.probeKind(tasks)),
      result: tasks.ok ? "等待执行 " + queuedCount + " 个任务（依据任务表统计）" : "未取得队列数据",
      reason: tasks.ok ? (queuedCount > 20 ? "等待任务超过 20 个，可能存在消费积压" : "—") : UI.probeReason(tasks),
      action: queuedCount > 20 ? "确认 Worker 是否在线，并检查排队任务是否集中在同一智能体" : "无需处理",
      href: "/task-board", hrefLabel: "查看等待任务", checkedAt: now
    }));

    var failedCount = statusCount("failed");
    var timedOutCount = statusCount("timed_out");
    services.push(svc({
      key: "failures", group: "execution", name: "失败任务处理", check: "重试积压情况",
      state: tasks.ok ? (failedCount > 0 ? "warn" : "ok") : (tasks.status === 0 ? "undetected" : UI.probeKind(tasks)),
      result: tasks.ok ? "失败任务 " + failedCount + " 个" + (timedOutCount ? "，超时任务 " + timedOutCount + " 个" : "") : "未取得失败任务数据",
      reason: tasks.ok ? (failedCount ? "存在执行失败的任务，尚未全部处理" : "—") : UI.probeReason(tasks),
      action: failedCount ? "在任务全局看板查看失败原因，确认后重试或转人工处理" : "无需处理",
      href: "/task-board", hrefLabel: "查看失败任务", checkedAt: now
    }));

    var dlqItems = (dlq.ok && dlq.data && dlq.data.items) || [];
    var dlqState;
    if (dlq.ok) dlqState = dlqItems.length ? "bad" : "ok";
    else dlqState = dlq.status === 0 ? "undetected" : UI.probeKind(dlq);
    services.push(svc({
      key: "dlq", group: "execution", name: "死信队列", check: "DLQ 积压数量",
      state: dlqState,
      result: dlq.ok ? "死信消息 " + dlqItems.length + " 条" : "未取得死信数据",
      reason: dlq.ok ? (dlqItems.length ? "存在需要人工处理的消息" : "—") : UI.probeReason(dlq),
      action: dlqItems.length ? "打开死信列表，确认失败原因后重试或标记解决" : (dlq.ok ? "无需处理" : "确认死信管理能力是否已配置"),
      href: "/task-board", hrefLabel: "查看死信", checkedAt: now
    }));

    // ---- Pending / "消息待确认队列" — 用 outbox + streams 组合判断 ----
    var pendingState, pendingResult, pendingReason, pendingAction;
    // 优先用 Redis Streams 的 pending，如果没启用再看 outbox pending
    var streamsPending = (streamsInfo && streamsInfo.enabled) ? (streamsInfo.pending_total || 0) : null;
    var outboxPending = (outboxInfo && outboxInfo.enabled) ? (outboxInfo.total_pending || 0) : null;
    if (streamsPending !== null) {
      pendingState = streamsPending > 1000 ? "warn" : "ok";
      pendingResult = "Redis Streams Pending " + streamsPending + " 条";
      pendingReason = streamsPending > 1000 ? "Pending 积压可能影响端到端延迟" : "—";
      pendingAction = streamsPending > 1000 ? "检查消费者进程与消费速率" : "无需处理";
    } else if (outboxPending !== null) {
      // Local deployment: outbox pending ≈ messages not yet published to event bus
      var topicPending = [];
      (outboxInfo.topics || []).forEach(function (t) {
        if (t.pending > 0) topicPending.push(t.topic + "(" + t.pending + ")");
      });
      pendingState = outboxPending > 50 ? "warn" : "ok";
      pendingResult = "Outbox 待发布 " + outboxPending + " 条" + (topicPending.length ? "：" + topicPending.join("、") : "");
      pendingReason = outboxPending > 50 ? "Outbox 积压较多，发布器可能延迟" : "—";
      pendingAction = outboxPending > 50 ? "检查 Outbox 发布器进程是否在线" : "无需处理";
    } else if (!ctx.outbox.ok && !ctx.streams.ok) {
      pendingState = ctx.outbox.status === 0 ? "undetected" : "unconfigured";
      pendingResult = "未取得 Pending / Outbox 数据";
      pendingReason = "Pending 监控接口未返回有效数据";
      pendingAction = "确认消息队列监控接口是否已挂载";
    } else {
      pendingState = "unconfigured";
      pendingResult = "消息待确认监控未启用";
      pendingReason = "当前部署未暴露 Pending 统计接口";
      pendingAction = "如需监控，请启用 Redis Streams 或 Outbox 监控";
    }
    services.push(svc({
      key: "pending", group: "execution", name: "消息待确认队列", check: "Pending 数量",
      state: pendingState, result: pendingResult, reason: pendingReason, action: pendingAction,
      href: ctx.outbox.ok ? (API + "/workbench/ops/outbox-stats") : null, hrefLabel: "查看待确认消息", checkedAt: now
    }));

    // ---- Outbox ----
    var outboxState, outboxResult, outboxReason, outboxAction;
    if (!ctx.outbox.ok) {
      outboxState = ctx.outbox.status === 0 ? "undetected" : UI.probeKind(ctx.outbox);
      outboxResult = "未取得 Outbox 检查结果";
      outboxReason = UI.probeReason(ctx.outbox);
      outboxAction = "确认事务发件箱监控接口是否已挂载";
    } else if (!outboxInfo || outboxInfo.enabled === false) {
      outboxState = "unconfigured";
      outboxResult = "事务发件箱未启用";
      outboxReason = (outboxInfo && outboxInfo.reason) || "Outbox 监控未启用";
      outboxAction = "如需监控，请启用事务发件箱能力";
    } else {
      var pTotal = outboxInfo.total_pending || 0;
      var pLease = outboxInfo.lease_expired || 0;
      var pError = 0;
      (outboxInfo.topics || []).forEach(function (t) { pError += t.erroring || 0; });
      var totalMsg = outboxInfo.total || 0;
      if (pTotal === 0 && pError === 0) {
        outboxState = "ok";
        outboxResult = "Outbox 无积压（共 " + totalMsg + " 条历史已发布）";
        outboxReason = "—";
        outboxAction = "无需处理";
      } else if (pError > 0) {
        outboxState = "warn";
        outboxResult = "待发布 " + pTotal + " 条，其中 " + pError + " 条重试失败";
        outboxReason = "存在多次重试仍未发布的消息";
        outboxAction = "查看 Outbox 发布器日志，确认失败原因后修复";
      } else {
        outboxState = pTotal > 100 ? "warn" : "ok";
        outboxResult = "Outbox 待发布 " + pTotal + " 条";
        outboxReason = pTotal > 100 ? "Outbox 积压超过阈值（100）" : "—";
        outboxAction = pTotal > 100 ? "检查 Outbox 发布器是否在线、消费速率是否匹配" : "无需处理";
      }
    }
    services.push(svc({
      key: "outbox", group: "execution", name: "事务发件箱", check: "Outbox 积压数量",
      state: outboxState, result: outboxResult, reason: outboxReason, action: outboxAction,
      href: ctx.outbox.ok ? (API + "/workbench/ops/outbox-stats") : null, hrefLabel: "查看 Outbox 详情", checkedAt: now
    }));

    // ---- Lease ----
    var leaseState, leaseResult, leaseReason, leaseAction;
    var runningCount = statusCount("running") + statusCount("cancelling");
    if (!ctx.lease.ok) {
      leaseState = ctx.lease.status === 0 ? "undetected" : UI.probeKind(ctx.lease);
      leaseResult = tasks.ok ? "当前执行中任务 " + runningCount + " 个；未取得租约明细" : "未取得租约数据";
      leaseReason = UI.probeReason(ctx.lease);
      leaseAction = "确认执行租约监控接口是否已挂载";
    } else if (!leaseInfo || leaseInfo.enabled === false) {
      leaseState = "unconfigured";
      leaseResult = tasks.ok ? "当前执行中任务 " + runningCount + " 个" : "租约监控未启用";
      leaseReason = (leaseInfo && leaseInfo.reason) || "租约监控未返回有效数据";
      leaseAction = "如需监控租约，请启用执行租约监控";
    } else {
      var activeLeases = leaseInfo.active || 0;
      var expiredLeases = leaseInfo.expired || 0;
      var workerOnline = (workerInfo && workerInfo.active_online && workerInfo.active_online > 0) || false;
      if (activeLeases > 0) {
        leaseState = "ok";
        leaseResult = "活跃租约 " + activeLeases + " 个；" + expiredLeases + " 条历史已过期";
        leaseReason = "—";
        leaseAction = "无需处理";
      } else if (workerOnline) {
        // Worker 在线但没活跃租约 → 空闲中，正常
        leaseState = "ok";
        leaseResult = "活跃租约 0 个（Worker 空闲，无执行中的 run）";
        leaseReason = "Worker 在线，当前没有需要 claim 的任务";
        leaseAction = "无需处理";
      } else if (runningCount > 0) {
        // 执行中但没租约也没在线 Worker → 异常
        leaseState = "warn";
        leaseResult = "执行中任务 " + runningCount + " 个；但 Worker 不在线";
        leaseReason = "Worker 已退出但有 run 还在 running，可能需要人工干预";
        leaseAction = "检查 Worker 进程日志，必要时取消这些 run";
      } else {
        // 全都 0 → 平台空闲，ok
        leaseState = "ok";
        leaseResult = "活跃租约 0 个；执行中任务 0 个";
        leaseReason = (expiredLeases > 0 ? expiredLeases + " 条历史租约已过期" : "—");
        leaseAction = expiredLeases > 0 ? "历史租约已清理，无需处理" : "无需处理";
      }
    }
    services.push(svc({
      key: "lease", group: "execution", name: "执行租约", check: "Lease 状态",
      state: leaseState, result: leaseResult, reason: leaseReason, action: leaseAction,
      href: ctx.lease.ok ? (API + "/workbench/ops/lease-stats") : null, hrefLabel: "查看租约详情", checkedAt: now
    }));

    /* ---------- 监控与告警 ---------- */
    var capacityItem = capacity.data && typeof capacity.data === "object" ? capacity.data : null;
    var capacityStatus = capacityItem && capacityItem.status;
    var capacityState;
    var capacityResult;
    var capacityReason;
    if (capacity.ok && capacityStatus) {
      capacityState = capacityStatus === "ok" ? "ok" : capacityStatus === "warning" ? "warn" : "bad";
      var capacityNames = Object.keys(capacityItem.capacity || {});
      capacityResult = "容量检查 " + capacityNames.length + " 项，整体状态：" + (capacityStatus === "ok" ? "正常" : capacityStatus === "warning" ? "接近上限" : "超出阈值");
      capacityReason = capacityState === "ok" ? "—" : "存在容量项超过告警阈值";
    } else if (capacityStatus === "unavailable") {
      capacityState = "undetected";
      capacityResult = "容量监控已配置，但检查执行失败";
      capacityReason = "检查项返回 unavailable";
    } else if (capacity.status === 503) {
      capacityState = "unconfigured";
      capacityResult = "当前部署未配置容量监控";
      capacityReason = "容量监控接口未启用";
    } else if (capacity.status === 0) {
      capacityState = "undetected";
      capacityResult = "容量监控接口不可达";
      capacityReason = "网络或服务异常";
    } else {
      capacityState = "undetected";
      capacityResult = "未取得容量检查结果";
      capacityReason = UI.probeReason(capacity);
    }
    services.push(svc({
      key: "capacity", group: "monitor", name: "容量监控", check: "Capacity Health",
      state: capacityState, result: capacityResult, reason: capacityReason,
      action: capacityState === "ok" ? "无需处理" : "查看容量接口中的具体检查项，确认使用率与上限",
      href: capacityState === "ok" ? null : API + "/observability/health/capacity", hrefLabel: "查看容量数据",
      checkedAt: now
    }));

    var metricItems = (metrics.ok && metrics.data && metrics.data.items) || [];
    services.push(svc({
      key: "metrics", group: "monitor", name: "指标采集", check: "Prometheus Metrics",
      state: metrics.ok ? "ok" : (metrics.status === 503 ? "unconfigured" : UI.probeKind(metrics)),
      result: metrics.ok ? "指标已导出，共 " + metricItems.length + " 个指标族" : "未取得指标数据",
      reason: metrics.ok ? "—" : UI.probeReason(metrics),
      action: metrics.ok ? "无需处理" : "确认指标导出器是否初始化",
      href: metrics.ok ? null : API + "/observability/metrics/json", hrefLabel: "查看指标数据",
      checkedAt: now
    }));

    var alertItems = (alerts.ok && alerts.data && alerts.data.items) || [];
    services.push(svc({
      key: "alerts", group: "monitor", name: "告警检测", check: "Alerts",
      state: alerts.ok ? (alertItems.length ? "warn" : "ok") : (alerts.status === 503 ? "unconfigured" : UI.probeKind(alerts)),
      result: alerts.ok ? "未关闭告警 " + alertItems.length + " 条" : "未取得告警数据",
      reason: alerts.ok ? (alertItems.length ? "存在未确认或未解决的告警事件" : "—") : UI.probeReason(alerts),
      action: alertItems.length ? "逐条确认告警事件，处理后在告警接口中标记解决" : (alerts.ok ? "无需处理" : "确认告警事件存储是否已配置"),
      href: alerts.ok ? API + "/observability/alerts" : null, hrefLabel: "查看告警事件",
      checkedAt: now
    }));

    var feishu = jobs.ok && jobs.data ? jobs.data.feishu : null;
    var notifyConfigured = !!(feishu && feishu.configured);
    services.push(svc({
      key: "notify", group: "monitor", name: "告警通知", check: "Notification Transport",
      state: jobs.ok ? (notifyConfigured ? "ok" : "unconfigured") : (jobs.status === 0 ? "undetected" : UI.probeKind(jobs)),
      result: !jobs.ok ? "未取得通知状态" : notifyConfigured ? "通知通道已配置" : "通知通道未配置",
      reason: !jobs.ok ? UI.probeReason(jobs) : notifyConfigured ? "—" : (feishu && feishu.reason ? String(feishu.reason) : "未检测到可用的通知适配器"),
      action: notifyConfigured ? "无需处理" : "配置飞书机器人等通知通道；未配置时告警只写入事件存储，不会主动通知",
      checkedAt: now
    }));

    return services;
  }

  function counts(services) {
    var result = { ok: 0, bad: 0, warn: 0, undetected: 0, unconfigured: 0 };
    services.forEach(function (item) {
      if (item.state === "ok") result.ok += 1;
      else if (item.state === "bad") result.bad += 1;
      else if (item.state === "warn") result.warn += 1;
      else if (item.state === "unconfigured") result.unconfigured += 1;
      else result.undetected += 1;
    });
    return result;
  }

  function severityLabel(level) {
    return level === "critical" ? "严重" : level === "warning" ? "警告" : "提示";
  }

  function buildIssues(ctx, services) {
    if (!ctx.monitorConnected) return [];
    var issues = [];
    var byKey = {};
    services.forEach(function (item) { byKey[item.key] = item; });

    ["api", "postgres", "redis"].forEach(function (key) {
      var item = byKey[key];
      if (item && item.state === "bad") {
        issues.push({
          title: item.name + " 异常",
          scope: "基础服务 · " + item.check,
          severity: "critical",
          foundAt: ctx.fetchedAt,
          advice: item.action,
          href: item.href,
          hrefLabel: item.hrefLabel
        });
      }
    });

    var capacityItem = ctx.capacity.data && typeof ctx.capacity.data === "object" ? ctx.capacity.data : null;
    if (capacityItem && capacityItem.capacity) {
      Object.keys(capacityItem.capacity).forEach(function (name) {
        var entry = capacityItem.capacity[name];
        if (entry.status === "critical" || entry.status === "warning") {
          var label = CAPACITY_LABELS[name] || name;
          issues.push({
            title: label + (entry.status === "critical" ? " 已达严重阈值" : " 接近上限"),
            scope: "容量监控" + (entry.used !== undefined && entry.capacity !== undefined ? " · " + entry.used + " / " + entry.capacity : ""),
            severity: entry.status === "critical" ? "critical" : "warning",
            foundAt: capacityItem.observed_at_utc || ctx.fetchedAt,
            advice: "确认该项资源的使用来源，必要时扩容或清理；处理后重新检查容量状态。",
            href: API + "/observability/health/capacity",
            hrefLabel: "查看容量数据"
          });
        }
      });
    }

    var alertItems = (ctx.alerts.ok && ctx.alerts.data && ctx.alerts.data.items) || [];
    alertItems.forEach(function (alert) {
      issues.push({
        title: alert.description || (alert.rule_id ? "告警规则 " + alert.rule_id : "未命名告警"),
        scope: "告警检测 · 指标 " + (alert.metric_name || "—") + "，观测值 " + (alert.observed_value !== undefined ? alert.observed_value : "—"),
        severity: alert.severity === "critical" ? "critical" : alert.severity === "warning" ? "warning" : "info",
        foundAt: alert.fired_at || ctx.fetchedAt,
        advice: "查看告警事件中的指标与阈值，处理后在告警接口中标记解决。",
        href: API + "/observability/alerts",
        hrefLabel: "查看告警事件"
      });
    });

    var taskItems = (ctx.tasks.ok && ctx.tasks.data && ctx.tasks.data.items) || [];
    var failedTasks = taskItems.filter(function (item) { return item.status === "failed"; });
    if (failedTasks.length) {
      var latest = failedTasks.reduce(function (acc, item) {
        return !acc || String(item.updated_at) > String(acc) ? item.updated_at : acc;
      }, null);
      issues.push({
        title: failedTasks.length + " 个任务执行失败",
        scope: "任务执行 · 需要人工确认或重试",
        severity: "warning",
        foundAt: latest || ctx.fetchedAt,
        advice: "打开任务全局看板，查看失败原因与运行记录；确认后重试任务或转人工处理。",
        href: "/task-board",
        hrefLabel: "查看失败任务"
      });
    }

    var dlqItems = (ctx.dlq.ok && ctx.dlq.data && ctx.dlq.data.items) || [];
    if (dlqItems.length) {
      issues.push({
        title: "死信队列有 " + dlqItems.length + " 条积压",
        scope: "任务执行 · 多次重试仍失败的消息",
        severity: "critical",
        foundAt: ctx.fetchedAt,
        advice: "打开死信列表，确认真实失败原因；修复后重试，确认无法恢复的再标记解决。",
        href: "/task-board",
        hrefLabel: "查看死信"
      });
    }

    var jobItems = (ctx.jobs.ok && ctx.jobs.data && ctx.jobs.data.items) || [];
    var failedJobs = jobItems.filter(function (job) { return job.last_result === "failed"; });
    failedJobs.forEach(function (job) {
      issues.push({
        title: "定时任务上次执行失败：" + (job.name || job.job_id),
        scope: "定时任务 · 每 " + Math.round((job.interval_seconds || 0) / 60) + " 分钟执行一次",
        severity: "warning",
        foundAt: job.last_run_at || ctx.fetchedAt,
        advice: job.last_message ? "上次返回：" + job.last_message : "检查定时任务配置和平台日志，确认失败原因。",
        href: "/task-board#scheduler",
        hrefLabel: "查看定时任务"
      });
    });

    var queued = taskItems.filter(function (item) { return item.status === "queued"; });
    if (queued.length > 20) {
      issues.push({
        title: "执行队列积压 " + queued.length + " 个等待任务",
        scope: "任务执行 · 等待执行",
        severity: "warning",
        foundAt: queued.reduce(function (acc, item) { return !acc || String(item.updated_at) < String(acc) ? item.updated_at : acc; }, null) || ctx.fetchedAt,
        advice: "确认 Worker 是否在线；如集中在同一智能体，检查该智能体的依赖服务是否异常。",
        href: "/task-board",
        hrefLabel: "查看等待任务"
      });
    }

    var feishu = ctx.jobs.ok && ctx.jobs.data ? ctx.jobs.data.feishu : null;
    if (feishu && !feishu.configured) {
      issues.push({
        title: "告警通知通道未配置",
        scope: "告警通知 · 告警仍会记录，但不会主动推送",
        severity: "info",
        foundAt: ctx.fetchedAt,
        advice: "配置飞书机器人等通知通道；未配置期间请以本页告警数量为准。",
        href: "/board",
        hrefLabel: "打开运营看板"
      });
    }

    var order = { critical: 0, warning: 1, info: 2 };
    issues.sort(function (a, b) { return order[a.severity] - order[b.severity]; });
    return issues;
  }

  function renderHero(ctx) {
    var services = state.services;
    var c = counts(services);
    var unknown = c.undetected + c.unconfigured;
    var alertCount = ctx.alerts.ok ? ((ctx.alerts.data && ctx.alerts.data.items) || []).length : null;
    var overall = document.getElementById("overallState");
    var hint = document.getElementById("overallHint");
    var tone = "idle";
    var label = "无法判断";
    var hintText = "";

    if (!ctx.monitorConnected) {
      tone = "idle";
      label = ctx.unauthorized ? "未登录" : "监控未接入";
      hintText = ctx.unauthorized
        ? "当前浏览器会话未登录，无法读取监控数据。请先在现有工作台完成登录，然后回到本页刷新。"
        : "核心监控接口尚未接通，不能用默认值判定平台健康状态。请先完成下方未接入项的配置。";
    } else if (c.bad > 0) {
      tone = "bad";
      label = "存在异常";
      hintText = "已确认 " + c.bad + " 项服务异常，请优先处理下方「当前需要优先处理」中的第一项。";
    } else if (c.warn > 0) {
      tone = "warn";
      label = "存在警告";
      hintText = "核心检查未发现异常，但有 " + c.warn + " 项警告需要关注。";
    } else if (c.ok > 0) {
      tone = "ok";
      label = "运行正常";
      hintText = "所有已接入的检查项均返回正常；未接入项不会被当作正常。";
    } else {
      tone = "idle";
      label = "未检测";
      hintText = "当前没有取得任何有效检查结果，请确认登录状态与接口接入情况。";
    }

    overall.className = "state-big " + tone;
    overall.innerHTML = '<span class="state-dot"></span>' + UI.esc(label);
    hint.textContent = hintText;

    document.getElementById("countOk").textContent = String(c.ok);
    document.getElementById("countBad").textContent = String(c.bad);
    document.getElementById("countUnknown").textContent = String(unknown);
    document.getElementById("countUnknownFoot").textContent = "其中未配置 " + c.unconfigured + " 项、未检测 " + c.undetected + " 项";
    var alertNode = document.getElementById("countAlerts");
    if (alertCount === null) {
      alertNode.textContent = "未接入";
      alertNode.className = "value idle";
      alertNode.style.fontSize = "18px";
    } else {
      alertNode.textContent = String(alertCount);
      alertNode.className = "value " + (alertCount > 0 ? "warn" : "ok");
      alertNode.style.fontSize = "";
    }

    document.getElementById("lastChecked").textContent = UI.fmtTime(ctx.fetchedAt);
    document.getElementById("sourceBadge").textContent = "平台真实接口";
    document.getElementById("sourceCount").textContent = ctx.responded + " / " + ctx.total;
    var banner = document.getElementById("monitorBanner");
    banner.classList.toggle("hidden", ctx.monitorConnected);
    if (!ctx.monitorConnected) {
      banner.innerHTML = ctx.unauthorized
        ? '<span>!</span><div><strong>未登录，无法读取监控数据</strong><br>请先打开 <a href="/app">现有工作台</a> 完成登录，再回到本页刷新。本页不会用默认值代替真实状态。</div>'
        : '<span>!</span><div><strong>监控数据尚未接入，暂时无法判断平台整体健康状态</strong><br>以下未接入项均已明确标注，不会用默认值代替真实状态。</div>';
    }
    document.getElementById("refreshStamp").textContent = "最近刷新 " + UI.fmtClock(ctx.fetchedAt);
    document.getElementById("serviceSummary").textContent = "共 " + services.length + " 项检查";
  }

  function renderIssues(ctx) {
    var issues = buildIssues(ctx, state.services);
    var list = document.getElementById("issueList");
    var summary = document.getElementById("issueSummary");

    if (!ctx.monitorConnected) {
      list.innerHTML = '<div class="empty-box"><strong>监控数据尚未接入，暂时无法判断平台整体健康状态</strong>不会显示虚假的绿色正常状态。完成接口接入后，这里会自动列出需要优先处理的问题。</div>';
      summary.textContent = "等待监控接口接入";
      return;
    }
    if (!issues.length) {
      list.innerHTML = '<div class="empty-box"><strong>当前没有发现已确认的异常</strong>已接入的检查项均未触发告警；未接入项请在上方服务清单中查看。</div>';
      summary.textContent = "没有已确认的异常";
      return;
    }
    summary.textContent = "共 " + issues.length + " 项，按严重程度排序";
    list.innerHTML = issues.slice(0, 8).map(function (issue) {
      var level = issue.severity;
      return '<article class="issue ' + level + '">' +
        '<div class="issue-main">' +
          '<div class="issue-title">' + UI.esc(issue.title) +
            '<span class="tag ' + (level === "critical" ? "bad" : level === "warning" ? "warn" : "info") + '">' + severityLabel(level) + '</span>' +
          '</div>' +
          '<div class="issue-scope">影响范围：' + UI.esc(issue.scope) + '</div>' +
          '<div class="issue-advice">处理建议：' + UI.esc(issue.advice) + '</div>' +
        '</div>' +
        '<div class="issue-side">' +
          '<span>发现时间 ' + UI.esc(UI.fmtTime(issue.foundAt)) + '</span>' +
          (issue.href ? '<a href="' + UI.esc(issue.href) + '">' + UI.esc(issue.hrefLabel || "查看详情") + '</a>' : '<span>暂无详情入口</span>') +
        '</div>' +
      '</article>';
    }).join("");
  }

  function renderReliability(ctx) {
    var tasksOk = ctx.tasks.ok;
    var items = (tasksOk && ctx.tasks.data && ctx.tasks.data.items) || [];
    function count(status) { return items.filter(function (item) { return item.status === status; }).length; }
    var queued = count("queued");
    var running = count("running") + count("cancelling");
    var paused = count("paused");
    var failed = count("failed") + count("timed_out");
    var retryBacklog = items.filter(function (item) { return item.status === "queued" && Number(item.attempts || 0) > 0; }).length;
    var dlqItems = (ctx.dlq.ok && ctx.dlq.data && ctx.dlq.data.items) || [];

    var cards = [
      { label: "等待执行", value: tasksOk ? queued : null, desc: "排队中、尚未开始的任务" },
      { label: "正在执行", value: tasksOk ? running : null, desc: "含正在取消的任务" },
      { label: "暂停任务", value: tasksOk ? paused : null, desc: "等待人工恢复" },
      { label: "失败任务", value: tasksOk ? failed : null, desc: "失败与超时任务" },
      { label: "重试积压", value: tasksOk ? retryBacklog : null, desc: "已重试仍在等待的任务" },
      { label: "消息待确认（Pending）", value: null, desc: "接口未接入" },
      { label: "死信消息（DLQ）", value: ctx.dlq.ok ? dlqItems.length : null, desc: "需要人工处理的消息", tone: dlqItems.length ? "bad" : "ok" },
      { label: "事务消息积压（Outbox）", value: null, desc: "接口未接入" }
    ];

    document.getElementById("reliability").innerHTML = cards.map(function (card) {
      var missing = card.value === null;
      var tone = card.tone || (missing ? "" : card.value > 0 ? (card.label === "失败任务" ? "bad" : "warn") : "ok");
      var valueHtml = missing
        ? '<div class="value">未接入</div>'
        : '<div class="value ' + tone + '">' + card.value + '<small>个</small></div>';
      return '<div class="metric-card ' + (missing ? "na" : tone) + '">' +
        '<div class="label">' + UI.esc(card.label) + '</div>' +
        valueHtml +
        '<div class="desc">' + UI.esc(card.desc) + '</div>' +
      '</div>';
    }).join("");
  }

  function renderFilters() {
    var c = counts(state.services);
    document.getElementById("filterCountAll").textContent = String(state.services.length);
    document.getElementById("filterCountOk").textContent = String(c.ok);
    document.getElementById("filterCountBad").textContent = String(c.bad);
    document.getElementById("filterCountWarn").textContent = String(c.warn);
    document.getElementById("filterCountUndetected").textContent = String(c.undetected);
    document.getElementById("filterCountUnconfigured").textContent = String(c.unconfigured);
    Array.prototype.forEach.call(document.querySelectorAll("#serviceFilters .filter"), function (btn) {
      btn.classList.toggle("active", btn.getAttribute("data-filter") === state.filter);
    });
  }

  function renderServices() {
    var visible = state.services.filter(function (item) {
      return state.filter === "all" || item.state === state.filter;
    });
    var host = document.getElementById("serviceGroups");
    if (!visible.length) {
      host.innerHTML = '<div class="empty-box" style="margin:0 22px 22px;">当前筛选下没有服务</div>';
      return;
    }
    host.innerHTML = GROUPS.map(function (group) {
      var items = visible.filter(function (item) { return item.group === group.id; });
      if (!items.length) return "";
      return '<div class="svc-group" data-group="' + group.id + '">' +
        '<div class="svc-group-head"><h3>' + UI.esc(group.name) + '</h3><span class="hint">' + UI.esc(group.hint) + '</span></div>' +
        items.map(function (item) {
          var meta = UI.serviceMeta(item.state);
          return '<div class="svc-row" data-state="' + item.state + '">' +
            '<div class="svc-main">' +
              '<div class="svc-title"><span class="dot ' + meta.tone + '"></span><b>' + UI.esc(item.name) + '</b><span class="tag ' + meta.tone + '">' + meta.label + '</span></div>' +
              '<div class="svc-check">检查内容：' + UI.esc(item.check) + '</div>' +
              '<div class="svc-result"><b>检查结果：</b>' + UI.esc(item.result) + '</div>' +
              '<div class="svc-reason"><b>异常原因：</b>' + UI.esc(item.reason) + '</div>' +
              '<div class="svc-action"><b>建议处理：</b>' + UI.esc(item.action) + '</div>' +
            '</div>' +
            '<div class="svc-side">' +
              '<span>最近检查<br>' + UI.esc(UI.fmtTime(item.checkedAt)) + '</span>' +
              (item.href ? '<a href="' + UI.esc(item.href) + '"' + (String(item.href).indexOf("/api/") === 0 ? ' target="_blank" rel="noreferrer"' : "") + '>' + UI.esc(item.hrefLabel) + '</a>' : '<span>暂无详情入口</span>') +
            '</div>' +
          '</div>';
        }).join("") +
      '</div>';
    }).join("");
  }

  function load() {
    if (state.fetching) return Promise.resolve();
    state.fetching = true;
    var paths = {
      live: "/health/live",
      ready: "/health/ready",
      bootstrap: API + "/bootstrap",
      caps: API + "/workbench/capabilities",
      tasks: API + "/workbench/tasks?limit=200",
      dlq: API + "/workbench/dead-letters?limit=100",
      jobs: API + "/scheduler/jobs",
      capacity: API + "/observability/health/capacity",
      metrics: API + "/observability/metrics/json",
      alerts: API + "/observability/alerts?limit=100",
      worker: API + "/workbench/ops/worker-health",
      outbox: API + "/workbench/ops/outbox-stats",
      lease: API + "/workbench/ops/lease-stats",
      streams: API + "/workbench/ops/streams-stats"
    };
    var keys = Object.keys(paths);
    return Promise.all(keys.map(function (key) { return UI.request(paths[key]); })).then(function (results) {
      var ctx = { fetchedAt: new Date().toISOString(), responded: 0, total: results.length };
      keys.forEach(function (key, index) {
        ctx[key] = results[index];
        if (results[index].status > 0) ctx.responded += 1;
      });
      var readyChecks = ctx.ready.data && ctx.ready.data.checks ? Object.keys(ctx.ready.data.checks).length : 0;
      var capacityStatus = ctx.capacity.data && ctx.capacity.data.status;
      ctx.unauthorized = ctx.bootstrap.status === 401 || ctx.ready.status === 401;
      ctx.monitorConnected = Boolean(ctx.ready.ok || readyChecks > 0 || ctx.alerts.ok || ctx.metrics.ok || (capacityStatus && capacityStatus !== "unavailable"));
      state.services = buildServices(ctx);
      state.monitorConnected = ctx.monitorConnected;
      renderHero(ctx);
      renderIssues(ctx);
      renderReliability(ctx);
      renderFilters();
      renderServices();
    }).catch(function (err) {
      console.error(err);
      UI.setText("lastChecked", "—");
      UI.setText("sourceBadge", "接口调用失败");
      document.getElementById("issueList").innerHTML = '<div class="notice bad"><span>!</span><div><strong>无法读取平台接口</strong><br>' + UI.esc(err && err.message ? err.message : "未知错误") + '</div></div>';
    }).then(function () {
      state.fetching = false;
    });
  }

  UI.renderNav("ops");
  UI.loadIdentity();
  document.getElementById("serviceFilters").addEventListener("click", function (event) {
    var btn = event.target.closest(".filter");
    if (!btn) return;
    state.filter = btn.getAttribute("data-filter") || "all";
    renderFilters();
    renderServices();
  });
  document.getElementById("refreshBtn").addEventListener("click", function () { load(); });

  load();
  UI.startPolling(load, 20000);
})();
