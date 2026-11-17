import { ROLE_META, PROFILE, COMMON_SPEECH, pickSpeech, ROLE_ORDER, OWNER_LABEL } from './console-config.js';
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const FONT = '"PingFang SC","Microsoft YaHei",sans-serif';
  const MONO = '"SF Mono",Menlo,monospace';
  const C = { ink:'#EEF2F8', dim:'#9AA6B8', muted:'#6E7A8C', ac:'#7CC6FF', red:'#FF6B7F', yl:'#FFC247', gn:'#4ADE9B' };

  const { ROLE_META, PROFILE, COMMON_SPEECH, pickSpeech, ROLE_ORDER, OWNER_LABEL } = await import('./console-config.js');

  let S = null;
  let currentRoom = 'o', boardView = 'st', boardFilter = 'all', boardQuery = '', closedAll = false;
  let runBusy = false, currentRole = null, lastSyncOut = '';
  let hoverKey = null;

  const isClosed = (t) => t.db_status === 'done' || t.db_status === 'canceled';
  const isOver = (t) => !isClosed(t) && t.state.startsWith('逾期');
  const isRisk = (t) => !isClosed(t) && (isOver(t) || t.state === '临期');
  const colOf = (t) => isClosed(t) ? 'closed' : isRisk(t) ? 'risk' : t.state === '进行中' ? 'doing' : 'todo';
  const shortDate = (d) => d ? d.slice(5).replace('-', '.') : '—';
  const ownerLabel = (o) => OWNER_LABEL[o] || o || '—';
  const money = (v) => '¥' + Number(v || 0).toLocaleString();
  const stateClass = (s) => {
    if (s === '已完成' || s === '已作废' || s === '已关闭') return 'closed';
    if (s === '进行中') return 'doing';
    if (s === '临期') return 'over';
    if (s === '明日开始') return 'next';
    if (s === '要素不全') return 'incomplete';
    if (s.startsWith('逾期')) return 'over';
    return 'todo';
  };
  const stateColor = (s) => s.startsWith('逾期') ? C.red : s === '进行中' ? C.gn : s === '临期' ? C.yl
    : s === '明日开始' ? C.ac : s === '要素不全' ? C.yl : (s === '已完成' || s === '已作废' || s === '已关闭') ? '#5a665d' : '#8896AA';
  const leftDays = (t) => {
    if (!S || !t.due) return null;
    return Math.round((new Date(t.due + 'T00:00:00') - new Date(S.today + 'T00:00:00')) / 86400000);
  };

  function launchDue() {
    const ms = (S && S.milestones) || [];
    const hit = ms.find(g => /上线/.test(g.key)) || ms.find(g => /^M6/.test(g.key));
    if (hit && hit.due) return hit.due;
    const dues = ms.map(g => g.due).filter(Boolean).sort();
    return dues.length ? dues[dues.length - 1] : '';
  }

  function daysTo(date) {
    if (!date || !S) return null;
    return Math.round((new Date(date + 'T00:00:00') - new Date(S.today + 'T00:00:00')) / 86400000);
  }

  function boardStats() {
    const tasks = S ? S.tasks : [];
    const open = tasks.filter(t => !isClosed(t));
    const m = (S && S.metrics) || {};
    return {
      total: tasks.length, open: open.length, closed: tasks.length - open.length,
      dueToday: S ? open.filter(t => t.due === S.today).length : 0,
      doing: open.filter(t => t.state === '进行中').length,
      soon: open.filter(t => t.state === '临期').length,
      over: open.filter(t => t.state.startsWith('逾期')).length,
      hoursDone: m.hours_done || 0, hoursTotal: m.hours_total || 0
    };
  }

  function sortTasks(list) {
    const rank = (t) => isClosed(t) ? 3 : isOver(t) ? 0 : t.state === '临期' ? 1 : 2;
    return [...list].sort((a, b) => {
      if (isClosed(a) !== isClosed(b)) return isClosed(a) ? 1 : -1;
      if (isClosed(a)) return String(b.closed_at || '').localeCompare(String(a.closed_at || ''));
      return rank(a) - rank(b) || String(a.due || '').localeCompare(String(b.due || '')) || a.id.localeCompare(b.id);
    });
  }

  function toast(msg, kind = 'ok') {
    const el = document.createElement('div');
    el.className = 'toast' + (kind === 'err' ? ' err' : '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 3200);
    setTimeout(() => el.remove(), 3600);
  }

  const CONSOLE_TOKEN = new URLSearchParams(location.search).get('token')
    || sessionStorage.getItem('console_token') || '';
  if (CONSOLE_TOKEN) sessionStorage.setItem('console_token', CONSOLE_TOKEN);

  async function api(path, options) {
    const opts = options || {};
    const headers = Object.assign(
      { 'Content-Type': 'application/json', 'X-API-Version': 'v1' },
      CONSOLE_TOKEN ? { 'X-Console-Token': CONSOLE_TOKEN } : {},
      opts.headers || {});
    const res = await fetch(path, { ...opts, headers });
    let body = {};
    try { body = await res.json(); } catch { /* ignore */ }
    if (!res.ok || body.ok === false) throw new Error((body.error && body.error.message) || body.error || ('请求失败：' + res.status));
    return body;
  }

  let stateEtag = '';
  async function load(showSpin = false) {
    if (showSpin) $('#bRefresh').textContent = '刷新中';
    try {
      const headers = Object.assign(
        { 'Content-Type': 'application/json' },
        CONSOLE_TOKEN ? { 'X-Console-Token': CONSOLE_TOKEN } : {});
      if (stateEtag) headers['If-None-Match'] = stateEtag;
      const res = await fetch('/api/v1/legacy/state', { headers });
      if (res.status === 304) return;           // 状态没变，跳过整包传输与重绘
      stateEtag = res.headers.get('ETag') || '';
      const body = await res.json();
      if (!res.ok || body.ok === false) throw new Error((body.error && body.error.message) || body.error || '请求失败');
      S = body.data;
      $('#banner').classList.remove('on');
      $('#livePill').className = 'pill ok';
      $('#livePill').textContent = '在线';
      renderAll();
    } catch (err) {
      $('#banner').textContent = '连不上本地服务：' + err.message + '　请确认 python3 console.py 正在运行';
      $('#banner').classList.add('on');
      $('#livePill').className = 'pill off';
      $('#livePill').textContent = '离线';
    } finally {
      if (showSpin) setTimeout(() => { $('#bRefresh').textContent = '刷新'; }, 500);
    }
  }

  function renderAll() {
    renderTop();
    renderAct();
    drawBoard();
    drawBiz();
    renderEvents();
    renderBoardOverlay();
    renderOps();
    syncRunning();
    const dl = $('#ownerList');
    if (dl && S) dl.innerHTML = ['longyi', ...S.agents.map(a => a.name)].map(n => '<option value="' + esc(n) + '">').join('');
  }

  function renderTop() {
    const k = boardStats(), llm = (S && S.llm) || {}, gate = S && S.project && S.project.gate || {};
    const gateOk = !!gate.confirmed && !gate.revoked_at;
    const gp = $('#gatePill');
    gp.className = 'pill ' + (gateOk ? 'ok' : 'warn');
    gp.textContent = gate.revoked_at ? '闸门已撤销' : gate.confirmed ? '闸门已确认' : '闸门未确认';
    $('#s1').innerHTML = '在岗 <b>' + (S ? S.agents.length : 0) + '</b> · 未关闭 <b>' + k.open + '</b>';
    $('#s2').innerHTML = '今日 token <b>' + Number(llm.tokens_today || 0).toLocaleString() + '</b> / ' + Number(llm.token_limit || 0).toLocaleString();
  }

  function renderAct() {
    if (!S) return;
    const k = boardStats();
    const list = sortTasks(S.tasks.filter(t => !isClosed(t))).slice(0, 3);
    $('#actMeta').textContent = 'TOP ' + list.length;
    $('#hoursBar').style.width = (k.hoursTotal ? Math.round(k.hoursDone / k.hoursTotal * 100) : 0) + '%';
    $('#attList').innerHTML = list.length ? list.map(t => {
      const lf = leftDays(t);
      const time = isOver(t) ? t.state : t.due === S.today ? '今日到期' : lf != null ? '剩 ' + lf + ' 天' : '—';
      return '<div class="att" data-id="' + t.id + '">' +
        '<div><b>' + esc(t.title) + '</b><small>' + t.id + ' · ' + esc(ownerLabel(t.owner)) + ' · ' + esc(t.milestone || '未挂里程碑') + '</small></div>' +
        '<span class="sp' + (isOver(t) || t.due === S.today ? ' over' : '') + '">' + esc(time) + '</span></div>';
    }).join('') : '<p style="color:var(--t2);padding:6px 0">没有未关闭任务，台账干净。</p>';
    $$('#attList .att').forEach(el => el.addEventListener('click', () => openTask(el.dataset.id)));
    $('#actFoot').textContent = '未关 ' + k.open + ' · 今日到期 ' + k.dueToday + ' · 逾期 ' + k.over +
      ' · 工时 ' + k.hoursDone + '/' + k.hoursTotal + 'h';
  }

  // ===== 画布：任务看板 =====
  let KC = null, KT = null, BC = null, BT = null;
  function rr(c, x, y, w, h, r) {
    c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }
  function fitT(c, s, w) { while (c.measureText(s).width > w && s.length > 1) s = s.slice(0, -1); return s; }

  function drawBoard() {
    if (!KC) return;
    const c = KC.getContext('2d'), k = boardStats();
    c.fillStyle = '#0f141d'; c.fillRect(0, 0, 2304, 704);
    c.textAlign = 'left';
    c.font = '600 34px ' + FONT; c.fillStyle = C.ink;
    c.fillText('OPC-002 · FDE 一人公司 · 任务台账 · ' + (S ? S.today : '—'), 36, 56);
    c.font = '26px ' + FONT; c.fillStyle = C.dim;
    c.fillText('未关闭 ' + k.open + '　今日到期 ' + k.dueToday + '　进行中 ' + k.doing + '　临期 ' + k.soon + '　逾期 ' + k.over + '　已关闭 ' + k.closed, 1010, 54);
    const cols = [
      ['待开始', t => colOf(t) === 'todo'],
      ['进行中', t => colOf(t) === 'doing'],
      ['临期 / 逾期', t => colOf(t) === 'risk'],
      ['最近关闭', t => colOf(t) === 'closed']
    ];
    cols.forEach((cl, i) => {
      const x = 24 + i * 564;
      c.fillStyle = '#18202c'; rr(c, x, 84, 540, 596, 24); c.fill();
      let list = sortTasks((S ? S.tasks : []).filter(cl[1]));
      c.font = '600 30px ' + FONT; c.fillStyle = C.ink; c.fillText(cl[0], x + 22, 128);
      c.textAlign = 'right'; c.fillStyle = C.dim; c.fillText(String(list.length), x + 516, 128); c.textAlign = 'left';
      if (!list.length) {
        c.strokeStyle = '#3a4658'; c.setLineDash([10, 8]); rr(c, x + 20, 150, 500, 90, 14); c.stroke(); c.setLineDash([]);
        c.font = '26px ' + FONT; c.fillStyle = C.dim; c.fillText('暂无任务', x + 210, 205);
      }
      if (i === 3) list = list.slice(0, 3);
      list.slice(0, 4).forEach((t, j) => {
        const y = 148 + j * 132, over = isOver(t);
        c.fillStyle = hlTask === t.id ? '#2a3a55' : '#222c3a';
        rr(c, x + 16, y, 508, 120, 16); c.fill();
        if (hlTask === t.id) { c.strokeStyle = C.ac; c.lineWidth = 4; rr(c, x + 16, y, 508, 120, 16); c.stroke(); c.lineWidth = 1; }
        c.fillStyle = stateColor(t.state); rr(c, x + 16, y, 10, 120, 5); c.fill();
        c.font = '600 24px ' + MONO; c.fillStyle = C.dim;
        c.fillText(t.id + ' · ' + t.state, x + 40, y + 32);
        c.font = '600 28px ' + FONT; c.fillStyle = C.ink;
        c.fillText(fitT(c, t.title, 452), x + 40, y + 68);
        c.font = '22px ' + FONT; c.fillStyle = over || t.due === (S && S.today) ? C.red : C.dim;
        const lf = leftDays(t);
        const tail = isClosed(t) ? '' : over ? ' ' + t.state : t.due === (S && S.today) ? ' 今日到期' : lf != null && lf >= 0 ? ' 剩' + lf + '天' : '';
        c.fillText(ownerLabel(t.owner) + ' · 截止 ' + shortDate(t.due) + tail, x + 40, y + 100);
        const tags = [];
        if (t.milestone && t.milestone !== '—') tags.push(t.milestone);
        if (t.hours) tags.push(t.hours + 'h');
        if (isClosed(t)) tags.push(t.evidence ? '证据已留' : '缺证据');
        c.textAlign = 'right'; c.font = '600 21px ' + FONT;
        tags.slice(0, 2).forEach((q, n) => { c.fillStyle = n === 0 ? '#9fb5cc' : '#c9d3e1'; c.fillText(fitT(c, q, 260), x + 512, y + 32 + n * 32); });
        c.textAlign = 'left';
      });
    });
    KT.needsUpdate = true;
  }

  // ===== 画布：经营看板 =====
  function drawBiz() {
    if (!BC) return;
    const c = BC.getContext('2d'), k = boardStats();
    const llm = (S && S.llm) || {}, f = (S && S.finance) || {}, inv = f.invoices || {};
    c.fillStyle = '#10151f'; c.fillRect(0, 0, 1024, 512);
    c.textAlign = 'left';
    c.fillStyle = C.dim; c.font = '22px ' + FONT;
    c.fillText('经营看板 · ' + (S ? S.today : '—') + ' · 实时读取台账', 24, 38);
    const big = [
      [String(k.open), '未关闭任务'],
      [String(k.dueToday), '今日到期'],
      [String(k.over), '逾期'],
      [Math.round((llm.tokens_today || 0) / 1000) + 'k', '今日 token']
    ];
    big.forEach((q, i) => {
      c.fillStyle = i === 2 && k.over ? C.red : C.ink; c.font = '700 76px ' + FONT;
      c.fillText(q[0], 24 + i * 250, 148);
      c.fillStyle = C.dim; c.font = '22px ' + FONT; c.fillText(q[1], 24 + i * 250, 184);
    });
    [
      ['已开票', money(inv.issued)],
      ['已回款', money(inv.received)],
      ['未收', money(inv.outstanding)],
      ['成本', money(f.costs)]
    ].forEach((q, i) => {
      c.fillStyle = '#18202c'; rr(c, 24 + i * 246, 220, 230, 100, 16); c.fill();
      c.fillStyle = C.dim; c.font = '22px ' + FONT; c.fillText(q[0], 42 + i * 246, 256);
      c.fillStyle = C.ink; c.font = '600 34px ' + FONT; c.fillText(fitT(c, q[1], 200), 42 + i * 246, 300);
    });
    const ms = (S && S.milestones || []).slice(0, 6);
    ms.forEach((g, i) => {
      const done = g.tasks.filter(t => t.state === '已关闭').length;
      const pct = g.tasks.length ? done / g.tasks.length : 0;
      const x = 24 + i * 164;
      c.fillStyle = '#2a3442'; rr(c, x, 396, 150, 10, 5); c.fill();
      c.fillStyle = g.closed ? C.gn : C.ac; rr(c, x, 396, Math.max(4, 150 * pct), 10, 5); c.fill();
      c.fillStyle = C.dim; c.font = '18px ' + FONT; c.fillText(fitT(c, g.key, 150), x, 434);
      c.fillText(done + '/' + g.tasks.length + ' · ' + g.hours + 'h', x, 458);
    });
    if (!ms.length) { c.fillStyle = C.dim; c.font = '20px ' + FONT; c.fillText('还没有挂里程碑的任务', 24, 430); }
    c.fillStyle = '#5d6979'; c.font = '18px ' + FONT;
    c.fillText('事实源 agency/state/agency.db · 飞书只做映射与提醒 · 本地 127.0.0.1', 24, 492);
    BT.needsUpdate = true;
  }
  // ===== 3D 场景 =====
  let scene = null, camera = null, renderer = null, sun = null;
  let boardMesh = null, bizMesh = null, hlTask = null;
  let THREE_OK = typeof THREE !== 'undefined';
  let V3 = null;

  function initScene() {
    const cv = (w, h, fn) => {
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const x = c.getContext('2d'); x.fillStyle = '#0f141d'; x.fillRect(0, 0, w, h);
      if (fn) fn(x); return c;
    };
    renderer = new THREE.WebGLRenderer({ canvas: $('#c'), antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d1320);
    scene.fog = new THREE.Fog(0x0d1320, 34, 74);
    camera = new THREE.PerspectiveCamera(45, 1, 0.1, 130);
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    camera.position.set(0, 9.5, 22);
    renderer.setSize(window.innerWidth, window.innerHeight);
    applyFit();

    scene.add(new THREE.HemisphereLight(0x9fc4ff, 0x141a24, 0.85));
    sun = new THREE.DirectionalLight(0xfff3dd, 0.9);
    sun.position.set(-9, 17, 9); sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    sun.shadow.camera.left = -26; sun.shadow.camera.right = 26;
    sun.shadow.camera.top = 20; sun.shadow.camera.bottom = -20;
    scene.add(sun);
    [[-3, 3.6, 0], [4, 3.6, 0], [0, 3.6, -3], [14, 3.4, 0]].forEach(([x, y, z]) => {
      const l = new THREE.PointLight(0xdce9ff, 0.5, 16); l.position.set(x, y, z); scene.add(l);
    });

    const woodM = new THREE.MeshStandardMaterial({ color: 0x7d6246, roughness: 0.8 });
    const woodD = new THREE.MeshStandardMaterial({ color: 0x4a3b2d, roughness: 0.88 });
    const wallM = new THREE.MeshStandardMaterial({ color: 0x2c333f, roughness: 0.94 });
    const concM = new THREE.MeshStandardMaterial({ color: 0x3a414c, roughness: 0.95 });
    const glass = new THREE.MeshPhysicalMaterial({ color: 0xa9d8ff, transparent: true, opacity: 0.16, roughness: 0.06, metalness: 0.1, side: THREE.DoubleSide });
    const frameM = new THREE.MeshStandardMaterial({ color: 0x2b3038, roughness: 0.45, metalness: 0.55 });
    const darkM = new THREE.MeshStandardMaterial({ color: 0x30363d, roughness: 0.7 });
    const fabM = new THREE.MeshStandardMaterial({ color: 0x5e6d7e, roughness: 0.86 });
    const accwood = new THREE.MeshStandardMaterial({ color: 0x8f7551, roughness: 0.8 });
    const gold = new THREE.MeshStandardMaterial({ color: 0xc9a35f, roughness: 0.32, metalness: 0.72 });
    const bookM = [0x6a5846, 0x49607a, 0x7c5a42, 0x3f6457, 0x6b4f6e].map(c => new THREE.MeshStandardMaterial({ color: c, roughness: 0.84 }));
    const plantSpot = new THREE.SpotLight(0xffe2ae, 0.55, 8, 0.9, 0.5, 1);
    plantSpot.position.set(13.6, 4, 0.6); plantSpot.target.position.set(13.6, 1, 0.6); scene.add(plantSpot, plantSpot.target);

    function bx(w, h, d, mat, x, y, z, p = scene) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
      m.position.set(x, y, z); m.castShadow = true; m.receiveShadow = true; (p || scene).add(m); return m;
    }
    function pl(w, d, mat, x, y, z, p = scene) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), mat);
      m.rotation.x = -Math.PI / 2; m.position.set(x, y, z); m.receiveShadow = true; (p || scene).add(m); return m;
    }
    function cyl(rt, rb, h, mat, x, y, z, p = scene) {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, 18), mat);
      m.position.set(x, y, z); m.castShadow = true; (p || scene).add(m); return m;
    }

    // 地板
    pl(17, 13, concM, 0, 0, 0);
    pl(11.2, 13, woodM, 14, 0, 0);
    const grid = new THREE.GridHelper(17, 17, 0x33414f, 0x28323f);
    grid.position.set(0, 0.014, 0); scene.add(grid);

    // 墙体
    bx(17, 5.6, 0.2, wallM, 0, 2.8, -6.5);
    bx(0.2, 5.6, 13, wallM, -8.6, 2.8, 0);
    bx(0.2, 5.6, 13, wallM, 19.6, 2.8, 0);
    bx(11.2, 5.6, 0.2, wallM, 14, 2.8, -6.5);
    bx(17, 0.2, 0.1, woodD, 0, 2.9, -6.39);
    bx(11.2, 0.2, 0.1, woodD, 14, 2.9, -6.39);
    bx(0.08, 3.2, 13, glass, 8.4, 1.6, 0);
    bx(0.26, 3.24, 0.18, frameM, 8.4, 1.62, -6.42);
    bx(2.2, 3.2, 0.08, glass, 9.5, 1.6, 1.9);
    bx(0.26, 3.24, 3.9, frameM, 8.4, 1.62, -4.38);
    bx(0.26, 3.24, 3.0, frameM, 8.4, 1.62, 4.9);

    // 任务看板
    KC = cv(2304, 704);
    KT = new THREE.CanvasTexture(KC);
    const bmat = new THREE.MeshBasicMaterial({ map: KT });
    boardMesh = bx(14, 4.2, 0.12, bmat, 0, 2.8, -6.34);
    boardMesh.userData.fn = openBoard;
    bx(14.4, 0.12, 0.3, frameM, 0, 4.96, -6.34);
    bx(14.4, 0.12, 0.3, frameM, 0, 0.66, -6.34);
    bx(0.12, 4.5, 0.3, frameM, -7.2, 2.81, -6.34);
    bx(0.12, 4.5, 0.3, frameM, 7.2, 2.81, -6.34);

    // 老板办公室经营看板
    BC = cv(1024, 512);
    BT = new THREE.CanvasTexture(BC);
    bizMesh = bx(4.9, 2.45, 0.1, new THREE.MeshBasicMaterial({ map: BT }), 14, 2.15, -6.35);
    bizMesh.userData.fn = openOps;
    bx(5.1, 0.12, 0.26, frameM, 14, 3.42, -6.35);
    bx(5.1, 0.12, 0.26, frameM, 14, 0.88, -6.35);

    // 开放区地毯 / 绿植
    const rug = new THREE.Mesh(new THREE.PlaneGeometry(12, 2.3), new THREE.MeshStandardMaterial({ color: 0x25384a, roughness: 0.95 }));
    rug.rotation.x = -Math.PI / 2; rug.position.set(0, 0.02, 1.6); rug.receiveShadow = true; scene.add(rug);
    [[-7.6, -5.5], [7.4, -5.5], [-7.8, 5.4], [7.6, 5.4], [9.6, 5.5], [18.6, 5.6]].forEach(([x, z]) => {
      cyl(0.42, 0.34, 0.62, new THREE.MeshStandardMaterial({ color: 0xb9b2a4, roughness: 0.8 }), x, 0.31, z);
      const leaf = new THREE.MeshStandardMaterial({ color: 0x3f7b4a, roughness: 0.9 });
      for (let i = 0; i < 5; i++) {
        const l = new THREE.Mesh(new THREE.SphereGeometry(0.34, 8, 6), leaf);
        l.position.set(x + Math.cos(i * 1.26) * 0.26, 0.92 + (i % 2) * 0.2, z + Math.sin(i * 1.26) * 0.26);
        l.scale.set(1, 1.5, 0.7); l.castShadow = true; scene.add(l);
      }
    });
    V3 = new THREE.Vector3();
    buildFurniture({ bx, pl, cyl, frameM, woodM, woodD, fabM, darkM, accwood, gold, bookM, glass });
    scene.children.forEach(o => { if (o.isMesh || o.isGroup || o.isPointLight || o.isSprite) o.userData.room = o.position.x > 9 ? 'b' : 'o'; });
  }

  const SEATS = {};
  function buildFurniture(M) {
    const { bx, cyl, woodM, woodD, frameM, darkM, fabM, accwood, gold, bookM } = M;
    function desk(x, z, dir, color) {
      const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = dir > 0 ? 0 : Math.PI; scene.add(g);
      const TOP = 1.1; // 桌面高度：对齐 1.45 倍人物的坐姿髋部，人才像坐在桌前
      bx(2.0, 0.07, 1, woodM, 0, TOP, 0, g);
      [-0.88, 0.88].forEach(lx => [-0.42, 0.42].forEach(lz => bx(0.07, TOP - 0.04, 0.07, frameM, lx, (TOP - 0.04) / 2, lz, g)));
      bx(0.6, 0.94, 0.5, woodD, 0.58, 0.47, 0.02, g);
      bx(1.94, 0.58, 0.05, woodD, 0, 0.81, 0.46, g); // 前挡板：遮住腿部，坐姿更明确
      const mon = bx(0.74, 0.44, 0.04, darkM, 0, TOP + 0.27, -0.2, g);
      bx(0.06, 0.2, 0.06, frameM, 0, TOP + 0.11, -0.2, g);
      const scr = new THREE.Mesh(new THREE.PlaneGeometry(0.66, 0.37),
        new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: 0.82 }));
      scr.position.set(0, TOP + 0.28, -0.176); g.add(scr);
      bx(0.58, 0.025, 0.17, new THREE.MeshStandardMaterial({ color: 0x22262c, roughness: 0.6 }), 0, TOP + 0.04, 0.14, g);
      cyl(0.055, 0.045, 0.1, new THREE.MeshStandardMaterial({ color: color, roughness: 0.5 }), 0.68, TOP + 0.09, 0.24, g);
      bx(0.56, 0.07, 0.52, fabM, 0, 0.7, 1.02, g);
      bx(0.56, 0.62, 0.08, fabM, 0, 1.06, 1.29, g);
      cyl(0.04, 0.04, 0.66, frameM, 0, 0.33, 1.02, g);
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.26, 0.025, 8, 16), frameM);
      ring.rotation.x = -Math.PI / 2; ring.position.set(0, 0.03, 1.02); g.add(ring);
      return g;
    }
    // 两排都面向镜头（+z），桌子挡在身前，看起来是坐在桌前
    ROLE_ORDER.slice(0, 6).forEach((r, i) => {
      const x = -7 + i * 2.8;
      SEATS[r] = { x, z: 0.72, ry: 0 };
      desk(x, 1.75, -1, ROLE_META[r].color);
    });
    ROLE_ORDER.slice(6).forEach((r, i) => {
      const x = -5.6 + i * 2.8;
      SEATS[r] = { x, z: 3.45, ry: 0 };
      desk(x, 4.5, -1, ROLE_META[r].color);
    });

    // 老板桌（独立函数：宽 3.6m × 深 1.8m，弧形前缘朝 +z 会客区方向）
    // 几何约定：g.position=(14,0,-0.2) → 桌面中心世界 z=-0.2
    //   桌面 g.z ∈ [-0.9, +0.9] → 世界 z ∈ [-1.1, +0.7]
    //   前缘 g.z=+0.9 → 世界 z=+0.7（朝会客区/相机）
    //   后缘 g.z=-0.9 → 世界 z=-1.1（朝经济看板墙）
    // 老板坐桌子后方（g.z≈-1.1，世界 z≈-1.3），面朝 +z 看相机/客人
    function bossDesk(bx, cyl, woodM, woodD, frameM, darkM, fabM, gold) {
      const g = new THREE.Group(); g.position.set(14, 0, -0.2); scene.add(g);
      const TOP = 1.12;
      // === 桌面主体 ===
      bx(3.6, 0.09, 1.8, woodM, 0, TOP, 0, g);
      // 前镀金边
      bx(3.5, 0.02, 0.03, gold, 0, TOP + 0.055, 0.97, g);
      // 桌面边缘打磨（四周深色）
      bx(3.62, 0.11, 0.02, woodD, 0, TOP + 0.01, 0.9, g);
      bx(3.62, 0.11, 0.02, woodD, 0, TOP + 0.01, -0.9, g);
      bx(0.02, 0.11, 1.82, woodD, -1.8, TOP + 0.01, 0, g);
      bx(0.02, 0.11, 1.82, woodD, 1.8, TOP + 0.01, 0, g);
      // 桌腿（4 根，内缩）
      [-1.5, -0.5, 0.5, 1.5].forEach(lx => {
        [-0.65, 0.65].forEach(lz => {
          bx(0.08, TOP - 0.06, 0.08, frameM, lx, (TOP - 0.06) / 2, lz, g);
        });
      });
      // 中部横梁（前后腿之间）
      bx(0.04, 0.1, 1.4, frameM, 0, 0.35, 0, g);
      // === 右侧边柜（内嵌式，和桌面一体） ===
      bx(0.85, 0.88, 0.75, woodD, 1.88, 0.48, -0.15, g);
      bx(0.83, 0.03, 0.73, gold, 1.88, 0.925, -0.15, g);
      bx(0.8, 0.8, 0.01, woodM, 1.88, 0.52, 0.23, g);
      bx(0.15, 0.025, 0.025, gold, 1.88, 0.56, 0.25, g);
      // === 老板大显示器（桌面上方靠前，龙奕低头能看到） ===
      bx(0.92, 0.58, 0.045, darkM, -0.4, TOP + 0.36, 0.4, g);
      bx(0.1, 0.3, 0.09, frameM, -0.4, TOP + 0.16, 0.4, g);
      const bmon = new THREE.Mesh(new THREE.PlaneGeometry(0.84, 0.52),
        new THREE.MeshBasicMaterial({ color: 0x2B4A6F }));
      bmon.position.set(-0.4, TOP + 0.37, 0.43); g.add(bmon);
      // === 座机（桌面右侧） ===
      bx(0.32, 0.04, 0.24, darkM, 1.1, TOP + 0.04, 0.55, g);
      bx(0.26, 0.2, 0.14, frameM, 1.1, TOP + 0.16, 0.4, g);
      // === 合同文件夹（桌面右侧稍后） ===
      bx(0.45, 0.02, 0.32, new THREE.MeshStandardMaterial({ color: 0x4A2E1A, roughness: 0.7 }), 1.5, TOP + 0.01, -0.3, g);
      bx(0.42, 0.01, 0.3, new THREE.MeshStandardMaterial({ color: 0xF0E8D8 }), 1.5, TOP + 0.04, -0.25, g);
      bx(0.4, 0.01, 0.28, new THREE.MeshStandardMaterial({ color: 0xEAE2D2 }), 1.5, TOP + 0.05, -0.2, g);
      // === 左上盆栽 ===
      cyl(0.12, 0.1, 0.18, new THREE.MeshStandardMaterial({ color: 0x7a5c3a, roughness: 0.7 }), -1.45, TOP + 0.09, -0.35, g);
      const leaf2 = new THREE.MeshStandardMaterial({ color: 0x4f8a53, roughness: 0.9 });
      [-0.42, -0.2, 0.0, 0.22, 0.4].forEach((off, i) => {
        const l = new THREE.Mesh(new THREE.SphereGeometry(0.18, 8, 6), leaf2);
        l.position.set(-1.45 + off * 0.28, TOP + 0.3 + (i % 2) * 0.08, -0.35 + off * 0.05);
        l.scale.set(1, 1.4, 0.8); g.add(l);
      });
      // === 桌下键盘 & 鼠标 ===
      bx(0.66, 0.025, 0.2, new THREE.MeshStandardMaterial({ color: 0x22262c, roughness: 0.6 }), -0.4, TOP + 0.04, 0.25, g);
      cyl(0.055, 0.045, 0.1, new THREE.MeshStandardMaterial({ color: 0x2d3644, roughness: 0.5 }), -0.1, TOP + 0.09, 0.4, g);
      // === 老板专属椅子（桌子后方，朝 +z 坐） ===
      // 椅面中心 g.z=-1.1 → 世界 z=-1.3；靠背更靠 -z
      bx(0.7, 0.08, 0.62, fabM, 0, 0.82, -1.1, g);
      bx(0.7, 0.85, 0.1, fabM, 0, 1.25, -1.45, g);
      // 扶手
      bx(0.08, 0.12, 0.45, fabM, -0.38, 0.92, -1.05, g);
      bx(0.08, 0.12, 0.45, fabM, 0.38, 0.92, -1.05, g);
      // 椅子支柱
      cyl(0.055, 0.055, 0.56, frameM, 0, 0.43, -1.1, g);
      const base = new THREE.Mesh(new THREE.TorusGeometry(0.34, 0.03, 8, 16), frameM);
      base.rotation.x = -Math.PI / 2; base.position.set(0, 0.03, -1.1); g.add(base);
      // 椅轮（5 个小点）
      for (let i = 0; i < 5; i++) {
        const a = (i / 5) * Math.PI * 2;
        cyl(0.03, 0.03, 0.04, frameM, Math.cos(a) * 0.32, 0.02, -1.1 + Math.sin(a) * 0.32, g);
      }
      return g;
    }
    bossDesk(bx, cyl, woodM, woodD, frameM, darkM, fabM, gold);
    // 龙奕的座位：老板桌 group.z=-0.2、椅面 g.z=-1.1 → 世界 z=-1.3；面朝 +z（看会客区/镜头方向）
    SEATS.longyi = { x: 14, z: -1.3, ry: 0 };
    const rugB = new THREE.Mesh(new THREE.PlaneGeometry(7.4, 5.2), new THREE.MeshStandardMaterial({ color: 0x21303f, roughness: 0.96 }));
    rugB.rotation.x = -Math.PI / 2; rugB.position.set(14, 0.016, 1.6); rugB.receiveShadow = true; scene.add(rugB);
    cyl(0.9, 0.95, 0.05, accwood, 16.6, 0.42, 3.7);
    cyl(0.06, 0.06, 0.42, frameM, 16.6, 0.21, 3.7);
    [[15.6, 3.7], [17.6, 3.7]].forEach(([x, z]) => {
      bx(0.9, 0.34, 0.9, fabM, x, 0.44, z);
      bx(0.9, 0.5, 0.14, fabM, x, 0.62, z + (x < 16.6 ? 0.42 : -0.42));
    });
    // 书架
    const shelf = new THREE.Group(); shelf.position.set(19.15, 0, -2.4); scene.add(shelf);
    bx(0.5, 3.0, 2.6, woodD, 0, 1.5, 0, shelf);
    for (let lv = 0; lv < 4; lv++) {
      const y = 0.5 + lv * 0.62;
      for (let i = 0; i < 9; i++) {
        const bh = 0.3 + ((i + lv) % 3) * 0.05;
        bx(0.3, bh, 0.16, bookM[(i + lv) % bookM.length], -0.32, y + bh / 2, -1.0 + i * 0.24, shelf);
      }
    }
    shelf.userData.fn = openArchive;
    lb3('档案室 ARCHIVE', 19.1, 3.4, -2.4);

    // 保险柜 / 保密室
    const safe = new THREE.Group(); safe.position.set(9.7, 0, -5.7); scene.add(safe);
    bx(0.9, 1.1, 0.8, darkM, 0, 0.55, 0, safe);
    const dial = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.03, 8, 20), gold);
    dial.position.set(0, 0.62, 0.41); safe.add(dial);
    bx(0.04, 0.34, 0.02, gold, 0.18, 0.62, 0.41, safe);
    safe.userData.fn = openEnv;
    lb3('保密室 ENV', 9.7, 1.55, -5.7);

    // 宪法基座
    const ped = new THREE.Group(); ped.position.set(17.9, 0, -5.6); scene.add(ped);
    cyl(0.4, 0.46, 1.0, accwood, 0, 0.5, 0, ped);
    bx(0.62, 0.06, 0.62, gold, 0, 1.03, 0, ped);
    const book = bx(0.5, 0.16, 0.36, new THREE.MeshStandardMaterial({ color: 0x7a2f2f, roughness: 0.6 }), 0, 1.14, 0, ped);
    bx(0.03, 0.16, 0.36, gold, 0.2, 1.14, 0, ped);
    ped.userData.fn = openConstitution;
    lb3('机要室 · 宪法', 17.9, 1.75, -5.6);

    // 电话
    const ph = new THREE.Group(); ph.position.set(13.0, 1.14, 1.2); scene.add(ph);
    bx(0.3, 0.06, 0.24, new THREE.MeshStandardMaterial({ color: 0x1f242b, roughness: 0.5 }), 0, 0.02, 0, ph);
    const hs = new THREE.Group(); hs.position.set(0, 0.12, 0); ph.add(hs);
    bx(0.34, 0.07, 0.09, new THREE.MeshStandardMaterial({ color: 0x2c323a, roughness: 0.45 }), 0, 0, 0, hs);
    bx(0.09, 0.06, 0.11, new THREE.MeshStandardMaterial({ color: 0x2c323a }), -0.14, -0.01, 0.03, hs);
    bx(0.09, 0.06, 0.11, new THREE.MeshStandardMaterial({ color: 0x2c323a }), 0.14, -0.01, 0.03, hs);
    ph.userData.fn = openRun;
    lb3('电话 · 派活', 12.3, 2.0, 1.6);

    // 摆设
    [[10.6, 4.9, 0.5], [12.2, 5.2, 0.32]].forEach(([x, z, r]) => {
      cyl(r, r, 0.9, new THREE.MeshStandardMaterial({ color: 0x5b5142, roughness: 0.9 }), x, 0.45, z);
    });
  }

  function lb3(txt, x, y, z) {
    const c = document.createElement('canvas'); c.width = 512; c.height = 128;
    const g = c.getContext('2d');
    g.fillStyle = 'rgba(9,13,20,.84)'; rr(g, 4, 20, 504, 88, 40); g.fill();
    g.strokeStyle = 'rgba(255,255,255,.32)'; g.lineWidth = 3; rr(g, 4, 20, 504, 88, 40); g.stroke();
    g.fillStyle = '#EEF2F8'; g.font = '600 42px ' + FONT; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(txt, 256, 65);
    const t = new THREE.CanvasTexture(c);
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, depthWrite: false }));
    s.position.set(x, y, z); s.scale.set(1.9, 0.48, 1); scene.add(s); return s;
  }

  function fig(role, override) {
    const m = override || ROLE_META[role];
    const g = new THREE.Group();
    const body = new THREE.MeshStandardMaterial({ color: m.color, roughness: 0.82 });
    const skin = new THREE.MeshStandardMaterial({ color: m.skin, roughness: 0.9 });
    const hair = new THREE.MeshStandardMaterial({ color: m.hair, roughness: 0.9 });
    const trouser = new THREE.MeshStandardMaterial({ color: 0x2b3038, roughness: 0.9 });
    const shoe = new THREE.MeshStandardMaterial({ color: 0x191d23, roughness: 0.85 });
    const add = (mesh) => { mesh.castShadow = true; g.add(mesh); return mesh; };
    const torso = add(new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.27, 0.72, 10), body));
    torso.position.y = 0.79;
    const lap = add(new THREE.Mesh(new THREE.BoxGeometry(0.56, 0.12, 0.32), body));
    lap.position.y = 0.43;
    const legL = add(new THREE.Mesh(new THREE.BoxGeometry(0.15, 0.44, 0.2), trouser)); legL.position.set(-0.13, 0.24, 0);
    const legR = add(new THREE.Mesh(new THREE.BoxGeometry(0.15, 0.44, 0.2), trouser)); legR.position.set(0.13, 0.24, 0);
    const footL = add(new THREE.Mesh(new THREE.BoxGeometry(0.17, 0.09, 0.28), shoe)); footL.position.set(-0.13, 0.045, 0.05);
    const footR = add(new THREE.Mesh(new THREE.BoxGeometry(0.17, 0.09, 0.28), shoe)); footR.position.set(0.13, 0.045, 0.05);
    const neck = add(new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.08, 0.12, 8), skin)); neck.position.y = 1.2;
    const head = add(new THREE.Mesh(new THREE.SphereGeometry(0.235, 18, 14), skin)); head.position.y = 1.37;
    const nose = add(new THREE.Mesh(new THREE.SphereGeometry(0.028, 8, 6), skin)); nose.position.set(0, 1.35, 0.228);
    [-0.075, 0.075].forEach(dx => {
      const eye = add(new THREE.Mesh(new THREE.SphereGeometry(0.021, 8, 6), new THREE.MeshBasicMaterial({ color: 0x232830 })));
      eye.position.set(dx, 1.4, 0.229);
    });
    const mouth = add(new THREE.Mesh(new THREE.BoxGeometry(0.075, 0.012, 0.01), new THREE.MeshBasicMaterial({ color: 0x8a4a45 })));
    mouth.position.set(0, 1.315, 0.226);
    const hairM = add(new THREE.Mesh(new THREE.SphereGeometry(0.248, 16, 12), hair));
    hairM.position.set(0, 1.43, -0.06); hairM.scale.set(1.03, 0.9, 1.02);
    if (m.female) {
      const bun = add(new THREE.Mesh(new THREE.SphereGeometry(0.11, 12, 10), hair));
      bun.position.set(0, 1.45, -0.24);
    }
    if (role === 'auditor' || role === 'hr' || role === 'planner') {
      [-0.09, 0.09].forEach(dx => {
        const ring = add(new THREE.Mesh(new THREE.TorusGeometry(0.055, 0.012, 6, 14), new THREE.MeshStandardMaterial({ color: 0x1a1e24, roughness: 0.4 })));
        ring.position.set(dx, 1.38, 0.22);
      });
      const bridge = add(new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.01, 0.01), new THREE.MeshStandardMaterial({ color: 0x1a1e24 })));
      bridge.position.set(0, 1.385, 0.232);
    }
    const badge = add(new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.09, 0.02), new THREE.MeshStandardMaterial({ color: 0xd8b26a, roughness: 0.4 })));
    badge.position.set(0.13, 0.92, 0.28);
    const armM = new THREE.MeshStandardMaterial({ color: m.color, roughness: 0.84 });
    const armL = add(new THREE.Mesh(new THREE.CylinderGeometry(0.064, 0.07, 0.56, 8), armM));
    armL.position.set(-0.35, 0.82, 0); armL.rotation.z = 0.13;
    const armR = add(new THREE.Mesh(new THREE.CylinderGeometry(0.064, 0.07, 0.56, 8), armM));
    armR.position.set(0.35, 0.82, 0); armR.rotation.z = -0.13;
    const handL = add(new THREE.Mesh(new THREE.SphereGeometry(0.072, 8, 6), skin)); handL.position.set(-0.39, 0.53, 0);
    const handR = add(new THREE.Mesh(new THREE.SphereGeometry(0.072, 8, 6), skin)); handR.position.set(0.39, 0.53, 0);
    g.scale.setScalar(1.45);
    g.userData.J = { armL, armR, legL, legR, handL, handR, footL, footR };
    return g;
  }
  // ===== 人物 / 镜头 / 交互 =====
  const P = {};
  const CAM = {
    o: { t: [0, 2.6, -0.9], th: 0.0, ph: 0.21, r: 15.2 },   // 默认：正面看板 + 两排工位
    k: { t: [0, 2.85, -6.2], th: 0.0, ph: 0.72, r: 11.5 },
    s: { t: [14, 1.4, -0.8], th: 0.0, ph: 0.28, r: 9.0 }  // 老板室：龙奕 z≈-1.3 坐桌后方面朝 +z，相机从 +z 侧会客区看过来
  };
  let camCur = null, camGoal = null;
  let camFitK = 1;
  // 竖屏 / 窄屏: 拉远并放宽视角, 保证看板与工位整体入画
  function applyFit() {
    const a = window.innerWidth / Math.max(1, window.innerHeight);
    camFitK = a >= 1.3 ? 1 : Math.min(1.9, 1.3 / Math.max(a, 0.4));
    if (camera) {
      camera.fov = a >= 1.3 ? 45 : Math.min(58, 45 + (1.3 - a) * 18);
      camera.aspect = a;
      camera.updateProjectionMatrix();
    }
    if (scene && scene.fog) { scene.fog.near = 34 * camFitK; scene.fog.far = 74 * camFitK; }
  }
  let dragging = null, dragMoved = 0, lastHover = 0;
  const raycaster = THREE_OK ? new THREE.Raycaster() : null;
  const pointer = THREE_OK ? new THREE.Vector2() : null;
  const clock = THREE_OK ? new THREE.Clock() : { getDelta: () => 0.016 };

  function mkLabel(name) {
    const el = document.createElement('div');
    el.className = 'lbl';
    el.innerHTML = '<div class="bub"></div><div class="tag"></div>';
    document.body.appendChild(el);
    return { el, bub: el.firstChild, tag: el.lastChild, name };
  }

  function spawnAgents() {
    ROLE_ORDER.forEach(role => {
      const s = SEATS[role], g = fig(role);
      g.position.set(s.x, 0, s.z); g.rotation.y = s.ry;
      g.traverse(o => { o.userData.k = role; });
      g.userData.k = role;
      g.userData.room = 'o';
      scene.add(g);
      P[role] = {
        key: role, g, x: s.x, z: s.z, seat: [s.x, s.z], ry: s.ry, path: [], moveTag: '',
        speech: '', speechUntil: 0, busy: false, autoBusy: false, moving: false,
        phase: Math.random() * 6, label: mkLabel(ROLE_META[role].name), resolvePath: null,
        // 状态机字段
        state: 'idle', stateUntil: 0, pauseUntil: 0, speechCooldown: 0,
        rushing: false, destination: null, chatTarget: null
      };
    });
    const bossMeta = { name: '龍奕', color: 0x2E3B4E, hair: 0x1b1b1f, skin: 0xE6B896, female: 0 };
    const b = fig(null, bossMeta);
    b.position.set(14, 0, -1.3); b.rotation.y = 0;
    b.traverse(o => { o.userData.k = 'longyi'; });
    b.userData.k = 'longyi';
    b.userData.room = 'b';
    scene.add(b);
    P.longyi = {
      key: 'longyi', g: b, x: 14, z: -1.3, seat: [14, -1.3], ry: 0, path: [], moveTag: '',
      speech: '', speechUntil: 0, busy: false, autoBusy: false, moving: false,
      phase: 0, label: mkLabel('龍奕'), resolvePath: null,
      state: 'idle', stateUntil: 0, pauseUntil: 0, speechCooldown: 0, rushing: false, destination: null, chatTarget: null
    };

    // CEO：调度层展示位（只展示，不在 agents/ 目录，不消耗 token）
    const ceoMeta = { name: 'CEO', color: 0x2A3140, hair: 0x232327, skin: 0xE6B896, female: 0 };
    const c = fig(null, ceoMeta);
    c.position.set(6.4, 0, -3.3); c.rotation.y = 0.16;
    c.traverse(o => { o.userData.k = 'ceo'; });
    c.userData.k = 'ceo';
    c.userData.room = 'o';
    scene.add(c);
    P.ceo = {
      key: 'ceo', g: c, x: 6.4, z: -3.3, seat: null, ry: 0.16, path: [], moveTag: '',
      speech: '', speechUntil: 0, busy: false, autoBusy: false, moving: false,
      phase: 0, label: mkLabel('CEO · 调度层'), resolvePath: null,
      state: 'idle', stateUntil: 0, pauseUntil: 0, speechCooldown: 0, rushing: false, destination: null, chatTarget: null
    };
  }

  function initCamera() {
    const c = CAM.o;
    camCur = { t: new THREE.Vector3(...c.t), th: c.th, ph: c.ph, r: c.r };
    camGoal = { t: new THREE.Vector3(...c.t), th: c.th, ph: c.ph, r: c.r };
  }

  function boardSpot(role) {
    const s = SEATS[role];
    return [Math.max(-6.4, Math.min(6.4, s.x)), -5.25];
  }

  function route0(s, t) {
    const near = (a, b) => Math.abs(a - b) < 0.06;
    const gaps = [-5.6, -2.8, 0, 2.8, 5.6];
    const SOUTH = 2.88, NORTH = -0.55;
    const w = [];
    const push = (x, z) => {
      if (!w.length || !near(w[w.length - 1][0], x) || !near(w[w.length - 1][1], z)) w.push([x, z]);
    };
    if (near(s[0], t[0]) && near(s[1], t[1])) return w;
    const lane = gaps.reduce((a, g) => Math.abs(g - s[0]) < Math.abs(a - s[0]) ? g : a, gaps[0]);
    if (t[1] < -2.2) {                       // 去任务看板
      if (s[1] > 2.4) { push(s[0], SOUTH); push(lane, SOUTH); }   // B 排：先退到两排之间
      else push(s[0], NORTH);                                     // A 排：从椅子后面绕
      push(lane, NORTH);
      push(t[0], NORTH);
      push(t[0], t[1]);
    } else if (s[1] < -2.2) {                // 从看板回座位
      push(s[0], NORTH);
      push(lane, NORTH);
      if (t[1] > 2.4) { push(lane, SOUTH); push(t[0], SOUTH); }
      else push(t[0], NORTH);
      push(t[0], t[1]);
    } else if (s[1] > 2.4 && t[1] < 2.4) {   // B 排 → A 区
      push(s[0], SOUTH); push(lane, SOUTH); push(lane, t[1]); push(t[0], t[1]);
    } else {
      const z = s[1] > 2.4 ? SOUTH : NORTH;
      push(s[0], z); push(t[0], z); push(t[0], t[1]);
    }
    return w;
  }

  function go(role, pt, tag) {
    const p = P[role];
    if (!p) return Promise.resolve();
    if (p.resolvePath) { const r = p.resolvePath; p.resolvePath = null; r(); }
    p.path = route0([p.x, p.z], pt);
    p.moveTag = tag || '';
    if (!p.path.length) return Promise.resolve();
    return new Promise(res => { p.resolvePath = res; });
  }

  // ===== 状态机辅助 =====
  function pickAgentSpot(role) {
    const s = SEATS[role];
    const others = ROLE_ORDER.filter(r => r !== role && SEATS[r] && Math.abs(SEATS[r].x - s.x) < 3.5);
    const pick = others[Math.floor(Math.random() * others.length)];
    if (pick) { const t = SEATS[pick]; return [t.x, t.z - 0.6]; }
    return boardSpot(role);
  }
  function pickRandomSpot() {
    // 两排之间通道的随机闲逛点，或看板前
    const gaps = [-5.6, -2.8, 0, 2.8, 5.6];
    const lane = gaps[Math.floor(Math.random() * gaps.length)];
    if (Math.random() < 0.55) return [lane + (Math.random() - 0.5) * 1.0, 1.5 + (Math.random() - 0.5) * 1.2];
    return [lane + (Math.random() - 0.5) * 1.0, -4.5 + Math.random() * 0.8];
  }
  function setState(p, state, opts = {}) {
    p.state = state;
    p.stateUntil = performance.now() + (opts.ms || (6000 + Math.random() * 10000));
    if (typeof opts.rushing === 'boolean') p.rushing = opts.rushing;
    if (opts.destination) p.destination = opts.destination;
    if (opts.chatTarget) p.chatTarget = opts.chatTarget;
  }
  async function tryLoiter(role) {
    const p = P[role]; if (!p) return;
    const prof = PROFILE[role];
    const spot = pickRandomSpot();
    setState(p, 'loitering', { ms: 5000 + Math.random() * 6000 });
    await go(role, spot, '逛逛');
    say(role, pickSpeech(role, 'loiter'), 2800);
    // 到点自己回座位（或在路上被打断）
  }
  async function tryChat(role) {
    const p = P[role]; if (!p) return;
    const prof = PROFILE[role];
    const others = ROLE_ORDER.filter(r => r !== role && P[r] && !P[r].busy && !P[r].state.startsWith('deliver'));
    if (!others.length) return;
    const target = others[Math.floor(Math.random() * others.length)];
    const tp = P[target];
    const spot = [tp.x, tp.z - 0.6];
    setState(p, 'chat', { ms: 6000 + Math.random() * 5000, chatTarget: target });
    await go(role, spot, '聊聊');
    say(role, pickSpeech(role, 'loiter'), 2500);
    if (Math.random() < 0.6) {
      setTimeout(() => say(target, pickSpeech(target, 'loiter'), 2500), 1100);
    }
  }
  // 任务交付行为：按 PROFILE.delivery 决定去向，过去后 say 交付台词，再回座位
  async function deliverBehavior(role, task, ok) {
    const p = P[role]; if (!p) return;
    const prof = PROFILE[role] || {};
    let target, tag;
    const d = prof.delivery || 'board';
    if (d === 'board') {
      target = boardSpot(role);
      tag = '去看板交付';
    } else if (d === 'longyi') {
      target = [13, -0.8];  // 老板桌前方会客区侧（龙奕面朝 +z，agent 站在他面前汇报）
      tag = '给老板汇报';
    } else {
      // random：随机挑一个非自己的 agent 工位前（同事交接）
      const others = ROLE_ORDER.filter(r => r !== role);
      const k = others[Math.floor(Math.random() * others.length)];
      const t = SEATS[k];
      target = [t.x, t.z - 0.7];
      tag = '交接给' + ROLE_META[k].name;
    }
    const rush = Math.random() < (prof.rushChance || 0.25);
    setState(p, 'delivering', { ms: 99999, rushing: rush, destination: target });
    await go(role, target, tag);
    // 说交付台词
    const speech = (ok ? pickSpeech(role, 'deliver') : '执行失败了，我来汇报一下');
    say(role, (task ? task + ' · ' : '') + speech, 3600);
    setState(p, 'delivering', { ms: 3200, rushing: false });
    // 站一会儿，回座位
    await new Promise(r => setTimeout(r, 3400));
    p.rushing = Math.random() < 0.15;
    await go(role, p.seat, '');
    p.rushing = false;
    setState(p, 'idle', { ms: 3000 + Math.random() * 4000 });
  }

  function say(role, text, ms = 2600) {
    const p = P[role];
    if (!p) return;
    p.speech = text;
    p.speechUntil = performance.now() + ms;
  }

  function setRoom(r) {
    currentRoom = r;
    if (scene) scene.children.forEach(o => { if (o.userData.room) o.visible = o.userData.room === r; });
    $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === r));
    $('#rooms').style.display = r === 'b' ? 'flex' : 'none';
    $('#tabs button[data-t="b"]').classList.toggle('on', r === 'b');
    $('#tabs button[data-t="o"]').classList.toggle('on', r === 'o');
  }

  function view(v) {
    if (!camGoal || !CAM[v]) return;
    const c = CAM[v];
    camGoal.t.set(...c.t); camGoal.th = c.th; camGoal.ph = c.ph; camGoal.r = c.r;
    setRoom(v === 's' ? 'b' : 'o');
    $$('#cam button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  }

  function findPick(ev) {
    if (!renderer) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(scene.children, true);
    for (const h of hits) {
      let q = h.object, vis = true;
      while (q) { if (!q.visible) { vis = false; break; } q = q.parent; }
      if (!vis) continue;
      q = h.object;
      while (q && !q.userData.k && !q.userData.fn) q = q.parent;
      if (!q) continue;
      if (q.userData.room && q.userData.room !== currentRoom) continue;
      return { key: q.userData.k || null, fn: q.userData.fn || null, obj: q };
    }
    return null;
  }

  function bindScene() {
    const cv = $('#c');
    cv.addEventListener('pointerdown', e => {
      dragging = { x: e.clientX, y: e.clientY }; dragMoved = 0;
      cv.setPointerCapture(e.pointerId); cv.style.cursor = 'grabbing';
    });
    cv.addEventListener('pointermove', e => {
      if (dragging) {
        const dx = e.clientX - dragging.x, dy = e.clientY - dragging.y;
        dragMoved += Math.abs(dx) + Math.abs(dy);
        dragging = { x: e.clientX, y: e.clientY };
        if (camGoal) {
          camGoal.th -= dx * 0.005;
          camGoal.ph = Math.max(0.35, Math.min(1.45, camGoal.ph + dy * 0.004));
        }
        return;
      }
      const now = performance.now();
      if (now - lastHover > 90) {
        lastHover = now;
        const hit = findPick(e);
        hoverKey = hit && hit.key ? hit.key : null;
        cv.style.cursor = hit ? 'pointer' : 'grab';
      }
    });
    cv.addEventListener('pointerup', e => {
      cv.style.cursor = 'grab';
      const wasDrag = dragMoved > 6;
      dragging = null;
      if (wasDrag) return;
      const hit = findPick(e);
      if (!hit) return;
      if (hit.key) openAgent(hit.key);
      else if (hit.fn) hit.fn();
    });
    cv.addEventListener('pointercancel', () => { dragging = null; cv.style.cursor = 'grab'; });
    cv.addEventListener('wheel', e => {
      e.preventDefault();
      if (camGoal) camGoal.r = Math.max(6, Math.min(34, camGoal.r + e.deltaY * 0.012));
    }, { passive: false });
  }

  function tick(now) {
    requestAnimationFrame(tick);
    const dt = Math.min(clock.getDelta(), 0.05);
    if (!scene || !camera) return;
    if (camCur && camGoal) {
      const k = 1 - Math.pow(0.001, dt);
      camCur.t.lerp(camGoal.t, k);
      camCur.th += (camGoal.th - camCur.th) * k;
      camCur.ph += (camGoal.ph - camCur.ph) * k;
      camCur.r += (camGoal.r - camCur.r) * k;
      const rr = camCur.r * camFitK;
      const hor = Math.cos(camCur.ph) * rr;
      camera.position.set(camCur.t.x + Math.sin(camCur.th) * hor, camCur.t.y + Math.sin(camCur.ph) * rr, camCur.t.z + Math.cos(camCur.th) * hor);
      camera.lookAt(camCur.t);
    }
    const t = now / 1000;
    Object.values(P).forEach(p => {
      if (!p.g || !p.key) return; // 跳过 P 上挂的非 agent 属性（如 _lastJobByRole）
      if (p.key === 'longyi' || p.key === 'ceo') {
        // 老板和 CEO 只做很简化的动画（无状态机）
        const J = p.g.userData.J;
        if (J) {
          J.armL.rotation.x = Math.sin(t * 1.1) * 0.05;
          J.armR.rotation.x = -Math.sin(t * 1.1) * 0.05;
        }
        return;
      }
      const prof = PROFILE[p.key] || { pace: 1, typingMul: 1, loiterChance: 0.003, chatChance: 0.2 };
      const s = SEATS[p.key];
      const atSeat = s && Math.abs(p.x - s.x) < 0.14 && Math.abs(p.z - s.z) < 0.14;
      const onBoard = !atSeat && p.z < -3.6;

      // ---- 走路（有路径即走）----
      if (p.path.length) {
        p.moving = true;
        const pace = prof.pace || 1;
        const step = 1.9 * dt * pace * (p.rushing ? 1.7 : 1);
        const [tx, tz] = p.path[0];
        let dx = tx - p.x, dz = tz - p.z;
        const d = Math.hypot(dx, dz);
        if (d <= step) { p.x = tx; p.z = tz; p.path.shift(); }
        else { p.x += dx / d * step; p.z += dz / d * step; }
        if (d > 0.001) p.g.rotation.y = Math.atan2(dx, dz);
        p.phase += dt * (p.rushing ? 14 : 9);
      } else if (p.moving) {
        p.moving = false;
        p.phase = 0;
        if (p.resolvePath) { const r = p.resolvePath; p.resolvePath = null; r(); }
      }

      // ---- 位置/朝向 ----
      p.g.position.x = p.x;
      p.g.position.z = p.z;
      p.g.position.y = atSeat ? 0.1 : 0;
      if (atSeat) p.g.rotation.y = s.ry;
      else if (!p.path.length && onBoard) p.g.rotation.y = Math.PI;

      // ---- 状态转换（只在没在路上时做决策）----
      if (!p.moving && !p.path.length) {
        const working = p.busy || p.autoBusy;
        if (working && p.state !== 'working') {
          setState(p, 'working', { ms: 99999 });
          p.stateUntil = 0;  // working 不自动到点，由 syncRunning 取消
        } else if (!working && p.state === 'working') {
          // 执行结束 —— 由 deliverBehavior 接管，不直接变 idle
        } else if (p.stateUntil && now > p.stateUntil) {
          // 上一个状态到点，决定下一个
          if (p.state === 'loitering') { setState(p, 'idle', { ms: 4000 + Math.random() * 6000 }); go(p.key, p.seat, ''); }
          else if (p.state === 'chat') { setState(p, 'idle', { ms: 3000 + Math.random() * 4000 }); p.chatTarget = null; }
          else if (p.state === 'delivering') { /* deliverBehavior 自己会去 go + setState */ }
          else if (p.state === 'rushing') { setState(p, 'idle', { ms: 3000 }); }
          else if (p.state === 'idle') {
            // idle 到点，按 PROFILE 概率触发 loiter / chat
            if (Math.random() < prof.loiterChance * 25 || Math.random() < prof.chatChance * 0.4) {
              (Math.random() < prof.chatChance * 0.7 ? tryChat : tryLoiter)(p.key);
            } else {
              setState(p, 'idle', { ms: 4000 + Math.random() * 6000 });
            }
          }
        } else if (!p.stateUntil && p.state === 'idle' && atSeat) {
          // 刚到 idle 状态，等一会儿再触发 loiter
          setState(p, 'idle', { ms: 5000 + Math.random() * 6000 });
        }
      }

      // ---- 随机说台词 ----
      if (!p.speechUntil && now > p.speechCooldown) {
        if (p.state === 'working' && Math.random() < 0.35) {
          say(p.key, pickSpeech(p.key, 'working'), 2000); p.speechCooldown = now + 4000;
        } else if (p.state === 'idle' && Math.random() < 0.05) {
          say(p.key, pickSpeech(p.key, 'idle'), 2200); p.speechCooldown = now + 8000;
        } else if (p.state === 'loitering' && Math.random() < 0.15) {
          say(p.key, pickSpeech(p.key, 'loiter'), 2200); p.speechCooldown = now + 5000;
        } else if (p.state === 'chat' && Math.random() < 0.3) {
          say(p.key, pickSpeech(p.key, 'loiter'), 2000); p.speechCooldown = now + 5000;
        }
      }

      // ---- 按状态选动画参数 ----
      const J = p.g.userData.J;
      if (atSeat) {
        J.legL.rotation.x = -1.32; J.legR.rotation.x = -1.32;
        J.legL.position.z = J.legR.position.z = 0.1;
        J.footL.position.z = J.footR.position.z = 0.42;
        let typing = 0.12;
        if (p.state === 'working') typing = 0.35 * (prof.typingMul || 1);
        else if (p.state === 'idle') typing = 0.08 * (prof.typingMul || 1);
        else if (p.state === 'loitering') typing = 0.1;
        J.armL.rotation.x = -0.75 + Math.sin(t * 9 + p.phase) * typing;
        J.armR.rotation.x = -0.75 + Math.cos(t * 9 + p.phase) * typing;
        J.handL.position.z = 0.3; J.handR.position.z = 0.3;
      } else if (p.moving) {
        const swing = p.rushing ? 0.8 : 0.55;
        J.legL.rotation.x = Math.sin(p.phase) * (p.rushing ? 0.8 : 0.5);
        J.legR.rotation.x = -Math.sin(p.phase) * (p.rushing ? 0.8 : 0.5);
        J.legL.position.z = J.legR.position.z = 0;
        J.footL.position.z = J.footR.position.z = 0.05;
        J.armL.rotation.x = -Math.sin(p.phase) * swing;
        J.armR.rotation.x = Math.sin(p.phase) * swing;
        J.handL.position.z = 0; J.handR.position.z = 0;
      } else {
        // 站着（非在座位、非走路）—— loiter / chat / deliver 中
        J.legL.rotation.x = J.legR.rotation.x = 0;
        J.legL.position.z = J.legR.position.z = 0; J.footL.position.z = J.footR.position.z = 0.05;
        if (p.state === 'loitering' || p.state === 'chat') {
          // 自然抖腿/手：低频小幅摆动
          const wobble = Math.sin(t * 1.2 + p.phase) * 0.08;
          J.armL.rotation.x = wobble - 0.02;
          J.armR.rotation.x = -wobble - 0.02;
        } else if (p.state === 'delivering') {
          // 交付：一手拿"东西"（前伸）
          J.armR.rotation.x = -0.9 + Math.sin(t * 2) * 0.04;
          J.armL.rotation.x = 0.05 + Math.sin(t * 1.5 + 1) * 0.03;
        } else {
          J.armL.rotation.x = Math.sin(t * 1.6) * 0.04;
          J.armR.rotation.x = -Math.sin(t * 1.6) * 0.04;
        }
        J.handL.position.z = 0; J.handR.position.z = 0;
      }

      // ---- 名字牌 + 气泡 ----
      if (p.speechUntil && now > p.speechUntil) { p.speech = ''; p.speechUntil = 0; }
      const tag = (p.busy || p.autoBusy) ? '执行中…'
        : (p.rushing ? '赶工!'
        : p.state === 'loitering' ? '摸鱼中'
        : p.state === 'chat' ? '聊天'
        : p.state === 'delivering' ? p.destination ? '交付中…' : '交付'
        : p.moveTag);
      const show = p.g.visible;
      const L = p.label;
      if (!show) { L.el.style.display = 'none'; return; }
      V3.set(p.x, atSeat ? 2.36 : 2.2, p.z).project(camera);
      if (V3.z > 1) { L.el.style.display = 'none'; return; }
      L.el.style.display = 'block';
      L.el.style.left = ((V3.x * 0.5 + 0.5) * window.innerWidth) + 'px';
      L.el.style.top = ((-V3.y * 0.5 + 0.5) * window.innerHeight) + 'px';
      L.bub.textContent = p.speech || L.name;
      L.bub.style.display = 'block';
      L.tag.textContent = tag;
      L.tag.style.display = tag ? 'inline-block' : 'none';
    });
    renderer.render(scene, camera);
  }

  let _prevRunning = new Set();
  function syncRunning() {
    if (!S || !THREE_OK || !P) return;
    const running = new Set((S.llm && S.llm.running) || []);
    // 新启动：进入 working 状态，走到看板前
    running.forEach(role => {
      const p = P[role];
      if (!p || p.busy || p.state === 'working') return;
      p.autoBusy = true;
      setState(p, 'working', { ms: 99999 });
      go(role, boardSpot(role), '去执行');
    });
    // 执行结束（不在 running 里但之前在，且 autoBusy=true）：触发交付行为
    _prevRunning.forEach(role => {
      if (running.has(role)) return;
      const p = P[role]; if (!p) return;
      if (p.autoBusy) {
        p.autoBusy = false;
        // 任务信息 —— 从 jobs 里找该角色最近的完成任务
        const j = (P._lastJobByRole || {})[role];
        const task = j && j.task ? j.task : '';
        const ok = !(j && j.ok === false);
        // 回座位，然后触发交付
        deliverBehavior(role, task, ok);
      }
    });
    _prevRunning = running;
  }
  // ===== 任务看板 =====
  function openBoard() { $('#boardOverlay').classList.add('o'); renderBoardOverlay(); }
  function closeBoard() { $('#boardOverlay').classList.remove('o'); }
  function openOps() { $('#opsOverlay').classList.add('o'); renderOps(); }
  function closeOps() { $('#opsOverlay').classList.remove('o'); }
  // ===== 商务 =====
  function openBiz() { $('#bizOverlay').classList.add('o'); renderBiz(); }
  function closeBiz() { $('#bizOverlay').classList.remove('o'); }
  const bizMoney = (v) => '¥' + (Number(v) || 0).toLocaleString('zh-CN');
  function bizTable(title, rows, cols) {
    if (!rows || !rows.length) {
      return '<section class="sec"><h3>' + title + ' <small>0</small></h3>' +
        '<p style="color:var(--muted)">暂无</p></section>';
    }
    return '<section class="sec"><h3>' + title + ' <small>' + rows.length + '</small></h3>' +
      '<div style="overflow:auto"><table style="width:100%;border-collapse:collapse;font-size:12px">' +
      '<tr>' + cols.map(c => '<th style="text-align:left;padding:4px 6px;color:var(--muted)">' + c + '</th>').join('') + '</tr>' +
      rows.map(r => '<tr>' + cols.map(c => '<td style="padding:4px 6px;border-top:1px solid var(--line)">' +
        esc(r[c] == null ? '—' : r[c]) + '</td>').join('') + '</tr>').join('') +
      '</table></div></section>';
  }
  // ===== 圆桌会议 =====
  function openRoundtable() { $('#rtOverlay').classList.add('o'); renderRoundtableList(); }
  function closeRoundtable() { $('#rtOverlay').classList.remove('o'); }
  async function renderRoundtableList() {
    const box = $('#rtList'); if (!box) return;
    let rows = [];
    try { rows = (await api('/api/v1/legacy/roundtable')).data || []; }
    catch { rows = []; }
    if (!rows.length) { box.innerHTML = '<p style="color:var(--muted);font-size:12px">暂无历史会议</p>'; return; }
    box.innerHTML = '<h4 style="margin:14px 0 6px;font-size:13px">历史会议（点击查看三份产物）</h4>' +
      rows.slice(0, 10).map(m => {
        const files = m.files || {};
        const links = Object.keys(files).map(k =>
          '<button class="ghost" data-rt-file="' + esc(files[k]) + '">' + k + '</button>').join(' ');
        return '<div style="padding:6px 0;border-top:1px solid var(--line);font-size:12px">' +
          '<b>' + esc(m.id) + '</b>｜' + esc((m.topic || '').slice(0, 40)) +
          '｜' + esc((m.roles || []).join('、')) + '｜' + (m.tokens ? m.tokens.total : 0) + ' tokens' +
          '<div style="margin-top:4px">' + links + '</div></div>';
      }).join('');
  }
  async function openRoundtableFile(path) {
    if (!path) return;
    try {
      const res = await fetch('/api/v1/legacy/output?path=' + encodeURIComponent(path),
        { headers: CONSOLE_TOKEN ? { 'X-Console-Token': CONSOLE_TOKEN } : {} });
      const txt = await res.text();
      if (!res.ok) throw new Error(txt || ('HTTP ' + res.status));
      $('#rtResult').textContent = txt;
    } catch (e) {
      $('#rtStatus').textContent = '读取失败：' + e.message;
    }
  }
  async function runRoundtable() {
    const topic = $('#rtTopic').value.trim();
    if (!topic) { $('#rtStatus').textContent = '请先填议题'; return; }
    const btn = $('#rtRun');
    btn.disabled = true; btn.textContent = '讨论中…';
    $('#rtStatus').textContent = '正在逐轮发言，通常需要 1–3 分钟…';
    $('#rtPath').textContent = '';
    try {
      const body = {
        topic,
        roles: $('#rtRoles').value.trim(),
        rounds: Number($('#rtRounds').value) || 0,
      };
      const d = await api('/api/v1/legacy/roundtable', { method: 'POST', body: JSON.stringify(body) });
      const r = d.data || {};
      const tk = r.tokens || {};
      $('#rtStatus').textContent = '角色：' + (r.roles || []).join(' → ')
        + '｜tokens：' + (tk.total || 0) + '｜耗时：' + (Number(r.elapsed) || 0).toFixed(1) + 's'
        + (r.stopped ? '｜预算中止' : '');
      $('#rtPath').textContent = r.path ? ('已保存：' + r.path) : '';
      $('#rtResult').textContent = r.markdown || '';
      renderRoundtableList();
    } catch (e) {
      $('#rtStatus').textContent = '失败：' + e.message;
    } finally {
      btn.disabled = false; btn.textContent = '开始讨论';
    }
  }
  // ===== 健康看板 =====
  function openHealth() { $('#healthOverlay').classList.add('o'); renderHealth(); }
  function closeHealth() { $('#healthOverlay').classList.remove('o'); }
  async function renderHealth() {
    const box = $('#healthBody');
    box.innerHTML = '<p style="color:var(--muted)">读取自检结果…</p>';
    let d;
    try { d = (await api('/api/v1/legacy/selfcheck')).data; }
    catch (e) { box.innerHTML = '<p style="color:#c33">自检失败：' + esc(e.message) + '</p>'; return; }
    const svc = d.services || {};
    const names = ['listener', 'console', 'tick', 'selfcheck'];
    const alive = names.every(k => svc[k] && svc[k].ok);
    const last = d.last_message ? d.last_message.replace('T', ' ').slice(0, 19) : '无数据';
    const tk = d.tokens || {}; const pct = tk.budget ? Math.round(tk.used / tk.budget * 100) : 0;
    const card = (title, main, rows, color) =>
      '<div style="flex:1;min-width:230px;border:1px solid var(--line);border-radius:10px;padding:14px">' +
      '<div style="font-size:12px;color:var(--muted)">' + title + '</div>' +
      '<div style="font-size:26px;font-weight:600;margin:6px 0;color:' + color + '">' + main + '</div>' +
      rows.map(r => '<div style="font-size:12px;color:var(--muted);margin-top:3px">' + r + '</div>').join('') +
      '</div>';
    const svcRows = names.map(k => {
      const s = svc[k] || {};
      return k + '：' + (s.ok ? '正常' : ('异常' + (s.reason ? '｜' + s.reason : '')));
    });
    box.innerHTML =
      '<div style="display:flex;gap:12px;flex-wrap:wrap">' +
      card('活着吗', alive ? '活着' : '有异常', svcRows.concat(['最后消息：' + last]), alive ? '#1a7f37' : '#c33') +
      card('在花钱吗', (tk.used || 0) + ' / ' + (tk.budget || 0), ['今日 ' + pct + '%'], pct >= 100 ? '#c33' : (pct >= 80 ? '#b26a00' : '#1a7f37')) +
      card('卡在哪', (d.dead_letters || 0) + ' 死信',
        ['失败运行 24h：' + (d.failed_runs_24h || 0),
         '待确认依赖：' + (d.pending_dependencies || 0),
         '凭据：' + ((d.credential || {}).message || '无数据')],
        (d.dead_letters ? '#c33' : '#1a7f37')) +
      '</div>' +
      '<h4 style="margin:18px 0 6px;font-size:13px">运行配置（只读；修改请走命令）</h4>' +
      '<table style="font-size:12px;border-collapse:collapse;width:100%">' +
      [['环境', (d.version || {}).env, '固定值'],
       ['数据库', (d.version || {}).db, '⚠ 禁止在控制台切换'],
       ['端口', (d.version || {}).port, '⚠ 改端口需改 launchd'],
       ['商务闸门', (d.version || {}).gate_mode, '⚠ 命令：agency.py finance config --gate-mode'],
       ['圆桌 token 预算', (d.version || {}).roundtable_tokens, '⚠ 改 config.json'],
       ['日 token 预算', (tk.budget || 0), '⚠ 改 .env / config token_budget']]
      .map(r => '<tr><td style="padding:4px 8px;border-top:1px solid var(--line)">' + r[0] +
        '</td><td style="padding:4px 8px;border-top:1px solid var(--line)">' + esc(String(r[1] ?? '无数据')) +
        '</td><td style="padding:4px 8px;border-top:1px solid var(--line);color:var(--muted)">' + r[2] +
        '</td></tr>').join('') + '</table>';
  }
  // ===== 内容日历 =====
  function openContent() { $('#contentOverlay').classList.add('o'); renderContent(); }
  function closeContent() { $('#contentOverlay').classList.remove('o'); }
  async function renderContent() {
    const box = $('#contentBody'); if (!box) return;
    box.innerHTML = '<p style="color:var(--muted)">加载中…</p>';
    let d;
    try { d = (await api('/api/v1/legacy/content')).data || {}; }
    catch (e) { box.innerHTML = '<p class="danger">加载失败：' + esc(e.message) + '</p>'; return; }
    const plans = d.plans || [];
    const published = plans.filter(p => p.status === 'published');
    $('#contentMeta').textContent = (d.month || '') + ' · 计划 ' + plans.length +
      ' · 待发布 ' + (d.pending || []).length + ' · 已发 ' + published.length;
    box.innerHTML =
      '<section class="sec wide"><h3>按渠道汇总 <small>播放/互动</small></h3><div class="hero3">' +
        ((d.summary || []).length ? d.summary.map(s =>
          '<div><b>' + (s.views || 0) + '</b><span>' + esc(s.channel) + ' 播放</span><small>计划 ' +
          s.n + '｜已发 ' + (s.published || 0) + '｜赞 ' + (s.likes || 0) + '｜评 ' +
          (s.comments || 0) + '｜私信 ' + (s.dms || 0) + '</small></div>').join('')
          : '<div><b>0</b><span>暂无计划</span></div>') +
      '</div></section>' +
      bizTable('本月计划（' + (d.month || '') + '）', d.this_month,
        ['id', 'channel', 'planned_at', 'status', 'title']) +
      bizTable('待发布', d.pending, ['id', 'channel', 'planned_at', 'title']) +
      bizTable('已发布数据', published,
        ['id', 'channel', 'published_at', 'title', 'views', 'likes', 'comments', 'dms']) +
      '<section class="sec wide"><h3>新增计划 <small>不自动发布，只登记</small></h3>' +
        '<div class="grid2">' +
          '<label>渠道<select id="ctChannel"><option>B站</option><option>抖音</option></select></label>' +
          '<label>标题<input id="ctTitle" placeholder="本期选题标题"></label>' +
          '<label>计划发布日<input id="ctPlanned" type="date"></label>' +
        '</div><div style="margin-top:8px"><button id="ctAdd">新增计划</button></div>' +
      '</section>' +
      '<section class="sec wide"><h3>数据回填 <small>发布后手工录入</small></h3>' +
        '<div class="grid2">' +
          '<label>计划编号<input id="ctId" placeholder="CP-0001"></label>' +
          '<label>状态<select id="ctStatus"><option value="published">已发布</option><option value="skipped">跳过</option><option value="planned">待发布</option></select></label>' +
          '<label>实际发布日<input id="ctPublished" type="date"></label>' +
          '<label>播放<input id="ctViews" type="number" min="0"></label>' +
          '<label>点赞<input id="ctLikes" type="number" min="0"></label>' +
          '<label>评论<input id="ctComments" type="number" min="0"></label>' +
          '<label>私信<input id="ctDms" type="number" min="0"></label>' +
        '</div><div style="margin-top:8px"><button id="ctUpdate">回填</button></div>' +
      '</section>';
    $('#ctAdd').addEventListener('click', async () => {
      const p = { kind: 'add', channel: $('#ctChannel').value,
                  title: ($('#ctTitle').value || '').trim(),
                  planned: $('#ctPlanned').value || '' };
      try { const r = await api('/api/v1/legacy/content', { method: 'POST', body: JSON.stringify(p) });
        toast(r.output || '已新增'); renderContent(); }
      catch (e) { toast('新增失败：' + e.message, 'err'); }
    });
    $('#ctUpdate').addEventListener('click', async () => {
      const v = id => ($(id).value || '').trim();
      const p = { kind: 'update', id: v('#ctId'), status: $('#ctStatus').value,
                  published_at: v('#ctPublished'), views: v('#ctViews'),
                  likes: v('#ctLikes'), comments: v('#ctComments'), dms: v('#ctDms') };
      try { const r = await api('/api/v1/legacy/content', { method: 'POST', body: JSON.stringify(p) });
        toast(r.output || '已回填'); renderContent(); }
      catch (e) { toast('回填失败：' + e.message, 'err'); }
    });
  }
  async function renderBiz() {
    const box = $('#bizBody'); if (!box) return;
    box.innerHTML = '<p style="color:var(--muted)">加载中…</p>';
    let d;
    try { d = (await api('/api/v1/legacy/finance')).data || {}; }
    catch (e) { box.innerHTML = '<p class="danger">加载失败：' + esc(e.message) + '</p>'; return; }
    const s = d.summary || {};
    $('#bizMeta').textContent = '已开票 ' + bizMoney(s.invoiced) + ' · 已回款 ' + bizMoney(s.paid);
    box.innerHTML =
      '<section class="sec wide"><h3>款项汇总 <small>LIVE · 台账读取</small></h3><div class="hero3">' +
        '<div><b>' + bizMoney(s.invoiced) + '</b><span>已开票</span></div>' +
        '<div><b>' + bizMoney(s.paid) + '</b><span>已回款</span></div>' +
        '<div class="danger"><b>' + bizMoney(s.unpaid) + '</b><span>未回款</span><small>逾期 ' +
          bizMoney(s.overdue) + '（' + (s.overdue_count || 0) + ' 张）</small></div>' +
      '</div></section>' +
      bizTable('客户', d.clients, ['id', 'name', 'contact', 'phone']) +
      bizTable('报价', d.quotes, ['id', 'project_id', 'client_id', 'amount', 'status', 'valid_until']) +
      bizTable('合同', d.contracts, ['id', 'project_id', 'client_id', 'quote_id', 'amount', 'signed_at']) +
      bizTable('发票', d.invoices, ['id', 'contract_id', 'no', 'amount', 'issued_at', 'due_at', 'status']) +
      bizTable('回款', d.payments, ['id', 'invoice_id', 'amount', 'paid_at', 'method']) +
      '<section class="sec wide"><h3>新增单据 <small>金额必须 &gt;0，日期 YYYY-MM-DD</small></h3>' +
        '<div class="grid2">' +
          '<label>类型<select id="bzKind"><option value="client">客户</option><option value="quote">报价</option><option value="contract">合同</option><option value="invoice">发票</option><option value="payment">回款</option></select></label>' +
          '<label>名称/单号<input id="bzName" placeholder="客户名 / 发票号"></label>' +
          '<label>项目<input id="bzProject" value="OPC-002"></label>' +
          '<label>金额<input id="bzAmount" type="number" min="0" step="0.01"></label>' +
          '<label>客户编号<input id="bzClient" placeholder="CL-0001（可选）"></label>' +
          '<label>报价单<input id="bzQuote" placeholder="Q-0001"></label>' +
          '<label>合同<input id="bzContract" placeholder="C-0001"></label>' +
          '<label>发票<input id="bzInvoice" placeholder="INV-0001"></label>' +
          '<label>日期<input id="bzDate" type="date"></label>' +
          '<label>到期日<input id="bzDate2" type="date"></label>' +
        '</div><div style="margin-top:8px"><button id="bzAdd">新增</button></div>' +
      '</section>';
    $('#bzAdd').addEventListener('click', bizAdd);
  }
  async function bizAdd() {
    const kind = $('#bzKind').value;
    const v = (id) => ($(id).value || '').trim();
    const p = { kind, name: v('#bzName'), project: v('#bzProject'),
                amount: v('#bzAmount'), client_id: v('#bzClient'),
                quote_id: v('#bzQuote'), contract_id: v('#bzContract'),
                invoice_id: v('#bzInvoice'), issued_at: v('#bzDate'),
                due_at: v('#bzDate2'), signed_at: v('#bzDate'),
                paid_at: v('#bzDate'), valid_until: v('#bzDate2'), no: v('#bzName') };
    try {
      const r = await api('/api/v1/legacy/finance', { method: 'POST', body: JSON.stringify(p) });
      toast(r.output || '已新增');
      renderBiz();
    } catch (e) { toast('新增失败：' + e.message, 'err'); }
  }

  function cardHTML(t) {
    const lf = leftDays(t);
    const when = isClosed(t) ? (t.closed_at ? '关单 ' + shortDate(t.closed_at) : '')
      : isOver(t) ? t.state : t.due === (S && S.today) ? '今日到期' : lf != null && lf >= 0 ? '剩 ' + lf + ' 天' : '';
    const tags = [];
    if (t.milestone) tags.push('<span class="tg">' + esc(t.milestone) + '</span>');
    if (t.hours) tags.push('<span class="tg">' + t.hours + 'h</span>');
    if (t.dep) tags.push('<span class="tg y">依赖 ' + esc(t.dep) + '</span>');
    if (t.state === '要素不全') tags.push('<span class="tg y">要素不全</span>');
    if (isClosed(t)) tags.push('<span class="tg ' + (t.evidence ? 'g' : 'r') + '">' + (t.evidence ? '证据已留' : '缺证据') + '</span>');
    if (t.last_run) tags.push('<span class="tg a" title="' + esc(t.last_run.role + ' · ' + t.last_run.at) + '">已跑 ' +
      t.last_run.count + ' 次 · ' + esc(evTime(t.last_run.at)) + (t.last_run.ok ? '' : ' · 失败') + '</span>');
    return '<div class="kd st-' + colOf(t) + '" data-id="' + esc(t.id) + '" role="button" tabindex="0">' +
      '<div class="kdTop"><span class="mono">' + esc(t.id) + '</span><span class="pill tiny ' + stateClass(t.state) + '">' + esc(t.state) + '</span></div>' +
      '<b>' + esc(t.title) + '</b>' +
      '<small>' + esc(ownerLabel(t.owner)) + ' · 截止 ' + shortDate(t.due) + (when ? ' · ' + when : '') + '</small>' +
      '<div>' + tags.join('') + '</div></div>';
  }

  function kbMatch(t) {
    if (boardFilter === 'open' && isClosed(t)) return false;
    if (boardFilter === 'closed' && !isClosed(t)) return false;
    if (boardFilter === 'risk' && !isRisk(t)) return false;
    if (boardFilter === 'code' && t.kind !== 'code') return false;
    if (boardFilter === 'mine' && t.owner !== 'longyi') return false;
    const q = boardQuery.trim().toLowerCase();
    if (!q) return true;
    return (t.id + ' ' + t.title + ' ' + t.owner + ' ' + ownerLabel(t.owner) + ' ' + (t.milestone || '')).toLowerCase().includes(q);
  }

  function renderBoardOverlay() {
    if (!$('#boardOverlay').classList.contains('o') || !S) return;
    const k = boardStats();
    const launch = launchDue();
    const daysLeft = daysTo(launch);
    const pct = k.hoursTotal ? Math.round(k.hoursDone / k.hoursTotal * 100) : 0;
    const wipDoing = 5, wipReview = 3;
    $('#boardMeta').textContent = 'OPC-002 · 交付中 · 距上线 ' + (daysLeft == null ? '—' : daysLeft + ' 天') + ' · 完成率 ' + pct + '% · 未关 ' + k.open +
      '（待开始 ' + (k.open - k.doing - k.soon - k.over) + '／进行中 ' + k.doing + '／临期 ' + k.soon + '／逾期 ' + k.over + '）· 今日到期 ' + k.dueToday + ' · 已关闭 ' + k.closed +
      (k.doing > wipDoing ? ' · ⚠ 进行中超限' : '') + (k.soon > wipReview ? ' · ⚠ 临期超限' : '');
    $$('#boardOverlay [data-kf]').forEach(b => b.classList.toggle('on', b.dataset.kf === boardFilter));
    $('#kbView').textContent = boardView === 'st' ? '按负责人' : boardView === 'own' ? '按里程碑' : '按状态';
    const inp = $('#kbSearch');
    if (document.activeElement !== inp) inp.value = boardQuery;
    const filtered = S.tasks.filter(kbMatch);
    const box = $('#kbCols');
    if (boardView === 'st') {
      box.style.display = 'grid';
      box.style.gridTemplateColumns = window.innerWidth <= 760 ? '1fr' : 'repeat(4,1fr)';
      const cols = [['待开始', 'todo'], ['进行中', 'doing'], ['临期 / 逾期', 'risk'], ['最近关闭', 'closed']];
      box.innerHTML = cols.map(([name, c]) => {
        let list = sortTasks(filtered.filter(t => colOf(t) === c));
        const over = c === 'doing' ? k.doing - wipDoing : c === 'risk' ? k.soon - wipReview : 0;
        let extra = '';
        if (c === 'closed' && !closedAll && list.length > 3) {
          extra = '<button class="moreBtn" data-more="1">查看全部 ' + list.length + ' 条</button>';
          list = list.slice(0, 3);
        }
        return '<div class="kc"><h4>' + name +
          (over > 0 ? '<span class="wip">超限 +' + over + '</span>' : '') +
          '<em>' + (c === 'closed' ? filtered.filter(t => colOf(t) === c).length : list.length) + '</em></h4>' +
          (list.map(cardHTML).join('') || '<p style="color:var(--muted);font-size:12px;padding:8px 2px">没有任务</p>') + extra + '</div>';
      }).join('');
    } else if (boardView === 'own') {
      box.style.display = 'block';
      const owners = [];
      S.agents.forEach(a => owners.push(a.name));
      owners.push('longyi');
      S.tasks.forEach(t => { if (!owners.includes(t.owner)) owners.push(t.owner); });
      const seen = new Set();
      box.innerHTML = owners.filter(o => (seen.has(o) ? false : (seen.add(o), true))).map(o => {
        const list = sortTasks(filtered.filter(t => t.owner === o));
        if (!list.length) return '';
        return '<div class="kc" style="margin-bottom:10px;overflow:visible;min-height:0"><h4>' + esc(ownerLabel(o)) + '<em>' + list.length + ' 条 · 未关 ' +
          list.filter(t => !isClosed(t)).length + '</em></h4><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:7px">' +
          list.map(cardHTML).join('') + '</div></div>';
      }).join('') || '<p style="color:var(--dim)">没有匹配的任务</p>';
    } else {
      box.style.display = 'block';
      const groups = {};
      const msKey = (t) => (!t.milestone || t.milestone === '—' || t.milestone === '-') ? '未挂里程碑' : t.milestone;
      filtered.forEach(t => (groups[msKey(t)] ||= []).push(t));
      const keys = Object.keys(groups).sort((a, b) => a.localeCompare(b, 'zh', { numeric: true }));
      box.innerHTML = keys.map(key => {
        const list = sortTasks(groups[key]);
        const openN = list.filter(t => !isClosed(t)).length;
        const closed = list.length - openN;
        const p = Math.round(closed / list.length * 100);
        return '<div class="kc" style="margin-bottom:10px;overflow:visible;min-height:0"><h4>' + esc(key) +
          '<span class="wip" style="color:var(--muted);border-color:var(--ln)">' + closed + '/' + list.length + ' 已关闭</span>' +
          '<em>' + p + '%</em></h4><div class="msBar" style="margin:0 2px 8px"><i style="width:' + p + '%"></i></div>' +
          '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:7px">' + list.map(cardHTML).join('') + '</div></div>';
      }).join('') || '<p style="color:var(--dim)">没有匹配的任务</p>';
    }
  }

  // ===== 经营看板 =====
  function renderOps() {
    if (!$('#opsOverlay').classList.contains('o') || !S) return;
    const k = boardStats(), llm = S.llm || {}, f = S.finance || {}, ev = S.events || {};
    const last = ev.last || {}, gate = (S.project && S.project.gate) || {};
    const gateOk = !!gate.confirmed && !gate.revoked_at;
    const ms = S.milestones || [];
    const runs = S.runs || [];
    const subs = S.subscriptions || [];
    const pct = k.hoursTotal ? Math.round(k.hoursDone / k.hoursTotal * 100) : 0;
    const lastDue = launchDue();
    const daysLeft = daysTo(lastDue);
    const risks = sortTasks(S.tasks.filter(t => !isClosed(t) && (isOver(t) || t.state === '临期' || t.state === '要素不全')));
    const incomplete = S.tasks.filter(t => !isClosed(t) && t.state === '要素不全').length;
    $('#opsMeta').textContent = S.today + ' · ' + S.tasks.length + ' 条任务 · ' + S.agents.length + ' 个角色';
    $('#opsBody').innerHTML =
      '<section class="sec wide"><h3>老板关心的三个数 <small>LIVE · 台账读取</small></h3><div class="hero3">' +
        '<div><b>' + (daysLeft == null ? '—' : daysLeft + ' 天') + '</b><span>距上线</span><small>' +
          (lastDue ? 'M6 上线 ' + lastDue : '台账里还没有上线里程碑') + '</small></div>' +
        '<div><b>' + pct + '%</b><span>工时完成</span><small>' + k.hoursDone + 'h / ' + k.hoursTotal + 'h</small></div>' +
        '<div class="danger"><b>' + risks.length + ' 项</b><span>风险任务</span><small>逾期 ' + k.over + ' · 临期 ' + k.soon + ' · 要素不全 ' + incomplete + '</small></div>' +
      '</div></section>' +
      '<section class="sec wide"><h3>风险 <small>按截止排序 · 点击看详情</small></h3>' +
        (risks.length ? risks.slice(0, 8).map(t =>
          '<div class="riskRow" data-risk="' + esc(t.id) + '">' +
            '<span class="pill tiny ' + stateClass(t.state) + '">' + esc(t.state) + '</span>' +
            '<div><b>' + esc(t.title) + '</b><small>' + esc(t.id) + ' · ' + esc(ownerLabel(t.owner)) + ' · 截止 ' + shortDate(t.due) + '</small></div>' +
            '<span class="mono" style="font-size:11px;color:var(--t2)">' + esc(t.milestone || '—') + '</span></div>').join('')
          : '<p style="color:var(--gn)">没有逾期、临期或要素不全的任务</p>') +
      '</section>' +
      '<section class="sec wide"><h3>今日态势 <small>LIVE · 台账读取</small></h3><div class="tiles">' +
        '<div class="tile"><span>未关闭</span><b>' + k.open + '</b></div>' +
        '<div class="tile"><span>今日到期</span><b>' + k.dueToday + '</b></div>' +
        '<div class="tile' + (k.over ? ' danger' : '') + '"><span>逾期</span><b>' + k.over + '</b></div>' +
        '<div class="tile"><span>工时完成</span><b>' + pct + '%</b></div>' +
        '<div class="tile"><span>今日 token</span><b>' + Number(llm.tokens_today || 0).toLocaleString() + '</b></div>' +
        '<div class="tile"><span>预算上限</span><b>' + Number(llm.token_limit || 0).toLocaleString() + '</b></div>' +
        '<div class="tile"><span>已关闭</span><b>' + k.closed + '</b></div>' +
        '<div class="tile"><span>在岗</span><b>' + S.agents.length + '</b></div>' +
      '</div></section>' +
      '<section class="sec"><h3>里程碑 <small>' + ms.length + ' 组</small></h3>' +
        (ms.length ? ms.map(g => {
          const done = g.tasks.filter(t => t.state === '已关闭').length;
          const p = g.tasks.length ? Math.round(done / g.tasks.length * 100) : 0;
          return '<div class="msRow"><div><b>' + esc(g.key) + '</b><small>' + shortDate(g.start) + ' → ' + shortDate(g.due) + ' · ' + g.hours + 'h</small></div>' +
            '<div class="msBar"><i style="width:' + p + '%"></i></div><span class="pct">' + done + '/' + g.tasks.length + ' · ' + p + '%</span></div>';
        }).join('') : '<p style="color:var(--muted)">还没有挂里程碑的任务</p>') +
      '</section>' +
      '<section class="sec"><h3>财务 <small>只读</small></h3><dl class="kv">' +
        '<dt>报价</dt><dd>' + (f.quotes ? f.quotes.count : 0) + ' 单 · ' + money(f.quotes && f.quotes.amount) + '</dd>' +
        '<dt>合同</dt><dd>' + (f.contracts ? f.contracts.count : 0) + ' 份 · ' + money(f.contracts && f.contracts.amount) + '</dd>' +
        '<dt>已开票</dt><dd>' + money(f.invoices && f.invoices.issued) + '</dd>' +
        '<dt>已回款</dt><dd style="color:var(--gn)">' + money(f.invoices && f.invoices.received) + '</dd>' +
        '<dt>未收</dt><dd style="color:var(--yl)">' + money(f.invoices && f.invoices.outstanding) + '</dd>' +
        '<dt>逾期发票</dt><dd style="color:var(--red)">' + (f.invoices ? f.invoices.overdue_count : 0) + ' 张 · ' + money(f.invoices && f.invoices.overdue) + '</dd>' +
        '<dt>成本</dt><dd>' + money(f.costs) + '</dd>' +
      '</dl></section>' +
      '<section class="sec wide"><h3>事件总线 <small>待投递 ' + Number(ev.pending || 0) + '</small>' +
        '<button data-sync' + (runBusy ? ' disabled' : '') + '>推进一轮</button></h3>' +
        '<p style="color:var(--t2);font-size:12px">上次同步：' + esc(last.at || last.time || '—') +
        (last.ok === undefined ? '' : ' · ' + (last.ok ? '<span style="color:var(--gn)">成功</span>' : '<span style="color:var(--red)">失败</span>')) +
        '　订阅 ' + subs.filter(x => x.enabled).length + '/' + subs.length + ' 生效</p>' +
        (lastSyncOut ? '<pre class="runOut" style="max-height:180px">' + esc(lastSyncOut) + '</pre>' : '') +
      '</section>' +
      '<section class="sec wide"><h3>订阅矩阵 <small>飞书只做映射与提醒</small></h3>' +
        (subs.length ? '<table class="tbl"><thead><tr><th>事件</th><th>目标</th><th>动作</th><th>今日</th><th>状态</th></tr></thead><tbody>' +
          subs.map((s, i) => '<tr><td>' + esc(s.event_type) + '</td><td>' + esc(s.target) + '</td><td>' + esc(s.action) + '</td>' +
            '<td class="mono">' + Number(s.spend_today || 0) + ' / ' + Number(s.budget || 0) + '</td>' +
            '<td><button data-sub="' + i + '" style="padding:1px 9px;font-size:10px">' + (s.enabled ? '已开启' : '已关闭') + '</button></td></tr>').join('') +
          '</tbody></table>' : '<p style="color:var(--muted)">没有订阅记录</p>') +
      '</section>' +
      '<section class="sec wide"><h3>最近调度 <small>agent_runs</small></h3>' +
        (runs.length ? '<table class="tbl"><thead><tr><th>时间</th><th>角色</th><th>任务</th><th>模型</th><th>token</th><th>结果</th><th>产出</th></tr></thead><tbody>' +
          runs.map(r => '<tr><td class="mono">' + esc(String(r.created_at || '').slice(5, 16)) + '</td><td>' + esc(ownerLabel(r.role)) + '</td>' +
            '<td>' + esc(r.task_id || '—') + '</td><td class="mono">' + esc(r.model || '—') + '</td>' +
            '<td class="mono">' + (Number(r.tokens_in || 0) + Number(r.tokens_out || 0)) + '</td>' +
            '<td class="' + (r.ok ? 'ok' : 'bad') + '">' + (r.ok ? '成功' : '失败') + '</td>' +
            '<td class="path" title="' + esc(r.output_path || '') + '">' + (r.ok ? outLink(r.output_path, (r.output_path || '').split('/').pop()) : esc(r.error || '—')) + '</td></tr>').join('') +
          '</tbody></table>' : '<p style="color:var(--muted)">还没有调度记录</p>') +
      '</section>' +
      '<div class="opsFoot">闸门：' + (gateOk ? '已确认' : gate.revoked_at ? '已撤销' : '未确认') +
      ' · 事实源 agency/state/agency.db · 本地 127.0.0.1 · ' + S.today + '</div>';
  }

  async function doSync(btn) {
    btn.disabled = true; btn.textContent = '投递中…';
    try {
      const r = await api('/api/v1/legacy/sync', { method: 'POST', body: '{}' });
      lastSyncOut = (r.output || '').trim() || (r.error || '').trim() || '完成';
      toast(r.ok ? '推进完成' : '推进失败：' + (r.error || ''), r.ok ? 'ok' : 'err');
      await load();
    } catch (err) {
      lastSyncOut = err.message;
      toast('推进失败：' + err.message, 'err');
    } finally {
      renderOps();
    }
  }

  async function toggleSub(i) {
    const s = (S.subscriptions || [])[i];
    if (!s) return;
    try {
      await api('/api/v1/legacy/subscriptions/toggle', {
        method: 'POST',
        body: JSON.stringify({ event_type: s.event_type, target: s.target, action: s.action, enabled: !s.enabled })
      });
      await load();
    } catch (err) { toast('订阅切换失败：' + err.message, 'err'); }
  }
  // ===== 事件流 =====
  const evAbnormal = (x) => /overdue|blocked|conflict|revoked|flag|error|dead/.test(x.type || '');
  const evTime = (v) => {
    if (!v) return '—';
    const d = new Date(v);
    return isNaN(d.getTime()) ? String(v).slice(11, 16)
      : d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' });
  };
  function renderEvents() {
    const list = (S && S.events && S.events.list) || [];
    const tick = $('#evTicker');
    if (!tick) return;
    tick.innerHTML = list.length ? list.slice(0, 3).map((x, i) =>
      '<span style="flex:none' + (i ? ';opacity:.6' : '') + '"><i>' + esc(evTime(x.at)) + '</i> ' +
      (evAbnormal(x) ? '<span class="evx">' + esc(x.task || x.type) + '</span>' : '<b>' + esc(x.task || x.type) + '</b>') +
      ' ' + esc(x.text || x.type) + '</span>').join('')
      : '<span style="color:var(--muted)">暂无事件</span>';
  }
  let evOnlyBad = false;
  function openEvents() {
    if (!S) return;
    const all = (S.events && S.events.list) || [];
    const list = all.filter(x => !evOnlyBad || evAbnormal(x));
    openDrawerShell('EVENTS / 事件流');
    $('#drBody').innerHTML =
      '<h2>事件流 · 最近 20 条</h2><p>直接读本地 events 表，按写入时间倒序；异常类型标红。</p>' +
      '<div class="blk" style="display:flex;gap:6px"><button id="evAll" class="' + (evOnlyBad ? '' : 'on') + '">全部 ' + all.length + '</button>' +
      '<button id="evBad" class="' + (evOnlyBad ? 'on' : '') + '">只看异常 ' + all.filter(evAbnormal).length + '</button></div>' +
      '<div class="blk">' + (list.length ? list.map(x =>
        '<div class="evRow"><time>' + esc(evTime(x.at)) + '</time><b class="' + (evAbnormal(x) ? 'evx' : '') + '">' + esc(x.type) + '</b>' +
        '<div>' + esc(x.text || '—') + '<small>' + esc(x.task || '无任务') + (x.delivered ? ' · 已投递' : ' · 未投递') + '</small></div></div>'
      ).join('') : '<p style="color:var(--muted)">没有事件</p>') + '</div>';
    $('#evAll').addEventListener('click', () => { evOnlyBad = false; openEvents(); });
    $('#evBad').addEventListener('click', () => { evOnlyBad = true; openEvents(); });
  }

  // ===== 抽屉 =====
  function openDrawerShell(tag) {
    $('#drTag').textContent = tag;
    $('#drawer').classList.add('o');
    $('#drawer').setAttribute('aria-hidden', 'false');
  }
  function closeDrawer() {
    $('#drawer').classList.remove('o');
    $('#drawer').setAttribute('aria-hidden', 'true');
  }

  function openTask(id) {
    if (!S) return;
    const t = S.tasks.find(x => x.id === id);
    if (!t) return toast('台账里没有 ' + id, 'err');
    hlTask = id; drawBoard();
    const lf = leftDays(t);
    const closed = isClosed(t);
    const when = isOver(t) ? t.state : t.due === S.today ? '今日到期' : lf != null && lf >= 0 ? '剩 ' + lf + ' 天' : '已过期';
    const row = (k, v) => '<div><dt>' + k + '</dt><dd>' + v + '</dd></div>';
    openDrawerShell('TASK / ' + t.id);
    $('#drBody').innerHTML =
      '<h2>' + esc(t.title) + '</h2>' +
      '<span class="pill ' + (closed ? 'ok' : isOver(t) ? 'off' : 'warn') + '">' + esc(t.state) + '</span> ' +
      '<span class="pill ' + (t.evidence ? 'ok' : 'warn') + '">' + (t.evidence ? '证据已留' : '尚未留证据') + '</span>' +
      '<dl class="meta">' +
        row('负责人', esc(ownerLabel(t.owner))) +
        row('项目', esc(t.project || 'OPC-002')) +
        row('开始', shortDate(t.start)) +
        row('截止', shortDate(t.due) + ' · ' + esc(when)) +
        row('工时', (t.hours || '—') + 'h') +
        row('类型', esc(t.kind || '—')) +
        row('里程碑', esc(t.milestone || '未挂')) +
        row('依赖', esc(t.dep || '无')) +
        row('创建', esc(String(t.created || t.created_at || '—').slice(0, 16))) +
        row('关闭', esc(String(t.closed_at || '—').slice(0, 16))) +
      '</dl>' +
      '<div class="blk"><h3>完成标准</h3><p>' + esc(t.done_when || '—') + '</p></div>' +
      (t.deliverable ? '<div class="blk"><h3>产出物</h3><p>' + esc(t.deliverable) + '</p></div>' : '') +
      (t.evidence ? '<div class="blk"><h3>关闭证据</h3><p>' + esc(t.evidence) + '</p></div>' : '') +
      (closed ? '' :
        '<div class="blk"><h3>关闭任务</h3>' +
          '<label class="field"><span>证据（必填）</span><textarea id="evi" placeholder="产出物路径、命令输出摘要或验收记录"></textarea></label>' +
          '<label class="field"><span>验收命令（必填，退出码 0 才允许关闭；完成标准为 cmd: 开头时可留空）</span><input id="vfy" placeholder="例如 python3 -m unittest tests.test_state"></label>' +
          '<p class="err" id="taskErr"></p>' +
          '<button class="primary" id="closeBtn">关闭任务 · ' + t.id + '</button>' +
          '<details><summary>改期 / 改负责人 / 改里程碑</summary>' +
            '<div class="two"><label class="field"><span>开始</span><input type="date" id="upStart" value="' + esc(t.start || '') + '"></label>' +
            '<label class="field"><span>截止</span><input type="date" id="upDue" value="' + esc(t.due || '') + '"></label></div>' +
            '<label class="field"><span>负责人</span><input id="upOwner" value="' + esc(t.owner || '') + '"></label>' +
            '<label class="field"><span>里程碑</span><input id="upMs" value="' + esc(t.milestone || '') + '"></label>' +
            '<p class="err" id="upErr"></p><button id="upBtn">保存修改</button>' +
          '</details></div>');
    if (closed) return;
    const closeBtn = $('#closeBtn');
    closeBtn.addEventListener('click', async () => {
      const evidence = $('#evi').value.trim();
      if (!evidence) { $('#taskErr').textContent = '没有证据不能关闭任务'; $('#evi').focus(); return; }
      closeBtn.disabled = true; closeBtn.textContent = '写入中…';
      try {
        await api('/api/v1/legacy/tasks/' + t.id + '/close', { method: 'POST', body: JSON.stringify({ evidence, verify: $('#vfy').value.trim() }) });
        toast(t.id + ' 已关闭');
        await load();
        openTask(t.id);
      } catch (err) {
        $('#taskErr').textContent = err.message;
        closeBtn.disabled = false; closeBtn.textContent = '关闭任务 · ' + t.id;
      }
    });
    $('#upBtn').addEventListener('click', async () => {
      const payload = {
        start: $('#upStart').value, due: $('#upDue').value,
        owner: $('#upOwner').value.trim(), milestone: $('#upMs').value.trim()
      };
      const btn = $('#upBtn'); btn.disabled = true;
      try {
        await api('/api/v1/legacy/tasks/' + t.id + '/update', { method: 'POST', body: JSON.stringify(payload) });
        toast(t.id + ' 已更新');
        await load(); openTask(t.id);
      } catch (err) {
        $('#upErr').textContent = err.message; btn.disabled = false;
      }
    });
  }

  function openAgent(role) {
    if (!S) return;
    if (role === 'ceo') {
      const running = (S.llm && S.llm.running) || [];
      openDrawerShell('LAYER / CEO 调度层');
      $('#drBody').innerHTML =
        '<h2>CEO <small style="font-family:var(--mono);font-size:10px;color:var(--muted)">DISPATCH LAYER</small></h2>' +
        '<p>只做展示的调度层：不亲自执行任务。真实调度是 <b>agency.py dispatch</b> 按负责人把任务派给对应 Agent，' +
        '<b>console.py</b> 负责排队执行、记录 agent_runs，跑完再向你汇报。</p>' +
        '<div class="meta">' +
          '<div><dt>在岗 Agent</dt><dd>' + S.agents.length + '</dd></div>' +
          '<div><dt>正在执行</dt><dd>' + (running.length ? running.map(ownerLabel).join('、') : '无') + '</dd></div>' +
          '<div><dt>今日 token</dt><dd>' + Number((S.llm && S.llm.tokens_today) || 0).toLocaleString() + '</dd></div>' +
          '<div><dt>未关闭任务</dt><dd>' + S.tasks.filter(t => !isClosed(t)).length + '</dd></div>' +
        '</div>' +
        '<div class="blk"><h3>说明</h3><p>CEO 不在 agents/ 目录里，不算角色、不接 token；点它只是看这一层怎么运转。</p></div>' +
        '<div class="blk"><button class="primary" id="ceoBoard">打开任务看板</button> <button id="ceoOps">经营看板</button></div>';
      $('#ceoBoard').addEventListener('click', () => { closeDrawer(); openBoard(); });
      $('#ceoOps').addEventListener('click', () => { closeDrawer(); openOps(); });
      return;
    }
    if (role === 'longyi') {
      openDrawerShell('FOUNDER / 龍奕');
      $('#drBody').innerHTML =
        '<h2>龍奕 <small style="font-family:var(--mono);font-size:10px;color:var(--muted)">FOUNDER</small></h2>' +
        '<p>一人公司创始人。台账、闸门、派活与验收的最终确认人。</p>' +
        '<div class="meta"><div><dt>未关闭任务</dt><dd>' + S.tasks.filter(t => t.owner === 'longyi' && !isClosed(t)).length + '</dd></div>' +
        '<div><dt>今日 token</dt><dd>' + Number((S.llm && S.llm.tokens_today) || 0).toLocaleString() + '</dd></div></div>' +
        '<button class="primary" id="agBoard">打开任务看板</button> ' +
        '<button id="agOps">经营看板</button>';
      $('#agBoard').addEventListener('click', () => { closeDrawer(); openBoard(); });
      $('#agOps').addEventListener('click', () => { closeDrawer(); openOps(); });
      return;
    }
    const meta = ROLE_META[role] || { name: role, color: 0x666666 };
    const info = S.agents.find(a => a.name === role) || {};
    const tasks = S.tasks.filter(t => t.owner === role);
    const open = sortTasks(tasks.filter(t => !isClosed(t)));
    const runs = (S.runs || []).filter(r => r.role === role).slice(0, 3);
    openDrawerShell('AGENT / ' + role.toUpperCase());
    $('#drBody').innerHTML =
      '<h2>' + esc(meta.name) + ' <small style="font-family:var(--mono);font-size:10px;color:var(--muted)">' + esc(role) + '</small></h2>' +
      '<p>' + esc(info.desc || info.title || '') + '</p>' +
      '<div class="meta">' +
        '<div><dt>未关闭</dt><dd>' + open.length + '</dd></div>' +
        '<div><dt>已关闭</dt><dd>' + (tasks.length - open.length) + '</dd></div>' +
        '<div><dt>今日 token</dt><dd>' + Number((S.llm && S.llm.tokens_today) || 0).toLocaleString() + '</dd></div>' +
        '<div><dt>状态</dt><dd>' + ((S.llm.running || []).includes(role) ? '执行中' : '待命') + '</dd></div>' +
      '</div>' +
      '<div class="blk tkList"><h3>当前任务</h3>' +
        (open.length ? open.map(t => '<button class="tk" data-id="' + esc(t.id) + '"><b>' + esc(t.title) + '</b><small>' + t.id + '</small><span class="state">' + esc(t.state) + '</span></button>').join('')
          : '<p>手上没有未关闭任务。</p>') +
      '</div>' +
      (runs.length ? '<div class="blk"><h3>最近产出</h3>' + runs.map(r => '<p><b>' + esc(String(r.created_at || '').slice(5, 16)) + '</b> · ' +
        (r.ok ? '成功' : '失败') + ' · ' + (Number(r.tokens_in || 0) + Number(r.tokens_out || 0)) + ' token<br><small>' + (r.ok ? outLink(r.output_path, (r.output_path || '').split('/').pop()) : esc(r.error || '')) + '</small></p>').join('') + '</div>' : '') +
      '<div class="blk"><button class="primary" id="agRun">派活</button> <button id="agCopy">复制命令</button></div>';
    $$('#drBody .tk').forEach(b => b.addEventListener('click', () => openTask(b.dataset.id)));
    $('#agRun').addEventListener('click', () => { closeDrawer(); openRun(role); });
    $('#agCopy').addEventListener('click', async () => {
      const cmd = 'python3 agency.py run ' + role;
      try { await navigator.clipboard.writeText(cmd); toast('已复制：' + cmd); } catch { toast(cmd); }
    });
  }

  function openArchive(tab = 'tasks') {
    openDrawerShell('ARCHIVE / 档案室');
    const facts = (S && S.facts) || [];
    $('#drBody').innerHTML =
      '<h2>档案室</h2><p>台账是唯一事实源；关单必须留证据，事实入库必须标来源等级。</p>' +
      '<div class="blk" style="display:flex;gap:6px">' +
        '<button id="arTabT" class="' + (tab === 'tasks' ? 'on' : '') + '">任务档案</button>' +
        '<button id="arTabK" class="' + (tab === 'facts' ? 'on' : '') + '">知识库 ' + facts.length + '</button>' +
      '</div><div id="arBody"></div>';
    $('#arTabT').addEventListener('click', () => openArchive('tasks'));
    $('#arTabK').addEventListener('click', () => openArchive('facts'));
    if (tab === 'tasks') {
      $('#arBody').innerHTML =
        '<label class="field"><span>检索</span><input id="arQ" placeholder="编号 / 标题 / 负责人" autocomplete="off"></label>' +
        '<div class="blk tkList" id="arList"></div>';
      const render = (q) => {
        const qq = q.trim().toLowerCase();
        const list = sortTasks((S ? S.tasks : []).filter(t => !qq ||
          (t.id + ' ' + t.title + ' ' + t.owner + ' ' + ownerLabel(t.owner)).toLowerCase().includes(qq)));
        $('#arList').innerHTML = list.map(t => '<button class="tk" data-id="' + esc(t.id) + '">' +
          '<b>' + esc(t.title) + '</b><small>' + t.id + ' · ' + esc(ownerLabel(t.owner)) + '</small>' +
          '<span class="state" style="color:' + (t.evidence ? 'var(--gn)' : 'var(--yl)') + '">' + (isClosed(t) ? (t.evidence ? '证据已留' : '缺证据') : t.state) + '</span></button>').join('')
          || '<p>没有匹配记录。</p>';
        $$('#arList .tk').forEach(b => b.addEventListener('click', () => openTask(b.dataset.id)));
      };
      $('#arQ').addEventListener('input', e => render(e.target.value));
      render('');
      return;
    }
    let lv = 'all';
    const paint = () => {
      const list = facts.filter(f => lv === 'all' || f.level === lv);
      $('#arBody').innerHTML =
        '<div class="blk" style="display:flex;gap:6px;flex-wrap:wrap">' +
          ['all', 'S1', 'S2', 'S3', 'S4'].map(k => '<button data-lv="' + k + '" class="' + (lv === k ? 'on' : '') + '">' +
            (k === 'all' ? '全部 ' + facts.length : k + ' ' + facts.filter(f => f.level === k).length) + '</button>').join('') +
        '</div><div class="blk">' +
        (list.length ? list.map(f =>
          '<div style="padding:7px 0;border-top:1px solid rgba(255,255,255,.07)">' +
            '<span class="pill tiny ' + (f.level === 'S4' ? 'warn' : 'ok') + '">' + esc(f.level || '未标级') + '</span> ' +
            '<b style="font-size:12px">' + esc(f.content || '') + '</b>' +
            '<small style="display:block;color:var(--muted);font-size:10px;margin-top:2px">' +
              esc(f.ref || '无来源') + ' · ' + esc(String(f.at || '').slice(0, 10)) +
              (f.state && f.state !== 'active' ? ' · ' + esc(f.state) : '') + '</small></div>').join('')
          : '<p style="color:var(--muted)">暂无已入库知识。facts 表新增条目后会自动出现在这里，不放假数据。</p>') + '</div>';
      $$('#arBody [data-lv]').forEach(b => b.addEventListener('click', () => { lv = b.dataset.lv; paint(); }));
    };
    paint();
  }

  function openConstitution() {
    const gate = (S && S.project && S.project.gate) || {};
    const gateOk = !!gate.confirmed && !gate.revoked_at;
    openDrawerShell('VAULT / 机要室');
    $('#drBody').innerHTML =
      '<h2>工程宪法 v1.0</h2>' +
      '<p>闸门状态：<b style="color:' + (gateOk ? 'var(--gn)' : 'var(--yl)') + '">' +
      (gate.revoked_at ? '已撤销' : gate.confirmed ? '已确认' : '未确认') + '</b>' +
      (gate.by ? ' · 确认人 ' + esc(gate.by) : '') + '</p>' +
      '<div class="blk"><h3>第一条 · 不沟通清楚，不开工写代码</h3><p>写正式代码前必须有《开工确认单》，七项齐全：目标、范围、验收标准、不做什么、技术边界、时间、确认人。缺一项不写代码，只允许调研、画图、≤1 天可丢弃原型。口头“先做着”不算确认。</p></div>' +
      '<div class="blk"><h3>第二条 · 可迁移</h3><p>不硬编码绝对路径、账号、密钥；状态用纯文本落盘；单文件能跑优先；一键导出导入；密钥只走环境变量。</p></div>' +
      '<div class="blk"><h3>第三条 · 不臆造</h3><p>不伪造接口、数据、日志、测试结果；没跑过的测试不许写“通过”；推断标 [假设·S4]；外部数据注明来源；新信息立即落盘，未落盘视为未获取。</p></div>' +
      '<div class="blk"><h3>第四条 · 闸门</h3><p>需求沟通 → 开工确认单 → gate confirm → 编码 → 自测 → 审计 → 验收。闸门未通过，不得创建 code 任务；需求变更必须 gate revoke 后重新确认。</p></div>' +
      '<div class="blk"><h3>第五条 · 例外</h3><p>生产事故、安全漏洞可先止血，但必须在 24 小时内补齐确认单，并在事件日志中记录原因。</p></div>' +
      '<div class="blk"><h3>第六条 · 违规处理</h3><p>未确认先写：代码不计交付，回退重做。伪造测试：全部重跑。硬编码密钥：立即轮换并回退。变更未重确认：暂停合并。</p></div>' +
      '<div class="blk"><h3>第七条 · 适用</h3><p>适用于创始人、所有 Agent、所有外援与外包，没有例外身份。</p></div>' +
      '<div class="blk"><button id="cstGate">查看闸门详情</button></div>';
    $('#cstGate').addEventListener('click', openEnv);
  }

  function openEnv() {
    if (!S) return;
    const llm = S.llm || {}, ev = S.events || {}, last = ev.last || {};
    openDrawerShell('ENV / 保密室');
    $('#drBody').innerHTML =
      '<h2>运行环境</h2><p>本地 127.0.0.1，数据不出本机。这里只显示运行参数，不显示任何密钥。</p>' +
      '<dl class="kv" style="grid-template-columns:110px 1fr;gap:7px 12px;margin-top:10px">' +
        '<dt>模型</dt><dd>' + esc(llm.model || '—') + '</dd>' +
        '<dt>今日 token</dt><dd>' + Number(llm.tokens_today || 0).toLocaleString() + ' / ' + Number(llm.token_limit || 0).toLocaleString() + '</dd>' +
        '<dt>执行中</dt><dd>' + ((llm.running || []).length ? esc((llm.running || []).join('、')) : '无') + '</dd>' +
        '<dt>Agent 数量</dt><dd>' + S.agents.length + '</dd>' +
        '<dt>待投递事件</dt><dd>' + Number(ev.pending || 0) + '</dd>' +
        '<dt>上次同步</dt><dd>' + esc(last.at || last.time || '—') + '</dd>' +
        '<dt>事实源</dt><dd>agency/state/agency.db</dd>' +
        '<dt>服务</dt><dd>python3 console.py · 本地回环</dd>' +
        '<dt>闸门</dt><dd>' + (S.project && S.project.gate && S.project.gate.confirmed ? '已确认' : '未确认') + '</dd>' +
      '</dl>' +
      '<div class="blk"><h3>说明</h3><p>飞书只做映射与提醒，台账仍在本地。密钥只从环境变量读取，不进仓库、不进导出包。</p></div>' +
      '<div class="blk"><button id="envSync">推进一轮事件总线</button> <button id="envRefresh">刷新状态</button></div>';
    $('#envSync').addEventListener('click', () => { closeDrawer(); openOps(); });
    $('#envRefresh').addEventListener('click', async () => { await load(); openEnv(); toast('已刷新'); });
  }
  // ===== 派活：后台执行 + 汇报卡片 =====
  const JOB_KEY = 'fde.jobs.seen';
  let jobSeen = new Set();
  try { jobSeen = new Set(JSON.parse(localStorage.getItem(JOB_KEY) || '[]')); } catch { jobSeen = new Set(); }
  let jobCards = [];
  const saveJobSeen = () => { try { localStorage.setItem(JOB_KEY, JSON.stringify([...jobSeen].slice(-200))); } catch { /* 隐私模式忽略 */ } };
  let diagResult = null;   // 最近一次意图识别结果
  let runStartChecked = new Set();   // 确认执行时勾选的 role 集合

  function fillRunTasks() {
    if (!S) return;
    const open = sortTasks(S.tasks.filter(t => !isClosed(t)));
    const opt = t => '<option value="' + esc(t.id) + '">' + esc(t.id + ' · ' + t.title) +
      (t.last_run ? ' · 已跑 ' + t.last_run.count + ' 次' : '') + '</option>';
    $('#runTask').innerHTML = '<option value="">不绑定任务（自由任务）</option>' +
      (open.length ? open.map(opt).join('') : '');
    $('#runErr').textContent = '';
  }

  function openRun(role) {
    if (!S) return;
    $('#runInstruction').value = '';
    $('#runScrim').hidden = false;
    $('#runDiagOut').hidden = true;
    $('#runAct').hidden = true;
    $('#runStart').hidden = true;
    $('#runErr').textContent = '';
    $('#runDiag').disabled = false; $('#runDiag').textContent = '意图识别';
    fillRunTasks();
    runStartChecked = new Set();
    diagResult = null;
    $('#runTokens').textContent = Number((S.llm && S.llm.tokens_today) || 0).toLocaleString() +
      ' / ' + Number((S.llm && S.llm.token_limit) || 0).toLocaleString();
    setTimeout(() => $('#runInstruction').focus(), 80);
  }

  function closeRun() { $('#runScrim').hidden = true; }

  // 渲染意图识别结果
  function renderDiag(data) {
    diagResult = data;
    $('#runDiagOut').hidden = false;
    const intentLabel = {
      agent_task: '需要 agent 执行', manual: '手动指派',
      create_task: '新建任务', close_task: '关闭任务',
      view: '查看/查询', sync: '同步投递', report: '统计报表',
    }[data.intent] || data.intent;
    $('#diagIntent').textContent = intentLabel;
    const pill = $('#diagNeedAgent');
    pill.textContent = data.needs_agent ? 'AGENT' : 'SYSTEM';
    pill.className = 'pill tiny ' + (data.needs_agent ? 'ok' : 'off');
    $('#diagConf').textContent = (data.confidence || 0).toFixed(2);
    $('#diagWhy').textContent = data.why || '';
    $('#diagNote').textContent = data.note || '';

    const box = $('#diagRoles');
    if (!data.needs_agent || !data.suggested_roles || !data.suggested_roles.length) {
      box.innerHTML = '<p style="color:var(--muted);font-size:12px">未命中 agent 职责域。下方手动勾选要派的角色，或直接取消用看板操作。</p>';
      $('#runStart').hidden = true;
      $('#runAct').hidden = true;
      return;
    }
    // 全量 agent 列表（供手动补充）
    const all = S.agents.map(a => ({ role: a.name, label: ownerLabel(a.name), score: 0, evidence: [], bundle: '' }));
    // 建议 roles 打勾；未在建议里但在 agents 目录的也列出（手动选）
    const suggestedSet = new Set(data.suggested_roles.map(r => r.role));
    const merged = [
      ...data.suggested_roles.map(r => ({ ...r, suggested: true })),
      ...all.filter(a => !suggestedSet.has(a.role)).map(a => ({ ...a, suggested: false, evidence: ['手动'] })),
    ];
    box.innerHTML = merged.map(r => {
      const chk = r.suggested ? 'checked' : '';
      const sc = r.score ? '<span class="sc">· ' + r.score + '</span>' : '';
      const ev = r.evidence && r.evidence.length ? '<span class="ev">' + esc(r.evidence.slice(0, 4).join('、')) + '</span>' : '';
      const bundle = r.bundle ? '<span class="pill tiny warn" style="margin-left:6px">' + esc(r.bundle) + '</span>' : '';
      return '<div class="diagRole">' +
        '<input type="checkbox" id="chk_' + r.role + '" value="' + esc(r.role) + '" data-suggested="' + (r.suggested ? '1' : '0') + '" ' + chk + '>' +
        '<label for="chk_' + r.role + '">' + esc(r.label) + bundle + '</label>' +
        '<small>role/' + esc(r.role) + sc + ev + '</small>' +
        '</div>';
    }).join('');

    // 监听勾选变化 → 控制 "转入后台执行" 按钮
    const updateStartBtn = () => {
      runStartChecked = new Set($$('#diagRoles input[type=checkbox]:checked').map(c => c.value));
      const hasAny = runStartChecked.size > 0;
      $('#runStart').hidden = !hasAny;
      $('#runStart').textContent = hasAny ? '转入后台执行（' + runStartChecked.size + ' 个 agent）' : '转入后台执行';
    };
    $('#diagRoles').addEventListener('change', updateStartBtn);
    updateStartBtn();
    $('#runAct').hidden = false;
  }

  async function runDiagnose() {
    const instruction = $('#runInstruction').value.trim();
    const task = $('#runTask').value;
    $('#runErr').textContent = '';
    if (!instruction) { $('#runErr').textContent = '请先输入指令'; return; }
    $('#runDiag').disabled = true; $('#runDiag').textContent = '识别中…';
    try {
      const r = await api('/api/v1/legacy/dispatch/diagnose', {
        method: 'POST', body: JSON.stringify({ instruction, task })
      });
      if (!r.ok) throw new Error(r.error || '识别失败');
      renderDiag(r.data);
    } catch (err) {
      $('#runErr').textContent = err.message;
    } finally {
      $('#runDiag').disabled = false; $('#runDiag').textContent = '意图识别';
    }
  }

  async function startRun() {
    if (runBusy || !S) return;
    if (!runStartChecked.size) { $('#runErr').textContent = '至少选一个 agent'; return; }
    const instruction = $('#runInstruction').value.trim();
    const task = $('#runTask').value;
    runBusy = true;
    $('#runStart').disabled = true; $('#runStart').textContent = '提交中…';
    $('#runErr').textContent = '';
    const dispatched = [];
    for (const role of runStartChecked) {
      try {
        const r = await api('/api/v1/legacy/agents/' + encodeURIComponent(role) + '/run', {
          method: 'POST', body: JSON.stringify({ task, note: instruction })
        });
        const job = r.data || {};
        dispatched.push({ role, job });
      } catch (err) {
        $('#runErr').textContent = ($('#runErr').textContent ? $('#runErr').textContent + '；' : '') + role + ': ' + err.message;
      }
    }
    $('#runScrim').hidden = true;
    if (dispatched.length) {
      toast('已转后台 ' + dispatched.length + ' 个 agent：' + dispatched.map(d => ownerLabel(d.role)).join('、') + '，跑完给你汇报');
      await load();
      pollJobs();
    }
    runBusy = false;
  }

  async function pollJobs() {
    let list;
    try { list = (await api('/api/v1/legacy/jobs')).data || []; } catch { return; }
    if (!P._lastJobByRole) P._lastJobByRole = {};
    let fresh = 0, changed = 0;
    list.forEach(j => {
      if (j.state === 'running' || j.state === 'retrying') {
        P._lastJobByRole[j.role] = j;
        const i = jobCards.findIndex(x => x.id === j.id);
        if (i >= 0) { jobCards[i] = j; changed++; }
        else if (j.state === 'retrying') { jobCards.unshift(j); changed++; }
        return;
      }
      if (jobSeen.has(j.id)) return;
      jobSeen.add(j.id); saveJobSeen();
      jobCards.unshift(j); fresh++;
      // 记住：给 syncRunning 结束时的 deliverBehavior 用
      P._lastJobByRole[j.role] = j;
    });
    if (!fresh && changed) { renderReports(); return; }
    if (!fresh) return;
    renderReports();
    toast(fresh === 1 ? '有 1 个后台任务跑完了' : '有 ' + fresh + ' 个后台任务跑完了');
    load();
  }

  // 产出文件 → 可直接在页面打开的链接（服务端只放行 agency/outputs 内文件）
  function outLink(p, text) {
    if (!p) return '';
    return '<a href="/api/v1/legacy/output?path=' + encodeURIComponent(p) + '" target="_blank">' +
      esc(text || p) + '</a>';
  }

  function renderReports() {
    const box = $('#reports');
    if (!box) return;
    box.innerHTML = jobCards.map(j => {
      const st = j.state || 'done';
      const ok = st === 'done' && j.ok !== false;
      const label = st === 'retrying' ? '排队重试 ' + (j.retry_in || 0) + 's'
                  : st === 'running' ? '执行中' : ok ? '完成' : '失败';
      return '<div class="rep' + (ok ? '' : ' fail') + '">' +
        '<div class="repH"><span class="mono">' + esc(j.id) + ' · 后台执行</span>' +
          '<span class="pill tiny ' + (ok ? 'ok' : 'off') + '">' + label + '</span></div>' +
        '<b>' + esc(ownerLabel(j.role)) + ' · ' + esc(j.task || '自由任务') + '</b>' +
        '<small>' + esc(j.elapsed || 0) + 's · ' + Number(j.tokens || 0).toLocaleString() + ' token</small>' +
        '<p>' + (j.path
          ? '<a href="/api/v1/legacy/output?path=' + encodeURIComponent(j.path) + '" target="_blank">打开产出</a> <span class="mono">' + esc(j.path) + '</span>'
          : esc(j.error || '无产出文件')) + '</p>' +
        '<div class="repAct"><button data-rep="' + esc(j.id) + '">看汇报</button>' +
          '<button class="ghost" data-rep-x="' + esc(j.id) + '">知道了</button></div></div>';
    }).join('');
  }

  function openReport(id) {
    const j = jobCards.find(x => x.id === id);
    if (!j) return;
    const ok = j.state === 'done' && j.ok !== false;
    closeDrawer();
    openDrawerShell('JOB / ' + j.id);
    $('#drBody').innerHTML =
      '<h2>' + esc(ownerLabel(j.role)) +
        (j.state === 'retrying' ? ' 排队重试（第 ' + esc(j.attempt || 1) + '/' + esc(j.retry_total || 1) + ' 次，约 ' + esc(j.retry_in || 0) + 's 后）'
          : ok ? ' 执行完成' : ' 执行失败') + '</h2>' +
      '<p>' + esc(j.task || '自由任务') + (j.warning ? ' · ' + esc(j.warning) : '') + '</p>' +
      '<div class="meta">' +
        '<div><dt>耗时</dt><dd>' + esc(j.elapsed || 0) + 's</dd></div>' +
        '<div><dt>token</dt><dd>' + Number(j.tokens || 0).toLocaleString() + '</dd></div>' +
        '<div><dt>模型</dt><dd>' + esc(j.model || '—') + '</dd></div>' +
        '<div><dt>产出文件</dt><dd>' + (j.path
          ? '<a href="/api/v1/legacy/output?path=' + encodeURIComponent(j.path) + '" target="_blank">' + esc(j.path) + '</a>'
          : '—') + '</dd></div>' +
      '</div>' +
      '<div class="blk"><h3>输出</h3><p>' + esc((j.output || '').trim() || j.error || '(无输出)') + '</p></div>' +
      '<div class="blk"><button id="repClose">知道了</button></div>';
    $('#repClose').addEventListener('click', () => {
      jobCards = jobCards.filter(x => x.id !== id);
      renderReports(); closeDrawer();
    });
  }

  // ===== 新建任务 =====
  function openNew() {
    const f = $('#newForm');
    f.reset();
    const today = (S && S.today) || new Date().toISOString().slice(0, 10);
    f.elements.start.value = today;
    f.elements.due.value = today;
    f.elements.owner.value = 'longyi';
    $('#newErr').textContent = '';
    $('#newScrim').hidden = false;
    setTimeout(() => f.elements.title.focus(), 60);
  }
  function closeNew() { $('#newScrim').hidden = true; }
  async function submitNew(e) {
    e.preventDefault();
    const el = e.target.elements;
    const payload = {
      title: el.title.value.trim(), project: el.project.value.trim(), owner: el.owner.value.trim(),
      start: el.start.value, due: el.due.value, kind: el.kind.value, hours: el.hours.value,
      milestone: el.milestone.value.trim(), done_when: el.done_when.value.trim(), deliverable: el.deliverable.value.trim()
    };
    const btn = $('#newSubmit');
    btn.disabled = true; btn.textContent = '写入中…';
    try {
      const r = await api('/api/v1/legacy/tasks', { method: 'POST', body: JSON.stringify(payload) });
      toast((r.id || '任务') + ' 已写入台账');
      closeNew();
      await load();
      openBoard();
    } catch (err) {
      $('#newErr').textContent = err.message;
    } finally {
      btn.disabled = false; btn.textContent = '写入台账';
    }
  }

  function isTyping(el) {
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT');
  }

  function bindUI() {
    $('#bBoard').addEventListener('click', openBoard);
    $('#bNew').addEventListener('click', openNew);
    $('#bRun').addEventListener('click', () => openRun());
    $('#bOps').addEventListener('click', openOps);
    $('#bRefresh').addEventListener('click', () => load(true));
    $('#evOpen').addEventListener('click', openEvents);
    $('#evTicker').addEventListener('click', openEvents);
    $('#evTicker').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') openEvents(); });
    $$('#tabs button').forEach(b => b.addEventListener('click', () => view(b.dataset.t === 'b' ? 's' : 'o')));
    $$('#cam button').forEach(b => b.addEventListener('click', () => view(b.dataset.v)));
    $$('#rooms button').forEach(b => b.addEventListener('click', () => {
      const r = b.dataset.r;
      if (r === 'archive') openArchive();
      else if (r === 'constitution') openConstitution();
      else if (r === 'env') openEnv();
      else if (r === 'ops') openOps();
      else if (r === 'biz') openBiz();
      else if (r === 'content') openContent();
      else if (r === 'roundtable') openRoundtable();
      else if (r === 'board') openHealth();
      else if (r === 'run') openRun();
    }));
    $('#drClose').addEventListener('click', closeDrawer);
    $('#kbClose').addEventListener('click', closeBoard);
    $('#kbNew').addEventListener('click', openNew);
    $('#kbView').addEventListener('click', () => {
      boardView = boardView === 'st' ? 'own' : boardView === 'own' ? 'ms' : 'st';
      renderBoardOverlay();
    });
    $$('#boardOverlay [data-kf]').forEach(b => b.addEventListener('click', () => { boardFilter = b.dataset.kf; renderBoardOverlay(); }));
    $('#kbSearch').addEventListener('input', e => { boardQuery = e.target.value; renderBoardOverlay(); });
    $('#kbCols').addEventListener('click', e => {
      const more = e.target.closest('[data-more]');
      if (more) { closedAll = true; renderBoardOverlay(); return; }
      const card = e.target.closest('.kd');
      if (card) openTask(card.dataset.id);
    });
    $('#kbCols').addEventListener('keydown', e => {
      if (e.key !== 'Enter') return;
      const card = e.target.closest('.kd');
      if (card) openTask(card.dataset.id);
    });
    $('#opsClose').addEventListener('click', closeOps);
    $('#bizClose').addEventListener('click', closeBiz);
    $('#bizRefresh').addEventListener('click', renderBiz);
    $('#contentClose').addEventListener('click', closeContent);
    $('#contentRefresh').addEventListener('click', renderContent);
    $('#rtClose').addEventListener('click', closeRoundtable);
    $('#rtRun').addEventListener('click', runRoundtable);
    $('#healthClose').addEventListener('click', closeHealth);
    $('#healthRefresh').addEventListener('click', renderHealth);
    $('#rtList').addEventListener('click', e => {
      const b = e.target.closest('[data-rt-file]');
      if (b) openRoundtableFile(b.dataset.rtFile);
    });
    $('#opsRefresh').addEventListener('click', () => load(true));
    $('#opsBody').addEventListener('click', e => {
      const risk = e.target.closest('[data-risk]');
      if (risk) { closeOps(); openTask(risk.dataset.risk); return; }
      const sync = e.target.closest('[data-sync]');
      if (sync) { doSync(sync); return; }
      const sub = e.target.closest('[data-sub]');
      if (sub) toggleSub(Number(sub.dataset.sub));
    });
    $('#newForm').addEventListener('submit', submitNew);
    $('#newCancel').addEventListener('click', closeNew);
    $('#newScrim').addEventListener('click', e => { if (e.target.id === 'newScrim') closeNew(); });
    $('#runX').addEventListener('click', closeRun);
    $('#runCancel').addEventListener('click', closeRun);
    $('#runStart').addEventListener('click', startRun);
    $('#runDiag').addEventListener('click', runDiagnose);
    $('#runInstruction').addEventListener('keydown', e => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runDiagnose(); }
    });
    $('#runScrim').addEventListener('click', e => { if (e.target.id === 'runScrim') closeRun(); });
    $('#reports').addEventListener('click', e => {
      const view = e.target.closest('[data-rep]');
      if (view) { openReport(view.dataset.rep); return; }
      const x = e.target.closest('[data-rep-x]');
      if (x) {
        jobCards = jobCards.filter(j => j.id !== x.dataset.repX);
        renderReports();
      }
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        if (!$('#runScrim').hidden) return closeRun();
        if (!$('#newScrim').hidden) return closeNew();
        if ($('#drawer').classList.contains('o')) return closeDrawer();
        if ($('#rtOverlay').classList.contains('o')) return closeRoundtable();
        if ($('#boardOverlay').classList.contains('o')) return closeBoard();
        if ($('#healthOverlay').classList.contains('o')) return closeHealth();
        if ($('#contentOverlay').classList.contains('o')) return closeContent();
        if ($('#bizOverlay').classList.contains('o')) return closeBiz();
        if ($('#opsOverlay').classList.contains('o')) return closeOps();
        return;
      }
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); openBoard(); setTimeout(() => $('#kbSearch').focus(), 80); }
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); openNew(); }
    });
    window.addEventListener('resize', () => {
      if (renderer && camera) {
        applyFit();
        renderer.setSize(window.innerWidth, window.innerHeight);
      }
      if ($('#boardOverlay').classList.contains('o')) renderBoardOverlay();
    });
  }

  function boot() {
    bindUI();
    if (THREE_OK) {
      try {
        initScene();
        spawnAgents();
        initCamera();
        bindScene();
        setRoom('o');
        tick(performance.now());
      } catch (err) {
        console.error(err);
        THREE_OK = false;
        const b = $('#banner');
        b.textContent = '3D 场景加载失败：' + err.message + '　面板功能仍可使用';
        b.classList.add('on');
        $('#c').style.display = 'none';
      }
    } else {
      const b = $('#banner');
      b.textContent = '3D 模块未加载（需要访问 CDN）　面板功能仍可使用';
      b.classList.add('on');
      $('#c').style.display = 'none';
    }
    load();
    setInterval(() => {
      const d = new Date();
      $('#clock').textContent = d.toLocaleTimeString('zh-CN', { hour12: false });
    }, 1000);
    setInterval(() => {
      if (document.visibilityState === 'visible' && !runBusy) load();
    }, 60000);
    setInterval(() => {
      if (document.visibilityState === 'visible') pollJobs();
    }, 5000);
    pollJobs();
  }

  boot();
})();
