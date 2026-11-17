// Skill lifecycle view for the platform workbench. Backend authorization remains authoritative.
(()=>{
 const API="/api/platform/v1",q=s=>document.querySelector(s);
 const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 const csrf=()=>{const c=document.cookie.split("; ").find(x=>x.startsWith("__Host-platform_csrf="));return c?decodeURIComponent(c.split("=").slice(1).join("=")):""};
 async function call(path,method="GET",body){
  const headers={Accept:"application/json","X-CSRF-Token":csrf()};
  if(body!==undefined)headers["Content-Type"]="application/json";
  const r=await fetch(API+path,{method,headers,credentials:"same-origin",body:body===undefined?undefined:JSON.stringify(body)});
  if(!r.ok){let m=r.status+" "+r.statusText;try{m=(await r.json()).detail||m}catch{}throw Error(m)}
  return r.status===204?null:r.json();
 }
 const agent=()=>String(q("#skill-agent")?.value||"").trim();
 async function renderSkills(){
  const root=q("#view");root.innerHTML="<section class='panel'>加载 Skill Registry…</section>";
  try{
   const id=agent();if(!id){root.innerHTML="<section class='panel'><h3>Agent 级 Skill 管理</h3><p>输入 Agent ID 后查看可安装 Skill。</p><form id='skill-agent-form' class='inline-form'><label>Agent ID<input id='skill-agent' name='agent_id' required></label><button class='button primary'>加载</button></form></section>";return}
   const data=await call("/skills?agent_id="+encodeURIComponent(id));
   const rows=(data.items||[]).map(x=>"<tr><td><b>"+esc(x.name)+"</b><small class='subline'>"+esc(x.skill_id)+"</small></td><td>"+esc(x.description)+"</td><td>"+esc(x.package_version)+"</td><td><span class='pill'>"+esc(x.status)+"</span></td><td><input aria-label='Registry version' type='number' min='1' value='"+esc(x.available_version||x.registry_version)+"' data-skill-version='"+esc(x.skill_id)+"' style='width:86px'></td><td>"+(x.status==="not_installed"?"<button class='button primary' data-skill-install='"+esc(x.skill_id)+"'>安装</button>": "<button class='button' data-skill-upgrade='"+esc(x.skill_id)+"'>升级</button> <button class='button' data-skill-uninstall='"+esc(x.skill_id)+"'>卸载</button>")+"</td></tr>").join("");
   root.innerHTML="<section class='panel'><div class='panel-head'><h3>Agent 级 Skill 管理</h3></div><form id='skill-agent-form' class='inline-form'><label>Agent ID<input id='skill-agent' name='agent_id' required value='"+esc(id)+"'></label><button class='button'>切换 Agent</button></form><p class='subtle'>安装、升级和卸载操作均由后端按当前租户与 Agent 权限校验；运行时由隔离 Skill runtime 执行。</p></section><section class='panel'><h3>已发布 Skill</h3><div class='table-scroll'><table><thead><tr><th>Skill</th><th>说明</th><th>包版本</th><th>状态</th><th>Registry 版本</th><th>操作</th></tr></thead><tbody>"+(rows||"<tr><td colspan='6' class='empty'>暂无已发布 Skill</td></tr>")+"</tbody></table></div></section>";
  }catch(e){root.innerHTML="<section class='panel'><h3>Skill 加载失败</h3><p class='error'>"+esc(e.message)+"</p><button class='button' data-skill-refresh>重试</button></section>"}
 }
 const baseNav=window.nav;
 document.addEventListener("click",async e=>{
  const b=e.target.closest("[data-view='skills'],[data-skill-install],[data-skill-uninstall],[data-skill-upgrade],[data-skill-refresh]");
  if(!b)return;
  if(b.matches("[data-view='skills']")){setTimeout(renderSkills,0);return}
  if(b.hasAttribute("data-skill-refresh")){renderSkills();return}
  const id=agent(),skill=b.getAttribute("data-skill-install")||b.getAttribute("data-skill-uninstall")||b.getAttribute("data-skill-upgrade");
  const field=document.querySelector("[data-skill-version='"+CSS.escape(skill)+"']");
  const version=Number(field?.value);
  try{
   if(b.hasAttribute("data-skill-install"))await call("/skills/"+encodeURIComponent(skill)+"/install","POST",{agent_id:id,version});
   else if(b.hasAttribute("data-skill-upgrade"))await call("/skills/"+encodeURIComponent(skill)+"/upgrade","POST",{agent_id:id,version});
   else await call("/skills/"+encodeURIComponent(skill)+"/install?agent_id="+encodeURIComponent(id),"DELETE");
   if(window.toast)window.toast("Skill 操作成功");await renderSkills();
  }catch(x){if(window.toast)window.toast(x.message);else alert(x.message)}
 });
 document.addEventListener("submit",e=>{
  if(e.target.id!=="skill-agent-form")return;
  e.preventDefault();renderSkills();
 });
 // Add a direct entry point for capability-gated navigation.
 window.addEventListener("load",()=>{const button=document.querySelector("nav [data-view='skills']");if(button&&!button.hidden)button.addEventListener("click",()=>setTimeout(renderSkills,0))});
})();
