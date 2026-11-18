if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/app/sw.js", {scope: "/app/"}).catch(() => {});
  });
}
const API="/api/platform/v1",$=s=>document.querySelector(s),S={features:{},view:"overview",session:null,task:null,approval:null};
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const json=v=>esc(JSON.stringify(v??{},null,2));
function csrf(){const c=document.cookie.split("; ").find(x=>x.startsWith("__Host-platform_csrf="));return c?decodeURIComponent(c.split("=").slice(1).join("=")):"";}
// 后端错误详情统一转成可读中文；未命中词典时保留原文，方便排查。
const ERROR_TEXT={"RAG provider is unavailable":"本地模型服务不可用，请确认 Ollama 已安装 qwen3 与 bge-m3","RAG evaluation provider is unavailable":"RAG 评测所需的本地模型服务不可用","JSON object required":"请求体必须是 JSON 对象","authenticated knowledge principal required":"请先登录后再使用知识库","RAG query is not authorized":"当前身份无权执行 RAG 检索","RAG evaluation is not authorized":"当前身份无权执行 RAG 评测","embedding_model is not an enabled model":"所选向量模型未启用","collection not found":"知识库集合不存在","document contains no text after cleaning":"文档解析后没有可用文本","document exceeds configured upload size limit":"文档超过上传大小限制"};
function localizeError(message){const text=String(message??"").trim();for(const key of Object.keys(ERROR_TEXT)){if(text.includes(key))return ERROR_TEXT[key]}return text||"请求失败"}
async function api(path,opt={}){const h={Accept:"application/json",...(opt.headers||{})};if(opt.body&&typeof opt.body==="string")h["Content-Type"]="application/json";if(["POST","PUT","PATCH","DELETE"].includes((opt.method||"GET").toUpperCase()))h["X-CSRF-Token"]=csrf();const r=await fetch(path,{...opt,headers:h,credentials:"same-origin"});if(!r.ok){let m=r.status+" "+r.statusText;try{m=(await r.json()).detail||m}catch{}throw Error(localizeError(m))}if(r.status===204)return null;const t=r.headers.get("content-type")||"";return t.includes("application/json")?r.json():r;}
async function upload(path,file){const r=await fetch(path,{method:"POST",body:file,headers:{"Content-Type":file.type||"application/octet-stream","X-Filename":encodeURIComponent(file.name),"X-CSRF-Token":csrf()},credentials:"same-origin"});if(!r.ok){let m=r.statusText;try{m=(await r.json()).detail||m}catch{}throw Error(localizeError(m))}return r.json();}
function toast(t){const e=$("#toast");e.textContent=t;e.classList.add("show");setTimeout(()=>e.classList.remove("show"),2600)}
function table(cols,rows){return "<div class='table-scroll'><table><thead><tr>"+cols.map(x=>"<th>"+x+"</th>").join("")+"</tr></thead><tbody>"+(rows.length?rows.join(""):"<tr><td colspan='"+cols.length+"' class='empty'>暂无记录</td></tr>")+"</tbody></table></div>"}
function panel(t,b,a=""){return "<section class='panel'><div class='panel-head'><h3>"+t+"</h3>"+a+"</div>"+b+"</section>"}
function pre(v){return "<pre class='json'>"+json(v)+"</pre>"}
function btn(t,attr,cls=""){return "<button class='button "+cls+"' "+attr+">"+t+"</button>"}
const shortId=id=>id?String(id).slice(0,8):"—";
// 统一的中文状态字典：后端仍返回英文枚举，界面只做展示层翻译。
const STATUS_LABELS={queued:"排队中",pending:"待处理",waiting:"等待中",running:"运行中",active:"运行中",succeeded:"已完成",success:"已完成",completed:"已完成",complete:"已完成",failed:"失败",failure:"失败",error:"失败",cancelled:"已取消",canceled:"已取消",partial_failure:"部分失败",paused:"已暂停",draft:"草稿",published:"已发布",disabled:"已停用",deprecated:"已废弃",retired:"已退役",open:"待处理",acknowledged:"已确认",resolved:"已解决",closed:"已关闭",expired:"已过期",abstained:"无依据",approved:"已批准",rejected:"已拒绝",created:"已创建",unknown:"未知"};
const STATUS_TONES={running:"ok",active:"ok",succeeded:"ok",success:"ok",completed:"ok",complete:"ok",approved:"ok",published:"ok",resolved:"ok",queued:"warn",pending:"warn",waiting:"warn",paused:"warn",open:"warn",acknowledged:"warn",partial_failure:"bad",failed:"bad",failure:"bad",error:"bad",cancelled:"bad",canceled:"bad",rejected:"bad",draft:"muted",disabled:"muted",deprecated:"muted",retired:"muted",closed:"muted",expired:"muted"};
function statusKey(value){return String(value??"").trim().toLowerCase()}
function statusLabel(value){const key=statusKey(value);return STATUS_LABELS[key]||(key?String(value):"—")}
function statusPill(value){const tone=STATUS_TONES[statusKey(value)];return"<span class='pill"+(tone?" tone-"+tone:"")+"'>"+esc(statusLabel(value))+"</span>"}
const SEVERITY_LABELS={debug:"调试",info:"提示",notice:"通知",warning:"警告",warn:"警告",error:"错误",critical:"严重",fatal:"致命"};
function severityLabel(value){const key=statusKey(value);return SEVERITY_LABELS[key]||(key?String(value):"—")}
const RISK_LABELS={low:"低",medium:"中",high:"高",critical:"极高"};
function riskLabel(value){const key=statusKey(value);return RISK_LABELS[key]||(key?String(value):"—")}
const OUTCOME_LABELS={success:"成功",succeeded:"成功",failure:"失败",failed:"失败",error:"错误",denied:"已拒绝",allowed:"已允许",cancelled:"已取消"};
function outcomeLabel(value){const key=statusKey(value);return OUTCOME_LABELS[key]||(key?String(value):"—")}
const SCOPE_LABELS={tenant:"租户",project:"项目",private:"私有",run:"运行",task:"任务",user:"用户",session:"会话"};
function scopeLabel(value){const key=statusKey(value);return SCOPE_LABELS[key]||(key?String(value):"—")}
// Agent 展示名优先取注册表定义中的中文名称，缺失时回退到稳定的 Agent ID。
function agentName(id){if(!id)return"—";const labels=S.agentLabels||{};return labels[id]||String(id)}
function timeLabel(iso){if(!iso)return"—";try{const d=new Date(iso);const now=new Date();const diffMs=now-d;const diffMin=Math.round(diffMs/60000);const sameDay=d.toDateString()===now.toDateString();if(sameDay&&diffMin>=0)return diffMin<1?"刚刚":diffMin<60?diffMin+" 分钟前":"今天 "+d.getHours()+":"+String(d.getMinutes()).padStart(2,"0");const diffDay=Math.round(diffMin/1440);if(diffDay>-7&&diffDay<=0)return diffDay===0?"今天":diffDay===-1?"昨天":Math.abs(diffDay)+" 天前";return (d.getMonth()+1)+"-"+d.getDate()+" "+String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0")}catch{return String(iso).slice(0,16)}}
async function list(p){const r=await api(p);return r.items||[]}
async function refresh(){try{S.me=await api("/auth/me");$("#identity").textContent=S.me.display_name||S.me.email||S.me.user_id;$("#logout").hidden=false;$("#login").hidden=true;$("#content").hidden=false;S.features=(await api(API+"/workbench/capabilities")).features||{};try{const agents=await list(API+"/registry/agent?limit=200");S.agentLabels=Object.fromEntries(agents.map(x=>[x.item_id,(x.definition&&x.definition.name)||x.item_id]))}catch{S.agentLabels={}}$("#api-state").textContent="API 已连接"}catch(e){S.me=null;$("#identity").textContent="未登录";$("#logout").hidden=true;$("#login").hidden=false;$("#content").hidden=true;$("#api-state").textContent="未认证"}}
function nav(v){
  if(v!=="task-detail"&&S.runStream){S.runStream.close();S.runStream=null}S.view=v;S.session=null;S.task=null;S.approval=null;document.querySelectorAll("[data-view]").forEach(b=>{b.classList.toggle("active",b.dataset.view===v);if(b.tagName==="BUTTON"){b.classList.toggle("active",b.dataset.view===v);b.setAttribute("aria-current",b.dataset.view===v?"page":"false")}});const names={overview:"驾驶舱",board:"运营看板",tasks:"在办任务",sessions:"会话中心",batch:"批量执行",replay:"Run 回放",approvals:"待我拍板",artifacts:"产物交付",registry:"Agent 与资源管理","knowledge-graph":"知识图谱",governance:"知识与运营管理",observability:"可观测性","prompt-lab":"Prompt 版本管理"};$("#title").textContent=names[v]||v;$("#crumb").textContent=names[v]||v;$("#hero-slot").innerHTML="";render()}
async function render(){const root=$("#view");root.innerHTML="<div class='panel'>加载中…</div>";try{
if(S.view==="overview"){
  $("#hero-slot").innerHTML="<div class='hero'><h2>龍奕 FDE 工作室 · 驾驶舱</h2><p>从会话输入到任务派发、Run 监控、人工审批与产物交付的工作台。这里是你每天打开的第一屏。</p></div>";
  const rs=await Promise.allSettled([
    list(API+"/approvals?limit=50"),
    list(API+"/workbench/tasks?limit=50"),
    list(API+"/sessions?limit=50")
  ]);
  const approvals=rs[0].status==="fulfilled"?rs[0].value:[];
  const tasks=rs[1].status==="fulfilled"?rs[1].value:[];
  const sessions=rs[2].status==="fulfilled"?rs[2].value:[];
  const pendingApprovals=approvals.filter(a=>String(a.status).toLowerCase()==="pending");
  const activeTasks=tasks.filter(t=>{const s=String(t.status).toLowerCase();return s==="running"||s==="pending"||s==="queued"});
  const failedTasks=tasks.filter(t=>{const s=String(t.status).toLowerCase();return s==="failed"||s==="cancelled"});
  const enabledFeatures=Object.values(S.features).filter(Boolean).length;
  const statusBadge=v=>statusPill(v);
  let approvalCard="";
  if(pendingApprovals.length){
    approvalCard=panel("🔴 待我拍板 · "+pendingApprovals.length+" 项",
      pendingApprovals.slice(0,5).map(a=>{
        const id=esc(shortId(a.approval_id));
        return "<div style='padding:10px 0;border-top:1px solid #29344b;display:flex;align-items:center;gap:10px'>"+
          "<div style='flex:1;min-width:0'>"+
          "<b>审批 #"+id+"</b> · "+esc(a.action||"—")+" · "+esc(a.resource||"—")+
          "<br><span class='subtle'>申请人："+esc(a.requested_by||"—")+" · 风险："+esc(a.risk_level?riskLabel(a.risk_level):"—")+"</span>"+
          "</div>"+
          btn("批准","data-go='approvals'","primary")+
          btn("拒绝","data-go='approvals'")+
          "</div>";
      }).join("")+(pendingApprovals.length>5?"<p class='subtle' style='margin-top:8px'>还有 "+(pendingApprovals.length-5)+" 项待处理</p>":""),
      btn("全部审批 →","data-go='approvals'"));
  }else{
    approvalCard=panel("🟢 没有待拍板审批","<p class='subtle'>所有审批已处理完毕。</p>",btn("审批中心 →","data-go='approvals'"));
  }
  let failedCard="";
  if(failedTasks.length){
    failedCard=panel("🟠 近期失败 · "+failedTasks.length+" 项",
      failedTasks.slice(0,5).map(t=>{
        return "<div style='padding:10px 0;border-top:1px solid #29344b;display:flex;align-items:center;gap:10px'>"+
          "<div style='flex:1;min-width:0'>"+
          "<b>任务 #"+esc(shortId(t.task_id))+"</b> · Agent "+esc(agentName(t.agent_id))+
          "<br><span class='subtle'>"+esc(timeLabel(t.updated_at||t.created_at))+"</span>"+
          "</div>"+
          btn("查看详情","data-task='"+esc(t.task_id)+"'")+
          "</div>";
      }).join("")+(failedTasks.length>5?"<p class='subtle' style='margin-top:8px'>还有 "+(failedTasks.length-5)+" 项失败</p>":""),
      btn("全部任务 →","data-go='tasks'"));
  }else{
    failedCard=panel("🟢 没有失败任务","<p class='subtle'>全部任务运行正常。</p>");
  }
  let tasksCard="";
  if(activeTasks.length){
    tasksCard=panel("🟡 在办任务 · "+activeTasks.length+" 项",
      activeTasks.slice(0,5).map(t=>{
        return "<div style='padding:10px 0;border-top:1px solid #29344b;display:flex;align-items:center;gap:10px'>"+
          "<div style='flex:1;min-width:0'>"+
          "<b>任务 #"+esc(shortId(t.task_id))+"</b> · Agent "+esc(agentName(t.agent_id))+" · "+statusBadge(t.status)+
          "<br><span class='subtle'>"+esc(timeLabel(t.updated_at||t.created_at))+"</span>"+
          "</div>"+
          btn("查看详情","data-task='"+esc(t.task_id)+"'")+
          "</div>";
      }).join("")+(activeTasks.length>5?"<p class='subtle' style='margin-top:8px'>还有 "+(activeTasks.length-5)+" 项在办</p>":""),
      btn("全部任务 →","data-go='tasks'"));
  }else{
    tasksCard=panel("⚪ 当前没有在办任务","<p class='subtle'>可以从「在办任务」页面提交新任务。</p>",btn("提交任务 →","data-go='tasks'"));
  }
const featureLabels={knowledge:"知识入库",rag:"RAG 检索引擎",rag_query:"RAG 查询端点",memory:"对话记忆持久化",sessions:"会话服务",registry:"资源与模板注册",agent_management:"Agent 管理",task_execution:"任务执行中心",graph:"知识图谱",graph_annotation:"图谱标注",graph_visualization:"图谱可视化",batch_execution:"批量执行",approvals:"人工审批",blackboard:"黑板协作",quotas:"配额管理",metrics:"指标采集",audit:"审计追踪",prompt_management:"Prompt 管理"};
const featureEntries=Object.entries(S.features||{}).map(([k,v])=>({key:k,label:featureLabels[k]||k,enabled:Boolean(v)}));
const featEnabledCount=featureEntries.filter(f=>f.enabled).length;
const featurePills=featureEntries.map(f=>"<span class='pill' style='background:"+(f.enabled?"rgba(74,222,155,.18);color:#4ADE9B":"rgba(255,107,127,.15);color:#b4bfe8")+"'>"+esc(f.label)+" "+(f.enabled?"✓":"✗")+"</span>").join(" ");
const featuresPanel=panel("平台能力开关 · "+featEnabledCount+"/"+featureEntries.length+" 启用",
  "<p class='subtle'>服务端在租户入口统一校验能力边界；工作台不会绕过后端权限。</p>"+
  "<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:8px'>"+featurePills+"</div>");
  root.innerHTML=
    "<div class='grid'>"+
    "<div class='stat'><small>在办任务</small><strong>"+activeTasks.length+"</strong></div>"+
    "<div class='stat'><small>失败任务</small><strong style='color:#ff909b'>"+failedTasks.length+"</strong></div>"+
    "<div class='stat'><small>待我拍板</small><strong style='color:#ffc247'>"+pendingApprovals.length+"</strong></div>"+
    "<div class='stat'><small>启用能力</small><strong>"+featEnabledCount+"/"+featureEntries.length+"</strong></div>"+
    "</div>"+
    approvalCard+
    tasksCard+
    failedCard+
    featuresPanel;
}
else if(S.view==="sessions"){if(!S.features.sessions){root.innerHTML=panel("会话服务未配置","<p>后端尚未启用会话能力。</p>");return}const [ss,fs]=await Promise.all([list(API+"/sessions?limit=100"),list(API+"/files?limit=100")]);root.innerHTML=panel("创建会话","<form id='session-form' class='inline-form'><label>会话标题<input name='title' required maxlength='200'></label><label>项目<input name='project_id' placeholder='OPC-002' value='OPC-002'></label><button class='button primary'>创建会话</button></form>")+panel("会话列表",table(["会话","项目","状态","更新时间","操作"],ss.map(x=>"<tr><td><b>"+esc(x.title||"会话 #"+shortId(x.session_id))+"</b><small class='subline'>"+esc(shortId(x.session_id))+"</small></td><td>"+esc(x.project_id||"OPC-002")+"</td><td>"+statusPill(x.status)+"</td><td>"+esc(timeLabel(x.updated_at))+"</td><td>"+btn("打开","data-session='"+esc(x.session_id)+"'")+"</td></tr>")))+panel("文件资产",fs.length?table(["文件","大小","关联会话","版本","操作"],fs.map(x=>"<tr><td>"+esc(x.filename)+"</td><td>"+esc((x.size_bytes/1024).toFixed(1))+" KB</td><td>"+esc(x.session_id?shortId(x.session_id):"—")+"</td><td>"+esc(x.version)+"</td><td>"+btn("下载","data-download='"+esc(x.asset_id)+"'")+"</td></tr>")):"<p class='empty'>暂无文件。可以在会话详情页上传，或关联到任务产出。</p>")}
else if(S.view==="session-detail"){await renderSession(S.session);return}
else if(S.view==="tasks"){if(!S.features.task_execution){root.innerHTML=panel("任务执行未配置","<p>服务端未启用 Task Center；工作台不会绕过 Runtime 启动 Agent。</p>");return}const [ts,ss]=await Promise.all([list(API+"/workbench/tasks?limit=100"),list(API+"/sessions?limit=100")]);const agentOptions=Object.entries(S.agentLabels||{}).map(([id,name])=>"<option value='"+esc(id)+"'>"+esc(name)+"（"+esc(id)+"）</option>").join("")||"<option value='local-assistant'>本地助手（local-assistant）</option>";root.innerHTML=panel("提交任务","<form id='task-form'><div class='form-grid'><label>Agent<select name='agent_id' required>"+agentOptions+"</select></label><label>关联会话<select name='session_id'><option value=''>不关联</option>"+ss.map(x=>"<option value='"+esc(x.session_id)+"'>"+esc(x.title||shortId(x.session_id))+"</option>").join("")+"</select></label><label>项目 Project ID<input name='project_id' value='OPC-002' placeholder='OPC-002'></label><label>优先级<input name='priority' type='number' min='0' max='1000' value='100'></label></div><label class='full'>任务指令（JSON 对象）<textarea name='input' required rows='5' placeholder='{\n  \"prompt\": \"请处理任务\"\n}'></textarea></label><p class='subtle'>说明：input 必须是合法 JSON 对象。可只传 prompt 字段，也可传入上下文、约束等结构化参数。</p><button class='button primary'>提交任务</button></form>")+panel("任务队列",table(["任务","Agent","状态","Run","更新时间","操作"],ts.map(x=>"<tr><td><b>#"+esc(shortId(x.task_id))+"</b></td><td>"+esc(agentName(x.agent_id))+"</td><td>"+statusPill(x.status)+"</td><td>"+esc(x.run_id?"#"+shortId(x.run_id):"—")+"</td><td>"+esc(timeLabel(x.updated_at||x.created_at))+"</td><td>"+btn("详情","data-task='"+esc(x.task_id)+"'")+"</td></tr>")))}
else if(S.view==="task-detail"){await renderTask(S.task);return}
else if(S.view==="approvals"){if(!S.features.approvals){root.innerHTML=panel("审批服务未配置","<p>后端未启用人工审批服务。</p>");return}const as=await list(API+"/approvals?limit=100");root.innerHTML=panel("人工审批队列",table(["审批","Run","动作","资源","风险","申请人","状态","创建时间","操作"],as.map(x=>"<tr><td><b>#"+esc(shortId(x.approval_id))+"</b></td><td>"+esc(x.run_id?shortId(x.run_id):"—")+"</td><td>"+esc(x.action)+"</td><td>"+esc(x.resource)+"</td><td>"+esc(riskLabel(x.risk_level))+"</td><td>"+esc(x.requested_by)+"</td><td>"+statusPill(x.status)+"</td><td>"+esc(timeLabel(x.created_at))+"</td><td>"+btn("查看","data-approval='"+esc(x.approval_id)+"'")+"</td></tr>")))}
else if(S.view==="approval-detail"){await renderApproval(S.approval);return}
else if(S.view==="artifacts"){const fs=await list(API+"/files?limit=200");root.innerHTML=panel("交付产物",fs.length?table(["文件","媒体类型","大小","关联会话","版本","操作"],fs.map(x=>"<tr><td><b>"+esc(x.filename)+"</b><small class='subline'>资产 #"+esc(shortId(x.asset_id))+"</small></td><td>"+esc(x.media_type)+"</td><td>"+esc((x.size_bytes/1024).toFixed(1))+" KB</td><td>"+esc(x.session_id?shortId(x.session_id):"—")+"</td><td>"+esc(x.version)+"</td><td>"+btn("下载","data-download='"+esc(x.asset_id)+"'")+"</td></tr>")):"<p class='empty'>暂无交付产物。</p>")}
else if(S.view==="knowledge-graph"){
  const rs=await Promise.allSettled([
    list(API+"/knowledge/documents?limit=200"),
    list(API+"/knowledge/collections?limit=100"),
  ]);
  const docs=rs[0].status==="fulfilled"?rs[0].value:[];
  const cols=rs[1].status==="fulfilled"?rs[1].value:[];
  const totalChunks=docs.reduce((s,d)=>s+Number(d.chunk_count||0),0);
  const root2=root;
  let docsHtml=docs.length?table(["文档","集合","块数","大小","创建时间"],docs.slice(0,20).map(d=>"<tr><td><b>"+esc(d.title||shortId(d.document_id))+"</b><small class='subline'>"+esc(shortId(d.document_id))+"</small></td><td>"+esc(d.collection_id?shortId(d.collection_id):"—")+"</td><td>"+esc(d.chunk_count||"—")+"</td><td>"+esc(d.size_bytes?Math.round(d.size_bytes/1024)+" KB":"—")+"</td><td>"+esc(timeLabel(d.created_at))+"</td></tr>")):"<p class='empty'>暂无知识文档。</p>";
  let colsHtml=cols.length?table(["集合","文档数","描述","创建时间"],cols.map(c=>"<tr><td><b>"+esc(c.name)+"</b><small class='subline'>"+esc(shortId(c.collection_id))+"</small></td><td>"+esc(c.document_count||"—")+"</td><td>"+esc(c.description||"—")+"</td><td>"+esc(timeLabel(c.created_at))+"</td></tr>")):"<p class='empty'>暂无知识集合。</p>";
  root2.innerHTML=
    "<div class='grid'>"+
    "<div class='stat'><small>知识文档</small><strong>"+docs.length+"</strong></div>"+
    "<div class='stat'><small>总块数</small><strong>"+totalChunks+"</strong></div>"+
    "<div class='stat'><small>知识集合</small><strong>"+cols.length+"</strong></div>"+
    "<div class='stat'><small>数据来源</small><strong>知识文档 + 知识集合</strong></div>"+
    "</div>"+
    panel("知识文档（最近 20）",docsHtml)+
    panel("知识集合",colsHtml)+
    panel("说明","<p class='subtle'>知识图谱视图聚合知识文档与知识集合两个后端接口的数据，展示知识入库状态和组织结构。完整的知识运营（上传、分块、评估、RAG 检索）请使用「知识与运营管理」页面。</p>",btn("知识运营 →","data-go='governance'"));
}
}catch(e){root.innerHTML=panel("数据加载失败","<p class='error'>"+esc(e.message)+"</p>"+btn("重试","data-retry='1'"))}}
async function renderSession(id){if(!id){nav("sessions");return}const [ss,ms,fs]=await Promise.all([list(API+"/sessions?limit=200"),list(API+"/sessions/"+encodeURIComponent(id)+"/messages?limit=200"),list(API+"/files?session_id="+encodeURIComponent(id)+"&limit=100")]);const s=ss.find(x=>x.session_id===id)||{session_id:id};$("#title").textContent=s.title||"会话详情";$("#crumb").textContent="会话详情";$("#view").innerHTML=panel("会话信息","<p>会话 ID：<code>"+esc(shortId(id))+"</code>（完整："+esc(id)+"）</p><p>项目："+esc(s.project_id||"—")+" · 状态："+esc(statusLabel(s.status||"—"))+"</p>",btn("返回会话列表","data-go='sessions'"))+panel("消息记录",ms.map(m=>"<article class='message'><header><b>"+esc(m.role==="user"?"用户":m.role==="assistant"?"助手":m.role)+"</b><small>"+esc(timeLabel(m.created_at))+"</small></header><p>"+esc(m.content)+"</p></article>").join("")||"<p class='empty'>暂无消息</p>")+panel("发送消息","<form id='message-form'><label>消息内容<textarea name='content' required></textarea></label><button class='button primary'>发送</button></form>")+panel("上传附件","<form id='upload-form'><input type='file' name='file' required><button class='button'>上传到此会话</button></form>")+panel("会话文件",table(["文件","大小","版本","操作"],fs.map(f=>"<tr><td>"+esc(f.filename)+"</td><td>"+esc((f.size_bytes/1024).toFixed(1))+" KB</td><td>"+esc(f.version)+"</td><td>"+btn("下载","data-download='"+esc(f.asset_id)+"'")+"</td></tr>")))}
function connectRunStream(runId){
 if(S.runStream){S.runStream.close();S.runStream=null}
 const output=$("#run-stream-output"),status=$("#run-stream-status"),events=$("#run-stream-events");
 if(!runId||!output||!window.EventSource)return;
 const source=new EventSource(API+"/runs/"+encodeURIComponent(runId)+"/stream",{withCredentials:true});
 S.runStream=source;
 const addEvent=(label,data)=>{
  if(!events)return;
  const line=document.createElement("div");
  line.textContent=label+" · "+JSON.stringify(data);
  events.appendChild(line);
  while(events.childNodes.length>200)events.removeChild(events.firstChild);
 };
 source.addEventListener("token_stream",event=>{
  try{const data=JSON.parse(event.data);output.textContent+=String(data.token??"")}
  catch(error){addEvent("token parse error",{message:error.message})}
 });
 source.addEventListener("step_progress",event=>{
  try{const data=JSON.parse(event.data);addEvent(data.event_type||"STEP",data)}
  catch(error){addEvent("step event parse error",{message:error.message})}
 });
 source.addEventListener("tool_output",event=>{
  try{addEvent("TOOL_OUTPUT",JSON.parse(event.data))}
  catch(error){addEvent("tool event parse error",{message:error.message})}
 });
 source.addEventListener("run_complete",event=>{
  try{addEvent("COMPLETE",JSON.parse(event.data))}catch{}
  if(status)status.textContent="流式执行完成";
  source.close();if(S.runStream===source)S.runStream=null;
 });
 source.addEventListener("error",event=>{
  if(typeof event.data==="string"){
   try{addEvent("ERROR",JSON.parse(event.data))}catch{addEvent("ERROR",{message:event.data})}
   if(status)status.textContent="执行失败";
   source.close();if(S.runStream===source)S.runStream=null;
  }else if(status){status.textContent="连接中断，浏览器将自动重连"}
 });
 source.onopen=()=>{if(status)status.textContent="实时连接中"};
}
async function renderTask(id){
 if(!id){nav("tasks");return}
 if(S.runStream){S.runStream.close();S.runStream=null}
 const t=await api(API+"/workbench/tasks/"+encodeURIComponent(id));S.currentTask=t;
 const runId=t.run_id||t.result?.run_id||t.output?.run_id;
 let run="<p class='subtle'>此任务尚未关联 Run ID。</p>";
 if(runId){
  try{
   const r=await api(API+"/runs/"+encodeURIComponent(runId));
   run="<p>Run：#"+esc(shortId(runId))+" · 状态："+esc(statusLabel(r.status))+" · 版本："+esc(r.version)+"</p>"+
    pre(r)+btn("查看 Run 事件","data-run-events='"+esc(runId)+"'")+
    "<p id='run-stream-status' class='subtle'>准备连接实时事件…</p>"+
    "<pre id='run-stream-output' class='json' aria-live='polite'></pre>"+
    "<div id='run-stream-events' class='subtle' aria-live='polite'></div>";
  }catch(e){run="<p class='error'>Run 详情不可用："+esc(e.message)+"</p>"}
 }
 const arts=t.artifacts||t.result?.artifacts||t.output?.artifacts||[];
 $("#title").textContent="任务详情";$("#crumb").textContent="任务详情";
 $("#view").innerHTML=panel("执行状态","<p>任务：#"+esc(shortId(id))+"</p><p>Agent："+esc(agentName(t.agent_id))+" · 状态："+esc(statusLabel(t.status))+"</p>"+pre(t),btn("返回任务列表","data-go='tasks'"))+
  panel("运行详情",run)+
  panel("执行产物",arts.length?arts.map(a=>"<p>"+esc(a.filename||a.name||shortId(a.asset_id)||a.uri)+"</p>").join(""):"<p class='subtle'>任务未直接声明产物；请在产物交付页查看已上传文件。</p>")+
  panel("任务操作",btn("取消任务","data-task-cancel='"+esc(id)+"'")+btn("重试任务","data-task-retry='"+esc(id)+"'"));
 if(runId)connectRunStream(runId);
}
async function renderApproval(id){if(!id){nav("approvals");return}const a=await api(API+"/approvals/"+encodeURIComponent(id));S.currentApproval=a;$("#title").textContent="审批详情";$("#crumb").textContent="审批详情";$("#view").innerHTML=panel("审批请求",pre(a),btn("返回审批队列","data-go='approvals'"))+(a.status==="pending"?panel("人工决策","<p>核对动作、资源和请求信息后作出决定。决策由服务端验证权限和状态。</p>"+btn("批准","data-decision='true'","primary")+btn("拒绝","data-decision='false'")):"")}
document.addEventListener("click",async e=>{const b=e.target.closest("button");if(!b)return;try{if(b.dataset.view){nav(b.dataset.view);return}if(b.dataset.go){nav(b.dataset.go);return}if(b.dataset.session){S.view="session-detail";S.session=b.dataset.session;await renderSession(S.session);return}if(b.dataset.task){S.view="task-detail";S.task=b.dataset.task;await renderTask(S.task);return}if(b.dataset.approval){S.view="approval-detail";S.approval=b.dataset.approval;await renderApproval(S.approval);return}if(b.dataset.download){const r=await fetch(API+"/files/"+encodeURIComponent(b.dataset.download),{credentials:"same-origin"});if(!r.ok)throw Error("文件下载失败："+r.status);const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="artifact";a.click();URL.revokeObjectURL(url);return}if(b.dataset.decision!==undefined){const yes=b.dataset.decision==="true";await api(API+"/approvals/"+encodeURIComponent(S.approval)+"/decision",{method:"POST",body:JSON.stringify({approved:yes})});toast(yes?"审批已批准":"审批已拒绝");nav("approvals");return}if(b.dataset.taskCancel){await api(API+"/workbench/tasks/"+encodeURIComponent(b.dataset.taskCancel)+"/cancel",{method:"POST",body:"{}"});toast("取消请求已提交");await renderTask(b.dataset.taskCancel);return}if(b.dataset.taskRetry){await api(API+"/workbench/tasks/"+encodeURIComponent(b.dataset.taskRetry)+"/retry",{method:"POST",body:JSON.stringify({idempotency_key:crypto.randomUUID()})});toast("重试请求已提交");await renderTask(b.dataset.taskRetry);return}if(b.dataset.runEvents){const ev=await list(API+"/runs/"+encodeURIComponent(b.dataset.runEvents)+"/events");$("#view").insertAdjacentHTML("beforeend",panel("Run 事件",pre(ev)));return}
if(b.dataset.replayOpen){S.view="replay";await openReplay(b.dataset.replayOpen);return}
if(b.dataset.retry){render();return}}catch(x){toast(x.message)}});
document.addEventListener("submit",async e=>{const f=e.target;if(f.id==="login-form"){e.preventDefault();const d=new FormData(f);try{await api("/auth/login",{method:"POST",body:JSON.stringify({tenant_id:d.get("tenant_id"),email:d.get("email"),password:d.get("password")})});await refresh();if(S.me)nav("overview")}catch(x){$("#login-error").textContent=x.message}return}if(f.id==="session-form"){e.preventDefault();const d=new FormData(f);try{const r=await api(API+"/sessions",{method:"POST",body:JSON.stringify({title:d.get("title"),project_id:d.get("project_id")||null})});toast("会话已创建");S.session=r.session_id;S.view="session-detail";await renderSession(S.session)}catch(x){toast(x.message)}return}if(f.id==="message-form"){e.preventDefault();const d=new FormData(f);try{await api(API+"/sessions/"+encodeURIComponent(S.session)+"/messages",{method:"POST",body:JSON.stringify({role:"user",content:d.get("content")})});toast("消息已发送");await renderSession(S.session)}catch(x){toast(x.message)}return}if(f.id==="upload-form"){e.preventDefault();const file=f.elements.file.files[0];if(!file)return;try{await upload(API+"/files?session_id="+encodeURIComponent(S.session),file);toast("文件已上传");await renderSession(S.session)}catch(x){toast(x.message)}return}if(f.id==="task-form"){e.preventDefault();const d=new FormData(f);let input;try{input=JSON.parse(d.get("input"))}catch{toast("任务输入必须是合法 JSON");return}if(!input||Array.isArray(input)||typeof input!=="object"||!Object.keys(input).length){toast("任务输入必须是非空 JSON 对象");return}try{const r=await api(API+"/workbench/tasks",{method:"POST",body:JSON.stringify({agent_id:d.get("agent_id"),project_id:d.get("project_id")||null,session_id:d.get("session_id")||null,priority:Number(d.get("priority")||100),input})});toast("任务已提交");if(r.task_id){S.task=r.task_id;S.view="task-detail";await renderTask(r.task_id)}else nav("tasks")}catch(x){toast(x.message)}}});
$("#logout").addEventListener("click",async()=>{try{await api("/auth/logout",{method:"POST",body:"{}"})}catch{}await refresh()});refresh().then(()=>{if(S.me)nav("overview")});

const REGISTRY_KINDS=["agent","workflow","model_provider","model_template","tool","skill"];
const REGISTRY_LABELS={agent:"Agent",workflow:"工作流",model_provider:"模型供应商",model_template:"模型模板",tool:"工具",skill:"Skill"};
const registryPath=kind=>API+"/registry/"+encodeURIComponent(kind);
async function renderRegistry(){
 const kind=S.registryKind||"agent";
 $("#title").textContent="Agent / 工作流 / 模型 / 工具 / Skills";
 $("#crumb").textContent="资源注册表";
 const items=await list(registryPath(kind));
 const selector="<label>资源类型<select id='registry-kind'>"+REGISTRY_KINDS.map(k=>"<option value='"+k+"' "+(k===kind?"selected":"")+">"+REGISTRY_LABELS[k]+"</option>").join("")+"</select></label>";
 const form="<form id='registry-create' class='registry-form'><div class='form-grid'><label>资源 ID<input name='item_id' required maxlength='128' pattern='[A-Za-z0-9][A-Za-z0-9._:-]{0,127}' placeholder='例如：support-agent'></label><label>资源定义（JSON）<textarea name='definition' required>{\n  \"name\": \"新资源\",\n  \"description\": \"描述该资源的用途\"\n}</textarea></label></div><button class='button primary'>创建草稿版本</button></form>";
 const rows=items.map(x=>"<tr><td><b>"+esc((x.definition&&x.definition.name)||x.item_id)+"</b><small class='subline'>"+esc(x.item_id)+"</small></td><td>"+esc(x.version)+"</td><td>"+statusPill(x.status)+"</td><td>"+esc(x.created_by)+"</td><td>"+esc(x.created_at)+"</td><td>"+btn("版本历史","data-reg-versions='"+esc(x.item_id)+"'")+((x.status==="draft")?btn("发布","data-reg-publish='"+esc(x.item_id)+"' data-version='"+esc(x.version)+"'","primary"):"")+((x.status==="published")?btn("停用","data-reg-disable='"+esc(x.item_id)+"'"):"")+"</td></tr>");
 $("#view").innerHTML=panel("资源类型",selector)+panel("新建资源草稿",form)+panel("资源目录",table(["资源（名称 / ID）","最新版本","状态","创建人","创建时间","管理"],rows))+
 panel("权限与安全边界","<p>资源目录、草稿写入、发布和停用分别由后端 Registry Authorizer 校验。所有资源按当前登录租户隔离；无权限时服务端返回 403。定义仅保存声明式元数据，密钥必须使用 Secret Reference，工具执行代码不从定义中加载。</p>");
}
const registryBaseRender=render;
render=async function(){if(S.view==="registry"){try{await renderRegistry()}catch(e){$("#view").innerHTML=panel("资源管理不可用","<p class='error'>"+esc(e.message)+"</p>")}return}return registryBaseRender()};
document.addEventListener("change",e=>{if(e.target.id==="registry-kind"){S.registryKind=e.target.value;renderRegistry().catch(x=>toast(x.message))}});
document.addEventListener("submit",async e=>{const f=e.target;if(f.id!=="registry-create")return;e.preventDefault();const d=new FormData(f);let definition;try{definition=JSON.parse(d.get("definition"))}catch{toast("资源定义必须是合法 JSON");return}if(!definition||Array.isArray(definition)||typeof definition!=="object"){toast("资源定义必须是 JSON 对象");return}try{const kind=S.registryKind||"agent";await api(registryPath(kind)+"/"+encodeURIComponent(d.get("item_id"))+"/versions",{method:"POST",body:JSON.stringify({definition})});toast("草稿已创建");await renderRegistry()}catch(x){toast(x.message)}});
document.addEventListener("click",async e=>{const b=e.target.closest("button");if(!b)return;try{if(b.dataset.regVersions){const kind=S.registryKind||"agent",id=b.dataset.regVersions,versions=await list(registryPath(kind)+"/"+encodeURIComponent(id)+"/versions");$("#view").insertAdjacentHTML("beforeend",panel("版本历史 · "+esc(id),table(["版本","状态","创建人","创建时间","发布时间"],versions.map(v=>"<tr><td>"+esc(v.version)+"</td><td>"+esc(statusLabel(v.status))+"</td><td>"+esc(v.created_by)+"</td><td>"+esc(v.created_at)+"</td><td>"+esc(v.published_at||"—")+"</td></tr>"))));return}if(b.dataset.regPublish){const kind=S.registryKind||"agent";await api(registryPath(kind)+"/"+encodeURIComponent(b.dataset.regPublish)+"/versions/"+encodeURIComponent(b.dataset.version)+"/publish",{method:"POST",body:"{}"});toast("资源版本已发布并设为 active");await renderRegistry();return}if(b.dataset.regDisable){const kind=S.registryKind||"agent";await api(registryPath(kind)+"/"+encodeURIComponent(b.dataset.regDisable)+"/disable",{method:"POST",body:"{}"});toast("资源已停用");await renderRegistry();return}}catch(x){toast(x.message)}});


// C-14-04: tenant-scoped knowledge, memory, collaboration and operations views.
const governanceBaseRender=render;
const isoWindow=()=>{const end=new Date(),start=new Date(end.getTime()-30*86400000);return {start:start.toISOString(),end:end.toISOString()}};
const rowsPanel=(title,items,fields)=>panel(title,table(fields,items.map(item=>"<tr>"+fields.map(k=>"<td>"+(k==="status"?statusPill(item[k]):k==="severity"?esc(severityLabel(item[k])):k==="risk_level"?esc(riskLabel(item[k])):k==="outcome"?esc(outcomeLabel(item[k])):k==="scope"?esc(scopeLabel(item[k])):esc(item[k]??"—"))+"</td>").join("")+"</tr>")));
async function governanceLoad(path){return list(API+path)}
async function renderGovernance(){
 const root=$("#view");$("#title").textContent="知识、记忆与平台运营";$("#crumb").textContent="知识与运营管理";
 const window=isoWindow(),q="start="+encodeURIComponent(window.start)+"&end="+encodeURIComponent(window.end);
 const jobs=[
  ["knowledge",()=>governanceLoad("/knowledge/documents?limit=100")],
  ["collections",()=>governanceLoad("/knowledge/collections?limit=100")],
  ["memory",()=>governanceLoad("/memories?limit=100")],
  ["candidates",()=>governanceLoad("/memories/candidates")],
  ["evaluations",()=>governanceLoad("/observability/evaluations?limit=50")],
  ["alerts",()=>governanceLoad("/observability/alerts?limit=50")],
  ["cost",()=>governanceLoad("/quotas/usage?"+q+"&limit=1000")],
  ["audit",()=>governanceLoad("/audit/tool-events?limit=100")],
  ["identityAudit",()=>governanceLoad("/audit/identity-events?limit=100")],
  ["metrics",()=>api(API+"/observability/metrics")]
 ];
 const settled=await Promise.allSettled(jobs.map(x=>x[1]()));
 const data=Object.fromEntries(jobs.map((x,i)=>[x[0],settled[i].status==="fulfilled"?settled[i].value:null]));
 const errors=jobs.filter((x,i)=>settled[i].status==="rejected").map((x,i)=>"<li>"+esc(x[0])+": "+esc(settled[jobs.indexOf(x)].reason?.message||"不可用")+"</li>").join("");
 const knowledge=data.knowledge?.items||[],memories=data.memory?.items||[],candidates=data.candidates?.items||[];
 const evals=data.evaluations?.items||[],alerts=data.alerts?.items||[],costs=data.cost?.items||[],audit=data.audit?.items||[],identity=data.identityAudit?.items||[];
 const totalCost=costs.reduce((n,x)=>n+Number(x.cost_usd||0),0),totalTokens=costs.reduce((n,x)=>n+Number(x.total_tokens||0),0);
 const scopeId=esc(S.me?.tenant_id||"");
 root.innerHTML=
  "<div class='grid'><div class='stat'><small>知识库文档</small><strong>"+knowledge.length+"</strong></div><div class='stat'><small>有效记忆</small><strong>"+memories.length+"</strong></div><div class='stat'><small>待审核记忆</small><strong>"+candidates.length+"</strong></div><div class='stat'><small>近 30 天成本 USD</small><strong>"+totalCost.toFixed(4)+"</strong></div><div class='stat'><small>近 30 天 Token</small><strong>"+totalTokens+"</strong></div><div class='stat'><small>待处理告警</small><strong>"+alerts.filter(x=>!['resolved','closed'].includes(String(x.status).toLowerCase())).length+"</strong></div></div>"+
  (errors?panel("部分数据源不可用","<ul class='error'>"+errors+"</ul>"):"")+
  panel("知识库管理",
"<form id='knowledge-doc-upload' class='inline-form'><label>上传知识文档<input type='file' name='file' accept='.md,.markdown,.pdf,.html' required></label><button class='button primary'>上传并入库</button></form><p class='subtle'>支持 Markdown、PDF、HTML；上传后按租户范围入库，并使用本地 bge-m3 生成向量。</p>"+
"<form id='knowledge-collection-create' class='inline-form'><label>新建知识库集合<input name='name' required maxlength='200' placeholder='集合名称'></label><label>项目 ID（可选）<input name='project_id' placeholder='project-a'></label><button class='button primary'>创建集合</button></form>"+
rowsPanel("集合目录",data.collections?.items||[],["collection_id","name","project_id","created_at"])+
"<form id='knowledge-index-create' class='inline-form'><label>集合 ID<input name='collection_id' required></label><label>索引名称<input name='index_name' required></label><label>Embedding 模型<input name='embedding_model' required value='bge-m3' placeholder='bge-m3'></label><label>向量维度<input name='dimensions' type='number' min='1' max='65536' value='1024' required></label><label>Schema 版本<input name='schema_version' type='number' min='1' value='1' required></label><button class='button'>创建索引版本</button></form>"+
rowsPanel("文档目录",knowledge,["document_id","title","version","scope","project_id","media_type"]))+
  panel("RAG 问答（本地模型）",
"<form id='rag-query' class='inline-form'><label>问题<input name='query' required maxlength='4096' placeholder='例如：平台的向量检索使用什么模型？'></label><label>召回条数<input name='top_k' type='number' min='1' max='20' value='5'></label><label>最低相似度<input name='min_score' type='number' min='0' max='1' step='0.01' value='0'></label><button class='button primary'>提问</button></form><div id='rag-results'><p class='subtle'>检索向量由本地 bge-m3 计算，回答由本地 Ollama 生成，不调用外部模型。</p></div>")+
  panel("记忆管理",rowsPanel("记忆记录",memories,["memory_id","scope","scope_id","content","confidence","version","status","expires_at"])+rowsPanel("待审核候选与冲突",candidates,["memory_id","scope","scope_id","content","confidence","status","version"]))+
  panel("Blackboard 协作视图","<form id='blackboard-query' class='inline-form'><label>范围<select name='scope'><option value='tenant'>租户</option><option value='project'>项目</option><option value='run'>Run</option><option value='task'>任务</option></select></label><label>范围 ID<input name='scope_id' required value='"+scopeId+"' placeholder='租户 / 项目 / Run / 任务 ID'></label><button class='button primary'>查询条目</button></form><div id='blackboard-results'></div>")+
  panel("评测报告",rowsPanel("评测运行",evals,["eval_run_id","suite_id","status","created_at","completed_at"]))+
  panel("成本与配额",rowsPanel("用量明细（近 30 天）",costs,["occurred_at","run_id","model_key","input_tokens","output_tokens","total_tokens","cost_usd","project_id","agent_id"]))+
  panel("运营告警",rowsPanel("告警列表",alerts,["event_id","severity","status","title","created_at","resolved_at"]))+
  panel("审计追踪",rowsPanel("工具事件",audit,["event_id","occurred_at","actor_id","run_id","tool_name","action","outcome"])+rowsPanel("身份事件",identity,["event_id","occurred_at","actor_id","action","target_id","outcome"]))+
  panel("指标快照",data.metrics?pre(data.metrics):"<p class='subtle'>指标接口未配置或无权限。</p>")+
  "<p class='subtle'>成本统计窗口："+esc(window.start)+" 至 "+esc(window.end)+"。所有数据通过当前会话身份访问；接口返回 403/404/503 时保留为数据源不可用，不绕过后端权限。</p>";
}
render=async function(){if(S.view==="governance"){try{await renderGovernance()}catch(e){$("#view").innerHTML=panel("运营视图加载失败","<p class='error'>"+esc(e.message)+"</p>"+btn("重试","data-retry='1'"))}return}return governanceBaseRender()};
document.addEventListener("submit",async e=>{
 const f=e.target;
 if(f.id==="knowledge-doc-upload"){
  e.preventDefault();const file=f.elements.file.files[0];
  if(!file){toast("请选择要上传的文档");return}
  try{const result=await upload(API+"/knowledge/documents/upload?filename="+encodeURIComponent(file.name),file);toast("已入库并生成向量："+result.chunk_count+" 个分块");await renderGovernance()}catch(x){toast(x.message)}
 }
 if(f.id==="knowledge-collection-create"){
  e.preventDefault();const d=new FormData(f),payload={name:String(d.get("name")||"").trim()};
  const project=String(d.get("project_id")||"").trim();if(project)payload.project_id=project;
  try{await api(API+"/knowledge/collections",{method:"POST",body:JSON.stringify(payload)});toast("知识库集合已创建");await renderGovernance()}catch(x){toast(x.message)}
 }
 if(f.id==="knowledge-index-create"){
  e.preventDefault();const d=new FormData(f),collection=String(d.get("collection_id")||"").trim();
  const dimensions=Number(d.get("dimensions")),schemaVersion=Number(d.get("schema_version"));
  if(!Number.isInteger(dimensions)||dimensions<1||!Number.isInteger(schemaVersion)||schemaVersion<1){toast("向量维度和 Schema 版本必须为正整数");return}
  try{const result=await api(API+"/knowledge/collections/"+encodeURIComponent(collection)+"/indexes",{method:"POST",body:JSON.stringify({index_name:d.get("index_name"),embedding_model:d.get("embedding_model"),dimensions,schema_version:schemaVersion})});toast("知识库索引版本已创建："+result.index_id);await renderGovernance()}catch(x){toast(x.message)}
 }
});
document.addEventListener("submit",async e=>{
 const f=e.target;if(f.id!=="blackboard-query")return;e.preventDefault();
 const d=new FormData(f),scope=String(d.get("scope")),scopeId=String(d.get("scope_id")||"").trim();
 if(!scopeId){toast("请输入 Blackboard 范围 ID");return}
 try{const result=await api(API+"/blackboard/entries?scope="+encodeURIComponent(scope)+"&scope_id="+encodeURIComponent(scopeId)+"&limit=100");
 $("#blackboard-results").innerHTML=rowsPanel("黑板条目",result.items||[],["entry_id","kind","status","version","author_id","created_at","content"]);
 }catch(x){$("#blackboard-results").innerHTML="<p class='error'>"+esc(x.message)+"</p>"}
});
document.addEventListener("submit",async e=>{
 const f=e.target;if(f.id!=="rag-query")return;e.preventDefault();
 const d=new FormData(f),box=$("#rag-results");
 const query=String(d.get("query")||"").trim(),topK=Number(d.get("top_k")||5),minScore=Number(d.get("min_score")||0);
 if(!query){toast("请输入问题");return}
 box.innerHTML="<p class='subtle'>正在检索知识库并生成回答…</p>";
 try{
  const r=await api(API+"/rag-playground/query",{method:"POST",body:JSON.stringify({query,top_k:topK,min_score:minScore,embedding_model:"bge-m3",rerank:false})});
  const hits=r.retrieved||[];
  const rows=hits.map(h=>"<tr><td>"+esc(h.title||"文档 #"+shortId(h.document_id))+"</td><td>"+esc(Number(h.score||0).toFixed(3))+"</td><td>"+esc(String(h.text||"").slice(0,180))+"</td></tr>");
  box.innerHTML="<p><b>回答：</b>"+esc(r.answer||(r.abstained?"未找到足够依据，已拒绝作答。":"—"))+"</p>"+
   (r.abstained?"<p class='subtle'>拒答原因："+esc(r.reason||"证据不足")+"</p>":"<p class='subtle'>引用编号："+esc((r.citations||[]).join("、")||"无")+" · 向量模型："+esc(r.embedding_model||"bge-m3")+"</p>")+
   (rows.length?table(["来源","相似度","片段"],rows):"<p class='empty'>未检索到相关片段，请确认知识库已入库文档。</p>");
 }catch(x){box.innerHTML="<p class='error'>"+esc(x.message)+"</p>"}
});


// Capability visibility is presentation-only; every API still enforces authorization.
const VIEW_CAPABILITIES={
 sessions:["sessions"],tasks:["task_execution"],approvals:["approvals"],
 registry:["registry","resource_registry"],
 governance:["knowledge","knowledge_base","memory","blackboard","evaluations","evaluation","quotas","cost","audit","observability"],
 "prompt-lab":["prompt_management"]
};
function viewCapabilityEnabled(view){
 const keys=VIEW_CAPABILITIES[view];if(!keys)return true;
 const declared=keys.filter(k=>Object.prototype.hasOwnProperty.call(S.features,k));
 return declared.length===0||declared.some(k=>Boolean(S.features[k]));
}
function applyCapabilityGate(){
 document.querySelectorAll("nav [data-view]").forEach(b=>{
  const enabled=viewCapabilityEnabled(b.dataset.view);
  b.hidden=!enabled;
  b.setAttribute("aria-hidden",String(!enabled));
 });
 if(!viewCapabilityEnabled(S.view)){
  const first=[...document.querySelectorAll("nav [data-view]")].find(b=>!b.hidden);
  if(first){S.view=first.dataset.view;S.session=null;S.task=null;S.approval=null}
 }
}
const capabilityBaseRefresh=refresh;
refresh=async function(){await capabilityBaseRefresh();if(S.me){applyCapabilityGate()}};
const capabilityBaseNav=nav;
nav=function(view){if(!viewCapabilityEnabled(view)){toast("该工作台能力当前未启用");return}return capabilityBaseNav(view)};

refresh().then(()=>{if(S.me){applyCapabilityGate();if(!viewCapabilityEnabled(S.view))nav("overview")}});

/* C-23-04: Prompt Lab embedded in the authenticated Workbench shell. */
async function renderPromptLab(){
 const root=$("#view");$("#title").textContent="Prompt 版本管理";$("#crumb").textContent="Prompt 版本管理";
 const id=S.promptId||"";
 root.innerHTML=panel("Prompt 标识与新版本","<form id='prompt-load-form' class='inline-form'><label>Prompt ID<input name='prompt_id' required maxlength='128' value='"+esc(id)+"' placeholder='例如：support-assistant'></label><button class='button'>加载版本</button></form><form id='prompt-version-form'><label>新版本模板<textarea name='template' required placeholder='使用 {variable} 定义模板变量'></textarea></label><button class='button primary'>创建不可变版本</button></form><p class='subtle'>版本内容不可原位修改或删除；编辑通过创建新版本完成。发布与回滚受服务端评测门禁和权限控制。</p>")+
 (id?panel("版本历史","<div id='prompt-versions'>加载中…</div>"):panel("开始使用","<p>输入 Prompt ID 后加载版本；首次创建模板会建立该 Prompt 的第一个版本。</p>"));
 if(id)await loadPromptLab();
}
async function loadPromptLab(){
 const id=S.promptId;if(!id)return;
 const base=API+"/prompts/"+encodeURIComponent(id);
 const [versions,variants,audit]=await Promise.all([
  api(base+"/versions"),api(base+"/variants"),
  api(base+"/audit?limit=50").catch(()=>({items:[]}))
 ]);
 const versionRows=(versions.items||[]).map(v=>{
  const actions=btn("查看","data-prompt-version='"+esc(v.version)+"'")+
   (Number(v.version)>1?btn("对比","data-prompt-diff='"+esc(v.version)+"'"):"")+
   btn("发布","data-prompt-activate='"+esc(v.version)+"'")+
   btn("回滚","data-prompt-rollback='"+esc(v.version)+"'");
  return "<tr><td>v"+esc(v.version)+"</td><td><code>"+esc(v.content_hash.slice(0,12))+"</code></td><td>"+esc((v.variables||[]).join(", ")||"—")+"</td><td>"+actions+"</td></tr>";
 });
 const variantRows=(variants.items||[]).map(v=>{
  const actions=btn("历史","data-prompt-history='"+esc(v.name)+"'")+
   btn("设版本","data-prompt-assign='"+esc(v.name)+"'")+
   (v.status==="active"?btn("废弃","data-prompt-deprecate='"+esc(v.name)+"'"):"");
  return "<tr><td>"+esc(v.name)+"</td><td>v"+esc(v.current_version)+"</td><td>"+esc(statusLabel(v.status))+"</td><td>"+actions+"</td></tr>";
 });
 $("#prompt-versions").innerHTML=panel("不可变版本",table(["版本","内容哈希","变量","操作"],versionRows))+
  panel("创建变体","<form id='prompt-variant-form' class='inline-form'><label>变体名称<input name='name' required pattern='[a-z][a-z0-9_-]{0,63}' placeholder='candidate'></label><label>初始版本<input name='version' required type='number' min='1' value='"+esc((versions.items||[]).at(-1)?.version||1)+"'></label><button class='button primary'>创建变体</button></form>")+
  panel("变体列表",table(["名称","当前版本","状态","操作"],variantRows))+
  panel("变更审计",table(["时间","动作","操作者","详情"],(audit.items||[]).map(x=>"<tr><td>"+esc(x.created_at||"—")+"</td><td>"+esc(x.action)+"</td><td>"+esc(x.actor_id)+"</td><td>"+esc(JSON.stringify(x.details||{}))+"</td></tr>")));
}
const promptLabBaseRender=render;
render=async function(){
 if(S.view==="prompt-lab"){
  try{await renderPromptLab()}catch(e){$("#view").innerHTML=panel("Prompt 版本管理加载失败","<p class='error'>"+esc(e.message)+"</p>")}
  return;
 }
 return promptLabBaseRender();
};
document.addEventListener("submit",async e=>{
 const f=e.target;
 if(f.id==="prompt-load-form"){
  e.preventDefault();S.promptId=String(new FormData(f).get("prompt_id")||"").trim();await renderPromptLab();return;
 }
 if(f.id==="prompt-version-form"){
  e.preventDefault();if(!S.promptId){toast("请先加载 Prompt ID");return}
  const d=new FormData(f);
  try{const r=await api(API+"/prompts/"+encodeURIComponent(S.promptId)+"/versions",{method:"POST",body:JSON.stringify({template:d.get("template")})});toast("已创建 v"+r.version);await loadPromptLab()}
  catch(x){toast(x.message)}
  return;
 }
 if(f.id==="prompt-variant-form"){
  e.preventDefault();const d=new FormData(f);
  try{await api(API+"/prompts/"+encodeURIComponent(S.promptId)+"/variants",{method:"POST",body:JSON.stringify({name:d.get("name"),version:Number(d.get("version"))})});toast("Variant 已创建");await loadPromptLab()}
  catch(x){toast(x.message)}
 }
});
document.addEventListener("click",async e=>{
 const b=e.target.closest("button");if(!b||!S.promptId)return;
 const base=API+"/prompts/"+encodeURIComponent(S.promptId);
 try{
  if(b.dataset.promptVersion){
   const v=await api(base+"/versions/"+b.dataset.promptVersion);
   $("#view").insertAdjacentHTML("beforeend",panel("Prompt v"+esc(v.version)+" · "+esc(v.content_hash),"<pre class='json'>"+esc(v.template)+"</pre>"));return;
  }
  if(b.dataset.promptDiff){
   const v=Number(b.dataset.promptDiff),baseVersion=v-1;
   const d=await api(base+"/versions/"+v+"/diff?base_version="+baseVersion);
   $("#view").insertAdjacentHTML("beforeend",panel("Diff v"+v+" vs v"+baseVersion,"<pre class='json'>"+esc(d.diff||"无差异")+"</pre>"));return;
  }
  if(b.dataset.promptActivate||b.dataset.promptRollback){
   const version=Number(b.dataset.promptActivate||b.dataset.promptRollback),action=b.dataset.promptActivate?"activate":"rollback";
   await api(base+"/"+action,{method:"POST",body:JSON.stringify({version})});toast("版本操作已完成");await loadPromptLab();return;
  }
  if(b.dataset.promptHistory){
   const h=await api(base+"/variants/"+encodeURIComponent(b.dataset.promptHistory)+"/history");
   $("#view").insertAdjacentHTML("beforeend",panel("Variant 历史 · "+esc(b.dataset.promptHistory),pre(h.items)));return;
  }
  if(b.dataset.promptAssign){
   const name=b.dataset.promptAssign,raw=window.prompt("请输入要分配的版本号","1"),version=Number(raw);
   if(!Number.isInteger(version)||version<1)return;
   await api(base+"/variants/"+encodeURIComponent(name)+"/versions",{method:"POST",body:JSON.stringify({version})});toast("Variant 已更新");await loadPromptLab();return;
  }
  if(b.dataset.promptDeprecate){
   await api(base+"/variants/"+encodeURIComponent(b.dataset.promptDeprecate)+"/deprecate",{method:"POST",body:"{}"});toast("Variant 已废弃");await loadPromptLab();
  }
 }catch(x){toast(x.message)}
});


/* C-29-03: persisted Run timeline, replay controls, Blackboard snapshots and Agent message flow. */
S.replay=null;
const replayEndpoint=runId=>API+"/runs/"+encodeURIComponent(runId)+"/replay";
const replayEventType=e=>String(e.event_type||e.type||"EVENT").toUpperCase();
const replayPayload=e=>e.payload&&typeof e.payload==="object"?e.payload:{};
function replayMessage(e){
 const p=replayPayload(e),type=replayEventType(e);
 const content=p.content??p.message??p.output??p.token??p.text;
 if(!/LLM|AGENT|MESSAGE|TOKEN|TOOL/.test(type)&&content===undefined)return null;
 return {type,role:p.role||(/TOOL/.test(type)?"tool":/LLM|AGENT/.test(type)?"agent":"system"),
  name:p.tool_name||p.name||p.agent_id||"",content:content??p.arguments??p.input??p};
}
function replayTimelineRows(events,cursor){
 return events.map((event,index)=>{
  const seq=Number(event.sequence??index+1),type=replayEventType(event);
  return "<tr class='"+(index<cursor?"replay-consumed":"")+"'><td>"+esc(seq)+"</td><td>"+esc(type)+"</td><td>"+esc(event.occurred_at||event.created_at||"—")+"</td><td>"+esc(event.step_id||"—")+"</td><td>"+btn("定位","data-replay-seek='"+(index+1)+"'")+"</td></tr>";
 });
}
function replayBlackboardContent(event){
 const p=replayPayload(event);
 return p.blackboard_snapshot??p.blackboard??p.snapshot?.blackboard??p.state?.blackboard??null;
}
async function openReplay(runId){
 const opened=await api(replayEndpoint(runId),{method:"POST",body:"{}"});
 const [timeline,blackboard]=await Promise.all([
  api(replayEndpoint(runId)+"/"+encodeURIComponent(opened.session_id)+"/events?limit=500"),
  api(API+"/blackboard/entries?scope=run&scope_id="+encodeURIComponent(runId)+"&limit=100").catch(error=>({items:[],error:error.message}))
 ]);
 const events=(timeline.items||[]).map((item,index)=>({
  ...item,sequence:Number(item.sequence??index+1),
  event_type:item.event_type||"RUN_EVENT",
  payload:item.payload||{}
 }));
 S.replay={...opened,runId,events,timelineTotal:timeline.total??events.length,
  blackboardItems:blackboard.items||[],blackboardError:blackboard.error||null,currentEvent:null};
 await renderReplay();
}
async function replayAction(action,body={}){
 if(!S.replay?.session_id)throw Error("请先打开 Run 回放");
 const result=await api(replayEndpoint(S.replay.runId)+"/"+encodeURIComponent(S.replay.session_id)+"/"+action,
  {method:"POST",body:JSON.stringify(body)});
 S.replay={...S.replay,...result,currentEvent:result.event||S.replay.currentEvent};
 await renderReplay();
}
async function renderReplay(){
 const r=S.replay,root=$("#view");
 $("#title").textContent="Run 时间线与回放";$("#crumb").textContent="Run 回放";
 if(!r){
  let recentRunsHtml="";
  try{
   const tr=await list(API+"/workbench/tasks?limit=20");
   const withRun=tr.filter(t=>t.run_id).slice(0,15);
   if(withRun.length){
    recentRunsHtml=panel("最近 20 条 Run（点击进入回放）",
      table(["任务","Agent","状态","Run","更新时间","操作"],withRun.map(t=>"<tr><td><b>#"+esc(shortId(t.task_id))+"</b></td><td>"+esc(agentName(t.agent_id))+"</td><td>"+statusPill(t.status)+"</td><td>"+esc(shortId(t.run_id))+"</td><td>"+esc(timeLabel(t.updated_at||t.created_at))+"</td><td>"+btn("回放","data-replay-open='"+esc(t.run_id)+"'")+"</td></tr>")));
   }
  }catch{}
  root.innerHTML=panel("打开 Run 回放","<form id='replay-open-form' class='inline-form'><label>Run ID<input name='run_id' required placeholder='输入已有 Run ID'></label><button class='button primary'>加载时间线</button></form><p class='subtle'>从持久化 Run 事件、上下文快照和 Blackboard 数据重建视图；所有读取与回放操作继续由服务端租户权限校验。</p>")+recentRunsHtml;
  return;
 }
 const events=r.events||[],cursor=Number(r.cursor||0),current=r.currentEvent||null;
 const selected=current||events[Math.max(0,cursor-1)]||null,payload=replayPayload(selected||{});
 const messages=events.map(replayMessage).filter(Boolean);
 const blackboardSnapshot=replayBlackboardContent(selected);
 const snapshot=blackboardSnapshot??(r.blackboardItems||[]);
 const controls=btn("上一步","data-replay-action='back'")+
  btn("单步","data-replay-action='step'","primary")+
  btn(r.paused?"继续":"暂停","data-replay-action='"+(r.paused?"resume":"pause")+"'")+
  btn("快进至结束","data-replay-action='fast-forward'")+
  btn("重新加载","data-replay-reopen='1'");
 const selectedSequence=Number(selected?.sequence||0);
 const canRerun=Boolean(selected?.step_id&&(payload.replay_input!==undefined||payload.replay_input_ref));
 const editableContext=r.context?.run_context||{};
 const editablePatch={};
 for(const key of ["objective","request","constraints","decisions","evidence","todo"]){
  if(Object.prototype.hasOwnProperty.call(editableContext,key))editablePatch[key]=editableContext[key];
 }
 const contextEditor="<form id='replay-context-form'><label>可编辑上下文（JSON）<textarea name='context_patch' rows='8' "+(r.paused?"":"disabled")+">"+esc(JSON.stringify(editablePatch,null,2))+"</textarea></label><button class='button primary' "+(r.paused?"":"disabled")+">保存修改</button><p class='subtle'>仅暂停状态可修改 objective、request、constraints、decisions、evidence、todo；修改仅作用于当前 Replay Session，不覆盖原 Run 快照。</p></form>";
 root.innerHTML=
  panel("Run 回放控制","<p>Run：<code>"+esc(r.runId)+"</code> · Session：<code>"+esc(r.session_id)+"</code></p><p>游标："+cursor+" / "+Number(r.total||events.length)+" · "+(r.paused?"已暂停":"播放就绪")+"</p>"+controls+(canRerun?btn("重跑当前步骤","data-replay-rerun='"+selectedSequence+"'"):"<p class='subtle'>当前事件没有持久化 step_id 与 replay_input/ref，不提供重跑入口。</p>"))+
  panel("Run 时间线",table(["序号","事件","发生时间","Step","操作"],replayTimelineRows(events,cursor)))+
  panel("当前事件与重建上下文","<p>事件："+esc(selected?replayEventType(selected):"尚未定位")+" · Step："+esc(selected?.step_id||"—")+"</p>"+pre({event:selected,context:r.context||{}}))+
  panel("暂停时修改回放上下文",contextEditor)+
  panel("Blackboard 快照",blackboardSnapshot!==null?pre(blackboardSnapshot):
   (r.blackboardError?"<p class='error'>Blackboard 数据不可用："+esc(r.blackboardError)+"</p>":pre(snapshot)))+
  panel("Agent / Tool 消息流转",messages.length?messages.map(m=>"<article class='message'><header><b>"+esc(m.role)+" · "+esc(m.type)+"</b><small>"+esc(m.name)+"</small></header>"+pre(m.content)+"</article>").join(""):"<p class='subtle'>当前 Run 事件中没有可识别的 Agent/LLM/Tool 消息载荷。</p>");
}
const replayBaseRender=render;
render=async function(){
 if(S.view==="replay"){try{await renderReplay()}catch(e){$("#view").innerHTML=panel("Replay UI 加载失败","<p class='error'>"+esc(e.message)+"</p>")}return}
 return replayBaseRender();
};
document.addEventListener("submit",async e=>{
 const form=e.target;
 if(form.id==="replay-context-form"){
  e.preventDefault();
  if(!S.replay?.paused){toast("请先暂停 Replay 再修改上下文");return}
  try{
   const patch=JSON.parse(String(new FormData(form).get("context_patch")||""));
   if(!patch||typeof patch!=="object"||Array.isArray(patch)||!Object.keys(patch).length)throw Error("请输入非空 JSON 对象");
   const result=await api(replayEndpoint(S.replay.runId)+"/"+encodeURIComponent(S.replay.session_id)+"/context",
    {method:"PATCH",body:JSON.stringify(patch)});
   S.replay={...S.replay,...result,currentEvent:S.replay.currentEvent};
   toast("Replay 上下文已更新（仅当前 Session）");
   await renderReplay();
  }catch(error){toast(error.message)}
  return;
 }
 const f=e.target;if(f.id!=="replay-open-form")return;e.preventDefault();
 const runId=String(new FormData(f).get("run_id")||"").trim();if(!runId){toast("请输入 Run ID");return}
 try{await openReplay(runId)}catch(x){toast(x.message)}
});
document.addEventListener("click",async e=>{
 const b=e.target.closest("button");if(!b)return;
 try{
  if(b.dataset.replayAction){await replayAction(b.dataset.replayAction);return}
  if(b.dataset.replaySeek!==undefined){await replayAction("seek",{sequence:Number(b.dataset.replaySeek)});return}
  if(b.dataset.replayRerun!==undefined){
   const result=await api(replayEndpoint(S.replay.runId)+"/"+encodeURIComponent(S.replay.session_id)+"/rerun",
    {method:"POST",body:JSON.stringify({sequence:Number(b.dataset.replayRerun)})});
   $("#view").insertAdjacentHTML("beforeend",panel("步骤重跑结果",pre(result)));return;
  }
  if(b.dataset.replayReopen){await openReplay(S.replay.runId)}
 }catch(x){toast(x.message)}
});


/* S-34-02: dedicated Batch and Observability workbench surfaces. */
async function renderBatch(){
 const root=$("#view");
 $("#title").textContent="批量执行";
 $("#crumb").textContent="批量执行";
 if(S.features.batch_execution===false){
  root.innerHTML=panel("批量执行未启用","<p class='subtle'>当前租户未启用 Batch Execution 能力；页面不会绕过服务端 capability 或授权检查。</p>");
  return;
 }
 const batches=await list(API+"/workbench/batches?limit=100");
 const rows=batches.map(x=>{
  const id=String(x.batch_id||"");
  const actions=btn("详情","data-batch='"+esc(id)+"'")+
   (["queued","running","pending"].includes(String(x.status).toLowerCase())?btn("取消","data-batch-cancel='"+esc(id)+"'"):"")+
   (["failed","cancelled","partial_failure"].includes(String(x.status).toLowerCase())?btn("重试","data-batch-retry='"+esc(id)+"'"):"");
  return "<tr><td><code>"+esc(id)+"</code></td><td>"+esc(x.template_id||"—")+"</td><td>"+statusPill(x.status||"unknown")+"</td><td>"+esc(x.total_tasks??x.total??"—")+"</td><td>"+esc(x.succeeded??x.success_count??"—")+"</td><td>"+esc(x.failed??x.failure_count??"—")+"</td><td>"+esc(x.created_at||"—")+"</td><td>"+actions+"</td></tr>";
 });
 let detail="";
 if(S.batchId){
  try{
   const item=await api(API+"/workbench/batches/"+encodeURIComponent(S.batchId));
   detail=panel("批次详情 · "+esc(S.batchId),pre(item));
  }catch(error){detail=panel("批次详情不可用","<p class='error'>"+esc(error.message)+"</p>")}
 }
 root.innerHTML=
  panel("提交批处理",
   "<form id='batch-submit-form' class='registry-form'>"+
   "<div class='form-grid'><label>模板 ID<input name='template_id' required maxlength='128' value='agent-task' placeholder='agent-task'></label>"+
   "<label>最大并发<input name='max_concurrency' type='number' min='1' max='1000' value='8' required></label></div>"+
   "<label>数据集（JSON 数组；每项为输入对象）<textarea name='dataset' rows='8' required>[{\"agent_id\":\"local-assistant\",\"prompt\":\"第一项任务\"},{\"agent_id\":\"local-assistant\",\"prompt\":\"第二项任务\"}]</textarea></label>"+
   "<p class='subtle'>模板 agent-task 会为每一行启动一个工作台 Agent 任务，因此每项需包含 agent_id 与 prompt。提交使用服务端 BatchTaskService，tenant/actor 从认证 Principal 派生；请求附带幂等键。</p>"+
   "<button class='button primary'>提交批次</button></form>")+
  detail+
  panel("批次队列",table(["批次 ID","模板","状态","任务数","成功","失败","创建时间","操作"],rows))+
  "<p class='subtle'>批次列表、详情、取消和重试均通过同源认证 API；重试生成新的幂等键，权限由服务端再次验证。</p>";
}
async function renderObservability(){
 const root=$("#view");
 $("#title").textContent="运行可观测性";
 $("#crumb").textContent="运行可观测性";
 const window=isoWindow(),q="start="+encodeURIComponent(window.start)+"&end="+encodeURIComponent(window.end);
 const jobs=[
  ["capacity",()=>api(API+"/observability/health/capacity")],
  ["alerts",()=>list(API+"/observability/alerts?limit=100")],
  ["metrics",async()=>{const response=await api(API+"/observability/metrics");return await response.text()}],
  ["usage",()=>list(API+"/quotas/usage?"+q+"&limit=1000")],
  ["audit",()=>list(API+"/audit/tool-events?limit=100")],
  ["identity",()=>list(API+"/audit/identity-events?limit=100")]
 ];
 const settled=await Promise.allSettled(jobs.map(x=>x[1]()));
 const data=Object.fromEntries(jobs.map((x,i)=>[x[0],settled[i].status==="fulfilled"?settled[i].value:null]));
 const errors=jobs.filter((x,i)=>settled[i].status==="rejected")
  .map(x=>"<li>"+esc(x[0])+": "+esc(settled[jobs.indexOf(x)].reason?.message||"不可用")+"</li>").join("");
 const alerts=data.alerts?.items||[],usage=data.usage?.items||[],audit=data.audit?.items||[],identity=data.identity?.items||[];
 const openAlerts=alerts.filter(x=>!["resolved","closed"].includes(String(x.status||"").toLowerCase()));
 const tokens=usage.reduce((n,x)=>n+Number(x.total_tokens||0),0);
 const cost=usage.reduce((n,x)=>n+Number(x.cost_usd||0),0);
 const alertRows=alerts.map(x=>{
  const id=String(x.event_id||"");
  const status=String(x.status||"").toLowerCase();
  const actions=(status==="open"?btn("确认","data-alert-ack='"+esc(id)+"'"):"")+
   (["open","acknowledged"].includes(status)?btn("解决","data-alert-resolve='"+esc(id)+"'"):"");
  return "<tr><td>"+esc(id)+"</td><td>"+esc(severityLabel(x.severity))+"</td><td>"+statusPill(x.status)+"</td><td>"+esc(x.title||"—")+"</td><td>"+esc(x.created_at||"—")+"</td><td>"+actions+"</td></tr>";
 });
 root.innerHTML=
  "<div class='grid'>"+
   "<div class='stat'><small>待处理告警</small><strong>"+openAlerts.length+"</strong></div>"+
   "<div class='stat'><small>近 30 天 Token</small><strong>"+tokens+"</strong></div>"+
   "<div class='stat'><small>近 30 天成本 USD</small><strong>"+cost.toFixed(4)+"</strong></div>"+
  "</div>"+
  (errors?panel("部分数据源不可用","<ul class='error'>"+errors+"</ul>"):"")+
  panel("容量与健康",data.capacity?pre(data.capacity):"<p class='subtle'>容量健康数据不可用。</p>")+
  panel("运营告警",table(["事件","级别","状态","标题","创建时间","操作"],alertRows))+
  panel("用量明细",rowsPanel("模型与 Run 用量",usage,["occurred_at","run_id","model_key","input_tokens","output_tokens","total_tokens","cost_usd","project_id","agent_id"]))+
  panel("审计追踪",rowsPanel("工具事件",audit,["event_id","occurred_at","actor_id","run_id","tool_name","action","outcome"])+
   rowsPanel("身份事件",identity,["event_id","occurred_at","actor_id","action","target_id","outcome"]))+
  panel("Prometheus 指标",data.metrics?"<pre class='json'>"+esc(data.metrics)+"</pre>":"<p class='subtle'>指标 exporter 未配置或无权限。</p>")+
  "<p class='subtle'>统计窗口："+esc(window.start)+" 至 "+esc(window.end)+"。数据源错误会显式显示；所有读取与告警变更均由服务端认证、租户隔离和权限策略控制。</p>";
}
const workbenchPagesBaseRender=render;
render=async function(){
 if(S.view==="batch"){
  try{await renderBatch()}catch(error){$("#view").innerHTML=panel("批量执行不可用","<p class='error'>"+esc(error.message)+"</p>"+btn("重试","data-retry='1'"))}
  return;
 }
 if(S.view==="observability"){
  try{await renderObservability()}catch(error){$("#view").innerHTML=panel("可观测性不可用","<p class='error'>"+esc(error.message)+"</p>"+btn("重试","data-retry='1'"))}
  return;
 }
 return workbenchPagesBaseRender();
};
VIEW_CAPABILITIES.batch=["batch_execution"];
VIEW_CAPABILITIES.observability=["observability","metrics","audit","quotas"];
document.addEventListener("submit",async event=>{
 const form=event.target;
 if(form.id!=="batch-submit-form")return;
 event.preventDefault();
 const data=new FormData(form);
 let dataset;
 try{dataset=JSON.parse(String(data.get("dataset")||""))}
 catch{toast("数据集必须是合法 JSON 数组");return}
 if(!Array.isArray(dataset)||!dataset.length||dataset.some(row=>!row||typeof row!=="object"||Array.isArray(row))){
  toast("数据集必须是非空的 JSON 对象数组");return;
 }
 const concurrency=Number(data.get("max_concurrency"));
 if(!Number.isInteger(concurrency)||concurrency<1||concurrency>1000){toast("最大并发必须为 1 到 1000 的整数");return}
 try{
  const result=await api(API+"/workbench/batches",{method:"POST",body:JSON.stringify({
   template_id:String(data.get("template_id")||"").trim(),dataset,
   max_concurrency:concurrency,idempotency_key:crypto.randomUUID()
  })});
  S.batchId=result.batch_id||null;
  toast("批处理已提交");
  await renderBatch();
 }catch(error){toast(error.message)}
});
document.addEventListener("click",async event=>{
 const button=event.target.closest("button");if(!button)return;
 try{
  if(button.dataset.batch){
   S.batchId=button.dataset.batch;await renderBatch();return;
  }
  if(button.dataset.batchCancel){
   await api(API+"/workbench/batches/"+encodeURIComponent(button.dataset.batchCancel)+"/cancel",{method:"POST",body:"{}"});
   toast("批次取消请求已提交");await renderBatch();return;
  }
  if(button.dataset.batchRetry){
   await api(API+"/workbench/batches/"+encodeURIComponent(button.dataset.batchRetry)+"/retry",{
    method:"POST",body:JSON.stringify({idempotency_key:crypto.randomUUID()})
   });
   toast("批次重试已提交");await renderBatch();return;
  }
  if(button.dataset.alertAck){
   await api(API+"/observability/alerts/"+encodeURIComponent(button.dataset.alertAck)+"/acknowledge",{method:"POST",body:"{}"});
   toast("告警已确认");await renderObservability();return;
  }
  if(button.dataset.alertResolve){
   await api(API+"/observability/alerts/"+encodeURIComponent(button.dataset.alertResolve)+"/resolve",{method:"POST",body:"{}"});
   toast("告警已解决");await renderObservability();return;
  }
 }catch(error){toast(error.message)}
});


/* UI-G2-756: Organization / RBAC shell.
 * TODO(api): connect organization, department, project, invitation, role,
 * permission-preview and data-scope endpoints after the backend contract lands.
 * Keep auth=false free of synthetic users, organizations, roles and ACL claims.
 */
S.orgRbac = S.orgRbac || { tab: "org", selectedOrg: null, orgs: [], members: [], roles: [], scopes: [], error: null };
function orgRbacMode(){
  // TODO(api): replace the bootstrap injection with the finalized public bootstrap endpoint.
  const mode = window.PLATFORM_BOOTSTRAP?.auth?.enabled;
  return mode === false ? "disabled" : mode === true ? "enabled" : "unknown";
}
function orgRbacNotice(){
  const mode=orgRbacMode();
  if(mode==="disabled") return "<p class='subtle'>当前实例 auth=false：不创建账号、组织、默认角色或身份隔离。此页仅展示管理入口壳；身份/RBAC 操作不适用。</p>";
  if(mode==="unknown") return "<p class='subtle'>认证模式尚未由公开 bootstrap 元信息确认。页面保持空态，不推断身份模式。</p>";
  return "<p class='subtle'>认证已启用。以下数据在后端 API 接入前保持空态；本地交互不会写入真实权限。</p>";
}
function orgRbacTabButton(key,label){
  return "<button class='button "+(S.orgRbac.tab===key?"primary":"")+"' data-org-tab='"+key+"' type='button'>"+label+"</button>";
}
function renderOrgRbac(){
  const root=$("#view"),s=S.orgRbac,mode=orgRbacMode();
  $("#title").textContent="组织与权限管理";$("#crumb").textContent="组织与权限管理";
  const enabled=mode==="enabled";
  const tabs=orgRbacTabButton("org","组织 / 部门")+
    orgRbacTabButton("members","成员邀请")+
    orgRbacTabButton("roles","角色权限矩阵")+
    orgRbacTabButton("scopes","数据范围策略");
  let body="";
  if(s.tab==="org"){
    const rows=s.orgs.map(o=>"<tr><td>"+esc(o.name)+"</td><td>"+esc(o.type||"组织")+"</td><td>"+esc(o.status||"草稿")+"</td><td>"+btn("选择","data-org-select='"+esc(o.id)+"'")+"</td></tr>");
    body=panel("组织 / 部门树",s.orgs.length?table(["名称","类型","状态","操作"],rows):"<div class='empty-state'><strong>暂无组织或部门</strong><p>后端组织 API 接入后将在此显示组织、部门和项目层级。</p></div>",
      btn("新建组织","data-org-action='create-org'", "primary")+
      btn("新建部门","data-org-action='create-dept'"))+
      (s.selectedOrg?panel("当前节点",pre(s.orgs.find(x=>x.id===s.selectedOrg)||{})):"");
  }else if(s.tab==="members"){
    body=panel("成员邀请",enabled?
      "<form id='org-invite-form' class='registry-form'><div class='form-grid'><label>邮箱<input name='email' type='email' required placeholder='name@example.com'></label><label>归属组织<select name='org_id' required><option value=''>请选择组织</option>"+s.orgs.map(o=>"<option value='"+esc(o.id)+"'>"+esc(o.name)+"</option>").join("")+"</select></label></div><label>初始角色<select name='role_id'><option value=''>暂不分配</option>"+s.roles.map(r=>"<option value='"+esc(r.id)+"'>"+esc(r.name)+"</option>").join("")+"</select></label><button class='button primary' "+(!s.orgs.length?"disabled":"")+">创建邀请（本地草稿）</button></form><p class='subtle'>TODO：接入邀请创建、过期、撤销和审计 API。当前提交只保存在页面状态。</p>":
      "<p class='empty'>auth=false 下不创建成员或邀请，也不生成虚构用户。</p>")+
      panel("成员列表",s.members.length?table(["邮箱","组织","状态"],s.members.map(m=>"<tr><td>"+esc(m.email)+"</td><td>"+esc(m.org||"—")+"</td><td>"+esc(m.status)+"</td></tr>")):"<p class='empty'>暂无成员记录。</p>");
  }else if(s.tab==="roles"){
    const permissions=["组织读取","组织管理","成员邀请","成员停用","角色读取","角色授权","数据范围管理","审计读取"];
    body=panel("角色与权限矩阵",enabled?
      (s.roles.length?table(["角色",...permissions].map((x)=>x),s.roles.map(r=>"<tr><td>"+esc(r.name)+"</td>"+permissions.map(p=>"<td>"+(r.permissions?.includes(p)?"✓":"—")+"</td>").join("")+"</tr>")):"<p class='empty'>暂无角色。后端角色 API 接入后显示真实角色和权限集合。</p>")+
      btn("新建角色草稿","data-org-action='create-role'","primary")+
      "<p class='subtle'>TODO：补充菜单/路由/操作/API 权限映射、授权预览/差异和影响提示。前端隐藏按钮不构成安全边界。</p>":
      "<p class='empty'>auth=false 下不创建默认角色或权限主体。</p>");
  }else{
    body=panel("数据范围策略",enabled?
      (s.scopes.length?pre(s.scopes):"<p class='empty'>暂无数据范围策略。</p>")+
      "<form id='org-scope-form' class='registry-form'><label>策略名称<input name='name' required placeholder='项目范围'></label><label>范围类型<select name='scope'><option value='all'>全部</option><option value='organization'>组织</option><option value='department'>部门</option><option value='project'>项目</option><option value='self'>本人</option></select></label><button class='button primary'>添加策略草稿</button></form>"+
      "<p class='subtle'>TODO：服务端执行数据范围过滤并提供策略校验/审计；页面配置不能替代资源级授权。</p>":
      "<p class='empty'>auth=false 不执行用户、部门、项目成员关系 ACL；无需配置数据范围策略。</p>");
  }
  root.innerHTML=panel("组织 / RBAC 管理",orgRbacNotice()+tabs)+body+
    panel("接口与状态", "<p>组织、部门、成员、邀请、角色、权限预览、数据范围 API 尚待后端契约确认。</p><p class='subtle'>加载失败不会回退到伪造身份或默认权限；数据源当前为空数组。</p>"+(s.error?"<p class='error'>"+esc(s.error)+"</p>":""));
}
const orgRbacBaseRender=render;
render=async function(){
  if(S.view==="org-rbac"){try{renderOrgRbac()}catch(error){S.orgRbac.error=error.message;$("#view").innerHTML=panel("组织权限页面加载失败","<p class='error'>"+esc(error.message)+"</p>"+btn("重试","data-org-retry='1'"))}return}
  return orgRbacBaseRender();
};
const orgRbacBaseNav=nav;
nav=function(view){orgRbacBaseNav(view);if(view==="org-rbac"){$("#title").textContent="组织与权限管理";$("#crumb").textContent="组织与权限管理"}};
document.addEventListener("click",event=>{
 const b=event.target.closest("button");if(!b)return;
 if(b.dataset.orgTab){S.orgRbac.tab=b.dataset.orgTab;renderOrgRbac();return}
 if(b.dataset.orgSelect){S.orgRbac.selectedOrg=b.dataset.orgSelect;renderOrgRbac();return}
 if(b.dataset.orgRetry){S.orgRbac.error=null;renderOrgRbac();return}
 if(b.dataset.orgAction){
  const action=b.dataset.orgAction;
  if(orgRbacMode()!=="enabled"){toast("当前认证模式不支持身份管理");return}
  if(action==="create-org"||action==="create-dept"||action==="create-role"){
   toast("后端 API 尚未就绪；当前仅提供 UI 壳，不写入数据");
  }
 }
});
document.addEventListener("submit",event=>{
 const form=event.target;
 if(form.id==="org-invite-form"){
  event.preventDefault();
  if(orgRbacMode()!=="enabled"){toast("当前认证模式不支持成员邀请");return}
  toast("邀请 API 尚未接入；未发送邀请");
  return;
 }
 if(form.id==="org-scope-form"){
  event.preventDefault();
  if(orgRbacMode()!=="enabled"){toast("当前认证模式不支持数据范围策略");return}
  const d=new FormData(form);S.orgRbac.scopes.push({name:String(d.get("name")||""),scope:String(d.get("scope")||"")});
  toast("已添加本地策略草稿，尚未保存到服务端");renderOrgRbac();
 }
});

/* UI-G2-756: auth=false entry is driven only by trusted public bootstrap metadata. */
const orgRbacRefreshBase=refresh;
refresh=async function(){
 const bootstrap=window.PLATFORM_BOOTSTRAP;
 if(bootstrap?.auth?.enabled===false){
  S.me=null;S.authMode="disabled";S.features=bootstrap.features||{};
  $("#identity").textContent="实例模式（无登录）";
  $("#logout").hidden=true;$("#login").hidden=true;$("#content").hidden=false;
  $("#api-state").textContent="实例模式 · 无身份认证";
  applyCapabilityGate();
  return;
 }
 return orgRbacRefreshBase();
};
refresh().then(()=>{if(S.authMode==="disabled"){applyCapabilityGate();nav("overview")}});
