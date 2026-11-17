if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/app/sw.js", {scope: "/app/"}).catch(() => {});
  });
}
const API="/api/platform/v1",$=s=>document.querySelector(s),S={features:{},view:"overview",session:null,task:null,approval:null};
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const json=v=>esc(JSON.stringify(v??{},null,2));
function csrf(){const c=document.cookie.split("; ").find(x=>x.startsWith("__Host-platform_csrf="));return c?decodeURIComponent(c.split("=").slice(1).join("=")):"";}
async function api(path,opt={}){const h={Accept:"application/json",...(opt.headers||{})};if(opt.body&&typeof opt.body==="string")h["Content-Type"]="application/json";if(["POST","PUT","PATCH","DELETE"].includes((opt.method||"GET").toUpperCase()))h["X-CSRF-Token"]=csrf();const r=await fetch(path,{...opt,headers:h,credentials:"same-origin"});if(!r.ok){let m=r.status+" "+r.statusText;try{m=(await r.json()).detail||m}catch{}throw Error(m)}if(r.status===204)return null;const t=r.headers.get("content-type")||"";return t.includes("application/json")?r.json():r;}
async function upload(path,file){const r=await fetch(path,{method:"POST",body:file,headers:{"Content-Type":file.type||"application/octet-stream","X-Filename":encodeURIComponent(file.name),"X-CSRF-Token":csrf()},credentials:"same-origin"});if(!r.ok){let m=r.statusText;try{m=(await r.json()).detail||m}catch{}throw Error(m)}return r.json();}
function toast(t){const e=$("#toast");e.textContent=t;e.classList.add("show");setTimeout(()=>e.classList.remove("show"),2600)}
function table(cols,rows){return "<div class='table-scroll'><table><thead><tr>"+cols.map(x=>"<th>"+x+"</th>").join("")+"</tr></thead><tbody>"+(rows.length?rows.join(""):"<tr><td colspan='"+cols.length+"' class='empty'>暂无记录</td></tr>")+"</tbody></table></div>"}
function panel(t,b,a=""){return "<section class='panel'><div class='panel-head'><h3>"+t+"</h3>"+a+"</div>"+b+"</section>"}
function pre(v){return "<pre class='json'>"+json(v)+"</pre>"}
function btn(t,attr,cls=""){return "<button class='button "+cls+"' "+attr+">"+t+"</button>"}
async function list(p){const r=await api(p);return r.items||[]}
async function refresh(){try{S.me=await api("/auth/me");$("#identity").textContent=S.me.display_name||S.me.email||S.me.user_id;$("#logout").hidden=false;$("#login").hidden=true;$("#content").hidden=false;S.features=(await api(API+"/workbench/capabilities")).features||{};$("#api-state").textContent="API 已连接"}catch(e){S.me=null;$("#identity").textContent="未登录";$("#logout").hidden=true;$("#login").hidden=false;$("#content").hidden=true;$("#api-state").textContent="未认证"}}
function nav(v){if(v!=="task-detail"&&S.runStream){S.runStream.close();S.runStream=null}S.view=v;S.session=null;S.task=null;S.approval=null;document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("active",b.dataset.view===v));const names={overview:"工作台总览",tasks:"任务中心",sessions:"会话与文件",approvals:"人工审批",artifacts:"产物交付"};$("#title").textContent=names[v]||v;$("#crumb").textContent=v.toUpperCase();render()}
async function render(){const root=$("#view");root.innerHTML="<div class='panel'>加载中…</div>";try{
if(S.view==="overview"){const rs=await Promise.allSettled([list(API+"/workbench/tasks?limit=50"),list(API+"/sessions?limit=50"),list(API+"/files?limit=50"),list(API+"/approvals?limit=50")]);const n=rs.map(x=>x.status==="fulfilled"?x.value.length:"—");root.innerHTML="<div class='grid'>"+[["启用能力",Object.values(S.features).filter(Boolean).length],["任务",n[0]],["会话",n[1]],["文件资产",n[2]]].map(x=>"<div class='stat'><small>"+x[0]+"</small><strong>"+x[1]+"</strong></div>").join("")+"</div>"+panel("平台能力",Object.entries(S.features).map(x=>"<span class='pill'>"+esc(x[0])+": "+(x[1]?"启用":"未启用")+"</span>").join(" "))+(rs[3].status==="fulfilled"?panel("待处理审批","<p>当前审批记录："+n[3]+"</p>",btn("打开审批中心","data-go='approvals'")):"")}
else if(S.view==="sessions"){if(!S.features.sessions){root.innerHTML=panel("会话服务未配置","<p>后端尚未启用会话能力。</p>");return}const [ss,fs]=await Promise.all([list(API+"/sessions?limit=100"),list(API+"/files?limit=100")]);root.innerHTML=panel("创建会话","<form id='session-form' class='inline-form'><label>会话标题<input name='title' required maxlength='200'></label><label>项目 ID（可选）<input name='project_id'></label><button class='button primary'>创建会话</button></form>")+panel("会话列表",table(["会话","项目","状态","更新时间","操作"],ss.map(x=>"<tr><td>"+esc(x.title||x.session_id)+"<small class='subline'>"+esc(x.session_id)+"</small></td><td>"+esc(x.project_id||"—")+"</td><td><span class='pill'>"+esc(x.status)+"</span></td><td>"+esc(x.updated_at)+"</td><td>"+btn("打开","data-session='"+esc(x.session_id)+"'")+"</td></tr>")))+panel("文件资产",table(["文件","大小","会话","版本","操作"],fs.map(x=>"<tr><td>"+esc(x.filename)+"</td><td>"+esc(x.size_bytes)+" bytes</td><td>"+esc(x.session_id||"—")+"</td><td>"+esc(x.version)+"</td><td>"+btn("下载","data-download='"+esc(x.asset_id)+"'")+"</td></tr>")))}
else if(S.view==="session-detail"){await renderSession(S.session);return}
else if(S.view==="tasks"){if(!S.features.task_execution){root.innerHTML=panel("任务执行未配置","<p>服务端未启用 Task Center；工作台不会绕过 Runtime 启动 Agent。</p>");return}const [ts,ss]=await Promise.all([list(API+"/workbench/tasks?limit=100"),list(API+"/sessions?limit=100")]);root.innerHTML=panel("提交任务","<form id='task-form'><div class='form-grid'><label>Agent ID<input name='agent_id' required></label><label>关联会话<select name='session_id'><option value=''>不关联</option>"+ss.map(x=>"<option value='"+esc(x.session_id)+"'>"+esc(x.title||x.session_id)+"</option>").join("")+"</select></label><label>Project ID<input name='project_id'></label><label>优先级<input name='priority' type='number' min='0' max='1000' value='100'></label></div><label>任务输入（JSON 对象）<textarea name='input' required>{\"prompt\":\"请处理任务\"}</textarea></label><button class='button primary'>提交任务</button></form>")+panel("任务队列",table(["任务","Agent","状态","Run","更新时间","操作"],ts.map(x=>"<tr><td>"+esc(x.task_id)+"</td><td>"+esc(x.agent_id)+"</td><td><span class='pill'>"+esc(x.status)+"</span></td><td>"+esc(x.run_id||"—")+"</td><td>"+esc(x.updated_at||x.created_at||"—")+"</td><td>"+btn("详情","data-task='"+esc(x.task_id)+"'")+"</td></tr>")))}
else if(S.view==="task-detail"){await renderTask(S.task);return}
else if(S.view==="approvals"){if(!S.features.approvals){root.innerHTML=panel("审批服务未配置","<p>后端未启用人工审批服务。</p>");return}const as=await list(API+"/approvals?limit=100");root.innerHTML=panel("人工审批队列","<p class='subtle'>决定由服务端执行权限校验、过期检查与状态转换。</p>"+table(["审批","Run","动作","资源","风险","申请人","状态","操作"],as.map(x=>"<tr><td>"+esc(x.approval_id)+"</td><td>"+esc(x.run_id||"—")+"</td><td>"+esc(x.action)+"</td><td>"+esc(x.resource)+"</td><td>"+esc(x.risk_level)+"</td><td>"+esc(x.requested_by)+"</td><td>"+esc(x.status)+"</td><td>"+btn("查看","data-approval='"+esc(x.approval_id)+"'")+"</td></tr>")))}
else if(S.view==="approval-detail"){await renderApproval(S.approval);return}
else if(S.view==="artifacts"){const fs=await list(API+"/files?limit=200");root.innerHTML=panel("交付产物","<p class='subtle'>文件资产经平台权限校验下载；可关联会话和任务。</p>"+table(["文件","媒体类型","大小","会话","版本","操作"],fs.map(x=>"<tr><td>"+esc(x.filename)+"<small class='subline'>"+esc(x.asset_id)+"</small></td><td>"+esc(x.media_type)+"</td><td>"+esc(x.size_bytes)+" bytes</td><td>"+esc(x.session_id||"—")+"</td><td>"+esc(x.version)+"</td><td>"+btn("下载","data-download='"+esc(x.asset_id)+"'")+"</td></tr>")))}
}catch(e){root.innerHTML=panel("数据加载失败","<p class='error'>"+esc(e.message)+"</p>"+btn("重试","data-retry='1'"))}}
async function renderSession(id){if(!id){nav("sessions");return}const [ss,ms,fs]=await Promise.all([list(API+"/sessions?limit=200"),list(API+"/sessions/"+encodeURIComponent(id)+"/messages?limit=200"),list(API+"/files?session_id="+encodeURIComponent(id)+"&limit=100")]);const s=ss.find(x=>x.session_id===id)||{session_id:id};$("#title").textContent=s.title||"会话详情";$("#crumb").textContent="SESSION DETAIL";$("#view").innerHTML=panel("会话信息","<p>会话 ID："+esc(id)+"</p><p>项目："+esc(s.project_id||"—")+" · 状态："+esc(s.status||"—")+"</p>",btn("返回会话列表","data-go='sessions'"))+panel("消息记录",ms.map(m=>"<article class='message'><header><b>"+esc(m.role)+"</b><small>"+esc(m.created_at)+"</small></header><p>"+esc(m.content)+"</p></article>").join("")||"<p class='empty'>暂无消息</p>")+panel("发送消息","<form id='message-form'><label>消息内容<textarea name='content' required></textarea></label><button class='button primary'>发送</button></form>")+panel("上传附件","<form id='upload-form'><input type='file' name='file' required><button class='button'>上传到此会话</button></form>")+panel("会话文件",table(["文件","大小","版本","操作"],fs.map(f=>"<tr><td>"+esc(f.filename)+"</td><td>"+esc(f.size_bytes)+" bytes</td><td>"+esc(f.version)+"</td><td>"+btn("下载","data-download='"+esc(f.asset_id)+"'")+"</td></tr>")))}
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
   run="<p>Run："+esc(runId)+" · 状态："+esc(r.status)+" · 版本："+esc(r.version)+"</p>"+
    pre(r)+btn("查看 Run 事件","data-run-events='"+esc(runId)+"'")+
    "<p id='run-stream-status' class='subtle'>准备连接实时事件…</p>"+
    "<pre id='run-stream-output' class='json' aria-live='polite'></pre>"+
    "<div id='run-stream-events' class='subtle' aria-live='polite'></div>";
  }catch(e){run="<p class='error'>Run 详情不可用："+esc(e.message)+"</p>"}
 }
 const arts=t.artifacts||t.result?.artifacts||t.output?.artifacts||[];
 $("#title").textContent="任务详情";$("#crumb").textContent="TASK DETAIL";
 $("#view").innerHTML=panel("执行状态","<p>任务："+esc(id)+"</p><p>Agent："+esc(t.agent_id)+" · 状态："+esc(t.status)+"</p>"+pre(t),btn("返回任务列表","data-go='tasks'"))+
  panel("运行详情",run)+
  panel("执行产物",arts.length?arts.map(a=>"<p>"+esc(a.filename||a.name||a.asset_id||a.uri)+"</p>").join(""):"<p class='subtle'>任务未直接声明产物；请在产物交付页查看已上传文件。</p>")+
  panel("任务操作",btn("取消任务","data-task-cancel='"+esc(id)+"'")+btn("重试任务","data-task-retry='"+esc(id)+"'"));
 if(runId)connectRunStream(runId);
}
async function renderApproval(id){if(!id){nav("approvals");return}const a=await api(API+"/approvals/"+encodeURIComponent(id));S.currentApproval=a;$("#title").textContent="审批详情";$("#crumb").textContent="APPROVAL DETAIL";$("#view").innerHTML=panel("审批请求",pre(a),btn("返回审批队列","data-go='approvals'"))+(a.status==="pending"?panel("人工决策","<p>核对动作、资源和请求信息后作出决定。决策由服务端验证权限和状态。</p>"+btn("批准","data-decision='true'","primary")+btn("拒绝","data-decision='false'")):"")}
document.addEventListener("click",async e=>{const b=e.target.closest("button");if(!b)return;try{if(b.dataset.view){nav(b.dataset.view);return}if(b.dataset.go){nav(b.dataset.go);return}if(b.dataset.session){S.view="session-detail";S.session=b.dataset.session;await renderSession(S.session);return}if(b.dataset.task){S.view="task-detail";S.task=b.dataset.task;await renderTask(S.task);return}if(b.dataset.approval){S.view="approval-detail";S.approval=b.dataset.approval;await renderApproval(S.approval);return}if(b.dataset.download){const r=await fetch(API+"/files/"+encodeURIComponent(b.dataset.download),{credentials:"same-origin"});if(!r.ok)throw Error("文件下载失败："+r.status);const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="artifact";a.click();URL.revokeObjectURL(url);return}if(b.dataset.decision!==undefined){const yes=b.dataset.decision==="true";await api(API+"/approvals/"+encodeURIComponent(S.approval)+"/decision",{method:"POST",body:JSON.stringify({approved:yes})});toast(yes?"审批已批准":"审批已拒绝");nav("approvals");return}if(b.dataset.taskCancel){await api(API+"/workbench/tasks/"+encodeURIComponent(b.dataset.taskCancel)+"/cancel",{method:"POST",body:"{}"});toast("取消请求已提交");await renderTask(b.dataset.taskCancel);return}if(b.dataset.taskRetry){await api(API+"/workbench/tasks/"+encodeURIComponent(b.dataset.taskRetry)+"/retry",{method:"POST",body:JSON.stringify({idempotency_key:crypto.randomUUID()})});toast("重试请求已提交");await renderTask(b.dataset.taskRetry);return}if(b.dataset.runEvents){const ev=await list(API+"/runs/"+encodeURIComponent(b.dataset.runEvents)+"/events");$("#view").insertAdjacentHTML("beforeend",panel("Run 事件",pre(ev)));return}if(b.dataset.retry){render();return}}catch(x){toast(x.message)}});
document.addEventListener("submit",async e=>{const f=e.target;if(f.id==="login-form"){e.preventDefault();const d=new FormData(f);try{await api("/auth/login",{method:"POST",body:JSON.stringify({tenant_id:d.get("tenant_id"),email:d.get("email"),password:d.get("password")})});await refresh();if(S.me)nav("overview")}catch(x){$("#login-error").textContent=x.message}return}if(f.id==="session-form"){e.preventDefault();const d=new FormData(f);try{const r=await api(API+"/sessions",{method:"POST",body:JSON.stringify({title:d.get("title"),project_id:d.get("project_id")||null})});toast("会话已创建");S.session=r.session_id;S.view="session-detail";await renderSession(S.session)}catch(x){toast(x.message)}return}if(f.id==="message-form"){e.preventDefault();const d=new FormData(f);try{await api(API+"/sessions/"+encodeURIComponent(S.session)+"/messages",{method:"POST",body:JSON.stringify({role:"user",content:d.get("content")})});toast("消息已发送");await renderSession(S.session)}catch(x){toast(x.message)}return}if(f.id==="upload-form"){e.preventDefault();const file=f.elements.file.files[0];if(!file)return;try{await upload(API+"/files?session_id="+encodeURIComponent(S.session),file);toast("文件已上传");await renderSession(S.session)}catch(x){toast(x.message)}return}if(f.id==="task-form"){e.preventDefault();const d=new FormData(f);let input;try{input=JSON.parse(d.get("input"))}catch{toast("任务输入必须是合法 JSON");return}if(!input||Array.isArray(input)||typeof input!=="object"||!Object.keys(input).length){toast("任务输入必须是非空 JSON 对象");return}try{const r=await api(API+"/workbench/tasks",{method:"POST",body:JSON.stringify({agent_id:d.get("agent_id"),project_id:d.get("project_id")||null,session_id:d.get("session_id")||null,priority:Number(d.get("priority")||100),input})});toast("任务已提交");if(r.task_id){S.task=r.task_id;S.view="task-detail";await renderTask(r.task_id)}else nav("tasks")}catch(x){toast(x.message)}}});
$("#logout").addEventListener("click",async()=>{try{await api("/auth/logout",{method:"POST",body:"{}"})}catch{}await refresh()});refresh().then(()=>{if(S.me)nav("overview")});

const REGISTRY_KINDS=["agent","workflow","model_provider","model_template","tool","skill"];
const REGISTRY_LABELS={agent:"Agent",workflow:"工作流",model_provider:"模型供应商",model_template:"模型模板",tool:"工具",skill:"Skill"};
const registryPath=kind=>API+"/registry/"+encodeURIComponent(kind);
async function renderRegistry(){
 const kind=S.registryKind||"agent";
 $("#title").textContent="Agent / 工作流 / 模型 / 工具 / Skills";
 $("#crumb").textContent="RESOURCE REGISTRY";
 const items=await list(registryPath(kind));
 const selector="<label>资源类型<select id='registry-kind'>"+REGISTRY_KINDS.map(k=>"<option value='"+k+"' "+(k===kind?"selected":"")+">"+REGISTRY_LABELS[k]+"</option>").join("")+"</select></label>";
 const form="<form id='registry-create' class='registry-form'><div class='form-grid'><label>资源 ID<input name='item_id' required maxlength='128' pattern='[A-Za-z0-9][A-Za-z0-9._:-]{0,127}' placeholder='例如：support-agent'></label><label>资源定义（JSON）<textarea name='definition' required>{\n  \"name\": \"New resource\",\n  \"description\": \"Describe its purpose\"\n}</textarea></label></div><button class='button primary'>创建草稿版本</button></form>";
 const rows=items.map(x=>"<tr><td><b>"+esc(x.item_id)+"</b></td><td>"+esc(x.version)+"</td><td><span class='pill'>"+esc(x.status)+"</span></td><td>"+esc(x.created_by)+"</td><td>"+esc(x.created_at)+"</td><td>"+btn("版本历史","data-reg-versions='"+esc(x.item_id)+"'")+((x.status==="draft")?btn("发布","data-reg-publish='"+esc(x.item_id)+"' data-version='"+esc(x.version)+"'","primary"):"")+((x.status==="published")?btn("停用","data-reg-disable='"+esc(x.item_id)+"'"):"")+"</td></tr>");
 $("#view").innerHTML=panel("资源类型",selector)+panel("新建资源草稿",form)+panel("资源目录",table(["资源 ID","最新版本","状态","创建人","创建时间","管理"],rows))+
 panel("权限与安全边界","<p>资源目录、草稿写入、发布和停用分别由后端 Registry Authorizer 校验。所有资源按当前登录租户隔离；无权限时服务端返回 403。定义仅保存声明式元数据，密钥必须使用 Secret Reference，工具执行代码不从定义中加载。</p>");
}
const registryBaseRender=render;
render=async function(){if(S.view==="registry"){try{await renderRegistry()}catch(e){$("#view").innerHTML=panel("资源管理不可用","<p class='error'>"+esc(e.message)+"</p>")}return}return registryBaseRender()};
document.addEventListener("change",e=>{if(e.target.id==="registry-kind"){S.registryKind=e.target.value;renderRegistry().catch(x=>toast(x.message))}});
document.addEventListener("submit",async e=>{const f=e.target;if(f.id!=="registry-create")return;e.preventDefault();const d=new FormData(f);let definition;try{definition=JSON.parse(d.get("definition"))}catch{toast("资源定义必须是合法 JSON");return}if(!definition||Array.isArray(definition)||typeof definition!=="object"){toast("资源定义必须是 JSON 对象");return}try{const kind=S.registryKind||"agent";await api(registryPath(kind)+"/"+encodeURIComponent(d.get("item_id"))+"/versions",{method:"POST",body:JSON.stringify({definition})});toast("草稿已创建");await renderRegistry()}catch(x){toast(x.message)}});
document.addEventListener("click",async e=>{const b=e.target.closest("button");if(!b)return;try{if(b.dataset.regVersions){const kind=S.registryKind||"agent",id=b.dataset.regVersions,versions=await list(registryPath(kind)+"/"+encodeURIComponent(id)+"/versions");$("#view").insertAdjacentHTML("beforeend",panel("版本历史 · "+esc(id),table(["版本","状态","创建人","创建时间","发布时间"],versions.map(v=>"<tr><td>"+esc(v.version)+"</td><td>"+esc(v.status)+"</td><td>"+esc(v.created_by)+"</td><td>"+esc(v.created_at)+"</td><td>"+esc(v.published_at||"—")+"</td></tr>"))));return}if(b.dataset.regPublish){const kind=S.registryKind||"agent";await api(registryPath(kind)+"/"+encodeURIComponent(b.dataset.regPublish)+"/versions/"+encodeURIComponent(b.dataset.version)+"/publish",{method:"POST",body:"{}"});toast("资源版本已发布并设为 active");await renderRegistry();return}if(b.dataset.regDisable){const kind=S.registryKind||"agent";await api(registryPath(kind)+"/"+encodeURIComponent(b.dataset.regDisable)+"/disable",{method:"POST",body:"{}"});toast("资源已停用");await renderRegistry();return}}catch(x){toast(x.message)}});


// C-14-04: tenant-scoped knowledge, memory, collaboration and operations views.
const governanceBaseRender=render;
const isoWindow=()=>{const end=new Date(),start=new Date(end.getTime()-30*86400000);return {start:start.toISOString(),end:end.toISOString()}};
const rowsPanel=(title,items,fields)=>panel(title,table(fields,items.map(item=>"<tr>"+fields.map(k=>"<td>"+esc(item[k]??"—")+"</td>").join("")+"</tr>")));
async function governanceLoad(path){return list(API+path)}
async function renderGovernance(){
 const root=$("#view");$("#title").textContent="知识、记忆与平台运营";$("#crumb").textContent="GOVERNANCE & OPERATIONS";
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
"<form id='knowledge-collection-create' class='inline-form'><label>新建知识库集合<input name='name' required maxlength='200' placeholder='集合名称'></label><label>项目 ID（可选）<input name='project_id' placeholder='project-a'></label><button class='button primary'>创建集合</button></form>"+
rowsPanel("集合目录",data.collections?.items||[],["collection_id","name","project_id","created_at"])+
"<form id='knowledge-index-create' class='inline-form'><label>集合 ID<input name='collection_id' required></label><label>索引名称<input name='index_name' required></label><label>Embedding 模型<input name='embedding_model' required></label><label>向量维度<input name='dimensions' type='number' min='1' max='65536' value='1536' required></label><label>Schema 版本<input name='schema_version' type='number' min='1' value='1' required></label><button class='button'>创建索引版本</button></form>"+
rowsPanel("文档目录",knowledge,["document_id","title","version","scope","project_id","media_type"]))+
  panel("记忆管理",rowsPanel("记忆记录",memories,["memory_id","scope","scope_id","content","confidence","version","status","expires_at"])+rowsPanel("待审核候选与冲突",candidates,["memory_id","scope","scope_id","content","confidence","status","version"]))+
  panel("Blackboard 协作视图","<form id='blackboard-query' class='inline-form'><label>范围<select name='scope'><option value='tenant'>Tenant</option><option value='project'>Project</option><option value='run'>Run</option><option value='task'>Task</option></select></label><label>范围 ID<input name='scope_id' required value='"+scopeId+"' placeholder='Tenant / Project / Run / Task ID'></label><button class='button primary'>查询条目</button></form><div id='blackboard-results'></div>")+
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
 $("#blackboard-results").innerHTML=rowsPanel("Blackboard 条目",result.items||[],["entry_id","kind","status","version","author_id","created_at","content"]);
 }catch(x){$("#blackboard-results").innerHTML="<p class='error'>"+esc(x.message)+"</p>"}
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
 const root=$("#view");$("#title").textContent="Prompt Lab · 版本管理";$("#crumb").textContent="PROMPT LAB";
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
   (Number(v.version)>1?btn("Diff","data-prompt-diff='"+esc(v.version)+"'"):"")+
   btn("发布","data-prompt-activate='"+esc(v.version)+"'")+
   btn("回滚","data-prompt-rollback='"+esc(v.version)+"'");
  return "<tr><td>v"+esc(v.version)+"</td><td><code>"+esc(v.content_hash.slice(0,12))+"</code></td><td>"+esc((v.variables||[]).join(", ")||"—")+"</td><td>"+actions+"</td></tr>";
 });
 const variantRows=(variants.items||[]).map(v=>{
  const actions=btn("历史","data-prompt-history='"+esc(v.name)+"'")+
   btn("设版本","data-prompt-assign='"+esc(v.name)+"'")+
   (v.status==="active"?btn("废弃","data-prompt-deprecate='"+esc(v.name)+"'"):"");
  return "<tr><td>"+esc(v.name)+"</td><td>v"+esc(v.current_version)+"</td><td>"+esc(v.status)+"</td><td>"+actions+"</td></tr>";
 });
 $("#prompt-versions").innerHTML=panel("不可变版本",table(["版本","内容哈希","变量","操作"],versionRows))+
  panel("创建 Variant","<form id='prompt-variant-form' class='inline-form'><label>Variant 名称<input name='name' required pattern='[a-z][a-z0-9_-]{0,63}' placeholder='candidate'></label><label>初始版本<input name='version' required type='number' min='1' value='"+esc((versions.items||[]).at(-1)?.version||1)+"'></label><button class='button primary'>创建 Variant</button></form>")+
  panel("Variants",table(["名称","当前版本","状态","操作"],variantRows))+
  panel("变更审计",table(["时间","动作","操作者","详情"],(audit.items||[]).map(x=>"<tr><td>"+esc(x.created_at||"—")+"</td><td>"+esc(x.action)+"</td><td>"+esc(x.actor_id)+"</td><td>"+esc(JSON.stringify(x.details||{}))+"</td></tr>")));
}
const promptLabBaseRender=render;
render=async function(){
 if(S.view==="prompt-lab"){
  try{await renderPromptLab()}catch(e){$("#view").innerHTML=panel("Prompt Lab 加载失败","<p class='error'>"+esc(e.message)+"</p>")}
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
 $("#title").textContent="Run 时间线与回放";$("#crumb").textContent="WORKFLOW REPLAY";
 if(!r){
  root.innerHTML=panel("打开 Run 回放","<form id='replay-open-form' class='inline-form'><label>Run ID<input name='run_id' required placeholder='输入已有 Run ID'></label><button class='button primary'>加载时间线</button></form><p class='subtle'>从持久化 Run 事件、上下文快照和 Blackboard 数据重建视图；所有读取与回放操作继续由服务端租户权限校验。</p>");
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
 $("#crumb").textContent="BATCH EXECUTION";
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
  return "<tr><td><code>"+esc(id)+"</code></td><td>"+esc(x.template_id||"—")+"</td><td><span class='pill'>"+esc(x.status||"unknown")+"</span></td><td>"+esc(x.total_tasks??x.total??"—")+"</td><td>"+esc(x.succeeded??x.success_count??"—")+"</td><td>"+esc(x.failed??x.failure_count??"—")+"</td><td>"+esc(x.created_at||"—")+"</td><td>"+actions+"</td></tr>";
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
   "<div class='form-grid'><label>模板 ID<input name='template_id' required maxlength='128' placeholder='已授权的 Agent / Workflow 模板'></label>"+
   "<label>最大并发<input name='max_concurrency' type='number' min='1' max='1000' value='8' required></label></div>"+
   "<label>数据集（JSON 数组；每项为输入对象）<textarea name='dataset' rows='8' required>[{\"prompt\":\"第一项任务\"},{\"prompt\":\"第二项任务\"}]</textarea></label>"+
   "<p class='subtle'>提交使用服务端 BatchTaskService，tenant/actor 从认证 Principal 派生；请求附带幂等键。数据集上限由 API 执行。</p>"+
   "<button class='button primary'>提交批次</button></form>")+
  detail+
  panel("批次队列",table(["Batch ID","模板","状态","任务数","成功","失败","创建时间","操作"],rows))+
  "<p class='subtle'>批次列表、详情、取消和重试均通过同源认证 API；重试生成新的幂等键，权限由服务端再次验证。</p>";
}
async function renderObservability(){
 const root=$("#view");
 $("#title").textContent="运行可观测性";
 $("#crumb").textContent="OBSERVABILITY";
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
  return "<tr><td>"+esc(id)+"</td><td>"+esc(x.severity||"—")+"</td><td>"+esc(x.status||"—")+"</td><td>"+esc(x.title||"—")+"</td><td>"+esc(x.created_at||"—")+"</td><td>"+actions+"</td></tr>";
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
