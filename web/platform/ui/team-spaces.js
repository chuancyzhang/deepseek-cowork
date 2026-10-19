"use strict";
let spacePoll = null;
let spaceVisibleCheck = null;
document.addEventListener("visibilitychange",()=>{if(!document.hidden && spaceVisibleCheck){clearTimeout(spacePoll);spaceVisibleCheck();}});
const spaceRole = {owner:"所有者",admin:"管理员",member:"成员"};
const spaceState = {active:"有效",archived:"已归档",pending:"待处理",in_progress:"进行中",completed:"已完成",withdrawn:"已撤回",accepted:"已接受",declined:"已拒绝",revoked:"已撤销",expired:"已过期",running:"阶段性成果",stopped:"已停止",failed:"未完成",manual:"人工整理"};
const spacePath = id => "/app/team-spaces/" + id;
const spaceAPI = id => "/team-spaces/" + id;
const spaceEnabled = () => platform?.team_spaces?.version === 1 && me?.can_use_team_spaces !== false;
const spaceDraftKey = suffix => "cowork.space." + JSON.stringify([platform.platform_id,me.member.id,suffix]);
function spaceDraft(suffix, value) {
  const key = spaceDraftKey(suffix);
  if(value === null) {localStorage.removeItem(key); return;}
  if(value !== undefined) {localStorage.setItem(key, JSON.stringify(value)); return value;}
  const raw = localStorage.getItem(key);
  if(!raw) return null;
  try {return JSON.parse(raw);} catch {throw new Error("本机草稿无法读取；请保留当前输入并联系维护者检查。")}
}
function spaceFormDraft(form, key) {
  const saved = spaceDraft(key);
  if(saved?.values) for(const [name,value] of Object.entries(saved.values)) {
    const field = form.elements.namedItem(name);
    if(!field) continue;
    if(field.type === "checkbox") field.checked = !!value;
    else field.value = value;
  }
  const capture = () => {
    const values = formData(form);
    form.querySelectorAll("input[type=checkbox][name]").forEach(el=>values[el.name]=el.checked);
    spaceDraft(key,{...spaceDraft(key),values});
  };
  form.addEventListener("input",capture);form.addEventListener("change",capture);watchForm(form);
  if(saved?.values) message("已恢复本机草稿；点击保存后才会提交到中心。");
}
async function spaceMutate(id, actionName, payload, key) {
  const previous = spaceDraft(key) || {};
  let pending = previous.pending;
  if(pending && JSON.stringify(pending.payload) !== JSON.stringify(payload)) {
    const status = await api("/team-space-operations/"+pending.id);
    if(status.state === "confirmed") {
      spaceDraft(key,null);
      throw new Error("原提交已确认保存，请刷新后再修改，避免重复操作。");
    }
    throw new Error("原提交结果尚未确认；请恢复原内容后重试，或先刷新核对结果。草稿仍保留。");
  }
  pending ||= {id:crypto.randomUUID(),payload};
  spaceDraft(key,{...previous,pending});
  try {
    const status = await api("/team-space-operations/"+pending.id);
    const result = status.state === "confirmed" ? status.result : await api(id ? spaceAPI(id)+"/actions/"+actionName : "/team-spaces",
      {method:"POST",body:{...pending.payload,operation_id:pending.id}});
    spaceDraft(key,null);dirty=false;return result;
  } catch(error) {
    if(error.status >= 400 && error.status < 500 && ![408,429].includes(error.status)) spaceDraft(key,{...previous,pending:null});
    throw error;
  }
}
function spaceLink(label, path) {return `<a data-route href="${base+path}">${esc(label)}</a>`;}
function spacePager(data, offset, navigatePage) {
  if(!offset&&!data.has_more)return;
  const bar=document.createElement("div");bar.className="pager";
  if(offset)bar.append(button("上一页",()=>navigatePage(Math.max(0,offset-50))));
  const label=document.createElement("span");label.textContent=`第 ${Math.floor(offset/50)+1} 页`;bar.append(label);
  if(data.has_more)bar.append(button("下一页",()=>navigatePage(offset+50)));
  content.append(bar);
}
function spaceDesktop(id, plan="") {
  const link = location.origin+base+spacePath(id)+(plan?"/plans/"+plan:"/activity");
  const box=document.createElement("div");box.className="note space-handoff";
  box.innerHTML='<p>在 Cowork 桌面的“团队空间 → 打开空间链接”继续。打开链接不会自动启动任务。</p>';
  const field=document.createElement("input");field.readOnly=true;field.value=link;field.setAttribute("aria-label","空间链接");
  box.append(field,button("复制空间链接",async()=>{await navigator.clipboard.writeText(link);message("链接已复制，请在桌面端打开。","success");}));
  content.append(box);
}
function spaceHeader(space, tab="activity") {
  heading(space.name,`${spaceRole[space.role]} · ${space.state==="archived"?"已归档 · 共享内容只读":"团队空间"}`,"/app/team-spaces");
  const controls=document.createElement("div");controls.className="actions-row";
  controls.append(button("成员",()=>navigate(spacePath(space.id)+"/members")),button("空间设置",()=>navigate(spacePath(space.id)+"/settings")),button("在桌面端继续",()=>spaceDesktop(space.id)));
  content.querySelector(".page-title").append(controls);
  const tabs=document.createElement("div");tabs.className="space-tabs";tabs.setAttribute("role","navigation");tabs.setAttribute("aria-label","空间内容");
  tabs.innerHTML=[["activity","动态"],["plans","计划"],["tasks","任务"],["assets","资产"]].map(([key,label])=>`<a data-route ${key===tab?'aria-current="page"':''} href="${base+spacePath(space.id)+"/"+key}">${label}</a>`).join("");content.append(tabs);
  const fresh=document.createElement("div");fresh.className="space-freshness";fresh.textContent="最后更新："+date(Date.now()/1000);content.append(fresh);
  const generation=routeGeneration;
  spaceVisibleCheck=null;
  let failures=0;
  const check=async()=>{
    if(generation!==routeGeneration)return;
    if(document.hidden){spacePoll=setTimeout(check,10000);return;}
    try{const latest=await api(spaceAPI(space.id));failures=0;
      if(latest.event_version!==space.event_version){fresh.replaceChildren();fresh.append("共享内容有更新。",button("刷新",()=>navigate(location.pathname.slice(base.length)+location.search)));}
      else fresh.textContent="最后更新："+date(Date.now()/1000);
    }catch(error){if(error.name==="AbortError")return;failures++;fresh.textContent="暂时无法刷新，已显示内容保留。";if(failures>=5){fresh.append(button("重新连接",()=>{failures=0;check();}));return;}}
    spacePoll=setTimeout(check,Math.min(60000,10000*2**failures));
  };
  spacePoll=setTimeout(check,10000);
  spaceVisibleCheck=check;
}
async function spaceList() {
  const params=new URLSearchParams(location.search),offset=Number(params.get("offset")||0),search=params.get("search")||"",archived=params.get("archived")==="true";
  const [data,inbox]=await Promise.all([api("/team-spaces?"+new URLSearchParams({offset,search,archived})),api("/team-space-invites")]);
  heading("团队空间","共享资料和分工，在自己的桌面执行，再把成果交给团队。");
  content.querySelector(".page-title").append(button("新建空间",()=>navigate("/app/team-spaces/new"),{primary:true}));
  content.insertAdjacentHTML("beforeend",`<form class="toolbar" id="space-search"><label>空间名称<input name="search" value="${esc(search)}" placeholder="搜索已加入的空间"></label><label>范围<select name="archived"><option value="false">我的空间</option><option value="true" ${archived?"selected":""}>已归档</option></select></label><button>搜索</button></form>`);
  document.getElementById("space-search").onsubmit=e=>{e.preventDefault();navigate("/app/team-spaces?"+new URLSearchParams(formData(e.target)));};
  if(inbox.items.length){const list=document.createElement("section");list.className="section";list.innerHTML="<h2>待处理邀请</h2>";
    inbox.items.forEach(invite=>{const row=document.createElement("div");row.className="source-row";const text=document.createElement("span");text.className="summary";text.textContent=`${invite.space_name} · ${invite.sender_name} 邀请你成为${spaceRole[invite.role]} · ${spaceState[invite.state]}`;row.append(text);
      if(invite.state==="pending")for(const [response,label] of [["accept","接受"],["decline","拒绝"]])row.append(button(label,async()=>{await spaceMutate(invite.space_id,"invite.respond",{id:invite.id,revision:invite.revision,response},"invite:"+invite.id);await render();}));list.append(row);});content.append(list);}
  content.insertAdjacentHTML("beforeend",data.items.length?`<div class="table-wrap"><table><thead><tr><th>空间</th><th>本人角色</th><th>共享内容更新</th></tr></thead><tbody>${data.items.map(s=>`<tr><td>${spaceLink(s.name,spacePath(s.id)+"/activity")}<span class="secondary-line">${esc(s.description.slice(0,160))}</span></td><td>${spaceRole[s.role]}</td><td>${date(s.updated_at)}</td></tr>`).join("")}</tbody></table></div>`:empty(archived?"没有已归档的空间。":"还没有团队空间。创建空间，或等待同事邀请。"));
  spacePager(data,offset,next=>navigate("/app/team-spaces?"+new URLSearchParams({search,archived,offset:next})));
}
async function spaceSettings(space=null) {
  const path=space?spacePath(space.id)+"/settings":"/app/team-spaces/new",key="form:"+path;
  if(space){spaceHeader(space,"settings");if(!["owner","admin"].includes(space.role)){content.insertAdjacentHTML("beforeend",`<p>${esc(space.description||"暂无空间说明")}</p><p>个人模型：${space.allow_personal?"允许":"不允许"}</p><p>设置由空间所有者或管理员维护。</p>`);return;}}
  else heading("新建团队空间","只需名称和可选说明，创建后邀请企业成员。","/app/team-spaces");
  const form=document.createElement("form");form.className="form";form.innerHTML=`<label>空间名称<input name="name" required maxlength="120" value="${esc(space?.name||"")}"></label><label>空间说明<textarea name="description" maxlength="20000">${esc(space?.description||"")}</textarea></label>${space?`<input name="revision" type="hidden" value="${space.revision}"><label>公司模型范围<select name="model_mode"><option value="authorized">成员已获授权的模型</option><option value="selected" ${space.model_mode==="selected"?"selected":""}>仅允许下方选定模型</option></select></label><div class="check-list">${space.models.map(m=>`<label><input type="checkbox" name="model_${m.id}" ${space.model_ids.includes(m.id)?"checked":""}>${esc(m.name)}${m.enabled?"":"（已停用）"}</label>`).join("")||"当前账号没有获准公司模型，仍可维护人工协作。"}</div><label class="check"><input type="checkbox" name="allow_personal" ${space.allow_personal?"checked":""} ${space.role!=="owner"?"disabled":""}>允许成员使用自己的个人模型（仅所有者可改）</label><p class="help">当前范围中不在你的模型目录内的模型保持原选择；此设置不增加员工授权。</p><label>Skill 引用（每行：来源 | 标识 | 版本）<textarea name="skills" placeholder="local | skill-name | 1.0.0">${esc(space.skills.map(s=>[s.source,s.id,s.version].join(" | ")).join("\n"))}</textarea></label>`:""}<button class="primary" ${space?.state==="archived"?"disabled":""}>${space?"保存设置":"创建空间"}</button>`;
  content.append(form);spaceFormDraft(form,key);
  form.onsubmit=e=>{e.preventDefault();action(async()=>{
    const values=formData(form),payload={name:values.name,description:values.description};
    if(space){Object.assign(payload,{revision:Number(values.revision),model_mode:values.model_mode,
      model_ids:[...space.model_ids.filter(id=>!space.models.some(m=>m.id===id)),...space.models.filter(m=>form.elements["model_"+m.id].checked).map(m=>m.id)],
      skills:values.skills.split("\n").map(s=>s.trim()).filter(Boolean).map(line=>{const parts=line.split("|").map(s=>s.trim());if(parts.length!==3||parts.some(s=>!s))throw new Error("每条 Skill 请填写来源、标识和版本。");return {source:parts[0],id:parts[1],version:parts[2]};})});if(space.role==="owner")payload.allow_personal=form.elements.allow_personal.checked;}
    const submit=form.querySelector("button");submit.disabled=true;try{const result=await spaceMutate(space?.id||"",space?"space.update":"space.create",payload,key);navigate(spacePath(result.id)+"/activity");message("中心已确认保存。","success");}catch(error){if(error.status===409){const latest=await api(spaceAPI(space.id));const comparison=document.createElement("details");comparison.open=true;comparison.innerHTML=`<summary>最新设置（请与上方草稿比较）</summary><pre>${esc(latest.name+"\n"+latest.description+"\n个人模型："+(latest.allow_personal?"允许":"关闭"))}</pre>`;comparison.append(button("保留草稿并采用最新版本号",()=>{form.elements.revision.value=latest.revision;form.dispatchEvent(new Event("change"));comparison.remove();message("已采用最新版本号，请核对内容后再次保存。");}));content.append(comparison);}throw error;}finally{submit.disabled=false;}
  });};
  if(space?.role==="owner") {
    const section=document.createElement("section");section.className="section";section.innerHTML="<h2>空间管理</h2><p>归档不会停止本机任务；归档期间不能发布成果。</p>";
    section.append(button(space.state==="archived"?"恢复空间":"归档空间",async()=>{if(!confirm(`空间有 ${space.unfinished_plans} 条未完成计划。${space.state==="archived"?"恢复后可继续编辑与发布，原撤回内容不会恢复。":"归档后共享内容只读，本机历史和文件保留。"}`))return;await spaceMutate(space.id,space.state==="archived"?"space.restore":"space.archive",{revision:space.revision},"archive:"+space.id);navigate(spacePath(space.id)+"/activity");}));
    const transfer=document.createElement("form");transfer.className="toolbar";transfer.innerHTML=`<label>转移所有权<select name="member_id" required><option value="">选择已加入的有效成员</option>${space.members.filter(m=>m.active&&m.enabled&&m.id!==me.member.id).map(m=>`<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select></label><button>转移所有权</button>`;transfer.onsubmit=e=>{e.preventDefault();action(async()=>{if(!confirm("转移后你将成为空间管理员，新所有者可管理个人模型开关及归档。"))return;await spaceMutate(space.id,"space.transfer",{revision:space.revision,member_id:transfer.elements.member_id.value},"transfer:"+space.id);navigate(spacePath(space.id)+"/members");});};section.append(transfer);content.append(section);
  }
}
async function spaceMembers(space) {
  spaceHeader(space,"members");const managing=["owner","admin"].includes(space.role);
  const table=document.createElement("div");table.className="table-wrap";table.innerHTML=`<table><thead><tr><th>成员</th><th>空间角色</th><th>状态</th><th>操作</th></tr></thead><tbody>${space.members.map(m=>`<tr><td>${esc(m.name)}</td><td>${spaceRole[m.role]}</td><td>${m.active?(m.enabled?"有效":"账号已停用"):"已退出或移除"}</td><td id="member-${m.id}"></td></tr>`).join("")}</tbody></table>`;content.append(table);
  space.members.filter(m=>m.active).forEach(m=>{const actions=document.getElementById("member-"+m.id);
    if(managing&&m.role!=="owner"&&(space.role==="owner"||m.role==="member"))actions.append(button("移除",async()=>{if(!confirm("移除后阻止后续空间访问，已有贡献保留，未完成计划需重新分配。"))return;await spaceMutate(space.id,"member.remove",{member_id:m.id,revision:m.revision},"remove:"+space.id+":"+m.id);await render();},{danger:true}));
    if(space.role==="owner"&&m.role!=="owner")actions.append(button(m.role==="admin"?"设为普通成员":"设为管理员",async()=>{await spaceMutate(space.id,"member.role",{member_id:m.id,revision:m.revision,role:m.role==="admin"?"member":"admin"},"role:"+space.id+":"+m.id);await render();}));
  });
  if(space.role!=="owner"){const self=space.members.find(m=>m.id===me.member.id);content.append(button("退出空间",async()=>{if(!confirm("退出后无法访问共享内容，你已发布的贡献仍保留。"))return;await spaceMutate(space.id,"space.leave",{revision:self.revision},"leave:"+space.id);navigate("/app/team-spaces");},{danger:true}));}
  if(!managing)return;
  const invite=document.createElement("section");invite.className="section";invite.innerHTML='<h2>邀请企业成员</h2><form class="toolbar" id="candidate-search"><label>姓名或账号<input name="search" maxlength="200"></label><button>查找成员</button></form><div id="candidate-list"></div>';content.append(invite);
  const search=document.getElementById("candidate-search");search.onsubmit=e=>{e.preventDefault();action(async()=>{const data=await api("/team-space-members?"+new URLSearchParams(formData(search))),list=document.getElementById("candidate-list");list.replaceChildren();data.items.filter(m=>!space.members.some(existing=>existing.id===m.id&&existing.active)).forEach(m=>{const row=document.createElement("div");row.className="source-row";const label=document.createElement("span");label.className="summary";label.textContent=m.name+" · "+m.account;row.append(label);const role=document.createElement("select");role.setAttribute("aria-label","邀请角色");role.innerHTML='<option value="member">普通成员</option>'+(space.role==="owner"?'<option value="admin">管理员</option>':"");row.append(role,button("发送邀请",async()=>{await spaceMutate(space.id,"invite.create",{member_id:m.id,role:role.value},"invite:"+space.id+":"+m.id);await render();message("邀请已发送，对方可在团队空间入口接受。","success");}));list.append(row);});if(!list.children.length)list.innerHTML=empty("没有可邀请的匹配成员。外部认证成员需先完成一次企业登录。");});};
  const invitations=await api(spaceAPI(space.id)+"/invites");if(invitations.items.length){const box=document.createElement("section");box.className="section";box.innerHTML="<h2>邀请记录</h2>";invitations.items.forEach(i=>{const row=document.createElement("div");row.className="source-row";row.append(`${i.recipient_name} · ${spaceRole[i.role]} · ${spaceState[i.state]} · ${date(i.expires_at)}`);if(i.state==="pending"&&(space.role==="owner"||i.role==="member"))row.append(button("撤销",async()=>{await spaceMutate(space.id,"invite.revoke",{id:i.id,revision:i.revision},"revoke:"+i.id);await render();}));box.append(row);});content.append(box);}
}
async function spaceComments(space,kind,id) {
  const comments=await api(spaceAPI(space.id)+"/comments?"+new URLSearchParams({target_kind:kind,target_id:id}));
  content.insertAdjacentHTML("beforeend",`<section class="section"><h2>讨论</h2>${comments.items.length?comments.items.map(c=>`<article class="space-comment"><p class="muted">${esc(c.author_name)} · ${date(c.updated_at)}${c.revision>1?" · 已修改":""}</p><pre>${esc(c.state==="active"?c.body:"留言已撤回")}</pre></article>`).join(""):empty("暂无留言。在桌面端讨论或要求重新打开计划。")}</section>`);
}
async function spaceObject(space,kind,id) {
  const item=await api(spaceAPI(space.id)+"/objects/"+kind+"/"+id);spaceHeader(space,kind==="plan"?"plans":kind==="asset"?"assets":"tasks");
  if(kind==="comment"){
    content.insertAdjacentHTML("beforeend",`<section class="section"><h2>留言</h2><p>${esc(item.author_name)} · ${date(item.updated_at)}${item.revision>1?" · 已修改":""}</p><pre>${esc(item.state==="active"?item.body:"留言已撤回")}</pre></section>`);
    const target={plan:"plans",asset:"assets",share:"tasks"}[item.target_kind];
    content.insertAdjacentHTML("beforeend",`<p>${spaceLink("查看讨论来源",spacePath(space.id)+(target?"/"+target+"/"+item.target_id:"/activity"))}</p>`);
    return;
  }
  content.insertAdjacentHTML("beforeend",`<section class="section"><h2>${esc(item.title||item.name)}</h2><p class="muted">${spaceState[item.state]||""} · ${esc(item.author_name||"")}</p><pre>${esc(item.description||"")}</pre></section>`);
  if(kind==="plan"){content.insertAdjacentHTML("beforeend",`<p>负责人：${esc(item.assignee_name)}${item.assignee_id&&!item.assignee_available?"（已不可用，请重新分配）":""} · ${item.priority==="high"?"高优先级":"普通优先级"}</p><pre>${esc(item.completion)}</pre>`);await spaceFileRefs(space,item.inputs);for(const ref of item.outcomes)content.insertAdjacentHTML("beforeend",`<p>${spaceLink("查看关联成果",spacePath(space.id)+"/tasks/"+ref.share_id+"?snapshot="+ref.snapshot_id)}</p>`);spaceDesktop(space.id,item.id);}
  if(kind==="asset"){content.insertAdjacentHTML("beforeend",`<p>来源：${esc(item.author_name)}；更新于 ${date(item.updated_at)}</p>`);for(const rev of item.revisions){const p=document.createElement("p");p.textContent=`修订 ${rev.number} · ${rev.size} 字节 · ${date(rev.created_at)} `;if(item.state==="active"){const a=document.createElement("a");a.href=base+"/api/v1"+spaceAPI(space.id)+"/files/"+rev.id;a.textContent="下载此版本";p.append(a);}content.append(p);}content.insertAdjacentHTML("beforeend",`<h3>引用位置</h3>${item.references.length?item.references.map(r=>`<p>${spaceLink(r.title,spacePath(space.id)+(r.kind==="plan"?"/plans/":"/tasks/")+r.id)}</p>`).join(""):"<p>暂无已共享的引用。</p>"}`);}
  if(kind==="share"&&item.state==="active"){
    const box=document.createElement("section");box.className="section";const select=document.createElement("select");select.setAttribute("aria-label","成果修订");select.innerHTML=item.snapshots.map(s=>`<option value="${s.id}">修订 ${s.number} · ${date(s.created_at)} · ${spaceState[s.source_state]}</option>`).join("");const body=document.createElement("pre"),files=document.createElement("div");box.append(select,body,files);content.append(box);
    const pinned=new URLSearchParams(location.search).get("snapshot");if(pinned){if(!item.snapshots.some(s=>s.id===pinned))throw new Error("所引用的成果版本不存在。");select.value=pinned;}
    const display=async()=>{const snapshot=item.snapshots.find(s=>s.id===select.value);body.textContent=snapshot.body;files.replaceChildren();await spaceFileRefs(space,snapshot.assets,files);};select.onchange=()=>action(display);await display();
  }
  await spaceComments(space,kind,id);
}
async function spaceFileRefs(space,refs,target=content) {
  for(const ref of refs){const p=document.createElement("p");try{const asset=await api(spaceAPI(space.id)+"/objects/asset/"+ref.asset_id);const revision=asset.revisions.find(r=>r.id===ref.revision_id);p.append(document.createTextNode(asset.name+" · 修订 "+(revision?.number||"不可用")+" · "));if(asset.state==="active"){const a=document.createElement("a");a.href=base+"/api/v1"+spaceAPI(space.id)+"/files/"+ref.revision_id;a.textContent="下载固定版本";p.append(a);}else p.append("资料已撤下");}catch(error){if(error.name==="AbortError")throw error;p.textContent="资料不可用："+error.message;}target.append(p);}
}
async function spaceOverview(space,tab) {
  const params=new URLSearchParams(location.search),offset=Number(params.get("offset")||0),query={offset};
  const collection=tab==="tasks"?"shares":tab;
  if(tab==="assets")query.folder_id=params.get("folder")||"";
  if(params.get("search"))query.search=params.get("search");
  if(tab==="plans"){if(params.get("assignee_id"))query.assignee_id=params.get("assignee_id");if(params.get("state"))query.state=params.get("state");}
  const data=await api(spaceAPI(space.id)+"/"+collection+"?"+new URLSearchParams(query));spaceHeader(space,tab);
  if(tab==="activity"){
    content.insertAdjacentHTML("beforeend",data.items.length?data.items.map(event=>`<article class="space-comment"><p>${esc(event.actor_name)} · ${esc(spaceEventLabel(event.kind))} · ${date(event.created_at)}</p><p>${spaceEventLink(space,event)}</p>${event.details.reason?`<pre>${esc(event.details.reason)}</pre>`:""}</article>`).join(""):empty("暂无共享动态。发布资料、计划或成果后会显示在这里。"));
    await spaceComments(space,"space",space.id);
  }else{
    if(tab==="tasks")content.insertAdjacentHTML("beforeend",'<p class="note">这里只显示已分享成果。本机任务请在原电脑打开，私人任务标题和数量不上传。</p>');
    if(tab==="plans"||tab==="assets"){const filter=document.createElement("form");filter.className="toolbar";filter.innerHTML=`<label>名称搜索<input name="search" value="${esc(params.get("search")||"")}"></label>${tab==="plans"?`<label>负责人<select name="assignee_id"><option value="">全部成员</option>${space.members.map(m=>`<option value="${m.id}" ${params.get("assignee_id")===m.id?"selected":""}>${esc(m.name)}</option>`).join("")}</select></label><label>状态<select name="state"><option value="">全部状态</option>${["pending","in_progress","completed"].map(s=>`<option value="${s}" ${params.get("state")===s?"selected":""}>${spaceState[s]}</option>`).join("")}</select></label>`:""}<button>筛选</button>`;filter.onsubmit=e=>{e.preventDefault();navigate(spacePath(space.id)+"/"+tab+"?"+new URLSearchParams({...formData(filter),...(tab==="assets"?{folder:query.folder_id}:{})}));};content.append(filter);}
    if(tab==="assets"){const folders=await api(spaceAPI(space.id)+"/folders?"+new URLSearchParams({folder_id:query.folder_id}));const row=document.createElement("div");row.className="actions-row";row.innerHTML=spaceLink("全部文件夹",spacePath(space.id)+"/assets")+folders.items.map(f=>spaceLink("文件夹："+f.name,spacePath(space.id)+"/assets?folder="+f.id)).join(" ");content.append(row);}
    content.insertAdjacentHTML("beforeend",data.items.length?`<div class="table-wrap"><table><thead><tr><th>${tab==="plans"?"计划":tab==="assets"?"资料":"成果"}</th><th>${tab==="plans"?"负责人":"发布人"}</th><th>状态</th><th>更新时间</th></tr></thead><tbody>${data.items.map(item=>`<tr><td>${spaceLink(item.title||item.name,spacePath(space.id)+"/"+tab+"/"+item.id)}</td><td>${esc(item.assignee_name||item.author_name||"")}${item.assignee_id&&!item.assignee_available?"（已不可用）":""}</td><td>${spaceState[item.state]||""}</td><td>${date(item.updated_at)}</td></tr>`).join("")}</tbody></table></div>`:empty("暂无匹配的共享内容。在桌面端新增或调整筛选条件。"));
  }
  spacePager(data,offset,next=>{params.set("offset",next);navigate(spacePath(space.id)+"/"+tab+"?"+params);});
}
function spaceEventLabel(kind){const names={created:"创建了空间","settings.updated":"更新了空间设置","invite.create":"发送了邀请","invite.accepted":"接受了邀请","invite.declined":"拒绝了邀请","invite.revoke":"撤销了邀请","plan.create":"创建了计划","plan.update":"更新了计划","plan.status":"更新了计划状态","comment.create":"发布了留言","comment.update":"修改了留言","comment.withdraw":"撤回了留言","asset.published":"发布了资料","asset.update":"整理了资料","asset.withdraw":"撤下了资料","asset.restore":"恢复了资料","folder.create":"新建了文件夹","share.publish":"发布了成果","share.withdraw":"撤回了成果","space.archive":"归档了空间","space.restore":"恢复了空间","space.transfer":"转移了所有权","space.leave":"退出了空间","member.remove":"移除了成员","member.role":"更新了成员角色","owner.recovered":"恢复了空间所有权"};return names[kind]||"更新了共享内容";}
function spaceEventLink(space,event){const route={plan:"plans",asset:"assets",share:"tasks",comment:"activity"}[event.target_kind];return route?spaceLink("查看来源",spacePath(space.id)+"/"+route+"/"+event.target_id):"";}
async function teamSpaceRoute(route){
  if(!spaceEnabled()){heading("团队空间暂不可用","请使用支持团队空间的中心和企业员工账号。","/app");return;}
  const parts=route.split("/").filter(Boolean),id=parts[2],tab=parts[3]||"activity",objectId=parts[4];
  if(!id)return spaceList();if(id==="new")return spaceSettings();
  const space=await api(spaceAPI(id));
  if(tab==="settings")return spaceSettings(space);if(tab==="members")return spaceMembers(space);
  if(!["activity","plans","tasks","assets"].includes(tab))throw new Error("空间页面不存在，请返回空间列表。");
  if(objectId)return spaceObject(space,{plans:"plan",tasks:"share",assets:"asset",activity:"comment"}[tab],objectId);
  return spaceOverview(space,tab);
}
async function spaceOwnerRecovery(member) {
  const data=await api("/admin/members/"+member.id+"/owned-spaces");
  heading("恢复空间所有权",`${member.name} 已停用。这里只提供必要标识和有效成员，不开放空间正文。`,"/app/members/"+member.id);
  if(!data.items.length){content.insertAdjacentHTML("beforeend",empty("该成员没有需要处理的空间。"));return;}
  for(const space of data.items){const form=document.createElement("form");form.className="form section";form.innerHTML=`<h2>${esc(space.name)}</h2><label>新所有者<select name="member_id" required><option value="">选择已有有效成员</option>${space.candidates.map(m=>`<option value="${m.id}">${esc(m.name)}</option>`).join("")}</select></label><label>恢复原因<textarea name="reason" required maxlength="2000"></textarea></label><button ${space.candidates.length?"":"disabled"}>恢复所有权</button>${space.candidates.length?"":"<p>没有其他有效成员，保留空间，等待原账号恢复。</p>"}`;content.append(form);watchForm(form);form.onsubmit=e=>{e.preventDefault();action(async()=>{const payload={...formData(form),revision:space.revision,operation_id:form.dataset.operation||crypto.randomUUID()};form.dataset.operation=payload.operation_id;await api("/admin/team-spaces/"+space.id+"/recover-owner",{method:"POST",body:payload});dirty=false;await spaceOwnerRecovery(member);message("所有权已恢复，未开放空间正文。","success");});};}
}
