"use strict";
const base = document.querySelector('meta[name="platform-base"]').content;
const content = document.getElementById("content"), notice = document.getElementById("notice");
let me = null, platform = null, dirty = false, routeGeneration = 0, releasePoll = null;
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const esc = escapeHTML;
const date = value => value ? new Date(value * 1000).toLocaleString("zh-CN", {hour12:false}) : "—";
const formData = form => Object.fromEntries(new FormData(form));
const operationId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join("");
const csrf = () => decodeURIComponent(document.cookie.split("; ").find(x => x.startsWith("cowork_csrf="))?.split("=").slice(1).join("=") || "");
const authNames={database:"平台账号密码",oauth2:"OAuth2",oidc:"OIDC",ldap:"LDAP"};
const statusNames = {draft:"草稿",processing:"处理中",failed:"失败",ready:"可发布",published:"已发布",withdrawn:"已撤下",deleted:"已删除"};
function message(text, kind="") {notice.textContent=text; notice.className=kind;}
async function api(path, options={}) {
  const generation=routeGeneration;
  const headers = {"X-CSRF-Token":csrf(), ...options.headers};
  if (options.body && typeof options.body !== "string") {headers["Content-Type"]="application/json"; options.body=JSON.stringify(options.body);}
  let response;
  try {response=await fetch(base+"/api/v1"+path, {...options,headers,credentials:"same-origin"});}
  catch {throw new Error("无法连接中心服务，输入已保留，请检查网络后重试。");}
  const data=await response.json().catch(()=>({}));
  if(generation!==routeGeneration)throw new DOMException("页面已切换","AbortError");
  if (!response.ok) {
    const error = new Error(data.error?.message || `请求失败（${response.status}），输入已保留。`);
    error.status = response.status;
    error.code = data.error?.code;
    throw error;
  }
  return data;
}
async function action(work) {
  try { message(""); await work(); }
  catch (error) {
    if (error.name === "AbortError") return;
    if (me && error.code === "authentication_required") {
      me = null;
      dirty = false;
      document.getElementById("detail").close();
      content.replaceChildren();
      try { await render(); }
      catch (loginError) { message(loginError.message, "error"); return; }
    }
    message(error.message, "error");
  }
}
function button(label, callback, {primary=false,danger=false}={}) {const b=document.createElement("button"); b.type="button"; b.textContent=label; b.className=primary?"primary":danger?"danger":""; b.onclick=()=>action(callback); return b;}
function heading(title, subtitle="", back="") {content.innerHTML=`<div class="page-title"><div><h1>${esc(title)}</h1>${subtitle?`<p>${esc(subtitle)}</p>`:""}</div>${back?`<a href="${base+back}" data-route>返回列表</a>`:""}</div>`;}
function empty(text) {return `<div class="empty">${esc(text)}</div>`;}
function watchForm(form) {const changed=event=>{if(!event.target.name?.startsWith("test_"))dirty=true;};form.addEventListener("input",changed);form.addEventListener("change",changed);}
function adminSplit(list, summary){
  return '<div class="admin-split"><div class="admin-overview">' + list + '</div><aside class="admin-detail" aria-label="选中项详情">' + summary + '</aside></div>';
}
function adminColumns(...names){
  return '<div class="admin-columns">' + names.map(name => '<span>' + esc(name) + '</span>').join("") + '</div>';
}
function adminFields(pairs){
  return '<dl class="admin-fields">' + pairs.map(([name, value]) => '<dt>' + esc(name) + '</dt><dd>' + esc(value) + '</dd>').join("") + '</dl>';
}
function adminRow(path, selected, name, status, scope, tone=""){
  return '<a class="admin-row" data-route href="' + esc(base + path) + '"' + (selected ? ' aria-current="page"' : '') +
    '><span>' + esc(name) + '</span><span class="admin-status ' + tone + '">' + esc(status) + '</span><span>' + esc(scope) + '</span></a>';
}
function adminViewLink(path, label, primary=false){
  return '<a class="button' + (primary ? ' primary' : '') + '" data-route href="' + esc(base + path) + '">' + esc(label) + '</a>';
}
function detail(title,text) {document.getElementById("detail-title").textContent=title; const el=document.getElementById("detail-body"); el.replaceChildren(); const pre=document.createElement("pre"); pre.textContent=text; el.append(pre); document.getElementById("detail").showModal();}
document.getElementById("close-detail").onclick=()=>document.getElementById("detail").close();
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
document.addEventListener("click",event=>{const link=event.target.closest("a[data-route]");if(link){event.preventDefault();navigate(link.getAttribute("href").slice(base.length));}});
window.addEventListener("popstate",()=>{if(dirty&&!confirm("有尚未保存的修改，确定离开？")){history.forward();return;}dirty=false;action(render);});
function navigate(path){if(dirty&&!confirm("有尚未保存的修改，确定离开？"))return;dirty=false;history.pushState({},"",base+path);action(render);}
function updateChrome(){
  const nav = document.getElementById("navigation"), identity = document.getElementById("identity");
  identity.replaceChildren();
  nav.hidden = !me;
  if (!me) {
    nav.replaceChildren();
    return;
  }
  const name = me.member.name;
  const role = me.can_assign_admin ? "超级管理员" : "平台管理员";
  identity.innerHTML = `<span class="admin-avatar" aria-hidden="true">${esc(Array.from(name)[0])}</span>
    <span class="admin-person"><strong>${esc(name)}</strong><small>${role}</small></span>`;
  identity.append(button("退出", async () => {
    await api("/auth/logout", {method:"POST"});
    me = null;
    dirty = false;
    navigate("/app");
  }));
  const active = location.pathname.slice(base.length).split("/")[2] || "members";
  const links = [["members","成员"],["models","模型"],["capabilities","企业能力"],["audit","审计"],
    ["releases","客户端发布"],["settings","设置"]];
  nav.innerHTML = links.map(([key,label]) => {
    const selected = active === key || (key === "settings" && ["sources", "team-spaces"].includes(active));
    return `<a data-route ${selected?'class="active" aria-current="page"':""} href="${base}/app/${key}">${label}</a>`;
  }).join("");
}
async function login(){
  const setup=await api("/setup");
  if(setup.required)return administratorForm(true);
  const data=await api("/auth/providers");
  if(setup.administrator_login && (location.pathname.endsWith("/administrator") || !data.items.length)) {
    return administratorForm(false, data.items.length > 0);
  }
  content.innerHTML = `<section class="login"><h1>登录管理后台</h1><form id="login-form" class="form">
    <label ${data.items.length < 2 ? "hidden" : ""}>认证方式<select name="provider_id">${data.items.map(source =>
      `<option value="${esc(source.id)}">${esc(source.name)}${source.verified?"":" · 待管理员验证"}</option>`).join("")}</select></label>
    <div id="ldap-fields" hidden><label>账号<input name="username" autocomplete="username"></label>
    <label>密码<input type="password" name="password" autocomplete="current-password"></label></div>
    <button class="primary" ${data.items.length?"":"disabled"}>通过企业账号登录</button>
    ${data.items.length?"":'<p class="muted">尚未配置企业认证来源。</p>'}</form>
    <div class="login-links">${setup.administrator_login?`<a href="${base}/app/administrator" data-route>超级管理员登录</a>`:""}
    <a href="${base}/downloads" data-route>下载客户端</a></div></section>`;
  const form=document.getElementById("login-form"),select=form.elements.provider_id;
  const toggle=()=>{document.getElementById("ldap-fields").hidden=data.items.find(x=>x.id===select.value)?.mode!=="ldap";}; select.onchange=toggle;toggle();
  form.onsubmit=event=>{event.preventDefault();action(async()=>{const values=formData(form),source=data.items.find(x=>x.id===values.provider_id);form.querySelector("button").disabled=true;try{if(source.mode==="ldap"){await api("/auth/ldap",{method:"POST",body:{...values,mode:"web"}});me=await api("/me");dirty=false;await render();}else{const result=await api("/auth/transactions",{method:"POST",body:{provider_id:values.provider_id,mode:"web"}});location.assign(result.login_url);}}finally{form.querySelector("button").disabled=false;}});};
}
function administratorForm(setup, hasCorporateLogin=false){
  content.innerHTML=`<section class="login"><h1>${setup?"创建超级管理员":"超级管理员登录"}</h1><form id="administrator-form" class="form"><label>管理员账号<input required name="username" autocomplete="username" maxlength="64" ${setup?'minlength="3" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}" placeholder="例如 admin"':""}></label><label>密码<input required type="password" name="password" autocomplete="${setup?"new-password":"current-password"}" maxlength="256" ${setup?'minlength="12" placeholder="至少 12 个字符"':""}></label>${setup?'<label>确认密码<input required type="password" name="confirmation" autocomplete="new-password" minlength="12" maxlength="256"></label>':""}<button class="primary">${setup?"创建管理员并配置认证":"登录管理后台"}</button>${hasCorporateLogin?`<a href="${base}/app" data-route>返回企业账号登录</a>`:""}</form></section>`;
  const form=document.getElementById("administrator-form");watchForm(form);
  form.onsubmit=event=>{event.preventDefault();action(async()=>{const values=formData(form);if(setup&&values.password!==values.confirmation)throw new Error("两次输入的密码不一致，请核对。");const submit=form.querySelector("button");submit.disabled=true;try{await api(setup?"/setup":"/auth/administrator",{method:"POST",body:values});dirty=false;me=await api("/me");navigate("/app/settings");}finally{submit.disabled=false;}});};
}
const superMember = member => member.is_super_admin || member.provider_id==="__platform_admin__";
const memberRole = member => superMember(member)?"超级管理员":member.role==="admin"?"平台管理员":"普通员工";
const memberAvatar = member => `<span class="admin-avatar" aria-hidden="true">${esc(Array.from(member.name||member.username||"员")[0])}</span>`;
const memberSearch = () => content.querySelector("#member-search")?.value ?? new URLSearchParams(location.hash.slice(1)).get("member") ?? "";
function memberPath(id="",panel="",query=memberSearch()){
  return "/app/members"+(id?"/"+encodeURIComponent(id)+(panel?"/"+panel:""):"")+(query?"#"+new URLSearchParams({member:query}):"");
}
const memberLink = (label,path,classes="button") => `<a class="${classes}" data-route href="${esc(base+path)}">${esc(label)}</a>`;
const memberLabel = (member,source) => member.username || member.email || source?.name || (superMember(member)?"平台内置账号":"企业认证账号");
function focusAdminDetail(pane){
  if(!pane)return;
  pane.tabIndex=-1;
  requestAnimationFrame(()=>{
    if(!pane.isConnected)return;
    pane.focus({preventScroll:true});
    if(matchMedia("(max-width: 780px)").matches)pane.scrollIntoView({block:"start"});
  });
}

async function members(id="",panel=""){
  const [catalog,models,settings]=await Promise.all([api("/admin/members"),api("/admin/models"),api("/admin/settings")]);
  const creating=id==="new";
  const selected=creating?null:id?catalog.items.find(member=>member.id===id):catalog.items.find(member=>member.role!=="admin")||catalog.items[0];
  if(id&&!creating&&!selected)throw new Error("成员不存在，请返回列表。");
  const sources=new Map(settings.sources.map(source=>[source.id,source]));
  const dbSources=settings.sources.filter(source=>source.mode==="database");
  const source=sources.get(selected?.provider_id);
  const query=new URLSearchParams(location.hash.slice(1)).get("member")||"";
  heading("成员", "");
  content.querySelector(".page-title").append(button("添加员工",()=>navigate(memberPath("new")),{primary:true}));
  content.insertAdjacentHTML("beforeend",`<div class="admin-split"><div class="admin-overview">
    <label>查找成员<input id="member-search" type="search" placeholder="姓名、账号或邮箱" value="${esc(query)}"></label>
    <div id="member-groups" class="admin-list" data-columns="two"></div>
  </div><aside class="admin-detail" aria-label="成员详情" id="member-detail"></aside></div>`);
  const list=document.getElementById("member-groups"),search=document.getElementById("member-search"),pane=document.getElementById("member-detail");
  const show=()=>{
    const query=search.value.trim().toLocaleLowerCase();
    const filtered=catalog.items.filter(member=>[member.name,member.username,member.email].some(value=>String(value||"").toLocaleLowerCase().includes(query)));
    const group=(title,items,blank)=>`<section class="admin-group"><h2 class="admin-group-title">${title}</h2>${items.map(member=>{
      const label=memberLabel(member,sources.get(member.provider_id));
      const meta=member.role==="admin"?memberRole(member)+(member.enabled?"":" · 已停用"):member.enabled?"启用":"停用";
      return `<a class="admin-row admin-person-row" data-route data-member="${esc(member.id)}" href="${esc(base+memberPath(member.id))}" ${selected?.id===member.id?'aria-current="true"':""}>
        <span class="admin-person">${memberAvatar(member)}<span><strong>${esc(member.name)}</strong><small>${esc(label)}</small></span></span>
        <span class="admin-status ${member.enabled?"ok":"warning"}">${meta}</span></a>`;
    }).join("")||empty(query?"没有匹配的成员。":blank)}</section>`;
    list.innerHTML=group("后台管理员",filtered.filter(member=>member.role==="admin"),"暂无后台管理员。")+
      group("员工账号",filtered.filter(member=>member.role!=="admin"),"暂无员工账号，可添加员工或等待首次企业登录。");
  };
  search.oninput=show;show();
  if(creating){
    pane.innerHTML='<h2>添加员工</h2><div id="member-account-editor"></div>';
    if(!dbSources.length){pane.insertAdjacentHTML("beforeend",empty("请先设置平台账号密码认证来源，再创建员工账号。")+memberLink("打开认证设置","/app/settings"));focusAdminDetail(pane);return;}
    const target=pane.querySelector("#member-account-editor");
    const defaultSource=dbSources.find(source=>source.id===settings.login_provider)||dbSources.find(source=>source.enabled)||dbSources[0];
    mountDatabaseAccountForm(target,defaultSource.id,null,{sources:dbSources,cancel:memberPath(),saved:account=>navigate(memberPath(account.id,"",""))});
    focusAdminDetail(pane);
    return;
  }
  if(!selected){pane.innerHTML=empty("请选择一位成员查看详情。");return;}
  await renderMemberDetail(pane,selected,source,models.items,panel);
  if(id)focusAdminDetail(pane);
}

async function renderMemberDetail(pane,member,source,models,panel){
  const founder=superMember(member),admin=member.role==="admin",path=memberPath(member.id),roleAction=admin?"收回后台权限":"授予平台管理员";
  const webOnly=member.provider_id==="__platform_admin__";
  const title=`<div class="admin-detail-head"><span class="admin-person">${memberAvatar(member)}<span><h2>${esc(member.name)}</h2><small>${esc(memberLabel(member,source))} · ${memberRole(member)}</small></span></span></div>`;
  const access=(label,status)=>`<div class="admin-access"><span>${label}</span><span class="admin-status ${status==="可登录"?"ok":""}">${status}</span></div>`;
  const desktopStatus=!member.enabled?"账号已停用":source&&!source.enabled?"认证来源已停用":"可登录";
  const rights=`<section class="admin-detail-section"><h3>访问权限</h3>${webOnly?"":access("Cowork 桌面端",desktopStatus)}${access("企业管理后台",!admin?"未授权":!member.enabled?"账号已停用":"可登录")}</section>`;
  pane.innerHTML=title+rights;
  if(webOnly)return;

  const write=async (values,noticeText)=>{
    await api("/admin/members/"+encodeURIComponent(member.id),{method:"PUT",body:{revision:member.revision,...values}});
    dirty=false;navigate(path);message(noticeText,"success");
  };
  if(panel==="models"){
    mountMemberModels(pane,member,models,path,write);
    return;
  }
  if(panel==="role"&&!founder&&me.can_assign_admin){
    const impact=admin?"收回后，该成员仍可在 Cowork 桌面端使用已授权资源。":"授权后，该成员可以管理成员、模型、企业能力、审计和客户端发布。";
    pane.insertAdjacentHTML("beforeend",`<section class="admin-detail-section"><h3>${roleAction}</h3><p>${impact}</p><div class="actions-row" id="member-role-actions">${memberLink("取消",path)}</div></section>`);
    const confirmRole=button(admin?"确认收回":"确认授权",async()=>{
      confirmRole.disabled=true;
      try{await write({role:admin?"member":"admin"},admin?"已收回后台权限。":"已授予平台管理员权限。");}
      finally{confirmRole.disabled=false;}
    },{primary:true});
    pane.querySelector("#member-role-actions").prepend(confirmRole);
    return;
  }
  if(panel==="account"&&source?.mode==="database"&&(!admin||me.can_assign_admin)){
    const accounts=await api("/admin/auth-sources/"+encodeURIComponent(source.id)+"/accounts");
    const account=accounts.items.find(item=>item.id===member.id);
    if(!account)throw new Error("此成员没有独立的员工账号可管理。");
    pane.insertAdjacentHTML("beforeend",'<section class="admin-detail-section"><h3>账号与密码</h3><div id="member-account-editor"></div></section>');
    mountDatabaseAccountForm(pane.querySelector("#member-account-editor"),source.id,account,{cancel:path,saved:()=>navigate(path)});
    return;
  }
  if(panel==="status"&&!founder){
    const label=member.enabled?"停用账号":"启用账号";
    const impact=member.enabled?"停用后，此成员的当前登录会失效，已授权的模型和能力仍会保留。":"启用后，此成员可以重新登录并使用已获授权的资源。";
    pane.insertAdjacentHTML("beforeend",`<section class="admin-detail-section"><h3>${label}</h3><p>${impact}</p><div class="actions-row" id="member-status-actions">${memberLink("取消",path)}</div></section>`);
    const confirmStatus=button(label,async()=>{confirmStatus.disabled=true;try{await write({enabled:!member.enabled},member.enabled?"账号已停用。":"账号已启用。");}finally{confirmStatus.disabled=false;}},{primary:!member.enabled,danger:member.enabled});
    pane.querySelector("#member-status-actions").prepend(confirmStatus);
    return;
  }
  const modelName=id=>models.find(model=>model.id===id)?.name||id;
  const summary=member.model_ids.length?member.model_ids.map(modelName).join("、"):"尚未分配模型";
  pane.insertAdjacentHTML("beforeend",`<section class="admin-detail-section"><div class="admin-access"><span>${esc(summary)}</span>${memberLink("分配模型",memberPath(member.id,"models"))}</div>
    ${member.default_model_id?`<p class="muted">默认模型 ${esc(modelName(member.default_model_id))}</p>`:""}</section>
    <section class="admin-detail-section"><div class="actions-row">
      ${!founder&&me.can_assign_admin?memberLink(roleAction,memberPath(member.id,"role")):""}
      ${source?.mode==="database"&&(!admin||me.can_assign_admin)?memberLink("账号与密码",memberPath(member.id,"account")):founder?"":memberLink(member.enabled?"停用账号":"启用账号",memberPath(member.id,"status"))}
      ${!member.enabled&&platform?.team_spaces?.version===1?memberLink("管理其空间所有权","/app/members/"+encodeURIComponent(member.id)+"/spaces"):""}
    </div><p class="muted">最近登录 ${date(member.last_login_at)}</p></section>`);
}

function mountMemberModels(pane,member,models,path,write){
  const visible=models.filter(model=>model.enabled||member.model_ids.includes(model.id));
  pane.insertAdjacentHTML("beforeend",`<section class="admin-detail-section"><h3>分配模型</h3><form class="form" id="member-form">
    ${visible.length?`<fieldset><legend>可用模型</legend><div class="check-list">${visible.map(model=>`<label><input type="checkbox" name="models" value="${esc(model.id)}" ${member.model_ids.includes(model.id)?"checked":""}>${esc(model.name)}${model.enabled?"":"（已停用）"}</label>`).join("")}</div></fieldset>`:empty("暂无可分配的企业模型。请先在模型页启用模型。")}
    <label>默认模型<select name="default_model_id"></select></label><div class="actions-row"><button class="primary" ${visible.length?"":"disabled"}>保存模型授权</button>${memberLink("取消",path)}</div></form></section>`);
  const form=pane.querySelector("#member-form"),defaultModel=form.elements.default_model_id;
  const syncDefault=(current=defaultModel.value)=>{
    const checked=new FormData(form).getAll("models"), allowed=visible.filter(model=>checked.includes(model.id));
    defaultModel.innerHTML=allowed.map(model=>`<option value="${esc(model.id)}">${esc(model.name)}</option>`).join("")||'<option value="">未分配模型</option>';
    if(checked.includes(current))defaultModel.value=current;
  };
  watchForm(form);
  form.querySelectorAll('[name="models"]').forEach(input=>input.onchange=()=>syncDefault());syncDefault(member.default_model_id);
  form.onsubmit=event=>{event.preventDefault();action(async()=>{
    const submit=form.querySelector("button.primary");submit.disabled=true;
    try{await write({model_ids:new FormData(form).getAll("models"),default_model_id:defaultModel.value},"模型授权已保存。");}
    finally{submit.disabled=false;}
  });};
}
const modelProviders = {openai:"OpenAI 兼容",moonshot:"Moonshot / Kimi",anthropic:"Anthropic"};
const modelSelection = id => "/app/models?selected=" + encodeURIComponent(id);

async function models(selectedId=new URLSearchParams(location.search).get("selected"), assigning=false){
  const [catalog, memberList] = await Promise.all([api("/admin/models"), api("/admin/members")]);
  heading("模型");
  content.querySelector(".page-title").insertAdjacentHTML("beforeend", adminViewLink("/app/models/new", "添加模型", true));
  const selected = catalog.items.find(model => model.id === selectedId) || catalog.items[0];
  if (assigning && selected?.id !== selectedId) throw new Error("模型不存在，请返回列表。");
  if (!selected) {
    content.insertAdjacentHTML("beforeend", empty("还没有企业模型。"));
    return;
  }
  const employees = memberList.items.filter(member => member.provider_id !== "__platform_admin__");
  const modelCounts = new Map();
  for (const member of employees) {
    for (const id of member.model_ids) modelCounts.set(id, (modelCounts.get(id) || 0) + 1);
  }
  const scope = id => modelCounts.has(id) ? modelCounts.get(id) + " 人" : "—";
  const rows = '<div class="admin-list">' + adminColumns("名称", "状态", "使用范围") + catalog.items.map(model =>
    adminRow(modelSelection(model.id), model.id === selected.id, model.name,
      model.enabled ? "已启用" : model.tested ? "未启用" : "待测试", scope(model.id), model.enabled ? "ok" : "warn")
  ).join("") + '</div>';
  const path = "/app/models/" + encodeURIComponent(selected.id);
  const fields = adminFields([["接入方式", modelProviders[selected.provider] || selected.provider],
    ["模型标识", selected.model_name], ["基础地址", selected.base_url], ["授权范围", scope(selected.id)]]);
  const detailHead = '<div class="admin-detail-head"><h2>' + esc(selected.name) + '</h2></div>';
  const summary = '<div class="admin-detail-section">' + fields + '</div><div class="admin-record-actions">' +
    adminViewLink(path + "/grants", "分配员工") + adminViewLink(path, "配置模型") + '</div>';
  content.insertAdjacentHTML("beforeend", adminSplit(rows, detailHead + (assigning ? "" : summary)));
  if (assigning) modelGrants(selected.id, employees);
  if (selected.id === selectedId) focusAdminDetail(content.querySelector(".admin-detail"));
}

function modelGrants(id, employees){
  const panel = content.querySelector(".admin-detail");
  const before = employees.filter(member => member.model_ids.includes(id)).map(member => member.id);
  const selected = new Set(before);
  const options = employees.filter(member => member.enabled || selected.has(member.id));
  const rows = options.map(member => `<label data-model-candidate="${esc([member.name, member.username, member.email].filter(Boolean).join(" "))}">
    <input type="checkbox" name="member_ids" value="${esc(member.id)}" ${selected.has(member.id)?"checked":""}>
    ${esc(member.name)}${member.enabled?"":"（已停用）"}</label>`).join("");
  panel.insertAdjacentHTML("beforeend", `<form class="form admin-detail-section" id="model-grants-form">
    <fieldset><legend>分配员工</legend>
    ${options.length?'<label>查找员工<input type="search" id="model-grant-search"></label><div class="check-list">'+rows+'</div>':'<p class="muted">暂无可分配的员工。</p>'}</fieldset>
    <div class="actions-row"><button class="primary" type="submit" ${options.length?"":"disabled"}>保存授权</button>
    <a data-route href="${esc(base+modelSelection(id))}">取消</a></div></form>`);
  const form = panel.querySelector("form");
  const search = form.querySelector('#model-grant-search');
  const submit = form.querySelector('button[type="submit"]');
  if (search) search.oninput = () => {
    const query = search.value.trim().toLocaleLowerCase();
    form.querySelectorAll("[data-model-candidate]").forEach(row => {
      row.hidden = !row.dataset.modelCandidate.toLocaleLowerCase().includes(query);
    });
  };
  form.onchange = event => { if (event.target.name === "member_ids") dirty = true; };
  form.onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const memberIds = new FormData(form).getAll("member_ids");
      form.querySelector("fieldset").disabled = submit.disabled = true;
      try {
        await api("/admin/models/" + encodeURIComponent(id) + "/grants", {method:"PUT",
          body:{member_ids:memberIds, previous_member_ids:before}});
        dirty = false;
        history.replaceState({}, "", base + modelSelection(id));
        await render();
        message("模型授权已保存。", "success");
      } finally {
        form.querySelector("fieldset").disabled = submit.disabled = false;
      }
    });
  };
}
function editModel(model){
  heading(model?"配置模型":"添加模型","",model?modelSelection(model.id):"/app/models");
  content.insertAdjacentHTML("beforeend",`<form class="form" id="model-form"><div class="row"><label>显示名称<input required name="name" maxlength="128" value="${esc(model?.name)}"></label><label>模型标识<input required name="model_name" value="${esc(model?.model_name)}" placeholder="模型服务提供的模型 ID"></label></div><div class="row"><label>服务类型<select name="provider">${["openai","moonshot","anthropic"].map(v=>`<option value="${v}" ${model?.provider===v?"selected":""}>${{openai:"OpenAI 兼容",moonshot:"Moonshot / Kimi",anthropic:"Anthropic"}[v]}</option>`).join("")}</select></label><label>API 协议<select name="protocol">${["chat_completions","responses","anthropic"].map(v=>`<option value="${v}" ${model?.protocol===v?"selected":""}>${v}</option>`).join("")}</select></label></div><label>服务基础地址<input type="url" required name="base_url" placeholder="https://models.example.com/v1" value="${esc(model?.base_url)}"></label><label>服务密钥<input type="password" name="api_key" autocomplete="new-password" ${model?"":"required"} placeholder="${model?"留空保留现有密钥":"仅保存到中心"}"></label><details><summary>能力与高级参数</summary><textarea name="options" class="code">${esc(JSON.stringify(model?.options||{supports_vision:false,stream_usage_enabled:true},null,2))}</textarea></details><p class="help">配置变更后需重新测试，启用后才可供员工调用。</p><div class="actions-row"><button class="primary">保存配置</button><button type="button" id="test-model" ${model&&!dirty?"":"disabled"}>测试调用</button><button type="button" id="toggle-model" ${model?"":"disabled"}>${model?.enabled?"停用":"启用"}</button></div></form>`);
  const form=document.getElementById("model-form");watchForm(form);form.elements.provider.onchange=()=>{form.elements.protocol.value=form.elements.provider.value==="anthropic"?"anthropic":"chat_completions";dirty=true;};
  const payload=enabled=>{const v=formData(form);return {...v,options:JSON.parse(v.options),revision:model?.revision,enabled};};
  form.onsubmit=e=>{e.preventDefault();action(async()=>{model=await api("/admin/models"+(model?"/"+model.id:""),{method:model?"PUT":"POST",body:payload(false)});dirty=false;history.replaceState({}, "", base + "/app/models/" + encodeURIComponent(model.id));editModel(model);message("配置已保存，请测试实际调用。","success");});};
  document.getElementById("test-model").onclick=()=>action(async()=>{if(dirty)throw new Error("请先保存修改，再测试。");await api("/admin/models/"+model.id+"/test",{method:"POST"});model.tested=true;message("实际调用测试通过，可以启用并分配给成员。","success");});
  document.getElementById("toggle-model").onclick=()=>action(async()=>{if(dirty)throw new Error("请先保存修改。");model=await api("/admin/models/"+model.id,{method:"PUT",body:payload(!model.enabled)});dirty=false;editModel(model);message(model.enabled?"模型已启用。":"模型已停用。","success");});
}
const auditSections = [["", "聊天请求"], ["/events", "平台操作"]];

function auditHeader(section) {
  if (section === "/settings") {
    heading("审计设置", "聊天请求保留 30 天，平台操作保留 90 天。", "/app/audit");
    return;
  }
  heading("审计");
  content.querySelector(".page-title").insertAdjacentHTML("beforeend", adminViewLink("/app/audit/settings", "审计设置"));
  const links = document.createElement("div");
  links.className = "space-tabs";
  links.innerHTML = auditSections.map(([path, label]) => `<a ${section===path?'aria-current="page"':""} href="${base}/app/audit${path}" data-route>${label}</a>`).join("");
  content.append(links);
}

async function auditSettingsPage() {
  const [policy, platformSettings] = await Promise.all([api("/admin/chat-audit/policy"), api("/admin/settings")]);
  auditHeader("/settings");
  const cleanup = platformSettings.cleanup;
  content.insertAdjacentHTML("beforeend", `<form id="audit-policy" class="form section"><label class="check"><input type="checkbox" name="personal_enabled" ${policy.personal_enabled?"checked":""}>记录使用自配模型的企业任务请求</label><p class="help">默认关闭。保存后对后续任务生效，普通个人任务仍不记录。</p><div><button class="primary">保存设置</button></div></form><p class="muted">最近自动清理 ${date(cleanup.last_success_at)}，删除 ${cleanup.chats_deleted} 条聊天请求。</p>${cleanup.error?`<p class="danger">${esc(cleanup.error)}</p>`:""}`);
  const form = document.getElementById("audit-policy");
  watchForm(form);
  form.onsubmit = event => {
    event.preventDefault();
    action(async () => {
      const save = form.querySelector("button");
      save.disabled = true;
      try {
        const result = await api("/admin/chat-audit/policy", {method:"PUT", body:{revision:policy.revision, personal_enabled:form.elements.personal_enabled.checked}});
        policy.revision = result.revision;
        dirty = false;
        message("审计设置已保存。", "success");
      } finally {
        save.disabled = false;
      }
    });
  };
}

async function chatAuditPage() {
  const route = location.pathname.slice((base + "/app/audit").length);
  if (route === "/settings") return auditSettingsPage();
  const events = route === "/events";
  const members = await api("/admin/members");
  auditHeader(events?"/events":"");
  content.insertAdjacentHTML("beforeend", `<form id="audit-filters" class="toolbar"><label>成员<select name="member_id"><option value="">所有成员</option>${members.items.map(member=>`<option value="${esc(member.id)}">${esc(member.name)}</option>`).join("")}</select></label>${events?"":'<label>开始日期<input name="since" type="date"></label><label>结束日期<input name="until" type="date"></label>'}<label>${events?"操作名称":"聊天标识或模型"}<input name="keyword" maxlength="${events?80:240}"></label><button class="primary">查询</button></form>` +
    adminSplit('<div id="audit-items" aria-live="polite"></div><div id="audit-pager" class="pager"></div>',
      '<div id="audit-detail" aria-live="polite"><p class="muted">选择一条记录查看详情。</p></div>'));
  const form = document.getElementById("audit-filters"), list = document.getElementById("audit-items"), pager = document.getElementById("audit-pager");
  const summary = document.getElementById("audit-detail");
  const actionName = value => ({"model.grants_changed":"调整模型授权", "member.updated":"调整成员权限",
    "capability.published":"发布企业能力", "release.publish":"发布客户端"})[value] || value;
  let offset = 0, revision = 0, selectedRevision = 0;
  const load = async () => {
    const pending = ++revision;
    ++selectedRevision;
    const filters = formData(form);
    const params = new URLSearchParams({member_id:filters.member_id, keyword:filters.keyword, offset:String(offset)});
    if (filters.since) params.set("since", String(new Date(filters.since + "T00:00:00").getTime() / 1000));
    if (filters.until) {
      const end = new Date(filters.until + "T00:00:00");
      end.setDate(end.getDate() + 1);
      params.set("until", String(end.getTime() / 1000));
    }
    const result = await api("/admin/chat-audit" + (events?"/events":"") + "?" + params);
    if (pending !== revision) return;
    summary.innerHTML = '<p class="muted">选择一条记录查看详情。</p>';
    if (!result.items.length) list.innerHTML = empty("没有符合条件的记录。");
    else list.innerHTML = '<div class="admin-list">' + adminColumns(events ? "操作" : "聊天", "成员", "时间") +
      result.items.map((item,index) => `<button class="admin-row" type="button" data-audit-row="${index}"
        aria-pressed="false"><span>${esc(events?actionName(item.action):item.conversation_id)}</span>
        <span>${esc(item.member_name||"系统")}</span><span>${esc(date(events?item.created_at:item.captured_at))}</span></button>`).join("") + '</div>';
    list.querySelectorAll("[data-audit-row]").forEach(control => control.onclick = () => action(async () => {
      const requestRevision = ++selectedRevision;
      const row = result.items[Number(control.dataset.auditRow)];
      list.querySelectorAll("[data-audit-row]").forEach(button => button.setAttribute("aria-pressed", String(button === control)));
      if (events) {
        summary.innerHTML = '<h2>' + esc(actionName(row.action)) + '</h2>' +
          adminFields([["成员",row.member_name || "系统"],["时间",date(row.created_at)],["对象",row.target || "—"],["结果",row.outcome]]);
        focusAdminDetail(summary);
        return;
      }
      summary.innerHTML = '<p class="muted">正在读取请求…</p>';
      try {
        const item = await api("/admin/chat-audit/" + encodeURIComponent(row.id));
        if (requestRevision !== selectedRevision) return;
        const body = item.omitted ? "请求内容超过大小限制，仅保留请求信息。" : JSON.stringify(item.parameters, null, 2);
        summary.innerHTML = '<h2>最近一次请求</h2>' +
          adminFields([["成员",row.member_name],["模型", row.model_name + (row.model_source === "personal" ? " · 自配模型" : "")],
            ["时间",date(item.captured_at)]]) + '<div class="admin-detail-section"><pre>' + esc(body) + '</pre></div>';
        focusAdminDetail(summary);
      } catch (error) {
        if (requestRevision !== selectedRevision || error.name === "AbortError") return;
        summary.innerHTML = '<p class="danger">' + esc(error.message) + '</p>';
        summary.append(button("重新读取", () => control.click()));
        if (error.code === "authentication_required") throw error;
      }
    }));
    const previous = button("上一页", async () => { offset = Math.max(0, offset - 50); await load(); });
    const next = button("下一页", async () => { offset += 50; await load(); });
    previous.disabled = offset === 0;
    next.disabled = offset + 50 >= result.total;
    const count = document.createElement("span");
    count.textContent = `共 ${result.total} 条`;
    pager.replaceChildren(previous, count, next);
  };
  form.onsubmit = event => { event.preventDefault(); offset = 0; action(load); };
  await load();
}

async function settingsPage(){
  const data = await api("/admin/settings");
  heading("设置");
  content.querySelector(".page-title").insertAdjacentHTML("beforeend", adminViewLink("/app/sources/new", "添加认证来源", true));
  const sources = data.sources.map(source => `<div class="source-row"><div class="summary"><strong>${esc(source.name)}</strong>
    <small>${esc(authNames[source.mode] || source.mode)} · ${source.enabled ? "已启用" : "草稿"}</small></div>
    <button type="button" data-source="${esc(source.id)}">配置</button>
    <button type="button" class="danger" data-delete-source="${esc(source.id)}" aria-label="删除 ${esc(source.name)}">删除</button></div>`).join("");
  const loginSources = '<section><h2>账号认证</h2><div id="sources">' +
    (sources || empty("尚未配置企业认证。")) + '</div></section>';
  let summary = '<h2>平台</h2>' + adminFields([["平台标识",data.platform_id],["访问地址",data.public_url],
    ["会话期限",Math.round(data.session_seconds / 3600) + " 小时"]]);
  if (spaceEnabled()) summary += '<div class="admin-record-actions">' + adminViewLink("/app/team-spaces", "团队空间") + '</div>';
  content.insertAdjacentHTML("beforeend", adminSplit(loginSources, summary));
  content.querySelectorAll("[data-source]").forEach(b=>b.onclick=()=>navigate("/app/sources/"+b.dataset.source));
  content.querySelectorAll("[data-delete-source]").forEach(b=>b.onclick=()=>action(async()=>{const source=data.sources.find(s=>s.id===b.dataset.deleteSource);if(dirty)throw new Error("请先保存登录入口的修改，再删除认证来源。");if(!confirm(`确定删除认证来源“${source.name}”？删除后无法恢复此配置；已关联成员的来源不能删除。`))return;b.disabled=true;try{await api("/admin/auth-sources/"+encodeURIComponent(source.id),{method:"DELETE",body:{revision:source.revision}});await settingsPage();message("认证来源已删除。","success");}finally{b.disabled=false;}}));
  const entry=document.createElement("form");entry.className="toolbar";entry.innerHTML=`<label>客户端统一登录方式<select name="provider_id"><option value="">未指定（仅一个来源时自动使用）</option>${data.sources.map(source=>`<option value="${esc(source.id)}" ${source.id===data.login_provider?"selected":""} ${source.enabled&&source.verified?"":"disabled"}>${esc(source.name)}${source.enabled?"":"（未启用）"}</option>`).join("")}</select></label><button>保存登录入口</button>`;document.getElementById("sources").after(entry);watchForm(entry);entry.onsubmit=e=>{e.preventDefault();action(async()=>{await api("/admin/login-entry",{method:"PUT",body:{provider_id:entry.elements.provider_id.value,revision:data.login_revision}});dirty=false;await settingsPage();message("客户端登录入口已保存。","success");});};

}
async function editSource(source){
  const preset=await api("/admin/auth-preset");heading(source?"配置认证来源":"添加认证来源","平台账号密码可直接启用；外部认证需先测试登录。","/app/settings");
  content.insertAdjacentHTML("beforeend",`<form class="form" id="source-form"><div class="row"><label>名称<input required name="name" value="${esc(source?.name)}"></label><label>来源 ID<input required name="id" pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,127}" value="${esc(source?.id)}" ${source?"readonly":""}></label></div><label>认证方式<select name="mode">${["database","oauth2","oidc","ldap"].map(v=>`<option value="${v}" ${source?.mode===v||(!source&&v==="database")?"selected":""}>${authNames[v]}</option>`).join("")}</select></label><label>连接参数<textarea name="config" class="code">${esc(JSON.stringify(source?.config||{},null,2))}</textarea></label><p class="help" id="source-help">OAuth2 预设的端点和字段可替换。PKCE 不支持时需明确配置 disabled；不能在失败后自动降级。</p><label>应用密钥 / LDAP 查询密码<input type="password" name="secret" autocomplete="new-password" placeholder="${source?"留空保留现有凭据":"在中心加密保存"}"></label><div class="actions-row"><button class="primary">保存草稿</button><button type="button" id="test-source" ${source?"":"disabled"}>测试登录</button><button type="button" id="toggle-source" ${source?"":"disabled"}>${source?.enabled?"停用":"启用"}</button></div><div id="ldap-test-fields" ${source?.mode==="ldap"?"":"hidden"}><h3>测试目录账号</h3><label>账号<input name="test_username" autocomplete="off"></label><label>密码<input name="test_password" type="password" autocomplete="new-password"></label></div></form>`);
  const form=document.getElementById("source-form");watchForm(form);form.elements.mode.onchange=()=>{dirty=true;const mode=form.elements.mode.value;document.getElementById("ldap-test-fields").hidden=mode!=="ldap";if(!source)form.elements.config.value=JSON.stringify(mode==="database"?{}:mode==="oauth2"?preset.config:mode==="oidc"?{issuer:"https://identity.example.com",discovery_url:"https://identity.example.com/.well-known/openid-configuration",client_id:"",pkce:"S256"}:{host:"directory.example.com",tls:"ldaps",base_dn:"dc=example,dc=com",user_filter:"(uid={username})",identity_attribute:"entryUUID",bind_dn:""},null,2);};
  if(source?.mode==="database"){const link=document.createElement("a");link.className="button";link.dataset.route="";link.href=base+"/app/sources/"+source.id+"/accounts";link.textContent="管理员工账号";form.querySelector(".actions-row").before(link);}
  const originalModeChange=form.elements.mode.onchange;
  const basic=document.createElement("div");basic.className="form-fields";form.elements.config.closest("label").before(basic);
  const advanced=document.createElement("details"),summary=document.createElement("summary");summary.textContent="高级协议参数（JSON）";advanced.append(summary);
  const configLabel=form.elements.config.closest("label");configLabel.before(advanced);advanced.append(configLabel);
  const fields={database:[],oauth2:[["client_id","应用标识"],["authorize_url","授权地址"],["token_url","令牌地址"],["userinfo_url","用户资料地址"],["subject_token_path","令牌中的稳定用户字段"],["subject_userinfo_path","资料中的稳定用户字段"]],oidc:[["client_id","应用标识"],["issuer","身份来源 issuer"],["discovery_url","OIDC 发现地址"]],ldap:[["host","目录服务器"],["tls","TLS 方式（ldaps / starttls）"],["base_dn","搜索根目录"],["user_filter","用户过滤条件"],["identity_attribute","稳定身份属性"],["bind_dn","查询账号 DN（可选）"]]};
  const drawBasic=()=>{const local=form.elements.mode.value==="database";advanced.hidden=local;document.getElementById("test-source").hidden=local;document.getElementById("toggle-source").hidden=local&&!source;form.querySelector("button.primary").textContent=local?(source?"保存配置":"保存并启用"):"保存草稿";form.elements.secret.closest("label").hidden=local;document.getElementById("source-help").textContent=local?"保存并启用后进入账号管理，创建员工账号即可登录。无需测试登录或配置外部认证服务。":"OAuth2 预设的端点和字段可替换。PKCE 不支持时需明确配置 disabled；失败时不会自动降级。";const values=JSON.parse(form.elements.config.value);basic.innerHTML=fields[form.elements.mode.value].map(([key,label])=>`<label>${esc(label)}<input data-config="${key}" value="${esc(values[key]||"")}"></label>`).join("");for(const [key] of fields[form.elements.mode.value])delete values[key];form.elements.config.value=JSON.stringify(values,null,2);};
  drawBasic();form.elements.mode.onchange=()=>{originalModeChange();form.elements.secret.value="";drawBasic();};form.elements.mode.disabled=!!source;
  const payload=enabled=>{const v=formData(form),mode=source?.mode||v.mode,config=JSON.parse(v.config);basic.querySelectorAll("[data-config]").forEach(input=>{if(input.value.trim())config[input.dataset.config]=input.value.trim();});return {id:v.id,name:v.name,mode,config,secrets:mode==="database"?{}:v.secret?{[mode==="ldap"?"bind_password":"client_secret"]:v.secret}:{},revision:source?.revision,enabled};};
  const reload=async text=>{const id=form.elements.id.value,data=await api("/admin/settings");source=data.sources.find(x=>x.id===id);dirty=false;history.replaceState({},"",base+"/app/sources/"+id);await editSource(source);message(text,"success");};
  form.onsubmit=e=>{e.preventDefault();action(async()=>{const local=form.elements.mode.value==="database",p=payload(local?(source?.enabled??true):false);await api("/admin/auth-sources/"+encodeURIComponent(p.id),{method:"PUT",body:p});await reload(local?"配置已保存，可进入账号管理创建员工账号。":"草稿已保存，请完成测试登录后启用。");});};
  document.getElementById("test-source").onclick=()=>action(async()=>{if(dirty)throw new Error("请先保存连接参数。");if(source.mode==="ldap"){await api("/auth/ldap",{method:"POST",body:{provider_id:source.id,username:form.elements.test_username.value,password:form.elements.test_password.value,test:true}});await reload("目录认证测试通过，可以启用。");}else{const result=await api("/auth/transactions",{method:"POST",body:{provider_id:source.id,mode:"web",test:true}});const opened=window.open(result.login_url,"_blank","noopener");message("请在浏览器完成测试登录，然后返回此页点击启用。若未打开，请允许此站点打开登录窗口。");}});
  document.getElementById("toggle-source").onclick=()=>action(async()=>{if(dirty)throw new Error("请先保存修改。");await api("/admin/auth-sources/"+source.id,{method:"PUT",body:payload(!source.enabled)});await reload(source.enabled?"认证来源已停用。":"认证来源已启用。");});
}
async function databaseAccountsPage(sourceId,accountId){
  const [data,settings]=await Promise.all([api("/admin/auth-sources/"+sourceId+"/accounts"),api("/admin/settings")]);
  const source=settings.sources.find(x=>x.id===sourceId),path="/app/sources/"+sourceId+"/accounts";
  if(!accountId){
    heading((source?.name||"平台账号密码")+" · 员工账号","管理员创建账号后即可分配模型；密码重设和停用会使旧登录失效。","/app/sources/"+sourceId);
    content.querySelector(".page-title").append(button("创建员工账号",()=>navigate(path+"/new"),{primary:true}));
    content.insertAdjacentHTML("beforeend",data.items.length?`<div class="table-wrap"><table><thead><tr><th>账号 / 姓名</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.items.map(a=>`<tr><td>${esc(a.username)}<span class="secondary-line">${esc(a.name)}</span></td><td>${a.enabled?"启用":"停用"} · ${a.role==="admin"?"管理员":"成员"}</td><td><a data-route href="${base+path+"/"+a.id}">账号与密码</a> · <a data-route href="${base}/app/members/${a.id}">模型与权限</a></td></tr>`).join("")}</tbody></table></div>`:empty("还没有员工账号。创建账号后，员工即可通过已启用的来源登录。"));return;
  }
  const account=data.items.find(x=>x.id===accountId);
  if(accountId!=="new"&&!account)throw new Error("账号不存在，请返回列表。");
  heading(account?"管理员工账号":"创建员工账号","账号名称创建后保持不变；员工默认是普通成员，模型权限在成员页分配。",path);
  mountDatabaseAccountForm(content,sourceId,account,{cancel:path,saved:()=>navigate(path)});
}
function mountDatabaseAccountForm(target,sourceId,account,{sources=[],cancel,saved}){
  const otherAdmin=account?.role==="admin"&&!me.can_assign_admin;
  const sourceSelect=sources.length>1?`<label>认证来源<select name="source_id">${sources.map(source=>`<option value="${esc(source.id)}" ${source.id===sourceId?"selected":""}>${esc(source.name)}${source.enabled?"":"（已停用）"}</option>`).join("")}</select></label>`:"";
  target.insertAdjacentHTML("beforeend",`<form class="form" id="database-account-form">${sourceSelect}
    <label>成员姓名<input name="name" required maxlength="80" value="${esc(account?.name)}"></label>
    <label>账号<input name="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}" value="${esc(account?.username)}" ${account?"readonly":""}></label>
    <label>${account?"重设密码":"初始密码"}<input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="256" ${account?"":"required"} placeholder="${account?"留空保留现有密码":"至少 12 个字符"}"></label>
    <label>确认密码<input name="confirmation" type="password" autocomplete="new-password" maxlength="256" ${account?"":"required"}></label>
    <label>账号状态<select name="enabled"><option value="true">启用</option><option value="false" ${account&&!account.enabled?"selected":""}>停用</option></select></label>
    <p class="help">${otherAdmin?"此账号是管理员，只能由超级管理员管理其密码和状态。":account?"重设密码或停用账号会使现有登录失效。":"请把账号和初始密码交给对应员工。"}</p>
    <div class="actions-row"><button class="primary" ${otherAdmin?"disabled":""}>${account?"保存账号":"创建账号"}</button>${memberLink("取消",cancel)}</div></form>`);
  const form=target.querySelector("#database-account-form");watchForm(form);
  form.onsubmit=event=>{event.preventDefault();action(async()=>{
    if(otherAdmin)return;
    const values=formData(form);
    if(values.password!==values.confirmation)throw new Error("两次输入的密码不一致，请核对。");
    const submit=form.querySelector("button.primary");submit.disabled=true;
    try{
      const result=await api("/admin/auth-sources/"+encodeURIComponent(values.source_id||sourceId)+"/accounts"+(account?"/"+encodeURIComponent(account.id):""),{
        method:account?"PUT":"POST",body:{username:values.username,name:values.name,password:values.password,enabled:values.enabled==="true",revision:account?.revision,member_revision:account?.member_revision}});
      dirty=false;saved(result);message(account?"账号已保存。":"员工账号已创建，可以为其分配模型。","success");
    }finally{submit.disabled=false;}
  });};
}
const releasePath = id => "/app/releases/" + encodeURIComponent(id);
const releaseState = item => item.recommended ? "当前推荐" : statusNames[item.state];

async function releases() {
  const params = new URLSearchParams(location.search);
  const deleted = params.get("deleted") === "true";
  const catalog = await api("/admin/client-releases" + (deleted ? "?deleted=true" : ""));
  const selected = catalog.items.find(item => item.id === params.get("selected")) || catalog.items[0];
  if (selected && !deleted) return renderReleaseDetail(selected, catalog);
  const pane = releaseLayout(catalog, selected, deleted);
  if (selected) {
    pane.insertAdjacentHTML("beforeend", releaseFields(selected));
    pane.append(releaseArchiveButton(selected, catalog, true));
  }
}

function releaseLayout(catalog, selected, deleted=false){
  heading(deleted ? "已删除的发布" : "客户端发布", "", deleted ? "/app/releases" : "");
  const title = content.querySelector(".page-title");
  if (!deleted) title.insertAdjacentHTML("beforeend", adminViewLink("/app/releases/new", "新建版本", true));
  const recommended = catalog.items.find(item => item.recommended);
  const overview = !deleted && recommended ?
    '<div class="admin-recommended"><small>当前推荐</small><strong class="admin-version">' + esc(recommended.app_version) + '</strong><p>Windows ' +
    esc(recommended.arch) + ' · ' + esc(date(recommended.published_at)) + '</p></div>' : "";
  const list = catalog.items.length ? '<div class="admin-list">' + adminColumns("版本", "状态", "发布时间") + catalog.items.map(item =>
    adminRow(deleted ? "/app/releases?deleted=true&selected=" + encodeURIComponent(item.id) : releasePath(item.id),
      item.id === selected?.id, item.app_version, releaseState(item), date(item.published_at), item.state === "published" ? "ok" : item.state === "failed" ? "error" : "warn")
  ).join("") + '</div>' : empty(deleted ? "没有已删除的发布记录。" : "还没有客户端发布。");
  const controls = deleted ? "" : '<details id="release-options" class="section"><summary>其他操作</summary><div class="actions-row">' +
    adminViewLink("/downloads", "下载页") + adminViewLink("/app/releases?deleted=true", "显示已删除") + '</div></details>';
  if (selected) content.insertAdjacentHTML("beforeend", adminSplit(overview + list + controls, ""));
  else content.insertAdjacentHTML("beforeend", list + controls);
  const tools = content.querySelector("#release-options .actions-row");
  if (tools) tools.append(button("从 GitHub 导入最新版", async () => {
    const result = await api("/admin/client-releases/import-github", {method:"POST", body:{}});
    navigate(releasePath(result.id));
  }));
  return content.querySelector(".admin-detail");
}

function releaseFields(item) {
  const size = item.bytes ? (item.bytes / 1024 / 1024).toFixed(1) + " MiB" : "尚未生成";
  return '<div class="admin-detail-head"><h2>' + esc(item.app_version) + '</h2></div><div class="admin-detail-section">' +
    adminFields([["平台", "Windows " + item.arch], ["包大小", size], ["状态", releaseState(item)]]) + '</div>';
}

function releaseArchiveButton(item, catalog, deleted=false) {
  return button(deleted ? "恢复" : "删除", async () => {
    if (!deleted && !confirm(`删除 ${item.app_version} 的发布记录？可以从已删除的发布中恢复。`)) return;
    await api("/admin/client-releases/" + encodeURIComponent(item.id) + "/" + (deleted ? "restore" : "delete"), {method:"POST",
      body:{operation_id:operationId(), catalog_revision:catalog.catalog_revision, revision:item.revision}});
    history.replaceState({}, "", base + "/app/releases" + (deleted ? "?deleted=true" : ""));
    await render();
    message(deleted ? "发布记录已恢复。" : "发布记录已删除。", "success");
  }, {danger:!deleted});
}
function newRelease(){heading("新建客户端发布","填写程序版本，下一步上传 Windows x64 工作台标准包和更新清单。","/app/releases");content.insertAdjacentHTML("beforeend",'<form class="form" id="release-form"><div class="row"><label>程序版本<input required name="app_version" pattern="[0-9]+\\.[0-9]+\\.[0-9]+" placeholder="例如 5.4.0"></label></div><label>更新说明<textarea name="notes" maxlength="20000"></textarea></label><button class="primary">创建发布记录</button></form>');const form=document.getElementById("release-form");watchForm(form);form.onsubmit=e=>{e.preventDefault();action(async()=>{const result=await api("/admin/client-releases",{method:"POST",body:formData(form)});dirty=false;navigate("/app/releases/"+result.id);});};}
async function releaseDetail(id, focus=false){
  const [r,catalog]=await Promise.all([api("/admin/client-releases/"+encodeURIComponent(id)),api("/admin/client-releases")]);
  renderReleaseDetail(r,catalog);
  if (focus) focusAdminDetail(content.querySelector(".admin-detail"));
}

function renderReleaseDetail(r,catalog) {
  const id = encodeURIComponent(r.id);
  const pane = releaseLayout(catalog, r);
  pane.insertAdjacentHTML("beforeend", releaseFields(r) + (r.notes ? '<p>' + esc(r.notes) + '</p>' : "") +
    (r.error ? '<div class="note danger">' + esc(r.error) + '</div>' : "") +
    '<div class="admin-record-actions" id="release-actions"></div>');
  if (r.sha256 || (r.connection && Object.keys(r.connection).length)) {
    pane.insertAdjacentHTML("beforeend", '<details class="section"><summary>文件与企业配置</summary><p class="code">' +
      esc(r.sha256) + '</p><pre>' + esc(JSON.stringify(r.connection, null, 2)) + '</pre></details>');
  }
  const actions=document.getElementById("release-actions");
  const mutate=async(action,extra={})=>{await api("/admin/client-releases/"+id+"/"+action,{method:"POST",body:{operation_id:operationId(),catalog_revision:catalog.catalog_revision,...extra}});await releaseDetail(id);message("发布目录已更新。","success");};
  if(["draft","failed"].includes(r.state)&&(r.state==="failed"||!["receive","import_pending"].includes(r.stage))){
    pane.insertAdjacentHTML("beforeend", '<form id="upload-form" class="form admin-detail-section"><label>标准客户端 ZIP<input name="archive" type="file" accept=".zip" required></label><label>更新清单或打包检查报告<input name="report" type="file" accept=".json" required></label><progress id="upload-progress" value="0" max="100" hidden></progress><div class="actions-row"><button class="' +
      (r.source_sha256 ? "" : "primary") + '">上传文件</button><button type="button" id="cancel-upload" hidden>取消上传</button></div></form>');
    const form=document.getElementById("upload-form");watchForm(form);form.onsubmit=e=>{e.preventDefault();action(async()=>{const zip=form.elements.archive.files[0],report=form.elements.report.files[0];if(!zip||!report)throw new Error("请选择标准 ZIP 和更新清单或检查报告。");if(report.size>1024*1024)throw new Error("检查报告超过 1 MiB。");await api("/admin/client-releases/"+id+"/report",{method:"PUT",body:JSON.parse(await report.text())});const progress=document.getElementById("upload-progress"),cancel=document.getElementById("cancel-upload");progress.hidden=false;cancel.hidden=false;form.querySelector("button").disabled=true;try{await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open("PUT",base+"/api/v1/admin/client-releases/"+id+"/source");xhr.setRequestHeader("X-CSRF-Token",csrf());xhr.upload.onprogress=event=>{if(event.lengthComputable)progress.value=event.loaded/event.total*100;};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{let reason="上传失败，请重试。";try{reason=JSON.parse(xhr.responseText).error.message;}catch{}reject(new Error(reason));}};xhr.onerror=()=>reject(new Error("上传连接中断，文件选择已保留，请重试。"));xhr.onabort=()=>reject(new Error("上传已取消，可重新上传。"));cancel.onclick=()=>xhr.abort();xhr.send(zip);});dirty=false;await releaseDetail(id);message("源文件已完整接收，可以开始校验与封装。","success");}finally{cancel.hidden=true;form.querySelector("button").disabled=false;}});};
    if(r.source_sha256)actions.append(button("校验并生成企业包",async()=>{await api("/admin/client-releases/"+id+"/prepare",{method:"POST"});await releaseDetail(id);},{primary:true}));
  }
  if(r.state==="processing"||(r.state!=="failed"&&["receive","import_pending"].includes(r.stage))){actions.append(button("取消处理",async()=>{await api("/admin/client-releases/"+id+"/cancel",{method:"POST"});await releaseDetail(id);}));const generation=routeGeneration;releasePoll=setTimeout(()=>{if(generation===routeGeneration)action(()=>releaseDetail(id));},1500);}
  if(["ready","published"].includes(r.state)){const link=document.createElement("a");link.className="button";link.href=base+"/api/v1/admin/client-releases/"+id+"/download";link.textContent="下载安装包";actions.append(link);const manifest=document.createElement("a");manifest.className="button";manifest.href=base+"/api/v1/admin/client-releases/"+id+"/manifest";manifest.textContent="下载校验清单";actions.append(manifest);}
  if(r.state==="ready")actions.append(button("发布并设为推荐",()=>mutate("publish",{recommended:true}),{primary:true}));
  if(r.state==="published"){
    if(!r.recommended)actions.append(button("设为推荐",()=>mutate("recommend"),{primary:true}));
    actions.append(button("撤下版本",async()=>{const alternatives=catalog.items.filter(x=>x.id!==id&&x.state==="published"&&x.arch===r.arch);const dialog=document.getElementById("detail");document.getElementById("detail-title").textContent="撤下客户端版本";document.getElementById("detail-body").innerHTML=`<form id="withdraw-form" class="form"><p>撤下后将拒绝此版本的新下载，已下载副本仍可使用。</p><label>撤下原因<textarea name="reason" required maxlength="1000"></textarea></label>${r.recommended?`<label>新的推荐版本<select name="replacement_id"><option value="">不推荐任何版本（下载页显示暂无版本）</option>${alternatives.map(x=>`<option value="${x.id}">${esc(x.app_version)} · ${esc(x.arch)}</option>`).join("")}</select></label>`:""}<button class="danger">确认撤下</button></form>`;dialog.showModal();document.getElementById("withdraw-form").onsubmit=event=>{event.preventDefault();const values=formData(event.target);action(async()=>{await mutate("withdraw",{reason:values.reason,replacement_id:values.replacement_id||""});dialog.close();});};},{danger:true}));
  }
  if (!["processing","published"].includes(r.state) && !["receive","import_pending"].includes(r.stage)) {
    actions.append(releaseArchiveButton(r,catalog));
  }
}
async function downloads(){
  const data=await api("/client-releases/recommended");const r=data.release;
  content.innerHTML=`<section class="download"><h1>${esc(platform.platform_name)} · Cowork</h1><p class="muted">下载企业客户端，完整解压后运行。登录后取得管理员分配的模型。</p>${r?`<div class="form"><h2>Windows ${esc(r.arch)} · ${esc(r.app_version)}</h2><dl><dt>发布时间</dt><dd>${date(r.published_at)}</dd><dt>文件大小</dt><dd>${(r.bytes/1024/1024).toFixed(1)} MiB</dd><dt>SHA-256</dt><dd class="code">${esc(r.sha256)}</dd></dl><p>${esc(r.notes||"暂无更新说明")}</p><a class="button primary" href="${base}/api/v1/client-releases/${r.id}/download">下载企业客户端</a><a class="button" href="${base}/api/v1/client-releases/${r.id}/manifest">下载校验清单</a><p class="help">离线更新时，将安装包和本页校验清单放在同一目录。</p><p class="help">完整解压后运行 deepseek-cowork.exe。也可选择暂不登录，继续个人使用。</p></div>`:empty("暂无可下载版本，请联系管理员发布企业客户端。")}<p class="section"><a href="${base}/app" data-route>返回企业平台</a></p></section>`;
}
async function render(){
  routeGeneration++;clearTimeout(releasePoll);clearTimeout(spacePoll);message("");updateChrome();
  const route=location.pathname.slice(base.length);
  if(route==="/downloads")return downloads();
  if(!me)return login();
  if(route.startsWith("/app/team-spaces") && spaceEnabled())return teamSpaceRoute(route);
  if(/^\/app\/members\/[^/]+\/spaces$/.test(route)){const id=route.split("/")[3],data=await api("/admin/members");const member=data.items.find(m=>m.id===id);if(!member)throw new Error("成员不存在。");return spaceOwnerRecovery(member);}
  if(route==="/app/releases/new")return newRelease();
  if(route.startsWith("/app/releases/"))return releaseDetail(decodeURIComponent(route.split("/").pop()), true);
  if(route==="/app/capabilities"||route.startsWith("/app/capabilities/"))return capabilityRoute(route);
  if(route.startsWith("/app/models/")){
    const [, , , modelId, panel] = route.split("/");
    const id = decodeURIComponent(modelId);
    if (panel === "grants") return models(id, true);
    if (id === "new") return editModel(null);
    const data = await api("/admin/models");
    const item = data.items.find(model => model.id === id);
    if (!item) throw new Error("模型不存在，请返回列表。");
    return editModel(item);
  }
  if(/^\/app\/sources\/[^/]+\/accounts(?:\/[^/]+)?$/.test(route)){const parts=route.split("/");return databaseAccountsPage(parts[3],parts[5]);}
  if(route.startsWith("/app/sources/")){const id=route.split("/").pop(),data=await api("/admin/settings");const item=data.sources.find(m=>m.id===id);if(id!=="new"&&!item)throw new Error("认证来源不存在，请返回设置。");return editSource(item||null);}
  if(route.startsWith("/app/members/")){const parts=route.split("/");return members(decodeURIComponent(parts[3]),parts[4]);}
  const page=route.split("/")[2]||"members";
  await ({members,models,audit:chatAuditPage,settings:settingsPage,releases}[page]||members)();
}
(async()=>{
  try {
    platform = await api("/platform-info");
    try {
      const session = await api("/me");
      if (session.member.role === "admin") me = session;
    } catch(error) { if (error.status !== 401) throw error; }
    await render();
  } catch(error) {
    message(error.message,"error");
    content.innerHTML=empty("页面未完成加载。请检查连接后重试。");
    content.append(button("重试",()=>location.reload()));
  }
})();
